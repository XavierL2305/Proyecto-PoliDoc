"""Tests para la construcción y carga del Organigrama Corporativo Oficial."""
import io
import json
import time
import zipfile
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.urls import reverse
from lxml import etree
from pypdf import PdfReader

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import Documento, TipoDocumento, VersionDocumento
from documentos.services.organigrama import (
    MAX_PAYLOAD_BYTES,
    crear_organigrama_inicial,
    guardar_organigrama,
    obtener_datos_version,
    validar_datos_organigrama,
)
from documentos.services.organigrama_export import _svg_rounded_edge_path, exportar_organigrama
from documentos.services.organigrama_oficial import construir_organigrama_oficial
from documentos.services.organigrama_router import enrutar_todas_aristas


def _boxes_overlap(a, b):
    return not (
        a['x'] + a['w'] <= b['x']
        or b['x'] + b['w'] <= a['x']
        or a['y'] + a['h'] <= b['y']
        or b['y'] + b['h'] <= a['y']
    )


def _segment_hits_box_interior(p0, p1, box, margin=1.0):
    x1, y1 = p0
    x2, y2 = p1
    return (
        max(x1, x2) > box['x'] + margin
        and min(x1, x2) < box['x'] + box['w'] - margin
        and max(y1, y2) > box['y'] + margin
        and min(y1, y2) < box['y'] + box['h'] - margin
    )


