"""Flujo de aprobación de documentos y planillas.

Concentra la creación de cadenas de aprobación y la resolución de solicitudes
para que vistas y API ejecuten el mismo código (SGC-FOR-001/002/005).
"""

from ..jefatura import es_delegado_vigente, es_jefe_efectivo
from ..models import (
    OPERACION_POR_ACCION, AprobacionPlanilla, Departamento,
    SolicitudAprobacionDocumento, SolicitudCambio,
)
from .aprobaciones import (
    avisar_paso_sin_asignar, notificar_planilla_resuelta,
    notificar_aprobador as _notificar_aprobador,
)


def _es_jefe(usuario):
    """True si el solicitante **ejerce jefatura**: rol jefe_departamento o
    delegación vigente (vacaciones).

    Se usa para decidir el modo de aprobación: quien ejerce jefe aprueba en un
    solo paso con Gerencia General (ver ``_es_aprobacion_unica``), lo que
    además evita que un delegado se autoapruebe su propio documento.
    """
    perfil = getattr(usuario, 'perfil', None)
    if perfil and perfil.es_jefe:
        return True
    return es_delegado_vigente(usuario)


def determinar_evaluador(usuario, departamento):
    """Determina el Evaluador (SOLO para solicitantes NO jefes).

    - Jefe del departamento de origen.
    - Sin jefe de departamento → jefe de GG (evitando autoevaluación).
    - Si no hay ninguno → None (la cadena queda en espera).

    Los JEFES no usan este paso: su documento se aprueba en UN solo paso por
    Gerencia General (ver `crear_cadena_aprobacion_documento`).
    """
    gg_jefe = None
    try:
        gg_jefe = Departamento.objects.get(codigo='GG').jefe
    except Departamento.DoesNotExist:
        pass

    if departamento and departamento.jefe and departamento.jefe != usuario:
        return departamento.jefe

    if gg_jefe and gg_jefe != usuario:
        return gg_jefe

    return None


def determinar_autorizador():
    """Determina el Autorizador (jefe de GG). Sin fallback a admin.

    Si no hay jefe de GG, retorna None y la autorización queda en espera.
    """
    try:
        return Departamento.objects.get(codigo='GG').jefe
    except Departamento.DoesNotExist:
        return None


def _es_aprobacion_unica(usuario):
    """True cuando el solicitante es jefe: UN solo aprobador (GG).

    Regla vigente: los jefes ya no pasan por evaluación de un jefe par (SGC);
    Gerencia General es el único aprobador. Si el creador ES el jefe de GG,
    se le genera una tarea pendiente que él mismo aprueba.
    """
    return _es_jefe(usuario)


def crear_cadena_aprobacion_documento(documento, usuario, departamento, accion, **kwargs):
    """Crea la solicitud de aprobación para un documento.

    - Solicitante JEFE → UN solo paso (autorizador/GG, es_final=True).
      Si no hay jefe de GG, el paso queda en 'esperando' con aprobador=None
      hasta que se asigne uno.
    - Solicitante NO jefe → cadena de DOS pasos (evaluador = jefe del depto,
      autorizador = jefe de GG). El evaluador nunca es final; solo el
      autorizador activa el documento.

    Con ``plantilla=True`` (creación de Plantillas) el flujo es SIMPLIFICADO:
    UN solo paso con el jefe/delegado del departamento (sin Gerencia
    General). Si el solicitante ES jefe/delegado, se autoaprueba y el
    documento queda activo; si no, queda pendiente del jefe. Sin jefe en el
    departamento → 'esperando' + aviso a los administradores.

    Retorna la solicitud del primer paso (autorizador si es un solo paso,
    evaluador si es la cadena de dos).
    """
    documentos_kwargs = dict(
        documento=documento,
        accion_solicitud=accion,
        solicitante=usuario,
        propietario=documento.propietario,
        departamento_origen=departamento,
    )
    es_plantilla = kwargs.pop('plantilla', False)
    documentos_kwargs.update(kwargs)

    if es_plantilla and accion == 'creacion':
        return _crear_solicitud_plantilla(documento, usuario, departamento,
                                          documentos_kwargs)

    if _es_aprobacion_unica(usuario):
        autorizador = determinar_autorizador()
        sol_unica = SolicitudAprobacionDocumento.objects.create(
            aprobador=autorizador, rol_aprobador='autorizador', es_final=True,
            estado='pendiente' if autorizador else 'esperando',
            **documentos_kwargs,
        )
        if autorizador and autorizador != usuario:
            _notificar_aprobador(autorizador, usuario, documento, es_evaluador=False)
        return sol_unica

    evaluador = determinar_evaluador(usuario, departamento)
    autorizador = determinar_autorizador()

    sol_eval = SolicitudAprobacionDocumento.objects.create(
        aprobador=evaluador, rol_aprobador='evaluador', es_final=False,
        estado='pendiente' if evaluador else 'esperando', **documentos_kwargs,
    )
    SolicitudAprobacionDocumento.objects.create(
        aprobador=autorizador, rol_aprobador='autorizador', es_final=True,
        estado='esperando', **documentos_kwargs,
    )
    if evaluador:
        _notificar_aprobador(evaluador, usuario, documento, es_evaluador=True)
    return sol_eval


