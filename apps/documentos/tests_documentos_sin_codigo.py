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
                'empresa': 'Empresas del Grupo',
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

    def test_sgc_trabajador_pendiente_y_jefe_autoaprueba(self):
        resp = self._crear('sgc', usuario=self.user_trabajador, titulo='Ext Trabajador')
        self.assertEqual(resp.status_code, 302)
        doc_trab = DocumentoSinCodigo.objects.get(titulo='Ext Trabajador')
        self.assertEqual(doc_trab.ambito, 'sgc')
        self.assertEqual(doc_trab.estado_aprobacion, 'pendiente')
        self.assertIsNone(doc_trab.aprobado_por)
        self.assertEqual(doc_trab.departamento_id, self.depto_ti.id)

        resp = self._crear('sgc', usuario=self.user_jefe_ti, titulo='Ext Jefe')
        self.assertEqual(resp.status_code, 302)
        doc_jefe = DocumentoSinCodigo.objects.get(titulo='Ext Jefe')
        self.assertEqual(doc_jefe.ambito, 'sgc')
        self.assertEqual(doc_jefe.estado_aprobacion, 'aprobado')
        self.assertEqual(doc_jefe.aprobado_por_id, self.user_jefe_ti.id)
        self.assertEqual(doc_jefe.responsable_texto, 'Junta Directiva')
        self.assertEqual(doc_jefe.empresa, 'Empresas del Grupo')

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

    def test_crear_infiere_tipo_archivo(self):
        self.client.force_login(self.user_trabajador)
        url = reverse('documentos:documento_sin_codigo_crear', args=['personal'])
        archivo = SimpleUploadedFile(
            'hoja.xlsx', b'PK\x03\x04' + b'\x00' * 8,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp = self.client.post(url, {'titulo': 'Hoja', 'archivo': archivo})
        self.assertEqual(resp.status_code, 302)
        doc = DocumentoSinCodigo.objects.get()
        self.assertEqual(doc.tipo_archivo, 'xlsx')

    def test_selector_muestra_opciones(self):
        self.client.force_login(self.user_trabajador)
        html = self.client.get(reverse('documentos:seleccionar_tipo')).content.decode()
        self.assertIn('Documento personal', html)
        self.assertIn('Documento sin código (SGC)', html)

        self.client.force_login(self.user_jefe_ti)
        html = self.client.get(reverse('documentos:seleccionar_tipo')).content.decode()
        self.assertIn('Documento sin código (SGC)', html)

    def test_formulario_usa_file_upload_componente(self):
        self.client.force_login(self.user_trabajador)
        url = reverse('documentos:documento_sin_codigo_crear', args=['personal'])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('file-upload-wrapper', html)
        self.assertIn('file-upload-label', html)
        self.assertIn('file-upload.css', html)
        self.assertIn('file-upload.js', html)

        url_sgc = reverse('documentos:documento_sin_codigo_crear', args=['sgc'])
        html_sgc = self.client.get(url_sgc).content.decode()
        self.assertNotIn('name="departamento"', html_sgc)
        self.assertIn(str(self.depto_ti), html_sgc)


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

    def test_sgc_visibilidad_propietario_y_jefe_departamento(self):
        self._crear('sgc', usuario=self.user_jefe_ti, titulo='De Jefe TI')
        doc_jefe = DocumentoSinCodigo.objects.get(titulo='De Jefe TI')
        self._crear('sgc', usuario=self.user_trabajador, titulo='De Trab TI')
        doc_trab = DocumentoSinCodigo.objects.get(titulo='De Trab TI')

        # Trabajador ve su bandeja y su propio SGC, pero NO el creado por el jefe.
        self.client.force_login(self.user_trabajador)
        self.assertEqual(
            self.client.get(reverse('documentos:documentos_sgc_sin_codigo')).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse(
                'documentos:documento_sin_codigo_detalle', args=[doc_trab.uuid],
            )).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse(
                'documentos:documento_sin_codigo_detalle', args=[doc_jefe.uuid],
            )).status_code,
            404,
        )

        # Jefe de TI ve lo subido por su trabajador; jefe de TH no.
        self.client.force_login(self.user_jefe_ti)
        self.assertEqual(
            self.client.get(reverse(
                'documentos:documento_sin_codigo_detalle', args=[doc_trab.uuid],
            )).status_code,
            200,
        )
        self.client.force_login(self.user_jefe_th)
        self.assertEqual(
            self.client.get(reverse(
                'documentos:documento_sin_codigo_detalle', args=[doc_trab.uuid],
            )).status_code,
            404,
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
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')


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
        self.assertTrue(flags['nav_puede_crear_sgc'])

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


class AprobacionSGCSinCodigoTest(BaseSinCodigoTest):
    def test_flujo_trabajador_notifica_jefe_y_no_entra_lista_maestra_hasta_aprobar(self):
        from autenticacion.models import Notificacion
        from documentos.services import for_006

        resp = self._crear('sgc', usuario=self.user_trabajador, titulo='Norma Externa ISO')
        self.assertEqual(resp.status_code, 302)
        doc = DocumentoSinCodigo.objects.get(titulo='Norma Externa ISO')
        self.assertEqual(doc.estado_aprobacion, 'pendiente')
        self.assertFalse(for_006._sin_codigo_fuente().filter(pk=doc.pk).exists())
        self.assertTrue(
            Notificacion.objects.filter(
                usuario=self.user_jefe_ti, tipo='solicitud_aprobacion',
            ).exists()
        )

        # Jefe de otro depto y otro trabajador no pueden aprobar ni rechazar
        self.client.force_login(self.user_jefe_th)
        self.assertEqual(
            self.client.post(
                reverse('documentos:documento_sin_codigo_aprobar', args=[doc.uuid]),
            ).status_code,
            404,
        )
        self.client.force_login(self.user_trabajador_otro)
        self.assertEqual(
            self.client.post(
                reverse('documentos:documento_sin_codigo_rechazar', args=[doc.uuid]),
                {'comentario': 'No'},
            ).status_code,
            404,
        )

        # Trabajador no ve tarjeta de aprobación en el detalle
        self.client.force_login(self.user_trabajador)
        html_trab = self.client.get(
            reverse('documentos:documento_sin_codigo_detalle', args=[doc.uuid]),
        ).content.decode()
        self.assertIn('Pendiente de aprobación', html_trab)
        self.assertNotIn('Aprobación de Jefatura', html_trab)

        # Jefe directo ve la tarjeta y aprueba
        self.client.force_login(self.user_jefe_ti)
        html_jefe = self.client.get(
            reverse('documentos:documento_sin_codigo_detalle', args=[doc.uuid]),
        ).content.decode()
        self.assertIn('Aprobación de Jefatura', html_jefe)

        resp_ok = self.client.post(
            reverse('documentos:documento_sin_codigo_aprobar', args=[doc.uuid]),
            {'comentario': 'Válido para TI'},
        )
        self.assertEqual(resp_ok.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.estado_aprobacion, 'aprobado')
        self.assertEqual(doc.aprobado_por_id, self.user_jefe_ti.id)
        self.assertEqual(doc.comentario_revision, 'Válido para TI')
        self.assertTrue(for_006._sin_codigo_fuente().filter(pk=doc.pk).exists())
        self.assertTrue(
            Notificacion.objects.filter(
                usuario=self.user_trabajador, tipo='aprobacion_documento',
            ).exists()
        )

    def test_coordinador_rechazo_y_reenvio_al_editar(self):
        from autenticacion.models import Notificacion
        from documentos.services import for_006

        self._crear('sgc', usuario=self.user_coordinador, titulo='Soporte Coord')
        doc = DocumentoSinCodigo.objects.get(titulo='Soporte Coord')
        self.assertEqual(doc.estado_aprobacion, 'pendiente')

        self.client.force_login(self.user_jefe_ti)
        # Rechazar sin motivo no cambia estado
        self.client.post(
            reverse('documentos:documento_sin_codigo_rechazar', args=[doc.uuid]),
            {'comentario': '   '},
        )
        doc.refresh_from_db()
        self.assertEqual(doc.estado_aprobacion, 'pendiente')

        # Rechazar con motivo notifica al coordinador y sigue fuera de Lista Maestra
        self.client.post(
            reverse('documentos:documento_sin_codigo_rechazar', args=[doc.uuid]),
            {'comentario': 'Falta fecha y referencia'},
        )
        doc.refresh_from_db()
        self.assertEqual(doc.estado_aprobacion, 'rechazado')
        self.assertEqual(doc.comentario_revision, 'Falta fecha y referencia')
        self.assertFalse(for_006._sin_codigo_fuente().filter(pk=doc.pk).exists())
        self.assertTrue(
            Notificacion.objects.filter(
                usuario=self.user_coordinador, tipo='rechazo_documento',
            ).exists()
        )

        # Coordinador edita y corrige -> vuelve a pendiente y re-notifica
        Notificacion.objects.filter(usuario=self.user_jefe_ti).delete()
        self.client.force_login(self.user_coordinador)
        self.client.post(
            reverse('documentos:documento_sin_codigo_editar', args=[doc.uuid]),
            {
                'titulo': 'Soporte Coord Corregido',
                'descripcion': '',
                'referencia': 'FAC-99',
                'clasificacion': 'interno',
                'empresa': 'Proveedor SA',
                'responsable_texto': 'Coord',
                'fecha': '',
                'periodo_retencion': '',
                'ubicacion': '',
            },
        )
        doc.refresh_from_db()
        self.assertEqual(doc.estado_aprobacion, 'pendiente')
        self.assertIsNone(doc.aprobado_por)
        self.assertEqual(doc.comentario_revision, '')
        self.assertTrue(
            Notificacion.objects.filter(
                usuario=self.user_jefe_ti, tipo='solicitud_aprobacion',
            ).exists()
        )

    def test_jefe_y_admin_autoaprueban_y_filtro_lista(self):
        from documentos.services import for_006

        self._crear('sgc', usuario=self.user_admin, titulo='Ext Admin')
        doc_admin = DocumentoSinCodigo.objects.get(titulo='Ext Admin')
        self.assertEqual(doc_admin.estado_aprobacion, 'aprobado')
        self.assertEqual(doc_admin.aprobado_por_id, self.user_admin.id)
        self.assertFalse(svc.puede_aprobar(self.user_admin, doc_admin))
        self.assertTrue(for_006._sin_codigo_fuente().filter(pk=doc_admin.pk).exists())

        self._crear('sgc', usuario=self.user_trabajador, titulo='Ext Pend')
        self.client.force_login(self.user_jefe_ti)
        resp = self.client.get(
            reverse('documentos:documentos_sgc_sin_codigo'),
            {'estado': 'pendiente'},
        )
        html = resp.content.decode()
        self.assertIn('Ext Pend', html)
        self.assertNotIn('Ext Admin', html)

        # Editar por admin (depto_gg) conserva el departamento original (depto_ti)
        doc_pend = DocumentoSinCodigo.objects.get(titulo='Ext Pend')
        self.assertEqual(doc_pend.departamento_id, self.depto_ti.id)
        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('documentos:documento_sin_codigo_editar', args=[doc_pend.uuid]),
            {
                'titulo': 'Ext Pend Editado',
                'descripcion': '',
                'referencia': 'FAC-001',
                'clasificacion': 'interno',
                'empresa': 'Empresas del Grupo',
                'responsable_texto': 'Junta Directiva',
                'fecha': '',
                'periodo_retencion': '',
                'ubicacion': '',
            },
        )
        doc_pend.refresh_from_db()
        self.assertEqual(doc_pend.titulo, 'Ext Pend Editado')
        self.assertEqual(doc_pend.departamento_id, self.depto_ti.id)
