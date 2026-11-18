"""
Servicio de exportación SGC-FOR-005: genera XLSX a partir de la plantilla
rellenando celdas y rellenando formas de checkbox.
"""
import copy
import io
import logging
import math
import os
import re
import tempfile
import textwrap
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


def exportar_xlsx_formato(formato, respuestas, para_pdf=False):
    """Genera XLSX lleno dado un ``formato`` y sus ``respuestas``.

    Camino genérico usado también por formatos que NO cuelgan de una
    ``SolicitudCambio`` (p. ej. la planilla SGC-FOR-002 de un lote de
    distribución).

    Args:
        formato: Objeto ``Formato`` con ``plantilla_xlsx`` y campos.
        respuestas: Dict ``{campo.clave: valor}`` ya resuelto.
        para_pdf: Ajusta el libro para imprimir el formulario (hoja activa,
            área de impresión con los gráficos y escala a una página de
            ancho). Se usa solo para generar el PDF; el XLSX descargable
            conserva todas las hojas.

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
            estilos = _EstilosXlsx(zf_in)
            anexos = []
            estado = {}

            try:
                sheet1 = _patch_sheet1(
                    zf_in.read('xl/worksheets/sheet1.xml'),
                    formato, respuestas, shared_strings,
                    estilos=estilos, anexos=anexos, estado=estado,
                )
            except KeyError:
                sheet1 = None

            reemplazos = {}
            nuevos = {}
            if anexos:
                reemplazos, nuevos = _preparar_anexo(
                    zf_in, formato, anexos,
                )
            rango_impresion = None
            if para_pdf and sheet1:
                raiz = etree.fromstring(sheet1)
                rango_impresion = _preparar_pdf_hoja(raiz, zf_in)
                sheet1 = _serializar(raiz)

            if (estado.get('recalcular') or anexos or para_pdf):
                wb_data = reemplazos.get(
                    'xl/workbook.xml', zf_in.read('xl/workbook.xml'),
                )
                wb = etree.fromstring(wb_data)
                if estado.get('recalcular') or anexos:
                    _marcar_recalculo(wb)
                if para_pdf:
                    _preparar_pdf_workbook(wb, rango_impresion)
                reemplazos['xl/workbook.xml'] = etree.tostring(
                    wb, xml_declaration=True, encoding='UTF-8',
                    standalone=True,
                )

            for item in zf_in.infolist():
                data = zf_in.read(item.filename)

                if item.filename == 'xl/worksheets/sheet1.xml' and sheet1:
                    data = sheet1

                elif item.filename == 'xl/drawings/drawing1.xml':
                    data = _patch_drawing1(data, formato, respuestas)

                elif item.filename == 'xl/styles.xml':
                    data = estilos.xml() or data

                elif item.filename in reemplazos:
                    data = reemplazos[item.filename]

                zf_out.writestr(item, data)

            for nombre_archivo, contenido in nuevos.items():
                zf_out.writestr(nombre_archivo, contenido)

        return buf.getvalue()


def _patch_sheet1(sheet_data, formato, respuestas, shared_strings,
                  estilos=None, anexos=None, estado=None):
    """Patch sheet1.xml: celdas, tablas y bloques de texto multilínea.

    ``estilos`` permite crear variantes con ``wrapText`` para los bloques;
    ``anexos`` acumula los textos que no caben en la plantilla (CA-1) y
    ``estado['recalcular']`` avisa que hay fórmulas con caché actualizada.
    """
    root = etree.fromstring(sheet_data)
    estilos = estilos or _EstilosXlsx(None)
    anexos = anexos if anexos is not None else []
    estado = estado if estado is not None else {}

    campos = formato.get_todos_los_campos()
    for campo in campos:
        valor = respuestas.get(campo.clave)
        if campo.tipo != 'tabla' and (valor is None or valor == ''):
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
            celda = campo.celda_excel.strip()
            if ':' in celda:
                _escribir_bloque(
                    root, celda, valor, campo.etiqueta, estilos, anexos,
                )
                continue
            ref = celda
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

        elif campo.tipo == 'tabla':
            config = campo.config_tabla or {}
            fila_inicio = config.get('fila_inicio', 36)
            #: Tope de filas de la tabla en la plantilla (p. ej. 33 en el
            #: FOR-002 o 6 en las causas del FOR-010). El servidor ya rechaza
            #: más filas; aquí el tope evita desbordar la plantilla.
            max_filas = config.get('max_filas', 6)
            columnas = config.get('columnas', [])
            filas = list(valor) if isinstance(valor, list) else []

            # Limpieza de filas no usadas (CA-4): la plantilla puede traer
            # datos de ejemplo (p. ej. las 6 causas del FOR-010) que no deben
            # sobrevivir si el registro tiene menos filas.
            if config.get('limpiar_filas'):
                for i in range(len(filas), max_filas):
                    row_num = fila_inicio + i
                    for col_def in columnas:
                        for ref in _refs_columna(col_def, row_num):
                            _limpiar_celda(root, ref)

            for i, fila_data in enumerate(filas[:max_filas]):
                row_num = fila_inicio + i
                for col_def in columnas:
                    cell_val = fila_data.get(col_def.get('clave', ''), '')
                    if cell_val in (None, ''):
                        continue
                    _escribir_columna_tabla(
                        root, col_def, row_num, cell_val,
                    )

            if config.get('totales'):
                _actualizar_totales(root, config, filas[:max_filas])
                estado['recalcular'] = True

    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _escribir_columna_tabla(root, col_def, row_num, cell_val):
    """Escribe una celda de columna de tabla según su definición.

    Formas soportadas en ``col_def``:

    * ``{'celda': 'A'}`` — valor normal en ``A{row}``.
    * ``{'tipo': 'si_no', 'celda_si': 'G', 'celda_no': 'H'}`` — escribe una
      marca (``X``) en la columna que corresponda según el valor booleano
      (``si``/``no``, ``True``/``False``, ``1``/``0``). Es el caso del
      RETIRO DE LA VERSIÓN ANTERIOR del SGC-FOR-002.
    * ``{'tipo': 'entero'|'numero'}`` — escribe una celda **numérica** real
      (``<v>87</v>``): el gráfico Pareto del FOR-010 solo lee números.
    """
    tipo = str(col_def.get('tipo') or '').strip().lower()
    if tipo == 'si_no':
        marca = col_def.get('marca', 'X')
        ref_col = _columna_si_no(col_def, cell_val)
        if not ref_col:
            return
        ref = f'{ref_col}{row_num}'
    else:
        ref_col = (col_def.get('celda') or '').strip()
        if not ref_col:
            return
        ref = f'{ref_col}{row_num}'
        marca = str(cell_val)

    style = _get_existing_style(root, ref)
    if tipo in ('entero', 'numero'):
        numero = _numero(cell_val)
        if numero is None:
            return
        cell = _build_number_cell(ref, numero, style)
    else:
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
        target_row = etree.Element(f'{{{ns_s}}}row')
        target_row.set('r', str(row_num))
        insertada = False
        for row in sheet_data.findall(f'{{{ns_s}}}row'):
            existente = row.get('r') or ''
            if existente.isdigit() and int(existente) > row_num:
                sheet_data.insert(list(sheet_data).index(row), target_row)
                insertada = True
                break
        if not insertada:
            sheet_data.append(target_row)

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


# ---------------------------------------------------------------------------
# Utilidades de bloques de texto, tablas numéricas y anexo (FOR-010)
# ---------------------------------------------------------------------------

def _split_ref(ref):
    """``'B11'`` → ``('B', 11)``; ``None`` si no es una celda válida."""
    ref = (ref or '').strip().upper()
    letras = ''.join(c for c in ref if c.isalpha())
    digitos = ''.join(c for c in ref if c.isdigit())
    if not letras or not digitos:
        return None
    return letras, int(digitos)


def _col_letter(idx):
    """Índice de columna 0-based → letra (``0`` → ``A``)."""
    letras = ''
    idx += 1
    while idx:
        idx, resto = divmod(idx - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def _parse_rango(rango):
    """``'B11:G13'`` → ``(col_ini0, fila_ini, col_fin0, fila_fin)``."""
    if not rango or ':' not in rango:
        return None
    ini, _, fin = rango.partition(':')
    a, b = _split_ref(ini), _split_ref(fin)
    if not a or not b:
        return None
    col_ini, fila_ini = _col_to_idx(a[0]), a[1]
    col_fin, fila_fin = _col_to_idx(b[0]), b[1]
    if col_fin < col_ini:
        col_ini, col_fin = col_fin, col_ini
    if fila_fin < fila_ini:
        fila_ini, fila_fin = fila_fin, fila_ini
    return col_ini, fila_ini, col_fin, fila_fin


def _anchos_columnas(root):
    """Anchos (en caracteres) de ``<cols>``, con clave 1-based."""
    ns = NS['s']
    anchos = {}
    cols = root.find(f'{{{ns}}}cols')
    if cols is None:
        return anchos
    for col in cols.findall(f'{{{ns}}}col'):
        try:
            minimo = int(col.get('min'))
            maximo = int(col.get('max') or minimo)
            ancho = float(col.get('width'))
        except (TypeError, ValueError):
            continue
        for idx in range(minimo, maximo + 1):
            anchos[idx] = ancho
    return anchos


def _rangos_mergeados(root):
    ns = NS['s']
    rangos = []
    celdas = root.find(f'{{{ns}}}mergeCells')
    if celdas is None:
        return rangos
    for merge in celdas.findall(f'{{{ns}}}mergeCell'):
        datos = _parse_rango(merge.get('ref'))
        if datos:
            rangos.append(datos)
    return rangos


def _celda_en_merge_no_topleft(ref, merges):
    """``True`` si la celda es parte de un merge pero no su esquina superior."""
    partes = _split_ref(ref)
    if not partes:
        return False
    col = _col_to_idx(partes[0])
    fila = partes[1]
    for col_ini, fila_ini, col_fin, fila_fin in merges:
        if col_ini <= col <= col_fin and fila_ini <= fila <= fila_fin:
            return not (col == col_ini and fila == fila_ini)
    return False


def _set_row_height(root, fila, altura):
    ns = NS['s']
    data = root.find(f'{{{ns}}}sheetData')
    if data is None:
        return
    for row in data.findall(f'{{{ns}}}row'):
        if row.get('r') == str(fila):
            row.set('ht', f'{min(altura, 409):.1f}')
            row.set('customHeight', '1')
            return


def _agregar_merge(root, ref):
    """Agrega un merge de fila si no existe ni solapa con otro."""
    datos = _parse_rango(ref)
    if not datos:
        return
    col_ini, fila_ini, col_fin, fila_fin = datos
    for ci, fi, cf, ff in _rangos_mergeados(root):
        cubre = ci <= col_ini and cf >= col_fin and fi <= fila_ini and ff >= fila_fin
        if cubre:
            return
        solapa = not (cf < col_ini or ci > col_fin or ff < fila_ini or fi > fila_fin)
        if solapa:
            return
    ns = NS['s']
    celdas = root.find(f'{{{ns}}}mergeCells')
    if celdas is None:
        celdas = etree.Element(f'{{{ns}}}mergeCells')
        data = root.find(f'{{{ns}}}sheetData')
        indice = list(root).index(data) + 1 if data is not None else len(root)
        root.insert(indice, celdas)
    nueva = etree.SubElement(celdas, f'{{{ns}}}mergeCell')
    nueva.set('ref', ref)
    celdas.set('count', str(len(celdas.findall(f'{{{ns}}}mergeCell'))))


def _escribir_bloque(root, rango, texto, etiqueta, estilos, anexos):
    """Reparte ``texto`` por las filas del rango (CA-1).

    Respeta merges (no escribe en celdas internas), estima el ancho de cada
    fila con los anchos de columna y, si el texto no cabe ni con la altura
    máxima de Excel, lo envía completo a la hoja de anexo.
    """
    datos = _parse_rango(rango)
    if not datos:
        return
    col_ini, fila_ini, col_fin, fila_fin = datos
    anchos = _anchos_columnas(root)
    ancho = sum(anchos.get(idx, 8.43) for idx in range(col_ini + 1, col_fin + 2))
    limite = max(10, int(ancho))

    lineas = []
    for parrafo in str(texto or '').split('\n'):
        if not parrafo.strip():
            lineas.append('')
        else:
            lineas.extend(textwrap.wrap(parrafo, width=limite) or [''])

    merges = _rangos_mergeados(root)
    destinos = []
    for fila in range(fila_ini, fila_fin + 1):
        ref = f'{_col_letter(col_ini)}{fila}'
        if _celda_en_merge_no_topleft(ref, merges):
            continue
        destinos.append((ref, fila))
    if not destinos or not lineas:
        return

    if len(lineas) <= len(destinos):
        por_fila = 1
        altura = None
    else:
        por_fila = math.ceil(len(lineas) / len(destinos))
        if por_fila > 27:  # 27 líneas * 15pt = 405 (tope práctico 409)
            por_fila = 27
            anexos.append((etiqueta, str(texto)))
        altura = por_fila * 15 + 2

    pos = 0
    for ref, fila in destinos:
        bloque = lineas[pos:pos + por_fila]
        pos += por_fila
        if not bloque:
            break
        style = _get_existing_style(root, ref)
        if por_fila > 1:
            style = estilos.con_wrap(style)
            _set_row_height(root, fila, altura)
            _agregar_merge(
                root,
                f'{_col_letter(col_ini)}{fila}:{_col_letter(col_fin)}{fila}',
            )
        cell = _build_inline_cell(ref, '\n'.join(bloque), style)
        _replace_or_add_cell(root, cell, ref)

    if pos < len(lineas) and (etiqueta, str(texto)) not in anexos:
        anexos.append((etiqueta, str(texto)))


def _refs_columna(col_def, row_num):
    """Celdas que ocupa una columna de tabla en la fila (1 o 2 para sí/no)."""
    if str(col_def.get('tipo') or '').strip().lower() == 'si_no':
        refs = []
        for clave in ('celda_si', 'celda_no'):
            col = (col_def.get(clave) or '').strip()
            if col:
                refs.append(f'{col}{row_num}')
        return refs
    col = (col_def.get('celda') or '').strip()
    return [f'{col}{row_num}'] if col else []


def _limpiar_celda(root, ref):
    """Deja la celda vacía conservando su estilo (bordes/fuente)."""
    ns = NS['s']
    style = _get_existing_style(root, ref)
    cell = etree.Element(f'{{{ns}}}c')
    cell.set('r', ref)
    cell.set('s', style)
    _replace_or_add_cell(root, cell, ref)


def _build_number_cell(ref, numero, style='0'):
    """Celda numérica real (sin ``t``), necesaria para gráficos y SUM."""
    ns = NS['s']
    cell = etree.Element(f'{{{ns}}}c')
    cell.set('r', ref)
    if style:
        cell.set('s', style)
    valor = etree.SubElement(cell, f'{{{ns}}}v')
    valor.text = str(numero) if isinstance(numero, int) else f'{numero:.15g}'
    return cell


def _numero(valor):
    """Convierte a ``int``/``float``; ``None`` si no es un número válido."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return valor
    try:
        numero = float(str(valor).strip().replace(',', '.'))
    except (TypeError, ValueError):
        return None
    return int(numero) if numero.is_integer() else numero


