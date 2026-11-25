"""Tests del Organigrama Corporativo Interactivo (SGC-ORG-001 — Fase 1)."""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from autenticacion.models import PerfilUsuario
from documentos.models import (
    AccesoFormato,
    AsignacionDocumento,
    DelegacionJefatura,
    Documento,
    Formato,
    RegistroFormato,
    ResumenIAUso,
    SolicitudAprobacionDocumento,
    TipoDocumento,
    VersionDocumento,
)
from documentos.services import acceso_formato
from documentos.services.organigrama import (
    MAX_PAYLOAD_BYTES,
    OrganigramaValidationError,
    crear_organigrama_inicial,
    hash_organigrama,
    parsear_y_validar_payload,
)


def _payload_valido():
    return {
        'schema_version': 1,
        'settings': {'gridSize': 20, 'snapToGrid': True},
        'sections': [
            {
                'id': 'sec_th',
                'title': 'Talento Humano',
                'empresa': 'PT',
                'departamento_id': None,
                'x': 40,
                'y': 40,
                'w': 520,
                'h': 320,
                'color': '#2563eb',
                'order': 0,
            },
        ],
        'nodes': [
            {
                'id': 'node_gg',
                'section_id': 'sec_th',
                'shape': 'rounded',
                'x': 180,
                'y': 80,
                'w': 200,
                'h': 70,
                'text': 'Gerente General',
                'zIndex': 2,
                'style': {
                    'bgColor': '#FFFFFF',
                    'textColor': 'auto',
                    'borderColor': '#1d4ed8',
                    'borderWidth': 2,
                    'borderStyle': 'solid',
                    'borderRadius': 10,
                    'fontWeight': 'bold',
                    'fontStyle': 'normal',
                    'fontSize': 14,
                    'textAlign': 'center',
                },
            },
            {
                'id': 'node_th',
                'section_id': 'sec_th',
                'shape': 'rect',
                'x': 180,
                'y': 220,
                'w': 200,
                'h': 64,
                'text': 'Jefe de Talento Humano',
                'zIndex': 1,
                'style': {'bgColor': 'auto', 'textColor': 'auto'},
            },
        ],
        'edges': [
            {
                'id': 'edge_1',
                'from': 'node_gg',
                'to': 'node_th',
                'fromAnchor': 'bottom',
                'toAnchor': 'top',
                'routing': 'orthogonal',
                'color': 'auto',
                'width': 3,
                'style': 'solid',
                'arrowEnd': True,
                'label': 'Línea directa',
            },
        ],
    }


class OrganigramaPayloadValidationTest(BaseDocumentosTest):
    """Validación estricta de JSON, colores, límites y referencias."""

    def test_payload_valido_normaliza_pt_a_ptca_y_hex(self):
        datos = parsear_y_validar_payload(json.dumps(_payload_valido()).encode('utf-8'))
        self.assertEqual(datos['sections'][0]['empresa'], 'PTCA')
        self.assertEqual(datos['sections'][0]['color'], '#2563EB')
        self.assertEqual(datos['nodes'][0]['style']['borderColor'], '#1D4ED8')
        self.assertEqual(datos['edges'][0]['width'], 3)

    def test_rechaza_payload_mayor_a_1mb_antes_de_parsear(self):
        gigante = b'{' + b' ' * MAX_PAYLOAD_BYTES + b'}'
        with self.assertRaises(OrganigramaValidationError):
            parsear_y_validar_payload(gigante)

    def test_rechaza_nan_e_infinity(self):
        raw = json.dumps(_payload_valido()).replace('520', 'NaN').encode('utf-8')
        with self.assertRaises(OrganigramaValidationError):
            parsear_y_validar_payload(raw)

    def test_rechaza_id_reservado_y_colores_o_formas_invalidas(self):
        p = _payload_valido()
        p['nodes'][0]['id'] = '__proto__'
        with self.assertRaises(OrganigramaValidationError):
            parsear_y_validar_payload(json.dumps(p).encode('utf-8'))

        p = _payload_valido()
        p['nodes'][0]['style']['bgColor'] = 'javascript:alert(1)'
        with self.assertRaises(OrganigramaValidationError):
            parsear_y_validar_payload(json.dumps(p).encode('utf-8'))

        p = _payload_valido()
        p['nodes'][0]['shape'] = 'hexagon'
        with self.assertRaises(OrganigramaValidationError):
            parsear_y_validar_payload(json.dumps(p).encode('utf-8'))

        p = _payload_valido()
        p['edges'][0]['width'] = 99
        with self.assertRaises(OrganigramaValidationError):
            parsear_y_validar_payload(json.dumps(p).encode('utf-8'))

    def test_rechaza_referencias_colgantes_y_autoenlace(self):
        p = _payload_valido()
        p['nodes'][0]['section_id'] = 'sec_inexistente'
        with self.assertRaises(OrganigramaValidationError):
            parsear_y_validar_payload(json.dumps(p).encode('utf-8'))

        p = _payload_valido()
        p['edges'][0]['to'] = 'node_fantasma'
        with self.assertRaises(OrganigramaValidationError):
            parsear_y_validar_payload(json.dumps(p).encode('utf-8'))

        p = _payload_valido()
        p['edges'][0]['to'] = 'node_gg'
        with self.assertRaises(OrganigramaValidationError):
            parsear_y_validar_payload(json.dumps(p).encode('utf-8'))


