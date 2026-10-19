# Diagramas UML de POLIDOC

Diagramas de referencia del sistema **POLIDOC**, modelados a partir del código
real (no de suposiciones). La fuente de verdad de cada diagrama es el archivo
`.mmd` (Mermaid); los `.svg` de `out/` se generan y **no deben editarse a mano**.

> **Aislamiento (decisión de producto):** estos diagramas están aislados del
> manual de usuario y de la aplicación; no se enlazan desde la UI. El único
> puntero es el interno en `CONTEXT.txt` §23.3. Si en el futuro se integran,
> se decidirá aparte.

## Índice

| # | Archivo | Artefacto | Estado |
|---|---------|-----------|--------|
| 00 | `00-vista-general.mmd` | Paquetes y dependencias generales | Revisado |
| 01 | `01-clases-dominio.mmd` | Clases: núcleo documental ISO | Revisado |
| 02 | `02-clases-usuarios.mmd` | Clases: organización, usuarios y delegación | Revisado |
| 03 | `03-clases-sgc.mmd` | Clases: motor dinámico SGC | Revisado |
| 04 | `04-clases-aprobaciones.mmd` | Clases: cadenas de aprobación | Revisado |
| 05 | `05-clases-acceso-descargas.mmd` | Clases: acceso, distribución y descargas | Revisado |
| 06 | `06-clases-auditoria.mmd` | Clases: auditoría e infraestructura | Revisado |
| 07 | `07-casos-uso.mmd` | Casos de uso por actor | Revisado |
| 08 | `08-secuencia-aprobacion-documento.mmd` | Secuencia: aprobación de documento | Revisado |
| 09 | `09-secuencia-aprobacion-planilla.mmd` | Secuencia: aprobación de planilla | Revisado |
| 10 | `10-secuencia-descarga.mmd` | Secuencia: descarga controlada | Revisado |
| 11 | `11-secuencia-cambio-estado.mmd` | Secuencia: cambio de estado | Revisado |
| 12 | `12-estados-documento.mmd` | Estados: `Documento` | Revisado |
| 13 | `13-estados-planilla.mmd` | Estados: planilla y solicitud de cambio | Revisado |
| 14 | `14-estados-descarga.mmd` | Estados: solicitud y aprobaciones de descarga | Revisado |
| 15 | `15-estados-delegacion.mmd` | Estados/condiciones: delegación de jefatura | Revisado |
| 16 | `16-componentes.mmd` | Componentes del stack | Revisado |
| 17 | `17-despliegue.mmd` | Despliegue Docker | Revisado |

## Render

