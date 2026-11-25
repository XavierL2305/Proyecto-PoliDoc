"""Servicio de exportación filtrada multi-formato del Organigrama (SGC-ORG-001).

Soporta filtrado por todo el mapa, por empresa y/o por secciones específicas,
generando archivos descargables en:
- PDF institucional SGC (ReportLab vectorial con membrete y leyenda §5.1.13.2)
- PNG de alta resolución (rasterizado con Ghostscript desde el PDF SGC; fallback Pillow)
- SVG vectorial puro (lxml)
- ODG editable (LibreOffice Draw vía SVG con perfil aislado)
- VSDX nativo editable para Microsoft Visio (ZIP/OPC puro)
"""
import copy
import io
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from django.conf import settings
from django.utils import timezone
from lxml import etree

from .organigrama import EMPRESAS_VALIDAS, ID_RE, IDS_PROHIBIDOS
from .organigrama_router import enrutar_todas_aristas
from .organigrama_vsdx import _mix_hex_with_white, generar_vsdx

logger = logging.getLogger(__name__)

FORMATOS_EXPORTACION = ('pdf', 'png', 'svg', 'odg', 'vsdx')
PAPELES_PDF = ('auto', 'letter', 'a4', 'a3')

LEYENDA_COPIA_NO_CONTROLADA = (
    'TODA VERSIÓN IMPRESA O FUERA DEL SITIO ESTABLECIDO PARA SU '
    'ALMACENAMIENTO SE CONSIDERA COPIA NO CONTROLADA'
)

MIME_TYPES = {
    'pdf': 'application/pdf',
    'png': 'image/png',
    'svg': 'image/svg+xml; charset=utf-8',
    'odg': 'application/vnd.oasis.opendocument.graphics',
    'vsdx': 'application/vnd.ms-visio.drawing.main+xml',
}

SVG_NS = 'http://www.w3.org/2000/svg'

_LIBERATION_DIR = '/usr/share/fonts/truetype/liberation'
_FONTS_READY = False
_PDF_FONT_MAP = {
    (False, False): 'Helvetica',
    (True, False): 'Helvetica-Bold',
    (False, True): 'Helvetica-Oblique',
    (True, True): 'Helvetica-BoldOblique',
}


class OrganigramaExportError(ValueError):
    """Error controlado durante el filtrado o renderizado de exportación."""


def _ensure_pdf_fonts():
    """Registra Liberation Sans una única vez si los TTF están disponibles."""
    global _FONTS_READY
    if _FONTS_READY:
        return _PDF_FONT_MAP

    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        specs = (
            ((False, False), 'LiberationSans', 'LiberationSans-Regular.ttf'),
            ((True, False), 'LiberationSans-Bold', 'LiberationSans-Bold.ttf'),
            ((False, True), 'LiberationSans-Italic', 'LiberationSans-Italic.ttf'),
            ((True, True), 'LiberationSans-BoldItalic', 'LiberationSans-BoldItalic.ttf'),
        )
        registered = {}
        for key, alias, filename in specs:
            path = os.path.join(_LIBERATION_DIR, filename)
            if not os.path.isfile(path):
                registered = {}
                break
            if alias not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(alias, path))
            registered[key] = alias

        if len(registered) == 4:
            pdfmetrics.registerFontFamily(
                'LiberationSans',
                normal='LiberationSans',
                bold='LiberationSans-Bold',
                italic='LiberationSans-Italic',
                boldItalic='LiberationSans-BoldItalic',
            )
            _PDF_FONT_MAP.update(registered)
    except Exception as exc:  # noqa: BLE001
        logger.debug('Fallback a Helvetica en exportación PDF: %s', exc)
    finally:
        _FONTS_READY = True

    return _PDF_FONT_MAP


def resolver_color_export(color, rol):
    """Resuelve colores semánticos ('auto', 'transparent') para fondo blanco impreso."""
    if color == 'transparent':
        return None
    if not color or color == 'auto':
        return {
            'bg': '#FFFFFF',
            'text': '#0F172A',
            'border': '#CBD5E1',
            'edge': '#64748B',
        }.get(rol, '#0F172A')
    return color


def _anchor_point(node, anchor):
    x = float(node['x'])
    y = float(node['y'])
    w = float(node['w'])
    h = float(node['h'])
    if anchor == 'top':
        return (x + w / 2.0, y)
    if anchor == 'right':
        return (x + w, y + h / 2.0)
    if anchor == 'left':
        return (x, y + h / 2.0)
    return (x + w / 2.0, y + h)


def _anchor_dir(anchor):
    if anchor == 'top':
        return (0.0, -1.0)
    if anchor == 'right':
        return (1.0, 0.0)
    if anchor == 'left':
        return (-1.0, 0.0)
    return (0.0, 1.0)


def calcular_geometria_arista(src_node, dst_node, edge):
    """Replica exactamente el enrutamiento de `renderer.js` (`orthogonal` / `straight`)."""
    p1 = _anchor_point(src_node, edge.get('fromAnchor', 'bottom'))
    p2 = _anchor_point(dst_node, edge.get('toAnchor', 'top'))

    if edge.get('routing', 'orthogonal') == 'straight':
        return {
            'points': [p1, p2],
            'label_pos': ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0),
        }

    d1 = _anchor_dir(edge.get('fromAnchor', 'bottom'))
    d2 = _anchor_dir(edge.get('toAnchor', 'top'))
    stub = 22.0
    s1 = (p1[0] + d1[0] * stub, p1[1] + d1[1] * stub)
    s2 = (p2[0] + d2[0] * stub, p2[1] + d2[1] * stub)

    pts = [p1, s1]
    v1 = d1[1] != 0.0
    v2 = d2[1] != 0.0

    if v1 and v2:
        mid_y = (s1[1] + s2[1]) / 2.0
        if (d1[1] > 0 and p2[1] < s1[1]) or (d1[1] < 0 and p2[1] > s1[1]):
            side_x = s1[0] + 80.0 if abs(s1[0] - s2[0]) < 40.0 else (s1[0] + s2[0]) / 2.0
            pts.extend([(side_x, s1[1]), (side_x, s2[1])])
        else:
            pts.extend([(s1[0], mid_y), (s2[0], mid_y)])
    elif not v1 and not v2:
        mid_x = (s1[0] + s2[0]) / 2.0
        if (d1[0] > 0 and p2[0] < s1[0]) or (d1[0] < 0 and p2[0] > s1[0]):
            side_y = s1[1] + 80.0 if abs(s1[1] - s2[1]) < 40.0 else (s1[1] + s2[1]) / 2.0
            pts.extend([(s1[0], side_y), (s2[0], side_y)])
        else:
            pts.extend([(mid_x, s1[1]), (mid_x, s2[1])])
    elif v1 and not v2:
        pts.append((s1[0], s2[1]))
    else:
        pts.append((s2[0], s1[1]))

    pts.extend([s2, p2])

    mid_idx = len(pts) // 2
    a = pts[mid_idx - 1]
    b = pts[mid_idx]
    return {
        'points': pts,
        'label_pos': ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0),
    }


def _validar_lista_ids(valores, campo):
    if valores is None:
        return []
    if not isinstance(valores, (list, tuple, set)):
        raise OrganigramaExportError(f'{campo} debe ser una lista.')
    limpios = []
    for item in valores:
        if not isinstance(item, str) or not ID_RE.match(item) or item in IDS_PROHIBIDOS:
            raise OrganigramaExportError(f'Identificador inválido en {campo}: {item!r}.')
        if item not in limpios:
            limpios.append(item)
    return limpios


def _validar_lista_empresas(valores):
    if valores is None:
        return []
    if not isinstance(valores, (list, tuple, set)):
        raise OrganigramaExportError('empresas debe ser una lista.')
    limpias = []
    for item in valores:
        if not isinstance(item, str):
            raise OrganigramaExportError('Cada empresa debe ser una cadena.')
        norm = item.strip().upper()
        if norm == 'PT':
            norm = 'PTCA'
        if norm not in EMPRESAS_VALIDAS:
            raise OrganigramaExportError(f'Empresa inválida en selección: {item!r}.')
        if norm not in limpias:
            limpias.append(norm)
    return limpias


