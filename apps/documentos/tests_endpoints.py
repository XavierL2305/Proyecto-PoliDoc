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
    SeccionFormato, TipoDocumento,
)
from autenticacion.models import PerfilUsuario, Notificacion


# =============================================================================
# API: CAMBIAR ESTADO
# =============================================================================

class APICambiarEstadoTest(BaseDocumentosTest):
    """Tests del endpoint de cambio de estado."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_cambiar_estado_jefe_puede(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/cambiar-estado/',
            data=json.dumps({'estado': 'obsoleto', 'motivo_seleccion': 'test'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'obsoleto')

    def test_cambiar_estado_trabajador_solicitud(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            f'/documentos/api/procedimientos_generales/{self.doc_en_revision.uuid}/cambiar-estado/',
            data=json.dumps({'estado': 'activo'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

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

    def test_eliminar_version(self):
        v1 = VersionDocumento.objects.create(
            documento=self.doc_activo,
            numero_version='1.0',
            archivo=SimpleUploadedFile('v1.pdf', b'content'),
            creado_por=self.user_jefe_ti,
            cambios='Version inicial',
        )
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/eliminar-version/{v1.id}/',
        )
        self.assertEqual(resp.status_code, 302)


# =============================================================================
# VISTAS: RENOVACIÓN DE VENCIMIENTO
# =============================================================================

class VistasRenovarTest(BaseDocumentosTest):
    """Tests de renovación de vencimiento."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_renovar_vencimiento(self):
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/renovar-vencimiento/',
        )
        self.assertEqual(resp.status_code, 302)

    def test_renovar_obsoleto_reactiva(self):
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_obsoleto.uuid}/renovar-vencimiento/',
        )
        self.assertEqual(resp.status_code, 302)


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
        self.assertContains(resp, 'Solicitar Eliminación')

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
        """Si ya hay solicitud aprobada, re-solicitar redirige con warning."""
        from documentos.models import SolicitudDescarga
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Aprobada', estado='aprobada'
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
        """Tras aprobación, el detalle muestra botón 'Descargar'."""
        from documentos.models import SolicitudDescarga
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador,
            motivo='Test', estado='aprobada'
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
        """Con solicitud aprobada, la lista muestra 'Descargar' en vez de 'Solicitar'."""
        from documentos.models import SolicitudDescarga
        SolicitudDescarga.objects.create(
            documento=self.doc_th, solicitado_por=self.user_trabajador,
            motivo='Aprobado', estado='aprobada'
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descargar')
        self.assertNotContains(resp, 'Solicitar')

    def test_solicitud_pendiente_ve_badge(self):
        """Con solicitud pendiente, la lista muestra badge en vez de 'Solicitar'."""
        from documentos.models import SolicitudDescarga
        # jefe_ti is propietario of doc_th (TH dept) → visible in list but not downloadable directly
        self.doc_th.propietario = self.user_jefe_ti
        self.doc_th.save(update_fields=['propietario'])
        SolicitudDescarga.objects.create(
            documento=self.doc_th, solicitado_por=self.user_jefe_ti,
            motivo='Pendiente', estado='pendiente'
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Solicitud pendiente')
        self.assertNotContains(resp, 'Solicitar')

    def test_doc_publico_ve_descargar(self):
        """Documento público siempre muestra 'Descargar' sin solicitud."""
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descargar')

    def test_propietario_de_otro_depto_ve_solicitar(self):
        """Jefe propietario de doc de otro depto (no admin) ve 'Solicitar'."""
        self.doc_th.propietario = self.user_jefe_ti
        self.doc_th.save(update_fields=['propietario'])
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Solicitar')

    def test_jefe_ve_descargar_en_deptos_que_puede_ver(self):
        """Jefe ve 'Descargar' para docs de su depto o que es propietario."""
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descargar')


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
        self.assertEqual(sol.respuestas.get('acc'), 'Creación')

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
        self.assertEqual(sol.respuestas.get('accion'), 'Creación')
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
