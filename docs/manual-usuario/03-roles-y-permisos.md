# 03. Roles, permisos y clasificación de seguridad

## Los cuatro roles

Tu rol viene del grupo al que perteneces en el directorio activo y lo ve
indicado en la esquina superior derecha, junto a tu nombre.

### Cómo verificar tu rol

1. Mira la **esquina superior derecha** de cualquier pantalla: bajo tu nombre
   aparece tu rol (Trabajador, Coordinador, Jefe de Departamento o
   Administrador).
2. Entra a **Mi Perfil**: el campo *Departamento* muestra el área que
   gestionas o a la que perteneces.
3. Si alguno de los dos está mal, solicita la corrección al administrador:
   no se edita en POLIDOC, se corrige en el directorio activo y el sistema lo
   sincroniza al siguiente inicio de sesión.

| Rol | Qué puede hacer en resumen |
|---|---|
| **Trabajador** | Crear y gestionar sus propios documentos; descargar formularios; solicitar descarga de documentos de su departamento |
| **Coordinador** | Todo lo del trabajador **más** los documentos que el jefe le asigne, con los permisos que el jefe defina (leer, editar, eliminar) |
| **Jefe de Departamento** | Gestionar todos los documentos de su departamento, aprobar y autorizar, asignar, transferir, distribuir, inhabilitar y consultar el panel *Departamento* |
| **Administrador** | Acceso a todos los documentos y a la administración del sistema (usuarios, catálogos, formatos, auditoría) |

> El rol y el departamento los asigna el administrador. Si el tuyo está mal,
> solicita la corrección: POLIDOC lo sincroniza con el directorio activo.

### Matriz rápida

| Acción | Trabajador | Coordinador | Jefe | Admin |
|---|---|---|---|---|
| Crear documentos | ✔ (propios) | ✔ | ✔ | ✔ |
| Editar / eliminar sus documentos | ✔ | ✔ | ✔ | ✔ |
| Ver documentos de su departamento | ✖ (solo los suyos) | ✖ (solo los suyos + asignados) | ✔ | ✔ |
| Editar documentos asignados | — | ✔ según permiso | ✔ | ✔ |
| Aprobar creación y ediciones | ✖ | ✖ | ✔ | ✔ |
| Aprobar / rechazar descargas | ✖ | ✖ | ✔ | ✔ |
| Asignar documentos a un coordinador | ✖ | ✖ | ✔ | ✔ |
| Transferir propiedad | ✖ | ✖ | ✔ (su departamento) | ✔ |
| Distribuir y revocar copias | ✖ | ✖ | ✔ | ✔ |
| Inhabilitar documentos | ✖ | ✖ | ✔ | ✔ |
| Ver *Departamento* | ✖ | ✖ | ✔ | ✔ |
| Gestionar usuarios y catálogos | ✖ | ✖ | ✖ | ✔ |
| Destruir documentos (fin de retención) | ✖ | ✖ | ✖ | ✔ (superusuario) |

## Clasificación de seguridad

Cada documento tiene una clasificación, elegida al crearlo. Es la que decide
**quién lo ve** y **quién puede descargarlo**. Solo *Público* cruza la
barrera entre departamentos.

| Clasificación | Quién lo ve | Descarga |
|---|---|---|
| **Público** | Cualquier usuario (documento activo) | Directa, queda registrada |
| **Interno** | Tu departamento (activos), el jefe, el propietario, los asignados y los distribuidos | Directa para el jefe del departamento, el propietario y quien tenga distribución; los demás, con solicitud aprobada |
| **Confidencial** | Solo el jefe del departamento, el propietario y el administrador | Con solicitud aprobada por el jefe |
| **Restringido** | Solo el propietario, el administrador y quienes tengan asignación, distribución o solicitud aprobada | **Doble control:** debe aprobar el jefe del departamento **y** el administrador |

### Errores frecuentes

| Mensaje | Causa | Solución |
|---|---|---|
| "No tienes permisos para ver este documento" | No eres propietario, ni del departamento, ni tienes asignación/distribución | Solicita acceso al jefe del departamento del documento o pide que te lo distribuyan |
| No aparece el botón *Descargar* | Tu clasificación exige autorización previa | Pulsa *Solicitar Descarga* y espera la aprobación (cap. 08) |
| No aparece *Solicitar Descarga* | Ya tienes permiso, o ya tienes una solicitud pendiente, o ya tienes una autorización vigente | Revisa el aviso mostrado en la página |

## Casos especiales

- **Descripción de Procesos (DPR)** es visible para *cualquier* jefe de
  departamento, no solo el propio, y solo los jefes pueden crearla.
- **Documentos asignados a un coordinador** llevan permisos granulares:
  leer, editar y eliminar pueden combinarse.
- **Distribuciones** (copias controladas) dan acceso hasta que se revocan.
- **Solicitudes de aprobación**: los participantes de un flujo (el creador y
  el aprobador) pueden ver el documento mientras el trámite está en curso,
  para poder evaluarlo; los demás no.

---

**Anterior:** [02. Primeros pasos](02-primeros-pasos.md) ·
**Siguiente:** [04. Consultar y buscar](04-consultar-y-buscar.md)
