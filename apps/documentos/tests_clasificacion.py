"""Tests de la clasificación propuesta en la Planilla de Cambios.

Cubren el campo del formulario (precargado con la clasificación actual), la
aplicación al documento al aprobar (helper compartido por la vista web y la
API) y la auditoría ISO (registro explícito ``aprobacion_planilla`` + edición
del documento con el cambio de clasificación).
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests_base import MONGO_PATCH_TARGET, BaseDocumentosTest
from documentos.forms import PlanillaCambiosForm
from documentos.models import AprobacionPlanilla, PlanillaCambios
from documentos.services.planillas import aplicar_planilla_al_documento


def _fake_mongo():
    """BD Mongo falsa que permite inspeccionar los registros insertados.

    ``get_mongo_client()`` devuelve la base de datos: se accede a las
    colecciones por atributo (``db.audit_logs``) y por índice
    (``db['planillas_cambios']``).
    """
    store = {}
    db = MagicMock()
    db.__getitem__.side_effect = lambda name: store.setdefault(name, MagicMock())
    db.audit_logs = store.setdefault('audit_logs', MagicMock())
    return db, store


def _registros(store, coleccion):
    return [llamada[0][0] for llamada in store[coleccion].insert_one.call_args_list]


class PlanillaClasificacionFormTest(BaseDocumentosTest):

    def test_form_incluye_la_clasificacion_con_opcion_sin_cambios(self):
        form = PlanillaCambiosForm(
            tipo_documento=self.tipo_procedimiento, documento=self.doc_activo,
        )
        self.assertIn('clasificacion_propuesta', form.fields)
        self.assertFalse(form.fields['clasificacion_propuesta'].required)
        valores = [valor for valor, _ in form.fields['clasificacion_propuesta'].choices]
        self.assertEqual(valores[0], '')
        self.assertIn('publico', valores)

    def test_crear_planilla_precarga_la_clasificacion_actual(self):
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(reverse('documentos:crear_planilla'), {
            'tipo': 'procedimientos_generales', 'doc_uuid': self.doc_activo.uuid,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.context['form'].initial['clasificacion_propuesta'], 'interno',
        )


class AplicarPlanillaHelperTest(BaseDocumentosTest):

    def _planilla(self, **extra):
        datos = {
            'codigo': 'PC-CL-HELPER',
            'documento': self.doc_activo,
            'tipo_cambio': 'modificacion',
            'justificacion': 'Prueba',
            'responsable': self.user_jefe_ti,
            'version_destino': '2.0',
            'estado': 'en_revision',
        }
        datos.update(extra)
        return PlanillaCambios.objects.create(**datos)

    def test_aplica_la_clasificacion_y_sube_version(self):
        planilla = self._planilla(
            clasificacion_propuesta='publico',
            clasificacion_actual_snapshot='interno',
        )

        doc, cambios = aplicar_planilla_al_documento(planilla, usuario=self.user_jefe_ti)

        doc.refresh_from_db()
        self.assertEqual(doc.clasificacion, 'publico')
        self.assertEqual(doc.version_actual, '2')
        self.assertEqual(cambios['clasificacion_anterior'], 'interno')
        self.assertEqual(cambios['clasificacion_nueva'], 'publico')

    def test_sin_propuesta_no_cambia_la_clasificacion(self):
        planilla = self._planilla()

        doc, cambios = aplicar_planilla_al_documento(planilla, usuario=self.user_jefe_ti)

        doc.refresh_from_db()
        self.assertEqual(doc.clasificacion, 'interno')
        self.assertEqual(cambios, {})

    def test_planilla_sin_documento_no_hace_nada(self):
        planilla = PlanillaCambios.objects.create(
            codigo='PC-CL-SIN-DOC', tipo_cambio='modificacion',
            justificacion='x', responsable=self.user_jefe_ti,
            version_destino='1.0', estado='en_revision',
        )
        doc, cambios = aplicar_planilla_al_documento(planilla, usuario=self.user_jefe_ti)
        self.assertIsNone(doc)
        self.assertEqual(cambios, {})

    def test_no_reinicia_la_fecha_de_vigencia(self):
        """Aplicar una modificación sube versión pero no mueve la vigencia."""
        vigencia_anterior = timezone.now() - timedelta(days=40)
        self.doc_activo.fecha_vigencia = vigencia_anterior
        self.doc_activo.save(update_fields=['fecha_vigencia'])
        planilla = self._planilla(clasificacion_propuesta='publico')

        aplicar_planilla_al_documento(planilla, usuario=self.user_jefe_ti)

        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.fecha_vigencia, vigencia_anterior)
        self.assertEqual(self.doc_activo.estado, 'activo')


class PlanillaClasificacionAprobacionTest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.planilla = PlanillaCambios.objects.create(
            codigo='PC-CL-WEB-1', documento=self.doc_activo,
            tipo_cambio='modificacion', justificacion='Cambio de clasificación',
            responsable=self.user_jefe_ti, version_destino='2.0',
            estado='en_revision', clasificacion_propuesta='publico',
            clasificacion_actual_snapshot='interno',
        )
        self.aprobacion = AprobacionPlanilla.objects.create(
            planilla=self.planilla, aprobador=self.user_jefe_gg, estado='pendiente',
        )

    def test_aprobar_web_aplica_la_clasificacion(self):
        self.client.login(username='jefe_gg', password='testpass123')
        url = reverse('documentos:aprobar_planilla', args=[self.planilla.uuid])
        resp = self.client.post(url, {'accion': 'aprobar', 'observaciones': 'ok'})

        self.assertEqual(resp.status_code, 302)
        self.planilla.refresh_from_db()
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.planilla.estado, 'aprobado')
        self.assertEqual(self.doc_activo.clasificacion, 'publico')

    def test_rechazo_web_no_aplica_la_clasificacion(self):
        self.client.login(username='jefe_gg', password='testpass123')
        url = reverse('documentos:aprobar_planilla', args=[self.planilla.uuid])
        self.client.post(url, {'accion': 'rechazar', 'observaciones': 'No procede'})

        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.clasificacion, 'interno')


class PlanillaClasificacionAPITest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.planilla = PlanillaCambios.objects.create(
            codigo='PC-CL-API-1', documento=self.doc_activo,
            tipo_cambio='modificacion', justificacion='Cambio',
            responsable=self.user_jefe_ti, version_destino='2.0',
            estado='en_revision', clasificacion_propuesta='restringido',
            clasificacion_actual_snapshot='interno',
        )
        self.aprobacion = AprobacionPlanilla.objects.create(
            planilla=self.planilla, aprobador=self.user_jefe_ti, estado='pendiente',
        )

    def test_aprobar_api_aplica_la_clasificacion(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/planilla/{self.planilla.uuid}/aprobar/',
            data=json.dumps({'observaciones': 'ok'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.clasificacion, 'restringido')


class PlanillaClasificacionAuditoriaTest(BaseDocumentosTest):

    def test_creacion_de_planilla_registra_la_propuesta_en_mongo(self):
        client, store = _fake_mongo()
        with patch(MONGO_PATCH_TARGET, return_value=client):
            PlanillaCambios.objects.create(
                codigo='PC-CL-AUD-1', documento=self.doc_activo,
                tipo_cambio='modificacion', justificacion='x',
                responsable=self.user_jefe_ti, version_destino='2.0',
                estado='en_revision', clasificacion_propuesta='publico',
                clasificacion_actual_snapshot='interno',
            )

        registros = _registros(store, 'planillas_cambios')
        self.assertEqual(registros[-1]['clasificacion_propuesta'], 'publico')
        self.assertEqual(registros[-1]['clasificacion_actual_snapshot'], 'interno')

    def test_aprobacion_registra_log_iso_explicito_y_edicion(self):
        planilla = PlanillaCambios.objects.create(
            codigo='PC-CL-AUD-2', documento=self.doc_activo,
            tipo_cambio='modificacion', justificacion='x',
            responsable=self.user_jefe_ti, version_destino='2.0',
            estado='en_revision', clasificacion_propuesta='publico',
            clasificacion_actual_snapshot='interno',
        )
        AprobacionPlanilla.objects.create(
            planilla=planilla, aprobador=self.user_jefe_gg, estado='pendiente',
        )
        client, store = _fake_mongo()
        self.client = Client()
        self.client.login(username='jefe_gg', password='testpass123')
        url = reverse('documentos:aprobar_planilla', args=[planilla.uuid])
        with patch(MONGO_PATCH_TARGET, return_value=client):
            self.client.post(url, {'accion': 'aprobar', 'observaciones': 'ok'})

        registros = _registros(store, 'audit_logs')
        aprobaciones = [
            r for r in registros if r['accion']['tipo'] == 'aprobacion_planilla'
        ]
        self.assertEqual(len(aprobaciones), 1)
        self.assertEqual(aprobaciones[0]['detalles']['planilla_codigo'], 'PC-CL-AUD-2')
        self.assertEqual(aprobaciones[0]['detalles']['clasificacion_anterior'], 'interno')
        self.assertEqual(aprobaciones[0]['detalles']['clasificacion_nueva'], 'publico')

        ediciones = [
            r for r in registros
            if r['accion']['tipo'] == 'edicion'
            and 'clasificacion' in r['detalles'].get('campos_modificados', [])
        ]
        self.assertTrue(ediciones, 'falta el asiento de edición con clasificación')
        self.assertEqual(
            ediciones[-1]['detalles']['valores_anteriores']['clasificacion'], 'interno',
        )
        self.assertEqual(
            ediciones[-1]['detalles']['valores_nuevos']['clasificacion'], 'publico',
        )
