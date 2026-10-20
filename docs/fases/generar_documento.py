#!/usr/bin/env python3
"""Genera el Documento de Fases de POLIDOC en formato Word (.docx).

El PDF se obtiene convirtiendo este .docx con LibreOffice (ver README de la
carpeta o el plan de ejecución). Este script es documentación: no forma parte
del entorno Django.

Uso:
    python docs/fases/generar_documento.py
"""

from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = Path(__file__).resolve().parent
SALIDA = BASE / "POLIDOC_Documento_de_Fases_v1.0.docx"

AZUL = RGBColor(0x1F, 0x3A, 0x5F)
AZUL_CLARO = RGBColor(0x2E, 0x5C, 0x8A)
GRIS = RGBColor(0x55, 0x55, 0x55)
NEGRO = RGBColor(0x22, 0x22, 0x22)

VERSION = "1.0"
FECHA = "20/10/2026"
COMMIT = "d79d89f"
AUTOR = "Pasante TSU. Xavier Lara"
ORGANIZACION = "Policlínica Táchira"


# ---------------------------------------------------------------------------
# Utilidades de formato
# ---------------------------------------------------------------------------
def _sombra_celda(cell, hex_color):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _configurar_estilos(doc):
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = NEGRO
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.08

    for nombre, tam, color in (
        ("Heading 1", 16, AZUL),
        ("Heading 2", 13, AZUL_CLARO),
        ("Heading 3", 11.5, GRIS),
    ):
        st = doc.styles[nombre]
        st.font.name = "Calibri"
        st.font.size = Pt(tam)
        st.font.bold = True
        st.font.color.rgb = color
        st.paragraph_format.space_before = Pt(14 if nombre == "Heading 1" else 10)
        st.paragraph_format.space_after = Pt(6)
        st.paragraph_format.keep_with_next = True


def _campo(pagina, instruccion):
    run = pagina.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruccion
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_end)


def _encabezado_y_pie(doc):
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.0)

    enc = section.header.paragraphs[0]
    enc.text = f"POLIDOC · Documento de Fases v{VERSION}"
    enc.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for run in enc.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = GRIS

    pie = section.footer.paragraphs[0]
    pie.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pie.add_run("Página ")
    _campo(pie, "PAGE")
    pie.add_run(" de ")
    _campo(pie, "NUMPAGES")
    for run in pie.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = GRIS


def h1(doc, texto):
    doc.add_heading(texto, level=1)


def h2(doc, texto):
    doc.add_heading(texto, level=2)


def h3(doc, texto):
    doc.add_heading(texto, level=3)


def p(doc, texto="", *, bold=False, italic=False, size=None, align=None,
      color=None, space_after=6):
    par = doc.add_paragraph()
    run = par.add_run(texto)
    run.bold = bold
    run.italic = italic
    if size:
        run.font.size = Pt(size)
    if color:
        run.font.color.rgb = color
    if align == "center":
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "right":
        par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif align == "justify":
        par.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    par.paragraph_format.space_after = Pt(space_after)
    return par


def bullets(doc, items):
    for item in items:
        par = doc.add_paragraph(item, style="List Bullet")
        par.paragraph_format.space_after = Pt(2)


def numeros(doc, items):
    for item in items:
        par = doc.add_paragraph(item, style="List Number")
        par.paragraph_format.space_after = Pt(2)


def tabla(doc, encabezados, filas, *, anchos=None, tam=9.5,
          repetir_encabezado=False):
    t = doc.add_table(rows=1, cols=len(encabezados))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False

    header = t.rows[0].cells
    for i, texto in enumerate(encabezados):
        header[i].text = ""
        par = header[i].paragraphs[0]
        run = par.add_run(texto)
        run.bold = True
        run.font.size = Pt(tam)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _sombra_celda(header[i], "1F3A5F")

    if repetir_encabezado:
        tr_pr = t.rows[0]._tr.get_or_add_trPr()
        tbl_header = OxmlElement("w:tblHeader")
        tbl_header.set(qn("w:val"), "true")
        tr_pr.append(tbl_header)

    for fila in filas:
        celdas = t.add_row().cells
        for i, valor in enumerate(fila):
            celdas[i].text = ""
            par = celdas[i].paragraphs[0]
            run = par.add_run(str(valor))
            run.font.size = Pt(tam)

    if anchos:
        for i, ancho in enumerate(anchos):
            for row in t.rows:
                row.cells[i].width = Cm(ancho)

    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def nota(doc, texto):
    par = doc.add_paragraph()
    run = par.add_run(texto)
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = GRIS
    par.paragraph_format.space_after = Pt(8)


