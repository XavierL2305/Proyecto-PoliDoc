"""Generación y almacenamiento de planillas (SGC-FOR-001) rellenadas.

Cuando se aprueba una solicitud de **creación** o **eliminación**, el sistema
rellena el formato con las respuestas de la solicitud y guarda el XLSX (+ el
PDF, si LibreOffice está disponible) en
``media/solicitudes_cambio/planillas/AAAA/MM/``, registrando la ruta en
``SolicitudCambio.planilla_xlsx`` y ``SolicitudCambio.planilla_pdf``.

La generación es **asíncrona** (Celery, publicada con ``transaction.on_commit``
para que el worker vea la solicitud ya aprobada) e **idempotente**: repetir la
operación no duplica archivos. Si el broker no responde, se degrada a
generación en línea y, en último extremo, las descargas siguen exportando al
vuelo.

Las descargas de la planilla (XLSX/PDF) persisten el resultado en esos mismos
campos con :func:`guardar_planilla_solicitud`, que también cubre las
operaciones sin planilla automática (`modificacion`).
"""

import logging
import re

from django.db import transaction

logger = logging.getLogger(__name__)

#: SolicitudCambio.operacion para las que existe planilla SGC-FOR-001.
OPERACIONES_CON_PLANILLA = ('creacion', 'eliminacion')

#: Política de publicación: falla rápido si el broker no responde.
_RETRY_POLICY = {
    'max_retries': 1,
    'interval_start': 0,
    'interval_step': 0,
    'interval_max': 0,
}


def generar_planilla_solicitud(solicitud, force=False):
    """Genera y guarda el XLSX (+ PDF) de una solicitud de cambio.

    Idempotente: si la solicitud ya tiene ambos archivos y no se pasa
    ``force=True``, no hace nada. Nunca lanza excepciones: devuelve
    ``{'success': bool, 'xlsx': str|None, 'pdf': str|None, 'error': str}``.

    Args:
        solicitud: instancia de ``SolicitudCambio``.
        force: regenera los archivos (borra antes los guardados).
    """
    if solicitud.operacion not in OPERACIONES_CON_PLANILLA:
        return {
            'success': False,
            'error': f"Operación '{solicitud.operacion}' sin planilla SGC-FOR-001",
        }
    if not solicitud.formato or not solicitud.formato.plantilla_xlsx:
        return {'success': False, 'error': 'El formato no tiene plantilla XLSX'}

    tiene_xlsx = bool(solicitud.planilla_xlsx)
    tiene_pdf = bool(solicitud.planilla_pdf)

    if force:
        _limpiar_planillas(solicitud)
        tiene_xlsx = tiene_pdf = False

    if tiene_xlsx and tiene_pdf:
        return {
            'success': True,
            'xlsx': solicitud.planilla_xlsx.name,
            'pdf': solicitud.planilla_pdf.name,
            'ya_generada': True,
        }

    if not tiene_xlsx and not _guardar_xlsx(solicitud):
        return {'success': False, 'error': 'No se pudo generar el XLSX de la planilla'}

    pdf = solicitud.planilla_pdf.name or None
    if not pdf:
        pdf = _guardar_pdf(solicitud)

    return {
        'success': True,
        'xlsx': solicitud.planilla_xlsx.name,
        'pdf': pdf,
    }


def guardar_planilla_solicitud(solicitud, forzar=False, con_pdf=True):
    """Genera y **persiste** la planilla de una solicitud para su descarga.

    A diferencia de :func:`generar_planilla_solicitud` no restringe la
    operación: sirve también para `modificacion` (el botón XLSX/PDF de la
    planilla exportaba siempre al vuelo, sin cachear nada). Idempotente salvo
    ``forzar=True``. Nunca lanza.

    Args:
        solicitud: instancia de ``SolicitudCambio``.
        forzar: regenera aunque ya existan archivos (borrador: sus respuestas
            pueden haber cambiado desde la última descarga).
        con_pdf: solo el XLSX (lo usa la descarga de XLSX para no exigir
            LibreOffice cuando el usuario no pide el PDF).

    Returns:
        ``{'success': bool, 'xlsx': str|None, 'pdf': str|None, 'error': str}``
        Donde ``success`` significa que el XLSX está persistido.
    """
    if not solicitud.formato or not solicitud.formato.plantilla_xlsx:
        return {'success': False, 'xlsx': None, 'pdf': None,
                'error': 'El formato no tiene plantilla XLSX'}

    if forzar:
        _limpiar_planillas(solicitud)

    if not solicitud.planilla_xlsx and not _guardar_xlsx(solicitud):
        return {'success': False, 'xlsx': None, 'pdf': None,
                'error': 'No se pudo generar el XLSX de la planilla'}

    if con_pdf and not solicitud.planilla_pdf:
        _guardar_pdf(solicitud)

    return {
        'success': True,
        'xlsx': solicitud.planilla_xlsx.name or None,
        'pdf': solicitud.planilla_pdf.name or None,
    }


