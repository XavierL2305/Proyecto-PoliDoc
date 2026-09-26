import json
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Count, Value, CharField
from django.utils import timezone
from .models import Documento, Departamento, SolicitudAccesoDocumento, SolicitudAprobacionDocumento, PlanillaCambios, AprobacionPlanilla, VersionDocumento, AsignacionDocumento, TipoDocumento
from .permissions import (
    obtener_documento, puede_editar, puede_descargar,
    puede_inhabilitar, puede_transferir, documentos_usuario,
    puede_ver_documento
)


@login_required(login_url='/login/')
def buscar_sugerencias(request):
    """API: Autocomplete — retorna hasta 8 sugerencias por nombre, código o propietario"""
    q = request.GET.get('q', '').strip()
    try:
        limit = min(int(request.GET.get('limit', 8)), 20)
    except (TypeError, ValueError):
        limit = 8

    if len(q) < 2:
        return JsonResponse({'sugerencias': []})

    user = request.user
    perfil = getattr(user, 'perfil', None)

    # Scope de permisos a nivel ORM
    # Públicos: solo estado='activo'; propios: cualquier estado
    solo_activos = Q(es_publico=True, estado='activo')
    if perfil and perfil.es_administrador:
        permiso = Q()  # Admin ve todo
    elif perfil and perfil.es_jefe:
        depto = perfil.departamento
        permiso = Q(departamento=depto) | Q(propietario=user) | solo_activos if depto else Q(propietario=user) | solo_activos
    elif perfil and perfil.es_coordinador:
        permiso = Q(propietario=user) | solo_activos
        from .models import AsignacionDocumento
        permiso |= Q(asignaciones__coordinador=user, asignaciones__activa=True)
    else:
        permiso = Q(propietario=user) | solo_activos

    documentos = Documento.objects.filter(
        permiso & (
            Q(nombre__icontains=q) |
            Q(codigo__icontains=q) |
            Q(propietario__first_name__icontains=q) |
            Q(propietario__last_name__icontains=q) |
            Q(propietario__username__icontains=q)
        )
    ).select_related('departamento', 'propietario').distinct()[:limit]

    return JsonResponse({
        'sugerencias': [{
            'tipo': d.tipo_id,
            'id': str(d.uuid),
            'nombre': d.nombre,
            'codigo': d.codigo,
            'estado': d.estado,
            'tipo_display': d.tipo.codigo if d.tipo_id else '',
            'propietario': d.propietario.get_full_name() or d.propietario.username,
            'fecha_vencimiento': d.fecha_vencimiento.isoformat() if d.fecha_vencimiento else None,
        } for d in documentos]
    })


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
    doc.propietario = nuevo_propietario
    doc.save(update_fields=['propietario'])
    from autenticacion.models import Notificacion
    Notificacion.objects.create(
        usuario=nuevo_propietario, titulo='Propiedad transferida',
        mensaje=f'Se le ha asignado la propiedad del documento "{doc.nombre}" ({doc.codigo})',
        tipo='propiedad_transferida', url_accion='/documentos/',
        departamento=doc.departamento,
        documento=doc
    )
    return JsonResponse({'success': True, 'mensaje': f'Propiedad transferida a {nuevo_propietario.get_full_name() or nuevo_propietario.username}'})