def filtrar_organigrama(
    datos,
    todo=True,
    section_ids=None,
    empresas=None,
    incluir_sin_seccion=None,
    mostrar_marcos=True,
    etiqueta_doc='Organigrama',
):
    """Filtra secciones, nodos y aristas y normaliza coordenadas desde `(pad, pad)`."""
    if not isinstance(datos, dict):
        raise OrganigramaExportError('Los datos del organigrama son inválidos.')
    if not isinstance(todo, bool):
        raise OrganigramaExportError('todo debe ser booleano.')
    if not isinstance(mostrar_marcos, bool):
        raise OrganigramaExportError('incluir_marcos debe ser booleano.')

    sec_ids_req = _validar_lista_ids(section_ids, 'section_ids')
    emp_req = _validar_lista_empresas(empresas)

    if incluir_sin_seccion is None:
        inc_sueltos = bool(todo)
    else:
        if not isinstance(incluir_sin_seccion, bool):
            raise OrganigramaExportError('incluir_sin_seccion debe ser booleano.')
        inc_sueltos = incluir_sin_seccion

    all_sections = copy.deepcopy(datos.get('sections') or [])
    all_nodes = copy.deepcopy(datos.get('nodes') or [])
    all_edges = copy.deepcopy(datos.get('edges') or [])

    if todo:
        sel_sections = all_sections
        sel_nodes = all_nodes
        resumen = f'{etiqueta_doc or "Organigrama"} completo'
    else:
        sec_set = set(sec_ids_req)
        emp_set = set(emp_req)
        sel_sections = [
            s for s in all_sections
            if s['id'] in sec_set or (s.get('empresa', '') in emp_set)
        ]
        selected_sec_ids = {s['id'] for s in sel_sections}
        sel_nodes = [
            n for n in all_nodes
            if (n.get('section_id') in selected_sec_ids)
            or (inc_sueltos and not n.get('section_id'))
        ]

        partes_resumen = []
        if emp_req:
            partes_resumen.append('Empresas: ' + ', '.join(e or 'General' for e in emp_req))
        if sec_ids_req:
            titulos = [s.get('title') or s['id'] for s in sel_sections if s['id'] in sec_set]
            if titulos:
                partes_resumen.append('Secciones: ' + ', '.join(titulos[:4]) + ('...' if len(titulos) > 4 else ''))
        if inc_sueltos:
            partes_resumen.append('Nodos generales')
        resumen = ' · '.join(partes_resumen) if partes_resumen else 'Selección personalizada'

    if not sel_nodes and not (mostrar_marcos and sel_sections):
        raise OrganigramaExportError(
            'La selección no contiene figuras ni secciones para exportar.'
        )

    sel_node_ids = {n['id'] for n in sel_nodes}
    sel_edges = [
        e for e in all_edges
        if e.get('from') in sel_node_ids and e.get('to') in sel_node_ids
    ]

    node_map = {n['id']: n for n in sel_nodes}
    routes = enrutar_todas_aristas(
        {'sections': sel_sections, 'nodes': sel_nodes, 'edges': sel_edges},
        mostrar_marcos=mostrar_marcos,
    )

    # Calcular bounding box unificado (marcos + nodos + rutas A* de aristas)
    min_x = float('inf')
    min_y = float('inf')
    max_x = float('-inf')
    max_y = float('-inf')

    if mostrar_marcos:
        for s in sel_sections:
            sx, sy, sw, sh = float(s['x']), float(s['y']), float(s['w']), float(s['h'])
            min_x = min(min_x, sx)
            min_y = min(min_y, sy - 20.0)
            max_x = max(max_x, sx + sw)
            max_y = max(max_y, sy + sh)

    for n in sel_nodes:
        nx, ny, nw, nh = float(n['x']), float(n['y']), float(n['w']), float(n['h'])
        min_x = min(min_x, nx)
        min_y = min(min_y, ny)
        max_x = max(max_x, nx + nw)
        max_y = max(max_y, ny + nh)

    for e in sel_edges:
        geom = routes.get(e.get('id')) or calcular_geometria_arista(
            node_map[e['from']], node_map[e['to']], e,
        )
        for px, py in geom['points']:
            min_x = min(min_x, px - 12.0)
            min_y = min(min_y, py - 12.0)
            max_x = max(max_x, px + 12.0)
            max_y = max(max_y, py + 12.0)
        if e.get('label'):
            lx, ly = geom['label_pos']
            min_x = min(min_x, lx - 60.0)
            max_x = max(max_x, lx + 60.0)
            min_y = min(min_y, ly - 16.0)
            max_y = max(max_y, ly + 16.0)

    if not math.isfinite(min_x):
        raise OrganigramaExportError('No se pudo calcular el área de exportación.')

    pad = 40.0
    dx = pad - min_x
    dy = pad - min_y

    for s in sel_sections:
        s['x'] = round(float(s['x']) + dx, 2)
        s['y'] = round(float(s['y']) + dy, 2)
    for n in sel_nodes:
        n['x'] = round(float(n['x']) + dx, 2)
        n['y'] = round(float(n['y']) + dy, 2)
    for geom in routes.values():
        geom['points'] = [
            (round(float(px) + dx, 2), round(float(py) + dy, 2))
            for px, py in (geom.get('points') or ())
        ]
        lp = geom.get('label_pos')
        if lp:
            geom['label_pos'] = (round(float(lp[0]) + dx, 2), round(float(lp[1]) + dy, 2))
        cr = geom.get('crossings')
        if cr:
            geom['crossings'] = {
                idx: [round(float(vx) + dx, 2) for vx in vxs]
                for idx, vxs in cr.items()
            }

    width = max(320.0, round((max_x - min_x) + pad * 2.0, 2))
    height = max(200.0, round((max_y - min_y) + pad * 2.0, 2))

    return {
        'sections': sel_sections,
        'nodes': sel_nodes,
        'edges': sel_edges,
        'routes': routes,
        'width': width,
        'height': height,
        'resumen_alcance': resumen,
    }


def _wrap_text(text, max_width_px, font_size_px, width_fn=None):
    """Divide texto en líneas respetando saltos manuales y ancho máximo."""
    raw_lines = str(text or '').split('\n')
    if not raw_lines:
        return ['']
    if width_fn is None:
        avg_char = max(4.2, font_size_px * 0.56)
        width_fn = lambda s: len(s) * avg_char

    out = []
    usable = max(24.0, float(max_width_px))
    for paragraph in raw_lines:
        if not paragraph.strip():
            out.append('')
            continue
        words = paragraph.split()
        cur = ''
        for word in words:
            cand = f'{cur} {word}' if cur else word
            if width_fn(cand) <= usable:
                cur = cand
            elif not cur:
                # Cortar palabra muy larga por caracteres
                chunk = ''
                for ch in word:
                    if chunk and width_fn(chunk + ch) > usable:
                        out.append(chunk)
                        chunk = ch
                    else:
                        chunk += ch
                cur = chunk
            else:
                out.append(cur)
                cur = word
        if cur:
            out.append(cur)
    return out or ['']


def _resolver_rutas_export(datos_filtrados, mostrar_marcos=True):
    """Devuelve rutas precalculadas por `filtrar_organigrama` o enruta bajo demanda."""
    rutas = datos_filtrados.get('routes')
    if isinstance(rutas, dict):
        return rutas
    return enrutar_todas_aristas(datos_filtrados, mostrar_marcos=mostrar_marcos)


