"""Tests de la planilla de creación/eliminación (SGC-FOR-001) guardada en media/.

Cubre el ciclo completo: generación del XLSX (+PDF), idempotencia, encolado
asíncrono tras el commit, generación en línea cuando el broker no responde, los
puntos de enganche (aprobación final y eliminación directa) y las descargas que
sirven el archivo guardado en lugar de exportar al vuelo.
"""

import io
import json
import shutil
import tempfile
import zipfile
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import Client, override_settings

from tests_base import BaseDocumentosTest
from documentos.models import (
    CampoFormato, Formato, SeccionFormato, SolicitudAprobacionDocumento,
    SolicitudCambio,
)
from documentos.services.planillas import (
    OPERACIONES_CON_PLANILLA,
    encolar_generacion_planilla,
    generar_planilla_solicitud,
)
from documentos.tasks import generar_planilla_solicitud_task, generar_planillas_pendientes


def plantilla_xlsx_minima():
    """XLSX mínimo (zip válido) con el que exportar_xlsx puede trabajar."""
    hoja = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData/></worksheet>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types/>')
        zf.writestr('xl/worksheets/sheet1.xml', hoja)
    return buf.getvalue()


class BasePlanillasTest(BaseDocumentosTest):
    """Formato SGC-FOR-001 con plantilla + SolicitudCambio en un MEDIA_ROOT temporal."""

    @classmethod
    def setUpClass(cls):
        # El override debe activarse ANTES de super().setUpClass() porque es
        # ahí donde se ejecuta setUpTestData() (que crea el Formato y guarda la
        # plantilla en MEDIA_ROOT): si no, la plantilla se escribe en el media
        # real y el test la busca en el temporal.
        cls._media_dir = tempfile.mkdtemp(prefix='polidoc-planillas-')
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._media_override.enable()
        try:
            super().setUpClass()
        except Exception:
            cls._media_override.disable()
            shutil.rmtree(cls._media_dir, ignore_errors=True)
            raise
        # addClassCleanup solo es válido una vez ejecutado super().setUpClass().
        cls.addClassCleanup(cls._media_override.disable)
        cls.addClassCleanup(shutil.rmtree, cls._media_dir, ignore_errors=True)

    @classmethod
    def crear_formato(cls, codigo='SGC-FOR-001', **kwargs):
        defaults = dict(
            codigo=codigo, nombre='Planilla de cambio', version='1', edicion='1',
            activo=True, aplica_creacion=True, aplica_eliminacion=True,
        )
        defaults.update(kwargs)
        # Formato.save() intenta convertir la plantilla a PDF: lo evitamos para
        # que los tests no dependan de LibreOffice.
        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=None):
            formato = Formato.objects.create(**defaults)
            formato.plantilla_xlsx.save(
                'plantilla.xlsx', ContentFile(plantilla_xlsx_minima()), save=True,
            )
        seccion = SeccionFormato.objects.create(formato=formato, titulo='General', orden=0)
        CampoFormato.objects.create(
            seccion=seccion, clave='motivo', etiqueta='Motivo', tipo='texto',
            origen='usuario', requerido=True, celda_excel='B10',
        )
        return formato

    @classmethod
    def crear_solicitud(cls, formato, codigo=None, estado='aprobado', **kwargs):
        defaults = dict(
            codigo=codigo or f'SC-PLAN-{formato.pk}',
            formato=formato,
            operacion='creacion',
            documento=cls.doc_en_revision,
            solicitante=cls.user_trabajador,
            departamento=cls.depto_ti,
            respuestas={'motivo': 'Revisión del procedimiento'},
            estado=estado,
        )
        defaults.update(kwargs)
        return SolicitudCambio.objects.create(**defaults)


# =============================================================================
# GENERACIÓN DE LA PLANILLA
# =============================================================================

