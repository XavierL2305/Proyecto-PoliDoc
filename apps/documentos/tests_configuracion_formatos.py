"""Tests de edición segura del Constructor de formatos (SGC-FOR-002).

Un administrador debe poder cambiar "una palabra o una pequeña estructura"
del formato de Control de Distribución sin romper la planilla:

* el Constructor conserva ``max_filas``, la marca ``si_no`` (G/H) y
  ``celda_marca`` (EXT/INT) al guardar;
* renombrar la ``clave`` de un campo o de una columna no vacía la planilla;
* ``max_filas`` gobierna el tamaño de los lotes;
* reemplazar el XLSX invalida (y borra) el preview PDF viejo;
* el comando ``integrar_formatos_sgc`` no pisa ediciones hechas en la UI.

La plantilla se inyecta con ``--plantilla``: si ``documentacion/`` no está
disponible (p. ej. CI) se genera un XLSX con el mismo layout.
"""
import io
import json
import os
import tempfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import openpyxl

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import CampoFormato, DistribucionDocumento, Formato
from documentos.services import distribucion as svc_dist
from documentos.tests_sgc_utils import crear_plantilla_for_002, leer_celda

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_formatos_')
PLANTILLA_ORIGEN = os.path.join(TEST_MEDIA, 'plantilla_for_002.xlsx')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseFormato002Test(BaseDocumentosTest):
    """Formato SGC-FOR-002 integrado antes de cada clase de tests."""

    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        crear_plantilla_for_002(PLANTILLA_ORIGEN)
        with patch('documentos.tasks.convertir_formato_a_pdf.delay',
                   return_value=None):
            call_command(
                'integrar_formatos_sgc', solo='002', apply=True,
                plantilla=PLANTILLA_ORIGEN, stdout=io.StringIO(),
            )
        cls.formato_002 = Formato.objects.get(codigo='SGC-FOR-002')

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato_002.refresh_from_db()

    # -- helpers ---------------------------------------------------------
    def _login_admin(self):
        self.client.login(username='admin01', password='testpass123')

    def _campo(self, clave):
        return CampoFormato.objects.get(
            seccion__formato=self.formato_002, clave=clave,
        )

    def _post_campo(self, campo, **extra):
        """POST tal como lo arma el JavaScript del ``campo_form.html``."""
        payload = {
            'clave': campo.clave,
            'etiqueta': campo.etiqueta,
            'tipo': campo.tipo,
            'origen': campo.origen,
            'ayuda': campo.ayuda or '',
            'opciones': json.dumps(campo.opciones or []),
            'config_tabla': json.dumps(campo.config_tabla or {}),
            'ancho': campo.ancho,
            'orden': campo.orden,
            'celda_excel': campo.celda_excel or '',
            'requerido': 'on' if campo.requerido else '',
            'incluir_etiqueta': 'on' if campo.incluir_etiqueta else '',
        }
        payload.update(extra)
        return self.client.post(
            reverse('documentos:campo_editar',
                    args=[self.formato_002.id, campo.id]),
            payload,
        )

    def _distribuir(self, internos):
        svc_dist.crear_distribuciones(
            self.doc_activo, internos=internos,
            distribuido_por=self.user_admin, metodo='sistema',
            tipo='procedimientos_generales',
        )
        return (
            DistribucionDocumento.objects
            .filter(documento=self.doc_activo).order_by('-id').first()
        )


# =============================================================================
# La sección Formatos: lista, constructor y edición de la plantilla
# =============================================================================

