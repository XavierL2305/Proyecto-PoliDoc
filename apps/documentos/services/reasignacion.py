import logging
from django.db import transaction

logger = logging.getLogger(__name__)


def obtener_admin_fallback():
    """(Legacy) Retorna el primer admin activo o None.

    Ya no se usa como fallback de aprobación: el flujo es estricto (solo
    jefes de departamento y de GG). Se mantiene por compatibilidad.
    """
    from autenticacion.models import PerfilUsuario
    perfil = (PerfilUsuario.objects
              .filter(rol='administrador', usuario__is_active=True)
              .select_related('usuario')
              .order_by('id')
              .first())
    return perfil.usuario if perfil else None


def reasignar_pendientes_departamento(departamento, nuevo_aprobador, actor):
    """Gestiona los pasos evaluador de un departamento (documentos y planillas).

    - Con ``nuevo_aprobador``: asigna los pasos en espera (aprobador=None) y
      reasigna los pendientes a ese jefe, dejándolos listos para evaluar.
      También reasigna descargas y solicitudes de acceso pendientes.
    - Sin ``nuevo_aprobador``: los pasos evaluador pendientes vuelven a
      ``esperando`` con ``aprobador=None`` hasta que se asigne un jefe.
    """
    from documentos.models import (
        SolicitudAprobacionDocumento, AprobacionPlanilla,
        AprobacionDescarga, SolicitudAccesoDocumento,
    )

    docs_afectados = 0
    planillas_afectadas = 0
    descargas_reasignadas = 0
    accesos_reasignados = 0

    with transaction.atomic():
        # === Documentos (evaluador) ===
        qs_docs = SolicitudAprobacionDocumento.objects.filter(
            departamento_origen=departamento,
            rol_aprobador='evaluador',
            estado__in=('pendiente', 'esperando'),
        )
        if nuevo_aprobador:
            qs_docs = (qs_docs.exclude(aprobador=nuevo_aprobador)
                       .exclude(solicitante=nuevo_aprobador)
                       .exclude(propietario=nuevo_aprobador))
        else:
            qs_docs = qs_docs.filter(estado='pendiente')

        for sol in qs_docs.select_related('solicitante', 'propietario', 'documento'):
            anterior = sol.aprobador
            if nuevo_aprobador:
                sol.aprobador = nuevo_aprobador
                sol.estado = 'pendiente'
                sol.save(update_fields=['aprobador', 'estado'])
                _notificar_reasignacion_destino(nuevo_aprobador, sol)
                _marcar_notif_previas_como_leidas(
                    anterior, sol.documento, tipo='solicitud_aprobacion')
                _auditar_reasignacion(actor, sol, anterior, nuevo_aprobador)
            else:
                sol.aprobador = None
                sol.estado = 'esperando'
                sol.save(update_fields=['aprobador', 'estado'])
                _auditar_reasignacion_espera(actor, sol, anterior, departamento)
            docs_afectados += 1

        # === Planillas (evaluador) ===
        qs_planillas = AprobacionPlanilla.objects.filter(
            planilla__documento__departamento=departamento,
            rol_aprobador='evaluador',
            estado__in=('pendiente', 'esperando'),
        )
        if nuevo_aprobador:
            qs_planillas = (qs_planillas.exclude(aprobador=nuevo_aprobador)
                            .exclude(planilla__responsable=nuevo_aprobador))
        else:
            qs_planillas = qs_planillas.filter(estado='pendiente')

        for ap in qs_planillas.select_related('planilla', 'planilla__documento',
                                              'aprobador'):
            anterior = ap.aprobador
            if nuevo_aprobador:
                if AprobacionPlanilla.objects.filter(
                    planilla=ap.planilla, aprobador=nuevo_aprobador
                ).exists():
                    ap.estado = 'cancelada'
                    ap.save(update_fields=['estado'])
                    planillas_afectadas += 1
                    continue
                ap.aprobador = nuevo_aprobador
                ap.estado = 'pendiente'
                ap.save(update_fields=['aprobador', 'estado'])
                _notificar_reasignacion_planilla(nuevo_aprobador, ap)
                _marcar_notif_previas_como_leidas(
                    anterior, ap.planilla.documento,
                    url_accion_prefix=f'/documentos/planilla-cambios/{ap.planilla.uuid}/',
                    tipo='solicitud_aprobacion',
                )
                _auditar_reasignacion_planilla(actor, ap, anterior, nuevo_aprobador)
            else:
                ap.aprobador = None
                ap.estado = 'esperando'
                ap.save(update_fields=['aprobador', 'estado'])
                _auditar_reasignacion_planilla_espera(actor, ap, anterior, departamento)
            planillas_afectadas += 1

        # === Descargas y accesos (solo con destino disponible) ===
        if nuevo_aprobador:
            qs_descargas = (AprobacionDescarga.objects
                            .filter(
                                solicitud__documento__departamento=departamento,
                                solicitud__estado='pendiente',
                                estado='pendiente',
                            )
                            .exclude(aprobador=nuevo_aprobador)
                            .exclude(solicitud__solicitado_por=nuevo_aprobador))
            for desc in qs_descargas.select_related('solicitud', 'solicitud__documento',
                                                    'solicitud__solicitado_por', 'aprobador'):
                if AprobacionDescarga.objects.filter(
                    solicitud=desc.solicitud, aprobador=nuevo_aprobador
                ).exists():
                    desc.delete()
                    continue
                anterior = desc.aprobador
                desc.aprobador = nuevo_aprobador
                desc.save(update_fields=['aprobador'])
                descargas_reasignadas += 1
                _notificar_reasignacion_descarga(nuevo_aprobador, desc)
                _marcar_notif_previas_como_leidas(
                    anterior, desc.solicitud.documento, tipo='solicitud_descarga')
                _auditar_reasignacion_descarga(actor, desc, anterior, nuevo_aprobador)

            qs_accesos = (SolicitudAccesoDocumento.objects
                          .filter(
                              departamento_destino=departamento,
                              estado='pendiente',
                          )
                          .exclude(documento__isnull=True)
                          .exclude(jefe_destino=nuevo_aprobador)
                          .exclude(solicitante=nuevo_aprobador))
            for acc in qs_accesos.select_related('solicitante', 'documento'):
                anterior = acc.jefe_destino
                acc.jefe_destino = nuevo_aprobador
                acc.save(update_fields=['jefe_destino'])
                accesos_reasignados += 1
                _notificar_reasignacion_acceso(nuevo_aprobador, acc)
                _marcar_notif_previas_como_leidas(
                    anterior, acc.documento, tipo='sistema', departamento=departamento)
                _auditar_reasignacion_acceso(actor, acc, anterior, nuevo_aprobador)

    destino_nombre = nuevo_aprobador.username if nuevo_aprobador else 'esperando_jefe'
    logger.info(
        f'reasignar_pendientes({departamento.codigo}): {docs_afectados} docs, '
        f'{planillas_afectadas} planillas, {descargas_reasignadas} descargas, '
        f'{accesos_reasignados} accesos → {destino_nombre} '
        f'(actor: {actor.username if actor else "sistema"})'
    )
    return {
        'documentos': docs_afectados,
        'planillas': planillas_afectadas,
        'descargas': descargas_reasignadas,
        'accesos': accesos_reasignados,
        'destino': destino_nombre,
    }


