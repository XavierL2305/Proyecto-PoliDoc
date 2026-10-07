"""
Servicio de exportación SGC-FOR-005: genera XLSX a partir de la plantilla
rellenando celdas y rellenando formas de checkbox.
"""
import io
import os
import tempfile
import logging
import zipfile
from lxml import etree

logger = logging.getLogger(__name__)

NS = {
    'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    's': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
}


def _col_to_idx(col_str):
    """Convert column letter to 0-based index. 'A' -> 0, 'Z' -> 25, 'AA' -> 26."""
    result = 0
    for ch in col_str.upper():
        result = result * 26 + (ord(ch) - 64)
    return result - 1


def _cell_col_idx(ref):
    """Extract column index from a cell ref like 'B30' -> 1."""
    col = ''
    for ch in ref:
        if ch.isalpha():
            col += ch
        else:
            break
    return _col_to_idx(col)


def _build_inline_cell(ref, text, style='15'):
    """Build an inlineStr cell element."""
    cell = etree.Element(f'{{{NS["s"]}}}c')
    cell.set('r', ref)
    cell.set('t', 'inlineStr')
    cell.set('s', style)
    is_el = etree.SubElement(cell, f'{{{NS["s"]}}}is')
    t_el = etree.SubElement(is_el, f'{{{NS["s"]}}}t')
    t_el.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    t_el.text = text
    return cell


def _resolve_shared_strings(zf):
    """Load sharedStrings.xml and return list of strings."""
    try:
        ss_xml = zf.read('xl/sharedStrings.xml')
        root = etree.fromstring(ss_xml)
        strings = []
        for si in root.findall(f'{{{NS["s"]}}}si'):
            t = si.find(f'{{{NS["s"]}}}t')
            strings.append(t.text if t is not None and t.text else '')
        return strings
    except Exception:
        return []


def _get_existing_style(sheet_xml, ref):
    """Find existing style index for a cell ref in sheet XML."""
    for c in sheet_xml.iter(f'{{{NS["s"]}}}c'):
        if c.get('r') == ref:
            return c.get('s', '15')
    return '15'


def _get_existing_label(sheet_xml, ref, shared_strings):
    """Get the existing text content of a cell (resolving shared strings)."""
    for c in sheet_xml.iter(f'{{{NS["s"]}}}c'):
        if c.get('r') == ref:
            t = c.get('t')
            if t == 's':
                v = c.find(f'{{{NS["s"]}}}v')
                if v is not None and v.text:
                    idx = int(v.text)
                    if idx < len(shared_strings):
                        return shared_strings[idx]
            elif t == 'inlineStr':
                is_el = c.find(f'{{{NS["s"]}}}is')
                if is_el is not None:
                    t_el = is_el.find(f'{{{NS["s"]}}}t')
                    if t_el is not None and t_el.text:
                        return t_el.text
            else:
                v = c.find(f'{{{NS["s"]}}}v')
                if v is not None and v.text:
                    return v.text
    return ''


def exportar_xlsx(solicitud):
    """Genera XLSX lleno a partir de la plantilla del formato.

    Returns:
        bytes: El contenido XLSX generado.
    """
    plantilla = solicitud.formato.plantilla_xlsx
    if not plantilla:
        raise ValueError('El formato no tiene plantilla XLSX')

    plantilla_path = plantilla.path
    respuestas = solicitud.respuestas or {}
    formato = solicitud.formato

    with zipfile.ZipFile(plantilla_path, 'r') as zf_in:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf_out:
            shared_strings = _resolve_shared_strings(zf_in)

            for item in zf_in.infolist():
                data = zf_in.read(item.filename)

                if item.filename == 'xl/worksheets/sheet1.xml':
                    data = _patch_sheet1(data, formato, respuestas, shared_strings)

                elif item.filename == 'xl/drawings/drawing1.xml':
                    data = _patch_drawing1(data, formato, respuestas)

                zf_out.writestr(item, data)

        return buf.getvalue()


