import json
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Count, Value, CharField
from django.utils import timezone
from .models import Documento, Departamento, SolicitudAccesoDocumento, SolicitudAprobacionDocumento, PlanillaCambios, AprobacionPlanilla, AsignacionDocumento, TipoDocumento
from .permissions import (
    obtener_documento, puede_editar, puede_descargar,
    puede_inhabilitar, puede_transferir, documentos_usuario,
    puede_ver_documento, q_documentos_visibles
)
from .services.planillas import aplicar_planilla_al_documento
from .cache_utils import tipos_activos
from auditoria.services import registrar_aprobacion_planilla, registrar_rechazo_planilla


@login_required(login_url='/login/')
def buscar_sugerencias(request):
    """API: Autocomplete — hasta 8 sugerencias.

    Busca por nombre, código y propietario, además de por documentos
    relacionados (M2M `documentos_relacionados`) y por referencias pendientes
    (`ReferenciaDocumento.codigo_destino`, que pueden existir sin registro del
    destino). Respeta la visibilidad del usuario tanto en el documento como en
    el relacionado.
    """
    q = request.GET.get('q', '').strip()
    try:
        limit = min(int(request.GET.get('limit', 8)), 20)
    except (TypeError, ValueError):
        limit = 8

    if len(q) < 2:
        return JsonResponse({'sugerencias': []})

    from .services.busqueda import buscar_documentos_visibles

    queryset, target_ids = buscar_documentos_visibles(request.user, q)
    documentos = list(queryset[:limit])

    sugerencias = []
    for d in documentos:
        relacionado_con = None
        if target_ids:
            rel = d.documentos_relacionados.filter(id__in=target_ids).first()
            if rel:
                relacionado_con = rel.codigo
        if not relacionado_con:
            ref = d.referencias_salientes.filter(
                codigo_destino__icontains=q
            ).first()
            if ref:
                relacionado_con = ref.codigo_destino

        sugerencias.append({
            'tipo': d.tipo_id,
            'id': str(d.uuid),
            'nombre': d.nombre,
            'codigo': d.codigo,
            'estado': d.estado,
            'tipo_display': d.tipo.codigo if d.tipo_id else '',
            'propietario': d.propietario.get_full_name() or d.propietario.username,
            'clasificacion': d.clasificacion,
            'clasificacion_display': d.get_clasificacion_display(),
            'clasificacion_color': d.clasificacion_color,
            'fecha_vencimiento': d.fecha_vencimiento.isoformat() if d.fecha_vencimiento else None,
            'relacionado_con': relacionado_con,
        })

    return JsonResponse({'sugerencias': sugerencias})


@login_required(login_url='/login/')
def documentos_lista(request):
    """API: Lista documentos con filtros, paginación y ordenamiento"""
    user = request.user
    buscar = request.GET.get('buscar', '').strip()
    tipo_doc = request.GET.get('tipo', '').strip()
    estado = request.GET.get('estado', '').strip()
    ordenar = request.GET.get('ordenar', 'nombre').strip()
    direccion = request.GET.get('direccion', 'asc').strip()
    pagina = request.GET.get('pagina', 1)

    documentos = documentos_usuario(user)

    if buscar:
        documentos = [d for d in documentos if buscar.lower() in d['nombre'].lower() or buscar.lower() in d['codigo'].lower() or buscar.lower() in d['propietario'].lower()]
    if tipo_doc:
        documentos = [d for d in documentos if d['tipo'] == tipo_doc]
    if estado:
        documentos = [d for d in documentos if d['estado'] == estado]

    reverse = direccion == 'desc'
    documentos.sort(key=lambda d: d.get(ordenar, ''), reverse=reverse)

    paginador = Paginator(documentos, 25)
    pagina_obj = paginador.get_page(pagina)

    return JsonResponse({
        'documentos': pagina_obj.object_list,
        'pagina_actual': pagina_obj.number,
        'total_paginas': paginador.num_pages,
        'total_documentos': paginador.count,
        'tiene_anterior': pagina_obj.has_previous(),
        'tiene_siguiente': pagina_obj.has_next(),
    })


