"""Tests de la integración con RRHH (cédula, cargo y nombres reales).

Nunca se conecta a SQL Server ni a AD reales: `pymssql`/`ldap` se mockean.
"""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest

from autenticacion.backends import LDAPBackend
from autenticacion.models import PerfilUsuario
from autenticacion.services import rrhh_sync
from autenticacion.services.rrhh_sync import (
    RRHHConexionError, _CONSULTA_EMPLEADO, _CONSULTA_MASIVA,
    consultar_empleado_por_cedula, enmascarar_cedula, normalizar_cedula,
    rrhh_habilitado, sincronizar_todo_rrhh, validar_y_limpiar_cedula,
)
from autenticacion.tasks import sincronizar_cargos_rrhh
from documentos.models import _cargo_usuario

RRHH_ACTIVO = dict(RRHH_SQLSERVER_HOST='sql.test', RRHH_SQLSERVER_USER='u')


class _FakeCursor:
    def __init__(self, filas, capturado):
        self._filas = filas
        self._capturado = capturado

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        self._capturado['sql'] = sql
        self._capturado['params'] = params

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None


class _FakeConexion:
    def __init__(self, filas):
        self.filas = filas
        self.capturado = {}
        self.cerrada = False

    def cursor(self):
        return _FakeCursor(self.filas, self.capturado)

    def close(self):
        self.cerrada = True


class _FakeLdap:
    def __init__(self, attrs):
        self.attrs = attrs
        self.protocol_version = None

    def set_option(self, *args):
        pass

    def simple_bind_s(self, dn, password):
        pass

    def search_s(self, base, scope, filtro, atributos=None):
        return [('CN=usuario,DC=test', self.attrs)]

    def unbind_s(self):
        pass


class CedulaUtilsTest(TestCase):
    def test_normalizar(self):
        self.assertEqual(normalizar_cedula('V-12.345.678'), '12345678')
        self.assertEqual(normalizar_cedula(None), '')

    def test_validar_rango(self):
        self.assertEqual(validar_y_limpiar_cedula('V-12.345.678'), '12345678')
        self.assertEqual(validar_y_limpiar_cedula('012345678'), '12345678')
        for invalida in ('', 'abc', '123', '999999', '1234567890'):
            with self.assertRaises(ValueError):
                validar_y_limpiar_cedula(invalida)

    def test_enmascarar(self):
        self.assertEqual(enmascarar_cedula('V-12.345.678'), '***5678')
        self.assertEqual(enmascarar_cedula(''), '')

    def test_consultas_solo_activos(self):
        self.assertIn('a.activo = 1', _CONSULTA_EMPLEADO)
        self.assertIn('a.activo = 1', _CONSULTA_MASIVA)


@override_settings(**RRHH_ACTIVO)
class ConsultaRRHHTest(TestCase):
    def test_parametro_string_y_fila(self):
        conexion = _FakeConexion([{
            'nombre': 'Ana', 'apellido': 'Pérez', 'cedula': '12345678',
            'cargo': 'Analista', 'departamento': 'TI',
        }])
        with patch(
            'autenticacion.services.rrhh_sync._conectar',
            return_value=conexion,
        ):
            datos = consultar_empleado_por_cedula('V-12.345.678')

        self.assertEqual(conexion.capturado['params'], ('12345678',))
        self.assertIn('a.activo = 1', conexion.capturado['sql'])
        self.assertEqual(datos['cargo'], 'Analista')
        self.assertEqual(datos['cedula'], '12345678')
        self.assertTrue(conexion.cerrada)

    def test_no_encontrado(self):
        conexion = _FakeConexion([])
        with patch(
            'autenticacion.services.rrhh_sync._conectar',
            return_value=conexion,
        ):
            self.assertIsNone(consultar_empleado_por_cedula('12345678'))

    def test_error_de_conexion(self):
        with patch(
            'autenticacion.services.rrhh_sync._conectar',
            side_effect=RuntimeError('sin red'),
        ):
            with self.assertRaises(RRHHConexionError):
                consultar_empleado_por_cedula('12345678')


