# Checklist de sincronización — Diagramas UML

Control para que los diagramas `.mmd` reflejen siempre el código real.
La fuente de verdad es el **código**; los diagramas son su representación.
La tabla del `README.md` es el índice canónico (el PDF se arma a partir de ella).

> Regla: si tu commit cambia algo de la columna izquierda, actualiza el
> diagrama de la columna derecha **en el mismo PR** y regenera los renders.

## Matriz «si cambias X → actualiza Y»

| Si cambias… | Actualiza… |
|---|---|
| `Documento.ESTADO_CHOICES` / `CLASIFICACION_CHOICES` | `01`, `12` |
| `TipoDocumento` / `Periodo` / `VersionDocumento` / `ReferenciaDocumento` / `RenovacionVencimiento` | `01` |
| Índice, secciones y vínculos del `ManualCalidad` | `01` |
| `PerfilUsuario.ROL_CHOICES`, `Departamento`, `SubArea` | `02`, `07` |
| `DelegacionJefatura` (modelo o reglas) | `02`, `07`, `15` |
| `Formato` / `SeccionFormato` / `CampoFormato` / `FormatoPublicacion` | `03` |
| `SolicitudCambio` / `PlanillaCambios` / `DocumentoAsociadoPlanilla` | `03`, `09`, `13` |
| `SolicitudAprobacionDocumento` / `AprobacionPlanilla` (acciones, estados) | `04`, `08` |
| Reglas de cadena 1/2 pasos (`jefatura.py`, `determinar_evaluador`) | `04`, `08`, `09` |
| `AprobacionDescarga` (roles, escalado 48 h, `esperando`) | `04`, `05`, `14` |
| `SolicitudDescarga` / reglas por clasificación (`interno`/`confidencial`/`restringido`) | `05`, `10`, `14` |
| `DistribucionDocumento` / `SolicitudAccesoDocumento` / `AsignacionDocumento` | `05`, `07` |
| `services/descargas.py` (flujo secuencial, `_promover_autorizador`) | `10`, `14` |
| `services/cambios_estado.py` (inhabilitar, reactivar, renovar) | `11`, `12` |
| `services/reasignacion.py` (reasignar/desasignar autorizadores GG) | `08`, `09`, `10` |
| `apps/auditoria/` (modelos, `storage.py`, buffer Mongo, `reprocesar_logs_buffer`) | `06`, `16` |
| `ResumenIAUso` / `ConversionPDFDiagnostico` | `06` |
| Casos de uso por actor (nuevas vistas/permisos) | `07` |
| `tasks.py` / `CELERY_BEAT_SCHEDULE` (`escalar_aprobaciones_vencidas`, `verificar_vencimientos`) | `07`, `12`, `16` |
| `settings.py` (stack, `AUDIT_FAIL_CLOSED`, DB) | `16` |
| `docker-compose.yml` (servicios reales: rabbitmq, redis, mongodb, web, celery_*, mailpit) | `17` |

*(Si tu cambio no encaja en la matriz, añade la fila en el mismo PR.)*

## Definition of Done (por PR)

- [ ] `make` renderiza los SVG sin errores.
- [ ] `make pdf` genera `out/POLIDOC-UML-001_v1.0.pdf`.
- [ ] `make sync-check` pasa.
- [ ] El `.mmd` afectado está actualizado y revisado visualmente.
- [ ] Si el diagrama es nuevo: está en el índice del `README.md` (el PDF lo toma de ahí automáticamente).
- [ ] SVG/PNG/PDF regenerados (nunca editados a mano).
- [ ] Si la versión del documento cambia: `make pdf UML_VERSION=x.y`.
- [ ] Notas de `classDiagram` usan `<br/>` (no `\n` literal).

## Recordatorios de estilo (ver `README.md` §Convenciones)

1. **Nombres reales del código** (`AprobacionDescarga`, no «Aprobación de Descargas»).
2. **Estados persistidos ≠ condiciones derivadas ≠ fases conceptuales**
   (`ESTADO_CHOICES` vs «vencido» vs retención/destrucción).
3. **Un artefacto por archivo**, prefijo numérico para el orden de lectura.
4. Los `.svg`/`.png`/`.pdf` de `out/` **no se editan a mano**: se regeneran.