@login_required(login_url='/login/')
def documento_detalle(request, tipo, doc_id):
    doc = obtener_documento(tipo, doc_id)
    if not doc:
        return JsonResponse({'error': 'Documento no encontrado'}, status=404)
    user = request.user
    if not puede_ver_documento(user, doc):
        return JsonResponse({'error': 'No tiene permisos para ver este documento'}, status=403)
    data = {
        'tipo': tipo,
        'id': str(doc.uuid),
        'nombre': doc.nombre,
        'codigo': doc.codigo,
        'descripcion': doc.descripcion,
        'estado': doc.estado,
        'version': doc.version_actual,
        'propietario': {'id': doc.propietario.id, 'nombre': doc.propietario.get_full_name() or doc.propietario.username},
        'fecha_creacion': doc.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
        'fecha_modificacion': doc.fecha_modificacion.strftime('%d/%m/%Y %H:%M'),
        'puede_editar': puede_editar(user, doc),
        'puede_descargar': puede_descargar(user, doc),
    }
    depto = getattr(doc, 'departamento', None)
    if depto:
        data['departamento'] = {'id': depto.id, 'codigo': depto.codigo, 'nombre': depto.nombre}
    return JsonResponse(data)


@login_required(login_url='/login/')
def cambiar_estado(request, tipo, doc_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    doc = obtener_documento(tipo, doc_id)
    if not doc:
        return JsonResponse({'error': 'Documento no encontrado'}, status=404)
    user = request.user
    puede, razon = puede_inhabilitar(user, doc)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    nuevo_estado = data.get('estado')
    motivo_seleccion = data.get('motivo_seleccion', '')
    motivo_texto = data.get('motivo_texto', '')
    if nuevo_estado not in ['activo', 'obsoleto']:
        return JsonResponse({'error': 'Estado inválido'}, status=400)
    if not puede:
        from autenticacion.models import Notificacion
        Notificacion.objects.create(
            usuario=doc.propietario,
            titulo=f'Solicitud de {"inhabilitar" if nuevo_estado == "obsoleto" else "habilitar"} documento',
            mensaje=f'{user.get_full_name() or user.username} solicita cambiar estado de "{doc.nombre}" a {nuevo_estado}. Motivo: {motivo_seleccion} - {motivo_texto}',
            tipo='sistema', url_accion='/documentos/',
            departamento=doc.departamento,
            documento=doc
        )
        return JsonResponse({'success': True, 'mensaje': 'Solicitud enviada al Jefe de Departamento para aprobación'})
    doc.estado = nuevo_estado
    doc.save(update_fields=['estado'])
    return JsonResponse({'success': True, 'mensaje': f'Documento {"habilitado" if nuevo_estado == "activo" else "inhabilitado"} correctamente'})


@login_required(login_url='/login/')
def transferir_propiedad(request, tipo, doc_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    doc = obtener_documento(tipo, doc_id)
    if not doc:
        return JsonResponse({'error': 'Documento no encontrado'}, status=404)
    user = request.user
    puede, razon = puede_transferir(user, doc)
    if not puede:
        return JsonResponse({'error': 'No tiene permisos para transferir este documento'}, status=403)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    nuevo_propietario_id = data.get('nuevo_propietario_id')
    if not nuevo_propietario_id:
        return JsonResponse({'error': 'Debe seleccionar un nuevo propietario'}, status=400)
    from django.contrib.auth.models import User
    try:
        nuevo_propietario = User.objects.get(id=nuevo_propietario_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'Usuario no encontrado'}, status=404)
    # Igual que la vista web: el nuevo propietario debe ser del mismo departamento.
    nuevo_perfil = getattr(nuevo_propietario, 'perfil', None)
    depto_doc = getattr(doc, 'departamento', None)
    if not nuevo_perfil or nuevo_perfil.departamento != depto_doc:
        return JsonResponse(
            {'error': 'El nuevo propietario debe pertenecer al mismo departamento del documento'},
            status=400
        )
    if nuevo_propietario.id == doc.propietario_id:
        return JsonResponse(
            {'error': 'El nuevo propietario debe ser distinto al actual'},
            status=400
        )
    from .services.transferencia import transferir_propiedad_documento
    from auditoria.middleware import resolver_ip_cliente
    transferir_propiedad_documento(
        doc, nuevo_propietario, user, ip_address=resolver_ip_cliente(request),
    )
    return JsonResponse({'success': True, 'mensaje': f'Propiedad transferida a {nuevo_propietario.get_full_name() or nuevo_propietario.username}'})


@login_required(login_url='/login/')
def opciones_filtros(request):
    return JsonResponse({
        'tipos': [
            {'valor': t.key, 'texto': t.nombre}
            for t in tipos_activos()
        ],
        'estados': [
            {'valor': 'borrador', 'texto': 'Borrador'},
            {'valor': 'activo', 'texto': 'Activo'},
            {'valor': 'obsoleto', 'texto': 'Obsoleto'},
        ],
    })


@login_required(login_url='/login/')
def contadores_documentos(request):
    user = request.user

    # Documentos propios + asignados (como coordinador). `distinct=True` en el
    # conteo evita duplicar filas por el JOIN con asignaciones (que puede tener
    # más de una fila activa para el mismo documento). Dos aggregates en total,
    # en vez de 1 consulta por tipo.
    todos_docs = Documento.objects.filter(
        Q(propietario=user)
        | Q(asignaciones__coordinador=user, asignaciones__activa=True)
    )

    # Contar por tipo (incluyendo tipos activos con 0 documentos)
    tipos_keys = [t.key for t in tipos_activos()]
    conteo_tipos = {
        item['tipo']: item['total']
        for item in todos_docs.values('tipo').annotate(total=Count('id', distinct=True))
    }
    por_tipo = {key: conteo_tipos.get(key, 0) for key in tipos_keys}

    # Contar por estado usando un único aggregate
    conteo_estados = {
        item['estado']: item['total']
        for item in todos_docs.values('estado').annotate(total=Count('id', distinct=True))
    }
    por_estado = {
        key: conteo_estados.get(key, 0)
        for key in ['borrador', 'en_revision', 'activo', 'obsoleto']
    }

    return JsonResponse({'por_tipo': por_tipo, 'por_estado': por_estado})


@login_required(login_url='/login/')
def usuarios_departamento(request):
    """Usuarios candidatos a nuevo propietario.

    Con `tipo` y `doc_id` usa el departamento DEL DOCUMENTO (necesario para el
    admin, que no tiene departamento) y excluye al propietario actual. Sin
    parámetros, usa el departamento del solicitante (comportamiento previo).
    """
    user = request.user
    doc_tipo = request.GET.get('tipo', '').strip()
    doc_id = request.GET.get('doc_id', '').strip()

    if doc_tipo and doc_id:
        documento = obtener_documento(doc_tipo, doc_id)
        if not documento:
            return JsonResponse({'error': 'Documento no encontrado'}, status=404)
        puede, _ = puede_transferir(user, documento)
        if not puede:
            return JsonResponse({'error': 'Sin permisos'}, status=403)
        depto = documento.departamento
        excluir_id = documento.propietario_id
    else:
        depto = user.perfil.departamento
        excluir_id = user.id

    if not depto:
        return JsonResponse({'usuarios': []})

    from django.contrib.auth.models import User
    usuarios = User.objects.filter(perfil__departamento=depto).exclude(id=excluir_id).values(
        'id', 'username', 'first_name', 'last_name'
    )
    lista = [{'id': u['id'], 'nombre': f"{u['first_name']} {u['last_name']}".strip() or u['username']} for u in usuarios]
    return JsonResponse({'usuarios': lista})


@login_required(login_url='/login/')
def documentos_por_departamento(request):
    """API: Documentos disponibles por departamento y tipo (reemplaza endpoints específicos)"""
    depto_id = request.GET.get('departamento')
    tipo = request.GET.get('tipo', '')
    buscar = request.GET.get('buscar', '').strip()
    if not depto_id:
        return JsonResponse({'documentos': [], 'total': 0})
    qs = Documento.objects.filter(departamento_id=depto_id, estado='activo')
    # Limitar a lo visible para el usuario: su propio departamento o documentos públicos.
    perfil = getattr(request.user, 'perfil', None)
    mi_depto_id = perfil.departamento_id if perfil else None
    visible = Q(clasificacion='publico')
    if mi_depto_id:
        visible |= Q(departamento_id=mi_depto_id)
    qs = qs.filter(visible)
    if tipo:
        qs = qs.filter(tipo=tipo)
    if buscar:
        qs = qs.filter(Q(nombre__icontains=buscar) | Q(codigo__icontains=buscar))
    docs = qs.values('id', 'uuid', 'nombre', 'codigo', 'estado', 'tipo')[:20]
    return JsonResponse({'documentos': list(docs), 'total': docs.count()})


@login_required(login_url='/login/')
def documentos_por_departamentos(request):
    """API: Documentos para múltiples departamentos"""
    depto_ids = request.GET.getlist('departamentos')
    tipo = request.GET.get('tipo', '')
    if not depto_ids:
        return JsonResponse({'documentos': []})
    qs = Documento.objects.filter(departamento_id__in=depto_ids, estado__in=['borrador', 'activo'])
    # Borradores solo del propio departamento; de otros departamentos solo públicos activos.
    perfil = getattr(request.user, 'perfil', None)
    mi_depto_id = perfil.departamento_id if perfil else None
    visible = Q(clasificacion='publico', estado='activo')
    if mi_depto_id:
        visible |= Q(departamento_id=mi_depto_id)
    qs = qs.filter(visible)
    if tipo:
        qs = qs.filter(tipo=tipo)
    docs = qs.values('id', 'uuid', 'nombre', 'codigo', 'estado', 'tipo')
    return JsonResponse({'documentos': list(docs)})


@login_required(login_url='/login/')
def documentos_referenciables(request):
    """API: Documentos accesibles para asociar/relacionar (búsqueda libre).

    Universo por defecto: propio departamento + públicos activos. Parámetros:
      - ``buscar``: filtra por nombre/código.
      - ``exclude_id``: excluye el documento actual.
      - ``departamentos``: ids extra (p. ej. afectados por una planilla).
      - ``incluir_borradores=1``: permite borrador en propio depto y en los
        departamentos extra (los públicos de otros siguen solo activos).
      - ``todos=1``: incluye documentos activos de todos los departamentos.
    """
    user = request.user
    perfil = getattr(user, 'perfil', None)
    buscar = request.GET.get('buscar', '').strip()
    exclude_id = request.GET.get('exclude_id', '')
    depto_ids = [d for d in request.GET.getlist('departamentos') if d.isdigit()]
    incluir_borradores = request.GET.get('incluir_borradores') in ('1', 'true', 'True')
    todos = request.GET.get('todos') in ('1', 'true', 'True')
    mi_depto_id = perfil.departamento_id if perfil else None

    qs = Documento.objects.all()
    if todos:
        qs = qs.filter(estado='activo')
    else:
        dep_ids = set(depto_ids)
        if mi_depto_id:
            dep_ids.add(mi_depto_id)
        estados = ['borrador', 'activo'] if incluir_borradores else ['activo']
        cond = Q(clasificacion='publico', estado='activo')
        if dep_ids:
            cond |= Q(departamento_id__in=dep_ids, estado__in=estados)
        qs = qs.filter(cond)

    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if buscar:
        qs = qs.filter(Q(nombre__icontains=buscar) | Q(codigo__icontains=buscar))
    docs = qs.select_related('tipo').distinct().values(
        'id', 'uuid', 'nombre', 'codigo', 'estado', 'tipo', 'departamento_id',
    )[:30]
    result = [
        {
            'id': d['id'],
            'uuid': str(d['uuid']),
            'nombre': d['nombre'],
            'codigo': d['codigo'],
            'estado': d['estado'],
            'tipo': d['tipo'],
            'departamento_id': d['departamento_id'],
        }
        for d in docs
    ]
    return JsonResponse({'documentos': result, 'total': len(result)})


# Compatibilidad: endpoints viejos que ahora son genéricos
@login_required(login_url='/login/')
def procedimientos_por_departamento(request):
    return documentos_por_departamento(request)

@login_required(login_url='/login/')
def formularios_por_departamento(request):
    return documentos_por_departamento(request)

@login_required(login_url='/login/')
def procedimientos_por_departamentos(request):
    return documentos_por_departamentos(request)

@login_required(login_url='/login/')
def formularios_por_departamento_planilla(request):
    return documentos_por_departamento(request)


@login_required(login_url='/login/')
def solicitar_acceso_documento(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    user = request.user
    documento_id = data.get('documento_id')
    motivo = data.get('motivo', '')
    if not (user.perfil.es_jefe or user.perfil.es_administrador):
        return JsonResponse({'error': 'Solo los Jefes de Departamento y Administradores pueden solicitar acceso'}, status=403)
    try:
        doc = Documento.objects.get(uuid=documento_id)
    except Documento.DoesNotExist:
        return JsonResponse({'error': 'Documento no encontrado'}, status=404)
    depto_doc = getattr(doc, 'departamento', None)
    if not depto_doc:
        return JsonResponse({'error': 'No se pudo determinar el departamento del documento'}, status=400)
    if depto_doc == user.perfil.departamento:
        return JsonResponse({'error': 'No puede solicitar acceso a documentos de su propio departamento'}, status=400)
    jefe_destino = depto_doc.jefe
    if not jefe_destino:
        return JsonResponse({'error': 'El departamento destino no tiene Jefe asignado'}, status=400)
    solicitud_existente = SolicitudAccesoDocumento.objects.filter(
        documento=doc, solicitante=user, estado='pendiente'
    ).exists()
    if solicitud_existente:
        return JsonResponse({'error': 'Ya tiene una solicitud pendiente para este documento'}, status=400)
    solicitud = SolicitudAccesoDocumento.objects.create(
        documento=doc, solicitante=user, jefe_destino=jefe_destino,
        departamento_destino=depto_doc, motivo=motivo
    )
    from autenticacion.models import Notificacion
    Notificacion.objects.create(
        usuario=jefe_destino, titulo='Solicitud de acceso a documento',
        mensaje=f'El Jefe {user.get_full_name() or user.username} solicita acceso al documento "{doc.nombre}" ({doc.codigo})',
        tipo='sistema', url_accion='/documentos/',
        departamento=doc.departamento,
        documento=doc
    )
    return JsonResponse({'success': True, 'mensaje': f'Solicitud enviada al Jefe del departamento {depto_doc.nombre}'})


@login_required(login_url='/login/')
def responder_solicitud_acceso(request, solicitud_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)
    user = request.user
    if not (user.perfil.es_jefe or user.perfil.es_administrador):
        return JsonResponse({'error': 'Solo los Jefes de Departamento y Administradores pueden responder solicitudes'}, status=403)
    try:
        solicitud = SolicitudAccesoDocumento.objects.get(id=solicitud_id)
    except SolicitudAccesoDocumento.DoesNotExist:
        return JsonResponse({'error': 'Solicitud no encontrada'}, status=404)
    if solicitud.jefe_destino != user:
        return JsonResponse({'error': 'No tiene permisos para responder esta solicitud'}, status=403)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    accion = data.get('accion')
    observaciones = data.get('observaciones', '')
    if accion not in ['aprobar', 'rechazar']:
        return JsonResponse({'error': 'Acción inválida'}, status=400)
    solicitud.estado = 'aprobada' if accion == 'aprobar' else 'rechazada'
    solicitud.fecha_respuesta = timezone.now()
    solicitud.observaciones = observaciones
    solicitud.save(update_fields=['estado', 'fecha_respuesta', 'observaciones'])
    from autenticacion.models import Notificacion
    Notificacion.objects.create(
        usuario=solicitud.solicitante,
        titulo=f'Solicitud de acceso {"aprobada" if accion == "aprobar" else "rechazada"}',
        mensaje=f'Su solicitud de acceso al documento ha sido {solicitud.estado}. {observaciones}',
        tipo='sistema', url_accion='/documentos/',
        departamento=solicitud.documento.departamento,
        documento=solicitud.documento
    )
    return JsonResponse({'success': True, 'mensaje': f'Solicitud {solicitud.estado} correctamente'})


@login_required(login_url='/login/')
def solicitudes_acceso_pendientes(request):
    user = request.user
    if not (user.perfil.es_jefe or user.perfil.es_administrador):
        return JsonResponse({'solicitudes': [], 'total': 0})
    solicitudes = SolicitudAccesoDocumento.objects.filter(
        jefe_destino=user, estado='pendiente'
    ).select_related('solicitante', 'departamento_destino', 'documento')
    lista = []
    for s in solicitudes:
        doc = s.documento
        lista.append({
            'id': s.id,
            'documento_tipo': doc.tipo_id if doc else '',
            'documento_id': str(doc.uuid) if doc else '',
            'documento_nombre': doc.nombre if doc else 'Documento eliminado',
            'documento_codigo': doc.codigo if doc else '',
            'solicitante': s.solicitante.get_full_name() or s.solicitante.username,
            'motivo': s.motivo,
            'fecha_solicitud': s.fecha_solicitud.strftime('%d/%m/%Y %H:%M'),
        })
    return JsonResponse({'solicitudes': lista, 'total': len(lista)})


# =====================================================================
# API: APROBACIÓN DE DOCUMENTOS
# =====================================================================

@login_required(login_url='/login/')
def solicitudes_aprobacion_pendientes(request):
    user = request.user
    if not (user.perfil.es_jefe or user.perfil.es_administrador):
        return JsonResponse({'solicitudes': [], 'total': 0})
    dept = user.perfil.departamento
    solicitudes = SolicitudAprobacionDocumento.objects.filter(
        estado='pendiente',
    ).filter(
        Q(aprobador=user) |
        Q(departamento_origen=dept, rol_aprobador='evaluador')
    ).select_related('solicitante', 'departamento_origen', 'documento').distinct()
    lista = [{
        'id': s.id,
        'documento_tipo': s.documento.tipo_id,
        'documento_id': str(s.documento.uuid),
        'codigo_documento': s.documento.codigo,
        'nombre_documento': s.documento.nombre,
        'solicitante': s.solicitante.get_full_name() or s.solicitante.username,
        'departamento': s.departamento_origen.nombre if s.departamento_origen else '',
        'fecha_solicitud': s.fecha_solicitud.strftime('%d/%m/%Y %H:%M'),
    } for s in solicitudes]
    return JsonResponse({'solicitudes': lista, 'total': len(lista)})


@login_required(login_url='/login/')
@transaction.atomic
def aprobar_creacion_documento(request, tipo, documento_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'mensaje': 'Método no permitido'}, status=405)
    user = request.user
    if not user.perfil.es_jefe:
        return JsonResponse({'success': False, 'mensaje': 'No tienes permisos'}, status=403)
    try:
        doc = Documento.objects.get(uuid=documento_id, tipo=tipo)
    except Documento.DoesNotExist:
        return JsonResponse({'success': False, 'mensaje': 'Documento no encontrado'}, status=404)
    solicitud = SolicitudAprobacionDocumento.objects.filter(
        documento=doc, estado='pendiente', aprobador=user,
    ).order_by('-fecha_solicitud').first()
    if not solicitud:
        return JsonResponse({'success': False, 'mensaje': 'Solicitud no encontrada'}, status=404)
    data = json.loads(request.body) if request.body else {}
    observaciones = data.get('observaciones', '')
    es_final = solicitud.es_final
    solicitud.aprobar(observaciones=observaciones)
    if es_final:
        from .models import SolicitudCambio
        from .services.planillas import OPERACIONES_CON_PLANILLA, encolar_generacion_planilla
        solicitudes_planilla = list(SolicitudCambio.objects.filter(
            documento=solicitud.documento,
            estado='en_revision',
            operacion__in=OPERACIONES_CON_PLANILLA,
        ))
        SolicitudCambio.objects.filter(documento=solicitud.documento, estado='en_revision').update(estado='aprobado')
        for sc in solicitudes_planilla:
            encolar_generacion_planilla(sc)
        from autenticacion.models import Notificacion
        usuario_a_notificar = solicitud.propietario or solicitud.solicitante
        Notificacion.objects.create(
            usuario=usuario_a_notificar, titulo='Documento aprobado',
            mensaje=f'Tu documento "{solicitud.documento.nombre}" ({solicitud.documento.codigo}) ha sido aprobado.{(" " + observaciones) if observaciones else ""}',
            tipo='aprobacion_documento', url_accion=f'/documentos/{tipo}/{documento_id}/',
            departamento=solicitud.documento.departamento,
            documento=solicitud.documento
        )
        return JsonResponse({'success': True, 'mensaje': 'Documento aprobado correctamente'})
    return JsonResponse({'success': True, 'mensaje': 'Evaluación registrada, pendiente de autorización'})


@login_required(login_url='/login/')
@transaction.atomic
def rechazar_creacion_documento(request, tipo, documento_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'mensaje': 'Método no permitido'}, status=405)
    user = request.user
    if not user.perfil.es_jefe:
        return JsonResponse({'success': False, 'mensaje': 'No tienes permisos'}, status=403)
    try:
        doc = Documento.objects.get(uuid=documento_id, tipo=tipo)
    except Documento.DoesNotExist:
        return JsonResponse({'success': False, 'mensaje': 'Documento no encontrado'}, status=404)
    solicitud = SolicitudAprobacionDocumento.objects.filter(
        documento=doc, estado='pendiente', aprobador=user,
    ).order_by('-fecha_solicitud').first()
    if not solicitud:
        return JsonResponse({'success': False, 'mensaje': 'Solicitud no encontrada'}, status=404)
    data = json.loads(request.body) if request.body else {}
    motivo = data.get('motivo', '')
    if not motivo:
        return JsonResponse({'success': False, 'mensaje': 'El motivo de rechazo es requerido'}, status=400)
    solicitud.rechazar(motivo=motivo)
    from .models import SolicitudCambio
    SolicitudCambio.objects.filter(documento=solicitud.documento, estado='en_revision').update(estado='rechazado')
    from autenticacion.models import Notificacion
    usuario_a_notificar = solicitud.propietario or solicitud.solicitante
    Notificacion.objects.create(
        usuario=usuario_a_notificar, titulo='Documento rechazado',
        mensaje=f'Tu documento "{solicitud.documento.nombre}" ({solicitud.documento.codigo}) ha sido rechazado. Motivo: {motivo}',
        tipo='rechazo_documento', url_accion=f'/documentos/{tipo}/{documento_id}/',
        departamento=solicitud.documento.departamento,
        documento=solicitud.documento
    )
    return JsonResponse({'success': True, 'mensaje': 'Documento rechazado correctamente'})


