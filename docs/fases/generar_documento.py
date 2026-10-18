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
FECHA = "18/10/2026"
COMMIT = "b1471c7"
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


def tabla(doc, encabezados, filas, *, anchos=None, tam=9.5):
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
        "5. Fase 3 — Cumplimiento ISO base (Pendiente)",
        "6. Fase 4 — Producción y migración de datos (Propuesta)",
        "7. Fase 5 — Cumplimiento ISO completo (Futura)",
        "8. Fase 6 — Experiencia y alcance (Futura)",
        "9. Fase 7 — Integraciones e IA (Futura)",
        "10. Cronograma y estimaciones",
        "11. Dependencias y riesgos",
        "12. Anexos: glosario, mapeo de conceptos y trazabilidad",
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
           "siguiente etapa se concentra en dos entregables dentro de una "
           "ventana contractual de tres meses:", align="justify")
    bullets(doc, [
        "Fase 2 — Módulo de Plantillas: documentos en blanco reutilizables "
        "que otros usuarios descargan, llenan y vuelven a subir.",
        "Fase 3 — Cumplimiento ISO (alcance base): retención y disposición "
        "programada, política de retención por tipo e informes de auditoría.",
    ])
    p(doc, "La base técnica de ambas fases se estima en 8,5 a 11,5 semanas de "
           "trabajo; el resto de la ventana de tres meses se reserva para "
           "pruebas, aceptación, ajustes e imprevistos. Las fases posteriores "
           "(producción, ISO completo, experiencia e integraciones) se "
           "presentan como propuesta y quedan fuera del contrato actual.",
      align="justify")
    nota(doc, "Estimaciones con un desarrollador (Xavier Lara) y desarrollo "
              "asistido por IA, con QA parcial. Se expresan como rangos, no "
              "como fechas comprometidas.")


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
    tabla(doc, ["Módulo", "Qué incluye"], [
        ["Autenticación y roles", "Acceso con Active Directory (LDAP) o "
                                  "cuenta local, perfiles por rol, "
                                  "notificaciones y aceptación de términos."],
        ["Gestión documental", "15 tipos ISO, códigos automáticos, "
                               "clasificación de seguridad, departamentos, "
                               "vigencia y retención."],
        ["Creación y formatos", "Formularios FOR-001 (creación/eliminación) y "
                                "FOR-005 (modificación), formularios "
                                "dinámicos configurables."],
        ["Aprobaciones y versionado", "Cadenas de aprobación por rol, "
                                      "bandeja de aprobaciones, planillas de "
                                      "cambio y delegación de jefatura."],
        ["Descargas y distribución", "Solicitudes de descarga, permisos por "
                                     "clasificación, copias controladas y "
                                     "revocación de distribución."],
        ["Vigencia y vencimientos", "Revisión periódica, renovación, "
                                    "establecimiento y retiro de vigencia, "
                                    "alertas."],
        ["Auditoría e informes", "Registro en MongoDB con HMAC y cadena de "
                                 "hashes, reportes exportables y verificación "
                                 "de integridad."],
        ["Búsqueda y apoyo con IA", "Búsqueda de documentos, resumen con IA y "
                                    "conversión a PDF con control de calidad."],
        ["Configuración administrativa", "Tipos, departamentos, períodos, "
                                         "formatos, ciclo de vida y auditoría "
                                         "desde un panel único."],
    ], anchos=[4.5, 11.5])

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
    nota(doc, "Base técnica: 6–8 semanas. La ventana contractual de 3 meses "
              "incorpora la holgura para pruebas, ajustes, aceptación y la "
              "documentación de la fase.")

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
    h1(doc, "5. Fase 3 — Cumplimiento ISO base (Pendiente)")
    p(doc, "Objetivo: reforzar el cumplimiento ISO con controles base que "
           "reutilizan la infraestructura ya construida (períodos, vigencia, "
           "ciclo de vida, auditoría y reportes).", align="justify")

    h2(doc, "5.1 Alcance")
    tabla(doc, ["Sub-fase", "Entregable", "Esfuerzo base"], [
        ["F3.1", "Política de retención por tipo de documento (configuración).",
         "0,5 semana"],
        ["F3.2", "Retención y disposición final programada (tarea automática y "
                 "estados).", "1,5–2 semanas"],
        ["F3.3", "Informes de auditoría consolidados y exportables.",
         "0,5–1 semana"],
    ], anchos=[2.0, 11.0, 3.0])
    nota(doc, "Base ISO: 2,5–3,5 semanas. Sumada a la Fase 2, el contrato de "
              "tres meses queda con 1,5–4,5 semanas de holgura.")

    h2(doc, "5.2 Criterios de aceptación")
    bullets(doc, [
        "Cada tipo de documento puede definir su política de retención.",
        "Los documentos cumplen su plazo y pasan a disposición final de forma "
        "trazable.",
        "Los informes de auditoría se exportan con los filtros y el rango "
        "solicitados.",
    ])

    h2(doc, "5.3 Fuera de alcance")
    p(doc, "No incluye firma electrónica, sellado de tiempo, gestión de no "
           "conformidades ni control de copias impresas. Esos controles quedan "
           "en la Fase 5 (futura).", align="justify")
    nota(doc, "El cumplimiento operativo real se valida al desplegar en "
              "producción (Fase 4). Esta fase implementa los controles en el "
              "software.")


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
    h1(doc, "7. Fase 5 — Cumplimiento ISO completo (Futura)")
    p(doc, "Objetivo: completar los controles ISO que requieren integración "
           "externa o módulos adicionales.", align="justify")
    h2(doc, "7.1 Alcance")
    bullets(doc, [
        "Firma electrónica y sellado de tiempo.",
        "Gestión de no conformidades.",
        "Control de copias impresas.",
    ])
    p(doc, "Estimación: 6–9,5 semanas. La firma electrónica depende de la "
           "definición de proveedor, certificados y validación legal.",
      align="justify")


