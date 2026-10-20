"""Tests del autollenado de firmas: Evaluado por / Autorizado por / Resolución.

Cubre las dos reglas del flujo de aprobación, aplicadas por igual al
SGC-FOR-001 (creación/eliminación, ``SolicitudAprobacionDocumento``) y al
SGC-FOR-005 (planilla de cambios, ``AprobacionPlanilla``):

- **Un solo paso** (jefe propietario → GG): el MISMO aprobador firma los dos
  bloques (Evaluado y Autorizado) y fija la Resolución.
- **Dos pasos** (trabajador/coordinador → jefe directo evalúa → GG autoriza):
  el evaluador solo firma Evaluado; el autorizador firma Autorizado y la
  Resolución. Si el evaluador rechaza, Autorizado queda vacío.

Además valida que las fechas son las reales de la respuesta (no el momento
de generar la planilla), que los XLSX/PDF cacheados se invalidan tras
firmar y que el comando ``reparar_planillas_evaluacion`` reconstruye las
planillas ya resueltas.
"""

import io
import json
import os
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import (
    AprobacionPlanilla, CampoFormato, Formato, PlanillaCambios,
    SeccionFormato, SolicitudAprobacionDocumento, SolicitudCambio,
    VersionDocumento,
)
from documentos.views import (
    crear_cadena_aprobacion_documento, crear_cadena_aprobacion_planilla,
)


def crear_formato_firmas(codigo, con_resolucion=True, **kwargs):
    """Formato mínimo con los campos de Evaluado/Autorizado (+ Resolución)."""
    defaults = dict(
        codigo=codigo, nombre='Planilla de firmas', version='1', edicion='1',
        activo=True, aplica_creacion=True, aplica_eliminacion=True,
        aplica_modificacion=True,
    )
    defaults.update(kwargs)
    with patch('documentos.tasks.convertir_a_pdf_sync', return_value=None):
        formato = Formato.objects.create(**defaults)
    seccion = SeccionFormato.objects.create(
        formato=formato, titulo='Evaluación y Resolución', orden=1,
    )
    campos = [
        ('evaluado_por_nombre', 'Nombre y Apellido - Evaluado Por', 'texto'),
        ('evaluado_por_cargo', 'Cargo - Evaluado Por', 'texto'),
        ('evaluado_por_fecha', 'Fecha - Evaluado Por', 'fecha'),
        ('autorizado_por_nombre', 'Nombre y Apellido - Autorizado Por', 'texto'),
        ('autorizado_por_cargo', 'Cargo - Autorizado Por', 'texto'),
        ('autorizado_por_fecha', 'Fecha - Autorizado Por', 'fecha'),
    ]
    if con_resolucion:
        campos.append(('resolucion', 'Resolución', 'select'))
    for clave, etiqueta, tipo in campos:
        CampoFormato.objects.create(
            seccion=seccion, clave=clave, etiqueta=etiqueta, tipo=tipo,
            origen=clave, orden=1,
        )
    return formato


def fecha_esperada(momento):
    """Fecha ISO que debe escribirse para una respuesta concreta."""
    return timezone.localtime(momento).date().isoformat()


