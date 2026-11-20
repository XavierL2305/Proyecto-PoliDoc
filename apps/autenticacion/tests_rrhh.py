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
        self.assertEqual(normalizar_cedula('V-31.097.865'), '31097865')
        self.assertEqual(normalizar_cedula(None), '')

    def test_validar_rango(self):
        self.assertEqual(validar_y_limpiar_cedula('V-31.097.865'), '31097865')
        self.assertEqual(validar_y_limpiar_cedula('031097865'), '31097865')
        for invalida in ('', 'abc', '123', '999999', '1234567890'):
            with self.assertRaises(ValueError):
                validar_y_limpiar_cedula(invalida)

    def test_enmascarar(self):
        self.assertEqual(enmascarar_cedula('V-31.097.865'), '***7865')
        self.assertEqual(enmascarar_cedula(''), '')

    def test_consultas_solo_activos(self):
        self.assertIn('a.activo = 1', _CONSULTA_EMPLEADO)
        self.assertIn('a.activo = 1', _CONSULTA_MASIVA)


@override_settings(**RRHH_ACTIVO)
class ConsultaRRHHTest(TestCase):
    def test_parametro_string_y_fila(self):
        conexion = _FakeConexion([{
            'nombre': 'Ana', 'apellido': 'Pérez', 'cedula': '31097865',
            'cargo': 'Analista', 'departamento': 'TI',
        }])
        with patch(
            'autenticacion.services.rrhh_sync._conectar',
            return_value=conexion,
        ):
            datos = consultar_empleado_por_cedula('V-31.097.865')

        self.assertEqual(conexion.capturado['params'], ('31097865',))
        self.assertIn('a.activo = 1', conexion.capturado['sql'])
        self.assertEqual(datos['cargo'], 'Analista')
        self.assertEqual(datos['cedula'], '31097865')
        self.assertTrue(conexion.cerrada)

    def test_no_encontrado(self):
        conexion = _FakeConexion([])
        with patch(
            'autenticacion.services.rrhh_sync._conectar',
            return_value=conexion,
        ):
            self.assertIsNone(consultar_empleado_por_cedula('31097865'))

    def test_error_de_conexion(self):
        with patch(
            'autenticacion.services.rrhh_sync._conectar',
            side_effect=RuntimeError('sin red'),
        ):
            with self.assertRaises(RRHHConexionError):
                consultar_empleado_por_cedula('31097865')


class SincronizarRRHHTest(BaseDocumentosTest):
    def _perfil(self, username='trab01'):
        return PerfilUsuario.objects.get(usuario__username=username)

    def _fila(self, cedula='31097865'):
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
            cedula_identidad='031097865',
        )
        with patch(
            'autenticacion.services.rrhh_sync._leer',
            return_value=[self._fila()],
        ):
            resumen = sincronizar_todo_rrhh()

        perfil.refresh_from_db()
        usuario = perfil.usuario
        usuario.refresh_from_db()
        self.assertEqual(perfil.cedula_identidad, '31097865')
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
            cedula_identidad='31097865', cargo='', departamento_rrhh='',
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
            cedula_identidad='31097865',
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
            'nombre': 'Ana', 'apellido': 'Pérez', 'cedula': '31097865',
            'cargo': 'Analista de Soporte',
            'departamento': 'Talento Humano',
        }

    def _buscar(self, cedula='V-31.097.865', follow=False):
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
        self.assertEqual(perfil.cedula_identidad, '31097865')
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
        self.assertEqual(perfil.cedula_identidad, '31097865')
        self.assertFalse(perfil.cedula_verificada_rrhh)

    def test_confirmar_sin_sesion(self):
        resp = self._confirmar()
        self.assertContains(resp, 'La verificación expiró')

    def test_duplicada_no_guarda(self):
        otro = PerfilUsuario.objects.get(usuario__username='trab02')
        PerfilUsuario.objects.filter(pk=otro.pk).update(
            cedula_identidad='31097865',
        )
        resp = self._buscar()
        self.assertContains(resp, 'ya está registrada')

    def test_rrhh_deshabilitado_guarda(self):
        with override_settings(RRHH_SQLSERVER_HOST=''):
            resp = self._buscar(follow=True)
        self.assertContains(resp, 'Cédula registrada')
        perfil = PerfilUsuario.objects.get(usuario__username='trab01')
        self.assertEqual(perfil.cedula_identidad, '31097865')
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
            cedula_identidad='31097865',
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
            cedula_identidad='31097865', cedula_verificada_rrhh=True,
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
        self.assertEqual(kwargs['estado_anterior'], '***7865')
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
            cedula_identidad='31097865', cedula_verificada_rrhh=True,
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
