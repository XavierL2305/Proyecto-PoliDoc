"""Solicitud y aprobación de descargas de documentos.

ISO 9001:2015 7.5.3: la copia de un documento solo sale del control del
organismo con una **autorización temporal, trazable y revocable**. Este
módulo es la fuente única de verdad del flujo:

* **Alta** -- :func:`crear_solicitud_descarga` crea la solicitud y un paso
  de aprobación por cada aprobador requerido, con notificación a los pasos
  accionables. El **jefe del departamento dueño del documento** es el primer
  aprobador; el documento ``restringido`` exige **doble control secuencial**
  (jefe → jefe de Gerencia General). Un paso sin jefe asignado nace en
  ``esperando`` (``aprobador=None``), sin fallback a administrador.
* **Resolución** -- :func:`resolver_aprobacion` registra la decisión de un
  aprobador (o de su delegado vigente) dentro de una transacción bloqueada.
  Una solicitud ya resuelta **no puede procesarse dos veces**: no se
  re-finaliza, no se duplica la distribución y no se reinicia la vigencia.
  Un rechazo cancela los pasos hermanos; la aprobación final fija vigencia y
  crea la distribución **una sola vez**.

El flujo espeja el de aprobación de documentos (§7.3/§20): cadena de uno o
dos pasos, estado ``esperando`` cuando falta el jefe, delegación de jefatura
y escalado por antigüedad (``tasks.escalar_aprobaciones_vencidas``).
"""
import logging
from datetime import timedelta
from typing import NamedTuple, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import AprobacionDescarga, DistribucionDocumento, SolicitudDescarga

logger = logging.getLogger(__name__)

#: estados de aprobación que siguen requiriendo decisión
APROBACIONES_PENDIENTES = ('pendiente',)


class PasoDescarga(NamedTuple):
    """Un paso de la cadena de aprobación de una descarga.

    ``usuario`` es ``None`` cuando el paso nace en espera (no hay jefe
    asignado todavía). ``es_final`` marca el paso que, al aprobarse, fija la
    vigencia y crea la distribución.
    """

    usuario: Optional[object]
    rol: str
    es_final: bool
    estado_inicial: str


class ResultadoDescarga(NamedTuple):
    """Resultado de alta/resolución de una descarga.

    ``codigo`` permite mapear la respuesta HTTP:

    * ``ok`` -- aprobación completa (vigencia + distribución).
    * ``parcial`` -- aprobación registrada, falta el control final.
    * ``rechazada`` -- rechazo con cancelación de las aprobaciones hermanas.
    * ``sin_departamento`` -- el documento no tiene departamento: no se puede
      determinar al jefe aprobador.
    * ``pendiente`` -- ya hay una solicitud en trámite del mismo usuario.
    * ``ya_puede`` / ``ya_autorizada`` -- ya tiene permiso vigente.
    * ``ya_resuelta`` -- la solicitud ya fue resuelta (idempotencia).
    * ``en_espera`` -- el paso del usuario aún no está activo (falta el jefe
      del paso anterior o de su propio departamento).
    * ``sin_pendiente`` -- el usuario no es aprobador asignado o ya decidió.
    * ``no_existe`` -- la solicitud ya no existe.
    """

    codigo: str
    solicitud: Optional[SolicitudDescarga]
    mensaje: str


def _jefe_departamento(documento):
    """Jefe NATURAL del departamento del documento (o ``None``).

    La delegación vigente no cambia quién queda asignado en el paso, pero sí
    permite resolverlo (``resolver_aprobacion`` usa ``aprobadores_que_repr``).
    """
    depto = getattr(documento, 'departamento', None)
    return depto.jefe if depto and depto.jefe_id else None


def aprobadores_descarga(documento):
    """Pasos de aprobación ``[PasoDescarga, ...]`` de una descarga.

    * ``restringido``: doble control **secuencial** -- jefe del departamento
      del documento (paso 1, no final) → jefe de Gerencia General (paso 2,
      final).
    * resto: un solo paso -- jefe del departamento del documento (final).

    Un paso sin jefe asignado nace en ``esperando`` con ``usuario=None`` (no
    hay fallback a administrador): al asignarse el jefe, el servicio de
    reasignación lo activa.
    """
    from .cambios_estado import jefe_gerencia_general

    jefe = _jefe_departamento(documento)

    if documento.clasificacion == 'restringido':
        autorizador = jefe_gerencia_general()
        return [
            PasoDescarga(
                usuario=jefe, rol='jefe', es_final=False,
                estado_inicial='pendiente' if jefe else 'esperando',
            ),
            PasoDescarga(
                usuario=autorizador, rol='autorizador', es_final=True,
                estado_inicial='esperando',
            ),
        ]

    return [
        PasoDescarga(
            usuario=jefe, rol='jefe', es_final=True,
            estado_inicial='pendiente' if jefe else 'esperando',
        ),
    ]


