"""Vistas HTML y APIs JSON para diagramas interactivos (ORG y Mapa de Procesos)."""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from auditoria.middleware import get_client_ip
from auditoria.services import registrar_consulta, registrar_descarga, registrar_log_iso

from .cache_utils import departamentos_activos
from .models import Documento, VersionDocumento
from .services import acceso_formato
from .services.organigrama import (
    MAX_PAYLOAD_BYTES,
    TIPO_MAPA_PROCESOS,
    TIPO_ORGANIGRAMA,
    TIPOS_DIAGRAMA_INTERACTIVO,
    ConflictoEdicionError,
    OrganigramaValidationError,
    _asegurar_soporte_mapa_procesos,
    _asegurar_soporte_organigrama,
    crear_mapa_procesos_inicial,
    crear_organigrama_inicial,
    guardar_organigrama,
    listar_mapas_procesos_vigentes,
    obtener_datos_version,
    obtener_mapa_procesos_vigente,
    obtener_organigrama_vigente,
    parsear_json_estricto,
    puede_construir_mapa_procesos,
    puede_construir_organigrama,
    puede_exportar_organigrama,
    puede_ver_organigrama,
    validar_datos_organigrama,
)
from .services.organigrama_export import OrganigramaExportError, exportar_organigrama

MAX_EXPORT_PAYLOAD_BYTES = 65_536

logger = logging.getLogger(__name__)


def _catalogo_organigrama():
    deptos = [
        {'id': d.id, 'codigo': d.codigo, 'nombre': d.nombre}
        for d in departamentos_activos()
    ]
    empresas = [
        {'codigo': codigo, 'nombre': nombre}
        for codigo, nombre in Documento.EMPRESA_CHOICES
    ]
    return deptos, empresas


def _serializar_versiones(documento):
    versiones = list(
        VersionDocumento.objects
        .filter(documento=documento, activa=True)
        .defer('datos_organigrama')
        .select_related('creado_por')
        .order_by('-id')
    )
    resultado = []
    for v in versiones:
        creador = ''
        if v.creado_por_id:
            creador = v.creado_por.get_full_name() or v.creado_por.username
        resultado.append({
            'id': v.id,
            'numero_version': v.numero_version,
            'cambios': v.cambios or '',
            'creado_por': creador,
            'fecha_creacion': v.fecha_creacion.strftime('%d/%m/%Y %H:%M') if v.fecha_creacion else '',
            'es_vigente': v.numero_version == documento.version_actual,
        })
    return resultado


def _empresa_tabs_mapa(modo, request_user, empresa_activa):
    docs_por_emp = listar_mapas_procesos_vigentes()
    puede_const = puede_construir_mapa_procesos(request_user)
    tabs = []
    for emp_cod, emp_nom in Documento.EMPRESA_CHOICES:
        d_emp = docs_por_emp.get(emp_cod)
        if modo == 'editor' and puede_const:
            url = reverse('documentos:mapa_procesos_constructor', args=[emp_cod])
        elif d_emp is not None and puede_ver_organigrama(request_user, d_emp):
            url = reverse('documentos:mapa_procesos_visor', args=[d_emp.uuid])
        elif puede_const:
            url = reverse('documentos:mapa_procesos_constructor', args=[emp_cod])
        else:
            url = ''
        tabs.append({
            'codigo': emp_cod,
            'nombre': emp_nom,
            'documento': d_emp,
            'activo': emp_cod == empresa_activa,
            'url': url,
        })
    return tabs