def _actualizar_cache_formula(root, ref, valor):
    """Actualiza ``<v>`` de una celda con fórmula, conservando ``<f>``."""
    ns = NS['s']
    for cell in root.iter(f'{{{ns}}}c'):
        if cell.get('r') != ref:
            continue
        if cell.find(f'{{{ns}}}f') is None:
            return
        cache = cell.find(f'{{{ns}}}v')
        if cache is None:
            cache = etree.SubElement(cell, f'{{{ns}}}v')
        if isinstance(valor, str):
            cell.set('t', 'str')
            cache.text = valor
        else:
            cell.attrib.pop('t', None)
            cache.text = (
                str(valor) if isinstance(valor, int) else f'{valor:.15g}'
            )
        return


def _actualizar_totales(root, config, filas):
    """Recalcula totales/porcentajes de una tabla con columna de fórmula."""
    totales = config.get('totales') or {}
    fila_total = totales.get('fila')
    col_valor = (totales.get('columna_valor') or '').strip()
    col_pct = (totales.get('columna_porcentaje') or '').strip()
    if not fila_total or not col_valor:
        return
    clave_valor = None
    for col in (config.get('columnas') or []):
        if (col.get('celda') or '').strip() == col_valor:
            clave_valor = col.get('clave')
            break
    if not clave_valor:
        return

    max_filas = int(
        config.get('max_filas')
        or (config.get('fila_fin', 0) - config.get('fila_inicio', 0) + 1)
        or len(filas)
    )
    incidencias = [
        _numero((fila or {}).get(clave_valor)) for fila in filas[:max_filas]
    ]
    # También se recalculan las filas no usadas: la plantilla trae fórmulas
    # con caché de los datos de ejemplo y quedarían porcentajes viejos.
    incidencias.extend([None] * (max_filas - len(incidencias)))
    total = sum(v for v in incidencias if isinstance(v, (int, float)))
    _actualizar_cache_formula(root, f'{col_valor}{fila_total}', total)

    fila_ini = config.get('fila_inicio', 0)
    if col_pct:
        for i, valor in enumerate(incidencias):
            ref = f'{col_pct}{fila_ini + i}'
            if total:
                _actualizar_cache_formula(
                    root, ref, (valor or 0) / total,
                )
            else:
                _actualizar_cache_formula(root, ref, '')
        _actualizar_cache_formula(
            root, f'{col_pct}{fila_total}', 1 if total else 0,
        )