class OrganigramaOficialBuilderTest(BaseDocumentosTest):
    """Valida integridad estructural, invariantes geométricas y exportación oficial."""

    def test_construccion_oficial_exactitud_e_invariantes_geometricas(self):
        datos = construir_organigrama_oficial({'GG': 14, 'FP': 37, 'FS': 12})
        validados = validar_datos_organigrama(datos)

        self.assertEqual(len(validados['sections']), 18)
        self.assertEqual(len(validados['nodes']), 147)

        raw = json.dumps(validados, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.assertLess(len(raw), MAX_PAYLOAD_BYTES)

        # Todas las empresas representadas
        empresas = {s['empresa'] for s in validados['sections']}
        self.assertEqual(empresas, {'', 'PTCA', 'PTH', 'FP', 'FS'})

        # Arista discontinua oficial de supervisión técnica en Farmacia
        dashed = [
            e for e in validados['edges']
            if e['from'] == 'n_fp_reg' and e['to'] == 'n_fp_aux'
        ]
        self.assertEqual(len(dashed), 1)
        self.assertEqual(dashed[0]['style'], 'dashed')

        # Cero solapamientos sección-sección
        secs = validados['sections']
        for i in range(len(secs)):
            for j in range(i + 1, len(secs)):
                self.assertFalse(
                    _boxes_overlap(secs[i], secs[j]),
                    msg=f"Secciones solapadas: {secs[i]['id']} y {secs[j]['id']}",
                )

        # Cero solapamientos nodo-nodo y contención estricta dentro de su sección
        sec_map = {s['id']: s for s in secs}
        nodes = validados['nodes']
        for i in range(len(nodes)):
            ni = nodes[i]
            sec = sec_map[ni['section_id']]
            self.assertGreaterEqual(ni['x'], sec['x'], msg=ni['id'])
            self.assertGreaterEqual(ni['y'], sec['y'], msg=ni['id'])
            self.assertLessEqual(ni['x'] + ni['w'], sec['x'] + sec['w'], msg=ni['id'])
            self.assertLessEqual(ni['y'] + ni['h'], sec['y'] + sec['h'], msg=ni['id'])

            for j in range(i + 1, len(nodes)):
                nj = nodes[j]
                self.assertFalse(
                    _boxes_overlap(ni, nj),
                    msg=f"Nodos solapados: {ni['id']} y {nj['id']}",
                )

    def test_exporta_organigrama_oficial_completo_y_filtrado(self):
        datos = construir_organigrama_oficial()
        meta = {
            'codigo': 'SGC-ORG-001',
            'nombre': 'Organigrama Corporativo',
            'version': '1',
            'fecha_vigencia': '25/11/2026',
            'usuario': 'Admin Prueba',
            'fecha_generacion': '25/11/2026 09:00',
        }

        # Completo en PDF y VSDX (147 nodos)
        pdf_bytes, _, ctype_pdf, info_pdf = exportar_organigrama(datos, 'pdf', meta, todo=True)
        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))
        self.assertEqual(ctype_pdf, 'application/pdf')
        self.assertEqual(info_pdf['nodos_exportados'], 147)
        txt_pdf = ' '.join(PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text().split())
        self.assertIn('SGC-ORG-001', txt_pdf)
        self.assertIn('Asamblea de Accionistas', txt_pdf)

        vsdx_bytes, _, _, info_vsdx = exportar_organigrama(datos, 'vsdx', meta, todo=True)
        self.assertEqual(info_vsdx['nodos_exportados'], 147)
        with zipfile.ZipFile(io.BytesIO(vsdx_bytes), 'r') as zf:
            page1 = etree.fromstring(zf.read('visio/pages/page1.xml'))
            self.assertIn('Gerencia General', ''.join(page1.itertext()))

        # Filtrado por empresa FP y PTCA
        pdf_fp, _, _, info_fp = exportar_organigrama(
            datos, 'pdf', meta, todo=False, empresas=['FP'],
        )
        self.assertEqual(info_fp['nodos_exportados'], 12)
        txt_fp = ' '.join(PdfReader(io.BytesIO(pdf_fp)).pages[0].extract_text().split())
        self.assertIn('Farmacia Policlínica', txt_fp)
        self.assertNotIn('Asamblea de Accionistas', txt_fp)

        svg_ptca, _, _, info_ptca = exportar_organigrama(
            datos, 'svg', meta, todo=False, empresas=['PTCA'],
        )
        self.assertEqual(info_ptca['nodos_exportados'], 18)
        svg_root = etree.fromstring(svg_ptca)
        self.assertIn('Policlínica Táchira C.A', ' '.join(''.join(svg_root.itertext()).split()))

    def test_enrutador_cero_colisiones_en_organigrama_oficial(self):
        datos = construir_organigrama_oficial({'GG': 14, 'FP': 37, 'FS': 12})
        t0 = time.monotonic()
        routes = enrutar_todas_aristas(datos, mostrar_marcos=True)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 2.0, f'Enrutador excedió presupuesto: {elapsed:.2f}s')
        self.assertEqual(len(routes), len(datos['edges']))

        node_map = {n['id']: n for n in datos['nodes']}
        node_cols = []
        sec_cols = []
        for edge in datos['edges']:
            pts = routes[edge['id']]['points']
            self.assertGreaterEqual(len(pts), 2)
            src = node_map[edge['from']]
            dst = node_map[edge['to']]
            own_nodes = {src['id'], dst['id']}
            own_secs = {src.get('section_id'), dst.get('section_id')}
            for p0, p1 in zip(pts, pts[1:]):
                for n in datos['nodes']:
                    if n['id'] not in own_nodes and _segment_hits_box_interior(p0, p1, n, margin=1.0):
                        node_cols.append((edge['id'], src['id'], dst['id'], n['id']))
                for s in datos['sections']:
                    if s['id'] not in own_secs and _segment_hits_box_interior(p0, p1, s, margin=1.0):
                        sec_cols.append((edge['id'], src['id'], dst['id'], s['id']))

        self.assertEqual(node_cols, [], f'Colisiones contra nodos: {node_cols[:5]}')
        self.assertEqual(sec_cols, [], f'Colisiones contra marcos de sección: {sec_cols[:5]}')

        # Verifica arcos de salto en ambas direcciones horizontales y curvas SVG/halo
        d_ltr = _svg_rounded_edge_path([(0, 100), (200, 100)], {0: [100.0]}, corner_r=8.0, jump_r=6.0)
        d_rtl = _svg_rounded_edge_path([(200, 100), (0, 100)], {0: [100.0]}, corner_r=8.0, jump_r=6.0)
        self.assertIn('A 6.00 6.00 0 0 1 106.00 100.00', d_ltr)
        self.assertIn('A 6.00 6.00 0 0 0 94.00 100.00', d_rtl)

        svg_bytes, _, _, _ = exportar_organigrama(datos, 'svg', {'codigo': 'SGC-ORG-001', 'nombre': 'Org'}, todo=True)
        svg_text = svg_bytes.decode('utf-8')
        self.assertIn('stroke="#FFFFFF"', svg_text)
        self.assertIn(' Q ', svg_text)


