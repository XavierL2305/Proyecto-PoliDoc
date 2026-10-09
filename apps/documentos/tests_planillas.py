"""Tests de la planilla de creación/eliminación (SGC-FOR-001) guardada en media/.

Cubre el ciclo completo: generación del XLSX (+PDF), idempotencia, encolado
asíncrono tras el commit, generación en línea cuando el broker no responde, los
puntos de enganche (aprobación final y eliminación directa) y las descargas que
sirven el archivo guardado en lugar de exportar al vuelo.
"""

import io
import json
import os
import shutil
import tempfile
import zipfile
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import Client, override_settings
from django.utils import timezone

from tests_base import BaseDocumentosTest
from documentos.models import (
    CampoFormato, Formato, SeccionFormato, SolicitudAprobacionDocumento,
    SolicitudCambio, respuestas_planilla_actual,
)
from documentos.services.exportacion import exportar_xlsx
from documentos.services.planillas import (
    OPERACIONES_CON_PLANILLA,
    encolar_generacion_planilla,
    generar_planilla_solicitud,
    guardar_planilla_solicitud,
    invalidar_planillas_documento,
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
        """Sin archivo persistido lo genera, lo persiste y lo sirve."""
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
        self.solicitud.refresh_from_db()
        self.assertTrue(self.solicitud.planilla_xlsx)


# =============================================================================
# PERSISTENCIA DEL RESULTADO EN LA SOLICITUD
# =============================================================================

class GuardarPlanillaSolicitudTest(BasePlanillasTest):
    """``guardar_planilla_solicitud``: exporta, persiste y reutiliza."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.formato = cls.crear_formato()
        cls.solicitud = cls.crear_solicitud(cls.formato)

    def test_persiste_modificacion_tambien(self):
        """La operación que `generar_planilla_solicitud` rechaza sí se persiste."""
        solicitud = self.crear_solicitud(
            self.formato, codigo='SC-PERSIST-MOD', operacion='modificacion',
        )

        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=None):
            resultado = guardar_planilla_solicitud(solicitud)

        self.assertTrue(resultado['success'], resultado)
        solicitud.refresh_from_db()
        self.assertTrue(solicitud.planilla_xlsx)
        self.assertFalse(solicitud.planilla_pdf)
        # El flujo automático no se amplía: el barrido sigue sin tocarla.
        self.assertNotIn(solicitud.operacion, OPERACIONES_CON_PLANILLA)

    def test_es_idempotente(self):
        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=None):
            guardar_planilla_solicitud(self.solicitud)
            self.solicitud.refresh_from_db()
            nombre = self.solicitud.planilla_xlsx.name

            with patch('documentos.services.exportacion.exportar_xlsx') as mock_export:
                resultado = guardar_planilla_solicitud(self.solicitud)

        mock_export.assert_not_called()
        self.assertTrue(resultado['success'])
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.planilla_xlsx.name, nombre)

    def test_forzar_regenera_los_archivos(self):
        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=None):
            guardar_planilla_solicitud(self.solicitud)
            self.solicitud.refresh_from_db()
            with patch('documentos.services.exportacion.exportar_xlsx') as mock_export:
                mock_export.return_value = b'REGENERADO'
                resultado = guardar_planilla_solicitud(self.solicitud, forzar=True)

        self.assertTrue(resultado['success'])
        mock_export.assert_called_once()
        self.solicitud.refresh_from_db()
        with self.solicitud.planilla_xlsx.open('rb') as f:
            self.assertEqual(f.read(), b'REGENERADO')

    def test_con_pdf_false_no_intenta_convertir(self):
        """La descarga de XLSX no exige LibreOffice."""
        with patch('documentos.tasks.convertir_a_pdf_sync') as mock_convertir:
            resultado = guardar_planilla_solicitud(
                self.solicitud, con_pdf=False,
            )

        mock_convertir.assert_not_called()
        self.assertTrue(resultado['success'])
        self.assertIsNone(resultado['pdf'])

    def test_sin_plantilla_devuelve_error(self):
        formato = self.crear_formato(codigo='SGC-SIN-PLANTILLA-PERSIST')
        formato.plantilla_xlsx = ''
        formato.save(update_fields=['plantilla_xlsx'])
        solicitud = self.crear_solicitud(formato, codigo='SC-PERSIST-SP')

        resultado = guardar_planilla_solicitud(solicitud)

        self.assertFalse(resultado['success'])
        self.assertIn('plantilla', resultado['error'])
        self.assertFalse(solicitud.planilla_xlsx)


class DescargaPersistePlanillaTest(BasePlanillasTest):
    """La vista de descarga persiste el resultado y la reutiliza después."""

    #: ruta relativa a MEDIA_ROOT que devuelve el falso conversor
    PDF_RELATIVO = 'solicitudes_cambio/planillas/2026/10/SC-persist.pdf'

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato = self.crear_formato()
        self.solicitud = self.crear_solicitud(
            self.formato, codigo='SC-PERSIST', operacion='modificacion',
            estado='en_revision',
        )
        self.client.login(username='trab01', password='testpass123')

    def _falso_convertir(self, archivo_path, reparar_sincrono=False):
        """Escribe el PDF en Media (como haría LibreOffice) y devuelve la ruta."""
        ruta = os.path.join(self._media_dir, self.PDF_RELATIVO)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, 'wb') as f:
            f.write(b'%PDF-1.4 PERSISTIDO')
        return self.PDF_RELATIVO

    def test_pdf_persiste_el_resultado_en_la_solicitud(self):
        with patch('documentos.tasks.convertir_a_pdf_sync',
                   side_effect=self._falso_convertir) as mock:
            resp = self.client.get(
                f'/documentos/solicitud/{self.solicitud.uuid}/exportar/pdf/'
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'%PDF-1.4 PERSISTIDO')
        # La reparación debe ser en línea: el XLSX temporal se borra al servir.
        self.assertTrue(mock.call_args.kwargs.get('reparar_sincrono'))
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.planilla_pdf.name, self.PDF_RELATIVO)
        self.assertTrue(self.solicitud.planilla_xlsx)

    def test_pdf_se_sirve_guardado_sin_regenerar(self):
        with patch('documentos.tasks.convertir_a_pdf_sync',
                   side_effect=self._falso_convertir):
            self.client.get(
                f'/documentos/solicitud/{self.solicitud.uuid}/exportar/pdf/'
            )

        with patch('documentos.tasks.convertir_a_pdf_sync') as mock, \
                patch('documentos.services.exportacion.exportar_pdf') as mock_export:
            resp = self.client.get(
                f'/documentos/solicitud/{self.solicitud.uuid}/exportar/pdf/'
            )

        mock.assert_not_called()
        mock_export.assert_not_called()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'%PDF-1.4 PERSISTIDO')

    def test_pdf_de_borrador_se_regenera(self):
        """Un borrador puede cambiar: no se sirve el PDF de una versión previa."""
        self.solicitud.estado = 'borrador'
        self.solicitud.save(update_fields=['estado'])
        self.solicitud.planilla_xlsx.save('previo.xlsx', ContentFile(b'PREVIO'), save=True)

        with patch('documentos.tasks.convertir_a_pdf_sync',
                   side_effect=self._falso_convertir) as mock:
            resp = self.client.get(
                f'/documentos/solicitud/{self.solicitud.uuid}/exportar/pdf/'
            )

        self.assertEqual(resp.status_code, 200)
        mock.assert_called_once()
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.planilla_pdf.name, self.PDF_RELATIVO)

    def test_xlsx_de_borrador_se_regenera(self):
        self.solicitud.estado = 'borrador'
        self.solicitud.save(update_fields=['estado'])
        self.solicitud.planilla_xlsx.save('previo.xlsx', ContentFile(b'PREVIO'), save=True)

        with patch('documentos.services.exportacion.exportar_xlsx') as mock_export:
            mock_export.return_value = b'RESPUESTAS ACTUALIZADAS'
            resp = self.client.get(
                f'/documentos/solicitud/{self.solicitud.uuid}/exportar/xlsx/'
            )

        self.assertEqual(resp.status_code, 200)
        mock_export.assert_called_once()
        self.assertEqual(
            b''.join(resp.streaming_content), b'RESPUESTAS ACTUALIZADAS',
        )
        self.solicitud.refresh_from_db()
        with self.solicitud.planilla_xlsx.open('rb') as f:
            self.assertEqual(f.read(), b'RESPUESTAS ACTUALIZADAS')

    def test_xlsx_persistido_sin_borrador_no_se_regenera(self):
        self.solicitud.planilla_xlsx.save('listo.xlsx', ContentFile(b'LISTO'), save=True)

        with patch('documentos.services.exportacion.exportar_xlsx') as mock_export:
            resp = self.client.get(
                f'/documentos/solicitud/{self.solicitud.uuid}/exportar/xlsx/'
            )

        mock_export.assert_not_called()
        self.assertEqual(b''.join(resp.streaming_content), b'LISTO')


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


# =============================================================================
# LA PLANILLA REFLEJA SIEMPRE EL ESTADO ACTUAL DEL DOCUMENTO
# =============================================================================

class PlanillaReflejaVigenciaActualTest(BasePlanillasTest):
    """H18 y el resto de campos auto se releen de la BD, no del snapshot.

    La planilla aprobada guarda en ``respuestas`` una instantánea; mostrarla
    o exportarla debe recalcular los campos auto contra el documento actual
    (vigencia, versión, título, operación…) y los archivos persistidos se
    invalidan cuando esos datos cambian.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.formato = cls.crear_formato()
        seccion = SeccionFormato.objects.filter(formato=cls.formato).first()
        CampoFormato.objects.create(
            seccion=seccion, clave='fecha_vigencia',
            etiqueta='Fecha de Vigencia', tipo='fecha',
            origen='fecha_vigencia_documento', celda_excel='H18',
        )
        # El documento está vigente "hoy"; la planilla se aprobó hace años.
        cls.vigencia_vieja = '2000-01-01'
        cls.doc_activo.fecha_vigencia = timezone.now()
        cls.doc_activo.save(update_fields=['fecha_vigencia'])
        cls.vigencia_actual = cls.doc_activo.fecha_vigencia.date().isoformat()
        cls.solicitud = cls.crear_solicitud(
            cls.formato,
            documento=cls.doc_activo,
            respuestas={
                'motivo': 'Revisión del procedimiento',
                'fecha_vigencia': cls.vigencia_vieja,
            },
        )

    @staticmethod
    def _hoja(xlsx_bytes):
        with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as zf:
            return zf.read('xl/worksheets/sheet1.xml').decode('utf-8')

    def test_el_snapshot_guardado_conserva_la_fecha_antigua(self):
        """Premisa: sin el recálculo, la BD y la planilla discrepan."""
        self.assertEqual(
            SolicitudCambio.objects.get(pk=self.solicitud.pk).respuestas['fecha_vigencia'],
            self.vigencia_vieja,
        )

    def test_el_helper_relee_la_vigencia_desde_la_bd(self):
        sol = SolicitudCambio.objects.get(pk=self.solicitud.pk)

        actual = respuestas_planilla_actual(sol)

        self.assertEqual(actual['fecha_vigencia'], self.vigencia_actual)
        self.assertNotEqual(actual['fecha_vigencia'], self.vigencia_vieja)
        # El resto de las respuestas no se toca.
        self.assertEqual(actual['motivo'], 'Revisión del procedimiento')
        # Y no se persiste: el snapshot en BD sigue intacto.
        sol.refresh_from_db()
        self.assertEqual(sol.respuestas['fecha_vigencia'], self.vigencia_vieja)

    def test_el_helper_vacia_el_campo_si_el_documento_no_tiene_vigencia(self):
        self.doc_activo.fecha_vigencia = None
        self.doc_activo.save(update_fields=['fecha_vigencia'])
        sol = SolicitudCambio.objects.get(pk=self.solicitud.pk)

        actual = respuestas_planilla_actual(sol)

        self.assertNotIn('fecha_vigencia', actual)

    def test_el_xlsx_exporta_la_vigencia_actual(self):
        contenido = exportar_xlsx(self.solicitud)

        hoja = self._hoja(contenido)
        self.assertIn(self.vigencia_actual, hoja)
        self.assertNotIn(self.vigencia_vieja, hoja)

    def test_la_pantalla_del_documento_muestra_la_vigencia_actual(self):
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.vigencia_actual)
        self.assertNotContains(resp, self.vigencia_vieja)
        planillas = [
            sc
            for op in resp.context['solicitudes_por_operacion']
            for sc in op['solicitudes']
            if sc.pk == self.solicitud.pk
        ]
        self.assertEqual(len(planillas), 1)
        self.assertEqual(
            planillas[0].respuestas_efectivas['fecha_vigencia'],
            self.vigencia_actual,
        )

    def test_invalidar_limpia_la_planilla_guardada(self):
        self.solicitud.planilla_xlsx.save(
            'viejo.xlsx', ContentFile(b'XLSX VIEJO'), save=True,
        )
        self.solicitud.planilla_pdf.save(
            'viejo.pdf', ContentFile(b'PDF VIEJO'), save=True,
        )

        afectadas = invalidar_planillas_documento(self.doc_activo)

        self.solicitud.refresh_from_db()
        self.assertEqual(afectadas, 1)
        self.assertFalse(self.solicitud.planilla_xlsx)
        self.assertFalse(self.solicitud.planilla_pdf)

    def test_no_invalida_documentos_sin_planilla(self):
        self.doc_en_revision.fecha_vigencia = timezone.now()
        self.doc_en_revision.save(update_fields=['fecha_vigencia'])

        self.assertEqual(invalidar_planillas_documento(self.doc_en_revision), 0)
        self.assertEqual(invalidar_planillas_documento(None), 0)

    def test_tras_invalidar_la_descarga_regenera_con_la_vigencia_actual(self):
        self.solicitud.planilla_xlsx.save(
            'viejo.xlsx', ContentFile(b'XLSX VIEJO'), save=True,
        )
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

        invalidar_planillas_documento(self.doc_activo)
        resp = self.client.get(
            f'/documentos/solicitud/{self.solicitud.uuid}/exportar/xlsx/'
        )

        self.assertEqual(resp.status_code, 200)
        hoja = self._hoja(b''.join(resp.streaming_content))
        self.assertIn(self.vigencia_actual, hoja)
        self.assertNotIn(self.vigencia_vieja, hoja)
        self.solicitud.refresh_from_db()
        self.assertTrue(self.solicitud.planilla_xlsx)