# ---------------------------------------------------------------------------
# Portada
# ---------------------------------------------------------------------------
def portada(doc):
    for _ in range(4):
        doc.add_paragraph()

    p(doc, "POLIDOC", bold=True, size=30, align="center", color=AZUL,
      space_after=4)
    p(doc, "Documento de Fases", bold=True, size=24, align="center",
      color=AZUL_CLARO, space_after=8)
    p(doc, "V1.0 elaborada y hoja de ruta de evolución", size=13,
      align="center", color=GRIS, space_after=24)

    p(doc, "Sistema de Gestión Documental", bold=True, size=12,
      align="center", space_after=2)
    p(doc, ORGANIZACION, size=12, align="center", space_after=28)

    p(doc, AUTOR, bold=True, size=11, align="center", space_after=2)
    p(doc, "Pasante TSU", size=10, align="center", color=GRIS, space_after=36)

    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    datos = [
        ("Documento", "Documento de Fases — POLIDOC"),
        ("Versión", VERSION),
        ("Fecha", FECHA),
        ("Sistema verificado", f"commit {COMMIT}"),
        ("Autor", AUTOR),
        ("Organización", ORGANIZACION),
        ("Estado", "Documento de planificación"),
    ]
    for etiqueta, valor in datos:
        celdas = t.add_row().cells
        celdas[0].text = etiqueta
        celdas[1].text = valor
        for run in celdas[0].paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(9.5)
        for run in celdas[1].paragraphs[0].runs:
            run.font.size = Pt(9.5)
        celdas[0].width = Cm(5.5)
        celdas[1].width = Cm(8.5)

    doc.add_paragraph()
    p(doc, "Documento de planificación. No sustituye al manual de usuario ni a "
           "CONTEXT.txt, que sigue siendo la fuente técnica de verdad.",
      italic=True, size=8.5, align="center", color=GRIS)

    doc.add_page_break()


# ---------------------------------------------------------------------------
# Contenido
# ---------------------------------------------------------------------------
def contenido(doc):
    h1(doc, "Contenido")
    secciones = [
        "1. Resumen ejecutivo",
        "2. Contexto y usuarios",
        "3. Fase 1 — V1.0 (Elaborada)",
        "4. Fase 2 — Plantillas (Pendiente)",
        "5. Fase 3 — Cumplimiento ISO completo (Pendiente)",
        "6. Fase 4 — Producción y migración de datos (Propuesta)",
        "7. Fase 5 — Experiencia y alcance (Futura)",
        "8. Fase 6 — Integraciones e IA (Futura)",
        "9. Cronograma y estimaciones",
        "10. Dependencias y riesgos",
        "11. Anexos: glosario, mapeo de conceptos y trazabilidad",
    ]
    for s in secciones:
        par = doc.add_paragraph()
        run = par.add_run(s)
        run.font.size = Pt(11)
        par.paragraph_format.space_after = Pt(3)


def resumen_ejecutivo(doc):
    h1(doc, "1. Resumen ejecutivo")
    p(doc, "POLIDOC es el sistema de gestión documental de la Policlínica "
           "Táchira, orientado a las normas ISO 9001 (gestión de la calidad), "
           "ISO 27001 (seguridad de la información) e ISO 15489 (gestión de "
           "documentos). Centraliza el control de versiones, las aprobaciones, "
           "la vigencia, la clasificación de seguridad, las descargas "
           "trazables y la auditoría inmutable.", align="justify")
    p(doc, "La Fase 1 (V1.0) está elaborada y cubre el núcleo de la gestión "
           "documental para todos los cargos de la institución: trabajadores, "
           "coordinadores, jefes de departamento y administradores. La "
           "siguiente etapa se concentra en dos entregables:", align="justify")
    bullets(doc, [
        "Fase 2 — Módulo de Plantillas: documentos en blanco reutilizables "
        "que otros usuarios descargan, llenan y vuelven a subir.",
        "Fase 3 — Cumplimiento ISO completo: retención y disposición "
        "programada, política de retención por tipo, informes de auditoría, "
        "firma electrónica y sellado de tiempo, y control de copias "
        "impresas.",
    ])
    p(doc, "La base técnica de ambas fases se estima en 10 a 12,5 semanas de "
           "trabajo; el tiempo restante se reserva para pruebas, aceptación, "
           "ajustes e imprevistos. Las fases siguientes (producción, "
           "experiencia e integraciones) se presentan como propuesta y quedan "
           "para etapas posteriores.", align="justify")
    nota(doc, "Estimaciones con un desarrollador (Xavier Lara) y desarrollo "
              "asistido por IA, con QA parcial. Se expresan como rangos, no "
              "como fechas cerradas.")