class SincronizarRRHHTest(BaseDocumentosTest):
    def _perfil(self, username='trab01'):
        return PerfilUsuario.objects.get(usuario__username=username)

    def _fila(self, cedula='12345678'):
        return {
            'cedula': cedula, 'nombre': 'Ana', 'apellido': 'Pérez',
            'cargo': 'Analista de Soporte', 'departamento': 'Talento Humano',
        }

    def test_rrhh_habilitado(self):
        self.assertFalse(rrhh_habilitado())
        with override_settings(**RRHH_ACTIVO):
            self.assertTrue(rrhh_habilitado())

    def test_deshabilitado_no_conecta(self):
        with patch('autenticacion.services.rrhh_sync._conectar') as conectar:
            resumen = sincronizar_todo_rrhh()
            tarea = sincronizar_cargos_rrhh()
        self.assertEqual(resumen['errores'], 1)
        self.assertEqual(tarea, {'omitido': True})
        conectar.assert_not_called()

    @override_settings(**RRHH_ACTIVO)
    def test_sincroniza_cargo_nombres_y_flags(self):
        perfil = self._perfil()
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cedula_identidad='012345678',
        )
        with patch(
            'autenticacion.services.rrhh_sync._leer',
            return_value=[self._fila()],
        ):
            resumen = sincronizar_todo_rrhh()

        perfil.refresh_from_db()
        usuario = perfil.usuario
        usuario.refresh_from_db()
        self.assertEqual(perfil.cedula_identidad, '12345678')
        self.assertEqual(perfil.cargo, 'Analista de Soporte')
        self.assertEqual(perfil.departamento_rrhh, 'Talento Humano')
        self.assertTrue(perfil.cedula_verificada_rrhh)
        self.assertIsNotNone(perfil.ultimo_sync_rrhh)
        self.assertEqual(usuario.first_name, 'Ana')
        self.assertEqual(usuario.last_name, 'Pérez')
        self.assertEqual(resumen['total_rrhh'], 1)
        self.assertEqual(resumen['perfiles_actualizados'], 1)
        self.assertGreaterEqual(resumen['sin_cedula'], 1)

    @override_settings(**RRHH_ACTIVO)
    def test_dry_run_no_escribe(self):
        perfil = self._perfil()
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cedula_identidad='12345678', cargo='', departamento_rrhh='',
        )
        with patch(
            'autenticacion.services.rrhh_sync._leer',
            return_value=[self._fila()],
        ):
            resumen = sincronizar_todo_rrhh(dry_run=True)

        perfil.refresh_from_db()
        self.assertEqual(perfil.cargo, '')
        self.assertFalse(perfil.cedula_verificada_rrhh)
        self.assertEqual(resumen['perfiles_actualizados'], 1)

    @override_settings(**RRHH_ACTIVO)
    def test_sin_match(self):
        perfil = self._perfil()
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cedula_identidad='12345678',
        )
        with patch(
            'autenticacion.services.rrhh_sync._leer',
            return_value=[self._fila(cedula='99999999')],
        ):
            resumen = sincronizar_todo_rrhh()

        perfil.refresh_from_db()
        self.assertEqual(perfil.cargo, '')
        self.assertGreaterEqual(resumen['sin_match'], 1)


@override_settings(**RRHH_ACTIVO)
class PerfilCedulaFlowTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')
        self.url = reverse('autenticacion:perfil')
        self.datos = {
            'nombre': 'Ana', 'apellido': 'Pérez', 'cedula': '12345678',
            'cargo': 'Analista de Soporte',
            'departamento': 'Talento Humano',
        }

    def _buscar(self, cedula='V-12.345.678', follow=False):
        return self.client.post(
            self.url,
            {'accion': 'buscar_cedula', 'cedula_identidad': cedula},
            follow=follow,
        )

    def _confirmar(self):
        return self.client.post(
            self.url, {'accion': 'confirmar_cedula'}, follow=True,
        )

    def test_dos_pasos_guarda_y_bloquea(self):
        with patch(
            'autenticacion.services.rrhh_sync.'
            'consultar_empleado_por_cedula',
            return_value=self.datos,
        ):
            resp = self._buscar()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Confirmar y guardar')

        resp = self._confirmar()
        self.assertEqual(resp.status_code, 200)

        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        self.assertEqual(perfil.cedula_identidad, '12345678')
        self.assertEqual(perfil.cargo, 'Analista de Soporte')
        self.assertTrue(perfil.cedula_verificada_rrhh)
        self.assertIsNotNone(perfil.ultimo_sync_rrhh)
        self.assertEqual(perfil.usuario.first_name, 'Ana')
        self.assertEqual(perfil.usuario.last_name, 'Pérez')

        resp = self.client.get(self.url)
        self.assertContains(resp, 'disabled')

    def test_no_encontrado_no_guarda(self):
        with patch(
            'autenticacion.services.rrhh_sync.'
            'consultar_empleado_por_cedula',
            return_value=None,
        ):
            resp = self._buscar()
        self.assertContains(resp, 'No encontramos esa cédula')
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        self.assertEqual(perfil.cedula_identidad, '')

    def test_caida_rrhh_guarda_sin_verificar(self):
        with patch(
            'autenticacion.services.rrhh_sync.'
            'consultar_empleado_por_cedula',
            side_effect=RRHHConexionError('caído'),
        ):
            resp = self._buscar(follow=True)
        self.assertContains(resp, 'No se pudo validar contra RRHH')
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        self.assertEqual(perfil.cedula_identidad, '12345678')
        self.assertFalse(perfil.cedula_verificada_rrhh)

    def test_confirmar_sin_sesion(self):
        resp = self._confirmar()
        self.assertContains(resp, 'La verificación expiró')

    def test_duplicada_no_guarda(self):
        otro = PerfilUsuario.objects.get(usuario__username='trab02')
        PerfilUsuario.objects.filter(pk=otro.pk).update(
            cedula_identidad='12345678',
        )
        resp = self._buscar()
        self.assertContains(resp, 'ya está registrada')

    def test_rrhh_deshabilitado_guarda(self):
        with override_settings(RRHH_SQLSERVER_HOST=''):
            resp = self._buscar(follow=True)
        self.assertContains(resp, 'Cédula registrada')
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        self.assertEqual(perfil.cedula_identidad, '12345678')
        self.assertFalse(perfil.cedula_verificada_rrhh)


