"""Backends de correo de POLIDOC.

`SmtpWithConsoleFallback` intenta enviar por SMTP (Mailpit en desarrollo) y,
si el servidor no está disponible, imprime el correo en la terminal en vez
de fallar. Así el flujo de distribución funciona con o sin Mailpit levantado.
"""

import logging
import smtplib

from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend

logger = logging.getLogger(__name__)


class SmtpWithConsoleFallback(SMTPBackend):
    """SMTP con respaldo a consola si la conexión falla."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        try:
            return super().send_messages(email_messages)
        except (smtplib.SMTPException, OSError) as exc:
            # En desarrollo cae a consola; se registra para que la caída del
            # servidor SMTP no pase desapercibida (los correos no salen realmente).
            logger.warning(
                "SMTP no disponible (%s); %d correo(s) reenviado(s) a la consola.",
                exc, len(email_messages),
            )
            return ConsoleBackend().send_messages(email_messages)
