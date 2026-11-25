"""Generador nativo puro en Python de archivos Microsoft Visio (.vsdx / OPC).

Construye paquetes OPC/OOXML compatibles con Visio 2013+/16 y LibreOffice Draw
(libvisio) sin dependencias externas adicionales ni mutaciones de archivos en
disco. Invierte correctamente el eje Y del lienzo web (origen superior
izquierdo en px) al sistema cartesiano de Visio (origen inferior izquierdo en
pulgadas, 96 px = 1 pulgada).
"""
from datetime import datetime, timezone
import io
import zipfile

from lxml import etree

PX_PER_INCH = 96.0

VISIO_NS = 'http://schemas.microsoft.com/office/visio/2012/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_RELS_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
CP_NS = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
DC_NS = 'http://purl.org/dc/elements/1.1/'
DCTERMS_NS = 'http://purl.org/dc/terms/'
XSI_NS = 'http://www.w3.org/2001/XMLSchema-instance'
EP_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'
XML_NS = 'http://www.w3.org/XML/1998/namespace'

REL_VISIO_DOC = 'http://schemas.microsoft.com/visio/2010/relationships/document'
REL_VISIO_PAGES = 'http://schemas.microsoft.com/visio/2010/relationships/pages'
REL_VISIO_PAGE = 'http://schemas.microsoft.com/visio/2010/relationships/page'
REL_VISIO_WINDOWS = 'http://schemas.microsoft.com/visio/2010/relationships/windows'
REL_CORE_PROPS = 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties'
REL_EXT_PROPS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties'

ANCHOR_TO_IX = {'top': 0, 'right': 1, 'bottom': 2, 'left': 3}
BORDER_STYLE_PATTERN = {'solid': 1, 'dashed': 2, 'dotted': 3}
TEXT_ALIGN_VAL = {'left': 0, 'center': 1, 'right': 2}


def _v_tag(local):
    return f'{{{VISIO_NS}}}{local}'


def _r_tag(local):
    return f'{{{R_NS}}}{local}'


def _to_in(px):
    return float(px) / PX_PER_INCH


def _fmt(val):
    formatted = f'{float(val):.6f}'.rstrip('0').rstrip('.')
    return '0' if formatted in ('', '-0') else formatted


def _view_scale(page_w_in):
    return min(1.0, max(0.15, 14.0 / max(float(page_w_in), 1.0)))


def _parse_hex_rgb(hex_color):
    if not hex_color or not isinstance(hex_color, str) or len(hex_color) != 7 or hex_color[0] != '#':
        return None
    try:
        return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    except ValueError:
        return None


def _hex_to_rgb_formula(hex_color):
    rgb = _parse_hex_rgb(hex_color)
    if rgb is None:
        return None
    return f'RGB({rgb[0]},{rgb[1]},{rgb[2]})'


def _mix_hex_with_white(hex_color, tint_ratio=0.08):
    """Mezcla un color #RRGGBB con blanco (tint_ratio del color original)."""
    rgb = _parse_hex_rgb(hex_color)
    if rgb is None:
        return '#EEF2FF'
    r, g, b = rgb
    w = 1.0 - tint_ratio
    nr = min(255, max(0, int(round(255 * w + r * tint_ratio))))
    ng = min(255, max(0, int(round(255 * w + g * tint_ratio))))
    nb = min(255, max(0, int(round(255 * w + b * tint_ratio))))
    return f'#{nr:02X}{ng:02X}{nb:02X}'


def _add_cell(parent, name, value, formula=None, unit=None):
    cell = etree.SubElement(parent, _v_tag('Cell'))
    cell.set('N', name)
    cell.set('V', str(value))
    if formula is not None:
        cell.set('F', formula)
    if unit is not None:
        cell.set('U', unit)
    return cell


def _add_color_cell(parent, name, hex_color):
    formula = _hex_to_rgb_formula(hex_color)
    if formula is not None:
        return _add_cell(parent, name, hex_color.upper(), formula=formula)
    return _add_cell(parent, name, hex_color)


def _add_geom_row(geom_sec, ix, row_type, x_in, y_in, fx=None, fy=None):
    row = etree.SubElement(geom_sec, _v_tag('Row'))
    row.set('T', row_type)
    row.set('IX', str(ix))
    _add_cell(row, 'X', _fmt(x_in), formula=fx)
    _add_cell(row, 'Y', _fmt(y_in), formula=fy)
    return row


def _serialize_xml(root):
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _serialize_visio_xml(root):
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8')


def _build_content_types():
    root = etree.Element(f'{{{CT_NS}}}Types', nsmap={None: CT_NS})
    for ext, ctype in (
        ('rels', 'application/vnd.openxmlformats-package.relationships+xml'),
        ('xml', 'application/xml'),
    ):
        el = etree.SubElement(root, f'{{{CT_NS}}}Default')
        el.set('Extension', ext)
        el.set('ContentType', ctype)
    for part, ctype in (
        ('/visio/document.xml', 'application/vnd.ms-visio.drawing.main+xml'),
        ('/visio/pages/pages.xml', 'application/vnd.ms-visio.pages+xml'),
        ('/visio/pages/page1.xml', 'application/vnd.ms-visio.page+xml'),
        ('/visio/windows.xml', 'application/vnd.ms-visio.windows+xml'),
        ('/docProps/core.xml', 'application/vnd.openxmlformats-package.core-properties+xml'),
        ('/docProps/app.xml', 'application/vnd.openxmlformats-officedocument.extended-properties+xml'),
    ):
        el = etree.SubElement(root, f'{{{CT_NS}}}Override')
        el.set('PartName', part)
        el.set('ContentType', ctype)
    return _serialize_xml(root)