# =============================================================================
# SGC-FOR-001: aprobación de documentos (SolicitudAprobacionDocumento)
# =============================================================================
class FirmasDocumentoPasoUnicoTest(BaseDocumentosTest):
    """Jefe propietario → GG aprueba en UN solo paso: firma en ambos bloques."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.formato = crear_formato_firmas('SGC-FOR-001-F1', con_resolucion=False)
        cls.solicitud = SolicitudCambio.objects.create(
            codigo='SC-FIRMAS-DOC-1', formato=cls.formato, operacion='creacion',
            documento=cls.doc_en_revision, solicitante=cls.user_jefe_ti,
            departamento=cls.depto_ti, estado='en_revision', respuestas={},
        )
        cls.paso = crear_cadena_aprobacion_documento(
            documento=cls.doc_en_revision, usuario=cls.user_jefe_ti,
            departamento=cls.depto_ti, accion='creacion',
        )

    def test_la_cadena_de_un_jefe_tiene_un_solo_paso(self):
        self.assertEqual(self.paso.rol_aprobador, 'autorizador')
        self.assertTrue(self.paso.es_final)
        self.assertTrue(self.paso.es_paso_unico())
        self.assertEqual(self.paso.aprobador, self.user_jefe_gg)

    def test_aprobar_llena_evaluado_y_autorizado_con_el_mismo_jefe(self):
        self.paso.aprobar(observaciones='ok')

        self.solicitud.refresh_from_db()
        respuestas = self.solicitud.respuestas
        # El jefe de GG evalúa Y autoriza en el flujo de un solo paso.
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertIn('Jefe de Departamento', respuestas['evaluado_por_cargo'])
        self.assertIn('Jefe de Departamento', respuestas['autorizado_por_cargo'])
        self.assertEqual(
            respuestas['evaluado_por_fecha'], fecha_esperada(self.paso.fecha_respuesta),
        )
        self.assertEqual(
            respuestas['autorizado_por_fecha'], fecha_esperada(self.paso.fecha_respuesta),
        )

    def test_rechazar_llena_ambos_bloques_y_cancela_el_paso(self):
        self.paso.rechazar(motivo='No aplica')

        self.solicitud.refresh_from_db()
        respuestas = self.solicitud.respuestas
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertEqual(
            respuestas['evaluado_por_fecha'], fecha_esperada(self.paso.fecha_respuesta),
        )


class FirmasDocumentoDosPasosTest(BaseDocumentosTest):
    """Trabajador → jefe directo evalúa → GG autoriza: cada rol firma lo suyo."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.formato = crear_formato_firmas('SGC-FOR-001-F2', con_resolucion=False)
        cls.solicitud = SolicitudCambio.objects.create(
            codigo='SC-FIRMAS-DOC-2', formato=cls.formato, operacion='creacion',
            documento=cls.doc_en_revision, solicitante=cls.user_trabajador,
            departamento=cls.depto_ti, estado='en_revision', respuestas={},
        )
        cls.paso_eval = crear_cadena_aprobacion_documento(
            documento=cls.doc_en_revision, usuario=cls.user_trabajador,
            departamento=cls.depto_ti, accion='creacion',
        )

    def _autorizador(self):
        return SolicitudAprobacionDocumento.objects.get(
            documento=self.doc_en_revision, accion_solicitud='creacion',
            rol_aprobador='autorizador',
        )

    def test_la_cadena_de_un_trabajador_tiene_dos_pasos(self):
        self.assertEqual(self.paso_eval.rol_aprobador, 'evaluador')
        self.assertFalse(self.paso_eval.es_final)
        self.assertFalse(self._autorizador().es_paso_unico())

    def test_el_evaluador_solo_firma_evaluado(self):
        self.paso_eval.aprobar(observaciones='evaluado')

        self.solicitud.refresh_from_db()
        respuestas = self.solicitud.respuestas
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertEqual(
            respuestas['evaluado_por_fecha'], fecha_esperada(self.paso_eval.fecha_respuesta),
        )
        self.assertNotIn('autorizado_por_nombre', respuestas)
        self.assertNotIn('autorizado_por_fecha', respuestas)

    def test_el_autorizador_firma_autorizado_sin_pisar_evaluado(self):
        self.paso_eval.aprobar(observaciones='evaluado')
        autorizador = self._autorizador()
        autorizador.aprobar(observaciones='ok')

        self.solicitud.refresh_from_db()
        respuestas = self.solicitud.respuestas
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertEqual(
            respuestas['autorizado_por_fecha'], fecha_esperada(autorizador.fecha_respuesta),
        )

    def test_rechazo_del_evaluador_deja_autorizado_vacio(self):
        self.paso_eval.rechazar(motivo='Insuficiente')

        self.solicitud.refresh_from_db()
        respuestas = self.solicitud.respuestas
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertNotIn('autorizado_por_nombre', respuestas)


