"""Enrutador ortogonal anticolisiones (A*), separación de carriles y cruces.

Calcula trayectorias limpias para todas las conexiones de un organigrama
esquivando figuras y marcos de secciones ajenas, separando carriles paralelos
entre orígenes distintos y anotando puntos de salto para cruces legibles.
"""
import heapq
import math

STUB_LEN = 10.0
NODE_PAD = 6.0
ENDPOINT_NODE_PAD = 4.0
SEC_PAD = 8.0
SEC_TOP_PAD = 20.0
LANE_STEP = 8.0
BEND_PENALTY = 180.0
SHARED_BUS_PENALTY = 380.0
SOFT_SECTION_PENALTY = 50_000.0
SOFT_NODE_PENALTY = 120_000.0


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


def _box_for_node(node, pad=NODE_PAD):
    return (
        float(node['x']) - pad,
        float(node['y']) - pad,
        float(node['x']) + float(node['w']) + pad,
        float(node['y']) + float(node['h']) + pad,
    )


def _box_for_section(sec, pad=SEC_PAD, top_pad=SEC_TOP_PAD):
    return (
        float(sec['x']) - pad,
        float(sec['y']) - top_pad,
        float(sec['x']) + float(sec['w']) + pad,
        float(sec['y']) + float(sec['h']) + pad,
    )


def _axis_seg_hits_box(a, b, box):
    x1, y1 = a
    x2, y2 = b
    rx0, ry0, rx1, ry1 = box
    if abs(y1 - y2) < 1e-6:
        if not (ry0 < y1 < ry1):
            return False
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return hi > rx0 and lo < rx1
    if abs(x1 - x2) < 1e-6:
        if not (rx0 < x1 < rx1):
            return False
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return hi > ry0 and lo < ry1
    return False


def _pt_in_box(pt, box):
    px, py = pt
    return box[0] < px < box[2] and box[1] < py < box[3]


def _clean_points(pts):
    """Elimina vértices repetidos y puntos intermedios colineales."""
    if not pts:
        return []
    dedup = [pts[0]]
    for pt in pts[1:]:
        if abs(pt[0] - dedup[-1][0]) > 0.25 or abs(pt[1] - dedup[-1][1]) > 0.25:
            dedup.append(pt)
    if len(dedup) <= 2:
        return dedup

    out = [dedup[0]]
    for i in range(1, len(dedup) - 1):
        ax, ay = out[-1]
        bx, by = dedup[i]
        cx, cy = dedup[i + 1]
        same_x = abs(ax - bx) < 0.25 and abs(bx - cx) < 0.25
        same_y = abs(ay - by) < 0.25 and abs(by - cy) < 0.25
        if same_x or same_y:
            continue
        out.append((round(bx, 2), round(by, 2)))
    out.append(dedup[-1])
    return out


def _dedup_coords(coords, tol=2.0):
    sorted_vals = sorted(round(float(v), 2) for v in coords)
    if not sorted_vals:
        return []
    out = [sorted_vals[0]]
    for v in sorted_vals[1:]:
        if v - out[-1] > tol:
            out.append(v)
    return out


def _fast_orthogonal_points(src_node, dst_node, edge):
    fa = edge.get('fromAnchor', 'bottom')
    ta = edge.get('toAnchor', 'top')
    p1 = _anchor_point(src_node, fa)
    p2 = _anchor_point(dst_node, ta)
    d1 = _anchor_dir(fa)
    d2 = _anchor_dir(ta)

    # Ajustar stub si los puertos están muy cerca entre sí para no invertir el giro
    gap_v = abs(p2[1] - p1[1])
    gap_h = abs(p2[0] - p1[0])
    stub1 = STUB_LEN
    stub2 = STUB_LEN
    if d1[1] != 0 and d1[1] == -d2[1] and (p2[1] - p1[1]) * d1[1] > 0:
        half = max(6.0, gap_v / 2.0 - 1.0)
        stub1 = min(STUB_LEN, half)
        stub2 = min(STUB_LEN, half)
    elif d1[0] != 0 and d1[0] == -d2[0] and (p2[0] - p1[0]) * d1[0] > 0:
        half = max(6.0, gap_h / 2.0 - 1.0)
        stub1 = min(STUB_LEN, half)
        stub2 = min(STUB_LEN, half)

    s1 = (p1[0] + d1[0] * stub1, p1[1] + d1[1] * stub1)
    s2 = (p2[0] + d2[0] * stub2, p2[1] + d2[1] * stub2)

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
    return _clean_points(pts), p1, s1, s2, p2