def _build_rels(items):
    root = etree.Element(f'{{{PKG_RELS_NS}}}Relationships', nsmap={None: PKG_RELS_NS})
    for rid, rel_type, target in items:
        el = etree.SubElement(root, f'{{{PKG_RELS_NS}}}Relationship')
        el.set('Id', rid)
        el.set('Type', rel_type)
        el.set('Target', target)
    return _serialize_xml(root)


def _build_core_props(meta):
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    nsmap = {'cp': CP_NS, 'dc': DC_NS, 'dcterms': DCTERMS_NS, 'xsi': XSI_NS}
    root = etree.Element(f'{{{CP_NS}}}coreProperties', nsmap=nsmap)

    codigo = (meta or {}).get('codigo') or 'SGC-ORG-001'
    nombre = (meta or {}).get('nombre') or 'Organigrama Corporativo'
    title = etree.SubElement(root, f'{{{DC_NS}}}title')
    title.text = f'{codigo} — {nombre}'

    creator = etree.SubElement(root, f'{{{DC_NS}}}creator')
    creator.text = (meta or {}).get('usuario') or 'POLIDOC'

    mod_by = etree.SubElement(root, f'{{{CP_NS}}}lastModifiedBy')
    mod_by.text = 'POLIDOC'

    for tag in ('created', 'modified'):
        dt = etree.SubElement(root, f'{{{DCTERMS_NS}}}{tag}')
        dt.set(f'{{{XSI_NS}}}type', 'dcterms:W3CDTF')
        dt.text = now_iso

    return _serialize_xml(root)


def _build_app_props():
    root = etree.Element(f'{{{EP_NS}}}Properties', nsmap={None: EP_NS})
    for tag, val in (
        ('Application', 'POLIDOC'),
        ('ScaleCrop', 'false'),
        ('Company', 'Policlínica Táchira C.A.'),
        ('LinksUpToDate', 'false'),
        ('SharedDoc', 'false'),
        ('HyperlinksChanged', 'false'),
    ):
        el = etree.SubElement(root, f'{{{EP_NS}}}{tag}')
        el.text = val
    return _serialize_xml(root)