class _EstilosXlsx:
    """Crea variantes con ``wrapText`` de los estilos existentes."""

    def __init__(self, zf):
        self._root = None
        self._modificado = False
        self._wrap = {}
        if zf is None:
            return
        try:
            self._root = etree.fromstring(zf.read('xl/styles.xml'))
        except KeyError:
            self._root = None

    def con_wrap(self, style_idx):
        if self._root is None:
            return style_idx
        style_idx = str(style_idx or '0')
        if style_idx in self._wrap:
            return self._wrap[style_idx]
        ns = NS['s']
        cell_xfs = self._root.find(f'{{{ns}}}cellXfs')
        if cell_xfs is None:
            return style_idx
        xfs = list(cell_xfs)
        try:
            original = xfs[int(style_idx)]
        except (ValueError, IndexError):
            return style_idx
        nuevo = copy.deepcopy(original)
        alineacion = nuevo.find(f'{{{ns}}}alignment')
        if alineacion is None:
            alineacion = etree.SubElement(nuevo, f'{{{ns}}}alignment')
        alineacion.set('wrapText', '1')
        nuevo.set('applyAlignment', '1')
        cell_xfs.append(nuevo)
        nuevo_idx = str(len(cell_xfs) - 1)
        cell_xfs.set('count', str(len(cell_xfs)))
        self._wrap[style_idx] = nuevo_idx
        self._modificado = True
        return nuevo_idx

    def xml(self):
        if self._root is None or not self._modificado:
            return None
        return etree.tostring(
            self._root, xml_declaration=True, encoding='UTF-8',
            standalone=True,
        )