class GenerarPlanillaSolicitudTest(BasePlanillasTest):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.formato = cls.crear_formato()
        cls.solicitud = cls.crear_solicitud(cls.formato)

    def test_genera_xlsx_y_lo_guarda_en_media(self):
        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=None):
            resultado = generar_planilla_solicitud(self.solicitud)

        self.assertTrue(resultado['success'], resultado)
        self.solicitud.refresh_from_db()
        self.assertTrue(self.solicitud.planilla_xlsx)
        self.assertTrue(self.solicitud.planilla_xlsx.name.startswith(
            'solicitudes_cambio/planillas/'
        ))
        self.assertTrue(self.solicitud.planilla_xlsx.path.endswith('.xlsx'))
        with self.solicitud.planilla_xlsx.open('rb') as f:
            contenido = f.read()
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(contenido)))
        # Sin LibreOffice no hay PDF, pero la generación no falla.
        self.assertFalse(self.solicitud.planilla_pdf)

    def test_es_idempotente(self):
        ruta_pdf = 'solicitudes_cambio/planillas/2026/09/SC-idem.pdf'
        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=ruta_pdf):
            primero = generar_planilla_solicitud(self.solicitud)
            self.solicitud.refresh_from_db()
            nombre = self.solicitud.planilla_xlsx.name

            with patch('documentos.services.exportacion.exportar_xlsx') as mock_export:
                segundo = generar_planilla_solicitud(self.solicitud)
            mock_export.assert_not_called()

        self.assertTrue(primero['success'])
        self.assertTrue(segundo['success'])
        self.assertTrue(segundo.get('ya_generada'))
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.planilla_xlsx.name, nombre)
        self.assertEqual(self.solicitud.planilla_pdf.name, ruta_pdf)

    def test_pdf_se_guarda_cuando_libreoffice_responde(self):
        ruta_pdf = 'solicitudes_cambio/planillas/2026/09/SC-plan.pdf'
        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=ruta_pdf):
            resultado = generar_planilla_solicitud(self.solicitud)

        self.assertTrue(resultado['success'], resultado)
        self.assertEqual(resultado['pdf'], ruta_pdf)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.planilla_pdf.name, ruta_pdf)

    def test_sin_plantilla_devuelve_error(self):
        formato = self.crear_formato(codigo='SGC-SIN-PLANTILLA')
        formato.plantilla_xlsx = ''
        formato.save(update_fields=['plantilla_xlsx'])
        solicitud = self.crear_solicitud(formato)

        resultado = generar_planilla_solicitud(solicitud)

        self.assertFalse(resultado['success'])
        self.assertIn('plantilla', resultado['error'])
        self.assertFalse(solicitud.planilla_xlsx)

    def test_operacion_modificacion_no_tiene_planilla(self):
        solicitud = self.crear_solicitud(
            self.formato, codigo='SC-PLAN-MOD', operacion='modificacion',
        )

        resultado = generar_planilla_solicitud(solicitud)

        self.assertFalse(resultado['success'])
        self.assertIn('SGC-FOR-001', resultado['error'])
        self.assertNotIn('modificacion', OPERACIONES_CON_PLANILLA)

    def test_force_regenera_los_archivos(self):
        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=None):
            generar_planilla_solicitud(self.solicitud)
            self.solicitud.refresh_from_db()
            with patch('documentos.services.exportacion.exportar_xlsx') as mock_export:
                mock_export.return_value = b'REGENERADO'
                resultado = generar_planilla_solicitud(self.solicitud, force=True)

        self.assertTrue(resultado['success'])
        mock_export.assert_called_once()
        self.solicitud.refresh_from_db()
        self.assertTrue(self.solicitud.planilla_xlsx)

    def test_tarea_rechaza_solicitudes_no_aprobadas(self):
        en_revision = self.crear_solicitud(
            self.formato, codigo='SC-PLAN-EN-REVISION', estado='en_revision',
        )
        with patch('documentos.services.planillas.generar_planilla_solicitud') as mock_gen:
            result = generar_planilla_solicitud_task.apply(args=[str(en_revision.uuid)])

        mock_gen.assert_not_called()
        self.assertFalse(result.result['success'])
        self.assertIn('en_revision', result.result['error'])


# =============================================================================
# ENCOLADO ASÍNCRONO
# =============================================================================

class EncolarGeneracionPlanillaTest(BasePlanillasTest):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.formato = cls.crear_formato()
        cls.solicitud = cls.crear_solicitud(cls.formato)

    def test_encola_despues_del_commit(self):
        with patch('documentos.tasks.generar_planilla_solicitud_task') as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                encolar_generacion_planilla(self.solicitud)

        mock_task.apply_async.assert_called_once()
        self.assertEqual(
            mock_task.apply_async.call_args.kwargs['args'],
            [str(self.solicitud.uuid)],
        )

    def test_no_publica_antes_del_commit(self):
        with patch('documentos.tasks.generar_planilla_solicitud_task') as mock_task:
            encolar_generacion_planilla(self.solicitud)
            mock_task.apply_async.assert_not_called()

    def test_si_el_broker_cae_genera_en_linea(self):
        with patch('documentos.tasks.generar_planilla_solicitud_task') as mock_task:
            mock_task.apply_async.side_effect = Exception('broker caído')
            with patch('documentos.services.planillas.generar_planilla_solicitud') as mock_gen:
                mock_gen.return_value = {'success': True}
                with self.captureOnCommitCallbacks(execute=True):
                    encolar_generacion_planilla(self.solicitud)

        mock_gen.assert_called_once()
        self.assertEqual(mock_gen.call_args.args[0].uuid, self.solicitud.uuid)

    def test_si_falla_en_linea_no_propaga_la_excepcion(self):
        with patch('documentos.tasks.generar_planilla_solicitud_task') as mock_task:
            mock_task.apply_async.side_effect = Exception('broker caído')
            with patch('documentos.services.planillas.generar_planilla_solicitud') as mock_gen:
                mock_gen.side_effect = RuntimeError('explosión')
                with self.captureOnCommitCallbacks(execute=True):
                    encolar_generacion_planilla(self.solicitud)

        mock_gen.assert_called_once()


