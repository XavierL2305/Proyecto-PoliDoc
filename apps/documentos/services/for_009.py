"""Minuta de reunión (SGC-FOR-009).

La captura usa el **motor dinámico** (``Formato`` + ``RegistroFormato``); este
servicio concentra el alta/guardado, la validación del Nº de reunión, el
prefijo de responsables y la **exportación XLSX/PDF** con la plantilla oficial
(hoja ``SGC-FOR-009`` + instructivo ``SGC-IDL-015`` intacto).

La minuta sirve para cualquier reunión del SGC: ``tipo_reunion='revision'``
(Revisión por la Dirección, puede enlazarse al FOR-007) u ``'otra'`` (no
planificada, Nº ``XXX - NPR``).
"""
import io
import logging
import re
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
    'responsable': 'B20',
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
    'responsable': 'B20:H20',
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


def anio_mes(respuestas, fecha):
    """Año/mes (clave ENE..DIC) para marcar el programa FOR-007."""
    respuestas = respuestas or {}
    anio = respuestas.get('anio') or (fecha.year if fecha else None)
    try:
        anio = int(anio)
    except (TypeError, ValueError):
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
    return registro


def guardar_minuta(registro, respuestas, *, fecha=None, usuario=None):
    """Actualiza respuestas/fecha/título e invalida el XLSX/PDF guardado."""
    from ..models import RegistroFormato

    respuestas = dict(respuestas or {})
    if respuestas.get('reunion_no'):
        respuestas['reunion_no'] = validar_reunion_no(
            respuestas.get('reunion_no'),
        )
    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(
            pk=registro.pk,
        )
        if fecha is not None:
            bloqueado.fecha = fecha
        bloqueado.respuestas = respuestas
        bloqueado.titulo = _titulo(respuestas, bloqueado.fecha)
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
    if rango not in {str(r) for r in hoja.merged_cells.ranges}:
        hoja.merge_cells(rango)


def _escribir_texto_bloque(hoja, celda, etiqueta, valor):
    from openpyxl.styles import Alignment

    texto = f'{etiqueta}\n{valor}' if valor else etiqueta
    hoja[celda] = texto
    hoja[celda].alignment = Alignment(
        horizontal='left', vertical='top', wrap_text=True,
    )


def _escribir_campo(hoja, clave, valor):
    from openpyxl.styles import Alignment

    celda = CELDAS[clave]
    hoja[celda] = valor or ''
    hoja[celda].alignment = Alignment(
        horizontal='left', vertical='center', wrap_text=True,
    )
    if clave in MERGES:
        _merge_si_falta(hoja, MERGES[clave])


def _escribir_asistentes(hoja, filas, config):
    columnas = {c.get('clave'): c.get('celda')
                for c in (config.get('columnas') or [])}
    inicio = int(config.get('fila_inicio') or 26)
    maximo = int(config.get('max_filas') or MAX_ASISTENTES_HOJA)
    for offset, fila in enumerate(filas[:maximo]):
        numero = inicio + offset
        for clave, columna in columnas.items():
            if columna:
                hoja[f'{columna}{numero}'] = str(fila.get(clave) or '')


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

    for indice, (hoja, trozo) in enumerate(zip(hojas, trozos), start=1):
        es_primera = indice == 1
        hoja[CELDAS['pagina']] = f'PAGINA: {indice} DE {total}'
        hoja[CELDAS['reunion_no']] = respuestas.get('reunion_no', '')
        hoja[CELDAS['fecha']] = _fecha_texto(registro.fecha)
        _merge_si_falta(hoja, 'B10:C11')
        if es_primera:
            _escribir_campo(hoja, 'objetivo', respuestas.get('objetivo'))
            _escribir_campo(hoja, 'lugar', respuestas.get('lugar'))
            _escribir_campo(hoja, 'responsable',
                            respuestas.get('responsable_elaboracion'))
            _escribir_campo(hoja, 'duracion', respuestas.get('duracion'))
            _escribir_texto_bloque(
                hoja, CELDAS['tema'], ETIQUETAS['tema'],
                respuestas.get('tema'),
            )
            _escribir_texto_bloque(
                hoja, CELDAS['conclusiones'], ETIQUETAS['conclusiones'],
                respuestas.get('conclusiones'),
            )
            _escribir_texto_bloque(
                hoja, CELDAS['acuerdos_cerrados'],
                ETIQUETAS['acuerdos_cerrados'],
                respuestas.get('acuerdos_cerrados'),
            )
            _escribir_texto_bloque(
                hoja, CELDAS['acuerdos_pendientes'],
                ETIQUETAS['acuerdos_pendientes'],
                respuestas.get('acuerdos_pendientes'),
            )
            _escribir_texto_bloque(
                hoja, CELDAS['responsables'], ETIQUETAS['responsables'],
                respuestas.get('responsables_aprobacion'),
            )
            hoja.row_dimensions[65].height = 45
        else:
            # Hoja de continuación: solo identificación + asistentes.
            for clave in ('objetivo', 'lugar', 'responsable', 'duracion'):
                hoja[CELDAS[clave]] = ''
            for ref in ('A13', 'A16', 'A20', 'A22'):
                hoja[ref] = ''
            for clave in ('conclusiones', 'acuerdos_cerrados',
                          'acuerdos_pendientes', 'responsables'):
                hoja[CELDAS[clave]] = ''
            hoja['A33'] = (
                f'Continuación de asistentes — Minuta Nº '
                f'{respuestas.get("reunion_no", "")}'
            )
        _escribir_asistentes(hoja, trozo, config)

    orden = list(hojas) + [s for s in wb.worksheets if s not in hojas]
    wb._sheets = orden
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


def generar_pdf(registro):
    from .xlsx_comun import xlsx_bytes_a_pdf

    return xlsx_bytes_a_pdf(generar_xlsx(registro))


def nombre_archivo(registro, extension='xlsx'):
    codigo = str((registro.respuestas or {}).get('reunion_no') or '')
    seguro = re.sub(r'[^A-Za-z0-9_-]+', '_', codigo).strip('_') or registro.uuid.hex[:8]
    return f'SGC-FOR-009_Minuta_{seguro}.{extension}'


def generar_y_guardar(registro, forzar=False):
    """Genera y guarda XLSX/PDF en el registro (idempotente)."""
    from django.core.files.base import ContentFile

    from .xlsx_comun import xlsx_bytes_a_pdf

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
    pdf = xlsx_bytes_a_pdf(xlsx)
    if pdf:
        registro.archivo_pdf.save(
            nombre_archivo(registro, 'pdf'), ContentFile(pdf), save=False,
        )
    registro.save(update_fields=['archivo', 'archivo_pdf', 'actualizado'])
    return {
        'success': True, 'xlsx': registro.archivo.name,
        'pdf': registro.archivo_pdf.name or None, 'error': '',
    }