def _avisar_admins(titulo, mensaje, solicitud, tipo='solicitud_descarga'):
    """Avisa a los administradores activos de una situación sin aprobador."""
    from autenticacion.models import PerfilUsuario, Notificacion

    documento = solicitud.documento if solicitud else None
    admins = [
        perfil.usuario for perfil in (
            PerfilUsuario.objects
            .filter(rol='administrador', usuario__is_active=True)
            .select_related('usuario')
        )
    ]
    for admin in admins:
        Notificacion.objects.create(
            usuario=admin, titulo=titulo, mensaje=mensaje, tipo=tipo,
            url_accion=(
                f'/documentos/{documento.tipo_id}/{documento.uuid}/'
                if documento else '/documentos/bandeja-aprobaciones/'
            ),
            departamento=getattr(documento, 'departamento', None),
            documento=documento,
        )
    return len(admins)


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


def _notificar_aprobador(solicitud, aprobacion, *, es_autorizacion=False):
    """Avisa a un aprobador accionable (paso pendiente) de la solicitud."""
    from autenticacion.models import Notificacion

    documento = solicitud.documento
    solicitante = solicitud.solicitado_por
    if es_autorizacion:
        titulo = 'Descarga pendiente de autorización'
        verbo = 'autorización'
    else:
        titulo = 'Nueva solicitud de descarga'
        verbo = 'aprobación'
    Notificacion.objects.create(
        usuario=aprobacion.aprobador,
        titulo=titulo,
        mensaje=(
            f'{solicitante.get_full_name() or solicitante.username} '
            f'solicita descargar "{documento.nombre}" ({documento.codigo}). '
            f'Requiere tu {verbo}.'
        ),
        tipo='solicitud_descarga',
        url_accion=f'/documentos/solicitud/{solicitud.id}/aprobar/',
        departamento=documento.departamento,
        documento=documento,
    )


