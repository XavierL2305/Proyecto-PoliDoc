# Manual de Usuario — POLIDOC

Sistema de gestión documental para la Policlínica Táchira (normas ISO 9001,
27001 y 15489).

| Control de versión | |
|---|---|
| Versión del manual | **0.9 — MVP (Tomo 1)** |
| Fecha | 07/10/2026 |
| Versión del sistema verificada | commit `89549ba` |
| Fuente de verdad técnica | `CONTEXT.txt` (raíz del proyecto) + código de vistas y plantillas |
| Estado | En elaboración. Tomo 2 pendiente (ver "Fases" abajo) |

> Este manual describe la **interfaz de usuario**. No contiene instrucciones
> técnicas de instalación, configuración de variables de entorno ni uso de la
> API: para eso está `README.md` de la raíz y `CONTEXT.txt`.

---

## ¿Qué capítulo me toca?

| Si tu rol es... | Lee obligatoriamente | Lee solo si te interesa |
|---|---|---|
| **Cualquier usuario** | 01, 02, 03, 04 | 09 (problemas), A (glosario) |
| **Trabajador / Coordinador** | + 05, 06, 08 | 07 (si editas documentos) |
| **Jefe de Departamento** | + 06, 07, 08 | 05, 09 |
| **Administrador** | Todo | — |

Las secciones por rol ampliadas (qué ve exactamente cada perfil, catálogos,
usuarios y auditoría) están en el **Tomo 2**, aún pendiente.

---

## Índice

| Cap. | Título | Para quién |
|---|---|---|
| 01 | [Introducción y navegación](01-introduccion.md) | Todos |
| 02 | [Primeros pasos: acceso, panel y perfil](02-primeros-pasos.md) | Todos |
| 03 | [Roles, permisos y clasificación de seguridad](03-roles-y-permisos.md) | Todos |
| 04 | [Consultar y buscar documentos](04-consultar-y-buscar.md) | Todos |
| 05 | [Crear un documento](05-crear-documento.md) | Todos (creadores) |
| 06 | [Aprobaciones: evaluación y autorización](06-aprobaciones.md) | Jefes y creadores |
| 07 | [Versionado y Planilla de Cambios](07-versionado-planilla.md) | Creadores y jefes |
| 08 | [Descargas, distribución y trazabilidad](08-descargas-distribucion.md) | Todos |
| 09 | [Solución de problemas](09-troubleshooting.md) | Todos |
| A | [Glosario](A-glosario.md) | Todos |

---

## Criterios de "capítulo terminado"

Un capítulo se considera completo solo si cumple los cinco:

1. Instrucciones en **pasos numerados** o secciones numeradas (no solo
   párrafos descriptivos; el anexo A es una tabla de referencia y queda
   exento).
2. Sección **"Campos obligatorios"** con lo imprescindible para no fallar
   (capítulos de creación y edición).
3. Caja **"Errores frecuentes"** con el error, la causa y la solución
   (todo capítulo de procedimiento; el 09 la sustituye por la guía completa).
4. Cada imagen referenciada con `![](imagenes/...)` si aplica, y el archivo
   existe en `imagenes/`.
5. Enlaces internos a otros capítulos válidos.

La comprobación automática de 1 a 5 se hace con
[`verificar.py`](verificar.py); la calidad del contenido es revisión manual.

---

## Capturas de pantalla

El inventario completo, con el nombre exacto de archivo, la pantalla, el rol
con el que tomarla y la URL, está en [`CAPTURAS.md`](CAPTURAS.md).

Estado actual del Tomo 1:

| | Cantidad | Estado |
|---|---|---|
| Capturas imprescindibles (★) | 16 | ⬜ pendientes |
| Capturas de ampliación (☆) | 15 | ⬜ pendientes (3 de ellas ya se usan en el Tomo 1) |

---

## Mantenimiento (revisar en cada cambio de interfaz)

Este manual se revisa cuando ocurra cualquiera de estos cuatro eventos:

1. **Cambios en el menú o la navegación** (sidebar, navbar, nuevas pantallas).
2. **Cambios en estados o flujos** (nuevos estados, otra cadena de aprobación).
3. **Nuevos tipos de documento** o cambios en sus campos por defecto.
4. **Cambios de permisos** por rol (`apps/documentos/permissions.py`).

Cada revisión actualiza la fecha y la versión del manual de la tabla de
control de versiones de esta portada. Las reglas de negocio descritas aquí
fueron contrastadas contra el código en la versión indicada arriba.

---

## Fases

| Fase | Contenido | Estado |
|---|---|---|
| **1. Tomo 1 (MVP)** | Capítulos 01-09 + glosario, 16 capturas ★ | En curso |
| **2. Tomo 2** | Vigencia y vencimientos, guías por rol (10-14), capturas ☆ | Pendiente |
| **3. Export** | Generación de PDF/HTML con portada y control de versiones para distribución | Pendiente — fuera de alcance del Tomo 1 |