def _build_base_grid(nodes, sections):
    if not nodes and not sections:
        return [], []

    items = list(nodes) + list(sections)
    min_x = min(float(it['x']) for it in items)
    min_y = min(float(it['y']) for it in items)
    max_x = max(float(it['x']) + float(it['w']) for it in items)
    max_y = max(float(it['y']) + float(it['h']) for it in items)

    xs = [min_x - 52.0, min_x - 28.0, max_x + 28.0, max_x + 52.0]
    ys = [min_y - 60.0, min_y - 36.0, max_y + 28.0, max_y + 52.0]

    sec_x_bounds = []
    sec_y_bounds = []
    for s in sections:
        sx, sy, sw, sh = float(s['x']), float(s['y']), float(s['w']), float(s['h'])
        xs.extend([sx - 24.0, sx + sw + 24.0])
        ys.extend([sy - 36.0, sy + sh + 24.0])
        sec_x_bounds.append((sx, sx + sw))
        sec_y_bounds.append((sy, sy + sh))

    # Puntos medios en pasillos entre secciones
    for coords_list, bounds in ((xs, sec_x_bounds), (ys, sec_y_bounds)):
        edges_pts = sorted({b[0] for b in bounds} | {b[1] for b in bounds})
        for a, b in zip(edges_pts, edges_pts[1:]):
            if b - a >= 24.0:
                coords_list.append((a + b) / 2.0)

    # Nodos sueltos sin sección: añadir sus márgenes a la grilla base
    for n in nodes:
        if not n.get('section_id'):
            nx, ny, nw, nh = float(n['x']), float(n['y']), float(n['w']), float(n['h'])
            xs.extend([nx - 14.0, nx + nw + 14.0])
            ys.extend([ny - 16.0, ny + nh + 16.0])

    return _dedup_coords(xs), _dedup_coords(ys)


def _edge_local_grid(base_xs, base_ys, nodes_by_sec, sec_map, src_node, dst_node, s1, s2):
    src_sec = src_node.get('section_id')
    dst_sec = dst_node.get('section_id')

    if src_sec and src_sec == dst_sec:
        # Arista intra-sección: acotar la grilla solo a su propia sección
        sec = sec_map.get(src_sec)
        if sec:
            xs = [float(sec['x']) - 24.0, float(sec['x']) + float(sec['w']) + 24.0, s1[0], s2[0]]
            ys = [float(sec['y']) - 36.0, float(sec['y']) + float(sec['h']) + 24.0, s1[1], s2[1]]
        else:
            xs = [s1[0], s2[0]]
            ys = [s1[1], s2[1]]
    else:
        xs = list(base_xs) + [s1[0], s2[0]]
        ys = list(base_ys) + [s1[1], s2[1]]

    relevant_secs = {sec for sec in (src_sec, dst_sec) if sec}
    local_nodes = []
    for sec_id in relevant_secs:
        local_nodes.extend(nodes_by_sec.get(sec_id, []))
    if not relevant_secs:
        for grp in nodes_by_sec.values():
            local_nodes.extend(grp)
    if src_node not in local_nodes:
        local_nodes.append(src_node)
    if dst_node not in local_nodes:
        local_nodes.append(dst_node)

    for n in local_nodes:
        nx, ny, nw, nh = float(n['x']), float(n['y']), float(n['w']), float(n['h'])
        xs.extend([nx - 10.0, nx + nw + 10.0])
        ys.extend([ny - 10.0, ny + nh + 10.0])

    # Coordenadas intermedias verticales/horizontales entre src y dst
    sx_c = float(src_node['x']) + float(src_node['w']) / 2.0
    dx_c = float(dst_node['x']) + float(dst_node['w']) / 2.0
    sy_c = float(src_node['y']) + float(src_node['h']) / 2.0
    dy_c = float(dst_node['y']) + float(dst_node['h']) / 2.0
    xs.append((sx_c + dx_c) / 2.0)
    ys.append((sy_c + dy_c) / 2.0)

    return _dedup_coords(xs, tol=1.5), _dedup_coords(ys, tol=1.5)