def bloqueo_solicitud_descarga(documento, usuario):
    """Motivo por el que no procede solicitar la descarga, o ``None``.

    Devuelve un :class:`ResultadoDescarga` cuyo ``codigo`` explica el
    bloqueo (``sin_departamento``, ``ya_puede``, ``pendiente``,
    ``ya_autorizada``) o ``None`` si procede solicitar. Es la misma
    comprobación que aplica :func:`crear_solicitud_descarga`, expuesta
    para que la vista pueda redirigir en GET sin crear nada.

    Nota: la ausencia de jefe de departamento **no** bloquea -- la solicitud
    queda ``esperando`` (ver :func:`aprobadores_descarga`).
    """
    from ..permissions import descarga_autorizada, puede_descargar

    if getattr(documento, 'departamento', None) is None:
        return ResultadoDescarga(
            'sin_departamento', None,
            'El documento no tiene departamento asignado; no se puede '
            'determinar al jefe que debe aprobar la descarga.',
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
    """Crea la solicitud y sus pasos. Devuelve :class:`ResultadoDescarga`."""
    bloqueo = bloqueo_solicitud_descarga(documento, solicitado_por)
    if bloqueo:
        return bloqueo

    pasos = aprobadores_descarga(documento)
    solicitud = SolicitudDescarga.objects.create(
        documento=documento, solicitado_por=solicitado_por, motivo=motivo,
    )
    creadas = []
    for paso in pasos:
        creadas.append(AprobacionDescarga.objects.create(
            solicitud=solicitud,
            aprobador=paso.usuario,
            rol_aprobador=paso.rol,
            es_final=paso.es_final,
            estado=paso.estado_inicial,
            fecha_activacion=(
                timezone.now() if paso.estado_inicial == 'pendiente' else None
            ),
        ))

    for aprobacion in creadas:
        if aprobacion.estado == 'pendiente' and aprobacion.aprobador_id:
            _notificar_aprobador(solicitud, aprobacion)

    # Si el primer paso no tiene aprobador, la solicitud queda muda: se avisa
    # a los administradores para que asignen un jefe de departamento.
    primer_paso = creadas[0] if creadas else None
    if primer_paso is not None and primer_paso.estado == 'esperando':
        _avisar_admins(
            'Solicitud de descarga en espera',
            f'La solicitud de descarga de "{documento.nombre}" ({documento.codigo}) '
            'quedó en espera porque el departamento no tiene jefe asignado. '
            'Asigna un jefe para poder tramitarla.',
            solicitud,
            tipo='solicitud_descarga',
        )

    return ResultadoDescarga('ok', solicitud, 'Solicitud de descarga enviada correctamente.')


def finalizar_descarga(solicitud, aprobador, *, ip_address=None):
    """Fija vigencia/versión, crea la distribución y audita la autorización.

    Llamar **solo** cuando el paso final se aprobó, dentro de la transacción
    de :func:`resolver_aprobacion`.
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


def _promover_autorizador(solicitud):
    """Activa el paso final (autorizador/GG) tras aprobar el jefe.

    Si el paso final ya tenía aprobador, lo notifica; si no, avisa a los
    administradores para que se asigne un jefe de Gerencia General.
    """
    siguiente = (
        AprobacionDescarga.objects
        .filter(solicitud=solicitud, es_final=True, estado='esperando')
        .order_by('id').first()
    )
    if siguiente is None:
        return None
    siguiente.estado = 'pendiente'
    siguiente.fecha_activacion = timezone.now()
    siguiente.save(update_fields=['estado', 'fecha_activacion'])
    if siguiente.aprobador_id:
        _notificar_aprobador(solicitud, siguiente, es_autorizacion=True)
    else:
        documento = solicitud.documento
        _avisar_admins(
            'Solicitud de descarga sin autorizador',
            f'La solicitud de descarga de "{documento.nombre}" ({documento.codigo}) '
            'fue evaluada por el jefe de departamento y quedó pendiente de '
            'autorización porque no hay jefe de Gerencia General asignado.',
            solicitud,
            tipo='solicitud_descarga',
        )
    return siguiente


def resolver_aprobacion(solicitud, aprobador, aprobar, observaciones='', *, ip_address=None):
    """Registra la decisión de un aprobador. Devuelve :class:`ResultadoDescarga`.

    Bloquea la solicitud con ``select_for_update`` para que el doble control
    secuencial no pueda resolverse dos veces ni dejar la vigencia sin fijar.
    El aprobador puede ser el jefe natural **o su delegado vigente**
    (``aprobadores_que_repr``). El rechazo cancela los pasos hermanos
    (actualización masiva: no auditan, porque nunca hubo decisión).
    """
    from ..jefatura import aprobadores_que_repr

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
            .filter(solicitud=bloqueada, aprobador_id__in=aprobadores_que_repr(aprobador))
            .order_by('id')
            .first()
        )
        if aprobacion is None:
            return ResultadoDescarga(
                'sin_pendiente', bloqueada,
                'No tienes una aprobación asignada para esta solicitud.',
            )
        if aprobacion.estado == 'esperando':
            return ResultadoDescarga(
                'en_espera', bloqueada,
                'Este paso aún no está activo: falta asignar al jefe que debe '
                'resolverlo.',
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
            bloqueada.aprobaciones.filter(
                estado__in=('pendiente', 'esperando'),
            ).update(estado='cancelada')
            bloqueada.estado = 'rechazada'
            bloqueada.observaciones = observaciones
            bloqueada.fecha_resolucion = timezone.now()
            bloqueada.save(update_fields=['estado', 'observaciones', 'fecha_resolucion'])
            _notificar_solicitante(bloqueada, aprobada=False)
            return ResultadoDescarga('rechazada', bloqueada, 'Descarga rechazada.')

        if not aprobacion.es_final:
            # Doble control secuencial: activa el paso del jefe de GG.
            _promover_autorizador(bloqueada)
            return ResultadoDescarga(
                'parcial', bloqueada,
                'Evaluación registrada. Pendiente de autorización final.',
            )

        finalizar_descarga(bloqueada, aprobador, ip_address=ip_address)
        _notificar_solicitante(bloqueada, aprobada=True)
        return ResultadoDescarga('ok', bloqueada, 'Descarga aprobada.')