def _build_document_xml():
    nsmap = {None: VISIO_NS, 'r': R_NS}
    root = etree.Element(_v_tag('VisioDocument'), nsmap=nsmap)
    root.set(f'{{{XML_NS}}}space', 'preserve')

    doc_settings = etree.SubElement(root, _v_tag('DocumentSettings'))
    doc_settings.set('TopPage', '0')
    doc_settings.set('DefaultTextStyle', '1')
    doc_settings.set('DefaultLineStyle', '2')
    doc_settings.set('DefaultFillStyle', '1')
    doc_settings.set('DefaultGuideStyle', '1')
    for tag, val in (
        ('GlueSettings', '9'),
        ('SnapSettings', '295'),
        ('SnapExtensions', '34'),
        ('SnapAngles', None),
        ('DynamicGridEnabled', '1'),
        ('ProtectStyles', '0'),
        ('ProtectShapes', '0'),
        ('ProtectMasters', '0'),
        ('ProtectBkgnds', '0'),
    ):
        el = etree.SubElement(doc_settings, _v_tag(tag))
        if val is not None:
            el.text = val

    colors = etree.SubElement(root, _v_tag('Colors'))
    for ix, rgb in ((24, '#000000'), (25, '#FFFFFF'), (26, '#2563EB'), (27, '#0F172A'), (28, '#CBD5E1')):
        entry = etree.SubElement(colors, _v_tag('ColorEntry'))
        entry.set('IX', str(ix))
        entry.set('RGB', rgb)

    faces = etree.SubElement(root, _v_tag('FaceNames'))
    for fname, uranges, charsets, panose, flags in (
        ('Calibri', '-469750017 -1073732485 9 0', '536871423 0', '2 15 5 2 2 2 4 3 2 4', '325'),
        ('Arial', '-536859905 -1073711013 9 0', '536871423 0', '2 11 6 4 2 2 2 2 2 4', '325'),
    ):
        face = etree.SubElement(faces, _v_tag('FaceName'))
        face.set('NameU', fname)
        face.set('UnicodeRanges', uranges)
        face.set('CharSets', charsets)
        face.set('Panose', panose)
        face.set('Flags', flags)

    sheets = etree.SubElement(root, _v_tag('StyleSheets'))

    # StyleSheet 0: No Style (base completa requerida por Visio 16)
    ss0 = etree.SubElement(sheets, _v_tag('StyleSheet'))
    ss0.set('ID', '0')
    ss0.set('NameU', 'No Style')
    ss0.set('Name', 'No Style')
    _add_cell(ss0, 'EnableLineProps', '1')
    _add_cell(ss0, 'EnableFillProps', '1')
    _add_cell(ss0, 'EnableTextProps', '1')
    _add_cell(ss0, 'LineWeight', _fmt(_to_in(1)), unit='IN')
    _add_color_cell(ss0, 'LineColor', '#0F172A')
    _add_cell(ss0, 'LinePattern', '1')
    _add_cell(ss0, 'Rounding', '0')
    _add_cell(ss0, 'BeginArrow', '0')
    _add_cell(ss0, 'EndArrow', '0')
    _add_cell(ss0, 'BeginArrowSize', '2')
    _add_cell(ss0, 'EndArrowSize', '2')
    _add_color_cell(ss0, 'FillForegnd', '#FFFFFF')
    _add_color_cell(ss0, 'FillBkgnd', '#FFFFFF')
    _add_cell(ss0, 'FillPattern', '1')
    _add_cell(ss0, 'VerticalAlign', '1')

    char0 = etree.SubElement(ss0, _v_tag('Section'))
    char0.set('N', 'Character')
    r0 = etree.SubElement(char0, _v_tag('Row'))
    r0.set('IX', '0')
    _add_cell(r0, 'Font', 'Calibri')
    _add_color_cell(r0, 'Color', '#0F172A')
    _add_cell(r0, 'Style', '0')
    _add_cell(r0, 'Size', _fmt(_to_in(13)), unit='IN')
    _add_cell(r0, 'LangID', 'es-VE')

    para0 = etree.SubElement(ss0, _v_tag('Section'))
    para0.set('N', 'Paragraph')
    pr0 = etree.SubElement(para0, _v_tag('Row'))
    pr0.set('IX', '0')
    _add_cell(pr0, 'HorzAlign', '1')

    # StyleSheet 1: Normal (2-D)
    ss1 = etree.SubElement(sheets, _v_tag('StyleSheet'))
    ss1.set('ID', '1')
    ss1.set('NameU', 'Normal')
    ss1.set('Name', 'Normal')
    ss1.set('LineStyle', '0')
    ss1.set('FillStyle', '0')
    ss1.set('TextStyle', '0')
    _add_color_cell(ss1, 'FillForegnd', '#FFFFFF')
    _add_color_cell(ss1, 'FillBkgnd', '#FFFFFF')
    _add_cell(ss1, 'FillPattern', '1')
    _add_color_cell(ss1, 'LineColor', '#CBD5E1')
    _add_cell(ss1, 'LineWeight', _fmt(_to_in(2)), unit='IN')
    _add_cell(ss1, 'LinePattern', '1')
    _add_cell(ss1, 'VerticalAlign', '1')

    char1 = etree.SubElement(ss1, _v_tag('Section'))
    char1.set('N', 'Character')
    r1 = etree.SubElement(char1, _v_tag('Row'))
    r1.set('IX', '0')
    _add_cell(r1, 'Font', 'Calibri')
    _add_color_cell(r1, 'Color', '#0F172A')
    _add_cell(r1, 'Style', '0')
    _add_cell(r1, 'Size', _fmt(_to_in(13)), unit='IN')
    _add_cell(r1, 'LangID', 'es-VE')

    # StyleSheet 2: Connector (1-D)
    ss2 = etree.SubElement(sheets, _v_tag('StyleSheet'))
    ss2.set('ID', '2')
    ss2.set('NameU', 'Connector')
    ss2.set('Name', 'Connector')
    ss2.set('LineStyle', '0')
    ss2.set('FillStyle', '0')
    ss2.set('TextStyle', '0')
    _add_cell(ss2, 'FillPattern', '0')
    _add_color_cell(ss2, 'LineColor', '#64748B')
    _add_cell(ss2, 'LineWeight', _fmt(_to_in(2)), unit='IN')
    _add_cell(ss2, 'LinePattern', '1')

    doc_sheet = etree.SubElement(root, _v_tag('DocumentSheet'))
    doc_sheet.set('NameU', 'TheDoc')
    doc_sheet.set('Name', 'TheDoc')
    doc_sheet.set('LineStyle', '0')
    doc_sheet.set('FillStyle', '0')
    doc_sheet.set('TextStyle', '0')
    for cname, cval in (
        ('OutputFormat', '0'),
        ('LockPreview', '0'),
        ('AddMarkup', '0'),
        ('ViewMarkup', '0'),
        ('DocLangID', 'es-ES'),
    ):
        _add_cell(doc_sheet, cname, cval)

    return _serialize_visio_xml(root)


