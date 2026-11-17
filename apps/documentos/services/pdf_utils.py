"""Utilidad compartida de conversión XLSX (bytes) → PDF (bytes).

La usan los servicios SGC (FOR-004 y FOR-006). ``convertir_a_pdf_sync`` puede
devolver una ruta **relativa a MEDIA_ROOT** cuando el PDF queda dentro de
Media; aquí se resuelve antes de abrirla (si MEDIA_ROOT vive bajo /tmp, p. ej.
en runners de test, la ruta relativa rompía la lectura).
"""
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


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
