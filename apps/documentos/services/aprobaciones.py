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

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def valores_autorrelleno_planilla(aprobacion, aprobada=True):
    """Valores de evaluación/autorización/resolución de una respuesta.

    Reglas (compartidas con ``SolicitudAprobacionDocumento``):

    - **Un solo paso** (jefe → GG): el mismo aprobador firma Evaluado y
      Autorizado.
    - **Dos pasos**: el evaluador firma solo Evaluado; el autorizador firma
      Autorizado y, si es final, fija la Resolución.
    - **Rechazo**: NO firma ni imprime nada (los bloques quedan en blanco);
      el documento/planilla vuelve para correcciones y la trazabilidad queda
      en la auditoría y las observaciones.

    Usa la fecha real de la respuesta (``fecha_aprobacion``).
    """
    from ..models import _valores_autorizacion, _valores_evaluacion

    if not aprobada:
        return {}

    valores = {}
    if aprobacion.rol_aprobador == 'evaluador' or aprobacion.es_paso_unico():
        valores.update(_valores_evaluacion(
            aprobacion.aprobador, aprobacion.fecha_aprobacion,
        ))
    if aprobacion.rol_aprobador == 'autorizador':
        valores.update(_valores_autorizacion(
            aprobacion.aprobador, aprobacion.fecha_aprobacion,
        ))
    if aprobacion.es_final:
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


def tomar_paso_esperando(aprobacion, usuario):
    """Asigna al admin un paso en ``esperando`` y registra la traza ISO.

    El llamador debe haber bloqueado la fila con ``select_for_update()`` para
    evitar que dos administradores tomen la misma aprobación.

    La traza se guarda en el outbox SQL dentro de la MISMA transacción: solo
    existe si la toma se confirma (no hay trazas de tomas revertidas) y un
    worker la reenvía a Mongo después, sin bloquear la operación.
    """
    from uuid import uuid4

    from auditoria.services import encolar_evento_auditoria

    planilla = getattr(aprobacion, 'planilla', None)
    documento = (
        getattr(aprobacion, 'documento', None)
        or getattr(planilla, 'documento', None)
    )
    payload = {
        'usuario_id': getattr(usuario, 'id', None),
        'accion_tipo': 'toma_aprobacion',
        'accion_categoria': 'documento',
        'recurso_tipo': getattr(documento, 'tipo_id', None),
        'recurso_id': getattr(documento, 'id', None),
        'recurso_codigo': getattr(documento, 'codigo', None),
        'recurso_nombre': getattr(documento, 'nombre', None),
        'estado_anterior': 'esperando',
        'estado_posterior': 'pendiente',
        'detalles': {
            'flujo': 'aprobacion',
            'planilla_codigo': getattr(planilla, 'codigo', None),
            'aprobacion_id': aprobacion.id,
        },
        'resultado_exitoso': True,
        'resultado_mensaje': 'Administrador tomó el paso en espera',
        'correlation_id': str(uuid4()),
    }

    with transaction.atomic():
        aprobacion.aprobador = usuario
        aprobacion.estado = 'pendiente'
        aprobacion.save(update_fields=['aprobador', 'estado'])
        encolar_evento_auditoria(payload)
    return aprobacion


