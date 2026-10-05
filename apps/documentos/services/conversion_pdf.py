"""Conversión office → PDF: tamaño de papel, márgenes y saneo del render.

El PDF sale con el ``pageSetup`` del archivo de origen (LibreOffice respeta el
documento, no hay opción de línea de comandos que cambie el papel). Si el
origen declara A4 y el contenido es más alto que la A4, las tablas se parten
entre páginas y las últimas filas saltan a la página siguiente; si en cambio el
papel es demasiado alto (Folio), el contenido de la página siguiente se
descoloca con el de la anterior.

Aquí se parchea el **tamaño de papel** y los **márgenes superior/inferior** de
una **copia temporal** (el archivo subido nunca se modifica) y se convierte esa
copia. El papel lo decide ``settings.PAPEL_PDF_CONVERSION`` (por defecto A4) y
los márgenes ``settings.MARGEN_{SUPERIOR,INFERIOR}_PDF_CONVERSION`` (0.4"
para que el formulario siga cabiendo en una página en A4).

La misma copia se **sanea** forzando un salto de página antes del primer
título repetido: las plantillas (SGC-FOR-001/002/005) tienen dos secciones, la
segunda empieza a mitad de página y su cabecera (título **y logo**) queda
cortada por el borde inferior de la anterior. Ese salto arregla a la vez el
título huérfano y el logo recortado.

La detección de secciones es **estructural**: en cada hoja, el logo de la
cabecera es la imagen anclada en la **fila 0**, y toda otra ancla que reutilice
esa misma imagen marca el inicio de sección. Las plantillas SGC reutilizan el
mismo ``rId`` en la fila 59 (``oneCellAnchor``), que es el logo de la última
sección. Si una hoja no reutiliza imagen, se cae al título repetido de siempre.

Los logos de cada sección **no se tocan** en la conversión normal: borrarlos
"por ser repetidos" se probó que quitaba el logo de la última página. Cuando el
QA del PDF detecta un logo recortado, ``generar_alternativas()`` ofrece copias
con el logo de sección una fila más abajo y ``convertir_lote()`` las convierte
de golpe para elegir la más limpia (ver services/pdf_adaptativo.py).

Tamaños: códigos OOXML ``ST_PaperSize`` (lo que escribe Excel/Word) —
``Folio 8.5"x13" = 14``, ``Legal 8.5"x14" = 5``, ``Letter 8.5"x11" = 1``,
``A4 = 9``. El valor por defecto viene de ``settings.PAPEL_PDF_CONVERSION``.
"""

import logging
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile

from django.conf import settings
from lxml import etree

logger = logging.getLogger(__name__)

NS_XL = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
NS_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS_XDR = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
TWIPS = 1440  # 1 pulgada = 1440 twips (unidades de Word)

# nombre → (código ST_PaperSize, ancho en pulgadas, alto en pulgadas)
PAPELES = {
    'letter': (1, 8.5, 11.0),
    'carta': (1, 8.5, 11.0),
    'legal': (5, 8.5, 14.0),
    'folio': (14, 8.5, 13.0),
    'oficio': (14, 8.5, 13.0),
    'a3': (8, 11.69, 16.54),
    'a4': (9, 8.27, 11.69),
}

# Hojas de un XLSX que admiten <pageSetup>
_RE_HOJA_XLSX = re.compile(r'^xl/worksheets/sheet\d+\.xml$')

# Elementos que en OOXML van justo antes/después de <pageSetup> dentro de
# una hoja; se usan para insertarlo con el orden correcto si no existe.
_DESPUES_DE = ('printOptions', 'pageMargins')
_ANTES_DE = (
    'headerFooter', 'rowBreaks', 'colBreaks', 'customProperties',
    'cellWatches', 'ignoredErrors', 'drawing', 'legacyDrawing',
    'picture', 'oleObjects', 'controls', 'tableParts', 'extLst',
)

