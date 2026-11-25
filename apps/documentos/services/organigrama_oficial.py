"""Constructor determinista del Organigrama Corporativo Oficial (SGC-ORG-001).

Replica los 147 cargos/unidades oficiales extraídos de
``Organigrama_Definitivo_-_copia.pdf`` con su paleta cromática corporativa,
dotación de personal, relaciones jerárquicas y supervisión técnica discontinua
en Farmacia, organizados en 18 secciones limpias por Empresa y Departamento sin
solapamientos.
"""
from .organigrama import validar_datos_organigrama

PALETAS = {
    'gob': {'bgColor': '#FFFFFF', 'borderColor': '#334155', 'textColor': '#0F172A'},
    'staff': {'bgColor': '#F59D56', 'borderColor': '#C2410C', 'textColor': '#0F172A'},
    'dir': {'bgColor': '#4BACC6', 'borderColor': '#0E7490', 'textColor': '#0F172A'},
    'ptca': {'bgColor': '#00B0F0', 'borderColor': '#0369A1', 'textColor': '#0F172A'},
    'pth': {'bgColor': '#FFD965', 'borderColor': '#B45309', 'textColor': '#0F172A'},
    'fp': {'bgColor': '#FFFF00', 'borderColor': '#A16207', 'textColor': '#0F172A'},
    'fs': {'bgColor': '#92CDDC', 'borderColor': '#0369A1', 'textColor': '#0F172A'},
}

DEFAULT_W = 180
DEFAULT_H = 60
GRID = 20


def _snap(val):
    return int(round(val / GRID) * GRID)


def _node_height(text):
    lines = text.split('\n')
    chars_max = max(len(line) for line in lines) if lines else 0
    if len(lines) >= 3 or len(text) > 48 or chars_max > 34:
        return 80
    return DEFAULT_H


def _make_node(node_id, section_id, text, paleta_key, bold=False, w=DEFAULT_W, h=None, z_index=2):
    pal = PALETAS[paleta_key]
    altura = h if h is not None else _node_height(text)
    return {
        'id': node_id,
        'section_id': section_id,
        'shape': 'rounded',
        'x': 0,
        'y': 0,
        'w': w,
        'h': altura,
        'text': text,
        'zIndex': z_index,
        'style': {
            'bgColor': pal['bgColor'],
            'textColor': pal['textColor'],
            'borderColor': pal['borderColor'],
            'borderWidth': 2,
            'borderStyle': 'solid',
            'borderRadius': 8,
            'fontWeight': 'bold' if bold else 'normal',
            'fontStyle': 'normal',
            'fontSize': 12,
            'textAlign': 'center',
        },
    }


def _make_edge(
    edge_id,
    from_id,
    to_id,
    from_anchor='bottom',
    to_anchor='top',
    routing='orthogonal',
    style='solid',
    color='auto',
    width=2,
    arrow_end=True,
    label='',
):
    return {
        'id': edge_id,
        'from': from_id,
        'to': to_id,
        'fromAnchor': from_anchor,
        'toAnchor': to_anchor,
        'routing': routing,
        'color': color,
        'width': width,
        'style': style,
        'arrowEnd': arrow_end,
        'label': label,
    }


def _layout_tree_down(root_id, children_map, nodes_by_id, edges_out, h_gap=20, v_gap=40):
    """Posiciona un árbol jerárquico top-down en coordenadas locales >= 0 sin solapes."""
    widths = {}

    def calc_width(nid):
        ch = children_map.get(nid, [])
        nw = nodes_by_id[nid]['w']
        if not ch:
            widths[nid] = nw
            return nw
        total = sum(calc_width(c) for c in ch) + h_gap * (len(ch) - 1)
        widths[nid] = max(nw, total)
        return widths[nid]

    calc_width(root_id)

    def place(nid, left_x, top_y):
        node = nodes_by_id[nid]
        sub_w = widths[nid]
        node['x'] = _snap(left_x + (sub_w - node['w']) / 2)
        node['y'] = _snap(top_y)

        ch = children_map.get(nid, [])
        if not ch:
            return
        child_y = _snap(node['y'] + node['h'] + v_gap)
        total_ch = sum(widths[c] for c in ch) + h_gap * (len(ch) - 1)
        cur_x = left_x + (sub_w - total_ch) / 2
        for cid in ch:
            edges_out.append((nid, cid, 'bottom', 'top'))
            place(cid, cur_x, child_y)
            cur_x += widths[cid] + h_gap

    place(root_id, 0, 0)
    return list(nodes_by_id.values())


def _layout_tree_right(root_id, children_map, nodes_by_id, edges_out, h_gap=40, v_gap=20):
    """Posiciona un sub-árbol horizontalmente de izquierda a derecha sin solapes."""
    heights = {}

    def calc_height(nid):
        ch = children_map.get(nid, [])
        nh = nodes_by_id[nid]['h']
        if not ch:
            heights[nid] = nh
            return nh
        total = sum(calc_height(c) for c in ch) + v_gap * (len(ch) - 1)
        heights[nid] = max(nh, total)
        return heights[nid]

    calc_height(root_id)

    def place(nid, left_x, top_y):
        node = nodes_by_id[nid]
        sub_h = heights[nid]
        node['x'] = _snap(left_x)
        node['y'] = _snap(top_y + (sub_h - node['h']) / 2)

        ch = children_map.get(nid, [])
        if not ch:
            return
        child_x = _snap(node['x'] + node['w'] + h_gap)
        total_ch = sum(heights[c] for c in ch) + v_gap * (len(ch) - 1)
        cur_y = top_y + (sub_h - total_ch) / 2
        for cid in ch:
            edges_out.append((nid, cid, 'right', 'left'))
            place(cid, child_x, cur_y)
            cur_y += heights[cid] + v_gap

    place(root_id, 0, 0)
    return heights[root_id]


def _layout_grid(node_list, cols=2, h_gap=20, v_gap=20, origin_x=0, origin_y=0):
    """Posiciona una lista de nodos en cuadrícula alineada a la grilla."""
    row_y = origin_y
    for idx in range(0, len(node_list), cols):
        chunk = node_list[idx:idx + cols]
        cur_x = origin_x
        row_h = max(n['h'] for n in chunk)
        for node in chunk:
            node['x'] = _snap(cur_x)
            node['y'] = _snap(row_y)
            cur_x += node['w'] + h_gap
        row_y = _snap(row_y + row_h + v_gap)


