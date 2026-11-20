"""Minuta de reunión (SGC-FOR-009).

La captura usa el **motor dinámico** (``Formato`` + ``RegistroFormato``); este
servicio concentra el alta/guardado, la validación del Nº de reunión, el
prefijo de responsables y la **exportación XLSX/PDF** con la plantilla oficial
(hoja ``SGC-FOR-009`` + instructivo ``SGC-IDL-015``). El XLSX descargable
conserva el instructivo y su escala original; el PDF se prepara aparte (una
página de ancho y sin el instructivo).

La minuta sirve para cualquier reunión del SGC: ``tipo_reunion='revision'``
(Revisión por la Dirección, puede enlazarse al FOR-007) u ``'otra'`` (no
planificada, Nº ``XXX - NPR``).
"""
import io
import logging
import math
import re
import textwrap
import uuid

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

FORMATO_FOR_009 = 'SGC-FOR-009'
HOJA_FOR_009 = 'SGC-FOR-009'
CONTEXTO_TIPO = 'minuta'
MAX_ASISTENTES_HOJA = 7

CELDAS = {
    'reunion_no': 'B10',
    'objetivo': 'C13',
    'lugar': 'B16',
    'fecha': 'B18',
    'responsable': 'A20',
    'duracion': 'B22',
    'tema': 'A33',
    'conclusiones': 'A38',
    'acuerdos_cerrados': 'A46',
    'acuerdos_pendientes': 'A54',
    'responsables': 'A65',
    'pagina': 'I6',
}
MERGES = {
    'objetivo': 'C13:J14',
    'lugar': 'B16:F16',
    'fecha': 'B18:C18',
    'duracion': 'B22:C22',
}
ETIQUETAS = {
    'objetivo': 'Objetivo de la Reunión: ',
    'tema': 'Descripciòn del Tema tratado: ',
    'conclusiones': 'Conclusiones:',
    'acuerdos_cerrados': 'Acuerdos Cerrados:',
    'acuerdos_pendientes': 'Acuerdos Pendientes:',
    'responsables': 'Responsables de la Aprobaciòn: ',
}
RANGOS_TEXTO = {
    'tema': 'A33:J36',
    'conclusiones': 'A38:J44',
    'acuerdos_cerrados': 'A46:J52',
    'acuerdos_pendientes': 'A54:J60',
    'responsables': 'A65:H65',
}
ETIQUETAS_CAMPO = {
    'objetivo': 'Objetivo de la Reunión',
    'lugar': 'Lugar',
    'responsable': 'Responsable de la Elaboración',
    'duracion': 'Duración',
}
MESES = ['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN',
         'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC']
MES_POR_NUMERO = {i + 1: mes for i, mes in enumerate(MESES)}

_RE_PLANIFICADA = re.compile(r'\d{1,4}$')
_RE_NPR = re.compile(r'(\d{3})\s*-\s*NPR$')


# ---------------------------------------------------------------------------
# Formato / config
# ---------------------------------------------------------------------------

def formato_for_009():
    from ..models import Formato

    return Formato.objects.filter(codigo=FORMATO_FOR_009, activo=True).first()


def campo_asistentes(formato=None):
    from ..models import CampoFormato

    formato = formato or formato_for_009()
    if formato is None:
        return None
    return CampoFormato.objects.filter(
        seccion__formato=formato, clave='asistentes',
    ).first()


def config_asistentes(formato=None):
    campo = campo_asistentes(formato)
    return dict(campo.config_tabla or {}) if campo else {}


# ---------------------------------------------------------------------------
# Validaciones y datos
# ---------------------------------------------------------------------------

def validar_reunion_no(valor):
    """Valida/normaliza el Nº de reunión (consecutivo o ``XXX - NPR``)."""
    texto = str(valor or '').strip().upper()
    if _RE_PLANIFICADA.fullmatch(texto):
        return texto
    npr = _RE_NPR.fullmatch(texto)
    if npr:
        return f'{npr.group(1)} - NPR'
    raise ValueError(
        'El Nº de reunión debe ser un consecutivo (1, 2, 3…) o '
        '"001 - NPR" para reuniones no planificadas.'
    )


def asistentes(respuestas):
    filas = (respuestas or {}).get('asistentes') or []
    return [f for f in filas if isinstance(f, dict) and (
        str(f.get('nombre') or '').strip()
        or str(f.get('cargo') or '').strip()
        or str(f.get('departamento') or '').strip()
    )]


