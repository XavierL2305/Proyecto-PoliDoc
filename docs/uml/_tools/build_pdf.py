"""
Compilador del PDF único de diagramas UML — POLIDOC.

Construye ``out/POLIDOC-UML-001_v<versión>.pdf`` a partir de:

- ``out/png/<nombre>.png``  : render Mermaid de alta resolución (lo genera `make pdf`)
- ``README.md``             : índice (orden y títulos) — única fuente de verdad
- logo del proyecto         : se busca automáticamente en ``polidoc/static/imagenes/``

Páginas: portada (metadatos + clasificación), índice con números de página y
una página A4 horizontal por diagrama. Pie con código del documento y
clasificación; marcadores laterales y metadatos PDF.

Uso:
    python _tools/build_pdf.py --src-dir . --out-dir out \
        --version 1.0 --code POLIDOC-UML-001 \
        --author "Pasante TSU. Xavier Lara" --classification CONFIDENCIAL

Dependencias: reportlab, svglib y Pillow (el `make check` las verifica).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from svglib.svglib import svg2rlg

# ------ constantes (paleta institucional, coherente con el resto de docs) ------
AZUL = "#005A9C"
AZUL_CLARO = "#1E88C7"
GRIS = "#444444"
GRIS_SUAVE = "#777777"
GRIS_LINEA = "#BBBBBB"
ROJO = "#B00020"
BLANCO = "#FFFFFF"
FONDO_FILA = "#F2F7FC"

PAGE_W, PAGE_H = landscape(A4)  # 841.89 x 595.28
MARGEN = 40.0
PIE_Y = 42.0
TOP_Y = PAGE_H - 66.0   # límite superior del área de imagen
ANCHO_TABLA = 520.0

ORGANIZACION = "Policlínica Táchira"
DESCRIPCION = "Sistema de Gestión Documental"
NORMAS = "ISO 9001 · ISO 27001 · ISO 15489"

# Agrupación del índice por prefijo numérico
GRUPOS = {
    "00": "Vista general",
    "01": "Clases", "02": "Clases", "03": "Clases",
    "04": "Clases", "05": "Clases", "06": "Clases",
    "07": "Casos de uso",
    "08": "Secuencias", "09": "Secuencias", "10": "Secuencias", "11": "Secuencias",
    "12": "Estados", "13": "Estados", "14": "Estados", "15": "Estados",
    "16": "Arquitectura", "17": "Arquitectura",
}


# ==============================================================================
# Utilidades
# ==============================================================================
def registrar_fuentes() -> tuple[str, str, str]:
    """Registra DejaVu Sans (acentos y símbolos); cae a Helvetica si faltan los TTF."""
    base = "/usr/share/fonts/truetype/dejavu/"
    try:
        pdfmetrics.registerFont(TTFont("PoliSans", base + "DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("PoliSans-Bold", base + "DejaVuSans-Bold.ttf"))
        pdfmetrics.registerFont(TTFont("PoliSans-Italic", base + "DejaVuSans-Oblique.ttf"))
        return "PoliSans", "PoliSans-Bold", "PoliSans-Italic"
    except Exception:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def leer_indice(src_dir: Path) -> list[dict[str, str]]:
    """Lee la tabla del índice de README.md (orden + títulos + archivos)."""
    readme = src_dir / "README.md"
    if not readme.exists():
        sys.exit(f"No existe {readme}")
    texto = readme.read_text(encoding="utf-8")
    patron = re.compile(
        r"^\|\s*(\d{2})\s*\|\s*`([^`]+\.mmd)`\s*\|\s*(.+?)\s*\|\s*[^|]+\|\s*$",
        re.MULTILINE,
    )
    filas = [
        {"num": num, "archivo": archivo, "titulo": titulo}
        for num, archivo, titulo in patron.findall(texto)
    ]
    if not filas:
        sys.exit("No se pudo parsear el índice de README.md (formato de tabla inesperado).")
    return filas


def commit_corto() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        return out or "—"
    except Exception:
        return "—"


def bbox_dibujo(dibujo) -> tuple[float, float, float, float]:
    """Caja (x, y, w, h) real de un Drawing, respetando sus transformaciones.

    ``getBounds()`` devuelve (x1, y1, x2, y2); es la API correcta porque
    incluye curvas y grupos (un bbox manual ignora transforms del SVG).
    """
    x1, y1, x2, y2 = dibujo.getBounds()
    return x1, y1, x2 - x1, y2 - y1


# ==============================================================================
# Compositor
# ==============================================================================
def auditoria(args: argparse.Namespace) -> int:
    """Comprueba .mmd ↔ PNG ↔ PDF ↔ índice README; exit 1 si algo desincroniza."""
    fallos: list[str] = []
    avisos: list[str] = []

    filas = leer_indice(args.src_dir)
    en_readme = {f["archivo"] for f in filas}
    en_disco = {p.name for p in args.src_dir.glob("*.mmd")}

    for faltante in sorted(en_disco - en_readme):
        fallos.append(f"índice: {faltante} no está en README.md")
    for sobrante in sorted(en_readme - en_disco):
        fallos.append(f"índice: README.md cita {sobrante}, que no existe")

    pdf = args.out_dir / f"POLIDOC-UML-001_v{args.version}.pdf"
    for fila in filas:
        base = Path(fila["archivo"]).with_suffix("")
        for artefacto in (
            args.src_dir / fila["archivo"],
            args.out_dir / f"{base.name}.svg",
            args.out_dir / "png" / f"{base.name}.png",
        ):
            if not artefacto.exists():
                fallos.append(f"falta: {artefacto} (make pdf lo regenera)")
            elif (args.src_dir / fila["archivo"]).stat().st_mtime > artefacto.stat().st_mtime \
                    and artefacto.suffix != ".mmd":
                fallos.append(f"desactualizado: {fila['archivo']} es más nuevo"
                              f" que {artefacto.name} (re-render)")

    if not pdf.exists():
        fallos.append(f"falta PDF: {pdf} (make pdf)")
    else:
        mas_nuevo = [
            f["archivo"] for f in filas
            if (args.src_dir / f["archivo"]).stat().st_mtime > pdf.stat().st_mtime
        ]
        if mas_nuevo:
            fallos.append(f"PDF desactualizado frente a: {', '.join(mas_nuevo)} (make pdf)")

    print(f"Auditoría de {len(filas) + len(en_disco - en_readme)} diagramas:")
    for linea in avisos + fallos:
        print(f"  ✗ {linea}")
    if fallos:
        print(f"resultado: FALLO ({len(fallos)})")
        return 1
    print("resultado: OK — diagramas, renders y PDF sincronizados")
    return 0


class CompositorPDF:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.fuente, self.fuente_b, self.fuente_i = registrar_fuentes()
        self.filas = leer_indice(args.src_dir)
        self.logo = self._buscar_logo()
        self.commit = commit_corto()
        self.fecha = datetime.now().strftime("%d/%m/%Y")
        self.pdf_nombre = f"POLIDOC-UML-001_v{args.version}.pdf"
        self.total_paginas = 2 + len(self.filas)

    # ---------- logo ----------
    def _buscar_logo(self) -> Path | None:
        candidatas = [
            self.args.logo,
            self.args.src_dir / ".." / ".." / "polidoc/static/imagenes/logopoliclinicatachira.svg",
            self.args.src_dir / ".." / ".." / "polidoc/static/imagenes/logoPolidoc.svg",
        ]
        for ruta in candidatas:
            if ruta and ruta.exists():
                return ruta.resolve()
        return None

    def _dibujar_logo(self, c: canvas.Canvas, centro_x: float, techo_y: float, alto: float) -> None:
        dibujo = svg2rlg(str(self.logo))
        if dibujo is None:
            return
        bx, by, bw, bh = bbox_dibujo(dibujo)
        if bh <= 0:
            return
        escala = alto / bh
        dibujo.scale(escala, escala)
        # renderPDF.draw(dx, dy) traslada el conteúdo: se centra en centro_x
        # y se apoya el borde superior del logo en techo_y.
        dx = centro_x - (bx + bw / 2.0) * escala
        dy = techo_y - (by + bh) * escala
        renderPDF.draw(dibujo, c, dx, dy)

    # ---------- pie ----------
    def _pie(self, c: canvas.Canvas, pagina: int) -> None:
        c.setStrokeColor(HexColor(GRIS_LINEA))
        c.setLineWidth(0.6)
        c.line(MARGEN, PIE_Y - 10, PAGE_W - MARGEN, PIE_Y - 10)
        c.setFont(self.fuente, 8)
        c.setFillColor(HexColor(GRIS_SUAVE))
        c.drawString(MARGEN, PIE_Y - 22,
                     f"POLIDOC · Diagramas UML · {self.args.code} · {self.args.classification}")
        c.drawRightString(PAGE_W - MARGEN, PIE_Y - 22,
                          f"página {pagina} de {self.total_paginas}")

    # ---------- portada ----------
    def _portada(self, c: canvas.Canvas) -> None:
        if self.logo:
            self._dibujar_logo(c, PAGE_W / 2, PAGE_H - 20, alto=92.0)

        y = PAGE_H - 166
        c.setFont(self.fuente_b, 40)
        c.setFillColor(HexColor(AZUL))
        c.drawCentredString(PAGE_W / 2, y, "POLIDOC")
        y -= 38
        c.setFont(self.fuente, 23)
        c.setFillColor(HexColor(GRIS))
        c.drawCentredString(PAGE_W / 2, y, "Diagramas UML")
        y -= 30
        c.setFont(self.fuente, 12)
        c.drawCentredString(PAGE_W / 2, y, DESCRIPCION)
        y -= 18
        c.drawCentredString(PAGE_W / 2, y, ORGANIZACION)
        y -= 20
        c.setFont(self.fuente, 9)
        c.setFillColor(HexColor(GRIS_SUAVE))
        c.drawCentredString(PAGE_W / 2, y, NORMAS)

        # --- clasificación destacada ---
        y -= 40
        self._etiqueta(c, PAGE_W / 2, y, self.args.classification)
        y -= 46

        # --- metadatos ---
        datos = [
            ("Documento", "Diagramas UML — POLIDOC"),
            ("Código", self.args.code),
            ("Versión", self.args.version),
            ("Fecha", self.fecha),
            ("Commit", self.commit),
            ("Autor", self.args.author),
            ("Organización", ORGANIZACION),
            ("Estado", "Documentación técnica"),
            ("Clasificación", self.args.classification),
            ("Alcance", f"{len(self.filas)} diagramas: clases, casos de uso, "
                         "secuencias, estados, componentes y despliegue"),
            ("Fuente", "docs/uml/ (Mermaid) · ‘make’ regenera SVG y PNG; "
                        "‘make pdf’ regenera este documento"),
        ]
        from reportlab.platypus import Table, TableStyle

        tabla = Table(datos, colWidths=[120.0, 400.0])
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), HexColor(FONDO_FILA)),
            ("GRID", (0, 0), (-1, -1), 0.5, HexColor(GRIS_LINEA)),
            ("FONT", (0, 0), (0, -1), self.fuente_b, 8.5),
            ("FONT", (1, 0), (1, -1), self.fuente, 8.5),
            ("TEXTCOLOR", (0, 0), (0, -1), HexColor(AZUL)),
            ("TEXTCOLOR", (1, 0), (1, -1), HexColor(GRIS)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))
        ancho, alto = tabla.wrapOn(c, ANCHO_TABLA, 10_000)
        y_tabla = y - alto
        tabla.drawOn(c, (PAGE_W - ancho) / 2, y_tabla)

        self._pie(c, 1)

    def _etiqueta(self, c: canvas.Canvas, centro_x: float, y: float, texto: str) -> None:
        """Banda de clasificación centrada (roja si CONFIDENCIAL)."""
        texto_banda = f"  {texto.upper()}  "
        c.setFont(self.fuente_b, 10.5)
        ancho = c.stringWidth(texto_banda, self.fuente_b, 10.5)
        confidencial = "CONFIDENCIAL" in texto.upper() or "RESTRINGIDO" in texto.upper()
        c.setFillColor(HexColor(ROJO if confidencial else GRIS_SUAVE))
        c.rect(centro_x - ancho / 2, y, ancho, 18, stroke=0, fill=1)
        c.setFillColor(HexColor(BLANCO))
        c.drawCentredString(centro_x, y + 5, texto_banda)

    # ---------- índice ----------
    def _indice(self, c: canvas.Canvas) -> None:
        y = PAGE_H - 64
        c.setFont(self.fuente_b, 24)
        c.setFillColor(HexColor(AZUL))
        c.drawString(MARGEN, y, "Índice")
        c.setStrokeColor(HexColor(AZUL))
        c.setLineWidth(1.2)
        c.line(MARGEN, y - 8, PAGE_W - MARGEN, y - 8)
        y -= 36

        grupo_anterior = None
        for i, fila in enumerate(self.filas):
            grupo = GRUPOS.get(fila["num"], "Otros")
            if grupo != grupo_anterior:
                if grupo_anterior is not None:
                    y -= 6
                c.setFont(self.fuente_b, 10.5)
                c.setFillColor(HexColor(AZUL_CLARO))
                c.drawString(MARGEN + 6, y, grupo.upper())
                y -= 18
                grupo_anterior = grupo

            pagina = i + 3
            c.setFont(self.fuente_b, 10)
            c.setFillColor(HexColor(GRIS))
            c.drawString(MARGEN + 6, y, fila["num"])
            x_titulo = MARGEN + 6 + 26
            c.setFont(self.fuente, 10)
            c.drawString(x_titulo, y, fila["titulo"])
            x_archivo = x_titulo + c.stringWidth(fila["titulo"], self.fuente, 10) + 8
            c.setFont(self.fuente_i, 8)
            c.setFillColor(HexColor(GRIS_SUAVE))
            if x_archivo + c.stringWidth(fila["archivo"], self.fuente_i, 8) < PAGE_W - MARGEN - 40:
                c.drawString(x_archivo, y, f"· {fila['archivo']}")

            c.setFont(self.fuente, 10)
            c.setFillColor(HexColor(AZUL))
            c.drawRightString(PAGE_W - MARGEN - 6, y, str(pagina))
            y -= 17

        self._pie(c, 2)

    # ---------- página de diagrama ----------
    def _diagrama(self, c: canvas.Canvas, fila: dict, pagina: int) -> None:
        png = self.args.out_dir / "png" / Path(fila["archivo"]).with_suffix(".png").name
        if not png.exists():
            sys.exit(f"Falta el render PNG de {fila['archivo']}: {png}\n"
                     "Regenera con `make pdf` desde docs/uml/.")

        c.setFont(self.fuente_b, 13)
        c.setFillColor(HexColor(AZUL))
        c.drawString(MARGEN, PAGE_H - 42, f"{fila['num']} · {fila['titulo']}")
        c.setFont(self.fuente_i, 8)
        c.setFillColor(HexColor(GRIS_SUAVE))
        c.drawRightString(PAGE_W - MARGEN, PAGE_H - 42, f"fuente: {fila['archivo']}")

        imagen = ImageReader(str(png))
        w, h = imagen.getSize()
        if w <= 0 or h <= 0:
            sys.exit(f"PNG vacío o ilegible: {png}")

        ancho_disp = PAGE_W - 2 * MARGEN
        alto_disp = TOP_Y - (PIE_Y + 22)
        escala = min(ancho_disp / w, alto_disp / h)
        dw, dh = w * escala, h * escala
        x = (PAGE_W - dw) / 2
        y = (PIE_Y + 22) + (alto_disp - dh) / 2

        c.drawImage(imagen, x, y, width=dw, height=dh, mask="auto")

        c.bookmarkPage(f"diag_{fila['num']}")
        c.addOutlineEntry(f"{fila['num']} · {fila['titulo']}",
                          f"diag_{fila['num']}", level=0, closed=False)

        self._pie(c, pagina)

    # ---------- construcción ----------
    def construir(self) -> Path:
        salida = self.args.out_dir / self.pdf_nombre
        c = canvas.Canvas(str(salida), pagesize=landscape(A4))

        c.setTitle(f"POLIDOC — Diagramas UML · {self.args.code} · v{self.args.version}")
        c.setAuthor(self.args.author)
        c.setSubject(
            f"Diagramas UML del sistema POLIDOC ({DESCRIPCION} — {ORGANIZACION})."
            f" Clasificación: {self.args.classification}."
        )
        c.setCreator("docs/uml/_tools/build_pdf.py (Mermaid + reportlab)")
        c.setKeywords(f"POLIDOC, UML, Mermaid, {self.args.code}, "
                      f"{self.args.classification}, {NORMAS}")

        c.bookmarkPage("portada")
        c.addOutlineEntry("Portada", "portada", level=0, closed=True)
        self._portada(c)
        c.showPage()

        c.bookmarkPage("indice")
        c.addOutlineEntry("Índice", "indice", level=0, closed=False)
        self._indice(c)
        c.showPage()

        for i, fila in enumerate(self.filas):
            self._diagrama(c, fila, i + 3)
            c.showPage()

        c.save()
        return salida


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compila el PDF único de diagramas UML.")
    parser.add_argument("--src-dir", type=Path, required=True,
                        help="directorio con los .mmd y README.md")
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="directorio out/ (con png/) y salida del PDF")
    parser.add_argument("--version", default="1.0")
    parser.add_argument("--code", default="POLIDOC-UML-001")
    parser.add_argument("--author", default="Pasante TSU. Xavier Lara")
    parser.add_argument("--classification", default="CONFIDENCIAL")
    parser.add_argument("--logo", type=Path, default=None,
                        help="ruta del logo SVG (omitir para autodetección)")
    parser.add_argument("--check", action="store_true",
                        help="auditoría de sincronización: .mmd vs PNG vs PDF vs README")
    args = parser.parse_args(argv)

    if args.check:
        return auditoria(args)

    salida = CompositorPDF(args).construir()
    print(f"PDF generado: {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
