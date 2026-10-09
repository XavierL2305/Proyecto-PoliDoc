from django.urls import path
from . import views
from . import api

app_name = 'documentos'

urlpatterns = [
    # API Documentos
    path('api/buscar-sugerencias/', api.buscar_sugerencias, name='api_buscar_sugerencias'),
    path('api/lista/', api.documentos_lista, name='api_documentos_lista'),
    path('api/<str:tipo>/<uuid:doc_id>/', api.documento_detalle, name='api_documento_detalle'),
    path('api/<str:tipo>/<uuid:doc_id>/cambiar-estado/', api.cambiar_estado, name='api_cambiar_estado'),
    path('api/<str:tipo>/<uuid:doc_id>/transferir/', api.transferir_propiedad, name='api_transferir'),
    path('api/filtros/', api.opciones_filtros, name='api_opciones_filtros'),
    path('api/contadores/', api.contadores_documentos, name='api_contadores_documentos'),
    path('api/usuarios-departamento/', api.usuarios_departamento, name='api_usuarios_departamento'),
    path('api/procedimientos/', api.procedimientos_por_departamento, name='api_procedimientos_por_departamento'),
    path('api/formularios/', api.formularios_por_departamento, name='api_formularios_por_departamento'),
    path('api/solicitar-acceso/', api.solicitar_acceso_documento, name='api_solicitar_acceso'),
    path('api/solicitud-acceso/<int:solicitud_id>/responder/', api.responder_solicitud_acceso, name='api_responder_solicitud_acceso'),
    path('api/solicitudes-acceso-pendientes/', api.solicitudes_acceso_pendientes, name='api_solicitudes_acceso_pendientes'),

    # API Aprobación de documentos
    path('api/solicitudes-aprobacion-pendientes/', api.solicitudes_aprobacion_pendientes, name='api_solicitudes_aprobacion_pendientes'),
    path('api/<str:tipo>/<uuid:documento_id>/aprobar-creacion/', api.aprobar_creacion_documento, name='api_aprobar_creacion'),
    path('api/<str:tipo>/<uuid:documento_id>/rechazar-creacion/', api.rechazar_creacion_documento, name='api_rechazar_creacion'),

    # API Aprobación de planillas de cambios
    path('api/planillas-pendientes/', api.planillas_pendientes, name='api_planillas_pendientes'),
    path('api/planilla/<uuid:planilla_uuid>/aprobar/', api.aprobar_planilla, name='api_aprobar_planilla'),
    path('api/planilla/<uuid:planilla_uuid>/rechazar/', api.rechazar_planilla, name='api_rechazar_planilla'),
    
    # API Documentos asociados para planilla de cambios
    path('api/procedimientos-por-departamentos/', api.procedimientos_por_departamentos, name='api_procedimientos_por_departamentos'),
    path('api/formularios-por-departamento-planilla/', api.formularios_por_departamento_planilla, name='api_formularios_por_departamento_planilla'),
    path('api/referenciables/', api.documentos_referenciables, name='api_documentos_referenciables'),
    path('api/tipos-documento/', api.tipos_documento_disponibles, name='api_tipos_documento'),
    path('api/siguiente-secuencia/', views.obtener_siguiente_secuencia, name='siguiente_secuencia'),

    # Formularios descargables
    path('', views.formularios, name='catalogo'),

    # Mis documentos
    path('mis-documentos/', views.mis_documentos, name='mis_documentos'),

    # Resultados de búsqueda (navbar)
    path('buscar/', views.buscar, name='buscar'),

    # Bandeja de aprobaciones (Jefe/Admin)
    path('bandeja-aprobaciones/', views.bandeja_aprobaciones, name='bandeja_aprobaciones'),

    # Documentos del departamento (Jefe)
    path('departamento/', views.documentos_departamento, name='documentos_departamento'),

    # Crear documentos (nueva vista genérica)
    path('seleccionar-tipo/', views.seleccionar_tipo, name='seleccionar_tipo'),
    path('nuevo/<str:tipo>/', views.crear_documento, name='crear_documento'),

    # Planilla de cambios (DEBE ir antes del genérico <str:tipo>/<uuid:/> para evitar conflicto)
    path('planilla-cambios/nueva/', views.crear_planilla, name='crear_planilla'),
    path('planilla-cambios/<uuid:planilla_uuid>/', views.detalle_planilla, name='detalle_planilla'),
    path('planilla-cambios/<uuid:planilla_uuid>/aprobar/', views.aprobar_planilla, name='aprobar_planilla'),
    path('planilla-cambios/<uuid:planilla_uuid>/enviar/', views.enviar_planilla, name='enviar_planilla'),

    # Detalle, editar, eliminar (genérico - DEBE ir después de rutas específicas)
    path('<str:tipo>/<uuid:documento_id>/', views.detalle_documento, name='detalle'),
    path('<str:tipo>/<uuid:documento_id>/enviar/', views.enviar_documento, name='enviar_documento'),
    path('<str:tipo>/<uuid:documento_id>/editar-borrador/', views.editar_borrador, name='editar_borrador'),
    path('<str:tipo>/<uuid:documento_id>/editar/', views.editar_documento, name='editar'),
    path('<str:tipo>/<uuid:documento_id>/eliminar/', views.eliminar_documento, name='eliminar'),
    path('<str:tipo>/<uuid:documento_id>/descargar/', views.descargar_archivo, name='descargar_archivo'),
    path('<str:tipo>/<uuid:documento_id>/ver-archivo/', views.ver_archivo, name='ver_archivo'),
    path('<str:tipo>/<uuid:documento_id>/estado-conversion/', views.estado_conversion, name='estado_conversion'),
    path('<str:tipo>/<uuid:documento_id>/resumen-ia/', views.resumen_ia, name='resumen_ia'),

    # Descargas
    path('<str:tipo>/<uuid:documento_id>/solicitar-descarga/', views.solicitar_descarga, name='solicitar_descarga'),
    path('solicitud/<int:solicitud_id>/aprobar/', views.aprobar_descarga, name='aprobar_descarga'),

    # Versiones
    path('<str:tipo>/<uuid:documento_id>/versiones/', views.historial_versiones, name='historial_versiones'),
    path('<str:tipo>/<uuid:documento_id>/eliminar-version/<int:version_id>/', views.eliminar_version, name='eliminar_version'),

    # Renovación de vencimiento
    path('<str:tipo>/<uuid:documento_id>/renovar-vencimiento/', views.renovar_vencimiento, name='renovar_vencimiento'),
    path('<str:tipo>/<uuid:documento_id>/establecer-vigencia/', views.establecer_vigencia, name='establecer_vigencia'),
    path('<str:tipo>/<uuid:documento_id>/quitar-vigencia/', views.quitar_vigencia, name='quitar_vigencia'),

    # Asignaciones (Jefe)
    path('<str:tipo>/<uuid:documento_id>/asignar/', views.asignar_documento, name='asignar_documento'),
    path('asignaciones/', views.gestionar_asignaciones, name='gestionar_asignaciones'),

    # Distribución de documentos (ISO 9001:7.5.3)
    path('<str:tipo>/<uuid:documento_id>/distribuir/', views.distribuir_documento, name='distribuir_documento'),
    path('<str:tipo>/<uuid:documento_id>/historial-distribucion/', views.historial_distribucion, name='historial_distribucion'),
    path('<str:tipo>/<uuid:documento_id>/distribucion/<int:dist_id>/revocar/', views.revocar_distribucion, name='revocar_distribucion'),

    # Transferir propiedad
    path('<str:tipo>/<uuid:documento_id>/transferir/', views.transferir_propiedad, name='transferir_propiedad'),

    # Configuración (Admin/Jefe)
    path('configuracion/', views.configuracion, name='configuracion'),
    path('configuracion/tipos-documento/', views.tipos_documento_lista, name='tipos_documento_lista'),
    path('configuracion/tipos-documento/nuevo/', views.tipo_documento_crear, name='tipo_documento_crear'),
    path('configuracion/tipos-documento/<int:tipo_id>/editar/', views.tipo_documento_editar, name='tipo_documento_editar'),
    path('configuracion/tipos-documento/<int:tipo_id>/eliminar/', views.tipo_documento_eliminar, name='tipo_documento_eliminar'),
    path('configuracion/tipos-documento/<int:tipo_id>/reactivar/', views.tipo_documento_reactivar, name='tipo_documento_reactivar'),

    path('configuracion/periodos/', views.periodos_lista, name='periodos_lista'),
    path('configuracion/periodos/nuevo/', views.periodo_crear, name='periodo_crear'),
    path('configuracion/periodos/<int:periodo_id>/editar/', views.periodo_editar, name='periodo_editar'),
    path('configuracion/periodos/<int:periodo_id>/eliminar/', views.periodo_eliminar, name='periodo_eliminar'),
    path('configuracion/periodos/<int:periodo_id>/reactivar/', views.periodo_reactivar, name='periodo_reactivar'),

    # Destrucción de documentos (admin)
    path('<str:tipo>/<uuid:documento_id>/destruir/', views.destruir_documento, name='destruir_documento'),

    path('configuracion/auditoria/', views.auditoria_lista, name='auditoria_lista'),
    path('configuracion/auditoria/reporte-pdf/', views.auditoria_reporte_pdf, name='auditoria_reporte_pdf'),
    path('configuracion/auditoria/reportes/', views.auditoria_reportes_lista, name='auditoria_reportes_lista'),
    path('configuracion/auditoria/reportes/<uuid:reporte_uuid>/descargar/',
         views.auditoria_reporte_descarga, name='auditoria_reporte_descarga'),
    path('configuracion/auditoria/<str:log_id>/', views.auditoria_detalle, name='auditoria_detalle'),
    path('configuracion/ciclo-vida/', views.ciclo_vida, name='ciclo_vida'),
    path('configuracion/departamentos/', views.departamentos_lista, name='departamentos_lista'),
    path('configuracion/departamentos/nuevo/', views.departamento_crear, name='departamento_crear'),
    path('configuracion/departamentos/<int:depto_id>/editar/', views.departamento_editar, name='departamento_editar'),
    path('configuracion/departamentos/<int:depto_id>/eliminar/', views.departamento_eliminar, name='departamento_eliminar'),
    path('configuracion/roles-permisos/', views.roles_permisos, name='roles_permisos'),
    path('configuracion/formatos/', views.formatos_lista, name='formatos_lista'),
    path('configuracion/formatos/nuevo/', views.formato_crear, name='formato_crear'),
    path('configuracion/formatos/<int:formato_id>/editar/', views.formato_editar, name='formato_editar'),
    path('configuracion/formatos/<int:formato_id>/desactivar/', views.formato_eliminar, name='formato_eliminar'),
    path('configuracion/formatos/<int:formato_id>/reactivar/', views.formato_reactivar, name='formato_reactivar'),
    path('configuracion/formatos/<int:formato_id>/constructor/', views.formato_constructor, name='formato_constructor'),
    path('configuracion/formatos/<int:formato_id>/publicar/', views.formato_publicar, name='formato_publicar'),
    path('configuracion/formatos/<int:formato_id>/preview/', views.formato_preview, name='formato_preview'),
    path('configuracion/formatos/<int:formato_id>/regenerar-preview/', views.formato_regenerar_preview, name='formato_regenerar_preview'),
    path('configuracion/formatos/<int:formato_id>/seccion/nueva/', views.seccion_crear, name='seccion_crear'),
    path('configuracion/formatos/<int:formato_id>/seccion/<int:seccion_id>/editar/', views.seccion_editar, name='seccion_editar'),
    path('configuracion/formatos/<int:formato_id>/seccion/<int:seccion_id>/eliminar/', views.seccion_eliminar, name='seccion_eliminar'),
    path('configuracion/formatos/<int:formato_id>/seccion/<int:seccion_id>/campo/nuevo/', views.campo_crear, name='campo_crear'),
    path('configuracion/formatos/<int:formato_id>/campo/<int:campo_id>/editar/', views.campo_editar, name='campo_editar'),
    path('configuracion/formatos/<int:formato_id>/campo/<int:campo_id>/eliminar/', views.campo_eliminar, name='campo_eliminar'),
    path('solicitud/<uuid:solicitud_uuid>/exportar/xlsx/', views.descargar_solicitud_xlsx, name='descargar_solicitud_xlsx'),
    path('solicitud/<uuid:solicitud_uuid>/exportar/pdf/', views.descargar_solicitud_pdf, name='descargar_solicitud_pdf'),
]
