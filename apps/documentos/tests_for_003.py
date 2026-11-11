"""Tests de la Tabla de cambios y modificaciones (SGC-FOR-003).

Cubre: multifila, autocompletado, persistencia para borrador/reingreso,
autorización al aprobar, generación XLSX multi-hoja con sello SES, descargas
y permisos.
"""
import io
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import openpyxl

from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import (
    CambioDocumento, FirmaElectronica, Formato, PlanillaCambios,
)
from documentos.services import for_003 as svc
from documentos.tests_sgc_utils import crear_plantilla_for_003

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_for003_')
PDF_BYTES = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseFor003Test(BaseDocumentosTest):
    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        plantilla = crear_plantilla_for_003(
            os.path.join(TEST_MEDIA, 'for003_sintetica.xlsx'),
        )
        call_command(
            'integrar_formatos_sgc', solo='003', apply=True, verbosity=0,
            plantilla=plantilla, stdout=io.StringIO(),
        )
        cls.formato = Formato.objects.get(codigo='SGC-FOR-003')

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato.refresh_from_db()

    def _planilla(self, estado='borrador', documento=None, responsable=None):
        documento = documento or self.doc_activo
        return PlanillaCambios.objects.create(
            codigo=f'PC-F003-{uuid.uuid4().hex[:8]}',
            documento=documento, tipo_cambio='modificacion',
            justificacion='Cambio de prueba',
            responsable=responsable or self.user_trabajador,
            version_destino='2', estado=estado,
            version_actual_snapshot=documento.version_actual,
        )

    def _filas(self, planilla, n=1, autorizar=False, firma=None):
        svc.guardar_filas(planilla, [
            {'numero_pagina': str(i + 1), 'descripcion': f'Cambio {i + 1}'}
            for i in range(n)
        ])
        if autorizar:
            svc.autorizar_filas(planilla, self.user_jefe_gg, firma=firma)
        return list(svc.filas_de_planilla(planilla))

    def _firma(self):
        return FirmaElectronica.objects.create(
            origen_tipo='distribucion', origen_id='1', tipo='interna',
            firmante=self.user_jefe_gg, firmante_clave='jefe_gg',
            firmante_nombre='Jefe GG', firmante_email='jefe_gg@example.com',
            estado='firmada', hash_contenido='a' * 64,
            fecha_firma=timezone.now(),
        )

    def _url_crear(self, documento, planilla_uuid=None):
        url = (
            reverse('documentos:crear_planilla')
            + f'?tipo={documento.tipo_id}&doc_uuid={documento.uuid}'
        )
        if planilla_uuid:
            url += f'&planilla_uuid={planilla_uuid}'
        return url


class IntegracionFormatoTest(BaseFor003Test):
    def test_comando_crea_formato_y_tabla(self):
        self.assertEqual(self.formato.nombre, 'Tabla de Cambios y Modificaciones')
        self.assertFalse(self.formato.aplica_creacion)
        self.assertFalse(self.formato.aplica_modificacion)
        self.assertFalse(self.formato.aplica_eliminacion)
        self.assertTrue(self.formato.plantilla_xlsx)
        campo = self.formato.get_todos_los_campos()[0]
        self.assertEqual(campo.tipo, 'tabla')
        self.assertEqual(
            campo.config_tabla['fila_inicio'], svc.FILA_INICIO,
        )


class FilasTest(BaseFor003Test):
    def test_autocompletar_fila(self):
        planilla = self._planilla()
        datos = svc.autocompletar_fila(planilla)
        self.assertEqual(datos['version'], '2')
        self.assertEqual(datos['descripcion'], 'Cambio de prueba')
        self.assertEqual(datos['realizado_por'], 'Trab Test')
        self.assertEqual(datos['fecha'], timezone.localdate())

    def test_parseo_borrador_permite_incompletas(self):
        filas, errores = svc.parsear_filas(
            [{'numero_pagina': '3', 'descripcion': ''}], parcial=True,
        )
        self.assertEqual(errores, [])
        self.assertEqual(len(filas), 1)

    def test_parseo_envio_exige_descripcion_y_filas(self):
        filas, errores = svc.parsear_filas(
            [{'numero_pagina': '3', 'descripcion': ''}], parcial=False,
        )
        self.assertTrue(errores)
        self.assertEqual(filas, [])

        filas, errores = svc.parsear_filas([], parcial=False)
        self.assertTrue(errores)
        self.assertEqual(filas, [])

    def test_ignora_filas_vacias(self):
        filas, errores = svc.parsear_filas(
            [{'numero_pagina': '', 'descripcion': ''}], parcial=False,
        )
        self.assertEqual(filas, [])
        self.assertTrue(errores)

    def test_guardar_filas_reemplaza_y_precarga(self):
        planilla = self._planilla()
        self._filas(planilla, n=2)
        self.assertEqual(svc.filas_de_planilla(planilla).count(), 2)

        svc.guardar_filas(planilla, [
            {'numero_pagina': '7', 'descripcion': 'Solo este queda'},
        ])
        filas = list(svc.filas_de_planilla(planilla))
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0].numero_pagina, '7')
        self.assertEqual(filas[0].realizado_por, self.user_trabajador)

    def test_autorizar_filas(self):
        planilla = self._planilla()
        firma = self._firma()
        self._filas(planilla, n=2, autorizar=True, firma=firma)

        filas = svc.filas_de_planilla(planilla)
        self.assertTrue(all(f.esta_autorizado for f in filas))
        self.assertTrue(all(f.autorizado_por == self.user_jefe_gg for f in filas))
        self.assertTrue(all(f.firma_autorizacion_id == firma.id for f in filas))

    def test_historicas_excluye_no_autorizadas(self):
        planilla = self._planilla()
        self._filas(planilla, n=1)
        self.assertEqual(svc.filas_historicas(planilla.documento).count(), 0)

        svc.autorizar_filas(planilla, self.user_jefe_gg)
        self.assertEqual(svc.filas_historicas(planilla.documento).count(), 1)