def _contexto_diagrama(documento, modo, request_user, version_vis):
    deptos, empresas = _catalogo_organigrama()
    es_map = documento.tipo_id == TIPO_MAPA_PROCESOS
    emp = documento.empresa or ''
    emp_nom = dict(Documento.EMPRESA_CHOICES).get(emp, emp) if emp else ''

    if es_map:
        label = 'Mapa de Procesos'
        icon = 'fa-diagram-project'
        constructor_url = reverse('documentos:mapa_procesos_constructor', args=[emp or 'PTCA'])
        visor_url = reverse('documentos:mapa_procesos_visor', args=[documento.uuid])
        accesos_url = reverse('documentos:mapa_procesos_accesos')
        tabs = _empresa_tabs_mapa(modo, request_user, emp)
    else:
        label = 'Organigrama'
        icon = 'fa-sitemap'
        constructor_url = reverse('documentos:organigrama_constructor')
        visor_url = reverse('documentos:organigrama_visor', args=[documento.uuid])
        accesos_url = reverse('documentos:organigrama_accesos')
        tabs = []

    ctx = {
        'documento': documento,
        'sin_organigrama': False,
        'tipo': documento.tipo_id,
        'modo': modo,
        'puede_construir': puede_construir_organigrama(request_user, documento),
        'puede_exportar': puede_exportar_organigrama(request_user, documento),
        'es_admin': acceso_formato.es_administrador(request_user),
        'diagrama_accesos_url': accesos_url,
        'version_visualizada': version_vis,
        'datos_url': reverse('documentos:organigrama_datos_api', args=[documento.uuid]),
        'exportar_url': reverse('documentos:organigrama_exportar_api', args=[documento.uuid]),
        'departamentos_catalogo': deptos,
        'empresas_catalogo': empresas,
        'diagrama_kind': documento.tipo_id,
        'diagrama_label': label,
        'diagrama_icon': icon,
        'diagrama_empresa': emp,
        'diagrama_empresa_nombre': emp_nom,
        'diagrama_constructor_url': constructor_url,
        'diagrama_visor_url': visor_url,
        'empresa_tabs': tabs,
    }
    if modo == 'editor':
        ctx['guardar_url'] = reverse('documentos:organigrama_guardar_api', args=[documento.uuid])
        ctx['clasificaciones'] = Documento.CLASIFICACION_CHOICES
    return ctx


@login_required(login_url='/login/')
def organigrama_index(request):
    """Entrada principal de Organigrama desde menú lateral."""
    doc = obtener_organigrama_vigente()
    if doc is not None and puede_ver_organigrama(request.user, doc):
        return redirect('documentos:organigrama_visor', documento_id=doc.uuid)
    if puede_construir_organigrama(request.user, tipo_id=TIPO_ORGANIGRAMA):
        return redirect('documentos:organigrama_constructor')
    return render(request, 'documentos/organigrama_visor.html', {
        'documento': None,
        'sin_organigrama': True,
        'puede_construir': False,
        'es_admin': acceso_formato.es_administrador(request.user),
        'diagrama_accesos_url': reverse('documentos:organigrama_accesos'),
        'tipo': TIPO_ORGANIGRAMA,
        'diagrama_kind': TIPO_ORGANIGRAMA,
        'diagrama_label': 'Organigrama',
        'diagrama_icon': 'fa-sitemap',
    })


@login_required(login_url='/login/')
def organigrama_constructor(request):
    """Constructor interactivo del organigrama (administrador y editores asignados)."""
    if not puede_construir_organigrama(request.user, tipo_id=TIPO_ORGANIGRAMA):
        messages.error(
            request,
            'No tienes permiso de edición para el Organigrama. Solicita acceso al administrador.',
        )
        doc_existente = obtener_organigrama_vigente()
        if doc_existente and puede_ver_organigrama(request.user, doc_existente):
            return redirect('documentos:organigrama_visor', documento_id=doc_existente.uuid)
        return redirect('documentos:mis_documentos')

    doc = obtener_organigrama_vigente()
    if doc is None:
        doc = crear_organigrama_inicial(
            request.user, ip_address=get_client_ip(),
        )

    if doc.estado in ('en_revision', 'obsoleto'):
        messages.warning(
            request,
            'El organigrama no puede editarse mientras está en revisión u obsoleto.',
        )
        return redirect('documentos:organigrama_visor', documento_id=doc.uuid)

    return render(
        request,
        'documentos/organigrama_constructor.html',
        _contexto_diagrama(doc, 'editor', request.user, doc.version_actual),
    )


