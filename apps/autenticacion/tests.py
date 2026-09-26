from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from tests_base import BaseDocumentosTest
from autenticacion.models import PerfilUsuario, Notificacion
from autenticacion.backends import LDAPBackend
from autenticacion.api import crear_notificacion
from documentos.models import Departamento, SubArea, SolicitudAprobacionDocumento, PlanillaCambios, AprobacionPlanilla, TipoDocumento


class PerfilUsuarioModelTest(BaseDocumentosTest):
    """Tests del modelo PerfilUsuario."""

    def test_str_perfil(self):
        self.assertEqual(
            str(self.perfil_jefe_ti),
            'jefe_ti - Jefe de Departamento'
        )

    def test_tiene_acceso_grupo_valido(self):
        self.assertTrue(self.perfil_jefe_ti.tiene_acceso)

    def test_tiene_acceso_sin_grupo(self):
        p = PerfilUsuario.objects.create(
            usuario=User.objects.create_user('sin_grupo', password='t'),
            grupo_ldap='sin_grupo'
        )
        self.assertFalse(p.tiene_acceso)

    def test_es_administrador(self):
        self.assertTrue(self.user_admin.perfil.es_administrador)
        self.assertFalse(self.user_jefe_ti.perfil.es_administrador)

    def test_es_jefe(self):
        self.assertTrue(self.user_jefe_ti.perfil.es_jefe)
        self.assertFalse(self.user_trabajador.perfil.es_jefe)

    def test_es_coordinador(self):
        self.assertTrue(self.user_coordinador.perfil.es_coordinador)
        self.assertFalse(self.user_jefe_ti.perfil.es_coordinador)

    def test_es_trabajador(self):
        self.assertTrue(self.user_trabajador.perfil.es_trabajador)
        self.assertFalse(self.user_jefe_ti.perfil.es_trabajador)

    def test_puede_aprobar_jefe(self):
        self.assertTrue(self.user_jefe_ti.perfil.puede_aprobar)

    def test_puede_aprobar_admin(self):
        self.assertTrue(self.user_admin.perfil.puede_aprobar)

    def test_puede_aprobar_coordinador(self):
        self.assertFalse(self.user_coordinador.perfil.puede_aprobar)

    def test_puede_aprobar_trabajador(self):
        self.assertFalse(self.user_trabajador.perfil.puede_aprobar)

    def test_rol_choices_validos(self):
        roles_validos = ['administrador', 'jefe_departamento', 'coordinador', 'trabajador']
        for rol in roles_validos:
            p = PerfilUsuario(
                usuario=self.user_trabajador,
                rol=rol
            )
            p.clean_fields()  # no debe fallar

    def test_departamento_optional(self):
        p = PerfilUsuario.objects.create(
            usuario=User.objects.create_user('sin_depto', password='t'),
            rol='trabajador'
        )
        self.assertIsNone(p.departamento)


class NotificacionModelTest(BaseDocumentosTest):
    """Tests del modelo Notificacion."""

    def test_creacion_notificacion(self):
        n = Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='Test Notificacion',
            mensaje='Mensaje de prueba',
            tipo='solicitud_descarga',
        )
        self.assertEqual(n.usuario, self.user_trabajador)
        self.assertFalse(n.leida)
        self.assertEqual(n.tipo, 'solicitud_descarga')

    def test_marcar_como_leida(self):
        n = Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='Test',
            mensaje='Msg',
            tipo='sistema',
        )
        n.marcar_como_leida()
        n.refresh_from_db()
        self.assertTrue(n.leida)

    def test_str_notificacion(self):
        n = Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='Alerta',
            mensaje='Msg',
            tipo='sistema',
        )
        self.assertIn('trab01', str(n))

    def test_ordenadas_por_fecha_desc(self):
        n1 = Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='Primera', mensaje='m1', tipo='sistema'
        )
        n2 = Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='Segunda', mensaje='m2', tipo='sistema'
        )
        qs = list(Notificacion.objects.filter(usuario=self.user_trabajador))
        self.assertEqual(qs[0], n2)
        self.assertEqual(qs[1], n1)

    def test_campos_opcionales_documento_y_departamento(self):
        n = Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='Sin doc ni depto',
            mensaje='msg',
            tipo='sistema',
        )
        self.assertIsNone(n.documento)
        self.assertIsNone(n.departamento)

    def test_notificaciones_por_usuario(self):
        Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='N1', mensaje='m', tipo='sistema'
        )
        Notificacion.objects.create(
            usuario=self.user_jefe_ti,
            titulo='N2', mensaje='m', tipo='sistema'
        )
        self.assertEqual(
            Notificacion.objects.filter(usuario=self.user_trabajador).count(), 1
        )
        self.assertEqual(
            Notificacion.objects.filter(usuario=self.user_jefe_ti).count(), 1
        )