# Ídem para <rowBreaks> (después de <headerFooter>, antes de <colBreaks>).
_DESPUES_DE_SALTOS = ('headerFooter', 'pageSetup', 'pageMargins', 'printOptions')
_ANTES_DE_SALTOS = (
    'colBreaks', 'customProperties', 'cellWatches', 'ignoredErrors',
    'drawing', 'legacyDrawing', 'picture', 'oleObjects', 'controls',
    'tableParts', 'extLst',
)


def resolver_papel(papel=None):
    """Resuelve un nombre/código de papel a ``(código, ancho, alto)``.

    ``ancho``/``alto`` van en twips (para DOCX). Devuelve ``(None, None, None)``
    cuando no hay que tocar el tamaño (valor vacío o desconocido).
    """
    if papel is None:
        papel = getattr(settings, 'PAPEL_PDF_CONVERSION', 'Folio')
    nombre = str(papel or '').strip().lower()
    if not nombre or nombre in ('auto', 'original', 'archivo', 'none', 'sin_cambio'):
        return None, None, None

    if nombre.isdigit():
        codigo = int(nombre)
        for _, (cod, ancho, alto) in PAPELES.items():
            if cod == codigo:
                return codigo, int(ancho * TWIPS), int(alto * TWIPS)
        return codigo, None, None

    datos = PAPELES.get(nombre)
    if datos is None:
        logger.warning("PAPEL_PDF_CONVERSION desconocido: %r (se respeta el original)", papel)
        return None, None, None
    codigo, ancho, alto = datos
    return codigo, int(ancho * TWIPS), int(alto * TWIPS)


# Márgenes por defecto (pulgadas) al crear <pageMargins> de una hoja que no
# tiene. Los atributos que faltan son obligatorios en OOXML.
MARGENES_XLSX_DEFECTO = {
    'left': 0.7, 'right': 0.7, 'top': 0.748, 'bottom': 0.748,
    'header': 0.315, 'footer': 0.315,
}

# Ídem para ``w:pgMar`` (twips), tal como los escribe Word: 1" = 1440.
MARGENES_DOCX_DEFECTO = {
    'top': 1440, 'right': 1440, 'bottom': 1440, 'left': 1440,
    'header': 720, 'footer': 720, 'gutter': 0,
}


def resolver_margenes(margenes=None):
    """Resuelve los márgenes superior/inferior (pulgadas) a aplicar.

    Por defecto lee ``settings.MARGEN_{SUPERIOR,INFERIOR}_PDF_CONVERSION``.
    Devuelve un dict tipo ``{'top': 0.4, 'bottom': 0.4}`` solo con los lados
    concretados, o ``None`` si no hay que tocar los márgenes.
    """
    if margenes is None:
        margenes = {
            'top': getattr(settings, 'MARGEN_SUPERIOR_PDF_CONVERSION', None),
            'bottom': getattr(settings, 'MARGEN_INFERIOR_PDF_CONVERSION', None),
        }
    if not margenes:
        return None

    salida = {}
    for clave in ('top', 'bottom'):
        valor = margenes.get(clave)
        if valor is None or valor == '':
            continue
        try:
            valor = float(valor)
        except (TypeError, ValueError):
            logger.warning("Margen PDF inválido para %r: %r (se ignora)", clave, valor)
            continue
        if valor < 0:
            logger.warning("Margen PDF negativo para %r: %r (se ignora)", clave, valor)
            continue
        salida[clave] = valor
    return salida or None


# ---------------------------------------------------------------------------
# Parcheo de la copia
# ---------------------------------------------------------------------------

def _insertar_hoja(raiz, nuevo, despues, antes):
    """Inserta un elemento en el orden que exige OOXML. Devuelve si se pudo."""
    for tag in despues:
        hermano = raiz.find('{%s}%s' % (NS_XL, tag))
        if hermano is not None:
            hermano.addnext(nuevo)
            return True
    for tag in antes:
        hermano = raiz.find('{%s}%s' % (NS_XL, tag))
        if hermano is not None:
            hermano.addprevious(nuevo)
            return True
    return False


