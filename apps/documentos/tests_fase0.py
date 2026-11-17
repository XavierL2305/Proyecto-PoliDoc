"""Tests de la Fase 0: AccesoFormato, versiones y constraint anual."""
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from tests_base import BaseDocumentosTest

from documentos.models import (
    AccesoFormato, Formato, RegistroFormato, VersionRegistroFormato,
)
from documentos.services import acceso_formato
from documentos.services import versiones as svc_versiones


class AccesoFormatoTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.formato = Formato.objects.create(
            codigo='TEST-F0-ACC', nombre='Formato F0', version='1',
        )

    def test_otorgar_ver_no_permite_editar(self):
        acceso_formato.otorgar(
            self.user_trabajador, self.formato, nivel='ver',
            otorgado_por=self.user_admin,
        )
        self.assertTrue(acceso_formato.puede_ver(
            self.user_trabajador, self.formato,
        ))
        self.assertFalse(acceso_formato.puede_editar(
            self.user_trabajador, self.formato,
        ))
        self.assertTrue(acceso_formato.puede_ver_codigo(
            self.user_trabajador, 'TEST-F0-ACC',
        ))
        self.assertFalse(acceso_formato.puede_editar_codigo(
            self.user_trabajador, 'TEST-F0-ACC',
        ))

    def test_otorgar_editar_permite_todo(self):
        acceso_formato.otorgar(
            self.user_trabajador, self.formato, nivel='editar',
        )
        self.assertTrue(acceso_formato.puede_editar(
            self.user_trabajador, self.formato,
        ))
        self.assertTrue(acceso_formato.puede_editar_codigo(
            self.user_trabajador, 'TEST-F0-ACC',
        ))

    def test_cambiar_nivel(self):
        acceso_formato.otorgar(self.user_trabajador, self.formato, nivel='ver')
        acceso_formato.otorgar(self.user_trabajador, self.formato, nivel='editar')
        concesion = AccesoFormato.objects.get(
            formato=self.formato, usuario=self.user_trabajador,
        )
        self.assertEqual(concesion.nivel, 'editar')
        self.assertEqual(
            AccesoFormato.objects.filter(
                formato=self.formato, usuario=self.user_trabajador,
            ).count(),
            1,
        )

    def test_revocar(self):
        acceso_formato.otorgar(self.user_trabajador, self.formato)
        acceso_formato.revocar(self.user_trabajador, self.formato)
        self.assertFalse(acceso_formato.puede_ver(
            self.user_trabajador, self.formato,
        ))
        # La fila queda inactiva (traza), no se borra.
        self.assertTrue(AccesoFormato.objects.filter(
            formato=self.formato, usuario=self.user_trabajador, activo=False,
        ).exists())

    def test_administrador_siempre(self):
        self.assertTrue(acceso_formato.puede_ver(
            self.user_admin, self.formato,
        ))
        self.assertTrue(acceso_formato.puede_editar(
            self.user_admin, self.formato,
        ))
        self.assertTrue(acceso_formato.puede_editar_codigo(
            self.user_admin, 'NO-EXISTE',
        ))


class VersionRegistroFormatoTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.formato = Formato.objects.create(
            codigo='TEST-F0-VER', nombre='Formato versión', version='1',
        )
        self.registro = RegistroFormato.objects.create(
            formato=self.formato, formato_codigo='TEST-F0-VER',
            contexto_tipo='programa_revision', contexto_id='2026',
            titulo='Programa 2026', responsable=self.user_admin,
            respuestas={'aspectos': [{'clave': 'a', 'etiqueta': 'A'}]},
        )

    def test_crea_versiones_secuenciales_con_hash(self):
        v1 = svc_versiones.registrar_version(
            self.registro, self.user_admin, 'Versión inicial',
        )
        self.assertEqual(v1.numero, 1)
        self.assertEqual(len(v1.hash), 64)
        self.assertEqual(v1.datos['respuestas']['aspectos'][0]['clave'], 'a')
        self.assertIn('config', v1.datos)

        self.registro.respuestas = {
            'aspectos': [{'clave': 'a', 'etiqueta': 'A editado'}],
        }
        self.registro.save(update_fields=['respuestas'])
        v2 = svc_versiones.registrar_version(
            self.registro, self.user_admin, 'Cambio',
        )
        self.assertEqual(v2.numero, 2)
        self.assertNotEqual(v1.hash, v2.hash)
        self.assertEqual(
            [v.numero for v in svc_versiones.versiones(self.registro)],
            [2, 1],
        )
        self.assertEqual(svc_versiones.ultima_version(self.registro).numero, 2)

    def test_numero_unico_por_registro(self):
        svc_versiones.registrar_version(self.registro, self.user_admin)
        with self.assertRaises(IntegrityError), transaction.atomic():
            VersionRegistroFormato.objects.create(
                registro=self.registro, numero=1, datos={},
            )


class ConstraintAnualTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.formato = Formato.objects.create(
            codigo='TEST-F0-ANUAL', nombre='Programa', version='1',
        )

    def _registro(self, contexto_tipo='programa_revision', contexto_id='2026'):
        return RegistroFormato.objects.create(
            formato=self.formato, formato_codigo='TEST-F0-ANUAL',
            contexto_tipo=contexto_tipo, contexto_id=contexto_id,
            titulo='x', responsable=self.user_admin,
        )

    def test_un_programa_por_anio(self):
        self._registro(contexto_id='2026')
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._registro(contexto_id='2026')
        # Otro año sí se permite.
        self._registro(contexto_id='2027')

    def test_otros_contextos_no_afectados(self):
        self._registro(contexto_tipo='documento', contexto_id='2026')
        # Dos registros con el mismo contexto_tipo/id no-programa son válidos.
        self._registro(contexto_tipo='documento', contexto_id='2026')
        self.assertEqual(
            RegistroFormato.objects.filter(
                formato=self.formato, contexto_tipo='documento',
            ).count(),
            2,
        )


class RegistrosVivosNoGenericosTest(BaseDocumentosTest):
    """Los registros vivos (FOR-006/007) no se editan desde el editor genérico."""

    def setUp(self):
        super().setUp()
        self.formato = Formato.objects.create(
            codigo='TEST-F0-LIVE', nombre='Living', version='1',
        )

        def _registro(contexto_tipo, contexto_id, titulo):
            return RegistroFormato.objects.create(
                formato=self.formato, formato_codigo='TEST-F0-LIVE',
                contexto_tipo=contexto_tipo, contexto_id=contexto_id,
                titulo=titulo, responsable=self.user_admin,
                estado='borrador',
            )

        self.vivo = _registro('sistema', 'lista-maestra', 'ZZZ-VIVO-LISTA')
        self.programa = _registro('programa_revision', '2026', 'ZZZ-VIVO-PROG')
        self.normal = _registro('documento', 'x', 'Registro normal')

    def test_excluidos_y_bloqueados(self):
        from documentos.services.registros import (
            puede_aprobar_registro, puede_editar_registro, puede_ver_registro,
            registros_visibles,
        )

        visibles = registros_visibles(self.user_admin)
        self.assertIn(self.normal, visibles)
        self.assertNotIn(self.vivo, visibles)
        self.assertNotIn(self.programa, visibles)
        self.assertFalse(puede_ver_registro(self.user_admin, self.vivo))
        self.assertFalse(puede_editar_registro(self.user_admin, self.vivo))
        self.assertFalse(puede_editar_registro(self.user_admin, self.programa))
        self.assertFalse(puede_aprobar_registro(self.user_admin, self.programa))
        self.assertTrue(puede_ver_registro(self.user_admin, self.normal))

    def test_lista_generica_no_los_muestra(self):
        client = Client()
        client.force_login(self.user_admin)
        resp = client.get(reverse('documentos:registros_lista'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Registro normal')
        self.assertNotContains(resp, 'ZZZ-VIVO-LISTA')
        self.assertNotContains(resp, 'ZZZ-VIVO-PROG')

    def test_editar_vivo_no_cambia(self):
        client = Client()
        client.force_login(self.user_admin)
        client.post(
            reverse('documentos:registro_editar', args=[self.vivo.uuid]),
            {'titulo': 'Hackeado'},
        )
        self.vivo.refresh_from_db()
        self.assertEqual(self.vivo.titulo, 'ZZZ-VIVO-LISTA')

    def test_modelos_registrados_en_admin(self):
        from django.contrib import admin
        from documentos.models import (
            AccesoFormato, ActaDesincorporacion, DocumentoSinCodigo,
            VersionRegistroFormato,
        )

        for modelo in (
            AccesoFormato, VersionRegistroFormato, DocumentoSinCodigo,
            ActaDesincorporacion,
        ):
            self.assertTrue(admin.site.is_registered(modelo))
