"""Solicitud y aplicación de cambios de estado de documentos.

ISO 9001:2015 7.5.3.2: inhabilitar, reactivar y renovar son cambios de la
información documentada que exigen UNA sola aprobación y una evidencia
conservada (``SolicitudAprobacionDocumento`` + auditoría):

* **Administrador** y **jefe de Gerencia General**: aplican el cambio
  directamente (registran la solicitud ya aprobada).
* **Jefe de departamento**: lo aprueba el jefe de Gerencia General.
* **Propietario**: lo aprueba el jefe del departamento del documento.

El documento NO cambia de estado mientras la solicitud está pendiente: la
edición sigue permitida y el cambio se aplica al resolver la aprobación.
"""
from typing import NamedTuple, Optional

from django.utils import timezone

from ..models import Departamento, SolicitudAprobacionDocumento, User

#: acciones de cambio de estado que pasan por este servicio
ACCIONES = ('inhabilitar', 'reactivar', 'renovar')

#: etiqueta humana por acción (mensajes, notificaciones y auditoría)
ACCION_LABEL = {
    'inhabilitar': 'inhabilitación',
    'reactivar': 'reactivación',
    'renovar': 'renovación de vencimiento',
}

#: estados de solicitud que siguen sin resolverse
PENDIENTES = ('pendiente', 'esperando')


class ResultadoCambio(NamedTuple):
    """Resultado de :func:`solicitar_cambio_estado`.

    ``codigo`` permite mapear la respuesta HTTP del endpoint de cambio de
    estado: ``ok``/``solicitado`` → 200, ``duplicado`` → 409,
    ``sin_permiso`` → 403, ``invalido``/``sin_departamento`` → 400.
    """
    aplicado: bool
    solicitud: Optional[SolicitudAprobacionDocumento]
    mensaje: str
    aprobador: Optional[User] = None
    codigo: str = 'ok'


def es_jefe_gg(usuario):
    """True si ``usuario`` es el jefe del departamento de Gerencia General."""
    perfil = getattr(usuario, 'perfil', None)
    if not perfil or not perfil.es_jefe:
        return False
    departamento = perfil.departamento
    return bool(departamento and departamento.codigo == 'GG')


def jefe_gerencia_general():
    """Jefe de Gerencia General (autoridad máxima de la cadena). None si falta."""
    gg = Departamento.objects.filter(codigo='GG').select_related('jefe').first()
    return gg.jefe if gg and gg.jefe_id else None


def aprobador_de_cambio(documento, usuario):
    """(aprobador, rol) único para la solicitud de cambio.

    ``(None, None)`` significa que no hay con quién aprobar (queda
    ``'esperando'``); el rol indica qué valores se autocompletan al aprobar.
    """
    perfil = usuario.perfil
    if perfil.es_jefe:
        # Jefe de cualquier departamento → jefe de Gerencia General.
        return jefe_gerencia_general(), 'autorizador'
    departamento = documento.departamento
    jefe_depto = departamento.jefe if departamento and departamento.jefe_id else None
    if jefe_depto and jefe_depto.id != usuario.id:
        return jefe_depto, 'evaluador'
    # Sin jefe asignado en el departamento → Gerencia General.
    return jefe_gerencia_general(), 'autorizador'


def puede_iniciar(usuario, documento):
    """True si el usuario puede iniciar (o aplicar) un cambio de estado.

    Admin y jefe de GG aplican directo; el resto debe solicitar, y solo el
    propietario del documento, el jefe de su departamento o un coordinador con
    permiso de eliminación pueden iniciarlo (mismo criterio que
    ``views.puede_eliminar_documento``).
    """
    perfil = usuario.perfil
    if perfil.es_administrador or es_jefe_gg(usuario):
        return True
    if documento.propietario_id == usuario.id:
        return True
    from ..permissions import es_jefe_de_departamento
    if es_jefe_de_departamento(usuario, documento):
        return True
    if perfil.es_coordinador:
        from ..models import AsignacionDocumento
        asignacion = AsignacionDocumento.objects.filter(
            documento=documento, coordinador=usuario, activa=True,
        ).first()
        if asignacion and asignacion.puede_eliminar:
            return True
    return False


def solicitud_pendiente(documento, accion):
    """Solicitud de cambio sin resolver para ``(documento, accion)``, o None."""
    return (
        SolicitudAprobacionDocumento.objects.filter(
            documento=documento, accion_solicitud=accion, estado__in=PENDIENTES,
        )
        .order_by('-fecha_solicitud')
        .first()
    )


def validar_cambio(documento, accion, hoy=None):
    """Devuelve el motivo por el que el cambio NO es válido, o None si lo es."""
    from datetime import date
    hoy = hoy or date.today()

    if accion == 'inhabilitar':
        if documento.estado == 'obsoleto':
            return 'El documento ya está inhabilitado (obsoleto).'
        return None

    if accion == 'reactivar':
        if documento.estado != 'obsoleto':
            return 'El documento no está obsoleto; no hay nada que reactivar.'
        if documento.fecha_vencimiento and documento.fecha_vencimiento <= hoy:
            return (
                f'El documento venció el {documento.fecha_vencimiento.strftime("%d/%m/%Y")}; '
                'para reactivarlo debes solicitar una renovación de vencimiento.'
            )
        return None

    if accion == 'renovar':
        if documento.estado not in ('activo', 'obsoleto'):
            return 'Solo se renuevan documentos activos u obsoletos.'
        return None

    raise ValueError(f'Acción de cambio no soportada: {accion}')