# =============================================================================
# SGC-FOR-005: planilla de cambios (AprobacionPlanilla, web y API)
# =============================================================================
class BaseFirmasPlanillaTest(BaseDocumentosTest):
    """Planilla ``en_revision`` con SolicitudCambio (FOR-005) enlazada."""

    responsable = 'trabajador'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            cls.formato = crear_formato_firmas('SGC-FOR-005-F')
            cls.solicitud = SolicitudCambio.objects.create(
                codigo='SC-FIRMAS-PC-1', formato=cls.formato,
                operacion='modificacion', documento=cls.doc_en_revision,
                solicitante=cls.user_trabajador, departamento=cls.depto_ti,
                estado='en_revision', respuestas={},
            )
            responsable = (
                cls.user_jefe_ti if cls.responsable == 'jefe'
                else cls.user_trabajador
            )
            cls.planilla = PlanillaCambios.objects.create(
                codigo='PC-FIRMAS-0001', documento=cls.doc_en_revision,
                tipo_cambio='modificacion', justificacion='Prueba de firmas',
                responsable=responsable, version_destino='2',
                estado='en_revision', solicitud_cambio=cls.solicitud,
            )
            cls.paso_eval = crear_cadena_aprobacion_planilla(
                planilla=cls.planilla, usuario=responsable,
                documento=cls.doc_en_revision,
            )

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _pasos(self):
        return AprobacionPlanilla.objects.filter(planilla=self.planilla)

    def _autorizador(self):
        return self._pasos().get(rol_aprobador='autorizador')

    def _aprobar_o_rechazar(self, username, accion='aprobar', observaciones='ok'):
        self.client.login(username=username, password='testpass123')
        resp = self.client.post(
            reverse('documentos:aprobar_planilla', args=[self.planilla.uuid]),
            {'accion': accion, 'observaciones': observaciones},
        )
        self.assertEqual(resp.status_code, 302)
        self.client.logout()
        return resp

    def _respuestas(self):
        self.solicitud.refresh_from_db()
        return self.solicitud.respuestas


class FirmasPlanillaDosPasosTest(BaseFirmasPlanillaTest):

    def test_el_evaluador_solo_firma_evaluado_y_no_resuelve(self):
        self._aprobar_o_rechazar('jefe_ti')

        respuestas = self._respuestas()
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertEqual(
            respuestas['evaluado_por_fecha'],
            fecha_esperada(self._pasos().get(rol_aprobador='evaluador').fecha_aprobacion),
        )
        self.assertNotIn('autorizado_por_nombre', respuestas)
        self.assertNotIn('resolucion', respuestas)

    def test_el_autorizador_firma_autorizado_y_resuelve_aceptado(self):
        self._aprobar_o_rechazar('jefe_ti')
        self._aprobar_o_rechazar('jefe_gg')

        respuestas = self._respuestas()
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['resolucion'], 'Aceptado')
        self.assertEqual(
            respuestas['autorizado_por_fecha'],
            fecha_esperada(self._autorizador().fecha_aprobacion),
        )

    def test_rechazo_del_evaluador_resuelve_rechazado_sin_autorizado(self):
        self._aprobar_o_rechazar(
            'jefe_ti', accion='rechazar', observaciones='No procede',
        )

        respuestas = self._respuestas()
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertEqual(respuestas['resolucion'], 'Rechazado')
        self.assertNotIn('autorizado_por_nombre', respuestas)

    def test_el_autorizador_que_rechaza_firma_su_bloque(self):
        self._aprobar_o_rechazar('jefe_ti')
        self._aprobar_o_rechazar(
            'jefe_gg', accion='rechazar', observaciones='Sin respaldo',
        )

        respuestas = self._respuestas()
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['resolucion'], 'Rechazado')

    def test_mismo_comportamiento_por_api(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/planilla/{self.planilla.uuid}/aprobar/',
            data=json.dumps({'observaciones': 'evaluado'}),
            content_type='application/json',
        )
        self.client.logout()
        self.assertEqual(resp.status_code, 200)

        respuestas = self._respuestas()
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertNotIn('autorizado_por_nombre', respuestas)


class FirmasPlanillaPasoUnicoTest(BaseFirmasPlanillaTest):
    """Planilla cuyo responsable ES jefe: GG aprueba en un solo paso."""

    responsable = 'jefe'

    def test_la_cadena_de_un_jefe_tiene_un_solo_paso(self):
        self.assertFalse(self._pasos().filter(rol_aprobador='evaluador').exists())
        self.assertTrue(self.paso_eval.es_paso_unico())

    def test_aprobar_llena_evaluado_y_autorizado_con_el_mismo_jefe(self):
        self._aprobar_o_rechazar('jefe_gg')

        respuestas = self._respuestas()
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['resolucion'], 'Aceptado')
        self.assertEqual(
            respuestas['evaluado_por_fecha'],
            fecha_esperada(self._autorizador().fecha_aprobacion),
        )

    def test_rechazar_llena_ambos_bloques(self):
        self._aprobar_o_rechazar('jefe_gg', accion='rechazar', observaciones='No')

        respuestas = self._respuestas()
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['resolucion'], 'Rechazado')


