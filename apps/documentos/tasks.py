from celery import shared_task
import os
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# Formatos soportados para conversión a PDF
FORMATOS_CONVERTIBLES = ('doc', 'docx', 'xls', 'xlsx', 'vsdx')


@shared_task(bind=True, max_retries=3)
def convertir_documento_a_pdf(self, documento_id):
    """
    Convierte documentos office a PDF usando LibreOffice headless.
    Soporta: DOC, DOCX, XLS, XLSX, VSDX
    Se ejecuta asíncronamente en Celery Worker (la vista lo hace en línea).
    """
    from .models import Documento
    from .services.documentos_pdf import convertir_documento_sync

    try:
        doc = Documento.objects.get(uuid=documento_id)

        if not doc.archivo or doc.tipo_archivo not in FORMATOS_CONVERTIBLES:
            return {'success': False, 'error': 'Formato no soportado para conversión'}

        if convertir_documento_sync(doc):
            return {'success': True, 'pdf_path': doc.archivo_pdf.name}

        return {'success': False, 'error': 'LibreOffice no generó el PDF'}

    except Exception as exc:
        logger.error(f"Error en conversión: {exc}")
        self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def convertir_planilla_a_pdf(self, planilla_id):
    """
    Convierte el archivo propuesto de una planilla a PDF para vista previa.
    Se ejecuta asíncronamente cuando se crea una planilla con archivo DOCX/XLSX.

    Usa el mismo conversor que los documentos (``convertir_a_pdf_sync``), de
    modo que la previsualización de planillas sale con el papel y los márgenes
    de ``settings`` y con la copia saneada.
    """
    try:
        from .models import PlanillaCambios
        planilla = PlanillaCambios.objects.get(id=planilla_id)

        if not planilla.archivo_propuesto:
            return {'success': False, 'error': 'No hay archivo propuesto'}

        archivo_path = planilla.archivo_propuesto.path
        ext = os.path.splitext(archivo_path)[1].lower().lstrip('.')

        if ext not in FORMATOS_CONVERTIBLES:
            return {'success': False, 'error': f'Formato {ext} no soportado para conversión'}

        pdf_path = convertir_a_pdf_sync(archivo_path)
        if not pdf_path:
            return {'success': False, 'error': 'LibreOffice no generó el PDF'}

        planilla.archivo_propuesto_pdf = pdf_path
        planilla.save(update_fields=['archivo_propuesto_pdf'])
        logger.info(f"PDF generado para planilla: {planilla.codigo}")
        return {'success': True, 'pdf_path': pdf_path}

    except Exception as exc:
        logger.error(f"Error en conversión planilla: {exc}")
        self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def generar_planilla_solicitud_task(self, solicitud_uuid):
    """
    Genera y guarda la planilla (XLSX + PDF) de una SolicitudCambio aprobada.
    Se ejecuta en el Celery Worker al aprobar una creación/eliminación
    (formato SGC-FOR-001). Ver documentos.services.planillas.
    """
    from .models import SolicitudCambio
    from .services.planillas import generar_planilla_solicitud

    try:
        solicitud = SolicitudCambio.objects.get(uuid=solicitud_uuid)
    except SolicitudCambio.DoesNotExist:
        return {'success': False, 'error': 'Solicitud no encontrada'}

    if solicitud.estado != 'aprobado':
        return {
            'success': False,
            'error': f"Solicitud {solicitud.codigo} en estado '{solicitud.estado}'",
        }

    try:
        resultado = generar_planilla_solicitud(solicitud)
        if not resultado.get('success'):
            logger.error("Planilla %s no generada: %s", solicitud.codigo, resultado.get('error'))
        return resultado
    except Exception as exc:
        logger.error("Error generando planilla de %s: %s", solicitud.codigo, exc)
        self.retry(exc=exc, countdown=60)