# =============================================================================
# PUNTOS DE ENGANCHE: APROBACIÓN FINAL
# =============================================================================

class AprobacionEncolaPlanillasTest(BasePlanillasTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato = self.crear_formato()
        self.solicitud = self.crear_solicitud(self.formato, estado='en_revision')
        self.solicitud_aprob = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision,
            accion_solicitud='creacion',
            solicitante=self.user_trabajador,
            propietario=self.user_trabajador,
            aprobador=self.user_jefe_ti,
            departamento_origen=self.depto_ti,
            estado='pendiente',
            es_final=True,
        )
        self.url = (
            f'/documentos/api/procedimientos_generales/'
            f'{self.doc_en_revision.uuid}/aprobar-creacion/'
        )

    def test_al_aprobar_encola_la_planilla(self):
        self.client.login(username='jefe_ti', password='testpass123')
        with patch('documentos.tasks.generar_planilla_solicitud_task') as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(
                    self.url, data=json.dumps({}), content_type='application/json',
                )

        self.assertEqual(resp.status_code, 200)
        mock_task.apply_async.assert_called_once()
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'aprobado')

    def test_si_no_es_final_no_encola(self):
        self.solicitud_aprob.es_final = False
        self.solicitud_aprob.save(update_fields=['es_final'])
        self.client.login(username='jefe_ti', password='testpass123')

        with patch('documentos.tasks.generar_planilla_solicitud_task') as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(
                    self.url, data=json.dumps({}), content_type='application/json',
                )

        self.assertEqual(resp.status_code, 200)
        mock_task.apply_async.assert_not_called()
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'en_revision')


# =============================================================================
# PUNTO DE ENGANCHE: ELIMINACIÓN DIRECTA
# =============================================================================

class EliminacionDirectaEncolaPlanillaTest(BasePlanillasTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato = self.crear_formato(codigo='SGC-FOR-001-ELI')
        self.url = (
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/eliminar/'
        )

    def test_eliminar_directamente_encola_la_planilla(self):
        self.client.login(username='admin01', password='testpass123')

        with patch('documentos.tasks.generar_planilla_solicitud_task') as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(self.url, data={'campo_motivo': 'Documento obsoleto'})

        self.assertEqual(resp.status_code, 302)
        solicitud = SolicitudCambio.objects.get(documento=self.doc_activo)
        self.assertEqual(solicitud.estado, 'aprobado')
        self.assertEqual(solicitud.operacion, 'eliminacion')
        mock_task.apply_async.assert_called_once()
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'obsoleto')


# =============================================================================
# DESCARGAS
# =============================================================================

class DescargaPlanillaGuardadaTest(BasePlanillasTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato = self.crear_formato()
        self.solicitud = self.crear_solicitud(self.formato)
        self.solicitud.planilla_xlsx.save(
            'SC-plan_creacion.xlsx', ContentFile(b'XLSX GUARDADO'), save=True,
        )
        self.solicitud.planilla_pdf.save(
            'SC-plan_creacion.pdf', ContentFile(b'%PDF-1.4 GUARDADO'), save=True,
        )
        self.client.login(username='trab01', password='testpass123')

    def test_descarga_xlsx_guardado_sin_reexportar(self):
        with patch('documentos.services.exportacion.exportar_xlsx') as mock_export:
            resp = self.client.get(
                f'/documentos/solicitud/{self.solicitud.uuid}/exportar/xlsx/'
            )

        mock_export.assert_not_called()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'XLSX GUARDADO')
        self.assertIn('attachment', resp['Content-Disposition'])

    def test_descarga_pdf_guardado_sin_regenerar(self):
        with patch('documentos.services.exportacion.exportar_pdf') as mock_export:
            resp = self.client.get(
                f'/documentos/solicitud/{self.solicitud.uuid}/exportar/pdf/'
            )

        mock_export.assert_not_called()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'%PDF-1.4 GUARDADO')

    def test_descarga_xlsx_exporta_al_vuelo_si_no_esta_guardado(self):
        self.solicitud.planilla_xlsx.delete(save=True)
        self.solicitud.refresh_from_db()

        with patch('documentos.services.exportacion.exportar_xlsx') as mock_export:
            mock_export.return_value = b'GENERADO AL VUELO'
            resp = self.client.get(
                f'/documentos/solicitud/{self.solicitud.uuid}/exportar/xlsx/'
            )

        mock_export.assert_called_once()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'GENERADO AL VUELO')