def contexto(doc):
    h1(doc, "2. Contexto y usuarios")
    p(doc, "POLIDOC está pensado para toda la organización, no para un área "
           "aislada. Cada documento se asocia a un departamento, un tipo "
           "documental y un responsable, y su visibilidad depende de la "
           "clasificación de seguridad asignada.", align="justify")

    h2(doc, "2.1 Roles cubiertos")
    tabla(doc, ["Rol", "Alcance principal"], [
        ["Trabajador", "Crea y gestiona sus propios documentos; consulta y "
                       "solicita descargas según permisos."],
        ["Coordinador", "Gestiona documentos propios y los asignados por su "
                        "jefe, con permisos granulares de lectura, edición y "
                        "eliminación."],
        ["Jefe de departamento", "Supervisa todos los documentos de su "
                                 "departamento, aprueba, asigna, transfiere "
                                 "propiedad y gestiona la vigencia."],
        ["Administrador", "Configura catálogos, formatos, períodos, "
                          "departamentos, usuarios y auditoría; supervisa el "
                          "cumplimiento."],
    ], anchos=[4.0, 12.0])

    h2(doc, "2.2 Normas de referencia")
    tabla(doc, ["Norma", "Aporte al sistema"], [
        ["ISO 9001", "Control de la información documentada, aprobaciones, "
                     "versiones y distribución de copias controladas."],
        ["ISO 27001", "Clasificación de la información, control de accesos y "
                      "registro de eventos de seguridad."],
        ["ISO 15489", "Ciclo de vida del documento: creación, vigencia, "
                      "retención y disposición final."],
    ], anchos=[3.5, 12.5])

    h2(doc, "2.3 Base tecnológica")
    p(doc, "Aplicación web Django 6 con base de datos SQLite (o PostgreSQL en "
           "producción), auditoría en MongoDB, tareas asíncronas con Celery, "
           "conversión de documentos con LibreOffice y despliegue con Docker.",
      align="justify")


