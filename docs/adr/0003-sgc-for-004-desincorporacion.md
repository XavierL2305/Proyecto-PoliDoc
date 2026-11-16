# ADR 0003 — SGC-FOR-004: Acta de desincorporación de archivos inactivos

## Contexto

ISO 9001:2015 §7.5.3.2 exige controlar la retención y disposición de la
información documentada. POLIDOC ya marca un documento como `obsoleto`
(inhabilitación, vencimiento o borrado de la última versión), pero no había
un acto formal que documentara la **desincorporación** de los registros
inactivos que un documento (padre) mantiene relacionados.

El formato oficial `SGC-FOR-004` ("ACTA DE DESINCORPORACION DE ARCHIVOS
INACTIVOS", v1/e1) recoge la fecha, la comisión (nombre/cargo), el propósito,
la lista de registros inactivos y el área, y se firma por conformidad.

## Decisión

1. **Disparador en la ficha del padre.** Cuando uno de sus
   `documentos_relacionados` pasa a `obsoleto`, la ficha ofrece generar el
   acta. El aviso se dispara desde **las tres rutas** que inactivan un
   documento: `SolicitudAprobacionDocumento.aplicar_cambio` (inhabilitar), la
   tarea `verificar_vencimientos` (que usa `.update()` y **no** emite
   señales) y `aplicar_eliminacion_version`. No se usa un `post_save` como
   único disparador porque dos de esas rutas no lo emiten.

2. **Modelo propio (migración 0064).**
   - `ActaDesincorporacion`: `padre` (`SET_NULL`), snapshots de
     código/nombre/departamento, `fecha`, `proposito`, `estado`
     (`borrador`/`pendiente_firma`/`firmada`/`aplicada`/`anulada`), firmas SES
     Elaborado/Aprobado, XLSX/PDF, `aplicada_en`, `anulada_en/motivo` y
     firmantes asignados (`elaborado_por`, `aprobado_por`).
   - `ParticipanteActa` y `RegistroDesincorporado` con snapshots (el acta
     sobrevive al borrado del registro).
   - **Constraint parcial**: una sola acta *abierta* (`borrador` o
     `pendiente_firma`) por padre; el histórico cerrado es ilimitado. La
     vista reanuda el acta abierta en vez de crear otra.

3. **Firma híbrida con el motor SES único.** Spec
   `FirmaActaDesincorporacion` (`origen_tipo='acta_desincorporacion'`).
   El rol se guarda **explícito** en `FirmaElectronica.datos['rol']`
   (`elaborado`/`aprobado`); no se infiere del usuario. La misma persona en
   ambos roles firma **una sola vez** y la firma se enlaza a los dos bloques.
   Métodos: PIN interno y token externo (`InvitacionFirma`, 7 días, un uso).
   El hash firmado es el **JSON canónico del acta**, no el PDF regenerado.

4. **La relación se retira al completar las firmas.** `aplicar_acta` corre
   con `select_for_update` (idempotente), exige ambas firmas y **revalida**
   que los registros sigan `obsoleto`; retira las relaciones del padre y deja
   el acta `aplicada`. Reactivación/renovación de un registro o su
   eliminación (`pre_delete`) anulan el acta abierta.

5. **Generación (`services/for_004.py`) sin alterar el layout oficial.**
   - Hoja `SGC-FOR-004`: fecha en letras (fila 13), participantes (16–19),
     propósito (21), registros (23–31), área (33), firmas (40–41) y sello SES
     (42) solo con ambas firmas.
   - Multifila: >9 registros → hojas `SGC-FOR-004 (2)…` con `PAGINA k DE n`;
     la hoja instructiva `SGC-IDL-010` se conserva (imágenes incluidas).
   - **Capacidad de participantes = 4** (filas 16–19; la fila 15 es el
     encabezado). No se insertan filas para no desplazar el resto del
     formulario.
   - PDF vía LibreOffice.

6. **Formato registrado** `SGC-FOR-004` (`aplica_*=False`), plantilla
   copiada a MEDIA con `integrar_formatos_sgc --solo=004 --apply`.

7. **Guardas de reincorporación.** Una relación retirada por un acta
   `aplicada` no se vuelve a enlazar desde `aplicar_planilla_al_documento` ni
   desde las referencias de la edición (`filtrar_relaciones_bloqueadas`, con
   aviso).

## Consecuencias

- Migración `0064` (documentos) y `0011` (nuevos tipos de `Notificacion`).
- Notificaciones nuevas: `desincorporacion_pendiente` y
  `desincorporacion_aplicada`.
- El acta es evidencia cerrada: reactivar el registro después de aplicarla
  **no** vuelve a enlazarlo (riesgo residual aceptado). Un administrador
  puede regenerar una relación desde la edición, pero perdería la coherencia
  con el acta (se registra el bloqueo).
- Los firmantes asignados pueden ver/firmar el acta aunque no tengan permiso
  de lectura del documento padre.
- `tests_for_004` (46) con plantilla sintética (incluye hoja instructivo)
  para CI, porque `documentacion/` no está versionado.
- Pendiente: validar con Asesoría Legal el texto de consentimiento del enlace
  de firma y confirmar la política de retención por tipo.