def _find_coord_idx(sorted_coords, target):
    best_i = 0
    best_d = abs(sorted_coords[0] - target)
    for i in range(1, len(sorted_coords)):
        d = abs(sorted_coords[i] - target)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def _run_astar(
    xs,
    ys,
    start_pt,
    goal_pt,
    node_boxes,
    endpoint_boxes,
    section_boxes,
    used_h_segs,
    used_v_segs,
    group_key,
    allow_section_cross=False,
    allow_node_cross=False,
):
    sx = _find_coord_idx(xs, start_pt[0])
    sy = _find_coord_idx(ys, start_pt[1])
    gx = _find_coord_idx(xs, goal_pt[0])
    gy = _find_coord_idx(ys, goal_pt[1])

    nx = len(xs)
    ny = len(ys)

    if sx == gx and sy == gy:
        return [(xs[sx], ys[sy])]

    # Pre-indexar cajas por coordenada en el eje perpendicular (poda de obstáculos)
    h_obs_by_iy = []
    for iy in range(ny):
        y = ys[iy]
        row_nodes = [b for b in node_boxes if b[1] < y < b[3]] + [
            b for b in endpoint_boxes if b[1] < y < b[3]
        ]
        row_secs = [b for b in section_boxes if b[1] < y < b[3]]
        h_obs_by_iy.append((row_nodes, row_secs))

    v_obs_by_ix = []
    for ix in range(nx):
        x = xs[ix]
        col_nodes = [b for b in node_boxes if b[0] < x < b[2]] + [
            b for b in endpoint_boxes if b[0] < x < b[2]
        ]
        col_secs = [b for b in section_boxes if b[0] < x < b[2]]
        v_obs_by_ix.append((col_nodes, col_secs))

    def step_cost(x1, y1, x2, y2, ix1, iy1, ix2, iy2):
        dist = abs(x2 - x1) + abs(y2 - y1)
        extra = 0.0
        if iy1 == iy2:
            rnodes, rsecs = h_obs_by_iy[iy1]
            lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
            for rx0, _, rx1, _ in rnodes:
                if hi > rx0 and lo < rx1:
                    if not allow_node_cross:
                        return None
                    extra += SOFT_NODE_PENALTY
            for sx0, _, sx1, _ in rsecs:
                if hi > sx0 and lo < sx1:
                    if not allow_section_cross:
                        return None
                    extra += SOFT_SECTION_PENALTY
            y_key = round(y1, 1)
            for u_lo, u_hi, u_grp in used_h_segs.get(y_key, ()):
                if u_grp != group_key and min(hi, u_hi) - max(lo, u_lo) > 2.0:
                    extra += SHARED_BUS_PENALTY
        else:
            cnodes, csecs = v_obs_by_ix[ix1]
            lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
            for _, ry0, _, ry1 in cnodes:
                if hi > ry0 and lo < ry1:
                    if not allow_node_cross:
                        return None
                    extra += SOFT_NODE_PENALTY
            for _, sy0, _, sy1 in csecs:
                if hi > sy0 and lo < sy1:
                    if not allow_section_cross:
                        return None
                    extra += SOFT_SECTION_PENALTY
            x_key = round(x1, 1)
            for u_lo, u_hi, u_grp in used_v_segs.get(x_key, ()):
                if u_grp != group_key and min(hi, u_hi) - max(lo, u_lo) > 2.0:
                    extra += SHARED_BUS_PENALTY
        return dist + extra

    g_score = {}
    came_from = {}
    heap = []
    # dir_axis: 0 = horizontal, 1 = vertical, 2 = inicial
    start_state = (sx, sy, 2)
    g_score[start_state] = 0.0
    h0 = abs(xs[gx] - xs[sx]) + abs(ys[gy] - ys[sy])
    heapq.heappush(heap, (h0, 0.0, sx, sy, 2))

    goal_state = None
    max_iters = min(30_000, nx * ny * 6)
    iters = 0

    while heap and iters < max_iters:
        iters += 1
        _, g_cur, ix, iy, d_cur = heapq.heappop(heap)
        st = (ix, iy, d_cur)
        if g_cur > g_score.get(st, float('inf')) + 1e-6:
            continue
        if ix == gx and iy == gy:
            goal_state = st
            break

        x1, y1 = xs[ix], ys[iy]
        neighbors = []
        if ix > 0:
            neighbors.append((ix - 1, iy, 0))
        if ix + 1 < nx:
            neighbors.append((ix + 1, iy, 0))
        if iy > 0:
            neighbors.append((ix, iy - 1, 1))
        if iy + 1 < ny:
            neighbors.append((ix, iy + 1, 1))

        for nix, niy, ndir in neighbors:
            sc = step_cost(x1, y1, xs[nix], ys[niy], ix, iy, nix, niy)
            if sc is None:
                continue
            bend = BEND_PENALTY if (d_cur != 2 and d_cur != ndir) else 0.0
            ng = g_cur + sc + bend
            nst = (nix, niy, ndir)
            if ng < g_score.get(nst, float('inf')) - 1e-6:
                g_score[nst] = ng
                came_from[nst] = st
                h_est = abs(xs[gx] - xs[nix]) + abs(ys[gy] - ys[niy])
                heapq.heappush(heap, (ng + h_est, ng, nix, niy, ndir))

    if goal_state is None:
        return None

    path = []
    cur = goal_state
    while cur is not None:
        path.append((xs[cur[0]], ys[cur[1]]))
        cur = came_from.get(cur)
    path.reverse()
    return path


