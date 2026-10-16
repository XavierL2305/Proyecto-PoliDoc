"""Tests de la interfaz al llenar los formularios.

Regresión de dos defectos de UX:

1. La descripción se pedía DOS veces (campo del HTML + campo del Formato
   SGC-FOR-001 / SGC-FOR-005). Ambos son el mismo dato: se conserva el del HTML
   y el del formato se autocompleta en el servidor (`origen='descripcion_solicitud'`).
2. Los campos automáticos del Formato (badge "Auto") se mostraban al llenar,
   forzando un scroll enorme. Ahora solo se muestran en modo solo lectura.
"""

import json
import shutil
import tempfile
from io import StringIO

from django.core.management import call_command
from django.test import Client
from django.test.utils import override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest

from documentos.models import (
    Documento, Formato, PlanillaCambios, SolicitudCambio,
    valores_auto_solicitud,
)


class BaseFormatoEdicionTest(BaseDocumentosTest):
    """Comunes: formatos SGC y helpers de relleno de formulario."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        media_tmp = tempfile.mkdtemp(prefix='polidoc_test_media_')
        cls.addClassCleanup(shutil.rmtree, media_tmp, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media_tmp):
            for cmd in ('poblar_formato_sgc_for_001', 'poblar_formato_sgc_for_005'):
                call_command(cmd, stdout=StringIO(), stderr=StringIO())
        cls.for001 = Formato.objects.get(codigo='SGC-FOR-001')
        cls.for005 = Formato.objects.get(codigo='SGC-FOR-005')

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _post_crear_documento(self, **extra):
        payload = {
            'nombre': 'Procedimiento de Prueba Creación',
            'descripcion': 'Descripción escrita en el HTML',
            'clasificacion': 'interno',
            'periodo_vigencia': '',
            'periodo_retencion': '',
            'secuencia': '009',
            'accion': 'borrador',
        }
        payload.update(extra)
        return self.client.post(
            reverse('documentos:crear_documento',
                    args=['procedimientos_generales']),
            payload,
        )

    def _url_planilla(self):
        return (
            reverse('documentos:crear_planilla')
            + f'?tipo={self.doc_activo.tipo_id}&doc_uuid={self.doc_activo.uuid}'
        )

    def _post_planilla(self, **extra):
        payload = {
            'nombre_propuesto': 'Nombre propuesto',
            'descripcion_propuesta': 'Descripción propuesta en el HTML',
            'clasificacion_propuesta': '',
            'departamentos_afectados': str(self.depto_ti.id),
            'accion': 'borrador',
            'campo_origen_cambio': 'Auditoría',
            'campo_elemento_sgc': 'Procesos',
            'campo_proposito_cambio': 'Propósito del cambio de prueba',
            'campo_consecuencias_potenciales': 'Consecuencias potenciales de prueba',
            'campo_plan_accion': json.dumps([
                {'actividad': 'Actividad 1', 'resp_ejecucion': 'A'},
            ]),
        }
        payload.update(extra)
        return self.client.post(self._url_planilla(), payload)

    def _sol_cambio_de(self, documento, operacion='creacion'):
        return (SolicitudCambio.objects
                .filter(documento=documento, operacion=operacion)
                .order_by('-id').first())


# =============================================================================
# 1. La descripción se pide una sola vez (se autocompleta en el formato)
# =============================================================================
class DescripcionUnicaTest(BaseFormatoEdicionTest):
    """El valor del HTML es la única fuente del campo del Formato SGC."""

    def test_formato_reclasificado_como_automatico(self):
        """FOR-001.descripcion y FOR-005.descripcion_cambio ya no son 'usuario'."""
        self.assertEqual(
            self.for001.get_todos_los_campos().get(clave='descripcion').origen,
            'descripcion_solicitud',
        )
        self.assertEqual(
            self.for005.get_todos_los_campos().get(clave='descripcion_cambio').origen,
            'descripcion_solicitud',
        )

    def test_crear_documento_inyecta_descripcion(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post_crear_documento()
        self.assertEqual(resp.status_code, 302)

        documento = Documento.objects.get(codigo='TI-PRG-009')
        self.assertEqual(documento.descripcion, 'Descripción escrita en el HTML')

        sol = self._sol_cambio_de(documento)
        self.assertIsNotNone(sol)
        self.assertEqual(
            sol.respuestas.get('descripcion'), 'Descripción escrita en el HTML'
        )
        # El valor no lo pisa el mapeo automático de los otros orígenes.
        self.assertNotIn('descripcion_solicitud', valores_auto_solicitud(sol))

    def test_editar_borrador_actualiza_descripcion(self):
        """El borrador existente toma la descripción nueva del HTML."""
        self.client.login(username='jefe_ti', password='testpass123')
        self._post_crear_documento()
        doc = Documento.objects.get(codigo='TI-PRG-009')

        resp = self.client.post(
            reverse('documentos:editar_borrador',
                    args=['procedimientos_generales', doc.uuid]),
            {
                'nombre': doc.nombre,
                'descripcion': 'Descripción revisada',
                'clasificacion': 'interno',
                'secuencia': '009',
                'accion': 'borrador',
            },
        )
        self.assertEqual(resp.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.descripcion, 'Descripción revisada')
        sol = self._sol_cambio_de(doc)
        self.assertEqual(sol.respuestas.get('descripcion'), 'Descripción revisada')

    def test_eliminar_documento_inyecta_descripcion(self):
        self.client.login(username='jefe_ti', password='testpass123')
        self.doc_activo.descripcion = 'Descripción del documento a inhabilitar'
        self.doc_activo.save(update_fields=['descripcion'])

        resp = self.client.post(
            reverse('documentos:eliminar',
                    args=['procedimientos_generales', self.doc_activo.uuid]),
        )
        self.assertEqual(resp.status_code, 302)

        sol = (SolicitudCambio.objects
               .filter(documento=self.doc_activo, operacion='eliminacion')
               .order_by('-id').first())
        self.assertIsNotNone(sol)
        self.assertEqual(
            sol.respuestas.get('descripcion'),
            'Descripción del documento a inhabilitar',
        )

    def test_crear_planilla_inyecta_descripcion(self):
        """descripcion_propuesta es la fuente de descripcion_cambio y justificación."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post_planilla()
        self.assertEqual(resp.status_code, 302)

        planilla = (PlanillaCambios.objects
                    .filter(documento=self.doc_activo)
                    .order_by('-id').first())
        self.assertIsNotNone(planilla)
        self.assertEqual(planilla.justificacion, 'Descripción propuesta en el HTML')
        self.assertEqual(
            planilla.solicitud_cambio.respuestas.get('descripcion_cambio'),
            'Descripción propuesta en el HTML',
        )


