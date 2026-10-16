"""Tests del flujo de aprobación de planillas de cambios.

Regresión del defecto que ocultó ``PC-20261013-191940``: al aprobar el
evaluador, el paso del autorizador se promovía a ``pendiente`` pero **no se
le notificaba**, no aparecía en la bandeja y nunca se escalaba, de modo que la
planilla quedaba invisible para quien tenía que autorizarla.

Ver ``documentos/services/aprobaciones.py``.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from autenticacion.models import Notificacion
from documentos.models import AprobacionPlanilla, PlanillaCambios


class BasePlanillaAprobacionTest(BaseDocumentosTest):
    """Planilla ``en_revision`` con cadena de DOS pasos (responsable no jefe).

    Pasos: evaluador = jefe_ti (pendiente), autorizador = jefe_gg (esperando).
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            cls.planilla = PlanillaCambios.objects.create(
                codigo='PC-TEST-0001', documento=cls.doc_en_revision,
                tipo_cambio='modificacion', justificacion='Prueba de flujo',
                responsable=cls.user_trabajador, version_destino='2',
                estado='en_revision',
            )
            from documentos.views import crear_cadena_aprobacion_planilla
            cls.paso_eval = crear_cadena_aprobacion_planilla(
                planilla=cls.planilla, usuario=cls.user_trabajador,
                documento=cls.doc_en_revision,
            )

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _pasos(self):
        return AprobacionPlanilla.objects.filter(planilla=self.planilla)

    def _autorizador(self):
        return self._pasos().get(rol_aprobador='autorizador')

    def _evaluador(self):
        return self._pasos().get(rol_aprobador='evaluador')

    def _aprobar_evaluador(self):
        """Aprueba el paso del evaluador (promueve al autorizador)."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse('documentos:aprobar_planilla', args=[self.planilla.uuid]),
            {'accion': 'aprobar', 'observaciones': 'evaluado'},
        )
        self.assertEqual(resp.status_code, 302)
        return resp


# =============================================================================
# B1: promoción del autorizador + notificación (web y API)
# =============================================================================
class PromocionAutorizadorTest(BasePlanillaAprobacionTest):

    def test_autorizador_queda_pendiente_con_fecha_activacion(self):
        self._aprobar_evaluador()
        autorizador = self._autorizador()
        self.assertEqual(autorizador.estado, 'pendiente')
        self.assertIsNotNone(autorizador.fecha_activacion)

    def test_evaluador_queda_aprobado(self):
        self._aprobar_evaluador()
        self.assertEqual(self._evaluador().estado, 'aprobada')

    def test_autorizador_recibe_notificacion(self):
        """Regresión: el autorizador promovido era invisible para todos."""
        Notificacion.objects.all().delete()
        self._aprobar_evaluador()
        notifs = Notificacion.objects.filter(
            usuario=self.user_jefe_gg, tipo='solicitud_aprobacion',
        )
        self.assertTrue(notifs.exists())
        self.assertIn('/aprobar/', notifs.first().url_accion)

    def test_responsable_sigue_recibiendo_notificacion(self):
        Notificacion.objects.all().delete()
        self._aprobar_evaluador()
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_trabajador, tipo='aprobacion_documento',
        ).exists())

    def test_no_duplica_notificaciones_del_autorizador(self):
        Notificacion.objects.all().delete()
        self._aprobar_evaluador()
        self.assertEqual(
            Notificacion.objects.filter(
                usuario=self.user_jefe_gg, tipo='solicitud_aprobacion',
            ).count(), 1,
        )

    def test_mismo_comportamiento_por_api(self):
        """La API debe hacer exactamente lo que hace la vista."""
        Notificacion.objects.all().delete()
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/planilla/{self.planilla.uuid}/aprobar/',
            data=json.dumps({'observaciones': 'evaluado'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._autorizador().estado, 'pendiente')
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_jefe_gg, tipo='solicitud_aprobacion',
        ).exists())
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_trabajador, tipo='aprobacion_documento',
        ).exists())

    def test_planilla_sigue_en_revision_hasta_autorizar(self):
        self._aprobar_evaluador()
        self.planilla.refresh_from_db()
        self.assertEqual(self.planilla.estado, 'en_revision')


# =============================================================================
# B2: la bandeja lista las planillas pendientes del usuario
# =============================================================================
class BandejaPlanillasTest(BasePlanillaAprobacionTest):

    def _bandeja(self, username, **params):
        self.client.login(username=username, password='testpass123')
        return self.client.get(reverse('documentos:bandeja_aprobaciones'), params)

    def test_evaluador_ve_su_planilla_pendiente(self):
        resp = self._bandeja('jefe_ti')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'PC-TEST-0001')
        self.assertContains(resp, 'Revisar y aprobar')

    def test_autorizador_ve_su_planilla_pendiente_tras_evaluacion(self):
        self._aprobar_evaluador()
        resp = self._bandeja('jefe_gg')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'PC-TEST-0001')
        self.assertContains(resp, 'Revisar y aprobar')

    def test_evaluador_que_ya_aprobo_no_la_ve_en_pendientes(self):
        self._aprobar_evaluador()
        resp = self._bandeja('jefe_ti')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'PC-TEST-0001')

    def test_planilla_no_aparece_para_usuarios_sin_paso(self):
        resp = self._bandeja('jefe_th')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'PC-TEST-0001')

    def test_contador_incluye_las_planillas_del_evaluador(self):
        resp = self._bandeja('jefe_ti')
        self.assertEqual(resp.context['total_pendientes_planilla'], 1)
        self.assertIn('planillas', resp.context)

    def test_contador_incluye_las_planillas_del_autorizador(self):
        self._aprobar_evaluador()
        resp = self._bandeja('jefe_gg')
        self.assertEqual(resp.context['total_pendientes_planilla'], 1)

    def test_contador_no_incluye_pasos_aprobados(self):
        self._aprobar_evaluador()
        resp = self._bandeja('jefe_ti')
        self.assertEqual(resp.context['total_pendientes_planilla'], 0)

    def test_seccion_planillas_visible(self):
        resp = self._bandeja('jefe_ti')
        self.assertContains(resp, 'Planillas de Cambios')

    def test_estado_vacio_no_se_muestra_hay_planilla(self):
        resp = self._bandeja('jefe_ti')
        self.assertNotContains(resp, 'No hay solicitudes')

    def test_filtro_estado_aprobada_muestra_el_paso_aprobado(self):
        self._aprobar_evaluador()
        resp = self._bandeja('jefe_ti', estado='aprobada')
        self.assertContains(resp, 'PC-TEST-0001')


# =============================================================================
# B3: el detalle de la planilla muestra el botón a quien tiene paso pendiente
# =============================================================================
class DetallePlanillaBotonTest(BasePlanillaAprobacionTest):

    def _detalle(self, username):
        self.client.login(username=username, password='testpass123')
        return self.client.get(
            reverse('documentos:detalle_planilla', args=[self.planilla.uuid]),
        )

    def test_evaluador_pendiente_ve_el_boton(self):
        resp = self._detalle('jefe_ti')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Revisar y aprobar')
        self.assertEqual(
            resp.context['mi_aprobacion_pendiente'].rol_aprobador, 'evaluador',
        )

    def test_autorizador_ve_el_boton_tras_evaluacion(self):
        self._aprobar_evaluador()
        resp = self._detalle('jefe_gg')
        self.assertContains(resp, 'Revisar y aprobar')
        self.assertEqual(
            resp.context['mi_aprobacion_pendiente'].rol_aprobador, 'autorizador',
        )

    def test_evaluador_que_ya_aprobo_no_ve_el_boton(self):
        self._aprobar_evaluador()
        resp = self._detalle('jefe_ti')
        self.assertNotContains(resp, 'Revisar y aprobar')
        self.assertIsNone(resp.context['mi_aprobacion_pendiente'])

    def test_usuario_sin_paso_no_ve_el_boton(self):
        resp = self._detalle('jefe_th')
        self.assertNotContains(resp, 'Revisar y aprobar')
        self.assertIsNone(resp.context['mi_aprobacion_pendiente'])

    def test_responsable_sin_paso_no_ve_el_boton(self):
        resp = self._detalle('trab01')
        self.assertNotContains(resp, 'Revisar y aprobar')


# =============================================================================
# B4: escalado de los pasos de planilla estancados
# =============================================================================
class EscaladoPlanillasTest(BasePlanillaAprobacionTest):

    def setUp(self):
        super().setUp()
        # El paso del autorizador solo existe en estado 'pendiente' tras
        # aprobar la evaluación (es el estado real de la planilla perdida).
        self._aprobar_evaluador()
        Notificacion.objects.all().delete()

    def _backdate(self, horas):
        AprobacionPlanilla.objects.filter(
            planilla=self.planilla, rol_aprobador='autorizador',
        ).update(fecha_activacion=timezone.now() - timedelta(hours=horas))

    def test_escalada_notifica_admin(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['planillas_escaladas'], 1)
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_admin, tipo='aprobacion_escalada',
        ).exists())

    def test_escalada_al_sobre_al_aprobador_pendiente(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        escalar_aprobaciones_vencidas()
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_jefe_gg, tipo='planilla_pendiente',
        ).exists())

    def test_escalada_usa_el_enlace_del_detalle(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        escalar_aprobaciones_vencidas()
        notif = Notificacion.objects.filter(
            usuario=self.user_jefe_gg, tipo='planilla_pendiente',
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn(
            f'/documentos/planilla-cambios/{self.planilla.uuid}/', notif.url_accion,
        )

    def test_no_escala_reciente(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['planillas_escaladas'], 0)
        self.assertFalse(
            Notificacion.objects.filter(tipo='planilla_pendiente').exists(),
        )

    def test_no_duplica_en_segunda_corrida(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        escalar_aprobaciones_vencidas()
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['planillas_escaladas'], 0)
        self.assertEqual(
            Notificacion.objects.filter(tipo='planilla_pendiente').count(), 1,
        )

    def test_fija_fecha_escalado(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        escalar_aprobaciones_vencidas()
        self.assertIsNotNone(self._autorizador().fecha_escalado)

    def test_no_escala_pasos_aprobados(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        AprobacionPlanilla.objects.filter(
            planilla=self.planilla, rol_aprobador='autorizador',
        ).update(estado='aprobada')
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['planillas_escaladas'], 0)
        self.assertFalse(
            Notificacion.objects.filter(tipo='planilla_pendiente').exists(),
        )

    def test_no_escala_pasos_esperando(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        AprobacionPlanilla.objects.filter(
            planilla=self.planilla, rol_aprobador='autorizador',
        ).update(
            estado='esperando',
            fecha_activacion=timezone.now() - timedelta(hours=50),
        )
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['planillas_escaladas'], 0)

    def test_el_resultado_conserva_el_contador_de_solicitudes(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['solicitudes_escaladas'], 0)
        self.assertEqual(resultado['planillas_escaladas'], 1)
        self.assertEqual(resultado['escaladas'], 1)


# =============================================================================
# B5: aviso a administradores cuando un paso queda sin aprobador
# =============================================================================
class PasoSinAprobadorTest(BasePlanillaAprobacionTest):

    def _sin_jefes(self):
        self.depto_ti.jefe = None
        self.depto_ti.save(update_fields=['jefe'])
        self.depto_gg.jefe = None
        self.depto_gg.save(update_fields=['jefe'])

    def test_dos_pasos_sin_evaluador_avisa_a_admins(self):
        from documentos.views import crear_cadena_aprobacion_planilla
        Notificacion.objects.all().delete()
        self._sin_jefes()
        planilla = PlanillaCambios.objects.create(
            codigo='PC-SIN-EVAL', documento=self.doc_en_revision,
            tipo_cambio='modificacion', justificacion='Prueba',
            responsable=self.user_trabajador, version_destino='2',
            estado='en_revision',
        )
        crear_cadena_aprobacion_planilla(
            planilla=planilla, usuario=self.user_trabajador,
            documento=self.doc_en_revision,
        )
        paso = AprobacionPlanilla.objects.get(planilla=planilla, rol_aprobador='evaluador')
        self.assertEqual(paso.estado, 'esperando')
        notifs = Notificacion.objects.filter(
            usuario=self.user_admin, tipo='planilla_pendiente',
        )
        self.assertTrue(notifs.exists())
        self.assertIn('no hay usuario asignado', notifs.first().mensaje)

    def test_paso_unico_sin_autorizador_avisa_a_admins(self):
        from documentos.views import crear_cadena_aprobacion_planilla
        Notificacion.objects.all().delete()
        self.depto_gg.jefe = None
        self.depto_gg.save(update_fields=['jefe'])
        planilla = PlanillaCambios.objects.create(
            codigo='PC-SIN-GG', documento=self.doc_en_revision,
            tipo_cambio='modificacion', justificacion='Prueba',
            responsable=self.user_jefe_ti, version_destino='2',
            estado='en_revision',
        )
        crear_cadena_aprobacion_planilla(
            planilla=planilla, usuario=self.user_jefe_ti,
            documento=self.doc_en_revision,
        )
        paso = AprobacionPlanilla.objects.get(planilla=planilla)
        self.assertEqual(paso.estado, 'esperando')
        notifs = Notificacion.objects.filter(
            usuario=self.user_admin, tipo='planilla_pendiente',
        )
        self.assertTrue(notifs.exists())
        self.assertIn('autorización', notifs.first().mensaje)

    def test_no_avisa_cuando_hay_aprobador(self):
        Notificacion.objects.all().delete()
        self._aprobar_evaluador()
        self.assertFalse(Notificacion.objects.filter(
            usuario=self.user_admin, tipo='planilla_pendiente',
        ).exists())


# =============================================================================
# ESTRUCTURA DEL FORMULARIO DE APROBACIÓN (responsive + observaciones)
# =============================================================================
class FormularioAprobarPlanillaTest(BasePlanillaAprobacionTest):
    """El pie de la página de aprobación.

    Regresión: el ``textarea`` de observaciones vivía en un ``<form>`` propio
    sin botón de envío, así que nunca viajaba junto con la acción. El backend
    sí lee ambos (``views.aprobar_planilla``: ``accion`` y ``observaciones``).
    """

    def _formularios(self, contenido):
        from html.parser import HTMLParser

        class Formularios(HTMLParser):
            def __init__(self):
                super().__init__()
                self.abierto = None
                self.formularios = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'form':
                    self.abierto = {'id': attrs.get('id', ''), 'controles': []}
                    self.formularios.append(self.abierto)
                elif self.abierto is not None and tag in ('input', 'textarea', 'button'):
                    self.abierto['controles'].append(attrs)

            def handle_endtag(self, tag):
                if tag == 'form':
                    self.abierto = None

        parser = Formularios()
        parser.feed(contenido)
        return parser.formularios

    def _get(self):
        self.client.login(username='jefe_gg', password='testpass123')
        return self.client.get(
            reverse('documentos:aprobar_planilla', args=[self.planilla.uuid]),
        )

    def test_observaciones_y_accion_en_el_mismo_formulario(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)

        formularios = self._formularios(resp.content.decode())
        con_accion = [
            f for f in formularios
            if any(c.get('name') == 'accion' for c in f['controles'])
        ]
        self.assertEqual(len(con_accion), 1)

        nombres = [c.get('name') for c in con_accion[0]['controles']]
        self.assertIn('observaciones', nombres,
                      'las observaciones no se envían con la acción')

        acciones = {
            c.get('value')
            for c in con_accion[0]['controles'] if c.get('name') == 'accion'
        }
        self.assertEqual(acciones, {'aprobar', 'rechazar'})

    def test_botones_de_accion_son_submit_con_name(self):
        resp = self._get()
        formularios = self._formularios(resp.content.decode())
        botones = [
            c for f in formularios for c in f['controles']
            if c.get('name') == 'accion'
        ]
        self.assertEqual(len(botones), 2)
        for boton in botones:
            self.assertEqual(boton.get('type'), 'submit')

    def test_pagina_pdf_usa_type_button(self):
        """Los controles de paginación no deben enviar el formulario."""
        planilla = PlanillaCambios.objects.get(pk=self.planilla.pk)
        planilla.archivo_propuesto_pdf = 'planillas/PC-TEST-0001.pdf'
        planilla.save(update_fields=['archivo_propuesto_pdf'])

        resp = self._get()
        contenido = resp.content.decode()
        formularios = self._formularios(contenido)
        controles = {
            c.get('id'): c for f in formularios for c in f['controles']
        }
        self.assertIn('btn-prev', controles)
        self.assertIn('btn-next', controles)
        self.assertEqual(controles['btn-prev'].get('type'), 'button')
        self.assertEqual(controles['btn-next'].get('type'), 'button')
        self.assertIn('event.preventDefault()', contenido)
