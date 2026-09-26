"""Backends de correo de POLIDOC.

`SmtpWithConsoleFallback` intenta enviar por SMTP (Mailpit en desarrollo) y,
si el servidor no está disponible, imprime el correo en la terminal en vez
de fallar. Así el flujo de distribución funciona con o sin Mailpit levantado.
"""

import smtplib

from django.core.mail.backends.console import EmailBackend as ConsoleBackend
from django.core.mail.backends.smtp import EmailBackend as SMTPBackend


class SmtpWithConsoleFallback(SMTPBackend):
    """SMTP con respaldo a consola si la conexión falla."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        try:
            return super().send_messages(email_messages)
        except (smtplib.SMTPException, OSError):
            return ConsoleBackend().send_messages(email_messages)
