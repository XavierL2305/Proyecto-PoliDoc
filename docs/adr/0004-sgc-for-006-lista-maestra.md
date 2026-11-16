# ADR 0004 — SGC-FOR-006: Lista Maestra de Documentos

## Contexto

ISO 9001:2015 §7.5.3.2 exige mantener una **lista maestra** de la información
documentada (qué existe, su código, título, versión/edición, permanencia,
ubicación, distribución, responsable y empresa). El SGC la mantenía en un
Excel con ~1400 filas, mezclando documentación controlada (con código) y
**registros/soportes de fuente** que no tienen código (facturas, tickets,
órdenes, expedientes, etc.).

POLIDOC ya registra los `Documento` controlados, pero derivar el XLSX en vivo
en cada cambio (distribución, propietario, versión…) es incorrecto: la lista
quedaría inestable y se perdería la curaduría manual de las columnas precisas.

## Decisión

1. **Documentos sin código = entidad separada, NO un tipo ISO.**
   `DocumentoSinCodigo` (identidad `uuid`, **sin `codigo`**) con dos ámbitos:
   - `sgc`: soportes/registros de fuente institucionales → aparecen en la
     Lista Maestra como **Ext** con "N/A".
   - `personal`: archivos privados del usuario → **no** entran a la lista.
   No se añade un `TipoDocumento` ISO nuevo (el catálogo y el procedimiento
   del SGC no se tocan) y no se relaja `Documento.codigo`.
   Permisos: crear SGC = admin/jefe; personal = cualquier usuario (dueño).
   La bandeja aparece con ≥1 registro; el botón de crear está siempre.

2. **La Lista Maestra es un registro vivo persistido, no una vista calculada.**
   Se reutiliza `RegistroFormato` (contexto `sistema`/`lista-maestra`) con las
   filas en `respuestas['documentos']` (JSON). Cada fila lleva `_clave`,
   `_origen_tipo` (`documento`/`sin_codigo`/`manual`) y `_manual` (columnas
   editadas por una persona).

3. **Actualización por señales + sincronización explícita.**
   `post_save`/`post_delete` de `Documento` y `DocumentoSinCodigo` (tras el
   commit, idempotentes) insertan/actualizan/quitan la fila. El botón
   **“Sincronizar (combinar)”** refresca solo las columnas con `origen` y
   **respeta `_manual`**; las filas manuales nunca se tocan. Cambiar la
   distribución o el propietario **no** regenera el XLSX.

4. **Columnas configurables en JSON.**
   `CampoFormato.config_tabla.columnas` define `clave`, `etiqueta`, `tipo`
   (`texto`/`textarea`/`combo`/`select`/`fecha`/`si_no`), `celda`, `origen` y
   `opciones`. Se editan desde el Constructor; los `combo` usan **"respuestas
   recientes"** (valores ya usados) + escritura libre, sin configurar
   dropdowns a mano. Un cambio de columnas de la norma ISO no exige código
   (solo actualizar la plantilla impresa si cambia la cabecera).

5. **Acceso por persona.**
   `AccesoListaMaestra` concede acceso (ver, editar, sincronizar, regenerar,
   descargar) a **personas concretas** desde la sesión del administrador; el
   admin siempre tiene acceso. La entrada del menú se muestra a quien puede
   verla.

6. **Generación solo bajo demanda (hoja continua).**
   El XLSX/PDF se genera al pulsar el botón y se guarda en el registro. Usa
   una **hoja continua**: si supera las 22 filas de la plantilla, agrega filas
   copiando estilo/borde, repite la cabecera al imprimir (`print_title_rows`)
   y conserva la hoja instructiva `SGC-IDL-012`. Sin firma SES (la plantilla
   no tiene bloque de firmas).

7. **Purga de documentos personales.**
   Avisos a los **60 y 80 días** sin iniciar sesión al jefe directo (o
   administradores) y por correo al usuario; **purga a los 90** solo de
   `ambito='personal'`. Se reinicia al iniciar sesión. Nunca toca los
   soportes SGC ni los documentos ISO. Umbrales configurables.

## Consecuencias

- Migraciones `documentos/0065` (`DocumentoSinCodigo`), `0066`
  (`AvisoPurgaPersonal`) y `0067` (`AccesoListaMaestra`), y
  `autenticacion/0012` (tipo de notificación `documento_personal_aviso`).
- Tareas nuevas: `purgar_documentos_personales` (beat diario) y
  `generar_for_006_lista`. Comando `purgar_personales --dry-run`.
- Las columnas automáticas quedan desactualizadas hasta la próxima
  sincronización (por diseño: la lista impresa es estable).
- Las filas manuales no se eliminan automáticamente; se gestionan en la vista.
- El PDF puede requerir la reparación automática de maquetación (imagen del
  instructivo recortada tras el round-trip de openpyxl); el QA la repara. Es
  el mismo comportamiento que en FOR-003/004.
- Tests: `tests_documentos_sin_codigo` (17), `tests_purga_personales` (8),
  `tests_for_006` (34) y `tests_auditoria_filtros` (6).
- Pendiente: validar con el SGC si el procedimiento debe mencionar los
  "soportes/registros de fuente" (no requiere un tipo ISO nuevo).