def responsables_sugeridos():
    """Responsables de la aprobación de la minuta más reciente (o ``''``)."""
    from ..models import RegistroFormato

    formato = formato_for_009()
    if formato is None:
        return ''
    for registro in RegistroFormato.objects.filter(
        formato=formato, contexto_tipo=CONTEXTO_TIPO,
    ).order_by('-creado')[:25]:
        valor = (registro.respuestas or {}).get('responsables_aprobacion')
        if valor:
            return str(valor)
    return ''


def _titulo(respuestas, fecha):
    reunion_no = str((respuestas or {}).get('reunion_no') or '').strip()
    fecha_txt = fecha.strftime('%d/%m/%Y') if fecha else ''
    if reunion_no and fecha_txt:
        return f'Minuta {reunion_no} — {fecha_txt}'
    if reunion_no:
        return f'Minuta {reunion_no}'
    return f'Minuta {fecha_txt}'.strip()


def validar_anio(valor):
    """Valida el año de la reunión (2000–2100)."""
    try:
        anio = int(str(valor).strip())
    except (TypeError, ValueError):
        raise ValueError(
            'El año debe ser un número entre 2000 y 2100.',
        ) from None
    if not 2000 <= anio <= 2100:
        raise ValueError('El año debe estar entre 2000 y 2100.')
    return anio


def anio_mes(respuestas, fecha):
    """Año/mes (clave ENE..DIC) para marcar el programa FOR-007."""
    respuestas = respuestas or {}
    anio = respuestas.get('anio')
    if anio not in (None, ''):
        try:
            anio = validar_anio(anio)
        except ValueError:
            anio = fecha.year if fecha else timezone.localdate().year
    else:
        anio = fecha.year if fecha else timezone.localdate().year
    mes = str(respuestas.get('mes') or '').upper()
    if mes not in MESES:
        mes = MES_POR_NUMERO.get(
            fecha.month if fecha else timezone.localdate().month, '',
        )
    return anio, mes


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------

def crear_minuta(*, fecha=None, respuestas=None, usuario=None):
    """Crea una minuta (``RegistroFormato``) con contexto ``minuta``."""
    from ..models import RegistroFormato

    formato = formato_for_009()
    if formato is None:
        raise ValueError(
            'El formato SGC-FOR-009 no está integrado.',
        )
    fecha = fecha or timezone.localdate()
    respuestas = dict(respuestas or {})
    if 'reunion_no' in respuestas and respuestas.get('reunion_no'):
        respuestas['reunion_no'] = validar_reunion_no(
            respuestas.get('reunion_no'),
        )
    from .versiones import registrar_version

    with transaction.atomic():
        registro = RegistroFormato.objects.create(
            formato=formato,
            formato_codigo=FORMATO_FOR_009,
            formato_version=formato.version or '',
            formato_edicion=formato.edicion or '',
            contexto_tipo=CONTEXTO_TIPO,
            contexto_id=uuid.uuid4().hex,
            titulo=_titulo(respuestas, fecha),
            fecha=fecha,
            responsable=usuario,
            estado='borrador',
            respuestas=respuestas,
        )
        registrar_version(registro, usuario, resumen='Alta de minuta')
    return registro


def guardar_minuta(registro, respuestas, *, fecha=None, usuario=None):
    """Actualiza respuestas/fecha/título, versiona e invalida los archivos.

    Si no hay cambios reales no crea una versión nueva ni invalida el
    XLSX/PDF (evita ruido de historial y regeneraciones inútiles).
    """
    from ..models import RegistroFormato
    from .versiones import registrar_version

    respuestas = dict(respuestas or {})
    if respuestas.get('reunion_no'):
        respuestas['reunion_no'] = validar_reunion_no(
            respuestas.get('reunion_no'),
        )
    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(
            pk=registro.pk,
        )
        nueva_fecha = fecha if fecha is not None else bloqueado.fecha
        nuevo_titulo = _titulo(respuestas, nueva_fecha)
        sin_cambios = (
            dict(bloqueado.respuestas or {}) == respuestas
            and bloqueado.fecha == nueva_fecha
            and bloqueado.titulo == nuevo_titulo
        )
        if sin_cambios:
            return registro

        bloqueado.fecha = nueva_fecha
        bloqueado.respuestas = respuestas
        bloqueado.titulo = nuevo_titulo
        for campo_archivo in ('archivo', 'archivo_pdf'):
            archivo = getattr(bloqueado, campo_archivo)
            if archivo:
                try:
                    archivo.delete(save=False)
                except Exception:  # noqa: BLE001
                    pass
        bloqueado.archivo = ''
        bloqueado.archivo_pdf = ''
        bloqueado.save(update_fields=[
            'respuestas', 'titulo', 'fecha', 'archivo', 'archivo_pdf',
            'actualizado',
        ])
        registrar_version(bloqueado, usuario, resumen='Edición de minuta')
        registro.respuestas = bloqueado.respuestas
        registro.titulo = bloqueado.titulo
        registro.fecha = bloqueado.fecha
    return registro