def _serializar(root):
    return etree.tostring(
        root, xml_declaration=True, encoding='UTF-8', standalone=True,
    )


def _hoja_anexo_xml(anexos):
    """Hoja simple con etiqueta + texto completo de los bloques desbordados."""
    ns = NS['s']
    root = etree.Element(f'{{{ns}}}worksheet')
    filas = []
    for etiqueta, texto in anexos:
        lineas = [etiqueta]
        for linea in str(texto).split('\n'):
            # El límite de una celda de Excel es 32.767 caracteres.
            while len(linea) > 30000:
                lineas.append(linea[:30000])
                linea = linea[30000:]
            lineas.append(linea)
        filas.extend(lineas + [''])
    dimension = etree.SubElement(root, f'{{{ns}}}dimension')
    dimension.set('ref', f'A1:A{max(len(filas), 1)}')
    vistas = etree.SubElement(root, f'{{{ns}}}sheetViews')
    vista = etree.SubElement(vistas, f'{{{ns}}}sheetView')
    vista.set('workbookViewId', '0')
    formato = etree.SubElement(root, f'{{{ns}}}sheetFormatPr')
    formato.set('defaultRowHeight', '15')
    cols = etree.SubElement(root, f'{{{ns}}}cols')
    col = etree.SubElement(cols, f'{{{ns}}}col')
    col.set('min', '1')
    col.set('max', '1')
    col.set('width', '100')
    col.set('customWidth', '1')
    data = etree.SubElement(root, f'{{{ns}}}sheetData')
    for numero, linea in enumerate(filas, start=1):
        row = etree.SubElement(data, f'{{{ns}}}row')
        row.set('r', str(numero))
        if not linea:
            continue
        cell = etree.SubElement(row, f'{{{ns}}}c')
        cell.set('r', f'A{numero}')
        cell.set('t', 'inlineStr')
        inline = etree.SubElement(cell, f'{{{ns}}}is')
        texto = etree.SubElement(inline, f'{{{ns}}}t')
        texto.set(
            '{http://www.w3.org/XML/1998/namespace}space', 'preserve',
        )
        texto.text = linea
    margenes = etree.SubElement(root, f'{{{ns}}}pageMargins')
    for clave, valor in (
        ('left', '0.5'), ('right', '0.5'), ('top', '0.75'),
        ('bottom', '0.75'), ('header', '0.3'), ('footer', '0.3'),
    ):
        margenes.set(clave, valor)
    return _serializar(root)


