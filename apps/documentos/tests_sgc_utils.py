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


def crear_plantilla_for_009(ruta):
    """XLSX sintético con el layout del FOR-009 (Minuta).

    Reproduce la hoja ``SGC-FOR-009`` (cabecera, datos, 7 asistentes, bloques
    de texto y aprobación) y una hoja instructiva ``SGC-IDL-015``. Las cajas de
    objetivo/lugar/fecha/responsable/duración quedan SIN mergear a propósito:
    el export las mergea y ajusta wrap.
    """
    from openpyxl.styles import Alignment, Border, Side

    fino = Side(style='thin')
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'SGC-FOR-009'

    for rango, texto in (
        ('C1:J2', 'MINUTA DE REUNION'),
        ('C3:J3', 'INFORMACION DOCUMENTADA'),
        ('C4:J5', '9.3 REVISION POR LA DIRECCION'),
        ('A6:B7', 'Nº DE IDENTIFICACION:  SGC-FOR-009'),
        ('C6:D7', 'VERSION: 1'),
        ('E6:F7', 'EDICION: 1'),
        ('G6:H7', 'FECHA DE VIGENCIA: 18/03/2025'),
        ('I6:J7', 'PAGINA:   1 DE 1'),
        ('A8:J8', 'PROCEDIMIENTO DE REVISION POR LA DIRECCION'),
        ('B10:C11', ''),
        ('A13:B13', 'Objetivo de la Reunión:'),
        ('A25:C25', 'Nombre'),
        ('D25:F25', 'Cargo'),
        ('G25:J25', 'Departamento/área/proceso'),
        ('A33:J36', 'Descripciòn del Tema tratado:'),
        ('A38:J44', 'Conclusiones:'),
        ('A46:J52', 'Acuerdos Cerrados:'),
        ('A54:J60', 'Acuerdos Pendientes:'),
        ('A65:H65', 'Responsables de la Aprobaciòn:'),
    ):
        ws.merge_cells(rango)
        ws[rango.split(':')[0]] = texto

    for fila in range(26, 33):
        ws.merge_cells(f'A{fila}:C{fila}')
        ws.merge_cells(f'D{fila}:F{fila}')
        ws.merge_cells(f'G{fila}:J{fila}')
        for col in 'ABCDEFGHIJ':
            ws[f'{col}{fila}'].border = borde

    ws['A11'] = 'REUNION Nº:'
    ws['A16'] = 'Lugar:'
    ws['A18'] = 'Fecha:'
    ws['A20'] = 'Responsable de la Elaboración:'
    ws['A22'] = 'Duración:'
    ws['I6'] = 'PAGINA:   1 DE 1'
    for ref in ('A11', 'A16', 'A18', 'A20', 'A22', 'B16', 'B18', 'B20',
                'B22', 'C13', 'A65'):
        ws[ref].alignment = Alignment(vertical='center', wrap_text=True)

    idl = wb.create_sheet('SGC-IDL-015')
    idl['B1'] = 'INSTRUCTIVO DE LLENADO PARA EL FORMULARIO DE MINUTA'
    idl['B2'] = 'PAGINA: 1 DE 2'

    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    wb.save(ruta)
    return ruta


