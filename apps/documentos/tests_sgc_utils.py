"""Utilidades compartidas de las pruebas del formato SGC-FOR-002.

El XLSX oficial vive en ``documentacion/`` (gitignorado, ver ``.gitignore``),
así que CI no puede contar con él: aquí se genera una plantilla SINTÉTICA con
el mismo layout que usa el comando ``integrar_formatos_sgc``.

Nada de este módulo se ejecuta al importarlo (solo definiciones), de modo que
puede ser recolectado por el runner de Django sin efectos secundarios.
"""
import os
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

import openpyxl

#: Nombre del XLSX oficial dentro de ``documentacion/``.
ARCHIVO_REAL_SGC002 = 'SGC-FOR-002 Control de Dist Doc.xlsx'


def ruta_plantilla_real(base):
    """Ruta absoluta al XLSX oficial de ``documentacion/`` (o ``None``)."""
    ruta = os.path.join(str(base), 'documentacion', ARCHIVO_REAL_SGC002)
    return ruta if os.path.exists(ruta) else None


def crear_plantilla_for_002(ruta):
    """XLSX mínimo con el layout del FOR-002 (cabecera 12, tabla 19-51).

    Reproduce las etiquetas, la fila de valores (12) y las 33 filas de
    destinatarios del XLSX oficial, de modo que el exportador escribe en las
    mismas referencias.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'FOR-002'
    for ref, texto in (
        ('A10', 'ORIGEN DE LA INFORMACIÓN'),
        ('A11', 'TIPO DE INFORMACIÓN DOCUMENTADA'),
        ('C10', 'EXT'), ('D10', 'INT'),
        ('C11', 'Externa'), ('D11', 'Interna'),
        ('E11', 'Nº DE IDENTIFICACIÓN'),
        ('F11', 'TÍTULO'), ('I11', 'EDICIÓN'),
        ('J11', 'VERSIÓN'), ('K11', 'VIGENCIA'),
        ('A17', 'NOMBRE DEL USUARIO'), ('A18', 'USUARIO'),
        ('C17', 'CARGO'), ('E17', 'FECHA DE DISTRIBUCIÓN'),
        ('G17', 'RETIRO DE LA VERSIÓN ANTERIOR'),
        ('I17', 'FIRMA CONFORME'),
    ):
        ws[ref] = texto
    for fila in range(19, 52):
        ws[f'A{fila}'] = ''
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    wb.save(ruta)
    return ruta


_NS_SS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def leer_celda(xlsx_bytes, ref):
    """Valor de una celda del XLSX exportado (se lee el XML, no openpyxl).

    Se evita ``openpyxl.load_workbook`` porque el exportador inyecta índices
    de estilo (p. ej. ``s="15"``) que una plantilla mínima no declara.
    """
    with zipfile.ZipFile(BytesIO(xlsx_bytes)) as zf:
        hoja = sorted(
            n for n in zf.namelist()
            if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')
        )[0]
        raiz = ET.fromstring(zf.read(hoja))
    celda = raiz.find(f'.//{_NS_SS}c[@r="{ref}"]')
    if celda is None:
        return None
    if celda.get('t') == 'inlineStr':
        nodo = celda.find(f'{_NS_SS}is/{_NS_SS}t')
    else:
        nodo = celda.find(f'{_NS_SS}v')
    return nodo.text if nodo is not None else None


# ---------------------------------------------------------------------------
# SGC-FOR-003 — Tabla de cambios y modificaciones
# ---------------------------------------------------------------------------

ARCHIVO_REAL_SGC003 = 'SGC-FOR-003 Tabla de Cam y Mod.xlsx'


def ruta_plantilla_real_003(base):
    """Ruta absoluta al XLSX oficial del FOR-003 (o ``None``)."""
    ruta = os.path.join(str(base), 'documentacion', ARCHIVO_REAL_SGC003)
    return ruta if os.path.exists(ruta) else None


def crear_plantilla_for_003(ruta):
    """XLSX mínimo con el layout del FOR-003 (tabla en filas 12-23).

    Reproduce merges y cabeceras del formato oficial para que la generación
    escriba en las mismas referencias (A/B/C/F/G/I).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'SGC-FOR-003'

    ws['C1'] = 'TABLA DE CAMBIOS Y MODIFICACIONES'
    ws.merge_cells('C1:J2')
    ws['C3'] = 'INFORMACION DOCUMENTADA'
    ws.merge_cells('C3:J3')
    ws['C4'] = '7.5.3.2 CONTROL DE LOS CAMBIOS'
    ws.merge_cells('C4:J5')

    for rango, texto in (
        ('A6:B7', 'Nº DE IDENTIFICACION: SGC - FOR - 003'),
        ('C6:D7', 'VERSION: 1'),
        ('E6:F7', 'EDICION: 1'),
        ('G6:H7', 'FECHA DE VIGENCIA: 18/03/2025'),
        ('I6:J7', 'PAGINA: 1 DE 1'),
        ('A8:J8', 'PROCEDIMIENTO DE CONTROL DE DOCUMENTOS'),
        ('A10:A11', 'Nº DE PAGINA MODIFICADA'),
        ('B10:B11', 'VERSIÒN'),
        ('C10:E11', 'DESCRIPCION DEL CAMBIO'),
        ('F10:F11', 'FECHA'),
        ('G10:H11', 'REALIZADO POR'),
        ('I10:J11', 'AUTORIZADO POR'),
    ):
        if ':' in rango:
            ws.merge_cells(rango)
        ws[rango.split(':')[0]] = texto

    for fila in range(12, 24):
        ws.merge_cells(f'C{fila}:E{fila}')
        ws.merge_cells(f'G{fila}:H{fila}')
        ws.merge_cells(f'I{fila}:J{fila}')

    # La plantilla real incluye el instructivo en otra hoja.
    idl = wb.create_sheet('SGC-IDL-009')
    idl['B1'] = 'INSTRUCTIVO DE LLENADO PARA EL FORMULARIO'

    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    wb.save(ruta)
    return ruta


