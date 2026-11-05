#!/usr/bin/env python3
"""Genera el Informe de Pruebas de Carga de POLIDOC v1.0 (Markdown + DOCX).

Uso:
    python3 docs/informes/generar_informe_carga.py

Genera a partir de la evidencia congelada en `docs/informes/datos/`:
    docs/informes/INFORME_PRUEBAS_CARGA_v1.0.md
    docs/informes/INFORME_PRUEBAS_CARGA_v1.0.docx
    docs/informes/graficas/*.png

El PDF se obtiene convirtiendo el .docx con LibreOffice:
    soffice --headless --convert-to pdf --outdir docs/informes \
        docs/informes/INFORME_PRUEBAS_CARGA_v1.0.docx

Este script es documentación: no forma parte del entorno Django.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATOS = BASE / "datos"
STATS = DATOS / "stats"
GRAFICAS = BASE / "graficas"
SALIDA_MD = BASE / "INFORME_PRUEBAS_CARGA_v1.0.md"
SALIDA_DOCX = BASE / "INFORME_PRUEBAS_CARGA_v1.0.docx"

AUTOR = "TSU. Xavier Lara"
VERSION = "1.0"
FECHA = date(2026, 11, 5)
TITULO = "Informe de Pruebas de Carga"
SUBTITULO = (
    "PoliDoc \u2014 Matriz de rendimiento a 27 y 54 usuarios, "
    "optimizaci\u00f3n de endpoints y blindaje de Active Directory"
)

AZUL = "#1F3A5F"
AZUL_CLARO = "#2E5C8A"
NARANJA = "#D97706"
GRIS = "#777777"
ROJO = "#B91C1C"
VERDE = "#15803D"

# --------------------------------------------------------------------------
# Cifras medidas en la sesi\u00f3n (fuentes: profile_endpoints.py, cpu_bench.py,
# inventario de corridas y logs de la suite).
# --------------------------------------------------------------------------
CONSULTAS_JEFE = {
    # endpoint: (antes, despu\u00e9s) \u2014 conteo de SQL por petici\u00f3n (rol jefe)
    "catalogo": (9, 8),
    "detalle": (44, 24),
    "mis_documentos": (28, 7),
    "departamento": (9, 7),
    "buscar_api": (19, 7),
    "ver_archivo": (4, 4),
    "descargar": (5, 5),
    "estado_conversion": (3, 3),
    "preview_plantilla": (7, 7),
    "crear_plantilla": (9, 9),
}

CPU_PETICION = {
    # endpoint: (Fase 0, final) \u2014 ms de CPU por petici\u00f3n (mejor de 5, rol jefe)
    "catalogo": (63.2, 47.4),
    "detalle": (178.3, 129.1),
    "mis_documentos": (78.7, 52.2),
    "departamento": (84.1, 61.2),
    "buscar_api": (55.2, 46.0),
}

FALLOS_TESTS = [
    ("test_un_solo_aggregate", "FAIL", "documentos.tests_performance",
     "Cuenta 2 consultas donde la prueba exige 1 (conteo de vigencia)."),
    ("test_visibilidad_afecta_todos", "FAIL", "documentos.tests",
     "El filtro \u00abafecta a todos\u00bb no devuelve el documento esperado."),
    ("test_afecta_todos_no_se_repite_por_departamento", "FAIL",
     "documentos.tests",
     "Un documento \u00abafecta a todos\u00bb se repite por departamento."),
    ("test_reservas_concurrentes_no_duplican", "ERROR",
     "documentos.tests_plantillas",
     "Carrera en la reserva de c\u00f3digo (intermitente: pas\u00f3 en 1 de 4 corridas)."),
]

CORRIDAS_TESTS = [
    ("tests_after.log", "14:38", "1210", "4 FAIL + 1 ERROR",
     "Cambiado el c\u00f3digo; incluye 1 fallo extra intermitente."),
    ("tests_before_subset.log", "14:40", "16", "3 FAIL + 1 ERROR",
     "Subconjunto con los cambios en stash: mismos 4 fallos (l\u00ednea base)."),
    ("tests_after2.log", "14:48", "1210", "3 FAIL + 1 ERROR",
     "Los 4 fallos preexistentes; el intermitente extra no se repiti\u00f3."),
    ("tests_fase2.log", "15:54", "1210", "3 FAIL",
     "Tras la Fase 2: el ERROR intermitente pas\u00f3 en esta corrida."),
    ("tests_fase2b.log", "16:29", "1210", "3 FAIL + 1 ERROR",
     "\u00daltima corrida: mismos 4 fallos preexistentes."),
]

SERVICIOS = [
    ("Aplicaci\u00f3n", "Django 6.1.2 (polidoc)"),
    ("Servidor WSGI", "gunicorn 26.2.0 \u2014 3 workers gthread \u00d7 4 threads (127.0.0.1:8002)"),
    ("Proxy / est\u00e1ticos", "nginx 1.26.3 (127.0.0.1:8080 \u2192 gunicorn)"),
    ("Base de datos", "PostgreSQL 17.11 (socket Unix)"),
    ("Cach\u00e9", "Redis 8.0.2"),
    ("Cola", "RabbitMQ (vhost loadtest)"),
    ("Almacenamiento", "MongoDB 7.0.14 (/opt/mongo)"),
    ("Generador de carga", "Locust 2.47.0 (CLI, desde 127.0.0.1)"),
    ("Perfilado", "profile_endpoints.py (CaptureQueriesContext), cpu_bench.py (getrusage)"),
    ("Muestreo", "sample_resources.py (CPU/mem/usuarios cada 5 s), sample_processes.py"),
]

ROLES = [
    ("AdminUser", "loadtest_admin", "3", "11 %",
     "Administraci\u00f3n de plantillas y par\u00e1metros"),
    ("CoordUser", "loadtest_coord", "7", "26 %",
     "Coordinaci\u00f3n y revisi\u00f3n de documentos"),
    ("JefeUser", "loadtest_jefe", "7", "26 %",
     "Aprobaci\u00f3n y delegaciones de departamento"),
    ("TrabUser", "loadtest_trab", "10", "37 %",
     "Consulta, descarga y uso diario"),
]

TAREAS = [
    ("GET /documentos/", "catalogo", "21", "Cat\u00e1logo con filtros"),
    ("GET detalle de documento", "detalle", "16", "Ficha, aprobaciones y descargas"),
    ("GET mis-documentos", "mis_documentos", "14", "Listado propio con conteos"),
    ("GET departamento", "departamento", "11", "Documentos del departamento"),
    ("GET api/buscar-sugerencias", "buscar_api", "10", "B\u00fasqueda de sugerencias (latencia en caliente)"),
    ("GET ver-archivo", "ver_archivo", "10", "Previsualizaci\u00f3n del PDF"),
    ("GET estado-conversion", "estado_conversion", "6", "Estado de conversi\u00f3n (polling)"),
    ("GET preview de plantilla", "preview_plantilla", "5", "Vista previa de plantilla"),
    ("GET descargar", "descargar", "5", "Descarga con registro de uso"),
    ("GET/POST nueva plantilla", "crear_plantilla", "1", "Alta de plantilla (poco frecuente)"),
]

OPTIMIZACIONES_CODIGO = [
    ("apps/documentos/views.py",
     "`mis_documentos` con conteo agrupado + `_PaginatorConteoFijo`",
     "28 \u2192 7 consultas; paginaci\u00f3n sin COUNT por fila"),
    ("apps/documentos/views.py",
     "`detalle_documento`: 1 consulta de `SolicitudDescarga` antes que 4",
     "44 \u2192 24 consultas (rol jefe)"),
    ("apps/documentos/views.py",
     "`select_related` de tipo, departamento, jefe y periodo de vigencia",
     "Evita N+1 en la ficha"),
    ("apps/documentos/views.py",
     "Aprobaciones consolidadas e `ai.consumo_hoy` solo si la IA est\u00e1 activa",
     "Evita trabajo in\u00fatil por petici\u00f3n"),
    ("apps/documentos/models.py",
     "`Formato.get_secciones()` memorizado con `prefetch_related('campos')`",
     "Secciones \u00aben caliente\u00bb sin idas a BD"),
    ("apps/documentos/jefatura.py",
     "Memoria por petici\u00f3n de delegaciones y jefaturas",
     "`es_jefe_efectivo`, `departamentos_jefados`, `es_delegado_vigente` y "
     "`aprobadores_que_repr` sin repetir consultas"),
    ("apps/documentos/middleware.py (nuevo)",
     "`CachePeticionMiddleware`: registro de cach\u00e9 por petici\u00f3n",
     "Sostiene las memorias anteriores sin fugas"),
    ("polidoc/settings.py",
     "Registro del middleware",
     "Sin cambios de comportamiento"),
    ("apps/documentos/permissions.py",
     "`puede_descargar(..., descarga_autorizada_previa=...)`",
     "Reutiliza la comprobaci\u00f3n ya hecha en la vista"),
    ("apps/documentos/api.py",
     "`prefetch_related` en `buscar_sugerencias`",
     "20 \u2192 7 consultas en la b\u00fasqueda"),
]

OPTIMIZACIONES_ENTORNO = [
    "PostgreSQL sobre socket Unix en lugar de TCP de 127.0.0.1 (menor coste por conexi\u00f3n).",
    "Prepared statements de PostgreSQL activos en la conexi\u00f3n del ORM.",
    "Health checks de gunicorn en nginx (workers viejos sin curl de arranque).",
    "keepalive hacia gunicorn en nginx (HTTP/1.1 en upstream).",
    "gzip descartado a prop\u00f3sito: en 4 vCPU el coste de comprimir "
    "superaba el ahorro de red en loopback.",
    "PBKDF2 fijado a 150 000 iteraciones en `loadtest_settings` (1 500 000 "
    "costaban 4\u20135 s por login).",
    "Warmup de 12 usuarios/30 s antes de cada corrida (los primeros logins "
    "pagan el arranque de gunicorn).",
]

INCIDENTE_CRONO = [
    ("Sesiones previas (03/11) y ma\u00f1ana del 04/11",
     "Corridas de carga con las 4 cuentas reales de personas",
     "Cada `POST /login/` ejecutaba un `simple_bind_s('PTH\\usuario', "
     "contrase\u00f1a local)` contra el DC de 15.15.1.60:389"),
    ("04/11, detecci\u00f3n",
     "Diagn\u00f3stico del entorno de carga",
     "`AUTHENTICATION_BACKENDS` inclu\u00eda `LDAPBackend` primero y "
     "`AUTH_LDAP_SERVER_URI` apuntaba al AD real"),
    ("04/11, correcci\u00f3n",
     "Blindaje en 5 capas + preflight + vigilante",
     "El entorno de carga deja de poder autenticar contra AD"),
    ("04/11, verificaci\u00f3n",
     "5 corridas con `check_no_ldap.sh`",
     "0 conexiones TCP hacia 15.15.1.60:389 en todas ellas"),
]

BLINDAJE = [
    ("1. Backends",
     "`AUTHENTICATION_BACKENDS = ['ModelBackend']` en `loadtest_settings.py`",
     "Imposibilita cualquier intento LDAP desde el harness"),
    ("2. URI muerta",
     "`AUTH_LDAP_SERVER_URI = 'ldap://127.0.0.1:9'`",
     "Red de descarte: si algo conectara, ser\u00eda a un puerto local cerrado"),
    ("3. Guard de arranque",
     "`assert` de `LDAPBackend` + `LDAP_DESHABILITADO = True`",
     "El arranque aborta si alguien reintroduce el backend"),
    ("4. Preflight en cada corrida",
     "Bloque Python en `run_loadtest.sh`",
     "Comprueba backends y URI antes de lanzar Locust"),
    ("5. Vigilante en red",
     "`check_no_ldap.sh [segundos]` (ss contra 15.15.1.60)",
     "0 conexiones = OK; autocorrecto (detecta una conexi\u00f3n y sale con rc=1)"),
    ("6. Cuentas dedicadas",
     "`loadtest_admin|jefe|coord|trab` (no existen en AD)",
     "Ninguna cuenta real vuelve a usarse en carga"),
]

PRUEBAS_POST_BLINDAJE = [
    ("smoke_loadtest", "3", "0", "Smoke de arranque con cuentas dedicadas"),
    ("opt3_27_w3t4", "27", "0", "27 usuarios (host cargado, p95 1700 ms)"),
    ("opt3b_27_w3t4", "27", "0", "27 usuarios (ventana tranquila, p95 990 ms)"),
    ("opt3c_27_w3t4", "27", "0", "27 usuarios (host cargado, p95 1900 ms)"),
    ("opt3_54_w3t4", "54", "0", "54 usuarios (p95 5300 ms)"),
]

COMANDOS = [
    ("Levantar el stack", """\