def convertir_a_pdf_sync(archivo_path):
    """
    Convierte un archivo office a PDF (síncrono) usando el mismo conversor que
    los documentos: papel de ``settings.PAPEL_PDF_CONVERSION``, márgenes
    ``settings.MARGEN_{SUPERIOR,INFERIOR}_PDF_CONVERSION`` y saneo de la copia
    temporal (salto de página del encabezado repetido). Después corre el QA del
    PDF; si detecta maquetación rota, programa la reparación adaptativa (ver
    services/pdf_adaptativo.py).

    Retorna la ruta del PDF generado relativa a ``MEDIA_ROOT`` (o la absoluta
    si el origen vive fuera de Media) o None si falla. Nunca lanza excepciones.
    """
    from .services.conversion_pdf import convertir_office_a_pdf

    try:
        ext = os.path.splitext(archivo_path)[1].lower().lstrip('.')
        if ext not in FORMATOS_CONVERTIBLES:
            return None

        output_dir = os.path.dirname(archivo_path) or '.'
        pdf_path = convertir_office_a_pdf(archivo_path, outdir=output_dir)
        if not pdf_path:
            return None

        try:
            from .services.pdf_adaptativo import verificar
            verificar(archivo_path, pdf_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"QA del PDF falló para {pdf_path}: {exc}")

        pdf_path = os.path.normpath(pdf_path)
        media_root = str(settings.MEDIA_ROOT)
        if pdf_path.startswith(media_root):
            pdf_path = pdf_path[len(media_root):].lstrip(os.sep)
        logger.info(f"PDF generado (sync): {pdf_path}")
        return pdf_path

    except Exception as exc:
        logger.error(f"Error en conversión sync: {exc}")
        return None


@shared_task(bind=True, max_retries=1)
def reparar_pdf_adaptativo(self, archivo, pdf):
    """Reconvierte ``archivo`` buscando una disposición sin defectos de maquetación.

    Se encola desde ``pdf_adaptativo.verificar`` cuando el QA del PDF detecta un
    logo recortado o un título huérfano. Sobrescribe ``pdf`` en su sitio; nunca
    lanza.
    """
    from .services.pdf_adaptativo import reparar

    try:
        diagnostico = reparar(archivo, pdf)
        logger.info("Reparación de %s: %s", pdf, diagnostico.resumen())
        return {'success': True, 'resumen': diagnostico.resumen()}
    except Exception as exc:  # noqa: BLE001
        logger.error("Reparación adaptativa falló para %s: %s", pdf, exc)
        return {'success': False, 'error': str(exc)}


@shared_task(bind=True, max_retries=1)
def verificar_vencimientos(self):
    """
    Tarea periódica: marca documentos vencidos como obsoletos,
    envía avisos de vencimiento próximo (7 días).
    """
    from datetime import date, timedelta
    from .models import Documento
    from autenticacion.models import Notificacion

    hoy = date.today()
    vencidos = 0
    avisos = 0

    # 1. Documentos activos con vencimiento vencido → obsoleto
    docs_vencidos = Documento.objects.filter(
        fecha_vencimiento__lte=hoy,
        estado='activo',
        fecha_vencimiento__isnull=False,
    )
    for doc in docs_vencidos:
        doc.estado = 'obsoleto'
        update_fields = ['estado']
        if doc.periodo_retencion:
            from .models import _sumar_meses
            doc.fecha_retencion = hoy
            # _sumar_meses ajusta el día al último válido del mes (ej. 31-ene +
            # 1 mes = 28/29-feb), evitando la aritmética manual de meses/años
            # que fallaba en esos bordes.
            doc.fecha_destruccion = _sumar_meses(hoy, doc.periodo_retencion.meses)
            update_fields.extend(['fecha_retencion', 'fecha_destruccion'])
        doc.save(update_fields=update_fields)
        vencidos += 1
        # Notificar propietario
        Notificacion.objects.create(
            usuario=doc.propietario,
            titulo='Documento vencido',
            mensaje=f'El documento "{doc.nombre}" ({doc.codigo}) ha vencido y fue marcado como obsoleto.',
            tipo='documento_vencido',
            url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
        )
        # Notificar jefe del depto
        if doc.departamento and doc.departamento.jefe:
            Notificacion.objects.create(
                usuario=doc.departamento.jefe,
                titulo='Documento del departamento vencido',
                mensaje=f'El documento "{doc.nombre}" ({doc.codigo}) de {doc.departamento.nombre} ha vencido.',
                tipo='documento_vencido',
                url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
            )
        # Auditoría
        try:
            from auditoria.services import registrar_log_iso
            registrar_log_iso(
                usuario=None,
                accion_tipo='vencimiento',
                accion_categoria='automatizacion',
                recurso_tipo=doc.tipo_id or '',
                recurso_id=str(doc.uuid),
                recurso_codigo=doc.codigo,
                recurso_nombre=doc.nombre,
                estado_anterior='activo',
                estado_posterior='obsoleto',
                detalles={'origen': 'Celery Beat', 'actor': 'sistema'},
                resultado_exitoso=True,
                resultado_mensaje=f'Documento vencido automáticamente: {doc.codigo}',
                ip_address='127.0.0.1',
                user_agent='Celery Beat',
            )
        except Exception as e:
            logger.error(f"Auditoría vencimiento: {e}")

    # 2. Documentos con vencimiento en ≤7 días → notificar aviso
    fecha_limite = hoy + timedelta(days=7)
    docs_por_vencer = Documento.objects.filter(
        fecha_vencimiento__gt=hoy,
        fecha_vencimiento__lte=fecha_limite,
        estado='activo',
        aviso_vencimiento_enviado=False,
        fecha_vencimiento__isnull=False,
    )
    for doc in docs_por_vencer:
        Notificacion.objects.create(
            usuario=doc.propietario,
            titulo='Documento próximo a vencer',
            mensaje=f'El documento "{doc.nombre}" ({doc.codigo}) vence el {doc.fecha_vencimiento}.',
            tipo='aviso_vencimiento',
            url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
        )
        if doc.departamento and doc.departamento.jefe:
            Notificacion.objects.create(
                usuario=doc.departamento.jefe,
                titulo='Documento del departamento próximo a vencer',
                mensaje=f'El documento "{doc.nombre}" ({doc.codigo}) de {doc.departamento.nombre} vence el {doc.fecha_vencimiento}.',
                tipo='aviso_vencimiento',
                url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
            )
        doc.aviso_vencimiento_enviado = True
        doc.save(update_fields=['aviso_vencimiento_enviado'])
        avisos += 1

    logger.info(f"Verificación vencimientos: {vencidos} obsoletos, {avisos} avisos enviados")
    return {'vencidos': vencidos, 'avisos': avisos}