# ---------------------------------------------------------------------------
# Export XLSX/PDF
# ---------------------------------------------------------------------------

def _fecha_texto(valor):
    if hasattr(valor, 'strftime'):
        return valor.strftime('%d/%m/%Y')
    return str(valor or '')


def _merge_si_falta(hoja, rango):
    """Mergea solo si no hay un merge existente que lo cubra o solape.

    Nunca crea merges solapados (Excel abriría el archivo con "reparar").
    """
    from openpyxl.worksheet.cell_range import CellRange

    nuevo = CellRange(rango)
    for existente in hoja.merged_cells.ranges:
        if nuevo.isdisjoint(existente):
            continue
        contiene = (
            existente.min_row <= nuevo.min_row
            and existente.max_row >= nuevo.max_row
            and existente.min_col <= nuevo.min_col
            and existente.max_col >= nuevo.max_col
        )
        if not contiene:
            logger.warning(
                'El merge %s solapa parcialmente con %s; se respeta la plantilla.',
                rango, existente,
            )
        return
    hoja.merge_cells(rango)


def _celda_escribible(hoja, ref):
    from openpyxl.cell.cell import MergedCell

    celda = hoja[ref]
    if isinstance(celda, MergedCell):
        logger.warning('Se omite la escritura en la celda mergeada %s.', ref)
        return None
    return celda


def _escribir_celda(hoja, ref, valor):
    celda = _celda_escribible(hoja, ref)
    if celda is not None:
        celda.value = valor
    return celda


def _ancho_rango(hoja, rango):
    from openpyxl.utils import get_column_letter, range_boundaries

    min_col, _, max_col, _ = range_boundaries(rango)
    total = 0
    for columna in range(min_col, max_col + 1):
        ancho = hoja.column_dimensions[get_column_letter(columna)].width
        total += ancho or 8.43
    return total


def _lineas_estimadas(texto, ancho):
    limite = max(10, int(ancho))
    lineas = 0
    for parrafo in str(texto or '').split('\n'):
        lineas += max(1, math.ceil(len(parrafo) / limite))
    return max(1, lineas)


def _ajustar_altura_bloque(hoja, rango, texto):
    """Aumenta las filas del merge para que el texto quepa (tope Excel 409).

    Returns:
        bool: ``True`` si cupo; ``False`` si quedó desbordado y debe ir a un
        anexo de texto.
    """
    from openpyxl.utils import range_boundaries

    _, min_row, _, max_row = range_boundaries(rango)
    lineas = _lineas_estimadas(texto, _ancho_rango(hoja, rango))
    necesario = lineas * 15 + 4
    filas = list(range(min_row, max_row + 1))
    actuales = {
        fila: (hoja.row_dimensions[fila].height or 15) for fila in filas
    }
    exceso = necesario - sum(actuales.values())
    if exceso <= 0:
        return True
    for fila in reversed(filas):
        if exceso <= 0:
            break
        actual = actuales[fila]
        capacidad = max(0, 409 - actual)
        agregar = min(exceso, capacidad)
        if agregar:
            hoja.row_dimensions[fila].height = actual + agregar
            exceso -= agregar
    if exceso > 0:
        logger.warning(
            'El texto de %s supera la altura máxima; se enviará al anexo.',
            rango,
        )
    return exceso <= 0


def _escribir_texto_bloque(hoja, celda, etiqueta, valor, rango):
    from openpyxl.styles import Alignment

    texto = f'{etiqueta}\n{valor}' if valor else etiqueta
    celda_obj = _escribir_celda(hoja, celda, texto)
    if celda_obj is not None:
        celda_obj.alignment = Alignment(
            horizontal='left', vertical='top', wrap_text=True,
        )
    return _ajustar_altura_bloque(hoja, rango, texto)


def _escribir_campo(hoja, clave, valor):
    from openpyxl.styles import Alignment

    celda_obj = _escribir_celda(hoja, CELDAS[clave], valor or '')
    if clave in MERGES:
        _merge_si_falta(hoja, MERGES[clave])
    if celda_obj is not None:
        celda_obj.alignment = Alignment(
            horizontal='left', vertical='center', wrap_text=True,
        )
    rango = MERGES.get(clave, CELDAS[clave])
    return _ajustar_altura_bloque(hoja, rango, valor or '')