def reasignar_autorizadores_gg(nuevo_jefe_gg, actor):
    """Asigna todos los pasos autorizador pendientes/en espera al jefe de GG.

    Los pasos cuyo evaluador ya aprobó pasan a 'pendiente' y notifican al
    jefe de GG; los que aún no fueron evaluados quedan en 'esperando'.
    Aplica a documentos y planillas.
    """
    from documentos.models import SolicitudAprobacionDocumento, AprobacionPlanilla

    docs = 0
    planillas = 0
    with transaction.atomic():
        qs_docs = (SolicitudAprobacionDocumento.objects
                   .filter(rol_aprobador='autorizador',
                           estado__in=('pendiente', 'esperando'))
                   .exclude(aprobador=nuevo_jefe_gg)
                   .select_related('documento'))
        for sol in qs_docs:
            anterior = sol.aprobador
            evaluador = SolicitudAprobacionDocumento.objects.filter(
                documento=sol.documento,
                accion_solicitud=sol.accion_solicitud,
                rol_aprobador='evaluador',
            ).order_by('id').first()
            evaluado = bool(evaluador and evaluador.estado == 'aprobada')
            sol.aprobador = nuevo_jefe_gg
            sol.estado = 'pendiente' if evaluado else 'esperando'
            sol.save(update_fields=['aprobador', 'estado'])
            docs += 1
            if evaluado:
                _notificar_autorizacion_pendiente(nuevo_jefe_gg, sol)
            _auditar_reasignacion(actor, sol, anterior, nuevo_jefe_gg)

        qs_planillas = (AprobacionPlanilla.objects
                        .filter(rol_aprobador='autorizador',
                                estado__in=('pendiente', 'esperando'))
                        .exclude(aprobador=nuevo_jefe_gg)
                        .select_related('planilla', 'planilla__documento'))
        for ap in qs_planillas:
            if AprobacionPlanilla.objects.filter(
                planilla=ap.planilla, aprobador=nuevo_jefe_gg
            ).exists():
                ap.estado = 'cancelada'
                ap.save(update_fields=['estado'])
                continue
            anterior = ap.aprobador
            evaluador = AprobacionPlanilla.objects.filter(
                planilla=ap.planilla, rol_aprobador='evaluador',
            ).order_by('id').first()
            evaluado = bool(evaluador and evaluador.estado == 'aprobada')
            ap.aprobador = nuevo_jefe_gg
            ap.estado = 'pendiente' if evaluado else 'esperando'
            ap.save(update_fields=['aprobador', 'estado'])
            planillas += 1
            if evaluado:
                _notificar_autorizacion_pendiente_planilla(nuevo_jefe_gg, ap)
            _auditar_reasignacion_planilla(actor, ap, anterior, nuevo_jefe_gg)

    logger.info(
        f'reasignar_autorizadores_gg → {nuevo_jefe_gg.username}: '
        f'{docs} docs, {planillas} planillas (actor: {actor.username if actor else "sistema"})'
    )
    return {'documentos': docs, 'planillas': planillas}


