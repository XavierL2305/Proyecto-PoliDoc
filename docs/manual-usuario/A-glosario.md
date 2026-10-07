# Anexo A. Glosario

Términos del sistema de calidad y de la interfaz de POLIDOC, en orden
alfabético.

| Término | Significado |
|---|---|
| **Activo** | Estado del documento aprobado, vigente y distribuible. Solo cambia mediante Planilla de Cambios o inhabilitación |
| **Aprobador** | Quien resuelve un paso de la cadena de aprobación |
| **Asignación** | Permiso que un jefe otorga a un coordinador sobre un documento, con las facultades leer / editar / eliminar |
| **Auditoría** | Registro inmutable de quién hizo qué y cuándo (login, descargas, aprobaciones, eliminaciones). En ISO 27001/15489 |
| **Autorizador** | Segundo paso de la cadena: el jefe de Gerencia General (GG). Solo él activa el documento |
| **Bandeja de Aprobaciones** | Pantalla del jefe/administrador con las solicitudes pendientes, aprobadas y rechazadas |
| **Borrador** | Estado de un documento en elaboración: no está aprobado ni es visible para los demás |
| **Cadena de aprobación** | Los dos pasos obligatorios: Evaluación → Autorización |
| **Clasificación de seguridad** | Nivel de confidencialidad: Público, Interno, Confidencial o Restringido. Decide quién ve y quién descarga |
| **Código** | Identificador único del documento, por ejemplo `TI-PRG-001` (departamento-tipo-secuencia). Excepción: DPR numerado globalmente |
| **Coordinador** | Rol que gestiona documentos propios y los que su jefe le asigna |
| **Destrucción** | Eliminación física del documento cuando venció su período de retención. Solo administrador |
| **Distribución** | Entrega de una copia controlada del documento a usuarios concretos; puede revocarse |
| **Doble control** | Aprobación que requiere dos personas (jefe + administrador), aplicada a los documentos *Restringidos* |
| **DPR** | Descripción de Procesos: tipo con código global, solo creado por jefes y visible por cualquier jefe |
| **En Revisión** | Estado del documento que está dentro de la cadena de aprobación |
| **Evaluador** | Primer paso de la cadena: jefe de departamento (o jefe par si el solicitante también es jefe) |
| **Formato** | Plantilla configurable (secciones y campos) que se aplica a determinadas operaciones; lo gestiona el administrador |
| **GG** | Gerencia General: departamento cuyo jefe actúa como Autorizador |
| **Habilitar / Inhabilitar** | Reactivar un documento obsoleto / pasarlo a obsoleto (jefe o administrador; los demás solicitan eliminación) |
| **Jefe de departamento** | Rol que gestiona todo su departamento: aprueba, asigna, distribuye, transfiere e inhabilita |
| **Obsoleto** | Estado del documento vencido o inhabilitado: solo lectura histórica, no se distribuye |
| **Período de retención** | Tiempo mínimo que un documento se conserva tras dejar de estar activo, antes de poder destruirse |
| **Planilla de Cambios** | Trámite formal para modificar un documento activo; al aprobarse, genera la nueva versión |
| **Propietario** | Usuario responsable del documento; el que lo creó salvo que se haya transferido |
| **Revisión (ISO)** | Contador del ciclo de revisión del documento (0, 1, 2...); se incrementa en cada activación. No confundir con *Versión* |
| **Solicitud de acceso** | Petición de un jefe a otro departamento para ver un documento que no es de su ámbito |
| **Solicitud de descarga** | Petición formal con motivo para descargar un documento que no puedes bajar directamente |
| **Trazabilidad** | Capacidad de reconstruir el historial de un documento: versiones, aprobaciones, descargas y distribuciones |
| **Transferencia de propiedad** | Cambio del propietario del documento a otro miembro del mismo departamento |
| **Versión** | Contador del contenido del documento (v1, v2...); sube cuando se aprueba una Planilla de Cambios |
| **Vigencia** | Período durante el cual un documento está vigente; se calcula desde la fecha de vigencia más el período (en meses) |
| **Vencimiento** | Día en que la vigencia termina. A los 7 días se avisa; al vencer, el documento pasa a *Obsoleto* |
| **Vencido / Vence pronto / Vigente** | Insignias de estado de vigencia: roja, amarilla y verde |

---

**Anterior:** [09. Solución de problemas](09-troubleshooting.md) ·
**Volver al [índice](README.md)**