@login_required(login_url='/login/')
def organigrama_visor(request, documento_id):
    """Visor interactivo en modo solo lectura para todo el personal autorizado."""
    documento = get_object_or_404(
        Documento.objects.select_related('departamento', 'propietario'),
        uuid=documento_id,
        tipo_id=TIPO_ORGANIGRAMA,
    )
    if not puede_ver_organigrama(request.user, documento):
        messages.error(request, 'No tienes permisos para ver el organigrama.')
        return redirect('documentos:mis_documentos')

    version_req = (request.GET.get('version') or '').strip() or None
    version_obj, _ = obtener_datos_version(documento, version_str=version_req)
    version_vis = (
        version_obj.numero_version if version_obj is not None else documento.version_actual
    )
    return render(
        request,
        'documentos/organigrama_visor.html',
        _contexto_diagrama(documento, 'viewer', request.user, version_vis),
    )


@login_required(login_url='/login/')
def mapa_procesos_index(request):
    """Índice corporativo de Mapas de Procesos por empresa (PTCA, PTH, FP, FS)."""
    docs_por_emp = listar_mapas_procesos_vigentes()
    puede_const = puede_construir_mapa_procesos(request.user)
    tarjetas = []
    for emp_cod, emp_nom in Documento.EMPRESA_CHOICES:
        doc = docs_por_emp.get(emp_cod)
        puede_ver = bool(doc is not None and puede_ver_organigrama(request.user, doc))
        tarjetas.append({
            'codigo': emp_cod,
            'nombre': emp_nom,
            'documento': doc,
            'puede_ver': puede_ver,
            'puede_construir': puede_const and (doc is None or doc.estado not in ('en_revision', 'obsoleto')),
            'visor_url': reverse('documentos:mapa_procesos_visor', args=[doc.uuid]) if doc and puede_ver else '',
            'constructor_url': reverse('documentos:mapa_procesos_constructor', args=[emp_cod]) if puede_const else '',
            'detalle_url': reverse('documentos:detalle', args=[TIPO_MAPA_PROCESOS, doc.uuid]) if doc and puede_ver else '',
        })
    return render(request, 'documentos/mapa_procesos_index.html', {
        'tarjetas': tarjetas,
        'puede_construir': puede_const,
        'es_admin': acceso_formato.es_administrador(request.user),
        'accesos_url': reverse('documentos:mapa_procesos_accesos'),
    })


@login_required(login_url='/login/')
def mapa_procesos_constructor(request, empresa):
    """Constructor interactivo de Mapa de Procesos heredado del Organigrama (una instancia por empresa)."""
    emp = (empresa or '').strip().upper()
    if emp == 'PT':
        emp = 'PTCA'
    if emp not in Documento.EMPRESAS_VALIDAS:
        raise Http404('Empresa no válida para Mapa de Procesos.')

    if not puede_construir_mapa_procesos(request.user):
        messages.error(
            request,
            'No tienes permiso de edición para el Mapa de Procesos. Solicita acceso al administrador.',
        )
        doc_existente = obtener_mapa_procesos_vigente(emp)
        if doc_existente and puede_ver_organigrama(request.user, doc_existente):
            return redirect('documentos:mapa_procesos_visor', documento_id=doc_existente.uuid)
        return redirect('documentos:mapa_procesos_index')

    doc = obtener_mapa_procesos_vigente(emp)
    if doc is None:
        doc = crear_mapa_procesos_inicial(
            request.user, emp, ip_address=get_client_ip(),
        )

    if doc.estado in ('en_revision', 'obsoleto'):
        messages.warning(
            request,
            'El mapa de procesos no puede editarse mientras está en revisión u obsoleto.',
        )
        return redirect('documentos:mapa_procesos_visor', documento_id=doc.uuid)

    return render(
        request,
        'documentos/organigrama_constructor.html',
        _contexto_diagrama(doc, 'editor', request.user, doc.version_actual),
    )


