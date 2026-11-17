# ADR 0006 — SGC-FOR-009: Minuta de reunión

## Contexto

El SGC necesitaba evidenciar las reuniones (incluida la Revisión por la
Dirección) con una **minuta** en el formulario oficial `SGC-FOR-009`. La
plantilla trae dos hojas: el formulario y el instructivo `SGC-IDL-015` (dos
páginas apiladas en la misma hoja, con su propio bloque de firmas
Elaborado/Revisado/Aprobado). El formulario de la minuta **no** tiene bloque de
firmas: se llena digital, se imprime y se firma en físico.

La plantilla tiene cajas de tamaño fijo (objetivo, lugar, fecha, responsable,
duración), 7 filas de asistentes, bloques de tema/conclusiones/acuerdos y una
fila de responsables de aprobación. El Nº de reunión es consecutivo, salvo las
reuniones no planificadas (`001 - NPR`).

## Decisión

1. **Captura con el motor dinámico** (`Formato` + `CampoFormato` +
   `_formato_dinamico.html`) y **un `RegistroFormato` por minuta** con contexto
   `minuta`/uuid. No se crea un modelo nuevo; `respuestas` (JSON) guarda los
   campos, incluida la tabla de asistentes.
2. **Versionado** con `VersionRegistroFormato`: se crea un snapshot al alta y
   en cada edición con cambios reales (respuestas + fecha + estructura del
   formato + SHA-256). El detalle muestra el historial read-only; una edición
   sin cambios no genera versión ni invalida el XLSX/PDF.
3. **Validaciones**: `reunion_no` acepta `^\d{1,4}$` o `^\d{3}\s*-\s*NPR$`
   (normaliza a `001 - NPR`); exige al menos un asistente y los campos
   marcados como requeridos. El título se autogenera
   `Minuta {reunion_no} — {dd/mm/aaaa}`.
4. **Responsables de aprobación** como texto libre, **prefijado desde la
   minuta más reciente** (cualquier año) y editable.
5. **Metadatos y vínculo FOR-007**: `tipo_reunion`, `anio` y `mes` no se
   imprimen; permiten clasificar la minuta. Si es Revisión por la Dirección y
   el checkbox (marcado por defecto al crear) está activo, se llama
   `for_007.marcar_ejecutado(anio, mes)`, que convierte P→V los aspectos del
   mes y crea una versión del programa. El checkbox solo se muestra (y el
   servidor solo ejecuta) si el usuario tiene `AccesoFormato` **editar** sobre
   FOR-007; el servicio revalida el permiso. La acción se audita como
   `programa_revision_ejecutado_desde_minuta` con el UUID de la minuta.
   `marcar_ejecutado` lee y transforma la lista bajo `select_for_update`, de
   modo que un guardado concurrente del FOR-007 no se pierda.
6. **Acceso** `AccesoFormato` ver/editar por persona (admin siempre); menú
   “Minutas” visible con nivel ver. Las minutas se excluyen de
   `/documentos/registros/` (`CONTEXTOS_VISTA_EXCLUSIVA`).
7. **Export dedicado** `services/for_009.py` sobre la plantilla oficial:
   escribe en las cajas exactas y **mergea/wrapea** las cajas que el Excel
   tenía sin merge (`C13:J14`, `B16:F16`, `B18:C18`, `B20:H20`, `B22:C22`) y
   el bloque de responsables; los merges se validan con `CellRange` para no
   solapar la plantilla y las escrituras evitan `MergedCell`. Las alturas de
   objetivo/tema/conclusiones/acuerdos/responsables se calculan según el texto
   (tope Excel 409 pt por fila); si un texto desborda, se copia completo en
   `SGC-FOR-009 (Anexo)` para no truncarlo. **7 asistentes/hoja** y hojas de
   continuación `SGC-FOR-009 (2)…` con `PAGINA k DE n`; conserva
   `SGC-IDL-015` (imágenes, dos páginas y bloque de firmas del instructivo).
   XLSX/PDF bajo demanda; editar invalida ambos. **Sin SES**.
8. **Módulo común** `services/xlsx_comun.py` (`copiar_hoja` + conversión a
   PDF), compartido con FOR-003/004/006/007.

## Consecuencias

- No requiere migración: el formato se integra con
  `integrar_formatos_sgc --solo=009 --apply` (la plantilla se copia a MEDIA).
- Rutas `/documentos/minutas/…` y página de acceso propia; el admin gestiona
  concesiones con el servicio genérico `acceso_formato`.
- Los permisos se validan antes de buscar el UUID (evita enumeración); el año
  debe estar entre 2000 y 2100.
- Riesgos aceptados: el instructivo dice `PAGINA: 1 DE 2` en su segunda página
  (typo oficial, se preserva); la maquetación del PDF depende de LibreOffice
  (igual que los demás FOR) y la minuta se firma en físico.
- **Decisión pendiente**: la minuta queda en `borrador`; falta un estado
  “Emitida/cerrada” que bloquee ediciones tras imprimir/firmar o fuerce una
  nueva versión. Se anota en backlog, no se implementa en esta fase.
- Backlog: estado Emitida/cierre, restaurar versiones del FOR-007 y un acceso
  genérico reutilizable para todos los formatos.
- Tests: `tests_for_009` (30) con plantilla sintética; validación del Nº,
  prefijo de responsables, hook P→V con permiso/auditoría, versionado,
  acceso ver/editar sin enumeración, rango de año, exclusión de registros,
  export a celdas/merges, alturas y anexo de texto, continuación de 10
  asistentes e IDL. `tests_for_007` cubre el lock y el permiso del hook.
