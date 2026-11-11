"""Canales de mensajería institucionales (interfaz extensible).

Hoy el canal operativo es el **correo** (backend de Django), que es lo que
POLIDOC usa para invitaciones, distribuciones y avisos.

**WhatsApp queda opcional y desactivado por defecto**: requiere un
proveedor oficial (Meta Cloud API o compatible) configurado por entorno
(``WHATSAPP_API_URL`` + ``WHATSAPP_TOKEN``). No se usa ningún bot no
oficial conectado a la sesión de WhatsApp de una PC: no es institucional,
no es auditable y depende de un dispositivo particular.

Activarlo en el futuro = configurar las variables de entorno; el flujo de
distribución no cambia.
"""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

#: Tiempo máximo de la llamada al proveedor de mensajería.
_TIMEOUT_SEGUNDOS = 10


class CanalMensajeria:
    """Contrato mínimo de un canal de mensajería institucional."""

    #: Identificador del canal (``'email'``, ``'whatsapp'``…).
    nombre = ''

    def disponible(self):
        """True si el canal está configurado y puede enviar."""
        raise NotImplementedError

    def enviar(self, *, destinatario, asunto, cuerpo, html=None):
        """Envía un mensaje.

        Returns:
            tuple: ``(ok, error)`` — ``(True, '')`` si se envió.

        Raises:
            NotImplementedError: si el canal no implementa el envío.
        """
        raise NotImplementedError


class CanalCorreo(CanalMensajeria):
    """Correo electrónico vía el backend configurado de Django."""

    nombre = 'email'

    def disponible(self):
        return True

    def enviar(self, *, destinatario, asunto, cuerpo, html=None):
        from django.core import mail

        message = mail.EmailMultiAlternatives(
            asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, [destinatario],
        )
        if html:
            message.attach_alternative(html, 'text/html')
        try:
            mail.mailers.default.send_messages([message])
            return True, ''
        except Exception as exc:  # noqa: BLE001
            logger.error('Correo no enviado a %s: %s', destinatario, exc)
            return False, str(exc)


class CanalWhatsApp(CanalMensajeria):
    """WhatsApp vía proveedor oficial (Meta Cloud API o compatible).

    Deshabilitado hasta que el entorno defina ``WHATSAPP_API_URL`` y
    ``WHATSAPP_TOKEN``; mientras tanto :meth:`disponible` es False y
    :meth:`enviar` no hace ninguna llamada.
    """

    nombre = 'whatsapp'

    def disponible(self):
        return bool(
            getattr(settings, 'WHATSAPP_API_URL', '')
            and getattr(settings, 'WHATSAPP_TOKEN', '')
        )

    def enviar(self, *, destinatario, asunto, cuerpo, html=None):
        if not self.disponible():
            return False, 'Canal WhatsApp deshabilitado (sin proveedor configurado)'

        url = settings.WHATSAPP_API_URL
        payload = {
            'messaging_product': 'whatsapp',
            'to': destinatario,
            'type': 'text',
            'text': {'body': f'{asunto}\n\n{cuerpo}'},
        }
        peticion = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Authorization': f'Bearer {settings.WHATSAPP_TOKEN}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(
                peticion, timeout=_TIMEOUT_SEGUNDOS,
            ) as respuesta:
                if 200 <= respuesta.status < 300:
                    return True, ''
                return False, f'Proveedor respondió HTTP {respuesta.status}'
        except urllib.error.HTTPError as exc:
            return False, f'Proveedor respondió HTTP {exc.code}'
        except (urllib.error.URLError, OSError) as exc:
            logger.error('WhatsApp no enviado a %s: %s', destinatario, exc)
            return False, str(exc)


#: Registro de canales disponibles por nombre.
CANALES = {
    CanalCorreo.nombre: CanalCorreo,
    CanalWhatsApp.nombre: CanalWhatsApp,
}


def obtener_canal(nombre):
    """Instancia del canal ``nombre``.

    Raises:
        KeyError: si el canal no está registrado.
    """
    try:
        return CANALES[nombre]()
    except KeyError:
        raise KeyError(f'Canal de mensajería desconocido: {nombre}') from None