Requisito: [Mermaid CLI](https://github.com/mermaid-js/mermaid-cli) y
`pip install reportlab svglib pillow` (el PDF usa las mismas versiones que
ya están en `requirements.txt`).

```bash
# Instalación global (una vez)
npm install --global @mermaid-js/mermaid-cli

# Renderizar todo a out/*.svg
cd docs/uml
make

# En contenedores/CI que corren como root (Puppeteer necesita --no-sandbox)
make PUPPETEER_CONFIG=_theme/puppeteer-config.json

# Un solo diagrama
make one F=08-secuencia-aprobacion-documento.mmd

# Comprobar el renderizador
make check
```

Sin instalación global también se puede usar:

```bash
npx --yes @mermaid-js/mermaid-cli -i 01-clases-dominio.mmd \
  -o out/01-clases-dominio.svg -c _theme/polidoc.config.json -b transparent
```

## PDF único

`make pdf` genera el documento compilado **`out/POLIDOC-UML-001_v1.0.pdf`**
(20 páginas, A4 horizontal):

1. **Portada** — logo institucional, código `POLIDOC-UML-001`, clasificación
   **CONFIDENCIAL**, versión, fecha automática, commit y metadatos.
2. **Índice** — los 18 diagramas agrupados por tipo (clases, casos de uso,
   secuencias, estados, arquitectura) con números de página.
3. **Un diagrama por página** — título, fuente `.mmd`, pie
   `POLIDOC · Diagramas UML · POLIDOC-UML-001 · CONFIDENCIAL` y numeración.
4. Extras: metadatos PDF (título/autor/asunto/palabras clave) y marcadores
   laterales por diagrama.

```bash
# crear el PDF (renderiza PNG de alta resolución + compone)
make pdf

# subir la versión de portada
make version VER=v1.1
```

Los títulos y el orden los toma del índice de este README, así que **agregar
un diagrama nuevo** es solo: crear el `.mmd`, añadirlo a la tabla del índice y
ejecutar `make pdf`.

## Checklist de sincronización

Ver [`checklist-sync.md`](checklist-sync.md). Matriz «si cambias X → actualiza
Y», Definition of Done por PR y recordatorios de estilo.

**Auditoría automática** (también pensada para CI, exit 0/1):

```bash
make checklist   # revisa .mmd ↔ SVG ↔ PNG ↔ PDF ↔ índice del README
```

Reporta tres tipos de fallo: archivos que faltan, `.mmd` más nuevo que su
render, y PDF desactualizado frente a los `.mmd`. Al añadir o renombrar un
diagrama hay que reflejarlo en la tabla del índice; el formato lo exige el
parser (`_tools/build_pdf.py`).

## Convenciones

1. **Un artefacto por archivo**, con prefijo numérico para el orden de lectura.
2. **Nombres reales del código** (`AprobacionDescarga`, no “Aprobación de
   Descarga”) para que el diagrama se pueda contrastar con `models.py`.
3. **Estados persistidos ≠ condiciones derivadas ≠ fases conceptuales**:
   - Estados: valores de `ESTADO_CHOICES`.
   - Condiciones derivadas: “vencido”, “vigente”, “programada”.
   - Fases conceptuales: retención/destrucción (fechas + comandos).
4. **Tema compartido** en `_theme/polidoc.config.json` (Mermaid no soporta
   `include`; el Makefile pasa el archivo con `-c`).
5. **Los artefactos no se editan**: `.svg`, `.png` y `.pdf` de `out/` se
   regeneran desde el `.mmd` (`make`, `make pdf`).
6. Si un cambio de código altera el modelo, el PR debe actualizar el `.mmd`
   correspondiente.

## Supuestos y correcciones clave

Estas decisiones están verificadas contra el código y corrigen errores comunes
al modelar POLIDOC:

- **`Documento` tiene 4 estados persistidos**: `borrador`, `en_revision`,
  `activo`, `obsoleto`. “Vencido” es una condición derivada de
  `fecha_vencimiento`, no un estado.
- **Aprobación de documentos/planillas**: 1 paso si el solicitante **ejerce
  jefatura** (jefe natural **o delegado vigente**); 2 pasos si no. En la cadena
  de 2 pasos el evaluador es el jefe del departamento de origen **solo si
  existe y no es el solicitante**; si no, cae al jefe de Gerencia General.
- **Descargas `restringido`**: doble control **secuencial** (jefe del
  departamento dueño → jefe de Gerencia General). `interno`/`confidencial`:
  1 paso (jefe del departamento dueño). Sin jefe: el paso queda `esperando`
  (`aprobador=None`), sin fallback a administrador.
- **El doble control `restringido` no aplica a la creación de documentos**:
  aplica a las descargas.
- **Cambio de estado**: admin y jefe de GG aplican directo; el resto pasa por
  **un solo aprobador** (rol variable: GG si ejerce jefatura; jefe del
  departamento si no; GG como fallback).
- **Retención/destrucción** no son estados automáticos: `verificar_vencimientos`
  fija `fecha_retencion`/`fecha_destruccion`; `verificar_destruccion` es un
  management command que notifica (no está en Celery Beat).
- **Base relacional**: SQLite (`db_new.sqlite3`) por defecto. PostgreSQL es
  opcional vía `DB_ENGINE`; no hay servicio Postgres en `docker-compose.yml`.
- **Auditoría**: los logs viven en MongoDB (no son una tabla Django), con
  buffer local y `reprocesar_logs_buffer`; `AUDIT_FAIL_CLOSED=True` bloquea la
  entrega de contenido si no se puede auditar.
- **`ReporteAuditoriaStorage`** es una clase de infraestructura
  (`FileSystemStorage`), no una entidad de dominio.

## Fuentes de verdad

- `apps/documentos/models.py`
- `apps/documentos/views.py`
- `apps/documentos/services/` (`descargas.py`, `cambios_estado.py`,
  `planillas.py`, `reasignacion.py`, `solicitudes.py`, `aprobaciones.py`)
- `apps/documentos/permissions.py`
- `apps/documentos/jefatura.py`
- `apps/autenticacion/models.py`
- `apps/auditoria/models.py`, `apps/auditoria/tasks.py`
- `polidoc/settings.py` (`CELERY_BEAT_SCHEDULE`, `AUDIT_FAIL_CLOSED`)
- `docker-compose.yml`
- `CONTEXT.txt` (§5, §6, §10.3, §22.7, §23)