def _build_windows_xml(page_w_in, page_h_in):
    nsmap = {None: VISIO_NS, 'r': R_NS}
    root = etree.Element(_v_tag('Windows'), nsmap=nsmap)
    root.set(f'{{{XML_NS}}}space', 'preserve')
    root.set('ClientWidth', '1920')
    root.set('ClientHeight', '960')

    win = etree.SubElement(root, _v_tag('Window'))
    win.set('ID', '0')
    win.set('WindowType', 'Drawing')
    win.set('WindowState', '1073741824')
    win.set('WindowLeft', '-8')
    win.set('WindowTop', '-31')
    win.set('WindowWidth', '1936')
    win.set('WindowHeight', '999')
    win.set('ContainerType', 'Page')
    win.set('Page', '0')
    win.set('ViewScale', _fmt(_view_scale(page_w_in)))
    win.set('ViewCenterX', _fmt(page_w_in / 2.0))
    win.set('ViewCenterY', _fmt(page_h_in / 2.0))

    for tag, val in (
        ('ShowRulers', '1'),
        ('ShowGrid', '0'),
        ('ShowPageBreaks', '0'),
        ('ShowGuides', '1'),
        ('ShowConnectionPoints', '1'),
        ('GlueSettings', '9'),
        ('SnapSettings', '295'),
        ('SnapExtensions', '34'),
        ('SnapAngles', None),
        ('DynamicGridEnabled', '1'),
        ('TabSplitterPos', '0.5'),
    ):
        el = etree.SubElement(win, _v_tag(tag))
        if val is not None:
            el.text = val

    return _serialize_visio_xml(root)


def _build_pages_xml(page_w_in, page_h_in):
    nsmap = {None: VISIO_NS, 'r': R_NS}
    root = etree.Element(_v_tag('Pages'), nsmap=nsmap)
    root.set(f'{{{XML_NS}}}space', 'preserve')

    page = etree.SubElement(root, _v_tag('Page'))
    page.set('ID', '0')
    page.set('NameU', 'Organigrama')
    page.set('Name', 'Organigrama')
    page.set('ViewScale', _fmt(_view_scale(page_w_in)))
    page.set('ViewCenterX', _fmt(page_w_in / 2.0))
    page.set('ViewCenterY', _fmt(page_h_in / 2.0))

    ps = etree.SubElement(page, _v_tag('PageSheet'))
    ps.set('LineStyle', '0')
    ps.set('FillStyle', '0')
    ps.set('TextStyle', '0')
    _add_cell(ps, 'PageWidth', _fmt(page_w_in), unit='IN')
    _add_cell(ps, 'PageHeight', _fmt(page_h_in), unit='IN')
    _add_cell(ps, 'ShdwOffsetX', '0.125', unit='IN')
    _add_cell(ps, 'ShdwOffsetY', '-0.125', unit='IN')
    _add_cell(ps, 'PageScale', '1', unit='IN')
    _add_cell(ps, 'DrawingScale', '1', unit='IN')
    _add_cell(ps, 'DrawingSizeType', '4')
    _add_cell(ps, 'DrawingScaleType', '0')
    _add_cell(ps, 'InhibitSnap', '0')

    rel = etree.SubElement(page, _v_tag('Rel'))
    rel.set(_r_tag('id'), 'rId1')

    return _serialize_visio_xml(root)


def _append_rect_geometry(shape, w_in, h_in, no_fill=False, no_line=False):
    geom = etree.SubElement(shape, _v_tag('Section'))
    geom.set('N', 'Geometry')
    geom.set('IX', '0')
    _add_cell(geom, 'NoFill', '1' if no_fill else '0')
    _add_cell(geom, 'NoLine', '1' if no_line else '0')
    _add_cell(geom, 'NoShow', '0')
    _add_cell(geom, 'NoSnap', '0')
    _add_cell(geom, 'NoQuickDrag', '0')
    _add_geom_row(geom, 1, 'MoveTo', 0.0, 0.0, fx='Width*0', fy='Height*0')
    _add_geom_row(geom, 2, 'LineTo', w_in, 0.0, fx='Width*1', fy='Height*0')
    _add_geom_row(geom, 3, 'LineTo', w_in, h_in, fx='Width*1', fy='Height*1')
    _add_geom_row(geom, 4, 'LineTo', 0.0, h_in, fx='Width*0', fy='Height*1')
    _add_geom_row(geom, 5, 'LineTo', 0.0, 0.0, fx='Geometry1.X1', fy='Geometry1.Y1')


def _append_diamond_geometry(shape, w_in, h_in, no_fill=False, no_line=False):
    geom = etree.SubElement(shape, _v_tag('Section'))
    geom.set('N', 'Geometry')
    geom.set('IX', '0')
    _add_cell(geom, 'NoFill', '1' if no_fill else '0')
    _add_cell(geom, 'NoLine', '1' if no_line else '0')
    _add_cell(geom, 'NoShow', '0')
    _add_cell(geom, 'NoSnap', '0')
    _add_cell(geom, 'NoQuickDrag', '0')
    hw = w_in / 2.0
    hh = h_in / 2.0
    _add_geom_row(geom, 1, 'MoveTo', hw, 0.0, fx='Width*0.5', fy='Height*0')
    _add_geom_row(geom, 2, 'LineTo', w_in, hh, fx='Width*1', fy='Height*0.5')
    _add_geom_row(geom, 3, 'LineTo', hw, h_in, fx='Width*0.5', fy='Height*1')
    _add_geom_row(geom, 4, 'LineTo', 0.0, hh, fx='Width*0', fy='Height*0.5')
    _add_geom_row(geom, 5, 'LineTo', hw, 0.0, fx='Geometry1.X1', fy='Geometry1.Y1')


