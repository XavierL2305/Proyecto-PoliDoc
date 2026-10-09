"""Solicitud y aprobación de descargas de documentos.

ISO 9001:2015 7.5.3: la copia de un documento solo sale del control del
organismo con una **autorización temporal, trazable y revocable**. Este
módulo es la fuente única de verdad del flujo:

* **Alta** -- :func:`crear_solicitud_descarga` crea la solicitud y una
  ``AprobacionDescarga`` por cada aprobador requerido, con notificación a
  cada uno. Documento ``restringido`` exige doble control (jefe +
  autorizador).
* **Resolución** -- :func:`resolver_aprobacion` registra la decisión de un
  aprobador dentro de una transacción bloqueada. Una solicitud ya
  resuelta **no puede procesarse dos veces**: no se re-finaliza, no se
  duplica la distribución y no se reinicia la vigencia. Un rechazo
  cancela las aprobaciones hermanas; la aprobación completa fija vigencia
  y crea la distribución **una sola vez**.
"""
import logging
from datetime import timedelta
from typing import NamedTuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import AprobacionDescarga, DistribucionDocumento, SolicitudDescarga

logger = logging.getLogger(__name__)

#: estados de aprobación que siguen requiriendo decisión
APROBACIONES_PENDIENTES = ('pendiente',)


class ResultadoDescarga(NamedTuple):
    """Resultado de alta/resolución de una descarga.

    ``codigo`` permite mapear la respuesta HTTP:

    * ``ok`` -- aprobación completa (vigencia + distribución).
    * ``parcial`` -- aprobación registrada, falta el otro control.
    * ``rechazada`` -- rechazo con cancelación de las aprobaciones hermanas.
    * ``sin_aprobadores`` -- sin jefe ni admin: no se puede solicitar.
    * ``pendiente`` -- ya hay una solicitud en trámite del mismo usuario.
    * ``ya_puede`` / ``ya_autorizada`` -- ya tiene permiso vigente.
    * ``ya_resuelta`` -- la solicitud ya fue resuelta (idempotencia).
    * ``sin_pendiente`` -- el usuario no es aprobador asignado o ya decidió.
    * ``no_existe`` -- la solicitud ya no existe.
    """

    codigo: str
    solicitud: SolicitudDescarga
    mensaje: str


def aprobadores_descarga(documento):
    """Aprobadores ``[(usuario, rol)]`` de una descarga según clasificación.

    Normal: jefe del departamento (o admin activo si no hay jefe).
    ``restringido``: jefe + autorizador (admin activo); ISO 9001:7.5.3
    (doble control). Solo administradores activos, con orden estable.
    """
    from autenticacion.models import PerfilUsuario

    depto = getattr(documento, 'departamento', None)
    jefe = depto.jefe if depto and depto.jefe else None
    admin_perfil = (
        PerfilUsuario.objects
        .filter(rol='administrador', usuario__is_active=True)
        .select_related('usuario')
        .order_by('usuario__date_joined', 'usuario_id')
        .first()
    )
    admin = admin_perfil.usuario if admin_perfil else None

    aprobadores = []
    if documento.clasificacion == 'restringido':
        if jefe:
            aprobadores.append((jefe, 'jefe'))
        if admin and (not jefe or admin.id != jefe.id):
            aprobadores.append((admin, 'autorizador'))
    else:
        if jefe:
            aprobadores.append((jefe, 'jefe'))
        elif admin:
            aprobadores.append((admin, 'autorizador'))
    return aprobadores


def _notificar_solicitante(solicitud, aprobada):
    """Avisa al solicitante el resultado final de su solicitud."""
    from autenticacion.models import Notificacion

    documento = solicitud.documento
    if aprobada:
        tipo, titulo = 'aprobacion_descarga', 'Descarga aprobada'
        mensaje = (
            f'La solicitud de descarga de "{documento.nombre}" '
            f'({documento.codigo}) ha sido aprobada.'
        )
    else:
        tipo, titulo = 'rechazo_descarga', 'Descarga rechazada'
        motivo = f' {solicitud.observaciones}' if solicitud.observaciones else ''
        mensaje = (
            f'La solicitud de descarga de "{documento.nombre}" '
            f'({documento.codigo}) ha sido rechazada.{motivo}'
        )
    Notificacion.objects.create(
        usuario=solicitud.solicitado_por,
        titulo=titulo,
        mensaje=mensaje,
        tipo=tipo,
        url_accion=f'/documentos/{documento.tipo_id}/{documento.uuid}/',
        departamento=documento.departamento,
        documento=documento,
    )