cd /tmp/polidoc_loadtest
WORKERS=3 THREADS=4 bash start_stack.sh"""),
    ("Correr la matriz", """\
cd /tmp/polidoc_loadtest
bash run_loadtest.sh opt3b_27_w3t4 27 150 1   # 27 usuarios
bash run_loadtest.sh opt3_54_w3t4  54 150 1   # 54 usuarios"""),
    ("Vigilar AD durante la corrida", """\
bash check_no_ldap.sh 120    # reporta 0 conexiones a 15.15.1.60:389"""),
    ("Perfilado de endpoints (queries)", """\
cd /tmp/polidoc_loadtest
DJANGO_SETTINGS_MODULE=loadtest_settings PYTHONPATH=/app:/tmp/polidoc_loadtest \\
    python3 profile_endpoints.py"""),
    ("CPU por petici\u00f3n", """\
cd /tmp/polidoc_loadtest
DJANGO_SETTINGS_MODULE=loadtest_settings PYTHONPATH=/app:/tmp/polidoc_loadtest \\
    python3 cpu_bench.py"""),
    ("Suite funcional", """\
DJANGO_SETTINGS_MODULE=polidoc.test_settings python3 -m django test apps --verbosity=1"""),
    ("Detener el stack", """\
cd /tmp/polidoc_loadtest && bash stop_stack.sh"""),
]

EVIDENCIAS = [
    ("`*_stats.csv`, `*_stats_history.csv`",
     "RPS, percentiles, peticiones y fallos (agregado y por endpoint)"),
    ("`*-resources.csv`",
     "CPU del host, memoria disponible y usuarios, cada 5 s"),
    ("`*-failures.csv`",
     "Detalle de cada fallo (tipo de error y ocurrencias)"),
    ("`*-run.log`",
     "Log completo de Locust de la corrida"),
    ("`*-ldap-check.txt`",
     "Salida del vigilante `check_no_ldap.sh` durante la corrida"),
    ("`*-pre.txt` / `*-post.txt`",
     "Inventario de datos antes/despu\u00e9s de cada corrida"),
    ("`proc_*.txt`",
     "CPU por proceso (gunicorn, nginx, postgres, mongo, redis)"),
    ("`tests_*.log`",
     "Salida completa de la suite funcional"),
    ("`profile_final.txt`, `cpu_bench_final.txt`",
     "Consultas y CPU por petici\u00f3n del estado final"),
    ("`INFORME_CARGA.md` (en /tmp)",
     "Borrador previo a este informe"),
]

ARCHIVOS_MODIFICADOS = [
    ("apps/documentos/views.py", "Modificado",
     "Conteos agrupados, `detalle_documento` con menos consultas, select_related"),
    ("apps/documentos/models.py", "Modificado",
     "`Formato.get_secciones()` memorizado con prefetch"),
    ("apps/documentos/jefatura.py", "Modificado",
     "Memorias por petici\u00f3n de delegaciones y jefaturas"),
    ("apps/documentos/permissions.py", "Modificado",
     "`puede_descargar(..., descarga_autorizada_previa=...)`"),
    ("apps/documentos/api.py", "Modificado",
     "`prefetch_related` en `buscar_sugerencias`"),
    ("polidoc/settings.py", "Modificado",
     "Registro de `CachePeticionMiddleware`"),
    ("apps/documentos/middleware.py", "Nuevo",
     "`CachePeticionMiddleware`"),
    ("AGENTS.md", "Nuevo",
     "Reglas de seguridad del entorno de carga"),
    ("docs/informes/", "Nuevo",
     "Este informe, su generador y la evidencia congelada"),
]

GLOSARIO = [
    ("p95", "Percentil 95: el 95 % de las peticiones respondi\u00f3 en ese tiempo o menos."),
    ("RPS", "Peticiones atendidas por segundo."),
    ("3 \u00d7 4", "3 workers de gunicorn con 4 hilos cada uno (12 hilos en total)."),
    ("bind", "Operaci\u00f3n LDAP con la que un cliente se autentica ante el directorio."),
    ("N+1", "Patr\u00f3n en el que una consulta general se repite una vez m\u00e1s por cada fila."),
    ("warmup", "Corrida corta previa que arranca procesos y calienta la cach\u00e9."),
    ("break-glass", "Autenticaci\u00f3n local sin LDAP, destinada a emergencias y al entorno de carga."),
    ("hold", "Fase de carga sostenida tras la rampa de usuarios."),
]


# --------------------------------------------------------------------------
# Utilidades de datos
# --------------------------------------------------------------------------
def _float(valor, default=None):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return default


def fmt(valor, dec=1):
    """Formatea un n\u00famero con coma decimal (convenci\u00f3n es/ES)."""
    v = _float(valor)
    if v is None:
        return "\u2013"
    texto = f"{v:,.{dec}f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return texto


def leer_stats(nombre):
    """Devuelve la fila agregada de `<nombre>_stats.csv`."""
    ruta = STATS / f"{nombre}_stats.csv"
    with ruta.open() as fh:
        for fila in csv.DictReader(fh):
            if fila.get("Name") == "Aggregated":
                return fila
    raise FileNotFoundError(f"sin fila agregada en {ruta}")


def leer_endpoints(nombre):
    """Devuelve las filas por endpoint de `<nombre>_stats.csv`."""
    filas = []
    with (STATS / f"{nombre}_stats.csv").open() as fh:
        for fila in csv.DictReader(fh):
            if fila.get("Type") in ("GET", "POST"):
                filas.append(fila)
    return filas


def leer_historial(nombre):
    """Devuelve las filas agregadas del hist\u00f3rico de 1 s de la corrida."""
    filas = []
    with (STATS / f"{nombre}_stats_history.csv").open() as fh:
        for fila in csv.DictReader(fh):
            if fila.get("Name") == "Aggregated":
                filas.append(fila)
    return filas


def leer_recursos(nombre):
    with (STATS / f"{nombre}-resources.csv").open() as fh:
        return list(csv.DictReader(fh))


