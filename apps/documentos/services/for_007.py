"""Programa de Revisión por la Dirección (SGC-FOR-007).

Registro vivo **por año** (``RegistroFormato`` con contexto
``programa_revision``/``<año>``). Los aspectos y su información documentada
son filas **N variables** (editables por año); las columnas de mes se marcan
con ``P`` (planificado) / ``V`` (ejecutado). Cada guardado crea una versión
inmutable (``VersionRegistroFormato``) con snapshot + hash.

El XLSX se genera desde la plantilla oficial, **reconstruyendo el bloque de
aspectos** para N filas (insertar/eliminar y desplazar leyenda/firmas), con
tope :data:`MAX_FILAS_HOJA` por hoja y hojas de continuación.
"""
import copy
import io
import logging
import math
from datetime import date, datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from .versiones import registrar_version

logger = logging.getLogger(__name__)

FORMATO_FOR_007 = 'SGC-FOR-007'
HOJA_FOR_007 = 'SGC-FOR-007'
CONTEXTO_TIPO = 'programa_revision'
MAX_FILAS_HOJA = 30

#: Filas de la plantilla oficial.
FILA_INICIO = 14
FILA_FIN = 27
FILA_LEYENDA = 29
FILA_CONFORME = 31
FILAS_FIRMA = (32, 33)
FILAS_ETIQUETA = (34, 35)

ESTADO_BORRADOR = 'borrador'
ESTADO_PUBLICADO = 'aprobado'  # se muestra como "Publicado"

MARCAS_VALIDAS = ('', 'P', 'V')


# ---------------------------------------------------------------------------
# Formato / config
# ---------------------------------------------------------------------------

def formato_for_007():
    from ..models import Formato

    return Formato.objects.filter(codigo=FORMATO_FOR_007, activo=True).first()


def campo_aspectos(formato=None):
    from ..models import CampoFormato

    formato = formato or formato_for_007()
    if formato is None:
        return None
    return CampoFormato.objects.filter(
        seccion__formato=formato, clave='aspectos',
    ).first()


def config_for_007(formato=None):
    campo = campo_aspectos(formato)
    return dict(campo.config_tabla or {}) if campo else {}


def meses(config):
    return list(config.get('meses') or [])


def _claves_meses(config):
    return [m.get('clave') for m in meses(config) if m.get('clave')]


def _responsable_por_defecto():
    from autenticacion.models import PerfilUsuario

    perfil = PerfilUsuario.objects.filter(
        rol='administrador', usuario__is_active=True,
    ).select_related('usuario').first()
    return perfil.usuario if perfil else None


# ---------------------------------------------------------------------------
# Registro anual y seed
# ---------------------------------------------------------------------------

def _aspecto(clave, etiqueta='', informacion='', marcas=None, meta=None):
    return {
        'clave': clave,
        'etiqueta': (etiqueta or '').strip(),
        'informacion': (informacion or '').strip(),
        'marcas': dict(marcas or {}),
        'meta': dict(meta or {}),
    }


def _seed_desde_config(config):
    return [
        _aspecto(
            item.get('clave') or f'seed_{i}',
            item.get('etiqueta', ''),
            item.get('informacion', ''),
        )
        for i, item in enumerate(config.get('seed') or [], start=1)
    ]


def aspecto(registro):
    return list((registro.respuestas or {}).get('aspectos') or [])


def anio_del_programa(registro):
    if registro is None:
        return None
    try:
        return int((registro.respuestas or {}).get('anio') or registro.contexto_id)
    except (TypeError, ValueError):
        return None


def anios_disponibles():
    from ..models import RegistroFormato

    formato = formato_for_007()
    if formato is None:
        return []
    anios = set()
    for valor in RegistroFormato.objects.filter(
        formato=formato, contexto_tipo=CONTEXTO_TIPO,
    ).values_list('contexto_id', flat=True):
        try:
            anios.add(int(valor))
        except (TypeError, ValueError):
            continue
    return sorted(anios, reverse=True)


def _programa_por_anio(anio):
    from ..models import RegistroFormato

    formato = formato_for_007()
    if formato is None:
        return None
    return RegistroFormato.objects.filter(
        formato=formato, contexto_tipo=CONTEXTO_TIPO,
        contexto_id=str(int(anio)),
    ).first()


