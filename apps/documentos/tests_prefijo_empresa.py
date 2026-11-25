"""Tests para codificación y secuencias por empresa (PTCA, PTH, FP, FS)."""
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest

from documentos.forms import TipoDocumentoForm
from documentos.models import Documento, TipoDocumento
from documentos.services.for_006 import empresa_documento

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_empresa_')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class PrefijoEmpresaTest(BaseDocumentosTest):
    """Numeración y alta de documentos/plantillas con `usa_prefijo_empresa=True`."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tipo_dpr, _ = TipoDocumento.objects.get_or_create(
            key='descripcion_procesos',
            defaults={
                'nombre': 'Descripción de Procesos',
                'codigo': 'DPR',
                'activo': True,
                'solo_jefes': True,
                'sin_prefijo_depto': False,
                'usa_prefijo_empresa': True,
            },
        )
        if not cls.tipo_dpr.usa_prefijo_empresa or cls.tipo_dpr.sin_prefijo_depto:
            cls.tipo_dpr.usa_prefijo_empresa = True
            cls.tipo_dpr.sin_prefijo_depto = False
            cls.tipo_dpr.save()

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_secuencias_independientes_por_empresa(self):
        """Cada empresa inicia y avanza su propia secuencia DPR."""
        self.assertTrue(Documento.usa_prefijo_empresa('descripcion_procesos'))
        self.assertEqual(
            Documento.generar_codigo_secuencial(
                self.depto_ti, 'descripcion_procesos', empresa='PTCA',
            ),
            'PTCA-DPR-001',
        )

        Documento.objects.create(
            tipo=self.tipo_dpr,
            nombre='Proceso PTCA 1',
            codigo='PTCA-DPR-001',
            empresa='PTCA',
            estado='borrador',
            propietario=self.user_jefe_ti,
            departamento=self.depto_ti,
        )

        self.assertEqual(
            Documento.generar_codigo_secuencial(
                self.depto_th, 'descripcion_procesos', empresa='PTCA',
            ),
            'PTCA-DPR-002',
        )
        self.assertEqual(
            Documento.generar_codigo_secuencial(
                self.depto_ti, 'descripcion_procesos', empresa='PTH',
            ),
            'PTH-DPR-001',
        )
        self.assertEqual(
            Documento.generar_codigo_secuencial(
                self.depto_ti, 'descripcion_procesos', empresa='FP',
            ),
            'FP-DPR-001',
        )
        self.assertEqual(
            Documento.generar_codigo_secuencial(
                self.depto_ti, 'descripcion_procesos', empresa='FS',
            ),
            'FS-DPR-001',
        )

    def test_reserva_atomica_plantilla_por_empresa(self):
        """La reserva atómica de plantillas separa la serie por empresa."""
        cod_ptca_1 = Documento.reservar_codigo_secuencial(
            self.depto_ti, 'descripcion_procesos',
            es_plantilla=True, empresa='PTCA',
        )
        cod_pth_1 = Documento.reservar_codigo_secuencial(
            self.depto_ti, 'descripcion_procesos',
            es_plantilla=True, empresa='PTH',
        )
        cod_ptca_2 = Documento.reservar_codigo_secuencial(
            self.depto_th, 'descripcion_procesos',
            es_plantilla=True, empresa='PTCA',
        )
        self.assertEqual(cod_ptca_1, 'PTCA-DPR-PLT-001')
        self.assertEqual(cod_pth_1, 'PTH-DPR-PLT-001')
        self.assertEqual(cod_ptca_2, 'PTCA-DPR-PLT-002')

    def test_crear_documento_con_empresa_valida_preserva_depto(self):
        """POST en crear_documento genera PTH-DPR-005 y conserva departamento TI."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse('documentos:crear_documento', args=['descripcion_procesos']),
            {
                'nombre': 'DPR Hospitalización',
                'descripcion': 'Descripción de proceso para PTH',
                'clasificacion': 'interno',
                'empresa': 'PTH',
                'secuencia': '005',
                'accion': 'borrador',
            },
        )
        self.assertEqual(resp.status_code, 302)
        doc = Documento.objects.get(codigo='PTH-DPR-005')
        self.assertEqual(doc.empresa, 'PTH')
        self.assertEqual(doc.departamento, self.depto_ti)

    def test_crear_documento_rechaza_empresa_invalida(self):
        """POST con empresa fuera del catálogo rechaza y no crea documento."""
        self.client.login(username='jefe_ti', password='testpass123')
        antes = Documento.objects.count()
        resp = self.client.post(
            reverse('documentos:crear_documento', args=['descripcion_procesos']),
            {
                'nombre': 'DPR Inválido',
                'descripcion': 'No debe crearse',
                'clasificacion': 'interno',
                'empresa': 'OTRA',
                'secuencia': '001',
                'accion': 'borrador',
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Documento.objects.count(), antes)
        self.assertContains(resp, 'empresa válida')

    def test_crear_plantilla_con_empresa(self):
        """POST en crear_plantilla con empresa=FP genera FP-DPR-PLT-001."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse('documentos:crear_plantilla', args=['descripcion_procesos']),
            {
                'nombre': 'Plantilla DPR Farmacia',
                'descripcion': 'Formato base',
                'clasificacion': 'interno',
                'empresa': 'FP',
                'archivo': SimpleUploadedFile('dpr.pdf', b'%PDF-1.4 fake'),
            },
        )
        self.assertEqual(resp.status_code, 302)
        doc = Documento.objects.get(codigo='FP-DPR-PLT-001')
        self.assertTrue(doc.es_plantilla)
        self.assertEqual(doc.empresa, 'FP')
        self.assertEqual(doc.departamento, self.depto_ti)

    def test_api_siguiente_secuencia_por_empresa(self):
        """La API devuelve la siguiente secuencia por empresa y rechaza inválidas."""
        Documento.objects.create(
            tipo=self.tipo_dpr,
            nombre='DPR FS 1',
            codigo='FS-DPR-001',
            empresa='FS',
            estado='borrador',
            propietario=self.user_jefe_ti,
            departamento=self.depto_ti,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        url = reverse('documentos:siguiente_secuencia')

        resp_fs = self.client.get(url, {
            'departamento_id': self.depto_ti.id,
            'tipo_codigo': 'DPR',
            'empresa': 'FS',
        })
        self.assertEqual(resp_fs.status_code, 200)
        self.assertEqual(resp_fs.json(), {'secuencia': '002', 'codigo': 'FS-DPR-002'})

        resp_pth = self.client.get(url, {
            'departamento_id': self.depto_ti.id,
            'tipo_codigo': 'DPR',
            'empresa': 'PTH',
        })
        self.assertEqual(resp_pth.status_code, 200)
        self.assertEqual(resp_pth.json(), {'secuencia': '001', 'codigo': 'PTH-DPR-001'})

        resp_bad = self.client.get(url, {
            'departamento_id': self.depto_ti.id,
            'tipo_codigo': 'DPR',
            'empresa': 'OTRA',
        })
        self.assertEqual(resp_bad.status_code, 400)

    def test_tipo_documento_form_rechaza_ambos_modos_prefijo(self):
        """TipoDocumentoForm y TipoDocumento.clean() impiden activar ambos flags."""
        form = TipoDocumentoForm(data={
            'codigo': 'TST',
            'nombre': 'Tipo Mixto',
            'activo': True,
            'sin_prefijo_depto': True,
            'usa_prefijo_empresa': True,
            'color': 'primary',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('usa_prefijo_empresa', form.errors)

        tipo = TipoDocumento(
            key='mixto', nombre='Mixto', codigo='MXT',
            sin_prefijo_depto=True, usa_prefijo_empresa=True,
        )
        with self.assertRaises(ValidationError):
            tipo.clean()

    def test_for_006_empresa_documento_resuelve_todas(self):
        """empresa_documento resuelve PTCA, PTH, FP y FS para Lista Maestra."""
        esperados = {
            'PTCA': 'PTCA',
            'PTH': 'Policlínica Táchira Hospitalización',
            'FP': 'Farmacia',
            'FS': 'Fuente de Soda',
        }
        for idx, (emp_cod, esperado) in enumerate(esperados.items(), start=1):
            doc = Documento(
                tipo=self.tipo_dpr,
                nombre=f'Doc {emp_cod}',
                codigo=f'{emp_cod}-DPR-{idx:03d}',
                empresa=emp_cod,
                departamento=self.depto_ti,
                propietario=self.user_jefe_ti,
            )
            self.assertEqual(empresa_documento(doc), esperado)