def _notificar_aprobadores(solicitud, solicitante, aprobadores):
    """Avisa a cada aprobador de una solicitud recién creada."""
    from autenticacion.models import Notificacion

    documento = solicitud.documento
    for aprobador, _rol in aprobadores:
        Notificacion.objects.create(
            usuario=aprobador,
            titulo='Nueva solicitud de descarga',
            mensaje=(
                f'{solicitante.get_full_name() or solicitante.username} '
                f'solicita descargar "{documento.nombre}" ({documento.codigo})'
            ),
            tipo='solicitud_descarga',
            url_accion=f'/documentos/solicitud/{solicitud.id}/aprobar/',
            departamento=documento.departamento,
            documento=documento,
        )


def bloqueo_solicitud_descarga(documento, usuario):
    """Motivo por el que no procede solicitar la descarga, o ``None``.

    Devuelve un :class:`ResultadoDescarga` cuyo ``codigo`` explica el
    bloqueo (``sin_aprobadores``, ``ya_puede``, ``pendiente``,
    ``ya_autorizada``) o ``None`` si procede solicitar. Es la misma
    comprobación que aplica :func:`crear_solicitud_descarga`, expuesta
    para que la vista pueda redirigir en GET sin crear nada.
    """
    from ..permissions import descarga_autorizada, puede_descargar

    if not aprobadores_descarga(documento):
        return ResultadoDescarga(
            'sin_aprobadores', None,
            'No se puede realizar la solicitud en este momento. '
            'Comuníquese con el administrador.',
        )
    if puede_descargar(usuario, documento):
        return ResultadoDescarga(
            'ya_puede', None,
            'Ya tienes permiso para descargar este documento.',
        )
    existe_pendiente = SolicitudDescarga.objects.filter(
        documento=documento, solicitado_por=usuario, estado='pendiente',
    ).exists()
    if existe_pendiente:
        return ResultadoDescarga(
            'pendiente', None,
            'Ya tienes una solicitud pendiente para este documento.',
        )
    if descarga_autorizada(documento, usuario):
        return ResultadoDescarga(
            'ya_autorizada', None,
            'Ya tienes una descarga aprobada. Usa el botón "Descargar".',
        )
    return None


def crear_solicitud_descarga(documento, solicitado_por, motivo, *, ip_address=None):
    """Crea la solicitud y sus aprobaciones. Devuelve :class:`ResultadoDescarga`."""
    bloqueo = bloqueo_solicitud_descarga(documento, solicitado_por)
    if bloqueo:
        return bloqueo

    aprobadores = aprobadores_descarga(documento)
    solicitud = SolicitudDescarga.objects.create(
        documento=documento, solicitado_por=solicitado_por, motivo=motivo,
    )
    for aprobador, rol in aprobadores:
        AprobacionDescarga.objects.create(
            solicitud=solicitud, aprobador=aprobador, rol_aprobador=rol,
        )
    _notificar_aprobadores(solicitud, solicitante=solicitado_por, aprobadores=aprobadores)
    return ResultadoDescarga('ok', solicitud, 'Solicitud de descarga enviada correctamente.')