def _offset_nodes(nodes, dx, dy):
    for n in nodes:
        n['x'] = _snap(n['x'] + dx)
        n['y'] = _snap(n['y'] + dy)


def _wrap_section(sec_id, title, empresa, depto_code, color, order, local_nodes, deptos_map):
    """Calcula la caja de sección alrededor de ``local_nodes`` normalizados."""
    pad_x = 40
    pad_top = 60
    pad_bottom = 40

    min_x = min(n['x'] for n in local_nodes)
    min_y = min(n['y'] for n in local_nodes)
    shift_x = pad_x - min_x
    shift_y = pad_top - min_y
    _offset_nodes(local_nodes, shift_x, shift_y)

    max_x = max(n['x'] + n['w'] for n in local_nodes)
    max_y = max(n['y'] + n['h'] for n in local_nodes)

    sec = {
        'id': sec_id,
        'title': title,
        'empresa': empresa,
        'departamento_id': deptos_map.get(depto_code),
        'x': 0,
        'y': 0,
        'w': _snap(max_x + pad_x),
        'h': _snap(max_y + pad_bottom),
        'color': color,
        'order': order,
    }
    return sec, local_nodes


def _build_section_gob_dir(deptos_map):
    sid = 'sec_gob_dir'
    nodes = [
        _make_node('n_asamblea', sid, 'Asamblea de Accionistas', 'gob', bold=True),
        _make_node('n_comisario', sid, 'Comisario', 'gob'),
        _make_node('n_junta', sid, 'Junta Directiva', 'gob', bold=True),
        _make_node('n_soc_med', sid, 'Sociedad Médica', 'dir'),
        _make_node('n_com_tec', sid, 'Comisión Técnica', 'dir'),
        _make_node('n_com_comp', sid, 'Comité de Compras', 'dir'),
        _make_node('n_coord_med', sid, 'Coordinación Médica', 'pth'),
        _make_node('n_gg', sid, 'Gerencia General', 'dir', bold=True),
        _make_node('n_com_fin', sid, 'Comité Financiero', 'dir'),
        _make_node('n_com_ope', sid, 'Comité Operativo', 'dir'),
        _make_node('n_sec_gg', sid, 'Secretaria de Gerencia General', 'dir'),
    ]
    by_id = {n['id']: n for n in nodes}

    # Niveles limpios sin cruces:
    # Fila 0: Asamblea (centro) + Comisario (der)
    by_id['n_asamblea'].update({'x': 300, 'y': 0})
    by_id['n_comisario'].update({'x': 540, 'y': 0})
    # Fila 1: Junta Directiva
    by_id['n_junta'].update({'x': 300, 'y': 100})
    # Fila 2: 4 comités/órganos adscritos a Junta (deja columna x=0 libre para GG)
    for idx, nid in enumerate(('n_soc_med', 'n_com_tec', 'n_com_comp', 'n_coord_med'), start=1):
        by_id[nid].update({'x': idx * 200 + 20, 'y': 200})
    # Fila 3: Gerencia General a la izquierda con canal despejado hacia abajo
    by_id['n_gg'].update({'x': 0, 'y': 320})
    # Fila 4: Comités y Secretaría bajo Gerencia General
    for idx, nid in enumerate(('n_com_fin', 'n_com_ope', 'n_sec_gg'), start=1):
        by_id[nid].update({'x': idx * 200 + 20, 'y': 420})

    edges_spec = [
        ('n_asamblea', 'n_comisario', 'right', 'left'),
        ('n_asamblea', 'n_junta', 'bottom', 'top'),
        ('n_junta', 'n_soc_med', 'bottom', 'top'),
        ('n_junta', 'n_com_tec', 'bottom', 'top'),
        ('n_junta', 'n_com_comp', 'bottom', 'top'),
        ('n_junta', 'n_coord_med', 'bottom', 'top'),
        ('n_junta', 'n_gg', 'left', 'top'),
        ('n_gg', 'n_com_fin', 'right', 'top'),
        ('n_gg', 'n_com_ope', 'right', 'top'),
        ('n_gg', 'n_sec_gg', 'right', 'top'),
    ]
    sec, placed = _wrap_section(
        sid, 'Gobernanza y Dirección General', '', 'GG', '#0284C7', 0, nodes, deptos_map,
    )
    return sec, placed, edges_spec


def _build_section_gob_staff(deptos_map):
    sid = 'sec_gob_staff'
    specs = [
        (
            'n_dpto_ssl',
            [
                _make_node('n_dpto_ssl', sid, 'Dpto. de Servicio de Seguridad y Salud Laboral', 'staff', bold=True),
                _make_node('n_jefe_ssl', sid, 'Jefe de Seguridad y Salud Laboral\n1', 'staff'),
                _make_node('n_enf_ocu', sid, 'Enfermero Ocupacional\n1', 'staff'),
                _make_node('n_med_ocu', sid, 'Médico Ocupacional\n1', 'staff'),
            ],
            {'n_dpto_ssl': ['n_jefe_ssl'], 'n_jefe_ssl': ['n_enf_ocu', 'n_med_ocu']},
        ),
        (
            'n_dpto_ai',
            [
                _make_node('n_dpto_ai', sid, 'Auditoría Interna', 'staff', bold=True),
                _make_node('n_aud_int', sid, 'Auditor Interno\n1', 'staff'),
                _make_node('n_aux_ai', sid, 'Auxiliar de Auditoría Interna\n1 - 2', 'staff'),
                _make_node('n_asist_ai', sid, 'Asistente de Auditoría Interna\n1', 'staff'),
            ],
            {'n_dpto_ai': ['n_aud_int'], 'n_aud_int': ['n_aux_ai', 'n_asist_ai']},
        ),
        (
            'n_dpto_cj',
            [
                _make_node('n_dpto_cj', sid, 'Dpto. de Consultoría Jurídica', 'staff', bold=True),
                _make_node('n_cons_jur', sid, 'Consultor Jurídico\n1', 'staff'),
                _make_node('n_sec_jur', sid, 'Secretaria Jurídica\n1', 'staff'),
                _make_node('n_ases_jur', sid, 'Asesor Jurídico Adjunto\n1', 'staff'),
            ],
            {'n_dpto_cj': ['n_cons_jur'], 'n_cons_jur': ['n_sec_jur', 'n_ases_jur']},
        ),
    ]
    all_nodes = []
    edges_spec = []
    cur_y = 0
    for root_id, track_nodes, tree_map in specs:
        by_id = {n['id']: n for n in track_nodes}
        track_h = _layout_tree_right(root_id, tree_map, by_id, edges_spec, h_gap=40, v_gap=20)
        _offset_nodes(track_nodes, 0, cur_y)
        all_nodes.extend(track_nodes)
        cur_y = _snap(cur_y + track_h + 40)

    sec, placed = _wrap_section(
        sid, 'Unidades Staff y Asesoría', '', 'AI', '#EA580C', 1, all_nodes, deptos_map,
    )
    return sec, placed, edges_spec