def solicitar_cambio_estado(documento, usuario, accion, *, observaciones='',
                            solicitud_cambio=None, fecha_propuesta=None,
                            ip_address=None, user_agent=None):
    """Aplica el cambio directamente o crea la solicitud de aprobación.

    Parámetros
    ----------
    documento, usuario, accion
        ``accion`` es una de :data:`ACCIONES`.
    observaciones
        Texto libre que viaja en la solicitud (motivo, estado previo, etc.).
    solicitud_cambio
        ``SolicitudCambio`` (operación ``eliminacion``) que acompaña la
        inhabilitación para generar la planilla SGC-FOR-001. Si el cambio se
        aplica directo se aprueba y se encola la planilla; si queda pendiente
        se conserva ``en_revision`` hasta la aprobación.
    fecha_propuesta
        Nueva fecha de vencimiento (solo ``renovar``).

    Retorna un :class:`ResultadoCambio`.
    """
    if accion not in ACCIONES:
        raise ValueError(f'Acción de cambio no soportada: {accion}')

    label = ACCION_LABEL[accion]

    if not puede_iniciar(usuario, documento):
        return ResultadoCambio(
            False, None,
            'No tienes permisos para solicitar la ' + label + ' de este documento.',
            codigo='sin_permiso',
        )

    motivo = validar_cambio(documento, accion)
    if motivo:
        return ResultadoCambio(False, None, motivo, codigo='invalido')

    existente = solicitud_pendiente(documento, accion)
    if existente:
        return ResultadoCambio(
            False, existente,
            f'Ya existe una solicitud de {label} pendiente para este documento.',
            existente.aprobador, codigo='duplicado',
        )

    directo = usuario.perfil.es_administrador or es_jefe_gg(usuario)
    departamento = documento.departamento or usuario.perfil.departamento
    if departamento is None:
        return ResultadoCambio(
            False, None,
            'El documento no tiene departamento asignado; no se puede tramitar la ' + label + '.',
            codigo='sin_departamento',
        )

    if directo:
        solicitud = SolicitudAprobacionDocumento(
            documento=documento,
            accion_solicitud=accion,
            solicitante=usuario,
            propietario=documento.propietario,
            aprobador=usuario,
            departamento_origen=departamento,
            rol_aprobador='autorizador',
            es_final=True,
            estado='aprobada',
            observaciones=observaciones,
            fecha_propuesta=fecha_propuesta,
        )
        solicitud.save()
        solicitud.fecha_respuesta = timezone.now()
        solicitud.save(update_fields=['fecha_respuesta'])
        solicitud.aplicar_cambio()
        if solicitud_cambio is not None:
            _resolver_solicitud_cambio(solicitud_cambio, aprobada=True)
        return ResultadoCambio(
            True, solicitud,
            f'Documento {"inhabilitado" if accion == "inhabilitar" else ("reactivado" if accion == "reactivar" else "renovado")} correctamente.',
            usuario, codigo='ok',
        )

    aprobador, rol = aprobador_de_cambio(documento, usuario)
    if aprobador is not None and aprobador.id == usuario.id:
        # Nadie se autoaprueba: sin autoridad superior, queda en espera.
        aprobador = None
    solicitud = SolicitudAprobacionDocumento(
        documento=documento,
        accion_solicitud=accion,
        solicitante=usuario,
        propietario=documento.propietario,
        aprobador=aprobador,
        departamento_origen=departamento,
        rol_aprobador=rol or 'evaluador',
        es_final=True,
        estado='pendiente' if aprobador else 'esperando',
        observaciones=observaciones,
        fecha_propuesta=fecha_propuesta,
    )
    solicitud.save()

    if aprobador:
        _notificar_aprobador(solicitud, label, ip_address=ip_address, user_agent=user_agent)
        mensaje = f'Solicitud de {label} enviada a {aprobador.get_full_name() or aprobador.username} para aprobación.'
    else:
        mensaje = (
            f'Solicitud de {label} registrada. No hay jefe de Gerencia General '
            'asignado; quedará en espera de aprobación.'
        )
    return ResultadoCambio(False, solicitud, mensaje, aprobador, codigo='solicitado')


def _resolver_solicitud_cambio(solicitud_cambio, aprobada):
    """Aprueba (y encola la planilla) la ``SolicitudCambio`` de eliminación."""
    solicitud_cambio.estado = 'aprobado' if aprobada else 'rechazado'
    solicitud_cambio.save(update_fields=['estado'])
    if not aprobada:
        return
    from .planillas import encolar_generacion_planilla
    encolar_generacion_planilla(solicitud_cambio)


def _notificar_aprobador(solicitud, label, ip_address=None, user_agent=None):
    from autenticacion.models import Notificacion

    documento = solicitud.documento
    solicitante = solicitud.solicitante
    motivo = f' Motivo: {solicitud.observaciones}' if solicitud.observaciones else ''
    Notificacion.objects.create(
        usuario=solicitud.aprobador,
        titulo=f'Solicitud de {label}',
        mensaje=(
            f'{solicitante.get_full_name() or solicitante.username} solicita la {label} '
            f' del documento "{documento.nombre}" ({documento.codigo}).{motivo}'
            ' Requiere tu aprobación.'
        ),
        tipo='solicitud_aprobacion',
        url_accion=f'/documentos/{documento.tipo_id}/{documento.uuid}/',
        departamento=documento.departamento,
        documento=documento,
    )
