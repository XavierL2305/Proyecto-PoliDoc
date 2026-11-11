"""Contexto de navegación de la barra lateral.

Calcula a qué grupo del menú pertenece la ruta activa para que la plantilla
renderice el acordeón ya abierto (sin parpadeo), marque el grupo con
``has-active`` y anuncie el estado con ``aria-expanded``. La lógica vive aquí
y no repartida en condicionales de plantilla (SOLID: una sola fuente).
"""

#: ``url_name`` de páginas que pertenecen al grupo "Documentación".
URLS_DOCUMENTACION = frozenset({
    'catalogo',
    'mis_documentos',
    'registros_lista', 'registro_crear', 'registro_editar',
    'registro_detalle', 'registro_enviar', 'registro_aprobar',
    'registro_rechazar', 'registro_xlsx', 'registro_pdf',
    'registro_distribuir', 'registro_subir_acuse',
    'registro_revocar_distribucion', 'registro_constancia',
    'bandeja_aprobaciones',
    'documentos_departamento',
})

#: ``url_name`` de páginas que pertenecen al grupo "Configuración".
URLS_CONFIGURACION = frozenset({
    'configuracion',
    'tipos_documento_lista', 'tipo_documento_crear', 'tipo_documento_editar',
    'tipo_documento_eliminar', 'tipo_documento_reactivar',
    'periodos_lista', 'periodo_crear', 'periodo_editar', 'periodo_eliminar',
    'periodo_reactivar',
    'ciclo_vida',
    'delegaciones_jefatura',
    'transferencia_masiva',
    'auditoria_lista', 'auditoria_detalle', 'auditoria_reporte_pdf',
    'auditoria_reportes_lista', 'auditoria_reporte_descarga',
    'departamentos_lista', 'departamento_crear', 'departamento_editar',
    'departamento_eliminar',
    'roles_permisos',
    'formatos_lista', 'formato_crear', 'formato_editar', 'formato_eliminar',
    'formato_reactivar', 'formato_constructor', 'formato_publicar',
    'formato_preview', 'formato_regenerar_preview',
    'seccion_crear', 'seccion_editar', 'seccion_eliminar',
    'campo_crear', 'campo_editar', 'campo_eliminar',
    'gestionar_usuarios', 'ver_usuario',
})


def navegacion(request):
    """Agrega ``sidebar_grupo_activo`` (``''``/``documentacion``/``configuracion``)."""
    match = getattr(request, 'resolver_match', None)
    url_name = (getattr(match, 'url_name', '') or '')
    if url_name in URLS_CONFIGURACION:
        grupo = 'configuracion'
    elif url_name in URLS_DOCUMENTACION:
        grupo = 'documentacion'
    else:
        grupo = ''
    return {'sidebar_grupo_activo': grupo}
