from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe
import json
import re

register = template.Library()


# Códigos tipo TI-FOR-001, TI-PRG-002, SGC-FOR-001, DPR-001
CODIGO_DOCUMENTO = r'[A-Z]{2,6}(?:-[A-Z]{2,6})?-\d{2,4}'


@register.simple_tag(name='resaltar')
def resaltar(texto, palabras_clave=None):
    """Resalta en negrita códigos y palabras clave de forma segura.

    Escapa el HTML del texto antes de insertar las etiquetas <strong>, por lo
    que es seguro aunque el contenido provenga de la IA.
    """
    if not texto:
        return ''

    texto = escape(str(texto))

    terminos = [CODIGO_DOCUMENTO]
    if palabras_clave:
        claves = sorted(
            {str(k).strip() for k in palabras_clave if str(k).strip()},
            key=len, reverse=True,
        )
        for clave in claves:
            terminos.append(re.escape(escape(clave)))

    patron = re.compile(
        r'(?<![\w-])(' + '|'.join(terminos) + r')(?![\w-])',
        re.IGNORECASE,
    )
    return mark_safe(patron.sub(r'<strong>\1</strong>', texto))


@register.filter(name='get_item')
def get_item(dictionary, key):
    """Retrieve a value from a dictionary by key."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, '')
    return ''


@register.filter(name='to_json')
def to_json(value):
    """Serialize a value to JSON string for safe use in <script> tags."""
    try:
        return json.dumps(value, ensure_ascii=False).replace('</', '<\\/')
    except (TypeError, ValueError):
        return '[]'


@register.filter(name='opt_valor')
def opt_valor(option):
    """Extract valor from an option dict, or return the string itself."""
    if isinstance(option, dict):
        return option.get('valor', option.get('label', ''))
    return option


@register.filter(name='opt_etiqueta')
def opt_etiqueta(option):
    """Extract etiqueta from an option dict, or return the string itself."""
    if isinstance(option, dict):
        return option.get('etiqueta', option.get('label', option.get('valor', '')))
    return option


@register.filter(name='opt_cell')
def opt_cell(option):
    """Extract celda from an option dict."""
    if isinstance(option, dict):
        return option.get('celda', '')
    return ''


@register.filter(name='opt_shape')
def opt_shape(option):
    """Extract shape from an option dict."""
    if isinstance(option, dict):
        return option.get('shape', '')
    return ''