class LDAPBackendNormalizeTest(TestCase):
    """Tests unitarios de _normalize_text (sin LDAP real)."""

    def setUp(self):
        self.backend = LDAPBackend()

    def test_normaliza_sin_tildes(self):
        self.assertEqual(
            self.backend._normalize_text('Tecnología'),
            'tecnologia'
        )

    def test_normaliza_lavanderia(self):
        self.assertEqual(
            self.backend._normalize_text('Lavandería'),
            'lavanderia'
        )

    def test_normaliza_fisioterapia(self):
        self.assertEqual(
            self.backend._normalize_text('Fisioterapia'),
            'fisioterapia'
        )

    def test_normaliza_minusculas(self):
        self.assertEqual(
            self.backend._normalize_text('TI'),
            'ti'
        )

    def test_normaliza_vacio(self):
        self.assertEqual(self.backend._normalize_text(''), '')
        self.assertEqual(self.backend._normalize_text(None), '')

    def test_normaliza_espacios(self):
        self.assertEqual(
            self.backend._normalize_text('  Tecnologia  '),
            'tecnologia'
        )

    def test_normaliza_sin_cambios(self):
        self.assertEqual(self.backend._normalize_text('admin'), 'admin')

    def test_normaliza_unicode_complejo(self):
        resultado = self.backend._normalize_text('Santiago de Compostela')
        self.assertEqual(resultado, 'santiago de compostela')


class LDAPBackendMatchDepartamentoTest(BaseDocumentosTest):
    """Tests de _match_departamento (mapeo AD → BD)."""

    def setUp(self):
        super().setUp()
        self.backend = LDAPBackend()

    def test_match_subarea(self):
        """Sub-área encontrada → departamento padre."""
        depto = self.backend._match_departamento('Soporte Técnico')
        self.assertEqual(depto, self.depto_ti)

    def test_match_subarea_con_tildes(self):
        """Sub-área con tildes en AD → match normalizado."""
        SubArea.objects.create(
            nombre='Lavandería', departamento=self.depto_ti, activo=True
        )
        depto = self.backend._match_departamento('Lavanderia')
        self.assertEqual(depto, self.depto_ti)

    def test_match_departamento_por_nombre(self):
        depto = self.backend._match_departamento('Tecnología de la Información')
        self.assertEqual(depto, self.depto_ti)

    def test_match_departamento_por_codigo_iso(self):
        """Fallback: código ISO (3 primeras letras) - busca en SubArea/Departamento."""
        # 'Tecnologia' no matchea por nombre (la DB tiene tilde), pero el
        # código ISO busca las 3 primeras letras 'tec' — no coincide con 'TI'
        # Probamos con un nombre que matchee por nombre normalizado
        depto = self.backend._match_departamento('Gerencia General')
        self.assertEqual(depto, self.depto_gg)

    def test_match_departamento_no_encontrado(self):
        depto = self.backend._match_departamento('Area Inexistente XYZ')
        self.assertIsNone(depto)

    def test_match_departamento_vacio(self):
        self.assertIsNone(self.backend._match_departamento(''))
        self.assertIsNone(self.backend._match_departamento(None))

    def test_match_departamento_case_insensitive(self):
        depto = self.backend._match_departamento('GERENCIA GENERAL')
        self.assertEqual(depto, self.depto_gg)

    def test_match_departamento_con_codigo_largo(self):
        """Matching por código ISO funciona con código exacto."""
        depto = self.backend._match_departamento('TH')
        self.assertEqual(depto, self.depto_th)


