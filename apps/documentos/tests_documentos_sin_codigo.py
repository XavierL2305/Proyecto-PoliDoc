"""Tests de Documentos sin código (personal / soportes SGC)."""
import tempfile
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import Documento, DocumentoSinCodigo
from documentos.services import documentos_sin_codigo as svc

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_sincodigo_')
PDF = b'%PDF-1.4\n%prueba\n'


def _archivo(nombre='doc.pdf'):
    return SimpleUploadedFile(nombre, PDF, content_type='application/pdf')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseSinCodigoTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def _crear(self, ambito='personal', usuario=None, **extra):
        usuario = usuario or self.user_trabajador
        datos = {
            'titulo': extra.pop('titulo', 'Documento de prueba'),
            'descripcion': '',
            'fecha': '',
        }
        if ambito == 'sgc':
            datos.update({
                'referencia': 'FAC-001', 'clasificacion': 'interno',
                'empresa': 'Empresas del Grupo', 'departamento': self.depto_ti.id,
                'responsable_texto': 'Junta Directiva',
                'periodo_retencion': '', 'ubicacion': 'Digital/ Carpeta SGC',
            })
        datos.update(extra)
        if 'archivo' not in datos:
            datos['archivo'] = _archivo()
        self.client.force_login(usuario)
        url = reverse('documentos:documento_sin_codigo_crear', args=[ambito])
        return self.client.post(url, datos, follow=False)


class CreacionTest(BaseSinCodigoTest):
    def test_personal_cualquier_usuario(self):
        resp = self._crear('personal', usuario=self.user_trabajador)
        self.assertEqual(resp.status_code, 302)
        doc = DocumentoSinCodigo.objects.get()
        self.assertEqual(doc.ambito, 'personal')
        self.assertEqual(doc.propietario_id, self.user_trabajador.id)
        self.assertTrue(doc.archivo)

    def test_sgc_solo_admin_jefe(self):
        resp = self._crear('sgc', usuario=self.user_trabajador)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DocumentoSinCodigo.objects.exists())

        resp = self._crear('sgc', usuario=self.user_jefe_ti)
        self.assertEqual(resp.status_code, 302)
        doc = DocumentoSinCodigo.objects.get()
        self.assertEqual(doc.ambito, 'sgc')
        self.assertEqual(doc.responsable_texto, 'Junta Directiva')
        self.assertEqual(doc.empresa, 'Empresas del Grupo')

    def test_archivo_requerido_al_crear(self):
        self.client.force_login(self.user_trabajador)
        url = reverse('documentos:documento_sin_codigo_crear', args=['personal'])
        resp = self.client.post(url, {'titulo': 'Sin archivo'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(DocumentoSinCodigo.objects.exists())

    def test_no_crea_documento_iso(self):
        antes = Documento.objects.count()
        self._crear('personal', usuario=self.user_trabajador)
        self.assertEqual(Documento.objects.count(), antes)

    def test_selector_muestra_opciones(self):
        self.client.force_login(self.user_trabajador)
        html = self.client.get(reverse('documentos:seleccionar_tipo')).content.decode()
        self.assertIn('Documento personal', html)
        self.assertNotIn('Documento sin código (SGC)', html)

        self.client.force_login(self.user_jefe_ti)
        html = self.client.get(reverse('documentos:seleccionar_tipo')).content.decode()
        self.assertIn('Documento sin código (SGC)', html)


class PermisosTest(BaseSinCodigoTest):
    def test_personal_solo_dueno(self):
        self._crear('personal', usuario=self.user_trabajador)
        doc = DocumentoSinCodigo.objects.get()

        # Dueño: ve la bandeja y el detalle.
        self.client.force_login(self.user_trabajador)
        self.assertEqual(
            self.client.get(reverse('documentos:documentos_personales')).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse(
                'documentos:documento_sin_codigo_detalle', args=[doc.uuid],
            )).status_code,
            200,
        )

        # Otro usuario: 404 en el detalle.
        self.client.force_login(self.user_trabajador_otro)
        self.assertEqual(
            self.client.get(reverse(
                'documentos:documento_sin_codigo_detalle', args=[doc.uuid],
            )).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse(
                'documentos:documento_sin_codigo_descargar', args=[doc.uuid],
            )).status_code,
            404,
        )

    def test_admin_ve_personal_de_otro(self):
        p = DocumentoSinCodigo.objects.create(
            titulo='Personal de otro', ambito='personal',
            propietario=self.user_trabajador,
        )
        self.client.force_login(self.user_admin)
        self.assertEqual(
            self.client.get(reverse(
                'documentos:documento_sin_codigo_detalle', args=[p.uuid],
            )).status_code,
            200,
        )

    def test_sgc_solo_jefe_o_admin(self):
        self._crear('sgc', usuario=self.user_jefe_ti)
        doc = DocumentoSinCodigo.objects.get()

        self.client.force_login(self.user_trabajador)
        self.assertEqual(
            self.client.get(reverse('documentos:documentos_sgc_sin_codigo')).status_code,
            302,  # sin permiso -> redirect
        )
        self.assertEqual(
            self.client.get(reverse(
                'documentos:documento_sin_codigo_detalle', args=[doc.uuid],
            )).status_code,
            404,
        )

        self.client.force_login(self.user_jefe_ti)
        self.assertEqual(
            self.client.get(reverse('documentos:documentos_sgc_sin_codigo')).status_code,
            200,
        )

    def test_descarga_dueno(self):
        self._crear('personal', usuario=self.user_trabajador)
        doc = DocumentoSinCodigo.objects.get()
        self.client.force_login(self.user_trabajador)
        resp = self.client.get(reverse(
            'documentos:documento_sin_codigo_descargar', args=[doc.uuid],
        ))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('attachment', resp['Content-Disposition'])


