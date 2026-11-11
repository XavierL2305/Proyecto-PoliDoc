"""Tests de navegación: sidebar con grupos colapsables (Documentación/Configuración).

Cubre: visibilidad por rol, accesos rápidos del panel, grupo activo abierto,
``aria-current``/``aria-expanded`` y las tarjetas del panel de configuración
(las admin-only no se muestran al jefe).
"""
from django.test import Client
from django.urls import reverse

from tests_base import BaseDocumentosTest

from autenticacion.context_processors import navegacion


class _Match:
    def __init__(self, url_name):
        self.url_name = url_name


class _Request:
    def __init__(self, url_name):
        self.resolver_match = _Match(url_name)


class ContextoNavegacionTest(BaseDocumentosTest):
    def test_grupo_documentacion(self):
        self.assertEqual(
            navegacion(_Request('mis_documentos'))['sidebar_grupo_activo'],
            'documentacion',
        )
        self.assertEqual(
            navegacion(_Request('registro_detalle'))['sidebar_grupo_activo'],
            'documentacion',
        )

    def test_grupo_configuracion(self):
        for url_name in ('configuracion', 'formatos_lista', 'periodo_crear',
                         'gestionar_usuarios', 'delegaciones_jefatura'):
            self.assertEqual(
                navegacion(_Request(url_name))['sidebar_grupo_activo'],
                'configuracion', url_name,
            )

    def test_fuera_de_grupos(self):
        self.assertEqual(
            navegacion(_Request('dashboard'))['sidebar_grupo_activo'], '',
        )
        self.assertEqual(
            navegacion(_Request('seleccionar_tipo'))['sidebar_grupo_activo'], '',
        )


class SidebarTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def _get(self, username, url=None):
        self.client.login(username=username, password='testpass123')
        respuesta = self.client.get(url or reverse('autenticacion:dashboard'))
        self.assertEqual(respuesta.status_code, 200)
        return respuesta

    def test_admin_ve_grupos_y_accesos_rapidos(self):
        respuesta = self._get('admin01')
        for texto in (
            'data-group="documentacion"',
            'data-group="configuracion"',
            'aria-controls="sidebar-group-documentacion"',
            'aria-controls="sidebar-group-configuracion"',
            'Documentación',
            'Configuración',
            'Panel',
            'Formatos / Formularios',
            'Tipos de Documento',
            'Períodos',
            'Ciclo de Vida',
            'Delegaciones de Jefatura',
            'Transferencia Masiva',
            'Roles y Permisos',
            'Departamentos',
            'Usuarios',
            'Reportes de Auditoría',
            'Bandeja de aprobaciones',
        ):
            self.assertContains(respuesta, texto)

        # Los accesos rápidos apuntan a las páginas reales del panel.
        for url in (
            reverse('documentos:configuracion'),
            reverse('documentos:formatos_lista'),
            reverse('documentos:tipos_documento_lista'),
            reverse('documentos:auditoria_lista'),
            reverse('autenticacion:gestionar_usuarios'),
        ):
            self.assertContains(respuesta, f'href="{url}"')

    def test_trabajador_no_ve_configuracion_ni_bandeja(self):
        respuesta = self._get('trab01')
        self.assertContains(respuesta, 'data-group="documentacion"')
        self.assertNotContains(respuesta, 'data-group="configuracion"')
        self.assertNotContains(respuesta, 'Bandeja de aprobaciones')
        self.assertNotContains(respuesta, f'href="{reverse("documentos:configuracion")}"')

    def test_jefe_ve_configuracion_sin_opciones_admin(self):
        respuesta = self._get('jefe_ti')
        self.assertContains(respuesta, 'data-group="configuracion"')
        self.assertContains(respuesta, 'Roles y Permisos')
        self.assertContains(respuesta, 'Bandeja de aprobaciones')
        # Solo admin (las vistas rebotarían al jefe).
        for url in (
            reverse('autenticacion:gestionar_usuarios'),
            reverse('documentos:departamentos_lista'),
            reverse('documentos:auditoria_reportes_lista'),
        ):
            self.assertNotContains(respuesta, f'href="{url}"')

    def test_grupo_activo_configuracion_abierto(self):
        respuesta = self._get(
            'admin01', reverse('documentos:configuracion'),
        )
        self.assertContains(
            respuesta,
            'class="sidebar-group is-open has-active" data-group="configuracion"',
        )
        self.assertContains(respuesta, 'aria-current="page"')

    def test_grupo_activo_documentacion_abierto(self):
        respuesta = self._get('trab01', reverse('documentos:mis_documentos'))
        self.assertContains(
            respuesta,
            'class="sidebar-group is-open has-active" data-group="documentacion"',
        )
        self.assertContains(respuesta, 'aria-current="page"')

    def test_panel_configuracion_del_jefe_sin_tarjetas_admin(self):
        respuesta = self._get(
            'jefe_ti', reverse('documentos:configuracion'),
        )
        self.assertContains(respuesta, 'Formatos / Formularios')
        self.assertContains(respuesta, 'Roles y Permisos')
        self.assertNotContains(
            respuesta, f'href="{reverse("autenticacion:gestionar_usuarios")}"',
        )
        self.assertNotContains(
            respuesta, f'href="{reverse("documentos:departamentos_lista")}"',
        )
        self.assertNotContains(
            respuesta,
            f'href="{reverse("documentos:auditoria_reportes_lista")}"',
        )

    def test_panel_configuracion_del_admin_tiene_todo(self):
        respuesta = self._get(
            'admin01', reverse('documentos:configuracion'),
        )
        for url in (
            reverse('autenticacion:gestionar_usuarios'),
            reverse('documentos:departamentos_lista'),
            reverse('documentos:auditoria_reportes_lista'),
        ):
            self.assertContains(respuesta, f'href="{url}"')