# ---------------------------------------------------------------------------
# Saneo de la copia (títulos repetidos)
# ---------------------------------------------------------------------------


def _texto_celda(celda):
    """Valor visible de una `<c>`: `<v>` o el texto de un `<is>` en línea."""
    v = celda.find('{%s}v' % NS_XL)
    if v is not None and v.text:
        return v.text
    en_linea = celda.find('{%s}is' % NS_XL)
    if en_linea is not None:
        return ''.join(t.text or '' for t in en_linea.iter('{%s}t' % NS_XL))
    return None


def _valores_fila(fila):
    """Valores no vacíos de una `<row>`, en orden de columna."""
    return [
        texto for texto in (_texto_celda(c) for c in fila.findall('{%s}c' % NS_XL))
        if texto
    ]


def _fila_titulo_repetido(raiz):
    """Nº de fila (base 1) que repite el contenido de la fila 1.

    Las plantillas de formulario tienen dos secciones: firmas (fila 1) e
    instrucciones (fila 60), y la segunda vuelve a escribir el título. El
    salto natural de LibreOffice cae justo después de ese título y lo deja
    huérfano al final de la página anterior.
    """
    filas = raiz.findall('{%s}sheetData/{%s}row' % (NS_XL, NS_XL))
    if len(filas) < 2:
        return None
    titulo = _valores_fila(filas[0])
    if not titulo:
        return None
    for fila in filas[1:]:
        if _valores_fila(fila) == titulo:
            numero = fila.get('r')
            return int(numero) if numero and numero.isdigit() else None
    return None


def _insertar_salto_de_pagina(raiz, fila):
    """Fuerza un salto manual antes de la fila (base 1). Devuelve si se hizo.

    LibreOffice interpreta el ``id`` de un ``<brk>`` como la última fila de la
    página anterior (verificado empíricamente): para que la fila 60 abra
    página hay que escribir ``id="59"``.
    """
    indice = fila - 1
    salto = raiz.find('{%s}rowBreaks' % NS_XL)
    if salto is not None:
        if any(b.get('id') == str(indice) for b in salto.findall('{%s}brk' % NS_XL)):
            return False
    else:
        salto = etree.Element('{%s}rowBreaks' % NS_XL)
        if not _insertar_hoja(raiz, salto, _DESPUES_DE_SALTOS, _ANTES_DE_SALTOS):
            datos = raiz.find('{%s}sheetData' % NS_XL)
            if datos is None:
                return False
            datos.addnext(salto)

    brk = etree.SubElement(salto, '{%s}brk' % NS_XL)
    brk.set('id', str(indice))
    brk.set('max', '16383')
    brk.set('man', '1')

    todos = salto.findall('{%s}brk' % NS_XL)
    salto.set('count', str(len(todos)))
    salto.set('manualBreakCount', str(sum(1 for b in todos if b.get('man') == '1')))
    logger.info('Saneo de hoja: salto de página antes de la fila %d', fila)
    return True


# ---------------------------------------------------------------------------
# Detección estructural de secciones por el logo de encabezado
# ---------------------------------------------------------------------------

def _leer_items(archivo):
    """Lee el XLSX completo en memoria: ``(orden, {nombre: bytes})``."""
    with zipfile.ZipFile(archivo, 'r') as z:
        orden = [item.filename for item in z.infolist()]
        items = {nombre: z.read(nombre) for nombre in orden}
    return orden, items


def _escribir_xlsx(destino, orden, items):
    with zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as z:
        for nombre in orden:
            z.writestr(nombre, items[nombre])


