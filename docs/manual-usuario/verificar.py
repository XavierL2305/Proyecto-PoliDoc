#!/usr/bin/env python3
"""Verificación del Manual de Usuario POLIDOC.

Comprueba (sin modificar nada):

  1. Enlaces internos entre capítulos → el archivo existe.
  2. Referencias a imágenes `imagenes/...` → el archivo existe
     (si no, se reporta como captura pendiente según CAPTURAS.md).
  3. Criterios de capítulo terminado de README.md:
       - pasos numerados o tablas de instrucciones,
       - sección "Campos obligatorios" (los capítulos que deben tenerla),
       - sección "Errores frecuentes" (los capítulos que deben tenerla).

Uso:  python3 docs/manual-usuario/verificar.py
Código de salida 0 = todo correcto; 1 = hay errores (no solo pendientes).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

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
    "A-glosario.md",
]

# Capítulos que DEBEN traer "Campos obligatorios" y "Errores frecuentes".
CON_CAMPOS = {"05-crear-documento.md", "07-versionado-planilla.md"}
CON_ERRORES = set(CAPITULOS) - {"A-glosario.md", "09-troubleshooting.md"}
CON_NUMERACION = set(CAPITULOS) - {"A-glosario.md"}

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+\.md)(?:#[^)]*)?\)")
IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
CODE_RE = re.compile(r"`[^`\n]*`")
NUM_RE = re.compile(r"^(\s*\d+\.\s|\s*#+\s*\d+\.\s)", re.MULTILINE)

errores: list[str] = []
pendientes: list[str] = []


def capturas_declaradas() -> set[str]:
    texto = (BASE / "CAPTURAS.md").read_text(encoding="utf-8")
    rutas = set(re.findall(r"`([\w./-]+\.png)`", texto))
    return {
        r if r.startswith("imagenes/") else f"imagenes/{r}"
        for r in rutas
    }


def main() -> int:
    capitulos = [BASE / c for c in CAPITULOS]
    declaradas = capturas_declaradas()
    usadas: set[str] = set()

    # 1. Enlaces internos
    for cap in capitulos + [BASE / "README.md"]:
        if not cap.exists():
            errores.append(f"existe: falta el capítulo {cap.name}")
            continue
        for enlace in LINK_RE.findall(cap.read_text(encoding="utf-8")):
            if enlace.startswith(("http://", "https://", "mailto:")):
                continue
            destino = (cap.parent / enlace).resolve()
            if not destino.exists():
                errores.append(f"enlace roto: {cap.name} → {enlace}")

    # 2. Imágenes
    for cap in capitulos + [BASE / "README.md"]:
        if not cap.exists():
            continue
        texto = CODE_RE.sub("", cap.read_text(encoding="utf-8"))
        for img in IMG_RE.findall(texto):
            if img.startswith(("http://", "https://")):
                continue
            usadas.add(img)
            if not (cap.parent / img).exists():
                if img in declaradas:
                    pendientes.append(f"{cap.name} → {img}")
                else:
                    errores.append(f"imagen no declarada: {cap.name} → {img}")

    for img in sorted(declaradas - usadas):
        pendientes.append(f"(declarada, sin usar) {img}")

    # 3. Criterios de capítulo terminado
    for cap in capitulos:
        if not cap.exists():
            continue
        texto = cap.read_text(encoding="utf-8")
        tiene_errores = "Errores frecuentes" in texto
        tiene_campos = "Campos obligatorios" in texto
        if cap.name in CON_ERRORES and not tiene_errores:
            errores.append(f"criterio 3: {cap.name} sin caja 'Errores frecuentes'")
        if cap.name in CON_CAMPOS and not tiene_campos:
            errores.append(f"criterio 2: {cap.name} sin sección 'Campos obligatorios'")
        if cap.name in CON_NUMERACION and not NUM_RE.search(texto):
            errores.append(f"criterio 1: {cap.name} sin secciones/pasos numerados")

    # Reporte
    print("Verificación del manual —\n")
    print(f"Capítulos: {len([c for c in capitulos if c.exists()])}/{len(CAPITULOS)}")
    print(f"Imágenes referenciadas: {len(usadas)} | declaradas en CAPTURAS.md: {len(declaradas)}")

    if pendientes:
        print(f"\nCapturas pendientes ({len(pendientes)}):")
        for p in sorted(pendientes):
            print(f"  ⬜ {p}")

    if errores:
        print(f"\nERRORES ({len(errores)}):")
        for e in errores:
            print(f"  ✗ {e}")
        return 1

    print("\n✓ Sin errores de enlaces, imágenes ni criterios.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