def leer_inventario():
    with (DATOS / "inventario_corridas.csv").open() as fh:
        return list(csv.DictReader(fh))


def serie_tiempo(nombre, columna):
    """Serie (t_segundos, valor) para la columna indicada de la corrida."""
    filas = leer_historial(nombre)
    if not filas:
        return [], []
    t0 = int(filas[0]["Timestamp"])
    xs, ys = [], []
    for fila in filas:
        valor = _float(fila.get(columna))
        if valor is None:
            continue
        xs.append(int(fila["Timestamp"]) - t0)
        ys.append(valor)
    return xs, ys


# --------------------------------------------------------------------------
# Gr\u00e1ficas
# --------------------------------------------------------------------------
def generar_graficas():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    GRAFICAS.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({
        "font.size": 9,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "figure.dpi": 150,
        "axes.edgecolor": "#555555",
        "axes.titleweight": "bold",
    })

    def guardar(fig, nombre):
        ruta = GRAFICAS / nombre
        fig.tight_layout()
        fig.savefig(ruta, bbox_inches="tight")
        plt.close(fig)
        print(f"  gr\u00e1fica -> {ruta.relative_to(BASE)}")

    # Figura 1: p95 acumulado a 27 usuarios, antes vs despu\u00e9s ------------
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    for nombre, etiqueta, color in (
        ("base27_w3t4", "L\u00ednea base (Fase 0)", ROJO),
        ("opt27_w3t4w", "Tras la optimizaci\u00f3n", VERDE),
    ):
        xs, ys = serie_tiempo(nombre, "95%")
        ax.plot([x / 60 for x in xs], ys, label=etiqueta, color=color, linewidth=1.8)
    ax.axhline(1000, color=NARANJA, linestyle="--", linewidth=1.4,
               label="SLA: p95 = 1000 ms")
    ax.set_xlabel("Tiempo de corrida (minutos)")
    ax.set_ylabel("p95 acumulado (ms)")
    ax.set_title("p95 acumulado a 27 usuarios: antes vs. despu\u00e9s")
    ax.legend(loc="upper right", frameon=True)
    guardar(fig, "fig1_p95_27usuarios.png")

    # Figura 2: RPS y usuarios, 27 vs 54 ----------------------------------
    fig, ejes = plt.subplots(1, 2, figsize=(7.8, 3.3), sharey=True)
    for ax, nombre, titulo in (
        (ejes[0], "opt3b_27_w3t4", "27 usuarios (p95 = 990 ms)"),
        (ejes[1], "opt3_54_w3t4", "54 usuarios (p95 = 5300 ms)"),
    ):
        xs, rps = serie_tiempo(nombre, "Requests/s")
        xu, usuarios = serie_tiempo(nombre, "User Count")
        ax.plot([x / 60 for x in xs], rps, color=AZUL_CLARO, linewidth=1.6,
                label="RPS")
        ax.plot([x / 60 for x in xu], usuarios, color=GRIS, linewidth=1.0,
                linestyle=":", label="Usuarios")
        ax.set_title(titulo)
        ax.set_xlabel("Tiempo (minutos)")
        ax.legend(loc="upper left", fontsize=8)
    ejes[0].set_ylabel("Peticiones/s (ventana)")
    fig.suptitle("Ritmo de servicio durante la carga sostenida", fontweight="bold")
    guardar(fig, "fig2_rps_27_vs_54.png")

    # Figura 3: p95 por endpoint antes vs despu\u00e9s ----------------------
    base = {f["Name"]: f for f in leer_endpoints("base27_w3t4")}
    despues = {f["Name"]: f for f in leer_endpoints("opt27_w3t4w")}
    comunes = sorted(
        (n for n in base if n in despues),
        key=lambda n: -(_float(base[n]["95%"]) or 0),
    )
    fig, ax = plt.subplots(figsize=(7.6, 3.8))
    x = range(len(comunes))
    ancho = 0.38
    ax.bar([i - ancho / 2 for i in x],
           [_float(base[n]["95%"], 0) for n in comunes],
           ancho, label="L\u00ednea base", color=ROJO, alpha=0.85)
    ax.bar([i + ancho / 2 for i in x],
           [_float(despues[n]["95%"], 0) for n in comunes],
           ancho, label="Tras la optimizaci\u00f3n", color=VERDE, alpha=0.85)
    ax.axhline(1000, color=NARANJA, linestyle="--", linewidth=1.4,
               label="SLA: p95 = 1000 ms")
    ax.set_xticks(list(x))
    ax.set_xticklabels(comunes, rotation=35, ha="right", fontsize=8)
    ax.set_ylabel("p95 (ms)")
    ax.set_title("p95 por endpoint a 27 usuarios")
    ax.legend(loc="upper right", fontsize=8)
    guardar(fig, "fig3_p95_por_endpoint.png")

    # Figura 4: consultas SQL antes vs despu\u00e9s -------------------------
    nombres = list(CONSULTAS_JEFE)
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    x = range(len(nombres))
    ancho = 0.38
    ax.bar([i - ancho / 2 for i in x],
           [CONSULTAS_JEFE[n][0] for n in nombres], ancho,
           label="L\u00ednea base", color=ROJO, alpha=0.85)
    ax.bar([i + ancho / 2 for i in x],
           [CONSULTAS_JEFE[n][1] for n in nombres], ancho,
           label="Estado final", color=VERDE, alpha=0.85)
    ax.set_xticks(list(x))
    ax.set_xticklabels(nombres, rotation=35, ha="right", fontsize=8)
    ax.set_ylabel("Consultas SQL por petici\u00f3n")
    ax.set_title("Consultas por endpoint (rol jefe): antes vs. despu\u00e9s")
    ax.legend(loc="upper right", fontsize=8)
    guardar(fig, "fig4_consultas_por_endpoint.png")

    # Figura 5: recursos del host durante la corrida de 54 ----------------
    rec = leer_recursos("opt3_54_w3t4")
    muestras = list(range(len(rec)))
    cpu = [_float(r.get("cpu_pct"), 0) for r in rec]
    mem = [_float(r.get("mem_available_kb"), 0) / 1024 for r in rec]
    fig, ax = plt.subplots(figsize=(7.6, 3.3))
    ax.plot(muestras, cpu, color=ROJO, linewidth=1.6, label="CPU host (%)")
    ax.axhline(90, color=NARANJA, linestyle="--", linewidth=1.2,
               label="Objetivo: < 90 %")
    ax.set_xlabel("Muestras (cada 5 s)")
    ax.set_ylabel("CPU (%)", color=ROJO)
    ax.set_ylim(0, 100)
    ax2 = ax.twinx()
    ax2.plot(muestras, mem, color=AZUL_CLARO, linewidth=1.4, label="Memoria libre (MB)")
    ax2.set_ylabel("Memoria disponible (MB)", color=AZUL_CLARO)
    ax2.grid(False)
    lineas = ax.get_lines() + ax2.get_lines()
    ax.legend(lineas, [l.get_label() for l in lineas], loc="lower left", fontsize=8)
    ax.set_title("Recursos del host durante la corrida de 54 usuarios")
    guardar(fig, "fig5_recursos_54usuarios.png")


# --------------------------------------------------------------------------
# Modelo de documento (bloques) y renderizadores
# --------------------------------------------------------------------------
def T(columnas, filas, caption):
    return ("tabla", caption, columnas, filas)


def IMG(nombre, caption, ancho=15.2):
    return ("img", nombre, caption, ancho)


def _negritas(texto):
    """Devuelve [(texto, es_negrita, es_codigo)] para **negrita** y `código`."""
    salida = []
    for i, trozo in enumerate(texto.split("**")):
        if not trozo:
            continue
        es_negrita = i % 2 == 1
        for j, pedazo in enumerate(trozo.split("`")):
            if pedazo:
                salida.append((pedazo, es_negrita, j % 2 == 1))
    return salida


# --- Markdown -------------------------------------------------------------
def render_md(bloques, ruta):
    out = []
    out.append(f"# {TITULO} \u2014 PoliDoc\n")
    out.append(f"**{SUBTITULO}**\n")
    out.append(f"- **Autor:** {AUTOR}")
    out.append(f"- **Versi\u00f3n:** {VERSION}")
    out.append(f"- **Fecha:** {FECHA.strftime('%d/%m/%Y')}")
    out.append("- **Clasificaci\u00f3n:** documento t\u00e9cnico interno\n")

    idx_tabla, idx_fig = 0, 0
    for bloque in bloques:
        tipo = bloque[0]
        if tipo == "cover":
            out.append("\n---\n")
        elif tipo == "h1":
            out.append(f"\n## {bloque[1]}\n")
        elif tipo == "h2":
            out.append(f"\n### {bloque[1]}\n")
        elif tipo == "h3":
            out.append(f"\n#### {bloque[1]}\n")
        elif tipo == "p":
            out.append(f"{bloque[1]}\n")
        elif tipo == "ul":
            for item in bloque[1]:
                out.append(f"- {item}")
            out.append("")
        elif tipo == "ol":
            for i, item in enumerate(bloque[1], 1):
                out.append(f"{i}. {item}")
            out.append("")
        elif tipo == "tabla":
            _, caption, columnas, filas = bloque
            idx_tabla += 1
            out.append(f"**Tabla {idx_tabla}. {caption}**\n")
            out.append("| " + " | ".join(columnas) + " |")
            out.append("|" + "|".join([" --- "] * len(columnas)) + "|")
            for fila in filas:
                out.append("| " + " | ".join(str(c) for c in fila) + " |")
            out.append("")
        elif tipo == "img":
            _, nombre, caption, _ = bloque
            idx_fig += 1
            out.append(f"**Figura {idx_fig}. {caption}**\n")
            out.append(f"![{caption}](graficas/{nombre})\n")
        elif tipo == "nota":
            out.append(f"> {bloque[1]}\n")
        elif tipo == "pre":
            out.append(f"```\n{bloque[1]}\n```\n")
        elif tipo == "pb":
            out.append("\n---\n")
        elif tipo == "orientacion":
            out.append("\n---\n")
        else:
            raise ValueError(f"bloque desconocido: {tipo}")
    ruta.write_text("\n".join(out), encoding="utf-8")
    print(f"  markdown -> {ruta.relative_to(BASE.parent.parent)}")