def _iterar_primitivas_arista(pts, crossings=None, corner_r=8.0, jump_r=6.0):
    """Genera primitivas geométricas compartidas (M, L, Q, JUMP) para SVG, PDF y PNG."""
    if len(pts) < 2:
        return
    cr_map = crossings or {}
    last_x, last_y = float(pts[0][0]), float(pts[0][1])
    yield ('M', (last_x, last_y))
    seg_start = (last_x, last_y)

    def _emit_seg(seg_idx, p0, p1):
        sx, sy = p0
        ex, ey = p1
        if math.hypot(ex - sx, ey - sy) < 1e-6:
            return
        raw_xs = cr_map.get(seg_idx)
        if raw_xs and abs(sy - ey) < 0.5 and abs(ex - sx) > jump_r * 2.2:
            going_right = ex > sx
            lo_x = min(sx, ex) + jump_r + 1.0
            hi_x = max(sx, ex) - jump_r - 1.0
            vxs = sorted(
                [float(vx) for vx in raw_xs if lo_x < float(vx) < hi_x],
                reverse=not going_right,
            )
            cur_x = sx
            for vx in vxs:
                if going_right:
                    a_start, a_end = vx - jump_r, vx + jump_r
                    if a_start <= cur_x + 0.5:
                        continue
                else:
                    a_start, a_end = vx + jump_r, vx - jump_r
                    if a_start >= cur_x - 0.5:
                        continue
                yield ('L', (a_start, sy))
                yield ('JUMP', (a_start, sy), (a_end, sy), vx, jump_r, going_right)
                cur_x = a_end
        yield ('L', (ex, ey))

    n_pts = len(pts)
    for i in range(n_pts - 1):
        bx, by = float(pts[i + 1][0]), float(pts[i + 1][1])
        if i + 1 < n_pts - 1:
            cx_next, cy_next = float(pts[i + 2][0]), float(pts[i + 2][1])
            dx1, dy1 = bx - seg_start[0], by - seg_start[1]
            dx2, dy2 = cx_next - bx, cy_next - by
            len1 = math.hypot(dx1, dy1)
            len2 = math.hypot(dx2, dy2)
            if len1 >= 1e-6 and len2 >= 1e-6:
                ux1, uy1 = dx1 / len1, dy1 / len1
                ux2, uy2 = dx2 / len2, dy2 / len2
                if abs(ux1 * uy2 - uy1 * ux2) >= 1e-3:
                    r = min(float(corner_r), len1 * 0.48, len2 * 0.45)
                    if r >= 1.5:
                        p_before = (bx - ux1 * r, by - uy1 * r)
                        p_after = (bx + ux2 * r, by + uy2 * r)
                        yield from _emit_seg(i, seg_start, p_before)
                        yield ('Q', (bx, by), p_after)
                        seg_start = p_after
                        continue
        yield from _emit_seg(i, seg_start, (bx, by))
        seg_start = (bx, by)


def _svg_rounded_edge_path(pts, crossings=None, corner_r=8.0, jump_r=6.0):
    """Construye string SVG `d` con esquinas redondeadas y arcos de salto."""
    parts = []
    for tok in _iterar_primitivas_arista(pts, crossings, corner_r=corner_r, jump_r=jump_r):
        kind = tok[0]
        if kind == 'M':
            parts.append(f'M {tok[1][0]:.2f} {tok[1][1]:.2f}')
        elif kind == 'L':
            parts.append(f'L {tok[1][0]:.2f} {tok[1][1]:.2f}')
        elif kind == 'Q':
            ctrl, end = tok[1], tok[2]
            parts.append(f'Q {ctrl[0]:.2f} {ctrl[1]:.2f} {end[0]:.2f} {end[1]:.2f}')
        elif kind == 'JUMP':
            _s, end, _vx, jr, going_right = tok[1:]
            sweep = 1 if going_right else 0
            parts.append(f'A {jr:.2f} {jr:.2f} 0 0 {sweep} {end[0]:.2f} {end[1]:.2f}')
    return ' '.join(parts)


def _primitivas_a_polyline(pts, crossings=None, scale=1.0, oy=0.0, corner_r=8.0, jump_r=6.0):
    """Muestrea primitivas geométricas a una polilínea escalada para Pillow."""
    out = []
    cur = (float(pts[0][0]), float(pts[0][1])) if pts else (0.0, 0.0)
    steps = 6
    for tok in _iterar_primitivas_arista(pts, crossings, corner_r=corner_r, jump_r=jump_r):
        kind = tok[0]
        if kind in ('M', 'L'):
            cur = tok[1]
            out.append((cur[0] * scale, cur[1] * scale + oy))
        elif kind == 'Q':
            ctrl, end = tok[1], tok[2]
            for s in range(1, steps + 1):
                t = s / steps
                mt = 1.0 - t
                qx = mt * mt * cur[0] + 2.0 * mt * t * ctrl[0] + t * t * end[0]
                qy = mt * mt * cur[1] + 2.0 * mt * t * ctrl[1] + t * t * end[1]
                out.append((qx * scale, qy * scale + oy))
            cur = end
        elif kind == 'JUMP':
            s_w, end_w, vx, jr, going_right = tok[1:]
            cy = s_w[1]
            a0 = math.pi if going_right else 0.0
            a1 = 2.0 * math.pi if going_right else -math.pi
            for s in range(1, steps + 1):
                t = s / steps
                ang = a0 + (a1 - a0) * t
                jx = vx + math.cos(ang) * jr
                jy = cy - abs(math.sin(ang)) * jr
                out.append((jx * scale, jy * scale + oy))
            cur = end_w
    return out


def _sanitize_svg_id(raw):
    return 'm_' + re.sub(r'[^a-zA-Z0-9_-]', '_', str(raw))


def _svg_el(tag, attrs=None, parent=None):
    qname = f'{{{SVG_NS}}}{tag}'
    if parent is None:
        el = etree.Element(qname, nsmap={None: SVG_NS})
    else:
        el = etree.SubElement(parent, qname)
    if attrs:
        for k, v in attrs.items():
            el.set(k, str(v))
    return el


