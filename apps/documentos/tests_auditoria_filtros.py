"""Tests del filtro de auditoría por checkboxes (multi-selección)."""
from unittest.mock import patch

from django.test import Client, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest

from auditoria.services import obtener_logs


class _Cursor:
    def __init__(self):
        pass

    def sort(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def __iter__(self):
        return iter([])


class _Coleccion:
    def __init__(self):
        self.ultima_query = None

    def find(self, query):
        self.ultima_query = query
        return _Cursor()


class _FakeDB:
    def __init__(self):
        self.audit_logs = _Coleccion()

    def command(self, *args, **kwargs):
        return {'ok': 1}


class ObtenerLogsMultiTest(BaseDocumentosTest):
    def test_lista_usa_in(self):
        db = _FakeDB()
        with patch('auditoria.services.get_mongo_client', return_value=db):
            obtener_logs(
                accion_tipo=['creacion', 'edicion'],
                accion_categoria=['documento', 'seguridad'],
            )
        self.assertEqual(
            db.audit_logs.ultima_query['accion.tipo'],
            {'$in': ['creacion', 'edicion']},
        )
        self.assertEqual(
            db.audit_logs.ultima_query['accion.categoria'],
            {'$in': ['documento', 'seguridad']},
        )

    def test_un_valor_usa_igualdad(self):
        db = _FakeDB()
        with patch('auditoria.services.get_mongo_client', return_value=db):
            obtener_logs(accion_tipo='creacion', accion_categoria='documento')
        self.assertEqual(db.audit_logs.ultima_query['accion.tipo'], 'creacion')
        self.assertEqual(db.audit_logs.ultima_query['accion.categoria'], 'documento')

    def test_sin_filtros_no_agrega_condiciones(self):
        db = _FakeDB()
        with patch('auditoria.services.get_mongo_client', return_value=db):
            obtener_logs(usuario='admin01')
        self.assertNotIn('accion.tipo', db.audit_logs.ultima_query)
        self.assertNotIn('accion.categoria', db.audit_logs.ultima_query)


@override_settings(ALLOWED_HOSTS=['testserver'])
class VistaAuditoriaMultiTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.user_admin)
        self._mongo = patch(
            'auditoria.services.get_mongo_client', return_value=_FakeDB(),
        )
        self._mongo.start()
        self.addCleanup(self._mongo.stop)

    def test_getlist_accion_y_categoria(self):
        resp = self.client.get(reverse('documentos:auditoria_lista'), {
            'accion_tipo': ['creacion', 'edicion'],
            'accion_categoria': ['documento'],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['accion_tipo'], ['creacion', 'edicion'])
        self.assertEqual(resp.context['accion_categoria'], ['documento'])

    def test_render_checkboxes_marcados(self):
        resp = self.client.get(reverse('documentos:auditoria_lista'), {
            'accion_tipo': ['creacion', 'edicion'],
        })
        html = resp.content.decode()
        self.assertIn('type="checkbox"', html)
        self.assertIn('name="accion_tipo"', html)
        self.assertIn('name="accion_categoria"', html)
        # Ya no hay un <select> de tipo de acción.
        self.assertNotIn('name="accion_tipo" class="form-input"', html)

    def test_pdf_recibe_multiseleccion(self):
        with patch('auditoria.services.obtener_logs', return_value=[]) as mock:
            resp = self.client.get(
                reverse('documentos:auditoria_reporte_pdf'),
                {'tipo': 'registro', 'accion_tipo': ['creacion', 'edicion']},
            )
        # Puede responder PDF (200), o redirigir si falta reportlab.
        self.assertIn(resp.status_code, (200, 302))
        if mock.called:
            _, kwargs = mock.call_args
            self.assertEqual(kwargs.get('accion_tipo'), ['creacion', 'edicion'])

    def test_pdf_footer_incluye_huella_y_total_paginas(self):
        from datetime import datetime
        from io import BytesIO
        from pypdf import PdfReader

        logs_fake = [
            {
                'log_id': f'LOG-2026-000{i}',
                'timestamp': datetime(2026, 11, 23, 12, 0),
                'usuario': {'username': 'admin01', 'nombre_completo': 'Admin'},
                'accion': {'tipo': 'login', 'categoria': 'autenticacion'},
                'recurso': {'nombre': 'POLIDOC'},
                'contexto': {'ip_address': '127.0.0.1'},
                'resultado': {'exitoso': True},
            }
            for i in range(1, 35)
        ]
        with patch('auditoria.services.obtener_logs', return_value=logs_fake):
            resp = self.client.get(
                reverse('documentos:auditoria_reporte_pdf'),
                {'tipo': 'registro'},
                REMOTE_ADDR='15.15.1.88',
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')

        pdf_bytes = b''.join(resp.streaming_content)
        reader = PdfReader(BytesIO(pdf_bytes))
        total_paginas = len(reader.pages)
        self.assertGreaterEqual(total_paginas, 2)
        texto_p1 = reader.pages[0].extract_text() or ''
        self.assertIn('IP de origen:', texto_p1)
        self.assertIn('15.15.1.88', texto_p1)
        for idx, page in enumerate(reader.pages, start=1):
            texto = page.extract_text() or ''
            self.assertIn(f'Página {idx} de {total_paginas}', texto)
            self.assertIn('SGC-RPT-AUD-', texto)
            self.assertIn('Huella: ', texto)
            self.assertNotIn('de ?', texto)