@login_required(login_url='/login/')
def mapa_procesos_visor(request, documento_id):
    """Visor interactivo en modo solo lectura para un Mapa de Procesos por empresa."""
    documento = get_object_or_404(
        Documento.objects.select_related('departamento', 'propietario'),
        uuid=documento_id,
        tipo_id=TIPO_MAPA_PROCESOS,
    )
    if not puede_ver_organigrama(request.user, documento):
        messages.error(request, 'No tienes permisos para ver este mapa de procesos.')
        return redirect('documentos:mapa_procesos_index')

    version_req = (request.GET.get('version') or '').strip() or None
    version_obj, _ = obtener_datos_version(documento, version_str=version_req)
    version_vis = (
        version_obj.numero_version if version_obj is not None else documento.version_actual
    )
    return render(
        request,
        'documentos/organigrama_visor.html',
        _contexto_diagrama(documento, 'viewer', request.user, version_vis),
    )


@login_required(login_url='/login/')
@require_GET
def organigrama_datos_api(request, documento_id):
    """Devuelve metadatos, historial de versiones y el JSON de un diagrama interactivo (ORG o MAP)."""
    documento = get_object_or_404(
        Documento.objects.select_related('departamento', 'propietario', 'tipo'),
        uuid=documento_id,
        tipo_id__in=TIPOS_DIAGRAMA_INTERACTIVO,
    )
    if not puede_ver_organigrama(request.user, documento, requerir_aprobada=True):
        return JsonResponse(
            {'error': 'No tienes permisos para consultar este diagrama.'},
            status=403,
        )

    version_solicitada = (request.GET.get('version') or '').strip() or None
    version_obj, datos = obtener_datos_version(documento, version_str=version_solicitada)
    if version_obj is None and version_solicitada:
        return JsonResponse(
            {'error': f'La versión {version_solicitada} no existe.'},
            status=404,
        )

    version_efectiva = (
        version_obj.numero_version if version_obj is not None else documento.version_actual
    )
    try:
        registrar_consulta(
            usuario=request.user,
            recurso_tipo=documento.tipo_id,
            recurso_id=str(documento.uuid),
            version=version_efectiva,
            codigo=documento.codigo,
            ip_address=get_client_ip(),
            fail_closed=settings.AUDIT_FAIL_CLOSED,
        )
    except Exception as exc:
        logger.critical(
            'Consulta diagrama (%s) BLOQUEADA por fallo de auditoría: doc=%s usuario=%s: %s',
            documento.tipo_id, documento.codigo, request.user.username, exc,
        )
        return JsonResponse(
            {'error': 'No se pudo registrar la auditoría de consulta.'},
            status=503,
        )

    deptos, empresas = _catalogo_organigrama()
    return JsonResponse({
        'documento': {
            'uuid': str(documento.uuid),
            'codigo': documento.codigo,
            'nombre': documento.nombre,
            'empresa': documento.empresa or '',
            'tipo': documento.tipo_id,
            'descripcion': documento.descripcion or '',
            'clasificacion': documento.clasificacion,
            'estado': documento.estado,
            'version_actual': documento.version_actual,
            'proxima_version': documento.proxima_version(),
            'revision_edicion': documento.revision_edicion,
            'fecha_vigencia': (
                documento.fecha_vigencia.strftime('%d/%m/%Y')
                if documento.fecha_vigencia else ''
            ),
        },
        'version_visualizada': version_efectiva,
        'es_version_vigente': version_efectiva == documento.version_actual,
        'versiones': _serializar_versiones(documento),
        'departamentos': deptos,
        'empresas': empresas,
        'datos': datos,
    })