# =============================================================================
# BARRIDO PERIÓDICO (CELERY BEAT) — RED DE SEGURIDAD ANTE WORKER CAÍDO
# =============================================================================

class BarridoPlanillasPendientesTest(BasePlanillasTest):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.formato = cls.crear_formato()

    def _pendiente(self, codigo, **kwargs):
        return self.crear_solicitud(self.formato, codigo=codigo, **kwargs)

    def test_genera_las_planillas_pendientes(self):
        solicitud = self._pendiente('SC-BARR-001')

        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=None):
            resultado = generar_planillas_pendientes()

        self.assertEqual(resultado, {'revisadas': 1, 'generadas': 1, 'errores': 0})
        solicitud.refresh_from_db()
        self.assertTrue(solicitud.planilla_xlsx)

    def test_no_toca_las_que_ya_tienen_planilla(self):
        solicitud = self._pendiente('SC-BARR-002')
        solicitud.planilla_xlsx.save('ya-existe.xlsx', ContentFile(b'OK'), save=True)

        with patch('documentos.services.exportacion.exportar_xlsx') as mock_export:
            resultado = generar_planillas_pendientes()

        mock_export.assert_not_called()
        self.assertEqual(resultado, {'revisadas': 0, 'generadas': 0, 'errores': 0})

    def test_ignora_las_no_aprobadas(self):
        self._pendiente('SC-BARR-003', estado='en_revision')

        resultado = generar_planillas_pendientes()

        self.assertEqual(resultado['revisadas'], 0)

    def test_ignora_las_de_operacion_modificacion(self):
        self._pendiente('SC-BARR-004', operacion='modificacion')

        resultado = generar_planillas_pendientes()

        self.assertEqual(resultado['revisadas'], 0)

    def test_ignora_los_formatos_sin_plantilla(self):
        """No reintenta indefinidamente si el formato no tiene plantilla."""
        formato = self.crear_formato(codigo='SGC-SIN-PLANTILLA-BARR')
        formato.plantilla_xlsx = ''
        formato.save(update_fields=['plantilla_xlsx'])
        self.crear_solicitud(formato, codigo='SC-BARR-005')

        resultado = generar_planillas_pendientes()

        self.assertEqual(resultado['revisadas'], 0)

    def test_un_error_no_aborta_el_resto(self):
        self._pendiente('SC-BARR-006')
        self._pendiente('SC-BARR-007')

        with patch('documentos.services.planillas.generar_planilla_solicitud') as mock_gen:
            mock_gen.side_effect = [RuntimeError('boom'), {'success': True}]
            resultado = generar_planillas_pendientes()

        self.assertEqual(resultado, {'revisadas': 2, 'generadas': 1, 'errores': 1})

    def test_respeta_el_tamano_del_lote(self):
        for indice in range(3):
            self._pendiente(f'SC-BARR-01{indice}')

        with override_settings(PLANILLAS_BARRIDO_LOTE=2):
            with patch('documentos.services.planillas.generar_planilla_solicitud') as mock_gen:
                mock_gen.return_value = {'success': True}
                resultado = generar_planillas_pendientes()

        self.assertEqual(resultado, {'revisadas': 2, 'generadas': 2, 'errores': 0})
        self.assertEqual(mock_gen.call_count, 2)

    def test_esta_programada_en_celery_beat(self):
        from django.conf import settings

        entrada = settings.CELERY_BEAT_SCHEDULE.get('generar-planillas-pendientes')

        self.assertIsNotNone(entrada)
        self.assertEqual(entrada['task'], 'documentos.tasks.generar_planillas_pendientes')
        self.assertEqual(
            entrada['schedule'].total_seconds(),
            60 * settings.PLANILLAS_BARRIDO_MINUTOS,
        )
        self.assertEqual(settings.PLANILLAS_BARRIDO_MINUTOS, 15)
        self.assertEqual(settings.PLANILLAS_BARRIDO_LOTE, 100)