def _aprobador_plantilla(usuario, departamento):
    """Decide quién aprueba una plantilla (flujo simplificado, sin GG).

    Retorna ``(aprobador, es_directo)``:

    - Quien ejerce jefatura/delegación en el departamento → ``(usuario, True)``.
    - Jefe natural distinto → ``(jefe, False)`` (queda pendiente).
    - Sin jefe → ``(None, False)`` (queda ``'esperando'`` + aviso a admins).
    """
    if es_jefe_efectivo(usuario, departamento):
        return usuario, True
    jefe_depto = getattr(departamento, 'jefe', None)
    if jefe_depto and jefe_depto != usuario:
        return jefe_depto, False
    return None, False


def _crear_solicitud_plantilla(documento, usuario, departamento, documentos_kwargs):
    """Flujo simplificado de aprobación de PLANTILLAS (creación).

    Regla: UN solo paso con el jefe/delegado del departamento del que subió
    la plantilla. Sin Gerencia General.

    - Creador jefe/delegado del depto → auto-aprobación (evidencia ISO:
      fila SolicitudAprobacionDocumento aprobada) y el documento queda
      activo.
    - Creador no jefe → pendiente del jefe natural del depto.
    - Sin jefe en el depto → 'esperando' + aviso a los administradores.
    """
    aprobador, directo = _aprobador_plantilla(usuario, departamento)
    if directo:
        sol = SolicitudAprobacionDocumento.objects.create(
            aprobador=aprobador, rol_aprobador='autorizador', es_final=True,
            estado='pendiente', **documentos_kwargs,
        )
        # Las plantillas no emiten firma SES (§31.6): solo estado + auditoría.
        sol.aprobar(observaciones=(
            'Aprobación directa: quien subió la plantilla ejerce jefatura '
            'en el departamento.'
        ))
        return sol

    if aprobador:
        sol = SolicitudAprobacionDocumento.objects.create(
            aprobador=aprobador, rol_aprobador='autorizador', es_final=True,
            estado='pendiente', **documentos_kwargs,
        )
        _notificar_aprobador(aprobador, usuario, documento, es_evaluador=False)
        return sol

    sol = SolicitudAprobacionDocumento.objects.create(
        aprobador=None, rol_aprobador='autorizador', es_final=True,
        estado='esperando', **documentos_kwargs,
    )
    avisar_paso_sin_asignar(documento, 'aprobación de plantillas')
    return sol


def crear_cadena_aprobacion_planilla(planilla, usuario, documento, **kwargs):
    """Crea la solicitud de aprobación para una planilla de cambios.

    Mismo criterio que documentos: UN solo paso (GG) si el responsable es
    jefe; cadena de DOS pasos si no lo es. Retorna la aprobación del primer
    paso.

    Para PLANTILLAS el flujo es SIMPLIFICADO (sin Gerencia General): UN solo
    paso con el jefe/delegado del departamento del documento. Si el
    responsable ES jefe/delegado, se autoaprueba y los cambios se aplican de
    inmediato; si no, queda pendiente del jefe; sin jefe → 'esperando' +
    aviso a los administradores.
    """
    departamento = getattr(documento, 'departamento', None)

    if getattr(documento, 'es_plantilla', False):
        return _crear_aprobacion_planilla_plantilla(
            planilla, usuario, documento, departamento, kwargs,
        )

    if _es_aprobacion_unica(usuario):
        autorizador = determinar_autorizador()
        ap_unica = AprobacionPlanilla.objects.create(
            planilla=planilla, aprobador=autorizador,
            rol_aprobador='autorizador', es_final=True,
            estado='pendiente' if autorizador else 'esperando', **kwargs,
        )
        if autorizador and autorizador != usuario:
            _notificar_aprobador(
                autorizador, usuario, documento,
                es_evaluador=False, planilla=planilla,
            )
        elif not autorizador:
            avisar_paso_sin_asignar(planilla, 'autorización')
        return ap_unica

    evaluador = determinar_evaluador(usuario, departamento)
    autorizador = determinar_autorizador()

    ap_eval = AprobacionPlanilla.objects.create(
        planilla=planilla, aprobador=evaluador,
        rol_aprobador='evaluador', es_final=False,
        estado='pendiente' if evaluador else 'esperando', **kwargs,
    )
    AprobacionPlanilla.objects.create(
        planilla=planilla, aprobador=autorizador,
        rol_aprobador='autorizador', es_final=True,
        estado='esperando', **kwargs,
    )
    if evaluador:
        _notificar_aprobador(evaluador, usuario, documento, es_evaluador=True, planilla=planilla)
    else:
        avisar_paso_sin_asignar(planilla, 'evaluación')
    return ap_eval