def _append_connections_section(shape, w_in, h_in):
    sec = etree.SubElement(shape, _v_tag('Section'))
    sec.set('N', 'Connection')
    coords = (
        (0, w_in / 2.0, h_in, 'Width*0.5', 'Height*1', 0, 1),    # top
        (1, w_in, h_in / 2.0, 'Width*1', 'Height*0.5', 1, 0),    # right
        (2, w_in / 2.0, 0.0, 'Width*0.5', 'Height*0', 0, -1),    # bottom
        (3, 0.0, h_in / 2.0, 'Width*0', 'Height*0.5', -1, 0),    # left
    )
    for ix, vx, vy, fx, fy, dx, dy in coords:
        row = etree.SubElement(sec, _v_tag('Row'))
        row.set('T', 'Connection')
        row.set('IX', str(ix))
        _add_cell(row, 'X', _fmt(vx), formula=fx, unit='IN')
        _add_cell(row, 'Y', _fmt(vy), formula=fy, unit='IN')
        _add_cell(row, 'DirX', str(dx))
        _add_cell(row, 'DirY', str(dy))
        _add_cell(row, 'Type', '0')
        _add_cell(row, 'AutoGen', '0')
        _add_cell(row, 'Prompt', '', formula='No Formula')


def _append_text_sections(shape, font_size_px, text_color, bold=False, italic=False, align='center'):
    style_bits = (1 if bold else 0) | (2 if italic else 0)
    char_sec = etree.SubElement(shape, _v_tag('Section'))
    char_sec.set('N', 'Character')
    c_row = etree.SubElement(char_sec, _v_tag('Row'))
    c_row.set('IX', '0')
    _add_cell(c_row, 'Font', 'Calibri')
    _add_color_cell(c_row, 'Color', text_color or '#0F172A')
    _add_cell(c_row, 'Style', str(style_bits))
    _add_cell(c_row, 'Size', _fmt(_to_in(font_size_px)), unit='IN')
    _add_cell(c_row, 'LangID', 'es-VE')

    para_sec = etree.SubElement(shape, _v_tag('Section'))
    para_sec.set('N', 'Paragraph')
    p_row = etree.SubElement(para_sec, _v_tag('Row'))
    p_row.set('IX', '0')
    _add_cell(p_row, 'HorzAlign', str(TEXT_ALIGN_VAL.get(align, 1)))


def _append_text_el(shape, text_val):
    t_el = etree.SubElement(shape, _v_tag('Text'))
    cp_el = etree.SubElement(t_el, _v_tag('cp'))
    cp_el.set('IX', '0')
    pp_el = etree.SubElement(t_el, _v_tag('pp'))
    pp_el.set('IX', '0')
    pp_el.tail = text_val
    return t_el


def _add_2d_box(shapes_el, shape_id, x_px, y_px, w_px, h_px, page_h_in, name_u):
    w_in = max(_to_in(w_px), 0.05)
    h_in = max(_to_in(h_px), 0.05)
    pin_x = _to_in(x_px + w_px / 2.0)
    pin_y = page_h_in - _to_in(y_px + h_px / 2.0)

    shape = etree.SubElement(shapes_el, _v_tag('Shape'))
    shape.set('ID', str(shape_id))
    shape.set('NameU', name_u)
    shape.set('Type', 'Shape')
    shape.set('LineStyle', '1')
    shape.set('FillStyle', '1')
    shape.set('TextStyle', '1')

    _add_cell(shape, 'PinX', _fmt(pin_x), unit='IN')
    _add_cell(shape, 'PinY', _fmt(pin_y), unit='IN')
    _add_cell(shape, 'Width', _fmt(w_in), unit='IN')
    _add_cell(shape, 'Height', _fmt(h_in), unit='IN')
    _add_cell(shape, 'LocPinX', _fmt(w_in * 0.5), formula='Width*0.5', unit='IN')
    _add_cell(shape, 'LocPinY', _fmt(h_in * 0.5), formula='Height*0.5', unit='IN')
    _add_cell(shape, 'Angle', '0')
    _add_cell(shape, 'FlipX', '0')
    _add_cell(shape, 'FlipY', '0')
    _add_cell(shape, 'ResizeMode', '0')
    _add_cell(shape, 'ObjType', '1')
    return shape, w_in, h_in


