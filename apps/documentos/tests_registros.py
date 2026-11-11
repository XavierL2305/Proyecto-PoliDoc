"""Tests de Registros de formato (SGC-FOR-010 y equivalentes).

Un registro es una instancia LLENA de un ``Formato`` ligada a un contexto. El
código del formato se repite por diseño; la identidad es el ``uuid``. No crea
``Documento`` ni toca ``Documento.codigo``.
"""
import io
import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import openpyxl

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import (
    CampoFormato, Documento, FirmaElectronica, Formato, RegistroFormato,
    SeccionFormato,
)
from documentos.services import registros as svc
from documentos.services.firmas import ErrorFirma, firmar

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_registros_')


def _xlsx_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws['A1'] = ''
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseRegistrosTest(BaseDocumentosTest):
    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        with patch('documentos.tasks.convertir_formato_a_pdf.delay',
                   return_value=None), patch(
                'documentos.tasks.convertir_formato_a_pdf_sync',
                return_value=None):
            cls.formato = Formato.objects.create(
                codigo='SGC-FOR-010', nombre='Plan de Acción ante NC',
                version='1', edicion='1', activo=True, permite_registros=True,
            )
            cls.formato.plantilla_xlsx.save(
                'for-010.xlsx',
                SimpleUploadedFile('for-010.xlsx', _xlsx_bytes()),
                save=True,
            )
        seccion = SeccionFormato.objects.create(
            formato=cls.formato, titulo='Datos', orden=1,
        )
        CampoFormato.objects.create(
            seccion=seccion, clave='descripcion', etiqueta='Descripción',
            tipo='texto', origen='usuario', celda_excel='A1',
            requerido=True, orden=1,
        )

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato.refresh_from_db()

    def _registro(self, documento=None, estado='borrador', aprobador=None,
                  titulo='Plan de acción'):
        documento = documento or self.doc_activo
        return RegistroFormato.objects.create(
            formato=self.formato,
            formato_codigo=self.formato.codigo,
            formato_version=self.formato.version,
            formato_edicion=self.formato.edicion,
            contexto_tipo='documento',
            contexto_id=str(documento.uuid),
            titulo=titulo,
            fecha='2026-11-10',
            responsable=self.user_trabajador,
            estado=estado,
            aprobador=aprobador,
            respuestas={'descripcion': f'{titulo} ({documento.codigo})'},
        )


class ModeloRegistrosTest(BaseRegistrosTest):
    def test_mismo_codigo_distinto_contexto_no_crea_documentos(self):
        r1 = self._registro(self.doc_activo, titulo='Plan DPR-005')
        r2 = self._registro(self.doc_borrador, titulo='Plan DPR-006')

        self.assertEqual(RegistroFormato.objects.count(), 2)
        self.assertEqual(r1.formato_codigo, r2.formato_codigo)
        self.assertEqual(r1.formato_codigo, 'SGC-FOR-010')
        self.assertNotEqual(r1.uuid, r2.uuid)
        # No se crea un Documento con el código del formato.
        self.assertFalse(Documento.objects.filter(codigo='SGC-FOR-010').exists())

    def test_codigo_visible_es_el_del_formato(self):
        registro = self._registro()
        self.assertEqual(registro.codigo, 'SGC-FOR-010')

    def test_solo_responsable_o_admin_puede_editar(self):
        registro = self._registro()
        self.assertTrue(
            svc.puede_editar_registro(self.user_trabajador, registro),
        )
        self.assertFalse(
            svc.puede_editar_registro(self.user_coordinador, registro),
        )
        self.assertTrue(svc.puede_editar_registro(self.user_admin, registro))