def _crear_aprobacion_planilla_plantilla(planilla, usuario, documento,
                                         departamento, kwargs):
    """Aprobación simplificada de planillas sobre PLANTILLAS (FOR-005).

    UN solo paso con el jefe/delegado del departamento. Sin Gerencia
    General. Si el responsable es jefe/delegado → auto-aprobación y los
    cambios se aplican de inmediato (evidencia: fila AprobacionPlanilla
    aprobada + auditoría ISO).
    """
    aprobador, directo = _aprobador_plantilla(usuario, departamento)
    if directo:
        from .aprobaciones import (
            notificar_planilla_resuelta, resolver_aprobacion_planilla,
        )

        ap = AprobacionPlanilla.objects.create(
            planilla=planilla, aprobador=aprobador,
            rol_aprobador='autorizador', es_final=True,
            estado='pendiente', **kwargs,
        )
        observaciones = (
            'Aprobación directa: quien propuso el cambio ejerce jefatura '
            'en el departamento de la plantilla.'
        )
        # Las plantillas no emiten firma SES (§31.6): resolver directo.
        resolver_aprobacion_planilla(
            ap, usuario, aprobada=True, observaciones=observaciones,
            firma=None,
        )
        if planilla.responsable_id != usuario.id:
            notificar_planilla_resuelta(
                planilla, True,
                observaciones=ap.observaciones or observaciones,
            )
        return ap

    if aprobador:
        ap = AprobacionPlanilla.objects.create(
            planilla=planilla, aprobador=aprobador,
            rol_aprobador='autorizador', es_final=True,
            estado='pendiente', **kwargs,
        )
        _notificar_aprobador(
            aprobador, usuario, documento, es_evaluador=False, planilla=planilla,
        )
        return ap

    ap = AprobacionPlanilla.objects.create(
        planilla=planilla, aprobador=None,
        rol_aprobador='autorizador', es_final=True,
        estado='esperando', **kwargs,
    )
    avisar_paso_sin_asignar(planilla, 'aprobación de plantillas')
    return ap


def _mensajes_aprobacion(solicitud):
    """(titulo, mensaje) de la notificación al resolver una aprobación."""
    documento = solicitud.documento
    ref = f'"{documento.nombre}" ({documento.codigo})'
    accion = solicitud.accion_solicitud
    if accion == 'inhabilitar':
        return 'Documento inhabilitado', f'Tu documento {ref} ha sido inhabilitado.'
    if accion == 'reactivar':
        return 'Documento reactivado', f'Tu documento {ref} ha sido reactivado.'
    if accion == 'renovar':
        hasta = f' hasta el {solicitud.fecha_propuesta}' if solicitud.fecha_propuesta else ''
        return (
            'Vencimiento renovado',
            f'El vencimiento de tu documento {ref} fue renovado{hasta}.',
        )
    if accion == 'eliminar_version':
        return (
            'Versión eliminada',
            f'La versión seleccionada de tu documento {ref} ha sido eliminada.',
        )
    return 'Documento aprobado', f'Tu documento {ref} ha sido aprobado.'