@shared_task(bind=True, max_retries=1)
def generar_resumen_ia(self, documento_id):
    """Genera el resumen IA de un documento y lo persiste."""
    from django.utils import timezone
    from .models import Documento
    from . import ai

    try:
        doc = Documento.objects.get(uuid=documento_id)
    except Documento.DoesNotExist:
        return {'success': False, 'error': 'Documento no encontrado'}

    doc.resumen_ia_estado = 'procesando'
    doc.resumen_ia_error = ''
    doc.save(update_fields=['resumen_ia_estado', 'resumen_ia_error'])

    try:
        resultado = ai.generar_resumen(doc)
    except Exception as exc:  # noqa: BLE001 - no dejar el estado colgado en 'procesando'
        doc.resumen_ia_estado = 'error'
        doc.resumen_ia_error = str(exc)[:2000]
        doc.save(update_fields=['resumen_ia_estado', 'resumen_ia_error'])
        logger.error("Resumen IA excepción para %s: %s", documento_id, exc)
        return {'success': False, 'error': str(exc)}
    if not resultado.get('ok'):
        doc.resumen_ia_estado = 'error'
        doc.resumen_ia_error = str(resultado.get('error', 'Error desconocido'))[:2000]
        doc.save(update_fields=['resumen_ia_estado', 'resumen_ia_error'])
        logger.error("Resumen IA falló para %s: %s", documento_id, doc.resumen_ia_error)
        return {'success': False, 'error': doc.resumen_ia_error}

    doc.resumen_ia = resultado.get('resumen')
    doc.resumen_ia_texto = resultado.get('texto', '')
    doc.resumen_ia_modelo = resultado.get('modelo', '')
    doc.resumen_ia_version = doc.version_actual
    doc.resumen_ia_fecha = timezone.now()
    doc.resumen_ia_estado = 'listo'
    doc.resumen_ia_error = ''
    doc.save(update_fields=[
        'resumen_ia', 'resumen_ia_texto', 'resumen_ia_modelo',
        'resumen_ia_version', 'resumen_ia_fecha', 'resumen_ia_estado',
        'resumen_ia_error',
    ])
    logger.info("Resumen IA generado para %s (%s)", doc.codigo, documento_id)
    return {'success': True}