@login_required(login_url='/login/')
@require_POST
def organigrama_guardar_api(request, documento_id):
    """Guarda cambios del diagrama interactivo (ORG o MAP) conservando o subiendo versión."""
    documento = get_object_or_404(
        Documento, uuid=documento_id, tipo_id__in=TIPOS_DIAGRAMA_INTERACTIVO,
    )
    if not puede_construir_organigrama(request.user, documento):
        return JsonResponse(
            {'error': 'No tienes permisos para modificar este diagrama.'},
            status=403,
        )

    raw_body = request.body or b''
    if len(raw_body) > MAX_PAYLOAD_BYTES:
        return JsonResponse(
            {'error': 'El tamaño de los datos supera el máximo permitido (1 MB).'},
            status=413,
        )

    try:
        envelope = parsear_json_estricto(raw_body)
        if not isinstance(envelope, dict):
            raise OrganigramaValidationError('El cuerpo JSON debe ser un objeto.')
        datos_validados = validar_datos_organigrama(envelope.get('datos'))

        revision_esperada = envelope.get('revision_edicion')
        if isinstance(revision_esperada, bool) or not isinstance(revision_esperada, int):
            raise OrganigramaValidationError('revision_edicion debe ser un número entero.')

        incrementar_version = envelope.get('incrementar_version', False)
        if not isinstance(incrementar_version, bool):
            raise OrganigramaValidationError('incrementar_version debe ser booleano.')

        doc_guardado, _ = guardar_organigrama(
            documento=documento,
            usuario=request.user,
            datos_validados=datos_validados,
            revision_esperada=revision_esperada,
            incrementar_version=incrementar_version,
            cambios=envelope.get('cambios') or '',
            nombre=envelope.get('nombre'),
            descripcion=envelope.get('descripcion'),
            clasificacion=envelope.get('clasificacion'),
            ip_address=get_client_ip(),
        )
    except OrganigramaValidationError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except ConflictoEdicionError as exc:
        return JsonResponse(
            {
                'error': str(exc),
                'revision_actual': exc.revision_actual,
                'version_actual': exc.version_actual,
            },
            status=409,
        )

    return JsonResponse({
        'ok': True,
        'codigo': doc_guardado.codigo,
        'nombre': doc_guardado.nombre,
        'descripcion': doc_guardado.descripcion or '',
        'clasificacion': doc_guardado.clasificacion,
        'version_actual': doc_guardado.version_actual,
        'proxima_version': doc_guardado.proxima_version(),
        'revision_edicion': doc_guardado.revision_edicion,
        'hash_integridad': doc_guardado.hash_integridad,
        'versiones': _serializar_versiones(doc_guardado),
    })


