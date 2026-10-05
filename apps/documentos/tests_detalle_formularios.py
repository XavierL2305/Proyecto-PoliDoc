"""Tests del card "Formularios SGC" del detalle: agrupación por operación,
pestañas y acordeones.
"""

from django.test import Client
from django.urls import reverse

from tests_base import BaseDocumentosTest
from documentos.models import Formato, SolicitudCambio


class DetalleFormulariosSGCTest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato = Formato.objects.create(
            codigo='SGC-FOR-001', nombre='Control de cambios', version='1', edicion='1',
        )
        self.doc = self.doc_activo  # propietario jefe_ti (TI)

    def _solicitud(self, operacion, codigo, estado='aprobado'):
        return SolicitudCambio.objects.create(
            codigo=codigo, formato=self.formato, operacion=operacion,
            documento=self.doc, solicitante=self.user_jefe_ti,
            departamento=self.depto_ti, respuestas={}, estado=estado,
        )

    def _get(self):
        self.client.login(username='jefe_ti', password='testpass123')
        return self.client.get(
            reverse('documentos:detalle', args=[self.doc.tipo_id, self.doc.uuid]),
        )

    def test_agrupa_por_operacion_solo_no_vacias(self):
        self._solicitud('creacion', 'SC-C1')
        self._solicitud('creacion', 'SC-C2')
        self._solicitud('eliminacion', 'SC-E1')
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        ops = resp.context['solicitudes_por_operacion']
        self.assertEqual([o['key'] for o in ops], ['creacion', 'eliminacion'])
        self.assertEqual(next(o for o in ops if o['key'] == 'creacion')['count'], 2)

    def test_varias_operaciones_muestran_tabs(self):
        self._solicitud('creacion', 'SC-C1')
        self._solicitud('eliminacion', 'SC-E1')
        resp = self._get()
        self.assertContains(resp, 'Formularios SGC')
        self.assertContains(resp, "switchTab('form-creacion')")
        self.assertContains(resp, "switchTab('form-eliminacion')")
        self.assertContains(resp, 'id="tab-form-creacion"')
        self.assertContains(resp, 'accordion-header')

    def test_una_sola_operacion_sin_tabs(self):
        self._solicitud('creacion', 'SC-C1')
        resp = self._get()
        self.assertEqual(len(resp.context['solicitudes_por_operacion']), 1)
        self.assertNotContains(resp, "switchTab('form-")
        self.assertContains(resp, 'id="tab-form-creacion"')
        self.assertContains(resp, 'accordion-header')

    def test_primera_solicitud_expandida(self):
        self._solicitud('creacion', 'SC-C1')
        self._solicitud('creacion', 'SC-C2')
        resp = self._get()
        # El primer acordeón de la operación arranca con `active`.
        self.assertContains(resp, 'class="accordion active"')
