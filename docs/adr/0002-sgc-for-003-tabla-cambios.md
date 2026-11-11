# ADR 0002 — SGC-FOR-003: Tabla de cambios y modificaciones

## Contexto

ISO 9001:2015 §7.5.3.2 exige controlar los cambios de la información
documentada. El SGC ya generaba SGC-FOR-001 (alta/baja), SGC-FOR-002
(distribución) y SGC-FOR-005 (planificación de cambios), pero no la **tabla
histórica de cambios** (SGC-FOR-003) que acompaña a cada documento.

La modificación se gestiona con `PlanillaCambios` (FOR-005): al aprobar se
aplica al documento (`aplicar_planilla_al_documento`) y se crea la nueva
`VersionDocumento`.

## Decisión

1. **Modelo propio `CambioDocumento`** (no reutilizar `VersionDocumento`):
   permite varias filas por modificación (multifila), autocompletar campos y
   autorizar por separado sin tocar el historial de archivos.
   - Campos: documento, planilla (nullable), nº página, versión, descripción,
     fecha, realizado por, autorizado por, fecha/firma de autorización, orden.
   - Histórico = filas con `autorizado_por` no nulo. Las de planillas
     rechazadas quedan como evidencia pero no se exportan.
2. **UI dentro de la planilla de modificación** (misma pantalla del FOR-005),
   con el estilo de tabla del motor de formatos: histórico autorizado
   (solo lectura) + filas nuevas editables (agregar/quitar). El borrador
   persiste las filas y al reingresar se precargan; si se entra sin
   `planilla_uuid`, se reanuda el borrador propio del documento.
3. **Formato registrado** `SGC-FOR-003` en Configuración → Formatos
   (`aplica_*=False` → “Planilla automática”), con la plantilla oficial
   copiada a MEDIA por `integrar_formatos_sgc --solo=003`.
4. **Autorización automática** en `resolver_aprobacion_planilla` (paso final,
   no plantillas): el autorizador completa `autorizado_por`/fecha y se vincula
   la firma SES. La generación se programa tras el commit (Celery con
   fallback en línea) y se guarda en `PlanillaCambios.for_003_xlsx/pdf`.
5. **Generación** (`services/for_003.py`):
   - Plantilla oficial sin cambios de layout; filas 12–23, columnas
     A/B/C/F/G/I respetando los merges (C:E, G:H, I:J).
   - Sello SES impreso debajo de la tabla (fila 25).
   - `PAGINA k DE n`; si hay más de 12 cambios se agregan hojas
     `SGC-FOR-003 (2)`… conservando intacta la hoja del instructivo
     SGC-IDL-009.
   - PDF vía el conversor del proyecto (LibreOffice).
6. **Descargas**: desde el detalle de la planilla (snapshot) y desde la ficha
   del documento (histórico acumulado, generado al vuelo si falta).

## Consecuencias

- Migración `0063` (`CambioDocumento` + `PlanillaCambios.for_003_*`).
- El flujo de modificación gana una sección obligatoria (≥1 fila al enviar)
  sin cambiar los pasos de aprobación existentes.
- El FOR-003 se puede imprimir/distribuir por hojas; cada hoja lleva su
  contador de páginas y la última el sello de firma.
- Tests con plantilla sintética (incluye hoja instructivo) para CI, porque
  `documentacion/` no está versionado.
- Pendiente: campaña de revisión masiva (§34) y validación legal de avisos.