def _inyectar_casillas_for_010(ruta):
    """Agrega las 4 casillas (shapes) de Detección al ``drawing1.xml``.

    openpyxl no crea formas; se inyectan como ``xdr:sp`` con los mismos
    nombres que usa la plantilla oficial (Rectángulo 70/71/79/80).
    """
    from lxml import etree

    xdr = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
    a = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    plantilla = '''<xdr:twoCellAnchor xmlns:xdr="{xdr}" xmlns:a="{a}">
  <xdr:from><xdr:col>{col}</xdr:col><xdr:colOff>0</xdr:colOff>
    <xdr:row>13</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
  <xdr:to><xdr:col>{col}</xdr:col><xdr:colOff>285750</xdr:colOff>
    <xdr:row>13</xdr:row><xdr:rowOff>228600</xdr:rowOff></xdr:to>
  <xdr:sp macro="" textlink="">
    <xdr:nvSpPr>
      <xdr:cNvPr id="{cid}" name="{nombre}"/><xdr:cNvSpPr/>
    </xdr:nvSpPr>
    <xdr:spPr>
      <a:xfrm><a:off x="0" y="0"/><a:ext cx="285750" cy="228600"/></a:xfrm>
      <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
      <a:solidFill><a:schemeClr val="bg1"/></a:solidFill>
      <a:ln><a:solidFill><a:sysClr val="windowText" lastClr="000000"/>
      </a:solidFill></a:ln>
    </xdr:spPr>
    <xdr:txBody><a:bodyPr/><a:lstStyle/><a:p/></xdr:txBody>
  </xdr:sp>
  <xdr:clientData/>
</xdr:twoCellAnchor>'''

    with zipfile.ZipFile(ruta) as zin:
        entradas = [(info, zin.read(info.filename)) for info in zin.infolist()]

    with zipfile.ZipFile(ruta, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info, data in entradas:
            if info.filename == 'xl/drawings/drawing1.xml':
                root = etree.fromstring(data)
                for cid, col, nombre in (
                    (71, 3, 'Rectángulo 70'),
                    (72, 4, 'Rectángulo 71'),
                    (80, 5, 'Rectángulo 79'),
                    (81, 6, 'Rectángulo 80'),
                ):
                    xml = plantilla.format(
                        xdr=xdr, a=a, col=col, cid=cid, nombre=nombre,
                    )
                    root.append(etree.fromstring(xml.encode('utf-8')))
                data = etree.tostring(
                    root, xml_declaration=True, encoding='UTF-8',
                    standalone=True,
                )
            zout.writestr(info, data)


_EXTERNO_FOR_010 = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<externalLink '
    'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships">'
    '<externalBook r:id="rId1">'
    '<sheetNames><sheetName val="PLAN DE ACCION"/></sheetNames>'
    '<sheetDataSet><sheetData sheetId="0" refreshError="1"/></sheetDataSet>'
    '</externalBook></externalLink>'
)

_RELS_EXTERNO_FOR_010 = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/externalLinkPath" '
    'Target="/Users/acastellano/Documents/DPR-23 Auditoria Interna.xlsx" '
    'TargetMode="External"/>'
    '</Relationships>'
)