# --- DOCX -----------------------------------------------------------------
def render_docx(bloques, ruta):
    from docx import Document
    from docx.enum.section import WD_ORIENT, WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    c_azul = RGBColor(0x1F, 0x3A, 0x5F)
    c_azul_claro = RGBColor(0x2E, 0x5C, 0x8A)
    c_gris = RGBColor(0x55, 0x55, 0x55)

    doc = Document()
    seccion0 = doc.sections[0]
    seccion0.page_width, seccion0.page_height = Cm(21), Cm(29.7)  # A4
    seccion0.left_margin = seccion0.right_margin = Cm(2)
    seccion0.top_margin = seccion0.bottom_margin = Cm(2)
    estilo = doc.styles["Normal"]
    estilo.font.name = "Calibri"
    estilo.font.size = Pt(10.5)
    estilo.paragraph_format.space_after = Pt(6)
    estilo.paragraph_format.line_spacing = 1.12

    def sombreado(parrafo, hex_color):
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), hex_color)
        parrafo._p.get_or_add_pPr().append(shd)

    def marcar(parrafo, texto, size=None, color=None, italic=False, bold=False):
        for trozo, es_negrita, es_codigo in _negritas(texto):
            run = parrafo.add_run(trozo)
            run.bold = es_negrita or bold
            run.italic = italic
            if es_codigo:
                run.font.name = "Consolas"
                run.font.size = Pt((size or 10.5) - 1)
            if size and not es_codigo:
                run.font.size = Pt(size)
            if color is not None:
                run.font.color.rgb = color
        return parrafo

    def pie_pagina(seccion):
        if seccion.footer.is_linked_to_previous:
            seccion.footer.is_linked_to_previous = False
        parrafo = seccion.footer.paragraphs[0]
        parrafo.text = ""
        parrafo.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = parrafo.add_run(f"{TITULO} \u2014 PoliDoc v{VERSION}  \u00b7  p\u00e1gina ")
        run.font.size = Pt(8)
        run.font.color.rgb = c_gris
        campo = OxmlElement("w:fldSimple")
        campo.set(qn("w:instr"), "PAGE")
        parrafo._p.append(campo)

    def portada():
        for _ in range(4):
            doc.add_paragraph()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(TITULO)
        run.bold = True
        run.font.size = Pt(30)
        run.font.color.rgb = c_azul
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(SUBTITULO)
        run.font.size = Pt(13)
        run.font.color.rgb = c_azul_claro
        for _ in range(4):
            doc.add_paragraph()
        for etiqueta, valor in (
            ("Autor", AUTOR),
            ("Versi\u00f3n", VERSION),
            ("Fecha", FECHA.strftime("%d/%m/%Y")),
            ("Clasificaci\u00f3n", "Documento t\u00e9cnico interno"),
        ):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(f"{etiqueta}: ")
            run.bold = True
            run.font.size = Pt(11)
            run = p.add_run(valor)
            run.font.size = Pt(11)
            run.font.color.rgb = c_gris
        doc.add_page_break()

    def tabla_bloque(caption, columnas, filas):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(3)
        marcar(p, caption, size=9, color=c_gris, italic=True, bold=True)
        p.paragraph_format.keep_with_next = True

        tabla = doc.add_table(rows=1, cols=len(columnas))
        tabla.style = "Table Grid"
        tabla.alignment = WD_TABLE_ALIGNMENT.CENTER
        encabezado = tabla.rows[0].cells
        for i, col in enumerate(columnas):
            encabezado[i].text = ""
            parrafo = encabezado[i].paragraphs[0]
            parrafo.paragraph_format.space_after = Pt(2)
            run = parrafo.add_run(str(col))
            run.bold = True
            run.font.size = Pt(8.5)
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:fill"), "2E5C8A")
            encabezado[i]._tc.get_or_add_tcPr().append(shd)
        for fila in filas:
            celdas = tabla.add_row().cells
            for i, valor in enumerate(fila):
                celdas[i].text = ""
                parrafo = celdas[i].paragraphs[0]
                parrafo.paragraph_format.space_after = Pt(1)
                marcar(parrafo, str(valor), size=8.5)
        p_final = doc.add_paragraph()
        p_final.paragraph_format.space_after = Pt(4)

    def figura(nombre, caption, ancho_cm):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(6)
        p.add_run().add_picture(str(GRAFICAS / nombre), width=Cm(ancho_cm))
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(8)
        marcar(p, caption, size=9, color=c_gris, italic=True)

    def orientacion(landscape):
        seccion = doc.add_section(WD_SECTION.NEW_PAGE)
        seccion.top_margin = seccion.bottom_margin = Cm(2)
        if landscape:
            seccion.orientation = WD_ORIENT.LANDSCAPE
            seccion.page_width, seccion.page_height = Cm(29.7), Cm(21)
            seccion.left_margin = seccion.right_margin = Cm(1.5)
        else:
            seccion.orientation = WD_ORIENT.PORTRAIT
            seccion.page_width, seccion.page_height = Cm(21), Cm(29.7)
            seccion.left_margin = seccion.right_margin = Cm(2)
        pie_pagina(seccion)

    pie_pagina(doc.sections[0])
    idx_tabla, idx_fig = 0, 0
    for bloque in bloques:
        tipo = bloque[0]
        if tipo == "cover":
            portada()
        elif tipo in ("h1", "h2", "h3"):
            tama, color, nivel = {
                "h1": (17, c_azul, 1),
                "h2": (13, c_azul_claro, 2),
                "h3": (11.5, c_azul_claro, 3),
            }[tipo]
            p = doc.add_heading("", level=nivel)
            p.paragraph_format.space_before = Pt(14 if nivel == 1 else 10)
            p.paragraph_format.space_after = Pt(5)
            p.paragraph_format.keep_with_next = True
            run = p.add_run(bloque[1])
            run.font.size = Pt(tama)
            run.font.color.rgb = color
            run.bold = True
            run.font.name = "Calibri"
        elif tipo == "p":
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            marcar(p, bloque[1])
        elif tipo == "ul":
            for item in bloque[1]:
                p = doc.add_paragraph(style="List Bullet")
                marcar(p, item)
        elif tipo == "ol":
            for item in bloque[1]:
                p = doc.add_paragraph(style="List Number")
                marcar(p, item)
        elif tipo == "tabla":
            idx_tabla += 1
            _, caption, columnas, filas = bloque
            tabla_bloque(f"Tabla {idx_tabla}. {caption}", columnas, filas)
        elif tipo == "img":
            idx_fig += 1
            _, nombre, caption, ancho = bloque
            figura(nombre, f"Figura {idx_fig}. {caption}", ancho)
        elif tipo == "nota":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.6)
            p.paragraph_format.space_before = Pt(4)
            marcar(p, bloque[1], size=9.5, color=c_gris, italic=True)
        elif tipo == "pre":
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(8)
            sombreado(p, "F2F2F2")
            run = p.add_run(bloque[1])
            run.font.name = "Consolas"
            run.font.size = Pt(8.5)
        elif tipo == "pb":
            doc.add_page_break()
        elif tipo == "orientacion":
            orientacion(bloque[1] == "landscape")
        else:
            raise ValueError(f"bloque desconocido: {tipo}")

    doc.core_properties.title = f"{TITULO} \u2014 PoliDoc"
    doc.core_properties.author = AUTOR
    doc.core_properties.subject = SUBTITULO
    doc.core_properties.comments = f"Versi\u00f3n {VERSION} \u2014 {FECHA:%d/%m/%Y}"
    doc.save(ruta)
    print(f"  docx -> {ruta.relative_to(BASE.parent.parent)}")