def _patch_sheet1(sheet_data, formato, respuestas, shared_strings):
    """Patch sheet1.xml: write cell values for text/textarea/fecha/table fields."""
    root = etree.fromstring(sheet_data)

    campos = formato.get_todos_los_campos()
    for campo in campos:
        valor = respuestas.get(campo.clave)
        if valor is None or valor == '':
            continue

        if campo.tipo in ('texto', 'fecha', 'select') and campo.celda_excel:
            ref = campo.celda_excel.strip().split(':')[0]
            style = _get_existing_style(root, ref)
            texto = str(valor)
            if getattr(campo, 'incluir_etiqueta', False):
                texto = f'{campo.etiqueta}: {texto}'
            cell = _build_inline_cell(ref, texto, style)
            _replace_or_add_cell(root, cell, ref)

        elif campo.tipo == 'textarea' and campo.celda_excel:
            ref = campo.celda_excel.strip().split(':')[0]
            style = _get_existing_style(root, ref)
            existing_label = _get_existing_label(root, ref, shared_strings)
            if existing_label:
                full_text = existing_label + '\n' + str(valor)
            else:
                full_text = str(valor)
            cell = _build_inline_cell(ref, full_text, style)
            _replace_or_add_cell(root, cell, ref)

        elif campo.tipo == 'tabla' and valor:
            config = campo.config_tabla or {}
            fila_inicio = config.get('fila_inicio', 36)
            columnas = config.get('columnas', [])
            for i, fila_data in enumerate(valor[:6]):
                row_num = fila_inicio + i
                for col_def in columnas:
                    celda_col = col_def.get('celda', '')
                    cell_val = fila_data.get(col_def.get('clave', ''), '')
                    if not celda_col or not cell_val:
                        continue
                    ref = f'{celda_col}{row_num}'
                    style = _get_existing_style(root, ref)
                    cell = _build_inline_cell(ref, str(cell_val), style)
                    _replace_or_add_cell(root, cell, ref)

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _replace_or_add_cell(root, new_cell, ref):
    """Replace existing cell or add new one in sorted column position within the row."""
    ns_s = NS['s']
    sheet_data = root.find(f'{{{ns_s}}}sheetData')
    if sheet_data is None:
        return

    row_num = int(''.join(c for c in ref if c.isdigit()))
    new_col_idx = _cell_col_idx(ref)

    target_row = None
    for row in sheet_data.iter(f'{{{ns_s}}}row'):
        if row.get('r') == str(row_num):
            target_row = row
            break

    if target_row is None:
        target_row = etree.SubElement(sheet_data, f'{{{ns_s}}}row')
        target_row.set('r', str(row_num))

    for existing in target_row.findall(f'{{{ns_s}}}c'):
        if existing.get('r') == ref:
            target_row.remove(existing)
            break

    existing_cells = list(target_row.findall(f'{{{ns_s}}}c'))
    insert_pos = len(existing_cells)
    for i, c in enumerate(existing_cells):
        if _cell_col_idx(c.get('r', '')) > new_col_idx:
            insert_pos = i
            break

    if insert_pos < len(existing_cells):
        target_row.insert(list(target_row).index(existing_cells[insert_pos]), new_cell)
    else:
        target_row.append(new_cell)


def _patch_drawing1(drawing_data, formato, respuestas):
    """Patch drawing1.xml: fill shapes for selected checkbox options."""
    root = etree.fromstring(drawing_data)

    campos = formato.get_todos_los_campos()
    selected_shapes = set()

    for campo in campos:
        valor = respuestas.get(campo.clave)
        if not valor or not campo.opciones:
            continue

        if campo.tipo == 'checkbox_grupo':
            selected_vals = valor if isinstance(valor, list) else [valor]
            for opt in campo.opciones:
                if isinstance(opt, dict) and opt.get('valor') in selected_vals:
                    shape = opt.get('shape', '')
                    if shape:
                        selected_shapes.add(shape)

        elif campo.tipo == 'select':
            for opt in campo.opciones:
                if isinstance(opt, dict) and opt.get('valor') == valor:
                    shape = opt.get('shape', '')
                    if shape:
                        selected_shapes.add(shape)

    if not selected_shapes:
        return drawing_data

    for sp in root.iter(f'{{{NS["xdr"]}}}sp'):
        nv_sp = sp.find(f'{{{NS["xdr"]}}}nvSpPr')
        if nv_sp is None:
            continue
        cnv_pr = nv_sp.find(f'{{{NS["xdr"]}}}cNvPr')
        if cnv_pr is None:
            continue
        name = cnv_pr.get('name', '')
        if name in selected_shapes:
            sp_pr = sp.find(f'{{{NS["xdr"]}}}spPr')
            if sp_pr is not None:
                solid_fill = sp_pr.find(f'{{{NS["a"]}}}solidFill')
                if solid_fill is not None:
                    for child in list(solid_fill):
                        solid_fill.remove(child)
                    srgb = etree.SubElement(solid_fill, f'{{{NS["a"]}}}srgbClr')
                    srgb.set('val', '00A651')

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def exportar_pdf(solicitud):
    """Genera PDF a partir del XLSX lleno usando LibreOffice.

    El XLSX vive en un temporal que se borra al salir, así que la maquetación
    se repara **en línea** (``reparar_sincrono=True``): una reparación en
    Celery se perdería. No toca la base de datos (también lo usa
    ``verificar_exportacion``).

    Returns:
        str: Ruta del PDF generado, o None si falla.
    """
    from documentos.tasks import convertir_a_pdf_sync

    xlsx_bytes = exportar_xlsx(solicitud)

    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp.write(xlsx_bytes)
        tmp_path = tmp.name

    try:
        pdf_path = convertir_a_pdf_sync(tmp_path, reparar_sincrono=True)
        return pdf_path
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
