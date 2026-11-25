"""Tests de Mapa de Procesos (MAP) heredado y unicidad por empresa (MAP, PLC, OBJ, MSC, PSS, POL)."""
import io
import json
import zipfile
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from lxml import etree

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.forms import TipoDocumentoForm
from documentos.models import (
    TIPOS_UNICOS_POR_EMPRESA,
    Documento,
    TipoDocumento,
    VersionDocumento,
)
from documentos.services.mapa_procesos_oficial import (
    construir_mapa_procesos_fs,
    construir_mapa_procesos_oficial,
)
from documentos.services.organigrama import (
    MAX_PAYLOAD_BYTES,
    TIPO_MAPA_PROCESOS,
    crear_mapa_procesos_inicial,
    obtener_datos_version,
    obtener_mapa_procesos_vigente,
    validar_datos_organigrama,
)
from documentos.services.organigrama_export import exportar_organigrama
from documentos.services.organigrama_router import enrutar_todas_aristas


def _payload_mapa_valido(empresa='PTCA'):
    return {
        'schema_version': 1,
        'settings': {'gridSize': 20, 'snapToGrid': True},
        'sections': [
            {
                'id': 'sec_est',
                'title': 'Procesos de Dirección',
                'empresa': empresa,
                'departamento_id': None,
                'x': 40,
                'y': 40,
                'w': 480,
                'h': 260,
                'color': '#0284C7',
                'order': 0,
            },
        ],
        'nodes': [
            {
                'id': 'n_plan',
                'section_id': 'sec_est',
                'shape': 'rounded',
                'x': 80,
                'y': 100,
                'w': 180,
                'h': 70,
                'text': 'Planificación Estratégica',
                'zIndex': 2,
                'style': {'bgColor': '#FFFFFF', 'textColor': '#0F172A', 'borderColor': '#0284C7'},
            },
            {
                'id': 'n_rev',
                'section_id': 'sec_est',
                'shape': 'rounded',
                'x': 310,
                'y': 100,
                'w': 180,
                'h': 70,
                'text': 'Revisión por la Dirección',
                'zIndex': 2,
                'style': {'bgColor': '#FFFFFF', 'textColor': '#0F172A', 'borderColor': '#0284C7'},
            },
        ],
        'edges': [
            {
                'id': 'e_1',
                'from': 'n_plan',
                'to': 'n_rev',
                'fromAnchor': 'right',
                'toAnchor': 'left',
                'routing': 'orthogonal',
                'color': '#0284C7',
                'width': 2,
                'style': 'solid',
                'arrowEnd': True,
                'label': 'Flujo',
            },
        ],
    }