def fase1(doc):
    h1(doc, "3. Fase 1 — V1.0 (Elaborada)")
    p(doc, "Objetivo: sustituir la gestión documental dispersa por un sistema "
           "único, trazable y alineado a las normas ISO, con flujos de "
           "aprobación y auditoría desde el primer día.", align="justify")

    h2(doc, "3.1 Módulos construidos")
    p(doc, "La V1.0 se organiza en 13 módulos operativos, verificables desde "
           "la propia aplicación.", align="justify")
    tabla(doc, ["Módulo", "Qué resuelve", "Funciones principales",
                "Evidencia / acceso"], [
        ["Autenticación, usuarios y roles",
         "Acceso seguro y perfilado por rol.",
         "Login LDAP/AD con respaldo local; logout; perfiles administrador, "
         "jefe, coordinador y trabajador; aceptación de términos; foto de "
         "perfil; gestión de usuarios y asignación de departamento.",
         "/login/, /perfil/, /usuarios/"],
        ["Notificaciones",
         "Avisar los eventos del flujo dentro de la app.",
         "16 tipos (solicitudes, aprobaciones, rechazos, asignaciones, "
         "versiones, transferencias, distribución/revocación, vigencia, "
         "escalado, formatos); campanita con contador; marcar leída/todas; "
         "API JSON.",
         "Campanita del navbar; /api/notificaciones/"],
        ["Documentos (CRUD y versionado)",
         "Núcleo del gestor documental.",
         "Catálogo y alta por tipo (15 tipos ISO); edición de borrador y "
         "documento; eliminación; envío a aprobación; detalle; versiones e "
         "historial; archivo original + PDF; visor; clasificación "
         "público/interno/confidencial/restringido; relaciones; departamentos "
         "afectados; retención/vencimiento; destrucción controlada.",
         "/documentos/, /documentos/mis-documentos/, detalle "
         "/<tipo>/<uuid>/"],
        ["Aprobaciones y planillas de cambio",
         "Autorizar creaciones, cambios y estados.",
         "Cadenas de 1/2 pasos; bandeja del jefe; aprobar/rechazar creación; "
         "planillas FOR-005 (crear, enviar, aprobar, rechazar); solicitudes "
         "de cambio de estado; delegación de jefatura; escalado a las 48 h; "
         "mis solicitudes.",
         "/documentos/bandeja-aprobaciones/, "
         "/documentos/planilla-cambios/nueva/, /documentos/delegaciones/, "
         "/documentos/mis-solicitudes/"],
        ["Descargas y solicitudes de acceso",
         "Entrega controlada de archivos.",
         "Solicitud con motivo; aprobación del jefe; doble control secuencial "
         "jefe → GG para «restringido»; versión aprobada y expiración; "
         "solicitudes de acceso a documentos; notificaciones y auditoría.",
         "/documentos/mis-solicitudes/, "
         "/<tipo>/<uuid>/solicitar-descarga/"],
        ["Distribución y copias controladas",
         "Reparto formal y revocable (ISO 9001:7.5.3).",
         "Distribuir a usuarios; método sistema/email/impreso; historial por "
         "documento; revocación con motivo y correo; registro de envío/error; "
         "notificaciones.",
         "/<tipo>/<uuid>/distribuir/, "
         "/<tipo>/<uuid>/historial-distribucion/"],
        ["Vigencia, retención y ciclo de vida",
         "Mantener vigente lo que debe y retirar lo obsoleto.",
         "Renovar vencimiento; establecer/quitar vigencia; panel y conteos; "
         "alertas; retención/destrucción; revisiones pendientes; comandos de "
         "verificación; ciclo de vida.",
         "Panel de vigencia; /documentos/configuracion/ciclo-vida/; comandos "
         "verificar_*"],
        ["Asignaciones y transferencia de propiedad",
         "Mover la responsabilidad sin perder trazabilidad.",
         "Asignar a usuarios del departamento; gestionar asignaciones; "
         "transferir propiedad individual; transferencia masiva (admin); "
         "reasignación automática de pendientes al cambiar de jefe.",
         "/documentos/asignaciones/, "
         "/documentos/configuracion/transferencia-masiva/"],
        ["Auditoría e integridad",
         "Evidencia inmutable de lo ocurrido.",
         "Registro en MongoDB con HMAC y cadena de hashes; buffer ante "
         "caídas; login/logout y acciones de negocio; consulta y detalle; "
         "reportes PDF y almacenados; verificación de integridad; "
         "fail-closed.",
         "/documentos/configuracion/auditoria/, reporte PDF"],
        ["Panel administrativo y configuración",
         "Gobierno del sistema sin tocar código.",
         "Dashboard; tipos de documento (CRUD y reactivación); períodos "
         "(CRUD); departamentos y subáreas (CRUD); roles y permisos; ciclo de "
         "vida; auditoría; transferencia masiva; gestión de usuarios.",
         "/documentos/configuracion/, /usuarios/"],
        ["Formularios dinámicos y exportaciones",
         "Formatos SGC configurables y llenables.",
         "Constructor de secciones/campos; preview; publicación y versionado "
         "de formatos; formulario dinámico; FOR-001/FOR-005; exportación "
         "XLSX/PDF de solicitudes.",
         "/documentos/configuracion/formatos/, /documentos/nuevo/<tipo>/"],
        ["Búsqueda, IA y conversión a PDF",
         "Encontrar y preparar documentos.",
         "Búsqueda global con permisos; sugerencias; resumen con IA "
         "(Gemini); conversión a PDF con QA/adaptativo; diagnóstico y "
         "reparación.",
         "/documentos/buscar/, /<tipo>/<uuid>/resumen-ia/, "
         "/<tipo>/<uuid>/estado-conversion/"],
        ["Servicios internos y tareas programadas",
         "Sostener la operación sin bloquear la interfaz.",
         "API JSON interna; Celery (conversión PDF, planillas y auditoría); "
         "comandos de limpieza, población y verificación; buffer de auditoría "
         "y reintentos.",
         "apps/documentos/api.py, apps/documentos/tasks.py, "
         "apps/auditoria/tasks.py, management/commands/"],
    ], anchos=[3.0, 3.8, 6.2, 3.4], tam=8.5, repetir_encabezado=True)

    h2(doc, "3.2 Roles cubiertos")
    p(doc, "La V1.0 cubre a trabajadores, coordinadores, jefes de departamento "
           "y administradores. La Gerencia General participa como instancia de "
           "autorización en los flujos que la requieren.", align="justify")

    h2(doc, "3.3 Evidencia de elaboración")
    bullets(doc, [
        "Suite amplia de pruebas automatizadas sobre documentos, "
        "autenticación y auditoría.",
        "Manual de usuario de 14 capítulos, con guías por rol y capturas de "
        "pantalla.",
        "Documentación técnica de decisiones y arquitectura en CONTEXT.txt.",
    ])

    h2(doc, "3.4 Deuda conocida")
    bullets(doc, [
        "La unificación de los tres flujos de aprobación (documento, planilla "
        "y plantilla) queda como deuda técnica para una fase posterior.",
        "La puesta en producción y la migración de datos históricos se "
        "abordan en la Fase 4.",
    ])
    nota(doc, "Estado: Elaborada. No se asigna estimación porque ya está "
              "construida; sirve como base de las fases siguientes.")