def _inyectar_vinculo_externo_for_010(ruta):
    """Inyecta un ``externalLink`` como el de la plantilla oficial.

    La plantilla real incluye un vínculo a un XLSX local de otra máquina, que
    hace que Excel muestre el aviso de "fuentes externas". El export debe
    eliminarlo; este fixture garantiza que se pruebe ese camino.
    """
    from lxml import etree

    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    pkg = 'http://schemas.openxmlformats.org/package/2006/relationships'
    ct = 'http://schemas.openxmlformats.org/package/2006/content-types'

    def _serializar(root):
        return etree.tostring(
            root, xml_declaration=True, encoding='UTF-8', standalone=True,
        )

    with zipfile.ZipFile(ruta) as zin:
        entradas = [(info, zin.read(info.filename)) for info in zin.infolist()]

    with zipfile.ZipFile(ruta, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info, data in entradas:
            if info.filename == 'xl/workbook.xml':
                root = etree.fromstring(data)
                refs = etree.Element(f'{{{main}}}externalReferences')
                referencia = etree.SubElement(
                    refs, f'{{{main}}}externalReference',
                )
                referencia.set(f'{{{rel}}}id', 'rIdExt1')
                hojas = root.find(f'{{{main}}}sheets')
                root.insert(list(root).index(hojas) + 1, refs)
                definidos = root.find(f'{{{main}}}definedNames')
                if definidos is None:
                    definidos = etree.Element(f'{{{main}}}definedNames')
                    calc = root.find(f'{{{main}}}calcPr')
                    if calc is not None:
                        root.insert(list(root).index(calc), definidos)
                    else:
                        root.append(definidos)
                for nombre, valor in (
                    ('_xlchart.v1.0', "'[1]PLAN DE ACCION'!$A$51:$A$56"),
                    ('_xlchart.v1.1', "'[1]PLAN DE ACCION'!$F$50"),
                    ('_xlchart.v1.2', "'[1]PLAN DE ACCION'!$F$51:$F$56"),
                ):
                    item = etree.SubElement(
                        definidos, f'{{{main}}}definedName',
                    )
                    item.set('name', nombre)
                    item.set('hidden', '1')
                    item.text = valor
                data = _serializar(root)
            elif info.filename == 'xl/_rels/workbook.xml.rels':
                root = etree.fromstring(data)
                nuevo = etree.SubElement(
                    root, f'{{{pkg}}}Relationship',
                )
                nuevo.set('Id', 'rIdExt1')
                nuevo.set('Type', f'{rel}/externalLink')
                nuevo.set('Target', 'externalLinks/externalLink1.xml')
                data = _serializar(root)
            elif info.filename == '[Content_Types].xml':
                root = etree.fromstring(data)
                override = etree.SubElement(root, f'{{{ct}}}Override')
                override.set(
                    'PartName', '/xl/externalLinks/externalLink1.xml',
                )
                override.set(
                    'ContentType',
                    'application/vnd.openxmlformats-officedocument.'
                    'spreadsheetml.externalLink+xml',
                )
                data = _serializar(root)
            zout.writestr(info, data)
        zout.writestr(
            'xl/externalLinks/externalLink1.xml', _EXTERNO_FOR_010,
        )
        zout.writestr(
            'xl/externalLinks/_rels/externalLink1.xml.rels',
            _RELS_EXTERNO_FOR_010,
        )


def crear_plantilla_for_010(ruta):
    """XLSX sintético con el layout del FOR-010 (Plan de Acción ante NC).

    Reproduce la hoja ``SGC-FOR-010`` (identificación, tablas, causas de
    ejemplo, fórmulas de totales, firmas), el gráfico Pareto y una hoja
    instructiva ``SGC-IDL-016``. Las casillas de detección se inyectan como
    formas en ``drawing1.xml``.
    """
    from datetime import date

    from openpyxl.chart import BarChart, Reference

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'SGC-FOR-010'
    for columna, ancho in (
        ('B', 12.71), ('C', 11.14), ('D', 21.0),
        ('E', 13.71), ('F', 21.0), ('G', 14.43),
    ):
        ws.column_dimensions[columna].width = ancho

    ws['B1'] = 'PLANES DE ACCION ANTE NO CONFORMIDADES'
    ws['B2'] = 'INFORMACION DOCUMENTADA'
    ws['B3'] = 'NO CONFORMIDAD Y ACCION CORRECTIVA'
    for celda, texto in (
        ('B4', 'Nº DE IDENTIFICACION'), ('D4', 'VERSION'),
        ('E4', 'EDICION'), ('F4', 'FECHA DE VIGENCIA'), ('G4', 'PAGINA'),
        ('B5', 'SGC-FOR-010'), ('D5', 1), ('E5', 1),
        ('F5', date(2025, 3, 18)), ('G5', '1 DE 1'),
        ('A6', 'PROCEDIMIENTO DE NO CONFORMIDAD Y ACCION CORRECTIVA'),
        ('A7', 'NC Nº'), ('E7', 'FECHA DEL ANALISIS'),
        ('A9', 'Proceso/ Departamento/ Unidad de Origen: '),
        ('A11', 'Descripciòn de la No conformidad: '),
        ('A14', 'Detecciòn de la No conformidad: '),
        ('C14', 'Auditoria'),
        ('D14', '                  Revisiòn por la Direcciòn'),
        ('F14', 'Quejas o Reclamos'), ('G14', 'Otros:'),
        ('A15', '1. ACCIONES DE CORRECCION'),
        ('D15', 'FECHA INI'), ('E15', 'FECHA FIN'),
        ('F15', 'RESPONSABLE'), ('G15', '% DE AVANCE'),
        ('A23', '2. ANALISIS Y SOLUCION DE PROBLEMAS'),
        ('A24', '2.1. INTEGRANTES DEL EQUIPO'),
        ('E24', 'LIDER DEL EQUIPO'),
        ('A31', '2.2. DATOS Y DESCRIPCION DEL PROBLEMA'),
        ('A32', 'GRAFICO O ESQUEMA REPRESENTATIVO DEL PROBLEMA'),
        ('A43', '2.3. ANALISIS DEL PROBLEMA'),
        ('A50', '2.4. CAUSAS DEL PROBLEMA'),
        ('A51', 'DESCRIPCION'), ('F51', 'INCIDENCIA'),
        ('G51', '% DE INCIDENCIA'),
        ('A59', '3. ACCIONES DE CORRECTIVAS'),
        ('D59', 'FECHA INI'), ('E59', 'FECHA FIN'),
        ('F59', 'RESPONSABLE'), ('G59', '% DE AVANCE'),
        ('A67', '4. REVISION DE LA EFICACIA DE LAS ACCIONES'),
        ('A69', 'FIRMA DEL LIDER'),
        ('D69', 'FIRMA DEL RESPONSABLE DE HACER LA REVISION'),
    ):
        ws[celda] = texto

    for rango in (
        'A14:B14', 'A15:C15', 'A23:G23', 'A24:D24', 'E24:G24',
        'A31:G31', 'A32:D42', 'A43:G43', 'A50:G50', 'A51:E51',
        'A58:C58', 'A59:C59', 'A67:G67', 'A68:G68', 'A69:C69',
        'D69:G69', 'A70:C70', 'D70:G70', 'E32:G33',
        'E25:G30',
    ):
        ws.merge_cells(rango)
    for fila in range(16, 23):
        ws.merge_cells(f'A{fila}:C{fila}')
    for fila in range(25, 31):
        ws.merge_cells(f'A{fila}:D{fila}')
    for fila in range(34, 43):
        ws.merge_cells(f'E{fila}:G{fila}')
    for fila in range(44, 50):
        ws.merge_cells(f'A{fila}:G{fila}')
    for fila in range(52, 58):
        ws.merge_cells(f'A{fila}:E{fila}')
    for fila in range(60, 67):
        ws.merge_cells(f'A{fila}:C{fila}')

    # Filas sobrantes del bloque de descripción con contenido de plantilla:
    # el export debe limpiarlas cuando el texto no las ocupa.
    ws['D12'] = 'TEXTO DE EJEMPLO (fila 12)'
    ws['D13'] = 'TEXTO DE EJEMPLO (fila 13)'

    causas = [
        ('FALTA DE PERSONAL PARA LA REALIZACION DE LOS ESTUDIOS', 87),
        ('FALTA DE CONOCIMIENTO DE LA METODOLOGIA', 38),
        ('MATERIALES DE MALA CALIDAD', 33),
        ('FALTA DE DISPONIBILIDAD DE LA MAQUINA', 26),
        ('FALTA DE CONDICIONES AMBIENTALES', 50),
        ('EQUIPO DESCALIBRADO', 77),
    ]
    for indice, (descripcion, incidencia) in enumerate(causas):
        fila = 52 + indice
        ws[f'A{fila}'] = descripcion
        ws[f'F{fila}'] = incidencia
        ws[f'G{fila}'] = f'=IFERROR(F{fila}/$F$58,"")'
    ws['F58'] = '=SUM(F52:F57)'
    ws['G58'] = '=SUM(G52:G57)'

    grafico = BarChart()
    grafico.title = 'DIAGRAMA DE PARETO'
    datos = Reference(ws, min_col=6, min_row=51, max_row=57)
    categorias = Reference(ws, min_col=1, min_row=52, max_row=57)
    grafico.add_data(datos, titles_from_data=True)
    grafico.set_categories(categorias)
    ws.add_chart(grafico, 'H50')

    idl = wb.create_sheet('SGC-IDL-016')
    idl['B1'] = 'INSTRUCTIVO DE LLENADO PARA EL PLAN DE ACCION ANTE NC'
    idl['B2'] = 'PAGINA: 1 DE 3'

    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    wb.save(ruta)
    _inyectar_casillas_for_010(ruta)
    _inyectar_vinculo_externo_for_010(ruta)
    return ruta


# ---------------------------------------------------------------------------
# SGC-FOR-017 — Descripción de Procesos
# ---------------------------------------------------------------------------

ARCHIVO_REAL_SGC017 = 'SGC-FOR-017 Descripción de Procesos.xlsx'


def ruta_plantilla_real_017(base):
    """Ruta absoluta al XLSX oficial del FOR-017 (o ``None``)."""
    ruta = os.path.join(str(base), 'documentacion', ARCHIVO_REAL_SGC017)
    return ruta if os.path.exists(ruta) else None


def crear_plantilla_for_017(ruta):
    """XLSX sintético con el layout del FOR-017 (Descripción de Procesos).

    Reproduce la hoja ``SGC-FOR-017``: identificación (7-10), cajas de
    dueño/participa/cliente (10-13), objetivo/alcance (14-20), matriz
    21-48 con el merge vertical ``H24:H47``, bloque de recursos/información
    (50-55), indicadores 57-58 **sin la fila 59** (el export debe crearla
    copiando el estilo de la 58) y firmas 60-67 con sus rótulos. Las filas
    24/25 llevan las alturas 121.5/63.75 de la plantilla oficial. Incluye
    la hoja instructiva ``SGC-IDL-023``.
    """
    from openpyxl.styles import Alignment, Border, Font, Side

    fino = Side(style='thin')
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'SGC-FOR-017'
    for columna, ancho in (
        ('A', 17.43), ('B', 16.71), ('C', 14.14), ('D', 15.29),
        ('E', 13.14), ('F', 18.86), ('G', 13.57), ('H', 14.86),
    ):
        ws.column_dimensions[columna].width = ancho

    for ref, texto in (
        ('B1', 'DESCRIPCION DE PROCESOS'),
        ('B2', 'INFORMACION DOCUMENTADA'),
        ('B3', 'SISTEMA DE GESTION DE LA CALIDAD Y SUS PROCESOS'),
        ('B4', 'Nº DE IDENTIFICACION:'), ('D4', 'VERSION:'),
        ('E4', 'EDICION:'), ('F4', 'FECHA DE VIGENCIA:'),
        ('H4', 'PAGINA:'),
        ('B5', 'SGC-FOR-017'), ('D5', '1'), ('E5', '1'),
        ('F5', '24/10/2025'), ('H5', '1 de 2'),
        ('A7', 'CODIGO DEL PROCESO'),
        ('C7', 'NOMBRE DEL PROCESO:'),
        ('F7', 'TIPO DE PROCESO'),
        ('F8', 'CONTRATADO'), ('G8', 'NO CONTRATADO'), ('H8', 'MIXTO'),
        ('A10', 'DUEÑO (LIDER) DEL PROCESO'),
        ('C10', 'PARTCIPA'), ('F10', 'CLIENTE'),
        ('A14', 'OBJETIVO GENERAL'), ('A17', 'ALCANCE'),
        ('A21', 'FUENTES DE LA ENTRADA'),
        ('B21', 'ENTRADAS DEL PROCESO'),
        ('C21', 'SECUENCIA DEL PROCESO'),
        ('F21', 'RESPONSABLES'),
        ('G21', 'SALIDAS DEL PROCESO'),
        ('H21', 'RECEPTORES DEL PROCESO'),
        ('A50', 'RECURSOS NECESARIOS PARA EL PROCESO:'),
        ('A52', 'INFORMACION DOCUMENTADA:'),
        ('A54', 'INTERACCION DE ESTOS PROCESOS:'),
        ('A56', 'INDICADORES DE GESTION'),
        ('A57', 'NOMBRE DEL INDICADOR'),
        ('B57', 'OBJETIVO DEL INDICADOR'),
        ('D57', 'FORMULA DEL INDICADOR'),
        ('F57', 'FRECUENCIA DEL INDICADOR'),
        ('G57', 'RESPONSABLE'),
        ('A60', 'Elaborado por:'), ('C60', 'Revisado por:'),
        ('F60', 'Aprobado por:'),
        ('A61', 'Nombre y Apellido:  '),
        ('C61', 'Nombre y Apellido:    '),
        ('F61', 'Nombre y Apellido:'),
        ('A63', 'Cargo:  '), ('C63', 'Cargo:  '), ('F63', 'Cargo:   '),
        ('A65', 'Firma:'), ('C65', 'Firma:'), ('F65', 'Firma:'),
        ('A67', 'Fecha:'), ('C67', 'Fecha:'), ('F67', 'Fecha:'),
    ):
        ws[ref] = texto

    # Cajas de datos: bordes como en la plantilla oficial.
    for fila in list(range(11, 14)) + list(range(21, 50)) + [57, 58] \
            + list(range(61, 68)):
        for col in 'ABCDEFGH':
            ws[f'{col}{fila}'].border = borde
    for col in 'ABCDEFGH':
        ws[f'{col}58'].font = Font(bold=True)
        ws[f'{col}58'].alignment = Alignment(wrap_text=True, vertical='top')
    ws['H24'].alignment = Alignment(wrap_text=True, vertical='top')

    for rango in (
        'A7:B8', 'C7:E8', 'F7:H7', 'A9:B9', 'C9:E9',
        'A10:B10', 'C10:E10', 'F10:H10',
        'A14:H14', 'A15:H16', 'A17:H17', 'A18:H20',
        'C21:E21', 'H24:H47',
        'A50:H50', 'A51:H51', 'A52:H52', 'A53:H53',
        'A54:H54', 'A55:H55', 'A56:H56',
        'B57:C57', 'D57:E57', 'G57:H57',
        'B58:C58', 'D58:E58', 'G58:H58',
        'A60:B60', 'C60:E60', 'F60:H60',
        'A61:B62', 'C61:E62', 'F61:H62',
        'A63:B64', 'C63:E64', 'F63:H64',
        'A65:B66', 'C65:E66', 'F65:H66',
        'A67:B67', 'C67:E67', 'F67:H67',
    ):
        ws.merge_cells(rango)
    for fila in range(11, 14):
        ws.merge_cells(f'C{fila}:E{fila}')
        ws.merge_cells(f'F{fila}:H{fila}')

    # Alturas fijadas por la plantilla (no deben aplastarse al exportar).
    for fila, alto in (
        (21, 30), (24, 121.5), (25, 63.75), (53, 39), (57, 30),
        (61, 15),
    ):
        ws.row_dimensions[fila].height = alto

    # PageSetup de la plantilla oficial: A4 vertical al 70% → 2 páginas.
    ws.page_setup.orientation = 'portrait'
    ws.page_setup.scale = 70

    idl = wb.create_sheet('SGC-IDL-023')
    idl['B1'] = (
        'INSTRUCTIVO DE LLENADO PARA EL FORMULARIO DE LA DESCRIPCION '
        'DE PROCESOS'
    )
    idl['A4'] = 'Nº DE IDENTIFICACION:      SGC-IDL-023'
    idl['E4'] = 'FECHA DE VIGENCIA:                                 24/10/2025'
    idl['G4'] = 'PAGINA: 1 DE 3'
    idl['A81'] = (
        '1. Codigo del Proceso: Escribir el còdigo del proceso de acuerdo a '
        'la nomenclatura indicada en el procedimiento de control de '
        'documentos'
    )
    idl['A93'] = 'PH: Policlinica Hospitalizaciòn'
    idl['A94'] = 'DPR: Descripciòn del Proceso'
    idl['A137'] = (
        '26. Fecha: Se escribe la fecha en que se elaboro, reviso y '
        'aprobò la descripciòn del proceso'
    )

    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    wb.save(ruta)
    return ruta


# ---------------------------------------------------------------------------
# Verificación de la copia preparada para PDF (compartida por FOR-003/004/006/
# 007/009)
# ---------------------------------------------------------------------------

def verificar_preparacion_pdf(caso, xlsx_pdf, ruta_descarga, hoja,
                              instructivo):
    """Comprueba la separación entre lo que ve el PDF y lo que se descarga.

    Args:
        caso: ``TestCase`` que hace las aserciones.
        xlsx_pdf: bytes entregados al convertidor a PDF.
        ruta_descarga: ruta del XLSX almacenado (el que se descarga).
        hoja: nombre de la hoja del formulario.
        instructivo: nombre de la hoja del instructivo (``SGC-IDL-*``).
    """
    preparado = openpyxl.load_workbook(BytesIO(xlsx_pdf))
    formulario = preparado[hoja]
    propiedades = formulario.sheet_properties.pageSetUpPr
    caso.assertIsNotNone(propiedades, 'Falta sheetPr/pageSetUpPr en el PDF')
    caso.assertTrue(propiedades.fitToPage)
    caso.assertEqual(formulario.page_setup.fitToWidth, 1)
    caso.assertEqual(formulario.page_setup.fitToHeight, 0)
    caso.assertIsNone(formulario.page_setup.scale, 'Queda una escala fija')
    caso.assertEqual(formulario.sheet_state, 'visible')
    caso.assertEqual(
        preparado[instructivo].sheet_state, 'hidden',
        'El instructivo no está oculto en la copia del PDF',
    )

    with open(ruta_descarga, 'rb') as fh:
        descargable = openpyxl.load_workbook(fh)
    caso.assertEqual(
        descargable[instructivo].sheet_state, 'visible',
        'El XLSX descargable perdió el instructivo',
    )
    ajustado = descargable[hoja].sheet_properties.pageSetUpPr
    caso.assertFalse(
        ajustado is not None and ajustado.fitToPage,
        'El XLSX descargable quedó ajustado para PDF',
    )