def resolver_aprobacion_planilla(aprobacion, usuario, *, aprobada,
                                  observaciones='', firma=None):
    """Resuelve una ``AprobacionPlanilla`` (aprobar o rechazar) y sus efectos.

    Único punto para vista, API y auto-aprobación de plantillas: fija el
    estado/firmas, aplica los cambios al documento cuando corresponde y
    registra la auditoría ISO. No crea notificaciones in-app; eso queda en
    ``notificar_planilla_resuelta`` para que cada superficie decida a quién
    avisar.

    Exige ``firma`` (``FirmaElectronica`` del paso) salvo en PLANTILLAS
    (``documento.es_plantilla=True``): aprobar una plantilla no emite firma
    SES. Las rutas humanas de documentos/planillas no-plantilla la obtienen
    con ``services.firmas.firmar``.

    Devuelve ``{'planilla', 'documento', 'cambios', 'es_final'}``.
    """
    from ..models import AprobacionPlanilla
    from .firmas import ErrorFirma
    from auditoria.services import (
        registrar_aprobacion_planilla, registrar_rechazo_planilla,
    )
    from .planillas import aplicar_planilla_al_documento

    planilla = aprobacion.planilla
    es_plantilla = bool(getattr(planilla.documento, 'es_plantilla', False))
    if aprobada and firma is None and not es_plantilla:
        raise ErrorFirma('Falta la firma electrónica de la aprobación.')
    if aprobada:
        es_final = aprobacion.es_final
        aprobacion.estado = 'aprobada'
        aprobacion.observaciones = observaciones
        aprobacion.fecha_aprobacion = timezone.now()
        aprobacion.save(update_fields=[
            'estado', 'observaciones', 'fecha_aprobacion',
        ])
        autocompletar_planilla(planilla, aprobacion, aprobada=True)
        if not es_final:
            promover_autorizador(planilla, aprobacion, usuario)
            return {
                'planilla': planilla, 'documento': None,
                'cambios': {}, 'es_final': False,
            }

        planilla.estado = 'aprobado'
        planilla.fecha_aprobacion = timezone.now()
        planilla.save(update_fields=['estado', 'fecha_aprobacion'])
        if planilla.solicitud_cambio:
            planilla.solicitud_cambio.estado = 'aprobado'
            planilla.solicitud_cambio.save(update_fields=['estado'])
        doc, cambios = aplicar_planilla_al_documento(planilla, usuario=usuario)
        if doc:
            registrar_aprobacion_planilla(
                usuario=usuario, planilla=planilla, documento=doc,
                cambios=cambios, observaciones=observaciones,
            )
        return {
            'planilla': planilla, 'documento': doc,
            'cambios': cambios, 'es_final': True,
        }

    aprobacion.estado = 'rechazada'
    aprobacion.observaciones = observaciones
    aprobacion.fecha_aprobacion = timezone.now()
    aprobacion.save(update_fields=[
        'estado', 'observaciones', 'fecha_aprobacion',
    ])
    # Rechazo: no se firma ni se imprime nada. Solo se invalida el preview
    # por si existiera un XLSX cacheado de un intento anterior.
    if planilla.solicitud_cambio_id:
        from .planillas import invalidar_planilla_solicitud

        invalidar_planilla_solicitud(planilla.solicitud_cambio)
    hermano = AprobacionPlanilla.objects.filter(
        planilla=planilla,
    ).exclude(id=aprobacion.id).exclude(
        estado__in=('aprobada', 'rechazada', 'cancelada'),
    ).first()
    if hermano:
        hermano.estado = 'cancelada'
        hermano.save(update_fields=['estado'])
    planilla.estado = 'rechazado'
    planilla.save(update_fields=['estado'])
    if planilla.solicitud_cambio:
        planilla.solicitud_cambio.estado = 'rechazado'
        planilla.solicitud_cambio.save(update_fields=['estado'])
    doc = planilla.documento
    if doc:
        doc.estado = 'activo'
        doc.save(update_fields=['estado'])
    registrar_rechazo_planilla(
        usuario=usuario, planilla=planilla, documento=doc,
        observaciones=observaciones,
    )
    return {
        'planilla': planilla, 'documento': doc,
        'cambios': {}, 'es_final': True,
    }