class UnicoPorEmpresaTest(BaseDocumentosTest):
    """Configuración y unicidad por empresa para MAP, PLC, OBJ, MSC, PSS y POL."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command('poblar_tipos_documento', stdout=io.StringIO())

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_tipos_corporativos_tienen_prefijo_y_unicidad_por_empresa(self):
        self.assertEqual(
            set(TIPOS_UNICOS_POR_EMPRESA),
            {'mapa_procesos', 'plan_calidad', 'objetivos', 'manual_calidad', 'programa_seguridad', 'politica_calidad'},
        )
        for key in TIPOS_UNICOS_POR_EMPRESA:
            tipo = TipoDocumento.objects.get(key=key)
            self.assertTrue(tipo.usa_prefijo_empresa, key)
            self.assertTrue(tipo.unico_por_empresa, key)
            self.assertFalse(tipo.sin_prefijo_depto, key)
        self.assertTrue(TipoDocumento.objects.get(key='mapa_procesos').solo_jefes)

    def test_tipo_documento_form_exige_prefijo_empresa_si_es_unico(self):
        form = TipoDocumentoForm(data={
            'codigo': 'TST',
            'nombre': 'Tipo Prueba',
            'descripcion': '',
            'activo': True,
            'icono': 'file',
            'color': 'primary',
            'usa_prefijo_empresa': False,
            'unico_por_empresa': True,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('unico_por_empresa', form.errors)

    def test_unicidad_por_empresa_en_modelo_y_vista_creacion(self):
        tipo_plc = TipoDocumento.objects.get(key='plan_calidad')
        archivo = SimpleUploadedFile('plc.pdf', b'%PDF-1.4\n%test\n', content_type='application/pdf')

        doc_ptca = Documento.objects.create(
            tipo=tipo_plc,
            nombre='Plan de Calidad PTCA',
            codigo='PTCA-PLC-001',
            empresa='PTCA',
            estado='activo',
            propietario=self.user_jefe_ti,
            departamento=self.depto_ti,
            archivo=archivo,
        )

        # Segundo PLC activo/borrador para la misma empresa falla validación
        duplicado = Documento(
            tipo=tipo_plc,
            nombre='Plan Duplicado PTCA',
            codigo='PTCA-PLC-002',
            empresa='PTCA',
            estado='borrador',
            propietario=self.user_jefe_ti,
            departamento=self.depto_ti,
        )
        with self.assertRaises(ValidationError):
            duplicado.full_clean()

        # En otra empresa (PTH) sí está permitido
        doc_pth = Documento(
            tipo=tipo_plc,
            nombre='Plan de Calidad PTH',
            codigo='PTH-PLC-001',
            empresa='PTH',
            estado='borrador',
            propietario=self.user_jefe_ti,
            departamento=self.depto_ti,
        )
        doc_pth.full_clean()
        doc_pth.save()

        # Plantilla en PTCA también sigue permitida aunque exista documento vigente
        plantilla = Documento(
            tipo=tipo_plc,
            nombre='Plantilla PLC PTCA',
            codigo='PTCA-PLC-PLT-001',
            empresa='PTCA',
            es_plantilla=True,
            estado='activo',
            propietario=self.user_jefe_ti,
            departamento=self.depto_ti,
        )
        plantilla.full_clean()

        # POST en crear_documento bloquea crear segundo PLC en PTCA
        self.client.force_login(self.user_jefe_ti)
        resp = self.client.post(
            reverse('documentos:crear_documento', args=['plan_calidad']),
            {
                'nombre': 'Otro PLC PTCA',
                'descripcion': 'Intento duplicado',
                'clasificacion': 'interno',
                'empresa': 'PTCA',
                'secuencia': '002',
                'accion': 'borrador',
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Documento.objects.filter(codigo='PTCA-PLC-002').exists())

        # Si el vigente de PTCA pasa a obsoleto, se puede crear el reemplazo
        doc_ptca.estado = 'obsoleto'
        doc_ptca.save(update_fields=['estado'])
        nuevo_ptca = Documento(
            tipo=tipo_plc,
            nombre='Plan Renovado PTCA',
            codigo='PTCA-PLC-002',
            empresa='PTCA',
            estado='borrador',
            propietario=self.user_jefe_ti,
            departamento=self.depto_ti,
        )
        nuevo_ptca.full_clean()
        nuevo_ptca.save()


class MapaProcesosInteractivoTest(BaseDocumentosTest):
    """Flujo heredado de Mapa de Procesos (MAP): índice, constructor, visor, guardado, export y auditoría."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command('poblar_tipos_documento', stdout=io.StringIO())

    def setUp(self):
        super().setUp()
        self._mongo = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo.start()
        self.addCleanup(self._mongo.stop)
        self.client = Client()

    def test_redirecciones_creacion_y_bloqueo_plantilla_y_no_jefes(self):
        self.client.force_login(self.user_jefe_ti)

        resp_crear = self.client.get(reverse('documentos:crear_documento', args=['mapa_procesos']))
        self.assertRedirects(resp_crear, reverse('documentos:mapa_procesos_index'))

        resp_crear_emp = self.client.get(
            reverse('documentos:crear_documento', args=['mapa_procesos']) + '?empresa=FS'
        )
        self.assertEqual(resp_crear_emp.status_code, 302)
        self.assertIn('/documentos/mapa-procesos/FS/constructor/', resp_crear_emp['Location'])

        resp_plt = self.client.get(reverse('documentos:crear_plantilla', args=['mapa_procesos']))
        self.assertRedirects(resp_plt, reverse('documentos:catalogo'))

        resp_sel = self.client.get(reverse('documentos:seleccionar_tipo') + '?plantilla=1')
        self.assertEqual(resp_sel.status_code, 200)
        self.assertNotIn('/documentos/plantillas/mapa_procesos/', resp_sel.content.decode('utf-8'))

        # Trabajador no puede abrir constructor de MAP
        self.client.force_login(self.user_trabajador)
        resp_trab = self.client.get(reverse('documentos:mapa_procesos_constructor', args=['PTCA']))
        self.assertEqual(resp_trab.status_code, 302)
        self.assertIn(reverse('documentos:mapa_procesos_index'), resp_trab['Location'])

    def test_constructor_por_empresa_guardado_versionado_export_y_vistas_heredadas(self):
        self.client.force_login(self.user_admin)

        with patch('auditoria.services.registrar_creacion_documento') as mock_crea:
            r_ptca = self.client.get(reverse('documentos:mapa_procesos_constructor', args=['PTCA']))
            r_fs = self.client.get(reverse('documentos:mapa_procesos_constructor', args=['FS']))

        self.assertEqual(r_ptca.status_code, 200)
        self.assertEqual(r_fs.status_code, 200)
        mock_crea.assert_called()
        self.assertTrue(any(c.kwargs.get('recurso_tipo') == TIPO_MAPA_PROCESOS for c in mock_crea.call_args_list))

        doc_ptca = Documento.objects.get(tipo_id=TIPO_MAPA_PROCESOS, empresa='PTCA', es_plantilla=False)
        doc_fs = Documento.objects.get(tipo_id=TIPO_MAPA_PROCESOS, empresa='FS', es_plantilla=False)
        self.assertEqual(doc_ptca.codigo, 'PTCA-MAP-001')
        self.assertEqual(doc_fs.codigo, 'FS-MAP-001')

        # Idempotente si se vuelve a entrar al constructor
        doc_ptca_2 = crear_mapa_procesos_inicial(self.user_admin, 'PTCA')
        self.assertEqual(doc_ptca_2.pk, doc_ptca.pk)

        html_const = r_ptca.content.decode('utf-8')
        self.assertIn('Constructor de Mapa de Procesos', html_const)
        self.assertIn('data-doc-kind="mapa_procesos"', html_const)
        self.assertIn('data-doc-empresa="PTCA"', html_const)
        self.assertIn('js/organigrama/router.js', html_const)
        self.assertIn('id="org-btn-toggle-cross-edges"', html_const)

        # Guardado con versionado v1 -> v2 y control optimista
        guardar_url = reverse('documentos:organigrama_guardar_api', args=[doc_ptca.uuid])
        with patch('auditoria.services.registrar_edicion_documento') as mock_ed:
            r_save = self.client.post(
                guardar_url,
                data=json.dumps({
                    'revision_edicion': doc_ptca.revision_edicion,
                    'incrementar_version': True,
                    'cambios': 'Procesos estratégicos PTCA',
                    'datos': _payload_mapa_valido('PTCA'),
                }),
                content_type='application/json',
            )
        self.assertEqual(r_save.status_code, 200)
        self.assertEqual(r_save.json()['version_actual'], '2')
        self.assertEqual(mock_ed.call_args.kwargs.get('recurso_tipo'), TIPO_MAPA_PROCESOS)

        doc_ptca.refresh_from_db()
        _, datos_v2 = obtener_datos_version(doc_ptca, version_str='2')
        self.assertEqual(len(datos_v2['nodes']), 2)
        self.assertEqual(VersionDocumento.objects.filter(documento=doc_ptca, activa=True).count(), 2)

        # Revisión obsoleta produce conflicto 409
        r_conf = self.client.post(
            guardar_url,
            data=json.dumps({
                'revision_edicion': 0,
                'incrementar_version': False,
                'datos': _payload_mapa_valido('PTCA'),
            }),
            content_type='application/json',
        )
        self.assertEqual(r_conf.status_code, 409)

        # API de consulta y visor + detalle + historial con auditoría MAP
        datos_url = reverse('documentos:organigrama_datos_api', args=[doc_ptca.uuid])
        with patch('documentos.views_organigrama.registrar_consulta') as mock_cons:
            r_datos = self.client.get(datos_url)
        self.assertEqual(r_datos.status_code, 200)
        self.assertEqual(r_datos.json()['documento']['tipo'], TIPO_MAPA_PROCESOS)
        self.assertEqual(r_datos.json()['documento']['empresa'], 'PTCA')
        self.assertEqual(mock_cons.call_args.kwargs.get('recurso_tipo'), TIPO_MAPA_PROCESOS)

        # Exportación SVG, PDF y VSDX sobre el mismo endpoint compartido
        exp_url = reverse('documentos:organigrama_exportar_api', args=[doc_ptca.uuid])
        for fmt in ('svg', 'pdf', 'vsdx'):
            with patch('documentos.views_organigrama.registrar_descarga') as mock_desc:
                r_exp = self.client.post(
                    exp_url,
                    data=json.dumps({'formato': fmt, 'todo': True}),
                    content_type='application/json',
                )
            self.assertEqual(r_exp.status_code, 200, fmt)
            self.assertEqual(mock_desc.call_args.kwargs.get('recurso_tipo'), TIPO_MAPA_PROCESOS)
            self.assertEqual(
                mock_desc.call_args.kwargs['detalles']['alcance'],
                'Mapa de Procesos completo',
            )
            if fmt == 'svg':
                self.assertIn('PTCA-MAP-001_v2_completo.svg', r_exp['Content-Disposition'])
                self.assertIn('Planificación', r_exp.content.decode('utf-8'))
            elif fmt == 'vsdx':
                with zipfile.ZipFile(io.BytesIO(r_exp.content), 'r') as zf:
                    page1 = etree.fromstring(zf.read('visio/pages/page1.xml'))
                    self.assertIn('Planificación Estratégica', ''.join(page1.itertext()))

        # Índice, visor dedicado, ficha detalle e historial de versiones
        r_idx = self.client.get(reverse('documentos:mapa_procesos_index'))
        self.assertEqual(r_idx.status_code, 200)
        idx_html = r_idx.content.decode('utf-8')
        self.assertIn('PTCA-MAP-001', idx_html)
        self.assertIn('FS-MAP-001', idx_html)

        for url in (
            reverse('documentos:mapa_procesos_visor', args=[doc_ptca.uuid]),
            reverse('documentos:detalle', args=[TIPO_MAPA_PROCESOS, doc_ptca.uuid]),
            reverse('documentos:historial_versiones', args=[TIPO_MAPA_PROCESOS, doc_ptca.uuid]),
        ):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
            body = resp.content.decode('utf-8')
            self.assertIn(f'/documentos/mapa-procesos/{doc_ptca.uuid}/visor/', body)