def _build_tree_section(
    sec_id,
    title,
    empresa,
    depto_code,
    color,
    order,
    node_defs,
    root_id,
    tree_map,
    deptos_map,
    h_gap=20,
    v_gap=40,
):
    by_id = {n['id']: n for n in node_defs}
    edges_spec = []
    placed = _layout_tree_down(root_id, tree_map, by_id, edges_spec, h_gap=h_gap, v_gap=v_gap)
    sec, wrapped = _wrap_section(sec_id, title, empresa, depto_code, color, order, placed, deptos_map)
    return sec, wrapped, edges_spec


def _build_ptca_sections(deptos_map):
    sections = []
    nodes = []
    edges_spec = []

    # 3. PTCA Dirección y Mantenimiento, Infraestructura y Resguardo (12 nodos)
    s_mir, n_mir, e_mir = _build_tree_section(
        'sec_ptca_mir',
        'Dirección PTCA y Mantenimiento, Infraestructura y Resguardo',
        'PTCA',
        'MIR',
        '#0284C7',
        2,
        [
            _make_node('n_ptca', 'sec_ptca_mir', 'Policlínica Táchira C.A\n(PTCA)', 'ptca', bold=True),
            _make_node('n_dpto_mir', 'sec_ptca_mir', 'Dpto. de Mantenimiento, Infraestructura y Resguardo', 'ptca', bold=True),
            _make_node('n_gte_mir', 'sec_ptca_mir', 'Gerente Mantenimiento de Infraestructura y Resguardo\n1', 'ptca', bold=True),
            _make_node('n_sec_mir', 'sec_ptca_mir', 'Secretaria Mantenimiento de Infraestructura y Equipos\n1', 'ptca'),
            _make_node('n_coord_mant', 'sec_ptca_mir', 'Coordinador de Mantenimiento\n2', 'ptca'),
            _make_node('n_aux_mant', 'sec_ptca_mir', 'Auxiliar de Mantenimiento\n1 - 2', 'ptca'),
            _make_node('n_tec_mant', 'sec_ptca_mir', 'Técnicos de Mantenimiento\n11 - 16', 'ptca'),
            _make_node('n_coord_resg', 'sec_ptca_mir', 'Coordinador de Mantenimiento y Resguardo\n1', 'ptca'),
            _make_node('n_sup_seg', 'sec_ptca_mir', 'Supervisor de Seguridad Interna\n5 - 6', 'ptca'),
            _make_node('n_ofi_seg', 'sec_ptca_mir', 'Oficiales de Seguridad Interna\n9 - 10', 'ptca'),
            _make_node('n_ope_est', 'sec_ptca_mir', 'Operador de Estacionamiento\n2 - 4', 'ptca'),
            _make_node('n_caj_est', 'sec_ptca_mir', 'Cajero de Estacionamiento\n2 - 4', 'ptca'),
        ],
        'n_ptca',
        {
            'n_ptca': ['n_dpto_mir'],
            'n_dpto_mir': ['n_gte_mir'],
            'n_gte_mir': ['n_sec_mir', 'n_coord_mant', 'n_tec_mant', 'n_coord_resg'],
            'n_coord_mant': ['n_aux_mant'],
            'n_coord_resg': ['n_sup_seg'],
            'n_sup_seg': ['n_ofi_seg', 'n_ope_est', 'n_caj_est'],
        },
        deptos_map,
    )
    sections.append(s_mir)
    nodes.extend(n_mir)
    edges_spec.extend(e_mir)

    # 4. Equipos de Apoyo Compartidos PTCA (6 nodos)
    apoyo_nodes = [
        _make_node('n_ptca_eq_af', 'sec_ptca_apoyo', 'Equipo de Administración y Finanzas', 'pth'),
        _make_node('n_ptca_eq_cyt', 'sec_ptca_apoyo', 'Equipo de Contabilidad y Tributos', 'pth'),
        _make_node('n_ptca_eq_th', 'sec_ptca_apoyo', 'Equipo de Talento Humano', 'pth'),
        _make_node('n_ptca_eq_sg', 'sec_ptca_apoyo', 'Equipo de Servicios Generales', 'pth'),
        _make_node('n_ptca_eq_ca', 'sec_ptca_apoyo', 'Equipo de Compras y Almacén', 'pth'),
        _make_node('n_ptca_eq_ti', 'sec_ptca_apoyo', 'Equipo de Tecnología de la Información', 'pth'),
    ]
    _layout_grid(apoyo_nodes, cols=2, h_gap=40, v_gap=40)
    s_apoyo, n_apoyo = _wrap_section(
        'sec_ptca_apoyo',
        'Equipos de Apoyo Compartidos (PTCA)',
        'PTCA',
        'PT',
        '#0EA5E9',
        3,
        apoyo_nodes,
        deptos_map,
    )
    sections.append(s_apoyo)
    nodes.extend(n_apoyo)
    return sections, nodes, edges_spec


