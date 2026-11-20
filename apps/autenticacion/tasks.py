import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='autenticacion.tasks.sincronizar_cargos_rrhh')
def sincronizar_cargos_rrhh():
    """Sincroniza cargo/departamento/nombres desde RRHH (diario, 23:59 VET).

    No-op si la integración no está configurada. Nunca propaga excepciones:
    el worker no debe caerse por un RRHH caído.
    """
    from .services.rrhh_sync import rrhh_habilitado, sincronizar_todo_rrhh

    if not rrhh_habilitado():
        logger.info('RRHH no configurado: se omite la sincronización de cargos.')
        return {'omitido': True}

    try:
        resumen = sincronizar_todo_rrhh()
    except Exception:  # noqa: BLE001 - la tarea no debe tumbar al worker
        logger.exception('Error inesperado sincronizando cargos desde RRHH.')
        return {'error': True}

    logger.info('Sincronización RRHH finalizada: %s', resumen)
    return resumen