def _aspectos_desde_programa(origen):
    if origen is None:
        return []
    return [
        _aspecto(
            item.get('clave') or f'asp_{i}',
            item.get('etiqueta', ''),
            item.get('informacion', ''),
        )
        for i, item in enumerate(aspecto(origen), start=1)
    ]


def programa_anual(anio, crear=False, usuario=None, seed_anio=None):
    """Registro del programa de ``anio`` (crea con seed si ``crear``)."""
    try:
        anio_int = int(anio)
    except (TypeError, ValueError):
        return None
    existente = _programa_por_anio(anio_int)
    if existente is not None or not crear:
        return existente

    formato = formato_for_007()
    if formato is None:
        return None
    config = config_for_007(formato)

    origen = None
    if seed_anio is not None:
        try:
            origen = _programa_por_anio(seed_anio)
        except (TypeError, ValueError):
            origen = None
    if origen is None:
        disponibles = [a for a in anios_disponibles() if a < anio_int]
        if disponibles:
            origen = _programa_por_anio(disponibles[0])
    aspectos = (_aspectos_desde_programa(origen)
                or _seed_desde_config(config))

    responsable = usuario or _responsable_por_defecto()
    if responsable is None:
        logger.warning('No hay administrador para crear el programa FOR-007.')
        return None

    from ..models import RegistroFormato

    try:
        with transaction.atomic():
            registro = RegistroFormato.objects.create(
                formato=formato,
                formato_codigo=formato.codigo,
                formato_version=formato.version,
                formato_edicion=formato.edicion,
                contexto_tipo=CONTEXTO_TIPO,
                contexto_id=str(anio_int),
                titulo=f'Programa de Revisión por la Dirección {anio_int}',
                responsable=responsable,
                estado=ESTADO_BORRADOR,
                respuestas={
                    'anio': anio_int,
                    'aspectos': aspectos,
                    'conforme': {'presidente': '', 'gerente': '', 'fecha': ''},
                },
            )
            origen_txt = (
                f' desde {anio_del_programa(origen)}' if origen is not None
                else ' (seed oficial)'
            )
            registrar_version(
                registro, usuario,
                resumen=f'Creación del programa {anio_int}{origen_txt}',
            )
    except IntegrityError:
        # Otra petición lo creó entre el chequeo y el insert.
        return _programa_por_anio(anio_int)
    return registro


# ---------------------------------------------------------------------------
# Guardado, marcas y seed desde otro año
# ---------------------------------------------------------------------------

def _marcas_limpias(marcas, claves):
    limpias = {}
    for clave in claves:
        valor = str((marcas or {}).get(clave) or '').strip().upper()
        if valor in ('P', 'V'):
            limpias[clave] = valor
    return limpias


def _normalizar_aspectos(entrantes, actuales, config, ahora):
    """Normaliza filas/marcas y actualiza ``meta`` (timestamps por mes)."""
    claves_meses = _claves_meses(config)
    actuales_por_clave = {a.get('clave'): a for a in actuales}
    resultado = []
    marcas_cambiadas = 0
    vistos = set()
    for i, fila in enumerate(entrantes or []):
        clave = str(fila.get('clave') or '').strip() or f'asp_{i}_{ahora:%Y%m%d%H%M%S}'
        while clave in vistos:
            clave = f'{clave}_x'
        vistos.add(clave)
        previo = actuales_por_clave.get(clave) or {}
        marcas_previas = previo.get('marcas') or {}
        meta_previa = dict(previo.get('meta') or {})
        marcas = _marcas_limpias(fila.get('marcas'), claves_meses)
        meta = {}
        for mes in claves_meses:
            nuevo = marcas.get(mes, '')
            anterior = str(marcas_previas.get(mes) or '')
            info_mes = dict(meta_previa.get(mes) or {})
            if nuevo != anterior:
                marcas_cambiadas += 1
            if nuevo == 'V':
                if not info_mes.get('planificado_en'):
                    info_mes['planificado_en'] = ahora.isoformat()
                if anterior != 'V' or not info_mes.get('ejecutado_en'):
                    info_mes['ejecutado_en'] = ahora.isoformat()
            elif nuevo == 'P':
                if not info_mes.get('planificado_en'):
                    info_mes['planificado_en'] = ahora.isoformat()
                info_mes.pop('ejecutado_en', None)
            else:
                info_mes = {}
            if info_mes:
                meta[mes] = info_mes
        resultado.append(_aspecto(
            clave,
            str(fila.get('etiqueta') or '').strip(),
            str(fila.get('informacion') or '').strip(),
            marcas,
            meta,
        ))
    return resultado, marcas_cambiadas


