# ADR 0001 — Modelo documental: Documento, Formato y Registro

- **Estado:** Aceptado
- **Fecha:** 2026-11-10
- **Contexto:** POLIDOC (SGC, ISO 9001:2015 §7.5.3)

## Problema

En el apartado de documentos se intentó crear más de un `Documento` con el
mismo código (por ejemplo `SGC-FOR-010` aplicado a `DPR-005` y a `DPR-006`).
El sistema lo impide porque `Documento.codigo` es único, y además
`crear_documento` valida duplicados. La causa no es la validación: es que se
está usando `Documento` para representar **registros**, no documentos
controlados.

## Decisión

Se distinguen tres capas y se mantienen sus reglas de unicidad:

| Capa | Entidad | Código | Identidad | Ciclo de vida |
|---|---|---|---|---|
| Documento controlado | `Documento` | **único** (`DPR-005`, `TI-PRG-001`) | `uuid` | versión/edición, aprobación, distribución, retención, obsolescencia |
| Formato / plantilla | `Formato` | **único** (`SGC-FOR-010`) | `pk`/`uuid` | versión/edición del formulario en blanco |
| Registro | `RegistroFormato` (nuevo) | **repetible** (el del formato) | `uuid` | estado, aprobación con PIN, archivo, evidencia |

Reglas derivadas:

1. **No se relaja** `Documento.codigo`. Sigue siendo único.
2. El código que se repite es el **código del formato**, no el del documento.
   Un registro muestra `SGC-FOR-010` y se distingue por su contexto (DPR),
   fecha y título; internamente lo identifica su `uuid`.
3. Un registro **no crea** un `Documento` ni entra en el catálogo de
   documentos controlados.
4. Al cambiar la edición de un formato, **los registros existentes no se
   regeneran**: quedan como evidencia de la edición anterior. Los nuevos
   registros usan la nueva edición.
5. Los documentos controlados afectados por un cambio de norma se gestionan
   con **campañas de revisión** (nuevas versiones + aprobación + distribución),
   nunca con un "botón regenerar" masivo (ver ADR/§34 de CONTEXT).

## Por qué (y alternativas descartadas)

- **Permitir códigos repetidos en `Documento`**: desbloquea hoy, pero mezcla
  documentos controlados con registros, vuelve ambiguas las referencias por
  código (`ReferenciaDocumento`, selectores, IA) y dificulta una migración
  posterior. Descartado.
- **Código compuesto (`SGC-FOR-010-DPR-005`)**: mantiene unicidad, pero el
  usuario necesita que el código del formato se repita y no distorsiona el
  nombre del formato SGC. Se puede usar como etiqueta de apoyo, no como
  identidad. Descartado como solución principal.
- **Reutilizar `FormatoRespuesta`**: su constraint es "una respuesta por
  origen" (`origen_tipo`, `origen_id`), pensada para la planilla FOR-002 por
  lote; un registro necesita **muchos** por formato/contexto. Se crea un
  modelo propio (`RegistroFormato`). Descartado reutilizarlo.

## Mapa del estado actual

- `Documento`: documento controlado; `codigo` único; `es_plantilla` marca una
  plantilla (documento en blanco subido). `plantilla_origen` enlaza el
  documento generado.
- `Formato`: formulario configurable (secciones/campos + XLSX) con
  `codigo` único. Ej. SGC-FOR-001/002/005/010.
- `SolicitudCambio`: planilla rellenada de un `Formato` para un `Documento`
  (creación/modificación/eliminación) con aprobación y archivos.
- `FormatoRespuesta`: respuestas de un formato para un origen que no es
  `SolicitudCambio` (p. ej. la planilla FOR-002 de un lote).
- `PlanillaCambios`: planilla FOR-005 de modificación, con cadena de
  aprobación.

## Consecuencias

- Nuevo modelo `RegistroFormato` (migración `0061`) y `Formato.permite_registros`.
- UI "Registros" en la ficha del documento de contexto y listado global.
- Aprobación de registros con el motor SES (`firmar()`), nuevo origen
  `registro_formato`; aprobar exige PIN, rechazar no firma.
- Migración futura (P2) de "documentos-registro" heredados.
- Documentación: `CONTEXT.txt` §32-§35.

## Fuentes consultadas

- Cognidox, *Document Control Software for ISO 9001 & ISO 13485: A Guide*
  (pirámide de control documental, versión maestra, versiones retenidas,
  firmas electrónicas).
- P3LogiQ, *9 Best ISO 9001 Software* (panorama de MasterControl, Veeva,
  ETQ, AssurX, Ideagen, Greenlight Guru, Unifize, TrackWise).
- Qwebdocuments / Focusys (automatización documental, workflows,
  auditorías y gestión de riesgo para ISO/GMP/HACCP).

## Addendum P1 — Distribución y acuse de registros

Decisiones tomadas al implementar la distribución de registros (`0062`):

- **Un solo token por destinatario externo.** `DistribucionRegistro` **no**
  duplica `token_hash`/`expira`: reutiliza `InvitacionFirma` (SHA-256, 7
  días) como enlace de firma **y** de descarga (`/firmar/<token>/descargar/`).
  La descarga funciona hasta la caducidad, incluso después de firmar; el
  "un solo uso" aplica solo a la firma. Si la distribución se revoca, el
  enlace se bloquea (404) aunque el token siga vigente.
- **Acuse = firma SES.** Interno con PIN (`FirmaDistribucionRegistro`,
  `requiere_otp=False`); externo con token + consentimiento. La firma es la
  evidencia del acuse; IP/UA viven en `FirmaElectronica` (sin duplicar).
- **Impreso con acuse escaneado.** Se registra la entrega y se sube el
  escaneo (PDF/JPG/PNG validados por magic bytes). Se añadió
  `acuse_archivo` también a `DistribucionDocumento` por paridad.
- **Avisos únicos por clasificación** en `services/avisos.py` (texto plano,
  HTML y banner de correo). Textos de confidencial/restringido pendientes de
  validación por Asesoría Legal.
- **WhatsApp no se integra.** Interfaz `CanalMensajeria` + `CanalWhatsApp`
  desactivado por defecto (proveedor oficial por variables de entorno); no
  se usan bots no oficiales de sesión/QR.
- **Sin IP del AD en el código.** `AUTH_LDAP_SERVER_URI` queda vacío por
  defecto y se configura en `.env`; con URI vacío el `LDAPBackend` no abre
  ninguna conexión.