@login_required(login_url='/login/')
@require_POST
def organigrama_exportar_api(request, documento_id):
    """Exporta todo o una selección del diagrama interactivo (ORG o MAP) en PDF, PNG, SVG, ODG o VSDX."""
    documento = get_object_or_404(
        Documento.objects.select_related('departamento', 'propietario', 'tipo'),
        uuid=documento_id,
        tipo_id__in=TIPOS_DIAGRAMA_INTERACTIVO,
    )
    if not puede_exportar_organigrama(request.user, documento):
        return JsonResponse(
            {'error': 'No tienes autorización para exportar este diagrama.'},
            status=403,
        )

    raw_body = request.body or b''
    if len(raw_body) > MAX_EXPORT_PAYLOAD_BYTES:
        return JsonResponse(
            {'error': 'El cuerpo de exportación excede el tamaño máximo permitido (64 KB).'},
            status=413,
        )

    try:
        payload = parsear_json_estricto(raw_body) if raw_body.strip() else {}
        if not isinstance(payload, dict):
            raise OrganigramaValidationError('El cuerpo JSON debe ser un objeto.')
    except OrganigramaValidationError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    version_raw = payload.get('version')
    if version_raw is not None and not isinstance(version_raw, str):
        return JsonResponse({'error': 'version debe ser una cadena.'}, status=400)
    version_req = (version_raw or '').strip() or None

    version_obj, datos = obtener_datos_version(documento, version_str=version_req)
    if version_obj is None and version_req:
        return JsonResponse(
            {'error': f'La versión {version_req} no existe.'},
            status=404,
        )

    version_efectiva = (
        version_obj.numero_version if version_obj is not None else documento.version_actual
    )
    es_obsoleto = (
        documento.estado == 'obsoleto' or version_efectiva != documento.version_actual
    )

    usuario_nombre = request.user.get_full_name() or request.user.username
    tipo_label = 'Mapa de Procesos' if documento.tipo_id == TIPO_MAPA_PROCESOS else 'Organigrama'
    meta = {
        'codigo': documento.codigo,
        'nombre': documento.nombre,
        'tipo_label': tipo_label,
        'version': version_efectiva,
        'fecha_vigencia': (
            documento.fecha_vigencia.strftime('%d/%m/%Y')
            if documento.fecha_vigencia else ''
        ),
        'usuario': usuario_nombre,
        'fecha_generacion': timezone.localtime().strftime('%d/%m/%Y %H:%M'),
    }

    try:
        contenido, filename, content_type, resumen_auditoria = exportar_organigrama(
            datos=datos,
            formato=payload.get('formato'),
            meta=meta,
            todo=payload.get('todo', True),
            section_ids=payload.get('section_ids'),
            empresas=payload.get('empresas'),
            incluir_sin_seccion=payload.get('incluir_sin_seccion'),
            mostrar_marcos=payload.get('incluir_marcos', True),
            papel=payload.get('papel', 'auto'),
            es_obsoleto=es_obsoleto,
        )
    except OrganigramaExportError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            'Error inesperado exportando diagrama %s: %s', documento.codigo, exc,
        )
        return JsonResponse(
            {'error': 'No se pudo generar el archivo de exportación.'},
            status=500,
        )

    try:
        registrar_descarga(
            usuario=request.user,
            recurso_tipo=documento.tipo_id,
            recurso_id=str(documento.uuid),
            version=version_efectiva,
            codigo=documento.codigo,
            detalles=resumen_auditoria,
            correlation_id=f'export-org-{documento.uuid}',
            ip_address=get_client_ip(),
            fail_closed=settings.AUDIT_FAIL_CLOSED,
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical(
            'Exportación diagrama (%s) BLOQUEADA por fallo de auditoría: doc=%s usuario=%s: %s',
            documento.tipo_id, documento.codigo, request.user.username, exc,
        )
        return JsonResponse(
            {'error': 'No se pudo registrar la auditoría de exportación.'},
            status=503,
        )

    resp = HttpResponse(contenido, content_type=content_type)
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


def _asegurar_tipo_soporte(tipo_key):
    if tipo_key == TIPO_MAPA_PROCESOS:
        return _asegurar_soporte_mapa_procesos()
    return _asegurar_soporte_organigrama()


def _meta_apartado_acceso(tipo_key):
    if tipo_key == TIPO_MAPA_PROCESOS:
        return (
            'Mapa de Procesos',
            'documentos:mapa_procesos_index',
            'documentos:mapa_procesos_accesos',
            'documentos:mapa_procesos_acceso_revocar',
        )
    return (
        'Organigrama',
        'documentos:organigrama_index',
        'documentos:organigrama_accesos',
        'documentos:organigrama_acceso_revocar',
    )


def _auditar_acceso_diagrama(request, tipo_obj, accion, detalles):
    try:
        registrar_log_iso(
            usuario=request.user,
            accion_tipo=accion,
            accion_categoria='documento',
            recurso_tipo=tipo_obj.key,
            recurso_id=tipo_obj.key,
            recurso_codigo=tipo_obj.codigo,
            recurso_nombre=tipo_obj.nombre,
            detalles=detalles or {},
            resultado_exitoso=True,
            resultado_mensaje=accion,
            ip_address=get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Auditoría %s (%s): %s', accion, tipo_obj.key, exc)


def _gestionar_acceso_diagrama(request, tipo_key):
    titulo, volver_url_name, accesos_url_name, revocar_url_name = _meta_apartado_acceso(tipo_key)
    if not acceso_formato.es_administrador(request.user):
        messages.error(request, 'Solo el administrador gestiona los accesos.')
        return redirect(volver_url_name)

    tipo_obj, _ = _asegurar_tipo_soporte(tipo_key)

    if request.method == 'POST':
        usuario = User.objects.filter(
            pk=request.POST.get('usuario_id'), is_active=True,
        ).first()
        if usuario is None:
            messages.error(request, 'Selecciona un usuario válido.')
        else:
            acceso_formato.otorgar_tipo(
                usuario,
                tipo_obj,
                nivel=acceso_formato.NIVEL_EDITAR,
                otorgado_por=request.user,
            )
            _auditar_acceso_diagrama(request, tipo_obj, f'{tipo_key}_acceso_otorgado', {
                'usuario_id': usuario.id,
                'nivel': acceso_formato.NIVEL_EDITAR,
            })
            messages.success(
                request,
                f'Permiso de edición en {titulo} otorgado a '
                f'{usuario.get_full_name() or usuario.username}.',
            )
        return redirect(accesos_url_name)

    concesiones = acceso_formato.concesiones_tipo(tipo_key)
    otorgados = set(concesiones.values_list('usuario_id', flat=True))
    usuarios = (
        User.objects
        .filter(is_active=True)
        .exclude(id__in=list(otorgados))
        .exclude(perfil__rol='administrador')
        .select_related('perfil', 'perfil__departamento')
        .order_by('first_name', 'last_name', 'username')
    )
    return render(request, 'documentos/diagrama_acceso.html', {
        'tipo_obj': tipo_obj,
        'titulo_apartado': titulo,
        'volver_url': reverse(volver_url_name),
        'revocar_url_name': revocar_url_name,
        'concesiones': concesiones,
        'usuarios': usuarios,
    })


def _revocar_acceso_diagrama(request, tipo_key, usuario_id):
    titulo, volver_url_name, accesos_url_name, _ = _meta_apartado_acceso(tipo_key)
    if not acceso_formato.es_administrador(request.user):
        messages.error(request, 'Solo el administrador gestiona los accesos.')
        return redirect(volver_url_name)

    tipo_obj, _ = _asegurar_tipo_soporte(tipo_key)
    usuario = get_object_or_404(User, pk=usuario_id)
    if acceso_formato.revocar_tipo(usuario, tipo_key):
        _auditar_acceso_diagrama(request, tipo_obj, f'{tipo_key}_acceso_revocado', {
            'usuario_id': usuario.id,
        })
        messages.success(
            request,
            f'Permiso de edición en {titulo} revocado a '
            f'{usuario.get_full_name() or usuario.username}.',
        )
    return redirect(accesos_url_name)


@login_required(login_url='/login/')
def organigrama_accesos(request):
    """Gestión de editores autorizados del Organigrama (solo Administrador)."""
    return _gestionar_acceso_diagrama(request, TIPO_ORGANIGRAMA)


@login_required(login_url='/login/')
@require_POST
def organigrama_acceso_revocar(request, usuario_id):
    """Revoca permiso de edición del Organigrama a un usuario (solo Administrador)."""
    return _revocar_acceso_diagrama(request, TIPO_ORGANIGRAMA, usuario_id)


@login_required(login_url='/login/')
def mapa_procesos_accesos(request):
    """Gestión de editores autorizados del Mapa de Procesos (solo Administrador)."""
    return _gestionar_acceso_diagrama(request, TIPO_MAPA_PROCESOS)


@login_required(login_url='/login/')
@require_POST
def mapa_procesos_acceso_revocar(request, usuario_id):
    """Revoca permiso de edición del Mapa de Procesos a un usuario (solo Administrador)."""
    return _revocar_acceso_diagrama(request, TIPO_MAPA_PROCESOS, usuario_id)