def _nudge_parallel_lanes(routes, meta_by_id, other_nodes_fn, other_secs_fn):
    """Separa segmentos interiores horizontales/verticales coincidentes en carriles paralelos."""
    for axis in ('h', 'v'):
        buckets = {}
        for eid, geom in routes.items():
            if meta_by_id[eid]['routing'] == 'straight':
                continue
            pts = geom['points']
            # Solo segmentos interiores donde ambos extremos se pueden deslizar perpendicularmente
            for idx in range(1, len(pts) - 2):
                a = pts[idx]
                b = pts[idx + 1]
                if axis == 'h' and abs(a[1] - b[1]) < 0.25 and abs(a[0] - b[0]) > 4.0:
                    coord = round(a[1] / 2.0) * 2.0
                    lo, hi = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
                    buckets.setdefault(coord, []).append((eid, idx, lo, hi, meta_by_id[eid]['group']))
                elif axis == 'v' and abs(a[0] - b[0]) < 0.25 and abs(a[1] - b[1]) > 4.0:
                    coord = round(a[0] / 2.0) * 2.0
                    lo, hi = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
                    buckets.setdefault(coord, []).append((eid, idx, lo, hi, meta_by_id[eid]['group']))

        for _, entries in buckets.items():
            # Agrupar segmentos que comparten el mismo rango con distinto group_key
            clusters = []
            for entry in sorted(entries, key=lambda it: (it[2], it[3])):
                eid, seg_idx, lo, hi, grp = entry
                placed_cluster = None
                for cl in clusters:
                    if any(min(hi, c_hi) - max(lo, c_lo) > 6.0 for _, _, c_lo, c_hi, _ in cl):
                        placed_cluster = cl
                        break
                if placed_cluster is None:
                    clusters.append([entry])
                else:
                    placed_cluster.append(entry)

            for cl in clusters:
                groups_in_cl = []
                for _, _, _, _, grp in cl:
                    if grp not in groups_in_cl:
                        groups_in_cl.append(grp)
                n_grp = len(groups_in_cl)
                if n_grp <= 1:
                    continue
                offset_by_grp = {
                    grp: (g_idx - (n_grp - 1) / 2.0) * LANE_STEP
                    for g_idx, grp in enumerate(groups_in_cl)
                }
                for eid, seg_idx, _, _, grp in cl:
                    delta = offset_by_grp[grp]
                    if abs(delta) < 0.5:
                        continue
                    pts = routes[eid]['points']
                    a = pts[seg_idx]
                    b = pts[seg_idx + 1]
                    prev_pt = pts[seg_idx - 1]
                    next_pt = pts[seg_idx + 2]
                    if axis == 'h':
                        # Verificar que extremos adyacentes sean verticales
                        if abs(prev_pt[0] - a[0]) > 1.0 or abs(next_pt[0] - b[0]) > 1.0:
                            continue
                        na = (a[0], round(a[1] + delta, 2))
                        nb = (b[0], round(b[1] + delta, 2))
                    else:
                        if abs(prev_pt[1] - a[1]) > 1.0 or abs(next_pt[1] - b[1]) > 1.0:
                            continue
                        na = (round(a[0] + delta, 2), a[1])
                        nb = (round(b[0] + delta, 2), b[1])

                    obs = other_nodes_fn(eid) + other_secs_fn(eid)
                    if any(
                        _axis_seg_hits_box(s_a, s_b, box)
                        for s_a, s_b in ((prev_pt, na), (na, nb), (nb, next_pt))
                        for box in obs
                    ):
                        continue
                    pts[seg_idx] = na
                    pts[seg_idx + 1] = nb
                    routes[eid]['points'] = _clean_points(pts)


