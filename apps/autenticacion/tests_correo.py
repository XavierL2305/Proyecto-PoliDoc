"""Tests del backend GmailApiBackend y del comando probar_correo."""

import base64
import json
import urllib.error
from io import BytesIO, StringIO
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.test import SimpleTestCase

from polidoc.mail_backends import GmailApiBackend


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode('utf-8')

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class GmailApiBackendTest(SimpleTestCase):
    def _backend(self, **kwargs):
        defaults = {
            'client_id': 'cid.apps.googleusercontent.com',
            'client_secret': 'csecret',
            'refresh_token': 'rtoken',
            'timeout': 5,
        }
        defaults.update(kwargs)
        return GmailApiBackend(**defaults)

    def test_envia_correo_con_adjunto_por_https(self):
        backend = self._backend()
        llamadas = []

        def fake_urlopen(req, timeout=15):
            llamadas.append({
                'url': req.full_url,
                'headers': dict(req.header_items()),
                'body': req.data.decode('utf-8'),
                'timeout': timeout,
            })
            if req.full_url == backend.TOKEN_URL:
                return _FakeHTTPResponse({'access_token': 'tok-1', 'expires_in': 3600})
            return _FakeHTTPResponse({'id': 'gmail-msg-1'})

        msg = mail.EmailMessage(
            subject='Prueba POLIDOC',
            body='Contenido de prueba.',
            from_email='polidoc@example.com',
            to=['destino@example.com'],
        )
        msg.attach('prueba.pdf', b'%PDF-1.4\n%ok\n', 'application/pdf')

        with patch('polidoc.mail_backends.urllib.request.urlopen', side_effect=fake_urlopen):
            enviados = backend.send_messages([msg])

        self.assertEqual(enviados, 1)
        self.assertEqual(len(llamadas), 2)
        self.assertEqual(llamadas[0]['url'], backend.TOKEN_URL)
        self.assertIn('refresh_token=rtoken', llamadas[0]['body'])

        send_call = llamadas[1]
        self.assertEqual(send_call['url'], backend.SEND_URL)
        self.assertEqual(send_call['headers'].get('Authorization'), 'Bearer tok-1')
        raw_b64 = json.loads(send_call['body'])['raw']
        mime_bytes = base64.urlsafe_b64decode(raw_b64.encode('ascii'))
        self.assertIn(b'Subject: Prueba POLIDOC', mime_bytes)
        self.assertIn(b'To: destino@example.com', mime_bytes)
        self.assertIn(b'filename="prueba.pdf"', mime_bytes)

    def test_reutiliza_access_token_cacheado(self):
        backend = self._backend()
        token_calls = 0

        def fake_urlopen(req, timeout=15):
            nonlocal token_calls
            if req.full_url == backend.TOKEN_URL:
                token_calls += 1
                return _FakeHTTPResponse({'access_token': 'tok-cache', 'expires_in': 3600})
            return _FakeHTTPResponse({'id': 'msg'})

        m1 = mail.EmailMessage('A1', 'C1', 'a@test.local', ['b@test.local'])
        m2 = mail.EmailMessage('A2', 'C2', 'a@test.local', ['b@test.local'])

        with patch('polidoc.mail_backends.urllib.request.urlopen', side_effect=fake_urlopen):
            self.assertEqual(backend.send_messages([m1]), 1)
            self.assertEqual(backend.send_messages([m2]), 1)

        self.assertEqual(token_calls, 1)

    def test_renueva_token_tras_401(self):
        backend = self._backend()
        tokens = ['tok-expirado', 'tok-nuevo']
        send_auths = []

        def fake_urlopen(req, timeout=15):
            if req.full_url == backend.TOKEN_URL:
                return _FakeHTTPResponse({'access_token': tokens.pop(0), 'expires_in': 3600})
            auth = dict(req.header_items()).get('Authorization')
            send_auths.append(auth)
            if auth == 'Bearer tok-expirado':
                raise urllib.error.HTTPError(
                    req.full_url, 401, 'Unauthorized', {}, BytesIO(b'expired'),
                )
            return _FakeHTTPResponse({'id': 'ok'})

        m = mail.EmailMessage('Asunto', 'Cuerpo', 'a@test.local', ['b@test.local'])
        with patch('polidoc.mail_backends.urllib.request.urlopen', side_effect=fake_urlopen):
            enviados = backend.send_messages([m])

        self.assertEqual(enviados, 1)
        self.assertEqual(send_auths, ['Bearer tok-expirado', 'Bearer tok-nuevo'])

    def test_sin_credenciales_respeta_fail_silently(self):
        msg = mail.EmailMessage('Asunto', 'Cuerpo', 'a@test.local', ['b@test.local'])
        backend_strict = GmailApiBackend(
            client_id='', client_secret='', refresh_token='', fail_silently=False,
        )
        with self.assertRaises(ValueError):
            backend_strict.send_messages([msg])

        backend_silent = GmailApiBackend(
            client_id='', client_secret='', refresh_token='', fail_silently=True,
        )
        self.assertEqual(backend_silent.send_messages([msg]), 0)


class ProbarCorreoCommandTest(SimpleTestCase):
    def test_comando_envia_por_mailer_activo_y_adjunta(self):
        mail.outbox.clear()
        salida = StringIO()
        call_command(
            'probar_correo',
            'xavilahur@example.com',
            '--con-adjunto',
            stdout=salida,
        )
        self.assertIn('Correo enviado correctamente', salida.getvalue())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['xavilahur@example.com'])
        self.assertEqual(len(mail.outbox[0].attachments), 1)