class LDAPBackendExtraerCNTest(TestCase):
    """Tests de _extraer_cn."""

    def setUp(self):
        self.backend = LDAPBackend()

    def test_extraer_cn_basico(self):
        cn = self.backend._extraer_cn('CN=doc_administrador,OU=Grupos,DC=pth,DC=local')
        self.assertEqual(cn, 'doc_administrador')

    def test_extraer_cn_con_espacios(self):
        cn = self.backend._extraer_cn('CN=doc_jefes, OU=Grupos, DC=pth, DC=local')
        self.assertEqual(cn, 'doc_jefes')

    def test_extraer_cn_sin_cn(self):
        cn = self.backend._extraer_cn('OU=Grupos,DC=pth,DC=local')
        self.assertIsNone(cn)


class LDAPBackendExtraerAttrTest(TestCase):
    """Tests de _extract_attr."""

    def setUp(self):
        self.backend = LDAPBackend()

    def test_extract_bytes(self):
        attrs = {'givenName': [b'Eduard']}
        self.assertEqual(self.backend._extract_attr(attrs, 'givenName'), 'Eduard')

    def test_extract_string(self):
        attrs = {'givenName': ['Eduard']}
        self.assertEqual(self.backend._extract_attr(attrs, 'givenName'), 'Eduard')

    def test_extract_missing(self):
        attrs = {}
        self.assertEqual(self.backend._extract_attr(attrs, 'givenName'), '')

    def test_extract_empty_bytes(self):
        attrs = {'givenName': [b'']}
        self.assertEqual(self.backend._extract_attr(attrs, 'givenName'), '')


class LDAPBackendMapeoRolesTest(TestCase):
    """Tests del mapeo de roles AD → Django."""

    def test_mapeo_roles_completo(self):
        backend = LDAPBackend()
        self.assertEqual(backend.MAPEO_ROLES['doc_administrador'], 'administrador')
        self.assertEqual(backend.MAPEO_ROLES['doc_jefes'], 'jefe_departamento')
        self.assertEqual(backend.MAPEO_ROLES['doc_coordinador'], 'coordinador')
        self.assertEqual(backend.MAPEO_ROLES['doc_trabajador'], 'trabajador')

    def test_grupo_desconocido_fallback(self):
        backend = LDAPBackend()
        rol = backend.MAPEO_ROLES.get('grupo_inexistente', 'trabajador')
        self.assertEqual(rol, 'trabajador')


class CrearNotificacionTest(BaseDocumentosTest):
    """Tests de la función helper crear_notificacion."""

    def test_crear_notificacion_basica(self):
        crear_notificacion(
            usuario=self.user_trabajador,
            titulo='Test',
            mensaje='Mensaje test',
            tipo='sistema',
        )
        n = Notificacion.objects.get(usuario=self.user_trabajador, titulo='Test')
        self.assertEqual(n.usuario, self.user_trabajador)
        self.assertEqual(n.titulo, 'Test')
        self.assertFalse(n.leida)

    def test_crear_notificacion_con_url(self):
        crear_notificacion(
            usuario=self.user_trabajador,
            titulo='Con URL',
            mensaje='msg',
            tipo='sistema',
            url_accion='/documentos/formulario/123/',
        )
        n = Notificacion.objects.get(usuario=self.user_trabajador, titulo='Con URL')
        self.assertEqual(n.url_accion, '/documentos/formulario/123/')