class CargarOrganigramaOficialCommandTest(BaseDocumentosTest):
    """Valida idempotencia, --dry-run, carga sobre v1 vacío y --nueva-version."""

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

    def test_dry_run_no_persiste_ni_crea_documento(self):
        out = io.StringIO()
        call_command('cargar_organigrama_oficial', '--dry-run', stdout=out)
        self.assertIn('18 secciones', out.getvalue())
        self.assertIn('147 figuras', out.getvalue())
        self.assertFalse(Documento.objects.filter(tipo_id='organigrama').exists())

    def test_carga_en_v1_vacio_protege_sobrescritura_y_permite_nueva_version(self):
        # 1. Caso real: ya existe SGC-ORG-001 v1 vacío (0 secciones / 0 nodos)
        doc = crear_organigrama_inicial(self.user_admin)
        out = io.StringIO()
        call_command('cargar_organigrama_oficial', stdout=out)
        doc.refresh_from_db()
        self.assertEqual(doc.version_actual, '1')
        _, datos_v1 = obtener_datos_version(doc)
        self.assertEqual(len(datos_v1['sections']), 18)
        self.assertEqual(len(datos_v1['nodes']), 147)

        # 2. Edición manual en v1 queda protegida si se vuelve a correr sin flags
        datos_custom = json.loads(json.dumps(datos_v1))
        datos_custom['nodes'][0]['text'] = 'Edición manual protegida'
        guardar_organigrama(
            doc,
            self.user_admin,
            datos_validados=datos_custom,
            revision_esperada=doc.revision_edicion,
            incrementar_version=False,
        )
        doc.refresh_from_db()
        rev_previa = doc.revision_edicion

        out2 = io.StringIO()
        call_command('cargar_organigrama_oficial', stdout=out2)
        doc.refresh_from_db()
        self.assertEqual(doc.revision_edicion, rev_previa)
        _, datos_tras = obtener_datos_version(doc)
        self.assertEqual(datos_tras['nodes'][0]['text'], 'Edición manual protegida')

        # 3. --nueva-version crea v2 oficial preservando v1
        out3 = io.StringIO()
        call_command('cargar_organigrama_oficial', '--nueva-version', stdout=out3)
        doc.refresh_from_db()
        self.assertEqual(doc.version_actual, '2')
        self.assertEqual(VersionDocumento.objects.filter(documento=doc, activa=True).count(), 2)
        _, datos_v2 = obtener_datos_version(doc, version_str='2')
        self.assertEqual(len(datos_v2['nodes']), 147)
        self.assertEqual(datos_v2['nodes'][0]['text'], 'Asamblea de Accionistas')

    def test_vistas_incluyen_router_antes_de_renderer_y_toggle_enlaces(self):
        call_command('cargar_organigrama_oficial', stdout=io.StringIO())
        doc = Documento.objects.get(tipo_id='organigrama', codigo='SGC-ORG-001')
        self.client.force_login(self.user_admin)

        rutas = [
            reverse('documentos:organigrama_constructor'),
            reverse('documentos:organigrama_visor', kwargs={'documento_id': doc.uuid}),
            reverse('documentos:detalle', kwargs={'tipo': 'organigrama', 'documento_id': doc.uuid}),
        ]
        for url in rutas:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
            html = resp.content.decode('utf-8')
            self.assertIn('js/organigrama/router.js', html, url)
            self.assertIn('js/organigrama/renderer.js', html, url)
            self.assertLess(
                html.index('js/organigrama/router.js'),
                html.index('js/organigrama/renderer.js'),
                url,
            )
            self.assertIn('id="org-btn-toggle-cross-edges"', html, url)