def _anclas_imagen(drawing_data):
    """Anclas de dibujo con su fila/columna y el ``r:embed`` de su imagen.

    Acepta tanto anclas con prefijo (``<xdr:oneCellAnchor>``) como sin él
    (las que inyectan algunos generadores/tests).
    """
    try:
        raiz = etree.fromstring(drawing_data)
    except etree.XMLSyntaxError:
        return []
    anclas = []
    for etiqueta in ('twoCellAnchor', 'oneCellAnchor', 'absoluteAnchor'):
        for ancla in raiz.findall('{*}' + etiqueta):
            desde = ancla.find('{*}from')
            if desde is None:
                continue
            fila = desde.find('{*}row')
            col = desde.find('{*}col')
            blip = ancla.find('.//{*}blip')
            embed = blip.get('{%s}embed' % NS_R) if blip is not None else None
            anclas.append({
                'tipo': etiqueta,
                'fila': int(fila.text) if fila is not None and fila.text else 0,
                'col': int(col.text) if col is not None and col.text else 0,
                'embed': embed,
            })
    return anclas


def _resolver_dibujo(items, hoja_path, rid):
    """Ruta del dibujo referenciado por ``r:id`` desde los rels de la hoja."""
    base = posixpath.dirname(hoja_path)
    rels_path = posixpath.join(base, '_rels', posixpath.basename(hoja_path) + '.rels')
    datos = items.get(rels_path)
    if not datos:
        return None
    try:
        rels = etree.fromstring(datos)
    except etree.XMLSyntaxError:
        return None
    destino = None
    for rel in rels:
        if rel.get('Id') == rid:
            destino = rel.get('Target')
            break
    if not destino:
        return None
    if destino.startswith('/'):
        candidato = posixpath.normpath(destino.lstrip('/'))
    else:
        candidato = posixpath.normpath(posixpath.join(base, destino))
    if candidato in items:
        return candidato
    candidato = posixpath.normpath(posixpath.join('xl', destino.lstrip('/')))
    return candidato if candidato in items else None


def _secciones_de_hoja(items, hoja_path):
    """Info de secciones de una hoja, o ``None`` si no hay encabezado repetido.

    La cabecera es el logo (imagen) anclado en la **fila 0**; toda otra ancla
    que reutilice esa misma imagen marca el inicio de sección. Devuelve
    ``{'filas': [filas 0-based], 'drawing': ruta, 'embed': rId}``.
    """
    datos = items.get(hoja_path)
    if not datos:
        return None
    try:
        raiz = etree.fromstring(datos)
    except etree.XMLSyntaxError:
        return None
    drawing = raiz.find('{*}drawing')
    if drawing is None:
        return None
    rid = drawing.get('{%s}id' % NS_R)
    if not rid:
        return None
    drawing_path = _resolver_dibujo(items, hoja_path, rid)
    if not drawing_path:
        return None
    con_imagen = [a for a in _anclas_imagen(items[drawing_path]) if a['embed']]
    if not con_imagen:
        return None
    cabecera = min(con_imagen, key=lambda a: (a['fila'], a['col']))
    if cabecera['fila'] != 0:
        return None
    embed = cabecera['embed']
    filas = sorted({a['fila'] for a in con_imagen
                    if a['embed'] == embed and a['fila'] > 0})
    return {'filas': filas, 'drawing': drawing_path, 'embed': embed}


def _detectar_secciones(orden, items):
    """``{hoja: info}`` para cada hoja con logo de cabecera repetido."""
    salida = {}
    for nombre in orden:
        if not _RE_HOJA_XLSX.match(nombre):
            continue
        info = _secciones_de_hoja(items, nombre)
        if info is not None:
            salida[nombre] = info
    return salida


def _mover_logos(items, secciones):
    """Baja una fila los logos de sección (no el de la cabecera fila 0)."""
    cambiado = False
    for info in secciones.values():
        clave = info['drawing']
        datos = items.get(clave)
        if not datos:
            continue
        try:
            raiz = etree.fromstring(datos)
        except etree.XMLSyntaxError:
            continue
        for etiqueta in ('twoCellAnchor', 'oneCellAnchor'):
            for ancla in raiz.findall('{*}' + etiqueta):
                blip = ancla.find('.//{*}blip')
                if blip is None or blip.get('{%s}embed' % NS_R) != info['embed']:
                    continue
                desde = ancla.find('{*}from')
                fila = desde.find('{*}row') if desde is not None else None
                if fila is None or not fila.text or int(fila.text) == 0:
                    continue
                for nodo in ancla.findall('{*}from/{*}row') + ancla.findall('{*}to/{*}row'):
                    if nodo.text and nodo.text.lstrip('-').isdigit():
                        nodo.text = str(int(nodo.text) + 1)
                        cambiado = True
        items[clave] = etree.tostring(
            raiz, xml_declaration=True, encoding='UTF-8', standalone=True,
        )
    return cambiado