class OrganigramaIntegracionTest(BaseDocumentosTest):
    """Unicidad SGC-ORG-001, bloqueo de plantillas, permisos, versiones y auditoría."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tipo_org, _ = TipoDocumento.objects.get_or_create(
            key='organigrama',
            defaults={
                'nombre': 'Organigrama',
                'codigo': 'ORG',
                'activo': True,
                'solo_jefes': True,
            },
        )
        cls.user_admin_sin_depto = User.objects.create(
            username='admin_sindepto',
            password=make_password('testpass123'),
            first_name='Admin',
            last_name='Global',
        )
        PerfilUsuario.objects.create(
            usuario=cls.user_admin_sin_depto,
            rol='administrador',
            grupo_ldap='doc_administrador',
            departamento=None,
        )

    def setUp(self):
        super().setUp()
        self._mongo = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo.start()
        self.addCleanup(self._mongo.stop)
        self.client = Client()

    def test_unicidad_sgc_org_001_y_poblar_tipos_documento(self):
        doc1 = crear_organigrama_inicial(self.user_jefe_th)
        doc2 = crear_organigrama_inicial(self.user_jefe_ti)
        self.assertEqual(doc1.pk, doc2.pk)
        self.assertEqual(doc1.codigo, 'SGC-ORG-001')
        self.assertEqual(doc1.departamento.codigo, 'SGC')

        # UniqueConstraint en BD impide segundo ORG vigente
        with self.assertRaises(IntegrityError), transaction.atomic():
            Documento.objects.create(
                tipo=self.tipo_org,
                nombre='Duplicado',
                codigo='SGC-ORG-002',
                estado='activo',
                es_plantilla=False,
                propietario=self.user_jefe_ti,
                departamento=self.depto_ti,
            )

        # poblar_tipos_documento no pisa flags clave
        call_command('poblar_tipos_documento')
        self.tipo_org.refresh_from_db()
        self.assertTrue(self.tipo_org.solo_jefes)
        dpr = TipoDocumento.objects.get(key='descripcion_procesos')
        self.assertTrue(dpr.usa_prefijo_empresa)

    def test_crear_documento_redirige_a_constructor_y_plantillas_bloqueadas(self):
        self.client.login(username='admin_sindepto', password='testpass123')
        resp_new = self.client.get(reverse('documentos:crear_documento', args=['organigrama']))
        self.assertRedirects(
            resp_new, reverse('documentos:organigrama_constructor'),
            fetch_redirect_response=False,
        )
        # Constructor funciona incluso para admin sin perfil.departamento
        resp_cons = self.client.get(reverse('documentos:organigrama_constructor'))
        self.assertEqual(resp_cons.status_code, 200)
        self.assertTrue(Documento.objects.filter(codigo='SGC-ORG-001').exists())

        # Seleccionar tipo muestra ORG a admin/jefe en docs, oculta en ?plantilla=1
        resp_sel_doc = self.client.get(reverse('documentos:seleccionar_tipo'))
        self.assertContains(resp_sel_doc, '/documentos/nuevo/organigrama/')
        resp_sel_plt = self.client.get(reverse('documentos:seleccionar_tipo') + '?plantilla=1')
        self.assertNotContains(resp_sel_plt, '/documentos/plantillas/nueva/organigrama/')

        # crear_plantilla para organigrama está bloqueada
        resp_plt = self.client.get(reverse('documentos:crear_plantilla', args=['organigrama']))
        self.assertRedirects(resp_plt, reverse('documentos:catalogo'), fetch_redirect_response=False)

    def test_permisos_lectura_vs_construccion(self):
        doc = crear_organigrama_inicial(self.user_jefe_th, clasificacion='publico')

        # AsignacionDocumento con puede_editar=True NO permite editar ORG ni debe guardarse
        coord_sgc = User.objects.create(
            username='coord_sgc', password=make_password('testpass123'),
            first_name='Coord', last_name='SGC',
        )
        PerfilUsuario.objects.create(
            usuario=coord_sgc, rol='coordinador',
            grupo_ldap='doc_coordinador', departamento=doc.departamento,
        )
        AsignacionDocumento.objects.create(
            documento=doc,
            coordinador=self.user_coordinador,
            jefe_asignador=self.user_jefe_th,
            puede_leer=True,
            puede_editar=True,
        )
        self.client.login(username='admin01', password='testpass123')
        resp_asig = self.client.post(
            reverse('documentos:asignar_documento', args=['organigrama', doc.uuid]),
            {'coordinador': coord_sgc.id, 'puede_leer': 'on', 'puede_editar': 'on'},
        )
        self.assertEqual(resp_asig.status_code, 200)
        self.assertContains(resp_asig, 'lo asigna únicamente el administrador')

        # Todos los usuarios autenticados ven ORG público, pero sin concesión
        # en AccesoFormato NO pueden construir/editar (incluidos jefes o delegados).
        hoy = timezone.now().date()
        DelegacionJefatura.objects.create(
            jefe=self.user_jefe_ti,
            delegado=self.user_coordinador,
            departamento=self.depto_ti,
            fecha_inicio=hoy - timedelta(days=1),
            fecha_fin=hoy + timedelta(days=2),
            activa=True,
        )
        for username in ('trab01', 'coord01', 'jefe_th'):
            self.client.login(username=username, password='testpass123')
            r_vis = self.client.get(reverse('documentos:organigrama_visor', args=[doc.uuid]))
            self.assertEqual(r_vis.status_code, 200)
            r_datos = self.client.get(reverse('documentos:organigrama_datos_api', args=[doc.uuid]))
            self.assertEqual(r_datos.status_code, 200)
            r_cons = self.client.get(reverse('documentos:organigrama_constructor'))
            self.assertEqual(r_cons.status_code, 302)
            r_save = self.client.post(
                reverse('documentos:organigrama_guardar_api', args=[doc.uuid]),
                data=json.dumps({
                    'revision_edicion': doc.revision_edicion,
                    'incrementar_version': False,
                    'datos': _payload_valido(),
                }),
                content_type='application/json',
            )
            self.assertEqual(r_save.status_code, 403)
            # Solo admin abre la pantalla de gestión de accesos
            r_acc_den = self.client.get(reverse('documentos:organigrama_accesos'))
            self.assertEqual(r_acc_den.status_code, 302)

        # Admin otorga permiso de edición a coord01 vía /organigrama/accesos/
        self.client.login(username='admin01', password='testpass123')
        r_grant = self.client.post(
            reverse('documentos:organigrama_accesos'),
            {'usuario_id': self.user_coordinador.id},
        )
        self.assertEqual(r_grant.status_code, 302)
        self.assertTrue(
            AccesoFormato.objects.filter(
                tipo_documento__key='organigrama',
                usuario=self.user_coordinador,
                activo=True,
                nivel='editar',
            ).exists()
        )

        self.client.login(username='coord01', password='testpass123')
        self.assertEqual(
            self.client.get(reverse('documentos:organigrama_constructor')).status_code,
            200,
        )
        r_save_del = self.client.post(
            reverse('documentos:organigrama_guardar_api', args=[doc.uuid]),
            data=json.dumps({
                'revision_edicion': doc.revision_edicion,
                'incrementar_version': False,
                'datos': _payload_valido(),
            }),
            content_type='application/json',
        )
        self.assertEqual(r_save_del.status_code, 200)

        # Admin revoca permiso -> vuelve a solo lectura conservando traza activa=False
        self.client.login(username='admin01', password='testpass123')
        r_rev = self.client.post(
            reverse('documentos:organigrama_acceso_revocar', args=[self.user_coordinador.id]),
        )
        self.assertEqual(r_rev.status_code, 302)
        self.assertTrue(
            AccesoFormato.objects.filter(
                tipo_documento__key='organigrama',
                usuario=self.user_coordinador,
                activo=False,
            ).exists()
        )
        self.client.login(username='coord01', password='testpass123')
        self.assertEqual(
            self.client.get(reverse('documentos:organigrama_constructor')).status_code,
            302,
        )

        # en_revision y obsoleto bloquean guardado incluso para admin
        doc.refresh_from_db()
        for estado_bloq in ('en_revision', 'obsoleto'):
            Documento.objects.filter(pk=doc.pk).update(estado=estado_bloq)
            self.client.login(username='admin01', password='testpass123')
            r_bloq = self.client.post(
                reverse('documentos:organigrama_guardar_api', args=[doc.uuid]),
                data=json.dumps({
                    'revision_edicion': doc.revision_edicion,
                    'incrementar_version': False,
                    'datos': _payload_valido(),
                }),
                content_type='application/json',
            )
            self.assertEqual(r_bloq.status_code, 403)

    def test_versionado_concurrencia_eliminacion_y_lista_maestra(self):
        # Preparar registro vivo FOR-006
        RegistroFormato.objects.create(
            formato=Formato.objects.create(
                codigo='SGC-FOR-006', nombre='Lista Maestra', activo=True,
            ),
            formato_codigo='SGC-FOR-006',
            contexto_tipo='sistema',
            contexto_id='lista-maestra',
            titulo='Lista Maestra',
            responsable=self.user_admin,
            respuestas={'documentos': []},
        )

        doc = crear_organigrama_inicial(self.user_jefe_ti)
        acceso_formato.otorgar_tipo(self.user_jefe_th, 'organigrama', otorgado_por=self.user_admin)
        hash_inicial = doc.hash_integridad
        rev_0 = doc.revision_edicion

        # 1. Conservar versión actual actualiza v1 en sitio
        self.client.login(username='jefe_th', password='testpass123')
        r_keep = self.client.post(
            reverse('documentos:organigrama_guardar_api', args=[doc.uuid]),
            data=json.dumps({
                'revision_edicion': rev_0,
                'incrementar_version': False,
                'cambios': 'Ajuste en v1',
                'datos': _payload_valido(),
            }),
            content_type='application/json',
        )
        self.assertEqual(r_keep.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.version_actual, '1')
        self.assertEqual(doc.revision_edicion, rev_0 + 1)
        self.assertNotEqual(doc.hash_integridad, hash_inicial)

        # 2. Revisión obsoleta devuelve 409 Conflict
        r_conflict = self.client.post(
            reverse('documentos:organigrama_guardar_api', args=[doc.uuid]),
            data=json.dumps({
                'revision_edicion': rev_0,
                'incrementar_version': False,
                'datos': _payload_valido(),
            }),
            content_type='application/json',
        )
        self.assertEqual(r_conflict.status_code, 409)

        # 3. Subir versión a v2 crea VersionDocumento v2 y sincroniza FOR-006
        payload_v2 = _payload_valido()
        payload_v2['nodes'][0]['text'] = 'Director General'
        with patch('documentos.services.for_006.upsert_documento') as mock_upsert:
            with self.captureOnCommitCallbacks(execute=True):
                r_bump = self.client.post(
                    reverse('documentos:organigrama_guardar_api', args=[doc.uuid]),
                    data=json.dumps({
                        'revision_edicion': doc.revision_edicion,
                        'incrementar_version': True,
                        'cambios': 'Creación v2',
                        'datos': payload_v2,
                    }),
                    content_type='application/json',
                )
        self.assertEqual(r_bump.status_code, 200)
        doc.refresh_from_db()
        self.assertEqual(doc.version_actual, '2')
        self.assertEqual(VersionDocumento.objects.filter(documento=doc, activa=True).count(), 2)
        self.assertTrue(mock_upsert.called)

        # GET ?version=1 sigue mostrando v1
        r_v1 = self.client.get(
            reverse('documentos:organigrama_datos_api', args=[doc.uuid]) + '?version=1'
        )
        self.assertEqual(r_v1.status_code, 200)
        self.assertEqual(r_v1.json()['datos']['nodes'][0]['text'], 'Gerente General')

        # Eliminar versión v2 restaura v1 en organigrama_sha256 y revision_edicion
        v1 = VersionDocumento.objects.get(documento=doc, numero_version='1')
        v2 = VersionDocumento.objects.get(documento=doc, numero_version='2')
        sha_v1 = doc.organigrama_sha256
        solic = SolicitudAprobacionDocumento(
            documento=doc,
            accion_solicitud='eliminar_version',
            version_a_eliminar=v2,
            solicitante=self.user_admin,
            propietario=doc.propietario,
            aprobador=self.user_admin,
            departamento_origen=doc.departamento,
            rol_aprobador='autorizador',
            es_final=True,
            estado='aprobada',
        )
        solic.aplicar_eliminacion_version(doc, v2)
        doc.refresh_from_db()
        self.assertEqual(doc.version_actual, '1')
        self.assertNotEqual(doc.organigrama_sha256, sha_v1)
        self.assertEqual(doc.organigrama_sha256, hash_organigrama(v1.datos_organigrama))

    def test_auditoria_atribucion_fail_closed_ia_e_historial(self):
        doc = crear_organigrama_inicial(self.user_jefe_ti, clasificacion='publico')
        acceso_formato.otorgar_tipo(self.user_jefe_th, 'organigrama', otorgado_por=self.user_admin)

        # Guardar por editor autorizado (jefe_th) audita usuario=jefe_th (no propietario jefe_ti)
        self.client.login(username='jefe_th', password='testpass123')
        with patch('auditoria.services.registrar_edicion_documento') as mock_ed:
            r_save = self.client.post(
                reverse('documentos:organigrama_guardar_api', args=[doc.uuid]),
                data=json.dumps({
                    'revision_edicion': doc.revision_edicion,
                    'incrementar_version': False,
                    'datos': _payload_valido(),
                }),
                content_type='application/json',
            )
        self.assertEqual(r_save.status_code, 200)
        mock_ed.assert_called_once()
        self.assertEqual(mock_ed.call_args.kwargs['usuario'], self.user_jefe_th)

        # GET datos ejecuta registrar_consulta y bajo AUDIT_FAIL_CLOSED bloquea en 503
        self.client.login(username='trab01', password='testpass123')
        with patch('documentos.views_organigrama.registrar_consulta') as mock_cons:
            r_ok = self.client.get(reverse('documentos:organigrama_datos_api', args=[doc.uuid]))
        self.assertEqual(r_ok.status_code, 200)
        mock_cons.assert_called_once()

        with override_settings(AUDIT_FAIL_CLOSED=True):
            with patch(
                'documentos.views_organigrama.registrar_consulta',
                side_effect=RuntimeError('Mongo caído'),
            ):
                r_503 = self.client.get(
                    reverse('documentos:organigrama_datos_api', args=[doc.uuid])
                )
        self.assertEqual(r_503.status_code, 503)

        # resumen_ia rechaza con 400 SIN consumir cuota diaria de IA
        with patch('documentos.ai.esta_habilitado', return_value=True):
            r_ia = self.client.post(
                reverse('documentos:resumen_ia', args=['organigrama', doc.uuid])
            )
        self.assertEqual(r_ia.status_code, 400)
        self.assertEqual(ResumenIAUso.objects.count(), 0)

        # historial_versiones muestra "Interactivo" y NO "Sin archivo"
        r_hist = self.client.get(
            reverse('documentos:historial_versiones', args=['organigrama', doc.uuid])
        )
        self.assertEqual(r_hist.status_code, 200)
        self.assertContains(r_hist, 'Interactivo')
        self.assertNotContains(r_hist, 'Sin archivo')

        # detalle.html oculta Descargar y muestra visor interactivo
        r_det = self.client.get(
            reverse('documentos:detalle', args=['organigrama', doc.uuid])
        )
        self.assertEqual(r_det.status_code, 200)
        self.assertContains(r_det, 'id="org-workspace"')
        self.assertNotContains(r_det, 'Solicitar Descarga')