@override_settings(EXIGIR_CEDULA_PERFIL=True)
class MiddlewareCedulaTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_redirige_sin_cedula(self):
        resp = self.client.get(reverse('autenticacion:dashboard'))
        self.assertRedirects(resp, reverse('autenticacion:perfil'))

    def test_perfil_no_genera_bucle(self):
        resp = self.client.get(reverse('autenticacion:perfil'))
        self.assertEqual(resp.status_code, 200)

    def test_permite_con_cedula(self):
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cedula_identidad='12345678',
        )
        resp = self.client.get(reverse('autenticacion:dashboard'))
        self.assertEqual(resp.status_code, 200)

    def test_api_documentos_exenta(self):
        resp = self.client.get('/documentos/api/contadores/')
        self.assertNotEqual(resp.status_code, 302)

    def test_options_exento(self):
        resp = self.client.options('/documentos/')
        self.assertNotEqual(resp.status_code, 302)

    def test_administrador_exento(self):
        self.client.logout()
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get(reverse('autenticacion:dashboard'))
        self.assertEqual(resp.status_code, 200)

    def test_superusuario_exento(self):
        root = User.objects.create_superuser(
            username='root_rrhh', password='testpass123',
            email='root@test.local',
        )
        PerfilUsuario.objects.create(usuario=root, rol='trabajador')
        self.client.logout()
        self.client.login(username='root_rrhh', password='testpass123')
        resp = self.client.get(reverse('autenticacion:dashboard'))
        self.assertEqual(resp.status_code, 200)


class AdminCedulaAuditTest(BaseDocumentosTest):
    def test_cambio_cedula_resetea_y_audita(self):
        from django.contrib.admin.sites import AdminSite

        from autenticacion.admin import PerfilUsuarioAdmin

        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cedula_identidad='12345678', cedula_verificada_rrhh=True,
            cargo='Analista', departamento_rrhh='TI',
        )
        perfil.refresh_from_db()
        perfil.cedula_identidad = 'V-99.999.999'

        admin_obj = PerfilUsuarioAdmin(PerfilUsuario, AdminSite())
        request = SimpleNamespace(user=self.user_admin)
        with patch('auditoria.services.registrar_log_iso') as auditar:
            admin_obj.save_model(request, perfil, form=None, change=True)

        perfil.refresh_from_db()
        self.assertEqual(perfil.cedula_identidad, '99999999')
        self.assertFalse(perfil.cedula_verificada_rrhh)
        self.assertEqual(perfil.cargo, '')
        self.assertEqual(perfil.departamento_rrhh, '')
        auditar.assert_called_once()
        kwargs = auditar.call_args.kwargs
        self.assertEqual(kwargs['estado_anterior'], '***5678')
        self.assertEqual(kwargs['estado_posterior'], '***9999')
        self.assertEqual(kwargs['detalles']['origen'], 'admin')