def _detect_crossings(routes, meta_by_id):
    """Registra cruces perpendiculares (segmento H sobre segmento V de distinto origen)."""
    h_segs = []
    v_segs = []
    for eid, geom in routes.items():
        grp = meta_by_id[eid]['group']
        pts = geom['points']
        for seg_idx in range(len(pts) - 1):
            x1, y1 = pts[seg_idx]
            x2, y2 = pts[seg_idx + 1]
            if abs(y1 - y2) < 0.25 and abs(x1 - x2) >= 24.0:
                lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
                h_segs.append((eid, seg_idx, y1, lo, hi, grp))
            elif abs(x1 - x2) < 0.25 and abs(y1 - y2) >= 24.0:
                lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
                v_segs.append((eid, seg_idx, x1, lo, hi, grp))

    for heid, hidx, hy, hx0, hx1, hgrp in h_segs:
        jumps = []
        for _, _, vx, vy0, vy1, vgrp in v_segs:
            if hgrp == vgrp:
                continue
            if (hx0 + 12.0) < vx < (hx1 - 12.0) and (vy0 + 10.0) < hy < (vy1 - 10.0):
                jumps.append(round(vx, 2))
        if jumps:
            jumps.sort()
            # Deduplicar saltos muy próximos (< 14px) para no solapar arcos
            clean_jumps = [jumps[0]]
            for jx in jumps[1:]:
                if jx - clean_jumps[-1] >= 14.0:
                    clean_jumps.append(jx)
            routes[heid]['crossings'][hidx] = clean_jumps


def _compute_label_pos(pts, crossings):
    if len(pts) < 2:
        return (0.0, 0.0)
    if len(pts) == 2:
        return ((pts[0][0] + pts[1][0]) / 2.0, (pts[0][1] + pts[1][1]) / 2.0)

    best_idx = 1 if len(pts) >= 4 else 0
    best_len = -1.0
    start_i = 1 if len(pts) >= 4 else 0
    end_i = len(pts) - 2 if len(pts) >= 4 else len(pts) - 1
    for i in range(start_i, end_i):
        seg_len = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
        if seg_len > best_len:
            best_len = seg_len
            best_idx = i

    a, b = pts[best_idx], pts[best_idx + 1]
    mx = (a[0] + b[0]) / 2.0
    my = (a[1] + b[1]) / 2.0
    jumps = crossings.get(best_idx, ())
    if jumps and abs(a[1] - b[1]) < 0.25:
        for jx in jumps:
            if abs(mx - jx) < 20.0:
                shift = 22.0 if mx + 22.0 < max(a[0], b[0]) - 16.0 else -22.0
                if min(a[0], b[0]) + 14.0 <= mx + shift <= max(a[0], b[0]) - 14.0:
                    mx += shift
                break
    return (round(mx, 2), round(my, 2))