def _auditar_resolucion(request, solicitud, resuelta, observaciones=''):
    """Registro ISO (con IP y user-agent) de aprobar/rechazar una solicitud."""
    try:
        from auditoria.middleware import resolver_ip_cliente
        from auditoria.services import registrar_log_iso
        registrar_log_iso(
            usuario=request.user if request else solicitud.aprobador,
            accion_tipo='aprobacion' if resuelta else 'rechazo',
            accion_categoria='documento',
            recurso_tipo=solicitud.documento.tipo_id or '',
            recurso_id=str(solicitud.documento.uuid),
            recurso_codigo=solicitud.documento.codigo,
            recurso_nombre=solicitud.documento.nombre,
            detalles={
                'accion_solicitud': solicitud.accion_solicitud,
                'rol_aprobador': solicitud.rol_aprobador,
                'observaciones': observaciones,
            },
            resultado_exitoso=resuelta,
            resultado_mensaje=(
                f'Solicitud {solicitud.accion_solicitud} '
                f'{"aprobada" if resuelta else "rechazada"}'
            ),
            ip_address=resolver_ip_cliente(request) if request else None,
            user_agent=(
                request.META.get('HTTP_USER_AGENT') if request else ''
            ),
        )
    except Exception:
        pass


def resolver_solicitud_documento(request, solicitud, *, aprobada,
                                 observaciones='', firma=None):
    """Aprueba o rechaza una ``SolicitudAprobacionDocumento`` y sus efectos.

    Incluye el gate de planillas por operación, la notificación al solicitante
    y la auditoría de resolución. El llamador debe estar dentro de una
    transacción (la API ya lo está).

    Exige ``firma`` (``FirmaElectronica`` del paso) salvo en PLANTILLAS
    (``documento.es_plantilla=True``): aprobar una plantilla no emite firma.
    Las rutas humanas de documentos no-plantilla la obtienen con
    ``services.firmas.firmar``.
    """
    from autenticacion.models import Notificacion

    from .cambios_estado import ACCION_LABEL
    from .firmas import ErrorFirma
    from .planillas import encolar_generacion_planilla

    es_plantilla = bool(getattr(solicitud.documento, 'es_plantilla', False))
    if aprobada and firma is None and not es_plantilla:
        raise ErrorFirma('Falta la firma electrónica de la aprobación.')

    accion = solicitud.accion_solicitud
    documento = solicitud.documento
    if aprobada:
        es_final = solicitud.es_final
        solicitud.aprobar(observaciones=observaciones)
        if not es_final:
            return {
                'es_final': False,
                'mensaje': 'Evaluación registrada, pendiente de autorización',
            }

        operacion = OPERACION_POR_ACCION.get(accion)
        if operacion:
            solicitudes_planilla = list(SolicitudCambio.objects.filter(
                documento=documento, estado='en_revision', operacion=operacion,
            ))
            SolicitudCambio.objects.filter(
                documento=documento, estado='en_revision', operacion=operacion,
            ).update(estado='aprobado')
            for sc in solicitudes_planilla:
                encolar_generacion_planilla(sc)

        usuario_a_notificar = solicitud.propietario or solicitud.solicitante
        titulo, mensaje = _mensajes_aprobacion(solicitud)
        Notificacion.objects.create(
            usuario=usuario_a_notificar, titulo=titulo,
            mensaje=mensaje + (f' {observaciones}' if observaciones else ''),
            tipo='aprobacion_documento',
            url_accion=f'/documentos/{documento.tipo_id}/{documento.uuid}/',
            departamento=documento.departamento,
            documento=documento,
        )
        _auditar_resolucion(request, solicitud, True, observaciones)
        return {'es_final': True, 'mensaje': mensaje}

    solicitud.rechazar(motivo=observaciones)
    operacion = OPERACION_POR_ACCION.get(accion)
    if operacion:
        SolicitudCambio.objects.filter(
            documento=documento, estado='en_revision', operacion=operacion,
        ).update(estado='rechazado')

    usuario_a_notificar = solicitud.propietario or solicitud.solicitante
    if accion in ('inhabilitar', 'reactivar', 'renovar'):
        titulo = f'Solicitud de {ACCION_LABEL[accion]} rechazada'
        mensaje = (
            f'Su solicitud para el documento "{documento.nombre}" '
            f'({documento.codigo}) fue rechazada. Motivo: {observaciones}'
        )
    else:
        titulo = 'Documento rechazado'
        mensaje = (
            f'Tu documento "{documento.nombre}" ({documento.codigo}) '
            f'ha sido rechazado. Motivo: {observaciones}'
        )
    Notificacion.objects.create(
        usuario=usuario_a_notificar, titulo=titulo, mensaje=mensaje,
        tipo='rechazo_documento',
        url_accion=f'/documentos/{documento.tipo_id}/{documento.uuid}/',
        departamento=documento.departamento,
        documento=documento,
    )
    _auditar_resolucion(request, solicitud, False, observaciones)
    return {'es_final': True, 'mensaje': 'Solicitud rechazada correctamente'}