def encolar_generacion_planilla(solicitud):
    """Encola la generación de la planilla de una solicitud aprobada.

    La publicación se difiere con ``transaction.on_commit`` para que el worker
    lea la solicitud ya en estado ``aprobado``. Nunca lanza: si el broker no
    responde se intenta generar en línea y, si tampoco es posible, se registra
    el error sin romper la aprobación.
    """
    solicitud_uuid = str(solicitud.uuid)

    def _encolar():
        from ..tasks import generar_planilla_solicitud_task
        try:
            generar_planilla_solicitud_task.apply_async(
                args=[solicitud_uuid],
                ignore_result=True,
                retry=False,
                retry_policy=_RETRY_POLICY,
            )
            return
        except Exception as exc:
            logger.error(
                'No se pudo encolar la generación de planilla de %s: %s',
                solicitud_uuid, exc,
            )
        _generar_en_linea(solicitud_uuid)

    transaction.on_commit(_encolar)
    return solicitud_uuid


def _generar_en_linea(solicitud_uuid):
    """Genera la planilla en línea (fallback cuando el broker no responde)."""
    try:
        from ..models import SolicitudCambio
        solicitud = SolicitudCambio.objects.filter(uuid=solicitud_uuid).first()
        if not solicitud:
            return
        resultado = generar_planilla_solicitud(solicitud)
        if not resultado.get('success'):
            logger.error(
                'Generación en línea de planilla %s falló: %s',
                solicitud_codigo(solicitud), resultado.get('error'),
            )
    except Exception as exc:
        logger.error('Generación en línea de planilla %s: %s', solicitud_uuid, exc)


def solicitud_codigo(solicitud):
    return getattr(solicitud, 'codigo', str(solicitud))


def _guardar_xlsx(solicitud):
    """Exporta la planilla a XLSX y la guarda en el FileField. Devuelve bool."""
    from django.core.files.base import ContentFile
    from .exportacion import exportar_xlsx

    try:
        contenido = exportar_xlsx(solicitud)
    except Exception as exc:
        logger.error('Error exportando XLSX de %s: %s', solicitud_codigo(solicitud), exc)
        return False

    solicitud.planilla_xlsx.save(
        _nombre_archivo(solicitud, 'xlsx'), ContentFile(contenido), save=True,
    )
    logger.info('Planilla XLSX guardada: %s', solicitud.planilla_xlsx.name)
    return True


def _guardar_pdf(solicitud):
    """Convierte el XLSX guardado a PDF y registra la ruta. Devuelve la ruta o None."""
    from ..tasks import convertir_a_pdf_sync

    if not solicitud.planilla_xlsx:
        return None
    try:
        ruta = convertir_a_pdf_sync(
            solicitud.planilla_xlsx.path, reparar_sincrono=True,
        )
    except Exception as exc:
        logger.error('Error convirtiendo planilla de %s: %s', solicitud_codigo(solicitud), exc)
        return None
    if not ruta:
        logger.warning(
            'No se pudo generar el PDF de %s (¿LibreOffice disponible?)',
            solicitud_codigo(solicitud),
        )
        return None

    solicitud.planilla_pdf = ruta
    solicitud.save(update_fields=['planilla_pdf'])
    logger.info('Planilla PDF guardada: %s', ruta)
    return ruta


def _limpiar_planillas(solicitud):
    """Elimina de disco los archivos de planilla ya guardados (regeneración)."""
    for campo in ('planilla_xlsx', 'planilla_pdf'):
        archivo = getattr(solicitud, campo)
        if not archivo:
            continue
        try:
            archivo.delete(save=True)
        except Exception as exc:
            logger.warning('No se pudo borrar %s de %s: %s', campo, solicitud_codigo(solicitud), exc)