class GeneracionXlsxTest(BaseFor003Test):
    def test_celdas_y_sello(self):
        planilla = self._planilla()
        firma = self._firma()
        self._filas(planilla, n=1, autorizar=True, firma=firma)

        contenido = svc.generar_xlsx(
            self.formato, list(svc.filas_historicas(planilla.documento)),
        )
        wb = openpyxl.load_workbook(io.BytesIO(contenido))
        ws = wb['SGC-FOR-003']
        self.assertEqual(ws['A12'].value, '1')
        self.assertEqual(ws['B12'].value, '2')
        self.assertEqual(ws['C12'].value, 'Cambio 1')
        self.assertEqual(ws['F12'].value, timezone.localdate().strftime('%d/%m/%Y'))
        self.assertEqual(ws['G12'].value, 'Trab Test')
        self.assertEqual(ws['I12'].value, 'Jefe GG')
        self.assertIn('PAGINA: 1 DE 1', ws[svc.CELDA_PAGINA].value)
        self.assertIn('Firmado electrónicamente', ws['A25'].value)
        self.assertIn('SES #', ws['A25'].value)
        # El instructivo (otra hoja de la plantilla) no se toca.
        self.assertIsNone(wb['SGC-IDL-009']['A25'].value)

    def test_multihoja_con_paginas(self):
        planilla = self._planilla()
        filas = self._filas(planilla, n=14, autorizar=True)

        contenido = svc.generar_xlsx(self.formato, filas)
        wb = openpyxl.load_workbook(io.BytesIO(contenido))
        self.assertIn('SGC-IDL-009', wb.sheetnames)
        self.assertEqual(len(wb.sheetnames), 3)
        primera = wb['SGC-FOR-003']
        segunda = wb['SGC-FOR-003 (2)']
        self.assertIn('PAGINA: 1 DE 2', primera[svc.CELDA_PAGINA].value)
        self.assertIn('PAGINA: 2 DE 2', segunda[svc.CELDA_PAGINA].value)
        self.assertEqual(primera['A12'].value, '1')
        self.assertEqual(segunda['A12'].value, '13')
        # La segunda hoja no arrastra filas de la primera.
        self.assertIsNone(segunda['A14'].value)


class GenerarYGuardarTest(BaseFor003Test):
    def test_guarda_xlsx_y_pdf(self):
        planilla = self._planilla()
        self._filas(planilla, n=2, autorizar=True)
        planilla.estado = 'aprobado'
        planilla.save(update_fields=['estado'])

        with patch('documentos.services.for_003._xlsx_a_pdf',
                   return_value=PDF_BYTES):
            resultado = svc.generar_y_guardar(planilla, forzar=True)

        self.assertTrue(resultado['success'])
        planilla.refresh_from_db()
        self.assertTrue(planilla.for_003_xlsx.name.endswith('.xlsx'))
        self.assertTrue(planilla.for_003_pdf.name.endswith('.pdf'))

    def test_sin_plantilla_no_rompe(self):
        Formato.objects.filter(codigo='SGC-FOR-003').update(activo=False)
        planilla = self._planilla()
        self._filas(planilla, n=1, autorizar=True)
        planilla.estado = 'aprobado'
        planilla.save(update_fields=['estado'])
        resultado = svc.generar_y_guardar(planilla, forzar=True)
        self.assertFalse(resultado['success'])
        self.assertIn('no está integrado', resultado['error'])


