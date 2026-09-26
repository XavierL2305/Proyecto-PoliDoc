"""Validadores de seguridad para archivos subidos.

Defensa en profundidad contra subidas maliciosas en un sistema de documentos
controlados (ISO 9001 §7.5): se rechazan extensiones peligrosas y se comprueba
que el CONTENIDO real del archivo (magic bytes) coincida con la extensión
declarada. Así un .html/.svg/.exe renombrado a .pdf no cuela, lo que evita
XSS almacenado al servirlo y ejecución de binarios.

El sniffing es 100% en Python (no depende de libmagic, que no está instalado en
la imagen Docker).
"""
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Extensiones permitidas (CONTEXT.txt §8: formatos ISO soportados).
EXTENSIONES_PERMITIDAS = frozenset({'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.vsdx'})

# Familias por firma mágica.
_EXT_OLE2 = frozenset({'.doc', '.xls'})            # Office legacy (compuesto OLE2)
_EXT_ZIP = frozenset({'.docx', '.xlsx', '.vsdx'})   # OOXML / Visio (contenedor ZIP)

_SIG_PDF = b'%PDF-'
_SIG_OLE2 = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
_SIG_ZIP = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')

# Bytes iniciales a inspeccionar (suficiente para todas las firmas usadas).
_CABECERA_BYTES = 16


def _max_upload_bytes():
    mb = getattr(settings, 'DOCUMENTOS_MAX_UPLOAD_MB', 10)
    return int(mb) * 1024 * 1024


def _leer_cabecera(value):
    """Lee los primeros bytes sin consumir el archivo (restaura la posición)."""
    try:
        pos = value.tell()
    except (AttributeError, OSError, ValueError):
        pos = None
    try:
        value.seek(0)
        return value.read(_CABECERA_BYTES) or b''
    except (AttributeError, OSError, ValueError):
        return b''
    finally:
        if pos is not None:
            try:
                value.seek(pos)
            except (OSError, ValueError):
                pass


def validar_archivo_documento(value):
    """Validador de Django para FileField de subida de documentos.

    Comprueba: extensión permitida, tamaño máximo y coherencia entre extensión
    y magic bytes del contenido.
    """
    nombre = getattr(value, 'name', '') or ''
    ext = os.path.splitext(nombre)[1].lower()

    if ext not in EXTENSIONES_PERMITIDAS:
        raise ValidationError(
            _('Extensión "%(ext)s" no permitida. Tipos aceptados: %(tipos)s.'),
            params={
                'ext': ext or '(sin extensión)',
                'tipos': ', '.join(sorted(EXTENSIONES_PERMITIDAS)),
            },
            code='extension_no_permitida',
        )

    size = getattr(value, 'size', None)
    if size is not None and size > _max_upload_bytes():
        raise ValidationError(
            _('El archivo supera el tamaño máximo de %(mb)s MB.'),
            params={'mb': getattr(settings, 'DOCUMENTOS_MAX_UPLOAD_MB', 10)},
            code='archivo_demasiado_grande',
        )

    cabecera = _leer_cabecera(value)
    if not cabecera:
        raise ValidationError(
            _('El archivo está vacío o no se pudo leer su contenido.'),
            code='archivo_ilegible',
        )

    if ext == '.pdf':
        # Algunos PDF válidos tienen bytes de basura antes de %PDF-; se tolera
        # buscar la firma dentro de la cabecera en lugar de exigir startswith.
        if _SIG_PDF not in cabecera:
            raise ValidationError(
                _('El contenido no es un PDF válido (firma no encontrada).'),
                code='contenido_invalido',
            )
    elif ext in _EXT_OLE2:
        if not cabecera.startswith(_SIG_OLE2):
            raise ValidationError(
                _('El contenido no es un documento Office válido (%(ext)s).'),
                params={'ext': ext},
                code='contenido_invalido',
            )
    elif ext in _EXT_ZIP:
        if not any(cabecera.startswith(s) for s in _SIG_ZIP):
            raise ValidationError(
                _('El contenido no es un documento Office/Visio válido (%(ext)s).'),
                params={'ext': ext},
                code='contenido_invalido',
            )