def enrutar_todas_aristas(datos, mostrar_marcos=True):
    """Calcula rutas ortogonales sin colisiones ni cruces ambiguos para ``datos``."""
    nodes = datos.get('nodes') or []
    sections = datos.get('sections') or []
    edges = datos.get('edges') or []

    node_map = {n['id']: n for n in nodes}
    node_boxes_all = {n['id']: _box_for_node(n, NODE_PAD) for n in nodes}
    endpoint_boxes_all = {n['id']: _box_for_node(n, ENDPOINT_NODE_PAD) for n in nodes}
    sec_boxes_all = (
        {s['id']: _box_for_section(s) for s in sections}
        if mostrar_marcos else {}
    )

    nodes_by_sec = {}
    for n in nodes:
        nodes_by_sec.setdefault(n.get('section_id'), []).append(n)

    sec_map = {s['id']: s for s in sections}
    base_xs, base_ys = _build_base_grid(nodes, sections if mostrar_marcos else [])

    # Ordenar: aristas internas primero (cortas), luego cruzadas por distancia ascendente
    valid_edges = [
        e for e in edges
        if e.get('from') in node_map and e.get('to') in node_map
    ]

    def _edge_sort_key(edge):
        src = node_map[edge['from']]
        dst = node_map[edge['to']]
        is_cross = 1 if (src.get('section_id') != dst.get('section_id')) else 0
        p1 = _anchor_point(src, edge.get('fromAnchor', 'bottom'))
        p2 = _anchor_point(dst, edge.get('toAnchor', 'top'))
        dist = abs(p1[0] - p2[0]) + abs(p1[1] - p2[1])
        return (is_cross, dist, edge.get('id', ''))

    sorted_edges = sorted(valid_edges, key=_edge_sort_key)

    routes = {}
    meta_by_id = {}
    used_h_segs = {}
    used_v_segs = {}

    def record_used_segments(pts, group_key):
        for a, b in zip(pts[1:-1], pts[2:-1]):
            if abs(a[1] - b[1]) < 0.25:
                lo, hi = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
                used_h_segs.setdefault(round(a[1], 1), []).append((lo, hi, group_key))
            elif abs(a[0] - b[0]) < 0.25:
                lo, hi = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
                used_v_segs.setdefault(round(a[0], 1), []).append((lo, hi, group_key))

    for edge in sorted_edges:
        eid = edge['id']
        src = node_map[edge['from']]
        dst = node_map[edge['to']]
        group_key = f"{edge['from']}:{edge.get('fromAnchor', 'bottom')}"
        routing = edge.get('routing', 'orthogonal')
        meta_by_id[eid] = {'group': group_key, 'routing': routing}

        if routing == 'straight':
            p1 = _anchor_point(src, edge.get('fromAnchor', 'bottom'))
            p2 = _anchor_point(dst, edge.get('toAnchor', 'top'))
            pts = [p1, p2]
            routes[eid] = {
                'points': pts,
                'label_pos': _compute_label_pos(pts, {}),
                'crossings': {},
            }
            continue

        fast_pts, p1, s1, s2, p2 = _fast_orthogonal_points(src, dst, edge)

        other_node_boxes = [
            box for nid, box in node_boxes_all.items()
            if nid not in (src['id'], dst['id'])
        ]
        is_cross_sec = bool(
            mostrar_marcos
            and (src.get('section_id') != dst.get('section_id'))
        )
        other_sec_boxes = (
            [
                box for sid, box in sec_boxes_all.items()
                if sid not in (src.get('section_id'), dst.get('section_id'))
            ]
            if is_cross_sec else []
        )

        # Verificar si la ruta rápida es 100% libre y no choca con carril ajeno
        fast_ok = True
        ep_src_box = endpoint_boxes_all[src['id']]
        ep_dst_box = endpoint_boxes_all[dst['id']]
        interior_fast = list(zip(fast_pts[1:-1], fast_pts[2:-1])) if len(fast_pts) >= 4 else []

        if _pt_in_box(s1, ep_src_box) or _pt_in_box(s2, ep_dst_box):
            fast_ok = False
        if fast_ok:
            for a, b in list(zip(fast_pts, fast_pts[1:])):
                if any(_axis_seg_hits_box(a, b, nb) for nb in other_node_boxes):
                    fast_ok = False
                    break
                if any(_axis_seg_hits_box(a, b, sb) for sb in other_sec_boxes):
                    fast_ok = False
                    break
        if fast_ok:
            for a, b in interior_fast:
                if _axis_seg_hits_box(a, b, ep_src_box) or _axis_seg_hits_box(a, b, ep_dst_box):
                    fast_ok = False
                    break
                if abs(a[1] - b[1]) < 0.25:
                    lo, hi = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
                    if any(
                        ug != group_key and min(hi, uhi) - max(lo, ulo) > 4.0
                        for ulo, uhi, ug in used_h_segs.get(round(a[1], 1), ())
                    ):
                        fast_ok = False
                        break
                elif abs(a[0] - b[0]) < 0.25:
                    lo, hi = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
                    if any(
                        ug != group_key and min(hi, uhi) - max(lo, ulo) > 4.0
                        for ulo, uhi, ug in used_v_segs.get(round(a[0], 1), ())
                    ):
                        fast_ok = False
                        break

        if fast_ok:
            chosen = fast_pts
        else:
            lxs, lys = _edge_local_grid(
                base_xs, base_ys, nodes_by_sec, sec_map, src, dst, s1, s2,
            )
            ep_boxes = [ep_src_box, ep_dst_box]
            astar_path = _run_astar(
                lxs, lys, s1, s2, other_node_boxes, ep_boxes, other_sec_boxes,
                used_h_segs, used_v_segs, group_key,
                allow_section_cross=False, allow_node_cross=False,
            )
            if astar_path is None and other_sec_boxes:
                astar_path = _run_astar(
                    lxs, lys, s1, s2, other_node_boxes, ep_boxes, other_sec_boxes,
                    used_h_segs, used_v_segs, group_key,
                    allow_section_cross=True, allow_node_cross=False,
                )
            if astar_path is None:
                astar_path = _run_astar(
                    lxs, lys, s1, s2, other_node_boxes, ep_boxes, other_sec_boxes,
                    used_h_segs, used_v_segs, group_key,
                    allow_section_cross=True, allow_node_cross=True,
                )
            if astar_path is not None:
                chosen = _clean_points([p1] + astar_path + [p2])
            else:
                chosen = fast_pts

        record_used_segments(chosen, group_key)
        routes[eid] = {
            'points': chosen,
            'label_pos': (0.0, 0.0),
            'crossings': {},
        }

    edge_lookup = {e['id']: e for e in valid_edges}

    def _other_nodes_for(eid):
        ed = edge_lookup[eid]
        return [
            box for nid, box in node_boxes_all.items()
            if nid not in (ed['from'], ed['to'])
        ]

    def _other_secs_for(eid):
        if not mostrar_marcos:
            return []
        ed = edge_lookup[eid]
        s_sec = node_map[ed['from']].get('section_id')
        d_sec = node_map[ed['to']].get('section_id')
        if s_sec == d_sec:
            return []
        return [
            box for sid, box in sec_boxes_all.items()
            if sid not in (s_sec, d_sec)
        ]

    _nudge_parallel_lanes(routes, meta_by_id, _other_nodes_for, _other_secs_for)
    _detect_crossings(routes, meta_by_id)

    for eid, geom in routes.items():
        geom['label_pos'] = _compute_label_pos(geom['points'], geom['crossings'])

    return routes