@override_settings(
    AUTH_LDAP_SERVER_URI='ldap://ldap.test',
    AUTH_LDAP_USER_DN='dc=test',
)
class LDAPNombresTest(BaseDocumentosTest):
    def _attrs(self):
        return {
            'givenName': [b'NombreAD'],
            'sn': [b'ApellidoAD'],
            'mail': [b'trab01@test.local'],
            'department': [b'Tecnologia'],
            'employeeID': [b'E-01'],
            'memberOf': [b'CN=doc_trabajador,OU=Grupos,DC=test'],
        }

    def _autenticar(self):
        conexion = _FakeLdap(self._attrs())
        with patch(
            'autenticacion.backends.ldap.initialize', return_value=conexion,
        ):
            return LDAPBackend().authenticate(
                None, username='trab01', password='secreta',
            )

    def test_no_pisa_nombres_verificados(self):
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cedula_identidad='12345678', cedula_verificada_rrhh=True,
        )
        User.objects.filter(username='trab01').update(
            first_name='Ana', last_name='Pérez',
        )

        user = self._autenticar()

        self.assertEqual(user.first_name, 'Ana')
        self.assertEqual(user.last_name, 'Pérez')

    def test_sin_verificar_si_actualiza_nombres(self):
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cedula_identidad='', cedula_verificada_rrhh=False,
        )
        User.objects.filter(username='trab01').update(
            first_name='Ana', last_name='Pérez',
        )

        user = self._autenticar()

        self.assertEqual(user.first_name, 'NombreAD')
        self.assertEqual(user.last_name, 'ApellidoAD')


class CargoUsuarioTest(BaseDocumentosTest):
    def test_cargo_rrhh_y_fallback(self):
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        self.assertEqual(
            _cargo_usuario(perfil.usuario),
            'Trabajador — TI - Tecnología de la Información',
        )
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cargo='Analista de Soporte',
        )
        usuario = User.objects.get(username='trab01')
        self.assertEqual(_cargo_usuario(usuario), 'Analista de Soporte')


class ComandoRRHHCedulaTest(BaseDocumentosTest):
    def test_deshabilitado_no_conecta(self):
        salida = StringIO()
        with patch('autenticacion.services.rrhh_sync._conectar') as conectar:
            call_command('sincronizar_cargos_rrhh', stdout=salida)
        self.assertIn('no configurado', salida.getvalue().lower())
        conectar.assert_not_called()