# =====================================================================
# API: APROBACIÓN DE PLANILLAS DE CAMBIOS
# =====================================================================

@login_required(login_url='/login/')
def planillas_pendientes(request):
    user = request.user
    if not (user.perfil.es_jefe or user.perfil.es_administrador):
        return JsonResponse({'planillas': [], 'total': 0})
    aprobaciones_pendientes = AprobacionPlanilla.objects.filter(
        aprobador=user, estado='pendiente'
    ).select_related('planilla', 'planilla__documento')
    lista = [{
        'id': ap.planilla.id,
        'uuid': str(ap.planilla.uuid),
        'codigo': ap.planilla.codigo,
        'documento_tipo': ap.planilla.documento.tipo_id if ap.planilla.documento_id else '',
        'documento_id': str(ap.planilla.documento.uuid) if ap.planilla.documento_id else '',
        'documento_nombre': ap.planilla.documento.nombre if ap.planilla.documento_id else '',
        'documento_codigo': ap.planilla.documento.codigo if ap.planilla.documento_id else '',
        'tipo_cambio': ap.planilla.get_tipo_cambio_display(),
        'responsable': ap.planilla.responsable.get_full_name() or ap.planilla.responsable.username,
        'fecha_creacion': ap.planilla.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
    } for ap in aprobaciones_pendientes]
    return JsonResponse({'planillas': lista, 'total': len(lista)})