def _parchear_margenes_hoja_xlsx(raiz, margenes):
    """Fija ``top``/``bottom`` en ``<pageMargins>``. Devuelve si hubo cambios."""
    if not margenes:
        return False

    nuevo = raiz.find('{%s}pageMargins' % NS_XL)
    if nuevo is None:
        nuevo = etree.Element('{%s}pageMargins' % NS_XL)
        for atributo, valor in MARGENES_XLSX_DEFECTO.items():
            nuevo.set(atributo, '%g' % valor)
        insertado = _insertar_hoja(raiz, nuevo, ('printOptions', 'pageMargins'),
                                   ('pageSetup',) + _ANTES_DE)
        if not insertado:
            datos_hoja = raiz.find('{%s}sheetData' % NS_XL)
            if datos_hoja is None:
                return False
            datos_hoja.addnext(nuevo)

    for atributo, valor in margenes.items():
        nuevo.set(atributo, '%g' % valor)
    return True


def _parchear_hoja_xlsx(data, codigo, margenes=None, salto_titulo=True,
                        filas_seccion=None, ajuste_salto=0):
    """Fija ``paperSize``/márgenes y salta antes de cada sección.

    ``filas_seccion`` son las filas 0-based de inicio de sección detectadas por
    el logo de encabezado; si es ``None`` se cae al título repetido (compara
    el contenido de la fila 1). Devuelve ``(xml, ok)``.
    """
    try:
        raiz = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data, False

    parcheado = False
    if codigo:
        for setup in raiz.findall('{%s}pageSetup' % NS_XL):
            setup.set('paperSize', str(codigo))
            parcheado = True

        if not parcheado:
            nuevo = etree.Element('{%s}pageSetup' % NS_XL)
            nuevo.set('paperSize', str(codigo))
            datos_hoja = raiz.find('{%s}sheetData' % NS_XL)
            if _insertar_hoja(raiz, nuevo, _DESPUES_DE, _ANTES_DE):
                parcheado = True
            elif datos_hoja is not None:
                datos_hoja.addnext(nuevo)
                parcheado = True

    parcheado = _parchear_margenes_hoja_xlsx(raiz, margenes) or parcheado

    if salto_titulo:
        if filas_seccion is None:
            titulo = _fila_titulo_repetido(raiz)
            filas = [titulo - 1] if titulo else []
        else:
            filas = filas_seccion
        for from_row in filas:
            parcheado = _insertar_salto_de_pagina(
                raiz, from_row + 1 + ajuste_salto,
            ) or parcheado

    if not parcheado:
        return data, False
    return etree.tostring(raiz, xml_declaration=True, encoding='UTF-8', standalone=True), True


def _parchear_items_xlsx(orden, items, codigo, margenes, secciones,
                         mover_logo=False, ajuste_salto=0):
    """Aplica papel/márgenes/saltos sobre ``items``; devuelve si hubo cambios."""
    parcheado = False
    if mover_logo and secciones:
        parcheado = _mover_logos(items, secciones) or parcheado
    for nombre in orden:
        if not _RE_HOJA_XLSX.match(nombre):
            continue
        info = secciones.get(nombre)
        filas = info['filas'] if info is not None else None
        datos, ok = _parchear_hoja_xlsx(
            items[nombre], codigo, margenes,
            filas_seccion=filas, ajuste_salto=ajuste_salto,
        )
        items[nombre] = datos
        parcheado = parcheado or ok
    return parcheado