def _preparar_anexo(zf, formato, anexos):
    """Añade la hoja ``<código> (Anexo)`` al paquete.

    Returns:
        ``(reemplazos, nuevas_entradas)``: contenidos que sustituyen partes
        existentes del zip y archivos nuevos que hay que agregar.
    """
    ns = NS['s']
    nombres = set(zf.namelist())
    numeros = [
        int(m.group(1))
        for nombre in nombres
        for m in [re.fullmatch(r'xl/worksheets/sheet(\d+)\.xml', nombre)]
        if m
    ]
    numero = max([n for n in numeros if n >= 1], default=1) + 1
    ruta = f'xl/worksheets/sheet{numero}.xml'

    wb = etree.fromstring(zf.read('xl/workbook.xml'))
    hojas = wb.find(f'{{{ns}}}sheets')
    if hojas is None:
        hojas = etree.SubElement(wb, f'{{{ns}}}sheets')
    nombre_hoja = f'{formato.codigo} (Anexo)'
    usados = {h.get('name') for h in hojas}
    if nombre_hoja in usados:
        i = 2
        while f'{nombre_hoja} {i}' in usados:
            i += 1
        nombre_hoja = f'{nombre_hoja} {i}'
    ids = [
        int(h.get('sheetId'))
        for h in hojas
        if (h.get('sheetId') or '').isdigit()
    ]
    nuevo_sheet_id = (max(ids) + 1) if ids else 1

    rels = etree.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
    rel_ns = 'http://schemas.openxmlformats.org/package/2006/relationships'
    usados_rid = [
        int(m.group(1))
        for rel in rels
        for m in [re.fullmatch(r'rId(\d+)', rel.get('Id') or '')]
        if m
    ]
    nuevo_rid = f'rId{(max(usados_rid) + 1) if usados_rid else 1}'
    etree.SubElement(
        rels, f'{{{rel_ns}}}Relationship',
        Id=nuevo_rid,
        Type=(
            'http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/worksheet'
        ),
        Target=f'worksheets/sheet{numero}.xml',
    )

    hoja = etree.SubElement(hojas, f'{{{ns}}}sheet')
    hoja.set('name', nombre_hoja)
    hoja.set('sheetId', str(nuevo_sheet_id))
    hoja.set(
        '{http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships}id', nuevo_rid,
    )

    tipos = etree.fromstring(zf.read('[Content_Types].xml'))
    ct_ns = 'http://schemas.openxmlformats.org/package/2006/content-types'
    etree.SubElement(
        tipos, f'{{{ct_ns}}}Override',
        PartName=f'/{ruta}',
        ContentType=(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.'
            'worksheet+xml'
        ),
    )

    reemplazos = {
        'xl/workbook.xml': _serializar(wb),
        'xl/_rels/workbook.xml.rels': _serializar(rels),
        '[Content_Types].xml': _serializar(tipos),
    }
    nuevos = {ruta: _hoja_anexo_xml(anexos)}
    return reemplazos, nuevos


