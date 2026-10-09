import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1)
def reprocesar_logs_buffer(self):
    """Reenvía a MongoDB los logs de auditoría bufferizados.

    Los bufferiza el circuit breaker de `auditoria.services` cuando Mongo no
    responde. Se ejecuta periódicamente por Celery Beat; si Mongo sigue caído
    se detiene al primer fallo y deja los ficheros para el siguiente intento.
    """
    from .services import reprocesar_buffer

    resumen = reprocesar_buffer()
    if resumen.get('enviados'):
        logger.info(
            "Auditoría: %s logs reenviados desde el buffer (%s fallidos).",
            resumen['enviados'], resumen.get('fallidos', 0),
        )
    return resumen


@shared_task
def purgar_reportes_auditoria():
    """Elimina los reportes de auditoría que superan la retención (ISO 15489).

    Corre a diario por Celery Beat. Borra el archivo del disco y la fila; si
    un fichero ya no existe (borrado manual) no falla, solo lo registra y
    elimina el registro. Nunca lanza excepción.
    """
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from .models import ReporteAuditoria

    anos = getattr(settings, 'AUDITORIA_REPORTES_RETENCION_ANIOS', 10)
    limite = timezone.now() - timedelta(days=365 * anos)
    expirados = ReporteAuditoria.objects.filter(fecha_generacion__lt=limite)

    purgados = 0
    for reporte in expirados:
        try:
            if reporte.archivo:
                reporte.archivo.delete(save=False)
            reporte.delete()
            purgados += 1
        except Exception:
            logger.exception(
                "Auditoría: no se pudo purgar el reporte %s", reporte.codigo
            )

    if purgados:
        logger.info(
            "Auditoría: %s reportes de auditoría purgados (retención %s años).",
            purgados, anos,
        )
    return {'purgados': purgados}