def fase2(doc):
    h1(doc, "4. Fase 2 — Plantillas (Pendiente)")
    p(doc, "Objetivo: incorporar el concepto de plantilla como documento en "
           "blanco reutilizable. Un usuario sube la estructura vacía y otros "
           "la descargan, la llenan por fuera y suben el documento resultante, "
           "quedando vinculado a su plantilla de origen.", align="justify")

    h2(doc, "4.1 Alcance")
    tabla(doc, ["Sub-fase", "Entregable", "Esfuerzo base"], [
        ["F2.0", "Modelo y migración 0055: marca de plantilla y vínculo con "
                 "la plantilla de origen.", "1 semana"],
        ["F2.1", "Catálogo de Plantillas y filtro en los listados generales.",
         "1 semana"],
        ["F2.2", "Alta de plantilla con aprobación condicional según el rol.",
         "1,5 semanas"],
        ["F2.3", "Modificación y eliminación reutilizando FOR-005 y FOR-001.",
         "0,5 semana"],
        ["F2.4", "Vínculo plantilla ↔ tipo ↔ documento.",
         "1 semana"],
        ["F2.5", "Pruebas, control de calidad y documentación de la fase.",
         "1–2 semanas"],
    ], anchos=[2.0, 11.0, 3.0])
    nota(doc, "Base técnica: 6–8 semanas. La estimación incorpora holgura "
              "para pruebas, ajustes, aceptación y documentación de la fase.")

    h2(doc, "4.2 Reglas de negocio principales")
    bullets(doc, [
        "Códigos de plantilla con el segmento PLT: {DEPTO}-{TIPO}-PLT-{SEC}, "
        "en una serie independiente de la de documentos.",
        "Aprobación condicional: el jefe de departamento activa la plantilla "
        "directamente; trabajadores y coordinadores requieren un paso de "
        "aprobación del jefe.",
        "La visibilidad respeta la clasificación de seguridad del documento.",
        "La modificación y la eliminación reutilizan los flujos existentes "
        "FOR-005 y FOR-001.",
    ])

    h2(doc, "4.3 Criterios de aceptación")
    bullets(doc, [
        "Una plantilla y un documento del mismo tipo obtienen códigos "
        "distintos y sin colisiones.",
        "Un jefe activa su plantilla sin aprobación; un trabajador queda "
        "pendiente del jefe y se activa al aprobarse.",
        "El catálogo muestra solo las plantillas visibles para el perfil.",
        "El documento lleno queda vinculado a su plantilla de origen.",
    ])

    h2(doc, "4.4 Riesgos y mitigaciones")
    tabla(doc, ["Riesgo", "Mitigación"], [
        ["Reclasificar formularios ya llenos como plantillas.",
         "No se hace backfill automático: un comando con modo de simulación "
         "lista los candidatos antes de aplicar."],
        ["Colisión de códigos.",
         "Segmento PLT en la serie de plantillas y validación de unicidad."],
        ["Cambio de significado del catálogo actual.",
         "Actualizar títulos, badges y pruebas del catálogo en la misma "
         "entrega."],
    ], anchos=[7.0, 9.0])

    h2(doc, "4.5 Fuera de alcance")
    p(doc, "La unificación de los flujos de aprobación (F5 del plan técnico) "
           "no forma parte de esta fase; queda como deuda técnica futura.",
      align="justify")