def _escribir_responsable(hoja, valor):
    """Escribe etiqueta y valor juntos en A20:H20 con ajuste de altura.

    La plantilla oficial trae la etiqueta en A20 **sin merge** (A20:B20) y la
    columna A mide 12 caracteres: si la etiqueta y el valor vivieran en celdas
    separadas, la etiqueta se cortaría al imprimir. Mergear ``A20:H20`` con
    ``wrap_text`` evita además cortes si el cargo/nombre es largo o si alguna
    celda intermedia trae espacios.

    Returns:
        bool: ``True`` si cupo en ``A20:H20``; ``False`` si debe ir al anexo.
    """
    from openpyxl.styles import Alignment

    texto = f'{ETIQUETAS_CAMPO["responsable"]}: {str(valor or "").strip()}'
    celda = _escribir_celda(hoja, CELDAS['responsable'], texto)
    _merge_si_falta(hoja, 'A20:H20')
    if celda is not None:
        celda.alignment = Alignment(
            horizontal='left', vertical='center', wrap_text=True,
        )
    return _ajustar_altura_bloque(hoja, 'A20:H20', texto)


def _escribir_asistentes(hoja, filas, config):
    columnas = {c.get('clave'): c.get('celda')
                for c in (config.get('columnas') or [])}
    inicio = int(config.get('fila_inicio') or 26)
    maximo = int(config.get('max_filas') or MAX_ASISTENTES_HOJA)
    for offset, fila in enumerate(filas[:maximo]):
        numero = inicio + offset
        for clave, columna in columnas.items():
            if columna:
                _escribir_celda(
                    hoja, f'{columna}{numero}', str(fila.get(clave) or ''),
                )


def _crear_anexo_texto(wb, respuestas, registro, desbordes):
    """Hoja de anexo simple (una línea por fila) para textos desbordados."""
    from openpyxl.styles import Alignment, Font

    hoja = wb.create_sheet('SGC-FOR-009 (Anexo)')
    hoja.column_dimensions['A'].width = 110
    hoja['A1'] = (
        f'ANEXO DE CONTENIDO — MINUTA Nº {respuestas.get("reunion_no", "")}'
    )
    hoja['A1'].font = Font(bold=True, size=12)
    hoja['A2'] = f'Fecha: {_fecha_texto(registro.fecha)}'
    fila = 4
    for etiqueta, valor in desbordes:
        hoja.cell(fila, 1, etiqueta).font = Font(bold=True)
        fila += 1
        for parrafo in str(valor or '').split('\n'):
            lineas = textwrap.wrap(
                parrafo, width=110, break_long_words=False,
                replace_whitespace=False,
            ) or ['']
            for linea in lineas:
                hoja.cell(fila, 1, linea).alignment = Alignment(
                    vertical='top',
                )
                fila += 1
        fila += 1
    hoja.page_setup.orientation = 'portrait'
    return hoja


