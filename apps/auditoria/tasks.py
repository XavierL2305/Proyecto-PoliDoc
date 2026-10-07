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
