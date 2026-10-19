# Makefile — diagramas UML POLIDOC
# ================================
# Uso básico:
#   make                  # renderiza/actualiza los 18 SVG en out/
#   make pdf              # genera out/POLIDOC-UML-001_v1.0.pdf (19 páginas, portada + índice + diagramas)
#   make version VER=v1.1 # regenera el PDF con otra versión de portada
#   make checklist        # auditoría: .mmd vs .svg vs .pdf vs README (salida legible)
#   make check            # CI/actual: 0 = todo sincronizado; 1 = algo desincronizado
#
# Requisitos:
#   - npm: npm install --global @mermaid-js/mermaid-cli   (o usar:  npx --yes)
#   - pip: pip install reportlab svglib pillow pypdf
#
# En entornos root/CI: PUPPETEER_CONFIG=_theme/puppeteer-config.json

MERMAID ?= mmdc
PYTHON  ?= python3
OUT     := out
SRC_DIR := .
PNG_DIR := $(OUT)/png
THEME   := _theme/polidoc.config.json
PUPPETEER ?= _theme/puppeteer-config.json

MMD     := $(wildcard *.mmd)
SVG     := $(patsubst %.mmd,$(OUT)/%.svg,$(MMD))
PNG     := $(patsubst %.mmd,$(PNG_DIR)/%.png,$(MMD))
PDF     := $(OUT)/POLIDOC-UML-001_v1.0.pdf
VER     := v1.0

MC      ?= $(if $(wildcard /tmp/opencode/mermaid-cli/node_modules/.bin/mmdc),/tmp/opencode/mermaid-cli/node_modules/.bin/mmdc,$(MERMAID))

PUPPETEER_FLAG := $(if $(wildcard $(PUPPETEER)),-p $(PUPPETEER),)

.PHONY: all pdf render check checklist clean version help

all: render pdf

help:
	@echo "make / make render   -> 18 SVG en out/"
	@echo "make pdf             -> PDF único $(PDF)"
	@echo "make version VER=v1.x-> recompila el PDF con otra versión"
	@echo "make checklist       -> revisión legible de sincronización"
	@echo "make check           -> auditoría silenciosa (exit 0/1, para CI)"
	@echo "make one F=<archivo> -> re-render individual"
	@echo "make clean           -> borra out/"

$(OUT) $(PNG_DIR):
	mkdir -p $@

# --- SVG (para web/repo) ------------------------------------------------------
$(OUT)/%.svg: %.mmd | $(OUT)
	$(MC) -i $< -o $@ -c $(THEME) -b transparent $(PUPPETEER_FLAG)

# --- PNG (insumo del PDF, fondo blanco) ---------------------------------------
$(PNG_DIR)/%.png: %.mmd | $(PNG_DIR)
	$(MC) -i $< -o $@ -c $(THEME) -b white -w 2200 -e png $(PUPPETEER_FLAG)

render: $(SVG)

one:
	@test -n "$(F)" || { echo "Uso: make one F=<archivo>.mmd"; exit 1; }
	$(MC) -i $(F) -o $(OUT)/$(F:.mmd=.svg) -c $(THEME) -b transparent $(PUPPETEER_FLAG)

# --- PDF único ----------------------------------------------------------------
pdf: $(PNG)
	$(PYTHON) _tools/build_pdf.py \
		--src-dir . --out-dir $(OUT) --version $(VER:v%=%) \
		--code POLIDOC-UML-001 \
		--author "Pasante TSU. Xavier Lara" \
		--classification CONFIDENCIAL

version:
	$(PYTHON) _tools/build_pdf.py \
		--src-dir . --out-dir $(OUT) --version $(VER:v%=%) \
		--code POLIDOC-UML-001 \
		--author "Pasante TSU. Xavier Lara" \
		--classification CONFIDENCIAL

# --- Auditoría de sincronización ----------------------------------------------
# Exit 1 si: (a) falta PNG de algún .mmd, (b) algún .mmd es más nuevo que su
# PNG, (c) el PDF no existe o es más viejo que algún .mmd.
checklist:
	@$(PYTHON) _tools/build_pdf.py --check --src-dir . --out-dir $(OUT)
# --- Auditoría silenciosa para CI ----------------------------------------------
check: checklist

clean:
	rm -rf $(OUT)