def _build_section_shapes(shapes_el, sec, next_id, page_h_in):
    color = sec.get('color') or '#2563EB'
    bg_tint = _mix_hex_with_white(color, 0.08)

    frame, fw_in, fh_in = _add_2d_box(
        shapes_el, next_id, sec['x'], sec['y'], sec['w'], sec['h'], page_h_in,
        f'SectionFrame_{next_id}',
    )
    _add_color_cell(frame, 'FillForegnd', bg_tint)
    _add_color_cell(frame, 'FillBkgnd', bg_tint)
    _add_cell(frame, 'FillPattern', '1')
    _add_color_cell(frame, 'LineColor', color)
    _add_cell(frame, 'LineWeight', _fmt(_to_in(2)), unit='IN')
    _add_cell(frame, 'LinePattern', '2')
    _add_cell(frame, 'Rounding', _fmt(_to_in(10)), unit='IN')
    _append_rect_geometry(frame, fw_in, fh_in)
    next_id += 1

    title_text = ((f"[{sec['empresa']}] " if sec.get('empresa') else '') + (sec.get('title') or 'Sección'))
    badge_w = min(max(110.0, len(title_text) * 7.2 + 24.0), max(110.0, float(sec['w']) - 16.0))
    badge_h = 24.0
    badge_x = float(sec['x']) + 12.0
    badge_y = float(sec['y']) - 12.0

    badge, bw_in, bh_in = _add_2d_box(
        shapes_el, next_id, badge_x, badge_y, badge_w, badge_h, page_h_in,
        f'SectionLabel_{next_id}',
    )
    _add_color_cell(badge, 'FillForegnd', color)
    _add_color_cell(badge, 'FillBkgnd', color)
    _add_cell(badge, 'FillPattern', '1')
    _add_color_cell(badge, 'LineColor', color)
    _add_cell(badge, 'LineWeight', _fmt(_to_in(1)), unit='IN')
    _add_cell(badge, 'LinePattern', '1')
    _add_cell(badge, 'Rounding', _fmt(_to_in(6)), unit='IN')
    _add_cell(badge, 'VerticalAlign', '1')
    _append_text_sections(badge, 10, '#FFFFFF', bold=True, italic=False, align='left')
    _append_rect_geometry(badge, bw_in, bh_in)
    _append_text_el(badge, title_text)
    next_id += 1

    return next_id


def _build_node_shape(shapes_el, node, shape_id, page_h_in, resolver_color_fn):
    st = node.get('style') or {}
    shape_kind = node.get('shape') or 'rounded'

    bg_color = resolver_color_fn(st.get('bgColor', 'auto'), 'bg')
    text_color = resolver_color_fn(st.get('textColor', 'auto'), 'text') or '#0F172A'
    border_color = resolver_color_fn(st.get('borderColor', 'auto'), 'border')
    b_width = float(st.get('borderWidth', 2))
    b_style = st.get('borderStyle', 'solid')

    box, w_in, h_in = _add_2d_box(
        shapes_el, shape_id, node['x'], node['y'], node['w'], node['h'], page_h_in,
        f'Node_{shape_id}',
    )

    if bg_color is None:
        _add_cell(box, 'FillPattern', '0')
        no_fill = True
    else:
        _add_color_cell(box, 'FillForegnd', bg_color)
        _add_color_cell(box, 'FillBkgnd', bg_color)
        _add_cell(box, 'FillPattern', '1')
        no_fill = False

    if b_width <= 0 or border_color is None:
        _add_cell(box, 'LinePattern', '0')
        _add_cell(box, 'LineWeight', '0', unit='IN')
        no_line = True
    else:
        _add_color_cell(box, 'LineColor', border_color)
        _add_cell(box, 'LineWeight', _fmt(_to_in(b_width)), unit='IN')
        _add_cell(box, 'LinePattern', str(BORDER_STYLE_PATTERN.get(b_style, 1)))
        no_line = False

    if shape_kind == 'pill':
        rounding = min(w_in, h_in) / 2.0
    elif shape_kind == 'rounded':
        max_r_px = min(float(node['w']), float(node['h'])) / 2.0
        rounding = _to_in(min(float(st.get('borderRadius', 10)), max_r_px))
    else:
        rounding = 0.0
    _add_cell(box, 'Rounding', _fmt(rounding), unit='IN')

    _add_cell(box, 'VerticalAlign', '1')
    for margin_cell, val in (
        ('LeftMargin', '0.06'), ('RightMargin', '0.06'),
        ('TopMargin', '0.04'), ('BottomMargin', '0.04'),
    ):
        _add_cell(box, margin_cell, val, unit='IN')

    _append_text_sections(
        box,
        st.get('fontSize', 13),
        text_color,
        bold=(st.get('fontWeight') == 'bold'),
        italic=(st.get('fontStyle') == 'italic'),
        align=st.get('textAlign', 'center'),
    )
    _append_connections_section(box, w_in, h_in)

    if shape_kind == 'diamond':
        _append_diamond_geometry(box, w_in, h_in, no_fill=no_fill, no_line=no_line)
    else:
        _append_rect_geometry(box, w_in, h_in, no_fill=no_fill, no_line=no_line)

    text_val = (node.get('text') or '').strip()
    if text_val:
        _append_text_el(box, text_val)


