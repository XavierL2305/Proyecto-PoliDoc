"""QA de un PDF ya generado: detecta maquetación rota.

LibreOffice puede dejar una copia recortada del logo de una sección al final de
la página anterior (el "fantasma" que se ve en SGC-FOR-005) o arrastrar un
encabezado/título al borde inferior. En vez de decidir el salto "a ciegas"
antes de convertir, aquí se inspecciona el PDF resultante para que el conversor
pueda repararlo reconvirtiendo con otra disposición (ver pdf_adaptativo.py).

Los defectos que se buscan son:
- ``imagen_recortada``: una imagen cae por debajo del área imprimible o fuera
  de la página (el logo que "se quedó" en la hoja anterior).
- ``texto_tapado``: una imagen de la mitad inferior tapa texto (el fantasma
  encima de "Fecha:").
- ``titulo_huerfano``: el título de una sección queda como última línea de una
  página y no encabeza la siguiente (se pasa el listado de títulos esperados).

Si el PDF no se puede leer (p. ej. un mock en tests), ``analizar_pdf`` devuelve
un diagnóstico limpio: nunca lanza.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PUNTOS_POR_PULGADA = 72.0
FRANJA_TEXTO = 0.5  # solo se busca solape con texto en la mitad inferior


@dataclass
class Defecto:
    """Un problema de maquetación localizado en una página."""

    tipo: str
    pagina: int
    detalle: str = ''


@dataclass
class Diagnostico:
    """Resultado del QA: nº de páginas y defectos encontrados."""

    paginas: int = 0
    defectos: list = field(default_factory=list)

    @property
    def limpio(self):
        return not self.defectos

    @property
    def puntuacion(self):
        return len(self.defectos)

    def resumen(self):
        if self.limpio:
            return 'ok'
        return '; '.join('%s@p%d' % (d.tipo, d.pagina) for d in self.defectos)

    def to_dict(self):
        return {
            'paginas': self.paginas,
            'defectos': [
                {'tipo': d.tipo, 'pagina': d.pagina, 'detalle': d.detalle}
                for d in self.defectos
            ],
        }


def _normalizar(texto):
    return ' '.join((texto or '').split()).casefold()


def _texto_tapado(pagina, imagen):
    """Caracteres que caen dentro de la caja de ``imagen`` (vacío si ninguno)."""
    x0 = imagen.get('x0', 0)
    x1 = imagen.get('x1', 0)
    top = imagen.get('top', 0)
    bottom = imagen.get('bottom', 0)
    letras = [
        ch.get('text', '') for ch in pagina.chars
        if ch['x0'] < x1 and ch['x1'] > x0 and ch['top'] < bottom and ch['bottom'] > top
    ]
    return ''.join(letras)


def _lineas_pagina(pagina):
    try:
        texto = pagina.extract_text() or ''
    except Exception:  # noqa: BLE001 - un PDF raro no debe tumbar el QA
        return []
    return [linea.strip() for linea in texto.splitlines() if linea.strip()]


def _titulos_huerfanos(lineas_por_pagina, titulos):
    """Defectos donde el último renglón de una página es un título de sección."""
    defectos = []
    normalizados = {_normalizar(t) for t in titulos if t}
    for indice in range(len(lineas_por_pagina) - 1):
        pagina = lineas_por_pagina[indice]
        siguiente = lineas_por_pagina[indice + 1]
        if not pagina or not siguiente:
            continue
        ultima = _normalizar(pagina[-1])
        if ultima in normalizados and _normalizar(siguiente[0]) != ultima:
            defectos.append(Defecto('titulo_huerfano', indice + 1, pagina[-1][:60]))
    return defectos


def analizar_pdf(ruta, margen_inferior=0.4, titulos_seccion=None,
                 detectar_solape=True):
    """Inspecciona ``ruta`` y devuelve un :class:`Diagnostico`.

    ``margen_inferior`` va en pulgadas (el mismo que se aplica al convertir).
    ``titulos_seccion`` es opcional (lista de títulos) para detectar huérfanos.
    """
    diagnostico = Diagnostico()
    try:
        import pdfplumber

        with pdfplumber.open(ruta) as pdf:
            diagnostico.paginas = len(pdf.pages)
            inferior = float(margen_inferior or 0) * PUNTOS_POR_PULGADA
            lineas = []
            for numero, pagina in enumerate(pdf.pages, start=1):
                alto = pagina.height or 0
                for imagen in pagina.images:
                    top = imagen.get('top', 0)
                    bottom = imagen.get('bottom', 0)
                    if bottom > alto - inferior or top < 0:
                        diagnostico.defectos.append(Defecto(
                            'imagen_recortada', numero,
                            'top=%.1f bottom=%.1f alto=%.1f' % (top, bottom, alto),
                        ))
                        continue
                    if detectar_solape and alto and top > alto * FRANJA_TEXTO:
                        tapados = _texto_tapado(pagina, imagen)
                        if tapados.strip():
                            diagnostico.defectos.append(Defecto(
                                'texto_tapado', numero, tapados[:40],
                            ))
                lineas.append(_lineas_pagina(pagina))
            if titulos_seccion:
                diagnostico.defectos.extend(_titulos_huerfanos(lineas, titulos_seccion))
    except Exception as exc:  # noqa: BLE001 - PDF ilegible o sin pdfplumber
        logger.warning('No se pudo analizar el PDF %s: %s', ruta, exc)
    return diagnostico