def desasignar_autorizadores_gg(anterior_jefe, actor):
    """El jefe de GG salió: sus pasos autorizador vuelven a espera (sin asignar)."""
    from documentos.models import SolicitudAprobacionDocumento, AprobacionPlanilla

    with transaction.atomic():
        qs_docs = (SolicitudAprobacionDocumento.objects
                   .filter(rol_aprobador='autorizador', aprobador=anterior_jefe,
                           estado__in=('pendiente', 'esperando')))
        for sol in qs_docs:
            sol.aprobador = None
            sol.estado = 'esperando'
            sol.save(update_fields=['aprobador', 'estado'])
            _auditar_reasignacion_espera(actor, sol, anterior_jefe,
                                         sol.departamento_origen)

        qs_planillas = (AprobacionPlanilla.objects
                        .filter(rol_aprobador='autorizador', aprobador=anterior_jefe,
                                estado__in=('pendiente', 'esperando')))
        for ap in qs_planillas:
            ap.aprobador = None
            ap.estado = 'esperando'
            ap.save(update_fields=['aprobador', 'estado'])
            _auditar_reasignacion_planilla_espera(
                actor, ap, anterior_jefe, ap.planilla.documento.departamento)


def reasignar_solicitud(solicitud, nuevo_aprobador, actor):
    """Reasigna una solicitud individual (uso puntual)."""
    if solicitud.aprobador_id == nuevo_aprobador.id:
        return

    anterior = solicitud.aprobador
    with transaction.atomic():
        solicitud.aprobador = nuevo_aprobador
        solicitud.save(update_fields=['aprobador'])

        _notificar_reasignacion_destino(nuevo_aprobador, solicitud)
        _marcar_notif_previas_como_leidas(
            anterior, solicitud.documento, tipo='solicitud_aprobacion')
        _auditar_reasignacion(actor, solicitud, anterior, nuevo_aprobador)


# ---------------------------------------------------------------------------
# Helpers internos (notificaciones + auditoría)
# ---------------------------------------------------------------------------

def _notificar_reasignacion_destino(destino, sol):
    """Crea notificación para el aprobador destino por solicitud de documento."""
    from autenticacion.models import Notificacion
    doc = sol.documento
    Notificacion.objects.create(
        usuario=destino,
        titulo='Documento pendiente de evaluación',
        mensaje=(
            f'El documento "{doc.nombre}" ({doc.codigo}) ha sido reasignado a tu '
            f'departamento y requiere tu evaluación.'
        ),
        tipo='solicitud_aprobacion',
        url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
        departamento=doc.departamento,
        documento=doc,
    )


def _notificar_autorizacion_pendiente(destino, sol):
    """Notifica al jefe de GG que un documento evaluado requiere autorización."""
    from autenticacion.models import Notificacion
    doc = sol.documento
    Notificacion.objects.create(
        usuario=destino,
        titulo='Documento pendiente de autorización',
        mensaje=(
            f'El documento "{doc.nombre}" ({doc.codigo}) ha sido evaluado y '
            f'requiere tu autorización.'
        ),
        tipo='solicitud_aprobacion',
        url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
        departamento=doc.departamento,
        documento=doc,
    )