def _build_edge_shape(
    shapes_el, connects_el, edge, src_node, dst_node, src_sid, dst_sid,
    next_id, page_h_in, geom_arista_fn, resolver_color_fn, rutas_aristas=None,
):
    geom = (rutas_aristas or {}).get(edge.get('id')) or geom_arista_fn(src_node, dst_node, edge)
    pts_px = geom['points']
    if len(pts_px) < 2:
        return next_id

    v_pts = [(_to_in(px), page_h_in - _to_in(py)) for px, py in pts_px]
    bx, by = v_pts[0]
    ex, ey = v_pts[-1]
    dx = ex - bx
    dy = ey - by

    from_ix = ANCHOR_TO_IX.get(edge.get('fromAnchor', 'bottom'), 2)
    to_ix = ANCHOR_TO_IX.get(edge.get('toAnchor', 'top'), 0)

    conn_id = next_id
    next_id += 1

    shape = etree.SubElement(shapes_el, _v_tag('Shape'))
    shape.set('ID', str(conn_id))
    shape.set('NameU', f'Connector_{conn_id}')
    shape.set('Type', 'Shape')
    shape.set('LineStyle', '2')
    shape.set('FillStyle', '2')
    shape.set('TextStyle', '1')

    _add_cell(shape, 'PinX', _fmt((bx + ex) / 2.0), formula='GUARD((BeginX+EndX)/2)', unit='IN')
    _add_cell(shape, 'PinY', _fmt((by + ey) / 2.0), formula='GUARD((BeginY+EndY)/2)', unit='IN')
    _add_cell(shape, 'Width', _fmt(dx), formula='GUARD(EndX-BeginX)', unit='IN')
    _add_cell(shape, 'Height', _fmt(dy), formula='GUARD(EndY-BeginY)', unit='IN')
    _add_cell(shape, 'LocPinX', _fmt(dx * 0.5), formula='GUARD(Width*0.5)', unit='IN')
    _add_cell(shape, 'LocPinY', _fmt(dy * 0.5), formula='GUARD(Height*0.5)', unit='IN')
    _add_cell(shape, 'Angle', '0')
    _add_cell(shape, 'FlipX', '0')
    _add_cell(shape, 'FlipY', '0')
    _add_cell(shape, 'ResizeMode', '0')
    _add_cell(shape, 'ObjType', '2')
    _add_cell(shape, 'ConFixedCode', '3')

    beg_x_f = f'PAR(PNT(Sheet.{src_sid}!Connections.X{from_ix + 1},Sheet.{src_sid}!Connections.Y{from_ix + 1}))'
    end_x_f = f'PAR(PNT(Sheet.{dst_sid}!Connections.X{to_ix + 1},Sheet.{dst_sid}!Connections.Y{to_ix + 1}))'
    _add_cell(shape, 'BeginX', _fmt(bx), formula=beg_x_f, unit='IN')
    _add_cell(shape, 'BeginY', _fmt(by), formula=beg_x_f, unit='IN')
    _add_cell(shape, 'EndX', _fmt(ex), formula=end_x_f, unit='IN')
    _add_cell(shape, 'EndY', _fmt(ey), formula=end_x_f, unit='IN')
    _add_cell(shape, 'BegTrigger', '2', formula=f'_XFTRIGGER(Sheet.{src_sid}!EventXFMod)')
    _add_cell(shape, 'EndTrigger', '2', formula=f'_XFTRIGGER(Sheet.{dst_sid}!EventXFMod)')

    stroke = resolver_color_fn(edge.get('color', 'auto'), 'edge') or '#64748B'
    width_px = float(edge.get('width', 2))
    _add_cell(shape, 'FillPattern', '0')
    _add_color_cell(shape, 'LineColor', stroke)
    _add_cell(shape, 'LineWeight', _fmt(_to_in(width_px)), unit='IN')
    _add_cell(shape, 'LinePattern', str(BORDER_STYLE_PATTERN.get(edge.get('style', 'solid'), 1)))
    if edge.get('arrowEnd', True):
        _add_cell(shape, 'EndArrow', '4')
        _add_cell(shape, 'EndArrowSize', '2')
    else:
        _add_cell(shape, 'EndArrow', '0')

    geom_sec = etree.SubElement(shape, _v_tag('Section'))
    geom_sec.set('N', 'Geometry')
    geom_sec.set('IX', '0')
    _add_cell(geom_sec, 'NoFill', '1')
    _add_cell(geom_sec, 'NoLine', '0')
    _add_cell(geom_sec, 'NoShow', '0')
    _add_cell(geom_sec, 'NoSnap', '0')
    _add_cell(geom_sec, 'NoQuickDrag', '0')
    for idx, (vx, vy) in enumerate(v_pts, start=1):
        row_type = 'MoveTo' if idx == 1 else 'LineTo'
        _add_geom_row(geom_sec, idx, row_type, vx - bx, vy - by)

    # Glue <Connects>
    for from_cell, from_part, to_sheet, ix_val in (
        ('BeginX', '9', src_sid, from_ix),
        ('EndX', '12', dst_sid, to_ix),
    ):
        c_el = etree.SubElement(connects_el, _v_tag('Connect'))
        c_el.set('FromSheet', str(conn_id))
        c_el.set('FromCell', from_cell)
        c_el.set('FromPart', from_part)
        c_el.set('ToSheet', str(to_sheet))
        c_el.set('ToCell', f'Connections.X{ix_val + 1}')
        c_el.set('ToPart', str(100 + ix_val))

    # Etiqueta opcional de la arista como figura dedicada para máxima fidelidad
    label = (edge.get('label') or '').strip()
    if label:
        lx_px, ly_px = geom['label_pos']
        lw = max(36.0, len(label) * 6.8 + 14.0)
        lh = 20.0
        lbl_box, bw_in, bh_in = _add_2d_box(
            shapes_el, next_id, lx_px - lw / 2.0, ly_px - lh / 2.0, lw, lh, page_h_in,
            f'EdgeLabel_{next_id}',
        )
        _add_color_cell(lbl_box, 'FillForegnd', '#FFFFFF')
        _add_color_cell(lbl_box, 'FillBkgnd', '#FFFFFF')
        _add_cell(lbl_box, 'FillPattern', '1')
        _add_color_cell(lbl_box, 'LineColor', '#CBD5E1')
        _add_cell(lbl_box, 'LineWeight', _fmt(_to_in(1)), unit='IN')
        _add_cell(lbl_box, 'LinePattern', '1')
        _add_cell(lbl_box, 'Rounding', _fmt(_to_in(4)), unit='IN')
        _add_cell(lbl_box, 'VerticalAlign', '1')
        _append_text_sections(lbl_box, 10, '#0F172A', bold=True, italic=False, align='center')
        _append_rect_geometry(lbl_box, bw_in, bh_in)
        _append_text_el(lbl_box, label)
        next_id += 1

    return next_id