# ---------------------------------------------------------------------------
# SGC-FOR-004 — Acta de desincorporación de archivos inactivos
# ---------------------------------------------------------------------------

ARCHIVO_REAL_SGC004 = 'SGC-FOR-004 Acta desinc Arch Inac.xlsx'


def ruta_plantilla_real_004(base):
    """Ruta absoluta al XLSX oficial del FOR-004 (o ``None``)."""
    ruta = os.path.join(str(base), 'documentacion', ARCHIVO_REAL_SGC004)
    return ruta if os.path.exists(ruta) else None


def crear_plantilla_for_004(ruta):
    """XLSX mínimo con el layout del FOR-004.

    Reproduce merges y filas de la plantilla oficial: fecha (13), tabla de
    participantes (15–19), propósito (21), registros (23–31), departamento
    (33) y bloques de firma (40–41). Incluye una hoja instructiva para
    comprobar que la generación la conserva.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'SGC-FOR-004'

    ws['C1'] = 'ACTA DE DESINCORPORACION DE ARCHIVOS INACTIVOS'
    ws.merge_cells('C1:J2')
    ws['C3'] = 'INFORMACION DOCUMENTADA'
    ws.merge_cells('C3:J3')
    ws['C4'] = '7.5.3.2 CONTROL DE REGISTROS'
    ws.merge_cells('C4:J5')

    for rango, texto in (
        ('A6:B7', 'Nº DE IDENTIFICACION:          SGC - FOR - 004'),
        ('C6:D7', 'VERSION: 1'),
        ('E6:F7', 'EDICION:  1'),
        ('G6:H7', 'FECHA DE VIGENCIA:  18/03/2025'),
        ('I6:J7', 'PAGINA:   1 DE 1'),
        ('A8:J8', 'PROCEDIMIENTO DE CONTROL DE REGISTROS'),
        ('A10:J10', 'ACTA DE DESINCORPORACION DE ARCHIVOS INACTIVOS'),
        (
            'A13:J13',
            'En el día de hoy _______________ (    ) de ____________ de '
            '________, se reunieron los ciudadanos:',
        ),
        ('A15:E15', 'NOMBRE'),
        ('F15:J15', 'CARGO'),
        (
            'A21:J21',
            'Con el propósito de llevar a cabo el acto de desincorporación o '
            'destrucción de los siguientes registros inactivos, por haber '
            'finalizado su tiempo de retención:',
        ),
        (
            'A33:J33',
            'Pertenecientes al Departamento/ Coordinación / Área: ',
        ),
    ):
        ws.merge_cells(rango)
        ws[rango.split(':')[0]] = texto

    for fila in range(16, 20):
        ws.merge_cells(f'A{fila}:E{fila}')
        ws.merge_cells(f'F{fila}:J{fila}')

    for fila in range(23, 32):
        ws.merge_cells(f'A{fila}:J{fila}')
        ws[f'A{fila}'] = '§\xa0\xa0 \xa0'

    ws['A38'] = 'En señal de conformidad, firman la presente acta.'
    for rango in ('A40:D40', 'A41:D41', 'F40:I40', 'F41:I41'):
        ws.merge_cells(rango)

    # Hoja instructiva (la generación debe conservarla intacta).
    idl = wb.create_sheet('SGC-IDL-010')
    idl['B1'] = 'INSTRUCTIVO DE LLENADO PARA EL FORMULARIO'

    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    wb.save(ruta)
    return ruta


def leer_celda_de_hoja(xlsx_bytes, ref, indice=0):
    """Valor de una celda de la hoja ``indice`` (0 = primera) del XLSX."""
    with zipfile.ZipFile(BytesIO(xlsx_bytes)) as zf:
        hojas = sorted(
            n for n in zf.namelist()
            if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')
        )
        # El orden de los ficheros sheetN.xml puede no coincidir con el de
        # pestañas, pero la plantilla sintética se crea en orden.
        raiz = ET.fromstring(zf.read(hojas[indice]))
    celda = raiz.find(f'.//{_NS_SS}c[@r="{ref}"]')
    if celda is None:
        return None
    if celda.get('t') == 'inlineStr':
        nodo = celda.find(f'{_NS_SS}is/{_NS_SS}t')
    else:
        nodo = celda.find(f'{_NS_SS}v')
    return nodo.text if nodo is not None else None


# ---------------------------------------------------------------------------
# SGC-FOR-006 — Lista Maestra de Documentos
# ---------------------------------------------------------------------------

ARCHIVO_REAL_SGC006 = 'SGC-FOR-006 Lista Maestra.xlsx'


def ruta_plantilla_real_006(base):
    """Ruta absoluta al XLSX oficial del FOR-006 (o ``None``)."""
    ruta = os.path.join(str(base), 'documentacion', ARCHIVO_REAL_SGC006)
    return ruta if os.path.exists(ruta) else None


def crear_plantilla_for_006(ruta):
    """XLSX mínimo con el layout del FOR-006.

    Reproduce merges y cabeceras del formato oficial: cabecera (1–8), tabla
    con encabezado en 10–12 y 22 filas de datos (13–34). Incluye una hoja
    instructiva para comprobar que la generación la conserva.
    """
    from openpyxl.styles import Border, Side

    fino = Side(style='thin')
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'SGC-FOR-006'

    ws['C1'] = 'LISTA MAESTRA DE DOCUMENTOS '
    ws.merge_cells('C1:N2')
    ws['C3'] = 'INFORMACION DOCUMENTADA'
    ws.merge_cells('C3:N3')
    ws['C4'] = '7.5.3.2 CONTROL DE LA INFORMACION DOCUMENTADA'
    ws.merge_cells('C4:N5')

    for rango, texto in (
        ('A6:D7', 'Nº DE IDENTIFICACION:  SGC - FOR - 006'),
        ('E6:F7', 'VERSION: 1'),
        ('G6:H7', 'EDICION: 1'),
        ('I6:K7', 'FECHA DE VIGENCIA: 18/03/2025'),
        ('L6:N7', 'PAGINA:   1 DE 1'),
        ('A8:N8', 'PROCEDIMIENTO DE CONTROL DE DOCUMENTOS'),
        ('A10:B10', 'Documento de fuente'),
        ('A11:A12', 'Ext'),
        ('B11:B12', 'Int'),
        ('C10:C12', 'No. de identificación'),
        ('D10:D12', 'Título'),
        ('E10:E12', 'Uso/ confi-dencial'),
        ('F10:F12', 'Fecha'),
        ('G10:G12', 'Nº Ediciòn'),
        ('H10:H12', 'No. Versión'),
        ('I10:J11', 'Permanencia en archivo'),
        ('I12', 'Activo'),
        ('J12', 'Inactivo'),
        ('K10:K12', 'Forma de Archivo y Ubicación'),
        ('L10:L12', 'Distribución'),
        ('M10:M12', 'Responsable'),
        ('N10:N12', 'Empresa '),
    ):
        if ':' in rango:
            ws.merge_cells(rango)
        ws[rango.split(':')[0]] = texto

    for fila in range(10, 35):
        for col in 'ABCDEFGHIJKLMN':
            ws[f'{col}{fila}'].border = borde

    # Hoja instructiva (la generación debe conservarla intacta).
    idl = wb.create_sheet('SGC-IDL-012')
    idl['B1'] = 'INSTRUCTIVO DE LLENADO PARA EL FORMULARIO DE LISTA MAESTRA'

    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    wb.save(ruta)
    return ruta


# ---------------------------------------------------------------------------
# SGC-FOR-007 — Programa de Revisión por la Dirección
# ---------------------------------------------------------------------------

ARCHIVO_REAL_SGC007 = 'SGC-FOR-007 Programa de Rev. Direc.xlsx'


def ruta_plantilla_real_007(base):
    ruta = os.path.join(str(base), 'documentacion', ARCHIVO_REAL_SGC007)
    return ruta if os.path.exists(ruta) else None


def crear_plantilla_for_007(ruta):
    """XLSX mínimo con el layout del FOR-007 (matriz dinámica).

    Cabecera (1–13), bloque de aspectos 14–27 (prototipo; se reconstruye al
    exportar), leyenda 29, CONFORME 31 y firmas/etiquetas 32–35. Incluye una
    hoja instructiva para comprobar que se conserva.
    """
    from openpyxl.styles import Alignment, Border, Font, Side

    fino = Side(style='thin')
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'SGC-FOR-007'

    ws['C1'] = 'PROGRAMA DE REVISION POR LA DIRECCION'
    ws.merge_cells('C1:N2')
    ws['C3'] = 'INFORMACION DOCUMENTADA'
    ws.merge_cells('C3:N3')
    ws['C4'] = '9.3 REVISION POR LA DIRECCION'
    ws.merge_cells('C4:N5')
    for rango, texto in (
        ('A6:B7', 'Nº DE IDENTIFICACION:  SGC-FOR-007'),
        ('C6:E7', 'VERSION: 1'),
        ('F6:H7', 'EDICION: 1'),
        ('I6:K7', 'FECHA DE VIGENCIA: 18/03/2025'),
        ('L6:N7', 'PAGINA:   1'),
        ('A8:N8', 'PROCEDIMIENTO DE REVISION POR LA DIRECCION'),
        ('A10:N10', 'AÑO A EVALUAR:'),
        ('H12:J12', 'TRIMESTRE I'),
        ('K12:M12', 'TRIMESTRE II'),
        ('N12:P12', 'TRIMESTRE III'),
        ('Q12:S12', 'TRIMESTRE IV'),
        ('A13:D13', 'ASPECTOS DEL SISTEMA DE GESTION DE LA CALIDAD A EVALUAR'),
        ('E13:G13', 'INFORMACION DOCUMENTADA A REVISAR'),
        ('A31:N31', 'CONFORME'),
        ('A34:D35', 'Presidente de  la Junta Directiva'),
        ('E34:H35', 'Gerencia General'),
        ('I34:N35', 'Fecha'),
    ):
        ws.merge_cells(rango)
        ws[rango.split(':')[0]] = texto
    for col, texto in zip('HIJKLMNOPQRS', [
        'ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN',
        'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC',
    ]):
        ws[f'{col}13'] = texto

    for fila in range(14, 28):
        ws.merge_cells(f'A{fila}:D{fila}')
        ws.merge_cells(f'E{fila}:G{fila}')
        ws[f'A{fila}'] = f'Aspecto {fila}'
    for fila in range(12, 36):
        if fila in (28, 29, 30):
            continue
        for col in 'ABCDEFGHIJKLMN':
            ws[f'{col}{fila}'].border = borde
            ws[f'{col}{fila}'].alignment = Alignment(wrap_text=True)
    ws['B29'] = 'Ï: Planificado;  V: Ejecutado'

    for rango in (
        'A32:D33', 'E32:H33', 'I32:J33', 'K32:L33', 'M32:N33',
    ):
        ws.merge_cells(rango)
    for col in 'ABCDEFGHIJKLMN':
        ws[f'{col}31'].font = Font(bold=True)

    idl = wb.create_sheet('SGC-IDL-013')
    idl['B1'] = 'INSTRUCTIVO DE LLENADO PARA EL FORMULARIO'

    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    wb.save(ruta)
    return ruta
