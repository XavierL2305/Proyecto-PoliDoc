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