def invalidar_planillas_documento(documento):
    """Descarta las planillas SGC-FOR-001 guardadas de un documento.

    Los campos auto de la planilla (vigencia, versión, título, operación…)
    se recalculan al mostrar/exportar, pero los archivos persistidos son una
    instantánea: se limpian aquí para que la siguiente descarga o la tarea de
    barrido los regeneren con los valores actuales.

    Devuelve el número de solicitudes afectadas.
    """
    from ..models import SolicitudCambio

    if not documento:
        return 0
    afectadas = 0
    solicitudes = SolicitudCambio.objects.filter(
        documento=documento, operacion__in=OPERACIONES_CON_PLANILLA,
    )
    for solicitud in solicitudes:
        if not solicitud.planilla_xlsx and not solicitud.planilla_pdf:
            continue
        _limpiar_planillas(solicitud)
        afectadas += 1
    if afectadas:
        logger.info(
            'Planillas de %s invalidadas para regeneración: %d',
            documento.codigo, afectadas,
        )
    return afectadas


def _nombre_archivo(solicitud, extension):
    """Nombre estable para el archivo guardado: ``{codigo}_{operacion}.{ext}``."""
    base = re.sub(r'[^A-Za-z0-9_-]+', '_', solicitud.codigo).strip('_')
    base = base or f'solicitud-{solicitud.pk}'
    return f'{base}_{solicitud.operacion}.{extension}'


def aplicar_planilla_al_documento(planilla, usuario=None):
    """Vuelca una ``PlanillaCambios`` aprobada sobre su documento.

    Único punto de aplicación de cambios (lo usan la vista web y la API para no
    divergir): nombre, descripción, archivo, participantes, ``afecta_todos``,
    departamentos/responsables/documentos relacionados y **clasificación**
    (si ``clasificacion_propuesta`` no está vacía y difiere de la actual).
    Sube la versión, crea el ``VersionDocumento`` y deja el documento activo.

    Devuelve ``(documento, cambios)`` donde ``cambios`` describe la
    clasificación para la auditoría (``clasificacion_anterior`` /
    ``clasificacion_nueva``), o ``(None, {})`` si la planilla no tiene
    documento. No audita ni notifica: eso lo hace quien llama.

    La **entrada en vigor no se reinicia**: aplicar una modificación cambia
    versión/título/etc. pero no debe mover ``fecha_vigencia`` del documento.
    Sí invalida las planillas guardadas, cuyos campos auto quedan viejos.
    """
    from ..models import Documento, ReferenciaDocumento, VersionDocumento

    documento = planilla.documento if planilla.documento_id else None
    if documento is None:
        return None, {}

    clasificacion_anterior = documento.clasificacion

    if planilla.nombre_propuesto:
        documento.nombre = planilla.nombre_propuesto
    if planilla.descripcion_propuesta is not None:
        documento.descripcion = planilla.descripcion_propuesta
    if planilla.archivo_propuesto:
        documento.archivo = planilla.archivo_propuesto
    if planilla.participantes_propuestos:
        documento.participantes = planilla.participantes_propuestos

    clasificacion_nueva = planilla.clasificacion_propuesta or clasificacion_anterior
    documento.clasificacion = clasificacion_nueva

    nueva_version = documento.proxima_version()
    documento.version_actual = nueva_version
    documento.estado = 'activo'
    documento.afecta_todos = planilla.afecta_todos
    documento.save()

    documento.responsables.set(planilla.responsables_propuestos.all())
    documento.departamentos_afectados.set(planilla.departamentos_afectados.all())
    asociados = [
        da.documento for da in planilla.documentos_asociados.all() if da.documento_id
    ]
    documento.documentos_relacionados.set(asociados)

    # Referencias pendientes (códigos aún inexistentes): si el destino ya
    # existe y es accesible se enlaza; si no, queda como referencia a resolver.
    for codigo in (planilla.referencias_pendientes or []):
        codigo = str(codigo).strip().upper()
        if not codigo:
            continue
        destino = Documento.objects.filter(codigo=codigo).first()
        accesible = destino and (
            destino.clasificacion == 'publico'
            or destino.departamento_id == documento.departamento_id
        )
        if accesible:
            documento.documentos_relacionados.add(destino)
        else:
            ReferenciaDocumento.objects.get_or_create(
                documento=documento, codigo_destino=codigo,
                defaults={'creado_por': usuario or planilla.responsable},
            )

    VersionDocumento.objects.create(
        documento=documento,
        numero_version=nueva_version,
        archivo=planilla.archivo_propuesto if planilla.archivo_propuesto else documento.archivo,
        cambios=f'Planilla de Cambios {planilla.codigo}: {planilla.justificacion}',
        creado_por=usuario or planilla.responsable,
    )

    cambios = {}
    if clasificacion_nueva != clasificacion_anterior:
        cambios = {
            'clasificacion_anterior': clasificacion_anterior,
            'clasificacion_nueva': clasificacion_nueva,
        }
    # Título/versión/clasificación cambiaron: la planilla guardada quedó vieja.
    invalidar_planillas_documento(documento)
    return documento, cambios

