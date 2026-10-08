#!/usr/bin/env python3
"""Exporta el Manual de Usuario POLIDOC a HTML y PDF (fase 3).

Genera un único archivo con portada (control de versión), índice y todos los
capítulos en orden. Las imágenes se incrustan en base64 para que el HTML y
el PDF sean autónomos.

Uso:
    python3 docs/manual-usuario/exportar.py            # HTML + PDF
    python3 docs/manual-usuario/exportar.py --html     # solo HTML
    python3 docs/manual-usuario/exportar.py --pdf      # solo PDF
    python3 docs/manual-usuario/exportar.py --salida /ruta/dir

Salida por defecto: docs/manual-usuario/dist/manual-polidoc.{html,pdf}

Requisitos: Opcionales y AJENOS al entorno de la aplicación. Este script no
forma parte del sistema Django, no se importa desde él y sus dependencias NO
están en `requirements.txt`: son solo para generar el manual distribuible.
Para instalarlas (solo quien quiera exportar):

    pip install -r docs/manual-usuario/requirements-export.txt

Sin ellas el HTML no se puede generar y el script sale con error 1; el
manual en Markdown sigue siendo la fuente válida.
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
DIST = BASE / "dist"

# Orden de lectura del manual (los mismos de verificar.py).
CAPITULOS = [
    "01-introduccion.md",
    "02-primeros-pasos.md",
    "03-roles-y-permisos.md",
    "04-consultar-y-buscar.md",
    "05-crear-documento.md",
    "06-aprobaciones.md",
    "07-versionado-planilla.md",
    "08-descargas-distribucion.md",
    "09-troubleshooting.md",
    "10-vigencia-y-vencimientos.md",
    "11-guia-trabajador-coordinador.md",
    "12-guia-jefe-departamento.md",
    "13-guia-administrador.md",
    "14-auditoria-e-informes.md",
    "A-glosario.md",
]

CSS = """
@page {
    size: A4;
    margin: 2cm 1.8cm;
    @bottom-center { content: counter(page); font-size: 9pt; color: #666; }
}
@page portada { margin: 0; @bottom-center { content: none; } }
body {
    font-family: "DejaVu Sans", "Helvetica", "Arial", sans-serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #1a1a1a;
}
h1 { font-size: 20pt; color: #0b3d5c; border-bottom: 2px solid #0b3d5c;
     padding-bottom: 4pt; margin: 0 0 12pt; page-break-before: always; }
h1:first-of-type { page-break-before: avoid; }
h2 { font-size: 14pt; color: #0b3d5c; margin-top: 14pt; }
h3 { font-size: 12pt; color: #245b7a; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9pt; }
th, td { border: 1px solid #bbb; padding: 4pt 6pt; text-align: left;
         vertical-align: top; }
th { background: #e8f0f6; }
code { font-family: "DejaVu Sans Mono", "Courier New", monospace;
       font-size: 9pt; background: #f2f2f2; padding: 0 2pt; }
pre { background: #f5f5f5; border: 1px solid #ddd; padding: 8pt;
      font-size: 9pt; white-space: pre-wrap; }
blockquote { border-left: 3px solid #0b3d5c; margin: 8pt 0; padding: 4pt 10pt;
             background: #f4f8fb; color: #333; }
img { max-width: 100%; border: 1px solid #ddd; margin: 6pt 0; }
a { color: #0b3d5c; text-decoration: none; }
.portada { page: portada; page-break-after: always; text-align: center;
           padding: 4cm 2cm 0; }
.portada h1 { border: none; page-break-before: avoid; font-size: 28pt; }
.portada .sub { font-size: 14pt; color: #444; margin-top: 10pt; }
.portada table { margin: 3cm auto 0; width: 80%; }
.toc ul { list-style: none; padding-left: 0; }
.toc li { margin: 4pt 0; }
.nota { font-size: 8.5pt; color: #666; margin-top: 30pt; }
"""


def metadata() -> dict[str, str]:
    """Lee el control de versión de la portada del README."""
    texto = (BASE / "README.md").read_text(encoding="utf-8")
    meta = {}
    for clave, valor in re.findall(
        r"^\| (Versión del manual|Fecha|Versión del sistema verificada|Estado) \| (.+?) \|$",
        texto, re.MULTILINE,
    ):
        meta[clave] = valor.strip().strip("*")
    meta.setdefault("Versión del manual", "—")
    meta.setdefault("Fecha", date.today().isoformat())
    meta.setdefault("Versión del sistema verificada", "—")
    meta.setdefault("Estado", "—")
    return meta


def incrustar_imagenes(html: str, cap: Path) -> str:
    """Sustituye las rutas de imagen por data URIs base64."""

    def repl(m: re.Match) -> str:
        src = m.group(1)
        ruta = (cap.parent / src).resolve()
        if not ruta.exists():
            return f"[imagen pendiente: {src}]"
        mime = "image/png" if ruta.suffix.lower() == ".png" else "image/jpeg"
        b64 = base64.b64encode(ruta.read_bytes()).decode("ascii")
        return f'src="data:{mime};base64,{b64}"'

    return re.sub(r'src="([^"]+)"', repl, html)


def md_a_html(texto: str, cap: Path) -> str:
    import markdown

    extensiones = ["tables", "fenced_code", "sane_lists"]
    extra = markdown.markdown(
        texto, extensions=extensiones, output_format="html5",
    )
    # Enlaces .md → anclas de capítulo del mismo documento.
    extra = re.sub(
        r'href="([^"]+)\.md(#[^"]*)?"',
        lambda m: f'href="#{Path(m.group(1)).stem}"', extra,
    )
    extra = incrustar_imagenes(extra, cap)
    return extra


def portada_html(meta: dict[str, str]) -> str:
    filas = "".join(
        f"<tr><th>{k}</th><td>{v}</td></tr>"
        for k, v in meta.items()
    )
    return f"""
<div class="portada">
  <h1>Manual de Usuario — POLIDOC</h1>
  <p class="sub">Sistema de gestión documental · Policlínica Táchira<br>
  Normas ISO 9001, 27001 y 15489</p>
  <table>{filas}</table>
  <p class="nota">Documento generado automáticamente desde
  <code>docs/manual-usuario/</code>. La fuente de verdad es el repositorio;
  este export refleja la versión de control indicada arriba.</p>
</div>
"""


def titulo_capitulo(texto: str) -> str:
    m = re.search(r"^# (.+)$", texto, re.MULTILINE)
    return m.group(1).strip() if m else "—"


def quitar_h1(texto: str) -> str:
    """Elimina la línea H1 inicial del capítulo (ya usada como ancla).

    Así no hace falta depurar un ``<h1>`` duplicado con regex después de
    convertir: el H1 con ``id`` se escribe a mano y el del markdown se omite.
    """
    return re.sub(r"^# .*\n", "", texto, count=1, flags=re.MULTILINE)


def construir_html() -> str:
    meta = metadata()
    partes = [
        "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>",
        "<title>Manual de Usuario — POLIDOC</title>",
        f"<style>{CSS}</style></head><body>",
        portada_html(meta),
    ]
    entradas = []
    cuerpo = []
    for nombre in CAPITULOS:
        cap = BASE / nombre
        if not cap.exists():
            print(f"  aviso: falta {nombre}", file=sys.stderr)
            continue
        texto = cap.read_text(encoding="utf-8")
        # Quitar la línea de navegación inferior (enlaces .md ya resueltos).
        texto = re.sub(r"^\*\*(Anterior|Siguiente|Volver).*\n?", "", texto,
                       flags=re.MULTILINE)
        ancla = Path(nombre).stem
        titulo = titulo_capitulo(texto)
        # El H1 del capítulo se escribe a mano con su ancla; el del markdown
        # se omite para no duplicarlo (ver quitar_h1).
        cuerpo.append(f"<h1 id='{ancla}'>{titulo}</h1>")
        cuerpo.append(md_a_html(quitar_h1(texto), cap))
        entradas.append(f"<li><a href='#{ancla}'>{titulo}</a></li>")
    partes.append(
        "<div class='toc'><h1 id='indice' style='page-break-before: avoid;'>"
        "Índice</h1><ul>" + "".join(entradas) + "</ul></div>"
    )
    partes.extend(cuerpo)
    partes.append("</body></html>")
    return "\n".join(par for par in partes if par)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", action="store_true", help="solo HTML")
    parser.add_argument("--pdf", action="store_true", help="solo PDF")
    parser.add_argument("--salida", type=Path, default=DIST,
                        help="directorio de salida")
    args = parser.parse_args()
    hacer_html = not args.pdf or args.html
    hacer_pdf = not args.html or args.pdf
    if args.html and args.pdf:
        hacer_html = hacer_pdf = True

    try:
        import markdown  # noqa: F401
    except ImportError:
        print("Falta la librería 'markdown': "
              "pip install -r docs/manual-usuario/requirements-export.txt",
              file=sys.stderr)
        return 1

    args.salida.mkdir(parents=True, exist_ok=True)
    html = construir_html()

    if hacer_html:
        destino = args.salida / "manual-polidoc.html"
        destino.write_text(html, encoding="utf-8")
        print(f"HTML: {destino} ({destino.stat().st_size // 1024} KB)")

    if hacer_pdf:
        try:
            import weasyprint
        except ImportError:
            print("Falta weasyprint para el PDF (el HTML ya quedó generado): "
                  "pip install -r docs/manual-usuario/requirements-export.txt",
                  file=sys.stderr)
            return 1
        destino = args.salida / "manual-polidoc.pdf"
        weasyprint.HTML(string=html, base_url=str(BASE)).write_pdf(destino)
        print(f"PDF:  {destino} ({destino.stat().st_size // 1024} KB)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