def fase6(doc):
    h1(doc, "8. Fase 6 — Experiencia y alcance (Futura)")
    bullets(doc, [
        "Notificaciones por correo y push.",
        "Búsqueda de texto completo y reconocimiento óptico de caracteres.",
        "Paneles de indicadores, reportería y análisis.",
        "Aplicación instalable (PWA) y acceso sin conexión.",
        "Accesibilidad e internacionalización.",
    ])
    p(doc, "Estimación: 6–8 semanas.", align="justify")


def fase7(doc):
    h1(doc, "9. Fase 7 — Integraciones e IA (Futura)")
    bullets(doc, [
        "API pública para integraciones con otros sistemas.",
        "Inicio de sesión único (SSO).",
        "Clasificación y sugerencias asistidas por IA.",
        "Integración con recursos humanos y gestión de la calidad.",
        "Soporte multi-sede.",
    ])
    p(doc, "Estimación: 6–10 semanas.", align="justify")


def cronograma(doc):
    h1(doc, "10. Cronograma y estimaciones")
    p(doc, "Las estimaciones asumen un desarrollador a tiempo completo y "
           "desarrollo asistido por IA, con control de calidad parcial. Se "
           "expresan como rangos.", align="justify")

    h2(doc, "10.1 Resumen de fases")
    tabla(doc, ["Fase", "Contenido", "Estado", "Estimación", "Contrato"], [
        ["1. V1.0", "Gestión documental completa", "Elaborada", "—",
         "Base existente"],
        ["2. Plantillas", "Catálogo, alta, modificación y vínculo",
         "Pendiente", "Base 6–8 sem", "Sí"],
        ["3. ISO base", "Retención, política por tipo e informes",
         "Pendiente", "Base 2,5–3,5 sem", "Sí"],
        ["4. Producción", "LDAP, PostgreSQL, respaldos, migración y "
                          "despliegue", "Propuesta", "8–12 sem", "No"],
        ["5. ISO completo", "Firma, no conformidades y copias impresas",
         "Futura", "6–9,5 sem", "No"],
        ["6. Experiencia", "Notificaciones, búsqueda, paneles y PWA",
         "Futura", "6–8 sem", "No"],
        ["7. Integraciones", "API, SSO, IA y multi-sede", "Futura",
         "6–10 sem", "No"],
    ], anchos=[3.0, 5.2, 2.2, 2.8, 2.4], tam=8.5)

    h2(doc, "10.2 Contrato de tres meses")
    p(doc, "El contrato cubre la Fase 2 y la Fase 3 (base). La suma de esfuerzo "
           "técnico es de 8,5 a 11,5 semanas; el tiempo restante hasta los tres "
           "meses se reserva para pruebas, aceptación, ajustes e imprevistos.",
      align="justify")
    tabla(doc, ["Bloque", "Esfuerzo base"], [
        ["Fase 2 — Plantillas", "6–8 semanas"],
        ["Fase 3 — ISO base", "2,5–3,5 semanas"],
        ["Total técnico", "8,5–11,5 semanas"],
        ["Holgura dentro del contrato", "1,5–4,5 semanas"],
    ], anchos=[9.0, 5.0])

    h2(doc, "10.3 Desglose de la Fase 2")
    tabla(doc, ["Sub-fase", "Entregable", "Esfuerzo"], [
        ["F2.0", "Modelo y migración 0055", "1 semana"],
        ["F2.1", "Catálogo y listados con filtro", "1 semana"],
        ["F2.2", "Alta y aprobación condicional", "1,5 semanas"],
        ["F2.3", "Modificación y eliminación", "0,5 semana"],
        ["F2.4", "Vínculo plantilla ↔ documento", "1 semana"],
        ["F2.5", "Pruebas, calidad y documentación", "1–2 semanas"],
        ["Total", "Base técnica", "6–8 semanas"],
    ], anchos=[2.0, 9.5, 4.5])

    h2(doc, "10.4 Desglose de la Fase 3")
    tabla(doc, ["Sub-fase", "Entregable", "Esfuerzo"], [
        ["F3.1", "Política de retención por tipo", "0,5 semana"],
        ["F3.2", "Retención y disposición programada", "1,5–2 semanas"],
        ["F3.3", "Informes de auditoría consolidados", "0,5–1 semana"],
        ["Total", "Base ISO", "2,5–3,5 semanas"],
    ], anchos=[2.0, 9.5, 4.5])