def fase3(doc):
    h1(doc, "5. Fase 3 — Cumplimiento ISO completo (Pendiente)")
    p(doc, "Objetivo: completar los controles ISO del sistema sobre la "
           "infraestructura ya construida (períodos, vigencia, ciclo de vida, "
           "auditoría y reportes): retención y disposición final, informes de "
           "auditoría, firma electrónica y control de copias impresas.",
      align="justify")

    h2(doc, "5.1 Alcance")
    tabla(doc, ["Sub-fase", "Entregable", "Esfuerzo base"], [
        ["F3.1", "Política de retención por tipo de documento (configuración).",
         "0,5 semana"],
        ["F3.2", "Retención y disposición final programada (tarea automática y "
                 "estados).", "1,5 semanas"],
        ["F3.3", "Informes de auditoría consolidados y exportables.",
         "0,5 semana"],
        ["F3.4", "Firma electrónica y sellado de tiempo (integración con "
                 "proveedor).", "1–1,5 semanas"],
        ["F3.5", "Control de copias impresas.", "0,5 semana"],
    ], anchos=[2.0, 11.0, 3.0])
    nota(doc, "Base ISO: 4–4,5 semanas. Sumada a la Fase 2, la etapa "
              "inmediata queda con ≈0,5–2,5 semanas de holgura.")

    h2(doc, "5.2 Criterios de aceptación")
    bullets(doc, [
        "Cada tipo de documento puede definir su política de retención.",
        "Los documentos cumplen su plazo y pasan a disposición final de forma "
        "trazable.",
        "Los informes de auditoría se exportan con los filtros y el rango "
        "solicitados.",
        "Los documentos firmados quedan sellados de tiempo y verificables "
        "mediante el proveedor de firma.",
        "Las copias impresas quedan registradas, numeradas y controladas.",
    ])

    h2(doc, "5.3 Fuera de alcance")
    p(doc, "La gestión de no conformidades no forma parte del alcance de "
           "POLIDOC; la Policlínica la gestiona mediante su proceso propio.",
      align="justify")
    nota(doc, "La contratación del proveedor de firma y su validación legal "
              "son externas y deben estar resueltas al inicio de la fase "
              "(semana 1). El cumplimiento operativo real se valida al "
              "desplegar en producción (Fase 4); esta fase implementa los "
              "controles en el software.")


def fase4(doc):
    h1(doc, "6. Fase 4 — Producción y migración de datos (Propuesta)")
    p(doc, "Objetivo: poner POLIDOC en operación real en la institución, con "
           "la información histórica migrada y el equipo capacitado.",
      align="justify")
    h2(doc, "6.1 Alcance")
    bullets(doc, [
        "Integración con Active Directory / LDAP real y políticas de acceso.",
        "Base de datos PostgreSQL, respaldos y plan de restauración.",
        "HTTPS, endurecimiento de seguridad y gestión de secretos.",
        "Integración continua, monitoreo, alertas y registros.",
        "Migración de documentos históricos del sistema anterior.",
        "Capacitación por rol, documentación operativa y despliegue.",
    ])
    p(doc, "Estimación: 8–12 semanas. La migración de datos es la variable con "
           "mayor incertidumbre, porque depende del estado y la calidad de la "
           "información de origen.", align="justify")


def fase5(doc):
    h1(doc, "7. Fase 5 — Experiencia y alcance (Futura)")
    bullets(doc, [
        "Notificaciones por correo y push.",
        "Búsqueda de texto completo y reconocimiento óptico de caracteres.",
        "Paneles de indicadores, reportería y análisis.",
        "Aplicación instalable (PWA) y acceso sin conexión.",
        "Accesibilidad e internacionalización.",
    ])
    p(doc, "Estimación: 6–8 semanas.", align="justify")


def fase6(doc):
    h1(doc, "8. Fase 6 — Integraciones e IA (Futura)")
    bullets(doc, [
        "API pública para integraciones con otros sistemas.",
        "Inicio de sesión único (SSO).",
        "Clasificación y sugerencias asistidas por IA.",
        "Integración con recursos humanos y gestión de la calidad.",
        "Soporte multi-sede.",
    ])
    p(doc, "Estimación: 6–10 semanas.", align="justify")


