"""
Sanitizador de HTML para contenido generado por administradores.

PoliDoc renderiza `Formato.instructivo_html` con el filtro `|safe`, por lo que
cualquier etiqueta peligrosa se ejecutaría en el navegador de quien consulte la
vista previa del formato. Este módulo aplica una lista blanca de etiquetas y
atributos usando únicamente la biblioteca estándar (html.parser), sin depender
de libmagic/nh3/bleach (que no están instalados en la imagen).

Se invoca desde `FormatoForm.clean_instructivo_html` (ruta de escritura), de
modo que el HTML almacenado ya está limpio antes de llegar al sink.
"""
from html.parser import HTMLParser
from urllib.parse import urlsplit

# Etiquetas permitidas (todo lo demás se descarta, conservando su texto).
ETIQUETAS_PERMITIDAS = frozenset({
    'p', 'br', 'hr', 'div', 'span',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li',
    'strong', 'b', 'em', 'i', 'u', 's', 'small', 'sub', 'sup',
    'blockquote', 'pre', 'code',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'caption',
    'a',
})

# Etiquetas que nunca deben procesarse ni conservar su contenido.
ETIQUETAS_PROHIBIDAS = frozenset({'script', 'style', 'iframe', 'object', 'embed', 'link', 'meta'})

# Etiquetas que no tienen cierre.
ETIQUETAS_VACIAS = frozenset({'br', 'hr'})

# Atributos permitidos por etiqueta ('*' aplica a todas).
ATRIBUTOS_PERMITIDOS = {
    '*': frozenset({'title', 'class'}),
    'a': frozenset({'href', 'target', 'rel'}),
    'td': frozenset({'colspan', 'rowspan'}),
    'th': frozenset({'colspan', 'rowspan'}),
}

# Esquemas de URL aceptados en href.
ESQUEMAS_SEGUROS = frozenset({'http', 'https', 'mailto'})


def _es_url_segura(valor):
    """Devuelve la URL si usa un esquema seguro; si no, cadena vacía."""
    if not valor:
        return ''
    candidato = valor.strip()
    # Descarta URLs relativas de esquema (//host) y esquemas peligrosos.
    if candidato.startswith('//'):
        return ''
    esquema = urlsplit(candidato).scheme.lower()
    if esquema == '':
        # Enlace relativo o ancla: se permite.
        return candidato
    if esquema in ESQUEMAS_SEGUROS:
        return candidato
    return ''


class _Sanitizador(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes = []
        self._omitir_profundidad = 0

    def handle_starttag(self, tag, attrs):
        if tag in ETIQUETAS_PROHIBIDAS:
            self._omitir_profundidad += 1
            return
        if self._omitir_profundidad:
            return
        if tag not in ETIQUETAS_PERMITIDAS:
            # Etiqueta desconocida: se ignora la marca pero se conserva el texto.
            return
        permitidos = ATRIBUTOS_PERMITIDOS.get('*', frozenset()) | ATRIBUTOS_PERMITIDOS.get(tag, frozenset())
        atributos_seguros = []
        for nombre, valor in attrs:
            nombre = (nombre or '').lower()
            if nombre not in permitidos:
                continue
            if nombre.startswith('on'):
                continue
            valor = valor or ''
            if tag == 'a' and nombre == 'href':
                valor = _es_url_segura(valor)
                if not valor:
                    continue
            if tag == 'a' and nombre == 'target':
                if valor.lower() not in ('_blank', '_self'):
                    continue
            atributos_seguros.append((nombre, valor))

        texto_attrs = ''.join(f' {n}="{_escapar_attr(v)}"' for n, v in atributos_seguros)
        if tag == 'a' and any(n == 'target' and v == '_blank' for n, v in atributos_seguros):
            if not any(n == 'rel' for n, v in atributos_seguros):
                texto_attrs += ' rel="noopener noreferrer"'
        autocierre = ' /' if tag in ETIQUETAS_VACIAS else ''
        self.partes.append(f'<{tag}{texto_attrs}{autocierre}>')

    def handle_startendtag(self, tag, attrs):
        # Etiqueta autocerrada: si es prohibida se descarta sin entrar en modo omisión
        # (no habrá handle_endtag que la cierre).
        if tag in ETIQUETAS_PROHIBIDAS:
            return
        if tag in ETIQUETAS_VACIAS:
            self.handle_starttag(tag, attrs)
            return
        # Para el resto, emitimos apertura+cierre explícitos.
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in ETIQUETAS_PROHIBIDAS:
            if self._omitir_profundidad:
                self._omitir_profundidad -= 1
            return
        if self._omitir_profundidad:
            return
        if tag in ETIQUETAS_VACIAS or tag not in ETIQUETAS_PERMITIDAS:
            return
        self.partes.append(f'</{tag}>')

    def handle_data(self, data):
        if self._omitir_profundidad:
            return
        self.partes.append(_escapar_texto(data))

    def resultado(self):
        return ''.join(self.partes)


def _escapar_texto(valor):
    return (
        valor.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def _escapar_attr(valor):
    return (
        valor.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&#x27;')
    )


def sanitizar_html(html):
    """Limpia una cadena HTML aplicando lista blanca de etiquetas/atributos."""
    if not html:
        return ''
    parser = _Sanitizador()
    parser.feed(html)
    parser.close()
    return parser.resultado()