def generar_xlsx(registro):
    """Bytes del XLSX de la minuta (con hojas de continuación si hace falta)."""
    import openpyxl

    from .xlsx_comun import copiar_hoja

    formato = registro.formato if registro is not None else formato_for_009()
    if formato is None or not formato.plantilla_xlsx:
        raise ValueError(
            'El formato SGC-FOR-009 no está integrado o no tiene plantilla.'
        )
    respuestas = dict(registro.respuestas or {})
    config = config_asistentes(formato)
    filas = asistentes(respuestas)
    maximo = int(config.get('max_filas') or MAX_ASISTENTES_HOJA)
    trozos = [
        filas[i:i + maximo] for i in range(0, len(filas), maximo)
    ] or [[]]
    total = len(trozos)

    with formato.plantilla_xlsx.open('rb') as fh:
        plantilla = fh.read()
    wb = openpyxl.load_workbook(io.BytesIO(plantilla))
    base = wb[HOJA_FOR_009] if HOJA_FOR_009 in wb.sheetnames else wb.worksheets[0]

    hojas = [base]
    for indice in range(1, total):
        nueva = copiar_hoja(wb, base)
        nueva.title = f'{HOJA_FOR_009} ({indice + 1})'
        hojas.append(nueva)

    desbordes = []
    for indice, (hoja, trozo) in enumerate(zip(hojas, trozos), start=1):
        es_primera = indice == 1
        _escribir_celda(hoja, CELDAS['pagina'], f'PAGINA: {indice} DE {total}')
        _escribir_celda(
            hoja, CELDAS['reunion_no'], respuestas.get('reunion_no', ''),
        )
        _escribir_celda(hoja, CELDAS['fecha'], _fecha_texto(registro.fecha))
        _merge_si_falta(hoja, 'B10:C11')
        if es_primera:
            for clave, valor in (
                ('objetivo', respuestas.get('objetivo')),
                ('lugar', respuestas.get('lugar')),
                ('duracion', respuestas.get('duracion')),
            ):
                if not _escribir_campo(hoja, clave, valor):
                    desbordes.append((ETIQUETAS_CAMPO[clave], valor))
            if not _escribir_responsable(
                hoja, respuestas.get('responsable_elaboracion'),
            ):
                desbordes.append((
                    ETIQUETAS_CAMPO['responsable'],
                    respuestas.get('responsable_elaboracion'),
                ))
            for clave, etiqueta in (
                ('tema', ETIQUETAS['tema']),
                ('conclusiones', ETIQUETAS['conclusiones']),
                ('acuerdos_cerrados', ETIQUETAS['acuerdos_cerrados']),
                ('acuerdos_pendientes', ETIQUETAS['acuerdos_pendientes']),
                ('responsables', ETIQUETAS['responsables']),
            ):
                valor = (
                    respuestas.get('responsables_aprobacion')
                    if clave == 'responsables' else respuestas.get(clave)
                )
                if not _escribir_texto_bloque(
                    hoja, CELDAS[clave], etiqueta, valor,
                    RANGOS_TEXTO[clave],
                ):
                    desbordes.append((etiqueta, valor))
        else:
            # Hoja de continuación: solo identificación + asistentes.
            for clave in ('objetivo', 'lugar', 'responsable', 'duracion'):
                _escribir_celda(hoja, CELDAS[clave], '')
            for ref in ('A13', 'A16', 'A22'):
                _escribir_celda(hoja, ref, '')
            for clave in ('conclusiones', 'acuerdos_cerrados',
                          'acuerdos_pendientes', 'responsables'):
                _escribir_celda(hoja, CELDAS[clave], '')
            _escribir_celda(
                hoja, 'A33',
                f'Continuación de asistentes — Minuta Nº '
                f'{respuestas.get("reunion_no", "")}',
            )
        _escribir_asistentes(hoja, trozo, config)

    if desbordes:
        hojas.append(_crear_anexo_texto(wb, respuestas, registro, desbordes))

    orden = list(hojas) + [s for s in wb.worksheets if s not in hojas]
    wb._sheets = orden
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


def generar_pdf(registro):
    from .xlsx_comun import preparar_para_pdf, xlsx_bytes_a_pdf

    return xlsx_bytes_a_pdf(preparar_para_pdf(generar_xlsx(registro)))


def nombre_archivo(registro, extension='xlsx'):
    codigo = str((registro.respuestas or {}).get('reunion_no') or '')
    seguro = re.sub(r'[^A-Za-z0-9_-]+', '_', codigo).strip('_') or registro.uuid.hex[:8]
    return f'SGC-FOR-009_Minuta_{seguro}.{extension}'


def generar_y_guardar(registro, forzar=False):
    """Genera y guarda XLSX/PDF en el registro (idempotente)."""
    from django.core.files.base import ContentFile

    from .xlsx_comun import preparar_para_pdf, xlsx_bytes_a_pdf

    if not forzar and registro.archivo:
        return {
            'success': True, 'xlsx': registro.archivo.name,
            'pdf': registro.archivo_pdf.name or None, 'error': '',
        }
    try:
        xlsx = generar_xlsx(registro)
    except Exception as exc:  # noqa: BLE001
        logger.error('No se pudo generar el XLSX FOR-009: %s', exc)
        return {'success': False, 'xlsx': None, 'pdf': None, 'error': str(exc)}
    if forzar:
        for campo in ('archivo', 'archivo_pdf'):
            archivo = getattr(registro, campo)
            if archivo:
                try:
                    archivo.delete(save=False)
                except Exception:  # noqa: BLE001
                    pass
    registro.archivo.save(
        nombre_archivo(registro, 'xlsx'), ContentFile(xlsx), save=False,
    )
    registro.archivo_pdf = ''
    pdf = xlsx_bytes_a_pdf(preparar_para_pdf(xlsx))
    if pdf:
        registro.archivo_pdf.save(
            nombre_archivo(registro, 'pdf'), ContentFile(pdf), save=False,
        )
    registro.save(update_fields=['archivo', 'archivo_pdf', 'actualizado'])
    return {
        'success': True, 'xlsx': registro.archivo.name,
        'pdf': registro.archivo_pdf.name or None, 'error': '',
    }
