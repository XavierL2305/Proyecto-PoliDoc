import json
from django.test import TestCase, Client
from django.contrib.auth.models import User
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from io import BytesIO
from django.core.files.uploadedfile import SimpleUploadedFile

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET
from documentos.models import (
    Documento, VersionDocumento, SolicitudDescarga, AprobacionDescarga,
    PlanillaCambios, SolicitudAprobacionDocumento, SolicitudAccesoDocumento,
    DistribucionDocumento, Formato, SolicitudCambio, CampoFormato,
    SeccionFormato, TipoDocumento, RenovacionVencimiento,
)
from autenticacion.models import PerfilUsuario, Notificacion


# =============================================================================
# API: CAMBIAR ESTADO
# =============================================================================

class APICambiarEstadoTest(BaseDocumentosTest):
    """Tests del endpoint de cambio de estado (inhabilitar / reactivar)."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _post(self, doc, estado, username, **extra):
        self.client.login(username=username, password='testpass123')
        payload = {'estado': estado, 'motivo_seleccion': 'test'}
        payload.update(extra)
        return self.client.post(
            f'/documentos/api/{doc.tipo_id}/{doc.uuid}/cambiar-estado/',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_cambiar_estado_jefe_solicita_aprobacion(self):
        """El jefe del departamento no inhabilita directo: aprueba GG."""
        resp = self._post(self.doc_activo, 'obsoleto', 'jefe_ti')
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'activo')
        solicitud = SolicitudAprobacionDocumento.objects.get(
            documento=self.doc_activo, accion_solicitud='inhabilitar',
        )
        self.assertEqual(solicitud.estado, 'pendiente')
        self.assertEqual(solicitud.aprobador, self.user_jefe_gg)
        self.assertTrue(solicitud.es_final)

    def test_cambiar_estado_admin_inhabilita_directo(self):
        resp = self._post(self.doc_activo, 'obsoleto', 'admin01')
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'obsoleto')
        solicitud = SolicitudAprobacionDocumento.objects.get(
            documento=self.doc_activo, accion_solicitud='inhabilitar',
        )
        self.assertEqual(solicitud.estado, 'aprobada')

    def test_cambiar_estado_jefe_gg_inhabilita_directo(self):
        resp = self._post(self.doc_activo, 'obsoleto', 'jefe_gg')
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'obsoleto')

    def test_cambiar_estado_propietario_trabajador_solicita(self):
        """El propietario (trabajador) solicita la reactivación al jefe de su depto."""
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Formulario Obsoleto Trab',
            codigo='TI-FOR-099', estado='obsoleto',
            propietario=self.user_trabajador, departamento=self.depto_ti,
            clasificacion='interno', version_actual='1.0',
        )
        resp = self._post(doc, 'activo', 'trab01')
        self.assertEqual(resp.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.estado, 'obsoleto')
        solicitud = SolicitudAprobacionDocumento.objects.get(
            documento=doc, accion_solicitud='reactivar',
        )
        self.assertEqual(solicitud.estado, 'pendiente')
        self.assertEqual(solicitud.aprobador, self.user_jefe_ti)

    def test_cambiar_estado_sin_permiso_403(self):
        resp = self._post(self.doc_activo, 'obsoleto', 'trab01')
        self.assertEqual(resp.status_code, 403)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'activo')

    def test_cambiar_estado_reactivar_no_obsoleto_400(self):
        resp = self._post(self.doc_activo, 'activo', 'jefe_ti')
        self.assertEqual(resp.status_code, 400)

    def test_cambiar_estado_reactivar_vencido_400(self):
        """Con fecha vencida la reactivación se niega: hay que renovar."""
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Formulario Vencido',
            codigo='TI-FOR-098', estado='obsoleto',
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
            clasificacion='interno', version_actual='1.0',
            fecha_vencimiento=date.today() - timedelta(days=1),
        )
        resp = self._post(doc, 'activo', 'jefe_ti')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('vencimiento', resp.json()['mensaje'])

    def test_cambiar_estado_get_405(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/cambiar-estado/'
        )
        self.assertEqual(resp.status_code, 405)

    def test_cambiar_estado_json_invalido(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/cambiar-estado/',
            data='no json',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_cambiar_estado_invalido(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/cambiar-estado/',
            data=json.dumps({'estado': 'borrador'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)


# =============================================================================
# API: TRANSFERIR PROPIEDAD
# =============================================================================

class APITransferirTest(BaseDocumentosTest):
    """Tests del endpoint de transferencia de propiedad."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_transferir_ok(self):
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/transferir/',
            data=json.dumps({'nuevo_propietario_id': self.user_trabajador.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.propietario, self.user_trabajador)
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_trabajador, tipo='propiedad_transferida'
        ).exists())

    def test_transferir_mismo_propietario_400(self):
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/transferir/',
            data=json.dumps({'nuevo_propietario_id': self.user_jefe_ti.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.propietario, self.user_jefe_ti)

    def test_admin_transfiere_directo(self):
        self.client.logout()
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/transferir/',
            data=json.dumps({'nuevo_propietario_id': self.user_trabajador.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.propietario, self.user_trabajador)

    def test_transferir_sin_permiso(self):
        self.client.logout()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/transferir/',
            data=json.dumps({'nuevo_propietario_id': self.user_trabajador.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_transferir_sin_id(self):
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/transferir/',
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_transferir_usuario_inexistente(self):
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/transferir/',
            data=json.dumps({'nuevo_propietario_id': 99999}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)


# =============================================================================
# API: PROCEDIMIENTOS Y FORMULARIOS POR DEPARTAMENTO
# =============================================================================

class APIPorDepartamentoTest(BaseDocumentosTest):
    """Tests de endpoints de documentos por departamento."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_procedimientos_por_departamento(self):
        resp = self.client.get(f'/documentos/api/procedimientos/?departamento={self.depto_ti.id}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('documentos', data)

    def test_formularios_por_departamento(self):
        resp = self.client.get(f'/documentos/api/formularios/?departamento={self.depto_ti.id}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('documentos', data)

    def test_procedimientos_por_departamentos(self):
        resp = self.client.get('/documentos/api/procedimientos-por-departamentos/')
        self.assertEqual(resp.status_code, 200)

    def test_formularios_por_departamento_planilla(self):
        resp = self.client.get(f'/documentos/api/formularios-por-departamento-planilla/?departamento={self.depto_ti.id}')
        self.assertEqual(resp.status_code, 200)

    def test_siguiente_secuencia(self):
        resp = self.client.get(
            f'/documentos/api/siguiente-secuencia/?departamento_id={self.depto_ti.id}&tipo_codigo=PRG'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('secuencia', data)


# =============================================================================
# API: SOLICITUD DE ACCESO
# =============================================================================

class APISolicitarAccesoTest(BaseDocumentosTest):
    """Tests de solicitud y respuesta de acceso."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_solicitar_acceso(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            '/documentos/api/solicitar-acceso/',
            data=json.dumps({'documento_id': str(self.doc_th.uuid), 'motivo': 'Necesito acceso'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_solicitar_acceso_no_jefe(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            '/documentos/api/solicitar-acceso/',
            data=json.dumps({'documento_id': str(self.doc_th.uuid), 'motivo': 'test'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_solicitudes_pendientes(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/api/solicitudes-acceso-pendientes/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('solicitudes', data)


# =============================================================================
# API: APROBACIÓN DE CREACIÓN
# =============================================================================

class APIAprobarCreacionTest(BaseDocumentosTest):
    """Tests de aprobación/rechazo de creación de documentos."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.solicitud = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision,
            accion_solicitud='crear',
            solicitante=self.user_trabajador,
            propietario=self.user_trabajador,
            aprobador=self.user_jefe_ti,
            departamento_origen=self.depto_ti,
            estado='pendiente',
        )

    def test_aprobar_creacion(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_en_revision.uuid}/aprobar-creacion/',
            data=json.dumps({'observaciones': 'Aprobado'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_rechazar_creacion_con_motivo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_en_revision.uuid}/rechazar-creacion/',
            data=json.dumps({'motivo': 'No cumple estándares'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_rechazar_creacion_sin_motivo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_en_revision.uuid}/rechazar-creacion/',
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_aprobar_creacion_no_jefe(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_en_revision.uuid}/aprobar-creacion/',
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)

    def test_aprobar_creacion_no_existe(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/aprobar-creacion/',
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)


class APIAprobarCambioEstadoTest(BaseDocumentosTest):
    """Aprobación de inhabilitar / reactivar / renovar vía api."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _aprobar(self, doc, username, observaciones=''):
        self.client.login(username=username, password='testpass123')
        return self.client.post(
            f'/documentos/api/{doc.tipo_id}/{doc.uuid}/aprobar-creacion/',
            data=json.dumps({'observaciones': observaciones}),
            content_type='application/json',
        )

    def _solicitar(self, doc, accion, username):
        estado = 'obsoleto' if accion == 'inhabilitar' else 'activo'
        self.client.login(username=username, password='testpass123')
        self.client.post(
            f'/documentos/api/{doc.tipo_id}/{doc.uuid}/cambiar-estado/',
            data=json.dumps({'estado': estado, 'motivo_seleccion': 'prueba'}),
            content_type='application/json',
        )

    def test_aprobar_inhabilitar_marca_obsoleto(self):
        self._solicitar(self.doc_activo, 'inhabilitar', 'jefe_ti')
        resp = self._aprobar(self.doc_activo, 'jefe_gg')
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'obsoleto')
        notif = Notificacion.objects.get(
            documento=self.doc_activo, tipo='aprobacion_documento',
        )
        self.assertEqual(notif.titulo, 'Documento inhabilitado')
        self.assertIn('inhabilitado', notif.mensaje)

    def test_aprobar_reactivar(self):
        self._solicitar(self.doc_obsoleto, 'reactivar', 'jefe_ti')
        resp = self._aprobar(self.doc_obsoleto, 'jefe_gg')
        self.assertEqual(resp.status_code, 200)
        self.doc_obsoleto.refresh_from_db()
        self.assertEqual(self.doc_obsoleto.estado, 'activo')
        notif = Notificacion.objects.get(
            documento=self.doc_obsoleto, tipo='aprobacion_documento',
        )
        self.assertEqual(notif.titulo, 'Documento reactivado')

    def test_aprobar_renovar(self):
        self.client.login(username='jefe_ti', password='testpass123')
        fecha = (date.today() + timedelta(days=365)).isoformat()
        self.client.post(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/renovar-vencimiento/',
            data={'fecha_nueva': fecha},
        )
        resp = self._aprobar(self.doc_activo, 'jefe_gg')
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.fecha_vencimiento, date.today() + timedelta(days=365))
        self.assertEqual(self.doc_activo.estado, 'activo')
        notif = Notificacion.objects.get(
            documento=self.doc_activo, tipo='aprobacion_documento',
        )
        self.assertEqual(notif.titulo, 'Vencimiento renovado')
        self.assertIn(f'hasta el {date.today() + timedelta(days=365)}', notif.mensaje)

    def test_rechazar_inhabilitar_no_marca_obsoleto(self):
        self._solicitar(self.doc_activo, 'inhabilitar', 'jefe_ti')
        self.client.login(username='jefe_gg', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/rechazar-creacion/',
            data=json.dumps({'motivo': 'Se mantiene vigente'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'activo')
        notif = Notificacion.objects.get(
            documento=self.doc_activo, tipo='rechazo_documento',
        )
        self.assertEqual(notif.titulo, 'Solicitud de inhabilitación rechazada')

    def test_aprobar_reactivar_no_autocompleta_planilla_ajena(self):
        """La reactivación no escribe en la última SolicitudCambio del doc."""
        formato = Formato.objects.create(
            codigo='SGC-FOR-AUTO', nombre='Planilla Auto', aplica_modificacion=True,
        )
        seccion = SeccionFormato.objects.create(formato=formato, titulo='S1', orden=1)
        CampoFormato.objects.create(
            seccion=seccion, clave='resolucion', etiqueta='Resolución',
            tipo='texto', origen='resolucion', orden=1,
        )
        planilla = SolicitudCambio.objects.create(
            codigo='SC-AUTO-001', formato=formato, operacion='modificacion',
            documento=self.doc_obsoleto, solicitante=self.user_jefe_ti,
            departamento=self.depto_ti, estado='en_revision', respuestas={},
        )
        self._solicitar(self.doc_obsoleto, 'reactivar', 'jefe_ti')
        resp = self._aprobar(self.doc_obsoleto, 'jefe_gg')
        self.assertEqual(resp.status_code, 200)
        planilla.refresh_from_db()
        self.assertEqual(planilla.respuestas, {})

    def test_gate_planillas_de_otra_operacion(self):
        """Aprobar una inhabilitación no aprueba planillas de otra operación."""
        formato = Formato.objects.create(
            codigo='SGC-FOR-GATE', nombre='Planilla Gate', aplica_creacion=True,
        )
        planilla_eliminacion = SolicitudCambio.objects.create(
            codigo='SC-GATE-ELI', formato=formato, operacion='eliminacion',
            documento=self.doc_activo, solicitante=self.user_jefe_ti,
            departamento=self.depto_ti, estado='en_revision',
        )
        planilla_creacion = SolicitudCambio.objects.create(
            codigo='SC-GATE-CRE', formato=formato, operacion='creacion',
            documento=self.doc_activo, solicitante=self.user_jefe_ti,
            departamento=self.depto_ti, estado='en_revision',
        )
        self._solicitar(self.doc_activo, 'inhabilitar', 'jefe_ti')
        resp = self._aprobar(self.doc_activo, 'jefe_gg')
        self.assertEqual(resp.status_code, 200)
        planilla_eliminacion.refresh_from_db()
        planilla_creacion.refresh_from_db()
        self.assertEqual(planilla_eliminacion.estado, 'aprobado')
        # La de creación sigue en revisión: otra operación, otra aprobación.
        self.assertEqual(planilla_creacion.estado, 'en_revision')


# =============================================================================
# API: APROBACIÓN DE PLANILLAS
# =============================================================================

class APIAprobarPlanillaTest(BaseDocumentosTest):
    """Tests de aprobación/rechazo de planillas via API."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.planilla = PlanillaCambios.objects.create(
            codigo='PC-TI-API-001',
            documento=self.doc_activo,
            tipo_cambio='modificacion',
            justificacion='Cambio de prueba',
            responsable=self.user_jefe_ti,
            version_destino='2.0',
            estado='en_revision',
        )
        from documentos.models import AprobacionPlanilla
        self.aprobacion = AprobacionPlanilla.objects.create(
            planilla=self.planilla,
            aprobador=self.user_jefe_ti,
            estado='pendiente',
        )

    def test_aprobar_planilla(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/planilla/{self.planilla.uuid}/aprobar/',
            data=json.dumps({'observaciones': 'Aprobado'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.planilla.refresh_from_db()
        self.assertEqual(self.planilla.estado, 'aprobado')

    def test_aprobar_planilla_copia_afecta_todos(self):
        self.planilla.afecta_todos = True
        self.planilla.save(update_fields=['afecta_todos'])
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/planilla/{self.planilla.uuid}/aprobar/',
            data=json.dumps({'observaciones': 'ok'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertTrue(self.doc_activo.afecta_todos)

    def test_rechazar_planilla_con_motivo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/planilla/{self.planilla.uuid}/rechazar/',
            data=json.dumps({'motivo': 'No aplica'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

    def test_rechazar_planilla_sin_motivo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/planilla/{self.planilla.uuid}/rechazar/',
            data=json.dumps({}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_planillas_pendientes(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/api/planillas-pendientes/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('planillas', data)


# =============================================================================
# VISTAS: EDITAR / ELIMINAR DOCUMENTO
# =============================================================================

class VistasEditarEliminarTest(BaseDocumentosTest):
    """Tests de vistas de edición y eliminación."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_editar_documento_propietario(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/editar/'
        )
        self.assertEqual(resp.status_code, 302)

    def test_editar_documento_sin_permiso(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/editar/'
        )
        self.assertEqual(resp.status_code, 302)

    def test_eliminar_documento_get(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/eliminar/'
        )
        self.assertIn(resp.status_code, [200, 302])


# =============================================================================
# VISTAS: SOLICITAR Y APROBAR DESCARGA
# =============================================================================

class VistasDescargaTest(BaseDocumentosTest):
    """Tests de solicitar y aprobar descarga."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_descargar_sin_archivo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/descargar/'
        )
        self.assertEqual(resp.status_code, 302)

    def test_solicitar_descarga(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/solicitar-descarga/',
            data={'motivo': 'Necesito el archivo'},
        )
        self.assertEqual(resp.status_code, 302)


# =============================================================================
# VISTAS: VERSIONES
# =============================================================================

class VistasVersionesTest(BaseDocumentosTest):
    """Tests de historial y subida de versiones."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_historial_versiones(self):
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/versiones/'
        )
        self.assertEqual(resp.status_code, 200)

    def _version(self, numero, documento=None, nombre='v.pdf', creado_por=None):
        """Crea una versión activa; los tests de borrado necesitan 2."""
        return VersionDocumento.objects.create(
            documento=documento or self.doc_activo,
            numero_version=numero,
            archivo=SimpleUploadedFile(nombre, b'c'),
            creado_por=creado_por or self.user_jefe_ti,
        )

    def test_eliminar_version(self):
        v1 = self._version('1.0', nombre='v1.pdf')
        self._version('1.1', nombre='v2.pdf')
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/eliminar-version/{v1.id}/',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(VersionDocumento.objects.filter(id=v1.id, activa=True).exists())

    # --- ISO: la eliminación es directa y solo propietario/admin ------------
    def test_eliminar_version_jefe_del_depto_no_propietario_denegado(self):
        """El jefe del departamento NO elimina versiones si no es propietario."""
        version = self._version('1.0', documento=self.doc_th,
                                creado_por=self.user_trabajador_otro)
        self._version('1.1', documento=self.doc_th,
                      creado_por=self.user_trabajador_otro)
        self.client.login(username='jefe_th', password='testpass123')
        resp = self.client.post(
            f'/documentos/{self.doc_th.tipo_id}/{self.doc_th.uuid}/eliminar-version/{version.id}/',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(VersionDocumento.objects.filter(id=version.id, activa=True).exists())

    def test_eliminar_version_propietario_no_creador_permitido(self):
        """El propietario elimina aunque no haya creado esa versión."""
        version = self._version('1.0', creado_por=self.user_trabajador)
        self._version('1.1', creado_por=self.user_trabajador)
        self.client.login(username='jefe_ti', password='testpass123')  # propietario
        resp = self.client.post(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/eliminar-version/{version.id}/',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(VersionDocumento.objects.filter(id=version.id, activa=True).exists())

    def test_eliminar_version_admin_permitido(self):
        version = self._version('1.0', documento=self.doc_th,
                                creado_por=self.user_trabajador_otro)
        self._version('1.1', documento=self.doc_th,
                      creado_por=self.user_trabajador_otro)
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(
            f'/documentos/{self.doc_th.tipo_id}/{self.doc_th.uuid}/eliminar-version/{version.id}/',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(VersionDocumento.objects.filter(id=version.id, activa=True).exists())

    def test_eliminar_version_trabajador_denegado(self):
        version = self._version('1.0', creado_por=self.user_trabajador)
        self._version('1.1', creado_por=self.user_trabajador)
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/eliminar-version/{version.id}/',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(VersionDocumento.objects.filter(id=version.id, activa=True).exists())

    def test_eliminar_version_directa_registra_aprobada(self):
        """Sin cadena de aprobación: queda solo el registro de auditoría."""
        version = self._version('1.0', creado_por=self.user_trabajador)
        self._version('1.1', creado_por=self.user_trabajador)
        self.client.login(username='jefe_ti', password='testpass123')
        self.client.post(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/eliminar-version/{version.id}/',
        )
        solicitudes = SolicitudAprobacionDocumento.objects.filter(
            documento=self.doc_activo, accion_solicitud='eliminar_version',
        )
        self.assertEqual(solicitudes.count(), 1)
        solicitud = solicitudes.get()
        self.assertEqual(solicitud.estado, 'aprobada')
        self.assertEqual(solicitud.es_final, True)

    def test_detalle_solo_versiones_activas(self):
        """La pestaña del detalle no muestra versiones eliminadas."""
        activa = VersionDocumento.objects.create(
            documento=self.doc_activo, numero_version='1.0',
            archivo=SimpleUploadedFile('v.pdf', b'c'), creado_por=self.user_jefe_ti,
        )
        borrada = VersionDocumento.objects.create(
            documento=self.doc_activo, numero_version='0.9',
            archivo=SimpleUploadedFile('v0.pdf', b'c'), creado_por=self.user_jefe_ti,
        )
        VersionDocumento.objects.filter(id=borrada.id).update(activa=False)
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(activa, resp.context['versiones'])
        self.assertNotIn(borrada, resp.context['versiones'])
        self.assertTrue(resp.context['puede_eliminar_versiones'])

    # --- Regla: un documento siempre conserva al menos una versión ----------
    def test_eliminar_unica_version_bloqueada(self):
        """Con una sola versión activa no se puede eliminar (retención ISO)."""
        version = self._version('1.0')
        resp = self.client.post(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/eliminar-version/{version.id}/',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(VersionDocumento.objects.filter(id=version.id, activa=True).exists())
        # El intento ni siquiera deja rastro de "aprobación".
        self.assertFalse(
            SolicitudAprobacionDocumento.objects.filter(
                documento=self.doc_activo, accion_solicitud='eliminar_version',
            ).exists()
        )

    def test_eliminar_version_actual_restaura_la_anterior(self):
        """Al borrar la versión actual con 2+, pasa a serlo la anterior."""
        self.doc_activo.version_actual = '1.1'
        self.doc_activo.save(update_fields=['version_actual'])
        v1 = self._version('1.0')
        v2 = self._version('1.1')
        self.client.post(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/eliminar-version/{v2.id}/',
        )
        self.doc_activo.refresh_from_db()
        self.assertFalse(VersionDocumento.objects.filter(id=v2.id, activa=True).exists())
        self.assertTrue(VersionDocumento.objects.filter(id=v1.id, activa=True).exists())
        self.assertEqual(self.doc_activo.version_actual, '1.0')

    def test_modelo_no_deja_documento_sin_versiones(self):
        """`aplicar_eliminacion_version` es la red de seguridad final."""
        version = self._version('1.0')
        solicitud = SolicitudAprobacionDocumento(
            documento=self.doc_activo, accion_solicitud='eliminar_version',
            version_a_eliminar=version, solicitante=self.user_jefe_ti,
            propietario=self.doc_activo.propietario, aprobador=self.user_jefe_ti,
            departamento_origen=self.doc_activo.departamento,
            rol_aprobador='autorizador', es_final=True, estado='aprobada',
        )
        solicitud.save()
        solicitud.aplicar_eliminacion_version(self.doc_activo, version)
        self.assertTrue(VersionDocumento.objects.filter(id=version.id, activa=True).exists())

    def test_boton_oculto_historial_con_una_version(self):
        version = self._version('1.0')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/versiones/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['versiones_eliminables'])
        self.assertNotContains(resp, f'/eliminar-version/{version.id}/')

    def test_boton_oculto_detalle_con_una_version(self):
        version = self._version('1.0')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['versiones_eliminables'])
        self.assertNotContains(resp, f'/eliminar-version/{version.id}/')

    def test_boton_visible_con_dos_versiones(self):
        v1 = self._version('1.0')
        self._version('1.1')
        base = f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}'
        for url in (f'{base}/versiones/', f'{base}/'):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.context['versiones_eliminables'])
            self.assertContains(resp, f'/eliminar-version/{v1.id}/')


# =============================================================================
# VISTAS: RENOVACIÓN DE VENCIMIENTO
# =============================================================================

class VistasRenovarTest(BaseDocumentosTest):
    """Tests de renovación de vencimiento (ISO 9001:7.5.3.2)."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def _post(self, doc, username=None, **extra):
        if username:
            self.client.login(username=username, password='testpass123')
        payload = {'fecha_nueva': (date.today() + timedelta(days=400)).isoformat()}
        payload.update(extra)
        return self.client.post(
            f'/documentos/{doc.tipo_id}/{doc.uuid}/renovar-vencimiento/',
            data=payload,
        )

    def test_renovar_vencimiento_jefe_solicita_aprobacion(self):
        """El jefe del departamento no renueva directo: aprueba GG."""
        fecha_anterior = self.doc_activo.fecha_vencimiento
        resp = self._post(self.doc_activo)
        self.assertEqual(resp.status_code, 302)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.fecha_vencimiento, fecha_anterior)
        solicitud = SolicitudAprobacionDocumento.objects.get(
            documento=self.doc_activo, accion_solicitud='renovar',
        )
        self.assertEqual(solicitud.estado, 'pendiente')
        self.assertEqual(solicitud.aprobador, self.user_jefe_gg)
        self.assertIsNotNone(solicitud.fecha_propuesta)

    def test_renovar_vencimiento_admin_directo(self):
        resp = self._post(self.doc_activo, 'admin01')
        self.assertEqual(resp.status_code, 302)
        self.doc_activo.refresh_from_db()
        self.assertEqual(
            self.doc_activo.fecha_vencimiento, date.today() + timedelta(days=400),
        )
        renovacion = RenovacionVencimiento.objects.get(documento=self.doc_activo)
        self.assertEqual(renovacion.renovado_por, self.user_admin)
        solicitud = SolicitudAprobacionDocumento.objects.get(
            documento=self.doc_activo, accion_solicitud='renovar',
        )
        self.assertEqual(solicitud.estado, 'aprobada')

    def test_renovar_obsoleto_reactiva_directo(self):
        resp = self._post(self.doc_obsoleto, 'admin01')
        self.assertEqual(resp.status_code, 302)
        self.doc_obsoleto.refresh_from_db()
        self.assertEqual(self.doc_obsoleto.estado, 'activo')

    def test_renovar_obsoleto_reactiva(self):
        resp = self._post(self.doc_obsoleto)
        self.assertEqual(resp.status_code, 302)
        self.doc_obsoleto.refresh_from_db()
        # Queda pendiente: el estado no se anticipa.
        self.assertEqual(self.doc_obsoleto.estado, 'obsoleto')
        solicitud = SolicitudAprobacionDocumento.objects.get(
            documento=self.doc_obsoleto, accion_solicitud='renovar',
        )
        self.assertEqual(solicitud.estado, 'pendiente')

    def test_renovar_sin_fecha_no_tramita(self):
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/renovar-vencimiento/',
            data={},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            SolicitudAprobacionDocumento.objects.filter(
                documento=self.doc_activo, accion_solicitud='renovar',
            ).exists(),
        )

    def test_renovar_propietario_trabajador_solicita(self):
        """El propietario (trabajador) solicita la renovación al jefe del depto."""
        resp = self._post(self.doc_th, 'trab02')
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            SolicitudAprobacionDocumento.objects.filter(
                documento=self.doc_th, accion_solicitud='renovar',
                estado='pendiente', aprobador=self.user_jefe_th,
            ).exists(),
        )
        self.doc_th.refresh_from_db()
        self.assertIsNone(self.doc_th.fecha_vencimiento)

    def test_renovar_sin_permiso_no_tramita(self):
        resp = self._post(self.doc_th, 'trab01')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            SolicitudAprobacionDocumento.objects.filter(
                documento=self.doc_th, accion_solicitud='renovar',
            ).exists(),
        )


class VistasEtiquetasCambioEstadoTest(BaseDocumentosTest):
    """Etiquetas y regla de fecha vencida en la ficha del documento."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_obsoleto_vencido_ofrece_renovar_no_habilitar(self):
        """Con fecha vencida no se ofrece 'Habilitar': hay que renovar."""
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Obsoleto Vencido',
            codigo='TI-FOR-097', estado='obsoleto',
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
            clasificacion='interno', version_actual='1.0',
            fecha_vencimiento=date.today() - timedelta(days=1),
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(f'/documentos/{doc.tipo_id}/{doc.uuid}/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['vencimiento_vencido'])
        self.assertContains(resp, 'Solicitar Reactivación / Renovación')
        self.assertNotContains(resp, 'onclick="cambiarEstadoDocumento')

    def test_obsoleto_sin_fecha_ofrece_reactivar_directo(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_obsoleto.tipo_id}/{self.doc_obsoleto.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['vencimiento_vencido'])
        self.assertTrue(resp.context['puede_inhabilitar_directo'])
        self.assertTrue(resp.context['puede_inhabilitar'])
        self.assertContains(resp, 'onclick="cambiarEstadoDocumento')

    def test_jefe_ve_etiquetas_de_solicitud(self):
        """El jefe del departamento ve 'Solicitar...' (no 'Inhabilitar')."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['puede_inhabilitar_directo'])
        self.assertTrue(resp.context['puede_inhabilitar'])
        self.assertTrue(resp.context['puede_eliminar'])
        self.assertContains(resp, 'Solicitar Inhabilitación')

    def test_reactivacion_pendiente_muestra_aviso_no_boton(self):
        """Con una reactivación ya solicitada se ofrece el aviso, no el botón.

        El API devuelve 409 "duplicado" si se vuelve a pedir; la ficha debe
        reflejarlo en vez de invitar a repetir la solicitud.
        """
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Obsoleto Requiere Reactivar',
            codigo='TI-FOR-098', estado='obsoleto',
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
            clasificacion='interno', version_actual='1.0',
            fecha_vencimiento=date.today() + timedelta(days=30),
        )
        SolicitudAprobacionDocumento.objects.create(
            documento=doc, accion_solicitud='reactivar',
            solicitante=self.user_jefe_ti, aprobador=self.user_jefe_gg,
            rol_aprobador='autorizador', estado='pendiente', es_final=True,
            departamento_origen=self.depto_ti,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(f'/documentos/{doc.tipo_id}/{doc.uuid}/')
        self.assertEqual(resp.status_code, 200)

        self.assertIsNotNone(resp.context['reactivacion_pendiente'])
        self.assertContains(
            resp, 'Reactivación solicitada — pendiente de aprobación'
        )
        # El botón de solicitud desaparece para quien no aplica directo.
        self.assertNotContains(resp, 'onclick="cambiarEstadoDocumento')

    def test_admin_conserva_habilitar_directo_con_reactivacion_pendiente(self):
        """El administrador (aplicación directa) no se ve bloqueado por el aviso."""
        SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_obsoleto, accion_solicitud='reactivar',
            solicitante=self.user_jefe_ti, aprobador=self.user_jefe_gg,
            rol_aprobador='autorizador', estado='pendiente', es_final=True,
            departamento_origen=self.depto_ti,
        )
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_obsoleto.tipo_id}/{self.doc_obsoleto.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['puede_inhabilitar_directo'])
        self.assertContains(resp, 'onclick="cambiarEstadoDocumento')

    def test_flujo_aprobacion_no_mezcla_acciones_anteriores(self):
        """La cadena ya aprobada de una acción no se mezcla con la actual.

        Antes se tomaban las 2 últimas solicitudes sin filtrar por acción:
        una reactivación pendiente se mostraba junto a la autorización
        aprobada de la creación, apareciendo dos filas "Autorización".
        """
        doc = self.doc_obsoleto
        SolicitudAprobacionDocumento.objects.create(
            documento=doc, accion_solicitud='creacion',
            solicitante=self.user_trabajador, aprobador=self.user_jefe_ti,
            rol_aprobador='evaluador', estado='aprobada', es_final=False,
            departamento_origen=self.depto_ti,
        )
        SolicitudAprobacionDocumento.objects.create(
            documento=doc, accion_solicitud='creacion',
            solicitante=self.user_trabajador, aprobador=self.user_jefe_gg,
            rol_aprobador='autorizador', estado='aprobada', es_final=True,
            departamento_origen=self.depto_ti,
        )
        SolicitudAprobacionDocumento.objects.create(
            documento=doc, accion_solicitud='reactivar',
            solicitante=self.user_jefe_ti, aprobador=self.user_jefe_gg,
            rol_aprobador='autorizador', estado='pendiente', es_final=True,
            departamento_origen=self.depto_ti,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(f'/documentos/{doc.tipo_id}/{doc.uuid}/')
        self.assertEqual(resp.status_code, 200)

        flujo = list(resp.context['aprobaciones_actuales'])
        self.assertEqual(len(flujo), 1)
        self.assertEqual(flujo[0].accion_solicitud, 'reactivar')
        self.assertEqual(flujo[0].estado, 'pendiente')


# =============================================================================
# VERIFICACIÓN: AUDITORÍA, NOTIFICACIÓN Y PLANILLA
# =============================================================================

class VerificacionCambioEstadoTest(BaseDocumentosTest):
    """Cierra el hueco de verificación del flujo de cambio de estado.

    Comprueba tres cosas que ningún otro test cubría: la evidencia ISO
    (``registrar_log_iso``), la notificación al aprobador cuando la
    solicitud queda pendiente y la planilla de inhabilitación encolada
    en el camino directo. ``registrar_log_iso`` se parchea a nivel de
    ``auditoria.services`` porque los flujos lo importan en local.
    """

    def setUp(self):
        super().setUp()
        self.client = Client()

    # --- Auditoría -------------------------------------------------------

    @patch('auditoria.services.registrar_log_iso')
    def test_renovar_directo_deja_evidencia_iso(self, mock_log):
        """La renovación aplicada directamente deja registro ISO con IP."""
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(
            f'/documentos/{self.doc_activo.tipo_id}/'
            f'{self.doc_activo.uuid}/renovar-vencimiento/',
            {'fecha_nueva': (date.today() + timedelta(days=400)).isoformat()},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(mock_log.called)
        kwargs = [c.kwargs for c in mock_log.call_args_list if c.kwargs]
        renovacion = [a for a in kwargs if a.get('accion_tipo') == 'renovacion']
        self.assertTrue(renovacion, 'No se registró la auditoría de renovación')
        self.assertEqual(renovacion[0]['accion_categoria'], 'vencimiento')
        self.assertTrue(renovacion[0]['resultado_exitoso'])
        self.assertEqual(renovacion[0]['usuario'], self.user_admin)
        self.assertIsNotNone(renovacion[0]['ip_address'])
        # El cliente de prueba no envía HTTP_USER_AGENT: solo exige la clave.
        self.assertIn('user_agent', renovacion[0])

    @patch('auditoria.services.registrar_log_iso')
    def test_aprobar_inhabilitar_deja_evidencia_iso(self, mock_log):
        """La aprobación de una inhabilitación también deja registro ISO."""
        self.client.login(username='jefe_ti', password='testpass123')
        self.client.post(
            f'/documentos/api/{self.doc_activo.tipo_id}/'
            f'{self.doc_activo.uuid}/cambiar-estado/',
            data=json.dumps({'estado': 'obsoleto', 'motivo_seleccion': 'prueba'}),
            content_type='application/json',
        )
        mock_log.reset_mock()
        self.client.login(username='jefe_gg', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/{self.doc_activo.tipo_id}/'
            f'{self.doc_activo.uuid}/aprobar-creacion/',
            data=json.dumps({'observaciones': 'aprobado'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        kwargs = [c.kwargs for c in mock_log.call_args_list if c.kwargs]
        aprobacion = [a for a in kwargs if a.get('accion_tipo') == 'aprobacion']
        self.assertTrue(aprobacion, 'No se registró la auditoría de aprobación')
        self.assertEqual(aprobacion[0]['accion_categoria'], 'documento')
        self.assertEqual(aprobacion[0]['usuario'], self.user_jefe_gg)
        self.assertEqual(aprobacion[0]['detalles']['accion_solicitud'], 'inhabilitar')

    # --- Notificación ----------------------------------------------------

    def test_solicitar_inhabilitar_notifica_al_aprobador(self):
        """La solicitud pendiente avisa al aprobador por notificación."""
        from documentos.services.cambios_estado import solicitar_cambio_estado

        resultado = solicitar_cambio_estado(
            self.doc_activo, self.user_jefe_ti, 'inhabilitar',
        )
        self.assertFalse(resultado.aplicado)
        self.assertEqual(resultado.codigo, 'solicitado')
        self.assertEqual(resultado.aprobador, self.user_jefe_gg)
        notif = Notificacion.objects.get(
            usuario=self.user_jefe_gg, tipo='solicitud_aprobacion',
        )
        self.assertIn('inhabilitación', notif.titulo)
        self.assertIn(self.doc_activo.codigo, notif.mensaje)

    # --- Planilla en el camino directo -----------------------------------

    @patch('documentos.services.planillas.encolar_generacion_planilla')
    def test_directo_encola_planilla_de_inhabilitacion(self, mock_encolar):
        """El camino directo aprueba y encola la planilla SGC-FOR-001."""
        formato = Formato.objects.create(
            codigo='SGC-FOR-005-TEST', nombre='Planilla de inhabilitación',
            aplica_creacion=False, aplica_modificacion=False,
            aplica_eliminacion=True,
        )
        sol_cambio = SolicitudCambio.objects.create(
            codigo='SC-VERIF-001', formato=formato, operacion='eliminacion',
            documento=self.doc_activo, solicitante=self.user_admin,
            departamento=self.depto_ti, estado='en_revision',
        )
        from documentos.services.cambios_estado import solicitar_cambio_estado

        resultado = solicitar_cambio_estado(
            self.doc_activo, self.user_admin, 'inhabilitar',
            solicitud_cambio=sol_cambio,
        )
        self.assertTrue(resultado.aplicado)
        self.assertEqual(resultado.codigo, 'ok')
        sol_cambio.refresh_from_db()
        self.assertEqual(sol_cambio.estado, 'aprobado')
        mock_encolar.assert_called_once_with(sol_cambio)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'obsoleto')


# =============================================================================
# VISTAS: DISTRIBUCIÓN
# =============================================================================

class VistasDistribucionTest(BaseDocumentosTest):
    """Tests de distribución de documentos."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_distribuir_documento_get(self):
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/distribuir/'
        )
        self.assertIn(resp.status_code, [200, 302])

    def test_distribuir_documento_post(self):
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/distribuir/',
            data={
                'usuarios': f'{self.user_trabajador.id},{self.user_trabajador_otro.id}',
                'metodo': 'sistema',
                'observaciones': 'Distribución de prueba',
            },
        )
        self.assertEqual(resp.status_code, 302)
        from documentos.models import DistribucionDocumento
        self.assertEqual(
            DistribucionDocumento.objects.filter(
                documento=self.doc_activo,
                usuario__in=[self.user_trabajador, self.user_trabajador_otro],
            ).count(),
            2,
        )

    def test_distribuir_sin_destinatarios(self):
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/distribuir/',
            data={'usuarios': '', 'metodo': 'sistema'},
        )
        self.assertEqual(resp.status_code, 302)

    def test_historial_distribucion(self):
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/historial-distribucion/'
        )
        self.assertEqual(resp.status_code, 200)


# =============================================================================
# VISTAS: TRANSFERIR PROPIEDAD
# =============================================================================

class VistasTransferirTest(BaseDocumentosTest):
    """Tests de transferencia de propiedad via vista."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_transferir_propiedad_get(self):
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/transferir/'
        )
        self.assertIn(resp.status_code, [200, 302])

    def test_transferir_propiedad_post(self):
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/transferir/',
            data={'nuevo_propietario': self.user_trabajador.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.propietario, self.user_trabajador)
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_trabajador, tipo='propiedad_transferida'
        ).exists())

    def test_transferir_mismo_propietario_error(self):
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/transferir/',
            data={'nuevo_propietario': self.user_jefe_ti.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.propietario, self.user_jefe_ti)

    def test_transferir_excluye_propietario_actual(self):
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/transferir/'
        )
        self.assertEqual(resp.status_code, 200)
        ids = [p.usuario_id for p in resp.context['miembros_transferibles']]
        self.assertNotIn(self.user_jefe_ti.id, ids)

    def test_admin_transfiere_directo_web(self):
        self.client.logout()
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/transferir/',
            data={'nuevo_propietario': self.user_trabajador.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.propietario, self.user_trabajador)


# =============================================================================
# VISTAS: PLANILLAS DE CAMBIOS
# =============================================================================

class VistasPlanillaTest(BaseDocumentosTest):
    """Tests de vistas de planilla de cambios."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.planilla = PlanillaCambios.objects.create(
            codigo='PC-TI-VIEW-001',
            documento=self.doc_activo,
            tipo_cambio='modificacion',
            justificacion='Test planilla vista',
            responsable=self.user_jefe_ti,
            version_destino='2.0',
            estado='borrador',
        )

    def test_detalle_planilla(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/planilla-cambios/{self.planilla.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)

    def test_enviar_planilla(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/planilla-cambios/{self.planilla.uuid}/enviar/',
        )
        self.assertEqual(resp.status_code, 302)

    def test_aprobar_planilla_vista(self):
        from documentos.models import AprobacionPlanilla
        self.planilla.estado = 'en_revision'
        self.planilla.save(update_fields=['estado'])
        AprobacionPlanilla.objects.create(
            planilla=self.planilla,
            aprobador=self.user_jefe_ti,
            estado='pendiente',
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/planilla-cambios/{self.planilla.uuid}/aprobar/',
            data={'accion': 'aprobar'},
        )
        self.assertEqual(resp.status_code, 302)

    # --- retomar un borrador de planilla ------------------------------------
    def _url_retomar(self, extra=''):
        return (
            '/documentos/planilla-cambios/nueva/'
            f'?tipo={self.doc_activo.tipo_id}&doc_uuid={self.doc_activo.uuid}'
            f'&planilla_uuid={self.planilla.uuid}{extra}'
        )

    def test_retomar_borrador_get(self):
        """El formulario se abre en modo edición con la planilla cargada."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self._url_retomar())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['planilla_existente'], self.planilla)
        self.assertEqual(resp.context['form'].instance.pk, self.planilla.pk)
        self.assertContains(resp, str(self.planilla.uuid))

    def test_retomar_borrador_get_sin_tipo_y_doc(self):
        """Los parámetros tipo/doc_uuid se derivan de la propia planilla."""
        self.client.login(username='jefe_ti', password='testpass123')
        url = f'/documentos/planilla-cambios/nueva/?planilla_uuid={self.planilla.uuid}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['planilla_existente'], self.planilla)

    def test_retomar_borrador_post_no_duplica(self):
        """El POST conserva el id y el código: no crea una segunda planilla."""
        self.client.login(username='jefe_ti', password='testpass123')
        antes = PlanillaCambios.objects.count()
        resp = self.client.post(self._url_retomar(), {
            'tipo_documento': self.doc_activo.tipo_id,
            'documento_uuid': str(self.doc_activo.uuid),
            'planilla_uuid': str(self.planilla.uuid),
            'nombre_propuesto': 'Nombre retomado',
            'descripcion_propuesta': 'Descripción retomada',
            'clasificacion_propuesta': '',
            'departamentos_afectados': str(self.depto_ti.id),
            'accion': 'borrador',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(PlanillaCambios.objects.count(), antes)
        self.planilla.refresh_from_db()
        self.assertEqual(self.planilla.codigo, 'PC-TI-VIEW-001')
        self.assertEqual(self.planilla.estado, 'borrador')
        self.assertEqual(self.planilla.nombre_propuesto, 'Nombre retomado')
        self.assertEqual(self.planilla.responsable_id, self.user_jefe_ti.id)

    def test_retomar_borrador_ajeno_denegado(self):
        """Solo el responsable puede retomar su borrador."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(self._url_retomar())
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/documentos/mis-documentos/', resp['Location'])

    def test_retomar_planilla_no_borrador_denegado(self):
        """Una planilla enviada a revisión ya no se puede retomar."""
        PlanillaCambios.objects.filter(pk=self.planilla.pk).update(estado='en_revision')
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self._url_retomar())
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/documentos/mis-documentos/', resp['Location'])

    def test_detalle_borrador_muestra_continuar_edicion(self):
        """El detalle del borrador ofrece 'Continuar edición'."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(f'/documentos/planilla-cambios/{self.planilla.uuid}/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Continuar edición')
        self.assertContains(resp, 'planilla_uuid=')

    def test_detalle_borrador_oculto_para_otro_usuario(self):
        """El botón no aparece para quien no es el responsable."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(f'/documentos/planilla-cambios/{self.planilla.uuid}/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Continuar edición')

    def test_ficha_enlaza_borrador_de_planilla(self):
        """La ficha del documento enlaza el borrador en vez de crear otro."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Planilla de Cambios (borrador)')
        self.assertContains(
            resp, f'/documentos/planilla-cambios/{self.planilla.uuid}/',
        )

    def test_ficha_sin_borrador_mantiene_boton_normal(self):
        """Sin borrador propio, la ficha sigue enlazando la creación."""
        PlanillaCambios.objects.filter(pk=self.planilla.pk).delete()
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Planilla de Cambios')
        self.assertNotContains(resp, 'Planilla de Cambios (borrador)')

    def test_mis_documentos_enlaza_borrador_de_planilla(self):
        """Mis Documentos enlaza el borrador para retomarlo."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/mis-documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Planilla de Cambios (borrador)')


# =============================================================================
# VISTAS: CONFIGURACIÓN (CRUD TIPOS DE DOCUMENTO)
# =============================================================================

class VistasTiposDocumentoTest(BaseDocumentosTest):
    """Tests de CRUD de tipos de documento."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='admin01', password='testpass123')

    def test_tipos_documento_lista(self):
        resp = self.client.get('/documentos/configuracion/tipos-documento/')
        self.assertEqual(resp.status_code, 200)

    def test_tipo_documento_crear_get(self):
        resp = self.client.get('/documentos/configuracion/tipos-documento/nuevo/')
        self.assertEqual(resp.status_code, 200)

    def test_tipo_documento_editar(self):
        resp = self.client.get(
            f'/documentos/configuracion/tipos-documento/{self.tipo_procedimiento.id}/editar/'
        )
        self.assertEqual(resp.status_code, 200)

    def test_tipo_documento_eliminar(self):
        resp = self.client.post(
            f'/documentos/configuracion/tipos-documento/{self.tipo_formulario.id}/eliminar/',
        )
        self.assertEqual(resp.status_code, 302)
        self.tipo_formulario.refresh_from_db()
        self.assertFalse(self.tipo_formulario.activo)

    def test_tipo_documento_reactivar(self):
        self.tipo_formulario.activo = False
        self.tipo_formulario.save(update_fields=['activo'])
        resp = self.client.post(
            f'/documentos/configuracion/tipos-documento/{self.tipo_formulario.id}/reactivar/',
        )
        self.assertEqual(resp.status_code, 302)
        self.tipo_formulario.refresh_from_db()
        self.assertTrue(self.tipo_formulario.activo)


# =============================================================================
# VISTAS: CONFIGURACIÓN (AUDITORÍA)
# =============================================================================

class VistasAuditoriaTest(BaseDocumentosTest):
    """Tests de vistas de auditoría."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='admin01', password='testpass123')

    def test_auditoria_lista(self):
        resp = self.client.get('/documentos/configuracion/auditoria/')
        self.assertEqual(resp.status_code, 200)

    def test_auditoria_detalle(self):
        resp = self.client.get('/documentos/configuracion/auditoria/LOG-2026-01-01-0001/')
        self.assertIn(resp.status_code, [200, 302])

    def test_auditoria_reporte_pdf(self):
        resp = self.client.get('/documentos/configuracion/auditoria/reporte-pdf/')
        self.assertIn(resp.status_code, [200, 302])


# =============================================================================
# VISTAS: CONFIGURACIÓN (DEPARTAMENTOS)
# =============================================================================

class VistasDepartamentosTest(BaseDocumentosTest):
    """Tests de CRUD de departamentos."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='admin01', password='testpass123')

    def test_departamentos_lista(self):
        resp = self.client.get('/documentos/configuracion/departamentos/')
        self.assertEqual(resp.status_code, 200)

    def test_departamento_crear_get(self):
        resp = self.client.get('/documentos/configuracion/departamentos/nuevo/')
        self.assertEqual(resp.status_code, 200)

    def test_departamento_editar(self):
        resp = self.client.get(
            f'/documentos/configuracion/departamentos/{self.depto_ti.id}/editar/'
        )
        self.assertEqual(resp.status_code, 200)

    def test_departamento_form_usa_form_card(self):
        """El formulario usa la tarjeta .form-card y el checkbox .form-checkbox."""
        for url in (
            '/documentos/configuracion/departamentos/nuevo/',
            f'/documentos/configuracion/departamentos/{self.depto_ti.id}/editar/',
        ):
            resp = self.client.get(url)
            self.assertContains(resp, 'class="form-card"')
            self.assertContains(resp, 'form-card-footer')
            self.assertContains(resp, 'form-checkbox')

    def test_departamento_form_sin_volver_redundante(self):
        """El encabezado ya no incluye el enlace 'Volver' redundante."""
        resp = self.client.get('/documentos/configuracion/departamentos/nuevo/')
        self.assertContains(resp, 'class="form-card-footer"')
        self.assertNotContains(resp, 'fa-arrow-left')

    def test_departamento_confirmar_usa_btn_danger(self):
        """La confirmación de eliminación usa el botón .btn--danger."""
        resp = self.client.get(
            f'/documentos/configuracion/departamentos/{self.depto_ti.id}/eliminar/'
        )
        self.assertContains(resp, 'btn--danger')
        self.assertNotContains(resp, 'fa-arrow-left')


# =============================================================================
# VISTAS: OTRAS CONFIGURACIONES
# =============================================================================

class VistasOtrasConfigTest(BaseDocumentosTest):
    """Tests de otras vistas de configuración."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='admin01', password='testpass123')

    def test_ciclo_vida(self):
        resp = self.client.get('/documentos/configuracion/ciclo-vida/')
        self.assertEqual(resp.status_code, 200)

    def test_roles_permisos(self):
        resp = self.client.get('/documentos/configuracion/roles-permisos/')
        self.assertEqual(resp.status_code, 200)
        # Encabezado y tarjeta de detalle estilizados por page-shell.css
        self.assertContains(resp, 'class="page-header"')
        self.assertContains(resp, 'class="detail-card"')

    def test_seleccionar_tipo(self):
        resp = self.client.get('/documentos/seleccionar-tipo/')
        self.assertEqual(resp.status_code, 200)


# =============================================================================
# VISTAS: VISIBILIDAD DE BOTONES EN DETALLE POR PERMISOS
# =============================================================================

class DetalleVisibilidadBotonesTest(BaseDocumentosTest):
    """Tests de visibilidad de botones en detalle según permisos dept-scoped."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_jefe_otro_depto_no_ve_botones_edicion(self):
        """Jefe de otro depto no ve botones de acción sobre doc ajeno (público)."""
        self.client.login(username='jefe_th', password='testpass123')
        resp = self.client.get(
            f'/documentos/formulario/{self.doc_publico.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Planilla de Cambios')
        self.assertNotContains(resp, 'Distribuir')
        self.assertNotContains(resp, 'Asignar')
        self.assertNotContains(resp, 'Transferir')

    def test_trabajador_no_ve_botones_edicion(self):
        """Trabajador no propietario no ve botones de acción."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Planilla de Cambios')
        self.assertNotContains(resp, 'Distribuir')

    def test_jefe_depto_ve_botones_edicion(self):
        """Jefe del depto del documento SÍ ve botones de acción."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Planilla de Cambios')
        self.assertContains(resp, 'Inhabilitar')
        self.assertContains(resp, 'Distribuir')

    def test_propietario_ve_botones_edicion(self):
        """Propietario del documento ve botones de acción."""
        self.client.login(username='trab02', password='testpass123')
        resp = self.client.get(
            f'/documentos/formulario/{self.doc_th.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Planilla de Cambios')
        self.assertContains(resp, 'Solicitar Inhabilitación')

    def test_admin_ve_botones_en_cualquier_depto(self):
        """Admin ve botones en documentos de cualquier depto."""
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get(
            f'/documentos/formulario/{self.doc_th.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Planilla de Cambios')
        self.assertContains(resp, 'Inhabilitar')


# =============================================================================
# FLUJO DE SOLICITUD Y APROBACIÓN DE DESCARGAS
# =============================================================================

class SolicitudDescargaTest(BaseDocumentosTest):
    """Tests de flujo de solicitud y aprobación de descargas."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_solicitar_descarga_asigna_jefe_aprobador(self):
        """Solicitud asigna aprobador = jefe del depto del documento."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_th.uuid}/solicitar-descarga/',
            data={'motivo': 'Necesito el archivo'},
        )
        self.assertEqual(resp.status_code, 302)
        from documentos.models import AprobacionDescarga, SolicitudDescarga
        sol = SolicitudDescarga.objects.filter(
            documento=self.doc_th, solicitado_por=self.user_trabajador
        ).last()
        self.assertIsNotNone(sol)
        aprobacion = AprobacionDescarga.objects.get(solicitud=sol)
        self.assertEqual(aprobacion.aprobador, self.user_jefe_th)

    def test_solicitar_descarga_notifica_al_jefe(self):
        """La notificación va al jefe, NO al propietario."""
        from autenticacion.models import Notificacion
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_th.uuid}/solicitar-descarga/',
            data={'motivo': 'Motivo test'},
        )
        self.assertEqual(resp.status_code, 302)
        notif = Notificacion.objects.filter(
            usuario=self.user_jefe_th, tipo='solicitud_descarga'
        ).last()
        self.assertIsNotNone(notif)

    def test_solicitar_descarga_fallback_admin_sin_jefe(self):
        """Sin jefe en el depto, asigna a un admin."""
        self.depto_th.jefe = None
        self.depto_th.save(update_fields=['jefe'])
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_th.uuid}/solicitar-descarga/',
            data={'motivo': 'Fallback test'},
        )
        self.assertEqual(resp.status_code, 302)
        from documentos.models import AprobacionDescarga, SolicitudDescarga
        sol = SolicitudDescarga.objects.filter(
            documento=self.doc_th, solicitado_por=self.user_trabajador
        ).last()
        self.assertIsNotNone(sol)
        aprobacion = AprobacionDescarga.objects.get(solicitud=sol)
        self.assertEqual(aprobacion.aprobador, self.user_admin)

    def test_solicitar_descarga_sin_jefe_ni_admin_muestra_error(self):
        """Sin jefe ni admin: no crea solicitud y muestra error."""
        from autenticacion.models import PerfilUsuario
        PerfilUsuario.objects.filter(rol='administrador').update(rol='trabajador')
        self.depto_th.jefe = None
        self.depto_th.save(update_fields=['jefe'])
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_th.uuid}/solicitar-descarga/',
            data={'motivo': 'Sin aprobador test'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No se puede realizar la solicitud')
        from documentos.models import SolicitudDescarga
        self.assertFalse(
            SolicitudDescarga.objects.filter(
                documento=self.doc_th, solicitado_por=self.user_trabajador
            ).exists()
        )

    def test_solicitar_descarga_muestra_banner_jefe(self):
        """GET muestra banner dinámico con jefe aprobador."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/formulario/{self.doc_th.uuid}/solicitar-descarga/',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Se ha encontrado un jefe aprobador')
        self.assertContains(resp, 'Jefe TH')

    def test_solicitar_descarga_muestra_banner_admin(self):
        """Sin jefe, GET muestra banner con admin aprobador."""
        self.depto_th.jefe = None
        self.depto_th.save(update_fields=['jefe'])
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/formulario/{self.doc_th.uuid}/solicitar-descarga/',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Se ha encontrado un admin aprobador')

    def test_aprobar_descarga_como_aprobador_asignado(self):
        """Solo el aprobador asignado puede aprobar."""
        from documentos.models import AprobacionDescarga, SolicitudDescarga
        sol = SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Test aprobacion', estado='pendiente'
        )
        AprobacionDescarga.objects.create(
            solicitud=sol, aprobador=self.user_jefe_ti
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/solicitud/{sol.id}/aprobar/',
            data={'accion': 'aprobar'},
        )
        self.assertEqual(resp.status_code, 302)
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'aprobada')

    def test_aprobar_descarga_no_aprobador_asignado_rechazado(self):
        """Quien no es aprobador asignado es rechazado."""
        from documentos.models import AprobacionDescarga, SolicitudDescarga
        sol = SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Test', estado='pendiente'
        )
        AprobacionDescarga.objects.create(
            solicitud=sol, aprobador=self.user_jefe_ti
        )
        self.client.login(username='jefe_gg', password='testpass123')
        resp = self.client.get(f'/documentos/solicitud/{sol.id}/aprobar/')
        self.assertEqual(resp.status_code, 302)

    def test_aprobar_descarga_notifica_al_solicitante(self):
        """Al aprobar se notifica al solicitante."""
        from documentos.models import AprobacionDescarga, SolicitudDescarga
        from autenticacion.models import Notificacion
        sol = SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Test', estado='pendiente'
        )
        AprobacionDescarga.objects.create(
            solicitud=sol, aprobador=self.user_jefe_ti
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/solicitud/{sol.id}/aprobar/',
            data={'accion': 'aprobar'},
        )
        self.assertEqual(resp.status_code, 302)
        notif = Notificacion.objects.filter(
            usuario=self.user_trabajador, tipo='aprobacion_descarga'
        ).last()
        self.assertIsNotNone(notif)

    def test_rechazar_descarga_funciona(self):
        """Botón rechazar funciona correctamente (regresión del bug de hidden inputs)."""
        from documentos.models import AprobacionDescarga, SolicitudDescarga
        sol = SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Test rechazo', estado='pendiente'
        )
        AprobacionDescarga.objects.create(
            solicitud=sol, aprobador=self.user_jefe_ti
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/solicitud/{sol.id}/aprobar/',
            data={'accion': 'rechazar', 'observaciones': 'No aprobado'},
        )
        self.assertEqual(resp.status_code, 302)
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'rechazada')

    def test_detalle_muestra_descargas_pendientes(self):
        """Detalle muestra sección de descargas pendientes para el aprobador."""
        from documentos.models import AprobacionDescarga, SolicitudDescarga
        sol = SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Test detalle', estado='pendiente'
        )
        AprobacionDescarga.objects.create(
            solicitud=sol, aprobador=self.user_jefe_ti
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Solicitudes de Descarga Pendientes')

    def test_detalle_no_muestra_descargas_para_no_aprobador(self):
        """Detalle NO muestra descargas pendientes para usuario que no es aprobador."""
        from documentos.models import AprobacionDescarga, SolicitudDescarga
        sol = SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Test', estado='pendiente'
        )
        AprobacionDescarga.objects.create(
            solicitud=sol, aprobador=self.user_jefe_ti
        )
        self.client.login(username='coord01', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Solicitudes de Descarga Pendientes')


# =============================================================================
# BLOQUEO DE SOLICITUDES DUPLICADAS + BOTÓN DESCARGAR
# =============================================================================

class SolicitudDuplicadaTest(BaseDocumentosTest):
    """Tests de bloqueo de solicitudes duplicadas de descarga."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_solicitud_pendiente_bloquea_re_solicitud(self):
        """Si ya hay solicitud pendiente, re-solicitar redirige con warning."""
        from documentos.models import SolicitudDescarga, AprobacionDescarga
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Primera', estado='pendiente'
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/solicitar-descarga/',
            data={'motivo': 'Segunda intento'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            SolicitudDescarga.objects.filter(
                documento=self.doc_activo, solicitado_por=self.user_trabajador
            ).count(), 1
        )

    def test_solicitud_aprobada_bloquea_re_solicitud(self):
        """Si ya hay solicitud aprobada vigente, re-solicitar redirige con warning."""
        from django.utils import timezone
        from documentos.models import SolicitudDescarga
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Aprobada', estado='aprobada',
            version_aprobada=self.doc_activo.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=30),
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/solicitar-descarga/',
            data={'motivo': 'Intento post-aprobación'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            SolicitudDescarga.objects.filter(
                documento=self.doc_activo, solicitado_por=self.user_trabajador
            ).count(), 1
        )

    def test_solicitud_rechazada_permite_re_solicitud(self):
        """Si fue rechazada, permite volver a solicitar."""
        from documentos.models import SolicitudDescarga
        SolicitudDescarga.objects.create(
            documento=self.doc_th, solicitado_por=self.user_trabajador,
            motivo='Rechazada', estado='rechazada'
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_th.uuid}/solicitar-descarga/',
            data={'motivo': 'Nueva solicitud tras rechazo'},
            follow=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            SolicitudDescarga.objects.filter(
                documento=self.doc_th, solicitado_por=self.user_trabajador,
                motivo='Nueva solicitud tras rechazo'
            ).count(), 1
        )

    def test_detalle_muestra_boton_descargar_tras_aprobacion(self):
        """Tras aprobación vigente, el detalle muestra botón 'Descargar'."""
        from django.utils import timezone
        from documentos.models import SolicitudDescarga
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Test', estado='aprobada',
            version_aprobada=self.doc_activo.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=30),
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descargar')
        self.assertNotContains(resp, 'Solicitar Descarga')

    def test_detalle_muestra_badge_pendiente(self):
        """Con solicitud pendiente, el detalle muestra badge 'pendiente'."""
        from documentos.models import SolicitudDescarga
        SolicitudDescarga.objects.create(
            documento=self.doc_th, solicitado_por=self.user_trabajador,
            motivo='Pendiente', estado='pendiente'
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/formulario/{self.doc_th.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Solicitud pendiente de aprobación')
        self.assertNotContains(resp, 'Solicitar Descarga')

    def test_detalle_denegado_sin_permiso(self):
        """Usuario de otro depto sin relación no puede ver el documento."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/formulario/{self.doc_th.uuid}/'
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/documentos/mis-documentos/', resp.url)


class FormulariosDescargaTest(BaseDocumentosTest):
    """Tests de la vista /documentos/: descarga en la vista de lista."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_mismo_depto_ve_solicitar(self):
        """Usuario del mismo depto del doc sin aprobación ve 'Solicitar' (no 'Descargar')."""
        from documentos.models import Documento
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Formulario Interno',
            codigo='TI-FOR-100', estado='activo', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Solicitar')
        self.assertNotContains(
            resp,
            f'/documentos/formulario/{doc.uuid}/descargar/',
        )

    def test_solicitud_aprobada_ve_descargar(self):
        """Con solicitud aprobada vigente, la lista muestra 'Descargar'."""
        from django.utils import timezone
        from documentos.models import Documento, SolicitudDescarga
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Formulario Interno TI',
            codigo='TI-FOR-300', estado='activo', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
        )
        SolicitudDescarga.objects.create(
            documento=doc, solicitado_por=self.user_trabajador,
            motivo='Aprobado', estado='aprobada',
            version_aprobada=doc.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=30),
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp, f'/documentos/formulario/{doc.uuid}/descargar/',
        )

    def test_solicitud_pendiente_ve_badge(self):
        """Con solicitud pendiente, la lista muestra badge en vez de 'Solicitar'."""
        from documentos.models import Documento, SolicitudDescarga
        # Doc interno de TH propiedad del jefe de TH; trab02 (TH, no propietario)
        # no puede descargar directo y tiene una solicitud pendiente.
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Formulario TH Interno',
            codigo='TH-FOR-200', estado='activo', propietario=self.user_jefe_th,
            departamento=self.depto_th, version_actual='1.0',
        )
        SolicitudDescarga.objects.create(
            documento=doc, solicitado_por=self.user_trabajador_otro,
            motivo='Pendiente', estado='pendiente'
        )
        self.client.login(username='trab02', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Solicitud pendiente')
        self.assertNotContains(
            resp, f'/documentos/formulario/{doc.uuid}/solicitar-descarga/',
        )

    def test_doc_publico_ve_descargar(self):
        """Documento público siempre muestra 'Descargar' sin solicitud."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descargar')

    def test_propietario_de_otro_depto_descarga_directo(self):
        """El propietario de un interno descarga directo, aunque no sea de su depto."""
        self.doc_th.propietario = self.user_jefe_ti
        self.doc_th.save(update_fields=['propietario'])
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp, f'/documentos/formulario/{self.doc_th.uuid}/descargar/',
        )

    def test_jefe_ve_descargar_en_deptos_que_puede_ver(self):
        """Jefe ve 'Descargar' para docs de su depto o que es propietario."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descargar')

    def test_catalogo_muestra_for_de_otro_depto(self):
        """Un FOR interno de otro depto se lista en el catálogo para solicitar."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp, f'/documentos/formulario/{self.doc_th.uuid}/solicitar-descarga/',
        )
        self.assertNotContains(
            resp, f'/documentos/formulario/{self.doc_th.uuid}/descargar/',
        )

    def test_catalogo_muestra_for_restringido_de_otro_depto(self):
        """Un FOR restringido también se lista: expone metadatos y Solicitar."""
        from documentos.models import Documento
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Formulario Restringido',
            codigo='TH-FOR-900', estado='activo', propietario=self.user_jefe_th,
            departamento=self.depto_th, clasificacion='restringido',
            version_actual='1.0',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp, f'/documentos/formulario/{doc.uuid}/solicitar-descarga/',
        )
        self.assertNotContains(
            resp, f'/documentos/formulario/{doc.uuid}/descargar/',
        )

    def test_catalogo_solo_muestra_activos(self):
        """Borradores y documentos obsoletos no aparecen en el catálogo."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Formulario Publico')
        self.assertNotContains(resp, 'Formulario Borrador')
        self.assertNotContains(resp, 'Formulario Obsoleto')


# =============================================================================
# ENVIAR DOCUMENTO
# =============================================================================

class EnviarDocumentoTest(BaseDocumentosTest):
    """Tests del envío de documento a revisión (FieldError y get_full_name)."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.doc_borrador.refresh_from_db()

    def test_enviar_sin_jefes_queda_en_espera(self):
        """Sin jefe en depto ni GG, la cadena queda en espera (sin admin)."""
        self.depto_ti.jefe = None
        self.depto_ti.save(update_fields=['jefe'])
        self.depto_gg.jefe = None
        self.depto_gg.save(update_fields=['jefe'])

        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_borrador.uuid}/enviar/',
        )
        self.assertEqual(resp.status_code, 302)
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'en_revision')

        evaluador = SolicitudAprobacionDocumento.objects.filter(
            documento=self.doc_borrador, rol_aprobador='evaluador'
        ).first()
        autorizador = SolicitudAprobacionDocumento.objects.filter(
            documento=self.doc_borrador, rol_aprobador='autorizador'
        ).first()
        self.assertIsNotNone(evaluador)
        self.assertIsNotNone(autorizador)
        self.assertIsNone(evaluador.aprobador)
        self.assertEqual(evaluador.estado, 'esperando')
        self.assertFalse(evaluador.es_final)
        self.assertIsNone(autorizador.aprobador)
        self.assertEqual(autorizador.estado, 'esperando')
        self.assertTrue(autorizador.es_final)

    def test_enviar_con_jefe_departamento(self):
        """Con jefe de depto y de GG se crean dos pasos (evaluador + autorizador)."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_borrador.uuid}/enviar/',
        )
        self.assertEqual(resp.status_code, 302)
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'en_revision')

        evaluador = SolicitudAprobacionDocumento.objects.filter(
            documento=self.doc_borrador, rol_aprobador='evaluador'
        ).first()
        autorizador = SolicitudAprobacionDocumento.objects.filter(
            documento=self.doc_borrador, rol_aprobador='autorizador'
        ).first()
        self.assertEqual(evaluador.aprobador, self.user_jefe_ti)
        self.assertEqual(evaluador.estado, 'pendiente')
        self.assertFalse(evaluador.es_final)
        self.assertEqual(autorizador.aprobador, self.user_jefe_gg)
        self.assertEqual(autorizador.estado, 'esperando')
        self.assertTrue(autorizador.es_final)

    def test_get_full_name_vacio_bloquea_envio(self):
        """Usuario sin nombre + campo nombre_solicitante vacío → bloquea envío."""
        fmt = Formato.objects.create(
            codigo='TEST-FOR-001', nombre='Test', version='1', edicion='E1',
            activo=True, aplica_creacion=True,
        )
        from documentos.models import SeccionFormato
        secc = SeccionFormato.objects.create(
            formato=fmt, titulo='General', orden=0,
        )
        CampoFormato.objects.create(
            seccion=secc, clave='nombre_solicitante',
            etiqueta='Nombre del Solicitante', tipo='texto',
            origen='nombre_solicitante', requerido=True,
        )

        SolicitudCambio.objects.create(
            codigo='SC-TEST-001', formato=fmt, documento=self.doc_borrador,
            solicitante=self.user_trabajador,
            operacion='creacion', estado='borrador',
            respuestas={},
        )

        self.user_trabajador.first_name = ''
        self.user_trabajador.last_name = ''
        self.user_trabajador.save(update_fields=['first_name', 'last_name'])

        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_borrador.uuid}/enviar/',
            follow=True,
        )
        self.assertContains(resp, 'Edita el borrador y escribe tu nombre completo')
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'borrador')

    def test_get_full_name_con_nombre_permite_envio(self):
        """Usuario con nombre + campo nombre_solicitante vacío → permite envío."""
        fmt = Formato.objects.create(
            codigo='TEST-FOR-002', nombre='Test2', version='1', edicion='E1',
            activo=True, aplica_creacion=True,
        )
        from documentos.models import SeccionFormato
        secc = SeccionFormato.objects.create(
            formato=fmt, titulo='General', orden=0,
        )
        CampoFormato.objects.create(
            seccion=secc, clave='nombre_solicitante',
            etiqueta='Nombre del Solicitante', tipo='texto',
            origen='nombre_solicitante', requerido=True,
        )

        SolicitudCambio.objects.create(
            codigo='SC-TEST-002', formato=fmt, documento=self.doc_borrador,
            solicitante=self.user_trabajador,
            operacion='creacion', estado='borrador',
            respuestas={},
        )

        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_borrador.uuid}/enviar/',
        )
        self.assertEqual(resp.status_code, 302)
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'en_revision')

    def test_usa_formato_de_la_solicitud_no_el_global(self):
        """Formato global (con nombre_solicitante) no bloquea si la solicitud usa otro formato."""
        from documentos.models import SeccionFormato
        fmt_global = Formato.objects.create(
            codigo='AAA-GLOBAL', nombre='Global con nombre', version='1', edicion='E1',
            activo=True, aplica_creacion=True,
        )
        secc_g = SeccionFormato.objects.create(
            formato=fmt_global, titulo='General', orden=0,
        )
        CampoFormato.objects.create(
            seccion=secc_g, clave='nombre_solicitante',
            etiqueta='Nombre del Solicitante', tipo='texto',
            origen='nombre_solicitante', requerido=True,
        )

        fmt_local = Formato.objects.create(
            codigo='ZZZ-LOCAL', nombre='Local sin nombre', version='1', edicion='E1',
            activo=True, aplica_creacion=True,
        )

        SolicitudCambio.objects.create(
            codigo='SC-TEST-003', formato=fmt_local, documento=self.doc_borrador,
            solicitante=self.user_trabajador,
            operacion='creacion', estado='borrador',
            respuestas={},
        )

        self.user_trabajador.first_name = ''
        self.user_trabajador.last_name = ''
        self.user_trabajador.save(update_fields=['first_name', 'last_name'])

        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_borrador.uuid}/enviar/',
        )
        self.assertEqual(resp.status_code, 302)
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'en_revision')

    def test_sin_solicitud_no_bloquea_por_nombre(self):
        """Sin solicitud dinámica, usuario sin nombre no queda bloqueado."""
        fmt = Formato.objects.create(
            codigo='AAA-FOR-SOL', nombre='Formato con nombre', version='1', edicion='E1',
            activo=True, aplica_creacion=True,
        )
        from documentos.models import SeccionFormato
        secc = SeccionFormato.objects.create(
            formato=fmt, titulo='General', orden=0,
        )
        CampoFormato.objects.create(
            seccion=secc, clave='nombre_solicitante',
            etiqueta='Nombre del Solicitante', tipo='texto',
            origen='nombre_solicitante', requerido=True,
        )

        self.user_trabajador.first_name = ''
        self.user_trabajador.last_name = ''
        self.user_trabajador.save(update_fields=['first_name', 'last_name'])

        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_borrador.uuid}/enviar/',
        )
        self.assertEqual(resp.status_code, 302)
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'en_revision')


class BandejaDepartamentoTest(BaseDocumentosTest):
    """Tests de bandeja y API por departamento."""

    def _crear_solicitud_pendiente(self, doc, aprobador, solicitante):
        return SolicitudAprobacionDocumento.objects.create(
            documento=doc,
            accion_solicitud='creacion',
            solicitante=solicitante,
            propietario=doc.propietario,
            departamento_origen=doc.departamento,
            aprobador=aprobador,
            rol_aprobador='evaluador',
            es_final=True,
            estado='pendiente',
        )

    def test_bandeja_muestra_pendientes_del_depto(self):
        """El jefe ve solicitudes del depto aunque el aprobador sea otro."""
        self._crear_solicitud_pendiente(
            self.doc_en_revision, self.user_admin, self.user_trabajador,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/bandeja-aprobaciones/')
        self.assertEqual(resp.status_code, 200)
        solicitudes = list(resp.context['solicitudes'])
        self.assertEqual(len(solicitudes), 1)
        self.assertEqual(solicitudes[0].aprobador, self.user_admin)

    def test_bandeja_ve_aprobadas_del_depto(self):
        """El jefe ve solicitudes aprobadas del depto con filtro estado=aprobada."""
        sol = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision,
            accion_solicitud='creacion',
            solicitante=self.user_trabajador,
            propietario=self.doc_en_revision.propietario,
            departamento_origen=self.doc_en_revision.departamento,
            aprobador=self.user_admin,
            rol_aprobador='evaluador',
            es_final=True,
            estado='aprobada',
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/bandeja-aprobaciones/?estado=aprobada')
        self.assertEqual(resp.status_code, 200)
        solicitudes = list(resp.context['solicitudes'])
        self.assertEqual(len(solicitudes), 1)
        self.assertEqual(solicitudes[0].id, sol.id)
        self.assertEqual(solicitudes[0].estado, 'aprobada')

    def test_api_no_aprueba_si_no_es_aprobador(self):
        """El jefe no puede aprobar una solicitud asignada a otro usuario."""
        self._crear_solicitud_pendiente(
            self.doc_en_revision, self.user_admin, self.user_trabajador,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/{self.doc_en_revision.tipo_id}/{self.doc_en_revision.uuid}/aprobar-creacion/',
            data='{"observaciones": "OK"}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)

    def test_api_aprueba_si_es_aprobador(self):
        """El aprobador asignado puede aprobar su solicitud."""
        sol = self._crear_solicitud_pendiente(
            self.doc_en_revision, self.user_jefe_ti, self.user_trabajador,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/{self.doc_en_revision.tipo_id}/{self.doc_en_revision.uuid}/aprobar-creacion/',
            data='{"observaciones": "OK"}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'aprobada')
        self.assertEqual(sol.aprobador, self.user_jefe_ti)


class MotorFormulariosTest(BaseDocumentosTest):
    """Tests del motor de formularios (orígenes auto de documento y selección)."""

    def _crear_formato(self, codigo, **flags):
        defaults = dict(
            nombre='Test', version='1', edicion='1', activo=True,
            aplica_creacion=False, aplica_modificacion=False, aplica_eliminacion=False,
        )
        defaults.update(flags)
        fmt = Formato.objects.create(codigo=codigo, **defaults)
        secc = SeccionFormato.objects.create(formato=fmt, titulo='S', orden=0)
        return fmt, secc

    def test_valores_documento(self):
        from documentos.models import valores_documento
        vals = valores_documento(self.doc_en_revision)
        self.assertEqual(vals['num_identificacion'], self.doc_en_revision.codigo)
        self.assertEqual(vals['titulo_documento'], self.doc_en_revision.nombre)
        self.assertEqual(vals['version_documento'], self.doc_en_revision.version_actual)
        self.assertEqual(vals['tipo_informacion'], self.doc_en_revision.tipo.nombre)

    def test_autocompletar_respuestas_origenes_documento(self):
        from documentos.models import autocompletar_respuestas
        fmt, secc = self._crear_formato('TEST-FOR-001', aplica_creacion=True)
        CampoFormato.objects.create(
            seccion=secc, clave='num', etiqueta='Nº', tipo='texto',
            origen='num_identificacion',
        )
        CampoFormato.objects.create(
            seccion=secc, clave='acc', etiqueta='Acción', tipo='checkbox_grupo',
            origen='operacion',
            opciones=[{'valor': 'Creación', 'etiqueta': 'Creación'}],
        )
        sol = SolicitudCambio.objects.create(
            codigo='SC-MOTOR-001', formato=fmt, documento=self.doc_en_revision,
            solicitante=self.user_trabajador, operacion='creacion', respuestas={},
        )
        autocompletar_respuestas(self.doc_en_revision, {})
        sol.refresh_from_db()
        self.assertEqual(sol.respuestas.get('num'), self.doc_en_revision.codigo)
        self.assertEqual(sol.respuestas.get('acc'), ['Creación'])

    def test_seleccion_por_flags(self):
        self._crear_formato('ZZZ-CREA', aplica_creacion=True)
        self._crear_formato('ZZZ-ELIM', aplica_eliminacion=True)
        self._crear_formato('ZZZ-MOD', aplica_modificacion=True)
        self.assertEqual(
            Formato.objects.filter(activo=True, aplica_creacion=True)
            .order_by('codigo').first().codigo,
            'ZZZ-CREA',
        )
        self.assertEqual(
            Formato.objects.filter(activo=True, aplica_eliminacion=True)
            .order_by('codigo').first().codigo,
            'ZZZ-ELIM',
        )
        self.assertEqual(
            Formato.objects.filter(activo=True, aplica_modificacion=True)
            .order_by('codigo').first().codigo,
            'ZZZ-MOD',
        )

    def test_autollenar_envio_mapea_por_clave(self):
        """_autollenar_envio guarda por clave de campo, no por origen."""
        from documentos.views import _autollenar_envio
        fmt, secc = self._crear_formato('TEST-FOR-ENV', aplica_creacion=True)
        CampoFormato.objects.create(
            seccion=secc, clave='titulo', etiqueta='Título', tipo='texto',
            origen='titulo_documento',
        )
        CampoFormato.objects.create(
            seccion=secc, clave='accion', etiqueta='Acción', tipo='checkbox_grupo',
            origen='operacion',
            opciones=[{'valor': 'Creación', 'etiqueta': 'Creación'}],
        )
        sol = SolicitudCambio.objects.create(
            codigo='SC-ENV-001', formato=fmt, documento=self.doc_en_revision,
            solicitante=self.user_trabajador, operacion='creacion', respuestas={},
        )
        _autollenar_envio(sol, self.user_trabajador)
        sol.refresh_from_db()
        self.assertEqual(sol.respuestas.get('titulo'), self.doc_en_revision.nombre)
        self.assertEqual(sol.respuestas.get('accion'), ['Creación'])
        self.assertNotIn('titulo_documento', sol.respuestas)
        self.assertNotIn('operacion', sol.respuestas)


class DPRPermisosTest(BaseDocumentosTest):
    """DPR: creación solo jefes; el tipo no se muestra a no-jefes."""

    def setUp(self):
        super().setUp()
        TipoDocumento.objects.create(
            key='descripcion_procesos', nombre='Descripción de Procesos',
            codigo='DPR', activo=True, solo_jefes=True, sin_prefijo_depto=True,
        )

    def test_seleccionar_tipo_oculta_dpr_a_no_jefe(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/seleccionar-tipo/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Descripción de Procesos')

    def test_seleccionar_tipo_muestra_dpr_a_jefe(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/seleccionar-tipo/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descripción de Procesos')

    def test_no_jefe_no_puede_crear_dpr(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/nuevo/descripcion_procesos/')
        self.assertEqual(resp.status_code, 302)


# =============================================================================
# INTEGRIDAD DEL FLUJO DE DESCARGA (SERVICIO UNICO) + FEEDBACK AL SOLICITANTE
# =============================================================================

class DescargaIntegridadTest(BaseDocumentosTest):
    """Idempotencia y doble control del flujo de aprobación de descargas."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _solicitud(self, aprobadores, documento=None, **extra):
        doc = documento or self.doc_activo
        sol = SolicitudDescarga.objects.create(
            documento=doc, solicitado_por=self.user_trabajador,
            motivo='Integridad', estado='pendiente', **extra,
        )
        for aprobador in aprobadores:
            AprobacionDescarga.objects.create(
                solicitud=sol, aprobador=aprobador,
            )
        return sol

    def _restringido_ti(self):
        return Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Formulario Restringido',
            codigo='TI-FOR-097', estado='activo',
            propietario=self.user_trabajador, departamento=self.depto_ti,
            clasificacion='restringido', version_actual='1.0',
        )

    def test_reaprobacion_no_duplica_distribucion_ni_reinicia_vigencia(self):
        """El segundo POST de aprobación no vuelve a finalizar la descarga."""
        sol = self._solicitud([self.user_jefe_ti])
        url = f'/documentos/solicitud/{sol.id}/aprobar/'
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(url, data={'accion': 'aprobar'})
        self.assertEqual(resp.status_code, 302)
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'aprobada')
        vigencia_primera = sol.fecha_expiracion
        self.assertEqual(
            DistribucionDocumento.objects.filter(
                documento=self.doc_activo, usuario=self.user_trabajador,
            ).count(), 1,
        )

        resp = self.client.post(url, data={'accion': 'aprobar'})
        sol.refresh_from_db()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(sol.estado, 'aprobada')
        self.assertEqual(sol.fecha_expiracion, vigencia_primera)
        self.assertEqual(
            DistribucionDocumento.objects.filter(
                documento=self.doc_activo, usuario=self.user_trabajador,
            ).count(), 1,
        )

    def test_rechazo_tras_aprobacion_no_invierte_el_resultado(self):
        """Tras aprobar, el mismo aprobador ya no puede rechazar."""
        sol = self._solicitud([self.user_jefe_ti])
        url = f'/documentos/solicitud/{sol.id}/aprobar/'
        self.client.login(username='jefe_ti', password='testpass123')
        self.client.post(url, data={'accion': 'aprobar'})
        self.client.post(url, data={'accion': 'rechazar'})
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'aprobada')
        aprobacion = AprobacionDescarga.objects.get(
            solicitud=sol, aprobador=self.user_jefe_ti
        )
        self.assertEqual(aprobacion.estado, 'aprobada')

    def test_doble_control_rechazo_cancela_hermana(self):
        """Restringido: el rechazo cierra la solicitud y libera al otro control."""
        sol = self._solicitud(
            [self.user_jefe_ti, self.user_admin], documento=self._restringido_ti()
        )
        url = f'/documentos/solicitud/{sol.id}/aprobar/'

        self.client.login(username='jefe_ti', password='testpass123')
        self.client.post(url, data={'accion': 'aprobar'})
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'pendiente')

        self.client.logout()
        self.client.login(username='admin01', password='testpass123')
        self.client.post(
            url, data={'accion': 'rechazar', 'observaciones': 'Sin respaldo'}
        )
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'rechazada')
        self.assertEqual(
            AprobacionDescarga.objects.get(
                solicitud=sol, aprobador=self.user_jefe_ti
            ).estado, 'aprobada',
        )
        self.assertEqual(
            AprobacionDescarga.objects.get(
                solicitud=sol, aprobador=self.user_admin
            ).estado, 'rechazada',
        )
        self.assertEqual(
            DistribucionDocumento.objects.filter(
                usuario=self.user_trabajador, documento=sol.documento,
            ).count(), 0,
        )

    def test_doble_control_ambos_aprueban_finaliza_una_vez(self):
        """Restringido: ambos aprueban y solo entonces se distribuye."""
        sol = self._solicitud(
            [self.user_jefe_ti, self.user_admin], documento=self._restringido_ti()
        )
        url = f'/documentos/solicitud/{sol.id}/aprobar/'

        self.client.login(username='jefe_ti', password='testpass123')
        self.client.post(url, data={'accion': 'aprobar'})
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'pendiente')
        self.assertEqual(
            DistribucionDocumento.objects.filter(
                usuario=self.user_trabajador, documento=sol.documento,
            ).count(), 0,
        )

        self.client.logout()
        self.client.login(username='admin01', password='testpass123')
        self.client.post(url, data={'accion': 'aprobar'})
        sol.refresh_from_db()
        self.assertEqual(sol.estado, 'aprobada')
        self.assertEqual(
            DistribucionDocumento.objects.filter(
                usuario=self.user_trabajador, documento=sol.documento,
            ).count(), 1,
        )

    def test_ficha_no_ofrece_atender_sobre_solicitud_resuelta(self):
        """Una solicitud resuelta deja de aparecer en la ficha del aprobador."""
        sol = self._solicitud([self.user_jefe_ti])
        sol.estado = 'rechazada'
        sol.save(update_fields=['estado'])
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Solicitudes de Descarga Pendientes')

    def test_bandeja_no_muestra_solicitud_resuelta(self):
        """La bandeja tampoco ofrece resolver una solicitud ya resuelta."""
        sol = self._solicitud([self.user_jefe_ti])
        sol.estado = 'rechazada'
        sol.save(update_fields=['estado'])
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/bandeja-aprobaciones/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, f'/documentos/solicitud/{sol.id}/aprobar/')

    def test_get_solicitar_con_pendiente_redirige(self):
        """GET del formulario con solicitud en tramite redirige a la ficha."""
        self._solicitud([self.user_jefe_ti])
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/solicitar-descarga/',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/', resp.url,
        )