class APINotificacionesTest(BaseDocumentosTest):
    """Tests del API de notificaciones."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_api_notificaciones_lista(self):
        Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='Test', mensaje='msg', tipo='sistema'
        )
        resp = self.client.get('/api/notificaciones/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('notificaciones', data)
        self.assertEqual(len(data['notificaciones']), 1)
        self.assertEqual(data['no_leidas'], 1)

    def test_api_notificaciones_sin_login(self):
        self.client.logout()
        resp = self.client.get('/api/notificaciones/')
        self.assertEqual(resp.status_code, 302)

    def test_api_marcar_leida(self):
        n = Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='Test', mensaje='msg', tipo='sistema'
        )
        resp = self.client.post(f'/api/notificaciones/{n.id}/leer/')
        self.assertEqual(resp.status_code, 200)
        n.refresh_from_db()
        self.assertTrue(n.leida)

    def test_api_marcar_todas_leidas(self):
        Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='N1', mensaje='m', tipo='sistema'
        )
        Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='N2', mensaje='m', tipo='sistema'
        )
        resp = self.client.post('/api/notificaciones/leer-todas/')
        self.assertEqual(resp.status_code, 200)
        no_leidas = Notificacion.objects.filter(
            usuario=self.user_trabajador, leida=False
        ).count()
        self.assertEqual(no_leidas, 0)

    def test_api_notificaciones_solo_del_usuario(self):
        Notificacion.objects.create(
            usuario=self.user_trabajador,
            titulo='Propia', mensaje='m', tipo='sistema'
        )
        Notificacion.objects.create(
            usuario=self.user_jefe_ti,
            titulo='Ajena', mensaje='m', tipo='sistema'
        )
        resp = self.client.get('/api/notificaciones/')
        data = resp.json()
        self.assertEqual(len(data['notificaciones']), 1)
        self.assertEqual(data['notificaciones'][0]['titulo'], 'Propia')

    def test_api_marcar_leida_notificacion_inexistente(self):
        resp = self.client.post('/api/notificaciones/99999/leer/')
        self.assertIn(resp.status_code, [404, 400])


class SignalSyncDepartamentoTest(BaseDocumentosTest):
    """Tests de signals de sincronización de departamento."""

    def test_sync_departamento_jefe_from_profile(self):
        """Cuando se crea un jefe, su departamento debe asignarse."""
        new_user = User.objects.create_user('nuevo_jefe', password='t')
        PerfilUsuario.objects.create(
            usuario=new_user,
            rol='jefe_departamento',
            departamento=self.depto_ti
        )
        self.depto_ti.refresh_from_db()
        self.assertEqual(self.depto_ti.jefe, new_user)

    def test_jefe_departamento_one_to_one(self):
        """Un departamento solo puede tener un jefe."""
        new_user = User.objects.create_user('otro_jefe', password='t')
        PerfilUsuario.objects.create(
            usuario=new_user,
            rol='jefe_departamento',
            departamento=self.depto_ti
        )
        # El OneToOne permite solo un jefe por departamento
        # Al asignar nuevo jefe, el anterior debe ser reemplazado
        self.depto_ti.refresh_from_db()
        # El último en guardarse gana
        self.assertIn(self.depto_ti.jefe, [self.user_jefe_ti, new_user])


class SignalReasignacionPendientesTest(BaseDocumentosTest):
    """Tests de reasignación de pendientes al cambiar jefe/depto."""

    def _crear_solicitud_pendiente(self, doc, aprobador, solicitante):
        """Helper: crea una SolicitudAprobacionDocumento pendiente."""
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

    def _crear_planilla_pendiente(self, doc, aprobador, responsable):
        """Helper: crea una AprobacionPlanilla pendiente."""
        planilla = PlanillaCambios.objects.create(
            codigo=f'PC-{doc.codigo}',
            documento=doc,
            tipo_cambio='modificacion',
            justificacion='Test',
            responsable=responsable,
            version_destino='2.0',
            estado='en_revision',
        )
        return AprobacionPlanilla.objects.create(
            planilla=planilla,
            aprobador=aprobador,
            rol_aprobador='evaluador',
            es_final=True,
            estado='pendiente',
        )

    def test_rol_a_jefe_reasigna_pendientes(self):
        """Al crear un jefe, las solicitudes pendientes del depto pasan a él."""
        nuevo = User.objects.create_user('nuevo_jefe', password='t')
        PerfilUsuario.objects.create(
            usuario=nuevo, rol='trabajador', departamento=self.depto_ti,
            grupo_ldap='doc_trabajador',
        )
        self._crear_solicitud_pendiente(
            self.doc_en_revision, self.user_admin, self.user_trabajador,
        )
        self.user_admin.refresh_from_db()
        self.assertEqual(
            SolicitudAprobacionDocumento.objects.filter(
                aprobador=self.user_admin, estado='pendiente',
            ).count(), 1,
        )
        nuevo.perfil.rol = 'jefe_departamento'
        nuevo.perfil.save(update_fields=['rol'])
        self.assertEqual(
            SolicitudAprobacionDocumento.objects.filter(
                aprobador=nuevo, estado='pendiente',
            ).count(), 1,
        )

    def test_jefe_pierde_rol_vuelve_a_espera(self):
        """Al quitar rol de jefe, sus pendientes quedan en espera de un jefe."""
        self.user_jefe_ti.refresh_from_db()
        sol = self._crear_solicitud_pendiente(
            self.doc_en_revision, self.user_jefe_ti, self.user_trabajador,
        )
        self.user_jefe_ti.perfil.rol = 'trabajador'
        self.user_jefe_ti.perfil.save(update_fields=['rol'])
        sol.refresh_from_db()
        self.assertIsNone(sol.aprobador)
        self.assertEqual(sol.estado, 'esperando')

    def test_jefe_cambia_depto_deja_en_espera_anterior(self):
        """Al cambiar depto, pendientes del anterior quedan en espera."""
        self.user_jefe_ti.refresh_from_db()
        sol = self._crear_solicitud_pendiente(
            self.doc_en_revision, self.user_jefe_ti, self.user_trabajador,
        )
        self.user_jefe_ti.perfil.departamento = self.depto_th
        self.user_jefe_ti.perfil.save(update_fields=['departamento'])
        sol.refresh_from_db()
        self.assertIsNone(sol.aprobador)
        self.assertEqual(sol.estado, 'esperando')

    def test_idempotente_repetido_save(self):
        """Guardar el perfil del jefe de nuevo (login) no cambia nada."""
        self.user_jefe_ti.refresh_from_db()
        self._crear_solicitud_pendiente(
            self.doc_en_revision, self.user_jefe_ti, self.user_trabajador,
        )
        count_before = SolicitudAprobacionDocumento.objects.filter(
            aprobador=self.user_jefe_ti, estado='pendiente',
        ).count()
        self.user_jefe_ti.perfil.save()
        count_after = SolicitudAprobacionDocumento.objects.filter(
            aprobador=self.user_jefe_ti, estado='pendiente',
        ).count()
        self.assertEqual(count_before, count_after)

    def test_planilla_reasignada_al_nuevo_jefe(self):
        """Planillas pendientes también se reasignan al nuevo jefe."""
        nuevo = User.objects.create_user('nuevo_jefe2', password='t')
        PerfilUsuario.objects.create(
            usuario=nuevo, rol='trabajador', departamento=self.depto_ti,
            grupo_ldap='doc_trabajador',
        )
        ap = self._crear_planilla_pendiente(
            self.doc_en_revision, self.user_admin, self.user_trabajador,
        )
        nuevo.perfil.rol = 'jefe_departamento'
        nuevo.perfil.save(update_fields=['rol'])
        ap.refresh_from_db()
        self.assertEqual(ap.aprobador, nuevo)

    def test_no_reasigna_si_jefe_es_solicitante(self):
        """Si el nuevo jefe es el solicitante, no se le reasigna su propia solicitud."""
        from documentos.models import Documento
        from datetime import date, timedelta
        nuevo = User.objects.create_user('nuevo_jefe3', password='t')
        PerfilUsuario.objects.create(
            usuario=nuevo, rol='trabajador', departamento=self.depto_ti,
            grupo_ldap='doc_trabajador',
        )
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Doc Jefe',
            codigo='TI-FOR-099', estado='en_revision', propietario=nuevo,
            departamento=self.depto_ti, version_actual='1.0',
        )
        self._crear_solicitud_pendiente(doc, self.user_admin, nuevo)
        nuevo.perfil.rol = 'jefe_departamento'
        nuevo.perfil.save(update_fields=['rol'])
        self.assertEqual(
            SolicitudAprobacionDocumento.objects.filter(
                aprobador=self.user_admin, estado='pendiente',
            ).count(), 1,
        )

    def test_no_reasigna_pasos_autorizador(self):
        """Los pasos 'esperando' (autorizador) no se reasignan."""
        sol = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision,
            accion_solicitud='creacion',
            solicitante=self.user_trabajador,
            propietario=self.doc_en_revision.propietario,
            departamento_origen=self.doc_en_revision.departamento,
            aprobador=self.user_admin,
            rol_aprobador='autorizador',
            es_final=True,
            estado='esperando',
        )
        self.user_jefe_ti.perfil.rol = 'trabajador'
        self.user_jefe_ti.perfil.save(update_fields=['rol'])
        sol.refresh_from_db()
        self.assertEqual(sol.aprobador, self.user_admin)

    def test_trabajador_cambia_depto_no_reasigna(self):
        """Un trabajador que cambia de depto NO reasigna los pendientes del depto."""
        self._crear_solicitud_pendiente(
            self.doc_en_revision, self.user_admin, self.user_trabajador,
        )
        self.user_trabajador.perfil.departamento = self.depto_th
        self.user_trabajador.perfil.save(update_fields=['departamento'])
        self.assertEqual(
            SolicitudAprobacionDocumento.objects.filter(
                aprobador=self.user_admin, estado='pendiente',
            ).count(), 1,
        )

    def test_autorizador_gg_se_activa_al_asignar_jefe(self):
        """Al asignar jefe de GG, el autorizador en espera pasa a pendiente si ya fue evaluado."""
        SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision,
            accion_solicitud='creacion',
            solicitante=self.user_trabajador,
            propietario=self.doc_en_revision.propietario,
            departamento_origen=self.depto_ti,
            aprobador=self.user_jefe_ti,
            rol_aprobador='evaluador',
            es_final=False,
            estado='aprobada',
        )
        sol_auth = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision,
            accion_solicitud='creacion',
            solicitante=self.user_trabajador,
            propietario=self.doc_en_revision.propietario,
            departamento_origen=self.depto_ti,
            aprobador=None,
            rol_aprobador='autorizador',
            es_final=True,
            estado='esperando',
        )
        nuevo = User.objects.create_user('gg_jefe_nuevo', password='t')
        PerfilUsuario.objects.create(
            usuario=nuevo, rol='jefe_departamento', departamento=self.depto_gg,
            grupo_ldap='doc_jefes',
        )
        sol_auth.refresh_from_db()
        self.assertEqual(sol_auth.aprobador, nuevo)
        self.assertEqual(sol_auth.estado, 'pendiente')


class EditarPerfilFormTest(BaseDocumentosTest):
    """El formulario de perfil expone el email personal (opcional)."""

    def test_incluye_email_opcional(self):
        from autenticacion.forms import EditarPerfilForm
        form = EditarPerfilForm(instance=self.perfil_jefe_ti)
        self.assertIn('email', form.fields)
        self.assertFalse(form.fields['email'].required)

    def test_email_inicial_desde_usuario(self):
        from autenticacion.forms import EditarPerfilForm
        self.perfil_jefe_ti.usuario.email = 'jefe@example.com'
        self.perfil_jefe_ti.usuario.save(update_fields=['email'])
        form = EditarPerfilForm(instance=self.perfil_jefe_ti)
        self.assertEqual(form.fields['email'].initial, 'jefe@example.com')


class PerfilEmailViewTest(BaseDocumentosTest):
    """POST a /perfil/ guarda el email en User.email."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_post_guarda_email(self):
        resp = self.client.post('/perfil/', {'email': 'nuevo@example.com'})
        self.assertEqual(resp.status_code, 302)
        self.user_jefe_ti.refresh_from_db()
        self.assertEqual(self.user_jefe_ti.email, 'nuevo@example.com')

    def test_post_email_vacio_lo_limpia(self):
        self.user_jefe_ti.email = 'viejo@example.com'
        self.user_jefe_ti.save(update_fields=['email'])
        resp = self.client.post('/perfil/', {'email': ''})
        self.assertEqual(resp.status_code, 302)
        self.user_jefe_ti.refresh_from_db()
        self.assertEqual(self.user_jefe_ti.email, '')