class InvalidacionPlanillaCacheadaTest(BaseFirmasPlanillaTest):
    """Tras firmar, el XLSX/PDF cacheado debe regenerarse con las firmas."""

    def test_aprobar_invalida_el_xlsx_cacheado(self):
        tmp = tempfile.mkdtemp(prefix='firmas-media-')
        try:
            with self.settings(MEDIA_ROOT=tmp):
                self.solicitud.planilla_xlsx.save(
                    'planilla.xlsx', ContentFile(b'PK\x03\x04viejo'), save=True,
                )
                self._aprobar_o_rechazar('jefe_ti')
                self._aprobar_o_rechazar('jefe_gg')
                self.solicitud.refresh_from_db()
                self.assertFalse(
                    self.solicitud.planilla_xlsx,
                    'El XLSX cacheado debe borrarse para regenerarlo firmado',
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# =============================================================================
# Backfill: reparar_planillas_evaluacion
# =============================================================================
class RepararFirmasDocumentoTest(BaseDocumentosTest):
    """Historia de FOR-001 ya resuelta → el comando reconstruye las firmas."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.formato = crear_formato_firmas('SGC-FOR-001-BF', con_resolucion=True)

    def _solicitud(self, codigo, **kwargs):
        defaults = dict(
            codigo=codigo, formato=self.formato, operacion='creacion',
            documento=self.doc_en_revision, solicitante=self.user_trabajador,
            departamento=self.depto_ti, estado='aprobado', respuestas={},
        )
        defaults.update(kwargs)
        return SolicitudCambio.objects.create(**defaults)

    def _historia(self, fecha, estado='aprobada', rol='autorizador', aprobador=None):
        return SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision, accion_solicitud='creacion',
            solicitante=self.user_trabajador, propietario=self.user_trabajador,
            departamento_origen=self.depto_ti,
            aprobador=aprobador or self.user_jefe_gg,
            rol_aprobador=rol, es_final=(rol == 'autorizador'),
            estado=estado, fecha_respuesta=fecha,
        )

    def test_rellena_el_paso_unico_con_la_fecha_real(self):
        fecha = timezone.now() - timedelta(days=5)
        self._historia(fecha)
        sol = self._solicitud('SC-BF-UNICO')

        out = io.StringIO()
        call_command('reparar_planillas_evaluacion', stdout=out)

        sol.refresh_from_db()
        respuestas = sol.respuestas
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['resolucion'], 'Aceptado')
        self.assertEqual(respuestas['evaluado_por_fecha'], fecha_esperada(fecha))
        self.assertEqual(respuestas['autorizado_por_fecha'], fecha_esperada(fecha))

    def test_rellena_la_cadena_de_dos_pasos_segun_cada_rol(self):
        fecha_eval = timezone.now() - timedelta(days=7)
        fecha_aut = timezone.now() - timedelta(days=3)
        self._historia(fecha_eval, rol='evaluador', aprobador=self.user_jefe_ti)
        self._historia(fecha_aut, rol='autorizador')
        sol = self._solicitud('SC-BF-DOS')

        call_command('reparar_planillas_evaluacion', stdout=io.StringIO())

        sol.refresh_from_db()
        respuestas = sol.respuestas
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertEqual(respuestas['evaluado_por_fecha'], fecha_esperada(fecha_eval))
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['autorizado_por_fecha'], fecha_esperada(fecha_aut))

    def test_rechazo_del_evaluador_deja_autorizado_vacio(self):
        fecha = timezone.now() - timedelta(days=2)
        self._historia(fecha, estado='rechazada', rol='evaluador',
                       aprobador=self.user_jefe_ti)
        SolicitudAprobacionDocumento.objects.filter(
            documento=self.doc_en_revision, rol_aprobador='autorizador',
        ).update(estado='cancelada')
        sol = self._solicitud('SC-BF-RECH', estado='rechazado')

        call_command('reparar_planillas_evaluacion', stdout=io.StringIO())

        sol.refresh_from_db()
        respuestas = sol.respuestas
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertEqual(respuestas['resolucion'], 'Rechazado')
        self.assertNotIn('autorizado_por_nombre', respuestas)

    def test_dry_run_no_escribe(self):
        self._historia(timezone.now())
        sol = self._solicitud('SC-BF-DRY')

        out = io.StringIO()
        call_command('reparar_planillas_evaluacion', '--dry-run', stdout=out)

        sol.refresh_from_db()
        self.assertEqual(sol.respuestas, {})
        self.assertIn('evaluado_por_nombre', out.getvalue())

    def test_no_sobrescribe_sin_force_y_si_con_force(self):
        self._historia(timezone.now() - timedelta(days=1))
        sol = self._solicitud(
            'SC-BF-FORZA', respuestas={'evaluado_por_nombre': 'Valor manual'},
        )

        call_command('reparar_planillas_evaluacion', stdout=io.StringIO())
        sol.refresh_from_db()
        # El campo ya escrito se conserva; los vacíos se rellenan igual.
        self.assertEqual(sol.respuestas['evaluado_por_nombre'], 'Valor manual')
        self.assertEqual(sol.respuestas['autorizado_por_nombre'], 'Jefe GG')

        call_command('reparar_planillas_evaluacion', '--force', stdout=io.StringIO())
        sol.refresh_from_db()
        self.assertEqual(sol.respuestas['evaluado_por_nombre'], 'Jefe GG')

    def test_es_idempotente(self):
        self._historia(timezone.now())
        sol = self._solicitud('SC-BF-IDEM')

        call_command('reparar_planillas_evaluacion', stdout=io.StringIO())
        sol.refresh_from_db()
        primeras = dict(sol.respuestas)

        out = io.StringIO()
        call_command('reparar_planillas_evaluacion', stdout=out)
        sol.refresh_from_db()
        self.assertEqual(sol.respuestas, primeras)
        self.assertIn('firmas completadas: 0', out.getvalue())


class RepararFirmasPlanillaTest(BaseDocumentosTest):
    """Historia de FOR-005 (AprobacionPlanilla) → el comando reconstruye."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            cls.formato = crear_formato_firmas('SGC-FOR-005-BF')
            cls.solicitud = SolicitudCambio.objects.create(
                codigo='SC-BF-PC-1', formato=cls.formato, operacion='modificacion',
                documento=cls.doc_en_revision, solicitante=cls.user_trabajador,
                departamento=cls.depto_ti, estado='aprobado', respuestas={},
            )
            cls.planilla = PlanillaCambios.objects.create(
                codigo='PC-BF-0001', documento=cls.doc_en_revision,
                tipo_cambio='modificacion', justificacion='Backfill',
                responsable=cls.user_trabajador, version_destino='2',
                estado='aprobado', solicitud_cambio=cls.solicitud,
            )
            fecha_eval = timezone.now() - timedelta(days=7)
            fecha_aut = timezone.now() - timedelta(days=3)
            AprobacionPlanilla.objects.create(
                planilla=cls.planilla, aprobador=cls.user_jefe_ti,
                rol_aprobador='evaluador', es_final=False, estado='aprobada',
                fecha_aprobacion=fecha_eval,
            )
            AprobacionPlanilla.objects.create(
                planilla=cls.planilla, aprobador=cls.user_jefe_gg,
                rol_aprobador='autorizador', es_final=True, estado='aprobada',
                fecha_aprobacion=fecha_aut,
            )

    def test_reconstruye_firmas_y_resolucion_desde_las_aprobaciones(self):
        out = io.StringIO()
        call_command('reparar_planillas_evaluacion', stdout=out)

        self.solicitud.refresh_from_db()
        respuestas = self.solicitud.respuestas
        self.assertEqual(respuestas['evaluado_por_nombre'], 'Jefe TI')
        self.assertEqual(respuestas['autorizado_por_nombre'], 'Jefe GG')
        self.assertEqual(respuestas['resolucion'], 'Aceptado')
        self.assertIn('SC-BF-PC-1', out.getvalue())

    def test_invalida_el_xlsx_cacheado_tras_rellenar(self):
        tmp = tempfile.mkdtemp(prefix='firmas-bf-media-')
        try:
            with self.settings(MEDIA_ROOT=tmp):
                self.solicitud.planilla_xlsx.save(
                    'planilla.xlsx', ContentFile(b'PK\x03\x04viejo'), save=True,
                )
                call_command('reparar_planillas_evaluacion', stdout=io.StringIO())
                self.solicitud.refresh_from_db()
                self.assertFalse(self.solicitud.planilla_xlsx)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# =============================================================================
# Visualización de versiones: la vigente primero y marcada
# =============================================================================
class VersionVigenteEnDetalleTest(BaseDocumentosTest):
    """El detalle muestra todas las versiones y marca la vigente."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.doc_activo.version_actual = '2'
        cls.doc_activo.save(update_fields=['version_actual'])
        cls.v1 = VersionDocumento.objects.create(
            documento=cls.doc_activo, numero_version='1',
            creado_por=cls.user_trabajador, cambios='Versión inicial',
        )
        cls.v2 = VersionDocumento.objects.create(
            documento=cls.doc_activo, numero_version='2',
            creado_por=cls.user_trabajador, cambios='Cambio',
        )

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def _detalle(self):
        return self.client.get(reverse(
            'documentos:detalle',
            args=[self.doc_activo.tipo_id, self.doc_activo.uuid],
        ))

    def test_muestra_todas_y_marca_la_vigente_primero(self):
        resp = self._detalle()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            [v.numero_version for v in resp.context['versiones']], ['2', '1'],
        )
        self.assertEqual(resp.context['version_vigente_id'], self.v2.pk)
        html = resp.content.decode()
        pos_v2 = html.index('version-number">v2')
        pos_v1 = html.index('version-number">v1')
        self.assertLess(pos_v2, pos_v1)
        self.assertIn('Vigente', html[pos_v2:pos_v1])
        self.assertIn('Superada', html[pos_v1:])

    def test_documento_no_activo_muestra_ultima_en_vez_de_vigente(self):
        self.doc_activo.estado = 'obsoleto'
        self.doc_activo.save(update_fields=['estado'])

        html = self._detalle().content.decode()

        pos_v2 = html.index('version-number">v2')
        pos_v1 = html.index('version-number">v1')
        self.assertIn('Última', html[pos_v2:pos_v1])
        self.assertNotIn('Vigente', html[pos_v2:pos_v1])
        self.assertIn('Superada', html[pos_v1:])


# =============================================================================
# Backfill: reparar_creador_versiones_planilla
# =============================================================================
class RepararCreadorVersionesPlanillaTest(BaseDocumentosTest):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            cls.doc_activo.version_actual = '2'
            cls.doc_activo.save(update_fields=['version_actual'])
            cls.v1 = VersionDocumento.objects.create(
                documento=cls.doc_activo, numero_version='1',
                creado_por=cls.user_trabajador, cambios='Versión inicial',
            )
            cls.v2 = VersionDocumento.objects.create(
                documento=cls.doc_activo, numero_version='2',
                creado_por=cls.user_jefe_gg,
                cambios='Planilla de Cambios PC-CREADOR-1: cambio',
            )
            cls.planilla = PlanillaCambios.objects.create(
                codigo='PC-CREADOR-1', documento=cls.doc_activo,
                tipo_cambio='modificacion', justificacion='cambio',
                responsable=cls.user_trabajador, version_destino='2',
                estado='aprobado',
            )

    def test_dry_run_no_escribe(self):
        out = io.StringIO()
        call_command('reparar_creador_versiones_planilla', '--dry-run', stdout=out)

        self.v2.refresh_from_db()
        self.assertEqual(self.v2.creado_por, self.user_jefe_gg)
        self.assertIn('PC-CREADOR-1', out.getvalue())

    def test_corrige_el_creador_y_es_idempotente(self):
        call_command('reparar_creador_versiones_planilla', stdout=io.StringIO())

        self.v2.refresh_from_db()
        self.assertEqual(self.v2.creado_por, self.user_trabajador)
        # La v1 no la generó una planilla: no se toca.
        self.v1.refresh_from_db()
        self.assertEqual(self.v1.creado_por, self.user_trabajador)

        out = io.StringIO()
        call_command('reparar_creador_versiones_planilla', stdout=out)
        self.assertIn('corregidas: 0', out.getvalue())
