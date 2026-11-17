# ADR 0005 — SGC-FOR-007: Programa de Revisión por la Dirección

## Contexto

ISO 9001:2015 §9.3 exige planificar y evidenciar las **revisiones por la
dirección**. El SGC llevaba el programa en un Excel: aspectos del sistema a
evaluar, información documentada a revisar y un calendario de 12 meses donde
se marca la reunión (planificada/ejecutada), con firmas de “Conforme” de la
Presidencia de la Junta y la Gerencia General.

El número y el texto de los aspectos **cambian por año**, y el formulario debe
poder reimprimirse/firmarse en físico. No es una vista derivada de
`Documento`.

## Decisión

1. **Registro vivo anual** (`RegistroFormato` con contexto
   `programa_revision`/`<año>`) y **constraint condicional** (un programa por
   formato/año). Los datos (aspectos + marcas + Conforme) viven en
   `respuestas` (JSON); no se crea un modelo nuevo.
2. **Aspectos N variables por año.** La plantilla de 14 filas es solo
   *seed/prototipo*: cada año puede tener más o menos aspectos y otros textos.
   - Al crear un año se copia del **año elegido** (por defecto el anterior); si
     no hay, del **seed oficial** del formato.
   - Se pueden **agregar/quitar/editar** filas. Lo edita quien tenga
     `AccesoFormato` con nivel **editar**; el admin gestiona **estructura/seed**
     (columnas, celdas, seed, tope), **no** la cantidad de filas.
   - Acción “Copiar aspectos de [año]” para re-sembrar un año existente
     (conserva marcas por `clave`; crea versión).
3. **Marcas P/V** exclusivas (`'' | 'P' | 'V'`), según la leyenda impresa.
   `V` implica `P`; se guardan `planificado_en`/`ejecutado_en` por mes.
   *(Pendiente de confirmar con el SGC la leyenda P/V frente a X/círculo del
   instructivo; no se corrige el typo `Ï` ni la numeración de aspectos.)*
4. **Versiones** (`VersionRegistroFormato`): cada guardado/seed crea un
   snapshot inmutable con `respuestas` + **configuración de estructura** +
   SHA-256. Historial solo lectura (restaurar queda para una fase posterior).
   Estado `borrador`/`publicado` (aprobado); editar un publicado vuelve a
   `borrador`.
5. **Acceso** `AccesoFormato` (renombrado desde `AccesoListaMaestra`) con nivel
   **ver/editar** por persona; admin siempre. Se conserva la tabla original.
6. **Export XLSX/PDF** desde la plantilla oficial con **layout dinámico**:
   reconstruye el bloque de aspectos para N filas (estilos/merges), desplaza
   leyenda/“CONFORME”/firmas, **tope 30 filas/hoja** y hojas de continuación
   `SGC-FOR-007 (2)…` con `PÁGINA k DE n`; conserva la hoja `SGC-IDL-013`.
   “Conforme” imprime nombres editables y fecha en día/mes/año. **Sin SES**: se
   llena digital, se imprime y se firma en físico.
7. **Módulo común** `services/xlsx_comun.py` (`copiar_hoja` + conversión a
   PDF) compartido con FOR-003/004/006.

## Consecuencias

- Migración `documentos/0068` (rename de acceso, `VersionRegistroFormato`,
  constraint anual). El formato se integra con
  `integrar_formatos_sgc --solo=007 --apply` (no requiere migración de datos).
- Hook `marcar_ejecutado(anio, mes)` listo para enlazar con la Minuta
  (SGC-FOR-009) sin acoplar ambos flujos todavía.
- Riesgos aceptados: confirmar con el SGC P/V vs X/círculo y numeración;
  el PDF puede requerir la reparación automática de maquetación (round-trip de
  openpyxl), igual que FOR-003/004; N muy grande usa hojas de continuación.
- Tests: `tests_for_007` (24) con plantilla sintética; cubren seed por año,
  marcas, versiones, unicidad, acceso ver/editar, export N=3/14/31,
  continuación, Conforme e IDL.