@login_required(login_url='/login/')
@transaction.atomic
def aprobar_planilla(request, planilla_uuid):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'mensaje': 'Método no permitido'}, status=405)
    user = request.user
    if not user.perfil.es_jefe:
        return JsonResponse({'success': False, 'mensaje': 'No tienes permisos'}, status=403)
    try:
        planilla = PlanillaCambios.objects.get(uuid=planilla_uuid)
        aprobacion = AprobacionPlanilla.objects.get(planilla=planilla, aprobador=user, estado='pendiente')
    except (PlanillaCambios.DoesNotExist, AprobacionPlanilla.DoesNotExist):
        return JsonResponse({'success': False, 'mensaje': 'Planilla no encontrada'}, status=404)
    data = json.loads(request.body) if request.body else {}
    observaciones = data.get('observaciones', '')
    es_final = aprobacion.es_final
    aprobacion.estado = 'aprobada'
    aprobacion.observaciones = observaciones
    aprobacion.fecha_aprobacion = timezone.now()
    aprobacion.save()
    if es_final:
        planilla.estado = 'aprobado'
        planilla.fecha_aprobacion = timezone.now()
        planilla.save(update_fields=['estado', 'fecha_aprobacion'])
        if planilla.solicitud_cambio:
            planilla.solicitud_cambio.estado = 'aprobado'
            planilla.solicitud_cambio.save(update_fields=['estado'])
        doc, cambios = aplicar_planilla_al_documento(planilla, usuario=user)
        if doc:
            registrar_aprobacion_planilla(
                usuario=user, planilla=planilla, documento=doc,
                cambios=cambios, observaciones=observaciones,
            )
        from autenticacion.models import Notificacion
        Notificacion.objects.create(
            usuario=planilla.responsable, titulo='Planilla de Cambios aprobada',
            mensaje=f'Tu planilla {planilla.codigo} ha sido aprobada. Los cambios se han aplicado al documento.',
            tipo='aprobacion_documento', url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/',
            departamento=planilla.documento.departamento,
            documento=planilla.documento
        )
        return JsonResponse({'success': True, 'mensaje': 'Planilla aprobada y cambios aplicados correctamente'})
    hermano = AprobacionPlanilla.objects.filter(
        planilla=planilla, rol_aprobador='autorizador', estado='esperando',
    ).exclude(id=aprobacion.id).order_by('id').first()
    if hermano and hermano.aprobador_id:
        hermano.estado = 'pendiente'
        hermano.save(update_fields=['estado'])
    from autenticacion.models import Notificacion
    Notificacion.objects.create(
        usuario=planilla.responsable, titulo='Planilla de Cambios en autorización',
        mensaje=f'Tu planilla {planilla.codigo} ha sido evaluada. Pendiente de autorización final.',
        tipo='aprobacion_documento', url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/',
        departamento=planilla.documento.departamento,
        documento=planilla.documento
    )
    return JsonResponse({'success': True, 'mensaje': 'Evaluación registrada, pendiente de autorización'})


