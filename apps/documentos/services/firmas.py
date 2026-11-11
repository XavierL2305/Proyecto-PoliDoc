"""Motor ÚNICO de firma electrónica (SES).

Un solo modelo (``FirmaElectronica``) y una sola puerta de entrada
(:func:`firmar`). El objeto firmado se identifica con ``origen_tipo`` +
``origen_id``; los documentos firmables se declaran en
``services/firmables.py`` (registro), de modo que agregar uno nuevo no
requiere otra tabla ni otro flujo.

Métodos de verificación (parámetro, no otro mecanismo):

* ``pin``/``interna``: PIN local (``FirmaCredencial``) y, si el spec lo pide,
  OTP por correo. Nunca se re-autentica contra AD: el PIN vive hasheado en
  POLIDOC y los fallos no cuentan para la política de bloqueo de AD.
* ``token``/``externa``: invitación tokenizada (SHA-256 en BD), un solo uso y
  vigencia de :data:`VIGENCIA_INVITACION_DIAS`.
* ``auto``/``sistema``: auto-aprobaciones; no hay acto humano que verificar,
  pero la firma queda registrada igual (tipo ``sistema``).

Al firmar se calcula el SHA-256 del contenido del objeto, se registra la
auditoría ISO y se ejecuta el gancho ``despues`` del spec (regenerar la
planilla, resolver la aprobación, etc.).
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
# Registro de objetos firmables
# ---------------------------------------------------------------------------

#: ``origen_tipo`` → instancia de :class:`Firmable`.
REGISTRO_FIRMABLES = {}


class Firmable:
    """Contrato de un objeto que puede firmarse electrónicamente.

    Para agregar un documento nuevo basta con subclasificar, declarar
    ``tipo`` (el ``origen_tipo``) e implementar ``contenido``; ``despues``
    permite enganchar el efecto de negocio (regenerar planilla, resolver la
    aprobación…).
    """

    #: ``origen_tipo`` con el que se guarda la firma.
    tipo = ''
    #: ¿Exige OTP además del PIN (firma interna)?
    requiere_otp = False
    #: Metadatos de auditoría ISO.
    audit_accion = 'firma_electronica'
    audit_categoria = 'documento'

    # -- ganchos ---------------------------------------------------------
    def documento(self, obj):
        """Documento asociado (para la auditoría); por defecto ``obj.documento``."""
        return getattr(obj, 'documento', None)

    def puede_firmar(self, obj, usuario):
        """Validación de negocio. Debe lanzar ``ErrorFirma`` si no procede."""
        return None

    def contenido(self, obj):
        """Bytes del contenido firmado (su SHA-256 queda en ``hash_contenido``)."""
        raise NotImplementedError

    def detalles(self, obj):
        """Detalles extra para la auditoría."""
        return {}

    def despues(self, obj, firma, contexto=None):
        """Efecto posterior a la firma, dentro de la misma transacción."""
        return None

    def firmante(self, obj, usuario):
        """Datos del firmante para firmas creadas al vuelo."""
        from ..models import FirmaElectronica

        return {
            'firmante': usuario,
            'firmante_clave': FirmaElectronica.clave_interna(usuario),
            'firmante_nombre': usuario.get_full_name() or usuario.username,
            'firmante_email': getattr(usuario, 'email', '') or '',
            'firmante_cargo': '',
        }


def registrar_firmable(spec_cls):
    """Decorador: registra un :class:`Firmable` por su ``tipo``."""
    REGISTRO_FIRMABLES[spec_cls.tipo] = spec_cls()
    return spec_cls


def _cargar_firmables():
    """Importa ``firmables`` una vez (registra los specs sin ciclos)."""
    if REGISTRO_FIRMABLES:
        return
    from . import firmables  # noqa: F401


def spec_para(obj):
    """Instancia de :class:`Firmable` para ``obj`` (o ``None``)."""
    from ..models import FIRMA_ORIGEN_POR_MODELO

    _cargar_firmables()
    modelo = getattr(getattr(obj, '_meta', None), 'model_name', None)
    tipo = FIRMA_ORIGEN_POR_MODELO.get(modelo)
    return REGISTRO_FIRMABLES.get(tipo)


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
        'Tu código de verificación para firmar es:',
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
# Creación y firma
# ---------------------------------------------------------------------------

def crear_firma(objeto, *, tipo, usuario=None, spec=None, datos=None, **extra):
    """Crea una ``FirmaElectronica`` pendiente para ``objeto``.

    Centraliza ``origen_tipo``/``origen_id`` y los datos del firmante, para
    que ningún flujo escriba el origen a mano. ``extra`` permite sobrescribir
    campos del firmante (p. ej. ``firmante_cargo`` o el correo externo).
    """
    from ..models import FIRMA_ORIGEN_POR_MODELO, FirmaElectronica

    spec = spec or spec_para(objeto)
    origen_tipo = (spec.tipo if spec else
                   FIRMA_ORIGEN_POR_MODELO.get(objeto._meta.model_name))
    if not origen_tipo:
        raise ErrorFirma('Este documento no admite firma electrónica.')
    info = spec.firmante(objeto, usuario) if spec and usuario else {}
    if usuario is None and not extra.get('firmante_clave'):
        raise ErrorFirma('Debes indicar quién firma.')
    campos = {**info, **extra}
    return FirmaElectronica.objects.create(
        origen_tipo=origen_tipo,
        origen_id=str(objeto.pk),
        tipo=tipo,
        datos=datos or {},
        **campos,
    )


def datos_cliente(request):
    """``(ip, user_agent)`` del request (o ``(None, '')``)."""
    if request is None:
        return None, ''
    from auditoria.middleware import get_client_ip
    return get_client_ip(), request.META.get('HTTP_USER_AGENT', '')


def _hash_contenido(spec, objeto):
    """SHA-256 del contenido firmado (``''`` si no se puede calcular)."""
    if spec is None:
        return ''
    try:
        datos = spec.contenido(objeto)
    except Exception as exc:  # noqa: BLE001
        logger.warning('No se pudo calcular el hash del contenido: %s', exc)
        return ''
    if not datos:
        return ''
    return hashlib.sha256(datos).hexdigest()


def _registrar_auditoria_firma(firma, spec, objeto, request, tipo_firma):
    """Auditoría ISO de la firma electrónica (cualquier origen)."""
    documento = spec.documento(objeto) if spec else None
    ip, user_agent = datos_cliente(request)
    detalles = {
        'origen_tipo': firma.origen_tipo,
        'origen_id': firma.origen_id,
        'firma_id': firma.id,
        'tipo_firma': tipo_firma,
        'firmante': firma.firmante_nombre,
        'hash_contenido': firma.hash_contenido,
        'algoritmo': firma.algoritmo,
    }
    if spec is not None:
        detalles.update(spec.detalles(objeto) or {})
    try:
        from auditoria.services import registrar_log_iso
        registrar_log_iso(
            usuario=firma.firmante,
            accion_tipo=spec.audit_accion if spec else 'firma_electronica',
            accion_categoria=spec.audit_categoria if spec else 'documento',
            recurso_tipo=(documento.tipo_id if documento else ''),
            recurso_id=(
                str(getattr(documento, 'uuid', documento.pk))
                if documento else ''
            ),
            recurso_codigo=documento.codigo if documento else '',
            recurso_nombre=documento.nombre if documento else '',
            detalles=detalles,
            resultado_exitoso=True,
            resultado_mensaje=(
                f'Firma {tipo_firma} registrada para {firma.firmante_nombre}'
            ),
            ip_address=ip,
            user_agent=user_agent,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Auditoría de firma falló: %s', exc)


def _marcar_firmada(firma, request, tipo_firma, datos_extra=None,
                    spec=None, objeto=None, contexto=None):
    """Cierra la firma: estado, hash, auditoría y gancho ``despues``."""
    objeto = objeto if objeto is not None else firma.origen
    firma.estado = 'firmada'
    firma.fecha_firma = timezone.now()
    firma.hash_contenido = _hash_contenido(spec, objeto)
    firma.algoritmo = 'sha256'
    ip, user_agent = datos_cliente(request)
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

    _registrar_auditoria_firma(firma, spec, objeto, request, tipo_firma)

    if spec is not None:
        spec.despues(objeto, firma, contexto)


def _datos_metodo(metodo, extra=None):
    """Etiqueta de ``datos['metodo']`` (compatible con firmas históricas)."""
    etiqueta = {
        'pin': 'PIN', 'interna': 'OTP+PIN',
        'token': 'invitacion_token', 'externa': 'invitacion_token',
        'auto': 'auto', 'sistema': 'auto',
    }.get(metodo, metodo)
    datos = {'metodo': etiqueta}
    if extra:
        datos.update(extra)
    return datos


def _firma_pendiente(objeto, usuario, metodo):
    """Firma pendiente que corresponde a ``objeto``/``usuario``/``metodo``."""
    from ..models import FirmaElectronica

    qs = FirmaElectronica.para(objeto).filter(estado='pendiente')
    if metodo in ('token', 'externa'):
        return qs.filter(tipo='externa').first()
    if usuario is None:
        return None
    return qs.filter(
        firmante_clave=FirmaElectronica.clave_interna(usuario),
    ).first()


def firmar(objeto, *, usuario=None, pin=None, codigo=None, request=None,
           metodo='pin', consentimiento=False, crear=False, firma=None,
           datos=None, contexto=None):
    """Firma ``objeto`` con el motor único.

    Args:
        objeto: instancia firmable (distribución, aprobación…).
        usuario: firmante interno (``None`` para token externo).
        pin: PIN local (métodos ``pin``/``interna``).
        codigo: OTP (solo si el spec declara ``requiere_otp``).
        request: para IP/User-Agent.
        metodo: ``pin``|``interna``, ``token``|``externa`` o ``auto``.
        consentimiento: obligatorio en el método ``token``.
        crear: crea la firma si no existe pendiente (aprobaciones).
        firma: ``FirmaElectronica`` ya localizada (invitaciones/OTP).

    Returns:
        FirmaElectronica: la firma registrada.

    Raises:
        ErrorFirma: cualquier validación falla.
    """
    _cargar_firmables()
    if firma is not None and objeto is None:
        objeto = firma.origen
    spec = spec_para(objeto)
    if spec is None:
        raise ErrorFirma('Este documento no admite firma electrónica.')

    if firma is None:
        firma = _firma_pendiente(objeto, usuario, metodo)
    if firma is None and not crear:
        raise ErrorFirma('No hay una firma pendiente para este documento.')
    if firma is not None and firma.estado != 'pendiente':
        raise ErrorFirma('Esta firma ya fue registrada.')

    if usuario is not None:
        spec.puede_firmar(objeto, usuario)
        if (firma is not None and firma.tipo == 'interna'
                and firma.firmante_id and firma.firmante_id != usuario.id):
            raise ErrorFirma('Esta firma no te pertenece.')

    # Credenciales FUERA de la transacción: los contadores de intentos deben
    # persistir aunque la firma falle (si no, el bloqueo por fuerza bruta no
    # se activaría).
    if metodo in ('pin', 'interna'):
        if usuario is None:
            raise ErrorFirma('Debes iniciar sesión para firmar.')
        verificar_pin(usuario, pin)
        if spec.requiere_otp:
            if firma is None:
                raise ErrorFirma('Solicita primero un código de verificación.')
            verificar_otp(firma, codigo)
    elif metodo in ('token', 'externa'):
        if not consentimiento:
            raise ErrorFirma('Debes aceptar la firma electrónica para continuar.')
        if firma is None or firma.tipo != 'externa':
            raise ErrorFirma('Esta firma no es externa.')
    elif metodo not in ('auto', 'sistema'):
        raise ErrorFirma('Método de firma no reconocido.')

    tipo_firma = (
        'sistema' if metodo in ('auto', 'sistema')
        else ('externa' if metodo in ('token', 'externa') else 'interna')
    )
    with transaction.atomic():
        if firma is None:
            firma = crear_firma(
                objeto, tipo=tipo_firma, usuario=usuario,
                spec=spec, datos=datos,
            )
        else:
            firma.refresh_from_db(fields=['estado'])
            if firma.estado != 'pendiente':
                raise ErrorFirma('Esta firma ya fue registrada.')
        _marcar_firmada(
            firma, request, tipo_firma,
            datos_extra=_datos_metodo(metodo, datos), spec=spec,
            objeto=objeto, contexto=contexto,
        )
    return firma


# ---------------------------------------------------------------------------
# Envoltorios de compatibilidad (FOR-002)
# ---------------------------------------------------------------------------

def firmar_interna(firma, usuario, codigo, pin, request=None):
    """Firma interna: OTP + PIN; todo o nada.

    Raises:
        ErrorFirma: cualquier validación falla.
    """
    if firma.tipo != 'interna':
        raise ErrorFirma('Esta firma no es interna.')
    if firma.firmante_id != usuario.id:
        raise ErrorFirma('Esta firma no te pertenece.')
    return firmar(
        firma.origen, usuario=usuario, pin=pin, codigo=codigo,
        request=request, metodo='interna', firma=firma,
    )


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
        firmar(
            firma.origen, request=request, metodo='token',
            consentimiento=True, firma=firma,
            datos={'consentimiento': True},
        )
    return firma


def marcar_expirada(invitacion):
    """Marca la firma y el objeto como expirados (sin lanzar)."""
    firma = invitacion.firma
    if firma.estado == 'pendiente':
        firma.estado = 'expirada'
        firma.save(update_fields=['estado', 'actualizado'])
        dist = firma.origen
        if dist is not None and getattr(dist, 'estado_firma', '') == 'pendiente':
            dist.estado_firma = 'expirada'
            dist.save(update_fields=['estado_firma'])


def buscar_invitacion(token):
    """``InvitacionFirma`` a partir del token en claro (o ``None``)."""
    from ..models import InvitacionFirma
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return InvitacionFirma.objects.filter(
        token_hash=token_hash,
    ).select_related('firma').first()


# ---------------------------------------------------------------------------
# Sello de evidencia (XLSX/PDF)
# ---------------------------------------------------------------------------

def texto_sello(firma):
    """Línea legible de una firma para estampar en la planilla."""
    origen = firma.origen
    rol = str(getattr(origen, 'rol_aprobador', '') or '').capitalize()
    nombre = firma.firmante_nombre or firma.firmante_clave
    fecha = ''
    if firma.fecha_firma:
        fecha = timezone.localtime(firma.fecha_firma).strftime('%d/%m/%Y %H:%M')
    hash_corto = (firma.hash_contenido or '')[:16]
    partes = [f'Firma electrónica: {nombre}']
    if rol:
        partes.append(rol)
    partes.append(fecha)
    if hash_corto:
        partes.append(f'SHA-256 {hash_corto}…')
    partes.append(f'ID {firma.id}')
    return ' · '.join(p for p in partes if p)


def firmas_firmadas(firmas):
    """Filtra/ordena firmas ya completadas (cronológico, sin duplicados)."""
    unicas = {}
    for firma in firmas or []:
        if firma.estado == 'firmada':
            unicas[firma.id] = firma
    return sorted(
        unicas.values(),
        key=lambda f: (f.fecha_firma or f.creado, f.id),
    )


def firmas_de_solicitud(solicitud):
    """Firmas SES de las aprobaciones de una ``SolicitudCambio`` (001/005).

    - FOR-005: pasos ``AprobacionPlanilla`` de las planillas enlazadas.
    - FOR-001: pasos ``SolicitudAprobacionDocumento`` del documento para la
      operación equivalente, posteriores a la creación de la solicitud.
    """
    from ..models import (
        OPERACION_POR_ACCION, AprobacionPlanilla, FirmaElectronica,
        SolicitudAprobacionDocumento,
    )

    if solicitud is None:
        return []
    desde = getattr(solicitud, 'fecha_creacion', None)
    firmas = []

    for planilla in solicitud.planillas_cambios.all():
        pasos = AprobacionPlanilla.objects.filter(planilla=planilla)
        if desde:
            pasos = pasos.filter(fecha_aprobacion__gte=desde)
        for paso in pasos:
            firmas.extend(FirmaElectronica.para(paso).all())

    documento_id = getattr(solicitud, 'documento_id', None)
    if documento_id:
        acciones = [
            accion for accion, operacion in OPERACION_POR_ACCION.items()
            if operacion == solicitud.operacion
        ]
        pasos = SolicitudAprobacionDocumento.objects.filter(
            documento_id=documento_id, accion_solicitud__in=acciones,
        )
        if desde:
            pasos = pasos.filter(fecha_respuesta__gte=desde)
        for paso in pasos:
            firmas.extend(FirmaElectronica.para(paso).all())

    return firmas_firmadas(firmas)


def respuestas_con_sello(formato, firmas, respuestas):
    """Inyecta las líneas del sello en el campo ``tipo='sello'`` del formato.

    Solo incluye firmas ya completadas (``estado='firmada'``): un sello no
    puede anunciar una firma que aún no existe.
    """
    if formato is None:
        return respuestas
    firmas = firmas_firmadas(firmas)
    if not firmas:
        return respuestas
    campo = next(
        (c for c in formato.get_todos_los_campos() if c.tipo == 'sello'),
        None,
    )
    if campo is None:
        return respuestas
    respuestas = dict(respuestas)
    respuestas[campo.clave] = [texto_sello(f) for f in firmas]
    return respuestas
