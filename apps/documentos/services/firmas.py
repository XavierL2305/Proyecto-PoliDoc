"""Firma electrónica de distribuciones (SES — firma de control, FOR-002).

* Interna: OTP enviado al correo + PIN local (``FirmaCredencial``). Nunca se
  re-autentica contra AD: el PIN vive hasheado en POLIDOC, de modo que los
  intentos fallidos no cuentan para la política de bloqueo de Active Directory.
* Externa: invitación tokenizada (SHA-256 en BD), de un solo uso y con
  vigencia de :data:`VIGENCIA_INVITACION_DIAS`.

Al firmar se calcula el SHA-256 de la planilla del lote, se registra la
auditoría y se regenera la planilla para reflejar la conformidad.
"""
import hashlib
import logging
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core import mail
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

PIN_MIN_LEN = 4
PIN_MAX_LEN = 12
PIN_MAX_INTENTOS = 5
PIN_BLOQUEO_MINUTOS = 15

OTP_TTL_MINUTOS = 10
OTP_MAX_INTENTOS = 5

VIGENCIA_INVITACION_DIAS = 7


class ErrorFirma(Exception):
    """Error de negocio del flujo de firma (mensaje apto para el usuario)."""


# ---------------------------------------------------------------------------
# Credencial (PIN local)
# ---------------------------------------------------------------------------

def obtener_credencial(usuario):
    """``FirmaCredencial`` del usuario o ``None`` si aún no tiene PIN."""
    from ..models import FirmaCredencial
    return FirmaCredencial.objects.filter(usuario=usuario).first()


def _validar_pin(pin):
    if not pin or not pin.isdigit():
        raise ErrorFirma('El PIN debe contener solo dígitos.')
    if not (PIN_MIN_LEN <= len(pin) <= PIN_MAX_LEN):
        raise ErrorFirma(
            f'El PIN debe tener entre {PIN_MIN_LEN} y {PIN_MAX_LEN} dígitos.'
        )


def establecer_pin(usuario, pin, pin2=None):
    """Crea o reemplaza el PIN local del usuario.

    Raises:
        ErrorFirma: PIN inválido o confirmación distinta.
    """
    from ..models import FirmaCredencial

    _validar_pin(pin)
    if pin2 is not None and pin != pin2:
        raise ErrorFirma('La confirmación del PIN no coincide.')

    credencial, _ = FirmaCredencial.objects.get_or_create(
        usuario=usuario,
        defaults={'pin_hash': make_password(pin)},
    )
    credencial.pin_hash = make_password(pin)
    credencial.intentos_fallidos = 0
    credencial.bloqueado_hasta = None
    credencial.save(update_fields=[
        'pin_hash', 'intentos_fallidos', 'bloqueado_hasta', 'actualizado',
    ])
    return credencial