class FormularioPlanillaTest(BaseFor003Test):
    def test_precarga_filas_del_borrador(self):
        planilla = self._planilla(responsable=self.user_jefe_ti)
        svc.guardar_filas(planilla, [
            {'numero_pagina': '3', 'descripcion': 'Se actualizó el alcance'},
        ])
        self.client.login(username='jefe_ti', password='testpass123')

        respuesta = self.client.get(
            self._url_crear(self.doc_activo, planilla.uuid),
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'SGC-FOR-003')
        self.assertContains(respuesta, 'Se actualiz')
        self.assertContains(respuesta, 'for003-borrador')

    def test_auto_reanuda_el_borrador_existente(self):
        planilla = self._planilla(responsable=self.user_jefe_ti)
        self.client.login(username='jefe_ti', password='testpass123')

        respuesta = self.client.get(self._url_crear(self.doc_activo))
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn(
            f'planilla_uuid={planilla.uuid}', respuesta['Location'],
        )

    def test_nuevo_carga_fila_autocompletada(self):
        self.client.login(username='jefe_ti', password='testpass123')
        respuesta = self.client.get(self._url_crear(self.doc_activo))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'for003-auto')
        self.assertContains(respuesta, 'Agregar fila')

    def test_no_editable_sin_documento(self):
        respuesta = self.client.get(reverse('documentos:crear_planilla'))
        self.assertEqual(respuesta.status_code, 302)


class DescargasTest(BaseFor003Test):
    def test_descarga_documento_xlsx(self):
        planilla = self._planilla()
        self._filas(planilla, n=1, autorizar=True)
        planilla.estado = 'aprobado'
        planilla.save(update_fields=['estado'])
        self.client.login(username='jefe_ti', password='testpass123')

        respuesta = self.client.get(reverse(
            'documentos:documento_for_003',
            args=[self.doc_activo.tipo_id, self.doc_activo.uuid, 'xlsx'],
        ))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('attachment', respuesta['Content-Disposition'])

    def test_descarga_planilla_genera_si_falta(self):
        planilla = self._planilla()
        self._filas(planilla, n=1, autorizar=True)
        planilla.estado = 'aprobado'
        planilla.save(update_fields=['estado'])
        self.client.login(username='jefe_ti', password='testpass123')

        with patch('documentos.services.for_003._xlsx_a_pdf',
                   return_value=None):
            respuesta = self.client.get(reverse(
                'documentos:planilla_for_003',
                args=[planilla.uuid, 'xlsx'],
            ))
        self.assertEqual(respuesta.status_code, 200)
        planilla.refresh_from_db()
        self.assertTrue(planilla.for_003_xlsx.name)

    def test_documento_sin_cambios_redirige(self):
        self.client.login(username='jefe_ti', password='testpass123')
        respuesta = self.client.get(reverse(
            'documentos:documento_for_003',
            args=[self.doc_activo.tipo_id, self.doc_activo.uuid, 'xlsx'],
        ))
        self.assertEqual(respuesta.status_code, 302)


class AprobacionFor003Test(BaseFor003Test):
    def test_aprobacion_autoriza_filas_y_genera(self):
        documento = self.doc_en_revision
        planilla = self._planilla(
            estado='en_revision', documento=documento,
            responsable=self.user_trabajador,
        )
        self._filas(planilla, n=2)

        from documentos.views import crear_cadena_aprobacion_planilla

        crear_cadena_aprobacion_planilla(
            planilla=planilla, usuario=self.user_trabajador,
            documento=documento,
        )
        self.crear_pin_firma(self.user_jefe_ti)
        self.crear_pin_firma(self.user_jefe_gg)

        self.client.login(username='jefe_ti', password='testpass123')
        self.client.post(
            reverse('documentos:aprobar_planilla', args=[planilla.uuid]),
            {'accion': 'aprobar', 'observaciones': 'evaluado', 'pin': '1234'},
        )

        self.client.login(username='jefe_gg', password='testpass123')
        with patch('documentos.services.for_003._xlsx_a_pdf',
                   return_value=None):
            with self.captureOnCommitCallbacks(execute=True):
                respuesta = self.client.post(
                    reverse(
                        'documentos:aprobar_planilla', args=[planilla.uuid],
                    ),
                    {'accion': 'aprobar', 'observaciones': 'ok', 'pin': '1234'},
                )

        self.assertEqual(respuesta.status_code, 302)
        planilla.refresh_from_db()
        self.assertEqual(planilla.estado, 'aprobado')
        autorizadas = planilla.cambios_for_003.filter(
            autorizado_por__isnull=False,
        )
        self.assertEqual(autorizadas.count(), 2)
        self.assertTrue(all(
            f.autorizado_por == self.user_jefe_gg for f in autorizadas
        ))
        self.assertTrue(planilla.for_003_xlsx.name)