# =============================================================================
# 2. Al llenar el formulario no se muestran los campos automáticos
# =============================================================================
class CamposAutoOcultosTest(BaseFormatoEdicionTest):

    def test_creacion_de_documento_no_muestra_formato(self):
        """SGC-FOR-001 es 100% automático: su tarjeta desaparece del formulario."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            reverse('documentos:crear_documento', args=['procedimientos_generales']),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'SGC-FOR-001')
        self.assertNotContains(resp, 'name="campo_descripcion"')
        # El campo del HTML sigue visible (única descripción que se pide).
        self.assertContains(resp, 'name="descripcion"')

    def test_editar_borrador_no_muestra_formato(self):
        self.client.login(username='jefe_ti', password='testpass123')
        self._post_crear_documento()
        doc = Documento.objects.get(codigo='TI-PRG-009')
        resp = self.client.get(
            reverse('documentos:editar_borrador',
                    args=['procedimientos_generales', doc.uuid]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'SGC-FOR-001')

    def test_confirmar_eliminar_no_muestra_formato(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            reverse('documentos:eliminar',
                    args=['procedimientos_generales', self.doc_activo.uuid]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'SGC-FOR-001')

    def test_formulario_planilla_oculta_auto_y_descripcion_redundante(self):
        """Solo pide la descripción del HTML; los campos auto no aparecen."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self._url_planilla())
        self.assertEqual(resp.status_code, 200)

        # Descripción única: la del HTML, no la del formato.
        self.assertContains(resp, 'name="descripcion_propuesta"')
        self.assertNotContains(resp, 'name="campo_descripcion_cambio"')

        # Los campos auto del formato no se muestran.
        self.assertNotContains(resp, 'name="campo_num_solicitud"')
        self.assertNotContains(resp, 'name="campo_fecha_solicitud"')
        self.assertNotContains(resp, 'name="campo_version"')
        self.assertNotContains(resp, 'name="campo_evaluado_por_nombre"')
        self.assertNotContains(resp, 'name="campo_resolucion"')

        # Los campos del usuario sí se muestran.
        self.assertContains(resp, 'name="campo_origen_cambio"')
        self.assertContains(resp, 'name="campo_elemento_sgc"')
        self.assertContains(resp, 'name="campo_proposito_cambio"')
        self.assertContains(resp, 'name="campo_plan_accion"')

        # La tarjeta "Información Automática" se oculta.
        self.assertNotContains(resp, 'Información Automática')

    def test_formulario_planilla_mantiene_campo_nombre_solicitante(self):
        """Sin nombre en AD el usuario debe completar su nombre: sigue visible."""
        self.user_jefe_ti.first_name = ''
        self.user_jefe_ti.last_name = ''
        self.user_jefe_ti.save(update_fields=['first_name', 'last_name'])

        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self._url_planilla())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Escriba su nombre completo')
        self.assertContains(resp, 'name="campo_nombre_solicitante"')

    def test_formulario_planilla_desactiva_nombre_solicitante_con_nombre(self):
        """Con nombre completo el campo se oculta igual que el resto de los auto."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self._url_planilla())
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Escriba su nombre completo')


# =============================================================================
# 3. En solo lectura los campos automáticos SIGUEN viéndose
# =============================================================================
class SoloLecturaConservaAutoTest(BaseFormatoEdicionTest):

    def test_detalle_documento_muestra_campos_auto(self):
        self.client.login(username='jefe_ti', password='testpass123')
        self._post_crear_documento()
        doc = Documento.objects.get(codigo='TI-PRG-009')
        resp = self.client.get(
            reverse('documentos:detalle',
                    args=['procedimientos_generales', doc.uuid]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descripción escrita en el HTML')

    def test_detalle_planilla_muestra_campos_auto(self):
        self.client.login(username='jefe_ti', password='testpass123')
        self._post_planilla()
        planilla = (PlanillaCambios.objects
                    .filter(documento=self.doc_activo)
                    .order_by('-id').first())
        resp = self.client.get(
            reverse('documentos:detalle_planilla', args=[planilla.uuid]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'SGC-FOR-005')
        self.assertContains(resp, 'Descripción propuesta en el HTML')


# =============================================================================
# 4. Sin regresión de la validación del formato
# =============================================================================
class ValidacionFormatoTest(BaseFormatoEdicionTest):

    def test_crear_documento_con_formato_completo_valida(self):
        """Con los campos auto ocultos el formato deja de exigirlos al usuario."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post_crear_documento(
            **{'campo_descripcion': '', 'campo_accion': ''},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Documento.objects.filter(codigo='TI-PRG-009').exists())

    def test_crear_planilla_exige_campos_de_usuario_del_formato(self):
        """Los campos de origen 'usuario' del formato siguen siendo obligatorios."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(self._url_planilla(), {
            'nombre_propuesto': 'Nombre propuesto',
            'descripcion_propuesta': 'Descripción propuesta en el HTML',
            'clasificacion_propuesta': '',
            'departamentos_afectados': str(self.depto_ti.id),
            'accion': 'enviar',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            PlanillaCambios.objects.filter(documento=self.doc_activo).exists()
        )
        mensajes = [m.message for m in resp.context['messages']]
        self.assertTrue(any('obligatorio' in str(m) for m in mensajes))