def _mismo_contenido(a, b):
    def _clave(item):
        return (
            item.get('clave'), item.get('etiqueta'), item.get('informacion'),
            tuple(sorted((item.get('marcas') or {}).items())),
            tuple(sorted(
                (k, tuple(sorted((m or {}).items())))
                for k, m in (item.get('meta') or {}).items()
            )),
        )

    return [_clave(x) for x in a] == [_clave(x) for x in b]


def _normalizar_conforme(conforme, actual=None):
    actual = dict(actual or {})
    conforme = dict(conforme or {})
    fecha = conforme.get('fecha', actual.get('fecha', ''))
    if isinstance(fecha, (date, datetime)):
        fecha = fecha.isoformat()[:10]
    return {
        'presidente': str(conforme.get(
            'presidente', actual.get('presidente', ''),
        ) or '').strip(),
        'gerente': str(conforme.get(
            'gerente', actual.get('gerente', ''),
        ) or '').strip(),
        'fecha': str(fecha or '').strip(),
    }


def _guardar_programa_bloqueado(bloqueado, aspectos, conforme=None,
                                usuario=None, resumen='', config=None):
    """Guarda sobre un registro YA bloqueado (``select_for_update``).

    Es el punto único de escritura de aspectos/marcas/Conforme. Quien llame
    debe tener el lock del registro en la misma transacción.
    """
    respuestas = dict(bloqueado.respuestas or {})
    actuales = list(respuestas.get('aspectos') or [])
    config = config or config_for_007(bloqueado.formato)
    ahora = timezone.now()
    nuevas, marcas_cambiadas = _normalizar_aspectos(
        aspectos, actuales, config, ahora,
    )
    nuevo_conforme = _normalizar_conforme(
        conforme, respuestas.get('conforme'),
    )
    sin_cambios = (
        _mismo_contenido(actuales, nuevas)
        and (respuestas.get('conforme') or {}) == nuevo_conforme
    )
    if sin_cambios:
        return {'cambio': False, 'marcas': 0, 'version': None}

    respuestas['aspectos'] = nuevas
    respuestas['conforme'] = nuevo_conforme
    respuestas.setdefault('anio', anio_del_programa(bloqueado))
    bloqueado.respuestas = respuestas
    # El XLSX/PDF guardado queda desactualizado: se invalida para que la
    # próxima descarga/regeneración refleje el estado vigente.
    for campo_archivo in ('archivo', 'archivo_pdf'):
        archivo = getattr(bloqueado, campo_archivo)
        if archivo:
            try:
                archivo.delete(save=False)
            except Exception:  # noqa: BLE001
                pass
    bloqueado.archivo = ''
    bloqueado.archivo_pdf = ''
    campos = ['respuestas', 'archivo', 'archivo_pdf', 'actualizado']
    if bloqueado.estado == ESTADO_PUBLICADO:
        bloqueado.estado = ESTADO_BORRADOR
        campos.append('estado')
    bloqueado.save(update_fields=campos)
    version = registrar_version(
        bloqueado, usuario,
        resumen=(resumen or f'Guardado ({marcas_cambiadas} marca(s))'),
    )
    return {
        'cambio': True, 'marcas': marcas_cambiadas, 'version': version.numero,
    }


def guardar_programa(registro, aspectos, conforme=None, usuario=None,
                     resumen=''):
    """Guarda filas/marcas/Conforme y crea versión si hubo cambios.

    Returns:
        dict: ``{'cambio': bool, 'marcas': n, 'version': n|None}``.
    """
    from ..models import RegistroFormato

    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(
            pk=registro.pk,
        )
        resultado = _guardar_programa_bloqueado(
            bloqueado, aspectos, conforme=conforme, usuario=usuario,
            resumen=resumen,
        )
        if resultado['cambio']:
            registro.respuestas = bloqueado.respuestas
            registro.estado = bloqueado.estado
    return resultado


