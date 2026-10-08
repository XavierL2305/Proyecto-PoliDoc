# Capturas de pantalla — Manual de Usuario POLIDOC

Lista de capturas a tomar y agregar a `docs/manual-usuario/imagenes/`.
**Marca ✅ cuando el archivo exista** con el nombre exacto indicado; el
script `verificar.py` detecta las referencias rotas.

---

## Reglas de captura

- **Sin datos personales:** no deben verse cédulas, nombres de pacientes,
  correos ni teléfonos reales. Usa los datos de prueba del entorno.
- Ventana del navegador a **1440 px de ancho**, tema claro, zoom al 100 %.
- Formato **PNG** comprimido (pngquant/oxipng), ancho máximo 1440 px.
- Si una pantalla muestra mensajes del sistema, inclúyelos: ayudan al lector.

## Entorno de captura

Base de datos `db_new.sqlite3` (SQLite, ya creada) con usuarios de prueba:

| Rol | Cantidad | Para qué se necesita |
|---|---|---|
| Trabajador | 2 | creador, solicitante de descarga |
| Coordinador | 1 | asignaciones |
| Jefe de departamento | 3 | aprobaciones, departamento |
| Administrador | 1 | usuarios, configuración, auditoría |

Antes de fotografiar, prepara estados que hoy no existen en la base:

```bash
python manage.py poblar_tipos_documento      # 15 tipos ISO
python manage.py poblar_departamentos        # 38 departamentos
python manage.py poblar_periodos             # períodos de vigencia
python manage.py runserver                   # http://127.0.0.1:8001
```

Y crea manualmente (con las cuentas de prueba) documentos en cada estado:
**borrador**, **en revisión** y **activo**, más uno **vencido/obsoleto** y una
**solicitud de descarga pendiente**; sin esos estados no se pueden fotografiar
`aprobaciones/estados-documento.png` ni `aprobaciones/aprobar-rechazar.png`.

---

## Tomo 1 — imprescindibles (★)

| ✅ | Archivo | Pantalla | Rol | URL | Usada en |
|---|---|---|---|---|---|
| ✅ | `accesos/login.png` | Inicio de sesión con el modal de Términos y Condiciones | cualquiera | `/login/` | 02 |
| ✅ | `accesos/dashboard.png` | Panel de inicio | Trabajador | `/inicio/` | 02 |
| ✅ | `accesos/notificaciones.png` | Campana de notificaciones desplegada con alertas | Jefe | navbar → campana | 02 |
| ✅ | `consulta/catalogo-formularios.png` | Formularios Descargables (catálogo) | Trabajador | `/documentos/` | 04 |
| ✅ | `consulta/busqueda-navbar.png` | Autocompletado del buscador con resultados | Trabajador | navbar → buscar | 04 |
| ✅ | `consulta/mis-documentos.png` | Mis Documentos con los filtros desplegados | Trabajador | `/documentos/mis-documentos/` | 04 |
| ✅ | `consulta/detalle-documento.png` | Detalle completo con pestañas y botones de acción | Trabajador | `/documentos/<tipo>/<uuid>/` | 04 |
| ✅ | `consulta/visor-pdf.png` | Detalle con el visor PDF cargado (tarjeta "Documento") | Trabajador | ídem, tarjeta Documento | 04 |
| ✅ | `creacion/seleccionar-tipo.png` | Selector de tipo de documento | Trabajador | `/documentos/seleccionar-tipo/` | 05 |
| ✅ | `creacion/formulario-nuevo.png` | Formulario de creación con campos y botones inferiores | Trabajador | `/documentos/nuevo/<tipo>/` | 05 |
| ✅ | `aprobaciones/estados-documento.png` | Lista con los badges de estado (Borrador/En Revisión/Activo/Obsoleto) | Jefe | `/documentos/departamento/` | 06 |
| ✅ | `aprobaciones/bandeja-aprobaciones.png` | Bandeja de aprobaciones con filtros | Jefe | `/documentos/bandeja-aprobaciones/` | 06 |
| ✅ | `aprobaciones/aprobar-rechazar.png` | Panel de aprobación con "Evaluar"/"Autorizar" y campo de motivo | Jefe | detalle → banner de aprobación | 06 |
| ✅ | `aprobaciones/solicitar-descarga.png` | Formulario de solicitud de descarga con motivo | Trabajador | detalle → Solicitar Descarga | 08 |
| ✅ | `versionado/historial-versiones.png` | Historial de versiones | Trabajador | detalle → Versiones | 07 |
| ✅ | `versionado/planilla-cambios.png` | Formulario de Planilla de Cambios | Jefe | detalle → Planilla de Cambios | 07 |

## Tomo 2 — guías por rol (☆)

| ✅ | Archivo | Pantalla | Rol | URL | Usada en |
|---|---|---|---|---|---|
| ✅ | `ampliacion/perfil.png` | Mi Perfil | Trabajador | `/perfil/` | 11 |
| ⬜ | `ampliacion/conversion-pdf.png` | Barra de progreso de conversión a PDF | Trabajador | detalle tras subir .docx | 09 |
| ✅ | `ampliacion/resumen-ia.png` | Tarjeta "Resumen IA" con resultado | Jefe | detalle → Generar resumen | 11 |
| ✅ | `ampliacion/aprobar-planilla.png` | Aprobación de Planilla de Cambios | Jefe | detalle de planilla | 12 |
| ✅ | `ampliacion/departamento.png` | Documentos del Departamento con tarjetas de vigencia | Jefe | `/documentos/departamento/` | 12 |
| ✅ | `ampliacion/asignar-documento.png` | Asignación a coordinador con permisos granulares | Jefe | detalle → Asignar | 12 |
| ✅ | `ampliacion/distribuir.png` | Distribuir copia controlada | Jefe | detalle → Distribuir | 12 |
| ✅ | `ampliacion/transferir.png` | Transferir propiedad | Jefe | detalle → Transferir | 12 |
| ✅ | `ampliacion/usuarios.png` | Gestión de usuarios | Admin | `/usuarios/` | 13 |
| ✅ | `ampliacion/asignar-departamento.png` | Modal de asignación de departamento | Admin | `/usuarios/` | 13 |
| ✅ | `ampliacion/configuracion.png` | Dashboard de Configuración | Admin | `/documentos/configuracion/` | 13 |
| ✅ | `ampliacion/formatos-constructor.png` | Constructor de formatos (secciones y campos) | Admin | Configuración → Formatos | 13 |
| ✅ | `ampliacion/roles-permisos.png` | Matriz de roles y permisos | Admin | Configuración → Roles y Permisos | 13 |
| ✅ | `ampliacion/auditoria.png` | Lista de auditoría con filtros | Admin | `/documentos/configuracion/auditoria/` | 14 |
| ✅ | `ampliacion/reporte-auditoria.png` | Reporte PDF de auditoría (primera página) | Admin | Auditoría → Exportar PDF | 14 |

## Pendientes de capturar

Solo queda **una** captura declarada sin archivo:

| ✅ | Archivo | Pantalla | Rol | URL | Usada en |
|---|---|---|---|---|---|
| ⬜ | `ampliacion/conversion-pdf.png` | Barra de progreso de conversión a PDF | Trabajador | detalle tras subir .docx | 09 |
