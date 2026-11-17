"""Tests de endurecimiento: IP de auditoría y rutas de PDF."""
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, override_settings

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos import views
from documentos.services import xlsx_comun

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_hardening_')


class AuditoriaIPTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def test_ip_prioriza_get_client_ip_del_middleware(self):
        request = self.factory.get('/', REMOTE_ADDR='172.18.0.1')
        with patch('auditoria.middleware.get_client_ip',
                   return_value='203.0.113.7'):
            self.assertEqual(views._ip_auditoria(request), '203.0.113.7')

    def test_ip_cae_a_remote_addr_sin_middleware(self):
        request = self.factory.get('/', REMOTE_ADDR='10.0.0.5')
        with patch('auditoria.middleware.get_client_ip', return_value=None):
            self.assertEqual(views._ip_auditoria(request), '10.0.0.5')

    def test_auditar_lista_usa_la_ip_resuelta(self):
        request = self.factory.get('/')
        request.user = self.user_admin
        registro = MagicMock(pk=42)
        with patch('auditoria.middleware.get_client_ip',
                   return_value='203.0.113.9'), \
                patch('auditoria.services.registrar_log_iso') as registrar:
            views._auditar_lista(request, registro, 'lista_maestra_prueba')
        self.assertTrue(registrar.called)
        _, kwargs = registrar.call_args
        self.assertEqual(kwargs['ip_address'], '203.0.113.9')


class PdfRutaRelativaTest(BaseDocumentosTest):
    @override_settings(MEDIA_ROOT=TEST_MEDIA)
    def test_xlsx_a_pdf_resuelve_ruta_relativa_a_media(self):
        relativa = os.path.join('sub', 'salida.pdf')
        absoluta = os.path.join(TEST_MEDIA, relativa)
        os.makedirs(os.path.dirname(absoluta), exist_ok=True)
        with open(absoluta, 'wb') as fh:
            fh.write(b'%PDF-1.4\n%prueba\n')

        with patch('documentos.tasks.convertir_a_pdf_sync',
                   return_value=relativa):
            datos = xlsx_comun.xlsx_bytes_a_pdf(b'xlsx')

        self.assertEqual(datos, b'%PDF-1.4\n%prueba\n')
        # El PDF temporal se consume y elimina.
        self.assertFalse(os.path.exists(absoluta))
