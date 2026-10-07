"""Tests de rendimiento (forma de las consultas).

No miden milisegundos — frágiles en CI — sino el NÚMERO de consultas, que es
lo que controlamos con estas optimizaciones:

* `contadores_documentos` debe hacer un número FIJO de consultas aunque haya
  más tipos activos (antes: 1 consulta por tipo).
* `_conteos_vigencia` debe resolverse con un único aggregate (antes: 4).
* Guardar un `Documento` debe hacer UN solo SELECT de estado/clasificación
  previos (antes: dos, uno en pre_save y otro en post_save).
* Guardar un documento que YA estaba activo no debe consultar las referencias
  pendientes.
"""

from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests_base import BaseDocumentosTest
from documentos.models import Documento, TipoDocumento
from documentos.views import _conteos_vigencia


def _queries(fn):
    with CaptureQueriesContext(connection) as ctx:
        fn()
    return ctx.captured_queries


def _cuenta_sql(queries, fragmento):
    return sum(1 for q in queries if fragmento in q['sql'])


class ContadoresDocumentosPerformanceTest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.user_trabajador)

    def _get_contadores(self):
        return self.client.get(reverse('documentos:api_contadores_documentos'))

    def test_consultas_no_escalan_con_los_tipos(self):
        # Con los tipos de los fixtures.
        base = len(_queries(self._get_contadores))

        # Añadimos varios tipos activos más: el número de consultas no debe
        # crecer (antes crecía 1 por tipo).
        for i in range(6):
            TipoDocumento.objects.create(
                key=f'tipo_perf_{i}', nombre=f'Tipo {i}', codigo=f'TP{i}', activo=True,
            )
        con_mas_tipos = len(_queries(self._get_contadores))

        self.assertEqual(base, con_mas_tipos)

    def test_respuesta_correcta(self):
        self.client.force_login(self.user_trabajador)
        resp = self._get_contadores()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # El trabajador es propietario de doc_borrador y doc_en_revision.
        self.assertEqual(data['por_estado'].get('borrador'), 1)
        self.assertEqual(data['por_estado'].get('en_revision'), 1)
        self.assertIn('procedimientos_generales', data['por_tipo'])
        self.assertIn('formulario', data['por_tipo'])


class ConteosVigenciaPerformanceTest(BaseDocumentosTest):

    def test_un_solo_aggregate(self):
        # En las vistas `request.user.perfil` ya está resuelto; aquí lo
        # calentamos para medir SOLO el aggregate.
        self.user_jefe_ti.perfil
        queries = _queries(
            lambda: _conteos_vigencia([self.depto_ti], '', self.user_jefe_ti)
        )
        self.assertEqual(len(queries), 1)

    def test_conteos_correctos(self):
        conteos = _conteos_vigencia([self.depto_ti], '', self.user_jefe_ti)
        self.assertGreaterEqual(conteos['vencidos'], 1)      # doc_vencido
        self.assertGreaterEqual(conteos['vence_pronto'], 1)  # doc_por_vencer
        self.assertIn('vigentes', conteos)
        self.assertIn('sin_vigencia', conteos)


class SenalesDocumentoPerformanceTest(BaseDocumentosTest):

    def test_save_hace_un_solo_select_previo(self):
        """El pre_save consolidado lee estado y clasificación en 1 SELECT."""
        def _guardar():
            self.doc_borrador.estado = 'en_revision'
            self.doc_borrador.save(update_fields=['estado'])

        queries = _queries(_guardar)
        selects = [
            q for q in queries
            if q['sql'].lstrip().upper().startswith('SELECT')
            and 'documentos_documento' in q['sql']
        ]
        self.assertEqual(len(selects), 1)

    def test_guardar_activo_no_consulta_referencias(self):
        """Si ya estaba activo, no se resuelven referencias (no hay SELECT)."""
        self.doc_activo.estado = 'activo'
        queries = _queries(
            lambda: self.doc_activo.save(update_fields=['estado'])
        )
        self.assertEqual(_cuenta_sql(queries, 'referenciadocumento'), 0)


class ConversionAsincronaTest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.user_jefe_ti)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch('documentos.tasks.convertir_documento_a_pdf.delay')
    def test_detalle_encola_conversion_sin_bloquear(self, mock_delay):
        doc = Documento.objects.create(
            tipo=self.tipo_procedimiento, nombre='Doc Office',
            codigo='TI-PRG-A1', estado='activo', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
            archivo=SimpleUploadedFile('office.docx', b'contenido'),
            tipo_archivo='docx',
        )
        resp = self.client.get(
            reverse('documentos:detalle', args=[doc.tipo_id, doc.uuid])
        )
        self.assertEqual(resp.status_code, 200)
        # Se encoló la conversión y la vista muestra la barra de progreso en
        # vez de esperar ~2-3 s a LibreOffice.
        mock_delay.assert_called_once_with(str(doc.uuid))
        self.assertContains(resp, 'conv-progress')
