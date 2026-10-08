# 10. Vigencia y vencimientos

Todo documento activo puede tener **período de vigencia**: a partir de su
fecha de entrada en vigor, POLIDOC calcula la fecha de vencimiento, avisa
7 días antes y, al vencer, el documento pasa a **Obsoleto**. Este capítulo
explica cómo se controla esa vigencia y quién hace cada cosa.

## Períodos de vigencia

Un **período** es el número de meses que un documento puede estar vigente
(p. ej. 6 meses, 1 año, 2 años). El administrador los gestiona en
**Configuración → Períodos de Vigencia**
(`/documentos/configuracion/periodos/`); el jefe solo puede verlos.

| Quién | Qué puede hacer |
|---|---|
| Administrador | Crear, editar, eliminar y reactivar períodos |
| Jefe de departamento | Ver la lista |
| Los demás | No acceden |

Cada tipo de documento puede traer un período **por defecto**: al crear un
documento de ese tipo, el formulario ya lo trae precargado.

## Establecer o cambiar la vigencia

1. Abre la ficha de un documento **Activo**.
2. En la tarjeta ISO (bloque *Vigencia / Revisión*), pulsa **Establecer
   vigencia** (o **Cambiar vigencia** si ya tiene una).
3. Elige el período (por ejemplo "1 año") y confirma.
4. POLIDOC recalcula la **fecha de vencimiento** desde la fecha de entrada
   en vigor y te muestra la nueva fecha en el mensaje de confirmación.

Quién puede hacerlo: **el propietario del documento, el jefe del
departamento del documento o el administrador**. Si lo hace otra persona,
el propietario y el jefe reciben la notificación *"…estableció el período
de vigencia…"*, y la acción queda registrada en la auditoría (acción
`cambio_vigencia`).

> La vigencia solo se cambia sobre documentos **activos**: un borrador o un
> obsoleto no tiene vigencia que controlar.

## Quitar la vigencia

Si un documento no debe vencer (o su vigencia se controla fuera del
sistema):

1. En la misma tarjeta ISO, pulsa **Quitar vigencia**.
2. Escribe el **motivo** (obligatorio; es la justificación ISO) y confirma.
3. El período, la fecha de vencimiento y la próxima revisión se limpian; el
   documento queda como *sin vigencia* y la operación se registra en su
   historial y en la auditoría (`eliminacion_vigencia`).

**No se puede quitar** si el tipo de documento exige vigencia
(*requiere_vigencia*): en ese caso solo puedes **cambiarla** por otra.

> **Renovar** el vencimiento es otra cosa: sirve para reactivar un
> documento **obsoleto**, siempre con aprobación. Ver
> [capítulo 06](06-aprobaciones.md).

## Estado de vigencia: las tres insignias

En la ficha, en Mis Documentos y en el panel Departamento, cada documento
activo lleva una insignia calculada automáticamente:

| Insignia | Significado |
|---|---|
| 🟢 **Vigente** | Hay más de 7 días hasta el vencimiento |
| 🟡 **Vence pronto** | Faltan 7 días o menos |
| 🔴 **Vencido** | La fecha de vencimiento ya pasó |
| (sin insignia) | El documento no tiene vigencia establecida |

## Qué pasa automáticamente al vencer

Una tarea diaria (`verificar_vencimientos`, a la 1:00 AM) hace dos cosas:

1. **Aviso preventivo (7 días antes):** el propietario y el jefe del
   departamento reciben la notificación *"Vence pronto el documento X"*.
   Solo se avisa una vez por vencimiento.
2. **Al vencer:** el documento pasa a **Obsoleto** automáticamente: deja de
   editarse y de distribuirse como activo.

Si necesitas revisar el sistema en caliente, un administrador puede
ejecutar la verificación a mano:

```bash
python manage.py verificar_vencimientos
```

> Un documento **Obsoleto por vencimiento** se recupera con **Renovar el
> vencimiento** ([capítulo 06](06-aprobaciones.md)); reactivarlo "a la
> antigua" sin renovar la fecha se niega.

## Seguir la vigencia desde el panel Departamento

El jefe del departamento (y el administrador) tienen el panel
**Departamento** (`/documentos/departamento/`), con:

- **Tarjetas de conteo** por estado de vigencia (vencidos, vence pronto,
  vigentes, sin vigencia).
- **Filtro por vigencia** para ver solo lo que urge.
- El resto de filtros por clasificación y los grupos por departamento.

Ver la guía del jefe ([capítulo 12](12-guia-jefe-departamento.md)).

### Errores frecuentes

| Mensaje | Causa | Solución |
|---|---|---|
| "Solo se puede establecer la vigencia de documentos aprobados" | El documento no está activo | Actívalo primero (cap. 06) o trabaja sobre un activo |
| "Debes seleccionar un período de vigencia" | No elegiste período | Selecciona uno en el desplegable |
| "No tienes permisos para modificar la vigencia de este documento" | No eres propietario, jefe del departamento ni admin | Pídeselo a quien corresponda |
| "Este tipo de documento exige vigencia; cámbiala en lugar de quitarla" | El tipo tiene *requiere_vigencia* | Cambia el período en vez de quitarlo |
| "Debes indicar el motivo para quitar la vigencia" | El motivo está vacío | Escribe por qué se quita |
| El documento pasó a Obsoleto sin que nadie hiciera nada | Venció la fecha | **Renueva** el vencimiento (cap. 06) |
| No aparecen notificaciones de "Vence pronto" | El aviso ya se envió para ese vencimiento | Solo se avisa una vez; cambia la vigencia para generar un aviso nuevo |

---

**Anterior:** [09. Solución de problemas](09-troubleshooting.md) ·
**Siguiente:** [11. Guía del trabajador y del coordinador](11-guia-trabajador-coordinador.md)