def _parchear_xlsx(archivo, destino, codigo, margenes=None):
    """Copia el XLSX con ``paperSize``, márgenes y salto de sección aplicados."""
    try:
        orden, items = _leer_items(archivo)
        secciones = _detectar_secciones(orden, items)
        parcheado = _parchear_items_xlsx(orden, items, codigo, margenes, secciones)
        _escribir_xlsx(destino, orden, items)
        return parcheado
    except Exception as exc:  # noqa: BLE001 - un XLSX corrupto no debe tumbar la conversión
        logger.error("No se pudo parchear %s: %s", archivo, exc)
        return False


def _parchear_seccion_docx(raiz, ancho_twips, alto_twips, margenes=None):
    """Ajusta ``pgSz`` y márgenes de un ``sectPr``; devuelve True si cambió."""
    parcheado = _parchear_pg_sz(raiz, ancho_twips, alto_twips)
    parcheado = _parchear_margenes_seccion_docx(raiz, margenes) or parcheado
    return parcheado


def _parchear_pg_sz(raiz, ancho_twips, alto_twips):
    """Fija el ``w:pgSz`` de un ``sectPr``; devuelve True si hubo cambios."""
    pg_sz = raiz.find('{%s}pgSz' % NS_W)
    if pg_sz is not None:
        w_actual = pg_sz.get('{%s}w' % NS_W)
        h_actual = pg_sz.get('{%s}h' % NS_W)
        apaisado = (
            pg_sz.get('{%s}orient' % NS_W) == 'landscape'
            or (w_actual and h_actual and w_actual.isdigit() and h_actual.isdigit()
                and int(w_actual) > int(h_actual))
        )
        if apaisado:
            pg_sz.set('{%s}w' % NS_W, str(alto_twips))
            pg_sz.set('{%s}h' % NS_W, str(ancho_twips))
            pg_sz.set('{%s}orient' % NS_W, 'landscape')
        else:
            pg_sz.set('{%s}w' % NS_W, str(ancho_twips))
            pg_sz.set('{%s}h' % NS_W, str(alto_twips))
        return True

    nuevo = etree.Element('{%s}pgSz' % NS_W)
    nuevo.set('{%s}w' % NS_W, str(ancho_twips))
    nuevo.set('{%s}h' % NS_W, str(alto_twips))
    pg_mar = raiz.find('{%s}pgMar' % NS_W)
    if pg_mar is not None:
        pg_mar.addprevious(nuevo)
    else:
        raiz.append(nuevo)
    return True


def _parchear_margenes_seccion_docx(raiz, margenes):
    """Fija ``top``/``bottom`` en ``w:pgMar`` (twips). Devuelve si hubo cambios."""
    if not margenes:
        return False

    pg_mar = raiz.find('{%s}pgMar' % NS_W)
    if pg_mar is None:
        pg_mar = etree.Element('{%s}pgMar' % NS_W)
        for atributo, valor in MARGENES_DOCX_DEFECTO.items():
            pg_mar.set('{%s}%s' % (NS_W, atributo), str(valor))
        pg_sz = raiz.find('{%s}pgSz' % NS_W)
        if pg_sz is not None:
            pg_sz.addnext(pg_mar)
        else:
            raiz.append(pg_mar)

    for atributo, valor in margenes.items():
        pg_mar.set('{%s}%s' % (NS_W, atributo), str(int(round(float(valor) * TWIPS))))
    return True


def _texto_parrafo(parrafo):
    return ''.join(t.text or '' for t in parrafo.iter('{%s}t' % NS_W)).strip()