def publicar_programa(registro, usuario=None):
    """Marca el programa como publicado (crea versión). Idempotente."""
    from ..models import RegistroFormato

    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(
            pk=registro.pk,
        )
        if bloqueado.estado == ESTADO_PUBLICADO:
            return None
        bloqueado.estado = ESTADO_PUBLICADO
        bloqueado.save(update_fields=['estado', 'actualizado'])
        version = registrar_version(bloqueado, usuario, resumen='Publicación')
        registro.estado = bloqueado.estado
    return version


def cargar_seed(registro, anio_origen, usuario=None):
    """Reemplaza los aspectos por los de ``anio_origen`` (conserva marcas).

    Las marcas/meta se conservan para las filas con la misma ``clave``; las
    nuevas empiezan vacías. Crea versión y, si estaba publicado, borrador.
    """
    origen = _programa_por_anio(anio_origen)
    if origen is None:
        return {'cambio': False, 'error': 'El año de origen no existe.'}
    if origen.pk == registro.pk:
        return {'cambio': False, 'error': 'No puedes copiar el mismo año.'}

    config = config_for_007(registro.formato)
    claves_meses = _claves_meses(config)
    actuales = {a.get('clave'): a for a in aspecto(registro)}
    nuevas = []
    for i, item in enumerate(aspecto(origen), start=1):
        clave = item.get('clave') or f'asp_{i}'
        previo = actuales.get(clave) or {}
        nuevas.append(_aspecto(
            clave,
            item.get('etiqueta', ''),
            item.get('informacion', ''),
            {m: v for m, v in (previo.get('marcas') or {}).items()
             if m in claves_meses},
            previo.get('meta') or {},
        ))
    resultado = guardar_programa(
        registro, nuevas, usuario=usuario,
        resumen=f'Copia de aspectos de {anio_origen}',
    )
    resultado['error'] = ''
    return resultado


def marcar_ejecutado(anio, mes, usuario=None):
    """Hook (FOR-009): marca como ``V`` los aspectos planificados del mes.

    Lee y transforma la lista bajo el lock del programa, de modo que un guardado
    concurrente del FOR-007 no se pierda. El lock se mantiene hasta el commit.
    """
    from ..models import RegistroFormato
    from . import acceso_formato

    formato = formato_for_007()
    if formato is None:
        return 0
    try:
        anio_int = int(anio)
    except (TypeError, ValueError):
        return 0
    if usuario is not None and not acceso_formato.puede_editar(usuario, formato):
        raise PermissionError(
            'No tienes permiso de edición en el Programa de Revisión.',
        )
    mes = str(mes or '').strip().upper()
    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().filter(
            formato=formato, contexto_tipo=CONTEXTO_TIPO,
            contexto_id=str(anio_int),
        ).first()
        if bloqueado is None:
            return 0
        aspectos = aspecto(bloqueado)
        tocados = 0
        for item in aspectos:
            if (item.get('marcas') or {}).get(mes) == 'P':
                item.setdefault('marcas', {})[mes] = 'V'
                tocados += 1
        if not tocados:
            return 0
        _guardar_programa_bloqueado(
            bloqueado, aspectos, usuario=usuario,
            resumen=f'Ejecución de {mes} ({tocados} aspecto(s))',
        )
    return tocados


# ---------------------------------------------------------------------------
# Export XLSX (layout dinámico)
# ---------------------------------------------------------------------------

def _anio_texto(anio):
    return f'AÑO A EVALUAR: {anio}' if anio else 'AÑO A EVALUAR:'


def _fecha_partes(valor):
    texto = str(valor or '').strip()
    if not texto:
        return '', '', ''
    try:
        fecha = date.fromisoformat(texto[:10])
        return f'{fecha.day:02d}', f'{fecha.month:02d}', f'{fecha.year}'
    except ValueError:
        partes = texto.split('/')
        if len(partes) == 3:
            return partes[0], partes[1], partes[2]
    return texto, '', ''