def generar_svg(datos_filtrados, meta, mostrar_marcos=True, es_obsoleto=False):
    """Genera un SVG 1.1 limpio y seguro (lxml) del organigrama filtrado."""
    width = float(datos_filtrados['width'])
    height = float(datos_filtrados['height'])
    root = _svg_el('svg', {
        'width': f'{width:.0f}',
        'height': f'{height:.0f}',
        'viewBox': f'0 0 {width:.2f} {height:.2f}',
        'version': '1.1',
    })

    defs = _svg_el('defs', parent=root)
    marker_ids = {}

    def ensure_marker(color):
        if color in marker_ids:
            return marker_ids[color]
        mid = _sanitize_svg_id(color)
        marker = _svg_el('marker', {
            'id': mid,
            'viewBox': '0 0 10 10',
            'refX': '9',
            'refY': '5',
            'markerWidth': '7',
            'markerHeight': '7',
            'orient': 'auto-start-reverse',
            'markerUnits': 'strokeWidth',
        }, parent=defs)
        _svg_el('path', {'d': 'M 0 1 L 10 5 L 0 9 z', 'fill': color}, parent=marker)
        marker_ids[color] = mid
        return mid

    # Fondo blanco uniforme
    _svg_el('rect', {
        'x': '0', 'y': '0', 'width': f'{width:.2f}', 'height': f'{height:.2f}',
        'fill': '#FFFFFF',
    }, parent=root)

    # 1. Secciones
    if mostrar_marcos:
        secs = sorted(datos_filtrados.get('sections', []), key=lambda s: s.get('order', 0))
        sec_group = _svg_el('g', {'id': 'org-sections'}, parent=root)
        for s in secs:
            color = s.get('color') or '#2563EB'
            tint = _mix_hex_with_white(color, 0.08)
            sx, sy, sw, sh = float(s['x']), float(s['y']), float(s['w']), float(s['h'])
            _svg_el('rect', {
                'x': f'{sx:.2f}', 'y': f'{sy:.2f}', 'width': f'{sw:.2f}', 'height': f'{sh:.2f}',
                'rx': '12', 'ry': '12',
                'fill': tint, 'stroke': color, 'stroke-width': '2', 'stroke-dasharray': '7 5',
            }, parent=sec_group)

            title = ((f"[{s['empresa']}] " if s.get('empresa') else '') + (s.get('title') or 'Sección'))
            bw = min(max(96.0, len(title) * 6.8 + 20.0), max(96.0, sw - 20.0))
            _svg_el('rect', {
                'x': f'{sx + 12:.2f}', 'y': f'{sy - 13:.2f}',
                'width': f'{bw:.2f}', 'height': '24', 'rx': '6', 'ry': '6',
                'fill': color,
            }, parent=sec_group)
            lbl = _svg_el('text', {
                'x': f'{sx + 22:.2f}', 'y': f'{sy + 3:.2f}',
                'fill': '#FFFFFF',
                'font-family': 'Liberation Sans, Arial, sans-serif',
                'font-size': '11.5',
                'font-weight': 'bold',
            }, parent=sec_group)
            lbl.text = title

    # 2. Aristas
    node_map = {n['id']: n for n in datos_filtrados.get('nodes', [])}
    routes = _resolver_rutas_export(datos_filtrados, mostrar_marcos=mostrar_marcos)
    edge_group = _svg_el('g', {'id': 'org-edges'}, parent=root)
    for edge in datos_filtrados.get('edges', []):
        src = node_map.get(edge.get('from'))
        dst = node_map.get(edge.get('to'))
        if not src or not dst:
            continue
        geom = routes.get(edge.get('id')) or calcular_geometria_arista(src, dst, edge)
        pts = geom['points']
        if len(pts) < 2:
            continue
        d_str = _svg_rounded_edge_path(pts, geom.get('crossings'), corner_r=8.0, jump_r=6.0)
        stroke = resolver_color_export(edge.get('color', 'auto'), 'edge')
        sw_edge = float(edge.get('width', 2))
        _svg_el('path', {
            'd': d_str,
            'fill': 'none',
            'stroke': '#FFFFFF',
            'stroke-width': f'{sw_edge + 4.0:.2f}',
            'stroke-linecap': 'round',
            'stroke-linejoin': 'round',
            'opacity': '0.92',
        }, parent=edge_group)
        attrs = {
            'd': d_str,
            'fill': 'none',
            'stroke': stroke,
            'stroke-width': f'{sw_edge:.2f}',
            'stroke-linecap': 'round',
            'stroke-linejoin': 'round',
        }
        style = edge.get('style', 'solid')
        if style == 'dashed':
            attrs['stroke-dasharray'] = '8 5'
        elif style == 'dotted':
            attrs['stroke-dasharray'] = '2 4'
        if edge.get('arrowEnd', True):
            mid = ensure_marker(stroke)
            attrs['marker-end'] = f'url(#{mid})'
        _svg_el('path', attrs, parent=edge_group)

        label = (edge.get('label') or '').strip()
        if label:
            lx, ly = geom['label_pos']
            lw = max(30.0, len(label) * 6.8 + 14.0)
            _svg_el('rect', {
                'x': f'{lx - lw / 2.0:.2f}', 'y': f'{ly - 10.0:.2f}',
                'width': f'{lw:.2f}', 'height': '20', 'rx': '4',
                'fill': '#FFFFFF', 'stroke': '#CBD5E1', 'stroke-width': '1',
            }, parent=edge_group)
            t = _svg_el('text', {
                'x': f'{lx:.2f}', 'y': f'{ly + 4:.2f}',
                'fill': '#0F172A',
                'font-family': 'Liberation Sans, Arial, sans-serif',
                'font-size': '10.5',
                'font-weight': 'bold',
                'text-anchor': 'middle',
            }, parent=edge_group)
            t.text = label

    # 3. Nodos
    nodes_sorted = sorted(datos_filtrados.get('nodes', []), key=lambda n: n.get('zIndex', 1))
    node_group = _svg_el('g', {'id': 'org-nodes'}, parent=root)
    for node in nodes_sorted:
        st = node.get('style') or {}
        nx, ny, nw, nh = float(node['x']), float(node['y']), float(node['w']), float(node['h'])
        shape = node.get('shape', 'rounded')
        bg = resolver_color_export(st.get('bgColor', 'auto'), 'bg')
        fg = resolver_color_export(st.get('textColor', 'auto'), 'text')
        bc = resolver_color_export(st.get('borderColor', 'auto'), 'border')
        bw = float(st.get('borderWidth', 2))
        bstyle = st.get('borderStyle', 'solid')

        shape_attrs = {
            'fill': bg if bg is not None else 'none',
            'stroke': bc if (bw > 0 and bc is not None) else 'none',
            'stroke-width': f'{bw:.2f}',
        }
        if bstyle == 'dashed':
            shape_attrs['stroke-dasharray'] = '8 5'
        elif bstyle == 'dotted':
            shape_attrs['stroke-dasharray'] = '2 4'

        if shape == 'diamond':
            pad = max(1.0, bw / 2.0) if bw > 0 else 0.0
            pts_poly = (
                f'{nx + nw / 2.0:.2f},{ny + pad:.2f} '
                f'{nx + nw - pad:.2f},{ny + nh / 2.0:.2f} '
                f'{nx + nw / 2.0:.2f},{ny + nh - pad:.2f} '
                f'{nx + pad:.2f},{ny + nh / 2.0:.2f}'
            )
            shape_attrs['points'] = pts_poly
            shape_attrs['stroke-linejoin'] = 'round'
            _svg_el('polygon', shape_attrs, parent=node_group)
        else:
            rx = 0.0
            if shape == 'pill':
                rx = min(nw, nh) / 2.0
            elif shape == 'rounded':
                rx = min(float(st.get('borderRadius', 10)), min(nw, nh) / 2.0)
            elif shape == 'rect':
                rx = 2.0
            shape_attrs.update({
                'x': f'{nx:.2f}', 'y': f'{ny:.2f}',
                'width': f'{nw:.2f}', 'height': f'{nh:.2f}',
                'rx': f'{rx:.2f}', 'ry': f'{rx:.2f}',
            })
            _svg_el('rect', shape_attrs, parent=node_group)

        text_val = node.get('text') or ''
        if text_val.strip():
            fsize = float(st.get('fontSize', 13))
            align = st.get('textAlign', 'center')
            pad_x = nw * 0.20 if shape == 'diamond' else 10.0
            lines = _wrap_text(text_val, nw - pad_x * 2.0, fsize)
            line_h = fsize * 1.25
            total_h = len(lines) * line_h

            if align == 'left':
                tx = nx + pad_x
                anchor = 'start'
            elif align == 'right':
                tx = nx + nw - pad_x
                anchor = 'end'
            else:
                tx = nx + nw / 2.0
                anchor = 'middle'

            first_y = ny + (nh - total_h) / 2.0 + fsize * 0.88
            t_el = _svg_el('text', {
                'x': f'{tx:.2f}',
                'y': f'{first_y:.2f}',
                'fill': fg,
                'font-family': 'Liberation Sans, Arial, sans-serif',
                'font-size': f'{fsize:.1f}',
                'font-weight': 'bold' if st.get('fontWeight') == 'bold' else 'normal',
                'font-style': 'italic' if st.get('fontStyle') == 'italic' else 'normal',
                'text-anchor': anchor,
            }, parent=node_group)
            for idx, line in enumerate(lines):
                span = _svg_el('tspan', {
                    'x': f'{tx:.2f}',
                    'dy': '0' if idx == 0 else f'{line_h:.2f}',
                }, parent=t_el)
                span.text = line if line else ' '

    # 4. Marca de agua OBSOLETO si corresponde
    if es_obsoleto:
        wm_size = max(48.0, min(width, height) * 0.14)
        wm = _svg_el('text', {
            'x': f'{width / 2.0:.2f}',
            'y': f'{height / 2.0:.2f}',
            'fill': '#DC2626',
            'fill-opacity': '0.16',
            'font-family': 'Liberation Sans, Arial, sans-serif',
            'font-size': f'{wm_size:.1f}',
            'font-weight': 'bold',
            'text-anchor': 'middle',
            'transform': f'rotate(-28 {width / 2.0:.2f} {height / 2.0:.2f})',
        }, parent=root)
        wm.text = 'OBSOLETO'

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _resolve_pdf_pagesize(papel, width_px, height_px):
    from reportlab.lib.pagesizes import A3, A4, landscape, letter

    papel_norm = (papel or 'auto').strip().lower()
    if papel_norm not in PAPELES_PDF:
        raise OrganigramaExportError(f'Tamaño de papel no soportado: {papel!r}.')

    if papel_norm == 'letter':
        return landscape(letter)
    if papel_norm == 'a4':
        return landscape(A4)
    if papel_norm == 'a3':
        return landscape(A3)

    if width_px <= 1100 and height_px <= 700:
        return landscape(letter)
    if width_px <= 2200 and height_px <= 1450:
        return landscape(A3)
    return (1683.78, 1190.55)  # A2 landscape


