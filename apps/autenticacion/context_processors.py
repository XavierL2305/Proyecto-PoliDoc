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
    'documentos_personales', 'documentos_sgc_sin_codigo',
    'documento_sin_codigo_crear', 'documento_sin_codigo_detalle',
    'documento_sin_codigo_editar', 'documento_sin_codigo_eliminar',
    'documento_sin_codigo_descargar',
    'lista_maestra', 'lista_maestra_agregar_fila',
    'lista_maestra_editar_fila', 'lista_maestra_eliminar_fila',
    'lista_maestra_sincronizar', 'lista_maestra_generar',
    'lista_maestra_descargar', 'lista_maestra_acceso',
    'lista_maestra_revocar',
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
    """Contexto de la barra lateral.

    Agrega ``sidebar_grupo_activo`` (``''``/``documentacion``/``configuracion``)
    y las banderas de las bandejas de documentos sin código (la bandeja se
    muestra solo si hay al menos un registro visible; el botón de crear no
    depende de esto).
    """
    match = getattr(request, 'resolver_match', None)
    url_name = (getattr(match, 'url_name', '') or '')
    if url_name in URLS_CONFIGURACION:
        grupo = 'configuracion'
    elif url_name in URLS_DOCUMENTACION:
        grupo = 'documentacion'
    else:
        grupo = ''

    contexto = {'sidebar_grupo_activo': grupo}
    usuario = getattr(request, 'user', None)
    if usuario is not None and usuario.is_authenticated:
        try:
            from documentos.services import documentos_sin_codigo as svc
            contexto.update(svc.context_menu(usuario))
        except Exception:  # noqa: BLE001 - el menú nunca debe romper la página
            contexto.update({
                'nav_docs_personales': False,
                'nav_docs_sgc_sin_codigo': False,
                'nav_puede_crear_personal': True,
                'nav_puede_crear_sgc': False,
            })
        try:
            from documentos.services import lista_maestra as lm
            contexto.update(lm.context_menu(usuario))
        except Exception:  # noqa: BLE001
            contexto['nav_lista_maestra'] = False
    return contexto