def _rango_impresion(root, zf):
    """Área de impresión que cubre las celdas y los dibujos/gráficos."""
    max_col = 0
    max_row = 0
    ns = NS['s']
    dimension = root.find(f'{{{ns}}}dimension')
    ref_dim = dimension.get('ref') if dimension is not None else ''
    datos = _parse_rango(ref_dim)
    if datos:
        max_col = datos[2]
        max_row = datos[3]
    elif ref_dim:
        simple = _split_ref(ref_dim)
        if simple:
            max_col = _col_to_idx(simple[0])
            max_row = simple[1]
    try:
        dibujo = etree.fromstring(zf.read('xl/drawings/drawing1.xml'))
    except KeyError:
        dibujo = None
    if dibujo is not None:
        xdr = NS['xdr']
        for etiqueta in ('from', 'to'):
            for marca in dibujo.iter(f'{{{xdr}}}{etiqueta}'):
                col = marca.find(f'{{{xdr}}}col')
                fila = marca.find(f'{{{xdr}}}row')
                if col is not None and (col.text or '').isdigit():
                    max_col = max(max_col, int(col.text))
                if fila is not None and (fila.text or '').isdigit():
                    # Los anchors usan filas 0-based; la dimensión, 1-based.
                    max_row = max(max_row, int(fila.text) + 1)
    return f'A1:{_col_letter(max_col)}{max_row}'