def verificar_pin(usuario, pin):
    """Valida el PIN aplicando bloqueo temporal tras intentos fallidos.

    Raises:
        ErrorFirma: sin PIN, bloqueado, PIN incorrecto o error de formato.
    """
    credencial = obtener_credencial(usuario)
    if credencial is None:
        raise ErrorFirma('Todavía no has creado tu PIN de firma.')

    ahora = timezone.now()
    if credencial.bloqueado_hasta and credencial.bloqueado_hasta > ahora:
        restante = int((credencial.bloqueado_hasta - ahora).total_seconds() // 60) + 1
        raise ErrorFirma(
            f'PIN bloqueado por intentos fallidos. Reintenta en {restante} minuto(s).'
        )

    if not pin:
        raise ErrorFirma('Debes ingresar tu PIN.')

    if check_password(pin, credencial.pin_hash):
        if credencial.intentos_fallidos or credencial.bloqueado_hasta:
            credencial.intentos_fallidos = 0
            credencial.bloqueado_hasta = None
            credencial.save(update_fields=[
                'intentos_fallidos', 'bloqueado_hasta', 'actualizado',
            ])
        return True

    credencial.intentos_fallidos += 1
    if credencial.intentos_fallidos >= PIN_MAX_INTENTOS:
        credencial.bloqueado_hasta = ahora + timezone.timedelta(
            minutes=PIN_BLOQUEO_MINUTOS
        )
        credencial.intentos_fallidos = 0
        credencial.save(update_fields=[
            'intentos_fallidos', 'bloqueado_hasta', 'actualizado',
        ])
        raise ErrorFirma(
            f'PIN incorrecto. Se bloqueó por {PIN_BLOQUEO_MINUTOS} minutos.'
        )
    restantes = PIN_MAX_INTENTOS - credencial.intentos_fallidos
    credencial.save(update_fields=['intentos_fallidos', 'actualizado'])
    raise ErrorFirma(f'PIN incorrecto. Te quedan {restantes} intento(s).')


# ---------------------------------------------------------------------------
# OTP (firma interna)
# ---------------------------------------------------------------------------

def generar_otp(firma, request=None):
    """Genera un OTP de 6 dígitos, lo guarda hasheado y lo envía por correo.

    Returns:
        str: el código en claro (nunca se persiste), para pruebas/log.
    """
    if firma.estado != 'pendiente':
        raise ErrorFirma('Esta firma ya no está pendiente.')

    email = firma.firmante_email or (firma.firmante.email if firma.firmante else '')
    if not email:
        raise ErrorFirma(
            'No tienes un correo configurado para recibir el código de firma.'
        )

    codigo = f'{secrets.randbelow(1_000_000):06d}'
    firma.otp_hash = make_password(codigo)
    firma.otp_expira = timezone.now() + timedelta(minutes=OTP_TTL_MINUTOS)
    firma.otp_intentos = 0
    firma.save(update_fields=['otp_hash', 'otp_expira', 'otp_intentos', 'actualizado'])

    asunto = '[POLIDOC] Código de firma electrónica'
    lineas = [
        f'Hola {firma.firmante_nombre},',
        '',
        'Tu código de verificación para firmar la distribución es:',
        '',
        f'    {codigo}',
        '',
        f'Vence en {OTP_TTL_MINUTOS} minutos. Si no solicitaste este código, '
        'ignora este mensaje.',
        '',
        '— POLIDOC',
    ]
    message = mail.EmailMessage(
        asunto, '\n'.join(lineas),
        settings.DEFAULT_FROM_EMAIL, [email],
    )
    try:
        mail.mailers.default.send_messages([message])
    except Exception as exc:  # noqa: BLE001
        logger.error('No se pudo enviar el OTP de firma: %s', exc)
        raise ErrorFirma(
            'No se pudo enviar el código por correo. Intenta nuevamente.'
        ) from exc
    return codigo


def verificar_otp(firma, codigo):
    """Valida el OTP vigente con límite de intentos.

    Raises:
        ErrorFirma: sin OTP, expirado, demasiados intentos o código incorrecto.
    """
    if not firma.otp_hash or not firma.otp_expira:
        raise ErrorFirma('Solicita primero un código de verificación.')
    if firma.otp_expira <= timezone.now():
        raise ErrorFirma('El código de verificación expiró. Solicita uno nuevo.')
    if firma.otp_intentos >= OTP_MAX_INTENTOS:
        raise ErrorFirma('Demasiados intentos fallidos de código. Solicita uno nuevo.')
    if not codigo:
        raise ErrorFirma('Debes ingresar el código de verificación.')

    if not check_password(codigo, firma.otp_hash):
        firma.otp_intentos += 1
        firma.save(update_fields=['otp_intentos', 'actualizado'])
        restantes = OTP_MAX_INTENTOS - firma.otp_intentos
        raise ErrorFirma(f'Código incorrecto. Te quedan {restantes} intento(s).')
    return True


# ---------------------------------------------------------------------------
# Firma efectiva
# ---------------------------------------------------------------------------

def _hash_planilla(distribucion):
    """SHA-256 del XLSX de la planilla del lote (o '' si no se puede generar)."""
    if not distribucion.lote:
        return ''
    try:
        from .distribucion import exportar_planilla_lote
        contenido = exportar_planilla_lote(distribucion.lote)
    except Exception as exc:  # noqa: BLE001
        logger.warning('No se pudo calcular el hash de la planilla: %s', exc)
        return ''
    return hashlib.sha256(contenido).hexdigest()


def _datos_cliente(request):
    from auditoria.middleware import get_client_ip
    if request is None:
        return None, ''
    return get_client_ip(), request.META.get('HTTP_USER_AGENT', '')


def _registrar_auditoria_firma(firma, request, tipo_firma):
    """Auditoría ISO de la firma electrónica."""
    dist = firma.distribucion
    documento = dist.documento
    ip, user_agent = _datos_cliente(request)
    try:
        from auditoria.services import registrar_log_iso
        registrar_log_iso(
            usuario=firma.firmante,
            accion_tipo='firma_distribucion',
            accion_categoria='distribucion',
            recurso_tipo=(documento.tipo_id if documento else ''),
            recurso_id=str(documento.uuid) if documento else '',
            recurso_codigo=documento.codigo if documento else '',
            recurso_nombre=documento.nombre if documento else '',
            detalles={
                'distribucion_id': dist.id,
                'firma_id': firma.id,
                'tipo_firma': tipo_firma,
                'firmante': firma.firmante_nombre,
                'hash_contenido': firma.hash_contenido,
                'algoritmo': firma.algoritmo,
            },
            resultado_exitoso=True,
            resultado_mensaje=f'Firma {tipo_firma} registrada para {firma.firmante_nombre}',
            ip_address=ip,
            user_agent=user_agent,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Auditoría de firma falló: %s', exc)


def _marcar_firmada(firma, request, tipo_firma, datos_extra=None):
    """Cierra la firma: estado, hash, auditoría y regeneración de planilla."""
    firma.estado = 'firmada'
    firma.fecha_firma = timezone.now()
    firma.hash_contenido = _hash_planilla(firma.distribucion)
    firma.algoritmo = 'sha256'
    ip, user_agent = _datos_cliente(request)
    firma.ip_address = ip
    firma.user_agent = user_agent or ''
    datos = dict(firma.datos or {})
    datos.update(datos_extra or {})
    firma.datos = datos
    firma.otp_hash = ''
    firma.otp_expira = None
    firma.save(update_fields=[
        'estado', 'fecha_firma', 'hash_contenido', 'algoritmo',
        'ip_address', 'user_agent', 'datos', 'otp_hash', 'otp_expira',
        'actualizado',
    ])

    dist = firma.distribucion
    dist.estado_firma = 'firmada'
    dist.save(update_fields=['estado_firma'])

    _registrar_auditoria_firma(firma, request, tipo_firma)

    from .distribucion import regenerar_planilla_de_firma
    regenerar_planilla_de_firma(firma)


def firmar_interna(firma, usuario, codigo, pin, request=None):
    """Firma interna: OTP + PIN; todo o nada.

    Raises:
        ErrorFirma: cualquier validación falla.
    """
    if firma.tipo != 'interna':
        raise ErrorFirma('Esta firma no es interna.')
    if firma.firmante_id != usuario.id:
        raise ErrorFirma('Esta firma no te pertenece.')
    if firma.estado != 'pendiente':
        raise ErrorFirma('Esta firma ya no está pendiente.')

    # OTP y PIN se validan FUERA de la transacción: sus contadores de intentos
    # se guardan al fallar y un rollback no debe borrarlos (si no, el bloqueo
    # por fuerza bruta nunca se activaría).
    verificar_otp(firma, codigo)
    verificar_pin(usuario, pin)

    with transaction.atomic():
        # Re-lectura para cerrar la ventana de dos firmas concurrentes.
        firma.refresh_from_db(fields=['estado'])
        if firma.estado != 'pendiente':
            raise ErrorFirma('Esta firma ya fue registrada.')
        _marcar_firmada(
            firma, request, 'interna',
            datos_extra={'metodo': 'OTP+PIN'},
        )
    return firma


def firmar_externa(invitacion, request=None, consentimiento=False):
    """Firma externa: valida la invitación (token ya buscado) y firma.

    Raises:
        ErrorFirma: invitación usada, vencida o sin consentimiento.
    """
    if not consentimiento:
        raise ErrorFirma('Debes aceptar la firma electrónica para continuar.')
    if invitacion.usado:
        raise ErrorFirma('Esta invitación ya fue utilizada.')
    if invitacion.expira <= timezone.now():
        marcar_expirada(invitacion)
        raise ErrorFirma('Esta invitación expiró. Solicita una nueva al emisor.')

    firma = invitacion.firma
    if firma.estado != 'pendiente':
        raise ErrorFirma('Esta firma ya fue registrada.')

    with transaction.atomic():
        invitacion.usado = True
        invitacion.fecha_uso = timezone.now()
        invitacion.save(update_fields=['usado', 'fecha_uso'])
        _marcar_firmada(
            firma, request, 'externa',
            datos_extra={'metodo': 'invitacion_token', 'consentimiento': True},
        )
    return firma


def marcar_expirada(invitacion):
    """Marca la firma y la distribución como expiradas (sin lanzar)."""
    firma = invitacion.firma
    if firma.estado == 'pendiente':
        firma.estado = 'expirada'
        firma.save(update_fields=['estado', 'actualizado'])
        dist = firma.distribucion
        if dist.estado_firma == 'pendiente':
            dist.estado_firma = 'expirada'
            dist.save(update_fields=['estado_firma'])


def buscar_invitacion(token):
    """``InvitacionFirma`` a partir del token en claro (o ``None``)."""
    from ..models import InvitacionFirma
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return InvitacionFirma.objects.filter(token_hash=token_hash).select_related(
        'firma', 'firma__distribucion', 'firma__distribucion__documento',
        'firma__distribucion__distribuido_por',
    ).first()