def _build_pth_af_section(deptos_map):
    sid = 'sec_pth_af'
    nodes = [
        _make_node('n_dpto_af', sid, 'Dpto. Administración y Finanzas', 'pth', bold=True),
        _make_node('n_gte_af', sid, 'Gerente de Administración y Finanzas\n1', 'pth', bold=True),
        _make_node('n_tesorero', sid, 'Tesorero(a)\n1', 'pth'),
        _make_node('n_ana_bancos', sid, 'Analista Administrativo de Bancos y Pagos\n2', 'pth'),
        _make_node('n_ana_caja', sid, 'Analista Administrativo de Caja\n1', 'pth'),
        _make_node('n_ana_hon', sid, 'Analista Administrativo de Honorarios Médicos\n1', 'pth'),
        _make_node('n_aux_serv_img', sid, 'Auxiliar Administrativo de Servicios (Imagenología)\n2 - 4', 'pth'),
        _make_node('n_aux_serv_adm', sid, 'Auxiliar Administrativo de Servicios (Admisión)\n8 - 10', 'pth'),
        _make_node('n_aux_serv_lab', sid, 'Auxiliar Administrativo de Servicios (Laboratorio)\n1 - 2', 'pth'),
        _make_node('n_aux_serv_esp', sid, 'Auxiliar Administrativo de Servicios (Especialidades)\n2', 'pth'),
    ]
    by_id = {n['id']: n for n in nodes}
    edges_spec = []
    # Todos los analistas y auxiliares de servicio reportan directamente al Gerente AF
    hijos_gte = [
        'n_tesorero', 'n_ana_bancos', 'n_ana_caja', 'n_ana_hon',
        'n_aux_serv_img', 'n_aux_serv_adm', 'n_aux_serv_lab', 'n_aux_serv_esp',
    ]
    _layout_tree_down(
        'n_dpto_af',
        {'n_dpto_af': ['n_gte_af'], 'n_gte_af': hijos_gte},
        by_id,
        edges_spec,
        h_gap=20,
        v_gap=40,
    )

    return _wrap_section(sid, 'Administración y Finanzas', 'PTH', 'AF', '#CA8A04', 5, nodes, deptos_map) + (edges_spec,)


