"""Cadena de aprobación de planillas de cambios (SGC-FOR-002).

La aprobación de una planilla tiene dos superficies: la vista
``views.aprobar_planilla`` (render) y la API ``api.aprobar_planilla`` (JSON,
usada por la edición en línea). Históricamente la lógica estaba duplicada en
ambas y divergió: al promover el paso del autorizador solo se notificaba al
responsable de la planilla, no al propio autorizador, con lo que planillas
enteras (p. ej. ``PC-20261013-191940``) quedaban invisibles para quien tenía
que aprobarlas. Este módulo concentra esas operaciones para que ambas
superficies ejecuten exactamente el mismo código.
"""

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def valores_autorrelleno_planilla(aprobacion, aprobada=True):
    """Valores de evaluación/autorización/resolución de una respuesta.

    Reglas (compartidas con ``SolicitudAprobacionDocumento``):

    - **Un solo paso** (jefe → GG): el mismo aprobador firma Evaluado y
      Autorizado.
    - **Dos pasos**: el evaluador firma solo Evaluado; el autorizador firma
      Autorizado y, si es final, fija la Resolución.
    - **Rechazo**: firma el rol que actuó (en un solo paso, ambos) y la
      Resolución queda ``Rechazado``.

    Usa la fecha real de la respuesta (``fecha_aprobacion``).
    """
    from ..models import _valores_autorizacion, _valores_evaluacion

    valores = {}
    if aprobacion.rol_aprobador == 'evaluador' or aprobacion.es_paso_unico():
        valores.update(_valores_evaluacion(
            aprobacion.aprobador, aprobacion.fecha_aprobacion,
        ))
    if aprobacion.rol_aprobador == 'autorizador':
        valores.update(_valores_autorizacion(
            aprobacion.aprobador, aprobacion.fecha_aprobacion,
        ))
    if not aprobada:
        valores['resolucion'] = 'Rechazado'
    elif aprobacion.es_final:
        valores['resolucion'] = 'Aceptado'
    return valores


def autocompletar_planilla(planilla, aprobacion, aprobada=True):
    """Vuelca las firmas de evaluación/autorización en la planilla FOR-005.

    Escribe en la ``SolicitudCambio`` enlazada a la planilla (motor dinámico)
    y descarta el XLSX/PDF cacheado para que la siguiente descarga se
    regenere firmado. Silencioso si la planilla no tiene solicitud enlazada.
    """
    from ..models import autocompletar_respuestas_solicitud

    if not planilla.solicitud_cambio_id:
        return
    valores = valores_autorrelleno_planilla(aprobacion, aprobada=aprobada)
    autocompletar_respuestas_solicitud(planilla.solicitud_cambio, valores)


def notificar_aprobador(aprobador, solicitante, documento, es_evaluador=False, planilla=None):
    """Crea la notificación in-app de un paso pendiente de aprobación."""
    from autenticacion.models import Notificacion

    if planilla:
        titulo = (
            'Edición de documento pendiente de evaluación' if es_evaluador
            else 'Edición de documento pendiente de autorización'
        )
        msg = (
            f'{solicitante.get_full_name() or solicitante.username} ha editado el documento '
            f'"{documento.nombre}" ({documento.codigo}) y requiere tu '
            f'{"evaluación" if es_evaluador else "autorización"}.'
        )
        url = f'/documentos/planilla-cambios/{planilla.uuid}/aprobar/'
    else:
        titulo = (
            'Documento pendiente de evaluación' if es_evaluador
            else 'Documento pendiente de autorización'
        )
        msg = (
            f'{solicitante.get_full_name() or solicitante.username} ha creado el documento '
            f'"{documento.nombre}" ({documento.codigo}) y requiere tu '
            f'{"evaluación" if es_evaluador else "autorización"}.'
        )
        url = f'/documentos/{documento.tipo_id}/{documento.uuid}/'
    Notificacion.objects.create(
        usuario=aprobador, titulo=titulo, mensaje=msg,
        tipo='solicitud_aprobacion', url_accion=url,
        departamento=getattr(documento, 'departamento', None), documento=documento,
    )


def promover_autorizador(planilla, aprobacion_evaluada, solicitante):
    """Activa el paso del autorizador tras aprobar el evaluador.

    Promueve el registro ``esperando`` → ``pendiente`` (fijando
    ``fecha_activacion``, la base del escalado), **notifica al autorizador**
    (esto es el defecto que ocultó ``PC-20261013-191940``) y avisa al
    responsable de la planilla. Retorna la aprobación promovida, o ``None``.
    """
    from autenticacion.models import Notificacion
    from ..models import AprobacionPlanilla

    hermano = AprobacionPlanilla.objects.filter(
        planilla=planilla, rol_aprobador='autorizador', estado='esperando',
    ).exclude(id=aprobacion_evaluada.id).order_by('id').first()

    activada = None
    if hermano and hermano.aprobador_id:
        hermano.estado = 'pendiente'
        hermano.fecha_activacion = timezone.now()
        hermano.save(update_fields=['estado', 'fecha_activacion'])
        activada = hermano
        notificar_aprobador(
            hermano.aprobador, solicitante, planilla.documento,
            es_evaluador=False, planilla=planilla,
        )

    Notificacion.objects.create(
        usuario=planilla.responsable,
        titulo='Planilla de Cambios en autorización',
        mensaje=f'Tu planilla {planilla.codigo} ha sido evaluada. Pendiente de autorización final.',
        tipo='aprobacion_documento',
        url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/',
        departamento=getattr(planilla.documento, 'departamento', None),
        documento=planilla.documento,
    )
    return activada


def avisar_paso_sin_asignar(planilla, rol_label):
    """Red de seguridad: un paso sin aprobador queda mudo para todos.

    Si no hay jefe en el departamento (o no hay jefe de GG para el rol de
    autorizador), la cadena se crea en ``esperando`` y nadie recibe
    notificación. Aquí se avisa a los administradores para que asignen.
    Retorna el número de notificaciones enviadas.
    """
    from autenticacion.models import PerfilUsuario, Notificacion

    admins = [
        perfil.usuario for perfil in PerfilUsuario.objects.filter(
            rol='administrador', usuario__is_active=True,
        ).select_related('usuario')
    ]
    if not admins:
        return 0
    documento = planilla.documento
    for admin in admins:
        Notificacion.objects.create(
            usuario=admin,
            titulo='Planilla de Cambios sin aprobador asignado',
            mensaje=(
                f'La planilla {planilla.codigo} quedó en espera del paso de '
                f'{rol_label} porque no hay usuario asignado a ese rol. '
                f'Asigna un jefe y reenvía la planilla.'
            ),
            tipo='planilla_pendiente',
            url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/',
            departamento=getattr(documento, 'departamento', None),
            documento=documento,
        )
    logger.warning(
        "Planilla %s quedó sin aprobador para el rol '%s' (%s avisados)",
        planilla.codigo, rol_label, len(admins),
    )
    return len(admins)
