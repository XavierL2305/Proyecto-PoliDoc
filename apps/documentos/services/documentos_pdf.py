"""Estado del PDF de un documento: existencia en disco y regeneración.

`Documento.archivo_pdf` guarda una **ruta en BD**, no garantiza que el fichero
exista. Si se borra el PDF del disco el campo queda "fantasma": el visor lo
da por válido y `ver_archivo` revienta con `FileNotFoundError` (500).

Aquí se centraliza:
- `pdf_disponible()`      → ¿el PDF está declarado y realmente en disco?
- `limpiar_pdf_inexistente()` → borra un puntero a un fichero que ya no existe.
- `convertir_documento_sync()` → convierte el documento a PDF en el proceso
  actual (aplicando `settings.PAPEL_PDF_CONVERSION`, ver conversion_pdf.py).
- `asegurar_pdf()`        → si el PDF falta, lo regenera en línea.

Regeneración **síncrona**: la misma petición espera la conversión (~2-3 s con
LibreOffice) y devuelve el PDF ya listo, sin depender de broker/Celery.
"""

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

# Formatos que convertimos a PDF (mismo criterio que documentos.tasks)
FORMATOS_CONVERTIBLES = ('doc', 'docx', 'xls', 'xlsx', 'vsdx')


def archivo_existe(campo):
    """True si un FileField está seteado y su fichero existe en disco."""
    if not campo:
        return False
    try:
        return bool(campo.name) and campo.storage.exists(campo.name)
    except Exception as exc:  # noqa: BLE001 - storage roto no debe tumbar la vista
        logger.error("No se pudo comprobar la existencia de %s: %s",
                     getattr(campo, 'name', '?'), exc)
        return False


def pdf_disponible(documento):
    """True si `documento.archivo_pdf` está seteado y el fichero existe."""
    return archivo_existe(documento.archivo_pdf)


def fuente_disponible(documento):
    """True si `documento.archivo` está seteado y el fichero existe."""
    return archivo_existe(documento.archivo)


def limpiar_pdf_inexistente(documento):
    """Vacía `archivo_pdf` si apunta a un fichero que ya no existe.

    Devuelve True si hubo algo que limpiar. No toca nada si no hay campo.
    """
    if not documento.archivo_pdf:
        return False
    if pdf_disponible(documento):
        return False
    logger.warning(
        "PDF huérfano en %s (%s): se limpia la ruta %s",
        documento.codigo, documento.uuid, documento.archivo_pdf.name,
    )
    documento.archivo_pdf = ''
    documento.save(update_fields=['archivo_pdf'])
    return True


def convertir_documento_sync(documento):
    """Convierte el archivo office del documento a PDF en este proceso.

    Aplica `settings.PAPEL_PDF_CONVERSION` (ver services/conversion_pdf.py),
    actualiza `documento.archivo_pdf` y devuelve True/False. Nunca lanza.
    """
    if not documento.archivo or documento.tipo_archivo not in FORMATOS_CONVERTIBLES:
        return False
    if not fuente_disponible(documento):
        logger.error("No puedo generar PDF de %s: falta el archivo original", documento.codigo)
        return False

    # Un puntero a un fichero que ya no existe no debe sobrevivir a la
    # conversión: si ésta falla, el campo queda vacío (nunca fantasma).
    limpiar_pdf_inexistente(documento)

    try:
        from .conversion_pdf import convertir_office_a_pdf
        ruta_archivo = documento.archivo.path
        pdf = convertir_office_a_pdf(
            ruta_archivo,
            papel=getattr(settings, 'PAPEL_PDF_CONVERSION', None),
            outdir=os.path.dirname(ruta_archivo),
        )
        if not pdf or not os.path.exists(pdf):
            return False

        media_root = str(settings.MEDIA_ROOT)
        relativa = pdf
        if pdf.startswith(media_root):
            relativa = pdf[len(media_root):].lstrip(os.sep)

        documento.archivo_pdf = relativa
        documento.save(update_fields=['archivo_pdf'])
        logger.info("PDF regenerado en línea para %s (%s)",
                    documento.codigo, relativa)
        try:
            from .pdf_adaptativo import verificar
            verificar(ruta_archivo, pdf)
        except Exception as exc:  # noqa: BLE001
            logger.warning("QA del PDF falló para %s: %s", documento.codigo, exc)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("Conversión en línea de %s falló: %s", documento.codigo, exc)
        return False


def encolar_conversion(documento):
    """Encola la conversión a PDF del documento.

    Devuelve True solo si el fallback síncrono (broker/Celery no disponible)
    generó ya el PDF; en el caso normal (encolado correcto) devuelve False y la
    plantilla hace polling de `estado_conversion` mientras se genera.
    """
    from ..tasks import convertir_documento_a_pdf

    try:
        convertir_documento_a_pdf.delay(str(documento.uuid))
    except Exception as exc:  # noqa: BLE001 - broker caído: degradar a síncrono
        logger.warning(
            "No se pudo encolar la conversión de %s (%s); se intenta en línea: %s",
            documento.codigo, documento.uuid, exc,
        )
        return convertir_documento_sync(documento)

    # Con `CELERY_TASK_ALWAYS_EAGER` (tests) la tarea ya terminó; refrescar el
    # estado evita mostrar la barra de progreso cuando el PDF ya está listo.
    documento.refresh_from_db(fields=['archivo_pdf'])
    listo = pdf_disponible(documento)
    if not listo and getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        # La tarea eager no generó el PDF: reproducir el modo síncrono previo.
        return convertir_documento_sync(documento)
    return listo


def asegurar_pdf(documento):
    """Garantiza que `documento.archivo_pdf` apunte a un PDF real.

    - Si el PDF existe → devuelve True.
    - Si apunta a un fichero ausente → limpia el puntero y lo regenera.
    - Si no hay PDF y el formato es convertible con origen → lo genera.
    - Si no es convertible / falta el origen → devuelve False.

    Nunca lanza.
    """
    if pdf_disponible(documento):
        return True

    limpiar_pdf_inexistente(documento)

    if not documento.archivo or documento.tipo_archivo not in FORMATOS_CONVERTIBLES:
        return False

    convertir_documento_sync(documento)
    return pdf_disponible(documento)