def _hex_to_rl_color(hex_str):
    from reportlab.lib import colors
    return colors.HexColor(hex_str)


def _draw_pdf_header(c, pw, ph, margin, header_h, meta, scope_summary, font_map, es_obsoleto):
    from reportlab.lib import colors

    top_y = ph - margin
    box_y = top_y - header_h
    box_w = pw - margin * 2.0

    c.saveState()
    c.setFillColor(colors.HexColor('#F8FAFC'))
    c.setStrokeColor(colors.HexColor('#CBD5E1'))
    c.setLineWidth(1.0)
    c.roundRect(margin, box_y, box_w, header_h, 4, stroke=1, fill=1)

    # Logo institucional SVG
    logo_x = margin + 10
    text_left = logo_x
    logo_path = os.path.join(
        str(settings.BASE_DIR), 'polidoc', 'static', 'imagenes', 'logopoliclinicatachira.svg',
    )
    if os.path.isfile(logo_path):
        try:
            from reportlab.graphics import renderPDF
            from svglib.svglib import svg2rlg

            drawing = svg2rlg(logo_path)
            if drawing and drawing.height > 0 and drawing.width > 0:
                sc = min(38.0 / drawing.height, 120.0 / drawing.width)
                drawing.scale(sc, sc)
                drawn_w = drawing.width * sc
                drawn_h = drawing.height * sc
                renderPDF.draw(drawing, c, logo_x, box_y + (header_h - drawn_h) / 2.0)
                text_left = logo_x + drawn_w + 12.0
        except Exception as exc:  # noqa: BLE001
            logger.debug('No se pudo dibujar logo SVG en PDF de organigrama: %s', exc)

    # Bloque derecho de metadatos SGC
    meta_w = 185.0
    meta_x = pw - margin - meta_w
    c.setStrokeColor(colors.HexColor('#E2E8F0'))
    c.line(meta_x, box_y + 6, meta_x, top_y - 6)

    c.setFont(font_map[(True, False)], 9.5)
    c.setFillColor(colors.HexColor('#1E40AF'))
    c.drawString(text_left, top_y - 16, 'POLICLÍNICA TÁCHIRA C.A. — SISTEMA DE GESTIÓN DE LA CALIDAD')

    titulo_doc = (meta.get('nombre') or 'Organigrama Corporativo')
    c.setFont(font_map[(True, False)], 11.0)
    c.setFillColor(colors.HexColor('#0F172A'))
    c.drawString(text_left, top_y - 31, titulo_doc[:85])

    c.setFont(font_map[(False, False)], 8.0)
    c.setFillColor(colors.HexColor('#475569'))
    c.drawString(text_left, top_y - 45, f'Alcance: {scope_summary}'[:105])

    # Metadatos derecha
    codigo = meta.get('codigo') or 'SGC-ORG-001'
    version = meta.get('version') or '1'
    vigencia = meta.get('fecha_vigencia') or '—'
    estado_lbl = 'OBSOLETO' if es_obsoleto else 'VIGENTE'
    estado_clr = '#DC2626' if es_obsoleto else '#059669'

    c.setFont(font_map[(True, False)], 8.5)
    c.setFillColor(colors.HexColor('#0F172A'))
    c.drawString(meta_x + 10, top_y - 16, f'Código: {codigo}')
    c.drawString(meta_x + 10, top_y - 29, f'Versión: v{version}')
    c.setFont(font_map[(False, False)], 8.0)
    c.setFillColor(colors.HexColor('#475569'))
    c.drawString(meta_x + 10, top_y - 41, f'Vigencia: {vigencia}')
    c.setFont(font_map[(True, False)], 8.0)
    c.setFillColor(colors.HexColor(estado_clr))
    c.drawString(meta_x + 10, top_y - 51, f'Estado: {estado_lbl}')
    c.restoreState()


def _draw_pdf_footer(c, pw, margin, footer_h, meta, font_map):
    from reportlab.lib import colors

    c.saveState()
    line_y = margin + footer_h
    c.setStrokeColor(colors.HexColor('#CBD5E1'))
    c.setLineWidth(0.75)
    c.line(margin, line_y, pw - margin, line_y)

    c.setFont(font_map[(True, False)], 7.0)
    c.setFillColor(colors.HexColor('#475569'))
    c.drawCentredString(pw / 2.0, line_y - 11, LEYENDA_COPIA_NO_CONTROLADA)

    usuario = meta.get('usuario') or 'POLIDOC'
    fecha_gen = meta.get('fecha_generacion') or timezone.localtime().strftime('%d/%m/%Y %H:%M')
    codigo = meta.get('codigo') or 'SGC-ORG-001'
    version = meta.get('version') or '1'
    c.setFont(font_map[(False, False)], 7.0)
    c.setFillColor(colors.HexColor('#64748B'))
    c.drawString(margin, margin + 6, f'Exportado por: {usuario} · {fecha_gen}')
    c.drawRightString(pw - margin, margin + 6, f'{codigo} (v{version})')
    c.restoreState()


def _draw_pdf_arrow(c, p_prev, p_end, size_pt, color_hex):
    dx = p_end[0] - p_prev[0]
    dy = p_end[1] - p_prev[1]
    dist = math.hypot(dx, dy)
    if dist < 0.5:
        return
    ux = dx / dist
    uy = dy / dist
    px = -uy
    py = ux
    bx = p_end[0] - ux * size_pt
    by = p_end[1] - uy * size_pt
    half_w = size_pt * 0.42

    path = c.beginPath()
    path.moveTo(p_end[0], p_end[1])
    path.lineTo(bx + px * half_w, by + py * half_w)
    path.lineTo(bx - px * half_w, by - py * half_w)
    path.close()
    c.setFillColor(_hex_to_rl_color(color_hex))
    c.drawPath(path, stroke=0, fill=1)