def _build_pth_sections(deptos_map):
    sections = []
    nodes = []
    edges_spec = []

    # 5. Dirección P.T.H y Mantenimiento (2 nodos)
    dir_nodes = [
        _make_node('n_pth', 'sec_pth_dir', 'Policlínica Hospitalización\n(P.T.H)', 'pth', bold=True),
        _make_node('n_pth_eq_mir', 'sec_pth_dir', 'Equipo de Mantenimiento e Infraestructura y Equipos', 'ptca'),
    ]
    _layout_grid(dir_nodes, cols=1, h_gap=20, v_gap=40)
    s_dir, n_dir = _wrap_section(
        'sec_pth_dir', 'Dirección P.T.H y Mantenimiento', 'PTH', 'PTH', '#D97706', 4, dir_nodes, deptos_map,
    )
    sections.append(s_dir)
    nodes.extend(n_dir)
    edges_spec.append(('n_pth', 'n_pth_eq_mir', 'bottom', 'top'))

    # 6. Administración y Finanzas (10 nodos)
    s_af, n_af, e_af = _build_pth_af_section(deptos_map)
    sections.append(s_af)
    nodes.extend(n_af)
    edges_spec.extend(e_af)

    # 7. Contabilidad y Tributos (7 nodos)
    s_cyt, n_cyt, e_cyt = _build_tree_section(
        'sec_pth_cyt',
        'Contabilidad y Tributos',
        'PTH',
        'CYT',
        '#B45309',
        6,
        [
            _make_node('n_dpto_cyt', 'sec_pth_cyt', 'Dpto. Contabilidad y Tributos', 'pth', bold=True),
            _make_node('n_gte_cyt', 'sec_pth_cyt', 'Gerente de Contabilidad y Tributos\n1', 'pth', bold=True),
            _make_node('n_cont_adj', 'sec_pth_cyt', 'Contador Adjunto\n1', 'pth'),
            _make_node('n_asist_cont', 'sec_pth_cyt', 'Asistente Contable\n1', 'pth'),
            _make_node('n_asist_trib', 'sec_pth_cyt', 'Asistente Tributario\n1', 'pth'),
            _make_node('n_ana_costos', 'sec_pth_cyt', 'Analista de Costos\n1', 'pth'),
            _make_node('n_ana_cont', 'sec_pth_cyt', 'Analista Contable\n3', 'pth'),
        ],
        'n_dpto_cyt',
        {
            'n_dpto_cyt': ['n_gte_cyt'],
            'n_gte_cyt': ['n_cont_adj', 'n_asist_cont', 'n_asist_trib', 'n_ana_costos', 'n_ana_cont'],
        },
        deptos_map,
    )
    sections.append(s_cyt)
    nodes.extend(n_cyt)
    edges_spec.extend(e_cyt)

    # 8. Atención al Cliente, Crédito y Cobranza (10 nodos)
    s_acc, n_acc, e_acc = _build_tree_section(
        'sec_pth_acc',
        'Atención al Cliente, Crédito y Cobranza',
        'PTH',
        'ACC',
        '#EA580C',
        7,
        [
            _make_node('n_dpto_acc', 'sec_pth_acc', 'Dpto. de Atención al Cliente, Crédito y Cobranza', 'pth', bold=True),
            _make_node('n_jefe_acc', 'sec_pth_acc', 'Jefe de Atención al Cliente, Crédito y Cobranza\n1', 'pth', bold=True),
            _make_node('n_asist_acc', 'sec_pth_acc', 'Asistente de Atención al Cliente y Cobranza\n1', 'pth'),
            _make_node('n_aux_acc', 'sec_pth_acc', 'Auxiliar de Atención al Cliente y Cobranza\n1', 'pth'),
            _make_node('n_recep', 'sec_pth_acc', 'Recepcionista\n1 - 2', 'pth'),
            _make_node('n_coord_fac', 'sec_pth_acc', 'Coordinador de Facturación\n1', 'pth'),
            _make_node('n_ana_liq_qx', 'sec_pth_acc', 'Analista de Liquidación Área Quirúrgica\n1', 'pth'),
            _make_node('n_ana_liq_hosp', 'sec_pth_acc', 'Analista de Liquidación Área Hospitalización\n1', 'pth'),
            _make_node('n_coord_adm', 'sec_pth_acc', 'Coordinador de Admisión\n1', 'pth'),
            _make_node('n_reg_hist', 'sec_pth_acc', 'Registrador de Historias Médicas\n1', 'pth'),
        ],
        'n_dpto_acc',
        {
            'n_dpto_acc': ['n_jefe_acc'],
            'n_jefe_acc': ['n_asist_acc', 'n_recep', 'n_coord_fac', 'n_coord_adm'],
            'n_asist_acc': ['n_aux_acc'],
            'n_coord_fac': ['n_ana_liq_qx', 'n_ana_liq_hosp'],
            'n_coord_adm': ['n_reg_hist'],
        },
        deptos_map,
    )
    sections.append(s_acc)
    nodes.extend(n_acc)
    edges_spec.extend(e_acc)

    # 9. Talento Humano (5 nodos)
    s_th, n_th, e_th = _build_tree_section(
        'sec_pth_th',
        'Talento Humano',
        'PTH',
        'TH',
        '#0D9488',
        8,
        [
            _make_node('n_dpto_th', 'sec_pth_th', 'Dpto. Talento Humano', 'pth', bold=True),
            _make_node('n_gte_th', 'sec_pth_th', 'Gerente de Talento Humano\n1', 'pth', bold=True),
            _make_node('n_ana_th', 'sec_pth_th', 'Analista de Talento Humano\n1', 'pth'),
            _make_node('n_ana_nom', 'sec_pth_th', 'Analista de Nómina\n1', 'pth'),
            _make_node('n_ana_para', 'sec_pth_th', 'Analista de Contribuciones Parafiscales\n1', 'pth'),
        ],
        'n_dpto_th',
        {'n_dpto_th': ['n_gte_th'], 'n_gte_th': ['n_ana_th', 'n_ana_nom', 'n_ana_para']},
        deptos_map,
    )
    sections.append(s_th)
    nodes.extend(n_th)
    edges_spec.extend(e_th)

    # 10. Servicios Generales (6 nodos)
    s_sg, n_sg, e_sg = _build_tree_section(
        'sec_pth_sg',
        'Servicios Generales',
        'PTH',
        'SG',
        '#65A30D',
        9,
        [
            _make_node('n_dpto_sg', 'sec_pth_sg', 'Dpto. de Servicios Generales', 'pth', bold=True),
            _make_node('n_jefe_sg', 'sec_pth_sg', 'Jefe de Servicios Generales\n1', 'pth', bold=True),
            _make_node('n_coord_sg', 'sec_pth_sg', 'Coordinador de Servicios Generales\n1', 'pth'),
            _make_node('n_camarera', 'sec_pth_sg', 'Camarera\n12 - 13', 'pth'),
            _make_node('n_aux_limp', 'sec_pth_sg', 'Auxiliar de Limpieza\n5 - 24', 'pth'),
            _make_node('n_sec_sg', 'sec_pth_sg', 'Secretaria de Servicios Generales\n1', 'pth'),
        ],
        'n_dpto_sg',
        {
            'n_dpto_sg': ['n_jefe_sg'],
            'n_jefe_sg': ['n_coord_sg', 'n_sec_sg'],
            'n_coord_sg': ['n_camarera', 'n_aux_limp'],
        },
        deptos_map,
    )
    sections.append(s_sg)
    nodes.extend(n_sg)
    edges_spec.extend(e_sg)

    # 11. Compras y Almacén (5 nodos)
    s_ca, n_ca, e_ca = _build_tree_section(
        'sec_pth_ca',
        'Compras y Almacén',
        'PTH',
        'CA',
        '#0891B2',
        10,
        [
            _make_node('n_dpto_ca', 'sec_pth_ca', 'Dpto. Compras y Almacén', 'pth', bold=True),
            _make_node('n_jefe_ca', 'sec_pth_ca', 'Jefe de Compras y Almacén\n1', 'pth', bold=True),
            _make_node('n_coord_alm', 'sec_pth_ca', 'Coordinador de Almacén\n1', 'pth'),
            _make_node('n_despachador', 'sec_pth_ca', 'Despachador\n7 - 24', 'pth'),
            _make_node('n_asist_ca', 'sec_pth_ca', 'Asistente de Compras y Almacén\n1', 'pth'),
        ],
        'n_dpto_ca',
        {
            'n_dpto_ca': ['n_jefe_ca'],
            'n_jefe_ca': ['n_coord_alm', 'n_asist_ca'],
            'n_coord_alm': ['n_despachador'],
        },
        deptos_map,
    )
    sections.append(s_ca)
    nodes.extend(n_ca)
    edges_spec.extend(e_ca)

    # 12. Tecnología de la Información (7 nodos)
    s_ti, n_ti, e_ti = _build_tree_section(
        'sec_pth_ti',
        'Tecnología de la Información',
        'PTH',
        'TI',
        '#2563EB',
        11,
        [
            _make_node('n_dpto_ti', 'sec_pth_ti', 'Dpto. Tecnología de la Información', 'pth', bold=True),
            _make_node('n_gte_ti', 'sec_pth_ti', 'Gerente de Tecnología de la Información\n1', 'pth', bold=True),
            _make_node('n_coord_inf', 'sec_pth_ti', 'Coordinador de Infraestructura\n1', 'pth'),
            _make_node('n_tec_inf', 'sec_pth_ti', 'Técnico de Infraestructura\n3 - 14', 'pth'),
            _make_node('n_coord_sis', 'sec_pth_ti', 'Coordinador de Sistemas y Aplicaciones\n1', 'pth'),
            _make_node('n_ana_prog', 'sec_pth_ti', 'Analista Programador\n1', 'pth'),
            _make_node('n_ana_sis', 'sec_pth_ti', 'Analista de Sistemas\n1', 'pth'),
        ],
        'n_dpto_ti',
        {
            'n_dpto_ti': ['n_gte_ti'],
            'n_gte_ti': ['n_coord_inf', 'n_coord_sis', 'n_ana_sis'],
            'n_coord_inf': ['n_tec_inf'],
            'n_coord_sis': ['n_ana_prog'],
        },
        deptos_map,
    )
    sections.append(s_ti)
    nodes.extend(n_ti)
    edges_spec.extend(e_ti)

    # 13. Servicios Médicos — Enfermería, Residentes y Farmacia Hosp. (13 nodos)
    s_sm_a, n_sm_a, e_sm_a = _build_tree_section(
        'sec_pth_sm_asist',
        'Servicios Médicos — Enfermería, Residentes y Farmacia Hosp.',
        'PTH',
        'SMh',
        '#7C3AED',
        12,
        [
            _make_node('n_dpto_sm', 'sec_pth_sm_asist', 'Dpto. de Servicios Médicos', 'pth', bold=True),
            _make_node('n_coord_res', 'sec_pth_sm_asist', 'Coordinador de Residente Médico\n1', 'pth'),
            _make_node('n_med_res', 'sec_pth_sm_asist', 'Médicos Residentes\n4 - 11', 'pth'),
            _make_node('n_jefe_enf', 'sec_pth_sm_asist', 'Jefe de Enfermería\n1', 'pth', bold=True),
            _make_node('n_coord_enf', 'sec_pth_sm_asist', 'Coordinador de Enfermería\n5 - 6', 'pth'),
            _make_node('n_sup_qx', 'sec_pth_sm_asist', 'Supervisor de la Unidad Quirúrgica\n1', 'pth'),
            _make_node('n_enf', 'sec_pth_sm_asist', 'Enfermero\n42 - 54', 'pth'),
            _make_node('n_camillero', 'sec_pth_sm_asist', 'Camillero\n4 - 6', 'pth'),
            _make_node('n_sup_uci', 'sec_pth_sm_asist', 'Supervisor de la Unidad de UCI\n1', 'pth'),
            _make_node('n_sup_emg', 'sec_pth_sm_asist', 'Supervisor de la Unidad de Emergencia\n1', 'pth'),
            _make_node('n_sup_tr', 'sec_pth_sm_asist', 'Supervisor de la Unidad de Terapia Respiratoria\n1', 'pth'),
            _make_node('n_ter_resp', 'sec_pth_sm_asist', 'Terapeuta Respiratorio\n4 - 11', 'pth'),
            _make_node('n_farm_hosp', 'sec_pth_sm_asist', 'Farmacéutico Regente Hospitalario\n1', 'pth'),
        ],
        'n_dpto_sm',
        {
            'n_dpto_sm': ['n_coord_res', 'n_jefe_enf', 'n_farm_hosp'],
            'n_coord_res': ['n_med_res'],
            'n_jefe_enf': ['n_coord_enf'],
            'n_coord_enf': ['n_sup_qx', 'n_sup_uci', 'n_sup_emg', 'n_sup_tr'],
            'n_sup_qx': ['n_enf', 'n_camillero'],
            'n_sup_tr': ['n_ter_resp'],
        },
        deptos_map,
    )
    sections.append(s_sm_a)
    nodes.extend(n_sm_a)
    edges_spec.extend(e_sm_a)

    # 14. Servicios Médicos — Diagnóstico y Especialidades (13 nodos)
    s_sm_d, n_sm_d, e_sm_d = _build_tree_section(
        'sec_pth_sm_diag',
        'Servicios Médicos — Diagnóstico y Especialidades',
        'PTH',
        'CM',
        '#9333EA',
        13,
        [
            _make_node('n_jefe_serv', 'sec_pth_sm_diag', 'Jefe de Servicios\n1 - 2', 'pth', bold=True),
            _make_node('n_tec_rad', 'sec_pth_sm_diag', 'Técnico Radiólogo\n7 - 19', 'pth'),
            _make_node('n_enf_img', 'sec_pth_sm_diag', 'Enfermero del área de Imagenología\n2 - 10', 'pth'),
            _make_node('n_transc', 'sec_pth_sm_diag', 'Transcriptor\n1 - 2', 'pth'),
            _make_node('n_coord_lab', 'sec_pth_sm_diag', 'Coordinador de Laboratorio\n1', 'pth'),
            _make_node('n_bioan', 'sec_pth_sm_diag', 'Bioanalistas\n8 - 17', 'pth'),
            _make_node('n_asist_lab', 'sec_pth_sm_diag', 'Asistente de Laboratorio\n7 - 18', 'pth'),
            _make_node('n_asist_cris', 'sec_pth_sm_diag', 'Asistente de Laboratorio - Cristalería\n2 - 6', 'pth'),
            _make_node('n_ter_ocu', 'sec_pth_sm_diag', 'Terapeuta Ocupacional\n2', 'pth'),
            _make_node('n_fisio', 'sec_pth_sm_diag', 'Fisioterapeuta\n2', 'pth'),
            _make_node('n_aux_fisio', 'sec_pth_sm_diag', 'Auxiliar de Fisioterapia\n2', 'pth'),
            _make_node('n_odonto', 'sec_pth_sm_diag', 'Odontólogo\n1', 'pth'),
            _make_node('n_hig_den', 'sec_pth_sm_diag', 'Higienista Dental\n1', 'pth'),
        ],
        'n_jefe_serv',
        {
            'n_jefe_serv': [
                'n_tec_rad', 'n_enf_img', 'n_transc',
                'n_coord_lab', 'n_ter_ocu', 'n_fisio', 'n_odonto',
            ],
            'n_coord_lab': ['n_bioan'],
            'n_bioan': ['n_asist_lab', 'n_asist_cris'],
            'n_fisio': ['n_aux_fisio'],
            'n_odonto': ['n_hig_den'],
        },
        deptos_map,
    )
    sections.append(s_sm_d)
    nodes.extend(n_sm_d)
    edges_spec.extend(e_sm_d)

    return sections, nodes, edges_spec


