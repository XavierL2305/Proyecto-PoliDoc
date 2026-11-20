"""Utilidades XLSX compartidas por los servicios SGC (FOR-003/004/006/007/009).

Centraliza lo que antes estaba duplicado:

* ``copiar_hoja``: copia una hoja conservando merges e imágenes (openpyxl no
  copia las imágenes con ``copy_worksheet``).
* ``preparar_para_pdf``: copia el libro para la exportación PDF (una sola
  página de ancho y sin el instructivo ``SGC-IDL-*``).
* ``xlsx_bytes_a_pdf``: convierte bytes XLSX a PDF y resuelve la ruta devuelta
  por ``convertir_a_pdf_sync`` (que puede ser relativa a ``MEDIA_ROOT``).
"""
import copy
import io
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

HOJAS_INSTRUCTIVO = ('SGC-IDL',)


def copiar_hoja(wb, base):
    """Copia la hoja ``base`` conservando merges e imágenes."""
    nueva = wb.copy_worksheet(base)
    existentes = {str(rango) for rango in nueva.merged_cells.ranges}
    for rango in base.merged_cells.ranges:
        if str(rango) not in existentes:
            nueva.merge_cells(str(rango))
    for imagen in base._images:
        nueva._images.append(copy.deepcopy(imagen))
    return nueva


def _una_pagina_de_ancho(hoja):
    """Ajusta la hoja para que se imprima en una sola página de ancho."""
    from openpyxl.worksheet.properties import PageSetupProperties

    propiedades = hoja.sheet_properties.pageSetUpPr
    if propiedades is None:
        propiedades = PageSetupProperties()
        hoja.sheet_properties.pageSetUpPr = propiedades
    propiedades.fitToPage = True
    hoja.page_setup.fitToWidth = 1
    hoja.page_setup.fitToHeight = 0
    # Sin escala fija: con fitToPage activo, el visor usaría una de las dos.
    hoja.page_setup.scale = None


def preparar_para_pdf(xlsx_bytes):
    """Copia los bytes XLSX listos para la exportación a PDF.

    Aplica dos ajustes que **solo debe ver el PDF** (el XLSX descargable y
    almacenado queda intacto porque esta función trabaja sobre una copia):

    * una página de ancho (``fitToWidth=1`` sin escala fija), para que la
      última columna no acabe en una hoja aparte;
    * oculta las hojas del instructivo ``SGC-IDL-*`` (LibreOffice no exporta
      hojas ocultas).

    Solo debe usarse sobre libros generados con ``openpyxl`` (FOR-003/004/006/
    007/009); **no** sobre los parcheados por XML en ``exportacion.py``
    (FOR-005/010/017), porque ``openpyxl.load_workbook`` descarta formas
    (``sp``) y gráficos ``chartEx``.

    Nunca lanza: si algo falla devuelve los bytes originales.
    """
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
        hojas = wb.worksheets
        primera = hojas[0] if hojas else None
        for hoja in hojas:
            es_instructivo = (
                hoja is not primera
                and hoja.title.strip().upper().startswith(HOJAS_INSTRUCTIVO)
            )
            if es_instructivo:
                hoja.sheet_state = 'hidden'
                continue
            _una_pagina_de_ancho(hoja)
        salida = io.BytesIO()
        wb.save(salida)
        return salida.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning('No se pudo preparar el XLSX para PDF: %s', exc)
        return xlsx_bytes


def xlsx_bytes_a_pdf(xlsx_bytes):
    """Convierte bytes XLSX a PDF (o ``None`` si no se pudo). Nunca lanza."""
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp.write(xlsx_bytes)
        ruta_xlsx = tmp.name
    try:
        from django.conf import settings

        from ..tasks import convertir_a_pdf_sync

        pdf_path = convertir_a_pdf_sync(ruta_xlsx, reparar_sincrono=True)
        if not pdf_path:
            return None
        if not os.path.isabs(pdf_path):
            pdf_path = os.path.join(str(settings.MEDIA_ROOT), pdf_path)
        try:
            with open(pdf_path, 'rb') as fh:
                return fh.read()
        finally:
            try:
                os.unlink(pdf_path)
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning('No se pudo convertir el XLSX a PDF: %s', exc)
        return None
    finally:
        try:
            os.unlink(ruta_xlsx)
        except OSError:
            pass
