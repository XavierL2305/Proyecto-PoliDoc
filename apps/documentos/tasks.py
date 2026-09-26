from celery import shared_task
import subprocess
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
    Se ejecuta asíncronamente en Celery Worker.
    """
    from .models import Documento

    try:
        doc = Documento.objects.get(uuid=documento_id)

        if not doc.archivo or doc.tipo_archivo not in FORMATOS_CONVERTIBLES:
            return {'success': False, 'error': 'Formato no soportado para conversión'}

        archivo_path = doc.archivo.path
        output_dir = os.path.dirname(archivo_path)

        cmd = [
            'libreoffice', '--headless', '--convert-to', 'pdf',
            '--outdir', output_dir, archivo_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if result.returncode == 0:
            pdf_filename = os.path.splitext(os.path.basename(archivo_path))[0] + '.pdf'
            pdf_path = os.path.join(output_dir, pdf_filename)

            if os.path.exists(pdf_path):
                # Convertir ruta absoluta a relativa (relativa a MEDIA_ROOT)
                media_root = str(settings.MEDIA_ROOT)
                if pdf_path.startswith(media_root):
                    pdf_path = pdf_path[len(media_root):].lstrip(os.sep)

                doc.archivo_pdf = pdf_path
                doc.save(update_fields=['archivo_pdf'])
                logger.info(f"PDF generado: {doc.tipo_id} {documento_id} ({doc.tipo_archivo})")
                return {'success': True, 'pdf_path': pdf_path}

        logger.error(f"Error LibreOffice: {result.stderr}")
        return {'success': False, 'error': result.stderr}

    except Exception as exc:
        logger.error(f"Error en conversión: {exc}")
        self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=3)
def convertir_planilla_a_pdf(self, planilla_id):
    """
    Convierte el archivo propuesto de una planilla a PDF para vista previa.
    Se ejecuta asíncronamente cuando se crea una planilla con archivo DOCX/XLSX.
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

        output_dir = os.path.dirname(archivo_path)

        cmd = [
            'libreoffice', '--headless', '--convert-to', 'pdf',
            '--outdir', output_dir, archivo_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if result.returncode == 0:
            pdf_filename = os.path.splitext(os.path.basename(archivo_path))[0] + '.pdf'
            pdf_path = os.path.join(output_dir, pdf_filename)

            if os.path.exists(pdf_path):
                media_root = str(settings.MEDIA_ROOT)
                if pdf_path.startswith(media_root):
                    pdf_path = pdf_path[len(media_root):].lstrip(os.sep)

                planilla.archivo_propuesto_pdf = pdf_path
                planilla.save(update_fields=['archivo_propuesto_pdf'])
                logger.info(f"PDF generado para planilla: {planilla.codigo}")
                return {'success': True, 'pdf_path': pdf_path}

        logger.error(f"Error LibreOffice planilla: {result.stderr}")
        return {'success': False, 'error': result.stderr}

    except Exception as exc:
        logger.error(f"Error en conversión planilla: {exc}")
        self.retry(exc=exc, countdown=60)


def convertir_a_pdf_sync(archivo_path):
    """
    Convierte un archivo office a PDF usando LibreOffice (síncrono).
    Retorna la ruta relativa del PDF generado o None si falla.
    """
    try:
        ext = os.path.splitext(archivo_path)[1].lower().lstrip('.')
        if ext not in FORMATOS_CONVERTIBLES:
            return None

        output_dir = os.path.dirname(archivo_path)
        cmd = [
            'libreoffice', '--headless', '--convert-to', 'pdf',
            '--outdir', output_dir, archivo_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if result.returncode == 0:
            pdf_filename = os.path.splitext(os.path.basename(archivo_path))[0] + '.pdf'
            pdf_path = os.path.join(output_dir, pdf_filename)

            if os.path.exists(pdf_path):
                media_root = str(settings.MEDIA_ROOT)
                if pdf_path.startswith(media_root):
                    pdf_path = pdf_path[len(media_root):].lstrip(os.sep)
                logger.info(f"PDF generado (sync): {pdf_path}")
                return pdf_path

        logger.error(f"Error LibreOffice sync: {result.stderr}")
        return None

    except Exception as exc:
        logger.error(f"Error en conversión sync: {exc}")
        return None


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

    resultado = ai.generar_resumen(doc)
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
