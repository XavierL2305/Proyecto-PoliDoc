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


def _ref_desplazada(ref, offset):
    """``'A29'`` + 1 → ``'A30'`` (para sellos multilínea)."""
    ref = (ref or '').strip().upper()
    if not offset:
        return ref
    col = ''
    fila = ''
    for ch in ref:
        if ch.isalpha():
            col += ch
        elif ch.isdigit():
            fila += ch
        else:
            break
    if not col or not fila:
        return ref
    return f'{col}{int(fila) + offset}'


def _build_inline_cell(ref, text, style='0'):
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
    """Find existing style index for a cell ref in sheet XML.

    El fallback es ``'0'`` (estilo por defecto de ``cellXfs``): usar un índice
    fijo como ``'15'`` rompía plantillas mínimas, donde ese índice no existe,
    y dejaba el XLSX ilegible con ``openpyxl``.
    """
    for c in sheet_xml.iter(f'{{{NS["s"]}}}c'):
        if c.get('r') == ref:
            return c.get('s', '0')
    return '0'


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

    Los campos auto (vigencia, versión, título, operación…) se releen de la
    BD en el momento, no del snapshot guardado en ``solicitud.respuestas``.

    Returns:
        bytes: El contenido XLSX generado.
    """
    from ..models import respuestas_planilla_actual
    from .firmas import firmas_de_solicitud, respuestas_con_sello

    respuestas = respuestas_planilla_actual(solicitud)
    if solicitud.formato:
        respuestas = respuestas_con_sello(
            solicitud.formato, firmas_de_solicitud(solicitud), respuestas,
        )
    return exportar_xlsx_formato(solicitud.formato, respuestas)


def exportar_xlsx_formato(formato, respuestas):
    """Genera XLSX lleno dado un ``formato`` y sus ``respuestas``.

    Camino genérico usado también por formatos que NO cuelgan de una
    ``SolicitudCambio`` (p. ej. la planilla SGC-FOR-002 de un lote de
    distribución).

    Args:
        formato: Objeto ``Formato`` con ``plantilla_xlsx`` y campos.
        respuestas: Dict ``{campo.clave: valor}`` ya resuelto.

    Returns:
        bytes: El contenido XLSX generado.

    Raises:
        ValueError: Si el formato no tiene plantilla XLSX.
    """
    plantilla = formato.plantilla_xlsx
    if not plantilla:
        raise ValueError('El formato no tiene plantilla XLSX')

    plantilla_path = plantilla.path

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

        if campo.tipo == 'select':
            marca = _marca_de_opcion(campo, valor)
            if marca:
                ref, texto_marca = marca
                style = _get_existing_style(root, ref)
                cell = _build_inline_cell(ref, texto_marca, style)
                _replace_or_add_cell(root, cell, ref)
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

        elif campo.tipo == 'sello' and campo.celda_excel and valor:
            ref_base = campo.celda_excel.strip().split(':')[0]
            lineas = valor if isinstance(valor, (list, tuple)) else [valor]
            for i, linea in enumerate(lineas):
                ref = _ref_desplazada(ref_base, i)
                style = _get_existing_style(root, ref)
                cell = _build_inline_cell(ref, str(linea), style)
                _replace_or_add_cell(root, cell, ref)

        elif campo.tipo == 'tabla' and valor:
            config = campo.config_tabla or {}
            fila_inicio = config.get('fila_inicio', 36)
            #: Tope de filas de la tabla en la plantilla (p. ej. 33 en el
            #: FOR-002). Por defecto 6, que es el de la planilla FOR-005.
            max_filas = config.get('max_filas', 6)
            columnas = config.get('columnas', [])
            for i, fila_data in enumerate(valor[:max_filas]):
                row_num = fila_inicio + i
                for col_def in columnas:
                    cell_val = fila_data.get(col_def.get('clave', ''), '')
                    if cell_val in (None, ''):
                        continue
                    _escribir_columna_tabla(
                        root, col_def, row_num, cell_val,
                    )

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _escribir_columna_tabla(root, col_def, row_num, cell_val):
    """Escribe una celda de columna de tabla según su definición.

    Dos formas soportadas en ``col_def``:

    * ``{'celda': 'A'}`` — valor normal en ``A{row}``.
    * ``{'tipo': 'si_no', 'celda_si': 'G', 'celda_no': 'H'}`` — escribe una
      marca (``X``) en la columna que corresponda según el valor booleano
      (``si``/``no``, ``True``/``False``, ``1``/``0``). Es el caso del
      RETIRO DE LA VERSIÓN ANTERIOR del SGC-FOR-002.
    """
    if col_def.get('tipo') == 'si_no':
        marca = col_def.get('marca', 'X')
        ref_col = _columna_si_no(col_def, cell_val)
        if not ref_col:
            return
        ref = f'{ref_col}{row_num}'
    else:
        ref_col = col_def.get('celda', '')
        if not ref_col:
            return
        ref = f'{ref_col}{row_num}'
        marca = str(cell_val)

    style = _get_existing_style(root, ref)
    cell = _build_inline_cell(ref, marca, style)
    _replace_or_add_cell(root, cell, ref)


def _columna_si_no(col_def, valor):
    """Devuelve la columna (``G``/``H``…) que corresponde a un valor sí/no.

    Acepta ``'si'``/``'no'`` (cualquier variación de mayúsculas y acentos),
    ``True``/``False`` y ``1``/``0``. Devuelve ``''`` si el valor no es
    reconocible (no se marca nada en vez de marcar mal).
    """
    if isinstance(valor, bool):
        es_si = valor
    else:
        texto = str(valor).strip().lower()
        if texto in ('si', 'sí', 's', '1', 'true', 'x'):
            es_si = True
        elif texto in ('no', 'n', '0', 'false'):
            es_si = False
        else:
            return ''
    clave = 'celda_si' if es_si else 'celda_no'
    return (col_def.get(clave) or '').strip()


def _marca_de_opcion(campo, valor):
    """Si la opción elegida define ``celda_marca``, devuelve ``(ref, marca)``.

    Permite marcar una casilla de la plantilla con una X en vez de escribir
    el texto (p. ej. ORIGEN EXT/INT del SGC-FOR-002: ``C12``/``D12``). Las
    opciones sin ``celda_marca`` no se ven afectadas: los formatos existentes
    (FOR-005) que solo declaran ``celda``/``shape`` siguen igual.
    """
    if not valor or not campo.opciones:
        return None
    texto = str(valor).strip().lower()
    for opt in campo.opciones:
        if not isinstance(opt, dict):
            continue
        if str(opt.get('valor', '')).strip().lower() != texto:
            continue
        ref = str(opt.get('celda_marca') or '').strip()
        if not ref:
            return None
        return ref, str(opt.get('marca') or 'X')
    return None


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