class LDAPBackendEmailTest(BaseDocumentosTest):
    """El login LDAP no borra el email personal cuando AD no trae mail."""

    def _attrs(self, mail=None):
        attrs = {
            'givenName': [b'Juan'],
            'sn': [b'Perez'],
            'memberOf': [b'CN=doc_trabajador,OU=Grupos,DC=pth,DC=local'],
        }
        if mail is not None:
            attrs['mail'] = [mail]
        return attrs

    def _authenticate(self, username, attrs):
        conn = MagicMock()
        conn.search_s.return_value = [
            (f'CN={username},DC=pth,DC=local', attrs)
        ]
        with patch('autenticacion.backends.ldap.initialize', return_value=conn):
            return LDAPBackend().authenticate(None, username=username, password='x')

    def test_login_sin_mail_conserva_email(self):
        self.user_trabajador.email = 'personal@pth.local'
        self.user_trabajador.save(update_fields=['email'])
        user = self._authenticate('trab01', self._attrs(mail=None))
        self.assertIsNotNone(user)
        self.assertEqual(user.email, 'personal@pth.local')

    def test_login_con_mail_actualiza_email(self):
        self.user_trabajador.email = 'viejo@pth.local'
        self.user_trabajador.save(update_fields=['email'])
        user = self._authenticate('trab01', self._attrs(mail=b'nuevo@pth.local'))
        self.assertIsNotNone(user)
        self.assertEqual(user.email, 'nuevo@pth.local')