class ListaYConstructorTest(BaseFormato002Test):
    def test_for_002_aparece_en_la_lista(self):
        self._login_admin()
        resp = self.client.get(reverse('documentos:formatos_lista'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'SGC-FOR-002')
        self.assertContains(resp, 'Planilla automática')

    def test_constructor_lista_los_campos_del_formato(self):
        self._login_admin()
        resp = self.client.get(
            reverse('documentos:formato_constructor',
                    args=[self.formato_002.id]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'destinatarios')
        self.assertContains(resp, 'origen_informacion')

    def test_editor_de_campo_expone_los_controles_nuevos(self):
        """Los controles que sostienen el FOR-002 deben estar en la UI."""
        self._login_admin()
        resp = self.client.get(
            reverse('documentos:campo_editar',
                    args=[self.formato_002.id, self._campo('destinatarios').id]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Máx. filas (export)')
        self.assertContains(resp, 'Marca si/no')
        self.assertContains(resp, 'toggle-avanzado')

        resp = self.client.get(
            reverse('documentos:campo_editar',
                    args=[self.formato_002.id,
                          self._campo('origen_informacion').id]),
        )
        self.assertContains(resp, 'Celda marca (X)')

    def test_editar_ofrece_descargar_la_plantilla_actual(self):
        self._login_admin()
        resp = self.client.get(
            reverse('documentos:formato_editar', args=[self.formato_002.id]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Descargar plantilla actual')
        self.assertContains(resp, self.formato_002.plantilla_xlsx.url)

    def test_error_de_validacion_repita_lo_que_se_estaba_editando(self):
        """El form bound re-renderiza con lo enviado, no con la instancia.

        Sin esto, un error (p. ej. clave vacía/duplicada) devolvería la
        configuración guardada y se perdería el mapeo en pantalla.
        """
        self._login_admin()
        campo = self._campo('destinatarios')
        config = {
            'fila_inicio': 19, 'fila_fin': 51, 'max_filas': 33,
            'columnas': [{'rol': 'nombre', 'clave': 'nombre',
                          'etiqueta': 'MARCADOR-RENDER', 'celda': 'A'}],
        }
        resp = self._post_campo(campo, clave='', config_tabla=json.dumps(config))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors)
        self.assertIn('MARCADOR-RENDER', resp.content.decode())


# =============================================================================
# Guardar un campo no pierde la configuración avanzada
# =============================================================================

class ConservarConfiguracionTest(BaseFormato002Test):
    def test_editar_destinatarios_conserva_config_avanzada(self):
        self._login_admin()
        campo = self._campo('destinatarios')
        config = {
            'fila_inicio': 19, 'fila_fin': 51, 'max_filas': 33,
            'columnas': [
                {'rol': 'nombre', 'clave': 'nombre', 'etiqueta': 'Nombre',
                 'celda': 'A'},
                {'rol': 'retiro', 'clave': 'retiro', 'etiqueta': 'Retiro',
                 'tipo': 'si_no', 'celda_si': 'G', 'celda_no': 'H'},
                {'rol': 'firma', 'clave': 'firma', 'etiqueta': 'Firma',
                 'celda': 'I'},
            ],
        }
        resp = self._post_campo(campo, config_tabla=json.dumps(config))
        self.assertEqual(resp.status_code, 302)

        campo.refresh_from_db()
        guardado = campo.config_tabla
        self.assertEqual(guardado['fila_inicio'], 19)
        self.assertEqual(guardado['fila_fin'], 51)
        self.assertEqual(guardado['max_filas'], 33)
        retiro = next(c for c in guardado['columnas'] if c['clave'] == 'retiro')
        self.assertEqual(retiro['tipo'], 'si_no')
        self.assertEqual(retiro['celda_si'], 'G')
        self.assertEqual(retiro['celda_no'], 'H')
        self.assertTrue(all('rol' in c for c in guardado['columnas']))

    def test_editar_origen_conserva_celda_marca(self):
        """La marca EXT/INT (C12/D12) debe sobrevivir a una edición."""
        self._login_admin()
        campo = self._campo('origen_informacion')
        opciones = [
            {'valor': 'EXT', 'etiqueta': 'Externa', 'celda_marca': 'C12'},
            {'valor': 'INT', 'etiqueta': 'Interna', 'celda_marca': 'D12'},
        ]
        resp = self._post_campo(
            campo, opciones=json.dumps(opciones),
            etiqueta='Origen de la información documentada',
        )
        self.assertEqual(resp.status_code, 302)

        campo.refresh_from_db()
        self.assertEqual(campo.etiqueta, 'Origen de la información documentada')
        self.assertEqual(
            [o.get('celda_marca') for o in campo.opciones], ['C12', 'D12'],
        )

    def test_editar_tabla_conserva_claves_desconocidas(self):
        """Claves extra del JSON no declaradas en la UI no se pierden."""
        self._login_admin()
        campo = self._campo('destinatarios')
        config = {
            'fila_inicio': 19, 'fila_fin': 51, 'max_filas': 33,
            'totales': ['si'],   # clave que la UI no expone
            'columnas': [{'rol': 'nombre', 'clave': 'nombre',
                          'etiqueta': 'Nombre', 'celda': 'A'}],
        }
        self._post_campo(campo, config_tabla=json.dumps(config))
        campo.refresh_from_db()
        self.assertEqual(campo.config_tabla['totales'], ['si'])


# =============================================================================
# Renombrar claves sin romper la generación de la planilla
# =============================================================================

class RenombrarClavesTest(BaseFormato002Test):
    def test_renombrar_clave_de_cabecera_llena_la_misma_celda(self):
        self._login_admin()
        resp = self._post_campo(self._campo('titulo'), clave='titulo_doc')
        self.assertEqual(resp.status_code, 302)

        dist = self._distribuir([self.user_jefe_ti])
        _fr, respuestas = svc_dist.generar_planilla_lote(dist.lote)
        self.assertEqual(respuestas.get('titulo_doc'), self.doc_activo.nombre)
        self.assertNotIn('titulo', respuestas)

        xlsx = svc_dist.exportar_planilla_lote(dist.lote)
        self.assertEqual(leer_celda(xlsx, 'F12'), self.doc_activo.nombre)

    def test_renombrar_columna_de_tabla_con_rol_conserva_el_valor(self):
        self._login_admin()
        campo = self._campo('destinatarios')
        config = campo.config_tabla
        for columna in config['columnas']:
            if columna['clave'] == 'nombre':
                columna['clave'] = 'usuario_nombre'
        resp = self._post_campo(campo, config_tabla=json.dumps(config))
        self.assertEqual(resp.status_code, 302)

        dist = self._distribuir([self.user_jefe_ti])
        _fr, respuestas = svc_dist.generar_planilla_lote(dist.lote)
        filas = respuestas[campo.clave]
        self.assertEqual(
            filas[0]['usuario_nombre'],
            self.user_jefe_ti.get_full_name() or self.user_jefe_ti.username,
        )
        self.assertNotIn('nombre', filas[0])

        xlsx = svc_dist.exportar_planilla_lote(dist.lote)
        self.assertEqual(
            leer_celda(xlsx, 'A19'),
            self.user_jefe_ti.get_full_name() or self.user_jefe_ti.username,
        )


# =============================================================================
# Máx. filas gobierna los lotes
# =============================================================================

class MaxFilasTest(BaseFormato002Test):
    def test_max_filas_del_formato_redimensiona_los_lotes(self):
        self.assertEqual(svc_dist.filas_por_planilla(), 33)

        self._login_admin()
        campo = self._campo('destinatarios')
        config = campo.config_tabla
        config['max_filas'] = 2
        config['fila_fin'] = 20
        self._post_campo(campo, config_tabla=json.dumps(config))

        self.assertEqual(svc_dist.filas_por_planilla(), 2)
        dist = self._distribuir([
            self.user_admin, self.user_jefe_ti, self.user_jefe_gg,
        ])
        self.assertIsNotNone(dist.lote)
        dists = DistribucionDocumento.objects.filter(documento=self.doc_activo)
        self.assertEqual(dists.count(), 3)
        self.assertEqual(dists.values('lote').distinct().count(), 2)
        self.assertEqual(dists.filter(lote=dist.lote).count(), 1)


# =============================================================================
# Reemplazar la plantilla XLSX
# =============================================================================

class ReemplazarPlantillaTest(BaseFormato002Test):
    def _xlsx_bytes(self):
        buf = BytesIO()
        wb = openpyxl.Workbook()
        wb.active['A1'] = 'Plantilla revisada'
        wb.save(buf)
        return buf.getvalue()

    def test_reemplazar_plantilla_borra_el_pdf_viejo(self):
        formato = self.formato_002
        pdf_relativo = 'formatos/pdf/preview-viejo.pdf'
        ruta_pdf = os.path.join(TEST_MEDIA, pdf_relativo)
        os.makedirs(os.path.dirname(ruta_pdf), exist_ok=True)
        with open(ruta_pdf, 'wb') as fh:
            fh.write(b'%PDF-1.4 viejo')
        formato.plantilla_pdf = pdf_relativo
        formato.save(update_fields=['plantilla_pdf'])

        self._login_admin()
        xlsx = SimpleUploadedFile(
            'plantilla-revisada.xlsx', self._xlsx_bytes(),
            content_type='application/vnd.openxmlformats-officedocument.'
                         'spreadsheetml.sheet',
        )
        with patch('documentos.tasks.convertir_formato_a_pdf_sync') as conv:
            resp = self.client.post(
                reverse('documentos:formato_editar', args=[formato.id]),
                {
                    'codigo': formato.codigo,
                    'nombre': formato.nombre,
                    'version': formato.version,
                    'edicion': formato.edicion,
                    'fecha_vigencia': '',
                    'procedimiento': formato.procedimiento,
                    'instructivo_html': formato.instructivo_html,
                    'plantilla_xlsx': xlsx,
                    'aplica_creacion': '',
                    'aplica_modificacion': '',
                    'aplica_eliminacion': '',
                    'activo': 'on',
                },
            )
        self.assertEqual(resp.status_code, 302)

        formato.refresh_from_db()
        self.assertFalse(os.path.exists(ruta_pdf), 'El PDF viejo debe borrarse')
        self.assertNotEqual(formato.plantilla_pdf, pdf_relativo)
        self.assertTrue(str(formato.plantilla_xlsx).endswith(
            'plantilla-revisada.xlsx',
        ))
        # La preview se regenera en el mismo acto (aquí, con la conversión
        # simulada: solo se comprueba que se intenta).
        self.assertTrue(conv.called)


# =============================================================================
# El comando no pisa ediciones hechas desde la UI
# =============================================================================

class ComandoIntegrationTest(BaseFormato002Test):
    def _ejecutar(self, **kwargs):
        call_command(
            'integrar_formatos_sgc', solo='002', apply=True,
            plantilla=PLANTILLA_ORIGEN, stdout=io.StringIO(), **kwargs,
        )

    def test_reintegrar_conserva_las_ediciones_de_la_ui(self):
        campo = self._campo('titulo')
        campo.etiqueta = 'Título revisado por calidad'
        campo.save(update_fields=['etiqueta'])

        self._ejecutar()

        self.assertTrue(
            CampoFormato.objects.filter(
                seccion__formato=self.formato_002, clave='titulo',
                etiqueta='Título revisado por calidad',
            ).exists(),
        )

    def test_forzar_estructura_recrea_desde_la_especificacion(self):
        campo = self._campo('titulo')
        campo.etiqueta = 'Título revisado por calidad'
        campo.save(update_fields=['etiqueta'])

        self._ejecutar(forzar_estructura=True)

        self.assertTrue(
            CampoFormato.objects.filter(
                seccion__formato=self.formato_002, clave='titulo',
                etiqueta='Título',
            ).exists(),
        )

    def test_sin_plantilla_reporta_error_claro(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError) as ctx:
            call_command(
                'integrar_formatos_sgc', solo='002',
                plantilla='/ruta/que/no/existe.xlsx', stdout=io.StringIO(),
            )
        self.assertIn('--plantilla', str(ctx.exception))