class AvisosCedulaYCargoTest(BaseDocumentosTest):
    """Aviso de cédula en el dashboard y cargo visible en el perfil."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_dashboard_avisa_sin_cedula(self):
        resp = self.client.get(reverse('autenticacion:dashboard'))
        self.assertContains(resp, 'Falta tu cédula de identidad')

    def test_dashboard_no_avisa_con_cedula(self):
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cedula_identidad='12345678',
        )
        resp = self.client.get(reverse('autenticacion:dashboard'))
        self.assertNotContains(resp, 'Falta tu cédula de identidad')

    def test_perfil_muestra_cargo_fallback(self):
        resp = self.client.get(reverse('autenticacion:perfil'))
        self.assertContains(
            resp, 'Trabajador — TI - Tecnología de la Información',
        )
        self.assertFalse(resp.context['cargo_es_rrhh'])
        self.assertNotContains(resp, '>RRHH</span>')

    def test_perfil_muestra_cargo_rrhh(self):
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cargo='Analista de Soporte',
            departamento_rrhh='Talento Humano',
        )
        resp = self.client.get(reverse('autenticacion:perfil'))
        self.assertContains(resp, 'Analista de Soporte')
        self.assertContains(resp, 'Talento Humano')
        self.assertTrue(resp.context['cargo_es_rrhh'])
        self.assertContains(resp, 'badge--info')
        self.assertContains(resp, '>RRHH</span>')

    def test_cargo_efectivo_con_espacios_usa_fallback(self):
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        PerfilUsuario.objects.filter(pk=perfil.pk).update(cargo='   ')
        resp = self.client.get(reverse('autenticacion:perfil'))
        self.assertContains(
            resp, 'Trabajador — TI - Tecnología de la Información',
        )
        self.assertFalse(resp.context['cargo_es_rrhh'])
        self.assertNotContains(resp, '>RRHH</span>')

    def test_superuser_sin_perfil_no_falla_ni_avisa(self):
        User.objects.create_superuser(
            username='root_sin_perfil',
            password='testpass123',
            email='root_sp@test.local',
        )
        self.client.logout()
        self.client.login(username='root_sin_perfil', password='testpass123')

        resp_dash = self.client.get(reverse('autenticacion:dashboard'))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertNotContains(resp_dash, 'Falta tu cédula de identidad')

        resp_perfil = self.client.get(reverse('autenticacion:perfil'))
        self.assertEqual(resp_perfil.status_code, 200)
        self.assertNotContains(resp_perfil, 'Falta tu cédula de identidad')

    @override_settings(EXIGIR_CEDULA_PERFIL=True)
    def test_admin_ve_aviso_sin_bloqueo(self):
        self.client.logout()
        self.client.login(username='admin01', password='testpass123')

        resp_dash = self.client.get(reverse('autenticacion:dashboard'))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertContains(resp_dash, 'Falta tu cédula de identidad')

        resp_perfil = self.client.get(reverse('autenticacion:perfil'))
        self.assertEqual(resp_perfil.status_code, 200)
        self.assertContains(resp_perfil, 'Falta tu cédula de identidad')


class DesvincularRRHHAdminTest(BaseDocumentosTest):
    """Gestión y reversión de datos RRHH desde /usuarios/, detalle y admin."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url_api = reverse('autenticacion:desvincular_rrhh_usuario')
        self.trab = User.objects.get(username='trab01')
        self.perfil_trab = self.trab.perfil

    def _poblar_rrhh(self):
        from django.utils import timezone

        User.objects.filter(pk=self.trab.pk).update(
            first_name='ANA MARIA',
            last_name='PEREZ',
        )
        PerfilUsuario.objects.filter(pk=self.perfil_trab.pk).update(
            cedula_identidad='12345678',
            cargo='Analista de Soporte',
            departamento_rrhh='Tecnología',
            cedula_verificada_rrhh=True,
            ultimo_sync_rrhh=timezone.now(),
        )
        self.trab.refresh_from_db()
        self.perfil_trab.refresh_from_db()

    def test_servicio_desvincular_limpia_e_idempotente(self):
        from autenticacion.services.rrhh_sync import (
            desvincular_datos_rrhh, tiene_datos_rrhh,
        )

        self._poblar_rrhh()
        self.assertTrue(tiene_datos_rrhh(self.perfil_trab))

        with patch('auditoria.services.registrar_log_iso') as auditar:
            res = desvincular_datos_rrhh(
                self.perfil_trab,
                actor=self.user_admin,
                origen='gestion_usuarios',
                limpiar_nombres=False,
            )

        self.assertTrue(res['modificado'])
        self.perfil_trab.refresh_from_db()
        self.trab.refresh_from_db()
        self.assertFalse(tiene_datos_rrhh(self.perfil_trab))
        self.assertEqual(self.perfil_trab.cedula_identidad, '')
        self.assertEqual(self.perfil_trab.cargo, '')
        self.assertEqual(self.perfil_trab.departamento_rrhh, '')
        self.assertFalse(self.perfil_trab.cedula_verificada_rrhh)
        self.assertIsNone(self.perfil_trab.ultimo_sync_rrhh)
        self.assertEqual(self.trab.first_name, 'ANA MARIA')
        self.assertEqual(self.trab.last_name, 'PEREZ')
        auditar.assert_called_once()
        kwargs = auditar.call_args.kwargs
        self.assertEqual(kwargs['estado_anterior'], '***5678')
        self.assertEqual(kwargs['estado_posterior'], '')
        self.assertEqual(kwargs['detalles']['accion'], 'desvincular_rrhh')

        with patch('auditoria.services.registrar_log_iso') as auditar_2:
            res_2 = desvincular_datos_rrhh(
                self.perfil_trab,
                actor=self.user_admin,
                origen='gestion_usuarios',
            )
        self.assertFalse(res_2['modificado'])
        auditar_2.assert_not_called()

    def test_endpoint_seguridad_y_validaciones(self):
        self._poblar_rrhh()
        self.client.login(username='trab01', password='testpass123')

        resp_perm = self.client.post(
            self.url_api,
            data='{"usuario_id": %d}' % self.trab.pk,
            content_type='application/json',
        )
        self.assertEqual(resp_perm.status_code, 403)
        self.perfil_trab.refresh_from_db()
        self.assertEqual(self.perfil_trab.cedula_identidad, '12345678')

        self.client.login(username='admin01', password='testpass123')
        self.assertEqual(self.client.get(self.url_api).status_code, 405)
        self.assertEqual(
            self.client.post(
                self.url_api, data='{mal', content_type='application/json',
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                self.url_api,
                data='{"usuario_id": true}',
                content_type='application/json',
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                self.url_api,
                data='{"usuario_id": 999999}',
                content_type='application/json',
            ).status_code,
            404,
        )

    def test_endpoint_admin_y_superuser_desvinculan(self):
        self._poblar_rrhh()
        self.client.login(username='admin01', password='testpass123')

        with patch('auditoria.services.registrar_log_iso'):
            resp = self.client.post(
                self.url_api,
                data='{"usuario_id": %d, "limpiar_nombres": true}' % self.trab.pk,
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.assertFalse(resp.json()['sin_cambios'])
        self.perfil_trab.refresh_from_db()
        self.trab.refresh_from_db()
        self.assertEqual(self.perfil_trab.cedula_identidad, '')
        self.assertEqual(self.perfil_trab.cargo, '')
        self.assertEqual(self.trab.first_name, '')
        self.assertEqual(self.trab.last_name, '')

        # Superuser sin PerfilUsuario también puede usar el panel y API
        User.objects.create_superuser(
            username='root_panel', password='testpass123', email='rp@test.local',
        )
        self._poblar_rrhh()
        self.client.login(username='root_panel', password='testpass123')
        with patch('auditoria.services.registrar_log_iso'):
            resp_root = self.client.post(
                self.url_api,
                data='{"usuario_id": %d}' % self.trab.pk,
                content_type='application/json',
            )
        self.assertEqual(resp_root.status_code, 200)
        self.assertTrue(resp_root.json()['success'])
        self.perfil_trab.refresh_from_db()
        self.assertEqual(self.perfil_trab.cedula_identidad, '')

    def test_vistas_gestionar_y_ver_usuario(self):
        self.client.login(username='admin01', password='testpass123')

        # Sin datos RRHH: el botón Limpiar RRHH no aparece para trab01 ni en detalle
        resp_detalle_vacio = self.client.get(
            reverse('autenticacion:ver_usuario', args=[self.trab.pk]),
        )
        self.assertEqual(resp_detalle_vacio.status_code, 200)
        self.assertNotContains(resp_detalle_vacio, 'Desvincular datos RRHH')

        self._poblar_rrhh()

        resp_lista = self.client.get(
            reverse('autenticacion:gestionar_usuarios'),
            {'buscar': '12345678', 'estado': 'verificada_rrhh'},
        )
        self.assertEqual(resp_lista.status_code, 200)
        self.assertContains(resp_lista, 'Analista de Soporte')
        self.assertContains(resp_lista, 'Limpiar RRHH')

        resp_sin_ced = self.client.get(
            reverse('autenticacion:gestionar_usuarios'),
            {'estado': 'sin_cedula', 'buscar': '12345678'},
        )
        self.assertEqual(resp_sin_ced.status_code, 200)
        self.assertNotContains(resp_sin_ced, 'Limpiar RRHH')

        resp_detalle = self.client.get(
            reverse('autenticacion:ver_usuario', args=[self.trab.pk]),
        )
        self.assertEqual(resp_detalle.status_code, 200)
        self.assertContains(resp_detalle, 'Analista de Soporte')
        self.assertContains(resp_detalle, '>RRHH</span>')
        self.assertContains(resp_detalle, 'Desvincular datos RRHH')

    def test_admin_action_desvincular_rrhh(self):
        from django.contrib.admin.sites import AdminSite

        from autenticacion.admin import PerfilUsuarioAdmin

        self._poblar_rrhh()
        admin_obj = PerfilUsuarioAdmin(PerfilUsuario, AdminSite())
        request = SimpleNamespace(user=self.user_admin)
        qs = PerfilUsuario.objects.filter(pk=self.perfil_trab.pk)

        with patch('auditoria.services.registrar_log_iso'), \
             patch.object(admin_obj, 'message_user') as msg:
            admin_obj.desvincular_rrhh_seleccionados(request, qs)

        self.perfil_trab.refresh_from_db()
        self.assertEqual(self.perfil_trab.cedula_identidad, '')
        self.assertEqual(self.perfil_trab.cargo, '')
        self.assertFalse(self.perfil_trab.cedula_verificada_rrhh)
        msg.assert_called_once()
        self.assertIn('1 perfil(es)', msg.call_args.args[1])


class AccionesDetalleUsuarioTest(BaseDocumentosTest):
    """Tarjeta de acciones en /usuarios/<id>/, vincular RRHH y toggle activo."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.trab = User.objects.get(username='trab01')
        self.perfil_trab = self.trab.perfil
        self.url_vincular = reverse('autenticacion:vincular_rrhh_usuario')
        self.url_toggle = reverse('autenticacion:toggle_activo_usuario')
        self.url_depto = reverse('autenticacion:asignar_departamento_usuario')

    def test_render_acciones_detalle_usuario(self):
        self.client.login(username='admin01', password='testpass123')

        # trab01 tiene documentos en BaseDocumentosTest pero sin datos RRHH
        resp = self.client.get(
            reverse('autenticacion:ver_usuario', args=[self.trab.pk]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<h3>Acciones</h3>')
        self.assertContains(resp, 'Asignar / cambiar departamento')
        self.assertContains(resp, 'Vincular cédula RRHH')
        self.assertContains(resp, 'Ver auditoría')
        self.assertContains(resp, 'Transferir documentos')
        self.assertContains(resp, 'Desactivar cuenta')
        self.assertNotContains(resp, 'Desvincular datos RRHH')

        # Usuario sin documentos ni asignaciones no muestra Transferir documentos
        nuevo = User.objects.create_user(
            username='sin_docs', password='testpass123',
        )
        PerfilUsuario.objects.create(usuario=nuevo, rol='trabajador')
        resp_nuevo = self.client.get(
            reverse('autenticacion:ver_usuario', args=[nuevo.pk]),
        )
        self.assertEqual(resp_nuevo.status_code, 200)
        self.assertNotContains(resp_nuevo, 'Transferir documentos')
        self.assertContains(resp_nuevo, 'Desactivar cuenta')

    def test_no_muestra_desactivar_para_si_mismo_ni_superuser(self):
        root = User.objects.create_superuser(
            username='root_dest', password='testpass123', email='rd@test.local',
        )
        self.client.login(username='admin01', password='testpass123')

        resp_self = self.client.get(
            reverse('autenticacion:ver_usuario', args=[self.user_admin.pk]),
        )
        self.assertEqual(resp_self.status_code, 200)
        self.assertNotContains(resp_self, 'Desactivar cuenta')

        resp_root = self.client.get(
            reverse('autenticacion:ver_usuario', args=[root.pk]),
        )
        self.assertEqual(resp_root.status_code, 200)
        self.assertContains(resp_root, 'Superusuario')
        self.assertNotContains(resp_root, 'Desactivar cuenta')

    def test_vincular_rrhh_validaciones_y_permisos(self):
        self.client.login(username='trab01', password='testpass123')
        self.assertEqual(
            self.client.post(
                self.url_vincular,
                data='{"usuario_id": %d, "cedula": "12345678"}' % self.trab.pk,
                content_type='application/json',
            ).status_code,
            403,
        )

        self.client.login(username='admin01', password='testpass123')
        self.assertEqual(self.client.get(self.url_vincular).status_code, 405)
        self.assertEqual(
            self.client.post(
                self.url_vincular,
                data='{"usuario_id": %d, "cedula": "123"}' % self.trab.pk,
                content_type='application/json',
            ).status_code,
            400,
        )

        otro = User.objects.create_user(username='otro_ced', password='x')
        PerfilUsuario.objects.create(
            usuario=otro, rol='trabajador', cedula_identidad='87654321',
        )
        resp_dup = self.client.post(
            self.url_vincular,
            data='{"usuario_id": %d, "cedula": "87654321"}' % self.trab.pk,
            content_type='application/json',
        )
        self.assertEqual(resp_dup.status_code, 400)
        self.assertIn('otro usuario', resp_dup.json()['error'])

    def test_vincular_rrhh_con_y_sin_rrhh_y_flujo_dos_pasos(self):
        self.client.login(username='admin01', password='testpass123')

        # Sin RRHH configurado: guarda cédula sin verificar
        with patch('auditoria.services.registrar_log_iso') as auditar:
            resp_off = self.client.post(
                self.url_vincular,
                data='{"usuario_id": %d, "cedula": "V-12.345.678"}' % self.trab.pk,
                content_type='application/json',
            )
        self.assertEqual(resp_off.status_code, 200)
        self.assertTrue(resp_off.json()['guardado'])
        self.perfil_trab.refresh_from_db()
        self.assertEqual(self.perfil_trab.cedula_identidad, '12345678')
        self.assertFalse(self.perfil_trab.cedula_verificada_rrhh)
        auditar.assert_called_once()

        # Con RRHH activo: error de conexión → 503; no encontrado → 404
        with override_settings(**RRHH_ACTIVO), \
             patch('autenticacion.services.rrhh_sync._conectar', side_effect=OSError('down')):
            resp_503 = self.client.post(
                self.url_vincular,
                data='{"usuario_id": %d, "cedula": "20000001"}' % self.trab.pk,
                content_type='application/json',
            )
        self.assertEqual(resp_503.status_code, 503)

        conexion_vacia = _FakeConexion([])
        with override_settings(**RRHH_ACTIVO), \
             patch('autenticacion.services.rrhh_sync._conectar', return_value=conexion_vacia), \
             patch('auditoria.services.registrar_log_iso'):
            resp_404 = self.client.post(
                self.url_vincular,
                data='{"usuario_id": %d, "cedula": "20000001"}' % self.trab.pk,
                content_type='application/json',
            )
        self.assertEqual(resp_404.status_code, 404)

        fila = {
            'nombre': 'LUIS ANTONIO',
            'apellido': 'GOMEZ PEREZ',
            'cedula': '20000001',
            'cargo': 'ANALISTA PROGRAMADOR DE SISTEMAS',
            'departamento': 'TECNOLOGIA DE LA INFORMACION',
        }
        # Paso 1 (confirmar=False): devuelve preview sin guardar
        with override_settings(**RRHH_ACTIVO), \
             patch('autenticacion.services.rrhh_sync._conectar', return_value=_FakeConexion([fila])), \
             patch('auditoria.services.registrar_log_iso'):
            resp_prev = self.client.post(
                self.url_vincular,
                data='{"usuario_id": %d, "cedula": "V-20.000.001", "confirmar": false}' % self.trab.pk,
                content_type='application/json',
            )
        self.assertEqual(resp_prev.status_code, 200)
        self.assertFalse(resp_prev.json()['guardado'])
        self.assertEqual(
            resp_prev.json()['preview']['cargo'],
            'ANALISTA PROGRAMADOR DE SISTEMAS',
        )
        self.perfil_trab.refresh_from_db()
        self.assertEqual(self.perfil_trab.cedula_identidad, '12345678')

        # Paso 2 (confirmar=True): guarda cédula, cargo, nombres y audita
        with override_settings(**RRHH_ACTIVO), \
             patch('autenticacion.services.rrhh_sync._conectar', return_value=_FakeConexion([fila])), \
             patch('auditoria.services.registrar_log_iso') as auditar:
            resp_ok = self.client.post(
                self.url_vincular,
                data='{"usuario_id": %d, "cedula": "20000001", "confirmar": true}' % self.trab.pk,
                content_type='application/json',
            )
        self.assertEqual(resp_ok.status_code, 200)
        self.assertTrue(resp_ok.json()['guardado'])
        self.perfil_trab.refresh_from_db()
        self.trab.refresh_from_db()
        self.assertEqual(self.perfil_trab.cedula_identidad, '20000001')
        self.assertEqual(self.perfil_trab.cargo, 'ANALISTA PROGRAMADOR DE SISTEMAS')
        self.assertTrue(self.perfil_trab.cedula_verificada_rrhh)
        self.assertEqual(self.trab.first_name, 'LUIS ANTONIO')
        self.assertEqual(self.trab.last_name, 'GOMEZ PEREZ')
        auditar.assert_called_once()
        self.assertEqual(auditar.call_args.kwargs['estado_posterior'], '***0001')

    def test_toggle_activo_guardias_y_superuser_depto(self):
        self.client.login(username='trab01', password='testpass123')
        self.assertEqual(
            self.client.post(
                self.url_toggle,
                data='{"usuario_id": %d}' % self.trab.pk,
                content_type='application/json',
            ).status_code,
            403,
        )

        self.client.login(username='admin01', password='testpass123')
        self.assertEqual(self.client.get(self.url_toggle).status_code, 405)

        # No puede desactivarse a sí mismo
        self.assertEqual(
            self.client.post(
                self.url_toggle,
                data='{"usuario_id": %d}' % self.user_admin.pk,
                content_type='application/json',
            ).status_code,
            400,
        )

        # No puede desactivar un superuser
        root = User.objects.create_superuser(
            username='root_adm', password='testpass123', email='ra@test.local',
        )
        self.assertEqual(
            self.client.post(
                self.url_toggle,
                data='{"usuario_id": %d}' % root.pk,
                content_type='application/json',
            ).status_code,
            403,
        )

        # Desactivar y reactivar trab01 con auditoría
        with patch('auditoria.services.registrar_log_iso') as auditar:
            resp_off = self.client.post(
                self.url_toggle,
                data='{"usuario_id": %d}' % self.trab.pk,
                content_type='application/json',
            )
        self.assertEqual(resp_off.status_code, 200)
        self.assertFalse(resp_off.json()['is_active'])
        self.trab.refresh_from_db()
        self.assertFalse(self.trab.is_active)
        self.assertEqual(auditar.call_args.kwargs['estado_anterior'], 'activo')
        self.assertEqual(auditar.call_args.kwargs['estado_posterior'], 'inactivo')

        with patch('auditoria.services.registrar_log_iso'):
            resp_on = self.client.post(
                self.url_toggle,
                data='{"usuario_id": %d}' % self.trab.pk,
                content_type='application/json',
            )
        self.assertEqual(resp_on.status_code, 200)
        self.assertTrue(resp_on.json()['is_active'])
        self.trab.refresh_from_db()
        self.assertTrue(self.trab.is_active)

        # Superuser sin PerfilUsuario también puede asignar departamento
        self.client.login(username='root_adm', password='testpass123')
        resp_depto = self.client.post(
            self.url_depto,
            data='{"usuario_id": %d, "departamento_id": %d}' % (self.trab.pk, self.depto_gg.pk),
            content_type='application/json',
        )
        self.assertEqual(resp_depto.status_code, 200)
        self.assertTrue(resp_depto.json()['success'])
        self.perfil_trab.refresh_from_db()
        self.assertEqual(self.perfil_trab.departamento_id, self.depto_gg.pk)