def finalizar_descarga(solicitud, aprobador, *, ip_address=None):
    """Fija vigencia/versión, crea la distribución y audita la autorización.

    Llamar **solo** cuando todas las aprobaciones están resueltas, dentro
    de la transacción de :func:`resolver_aprobacion`.
    """
    from auditoria.middleware import get_client_ip
    from auditoria.services import registrar_distribucion

    documento = solicitud.documento
    dias = getattr(settings, 'DESCARGA_VIGENCIA_DIAS', 30)
    solicitud.estado = 'aprobada'
    solicitud.version_aprobada = documento.version_actual
    solicitud.fecha_expiracion = timezone.now() + timedelta(days=dias)
    solicitud.fecha_resolucion = timezone.now()
    solicitud.save(update_fields=[
        'estado', 'version_aprobada', 'fecha_expiracion', 'fecha_resolucion',
    ])
    try:
        dist = DistribucionDocumento.objects.create(
            documento=documento,
            usuario=solicitud.solicitado_por,
            metodo='sistema',
            distribuido_por=aprobador,
            observaciones=f'Autorizado por solicitud #{solicitud.id}',
        )
        registrar_distribucion(
            usuario=aprobador,
            recurso_tipo=documento.tipo_id or '',
            recurso_id=str(documento.uuid),
            destinatario=solicitud.solicitado_por.username,
            metodo='sistema',
            codigo=documento.codigo,
            distribucion_id=dist.id,
            correlacion=f'descarga-{solicitud.id}',
            correlation_id=f'descarga-{solicitud.id}',
            ip_address=ip_address or get_client_ip(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "No se pudo registrar la distribución de la solicitud #%s: %s",
            solicitud.id, exc,
        )


def resolver_aprobacion(solicitud, aprobador, aprobar, observaciones='', *, ip_address=None):
    """Registra la decisión de un aprobador. Devuelve :class:`ResultadoDescarga`.

    Bloquea la solicitud con ``select_for_update`` para que el doble
    control concurrente no pueda resolverla dos veces ni dejar la
    vigencia sin fijar. El rechazo cancela las aprobaciones hermanas
    (actualización masiva: no auditan, porque nunca hubo decisión).
    """
    with transaction.atomic():
        bloqueada = (
            SolicitudDescarga.objects
            .select_for_update()
            .select_related('documento', 'solicitado_por')
            .filter(pk=solicitud.pk)
            .first()
        )
        if bloqueada is None:
            return ResultadoDescarga(
                'no_existe', None, 'La solicitud ya no existe.',
            )
        if bloqueada.estado != 'pendiente':
            return ResultadoDescarga(
                'ya_resuelta', bloqueada,
                f'La solicitud ya fue {bloqueada.get_estado_display().lower()}.',
            )
        aprobacion = (
            AprobacionDescarga.objects
            .select_for_update()
            .filter(solicitud=bloqueada, aprobador=aprobador)
            .first()
        )
        if aprobacion is None:
            return ResultadoDescarga(
                'sin_pendiente', bloqueada,
                'No tienes una aprobación asignada para esta solicitud.',
            )
        if aprobacion.estado not in APROBACIONES_PENDIENTES:
            return ResultadoDescarga(
                'sin_pendiente', bloqueada, 'Tu evaluación ya fue registrada.',
            )
        aprobacion.estado = 'aprobada' if aprobar else 'rechazada'
        aprobacion.observaciones = observaciones
        aprobacion.fecha_aprobacion = timezone.now()
        aprobacion.save(update_fields=['estado', 'observaciones', 'fecha_aprobacion'])

        if not aprobar:
            # ISO: el rechazo de cualquier control cierra la solicitud y
            # libera a los demás aprobadores (quedan como 'cancelada').
            solicitud.aprobaciones.filter(estado='pendiente').update(estado='cancelada')
            bloqueada.estado = 'rechazada'
            bloqueada.observaciones = observaciones
            bloqueada.fecha_resolucion = timezone.now()
            bloqueada.save(update_fields=['estado', 'observaciones', 'fecha_resolucion'])
            _notificar_solicitante(bloqueada, aprobada=False)
            return ResultadoDescarga('rechazada', bloqueada, 'Descarga rechazada.')

        pendientes = bloqueada.aprobaciones.filter(estado__in=APROBACIONES_PENDIENTES).exists()
        if pendientes:
            # Doble control (restringido): falta la autorización final.
            return ResultadoDescarga(
                'parcial', bloqueada,
                'Evaluación registrada. Pendiente de autorización final.',
            )
        finalizar_descarga(bloqueada, aprobador, ip_address=ip_address)
        _notificar_solicitante(bloqueada, aprobada=True)
        return ResultadoDescarga('ok', bloqueada, 'Descarga aprobada.')