@login_required(login_url='/login/')
def opciones_filtros(request):
    return JsonResponse({
        'tipos': [
            {'valor': t.key, 'texto': t.nombre}
            for t in TipoDocumento.objects.filter(activo=True).exclude(key='').order_by('codigo')
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

    # IDs de documentos propios
    docs_propios = Documento.objects.filter(propietario=user)

    # IDs de documentos asignados (como coordinador)
    docs_asignados = Documento.objects.filter(
        asignaciones__coordinador=user,
        asignaciones__activa=True
    ).exclude(propietario=user)

    # Unificar ambas querysets
    todos_docs = docs_propios | docs_asignados

    # Contar por tipo
    por_tipo = {}
    for tipo in TipoDocumento.objects.filter(activo=True).exclude(key=''):
        por_tipo[tipo.key] = todos_docs.filter(tipo=tipo.key).count()

    # Contar por estado usando aggregation
    conteo_estados = todos_docs.values('estado').annotate(total=Count('id'))
    por_estado = {item['estado']: item['total'] for item in conteo_estados}

    # Asegurar que todos los estados estén presentes
    for estado_key in ['borrador', 'en_revision', 'activo', 'obsoleto']:
        if estado_key not in por_estado:
            por_estado[estado_key] = 0

    return JsonResponse({'por_tipo': por_tipo, 'por_estado': por_estado})


@login_required(login_url='/login/')
def usuarios_departamento(request):
    user = request.user
    depto = user.perfil.departamento
    if not depto:
        return JsonResponse({'usuarios': []})
    from django.contrib.auth.models import User
    usuarios = User.objects.filter(perfil__departamento=depto).exclude(id=user.id).values(
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
    visible = Q(es_publico=True)
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
    visible = Q(es_publico=True, estado='activo')
    if mi_depto_id:
        visible |= Q(departamento_id=mi_depto_id)
    qs = qs.filter(visible)
    if tipo:
        qs = qs.filter(tipo=tipo)
    docs = qs.values('id', 'uuid', 'nombre', 'codigo', 'estado', 'tipo')
    return JsonResponse({'documentos': list(docs)})


@login_required(login_url='/login/')
def documentos_referenciables(request):
    """API: Documentos accesibles para referencias (Variante A).
    Mismo depto: activo. Otros deptos: activo y es_publico.
    Excluye el documento actual (si se pasa ?exclude_id=).
    """
    user = request.user
    perfil = getattr(user, 'perfil', None)
    buscar = request.GET.get('buscar', '').strip()
    exclude_id = request.GET.get('exclude_id', '')

    qs = Documento.objects.filter(estado='activo')
    if perfil and perfil.departamento_id:
        qs = qs.filter(
            Q(departamento_id=perfil.departamento_id) |
            Q(es_publico=True)
        )
    else:
        qs = qs.filter(es_publico=True)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if buscar:
        qs = qs.filter(Q(nombre__icontains=buscar) | Q(codigo__icontains=buscar))
    docs = qs.select_related('tipo').values(
        'id', 'uuid', 'nombre', 'codigo', 'tipo', 'departamento_id',
    )[:30]
    result = []
    for d in docs:
        depto_mismo = d['departamento_id'] == (perfil.departamento_id if perfil else None)
        result.append({
            'id': d['id'],
            'uuid': str(d['uuid']),
            'nombre': d['nombre'],
            'codigo': d['codigo'],
            'tipo': d['tipo'],
            'accesible': depto_mismo or True,
        })
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
        SolicitudCambio.objects.filter(documento=solicitud.documento, estado='en_revision').update(estado='aprobado')
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
        doc = planilla.documento
        if doc:
            if planilla.nombre_propuesto:
                doc.nombre = planilla.nombre_propuesto
            if planilla.descripcion_propuesta is not None:
                doc.descripcion = planilla.descripcion_propuesta
            if planilla.departamento_propuesto:
                doc.departamento = planilla.departamento_propuesto
            if planilla.archivo_propuesto:
                doc.archivo = planilla.archivo_propuesto
            nueva_version = doc.proxima_version()
            doc.version_actual = nueva_version
            doc.estado = 'activo'
            doc.fecha_vigencia = timezone.now()
            doc.save()
            VersionDocumento.objects.create(
                documento=doc, numero_version=nueva_version,
                archivo=planilla.archivo_propuesto if planilla.archivo_propuesto else doc.archivo,
                cambios=f'Planilla de Cambios {planilla.codigo}: {planilla.justificacion}',
                creado_por=planilla.responsable
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
        for t in TipoDocumento.objects.filter(activo=True).exclude(key='').order_by('codigo')
    ]
    return JsonResponse({'tipos': tipos})
