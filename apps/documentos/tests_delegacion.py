"""Tests de la delegación de jefatura (cobertura por vacaciones).

La delegación da al miembro de un departamento los **poderes operativos**
del jefe (aprobar, transferir, asignar, distribuir, ver confidencial) dentro
de una vigencia, pero **no** la configuración global del sistema.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from autenticacion.models import Notificacion
from documentos import permissions
from documentos.jefatura import (
    aprobadores_que_repr,
    departamentos_jefados,
    delegacion_activa,
    es_delegado_vigente,
    es_jefe_efectivo,
)
from documentos.models import (
    AprobacionPlanilla, DelegacionJefatura, Departamento, Documento,
    PlanillaCambios,
)

HOY = timezone.now().date()


def _crear_delegacion(jefe, delegado, departamento, **kwargs):
    datos = dict(
        jefe=jefe, delegado=delegado, departamento=departamento,
        motivo='Vacaciones', fecha_inicio=HOY, fecha_fin=HOY + timedelta(days=15),
        creada_por=jefe,
    )
    datos.update(kwargs)
    return DelegacionJefatura.objects.create(**datos)


class BaseDelegacionTest(BaseDocumentosTest):
    """Fixtures con una delegación vigente: jefe_ti → coordinador de TI."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            cls.delegacion = _crear_delegacion(
                cls.user_jefe_ti, cls.user_coordinador, cls.depto_ti,
            )


# =============================================================================
# Helpers de jefatura
# =============================================================================
class DelegacionJefaturaHelpersTest(BaseDelegacionTest):

    def test_es_delegado_vigente_solo_para_el_delegado(self):
        self.assertTrue(es_delegado_vigente(self.user_coordinador))
        self.assertFalse(es_delegado_vigente(self.user_trabajador))
        self.assertFalse(es_delegado_vigente(self.user_jefe_ti))

    def test_delegacion_activa_devuelve_la_delegacion(self):
        self.assertEqual(
            delegacion_activa(self.user_coordinador).pk, self.delegacion.pk,
        )
        self.assertIsNone(delegacion_activa(self.user_trabajador))

    def test_no_vigente_fuera_del_rango_de_fechas(self):
        self.delegacion.fecha_inicio = HOY + timedelta(days=30)
        self.delegacion.fecha_fin = HOY + timedelta(days=45)
        self.delegacion.save(update_fields=['fecha_inicio', 'fecha_fin'])
        self.assertFalse(es_delegado_vigente(self.user_coordinador))

    def test_no_vigente_si_esta_revocada(self):
        self.delegacion.activa = False
        self.delegacion.save(update_fields=['activa'])
        self.assertFalse(es_delegado_vigente(self.user_coordinador))

    def test_departamentos_jefados_incluye_al_delegado(self):
        self.assertIn(self.depto_ti.id, departamentos_jefados(self.user_coordinador))
        self.assertNotIn(self.depto_th.id, departamentos_jefados(self.user_coordinador))
        # El jefe natural sigue jefando su departamento.
        self.assertIn(self.depto_ti.id, departamentos_jefados(self.user_jefe_ti))

    def test_es_jefe_efectivo_del_depto_delegado(self):
        self.assertTrue(es_jefe_efectivo(self.user_coordinador, self.depto_ti))
        self.assertTrue(es_jefe_efectivo(self.user_coordinador, self.depto_ti.id))
        self.assertFalse(es_jefe_efectivo(self.user_coordinador, self.depto_th))
        self.assertTrue(es_jefe_efectivo(self.user_jefe_ti, self.depto_ti))
        self.assertFalse(es_jefe_efectivo(self.user_trabajador, self.depto_ti))

    def test_aprobadores_que_repr_incluye_al_jefe_delegante(self):
        esperados = {self.user_coordinador.id, self.user_jefe_ti.id}
        self.assertEqual(set(aprobadores_que_repr(self.user_coordinador)), esperados)
        self.assertEqual(
            aprobadores_que_repr(self.user_trabajador), [self.user_trabajador.id],
        )

    def test_perfil_ejerce_jefatura(self):
        self.assertTrue(self.user_coordinador.perfil.ejerce_jefatura)
        self.assertTrue(self.user_jefe_ti.perfil.ejerce_jefatura)
        self.assertFalse(self.user_trabajador.perfil.ejerce_jefatura)
        self.assertIsNotNone(self.user_coordinador.perfil.delegacion_vigente)
        self.assertIsNone(self.user_jefe_ti.perfil.delegacion_vigente)

    def test_sin_delegacion_el_coordinador_no_ejerce_jefatura(self):
        self.delegacion.delete()
        self.assertFalse(self.user_coordinador.perfil.ejerce_jefatura)
        self.assertIsNone(self.user_coordinador.perfil.delegacion_vigente)
        self.assertFalse(es_jefe_efectivo(self.user_coordinador, self.depto_ti))