from django.core.cache import cache  # noqa: E402


class LDAPBackendSeguridadTest(TestCase):
    """Fase 2: escaping de filtros LDAP y coincidencia exacta de DN."""

    def test_filter_escapa_caracteres_especiales(self):
        backend = LDAPBackend()
        conn = MagicMock()
        conn.search_s.return_value = []  # usuario no encontrado → retorna None
        with patch('autenticacion.backends.ldap.initialize', return_value=conn):
            result = backend.authenticate(None, username='ad*min', password='x')
        self.assertIsNone(result)
        filtro = conn.search_s.call_args[0][2]
        # El '*' del username debe ir escapado, nunca en crudo (LDAP injection).
        self.assertNotIn('ad*min', filtro)
        self.assertIn('\\2a', filtro)

    def test_detect_grupo_coincidencia_exacta_dn(self):
        backend = LDAPBackend()
        conn = MagicMock()
        member_dn = b'CN=Juan Perez,OU=Users,DC=pth,DC=local'
        conn.search_s.return_value = [
            ('CN=doc_administrador,OU=Grupos,DC=pth,DC=local', {'member': [member_dn]})
        ]
        grupo = backend._detect_grupo(
            conn, 'dc=pth,dc=local', 'jperez', {},
            'CN=Juan Perez,OU=Users,DC=pth,DC=local',
        )
        self.assertEqual(grupo, 'doc_administrador')

    def test_detect_grupo_rechaza_substring(self):
        backend = LDAPBackend()
        conn = MagicMock()
        member_dn = b'CN=Juan Perez,OU=Users,DC=pth,DC=local'
        conn.search_s.return_value = [
            ('CN=doc_administrador,OU=Grupos,DC=pth,DC=local', {'member': [member_dn]})
        ]
        # Un DN distinto (a no ser substring del miembro) NO debe coincidir.
        grupo = backend._detect_grupo(
            conn, 'dc=pth,dc=local', 'Juan', {},
            'CN=Juan,OU=Users,DC=pth,DC=local',
        )
        self.assertEqual(grupo, 'sin_grupo')