def _capturar_prototipos(hoja):
    from openpyxl.styles import Border, PatternFill  # noqa: F401

    estilo_fila = {
        col: copy.copy(hoja.cell(row=FILA_INICIO, column=col)._style)
        for col in range(1, 20)
    }
    estilos_firma = {
        fila: {
            col: copy.copy(hoja.cell(row=fila, column=col)._style)
            for col in range(1, 15)
        }
        for fila in (*FILAS_FIRMA, *FILAS_ETIQUETA)
    }
    return {
        'estilo_fila': estilo_fila,
        'estilo_leyenda': copy.copy(hoja.cell(row=FILA_LEYENDA, column=2)._style),
        'estilo_conforme': copy.copy(hoja.cell(row=FILA_CONFORME, column=1)._style),
        'estilos_firma': estilos_firma,
        'texto_leyenda': hoja.cell(row=FILA_LEYENDA, column=2).value
                         or 'Ï: Planificado;  V: Ejecutado',
        'texto_conforme': hoja.cell(row=FILA_CONFORME, column=1).value
                          or 'CONFORME',
        'etiquetas': (
            hoja.cell(row=FILAS_ETIQUETA[0], column=1).value
            or 'Presidente de la Junta Directiva',
            hoja.cell(row=FILAS_ETIQUETA[0], column=5).value
            or 'Gerencia General',
            hoja.cell(row=FILAS_ETIQUETA[0], column=9).value or 'Fecha',
        ),
    }


def _limpiar_desde(hoja, desde):
    from openpyxl.styles import Border, PatternFill

    for rango in list(hoja.merged_cells.ranges):
        if rango.max_row >= desde:
            hoja.unmerge_cells(str(rango))
    for fila in range(desde, hoja.max_row + 1):
        for col in range(1, 20):
            celda = hoja.cell(row=fila, column=col)
            celda.value = None
            celda.border = Border()
            celda.fill = PatternFill(fill_type=None)
        hoja.row_dimensions[fila].height = None


def _aplicar_estilo(celda, estilo):
    celda._style = copy.copy(estilo)


def _escribir_aspectos(hoja, filas, prototipos, config):
    claves_meses = [m for m in meses(config) if m.get('celda')]
    for offset, item in enumerate(filas):
        fila = FILA_INICIO + offset
        for col in range(1, 20):
            _aplicar_estilo(
                hoja.cell(row=fila, column=col),
                prototipos['estilo_fila'][col],
            )
        hoja[f'A{fila}'] = item.get('etiqueta', '')
        hoja[f'E{fila}'] = item.get('informacion', '')
        marcas = item.get('marcas') or {}
        for mes in claves_meses:
            hoja[f'{mes["celda"]}{fila}'] = marcas.get(mes['clave'], '')
        for rango in (f'A{fila}:D{fila}', f'E{fila}:G{fila}'):
            hoja.merge_cells(rango)
        hoja.row_dimensions[fila].height = 30