def _marcar_salto_docx(raiz):
    """Marca con ``w:pageBreakBefore`` el párrafo que repite el primero.

    Es el equivalente DOCX del salto antes del título repetido de los XLSX:
    la 2ª sección (instrucciones) vuelve a escribir el mismo título y debe
    abrir página. Devuelve si hubo cambios.
    """
    cuerpo = raiz.find('{%s}body' % NS_W)
    if cuerpo is None:
        return False
    parrafos = cuerpo.findall('{%s}p' % NS_W)
    if len(parrafos) < 2:
        return False
    primero = _texto_parrafo(parrafos[0])
    if not primero:
        return False
    for parrafo in parrafos[1:]:
        if _texto_parrafo(parrafo) != primero:
            continue
        ppr = parrafo.find('{%s}pPr' % NS_W)
        if ppr is None:
            ppr = etree.Element('{%s}pPr' % NS_W)
            parrafo.insert(0, ppr)
        if ppr.find('{%s}pageBreakBefore' % NS_W) is not None:
            return False
        ppr.insert(0, etree.Element('{%s}pageBreakBefore' % NS_W))
        return True
    return False


def _parchear_docx_xml(data, ancho_twips, alto_twips, margenes=None,
                       salto_titulo=True):
    """Fija tamaño y márgenes de página en todas las secciones del documento."""
    try:
        raiz = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data, False

    secciones = raiz.findall('.//{%s}sectPr' % NS_W)
    parcheado = False
    for seccion in secciones:
        parcheado = _parchear_seccion_docx(
            seccion, ancho_twips, alto_twips, margenes,
        ) or parcheado

    if salto_titulo:
        parcheado = _marcar_salto_docx(raiz) or parcheado

    if not parcheado:
        return data, False
    return etree.tostring(raiz, xml_declaration=True, encoding='UTF-8', standalone=True), True


def _parchear_docx(archivo, destino, ancho_twips, alto_twips, margenes=None,
                   salto_titulo=True):
    """Copia el DOCX con ``w:pgSz`` y márgenes ajustados en sus secciones."""
    try:
        with zipfile.ZipFile(archivo, 'r') as origen, \
                zipfile.ZipFile(destino, 'w', zipfile.ZIP_DEFLATED) as salida:
            parcheado = False
            for item in origen.infolist():
                datos = origen.read(item.filename)
                if item.filename == 'word/document.xml':
                    datos, ok = _parchear_docx_xml(
                        datos, ancho_twips, alto_twips, margenes, salto_titulo,
                    )
                    parcheado = parcheado or ok
                salida.writestr(item, datos)
        return parcheado
    except Exception as exc:  # noqa: BLE001
        logger.error("No se pudo parchear %s: %s", archivo, exc)
        return False


def parchear_tamano_papel(archivo, papel=None, margenes=None):
    """Devuelve la ruta a convertir: una copia temporal parcheada si aplica,
    o la propia ruta original si no hay nada que tocar.

    En XLSX se parchea siempre (el salto de título no depende del papel) y,
    si hay papel configurado, también el tamaño y los márgenes
    superior/inferior; el original nunca se modifica. En DOCX solo se parchea
    cuando hay papel (no hay saneo que aplicar).
    """
    codigo, ancho, alto = resolver_papel(papel)
    margenes = resolver_margenes(margenes) if codigo else None
    ext = os.path.splitext(archivo)[1].lower().lstrip('.')

    parcheable = ext == 'xlsx' or (ext == 'docx' and codigo and ancho and alto)
    if not parcheable:
        return archivo, None

    temp_dir = tempfile.mkdtemp(prefix='polidoc_papel_')
    copia = os.path.join(temp_dir, os.path.basename(archivo))
    if ext == 'xlsx':
        ok = _parchear_xlsx(archivo, copia, codigo, margenes)
    else:
        ok = _parchear_docx(
            archivo, copia, ancho, alto, margenes,
            salto_titulo=getattr(settings, 'PDF_SALTO_DOCX', True),
        )

    if not ok:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info("Formato %s sin cambios que parchear; se convierte el original", ext)
        return archivo, None
    return copia, temp_dir


# ---------------------------------------------------------------------------
# Conversión
# ---------------------------------------------------------------------------