def cronograma(doc):
    h1(doc, "9. Cronograma y estimaciones")
    p(doc, "Las estimaciones asumen un desarrollador a tiempo completo y "
           "desarrollo asistido por IA, con control de calidad parcial. Se "
           "expresan como rangos.", align="justify")

    h2(doc, "9.1 Resumen de fases")
    tabla(doc, ["Fase", "Contenido", "Estado", "Estimación", "Horizonte"], [
        ["1. V1.0", "Gestión documental completa", "Elaborada", "—",
         "Base existente"],
        ["2. Plantillas", "Catálogo, alta, modificación y vínculo",
         "Pendiente", "Base 6–8 sem", "Inmediata"],
        ["3. ISO completo", "Retención, informes, firma electrónica y copias "
                            "impresas", "Pendiente", "Base 4–4,5 sem",
         "Inmediata"],
        ["4. Producción", "LDAP, PostgreSQL, respaldos, migración y "
                          "despliegue", "Propuesta", "8–12 sem", "Posterior"],
        ["5. Experiencia", "Notificaciones, búsqueda, paneles y PWA",
         "Futura", "6–8 sem", "Futura"],
        ["6. Integraciones", "API, SSO, IA y multi-sede", "Futura",
         "6–10 sem", "Futura"],
    ], anchos=[3.0, 5.2, 2.2, 2.8, 2.4], tam=8.5)

    h3(doc, "9.1.1 Desglose de la V1.0")
    tabla(doc, ["Bloque", "Entregable", "Evidencia"], [
        ["Núcleo documental", "15 tipos ISO, códigos automáticos, versionado, "
                              "vigencia y clasificación de seguridad.",
         "25 modelos de datos"],
        ["Aprobaciones", "Cadenas de 1 y 2 pasos, bandeja, planillas FOR-005, "
                         "delegación de jefatura y escalado a 48 horas.",
         "Servicios y pruebas"],
        ["Descargas y distribución", "Solicitudes de descarga, doble control "
                                     "restringido, copias controladas y "
                                     "revocación.",
         "Flujo secuencial documentado"],
        ["Seguridad y auditoría", "LDAP y cuenta local, roles, auditoría en "
                                  "MongoDB con HMAC y cadena de hashes.",
         "Reportes e integridad"],
        ["Formatos SGC", "FOR-001 y FOR-005, constructor de formularios "
                         "dinámicos.", "Catálogo de formatos"],
        ["Búsqueda e IA", "Búsqueda de documentos, resumen con IA y conversión "
                          "a PDF con control de calidad.", "Servicios"],
        ["Panel administrativo", "Tipos, departamentos, períodos, formatos y "
                                 "ciclo de vida desde un panel único.",
         "Vistas de configuración"],
        ["Calidad y documentación", "Más de 1.100 pruebas automatizadas, "
                                    "manual de 14 capítulos y 18 diagramas "
                                    "UML.", "Suite y documentación"],
    ], anchos=[3.6, 8.4, 4.0], tam=8.5)
    p(doc, "En dos meses de pasantía se construyeron los ocho bloques "
           "anteriores, documentados y con pruebas automatizadas; la V1.0 es "
           "una base operativa, no un prototipo.", align="justify")

    h2(doc, "9.2 Esfuerzo de la etapa inmediata")
    p(doc, "La etapa inmediata cubre la Fase 2 y la Fase 3. La suma de "
           "esfuerzo técnico es de 10 a 12,5 semanas; el tiempo restante se "
           "reserva para pruebas, aceptación, ajustes e imprevistos.",
      align="justify")
    tabla(doc, ["Bloque", "Esfuerzo base"], [
        ["Fase 2 — Plantillas", "6–8 semanas"],
        ["Fase 3 — ISO completo", "4–4,5 semanas"],
        ["Total técnico", "10–12,5 semanas"],
        ["Holgura para pruebas y aceptación", "≈0,5–2,5 semanas"],
    ], anchos=[9.0, 5.0])

    h2(doc, "9.3 Desglose de la Fase 2")
    tabla(doc, ["Sub-fase", "Entregable", "Esfuerzo"], [
        ["F2.0", "Modelo y migración 0055", "1 semana"],
        ["F2.1", "Catálogo y listados con filtro", "1 semana"],
        ["F2.2", "Alta y aprobación condicional", "1,5 semanas"],
        ["F2.3", "Modificación y eliminación", "0,5 semana"],
        ["F2.4", "Vínculo plantilla ↔ documento", "1 semana"],
        ["F2.5", "Pruebas, calidad y documentación", "1–2 semanas"],
        ["Total", "Base técnica", "6–8 semanas"],
    ], anchos=[2.0, 9.5, 4.5])

    h2(doc, "9.4 Desglose de la Fase 3")
    tabla(doc, ["Sub-fase", "Entregable", "Esfuerzo"], [
        ["F3.1", "Política de retención por tipo", "0,5 semana"],
        ["F3.2", "Retención y disposición programada", "1,5 semanas"],
        ["F3.3", "Informes de auditoría consolidados", "0,5 semana"],
        ["F3.4", "Firma electrónica y sellado (integración)", "1–1,5 semanas"],
        ["F3.5", "Control de copias impresas", "0,5 semana"],
        ["Total", "ISO completo", "4–4,5 semanas"],
    ], anchos=[2.0, 9.5, 4.5])