def generar_pdf(datos_filtrados, meta, papel='auto', mostrar_marcos=True, es_obsoleto=False):
    """Genera PDF institucional SGC vectorial usando ReportLab."""
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen import canvas as pdfcanvas

    font_map = _ensure_pdf_fonts()
    content_w = float(datos_filtrados['width'])
    content_h = float(datos_filtrados['height'])
    pagesize = _resolve_pdf_pagesize(papel, content_w, content_h)
    pw, ph = pagesize

    margin = 24.0
    header_h = 58.0
    footer_h = 30.0
    avail_w = pw - margin * 2.0
    avail_h = ph - margin * 2.0 - header_h - footer_h - 12.0

    scale = min(avail_w / content_w, avail_h / content_h, 1.0)
    origin_x = margin + (avail_w - content_w * scale) / 2.0
    origin_top_y = ph - margin - header_h - 6.0 - (avail_h - content_h * scale) / 2.0

    def pt(wx, wy):
        return (origin_x + float(wx) * scale, origin_top_y - float(wy) * scale)

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=pagesize)
    codigo = meta.get('codigo') or 'SGC-ORG-001'
    c.setTitle(f"{codigo} — {meta.get('nombre') or 'Organigrama'}")
    c.setAuthor(meta.get('usuario') or 'POLIDOC')
    c.setCreator('POLIDOC SGC')

    _draw_pdf_header(
        c, pw, ph, margin, header_h, meta,
        datos_filtrados.get('resumen_alcance') or 'Organigrama',
        font_map, es_obsoleto,
    )

    # 1. Secciones
    if mostrar_marcos:
        secs = sorted(datos_filtrados.get('sections', []), key=lambda s: s.get('order', 0))
        for s in secs:
            color_hex = s.get('color') or '#2563EB'
            tint_hex = _mix_hex_with_white(color_hex, 0.08)
            sx, sy, sw, sh = float(s['x']), float(s['y']), float(s['w']), float(s['h'])
            rx, ry_bottom = pt(sx, sy + sh)
            rw, rh = sw * scale, sh * scale

            c.saveState()
            c.setFillColor(_hex_to_rl_color(tint_hex))
            c.setStrokeColor(_hex_to_rl_color(color_hex))
            c.setLineWidth(max(0.6, 2.0 * scale))
            c.setDash([6.0 * scale, 4.0 * scale])
            c.roundRect(rx, ry_bottom, rw, rh, min(10.0 * scale, rw / 2.0, rh / 2.0), stroke=1, fill=1)
            c.setDash([])

            title = ((f"[{s['empresa']}] " if s.get('empresa') else '') + (s.get('title') or 'Sección'))
            badge_font_sz = max(5.5, min(11.0, 10.5 * scale))
            bw = min(
                max(60.0 * scale, pdfmetrics.stringWidth(title, font_map[(True, False)], badge_font_sz) + 14.0 * scale),
                max(60.0 * scale, rw - 16.0 * scale),
            )
            bh = max(12.0, 22.0 * scale)
            bx, by_top = pt(sx + 10.0, sy - 11.0)
            c.setFillColor(_hex_to_rl_color(color_hex))
            c.roundRect(bx, by_top - bh, bw, bh, min(5.0 * scale, bh / 2.0), stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont(font_map[(True, False)], badge_font_sz)
            c.drawString(bx + 7.0 * scale, by_top - bh + (bh - badge_font_sz * 0.72) / 2.0, title)
            c.restoreState()

    # 2. Aristas
    node_map = {n['id']: n for n in datos_filtrados.get('nodes', [])}
    routes = _resolver_rutas_export(datos_filtrados, mostrar_marcos=mostrar_marcos)

    def _build_pdf_edge_path(pts_world, crossings_map):
        p = c.beginPath()
        cur_w = pts_world[0]
        k_bez = 0.55228475
        for tok in _iterar_primitivas_arista(pts_world, crossings_map, corner_r=8.0, jump_r=6.0):
            kind = tok[0]
            if kind == 'M':
                cur_w = tok[1]
                px, py = pt(*cur_w)
                p.moveTo(px, py)
            elif kind == 'L':
                cur_w = tok[1]
                px, py = pt(*cur_w)
                p.lineTo(px, py)
            elif kind == 'Q':
                ctrl_w, end_w = tok[1], tok[2]
                c1x, c1y = pt(cur_w[0] + (ctrl_w[0] - cur_w[0]) * (2.0 / 3.0), cur_w[1] + (ctrl_w[1] - cur_w[1]) * (2.0 / 3.0))
                c2x, c2y = pt(end_w[0] + (ctrl_w[0] - end_w[0]) * (2.0 / 3.0), end_w[1] + (ctrl_w[1] - end_w[1]) * (2.0 / 3.0))
                ex, ey = pt(*end_w)
                p.curveTo(c1x, c1y, c2x, c2y, ex, ey)
                cur_w = end_w
            elif kind == 'JUMP':
                s_w, e_w, vx, jr, going_right = tok[1:]
                sx_p, sy_p = pt(*s_w)
                ex_p, _ = pt(*e_w)
                vx_p, py_peak = pt(vx, s_w[1] - jr)
                r_pt = jr * scale
                dir_x = 1.0 if going_right else -1.0
                p.curveTo(
                    sx_p, sy_p + k_bez * r_pt,
                    vx_p - dir_x * k_bez * r_pt, py_peak,
                    vx_p, py_peak,
                )
                p.curveTo(
                    vx_p + dir_x * k_bez * r_pt, py_peak,
                    ex_p, sy_p + k_bez * r_pt,
                    ex_p, sy_p,
                )
                cur_w = e_w
        return p

    for edge in datos_filtrados.get('edges', []):
        src = node_map.get(edge.get('from'))
        dst = node_map.get(edge.get('to'))
        if not src or not dst:
            continue
        geom = routes.get(edge.get('id')) or calcular_geometria_arista(src, dst, edge)
        raw_pts = geom['points']
        if len(raw_pts) < 2:
            continue

        stroke_hex = resolver_color_export(edge.get('color', 'auto'), 'edge')
        lw = max(0.6, float(edge.get('width', 2)) * scale)
        style = edge.get('style', 'solid')

        c.saveState()
        c.setLineCap(1)
        c.setLineJoin(1)

        path = _build_pdf_edge_path(raw_pts, geom.get('crossings'))
        c.setDash([])
        c.setStrokeColor(colors.white)
        c.setLineWidth(lw + 3.0 * scale)
        c.drawPath(path, stroke=1, fill=0)

        c.setStrokeColor(_hex_to_rl_color(stroke_hex))
        c.setLineWidth(lw)
        if style == 'dashed':
            c.setDash([8.0 * scale, 5.0 * scale])
        elif style == 'dotted':
            c.setDash([2.0 * scale, 4.0 * scale])
        c.drawPath(path, stroke=1, fill=0)
        c.setDash([])

        if edge.get('arrowEnd', True):
            pdf_pts = [pt(px, py) for px, py in raw_pts]
            p_prev = next(
                (pdf_pts[idx] for idx in range(len(pdf_pts) - 2, -1, -1)
                 if abs(pdf_pts[idx][0] - pdf_pts[-1][0]) > 1e-3 or abs(pdf_pts[idx][1] - pdf_pts[-1][1]) > 1e-3),
                pdf_pts[-2],
            )
            _draw_pdf_arrow(c, p_prev, pdf_pts[-1], max(4.5, 9.0 * scale), stroke_hex)

        label = (edge.get('label') or '').strip()
        if label:
            lx, ly = pt(*geom['label_pos'])
            lbl_fs = max(5.5, 10.0 * scale)
            lbl_w = max(24.0, pdfmetrics.stringWidth(label, font_map[(True, False)], lbl_fs) + 10.0 * scale)
            lbl_h = max(10.0, 18.0 * scale)
            c.setFillColor(colors.white)
            c.setStrokeColor(colors.HexColor('#CBD5E1'))
            c.setLineWidth(0.6)
            c.roundRect(lx - lbl_w / 2.0, ly - lbl_h / 2.0, lbl_w, lbl_h, 3.0 * scale, stroke=1, fill=1)
            c.setFillColor(colors.HexColor('#0F172A'))
            c.setFont(font_map[(True, False)], lbl_fs)
            c.drawCentredString(lx, ly - lbl_fs * 0.34, label)
        c.restoreState()

    # 3. Nodos
    nodes_sorted = sorted(datos_filtrados.get('nodes', []), key=lambda n: n.get('zIndex', 1))
    for node in nodes_sorted:
        st = node.get('style') or {}
        nx, ny, nw, nh = float(node['x']), float(node['y']), float(node['w']), float(node['h'])
        rx, ry_bottom = pt(nx, ny + nh)
        rw, rh = nw * scale, nh * scale
        shape = node.get('shape', 'rounded')

        bg = resolver_color_export(st.get('bgColor', 'auto'), 'bg')
        fg = resolver_color_export(st.get('textColor', 'auto'), 'text')
        bc = resolver_color_export(st.get('borderColor', 'auto'), 'border')
        bw = float(st.get('borderWidth', 2)) * scale
        bstyle = st.get('borderStyle', 'solid')

        has_fill = bg is not None
        has_stroke = (bw > 0 and bc is not None)

        c.saveState()
        if has_fill:
            c.setFillColor(_hex_to_rl_color(bg))
        if has_stroke:
            c.setStrokeColor(_hex_to_rl_color(bc))
            c.setLineWidth(max(0.4, bw))
            if bstyle == 'dashed':
                c.setDash([8.0 * scale, 5.0 * scale])
            elif bstyle == 'dotted':
                c.setDash([2.0 * scale, 4.0 * scale])

        if shape == 'diamond':
            top_pt = pt(nx + nw / 2.0, ny)
            right_pt = pt(nx + nw, ny + nh / 2.0)
            bot_pt = pt(nx + nw / 2.0, ny + nh)
            left_pt = pt(nx, ny + nh / 2.0)
            path = c.beginPath()
            path.moveTo(*top_pt)
            path.lineTo(*right_pt)
            path.lineTo(*bot_pt)
            path.lineTo(*left_pt)
            path.close()
            c.setLineJoin(1)
            c.drawPath(path, stroke=1 if has_stroke else 0, fill=1 if has_fill else 0)
        else:
            radius = 0.0
            if shape == 'pill':
                radius = min(rw, rh) / 2.0
            elif shape == 'rounded':
                radius = min(float(st.get('borderRadius', 10)) * scale, min(rw, rh) / 2.0)
            elif shape == 'rect':
                radius = min(2.0 * scale, min(rw, rh) / 2.0)
            if radius > 0:
                c.roundRect(rx, ry_bottom, rw, rh, radius, stroke=1 if has_stroke else 0, fill=1 if has_fill else 0)
            else:
                c.rect(rx, ry_bottom, rw, rh, stroke=1 if has_stroke else 0, fill=1 if has_fill else 0)

        c.setDash([])

        text_val = node.get('text') or ''
        if text_val.strip():
            font_key = (st.get('fontWeight') == 'bold', st.get('fontStyle') == 'italic')
            font_name = font_map[font_key]
            font_sz = max(4.5, float(st.get('fontSize', 13)) * scale)
            pad_x = rw * 0.20 if shape == 'diamond' else max(4.0, 8.0 * scale)
            lines = _wrap_text(
                text_val,
                rw - pad_x * 2.0,
                font_sz,
                width_fn=lambda s: pdfmetrics.stringWidth(s, font_name, font_sz),
            )
            line_h = font_sz * 1.25
            total_h = len(lines) * line_h
            baseline_y = ry_bottom + (rh + total_h) / 2.0 - font_sz * 0.85

            c.setFillColor(_hex_to_rl_color(fg))
            c.setFont(font_name, font_sz)
            align = st.get('textAlign', 'center')
            for idx, line in enumerate(lines):
                ly = baseline_y - idx * line_h
                if align == 'left':
                    c.drawString(rx + pad_x, ly, line)
                elif align == 'right':
                    c.drawRightString(rx + rw - pad_x, ly, line)
                else:
                    c.drawCentredString(rx + rw / 2.0, ly, line)

        c.restoreState()

    # 4. Marca de agua OBSOLETO si corresponde
    if es_obsoleto:
        c.saveState()
        c.setFillColorRGB(0.86, 0.15, 0.15, alpha=0.14)
        c.setFont(font_map[(True, False)], min(pw, ph) * 0.14)
        c.translate(pw / 2.0, ph / 2.0)
        c.rotate(28)
        c.drawCentredString(0, -18, 'OBSOLETO')
        c.restoreState()

    _draw_pdf_footer(c, pw, margin, footer_h, meta, font_map)
    c.showPage()
    c.save()
    return buf.getvalue()


def _generar_png_pillow_fallback(datos_filtrados, meta, mostrar_marcos=True, es_obsoleto=False):
    """Fallback puro en Pillow si Ghostscript (`gs`) no está disponible en el entorno."""
    from PIL import Image, ImageDraw, ImageFont

    scale = 2
    w = int(round(float(datos_filtrados['width']) * scale))
    header_px = 110
    footer_px = 64
    h = int(round(float(datos_filtrados['height']) * scale)) + header_px + footer_px

    img = Image.new('RGB', (w, h), '#FFFFFF')
    draw = ImageDraw.Draw(img)

    font_path = os.path.join(_LIBERATION_DIR, 'LiberationSans-Regular.ttf')
    bold_path = os.path.join(_LIBERATION_DIR, 'LiberationSans-Bold.ttf')
    try:
        font_title = ImageFont.truetype(bold_path, 22)
        font_meta = ImageFont.truetype(font_path, 16)
        font_footer = ImageFont.truetype(bold_path, 14)
    except OSError:
        font_title = font_meta = font_footer = ImageFont.load_default()

    draw.rectangle([16, 12, w - 16, header_px - 10], fill='#F8FAFC', outline='#CBD5E1', width=2)
    draw.text((32, 26), f"{meta.get('codigo') or 'SGC-ORG-001'} — {meta.get('nombre') or 'Organigrama'}", fill='#1E40AF', font=font_title)
    draw.text((32, 58), f"v{meta.get('version') or '1'} · {datos_filtrados.get('resumen_alcance') or ''}", fill='#475569', font=font_meta)

    oy = header_px
    if mostrar_marcos:
        for s in sorted(datos_filtrados.get('sections', []), key=lambda x: x.get('order', 0)):
            color = s.get('color') or '#2563EB'
            tint = _mix_hex_with_white(color, 0.08)
            sx, sy, sw, sh = s['x'] * scale, s['y'] * scale + oy, s['w'] * scale, s['h'] * scale
            draw.rectangle([sx, sy, sx + sw, sy + sh], fill=tint, outline=color, width=3)

    node_map = {n['id']: n for n in datos_filtrados.get('nodes', [])}
    routes = _resolver_rutas_export(datos_filtrados, mostrar_marcos=mostrar_marcos)
    for edge in datos_filtrados.get('edges', []):
        src = node_map.get(edge.get('from'))
        dst = node_map.get(edge.get('to'))
        if not src or not dst:
            continue
        geom = routes.get(edge.get('id')) or calcular_geometria_arista(src, dst, edge)
        pts = _primitivas_a_polyline(geom['points'], geom.get('crossings'), scale=scale, oy=oy)
        if len(pts) < 2:
            continue
        stroke = resolver_color_export(edge.get('color', 'auto'), 'edge')
        sw = max(2, int(round(edge.get('width', 2) * scale)))
        draw.line(pts, fill='#FFFFFF', width=sw + max(4, int(round(4 * scale))), joint='curve')
        draw.line(pts, fill=stroke, width=sw, joint='curve')

    for n in sorted(datos_filtrados.get('nodes', []), key=lambda x: x.get('zIndex', 1)):
        st = n.get('style') or {}
        nx, ny, nw, nh = n['x'] * scale, n['y'] * scale + oy, n['w'] * scale, n['h'] * scale
        bg = resolver_color_export(st.get('bgColor', 'auto'), 'bg')
        fg = resolver_color_export(st.get('textColor', 'auto'), 'text')
        bc = resolver_color_export(st.get('borderColor', 'auto'), 'border')
        bw = max(0, int(round(st.get('borderWidth', 2) * scale)))
        if n.get('shape') == 'diamond':
            poly = [(nx + nw / 2, ny), (nx + nw, ny + nh / 2), (nx + nw / 2, ny + nh), (nx, ny + nh / 2)]
            draw.polygon(poly, fill=bg, outline=bc if bw > 0 else None)
        else:
            draw.rectangle([nx, ny, nx + nw, ny + nh], fill=bg, outline=bc if bw > 0 else None, width=bw)
        fsize = max(14, int(round(float(st.get('fontSize', 13)) * scale)))
        try:
            nf = ImageFont.truetype(bold_path if st.get('fontWeight') == 'bold' else font_path, fsize)
        except OSError:
            nf = ImageFont.load_default()
        text = (n.get('text') or '').strip()
        if text:
            draw.multiline_text((nx + nw / 2, ny + nh / 2), text, fill=fg, font=nf, anchor='mm', align='center')

    if es_obsoleto:
        draw.text((w / 2, h / 2), 'OBSOLETO', fill='#DC2626', font=font_title, anchor='mm')

    draw.text((w / 2, h - 32), LEYENDA_COPIA_NO_CONTROLADA, fill='#475569', font=font_footer, anchor='mm')
    out = io.BytesIO()
    img.save(out, format='PNG')
    return out.getvalue()


def generar_png(datos_filtrados, meta, papel='auto', mostrar_marcos=True, es_obsoleto=False):
    """Genera PNG nítido de 180 DPI rasterizando el PDF institucional SGC con Ghostscript."""
    gs_bin = shutil.which('gs')
    if not gs_bin:
        return _generar_png_pillow_fallback(
            datos_filtrados, meta, mostrar_marcos=mostrar_marcos, es_obsoleto=es_obsoleto,
        )

    pdf_bytes = generar_pdf(
        datos_filtrados, meta, papel=papel, mostrar_marcos=mostrar_marcos, es_obsoleto=es_obsoleto,
    )
    with tempfile.TemporaryDirectory(prefix='polidoc_org_png_') as tmpdir:
        in_pdf = os.path.join(tmpdir, 'organigrama.pdf')
        out_png = os.path.join(tmpdir, 'organigrama.png')
        with open(in_pdf, 'wb') as fh:
            fh.write(pdf_bytes)
        try:
            res = subprocess.run(
                [
                    gs_bin,
                    '-dSAFER',
                    '-dBATCH',
                    '-dNOPAUSE',
                    '-dFirstPage=1',
                    '-dLastPage=1',
                    '-dTextAlphaBits=4',
                    '-dGraphicsAlphaBits=4',
                    '-sDEVICE=png16m',
                    '-r180',
                    f'-sOutputFile={out_png}',
                    in_pdf,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise OrganigramaExportError('Tiempo agotado al rasterizar el PNG.') from exc

        if res.returncode != 0 or not os.path.isfile(out_png):
            logger.error('Ghostscript falló al generar PNG: %s', (res.stderr or res.stdout).strip())
            raise OrganigramaExportError('No se pudo renderizar la imagen PNG del organigrama.')

        with open(out_png, 'rb') as fh:
            png_bytes = fh.read()
        if not png_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
            raise OrganigramaExportError('La salida PNG generada no es válida.')
        return png_bytes


def generar_odg(datos_filtrados, meta, mostrar_marcos=True, es_obsoleto=False):
    """Genera `.odg` editable convirtiendo SVG vía LibreOffice Draw con perfil aislado."""
    lo_bin = shutil.which('libreoffice')
    if not lo_bin:
        raise OrganigramaExportError('LibreOffice no está instalado para generar archivos ODG.')

    svg_bytes = generar_svg(
        datos_filtrados, meta, mostrar_marcos=mostrar_marcos, es_obsoleto=es_obsoleto,
    )
    with tempfile.TemporaryDirectory(prefix='polidoc_org_odg_') as tmpdir:
        prof_dir = os.path.join(tmpdir, 'lo_profile')
        out_dir = os.path.join(tmpdir, 'out')
        os.makedirs(prof_dir, exist_ok=True)
        os.makedirs(out_dir, exist_ok=True)
        svg_path = os.path.join(tmpdir, 'organigrama.svg')
        with open(svg_path, 'wb') as fh:
            fh.write(svg_bytes)

        try:
            res = subprocess.run(
                [
                    lo_bin,
                    '--headless',
                    '--norestore',
                    '--nolockcheck',
                    f'-env:UserInstallation=file://{prof_dir}',
                    '--convert-to',
                    'odg',
                    '--outdir',
                    out_dir,
                    svg_path,
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired as exc:
            raise OrganigramaExportError('Tiempo agotado al convertir a ODG.') from exc

        odg_path = os.path.join(out_dir, 'organigrama.odg')
        if res.returncode != 0 or not os.path.isfile(odg_path) or not zipfile.is_zipfile(odg_path):
            logger.error('LibreOffice ODG falló: %s', (res.stderr or res.stdout).strip())
            raise OrganigramaExportError('No se pudo convertir el organigrama a formato ODG.')

        with open(odg_path, 'rb') as fh:
            return fh.read()


def _slug_archivo(texto):
    slug = re.sub(r'[^A-Za-z0-9_-]+', '-', str(texto or '').strip()).strip('-_')
    return (slug[:40] or 'seleccion').lower()


def exportar_organigrama(
    datos,
    formato,
    meta,
    todo=True,
    section_ids=None,
    empresas=None,
    incluir_sin_seccion=None,
    mostrar_marcos=True,
    papel='auto',
    es_obsoleto=False,
):
    """Filtra el organigrama y genera `(bytes, filename, content_type, resumen_auditoria)`."""
    fmt = (formato or '').strip().lower()
    if fmt not in FORMATOS_EXPORTACION:
        raise OrganigramaExportError(
            f'Formato no soportado: {formato!r}. Permitidos: {", ".join(FORMATOS_EXPORTACION)}.'
        )

    etiqueta_doc = (meta or {}).get('tipo_label') or 'Organigrama'
    filtrados = filtrar_organigrama(
        datos,
        todo=todo,
        section_ids=section_ids,
        empresas=empresas,
        incluir_sin_seccion=incluir_sin_seccion,
        mostrar_marcos=mostrar_marcos,
        etiqueta_doc=etiqueta_doc,
    )

    if fmt == 'svg':
        contenido = generar_svg(filtrados, meta, mostrar_marcos=mostrar_marcos, es_obsoleto=es_obsoleto)
    elif fmt == 'pdf':
        contenido = generar_pdf(
            filtrados, meta, papel=papel, mostrar_marcos=mostrar_marcos, es_obsoleto=es_obsoleto,
        )
    elif fmt == 'png':
        contenido = generar_png(
            filtrados, meta, papel=papel, mostrar_marcos=mostrar_marcos, es_obsoleto=es_obsoleto,
        )
    elif fmt == 'odg':
        contenido = generar_odg(filtrados, meta, mostrar_marcos=mostrar_marcos, es_obsoleto=es_obsoleto)
    else:
        contenido = generar_vsdx(
            filtrados, meta, calcular_geometria_arista, resolver_color_export,
            mostrar_marcos=mostrar_marcos, rutas_aristas=filtrados.get('routes'),
        )

    codigo = _slug_archivo(meta.get('codigo') or 'SGC-ORG-001').upper()
    version = re.sub(r'[^A-Za-z0-9._-]+', '', str(meta.get('version') or '1')) or '1'
    alcance_slug = 'completo' if todo else _slug_archivo(filtrados.get('resumen_alcance'))
    filename = f'{codigo}_v{version}_{alcance_slug}.{fmt}'

    resumen_auditoria = {
        'formato': fmt,
        'papel': papel if fmt == 'pdf' else None,
        'todo': bool(todo),
        'section_ids': [s['id'] for s in filtrados['sections']],
        'empresas': sorted({s.get('empresa', '') for s in filtrados['sections']} - {''}),
        'nodos_exportados': len(filtrados['nodes']),
        'conexiones_exportadas': len(filtrados['edges']),
        'es_obsoleto': bool(es_obsoleto),
        'alcance': filtrados['resumen_alcance'],
    }
    return contenido, filename, MIME_TYPES[fmt], resumen_auditoria