# =============================================================================
# Alta de delegaciones
# =============================================================================
class CrearDelegacionTest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url = reverse('documentos:delegaciones_jefatura')
        self.hoy = HOY
        self.datos = {
            'departamento': self.depto_ti.id,
            'delegado': self.user_trabajador.id,
            'fecha_inicio': str(self.hoy),
            'fecha_fin': str(self.hoy + timedelta(days=10)),
            'motivo': 'Vacaciones programadas',
        }

    def _post(self, **extra):
        datos = dict(self.datos)
        datos.update(extra)
        return self.client.post(self.url, datos)

    def test_jefe_crea_delegacion(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post()

        self.assertEqual(resp.status_code, 302)
        delegacion = DelegacionJefatura.objects.get(delegado=self.user_trabajador)
        self.assertEqual(delegacion.jefe, self.user_jefe_ti)
        self.assertEqual(delegacion.departamento, self.depto_ti)
        self.assertEqual(delegacion.fecha_inicio, self.hoy)
        self.assertTrue(delegacion.activa)
        self.assertEqual(delegacion.creada_por, self.user_jefe_ti)
        self.assertTrue(
            Notificacion.objects.filter(
                usuario=self.user_trabajador, titulo__icontains='delegado',
            ).exists()
        )

    def test_jefe_no_puede_delegar_otro_departamento(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(
            departamento=self.depto_th.id,
            delegado=self.user_trabajador_otro.id,
        )

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DelegacionJefatura.objects.exists())

    def test_rechaza_delegado_de_otro_departamento(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(delegado=self.user_trabajador_otro.id)

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DelegacionJefatura.objects.exists())

    def test_rechaza_al_mismo_jefe_como_delegado(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(delegado=self.user_jefe_ti.id)

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DelegacionJefatura.objects.exists())

    def test_rechaza_fechas_invalidas(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(fecha_inicio='no-es-fecha')

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DelegacionJefatura.objects.exists())

    def test_rechaza_fin_anterior_a_inicio(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(
            fecha_inicio=str(self.hoy + timedelta(days=10)),
            fecha_fin=str(self.hoy),
        )

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DelegacionJefatura.objects.exists())

    def test_rechaza_delegacion_terminada_en_el_pasado(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(
            fecha_inicio=str(self.hoy - timedelta(days=20)),
            fecha_fin=str(self.hoy - timedelta(days=10)),
        )

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DelegacionJefatura.objects.exists())

    def test_rechaza_delegacion_solapada(self):
        _crear_delegacion(
            self.user_jefe_ti, self.user_coordinador, self.depto_ti,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(delegado=self.user_coordinador.id)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            DelegacionJefatura.objects.filter(delegado=self.user_coordinador).count(),
            1,
        )

    def test_admite_solape_en_un_rango_posterior(self):
        _crear_delegacion(
            self.user_jefe_ti, self.user_coordinador, self.depto_ti,
            fecha_inicio=self.hoy, fecha_fin=self.hoy + timedelta(days=5),
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(
            delegado=self.user_coordinador.id,
            fecha_inicio=str(self.hoy + timedelta(days=6)),
            fecha_fin=str(self.hoy + timedelta(days=20)),
        )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            DelegacionJefatura.objects.filter(delegado=self.user_coordinador).count(),
            2,
        )

    def test_admin_crea_para_otro_departamento(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self._post(
            departamento=self.depto_th.id,
            delegado=self.user_trabajador_otro.id,
        )

        self.assertEqual(resp.status_code, 302)
        delegacion = DelegacionJefatura.objects.get(departamento=self.depto_th)
        self.assertEqual(delegacion.jefe, self.user_jefe_th)
        self.assertEqual(delegacion.creada_por, self.user_admin)
        self.assertTrue(
            Notificacion.objects.filter(usuario=self.user_jefe_th).exists()
        )

    def test_admin_no_puede_delegar_departamento_sin_jefe(self):
        sin_jefe = Departamento.objects.create(
            nombre='Área Sin Jefe', codigo='ASJ', activo=True,
        )
        self.client.login(username='admin01', password='testpass123')
        resp = self._post(departamento=sin_jefe.id, delegado=self.user_trabajador.id)

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DelegacionJefatura.objects.exists())

    def test_trabajador_no_accede_a_la_gestion(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DelegacionJefatura.objects.exists())

    def test_delegado_consulta_pero_no_crea(self):
        delegacion = _crear_delegacion(
            self.user_jefe_ti, self.user_coordinador, self.depto_ti,
        )
        self.client.login(username='coord01', password='testpass123')

        resp_get = self.client.get(self.url)
        self.assertEqual(resp_get.status_code, 200)
        self.assertFalse(resp_get.context['puede_crear'])

        resp_post = self._post(delegado=self.user_trabajador.id)
        self.assertEqual(resp_post.status_code, 302)
        self.assertEqual(
            DelegacionJefatura.objects.filter(pk=delegacion.pk).count(), 1,
        )
        self.assertFalse(DelegacionJefatura.objects.exclude(pk=delegacion.pk).exists())


# =============================================================================
# Revocación
# =============================================================================
class RevocarDelegacionTest(BaseDelegacionTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url = reverse('documentos:revocar_delegacion', args=[self.delegacion.pk])

    def test_jefe_revoca(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(self.url, {'motivo_revocacion': 'Volvió de vacaciones'})

        self.assertEqual(resp.status_code, 302)
        self.delegacion.refresh_from_db()
        self.assertFalse(self.delegacion.activa)
        self.assertEqual(self.delegacion.revocada_por, self.user_jefe_ti)
        self.assertIsNotNone(self.delegacion.fecha_revocacion)
        self.assertEqual(self.delegacion.motivo_revocacion, 'Volvió de vacaciones')
        self.assertFalse(es_delegado_vigente(self.user_coordinador))

    def test_delegado_puede_renunciar(self):
        self.client.login(username='coord01', password='testpass123')
        resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 302)
        self.delegacion.refresh_from_db()
        self.assertFalse(self.delegacion.activa)
        self.assertEqual(self.delegacion.revocada_por, self.user_coordinador)

    def test_admin_revoca(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 302)
        self.delegacion.refresh_from_db()
        self.assertFalse(self.delegacion.activa)

    def test_tercero_no_puede_revocar(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(self.url)

        self.assertEqual(resp.status_code, 302)
        self.delegacion.refresh_from_db()
        self.assertTrue(self.delegacion.activa)

    def test_get_no_revoca(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 302)
        self.delegacion.refresh_from_db()
        self.assertTrue(self.delegacion.activa)

    def test_revocar_notifica_al_delegado(self):
        self.client.login(username='jefe_ti', password='testpass123')
        self.client.post(self.url)

        self.assertTrue(
            Notificacion.objects.filter(
                usuario=self.user_coordinador, titulo__icontains='revocada',
            ).exists()
        )


# =============================================================================
# Poderes operativos del delegado (y límite de la configuración global)
# =============================================================================
class PermisosDelDelegadoTest(BaseDelegacionTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='coord01', password='testpass123')

    def test_accede_a_documentos_del_departamento(self):
        resp = self.client.get(reverse('documentos:documentos_departamento'))
        self.assertEqual(resp.status_code, 200)

    def test_accede_a_la_bandeja_de_aprobaciones(self):
        resp = self.client.get(reverse('documentos:bandeja_aprobaciones'))
        self.assertEqual(resp.status_code, 200)

    def test_ve_la_tarjeta_de_departamento_en_el_dashboard(self):
        resp = self.client.get(reverse('autenticacion:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context['conteos_vigencia'])

    def test_visibilidad_de_documentos_por_departamento(self):
        visibles = Documento.objects.filter(
            permissions.q_documentos_visibles(self.user_coordinador)
        )
        self.assertIn(
            self.doc_activo.pk,
            set(visibles.values_list('pk', flat=True)),
            'El delegado debe ver los documentos internos de su departamento',
        )

        # Sin la delegación, el coordinador no ve ese mismo documento.
        self.delegacion.delete()
        visibles = Documento.objects.filter(
            permissions.q_documentos_visibles(self.user_coordinador)
        )
        self.assertNotIn(
            self.doc_activo.pk,
            set(visibles.values_list('pk', flat=True)),
        )

    def test_puede_transferir_documentos_de_su_departamento(self):
        permitido, modo = permissions.puede_transferir(
            self.user_coordinador, self.doc_activo,
        )
        self.assertTrue(permitido)
        self.assertEqual(modo, 'directo')

    def test_es_jefe_de_departamento(self):
        self.assertTrue(
            permissions.es_jefe_de_departamento(self.user_coordinador, self.doc_activo),
        )
        self.assertFalse(
            permissions.es_jefe_de_departamento(self.user_coordinador, self.doc_th),
        )

    def test_no_accede_a_la_configuracion_global(self):
        resp = self.client.get(reverse('documentos:configuracion'))
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('documentos:mis_documentos'))

    def test_sin_delegacion_no_accede_a_nada_de_jefatura(self):
        self.delegacion.delete()
        for nombre in ('documentos_departamento', 'bandeja_aprobaciones'):
            resp = self.client.get(reverse(f'documentos:{nombre}'))
            self.assertEqual(resp.status_code, 302, nombre)
            self.assertRedirects(resp, reverse('documentos:mis_documentos'))


# =============================================================================
# Aprobación de planillas por el delegado
# =============================================================================
class AprobacionPlanillaDelegadoTest(BaseDelegacionTest):

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _planilla_pendiente(self, aprobador, codigo='PC-DEL-1'):
        planilla = PlanillaCambios.objects.create(
            codigo=codigo, documento=self.doc_activo,
            tipo_cambio='modificacion', justificacion='Cambio',
            responsable=self.user_jefe_ti, version_destino='2.0',
            estado='en_revision', clasificacion_propuesta='publico',
            clasificacion_actual_snapshot='interno',
        )
        AprobacionPlanilla.objects.create(
            planilla=planilla, aprobador=aprobador, estado='pendiente',
        )
        return planilla

    def _aprobar(self, planilla):
        return self.client.post(
            f'/documentos/api/planilla/{planilla.uuid}/aprobar/',
            data=json.dumps({'observaciones': 'ok'}),
            content_type='application/json',
        )

    def test_delegado_aprueba_la_planilla_de_su_jefe(self):
        planilla = self._planilla_pendiente(self.user_jefe_ti)
        self.client.login(username='coord01', password='testpass123')

        resp = self._aprobar(planilla)

        self.assertEqual(resp.status_code, 200)
        planilla.refresh_from_db()
        self.assertEqual(planilla.estado, 'aprobado')

    def test_sin_delegacion_no_puede_aprobar(self):
        planilla = self._planilla_pendiente(self.user_jefe_ti, 'PC-DEL-2')
        self.delegacion.delete()
        self.client.login(username='coord01', password='testpass123')

        resp = self._aprobar(planilla)

        self.assertEqual(resp.status_code, 403)
        planilla.refresh_from_db()
        self.assertEqual(planilla.estado, 'en_revision')

    def test_no_aprueba_planillas_de_un_jefe_ajeno(self):
        planilla = self._planilla_pendiente(self.user_jefe_th, 'PC-DEL-3')
        self.client.login(username='coord01', password='testpass123')

        resp = self._aprobar(planilla)

        self.assertEqual(resp.status_code, 404)
        planilla.refresh_from_db()
        self.assertEqual(planilla.estado, 'en_revision')