@login_required(login_url='/login/')
@transaction.atomic
def rechazar_planilla(request, planilla_uuid):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'mensaje': 'Método no permitido'}, status=405)
    user = request.user
    if not user.perfil.es_jefe:
        return JsonResponse({'success': False, 'mensaje': 'No tienes permisos'}, status=403)
    try:
        planilla = PlanillaCambios.objects.get(uuid=planilla_uuid)
        aprobacion = AprobacionPlanilla.objects.get(planilla=planilla, aprobador=user, estado='pendiente')
    except (PlanillaCambios.DoesNotExist, AprobacionPlanilla.DoesNotExist):
        return JsonResponse({'success': False, 'mensaje': 'Planilla no encontrada'}, status=404)
    data = json.loads(request.body) if request.body else {}
    motivo = data.get('motivo', '')
    if not motivo:
        return JsonResponse({'success': False, 'mensaje': 'El motivo de rechazo es requerido'}, status=400)
    aprobacion.estado = 'rechazada'
    aprobacion.observaciones = motivo
    aprobacion.fecha_aprobacion = timezone.now()
    aprobacion.save()
    hermano = AprobacionPlanilla.objects.filter(
        planilla=planilla,
    ).exclude(id=aprobacion.id).exclude(estado__in=('aprobada', 'rechazada', 'cancelada')).first()
    if hermano:
        hermano.estado = 'cancelada'
        hermano.save(update_fields=['estado'])
    planilla.estado = 'rechazado'
    planilla.save(update_fields=['estado'])
    if planilla.solicitud_cambio:
        planilla.solicitud_cambio.estado = 'rechazado'
        planilla.solicitud_cambio.save(update_fields=['estado'])
    doc = planilla.documento
    if doc:
        doc.estado = 'activo'
        doc.save(update_fields=['estado'])
    registrar_rechazo_planilla(
        usuario=user, planilla=planilla, documento=doc, observaciones=motivo,
    )
    from autenticacion.models import Notificacion
    Notificacion.objects.create(
        usuario=planilla.responsable, titulo='Planilla de Cambios rechazada',
        mensaje=f'Tu planilla {planilla.codigo} ha sido rechazada. Motivo: {motivo}',
        tipo='rechazo_documento', url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/',
        departamento=planilla.documento.departamento,
        documento=planilla.documento
    )
    return JsonResponse({'success': True, 'mensaje': 'Planilla rechazada correctamente'})


# =============================================================================
# API TIPOS DE DOCUMENTO
# =============================================================================

@login_required(login_url='/login/')
def tipos_documento_disponibles(request):
    tipos = [
        {
            'key': t.key, 'codigo': t.codigo, 'nombre': t.nombre,
            'implementado': True, 'url': f'/documentos/nuevo/{t.key}/',
        }
        for t in tipos_activos()
    ]
    return JsonResponse({'tipos': tipos})