def _notificar_reasignacion_planilla(destino, ap):
    """Crea notificación para el aprobador destino por solicitud de planilla."""
    from autenticacion.models import Notificacion
    planilla = ap.planilla
    doc = planilla.documento
    Notificacion.objects.create(
        usuario=destino,
        titulo='Edición de documento pendiente de evaluación',
        mensaje=(
            f'La planilla "{planilla.codigo}" ({doc.nombre}) ha sido reasignada '
            f'a tu departamento y requiere tu evaluación.'
        ),
        tipo='solicitud_aprobacion',
        url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/aprobar/',
        departamento=doc.departamento,
        documento=doc,
    )


def _notificar_autorizacion_pendiente_planilla(destino, ap):
    """Notifica al jefe de GG que una planilla evaluada requiere autorización."""
    from autenticacion.models import Notificacion
    planilla = ap.planilla
    doc = planilla.documento
    Notificacion.objects.create(
        usuario=destino,
        titulo='Edición de documento pendiente de autorización',
        mensaje=(
            f'La planilla "{planilla.codigo}" ({doc.nombre}) ha sido evaluada y '
            f'requiere tu autorización.'
        ),
        tipo='solicitud_aprobacion',
        url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/aprobar/',
        departamento=doc.departamento,
        documento=doc,
    )


def _notificar_reasignacion_descarga(destino, desc):
    """Crea notificación para el aprobador destino por solicitud de descarga."""
    from autenticacion.models import Notificacion
    sol = desc.solicitud
    doc = sol.documento
    Notificacion.objects.create(
        usuario=destino,
        titulo='Solicitud de descarga pendiente',
        mensaje=(
            f'{sol.solicitado_por.get_full_name() or sol.solicitado_por.username} '
            f'solicita descargar "{doc.nombre}" ({doc.codigo}). '
            f'Requiere tu aprobación.'
        ),
        tipo='solicitud_descarga',
        url_accion=f'/documentos/solicitud/{sol.id}/aprobar/',
        departamento=doc.departamento,
        documento=doc,
    )


def _notificar_reasignacion_acceso(destino, acc):
    """Crea notificación para el jefe destino por solicitud de acceso."""
    from autenticacion.models import Notificacion
    doc = acc.documento
    Notificacion.objects.create(
        usuario=destino,
        titulo='Solicitud de acceso a documento',
        mensaje=(
            f'{acc.solicitante.get_full_name() or acc.solicitante.username} '
            f'solicita acceso al documento "{doc.nombre}" ({doc.codigo}). '
            f'Requiere tu respuesta.'
        ),
        tipo='sistema',
        url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
        departamento=doc.departamento,
        documento=doc,
    )


def _marcar_notif_previas_como_leidas(anterior, documento=None,
                                      url_accion_prefix=None,
                                      tipo=None, departamento=None):
    """Marca como leídas las notificaciones pendientes del aprobador anterior."""
    if anterior is None:
        return
    from autenticacion.models import Notificacion
    qs = Notificacion.objects.filter(usuario=anterior, leida=False)
    if tipo:
        qs = qs.filter(tipo=tipo)
    if documento:
        qs = qs.filter(documento=documento)
    if url_accion_prefix:
        qs = qs.filter(url_accion__startswith=url_accion_prefix)
    if departamento:
        qs = qs.filter(departamento=departamento)
    qs.update(leida=True)


def _auditar_reasignacion(actor, sol, anterior, destino):
    """Auditoría ISO de reasignación de solicitud de documento."""
    from auditoria.services import registrar_log_iso
    doc = sol.documento
    registrar_log_iso(
        usuario=actor,
        accion_tipo='reasignacion',
        accion_categoria='documento',
        recurso_tipo=doc.tipo_id or '',
        recurso_id=doc.id,
        recurso_codigo=doc.codigo,
        recurso_nombre=doc.nombre,
        estado_anterior=f'aprobador:{_username(anterior)}',
        estado_posterior=f'aprobador:{_username(destino)}',
        detalles={
            'solicitud_id': sol.id,
            'documento_codigo': doc.codigo,
            'aprobador_anterior': _username(anterior),
            'aprobador_nuevo': _username(destino),
            'motivo': 'cambio_jefe_departamento',
        },
    )