def _boxes_overlap_map(a, b):
    return not (
        a['x'] + a['w'] <= b['x']
        or b['x'] + b['w'] <= a['x']
        or a['y'] + a['h'] <= b['y']
        or b['y'] + b['h'] <= a['y']
    )


def _segment_hits_box_interior_map(p0, p1, box, margin=1.0):
    x1, y1 = p0
    x2, y2 = p1
    return (
        max(x1, x2) > box['x'] + margin
        and min(x1, x2) < box['x'] + box['w'] - margin
        and max(y1, y2) > box['y'] + margin
        and min(y1, y2) < box['y'] + box['h'] - margin
    )


class MapaProcesosOficialFSTest(BaseDocumentosTest):
    """Valida el Mapa de Procesos oficial de Fuente de Soda (FS-MAP-001) y su comando de carga."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        call_command('poblar_tipos_documento', stdout=io.StringIO())

    def setUp(self):
        super().setUp()
        self._mongo = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo.start()
        self.addCleanup(self._mongo.stop)

    def test_construccion_fs_contenido_y_geometria_sin_solapes(self):
        datos = construir_mapa_procesos_oficial('fs', {'FS': 12})
        validados = validar_datos_organigrama(datos)

        self.assertEqual(len(validados['sections']), 7)
        self.assertEqual(len(validados['nodes']), 23)
        self.assertEqual(len(validados['edges']), 12)

        raw = json.dumps(validados, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.assertLess(len(raw), MAX_PAYLOAD_BYTES)
        self.assertEqual({s['empresa'] for s in validados['sections']}, {'FS'})

        nodes_by_id = {n['id']: n for n in validados['nodes']}
        # Los 14 procesos numerados del PDF
        for num in range(1, 15):
            self.assertTrue(
                any(f'{num}. ' in n['text'] for n in validados['nodes']),
                msg=f'Falta proceso {num} en FS-MAP-001',
            )

        # Asteriscos (*) exactamente en los procesos 3, 4, 5, 6, 7, 13 y 14
        con_estrella = {
            nid for nid in ('fs_p03', 'fs_p04', 'fs_p05', 'fs_p06', 'fs_p07', 'fs_p13', 'fs_p14')
        }
        for nid in con_estrella:
            self.assertIn('*', nodes_by_id[nid]['text'], msg=nid)
        self.assertNotIn('*', nodes_by_id['fs_p12']['text'])

        self.assertEqual(nodes_by_id['fs_alimentos']['text'], 'Alimentos\nPreparados')
        self.assertIn('Actividades que Aportan Valor', nodes_by_id['fs_leg_valor']['text'])
        self.assertIn('Flujo de Información', nodes_by_id['fs_leg_info']['text'])
        self.assertIn('Procesos que interactúan con todos', nodes_by_id['fs_leg_star']['text'])

        # Sin solapes entre secciones
        secs = validados['sections']
        for i in range(len(secs)):
            for j in range(i + 1, len(secs)):
                self.assertFalse(
                    _boxes_overlap_map(secs[i], secs[j]),
                    msg=f"Secciones solapadas: {secs[i]['id']} y {secs[j]['id']}",
                )

        # Sin solapes entre nodos y contención estricta dentro de su sección
        sec_map = {s['id']: s for s in secs}
        nodes = validados['nodes']
        for i in range(len(nodes)):
            ni = nodes[i]
            sid = ni.get('section_id')
            if sid is not None:
                sec = sec_map[sid]
                self.assertGreaterEqual(ni['x'], sec['x'], msg=ni['id'])
                self.assertGreaterEqual(ni['y'], sec['y'], msg=ni['id'])
                self.assertLessEqual(ni['x'] + ni['w'], sec['x'] + sec['w'], msg=ni['id'])
                self.assertLessEqual(ni['y'] + ni['h'], sec['y'] + sec['h'], msg=ni['id'])
            for j in range(i + 1, len(nodes)):
                self.assertFalse(
                    _boxes_overlap_map(ni, nodes[j]),
                    msg=f"Nodos solapados: {ni['id']} y {nodes[j]['id']}",
                )

        with self.assertRaises(ValueError):
            construir_mapa_procesos_oficial('PTCA')

    def test_enrutador_cero_colisiones_y_exportacion_multiformato_fs(self):
        datos = construir_mapa_procesos_fs({'FS': 12})
        routes = enrutar_todas_aristas(datos, mostrar_marcos=True)
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
                    if n['id'] not in own_nodes and _segment_hits_box_interior_map(p0, p1, n):
                        node_cols.append((edge['id'], n['id']))
                for s in datos['sections']:
                    if s['id'] not in own_secs and _segment_hits_box_interior_map(p0, p1, s):
                        sec_cols.append((edge['id'], s['id']))

        self.assertEqual(node_cols, [], f'Colisiones contra nodos: {node_cols}')
        self.assertEqual(sec_cols, [], f'Colisiones contra secciones: {sec_cols}')

        meta = {
            'codigo': 'FS-MAP-001',
            'nombre': 'Mapa de Procesos — Fuente de Soda Policlínica Táchira, C.A.',
            'tipo_label': 'Mapa de Procesos',
            'version': '1',
            'fecha_vigencia': '25/11/2026',
            'usuario': 'Admin Prueba',
            'fecha_generacion': '25/11/2026 14:15',
        }
        svg_bytes, _, _, info_svg = exportar_organigrama(datos, 'svg', meta, todo=True)
        self.assertEqual(info_svg['nodos_exportados'], 23)
        svg_text = svg_bytes.decode('utf-8')
        self.assertIn('Planificación', svg_text)
        self.assertIn('Alimentos', svg_text)

        pdf_bytes, _, ctype_pdf, _ = exportar_organigrama(datos, 'pdf', meta, todo=True)
        self.assertEqual(ctype_pdf, 'application/pdf')
        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))

        vsdx_bytes, _, _, _ = exportar_organigrama(datos, 'vsdx', meta, todo=True)
        with zipfile.ZipFile(io.BytesIO(vsdx_bytes), 'r') as zf:
            page1_txt = ''.join(etree.fromstring(zf.read('visio/pages/page1.xml')).itertext())
            self.assertIn('Planificación', page1_txt)
            self.assertIn('Alimentos', page1_txt)

    def test_comando_cargar_mapa_procesos_dry_run_idempotencia_y_versionado(self):
        # 1. Dry-run no crea ni escribe en BD
        out_dry = io.StringIO()
        call_command('cargar_mapa_procesos', '--empresa', 'FS', '--dry-run', stdout=out_dry)
        self.assertIn('[DRY-RUN] Mapa de Procesos FS válido', out_dry.getvalue())
        self.assertIsNone(obtener_mapa_procesos_vigente('FS'))

        # 2. Carga sobre FS-MAP-001 vacío existente en v1
        doc_vacio = crear_mapa_procesos_inicial(self.user_admin, 'FS')
        self.assertEqual(doc_vacio.version_actual, '1')

        out_load = io.StringIO()
        with patch('auditoria.services.registrar_edicion_documento'):
            call_command(
                'cargar_mapa_procesos',
                '--empresa', 'FS',
                '--usuario', self.user_admin.username,
                stdout=out_load,
            )
        self.assertIn('Mapa de Procesos FS cargado en FS-MAP-001 v1', out_load.getvalue())

        doc = obtener_mapa_procesos_vigente('FS')
        _, datos_v1 = obtener_datos_version(doc, version_str='1')
        self.assertEqual(len(datos_v1['nodes']), 23)
        self.assertEqual(len(datos_v1['sections']), 7)
        self.assertEqual(len(datos_v1['edges']), 12)

        # 3. Segunda ejecución sin flags protege contenido existente
        out_warn = io.StringIO()
        call_command('cargar_mapa_procesos', '--empresa', 'FS', stdout=out_warn)
        self.assertIn('ya tiene contenido guardado', out_warn.getvalue())
        doc.refresh_from_db()
        self.assertEqual(doc.version_actual, '1')

        # 4. --nueva-version incrementa v1 -> v2
        out_v2 = io.StringIO()
        with patch('auditoria.services.registrar_edicion_documento'):
            call_command(
                'cargar_mapa_procesos',
                '--empresa', 'FS',
                '--usuario', self.user_admin.username,
                '--nueva-version',
                stdout=out_v2,
            )
        self.assertIn('v2', out_v2.getvalue())
        doc.refresh_from_db()
        self.assertEqual(doc.version_actual, '2')
        self.assertEqual(VersionDocumento.objects.filter(documento=doc, activa=True).count(), 2)
