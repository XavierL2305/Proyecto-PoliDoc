"""Backends de correo de POLIDOC.

* ``SmtpWithConsoleFallback``: intenta enviar por SMTP (Mailpit en desarrollo
  o servidor autenticado) y, si el servidor no está disponible, imprime el correo
  en consola para no romper los flujos en local.
* ``GmailApiBackend``: envía correos vía Gmail API (HTTPS puerto 443) usando
  OAuth2 (``client_id``, ``client_secret``, ``refresh_token``) sin necesitar
  puertos SMTP salientes.
"""

import base64
import json
import logging
import smtplib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
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


class GmailApiBackend(BaseEmailBackend):
    """Backend Django para enviar correos reales vía Gmail API (OAuth2 HTTPS).

    Usa únicamente la librería estándar de Python: intercambia ``refresh_token``
    por un ``access_token`` (cacheado en memoria hasta 60s antes de expirar) y
    publica el mensaje MIME armado por Django en ``users/me/messages/send``.
    """

    TOKEN_URL = 'https://oauth2.googleapis.com/token'
    SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'

    def __init__(
        self,
        client_id='',
        client_secret='',
        refresh_token='',
        timeout=15,
        fail_silently=False,
        **kwargs,
    ):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.client_id = (
            client_id or getattr(settings, 'GMAIL_CLIENT_ID', '') or ''
        ).strip()
        self.client_secret = (
            client_secret or getattr(settings, 'GMAIL_CLIENT_SECRET', '') or ''
        ).strip()
        self.refresh_token = (
            refresh_token or getattr(settings, 'GMAIL_REFRESH_TOKEN', '') or ''
        ).strip()
        self.timeout = int(timeout or getattr(settings, 'GMAIL_API_TIMEOUT', 15))
        self._lock = threading.Lock()
        self._access_token = ''
        self._token_expira_en = 0.0

    def _obtener_access_token(self, *, forzar=False):
        """Obtiene o renueva el ``access_token`` OAuth2 usando ``refresh_token``."""
        with self._lock:
            ahora = time.monotonic()
            if not forzar and self._access_token and ahora < self._token_expira_en:
                return self._access_token

            if not (self.client_id and self.client_secret and self.refresh_token):
                raise ValueError(
                    'Faltan credenciales de Gmail API '
                    '(GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN).'
                )

            payload = urllib.parse.urlencode({
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token',
            }).encode('utf-8')
            req = urllib.request.Request(
                self.TOKEN_URL,
                data=payload,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            token = data.get('access_token') or ''
            if not token:
                raise RuntimeError('Google OAuth2 no devolvió access_token.')

            expires_in = max(int(data.get('expires_in', 3600)), 60)
            self._access_token = token
            # 60s de margen para evitar usar un token justo en el borde.
            self._token_expira_en = time.monotonic() + max(expires_in - 60, 1)
            return token

    def _enviar_uno(self, email_message, token):
        """Codifica un EmailMessage como MIME base64url y lo envía a Gmail API."""
        raw_mime = email_message.message().as_bytes()
        raw_b64 = base64.urlsafe_b64encode(raw_mime).decode('ascii')
        body = json.dumps({'raw': raw_b64}).encode('utf-8')
        req = urllib.request.Request(
            self.SEND_URL,
            data=body,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            resp.read()

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        try:
            token = self._obtener_access_token()
        except Exception as exc:  # noqa: BLE001
            logger.error('No se pudo autenticar contra Gmail API: %s', exc)
            if not self.fail_silently:
                raise
            return 0

        enviados = 0
        for msg in email_messages:
            if not msg.recipients():
                continue
            try:
                try:
                    self._enviar_uno(msg, token)
                except urllib.error.HTTPError as exc:
                    if exc.code != 401:
                        raise
                    token = self._obtener_access_token(forzar=True)
                    self._enviar_uno(msg, token)
                enviados += 1
            except Exception as exc:  # noqa: BLE001
                logger.error('Error enviando correo vía Gmail API: %s', exc)
                if not self.fail_silently:
                    raise
        return enviados