def _build_fp_fs_sections(deptos_map):
    sections = []
    nodes = []
    edges_spec = []

    # 15. Farmacia Policlínica — Operación y Regencia (8 nodos)
    s_fp_op, n_fp_op, e_fp_op = _build_tree_section(
        'sec_fp_op',
        'Farmacia Policlínica — Operación y Regencia',
        'FP',
        'FP',
        '#CA8A04',
        14,
        [
            _make_node('n_fp', 'sec_fp_op', 'Farmacia Policlínica', 'fp', bold=True),
            _make_node('n_fp_adm', 'sec_fp_op', 'Administrador\n1', 'fp', bold=True),
            _make_node('n_fp_sup', 'sec_fp_op', 'Supervisor de Auxiliar de Farmacia\n2 - 6', 'fp'),
            _make_node('n_fp_aux', 'sec_fp_op', 'Auxiliar de Farmacia\n6 - 36', 'fp'),
            _make_node('n_fp_cont', 'sec_fp_op', 'Analista Contable\n1', 'fp'),
            _make_node('n_fp_coord_ca', 'sec_fp_op', 'Coordinador de Compras y Almacén\n1', 'fp'),
            _make_node('n_fp_ana_alm', 'sec_fp_op', 'Analista de Almacén\n1', 'fp'),
            _make_node('n_fp_reg', 'sec_fp_op', 'Farmacéutico Regente\n1', 'fp'),
        ],
        'n_fp',
        {
            'n_fp': ['n_fp_adm'],
            'n_fp_adm': ['n_fp_sup', 'n_fp_reg', 'n_fp_cont', 'n_fp_coord_ca'],
            'n_fp_sup': ['n_fp_aux'],
            'n_fp_coord_ca': ['n_fp_ana_alm'],
        },
        deptos_map,
    )
    sections.append(s_fp_op)
    nodes.extend(n_fp_op)
    edges_spec.extend(e_fp_op)

    # 16. Equipos de Apoyo Compartidos (Farmacia) (4 nodos)
    fp_apoyo = [
        _make_node('n_fp_eq_th', 'sec_fp_apoyo', 'Equipo de Talento Humano', 'pth'),
        _make_node('n_fp_eq_sg', 'sec_fp_apoyo', 'Equipo de Servicios Generales', 'pth'),
        _make_node('n_fp_eq_ti', 'sec_fp_apoyo', 'Equipo de Tecnología de la Información', 'pth'),
        _make_node('n_fp_eq_mir', 'sec_fp_apoyo', 'Equipo de Mantenimiento e Infraestructura y Equipos', 'ptca'),
    ]
    _layout_grid(fp_apoyo, cols=2, h_gap=40, v_gap=40)
    s_fp_ap, n_fp_ap = _wrap_section(
        'sec_fp_apoyo',
        'Equipos de Apoyo Compartidos (Farmacia)',
        'FP',
        'FP',
        '#EAB308',
        15,
        fp_apoyo,
        deptos_map,
    )
    sections.append(s_fp_ap)
    nodes.extend(n_fp_ap)

    # 17. Fuente de Soda — Operación y Nutrición (12 nodos)
    s_fs_op, n_fs_op, e_fs_op = _build_tree_section(
        'sec_fs_op',
        'Fuente de Soda — Operación y Nutrición',
        'FS',
        'FS',
        '#0284C7',
        16,
        [
            _make_node('n_fs', 'sec_fs_op', 'Fuente de Soda P.T C.A', 'fs', bold=True),
            _make_node('n_fs_adm', 'sec_fs_op', 'Administrador\n1', 'fs', bold=True),
            _make_node('n_fs_coord', 'sec_fs_op', 'Coordinador de Fuente de Soda\n2 - 3', 'fs'),
            _make_node('n_fs_self', 'sec_fs_op', 'Auxiliar de Self-Service\n1 - 2', 'fs'),
            _make_node('n_fs_cocina', 'sec_fs_op', 'Auxiliar de Cocina\n5 - 10', 'fs'),
            _make_node('n_fs_mesero', 'sec_fs_op', 'Mesero\n1 - 2', 'fs'),
            _make_node('n_fs_lonch', 'sec_fs_op', 'Lonchero\n2 - 3', 'fs'),
            _make_node('n_fs_aux_cont', 'sec_fs_op', 'Auxiliar Administrativo Contable\n1', 'fs'),
            _make_node('n_fs_cajero', 'sec_fs_op', 'Cajero\n2 - 4', 'fs'),
            _make_node('n_fs_asist', 'sec_fs_op', 'Asistente de Fuente de Soda', 'fs'),
            _make_node('n_fs_nutri', 'sec_fs_op', 'Nutricionista\n1', 'fs'),
            _make_node('n_fs_aux_nut', 'sec_fs_op', 'Auxiliar de Nutrición y Dietética\n3 - 8', 'fs'),
        ],
        'n_fs',
        {
            'n_fs': ['n_fs_adm'],
            'n_fs_adm': ['n_fs_coord', 'n_fs_aux_cont', 'n_fs_cajero', 'n_fs_asist', 'n_fs_nutri'],
            'n_fs_coord': ['n_fs_self', 'n_fs_cocina', 'n_fs_mesero', 'n_fs_lonch'],
            'n_fs_nutri': ['n_fs_aux_nut'],
        },
        deptos_map,
    )
    sections.append(s_fs_op)
    nodes.extend(n_fs_op)
    edges_spec.extend(e_fs_op)

    # 18. Equipos de Apoyo Compartidos (Fuente de Soda) (4 nodos)
    fs_apoyo = [
        _make_node('n_fs_eq_th', 'sec_fs_apoyo', 'Equipo de Talento Humano', 'pth'),
        _make_node('n_fs_eq_sg', 'sec_fs_apoyo', 'Equipo de Servicios Generales', 'pth'),
        _make_node('n_fs_eq_ti', 'sec_fs_apoyo', 'Equipo de Tecnología de la Información', 'pth'),
        _make_node('n_fs_eq_mir', 'sec_fs_apoyo', 'Equipo de Mantenimiento e Infraestructura y Equipos', 'ptca'),
    ]
    _layout_grid(fs_apoyo, cols=2, h_gap=40, v_gap=40)
    s_fs_ap, n_fs_ap = _wrap_section(
        'sec_fs_apoyo',
        'Equipos de Apoyo Compartidos (Fuente de Soda)',
        'FS',
        'FS',
        '#38BDF8',
        17,
        fs_apoyo,
        deptos_map,
    )
    sections.append(s_fs_ap)
    nodes.extend(n_fs_ap)

    return sections, nodes, edges_spec