class BandejasTest(BaseSinCodigoTest):
    def test_flags_menu(self):
        self.assertFalse(svc.context_menu(self.user_trabajador)['nav_docs_personales'])
        DocumentoSinCodigo.objects.create(
            titulo='P', ambito='personal', propietario=self.user_trabajador,
        )
        flags = svc.context_menu(self.user_trabajador)
        self.assertTrue(flags['nav_docs_personales'])
        self.assertFalse(flags['nav_docs_sgc_sin_codigo'])
        self.assertTrue(flags['nav_puede_crear_personal'])
        self.assertFalse(flags['nav_puede_crear_sgc'])

        self.assertTrue(svc.context_menu(self.user_jefe_ti)['nav_puede_crear_sgc'])
        self.assertFalse(svc.context_menu(self.user_jefe_ti)['nav_docs_sgc_sin_codigo'])
        DocumentoSinCodigo.objects.create(
            titulo='S', ambito='sgc', propietario=self.user_jefe_ti,
        )
        self.assertTrue(svc.context_menu(self.user_jefe_ti)['nav_docs_sgc_sin_codigo'])

    def test_personal_ajeno_no_cuenta(self):
        DocumentoSinCodigo.objects.create(
            titulo='P', ambito='personal', propietario=self.user_trabajador_otro,
        )
        self.assertFalse(svc.tiene_personales(self.user_trabajador))

    def test_bandeja_sgc_no_muestra_personales(self):
        DocumentoSinCodigo.objects.create(
            titulo='S', ambito='sgc', propietario=self.user_jefe_ti,
        )
        DocumentoSinCodigo.objects.create(
            titulo='P', ambito='personal', propietario=self.user_jefe_ti,
        )
        self.assertEqual(svc.sgc_visibles(self.user_jefe_ti).count(), 1)
        self.assertEqual(svc.personales_de(self.user_jefe_ti).count(), 1)


class EdicionEliminacionTest(BaseSinCodigoTest):
    def test_dueno_edita(self):
        self._crear('personal', usuario=self.user_trabajador)
        doc = DocumentoSinCodigo.objects.get()
        self.client.force_login(self.user_trabajador)
        resp = self.client.post(
            reverse('documentos:documento_sin_codigo_editar', args=[doc.uuid]),
            {'titulo': 'Renombrado', 'descripcion': '', 'fecha': ''},
        )
        self.assertEqual(resp.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.titulo, 'Renombrado')

    def test_otro_no_edita(self):
        self._crear('personal', usuario=self.user_trabajador)
        doc = DocumentoSinCodigo.objects.get()
        self.client.force_login(self.user_trabajador_otro)
        resp = self.client.get(
            reverse('documentos:documento_sin_codigo_editar', args=[doc.uuid]),
        )
        self.assertEqual(resp.status_code, 404)

    def test_eliminar_es_soft(self):
        self._crear('personal', usuario=self.user_trabajador)
        doc = DocumentoSinCodigo.objects.get()
        self.client.force_login(self.user_trabajador)
        resp = self.client.post(
            reverse('documentos:documento_sin_codigo_eliminar', args=[doc.uuid]),
        )
        self.assertEqual(resp.status_code, 302)
        doc.refresh_from_db()
        self.assertFalse(doc.activo)
        self.assertIsNotNone(doc.eliminado_en)
        self.assertFalse(svc.tiene_personales(self.user_trabajador))


class SidebarTest(BaseSinCodigoTest):
    def test_bandeja_personal_aparece_en_sidebar_con_registros(self):
        self.client.force_login(self.user_trabajador)
        url = reverse('documentos:mis_documentos')
        html = self.client.get(url).content.decode()
        self.assertNotIn('Mis documentos personales', html)

        DocumentoSinCodigo.objects.create(
            titulo='P', ambito='personal', propietario=self.user_trabajador,
        )
        html = self.client.get(url).content.decode()
        self.assertIn('Mis documentos personales', html)

    def test_bandeja_sgc_aparece_en_sidebar_para_jefe(self):
        self.client.force_login(self.user_jefe_ti)
        url = reverse('documentos:mis_documentos')
        html = self.client.get(url).content.decode()
        self.assertNotIn('Documentos sin código', html)

        DocumentoSinCodigo.objects.create(
            titulo='S', ambito='sgc', propietario=self.user_jefe_ti,
        )
        html = self.client.get(url).content.decode()
        self.assertIn('Documentos sin código', html)