class AprobacionRegistrosTest(BaseRegistrosTest):
    def test_aprobar_con_pin_firma_y_actualiza_estado(self):
        registro = self._registro(
            estado='en_revision', aprobador=self.user_jefe_ti,
        )
        self.crear_pin_firma(self.user_jefe_ti)

        firma = firmar(
            registro, usuario=self.user_jefe_ti, pin='1234', metodo='pin',
            crear=True,
            datos={'aprobada': True, 'observaciones': 'ok'},
        )

        self.assertEqual(firma.estado, 'firmada')
        self.assertEqual(firma.origen_tipo, 'registro_formato')
        self.assertEqual(firma.origen, registro)
        self.assertEqual(len(firma.hash_contenido), 64)
        registro.refresh_from_db()
        self.assertEqual(registro.estado, 'aprobado')
        self.assertEqual(registro.aprobador, self.user_jefe_ti)
        self.assertTrue(registro.hash_integridad)
        self.assertEqual(FirmaElectronica.para(registro).count(), 1)

    def test_sin_pin_no_aprueba(self):
        registro = self._registro(
            estado='en_revision', aprobador=self.user_jefe_ti,
        )
        with self.assertRaises(ErrorFirma):
            firmar(
                registro, usuario=self.user_jefe_ti, pin='', metodo='pin',
                crear=True, datos={'aprobada': True},
            )
        registro.refresh_from_db()
        self.assertEqual(registro.estado, 'en_revision')
        self.assertFalse(FirmaElectronica.para(registro).exists())

    def test_sin_aprobador_solo_admin_puede_aprobar(self):
        registro = self._registro(estado='en_revision', aprobador=None)
        self.assertFalse(
            svc.puede_aprobar_registro(self.user_trabajador, registro),
        )
        self.assertFalse(
            svc.puede_aprobar_registro(self.user_coordinador, registro),
        )
        self.assertTrue(
            svc.puede_aprobar_registro(self.user_admin, registro),
        )

    def test_rechazo_sin_pin_no_firma(self):
        registro = self._registro(
            estado='en_revision', aprobador=self.user_jefe_ti,
        )
        svc.rechazar_registro(registro, self.user_jefe_ti, 'Falta detalle')
        registro.refresh_from_db()
        self.assertEqual(registro.estado, 'rechazado')
        self.assertIn('Falta detalle', registro.observaciones)
        self.assertFalse(FirmaElectronica.para(registro).exists())


class ExportacionRegistrosTest(BaseRegistrosTest):
    def test_exportar_xlsx_genera_archivo_legible(self):
        registro = self._registro()
        contenido = svc.exportar_registro_xlsx(registro)
        wb = openpyxl.load_workbook(io.BytesIO(contenido))
        self.assertEqual(
            wb.active['A1'].value, registro.respuestas['descripcion'],
        )


class VistasRegistrosTest(BaseRegistrosTest):
    def test_lista_y_detalle_visibles_para_responsable(self):
        registro = self._registro()
        self.client.login(username='trab01', password='testpass123')

        lista = self.client.get(reverse('documentos:registros_lista'))
        self.assertEqual(lista.status_code, 200)
        self.assertContains(lista, 'SGC-FOR-010')

        detalle = self.client.get(reverse(
            'documentos:registro_detalle', args=[registro.uuid],
        ))
        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, registro.titulo)

    def test_detalle_documento_muestra_seccion_de_registros(self):
        self._registro()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(reverse(
            'documentos:detalle',
            args=[self.doc_activo.tipo_id, self.doc_activo.uuid],
        ))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Registros de formato')
        self.assertContains(resp, 'SGC-FOR-010')

    def test_aprobar_por_vista_con_pin(self):
        registro = self._registro(
            estado='en_revision', aprobador=self.user_jefe_ti,
        )
        self.crear_pin_firma(self.user_jefe_ti)
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse('documentos:registro_aprobar', args=[registro.uuid]),
            {'pin': '1234', 'observaciones': 'ok'},
        )
        self.assertEqual(resp.status_code, 302)
        registro.refresh_from_db()
        self.assertEqual(registro.estado, 'aprobado')
        self.assertTrue(FirmaElectronica.para(registro).exists())

    def test_enviar_a_revision_asigna_aprobador(self):
        registro = self._registro()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(reverse(
            'documentos:registro_enviar', args=[registro.uuid],
        ))
        self.assertEqual(resp.status_code, 302)
        registro.refresh_from_db()
        self.assertEqual(registro.estado, 'en_revision')
        self.assertEqual(registro.aprobador, self.user_jefe_ti)