def _escribir_pie(hoja, ultima_fila_aspecto, conforme, prototipos, config,
                  numero_hoja, total_hojas):
    from openpyxl.styles import Alignment, Font

    leyenda_fila = ultima_fila_aspecto + 2
    celda_leyenda = hoja.cell(row=leyenda_fila, column=2)
    _aplicar_estilo(celda_leyenda, prototipos['estilo_leyenda'])
    celda_leyenda.value = prototipos['texto_leyenda']

    conforme_fila = leyenda_fila + 2
    inicio = conforme_fila
    for col in range(1, 15):
        _aplicar_estilo(
            hoja.cell(row=inicio, column=col), prototipos['estilo_conforme'],
        )
    hoja.cell(row=inicio, column=1).value = prototipos['texto_conforme']
    hoja.merge_cells(f'A{inicio}:N{inicio}')

    # Filas de firma (2) y de etiquetas (2), desplazadas.
    for fila_proto in (*FILAS_FIRMA, *FILAS_ETIQUETA):
        fila = inicio + (fila_proto - FILA_CONFORME)
        for col in range(1, 15):
            _aplicar_estilo(
                hoja.cell(row=fila, column=col),
                prototipos['estilos_firma'][fila_proto][col],
            )
    sig = inicio + 1
    lab = inicio + 3
    conf = conforme or {}
    hoja[f'A{sig}'] = conf.get('presidente', '')
    hoja[f'E{sig}'] = conf.get('gerente', '')
    dia, mes, anio = _fecha_partes(conf.get('fecha'))
    hoja[f'I{sig}'] = dia
    hoja[f'K{sig}'] = mes
    hoja[f'M{sig}'] = anio
    etiquetas = prototipos['etiquetas']
    hoja[f'A{lab}'] = etiquetas[0]
    hoja[f'E{lab}'] = etiquetas[1]
    hoja[f'I{lab}'] = etiquetas[2]
    for rango in (
        f'A{sig}:D{sig + 1}', f'E{sig}:H{sig + 1}', f'I{sig}:J{sig + 1}',
        f'K{sig}:L{sig + 1}', f'M{sig}:N{sig + 1}',
        f'A{lab}:D{lab + 1}', f'E{lab}:H{lab + 1}', f'I{lab}:N{lab + 1}',
    ):
        hoja.merge_cells(rango)
    for celda in (hoja[f'A{sig}'], hoja[f'E{sig}'], hoja[f'I{sig}'],
                  hoja[f'K{sig}'], hoja[f'M{sig}']):
        celda.alignment = Alignment(horizontal='center', vertical='center')
        celda.font = Font(size=9)


def generar_xlsx(registro):
    """Bytes del XLSX del programa (hoja(s) según N, con continuación)."""
    import openpyxl

    from .xlsx_comun import copiar_hoja

    formato = registro.formato if registro is not None else formato_for_007()
    if formato is None or not formato.plantilla_xlsx:
        raise ValueError(
            'El formato SGC-FOR-007 no está integrado o no tiene plantilla.'
        )
    config = config_for_007(formato)
    maximo = int(config.get('max_filas_hoja') or MAX_FILAS_HOJA)
    filas = aspecto(registro)
    anio = anio_del_programa(registro)
    conforme = (registro.respuestas or {}).get('conforme') or {}

    with formato.plantilla_xlsx.open('rb') as fh:
        plantilla = fh.read()
    wb = openpyxl.load_workbook(io.BytesIO(plantilla))
    base = wb[HOJA_FOR_007] if HOJA_FOR_007 in wb.sheetnames else wb.worksheets[0]
    prototipos = _capturar_prototipos(base)

    trozos = [
        filas[i:i + maximo] for i in range(0, len(filas), maximo)
    ] or [[]]
    total = len(trozos)

    hojas = [base]
    for indice in range(1, total):
        nueva = copiar_hoja(wb, base)
        nueva.title = f'{HOJA_FOR_007} ({indice + 1})'
        hojas.append(nueva)

    for indice, (hoja, trozo) in enumerate(zip(hojas, trozos), start=1):
        _limpiar_desde(hoja, FILA_INICIO)
        hoja[config.get('celdas', {}).get('pagina', 'L6')] = (
            f'PAGINA:   {indice} DE {total}'
        )
        hoja[config.get('celdas', {}).get('anio', 'A10')] = _anio_texto(anio)
        try:
            hoja.print_title_rows = '1:13'
        except Exception:  # noqa: BLE001
            pass
        _escribir_aspectos(hoja, trozo, prototipos, config)
        if indice == total:
            ultima = FILA_INICIO + max(1, len(trozo)) - 1
            _escribir_pie(
                hoja, ultima, conforme, prototipos, config, indice, total,
            )

    # Ordenar hojas: programa(s) y luego el instructivo.
    orden = list(hojas) + [s for s in wb.worksheets if s not in hojas]
    wb._sheets = orden
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


def generar_pdf(registro):
    from .xlsx_comun import xlsx_bytes_a_pdf

    return xlsx_bytes_a_pdf(generar_xlsx(registro))


def nombre_archivo(registro, extension='xlsx'):
    anio = anio_del_programa(registro) or 'programa'
    return f'SGC-FOR-007_Programa_{anio}.{extension}'


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
        logger.error('No se pudo generar el XLSX FOR-007: %s', exc)
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