def convertir_office_a_pdf(archivo, papel=None, outdir=None, timeout=180,
                           margenes=None):
    """Convierte un archivo office a PDF aplicando papel y márgenes.

    El original nunca se modifica (se trabaja sobre una copia temporal cuando
    hay que parchear). Devuelve la ruta absoluta del PDF generado o ``None``.
    """
    from ..tasks import FORMATOS_CONVERTIBLES

    ext = os.path.splitext(archivo)[1].lower().lstrip('.')
    if ext not in FORMATOS_CONVERTIBLES:
        logger.warning("Formato no convertible: %s", archivo)
        return None

    if outdir is None:
        outdir = os.path.dirname(archivo) or '.'
    os.makedirs(outdir, exist_ok=True)

    entrada, temp_dir = parchear_tamano_papel(archivo, papel, margenes)
    try:
        cmd = [
            'libreoffice', '--headless', '--convert-to', 'pdf',
            '--outdir', outdir, entrada,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

        nombre = os.path.splitext(os.path.basename(entrada))[0] + '.pdf'
        pdf = os.path.join(outdir, nombre)
        if result.returncode == 0 and os.path.exists(pdf):
            logger.info("PDF generado con papel configurable: %s", pdf)
            return pdf

        error = (result.stderr or result.stdout or '').strip()
        logger.error("LibreOffice no convirtió %s: %s", archivo, error)
        return None
    except subprocess.TimeoutExpired:
        logger.error("LibreOffice agotó el tiempo convirtiendo %s", archivo)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("Error convirtiendo %s: %s", archivo, exc)
        return None
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def convertir_lote(rutas, outdir, timeout=180):
    """Convierte varios office a PDF en una sola llamada a LibreOffice.

    Devuelve ``{ruta_origen: ruta_pdf}`` solo con los que se generaron. Así la
    reparación adaptativa paga un solo arranque de LibreOffice para todas las
    disposiciones alternativas.
    """
    rutas = list(rutas)
    if not rutas:
        return {}
    os.makedirs(outdir, exist_ok=True)
    cmd = [
        'libreoffice', '--headless', '--convert-to', 'pdf',
        '--outdir', outdir,
    ] + rutas
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.error("LibreOffice no convirtió el lote: %s", exc)
        return {}
    salida = {}
    for ruta in rutas:
        pdf = os.path.join(outdir, os.path.splitext(os.path.basename(ruta))[0] + '.pdf')
        if os.path.exists(pdf):
            salida[ruta] = pdf
    return salida


def generar_alternativas(archivo, papel=None, margenes=None, max_alternativas=3):
    """Copia temporal del XLSX con disposiciones alternativas de reparación.

    Devuelve ``(alternativas, temp_dir)``; cada alternativa es
    ``{'nombre', 'ruta'}``. No incluye la base (su PDF ya existe). Pensado para
    que el QA elija la copia sin logo recortado.
    """
    ext = os.path.splitext(archivo)[1].lower().lstrip('.')
    if ext != 'xlsx':
        return [], None
    codigo, _, _ = resolver_papel(papel)
    margenes = resolver_margenes(margenes) if codigo else None
    try:
        orden, items = _leer_items(archivo)
    except Exception as exc:  # noqa: BLE001
        logger.error("No se pudo leer %s para reparar: %s", archivo, exc)
        return [], None
    secciones = _detectar_secciones(orden, items)
    if not secciones:
        return [], None

    combinaciones = [
        ('logo1', True, 0),
        ('salto1', False, -1),
        ('logo1salto1', True, -1),
    ][:max(0, int(max_alternativas))]
    temp_dir = tempfile.mkdtemp(prefix='polidoc_reparar_')
    stem = os.path.splitext(os.path.basename(archivo))[0]
    alternativas = []
    for nombre, mover_logo, ajuste in combinaciones:
        copia_items = dict(items)
        _parchear_items_xlsx(
            orden, copia_items, codigo, margenes, secciones,
            mover_logo=mover_logo, ajuste_salto=ajuste,
        )
        ruta = os.path.join(temp_dir, '%s__%s.xlsx' % (stem, nombre))
        _escribir_xlsx(ruta, orden, copia_items)
        alternativas.append({'nombre': nombre, 'ruta': ruta})
    return alternativas, temp_dir