# --------------------------------------------------------------------------
# Contenido
# --------------------------------------------------------------------------
def construir():
    inv = leer_inventario()
    por_nombre = {f["corrida"]: f for f in inv}
    b = []
    b.append(("cover",))

    # ---- 1. Resumen ejecutivo ------------------------------------------
    b.append(("h1", "1. Resumen ejecutivo"))
    b.append(("h2", "1.1 Qu\u00e9 se prob\u00f3"))
    b.append(("p",
        "Se someti\u00f3 a PoliDoc a una prueba de carga sostenida con la matriz "
        "completa del flujo de trabajo (cat\u00e1logo, ficha, mis documentos, "
        "departamento, b\u00fasqueda, previsualizaci\u00f3n, descarga y plantillas) "
        "sobre un stack local id\u00e9ntico al de despliegue: nginx frente a "
        "gunicorn con 3 workers y 4 hilos, PostgreSQL, Redis, RabbitMQ y "
        "MongoDB. Se midieron dos escenarios de usuario (27 y 54) con cuatro "
        "roles en la proporci\u00f3n 3:7:7:10, con carga sostenida de 150 segundos "
        "tras una rampa lineal de 1 usuario por segundo."))
    b.append(("h2", "1.2 Resultado frente al SLA"))
    b.append(T(
        ["Escenario", "Objetivo", "Resultado", "Estado"],
        [
            ["27 usuarios (3\u00d74)", "p95 < 1000 ms", "630\u2013990 ms", "\u2705 Cumple"],
            ["54 usuarios (3\u00d74)", "p95 < 1000 ms", "4900\u20135600 ms", "\u274c No cumple"],
            ["Errores 5xx", "0", "0 en las 33 corridas de la sesi\u00f3n", "\u2705 Cumple"],
            ["CPU del host", "< 90 %", "76\u201395 % en los picos "
             "(54 usuarios y host cargado)", "\u26a0 L\u00edmite"],
            ["Memoria disponible", "> 300 MB", "> 800 MB en toda la matriz",
             "\u2705 Cumple"],
        ],
        "Resultado por escenario frente a los criterios de aceptaci\u00f3n"))
    b.append(("h2", "1.3 Hallazgos principales"))
    b.append(("ul", [
        "**Consultas N+1 en el flujo de aprobaci\u00f3n.** `detalle_documento` "
        "ejecutaba 44 consultas SQL por petici\u00f3n (rol jefe) y `mis_documentos` "
        "28; hoy son 24 y 7. La b\u00fasqueda pas\u00f3 de 19 a 7.",
        "**El cuello de botella es la CPU de la aplicaci\u00f3n.** gunicorn consume "
        "del orden de 115\u2013138 ms de CPU por petici\u00f3n y las 5 rutas principales "
        "bajaron de 459,5 a 335,9 ms de CPU por petici\u00f3n (\u221227 %). Con 3 workers "
        "el techo pr\u00e1ctico es de 13\u201315 RPS, insuficiente para 54 usuarios.",
        "**Incidente de Active Directory durante las pruebas.** Las primeras "
        "corridas usaban las cuentas reales de personas y cada login generaba "
        "un intento de v\u00ednculo (bind) fallido contra el controlador de dominio; "
        "la pol\u00edtica de bloqueo dej\u00f3 cuentas bloqueadas. El entorno qued\u00f3 "
        "blindado y verificado: 5 corridas posteriores con 0 conexiones al AD.",
        "**La suite funcional no se ha roto.** 1210 pruebas con los mismos 4 "
        "fallos preexistentes que antes de tocar c\u00f3digo (verificados con los "
        "cambios en stash), y `check` / `makemigrations --check` limpios.",
    ]))
    b.append(("nota",
        "Recomendaci\u00f3n principal: 27 usuarios queda cubierto por el SLA. Para "
        "54 usuarios es necesario m\u00e1s capacidad (CPUs / workers) o reducir "
        "a\u00fan m\u00e1s el coste de CPU por petici\u00f3n; no hay margen de configuraci\u00f3n "
        "que lo resuelva solo."))

    # ---- 2. Objetivos y alcance ----------------------------------------
    b.append(("h1", "2. Objetivos y alcance"))
    b.append(("p",
        "El objetivo fue verificar el comportamiento del flujo de documentos "
        "bajo carga realista, identificar cuellos de botella, optimizarlos y "
        "demostrar los resultados con evidencia reproducible."))
    b.append(T(
        ["Criterio", "Meta"],
        [
            ["Latencia p95", "< 1000 ms con 27 y 54 usuarios"],
            ["Errores de servidor (5xx)", "0"],
            ["CPU del host", "< 90 %"],
            ["Memoria disponible", "> 300 MB"],
            ["Rendimiento de la suite funcional", "Sin regresiones nuevas"],
        ],
        "Criterios de aceptaci\u00f3n"))
    b.append(("h2", "2.1 Dentro del alcance"))
    b.append(("ul", [
        "Endpoints del flujo de carga: cat\u00e1logo, detalle, mis documentos, "
        "departamento, b\u00fasqueda, ver archivo, descargar, estado de "
        "conversi\u00f3n, preview de plantilla y alta de plantilla.",
        "Matriz 27 y 54 usuarios sobre 3\u00d74 (y corridas auxiliares de "
        "diagn\u00f3stico con 1, 3, 12 y 37 usuarios).",
        "Medici\u00f3n de consultas SQL y de CPU por petici\u00f3n, antes y despu\u00e9s.",
        "Verificaci\u00f3n de que el entorno de carga no toca Active Directory.",
    ]))
    b.append(("h2", "2.2 Fuera del alcance"))
    b.append(("ul", [
        "Rendimiento en producci\u00f3n (el entorno probado es local).",
        "Pruebas de seguridad y de accesibilidad (solo se aborda el incidente "
        "LDAP por ser efecto directo de estas pruebas).",
        "Pruebas de estr\u00e9s destructivo m\u00e1s all\u00e1 de 54 usuarios.",
    ]))

    # ---- 3. Metodolog\u00eda -------------------------------------------------
    b.append(("h1", "3. Metodolog\u00eda"))
    b.append(("h2", "3.1 Entorno de pruebas"))
    b.append(("p",
        "El entorno se reconstruy\u00f3 desde cero para que las mediciones fueran "
        "comparables entre s\u00ed mismas: PostgreSQL 17 sobre socket Unix (tras "
        "ajustar `pg_hba.conf`), Redis, RabbitMQ con vhost `loadtest`, "
        "MongoDB, est\u00e1ticos recogidos con `collectstatic` y compresi\u00f3n "
        "previa. Los datos se cargaron desde un volcado de 399 objetos "
        "(14 documentos, 11 plantillas, 7 usuarios) reiniciando las "
        "secuencias."))
    b.append(T(["Componente", "Detalle"], [[a, c] for a, c in SERVICIOS],
               "Componentes del entorno de pruebas"))
    b.append(("h2", "3.2 Datos de pruebas"))
    b.append(("p",
        "14 documentos, 11 plantillas y 16 versiones sobre los departamentos "
        "del turno. Cada rol us\u00f3 una cuenta: primero las cuentas reales "
        "(origen del incidente descrito en la secci\u00f3n 8) y, desde el "
        "blindaje, las cuentas dedicadas `loadtest_admin`, `loadtest_coord`, "
        "`loadtest_jefe` y `loadtest_trab`, que no existen en Active Directory."))
    b.append(("p",
        "Nota de comparabilidad: `loadtest_jefe` no tiene documentos propios "
        "(el usuario real tiene 6), as\u00ed que `mis_documentos` renderiza una "
        "p\u00e1gina m\u00e1s liviana; el coste por petici\u00f3n se verific\u00f3 equivalente "
        "(347 vs 357 ms) para poder comparar corridas."))
    b.append(("h2", "3.3 Modelo de carga"))
    b.append(T(["Clase", "Cuenta", "Peso", "Peso relativo", "Perfil"],
               [[a, c, d, e, f] for a, c, d, e, f in ROLES],
               "Distribuci\u00f3n de usuarios por rol (3:7:7:10)"))
    b.append(T(["Ruta", "Nombre en Locust", "Peso", "Descripci\u00f3n"],
               [[a, c, d, e] for a, c, d, e in TAREAS],
               "Tareas del usuario virtual y sus pesos"))
    b.append(("p",
        "Cada usuario espera `constant_pacing(3)` segundos entre tareas. La "
        "forma de carga (`RampShape`) sube a 1 usuario/s hasta el objetivo y "
        "mantiene la carga `LT_HOLD` segundos (150 en la matriz). El login se "
        "ejecuta una sola vez al iniciar cada usuario (`on_start`) con "
        "`allow_redirects=False`, de modo que el 302 del login se cuenta como "
        "fallo si no llega."))
    b.append(("p",
        "Antes de cada matriz se ejecuta un warmup de 12 usuarios durante 30 "
        "s; sin \u00e9l, los primeros logins pagan el arranque fr\u00edo de gunicorn y "
        "distorsionan el p95."))
    b.append(("h2", "3.4 M\u00e9tricas y herramientas"))
    b.append(("ul", [
        "**Latencia y RPS:** Locust 2.47.0, percentiles calculados sobre la "
        "ventana completa de la corrida (`*_stats.csv`).",
        "**CPU y memoria:** `sample_resources.py` muestrea el host cada 5 s "
        "(`*-resources.csv`); `sample_processes.py` y `top` capturan la CPU por "
        "proceso (`proc_*.txt`).",
        "**CPU por petici\u00f3n:** `cpu_bench.py` con `getrusage`, mejor de 5 "
        "series de 8 peticiones (evita el ruido de medici\u00f3n m\u00ednima).",
        "**Consultas SQL:** `profile_endpoints.py` con `CaptureQueriesContext` "
        "y `force_login` (sin coste de PBKDF2), 3 repeticiones por rol.",
        "**Verificaci\u00f3n LDAP:** `check_no_ldap.sh`, que vigila con `ss` "
        "durante toda la corrida cualquier conexi\u00f3n hacia el controlador de "
        "dominio.",
    ]))
    b.append(("h2", "3.5 Limitaciones de la medici\u00f3n"))
    b.append(("p",
        "El host es una m\u00e1quina compartida de 4 vCPU con carga externa: el "
        "`loadavg` de 1 minuto se movi\u00f3 entre 1,4 y 10,7 durante las corridas. "
        "Por eso se descartaron las ventanas ruidosas (p. ej. `opt3_27_w3t4` "
        "con loadavg 6,02) y el resultado principal usa las corridas en "
        "ventana tranquila (`opt27_w3t4w`, `opt3b_27_w3t4`). En estas "
        "condiciones, las m\u00e9tricas estables son la CPU por proceso y la CPU "
        "por petici\u00f3n; el tiempo de pared tiene un margen de \u00b120\u201330 %."))

    # ---- 4. L\u00ednea base --------------------------------------------------
    b.append(("pb",))
    b.append(("h1", "4. L\u00ednea base (Fase 0)"))
    base = leer_stats("base27_w3t4")
    b.append(("p",
        "La corrida `base27_w3t4` (27 usuarios, 150 s de sostenido) define el "
        "punto de partida con el c\u00f3digo sin optimizar:"))
    b.append(T(["M\u00e9trica", "Valor"],
        [["Peticiones", str(base["Request Count"])],
         ["Fallos", str(base["Failure Count"])],
         ["RPS", fmt(base["Requests/s"], 2)],
         ["Tiempo medio", fmt(base["Average Response Time"]) + " ms"],
         ["p95", str(base["95%"]) + " ms"],
         ["p99", str(base["99%"]) + " ms"],
         ["M\u00e1ximo", str(base["100%"]) + " ms"],
         ["CPU m\u00e1x. del host",
          fmt(por_nombre["base27_w3t4"]["cpu_host_max_pct"]) + " %"]],
        "Corrida de l\u00ednea base a 27 usuarios"))
    b.append(("p",
        "El p95 de 1800 ms duplicaba el objetivo. El detalle por endpoint "
        "se\u00f1alaba d\u00f3nde estaba el coste:"))
    filas_base = []
    for f in sorted(leer_endpoints("base27_w3t4"),
                    key=lambda r: -(_float(r["95%"]) or 0)):
        filas_base.append([f["Name"], f["Request Count"],
                           fmt(f["Average Response Time"]), f["95%"], f["100%"]])
    b.append(T(["Endpoint", "Peticiones", "Media (ms)", "p95 (ms)", "M\u00e1x (ms)"],
               filas_base, "Detalle por endpoint en la l\u00ednea base (27 usuarios)"))
    b.append(("nota",
        "Comparabilidad: la l\u00ednea base se midi\u00f3 sobre un esquema ya migrado a "
        "PostgreSQL con sus \u00edndices. Las corridas hist\u00f3ricas de la sesi\u00f3n "
        "anterior (`measure*`, `retest*`, `stress`, `capacity`) usaron un "
        "esquema cargado con pgloader sin todos los \u00edndices; se conservan en "
        "el Anexo A como contexto, no como comparaci\u00f3n v\u00e1lida."))

    # ---- 5. Optimizaciones ------------------------------------------------
    b.append(("h1", "5. Optimizaciones aplicadas"))
    b.append(("h2", "5.1 Cambios de c\u00f3digo"))
    b.append(T(["Archivo", "Cambio", "Efecto"],
               [[a, c, d] for a, c, d in OPTIMIZACIONES_CODIGO],
               "Cambios de c\u00f3digo realizados (incluidos en el repositorio)"))
    b.append(("h2", "5.2 Cambios de entorno"))
    b.append(("ul", OPTIMIZACIONES_ENTORNO))
    b.append(("h2", "5.3 Efecto medido: consultas SQL"))
    b.append(("p",
        "Medido con `profile_endpoints.py` sobre el rol jefe (el de mayor "
        "coste), 3 repeticiones:"))
    filas = []
    for ep, (antes, despues) in CONSULTAS_JEFE.items():
        delta = despues - antes
        filas.append([ep, str(antes), str(despues),
                      ("sin cambios" if delta == 0
                       else f"{delta * 100 / antes:+.0f} %")])
    b.append(T(["Endpoint", "Antes", "Despu\u00e9s", "Variaci\u00f3n"],
               filas, "Consultas SQL por petici\u00f3n (rol jefe)"))
    b.append(IMG("fig4_consultas_por_endpoint.png",
                 "Consultas por endpoint antes y despu\u00e9s de la optimizaci\u00f3n"))
    b.append(("h2", "5.4 Efecto medido: CPU por petici\u00f3n"))
    filas = []
    tot_antes = tot_despues = 0.0
    for ep, (antes, despues) in CPU_PETICION.items():
        tot_antes += antes
        tot_despues += despues
        filas.append([ep, fmt(antes), fmt(despues),
                      f"{(despues - antes) * 100 / antes:.0f} %"])
    filas.append(["Total (5 rutas)", fmt(tot_antes), fmt(tot_despues),
                  f"{(tot_despues - tot_antes) * 100 / tot_antes:.0f} %"])
    b.append(T(["Endpoint", "Fase 0 (ms)", "Final (ms)", "Variaci\u00f3n"],
               filas,
               "CPU por petici\u00f3n, rol jefe, mejor de 5 series (cpu_bench.py)"))
    b.append(("nota",
        "Ambas columnas se midieron con el mismo m\u00e9todo y la misma versi\u00f3n "
        "del script, pero en ventanas de carga del host distintas; el "
        "diferencial real est\u00e1 entre el 20 % y el 30 % por ruta. La l\u00ednea base "
        "de esta tabla es la de la Fase 0; las mediciones intermedias del "
        "camino (p. ej. 141 ms en `detalle` tras la Fase 1) aparecen en las "
        "evidencias."))

    # ---- 6. Resultados ----------------------------------------------------
    b.append(("pb",))
    b.append(("h1", "6. Resultados despu\u00e9s de optimizar"))
    b.append(("h2", "6.1 Escenario de 27 usuarios"))
    corridas_27 = ["base27_w3t4", "opt27_w3t4", "opt27_w3t4w", "opt2_27_w3t4",
                   "opt2_27c_w3t4", "opt3b_27_w3t4", "opt3c_27_w3t4"]
    b.append(T(["Corrida", "Peticiones", "Fallos", "RPS", "Media (ms)",
                "p95 (ms)", "M\u00e1x (ms)", "CPU host (%)", "Carga"],
        [[n, por_nombre[n]["peticiones"], por_nombre[n]["fallos"],
          fmt(por_nombre[n]["rps"], 2), fmt(por_nombre[n]["avg_ms"]),
          por_nombre[n]["p95_ms"], por_nombre[n]["max_ms"],
          fmt(por_nombre[n]["cpu_host_max_pct"]),
          por_nombre[n]["loadavg_registrado"] or "\u2013"]
         for n in corridas_27],
        "Matriz de 27 usuarios: evoluci\u00f3n por corrida"))
    b.append(("p",
        "Con la ventana tranquila, el escenario de 27 usuarios pas\u00f3 de 1800 ms "
        "a 630 ms de p95 (`opt27_w3t4w`). La corrida de cierre con blindaje "
        "LDAP y ventana tranquila (`opt3b_27_w3t4`) mide 990 ms, dentro del "
        "SLA. Cuando el host estaba cargado (`opt3_27_w3t4`, `opt3c_27_w3t4`, "
        "loadavg 6,02) el p95 sube a 1700–1900 ms: la diferencia la "
        "explica la carga del host, no la aplicaci\u00f3n."))
    filas = []
    desp = {f["Name"]: f for f in leer_endpoints("opt3b_27_w3t4")}
    for nombre in sorted(desp, key=lambda n: -(_float(desp[n]["95%"]) or 0)):
        f = desp[nombre]
        filas.append([nombre, f["Request Count"], fmt(f["Average Response Time"]),
                      f["95%"], f["100%"]])
    b.append(T(["Endpoint", "Peticiones", "Media (ms)", "p95 (ms)", "M\u00e1x (ms)"],
               filas, "Detalle por endpoint tras la optimizaci\u00f3n (27 usuarios, "
                      "corrida opt3b_27_w3t4)"))
    b.append(IMG("fig3_p95_por_endpoint.png",
                 "Comparaci\u00f3n de p95 por endpoint: l\u00ednea base vs. optimizado"))
    b.append(IMG("fig1_p95_27usuarios.png",
                 "Evoluci\u00f3n del p95 acumulado a 27 usuarios"))
    b.append(("h2", "6.2 Escenario de 54 usuarios"))
    b.append(T(["Corrida", "Peticiones", "Fallos", "RPS", "Media (ms)",
                "p95 (ms)", "M\u00e1x (ms)", "CPU host (%)"],
        [[n, por_nombre[n]["peticiones"], por_nombre[n]["fallos"],
          fmt(por_nombre[n]["rps"], 2), fmt(por_nombre[n]["avg_ms"]),
          por_nombre[n]["p95_ms"], por_nombre[n]["max_ms"],
          fmt(por_nombre[n]["cpu_host_max_pct"])]
         for n in ["opt54_w3t4", "opt2_54_w3t4", "opt2_54b_w3t4",
                   "diag54", "diag54b", "opt3_54_w3t4"]],
        "Matriz de 54 usuarios: ninguna corrida alcanz\u00f3 el SLA"))
    b.append(("p",
        "A 54 usuarios la p95 se mueve entre 4900 y 7800 ms con 0 fallos de "
        "servidor en todas las corridas (`diag54b` tuvo 2 cortes de conexi\u00f3n "
        "de red al servir archivos grandes, no respuestas 5xx). El techo est\u00e1 "
        "en la CPU: las corridas m\u00e1s limpias se acercan a 13\u201314,5 RPS con el "
        "host al 91\u201393 %."))
    b.append(IMG("fig2_rps_27_vs_54.png",
                 "Ritmo de servicio y usuarios en los dos escenarios"))
    b.append(IMG("fig5_recursos_54usuarios.png",
                 "CPU y memoria del host durante la corrida de 54 usuarios"))

    # ---- 7. An\u00e1lisis ----------------------------------------------------
    b.append(("h1", "7. An\u00e1lisis del l\u00edmite de capacidad"))
    b.append(("p",
        "La aplicaci\u00f3n es CPU-bound. Con 3 workers \u00d7 4 hilos hay 12 hilos "
        "competiendo por 4 vCPU, de las que una parte se lleva nginx, "
        "PostgreSQL y el muestreo. El coste de CPU por petici\u00f3n de las 5 rutas "
        "principales baj\u00f3 de 459,5 a 335,9 ms; con eso el techo "
        "te\u00f3rico es de unas 12 RPS, y las corridas m\u00e1s limpias midieron "
        "8,7 RPS a 27 usuarios y 13\u201314,5 RPS a 54 usuarios (con colas "
        "crecientes: la media sube de 431 a 2768 ms)."))
    b.append(T(["Situaci\u00f3n", "27 usuarios", "54 usuarios"],
        [["RPS observado", "8,7", "13,2\u201314,5"],
         ["p95", "630\u2013990 ms", "4900\u20135600 ms"],
         ["CPU host en pico", "57\u201384 %", "91\u201395 %"],
         ["Coste de CPU por petici\u00f3n", "\u2248 336 ms (5 rutas)", "Id\u00e9ntico: el "
          "coste por petici\u00f3n no cambia, solo la cola"]],
        "Comparaci\u00f3n de los dos escenarios"))
    b.append(("p",
        "Conclusi\u00f3n del an\u00e1lisis: a 54 usuarios no se trata de que la "
        "aplicaci\u00f3n falle (0 respuestas 5xx), sino de que la cola de "
        "peticiones crece m\u00e1s r\u00e1pido de lo que 4 vCPU pueden servir. Las "
        "opciones reales, por orden de coste, son:"))
    b.append(("ol", [
        "Reducir a\u00fan m\u00e1s el coste de CPU por petici\u00f3n (cach\u00e9 de "
        "cat\u00e1logo, prefetch de aprobaciones, m\u00e1s \u00edndices medidos con "
        "`EXPLAIN ANALYZE`).",
        "Subir los recursos del host (de 4 a 8 vCPU) manteniendo la misma "
        "configuraci\u00f3n de gunicorn.",
        "Ajustar workers/hilos respecto a vCPU (4\u20136 workers sin threads "
        "excesivos) una vez que haya m\u00e1s CPU disponible.",
        "Repartir carga entre m\u00e1s instancias si el SLA de 54 usuarios es "
        "definitivo.",
    ]))

    # ---- 8. Incidente AD --------------------------------------------------
    b.append(("pb",))
    b.append(("h1", "8. Incidente de Active Directory"))
    b.append(("h2", "8.1 Qu\u00e9 ocurri\u00f3"))
    b.append(("p",
        "Durante las pruebas de carga de las sesiones previas y de la ma\u00f1ana "
        "del 04/11, cada usuario virtual se autenticaba con **una cuenta real "
        "de personas** (una por rol probado: administraci\u00f3n, coordinaci\u00f3n, "
        "jefatura y trabajador) usando la contrase\u00f1a local de PoliDoc. El "
        "entorno de carga heredaba la configuraci\u00f3n LDAP de producci\u00f3n, de "
        "modo que cada `POST /login/` ejecutaba un intento de v\u00ednculo (bind) "
        "contra el controlador de dominio con esas credenciales."))
    b.append(("p",
        "Esos binds fallaban (la contrase\u00f1a local no es la de AD), pero la "
        "pol\u00edtica de bloqueo del directorio cuenta los intentos fallidos: las "
        "cuatro cuentas usadas quedaron bloqueadas y **ninguna contrase\u00f1a de "
        "personas se envi\u00f3 a AD** \u2014 lo que se envi\u00f3 fue la contrase\u00f1a local "
        "comparada contra el directorio. Se estima el impacto: 37 corridas y "
        "unos 815 inicios de sesi\u00f3n (uno por usuario virtual y corrida) "
        "generaron un intento de bind cada uno."))
    b.append(("nota",
        "Por privacidad no se listan las cuentas afectadas en este documento; "
        "el detalle de qu\u00e9 cuentas y su estado lo maneja IT a partir de los "
        "registros del controlador de dominio."))
    b.append(("h2", "8.2 Causa ra\u00edz"))
    b.append(("ul", [
        "`polidoc/settings.py` declara `AUTHENTICATION_BACKENDS = "
        "['autenticacion.backends.LDAPBackend', 'ModelBackend']` y "
        "`AUTH_LDAP_SERVER_URI = 'ldap://15.15.1.60:389'` (valor por defecto "
        "sin variable de entorno).",
        "`apps/autenticacion/backends.py` ejecuta "
        "`conn.simple_bind_s(f'PTH\\\\{username}', password)` contra esa URI.",
        "`loadtest_settings.py` heredaba esa configuraci\u00f3n: el entorno de "
        "carga autenticaba contra el AD real.",
        "El throttle de intentos fallidos (`LOGIN_MAX_INTENTOS = 5`, en "
        "`apps/autenticacion/views.py`) no fren\u00f3 nada: solo cuenta los "
        "logins que terminan en error, y aqu\u00ed el LDAP fallaba pero "
        "`ModelBackend` completaba el login con \u00e9xito, as\u00ed que el contador se "
        "borraba en cada intento. El riesgo real no era el intento de fuerza "
        "bruta, sino el v\u00ednculo fallido contra AD.",
    ]))
    b.append(("h2", "8.3 Cronolog\u00eda"))
    b.append(T(["Momento", "Acci\u00f3n", "Detalle"],
               [[a, c, d] for a, c, d in INCIDENTE_CRONO],
               "Cronolog\u00eda del incidente y su resoluci\u00f3n"))
    b.append(("h2", "8.4 Blindaje aplicado"))
    b.append(("p",
        "Se descart\u00f3 hacer autenticaci\u00f3n local en primera opci\u00f3n (\u00ablocal "
        "first\u00bb) porque una contrase\u00f1a local vencer\u00eda a la de AD en "
        "producci\u00f3n: regresi\u00f3n de seguridad. La l\u00ednea elegida fue **que el "
        "entorno de carga no pueda tocar AD, por construcci\u00f3n**:"))
    b.append(T(["Capa", "Implementaci\u00f3n", "Efecto"],
               [[a, c, d] for a, c, d in BLINDAJE],
               "Blindaje del entorno de carga contra Active Directory"))
    b.append(("p",
        "Adem\u00e1s, `AGENTS.md` del repositorio documenta estas reglas como "
        "obligatorias para cualquier persona que ejecute carga en el futuro."))
    b.append(("h2", "8.5 Verificaci\u00f3n"))
    b.append(T(["Corrida", "Usuarios", "Conexiones a AD", "Observaciones"],
               [[a, c, d, e] for a, c, d, e in PRUEBAS_POST_BLINDAJE],
               "Corridas posteriores al blindaje con vigilancia de red activa"))
    b.append(("ul", [
        "5 de 5 corridas con `0 conexiones a 15.15.1.60:389` durante toda la "
        "ventana (`*-ldap-check.txt`).",
        "184 avisos `LOGIN BREAK-GLASS v\u00eda ModelBackend (sin LDAP)` en el log "
        "de gunicorn, todos con cuentas `loadtest_*`.",
        "Preflight en cada corrida: `run_loadtest.sh` aborta si "
        "`AUTHENTICATION_BACKENDS` no es solo `ModelBackend` o si la URI "
        "apunta al AD.",
        "`check_no_ldap.sh` se autocorrect\u00f3: detectando una conexi\u00f3n sale con "
        "c\u00f3digo 1; sin ella, con 0 y mensaje `OK`.",
    ]))
    b.append(("h2", "8.6 Impacto y pendientes con IT"))
    b.append(("ul", [
        "**Producci\u00f3n intacta:** no se modific\u00f3 ning\u00fan archivo de "
        "configuraci\u00f3n de producci\u00f3n ni se cambi\u00f3 `polidoc/settings.py` salvo "
        "por el registro de un middleware.",
        "**Pendiente con IT:** confirmar el desbloqueo de las cuentas "
        "afectadas **sin hacer binds de prueba** (cada intento fallido vuelve "
        "a contar para la pol\u00edtica de bloqueo); que IT lo confirme desde el "
        "controlador.",
        "**Pendiente con IT:** exenci\u00f3n de la pol\u00edtica de bloqueo para las "
        "cuentas/OU de pruebas, y monitoreo de binds fallidos desde AD para "
        "que una prueba futura se detecte en minutos.",
        "**Pendiente de producto:** separar la configuraci\u00f3n LDAP por entorno "
        "(variable de entorno obligatoria en vez de IP por defecto en "
        "`settings.py`) para que ninguna instalaci\u00f3n local apunte al AD sin "
        "querer.",
    ]))

    # ---- 9. Suite funcional ----------------------------------------------
    b.append(("h1", "9. Estado de la suite de pruebas"))
    b.append(("p",
        "La suite se ejecut\u00f3 m\u00f3dulo completo (1210 pruebas) en cinco momentos "
        "clave de la sesi\u00f3n:"))
    b.append(T(["Log", "Hora", "Pruebas", "Resultado", "Lectura"],
               [[a, c, d, e, f] for a, c, d, e, f in CORRIDAS_TESTS],
               "Corridas de la suite funcional durante la sesi\u00f3n"))
    b.append(("p",
        "El subconjunto de 16 pruebas ejecutado con los cambios en stash "
        "(14:40) reprodujo exactamente los mismos 4 fallos, lo que confirma "
        "que son **preexistentes** y no regresiones de esta fase:"))
    b.append(T(["Prueba", "Tipo", "Paquete", "Descripci\u00f3n"],
               [[a, c, d, e] for a, c, d, e in FALLOS_TESTS],
               "Fallos preexistentes (verificados con y sin los cambios)"))
    b.append(("ul", [
        "`test_reservas_concurrentes_no_duplican` es intermitente: fall\u00f3 en "
        "4 de 5 corridas y pas\u00f3 en `tests_fase2.log`.",
        "`test_visibilidad_de_documentos_por_departamento` fall\u00f3 solo en la "
        "primera corrida (14:38) y pas\u00f3 en las tres siguientes; queda "
        "marcado para triaje aparte.",
        "`python3 manage.py check` y `makemigrations --check` sin incidencias.",
        "Las pruebas de carga usan `loadtest_settings`/`polidoc.test_settings`, "
        "que deshabilitan LDAP; `polidoc.settings` (producci\u00f3n) no se toc\u00f3.",
    ]))

    # ---- 10. Conclusiones -------------------------------------------------
    b.append(("h1", "10. Conclusiones y recomendaciones"))
    b.append(("h2", "10.1 Conclusiones"))
    b.append(("ol", [
        "El escenario de **27 usuarios cumple el SLA** (p95 de 630\u2013990 ms) "
        "con 0 errores 5xx y CPU por debajo del 90 % en la mejor ventana.",
        "El escenario de **54 usuarios no cumple** (p95 de 4,9\u20135,6 s): el "
        "cuello de botella es la CPU del host, no la aplicaci\u00f3n rota.",
        "Las optimizaciones redujeron las consultas del flujo principal de "
        "44 a 24 y de 28 a 7, y la CPU por petici\u00f3n de las rutas medidas un "
        "27 %.",
        "El entorno de carga qued\u00f3 **ciego para Active Directory** y as\u00ed lo "
        "demuestran 5 corridas con 0 conexiones al controlador de dominio.",
        "La suite funcional mantiene exactamente los mismos 4 fallos "
        "preexistentes: no hay regresiones nuevas.",
    ]))
    b.append(("h2", "10.2 Recomendaciones"))
    b.append(T(["#", "\u00c1rea", "Recomendaci\u00f3n", "Prioridad"],
        [["1", "IT", "Confirmar el desbloqueo de las cuentas afectadas sin "
                    "binds de prueba; excluir de la pol\u00edtica de bloqueo las "
                    "cuentas de pruebas.", "Alta"],
         ["2", "IT", "Monitorear binds fallidos desde AD para detectar en "
                     "minutos cualquier prueba mal orientada.", "Media"],
         ["3", "Producto", "Configurar LDAP por variable de entorno (sin IP "
                           "por defecto en `settings.py`).", "Alta"],
         ["4", "Rendimiento", "Seguir reduciendo CPU por petici\u00f3n (cach\u00e9 de "
                              "cat\u00e1logo, `EXPLAIN ANALYZE` de las rutas calientes).",
          "Media"],
         ["5", "Infraestructura", "Si 54 usuarios es un requisito firme: "
                                  "subir a 8 vCPU o a\u00f1adir una instancia.",
          "Media"],
         ["6", "Calidad", "Triaje de los 4 tests preexistentes (3 FAIL + 1 "
                          "ERROR) y del intermitente de `tests_delegacion`.",
          "Media"],
         ["7", "Proceso", "Commitear los cambios de optimizaci\u00f3n y este "
                          "informe; conservar `docs/informes/` como evidencia.",
          "Alta"]],
        "Recomendaciones priorizadas"))

    # ---- 11. Anexos -------------------------------------------------------
    b.append(("pb",))
    b.append(("h1", "11. Anexos"))
    b.append(("h2", "Anexo A. Inventario de corridas"))
    b.append(("p",
        "Las 42 corridas con evidencia generada en la sesi\u00f3n. `LDAP` indica "
        "la salida del vigilante de red (vac\u00edo = corrida previa al blindaje)."))
    b.append(("orientacion", "landscape"))
    filas = []
    for r in inv:
        filas.append([
            r["corrida"], r["fase"], r["usuarios_max"], r["peticiones"],
            r["fallos"], fmt(r["rps"], 2), fmt(r["avg_ms"]),
            r["p95_ms"], r["max_ms"], fmt(r["cpu_host_max_pct"]),
            r["loadavg_registrado"] or "\u2013", r["ldap_check"] or "\u2013",
        ])
    b.append(T(["Corrida", "Fase", "Usu.", "Petic.", "Fallos", "RPS",
                "Media", "p95", "M\u00e1x", "CPU %", "loadavg", "LDAP"],
               filas, "Inventario completo de corridas (evidencia en "
                      "`datos/` y `/tmp/polidoc_loadtest`)"))
    b.append(("orientacion", "portrait"))
    b.append(("nota",
        "Fallos: `capacity` (sesi\u00f3n previa) tuvo 8 respuestas 500 en el alta "
        "de plantilla y `diag54b` 2 cortes de conexi\u00f3n al servir archivos "
        "grandes; el resto de las corridas, incluida toda la matriz, cerr\u00f3 "
        "con 0 fallos."))

    b.append(("h2", "Anexo B. Detalle por endpoint en las corridas clave"))
    for nombre, titulo in (
        ("base27_w3t4", "L\u00ednea base, 27 usuarios (Fase 0)"),
        ("opt27_w3t4w", "Optimizado, 27 usuarios (ventana tranquila)"),
        ("opt3b_27_w3t4", "Cierre, 27 usuarios con blindaje LDAP"),
        ("opt3_54_w3t4", "Cierre, 54 usuarios con blindaje LDAP"),
    ):
        filas = []
        for f in sorted(leer_endpoints(nombre),
                        key=lambda r: -(_float(r["95%"]) or 0)):
            filas.append([f["Name"], f["Request Count"],
                          fmt(f["Average Response Time"]), f["95%"], f["100%"],
                          fmt(f["Requests/s"], 2)])
        b.append(T(["Endpoint", "Peticiones", "Media (ms)", "p95 (ms)",
                    "M\u00e1x (ms)", "RPS"],
                   filas, f"{titulo} \u2014 corrida `{nombre}`"))

    b.append(("h2", "Anexo C. Comandos reproducibles"))
    b.append(("p",
        "El harness vive en `/tmp/polidoc_loadtest` (fuera del repo a "
        "prop\u00f3sito): no debe commitearse ni ejecutarse contra `polidoc.settings`."))
    for titulo, comando in COMANDOS:
        b.append(("h3", titulo))
        b.append(("pre", comando))
    b.append(("nota",
        "Regla obligatoria: nunca ejecutar el harness con "
        "`DJANGO_SETTINGS_MODULE=polidoc.settings`. El preflight de "
        "`run_loadtest.sh` y el guard de `loadtest_settings` abortan si el "
        "entorno no est\u00e1 ciego para AD."))

    b.append(("h2", "Anexo D. \u00cdndice de evidencias"))
    b.append(T(["Archivos", "Contenido"],
               [[a, c] for a, c in EVIDENCIAS],
               "Evidencias de la sesi\u00f3n (`/tmp/polidoc_loadtest`)"))
    b.append(("p",
        "Evidencia congelada dentro del repositorio en `docs/informes/datos/`: "
        "`inventario_corridas.csv` (las 42 corridas), los CSV de las corridas "
        "clave, los 5 `*-ldap-check.txt`, `profile_final.txt`, "
        "`cpu_bench_final.txt` y las gr\u00e1ficas generadas en `graficas/`."))

    b.append(("h2", "Anexo E. Archivos modificados en el repositorio"))
    b.append(T(["Archivo", "Estado", "Contenido del cambio"],
               [[a, c, d] for a, c, d in ARCHIVOS_MODIFICADOS],
               "Cambios en el c\u00f3digo incluidos en el repositorio"))

    b.append(("h2", "Anexo F. Glosario"))
    b.append(T(["T\u00e9rmino", "Definici\u00f3n"],
               [[a, c] for a, c in GLOSARIO], "Glosario para lectores no "
               "t\u00e9cnicos"))
    return b


def main():
    print(f"Generando {TITULO} v{VERSION} ({FECHA:%d/%m/%Y}) de {AUTOR}")
    print("1/3 gr\u00e1ficas...")
    generar_graficas()
    bloques = construir()
    print("2/3 markdown...")
    render_md(bloques, SALIDA_MD)
    print("3/3 docx...")
    render_docx(bloques, SALIDA_DOCX)
    print("Listo. PDF: soffice --headless --convert-to pdf "
          f"--outdir {BASE} {SALIDA_DOCX}")


if __name__ == "__main__":
    main()
