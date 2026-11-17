"""Utilidades XLSX compartidas por los servicios SGC (FOR-003/004/006/007/009).

Centraliza lo que antes estaba duplicado:

* ``copiar_hoja``: copia una hoja conservando merges e imágenes (openpyxl no
  copia las imágenes con ``copy_worksheet``).
* ``xlsx_bytes_a_pdf``: convierte bytes XLSX a PDF y resuelve la ruta devuelta
  por ``convertir_a_pdf_sync`` (que puede ser relativa a ``MEDIA_ROOT``).
"""
import copy
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


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