class FeedbackSolicitanteTest(BaseDocumentosTest):
    """El solicitante ve el estado de lo que pidió (ficha, formulario y perfil)."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_ficha_muestra_rechazo_y_motivo(self):
        """La ficha informa el rechazo y las observaciones del aprobador."""
        sol = SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Lo necesito', estado='rechazada', observaciones='Documento reservado',
        )
        AprobacionDescarga.objects.create(
            solicitud=sol, aprobador=self.user_jefe_ti,
            estado='rechazada', observaciones='Documento reservado',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Mi solicitud de descarga')
        self.assertContains(resp, 'Documento reservado')
        self.assertContains(resp, 'Solicitud rechazada')

    def test_ficha_muestra_autorizacion_vencida(self):
        """Con una aprobación vencida la ficha ofrece solicitar de nuevo."""
        from django.utils import timezone

        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Antigua', estado='aprobada',
            version_aprobada=self.doc_activo.version_actual,
            fecha_expiracion=timezone.now() - timedelta(days=1),
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Autorización vencida')
        self.assertContains(resp, 'Solicitar Descarga')

    def test_formulario_avisa_solicitud_anterior_rechazada(self):
        """El formulario avisa si la solicitud anterior fue rechazada."""
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Anterior', estado='rechazada',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/solicitar-descarga/',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'solicitud anterior')
        self.assertContains(resp, 'fue rechazada')

    def test_perfil_lista_mis_solicitudes(self):
        """El perfil lista las solicitudes del usuario con su estado."""
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Para perfil', estado='rechazada',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/perfil/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Mis solicitudes de descarga')
        self.assertContains(resp, 'Para perfil')
        self.assertContains(resp, 'Rechazada')

    def test_perfil_no_lista_solicitudes_de_otros(self):
        """El listado del perfil solo muestra las solicitudes del propio usuario."""
        jefe_doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Doc Jefe',
            codigo='TI-FOR-096', estado='activo',
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
            clasificacion='interno', version_actual='1.0',
        )
        SolicitudDescarga.objects.create(
            documento=jefe_doc, solicitado_por=self.user_jefe_ti,
            motivo='Del jefe', estado='pendiente',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/perfil/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Del jefe')

    def test_endpoint_descarga_desde_notificaciones_eliminado(self):
        """El antiguo endpoint JSON de descarga ya no existe (404)."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            '/api/notificaciones/solicitud-descarga/1/responder/',
            data=json.dumps({'accion': 'aprobar'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)


# =============================================================================
# MIS SOLICITUDES (HISTORIAL NORMALIZADO DEL USUARIO)
# =============================================================================

class MisSolicitudesTest(BaseDocumentosTest):
    """La vista "Mis solicitudes" unifica lo que el usuario ha pedido."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _descarga(self, estado='pendiente', **extra):
        return SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Necesito el PDF', estado=estado, **extra,
        )

    def _cambio(self, accion='inhabilitar'):
        return SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_activo, accion_solicitud=accion,
            solicitante=self.user_trabajador, propietario=self.user_jefe_ti,
            aprobador=self.user_jefe_ti, departamento_origen=self.depto_ti,
            rol_aprobador='evaluador', estado='pendiente',
        )

    def _acceso(self, solicitante=None, documento=False):
        if documento is False:
            documento = self.doc_activo
        return SolicitudAccesoDocumento.objects.create(
            documento=documento,
            solicitante=solicitante or self.user_trabajador,
            jefe_destino=self.user_jefe_ti,
            departamento_destino=self.depto_ti,
            estado='pendiente', motivo='Apoyo en el área',
        )

    def test_trabajador_accede_sin_rol_de_jefe(self):
        """A diferencia de la bandeja, aquí no se redirige al trabajador."""
        self._descarga()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-solicitudes/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Mis solicitudes')

    def test_muestra_solo_las_propias(self):
        self._descarga()
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador_otro,
            motivo='De otro usuario', estado='pendiente',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-solicitudes/')
        self.assertContains(resp, 'Necesito el PDF')
        self.assertNotContains(resp, 'De otro usuario')

    def test_unifica_los_tres_tipos(self):
        self._descarga()
        self._cambio()
        self._acceso()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-solicitudes/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descarga')
        self.assertContains(resp, 'Inhabilitar')
        self.assertContains(resp, 'Acceso a documento')

    def test_descarga_vigente_ofrece_descargar(self):
        from django.utils import timezone

        sol = self._descarga(
            estado='aprobada',
            version_aprobada=self.doc_activo.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=10),
        )
        AprobacionDescarga.objects.create(
            solicitud=sol, aprobador=self.user_jefe_ti, estado='aprobada',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-solicitudes/')
        self.assertContains(resp, 'Descargar')
        self.assertContains(
            resp,
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/descargar/',
        )

    def test_descarga_vencida_marca_estado_y_re_solicitud(self):
        from django.utils import timezone

        self._descarga(
            estado='aprobada',
            version_aprobada=self.doc_activo.version_actual,
            fecha_expiracion=timezone.now() - timedelta(days=1),
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-solicitudes/')
        self.assertContains(resp, 'Vencida')
        self.assertContains(resp, 'Solicitar de nuevo')

    def test_filtro_por_estado(self):
        from django.utils import timezone

        self._descarga(
            estado='aprobada', fecha_resolucion=timezone.now(),
            version_aprobada=self.doc_activo.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=10),
        )
        self._cambio()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-solicitudes/?estado=aprobada')
        self.assertEqual(len(resp.context['items']), 1)
        self.assertEqual(resp.context['items'][0].estado, 'aprobada')
        self.assertContains(resp, 'Necesito el PDF')

    def test_filtro_por_tipo(self):
        self._descarga()
        self._cambio()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-solicitudes/?tipo=descarga')
        self.assertEqual(len(resp.context['items']), 1)
        self.assertEqual(resp.context['items'][0].tipo, 'descarga')
        self.assertContains(resp, 'Necesito el PDF')

    def test_estado_desconocido_muestra_todas(self):
        self._descarga()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-solicitudes/?estado=inexistente')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['filtro_estado'], '')
        self.assertContains(resp, 'Necesito el PDF')

    def test_contadores_de_estado(self):
        from django.utils import timezone

        from documentos.services.solicitudes import mis_solicitudes

        self._descarga(
            estado='aprobada', fecha_resolucion=timezone.now(),
            version_aprobada=self.doc_activo.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=10),
        )
        self._cambio()
        items, totales = mis_solicitudes(self.user_trabajador)
        self.assertEqual(len(items), 2)
        self.assertEqual(totales['todas'], 2)
        self.assertEqual(totales['pendiente'], 1)
        self.assertEqual(totales['aprobada'], 1)

    def test_servicio_lista_aprobadores(self):
        from documentos.services.solicitudes import mis_solicitudes

        sol = self._descarga()
        AprobacionDescarga.objects.create(
            solicitud=sol, aprobador=self.user_jefe_ti, rol_aprobador='jefe',
        )
        items, _ = mis_solicitudes(self.user_trabajador)
        self.assertEqual(len(items[0].aprobadores), 1)
        self.assertEqual(items[0].aprobadores[0]['rol'], 'jefe')
        self.assertEqual(items[0].aprobadores[0]['nombre'], 'Jefe TI')

    def test_acceso_sin_documento_no_rompe(self):
        """Una solicitud de acceso sin documento asociado se lista igual."""
        self._acceso(documento=None)
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-solicitudes/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Documento no disponible')

    def test_dropdown_usuario_enlaza_mis_solicitudes(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Mis solicitudes')

    def test_perfil_enlaza_ver_todas(self):
        self._descarga()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/perfil/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ver todas')

    def test_perfil_resumen_es_corto(self):
        """El perfil solo adelanta las 3 más recientes."""
        from django.utils import timezone

        base = timezone.now()
        for dia in range(5):
            sol = SolicitudDescarga.objects.create(
                documento=self.doc_activo, solicitado_por=self.user_trabajador,
                motivo=f'Pedido {dia}', estado='aprobada', fecha_resolucion=base,
            )
            SolicitudDescarga.objects.filter(pk=sol.pk).update(
                fecha_solicitud=base - timedelta(days=dia),
            )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/perfil/')
        self.assertContains(resp, 'Ver todas')
        self.assertContains(resp, 'Pedido 0')
        self.assertContains(resp, 'Pedido 2')
        self.assertNotContains(resp, 'Pedido 4')
