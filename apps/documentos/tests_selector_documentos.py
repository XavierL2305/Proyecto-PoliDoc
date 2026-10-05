"""Tests del selector compartido de documentos (búsqueda libre por
nombre/código) y del manejo de asociados/pendientes en la planilla.
"""

import json
from types import SimpleNamespace

from django.test import Client
from django.urls import reverse

from tests_base import BaseDocumentosTest
from documentos.models import (
    Documento,
    DocumentoAsociadoPlanilla,
    Formato,
    PlanillaCambios,
    ReferenciaDocumento,
)


class ReferenciablesAPITest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def _doc(self, **extra):
        datos = dict(
            tipo=self.tipo_formulario, nombre='Referenciable', codigo='TI-FOR-900',
            estado='activo', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
        )
        datos.update(extra)
        return Documento.objects.create(**datos)

    def _get(self, **params):
        qs = '&'.join('%s=%s' % (k, v) for k, v in params.items())
        return self.client.get('/documentos/api/referenciables/?' + qs).json()

    def test_default_propio_y_publico(self):
        self._doc(codigo='TI-FOR-901')
        self._doc(codigo='TH-FOR-902', departamento=self.depto_th,
                  clasificacion='publico')
        self._doc(codigo='TH-FOR-903', departamento=self.depto_th,
                  propietario=self.user_trabajador_otro)
        data = self._get(buscar='Referenciable')
        codigos = {d['codigo'] for d in data['documentos']}
        self.assertIn('TI-FOR-901', codigos)
        self.assertIn('TH-FOR-902', codigos)
        self.assertNotIn('TH-FOR-903', codigos)

    def test_departamentos_incluye_afectados(self):
        self._doc(codigo='TH-FOR-904', departamento=self.depto_th,
                  propietario=self.user_trabajador_otro)
        data = self._get(departamentos=self.depto_th.id, buscar='TH-FOR-904')
        codigos = {d['codigo'] for d in data['documentos']}
        self.assertIn('TH-FOR-904', codigos)

    def test_incluir_borradores(self):
        self._doc(codigo='TI-FOR-905', estado='borrador')
        self.assertEqual(self._get(buscar='TI-FOR-905')['total'], 0)
        self.assertEqual(
            self._get(buscar='TI-FOR-905', incluir_borradores='1')['total'], 1,
        )

    def test_exclude_id(self):
        doc = self._doc(codigo='TI-FOR-906')
        self.assertEqual(self._get(buscar='TI-FOR-906', exclude_id=doc.id)['total'], 0)


class AsociadosPendientesTest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.formato = Formato.objects.create(
            codigo='SGC-FOR-001', nombre='Cambios', version='1', edicion='1',
        )
        self.doc = self.doc_activo

    def _planilla(self):
        return PlanillaCambios.objects.create(
            codigo='PC-SEL-1', documento=self.doc, tipo_cambio='modificacion',
            justificacion='x', responsable=self.user_jefe_ti, version_destino='2',
            estado='borrador',
        )

    def test_pendiente_se_guarda(self):
        from documentos.views import _procesar_asociados_planilla
        planilla = self._planilla()
        request = SimpleNamespace(POST={
            'documentos_asociados_json': json.dumps([
                {'codigo': 'SGC-FOR-999', 'pendiente': True},
            ]),
        })
        _procesar_asociados_planilla(planilla, request, True, self.doc, self.user_jefe_ti)
        planilla.refresh_from_db()
        self.assertEqual(planilla.referencias_pendientes, ['SGC-FOR-999'])

    def test_asociado_existente_no_duplica(self):
        from documentos.views import _procesar_asociados_planilla
        planilla = self._planilla()
        request = SimpleNamespace(POST={
            'documentos_asociados_json': json.dumps([
                {'uuid': str(self.doc_activo.uuid)},
                {'uuid': str(self.doc_activo.uuid)},
            ]),
        })
        _procesar_asociados_planilla(planilla, request, True, self.doc, self.user_jefe_ti)
        self.assertEqual(
            DocumentoAsociadoPlanilla.objects.filter(planilla=planilla).count(), 1,
        )

    def test_aplicar_materializa_pendiente(self):
        from documentos.services.planillas import aplicar_planilla_al_documento
        planilla = self._planilla()
        planilla.referencias_pendientes = ['SGC-FOR-998']
        planilla.save(update_fields=['referencias_pendientes'])
        aplicar_planilla_al_documento(planilla, usuario=self.user_jefe_ti)
        self.assertTrue(ReferenciaDocumento.objects.filter(
            documento=self.doc, codigo_destino='SGC-FOR-998',
        ).exists())


class SelectorRenderTest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_formulario_incluye_selector(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(reverse('documentos:crear_documento', args=['formulario']))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'documentos-selector')
        self.assertContains(resp, 'js-ds-search')
        self.assertContains(resp, 'documentos_relacionados')

    def test_planilla_incluye_selector(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(reverse('documentos:crear_planilla'), {
            'tipo': self.doc_activo.tipo_id, 'doc_uuid': self.doc_activo.uuid,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'documentos-selector')
        self.assertContains(resp, 'documentos_asociados_json')