def _empaquetar_bandas(bandas, sec_nodes_map, gap_x=60, gap_y=80, start_x=40, start_y=40):
    """Acomoda grupos horizontales de secciones sin ningún solape."""
    cur_y = start_y
    for banda in bandas:
        cur_x = start_x
        max_h = 0
        for sec in banda:
            _offset_nodes(sec_nodes_map[sec['id']], cur_x, cur_y)
            sec['x'] = _snap(cur_x)
            sec['y'] = _snap(cur_y)
            max_h = max(max_h, sec['h'])
            cur_x = _snap(cur_x + sec['w'] + gap_x)
        cur_y = _snap(cur_y + max_h + gap_y)


def construir_organigrama_oficial(deptos_por_codigo=None):
    """Construye y valida el esquema JSON v1 del organigrama corporativo oficial."""
    deptos_map = dict(deptos_por_codigo or {})
    all_sections = []
    all_nodes = []
    raw_edges = []

    s_dir, n_dir, e_dir = _build_section_gob_dir(deptos_map)
    s_staff, n_staff, e_staff = _build_section_gob_staff(deptos_map)
    all_sections.extend([s_dir, s_staff])
    all_nodes.extend(n_dir)
    all_nodes.extend(n_staff)
    raw_edges.extend(e_dir)
    raw_edges.extend(e_staff)

    secs_ptca, n_ptca, e_ptca = _build_ptca_sections(deptos_map)
    all_sections.extend(secs_ptca)
    all_nodes.extend(n_ptca)
    raw_edges.extend(e_ptca)

    secs_pth, n_pth, e_pth = _build_pth_sections(deptos_map)
    all_sections.extend(secs_pth)
    all_nodes.extend(n_pth)
    raw_edges.extend(e_pth)

    secs_fil, n_fil, e_fil = _build_fp_fs_sections(deptos_map)
    all_sections.extend(secs_fil)
    all_nodes.extend(n_fil)
    raw_edges.extend(e_fil)

    sec_by_id = {s['id']: s for s in all_sections}
    sec_nodes_map = {s['id']: [] for s in all_sections}
    for n in all_nodes:
        sec_nodes_map[n['section_id']].append(n)

    bandas = [
        [sec_by_id['sec_gob_dir'], sec_by_id['sec_gob_staff']],
        [sec_by_id['sec_ptca_mir'], sec_by_id['sec_ptca_apoyo']],
        [sec_by_id['sec_pth_dir'], sec_by_id['sec_pth_af'], sec_by_id['sec_pth_cyt'], sec_by_id['sec_pth_acc']],
        [sec_by_id['sec_pth_th'], sec_by_id['sec_pth_sg'], sec_by_id['sec_pth_ca'], sec_by_id['sec_pth_ti']],
        [sec_by_id['sec_pth_sm_asist'], sec_by_id['sec_pth_sm_diag']],
        [sec_by_id['sec_fp_op'], sec_by_id['sec_fp_apoyo'], sec_by_id['sec_fs_op'], sec_by_id['sec_fs_apoyo']],
    ]
    _empaquetar_bandas(bandas, sec_nodes_map, gap_x=80, gap_y=100)

    edges = []
    idx = 1
    for src_id, dst_id, fa, ta in raw_edges:
        edges.append(_make_edge(f'e_{idx:03d}', src_id, dst_id, from_anchor=fa, to_anchor=ta))
        idx += 1

    # Línea discontinua oficial de supervisión técnica en Farmacia
    edges.append(_make_edge(
        f'e_{idx:03d}',
        'n_fp_reg',
        'n_fp_aux',
        from_anchor='bottom',
        to_anchor='right',
        style='dashed',
        label='Supervisión técnica',
        color='#B45309',
    ))
    idx += 1

    # Conexiones inter-sección limpias por rama corporativa
    inter_links = [
        ('n_junta', 'n_dpto_ssl', 'right', 'left', '#EA580C'),
        ('n_junta', 'n_dpto_ai', 'right', 'left', '#EA580C'),
        ('n_junta', 'n_dpto_cj', 'right', 'left', '#EA580C'),
        ('n_gg', 'n_ptca', 'bottom', 'top', '#0284C7'),
        ('n_gg', 'n_pth', 'bottom', 'top', '#D97706'),
        ('n_gg', 'n_fp', 'bottom', 'top', '#CA8A04'),
        ('n_gg', 'n_fs', 'bottom', 'top', '#0891B2'),
        ('n_pth', 'n_dpto_af', 'bottom', 'top', '#D97706'),
        ('n_pth', 'n_dpto_cyt', 'bottom', 'top', '#D97706'),
        ('n_pth', 'n_dpto_acc', 'bottom', 'top', '#D97706'),
        ('n_pth', 'n_dpto_th', 'bottom', 'top', '#D97706'),
        ('n_pth', 'n_dpto_sg', 'bottom', 'top', '#D97706'),
        ('n_pth', 'n_dpto_ca', 'bottom', 'top', '#D97706'),
        ('n_pth', 'n_dpto_ti', 'bottom', 'top', '#D97706'),
        ('n_pth', 'n_dpto_sm', 'bottom', 'top', '#D97706'),
        ('n_dpto_sm', 'n_jefe_serv', 'right', 'left', '#D97706'),
        ('n_ptca', 'n_ptca_eq_af', 'right', 'left', '#0284C7'),
        ('n_ptca', 'n_ptca_eq_cyt', 'right', 'left', '#0284C7'),
        ('n_ptca', 'n_ptca_eq_th', 'right', 'left', '#0284C7'),
        ('n_ptca', 'n_ptca_eq_sg', 'right', 'left', '#0284C7'),
        ('n_ptca', 'n_ptca_eq_ca', 'right', 'left', '#0284C7'),
        ('n_ptca', 'n_ptca_eq_ti', 'right', 'left', '#0284C7'),
        ('n_fp', 'n_fp_eq_th', 'right', 'left', '#CA8A04'),
        ('n_fp', 'n_fp_eq_sg', 'right', 'left', '#CA8A04'),
        ('n_fp', 'n_fp_eq_ti', 'right', 'left', '#CA8A04'),
        ('n_fp', 'n_fp_eq_mir', 'right', 'left', '#CA8A04'),
        ('n_fs', 'n_fs_eq_th', 'right', 'left', '#0891B2'),
        ('n_fs', 'n_fs_eq_sg', 'right', 'left', '#0891B2'),
        ('n_fs', 'n_fs_eq_ti', 'right', 'left', '#0891B2'),
        ('n_fs', 'n_fs_eq_mir', 'right', 'left', '#0891B2'),
    ]
    for src_id, dst_id, fa, ta, branch_color in inter_links:
        edges.append(_make_edge(
            f'e_{idx:03d}',
            src_id,
            dst_id,
            from_anchor=fa,
            to_anchor=ta,
            color=branch_color,
            width=1.5,
        ))
        idx += 1

    datos = {
        'schema_version': 1,
        'settings': {'gridSize': 20, 'snapToGrid': True},
        'sections': all_sections,
        'nodes': all_nodes,
        'edges': edges,
    }
    return validar_datos_organigrama(datos)
