"""Constructores deterministas de Mapas de Procesos Oficiales por empresa.

Replica la arquitectura modular de ``organigrama_oficial.py`` mediante un
registro por código de empresa (``MAPAS_PROCESOS_OFICIALES``) para incorporar
progresivamente FS, FP, PTH y PTCA sin alterar el motor ni el esquema JSON.
"""
from .organigrama import validar_datos_organigrama


def _make_section(sec_id, title, x, y, w, h, color, order, empresa='FS', departamento_id=None):
    return {
        'id': sec_id,
        'title': title,
        'empresa': empresa,
        'departamento_id': departamento_id,
        'x': x,
        'y': y,
        'w': w,
        'h': h,
        'color': color,
        'order': order,
    }


def _make_node(
    node_id,
    section_id,
    text,
    x,
    y,
    w=180,
    h=80,
    shape='rounded',
    bg_color='#FFFFFF',
    border_color='#0284C7',
    text_color='#0F172A',
    border_width=2,
    border_style='solid',
    border_radius=8,
    bold=False,
    font_size=12,
    z_index=2,
):
    return {
        'id': node_id,
        'section_id': section_id,
        'shape': shape,
        'x': x,
        'y': y,
        'w': w,
        'h': h,
        'text': text,
        'zIndex': z_index,
        'style': {
            'bgColor': bg_color,
            'textColor': text_color,
            'borderColor': border_color,
            'borderWidth': border_width,
            'borderStyle': border_style,
            'borderRadius': border_radius,
            'fontWeight': 'bold' if bold else 'normal',
            'fontStyle': 'normal',
            'fontSize': font_size,
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
    color='#0284C7',
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


def construir_mapa_procesos_fs(deptos_por_codigo=None):
    """Construye el Mapa de Procesos oficial de Fuente de Soda Policlínica C.A. (FS-MAP-001)."""
    deptos = deptos_por_codigo or {}
    depto_fs = deptos.get('FS')

    sections = [
        _make_section(
            'sec_fs_clientes_entrada', 'Clientes (Entradas)',
            x=40, y=180, w=220, h=660, color='#334155', order=0,
            empresa='FS', departamento_id=depto_fs,
        ),
        _make_section(
            'sec_fs_direccion', 'Procesos de Responsabilidad de la Dirección',
            x=720, y=160, w=440, h=140, color='#1D4ED8', order=1,
            empresa='FS', departamento_id=depto_fs,
        ),
        _make_section(
            'sec_fs_apoyo', '7 – 8.4 Procesos de Apoyo',
            x=360, y=380, w=620, h=240, color='#0E7490', order=2,
            empresa='FS', departamento_id=depto_fs,
        ),
        _make_section(
            'sec_fs_gestion', '9 – 10. Procesos de Gestión (Medición, Análisis y Mejora)',
            x=1120, y=380, w=620, h=140, color='#4F46E5', order=3,
            empresa='FS', departamento_id=depto_fs,
        ),
        _make_section(
            'sec_fs_medulares', '8. Procesos Medulares u Operativos',
            x=420, y=700, w=920, h=140, color='#0284C7', order=4,
            empresa='FS', departamento_id=depto_fs,
        ),
        _make_section(
            'sec_fs_clientes_salida', 'Clientes (Salidas)',
            x=1840, y=180, w=220, h=660, color='#334155', order=5,
            empresa='FS', departamento_id=depto_fs,
        ),
        _make_section(
            'sec_fs_leyenda', 'Leyenda',
            x=40, y=920, w=820, h=100, color='#64748B', order=6,
            empresa='FS', departamento_id=depto_fs,
        ),
    ]

    nodes = [
        # Banner institucional superior (sin sección)
        _make_node(
            'fs_banner', None,
            'MAPA DE PROCESOS DE FUENTE DE SODA POLICLÍNICA C.A.\nMejora Continua del Sistema de Gestión de la Calidad',
            x=560, y=40, w=980, h=60, shape='pill',
            bg_color='#0F172A', border_color='#0F172A', text_color='#FFFFFF',
            bold=True, font_size=14,
        ),
        # Clientes (Entradas)
        _make_node(
            'fs_cli_in', 'sec_fs_clientes_entrada', 'Clientes',
            x=60, y=240, w=180, h=60, shape='pill',
            bg_color='#F8FAFC', border_color='#334155', bold=True,
        ),
        _make_node(
            'fs_req', 'sec_fs_clientes_entrada', 'Requisitos',
            x=60, y=740, w=180, h=80, shape='rounded',
            bg_color='#FFFFFF', border_color='#2563EB', bold=True,
        ),
        # Responsabilidad de la Dirección
        _make_node(
            'fs_p01', 'sec_fs_direccion', '4 - 6\n1. Planificación\nEstratégica',
            x=740, y=200, w=190, h=80, shape='rounded',
            bg_color='#EFF6FF', border_color='#1D4ED8',
        ),
        _make_node(
            'fs_p02', 'sec_fs_direccion', '9.3\n2. Revisión por la\nDirección',
            x=950, y=200, w=190, h=80, shape='rounded',
            bg_color='#EFF6FF', border_color='#1D4ED8',
        ),
        # Procesos de Apoyo (todos interactúan con todos -> *)
        _make_node(
            'fs_p03', 'sec_fs_apoyo', '3. Provisión de\nRecursos Financieros *',
            x=380, y=420, w=180, h=80, shape='rounded',
            bg_color='#ECFEFF', border_color='#0E7490',
        ),
        _make_node(
            'fs_p04', 'sec_fs_apoyo', '4. Provisión de\nRecursos Humanos *',
            x=580, y=420, w=180, h=80, shape='rounded',
            bg_color='#ECFEFF', border_color='#0E7490',
        ),
        _make_node(
            'fs_p05', 'sec_fs_apoyo', '5. Provisión de\nInfraestructura *',
            x=780, y=420, w=180, h=80, shape='rounded',
            bg_color='#ECFEFF', border_color='#0E7490',
        ),
        _make_node(
            'fs_p06', 'sec_fs_apoyo', '6. Provisión de Ambiente\npara las Operaciones *',
            x=380, y=520, w=180, h=80, shape='rounded',
            bg_color='#ECFEFF', border_color='#0E7490',
        ),
        _make_node(
            'fs_p07', 'sec_fs_apoyo', '7. Provisión\nExterna *',
            x=580, y=520, w=180, h=80, shape='rounded',
            bg_color='#ECFEFF', border_color='#0E7490',
        ),
        # Procesos de Gestión
        _make_node(
            'fs_p12', 'sec_fs_gestion', '12. Gestión de la\nMejora Continua',
            x=1140, y=420, w=180, h=80, shape='rounded',
            bg_color='#EEF2FF', border_color='#4F46E5',
        ),
        _make_node(
            'fs_p13', 'sec_fs_gestion', '13. Satisfacción\nal Cliente *',
            x=1340, y=420, w=180, h=80, shape='rounded',
            bg_color='#EEF2FF', border_color='#4F46E5',
        ),
        _make_node(
            'fs_p14', 'sec_fs_gestion', '14. Auditoría *',
            x=1540, y=420, w=180, h=80, shape='rounded',
            bg_color='#EEF2FF', border_color='#4F46E5',
        ),
        # Procesos Medulares u Operativos
        _make_node(
            'fs_p08', 'sec_fs_medulares', '8. Almacenamiento',
            x=440, y=740, w=190, h=80, shape='rounded',
            bg_color='#E0F2FE', border_color='#0284C7',
        ),
        _make_node(
            'fs_p09', 'sec_fs_medulares', '9. Recepción del\nCliente y Facturación',
            x=670, y=740, w=190, h=80, shape='rounded',
            bg_color='#E0F2FE', border_color='#0284C7',
        ),
        _make_node(
            'fs_p10', 'sec_fs_medulares', '10. Preparación\nde Alimentos',
            x=900, y=740, w=190, h=80, shape='rounded',
            bg_color='#E0F2FE', border_color='#0284C7',
        ),
        _make_node(
            'fs_p11', 'sec_fs_medulares', '11. Entrega de\nAlimentos',
            x=1130, y=740, w=190, h=80, shape='rounded',
            bg_color='#E0F2FE', border_color='#0284C7',
        ),
        # Entregable / Salida de los medulares (sin sección)
        _make_node(
            'fs_alimentos', None, 'Alimentos\nPreparados',
            x=1440, y=740, w=180, h=80, shape='rounded',
            bg_color='#DBEAFE', border_color='#2563EB', bold=True,
        ),
        # Clientes (Salidas)
        _make_node(
            'fs_cli_out', 'sec_fs_clientes_salida', 'Clientes',
            x=1860, y=240, w=180, h=60, shape='pill',
            bg_color='#F8FAFC', border_color='#334155', bold=True,
        ),
        _make_node(
            'fs_sat', 'sec_fs_clientes_salida', 'Satisfacción',
            x=1860, y=420, w=180, h=80, shape='rounded',
            bg_color='#FFFFFF', border_color='#0F766E', bold=True,
        ),
        # Leyenda
        _make_node(
            'fs_leg_valor', 'sec_fs_leyenda', '━━▶  Actividades que Aportan Valor',
            x=60, y=950, w=240, h=50, shape='rounded',
            bg_color='#EFF6FF', border_color='#2563EB', border_width=3,
            bold=True, font_size=11,
        ),
        _make_node(
            'fs_leg_info', 'sec_fs_leyenda', '◀ - - ▶  Flujo de Información',
            x=320, y=950, w=240, h=50, shape='rounded',
            bg_color='#F8FAFC', border_color='#334155', border_width=2,
            border_style='dashed', bold=True, font_size=11,
        ),
        _make_node(
            'fs_leg_star', 'sec_fs_leyenda', '*  Procesos que interactúan con todos',
            x=580, y=950, w=260, h=50, shape='rounded',
            bg_color='#FEFCE8', border_color='#CA8A04', border_width=2,
            bold=True, font_size=11,
        ),
    ]

    edges = [
        _make_edge(
            'e_fs_info_in', 'fs_cli_in', 'fs_p01',
            from_anchor='right', to_anchor='left',
            style='dashed', color='#334155', width=2,
            label='Flujo de Información (↔)',
        ),
        _make_edge(
            'e_fs_entradas', 'fs_req', 'fs_p08',
            from_anchor='right', to_anchor='left',
            style='solid', color='#2563EB', width=3,
            label='Entradas',
        ),
        _make_edge(
            'e_fs_op_8_9', 'fs_p08', 'fs_p09',
            from_anchor='right', to_anchor='left',
            style='solid', color='#0284C7', width=2,
        ),
        _make_edge(
            'e_fs_op_9_10', 'fs_p09', 'fs_p10',
            from_anchor='right', to_anchor='left',
            style='solid', color='#0284C7', width=2,
        ),
        _make_edge(
            'e_fs_op_10_11', 'fs_p10', 'fs_p11',
            from_anchor='right', to_anchor='left',
            style='solid', color='#0284C7', width=2,
        ),
        _make_edge(
            'e_fs_prod', 'fs_p11', 'fs_alimentos',
            from_anchor='right', to_anchor='left',
            style='solid', color='#2563EB', width=3,
        ),
        _make_edge(
            'e_fs_salida', 'fs_alimentos', 'fs_sat',
            from_anchor='right', to_anchor='bottom',
            style='solid', color='#2563EB', width=3,
            label='Salida',
        ),
        _make_edge(
            'e_fs_info_out', 'fs_p14', 'fs_sat',
            from_anchor='right', to_anchor='left',
            style='dashed', color='#334155', width=2,
            label='Flujo de Información (↔)',
        ),
        _make_edge(
            'e_fs_ciclo_dir_apoyo', 'fs_p01', 'fs_p05',
            from_anchor='bottom', to_anchor='top',
            style='solid', color='#2563EB', width=3,
        ),
        _make_edge(
            'e_fs_ciclo_apoyo_med', 'fs_p07', 'fs_p09',
            from_anchor='bottom', to_anchor='top',
            style='solid', color='#2563EB', width=3,
        ),
        _make_edge(
            'e_fs_ciclo_med_gest', 'fs_p11', 'fs_p12',
            from_anchor='top', to_anchor='bottom',
            style='solid', color='#2563EB', width=3,
        ),
        _make_edge(
            'e_fs_ciclo_gest_dir', 'fs_p12', 'fs_p02',
            from_anchor='top', to_anchor='right',
            style='solid', color='#2563EB', width=3,
        ),
    ]

    datos = {
        'schema_version': 1,
        'settings': {'gridSize': 20, 'snapToGrid': True},
        'sections': sections,
        'nodes': nodes,
        'edges': edges,
    }
    return validar_datos_organigrama(datos)


MAPAS_PROCESOS_OFICIALES = {
    'FS': construir_mapa_procesos_fs,
}


def construir_mapa_procesos_oficial(empresa, deptos_por_codigo=None):
    """Construye y valida el Mapa de Procesos oficial para ``empresa``."""
    emp = (empresa or '').strip().upper()
    builder = MAPAS_PROCESOS_OFICIALES.get(emp)
    if builder is None:
        disponibles = ', '.join(sorted(MAPAS_PROCESOS_OFICIALES)) or '(ninguna)'
        raise ValueError(
            f'No hay constructor oficial registrado para la empresa {empresa!r}. '
            f'Disponibles: {disponibles}.'
        )
    return builder(deptos_por_codigo=deptos_por_codigo)