def riesgos(doc):
    h1(doc, "11. Dependencias y riesgos")
    h2(doc, "11.1 Matriz de riesgos")
    tabla(doc, ["Ámbito", "Riesgo", "Mitigación"], [
        ["Fase 2", "Reclasificar formularios ya llenos como plantillas.",
         "Comando con simulación previa; sin cambios automáticos en la "
         "migración."],
        ["Fase 2", "Cambio de significado del catálogo actual.",
         "Actualizar títulos, badges y pruebas en la misma entrega."],
        ["Fase 3", "Retención y disposición mal configuradas.",
         "Valores por defecto conservadores y validación en pruebas."],
        ["Fase 4", "Calidad de los datos históricos.",
         "Inventario, limpieza y migración por etapas con validación."],
        ["Fase 4", "Dependencia del Active Directory y la red.",
         "Pruebas con cuentas reales y plan de contingencia de acceso."],
        ["Fase 5", "Firma electrónica y validez legal.",
         "Definir proveedor y alcance jurídico antes de estimar en firme."],
        ["Transversal", "Adopción por parte de los usuarios.",
         "Capacitación por rol y manual de usuario actualizado."],
    ], anchos=[2.6, 6.4, 7.0], tam=8.5)

    h2(doc, "11.2 Dependencias entre fases")
    bullets(doc, [
        "La Fase 2 y la Fase 3 reutilizan los modelos y flujos de la V1.0.",
        "La Fase 4 requiere que la Fase 2 y la Fase 3 estén cerradas y "
        "probadas.",
        "La Fase 5 depende de decisiones de proveedor y validación legal.",
        "Las Fases 6 y 7 son evolutivos posteriores a la puesta en producción.",
    ])


def anexos(doc):
    h1(doc, "12. Anexos")
    h2(doc, "12.1 Glosario")
    tabla(doc, ["Término", "Definición"], [
        ["Documento", "Registro controlado con código, tipo, departamento, "
                      "versión, clasificación y estado."],
        ["Plantilla", "Documento en blanco reutilizable que sirve de base para "
                      "crear documentos llenos."],
        ["Planilla de cambios", "Formulario que documenta una modificación y "
                                "su aprobación."],
        ["Formato", "Plantilla institucional del flujo: FOR-001 para "
                    "creación/eliminación y FOR-005 para modificación."],
        ["Vigencia", "Período durante el cual el documento es válido."],
        ["Retención", "Período durante el cual el documento debe conservarse "
                      "antes de su disposición final."],
        ["Clasificación", "Nivel de seguridad: público, interno, confidencial "
                          "o restringido."],
        ["Auditoría", "Registro inmutable de las acciones sobre los "
                      "documentos."],
    ], anchos=[4.0, 12.0], tam=8.5)

    h2(doc, "12.2 Mapeo de conceptos")
    tabla(doc, ["Concepto", "Entidad", "Aprobación"], [
        ["Plantilla (en blanco)", "Documento con marca de plantilla",
         "Condicional según el rol"],
        ["Documento lleno (creación)", "Documento + FOR-001",
         "Cadena de aprobación"],
        ["Planilla de cambios (modificación)", "Planilla + FOR-005",
         "Aprobación de planilla"],
    ], anchos=[5.5, 5.5, 5.0], tam=9)

    h2(doc, "12.3 Trazabilidad")
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
    fase7(doc)
    cronograma(doc)
    riesgos(doc)
    anexos(doc)

    doc.save(str(SALIDA))
    print(f"Generado: {SALIDA}")


if __name__ == "__main__":
    main()