def _build_page1_xml(datos_filtrados, page_h_in, geom_arista_fn, resolver_color_fn, mostrar_marcos, rutas_aristas=None):
    nsmap = {None: VISIO_NS, 'r': R_NS}
    root = etree.Element(_v_tag('PageContents'), nsmap=nsmap)
    root.set(f'{{{XML_NS}}}space', 'preserve')
    shapes_el = etree.SubElement(root, _v_tag('Shapes'))
    connects_el = etree.Element(_v_tag('Connects'))

    next_id = 1

    if mostrar_marcos:
        secs = sorted(datos_filtrados.get('sections', []), key=lambda s: s.get('order', 0))
        for sec in secs:
            next_id = _build_section_shapes(shapes_el, sec, next_id, page_h_in)

    nodes_sorted = sorted(datos_filtrados.get('nodes', []), key=lambda n: n.get('zIndex', 1))
    node_map = {}
    node_shape_ids = {}
    for node in nodes_sorted:
        sid = next_id
        next_id += 1
        node_map[node['id']] = node
        node_shape_ids[node['id']] = sid
        _build_node_shape(shapes_el, node, sid, page_h_in, resolver_color_fn)

    rutas = rutas_aristas if rutas_aristas is not None else datos_filtrados.get('routes')
    for edge in datos_filtrados.get('edges', []):
        src_node = node_map.get(edge.get('from'))
        dst_node = node_map.get(edge.get('to'))
        if not src_node or not dst_node:
            continue
        next_id = _build_edge_shape(
            shapes_el, connects_el, edge,
            src_node, dst_node,
            node_shape_ids[src_node['id']], node_shape_ids[dst_node['id']],
            next_id, page_h_in, geom_arista_fn, resolver_color_fn,
            rutas_aristas=rutas,
        )

    if len(connects_el) > 0:
        root.append(connects_el)

    return _serialize_visio_xml(root)


def generar_vsdx(datos_filtrados, meta, geom_arista_fn, resolver_color_fn, mostrar_marcos=True, rutas_aristas=None):
    """Genera el binario ZIP/OPC de un archivo `.vsdx` editable en Microsoft Visio."""
    page_w_in = max(11.0, _to_in(datos_filtrados.get('width', 1056)))
    page_h_in = max(8.5, _to_in(datos_filtrados.get('height', 816)))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', _build_content_types())
        zf.writestr('_rels/.rels', _build_rels([
            ('rId1', REL_VISIO_DOC, 'visio/document.xml'),
            ('rId2', REL_CORE_PROPS, 'docProps/core.xml'),
            ('rId3', REL_EXT_PROPS, 'docProps/app.xml'),
        ]))
        zf.writestr('docProps/core.xml', _build_core_props(meta))
        zf.writestr('docProps/app.xml', _build_app_props())
        zf.writestr('visio/document.xml', _build_document_xml())
        zf.writestr('visio/_rels/document.xml.rels', _build_rels([
            ('rId1', REL_VISIO_PAGES, 'pages/pages.xml'),
            ('rId2', REL_VISIO_WINDOWS, 'windows.xml'),
        ]))
        zf.writestr('visio/windows.xml', _build_windows_xml(page_w_in, page_h_in))
        zf.writestr('visio/pages/pages.xml', _build_pages_xml(page_w_in, page_h_in))
        zf.writestr('visio/pages/_rels/pages.xml.rels', _build_rels([
            ('rId1', REL_VISIO_PAGE, 'page1.xml'),
        ]))
        zf.writestr(
            'visio/pages/page1.xml',
            _build_page1_xml(
                datos_filtrados, page_h_in, geom_arista_fn, resolver_color_fn,
                mostrar_marcos, rutas_aristas=rutas_aristas,
            ),
        )
    return buf.getvalue()