def _preparar_pdf_hoja(root, zf):
    """Prepara la hoja del formulario para el PDF (una página de ancho)."""
    ns = NS['s']
    datos = _parse_rango(_rango_impresion(root, zf))
    if not datos:
        return None
    _, _, col_fin, fila_fin = datos
    rango = f'$A$1:${_col_letter(col_fin)}${fila_fin}'

    sheet_pr = root.find(f'{{{ns}}}sheetPr')
    if sheet_pr is None:
        sheet_pr = etree.Element(f'{{{ns}}}sheetPr')
        root.insert(0, sheet_pr)
    page_pr = sheet_pr.find(f'{{{ns}}}pageSetUpPr')
    if page_pr is None:
        page_pr = etree.SubElement(sheet_pr, f'{{{ns}}}pageSetUpPr')
    page_pr.set('fitToPage', '1')

    page_setup = root.find(f'{{{ns}}}pageSetup')
    if page_setup is None:
        page_setup = etree.SubElement(root, f'{{{ns}}}pageSetup')
    page_setup.set('fitToWidth', '1')
    page_setup.set('fitToHeight', '0')
    return rango


def _preparar_pdf_workbook(wb, rango_impresion):
    """Oculta las hojas auxiliares y fija el área de impresión del formulario."""
    ns = NS['s']
    hojas = wb.find(f'{{{ns}}}sheets')
    if hojas is None:
        return
    primera = None
    for hoja in hojas:
        if primera is None:
            primera = hoja
        elif (hoja.get('name') or '').endswith('(Anexo)'):
            # El anexo también se imprime: es donde va el texto que no cupo.
            hoja.attrib.pop('state', None)
        else:
            hoja.set('state', 'hidden')
    if primera is None or not rango_impresion:
        return
    nombre = (primera.get('name') or '').replace("'", "''")

    definidos = wb.find(f'{{{ns}}}definedNames')
    if definidos is None:
        definidos = etree.Element(f'{{{ns}}}definedNames')
        indice = list(wb).index(hojas) + 1
        wb.insert(indice, definidos)
    for viejo in list(definidos):
        if (viejo.get('name') == '_xlnm.Print_Area'
                and viejo.get('localSheetId') == '0'):
            definidos.remove(viejo)
    area = etree.SubElement(definidos, f'{{{ns}}}definedName')
    area.set('name', '_xlnm.Print_Area')
    area.set('localSheetId', '0')
    area.text = f"'{nombre}'!{rango_impresion}"


def _marcar_recalculo(wb):
    """Fuerza el recálculo al abrir (por si algún visor ignora la caché)."""
    ns = NS['s']
    calc = wb.find(f'{{{ns}}}calcPr')
    if calc is None:
        calc = etree.Element(f'{{{ns}}}calcPr')
        ext = wb.find(f'{{{ns}}}extLst')
        if ext is not None:
            wb.insert(list(wb).index(ext), calc)
        else:
            wb.append(calc)
    calc.set('fullCalcOnLoad', '1')


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