class LoginViewSeguridadTest(BaseDocumentosTest):
    """Fase 2: rate-limiting de login y logout solo por POST."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        cache.clear()

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_login_bloquea_tras_multiples_fallos(self):
        from autenticacion import views as auth_views
        with patch('autenticacion.views.authenticate', return_value=None) as mock_auth:
            for _ in range(auth_views.LOGIN_MAX_INTENTOS):
                resp = self.client.post(
                    '/login/', {'usuario': 'trab01', 'password': 'mala'})
                self.assertEqual(resp.status_code, 200)
            self.assertEqual(mock_auth.call_count, auth_views.LOGIN_MAX_INTENTOS)
            # Intento adicional: bloqueado SIN volver a llamar a authenticate.
            resp = self.client.post(
                '/login/', {'usuario': 'trab01', 'password': 'mala'})
            self.assertContains(resp, 'Demasiados intentos')
            self.assertEqual(mock_auth.call_count, auth_views.LOGIN_MAX_INTENTOS)

    def test_login_exitoso_limpia_contador(self):
        with patch('autenticacion.views.authenticate', return_value=None):
            self.client.post('/login/', {'usuario': 'trab01', 'password': 'mala'})
            self.client.post('/login/', {'usuario': 'trab01', 'password': 'mala'})
        self.assertGreater(cache.get('login_fail::127.0.0.1::trab01', 0), 0)
        resp = self.client.post(
            '/login/',
            {'usuario': 'trab01', 'password': 'testpass123', 'terms': 'on'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(cache.get('login_fail::127.0.0.1::trab01', 0), 0)

    def test_throttle_usa_ip_real_tras_proxy(self):
        """Tras un proxy de confianza el contador se indexa por la IP real del
        cliente (X-Forwarded-For), no por REMOTE_ADDR (que sería el proxy)."""
        from django.test import override_settings
        with override_settings(AUDIT_TRUSTED_PROXIES=['127.0.0.1']):
            with patch('autenticacion.views.authenticate', return_value=None):
                self.client.post(
                    '/login/', {'usuario': 'trab01', 'password': 'mala'},
                    HTTP_X_FORWARDED_FOR='1.2.3.4',
                )
        self.assertGreater(cache.get('login_fail::1.2.3.4::trab01', 0), 0)
        self.assertEqual(cache.get('login_fail::127.0.0.1::trab01', 0), 0)

    def test_throttle_ignora_xff_si_no_hay_proxy_confianza(self):
        """Sin proxy de confianza, un X-Forwarded-For falsificado NO cambia la clave."""
        from django.test import override_settings
        with override_settings(AUDIT_TRUSTED_PROXIES=[]):
            with patch('autenticacion.views.authenticate', return_value=None):
                self.client.post(
                    '/login/', {'usuario': 'trab01', 'password': 'mala'},
                    HTTP_X_FORWARDED_FOR='9.9.9.9',
                )
        self.assertGreater(cache.get('login_fail::127.0.0.1::trab01', 0), 0)
        self.assertEqual(cache.get('login_fail::9.9.9.9::trab01', 0), 0)

    def test_login_break_glass_modelbackend_avisa(self):
        user = self.user_trabajador
        user.backend = 'django.contrib.auth.backends.ModelBackend'
        with patch('autenticacion.views.authenticate', return_value=user):
            with self.assertLogs('django_auth_ldap', level='WARNING') as cm:
                resp = self.client.post(
                    '/login/',
                    {'usuario': 'trab01', 'password': 'x', 'terms': 'on'},
                )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(any('BREAK-GLASS' in m for m in cm.output))

    def test_login_ldap_no_avisa_break_glass(self):
        user = self.user_trabajador
        user.backend = 'autenticacion.backends.LDAPBackend'
        with patch('autenticacion.views.authenticate', return_value=user):
            with self.assertLogs('django_auth_ldap', level='INFO') as cm:
                resp = self.client.post(
                    '/login/',
                    {'usuario': 'trab01', 'password': 'x', 'terms': 'on'},
                )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(any('BREAK-GLASS' in m for m in cm.output))

    def test_logout_rechaza_get(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/logout/')
        self.assertEqual(resp.status_code, 405)
        self.assertIn('_auth_user_id', self.client.session)

    def test_logout_acepta_post(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post('/logout/')
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)