def riesgos(doc):
    h1(doc, "10. Dependencias y riesgos")
    h2(doc, "10.1 Matriz de riesgos")
    tabla(doc, ["Ámbito", "Riesgo", "Mitigación"], [
        ["Fase 2", "Reclasificar formularios ya llenos como plantillas.",
         "Comando con simulación previa; sin cambios automáticos en la "
         "migración."],
        ["Fase 2", "Cambio de significado del catálogo actual.",
         "Actualizar títulos, badges y pruebas en la misma entrega."],
        ["Fase 3", "Retención y disposición mal configuradas.",
         "Valores por defecto conservadores y validación en pruebas."],
        ["Fase 3", "Firma electrónica sin proveedor definido.",
         "Definir proveedor y alcance jurídico en la semana 1; si no, se "
         "entrega la base y la firma se reprograma."],
        ["Fase 4", "Calidad de los datos históricos.",
         "Inventario, limpieza y migración por etapas con validación."],
        ["Fase 4", "Dependencia del Active Directory y la red.",
         "Pruebas con cuentas reales y plan de contingencia de acceso."],
        ["Transversal", "Adopción por parte de los usuarios.",
         "Capacitación por rol y manual de usuario actualizado."],
    ], anchos=[2.6, 6.4, 7.0], tam=8.5)

    h2(doc, "10.2 Dependencias entre fases")
    bullets(doc, [
        "La Fase 2 y la Fase 3 reutilizan los modelos y flujos de la V1.0.",
        "La firma electrónica de la Fase 3 depende de decisiones de proveedor "
        "y validación legal.",
        "La Fase 4 requiere que la Fase 2 y la Fase 3 estén cerradas y "
        "probadas.",
        "Las Fases 5 y 6 son evolutivos posteriores a la puesta en producción.",
    ])


def anexos(doc):
    h1(doc, "11. Anexos")
    h2(doc, "11.1 Glosario")
    tabla(doc, ["Término", "Definición"], [
        ["Documento", "Registro controlado con código, tipo, departamento, "
                      "versión, clasificación y estado."],
        ["Plantilla", "Documento en blanco reutilizable que sirve de base para "
                      "crear documentos llenos."],
        ["Planilla de cambios", "Formulario que documenta una modificación y "
                                "su aprobación."],
        ["Formulario", "Plantilla institucional del flujo: FOR-001 para "
                       "creación/eliminación y FOR-005 para modificación."],
        ["Vigencia", "Período durante el cual el documento es válido."],
        ["Retención", "Período durante el cual el documento debe conservarse "
                      "antes de su disposición final."],
        ["Clasificación", "Nivel de seguridad: público, interno, confidencial "
                          "o restringido."],
        ["Auditoría", "Registro inmutable de las acciones sobre los "
                      "documentos."],
    ], anchos=[4.0, 12.0], tam=8.5)

    h2(doc, "11.2 Mapeo de conceptos")
    tabla(doc, ["Concepto", "Entidad", "Aprobación"], [
        ["Plantilla (en blanco)", "Documento con marca de plantilla",
         "Condicional según el rol"],
        ["Documento lleno (creación)", "Documento + FOR-001",
         "Cadena de aprobación"],
        ["Planilla de cambios (modificación)", "Planilla + FOR-005",
         "Aprobación de planilla"],
    ], anchos=[5.5, 5.5, 5.0], tam=9)

    h2(doc, "11.3 Trazabilidad")
    p(doc, "Cada fase se valida contra criterios de aceptación y pruebas "
           "automatizadas. El detalle técnico de la Fase 2 vive en "
           "PLAN_PLANTILLAS.txt; la fuente técnica general sigue siendo "
           "CONTEXT.txt.", align="justify")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    doc = Document()
    _configurar_estilos(doc)
    _encabezado_y_pie(doc)

    portada(doc)
    contenido(doc)
    doc.add_page_break()

    resumen_ejecutivo(doc)
    contexto(doc)
    fase1(doc)
    fase2(doc)
    fase3(doc)
    fase4(doc)
    fase5(doc)
    fase6(doc)
    cronograma(doc)
    riesgos(doc)
    anexos(doc)

    doc.save(str(SALIDA))
    print(f"Generado: {SALIDA}")


if __name__ == "__main__":
    main()
