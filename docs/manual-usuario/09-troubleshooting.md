# 09. Solución de problemas

Guía por síntoma: qué pasa, por qué y qué hacer. Si ninguna fila aplica,
reúne la información de "Cuándo pedir ayuda al administrador" (al final).

## Acceso e inicio de sesión

| Síntoma / mensaje | Causa probable | Qué hacer |
|---|---|---|
| "Credenciales inválidas" | Usuario o contraseña incorrectos | Revisa mayúsculas; es tu contraseña habitual del directorio activo |
| "No tienes permisos para usar el sistema" | Tu cuenta no está en un grupo de documentación | Solicita al administrador grupo y departamento |
| Bloqueo tras intentos fallidos | 5 inicios fallidos en 5 minutos por usuario | Espera 5 minutos y reintenta |
| La sesión se cierra al ir y volver | No marcaste *Recordar sesión* | Vuelve a iniciar sesión; en equipo propio, márcalo |
| Necesitas cambiar la contraseña | No existe "olvidé mi contraseña" en POLIDOC | Cámbiala en el equipo/Active Directory de la institución |

## Permisos y visibilidad

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| "No tienes permisos para ver este documento" | No eres propietario, no es de tu departamento o la clasificación te excluye | Ver [cap. 03](03-roles-y-permisos.md); pide distribución o solicitud de acceso |
| No aparece *Descargar*, sino *Solicitar Descarga* | La clasificación exige autorización previa | Solicita la descarga y espera (cap. 08) |
| No aparece *Solicitar Descarga* | Ya tienes permiso, una solicitud pendiente o una autorización vigente | Lee el aviso mostrado en la ficha |
| "No tienes un departamento asignado" | Tu perfil no tiene departamento | Contacta al administrador |
| "Solo los jefes de departamento pueden crear este tipo" | Tipo restringido (p. ej. Descripción de Procesos) | Que lo cree tu jefe |

## Aprobaciones

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El documento está **"En Espera"** | El departamento no tiene jefe para ese paso (o no hay jefe de Gerencia General) | Un administrador debe asignar el jefe; no se salta el paso |
| Llega "Aprobación estancada (+48 h)" | Una aprobación lleva más de 48 h sin resolverse | Los administradores ya fueron notificados; resuelve el pendiente |
| "El motivo de rechazo es requerido" | Rechazaste sin escribir el motivo | Escribe el motivo y confirma de nuevo |
| Documento rechazado | El aprobador pidió cambios | Lee el motivo, corrige y vuelve a enviar a revisión |
| La misma persona aprueba dos veces | La cadena siempre tiene evaluador y autorizador | Es correcto: son dos pasos, aunque coincida la persona |

## Descargas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| "No tienes permiso para descargar este documento" | Sin autorización vigente | Solicita la descarga (cap. 08) |
| El botón volvió a ser *Solicitar Descarga* | Pasaron los 30 días o **subió de versión** el documento | Solicita de nuevo: la autorización es por versión y caduca |
| "No se pudo registrar la auditoría de la descarga. Por seguridad, la descarga queda bloqueada" (o el equivalente de *consulta* en el visor) | La auditoría no está registrando y el sistema bloquea por seguridad | Contacta al administrador: no se omitirá el registro |
| "El archivo de este documento no está disponible en el almacenamiento" | Falta el archivo original | Contacta al administrador |

## Documentos y estados

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El documento aparece **Obsoleto** / **Vencido** | Pasó la fecha de vencimiento | El propietario, el jefe o el administrador lo renuevan desde la ficha |
| "Vence pronto" (amarillo) | Faltan 7 días o menos | Renueva el período de vigencia o revisa el contenido |
| No puedes editar un documento activo | Por diseño: los activos se cambian por Planilla de Cambios | Crea la planilla desde la ficha (cap. 07) |
| Guardaste una planilla como borrador y no la encuentras | No hay un listado de planillas; solo se reabre desde su enlace | Botón **Planilla de Cambios (borrador)** en la ficha del documento o en Mis Documentos, y **Continuar edición** en el detalle (cap. 07) |
| "No se pueden eliminar versiones mientras el documento está en revisión" | Hay una aprobación en curso | Espera a que termine |
| El código que quieres ya existe | Colisión de secuencia | Cambia el número de secuencia en el formulario |

## Archivos, conversión y resumen IA

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| La barra de conversión a PDF no termina | La tarea asíncrona no respondió | Recarga la página; si persiste, contacta al administrador |
| "Extensión no permitida" / "supera el tamaño máximo" | Formato distinto a PDF/DOC/DOCX/XLS/XLSX/VSDX o más de 10 MB | Convierte a PDF o reduce el archivo |
| "El contenido no es un PDF válido" / "no es un documento Office válido" | El contenido real no es el de la extensión | Vuelve a guardar el archivo desde su aplicación original |
| *Resumen IA* no aparece | La función está deshabilitada | No es un error; contacta al administrador si la necesitas |
| *Resumen IA* queda en *Procesando* o *Error* | Demora o límite diario de generaciones | Espera unos minutos; si continúa, regenera más tarde |

## Notificaciones y tareas automáticas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| No llegan avisos de vencimiento ni se vencen documentos solos | El programador de tareas del sistema no está corriendo | Contacta al administrador |
| El contador de notificaciones no baja | Falta marcarlas como leídas | Campana → *Marcar todas como leídas* |
| No ves notificaciones de un rol nuevo | Asignaron tu rol recién | Vuelve a iniciar sesión para que se actualice tu perfil |

## Buscador

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El documento no aparece en la búsqueda | No tienes permiso para verlo | Verifica en *Mis Documentos* o pide acceso |
| Los filtros no devuelven nada | Combinación de filtros demasiado restrictiva | Quita el filtro de estado o clasificación |

---

## Cuándo pedir ayuda al administrador

Contacta al administrador de POLIDOC si:

1. Tu rol, departamento o grupo de directorio activo está mal asignado.
2. Un documento dice **"En Espera"** porque falta un jefe.
3. Una descarga falla con un **error del sistema** (auditoría no disponible).
4. Un archivo dice que **no está disponible en el almacenamiento**.
5. Las tareas automáticas (vencimientos, avisos) no se ejecutan.
6. Necesitas crear o reactivar una cuenta de usuario.

Incluye siempre: tu usuario, el código del documento, la fecha/hora y una
captura del mensaje.

---

**Anterior:** [08. Descargas y distribución](08-descargas-distribucion.md) ·
**Siguiente:** [Anexo A. Glosario](A-glosario.md)