def _auditar_reasignacion_espera(actor, sol, anterior, departamento):
    """Auditoría cuando un paso evaluador queda en espera de jefe."""
    from auditoria.services import registrar_log_iso
    doc = sol.documento
    registrar_log_iso(
        usuario=actor,
        accion_tipo='reasignacion',
        accion_categoria='documento',
        recurso_tipo=doc.tipo_id or '',
        recurso_id=doc.id,
        recurso_codigo=doc.codigo,
        recurso_nombre=doc.nombre,
        estado_anterior=f'aprobador:{_username(anterior)}',
        estado_posterior='aprobador:esperando_jefe',
        detalles={
            'solicitud_id': sol.id,
            'documento_codigo': doc.codigo,
            'departamento': departamento.codigo if departamento else '',
            'motivo': 'sin_jefe_departamento',
        },
    )


def _auditar_reasignacion_planilla(actor, ap, anterior, destino):
    """Auditoría ISO de reasignación de solicitud de planilla."""
    from auditoria.services import registrar_log_iso
    planilla = ap.planilla
    doc = planilla.documento
    registrar_log_iso(
        usuario=actor,
        accion_tipo='reasignacion',
        accion_categoria='documento',
        recurso_tipo='planilla',
        recurso_id=planilla.id,
        recurso_codigo=planilla.codigo,
        recurso_nombre=doc.nombre,
        estado_anterior=f'aprobador:{_username(anterior)}',
        estado_posterior=f'aprobador:{_username(destino)}',
        detalles={
            'planilla_id': planilla.id,
            'planilla_codigo': planilla.codigo,
            'documento_codigo': doc.codigo,
            'aprobador_anterior': _username(anterior),
            'aprobador_nuevo': _username(destino),
            'motivo': 'cambio_jefe_departamento',
        },
    )


def _auditar_reasignacion_planilla_espera(actor, ap, anterior, departamento):
    """Auditoría cuando un paso evaluador de planilla queda en espera de jefe."""
    from auditoria.services import registrar_log_iso
    planilla = ap.planilla
    doc = planilla.documento
    registrar_log_iso(
        usuario=actor,
        accion_tipo='reasignacion',
        accion_categoria='documento',
        recurso_tipo='planilla',
        recurso_id=planilla.id,
        recurso_codigo=planilla.codigo,
        recurso_nombre=doc.nombre,
        estado_anterior=f'aprobador:{_username(anterior)}',
        estado_posterior='aprobador:esperando_jefe',
        detalles={
            'planilla_id': planilla.id,
            'planilla_codigo': planilla.codigo,
            'departamento': departamento.codigo if departamento else '',
            'motivo': 'sin_jefe_departamento',
        },
    )


def _auditar_reasignacion_descarga(actor, desc, anterior, destino):
    """Auditoría ISO de reasignación de solicitud de descarga."""
    from auditoria.services import registrar_log_iso
    sol = desc.solicitud
    doc = sol.documento
    registrar_log_iso(
        usuario=actor,
        accion_tipo='reasignacion',
        accion_categoria='documento',
        recurso_tipo='descarga',
        recurso_id=sol.id,
        recurso_codigo=doc.codigo,
        recurso_nombre=doc.nombre,
        estado_anterior=f'aprobador:{_username(anterior)}',
        estado_posterior=f'aprobador:{_username(destino)}',
        detalles={
            'solicitud_descarga_id': sol.id,
            'documento_codigo': doc.codigo,
            'aprobador_anterior': _username(anterior),
            'aprobador_nuevo': _username(destino),
            'motivo': 'cambio_jefe_departamento',
        },
    )


def _auditar_reasignacion_acceso(actor, acc, anterior, destino):
    """Auditoría ISO de reasignación de solicitud de acceso."""
    from auditoria.services import registrar_log_iso
    doc = acc.documento
    registrar_log_iso(
        usuario=actor,
        accion_tipo='reasignacion',
        accion_categoria='documento',
        recurso_tipo='acceso',
        recurso_id=acc.id,
        recurso_codigo=doc.codigo,
        recurso_nombre=doc.nombre,
        estado_anterior=f'jefe_destino:{_username(anterior)}',
        estado_posterior=f'jefe_destino:{_username(destino)}',
        detalles={
            'solicitud_acceso_id': acc.id,
            'documento_codigo': doc.codigo,
            'jefe_destino_anterior': _username(anterior),
            'jefe_destino_nuevo': _username(destino),
            'motivo': 'cambio_jefe_departamento',
        },
    )


def _username(user):
    return user.username if user else 'sin_asignar'
