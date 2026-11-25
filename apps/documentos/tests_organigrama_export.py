"""Tests de exportación multi-formato y Fase 2 del Organigrama (SGC-ORG-001)."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from lxml import etree
from PIL import Image
from pypdf import PdfReader

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import Documento, SolicitudDescarga, TipoDocumento
from documentos.services import acceso_formato
from documentos.services.organigrama import crear_organigrama_inicial, guardar_organigrama
from documentos.services.organigrama_export import (
    LEYENDA_COPIA_NO_CONTROLADA,
    OrganigramaExportError,
    exportar_organigrama,
    filtrar_organigrama,
)


def _datos_multiempresa():
    return {
        'schema_version': 1,
        'settings': {'gridSize': 20, 'snapToGrid': True},
        'sections': [
            {
                'id': 'sec_ptca',
                'title': 'Talento Humano PTCA',
                'empresa': 'PTCA',
                'departamento_id': None,
                'x': 40,
                'y': 40,
                'w': 520,
                'h': 400,
                'color': '#2563EB',
                'order': 0,
            },
            {
                'id': 'sec_fp',
                'title': 'Regencia Farmacia',
                'empresa': 'FP',
                'departamento_id': None,
                'x': 640,
                'y': 40,
                'w': 480,
                'h': 360,
                'color': '#CA8A04',
                'order': 1,
            },
        ],
        'nodes': [
            {
                'id': 'n_gg',
                'section_id': 'sec_ptca',
                'shape': 'rounded',
                'x': 180,
                'y': 70,
                'w': 220,
                'h': 68,
                'text': 'Gerencia General & Dirección',
                'zIndex': 2,
                'style': {
                    'bgColor': '#DBEAFE',
                    'textColor': '#1E3A8A',
                    'borderColor': '#2563EB',
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
                'id': 'n_th_rombo',
                'section_id': 'sec_ptca',
                'shape': 'diamond',
                'x': 190,
                'y': 200,
                'w': 200,
                'h': 110,
                'text': 'Comité Técnico <script>alert(1)</script>',
                'zIndex': 1,
                'style': {
                    'bgColor': '#FEF3C7',
                    'textColor': '#92400E',
                    'borderColor': '#D97706',
                    'borderWidth': 2,
                    'borderStyle': 'dashed',
                    'borderRadius': 10,
                    'fontWeight': 'normal',
                    'fontStyle': 'italic',
                    'fontSize': 12,
                    'textAlign': 'center',
                },
            },
            {
                'id': 'n_pill',
                'section_id': 'sec_ptca',
                'shape': 'pill',
                'x': 80,
                'y': 350,
                'w': 180,
                'h': 56,
                'text': 'Ñoño Analista',
                'zIndex': 1,
                'style': {'bgColor': 'auto', 'textColor': 'auto', 'borderColor': 'auto'},
            },
            {
                'id': 'n_fp_reg',
                'section_id': 'sec_fp',
                'shape': 'rect',
                'x': 720,
                'y': 100,
                'w': 220,
                'h': 72,
                'text': 'Farmacéutico Regente',
                'zIndex': 2,
                'style': {'bgColor': '#FEF9C3', 'textColor': '#713F12', 'borderColor': '#CA8A04'},
            },
            {
                'id': 'n_suelto',
                'section_id': None,
                'shape': 'text',
                'x': 80,
                'y': 500,
                'w': 200,
                'h': 44,
                'text': 'Nota Corporativa',
                'zIndex': 1,
                'style': {
                    'bgColor': 'transparent',
                    'textColor': 'auto',
                    'borderColor': 'transparent',
                    'borderWidth': 0,
                    'fontWeight': 'bold',
                },
            },
        ],
        'edges': [
            {
                'id': 'e_ptca',
                'from': 'n_gg',
                'to': 'n_th_rombo',
                'fromAnchor': 'bottom',
                'toAnchor': 'top',
                'routing': 'orthogonal',
                'color': '#2563EB',
                'width': 2,
                'style': 'dashed',
                'arrowEnd': True,
                'label': 'Asesoría',
            },
            {
                'id': 'e_cruzada',
                'from': 'n_gg',
                'to': 'n_fp_reg',
                'fromAnchor': 'right',
                'toAnchor': 'left',
                'routing': 'straight',
                'color': 'auto',
                'width': 3,
                'style': 'solid',
                'arrowEnd': True,
                'label': '',
            },
        ],
    }


def _meta_ejemplo():
    return {
        'codigo': 'SGC-ORG-001',
        'nombre': 'Organigrama Corporativo',
        'version': '2',
        'fecha_vigencia': '25/11/2026',
        'usuario': 'Admin Prueba',
        'fecha_generacion': '25/11/2026 09:00',
    }


class OrganigramaFiltradoYMotorExportacionTest(BaseDocumentosTest):
    """Valida filtrado de subgrafos y los 5 generadores (PDF, PNG, SVG, ODG, VSDX)."""

    def test_filtrar_organigrama_por_empresa_seccion_y_nodos_sueltos(self):
        datos = _datos_multiempresa()

        completo = filtrar_organigrama(datos, todo=True)
        self.assertEqual(len(completo['sections']), 2)
        self.assertEqual(len(completo['nodes']), 5)
        self.assertEqual(len(completo['edges']), 2)

        # Normaliza PT -> PTCA y excluye arista cruzada hacia FP
        solo_ptca = filtrar_organigrama(datos, todo=False, empresas=['PT'], incluir_sin_seccion=False)
        self.assertEqual([s['id'] for s in solo_ptca['sections']], ['sec_ptca'])
        self.assertEqual({n['id'] for n in solo_ptca['nodes']}, {'n_gg', 'n_th_rombo', 'n_pill'})
        self.assertEqual([e['id'] for e in solo_ptca['edges']], ['e_ptca'])
        # Coordenadas normalizadas desde >= 0
        for n in solo_ptca['nodes']:
            self.assertGreaterEqual(n['x'], 0)
            self.assertGreaterEqual(n['y'], 0)

        # Filtro por section_ids + incluir_sin_seccion=True
        solo_fp_mas_suelto = filtrar_organigrama(
            datos, todo=False, section_ids=['sec_fp'], incluir_sin_seccion=True,
        )
        self.assertEqual({n['id'] for n in solo_fp_mas_suelto['nodes']}, {'n_fp_reg', 'n_suelto'})
        self.assertEqual(solo_fp_mas_suelto['edges'], [])

        # Selección vacía lanza error controlado
        with self.assertRaises(OrganigramaExportError):
            filtrar_organigrama(datos, todo=False, section_ids=[], empresas=[], incluir_sin_seccion=False)

    def test_genera_pdf_institucional_y_marca_obsoleto(self):
        datos = _datos_multiempresa()
        for papel in ('auto', 'letter', 'a4', 'a3'):
            pdf_bytes, fn, ctype, _ = exportar_organigrama(
                datos, 'pdf', _meta_ejemplo(), todo=True, papel=papel, es_obsoleto=False,
            )
            self.assertTrue(pdf_bytes.startswith(b'%PDF-'))
            self.assertEqual(ctype, 'application/pdf')
            self.assertTrue(fn.endswith('.pdf'))
            texto = PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()
            self.assertIn('SGC-ORG-001', texto)
            self.assertIn('COPIA NO CONTROLADA', texto)
            self.assertIn('Gerencia General', texto)
            self.assertNotIn('OBSOLETO', texto)

        pdf_obs, _, _, _ = exportar_organigrama(
            datos, 'pdf', _meta_ejemplo(), todo=False, section_ids=['sec_fp'], es_obsoleto=True,
        )
        texto_obs = PdfReader(io.BytesIO(pdf_obs)).pages[0].extract_text()
        self.assertIn('OBSOLETO', texto_obs)
        self.assertIn('Farmacéutico Regente', texto_obs)
        self.assertNotIn('Gerencia General', texto_obs)

    def test_genera_png_con_ghostscript_y_fallback_pillow(self):
        datos = _datos_multiempresa()
        png_bytes, fn, ctype, _ = exportar_organigrama(
            datos, 'png', _meta_ejemplo(), todo=True,
        )
        self.assertTrue(png_bytes.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(ctype, 'image/png')
        self.assertTrue(fn.endswith('.png'))
        im = Image.open(io.BytesIO(png_bytes))
        self.assertGreater(im.width, 600)
        self.assertGreater(im.height, 400)

        with patch('documentos.services.organigrama_export.shutil.which', return_value=None):
            png_fb, _, _, _ = exportar_organigrama(
                datos, 'png', _meta_ejemplo(), todo=False, section_ids=['sec_ptca'], es_obsoleto=True,
            )
        self.assertTrue(png_fb.startswith(b'\x89PNG\r\n\x1a\n'))
        im_fb = Image.open(io.BytesIO(png_fb))
        self.assertGreater(im_fb.width, 400)
        self.assertNotIn(LEYENDA_COPIA_NO_CONTROLADA.encode(), png_fb[:16])

    def test_genera_svg_xml_seguro_con_rombo_y_marcadores(self):
        datos = _datos_multiempresa()
        svg_bytes, fn, ctype, _ = exportar_organigrama(
            datos, 'svg', _meta_ejemplo(), todo=True, es_obsoleto=True,
        )
        self.assertTrue(fn.endswith('.svg'))
        self.assertIn('image/svg+xml', ctype)
        root = etree.fromstring(svg_bytes)
        self.assertTrue(root.tag.endswith('svg'))
        # El rombo se dibuja como <polygon> y las flechas con <marker>
        self.assertTrue(root.findall('.//{http://www.w3.org/2000/svg}polygon'))
        self.assertTrue(root.findall('.//{http://www.w3.org/2000/svg}marker'))
        # El texto <script> debe estar escapado como texto literal, NUNCA como elemento <script>
        self.assertEqual(root.findall('.//script'), [])
        self.assertEqual(root.findall('.//{http://www.w3.org/2000/svg}script'), [])
        all_txt = ''.join(root.itertext())
        self.assertIn('<script>alert(1)</script>', all_txt)
        self.assertIn('OBSOLETO', all_txt)

    def test_genera_odg_valido_via_libreoffice(self):
        datos = _datos_multiempresa()
        odg_bytes, fn, ctype, _ = exportar_organigrama(
            datos, 'odg', _meta_ejemplo(), todo=True,
        )
        self.assertTrue(fn.endswith('.odg'))
        self.assertEqual(ctype, 'application/vnd.oasis.opendocument.graphics')
        with zipfile.ZipFile(io.BytesIO(odg_bytes), 'r') as zf:
            names = set(zf.namelist())
            self.assertIn('mimetype', names)
            self.assertEqual(zf.read('mimetype').decode('utf-8').strip(), ctype)
            self.assertIn('content.xml', names)
            etree.fromstring(zf.read('content.xml'))

    def test_genera_vsdx_opc_valido_y_legible_por_libreoffice(self):
        datos = _datos_multiempresa()
        vsdx_bytes, fn, ctype, _ = exportar_organigrama(
            datos, 'vsdx', _meta_ejemplo(), todo=False, section_ids=['sec_ptca'],
        )
        self.assertTrue(fn.endswith('.vsdx'))
        self.assertEqual(ctype, 'application/vnd.ms-visio.drawing.main+xml')

        with zipfile.ZipFile(io.BytesIO(vsdx_bytes), 'r') as zf:
            names = set(zf.namelist())
            requeridos = {
                '[Content_Types].xml',
                '_rels/.rels',
                'docProps/core.xml',
                'docProps/app.xml',
                'visio/document.xml',
                'visio/_rels/document.xml.rels',
                'visio/windows.xml',
                'visio/pages/pages.xml',
                'visio/pages/_rels/pages.xml.rels',
                'visio/pages/page1.xml',
            }
            self.assertTrue(requeridos.issubset(names))
            for xml_name in requeridos:
                etree.fromstring(zf.read(xml_name))

            ct_root = etree.fromstring(zf.read('[Content_Types].xml'))
            overrides = {el.get('PartName'): el.get('ContentType') for el in ct_root if el.tag.endswith('Override')}
            self.assertEqual(overrides.get('/visio/windows.xml'), 'application/vnd.ms-visio.windows+xml')

            doc_rels = etree.fromstring(zf.read('visio/_rels/document.xml.rels'))
            rel_targets = {el.get('Target') for el in doc_rels}
            self.assertIn('windows.xml', rel_targets)

            ns_v = {'v': 'http://schemas.microsoft.com/office/visio/2012/main'}
            doc_xml = etree.fromstring(zf.read('visio/document.xml'))
            self.assertIsNotNone(doc_xml.find('.//v:DocumentSheet', namespaces=ns_v))
            self.assertIsNotNone(doc_xml.find('.//v:DocumentSettings/v:GlueSettings', namespaces=ns_v))

            face_names = doc_xml.findall('.//v:FaceName', namespaces=ns_v)
            self.assertGreaterEqual(len(face_names), 1)
            for fn_el in face_names:
                self.assertNotIn('ID', fn_el.attrib)
                self.assertNotIn('Name', fn_el.attrib)
                self.assertTrue(fn_el.get('NameU'))

            for xml_name in ('visio/document.xml', 'visio/pages/page1.xml'):
                part_root = etree.fromstring(zf.read(xml_name))
                for fcell in part_root.findall('.//v:Cell[@N="Font"]', namespaces=ns_v):
                    self.assertFalse(fcell.get('V', '').isdigit(), fcell.get('V'))

            page1 = etree.fromstring(zf.read('visio/pages/page1.xml'))
            connects = page1.findall('.//v:Connect', namespaces=ns_v)
            self.assertGreaterEqual(len(connects), 2)
            conn_rows = page1.findall('.//v:Section[@N="Connection"]/v:Row', namespaces=ns_v)
            self.assertGreaterEqual(len(conn_rows), 4)
            for r_el in conn_rows:
                self.assertEqual(r_el.get('T'), 'Connection')

            edge_shapes = [
                s for s in page1.findall('.//v:Shape', namespaces=ns_v)
                if (s.get('NameU') or '').startswith('Connector_')
            ]
            self.assertGreaterEqual(len(edge_shapes), 1)
            for es in edge_shapes:
                beg_x = es.find('./v:Cell[@N="BeginX"]', namespaces=ns_v)
                con_fixed = es.find('./v:Cell[@N="ConFixedCode"]', namespaces=ns_v)
                self.assertIsNotNone(beg_x)
                self.assertIn('PAR(PNT(', beg_x.get('F', ''))
                self.assertIsNotNone(con_fixed)
                self.assertEqual(con_fixed.get('V'), '3')

            self.assertIn('Gerencia General & Dirección', ''.join(page1.itertext()))

        # Sin aristas no debe emitir <Connects/> vacío
        datos_sin_aristas = dict(datos, edges=[])
        vsdx_sin_edges, _, _, _ = exportar_organigrama(
            datos_sin_aristas, 'vsdx', _meta_ejemplo(), todo=False, section_ids=['sec_ptca'],
        )
        with zipfile.ZipFile(io.BytesIO(vsdx_sin_edges), 'r') as zf0:
            p0 = etree.fromstring(zf0.read('visio/pages/page1.xml'))
            self.assertIsNone(p0.find('.//v:Connects', namespaces={'v': 'http://schemas.microsoft.com/office/visio/2012/main'}))

        # Smoke test real con libvisio (LibreOffice --convert-to pdf)
        lo_bin = shutil.which('libreoffice')
        self.assertIsNotNone(lo_bin)
        with tempfile.TemporaryDirectory() as tmpdir:
            v_path = os.path.join(tmpdir, 'test_org.vsdx')
            prof = os.path.join(tmpdir, 'lo_prof')
            out = os.path.join(tmpdir, 'out')
            os.makedirs(prof)
            os.makedirs(out)
            with open(v_path, 'wb') as fh:
                fh.write(vsdx_bytes)
            res = subprocess.run(
                [
                    lo_bin, '--headless', '--norestore', '--nolockcheck',
                    f'-env:UserInstallation=file://{prof}',
                    '--convert-to', 'pdf', '--outdir', out, v_path,
                ],
                capture_output=True, text=True, timeout=45,
            )
            pdf_out = os.path.join(out, 'test_org.pdf')
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertTrue(os.path.isfile(pdf_out))
            with open(pdf_out, 'rb') as fh:
                self.assertTrue(fh.read(5) == b'%PDF-')


class OrganigramaExportEndpointYTemplatesTest(BaseDocumentosTest):
    """Permisos ISO, auditoría fail-closed, versiones históricas y templates Fase 2."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        TipoDocumento.objects.get_or_create(
            key='organigrama',
            defaults={'nombre': 'Organigrama', 'codigo': 'ORG', 'activo': True, 'solo_jefes': True},
        )

    def setUp(self):
        super().setUp()
        self._mongo = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo.start()
        self.addCleanup(self._mongo.stop)
        self.client = Client()

    def test_endpoint_exportar_permisos_audit_fail_closed_y_version_historica(self):
        doc = crear_organigrama_inicial(
            self.user_jefe_ti, clasificacion='publico', datos=_datos_multiempresa(),
        )
        datos_v2 = _datos_multiempresa()
        datos_v2['nodes'][0]['text'] = 'Presidencia Ejecutiva v2'
        guardar_organigrama(
            documento=doc,
            usuario=self.user_jefe_ti,
            datos_validados=datos_v2,
            revision_esperada=doc.revision_edicion,
            incrementar_version=True,
            cambios='Actualización a v2',
        )
        doc.refresh_from_db()
        url = reverse('documentos:organigrama_exportar_api', args=[doc.uuid])

        # Público: trabajador exporta v1 histórica (con OBSOLETO) y registra auditoría
        self.client.login(username='trab01', password='testpass123')
        with patch('documentos.views_organigrama.registrar_descarga') as mock_desc:
            r_v1 = self.client.post(
                url,
                data=json.dumps({
                    'formato': 'pdf',
                    'version': '1',
                    'todo': False,
                    'empresas': ['PTCA'],
                }),
                content_type='application/json',
            )
        self.assertEqual(r_v1.status_code, 200)
        self.assertEqual(r_v1['Content-Type'], 'application/pdf')
        self.assertIn('attachment; filename="SGC-ORG-001_v1_', r_v1['Content-Disposition'])
        mock_desc.assert_called_once()
        self.assertEqual(mock_desc.call_args.kwargs['version'], '1')
        txt_v1 = PdfReader(io.BytesIO(r_v1.content)).pages[0].extract_text()
        self.assertIn('OBSOLETO', txt_v1)
        self.assertIn('Gerencia General', txt_v1)

        # Confidencial: trabajador sin solicitud recibe 403; admin recibe 200
        Documento.objects.filter(pk=doc.pk).update(clasificacion='confidencial')
        r_403 = self.client.post(
            url, data=json.dumps({'formato': 'svg', 'todo': True}), content_type='application/json',
        )
        self.assertEqual(r_403.status_code, 403)

        self.client.login(username='admin01', password='testpass123')
        with patch('documentos.views_organigrama.registrar_descarga'):
            r_adm = self.client.post(
                url, data=json.dumps({'formato': 'vsdx', 'todo': True}), content_type='application/json',
            )
        self.assertEqual(r_adm.status_code, 200)

        # Trabajador con SolicitudDescarga aprobada y vigente puede exportar confidencial
        SolicitudDescarga.objects.create(
            documento=doc,
            solicitado_por=self.user_trabajador,
            motivo='Auditoría interna',
            estado='aprobada',
            version_aprobada=doc.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=5),
        )
        self.client.login(username='trab01', password='testpass123')
        with patch('documentos.views_organigrama.registrar_descarga'):
            r_auth = self.client.post(
                url, data=json.dumps({'formato': 'svg', 'todo': True}), content_type='application/json',
            )
        self.assertEqual(r_auth.status_code, 200)

        # Validaciones de errores HTTP (400, 404, 413, 405, 503)
        self.assertEqual(self.client.get(url).status_code, 405)
        r_400 = self.client.post(
            url, data=json.dumps({'formato': 'exe', 'todo': True}), content_type='application/json',
        )
        self.assertEqual(r_400.status_code, 400)
        r_404 = self.client.post(
            url, data=json.dumps({'formato': 'svg', 'version': '999', 'todo': True}), content_type='application/json',
        )
        self.assertEqual(r_404.status_code, 404)
        r_413 = self.client.post(url, data=b'{' + b' ' * 70000 + b'}', content_type='application/json')
        self.assertEqual(r_413.status_code, 413)

        with override_settings(AUDIT_FAIL_CLOSED=True):
            with patch('documentos.views_organigrama.registrar_descarga', side_effect=RuntimeError('Mongo down')):
                r_503 = self.client.post(
                    url, data=json.dumps({'formato': 'svg', 'todo': True}), content_type='application/json',
                )
        self.assertEqual(r_503.status_code, 503)

    def test_templates_incluyen_minimap_undo_redo_y_modal_exportacion(self):
        doc = crear_organigrama_inicial(self.user_jefe_ti, clasificacion='publico')
        acceso_formato.otorgar_tipo(self.user_jefe_th, 'organigrama', otorgado_por=self.user_admin)

        # Constructor (editor asignado) incluye minimap.js, undo/redo y modal de exportación, sin botón admin
        self.client.login(username='jefe_th', password='testpass123')
        r_cons = self.client.get(reverse('documentos:organigrama_constructor'))
        self.assertEqual(r_cons.status_code, 200)
        self.assertNotContains(r_cons, 'Gestionar accesos')
        self.assertContains(r_cons, 'js/organigrama/minimap.js')
        self.assertContains(r_cons, 'id="org-btn-undo"')
        self.assertContains(r_cons, 'id="org-btn-redo"')
        self.assertContains(r_cons, 'id="org-btn-export"')
        self.assertContains(r_cons, 'id="org-export-modal"')

        # Visor (trabajador en ORG público) incluye minimap.js y botón/modal de exportación
        self.client.login(username='trab01', password='testpass123')
        r_vis = self.client.get(reverse('documentos:organigrama_visor', args=[doc.uuid]))
        self.assertEqual(r_vis.status_code, 200)
        self.assertContains(r_vis, 'js/organigrama/minimap.js')
        self.assertContains(r_vis, 'id="org-btn-export"')
        self.assertContains(r_vis, 'id="org-export-modal"')

        # Ficha SGC público incluye minimap.js, botón Exportar y oculta Solicitar Descarga
        r_det = self.client.get(reverse('documentos:detalle', args=['organigrama', doc.uuid]))
        self.assertEqual(r_det.status_code, 200)
        self.assertContains(r_det, 'js/organigrama/minimap.js')
        self.assertContains(r_det, 'id="org-detail-export-btn"')
        self.assertNotContains(r_det, 'Solicitar Descarga')

        # Si pasa a confidencial sin permiso de descarga ni solicitud previa, redirige al intentar ver ficha;
        # con solicitud pendiente muestra la ficha con "Solicitud pendiente de aprobación" y sin botón Exportar.
        Documento.objects.filter(pk=doc.pk).update(clasificacion='confidencial')
        r_det_conf = self.client.get(reverse('documentos:detalle', args=['organigrama', doc.uuid]))
        self.assertEqual(r_det_conf.status_code, 302)
        SolicitudDescarga.objects.create(
            documento=doc,
            solicitado_por=self.user_trabajador,
            motivo='Consulta',
            estado='pendiente',
        )
        r_det_pend = self.client.get(reverse('documentos:detalle', args=['organigrama', doc.uuid]))
        self.assertEqual(r_det_pend.status_code, 200)
        self.assertNotContains(r_det_pend, 'id="org-detail-export-btn"')
        self.assertContains(r_det_pend, 'Solicitud pendiente de aprobación')