@shared_task(bind=True, max_retries=1)
def escalar_aprobaciones_vencidas(self):
    """Notifica a los administradores las aprobaciones estancadas.

    Se consideran estancadas las solicitudes en estado pendiente/esperando
    cuya fecha de solicitud supera `APROBACION_ESCALADO_HORAS` (default 48h)
    y que no hayan sido escaladas antes.
    """
    from datetime import timedelta
    from django.utils import timezone
    from .models import SolicitudAprobacionDocumento
    from autenticacion.models import PerfilUsuario, Notificacion

    horas = int(getattr(settings, 'APROBACION_ESCALADO_HORAS', 48))
    ahora = timezone.now()
    limite = ahora - timedelta(hours=horas)

    pendientes = SolicitudAprobacionDocumento.objects.filter(
        estado__in=['pendiente', 'esperando'],
        fecha_solicitud__lte=limite,
        fecha_escalado__isnull=True,
    ).select_related('documento', 'aprobador', 'departamento_origen')

    admins = [
        perfil.usuario for perfil in PerfilUsuario.objects.filter(
            rol='administrador', usuario__is_active=True,
        ).select_related('usuario')
    ]
    escaladas = 0

    for solicitud in pendientes:
        doc = solicitud.documento
        if doc and admins:
            aprobador = solicitud.aprobador
            aprobador_txt = (
                (aprobador.get_full_name() or aprobador.username)
                if aprobador else 'sin asignar'
            )
            for admin in admins:
                Notificacion.objects.create(
                    usuario=admin,
                    titulo='Aprobación estancada (+%sh)' % horas,
                    mensaje=(
                        f'La solicitud de {solicitud.get_accion_solicitud_display()} del '
                        f'documento "{doc.nombre}" ({doc.codigo}) lleva más de {horas}h '
                        f'sin respuesta. Aprobador: {aprobador_txt}.'
                    ),
                    tipo='aprobacion_escalada',
                    url_accion='/documentos/bandeja-aprobaciones/',
                    departamento=solicitud.departamento_origen,
                    documento=doc,
                )
        solicitud.fecha_escalado = ahora
        solicitud.save(update_fields=['fecha_escalado'])
        escaladas += 1

    logger.info(
        "Escalado de aprobaciones: %s solicitudes, %s administradores",
        escaladas, len(admins),
    )
    return {'escaladas': escaladas, 'admins': len(admins)}


@shared_task(bind=True, max_retries=1)
def generar_planillas_pendientes(self):
    """Tarea periódica: genera las planillas SGC-FOR-001 que falten.

    Red de seguridad del flujo asíncrono (ver services/planillas.py): si la
    tarea disparada al aprobar no llegó a ejecutarse (worker caído, broker sin
    consumidor, fallo transitorio), aquí se detectan las solicitudes aprobadas
    de creación/eliminación sin `planilla_xlsx` y se generan. Idempotente: lo
    que ya existe no se vuelve a generar.

    Devuelve {'revisadas': n, 'generadas': k, 'errores': e}.
    """
    from django.db.models import Q
    from .models import SolicitudCambio
    from .services.planillas import OPERACIONES_CON_PLANILLA, generar_planilla_solicitud

    lote = int(getattr(settings, 'PLANILLAS_BARRIDO_LOTE', 100))
    pendientes = (
        SolicitudCambio.objects.filter(
            estado='aprobado',
            operacion__in=OPERACIONES_CON_PLANILLA,
            formato__plantilla_xlsx__isnull=False,
        )
        .exclude(Q(formato__plantilla_xlsx='') | Q(formato__plantilla_xlsx__isnull=True))
        .filter(Q(planilla_xlsx__isnull=True) | Q(planilla_xlsx=''))
        .select_related('formato')
        .order_by('fecha_creacion', 'id')[:lote]
    )

    revisadas = generadas = errores = 0
    for solicitud in pendientes:
        revisadas += 1
        try:
            resultado = generar_planilla_solicitud(solicitud)
        except Exception as exc:
            errores += 1
            logger.error(
                "Barrido de planillas: error generando %s: %s", solicitud.codigo, exc,
            )
            continue
        if resultado.get('success'):
            generadas += 1
        else:
            errores += 1
            logger.error(
                "Barrido de planillas: %s no generada: %s",
                solicitud.codigo, resultado.get('error'),
            )

    if revisadas:
        logger.info(
            "Barrido de planillas: %s revisadas, %s generadas, %s errores",
            revisadas, generadas, errores,
        )
    return {'revisadas': revisadas, 'generadas': generadas, 'errores': errores}