def notificar_planilla_resuelta(planilla, aprobada, observaciones=''):
    """Notifica al responsable que su planilla fue aprobada o rechazada."""
    from autenticacion.models import Notificacion

    if aprobada:
        titulo = 'Planilla de Cambios aprobada'
        mensaje = (
            f'Tu planilla {planilla.codigo} ha sido aprobada. '
            'Los cambios se han aplicado al documento.'
        )
        tipo = 'aprobacion_documento'
    else:
        titulo = 'Planilla de Cambios rechazada'
        mensaje = (
            f'Tu planilla {planilla.codigo} ha sido rechazada. '
            f'Motivo: {observaciones}'
        )
        tipo = 'rechazo_documento'
    documento = getattr(planilla, 'documento', None)
    Notificacion.objects.create(
        usuario=planilla.responsable,
        titulo=titulo,
        mensaje=mensaje,
        tipo=tipo,
        url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/',
        departamento=getattr(documento, 'departamento', None),
        documento=documento,
    )


def notificar_aprobador(aprobador, solicitante, documento, es_evaluador=False, planilla=None):
    """Crea la notificación in-app de un paso pendiente de aprobación."""
    from autenticacion.models import Notificacion

    es_plantilla = bool(getattr(documento, 'es_plantilla', False))
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
    elif es_plantilla:
        titulo = 'Plantilla pendiente de aprobación'
        msg = (
            f'{solicitante.get_full_name() or solicitante.username} ha subido la plantilla '
            f'"{documento.nombre}" ({documento.codigo}) y requiere tu aprobación.'
        )
        url = f'/documentos/{documento.tipo_id}/{documento.uuid}/'
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


def avisar_paso_sin_asignar(obj, rol_label):
    """Red de seguridad: un paso sin aprobador queda mudo para todos.

    Acepta una ``PlanillaCambios`` o un ``Documento``. Si no hay jefe en el
    departamento (o no hay jefe de GG para el rol de autorizador), la cadena
    se crea en ``esperando`` y nadie recibe notificación. Aquí se avisa a los
    administradores para que asignen. Retorna el número de notificaciones
    enviadas.
    """
    from autenticacion.models import PerfilUsuario, Notificacion
    from ..models import PlanillaCambios

    admins = [
        perfil.usuario for perfil in PerfilUsuario.objects.filter(
            rol='administrador', usuario__is_active=True,
        ).select_related('usuario')
    ]
    if not admins:
        return 0
    es_planilla = isinstance(obj, PlanillaCambios)
    if es_planilla:
        planilla = obj
        documento = planilla.documento
        codigo = planilla.codigo
        titulo = 'Planilla de Cambios sin aprobador asignado'
        mensaje = (
            f'La planilla {codigo} quedó en espera del paso de '
            f'{rol_label} porque no hay usuario asignado a ese rol. '
            f'Asigna un jefe y reenvía la planilla.'
        )
        url = f'/documentos/planilla-cambios/{planilla.uuid}/'
        tipo = 'planilla_pendiente'
    else:
        documento = obj
        codigo = documento.codigo
        es_plantilla = bool(getattr(documento, 'es_plantilla', False))
        etiqueta = 'La plantilla' if es_plantilla else 'El documento'
        titulo = (
            'Plantilla sin aprobador asignado' if es_plantilla
            else 'Documento sin aprobador asignado'
        )
        mensaje = (
            f'{etiqueta} {codigo} quedó en espera del paso de '
            f'{rol_label} porque no hay jefe de departamento asignado. '
            f'Asigna un jefe para que pueda ser aprobada.'
        )
        url = f'/documentos/{documento.tipo_id}/{documento.uuid}/'
        tipo = 'solicitud_aprobacion'
    for admin in admins:
        Notificacion.objects.create(
            usuario=admin,
            titulo=titulo,
            mensaje=mensaje,
            tipo=tipo,
            url_accion=url,
            departamento=getattr(documento, 'departamento', None),
            documento=documento,
        )
    logger.warning(
        "%s %s quedó sin aprobador para el rol '%s' (%s avisados)",
        'Planilla' if es_planilla else 'Documento',
        codigo, rol_label, len(admins),
    )
    return len(admins)
