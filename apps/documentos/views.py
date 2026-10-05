import os
import json
import logging
import uuid
from datetime import datetime, date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.utils import timezone
from django.db.models import Q
from django.db import transaction
from django.conf import settings
from .models import (
    Documento, VersionDocumento, SolicitudDescarga, AprobacionDescarga,
    PlanillaCambios, AprobacionPlanilla, AsignacionDocumento,
    Departamento, TipoDocumento, SolicitudAprobacionDocumento,
    DocumentoAsociadoPlanilla, DistribucionDocumento, SolicitudAccesoDocumento,
    RenovacionVencimiento, ReferenciaDocumento, Formato, SeccionFormato, CampoFormato,
    SolicitudCambio, Periodo, FormatoPublicacion,
)
from .forms import (
    DocumentoForm, SolicitudDescargaForm, PlanillaCambiosForm,
    AsignacionDocumentoForm, FiltroDocumentoForm, DistribucionDocumentoForm,
    validar_respuestas, validar_respuestas_guardadas,
)
from .icons import ICONOS_DISPONIBLES
from .services.planillas import aplicar_planilla_al_documento
from auditoria.services import registrar_aprobacion_planilla, registrar_rechazo_planilla
from . import ai

logger = logging.getLogger(__name__)


def get_documento(tipo, documento_id):
    """Obtiene un documento por tipo y UUID"""
    try:
        return Documento.objects.get(uuid=documento_id, tipo=tipo)
    except Documento.DoesNotExist:
        raise Http404("Documento no encontrado")


def determinar_evaluador(usuario, departamento):
    """Determina el Evaluador.

    - Si el solicitante es jefe (de cualquier departamento) → evalúa un jefe
      par (`_evaluador_par`), evitando la autoevaluación.
    - Si no es jefe → jefe del departamento de origen.
    - Sin jefe de departamento → jefe de GG.
    - Si no hay ninguno → None (la cadena queda en espera).
    """
    perfil = getattr(usuario, 'perfil', None)
    if getattr(perfil, 'es_jefe', False):
        return _evaluador_par(usuario)

    gg_jefe = None
    try:
        gg_jefe = Departamento.objects.get(codigo='GG').jefe
    except Departamento.DoesNotExist:
        pass

    if departamento and departamento.jefe and departamento.jefe != usuario:
        return departamento.jefe

    if gg_jefe and gg_jefe != usuario:
        return gg_jefe

    return None


def _evaluador_par(usuario):
    """Jefe par que evalúa cuando el solicitante es jefe.

    1) Jefe del departamento designado (`APROBACION_EVALUADOR_DEPTO_GG`,
       default `SGC`).
    2) Primer departamento activo con jefe (orden por código), distinto al
       solicitante.
    """
    codigo = getattr(settings, 'APROBACION_EVALUADOR_DEPTO_GG', 'SGC')
    designado = Departamento.objects.filter(codigo=codigo, activo=True).first()
    if designado and designado.jefe and designado.jefe != usuario:
        return designado.jefe

    candidato = Departamento.objects.filter(
        activo=True, jefe__isnull=False,
    ).exclude(jefe=usuario).order_by('codigo').first()
    if candidato:
        return candidato.jefe

    return None


def determinar_autorizador():
    """Determina el Autorizador (jefe de GG). Sin fallback a admin.

    Si no hay jefe de GG, retorna None y la autorización queda en espera.
    """
    try:
        return Departamento.objects.get(codigo='GG').jefe
    except Departamento.DoesNotExist:
        return None


def crear_cadena_aprobacion_documento(documento, usuario, departamento, accion, **kwargs):
    """Crea la cadena de aprobación (evaluador + autorizador) para un documento.

    Siempre crea dos pasos. Si no hay jefe para un paso, el paso queda en
    'esperando' con aprobador=None hasta que se asigne el jefe correspondiente.
    El paso evaluador nunca es final; solo el autorizador (GG) activa el documento.

    Retorna la solicitud del evaluador (primer paso).
    """
    evaluador = determinar_evaluador(usuario, departamento)
    autorizador = determinar_autorizador()

    documentos_kwargs = dict(
        documento=documento,
        accion_solicitud=accion,
        solicitante=usuario,
        propietario=documento.propietario,
        departamento_origen=departamento,
    )
    documentos_kwargs.update(kwargs)

    sol_eval = SolicitudAprobacionDocumento.objects.create(
        aprobador=evaluador, rol_aprobador='evaluador', es_final=False,
        estado='pendiente' if evaluador else 'esperando', **documentos_kwargs,
    )
    SolicitudAprobacionDocumento.objects.create(
        aprobador=autorizador, rol_aprobador='autorizador', es_final=True,
        estado='esperando', **documentos_kwargs,
    )
    if evaluador:
        _notificar_aprobador(evaluador, usuario, documento, es_evaluador=True)
    return sol_eval


def crear_cadena_aprobacion_planilla(planilla, usuario, documento, **kwargs):
    """Crea la cadena de aprobación (evaluador + autorizador) para una planilla.

    Mismo criterio que documentos: siempre dos pasos, sin colapso ni fallback
    a admin. Retorna la aprobación del evaluador (primer paso).
    """
    departamento = getattr(documento, 'departamento', None)
    evaluador = determinar_evaluador(usuario, departamento)
    autorizador = determinar_autorizador()

    ap_eval = AprobacionPlanilla.objects.create(
        planilla=planilla, aprobador=evaluador,
        rol_aprobador='evaluador', es_final=False,
        estado='pendiente' if evaluador else 'esperando', **kwargs,
    )
    AprobacionPlanilla.objects.create(
        planilla=planilla, aprobador=autorizador,
        rol_aprobador='autorizador', es_final=True,
        estado='esperando', **kwargs,
    )
    if evaluador:
        _notificar_aprobador(evaluador, usuario, documento, es_evaluador=True, planilla=planilla)
    return ap_eval


def _notificar_aprobador(aprobador, solicitante, documento, es_evaluador=False, planilla=None):
    from autenticacion.models import Notificacion
    if planilla:
        titulo = 'Edición de documento pendiente de evaluación' if es_evaluador else 'Edición de documento pendiente de autorización'
        msg = (
            f'{solicitante.get_full_name() or solicitante.username} ha editado el documento '
            f'"{documento.nombre}" ({documento.codigo}) y requiere tu {"evaluación" if es_evaluador else "autorización"}.'
        )
        url = f'/documentos/planilla-cambios/{planilla.uuid}/aprobar/'
    else:
        titulo = 'Documento pendiente de evaluación' if es_evaluador else 'Documento pendiente de autorización'
        msg = (
            f'{solicitante.get_full_name() or solicitante.username} ha creado el documento '
            f'"{documento.nombre}" ({documento.codigo}) y requiere tu {"evaluación" if es_evaluador else "autorización"}.'
        )
        url = f'/documentos/{documento.tipo_id}/{documento.uuid}/'
    Notificacion.objects.create(
        usuario=aprobador, titulo=titulo, mensaje=msg,
        tipo='solicitud_aprobacion', url_accion=url,
        departamento=documento.departamento, documento=documento,
    )


def puede_editar_documento(user, documento):
    if documento.estado == 'obsoleto':
        return False
    if documento.estado == 'en_revision':
        return documento.propietario == user
    perfil = user.perfil
    if perfil.es_administrador:
        return True
    if documento.tipo_id and documento.tipo.key == 'descripcion_procesos':
        # DPR: cualquier jefe y los usuarios a los que se distribuyó el documento.
        if perfil.es_jefe:
            return True
        if DistribucionDocumento.objects.filter(documento=documento, usuario=user, revocado=False).exists():
            return True
    from .permissions import es_jefe_de_departamento
    if es_jefe_de_departamento(user, documento):
        return True
    if documento.propietario == user:
        return True
    if perfil.es_coordinador:
        asignacion = AsignacionDocumento.objects.filter(
            documento=documento, coordinador=user, activa=True
        ).first()
        if asignacion and asignacion.puede_editar:
            return True
    return False


def puede_eliminar_documento(user, documento):
    if documento.estado == 'obsoleto':
        return False
    if documento.estado == 'en_revision':
        return False
    perfil = user.perfil
    if perfil.es_administrador:
        return True
    from .permissions import es_jefe_de_departamento
    if es_jefe_de_departamento(user, documento):
        return True
    if documento.propietario == user:
        return True
    if perfil.es_coordinador:
        asignacion = AsignacionDocumento.objects.filter(
            documento=documento, coordinador=user, activa=True
        ).first()
        if asignacion and asignacion.puede_eliminar:
            return True
    return False


def _origen_para_numeracion(sol_cambio, respuestas):
    """Origen del cambio para la numeración (primera opción marcada, en orden)."""
    seleccionados = respuestas.get('origen_cambio') or []
    if isinstance(seleccionados, str):
        seleccionados = [seleccionados]
    if not seleccionados:
        return ''
    campo = next(
        (c for c in sol_cambio.formato.get_todos_los_campos() if c.clave == 'origen_cambio'),
        None,
    )
    if campo and campo.opciones:
        for opcion in campo.opciones:
            valor = opcion.get('valor', opcion) if isinstance(opcion, dict) else opcion
            if valor in seleccionados:
                return str(valor)
    return str(seleccionados[0])


def _autollenar_envio(sol_cambio, usuario):
    """Autollena campos de sistema al enviar un documento a revisión."""
    if not sol_cambio:
        return
    from .models import valores_auto_solicitud, mapear_valores_auto
    respuestas = dict(sol_cambio.respuestas)
    respuestas['fecha_solicitud'] = timezone.now().date().isoformat()
    if not respuestas.get('nombre_solicitante'):
        respuestas['nombre_solicitante'] = usuario.get_full_name() or usuario.username

    # Numeración DDMMAA-XXX por ORIGEN del cambio (instructivo SGC-IDL-011)
    hoy = timezone.now()
    fecha_txt = hoy.strftime('%d%m%y')
    fecha_solicitud = respuestas.get('fecha_solicitud')
    if fecha_solicitud:
        try:
            fecha_txt = datetime.fromisoformat(str(fecha_solicitud)).strftime('%d%m%y')
        except (ValueError, TypeError):
            pass

    origen = _origen_para_numeracion(sol_cambio, respuestas)
    prefijo = f"{fecha_txt}-"
    if (sol_cambio.num_solicitud
            and sol_cambio.num_solicitud.startswith(prefijo)
            and sol_cambio.origen_cambio == origen):
        num_solicitud = sol_cambio.num_solicitud
    else:
        consecutivo = SolicitudCambio.objects.filter(
            formato=sol_cambio.formato,
            origen_cambio=origen,
            num_solicitud__startswith=prefijo,
        ).exclude(id=sol_cambio.id).count() + 1
        num_solicitud = f"{prefijo}{consecutivo:03d}"

    sol_cambio.origen_cambio = origen
    sol_cambio.num_solicitud = num_solicitud
    respuestas['num_solicitud'] = num_solicitud

    respuestas.update(mapear_valores_auto(
        sol_cambio.formato, valores_auto_solicitud(sol_cambio)))
    sol_cambio.respuestas = respuestas
    sol_cambio.save(update_fields=['respuestas', 'origen_cambio', 'num_solicitud'])


def _procesar_referencias(doc, request, perfil):
    """Procesa referencias_pendientes_json del POST y las asocia al documento."""
    if doc.tipo and not doc.tipo.usa_documentos_relacionados:
        return
    referencias_raw = request.POST.get('referencias_pendientes_json', '[]')
    try:
        referencias_codigos = json.loads(referencias_raw)
    except (json.JSONDecodeError, TypeError):
        referencias_codigos = []

    for ref_codigo in referencias_codigos:
        ref_codigo = ref_codigo.strip().upper()
        if not ref_codigo:
            continue
        destino = Documento.objects.filter(codigo=ref_codigo).first()
        if destino:
            mismo_depto = destino.departamento_id == perfil.departamento_id
            es_accesible = mismo_depto or destino.clasificacion == 'publico'
            if es_accesible and destino.estado == 'activo':
                doc.documentos_relacionados.add(destino)
            elif not destino.activo if hasattr(destino, 'activo') else destino.estado != 'activo':
                ReferenciaDocumento.objects.get_or_create(
                    documento=doc, codigo_destino=ref_codigo,
                    defaults={'creado_por': request.user}
                )
            else:
                messages.warning(
                    request,
                    f'Sin acceso al documento {ref_codigo}. No se agregó la referencia.'
                )
        else:
            ReferenciaDocumento.objects.get_or_create(
                documento=doc, codigo_destino=ref_codigo,
                defaults={'creado_por': request.user}
            )


def _selector_relacionados(documento=None):
    """Config del selector compartido para "Documentos Relacionados" (crear/editar)."""
    initial = []
    if documento is not None:
        for d in documento.documentos_relacionados.all():
            initial.append({
                'id': d.id, 'uuid': str(d.uuid), 'codigo': d.codigo,
                'nombre': d.nombre, 'estado': d.estado, 'tipo': d.tipo_id,
            })
        for ref in documento.referencias_salientes.filter(documento_destino__isnull=True):
            initial.append({'codigo': ref.codigo_destino, 'pendiente': True})
    return {
        'id': 'selector-relacionados',
        'search_url': '/documentos/api/referenciables/',
        'exclude_id': documento.id if documento is not None else '',
        'placeholder': 'Buscar por nombre o código...',
        'payload_mode': 'inputs',
        'hidden_name': 'documentos_relacionados',
        'json_name': 'referencias_pendientes_json',
        'permite_pendiente': True,
        'initial_items': initial,
    }


def _procesar_asociados_planilla(planilla, request, permitido, documento, usuario):
    """Crea los DocumentoAsociadoPlanilla del POST validando el acceso.

    Acepta el JSON ``documentos_asociados_json`` con items
    ``{uuid, codigo, nombre, estado, tipo, pendiente}``: los existentes se
    asocian; los ``pendiente`` (código aún inexistente) se guardan en
    ``planilla.referencias_pendientes``. Solo se asocian documentos que el
    usuario puede ver: públicos, propios, de un departamento afectado por la
    planilla (o cualquiera si ``afecta_todos``) o ya relacionados con el base.
    """
    if not permitido:
        return
    departamento_usuario = getattr(getattr(usuario, 'perfil', None), 'departamento_id', None)
    filtro_asociables = (
        Q(clasificacion='publico')
        | Q(departamento_id=departamento_usuario)
        | Q(id__in=documento.documentos_relacionados.values_list('id', flat=True))
    )
    if planilla.afecta_todos:
        filtro_asociables |= Q(estado='activo')
    else:
        filtro_asociables |= Q(
            departamento_id__in=planilla.departamentos_afectados.values_list('id', flat=True)
        )
    asociables = Documento.objects.filter(estado__in=['borrador', 'activo']).filter(filtro_asociables)
    try:
        items = json.loads(request.POST.get('documentos_asociados_json', '[]'))
    except (json.JSONDecodeError, TypeError):
        return
    pendientes = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('pendiente'):
            codigo = str(item.get('codigo', '')).strip().upper()
            if codigo:
                pendientes.append(codigo)
            continue
        doc_asoc = asociables.filter(uuid=item.get('uuid')).first()
        if doc_asoc:
            DocumentoAsociadoPlanilla.objects.get_or_create(
                planilla=planilla, documento=doc_asoc
            )
    planilla.referencias_pendientes = pendientes
    planilla.save(update_fields=['referencias_pendientes'])


@login_required(login_url='/login/')
def formularios(request):
    user = request.user
    dept = user.perfil.departamento
    docs = Documento.objects.filter(tipo='formulario', estado='activo').select_related('departamento', 'propietario')
    if user.perfil.es_administrador:
        pass
    elif user.perfil.es_jefe:
        docs = docs.filter(Q(propietario=user) | Q(departamento=dept) | Q(clasificacion='publico'))
    else:
        docs = docs.filter(Q(departamento=dept) | Q(clasificacion='publico'))

    from .permissions import descarga_autorizada, puede_descargar

    solicitudes = SolicitudDescarga.objects.filter(
        solicitado_por=user
    ).values_list('documento__uuid', 'estado')
    docs_pendientes = {uuid for uuid, e in solicitudes if e == 'pendiente'}

    docs = list(docs)
    docs_aprobadas = {doc.uuid for doc in docs if descarga_autorizada(doc, user)}
    docs_descargables = {
        doc.uuid for doc in docs
        if puede_descargar(user, doc) or doc.uuid in docs_aprobadas
    }

    if user.perfil.es_administrador:
        from collections import OrderedDict
        por_depto = OrderedDict()
        for d in docs:
            key = d.departamento.codigo if d.departamento else 'SIN'
            if key not in por_depto:
                por_depto[key] = {
                    'id': key,
                    'titulo': d.departamento.nombre if d.departamento else 'Sin Departamento',
                    'documentos': [],
                }
            por_depto[key]['documentos'].append(d)
        grupos = list(por_depto.values())
    else:
        mi_depto = []
        otros = []
        for d in docs:
            if dept and d.departamento_id == dept.id:
                mi_depto.append(d)
            else:
                otros.append(d)
        grupos = []
        if mi_depto:
            grupos.append({'id': 'mi-depto', 'titulo': 'Mi Departamento', 'documentos': mi_depto})
        if otros:
            grupos.append({'id': 'otros', 'titulo': 'Otros Departamentos', 'documentos': otros})

    return render(request, 'documentos/formularios.html', {
        'grupos': grupos,
        'docs_descargables': docs_descargables,
        'docs_pendientes': docs_pendientes,
        'hoy': date.today(),
        'dias7': date.today() + timedelta(days=7),
    })


@login_required(login_url='/login/')
def mis_documentos(request):
    from django.core.paginator import Paginator
    user = request.user
    perfil = user.perfil
    buscar = request.GET.get('buscar', '').strip()
    estado_filter = request.GET.get('estado', '')
    clasificacion_filter = request.GET.get('clasificacion', '')

    from .permissions import q_documentos_visibles

    # Documentos visibles que casan por sus propios campos: permiten encontrar
    # documentos propios/asignados por sus relacionados (M2M) o por referencias
    # pendientes guardadas en ReferenciaDocumento.codigo_destino.
    target_ids = []
    if buscar:
        target_ids = list(
            Documento.objects.filter(
                q_documentos_visibles(user),
                Q(nombre__icontains=buscar) | Q(codigo__icontains=buscar),
            ).values_list('id', flat=True)
        )

    tipos_documento = []
    for tipo in TipoDocumento.objects.filter(activo=True).exclude(key='').order_by('codigo'):
        tipo_key = tipo.key
        docs = Documento.objects.filter(propietario=user, tipo=tipo_key)
        if perfil.es_coordinador:
            asignaciones = AsignacionDocumento.objects.filter(
                coordinador=user, activa=True, puede_leer=True,
                documento__tipo=tipo_key
            )
            ids_asignados = asignaciones.values_list('documento_id', flat=True)
            if ids_asignados:
                docs = docs | Documento.objects.filter(id__in=ids_asignados)
        if buscar:
            docs = docs.filter(
                Q(nombre__icontains=buscar) | Q(codigo__icontains=buscar)
                | Q(documentos_relacionados__in=target_ids)
                | Q(referencias_salientes__codigo_destino__icontains=buscar)
            ).distinct()
        if estado_filter:
            docs = docs.filter(estado=estado_filter)
        if clasificacion_filter:
            docs = docs.filter(clasificacion=clasificacion_filter)
        if docs.exists():
            paginator = Paginator(docs, 12)
            page = request.GET.get(f'page_{tipo_key}', 1)
            tipos_documento.append({
                'key': tipo_key,
                'nombre': tipo.nombre,
                'codigo': tipo.codigo,
                'icono': tipo.icono or 'file',
                'color': tipo.color or 'primary',
                'documentos': paginator.get_page(page),
            })

    return render(request, 'documentos/mis_documentos.html', {
        'tipos_documento': tipos_documento,
        'buscar': buscar,
        'estado_filter': estado_filter,
        'clasificacion_filter': clasificacion_filter,
        'clasificacion_choices': Documento.CLASIFICACION_CHOICES,
        'hoy': date.today(),
        'dias7': date.today() + timedelta(days=7),
    })


@login_required(login_url='/login/')
def buscar(request):
    """Resultados de búsqueda (navbar). Usa los mismos visibles que el dropdown."""
    from django.core.paginator import Paginator

    from .services.busqueda import buscar_documentos_visibles

    q = request.GET.get('q', '').strip()
    resultados = None
    if len(q) >= 2:
        queryset, _ = buscar_documentos_visibles(request.user, q)
        resultados = Paginator(queryset, 20).get_page(request.GET.get('page'))
    return render(request, 'documentos/resultados_busqueda.html', {
        'q': q, 'resultados': resultados,
    })


@login_required(login_url='/login/')
def bandeja_aprobaciones(request):
    perfil = request.user.perfil
    if not perfil.es_jefe:
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('documentos:mis_documentos')
    filtro_estado = request.GET.get('estado', 'pendiente')
    filtro_tipo = request.GET.get('tipo_accion', '')
    dept = perfil.departamento
    solicitudes = SolicitudAprobacionDocumento.objects.filter(
        Q(aprobador=request.user) |
        Q(departamento_origen=dept, rol_aprobador='evaluador')
    ).select_related('solicitante', 'departamento_origen', 'propietario', 'documento').distinct()
    if filtro_estado:
        solicitudes = solicitudes.filter(estado=filtro_estado)
    if filtro_tipo:
        solicitudes = solicitudes.filter(accion_solicitud=filtro_tipo)
    from django.core.paginator import Paginator
    paginator = Paginator(solicitudes, 20)
    page = request.GET.get('page', 1)
    solicitudes_page = paginator.get_page(page)
    total_pendientes = SolicitudAprobacionDocumento.objects.filter(
        Q(aprobador=request.user, estado='pendiente') |
        Q(departamento_origen=dept, rol_aprobador='evaluador', estado='pendiente')
    ).distinct().count()
    descargas = AprobacionDescarga.objects.filter(
        aprobador=request.user
    ).select_related('solicitud', 'solicitud__documento', 'solicitud__solicitado_por')
    if filtro_estado:
        descargas = descargas.filter(estado=filtro_estado)
    total_pendientes_descarga = AprobacionDescarga.objects.filter(
        aprobador=request.user, estado='pendiente'
    ).count()
    return render(request, 'documentos/bandeja_aprobaciones.html', {
        'solicitudes': solicitudes_page,
        'filtro_estado': filtro_estado,
        'filtro_tipo': filtro_tipo,
        'total_pendientes': total_pendientes,
        'descargas': descargas,
        'total_pendientes_descarga': total_pendientes_descarga,
    })


def _grupos_documentos(departamentos, clasificacion='', vigencia='', usuario=None):
    """Agrupa documentos por departamento (propio | afectado) y por tipo, con
    consultas fijas (sin N+1). Reutilizado por jefe y admin.

    `departamentos`: lista ordenada de Departamento.
    `clasificacion`: filtro opcional por clasificación de seguridad.
    `vigencia`: filtro opcional ('vencido'|'vence_pronto'|'vigente'|'sin_vigencia').
    `usuario`: si se pasa, solo se incluyen documentos visibles para él.
    """
    from .permissions import q_documentos_visibles

    tipos = list(
        TipoDocumento.objects.filter(activo=True).exclude(key='').order_by('codigo')
    )
    agrupado = {d.id: {} for d in departamentos}
    generales_por_tipo = {}

    docs = Documento.objects.filter(tipo__activo=True).exclude(tipo__isnull=True)
    if usuario is not None:
        docs = docs.filter(q_documentos_visibles(usuario))
    if clasificacion:
        docs = docs.filter(clasificacion=clasificacion)
    if vigencia:
        hoy = date.today()
        if vigencia == 'vencido':
            docs = docs.filter(fecha_vencimiento__isnull=False, fecha_vencimiento__lte=hoy)
        elif vigencia == 'vence_pronto':
            docs = docs.filter(
                fecha_vencimiento__gt=hoy, fecha_vencimiento__lte=hoy + timedelta(days=7),
            )
        elif vigencia == 'vigente':
            docs = docs.filter(fecha_vencimiento__gt=hoy + timedelta(days=7))
        elif vigencia == 'sin_vigencia':
            docs = docs.filter(fecha_vencimiento__isnull=True)
    docs = (
        docs.select_related('tipo')
        .prefetch_related('departamentos_afectados')
        .order_by('fecha_vencimiento' if vigencia else '-fecha_modificacion')
    )
    for doc in docs:
        if doc.afecta_todos:
            generales_por_tipo.setdefault(doc.tipo_id, []).append(doc)
            continue
        afecta = set()
        if doc.departamento_id in agrupado:
            afecta.add(doc.departamento_id)
        for dep in doc.departamentos_afectados.all():
            if dep.id in agrupado:
                afecta.add(dep.id)
        for dep_id in afecta:
            agrupado[dep_id].setdefault(doc.tipo_id, []).append(doc)

    def _tipos_de(por_tipo):
        return [
            {
                'key': tipo.key,
                'nombre': tipo.nombre,
                'icono': tipo.icono or 'file',
                'color': tipo.color or 'primary',
                'documentos': por_tipo[tipo.key],
            }
            for tipo in tipos if tipo.key in por_tipo
        ]

    generales = _tipos_de(generales_por_tipo)

    grupos = []
    for dep in departamentos:
        tipos_documento = _tipos_de(agrupado[dep.id])
        if tipos_documento:
            grupos.append({'departamento': dep, 'tipos_documento': tipos_documento})
    return generales, grupos


def _conteos_vigencia(departamentos, clasificacion, usuario):
    """Conteos de estado de vigencia para el panel del jefe/admin."""
    from .permissions import q_documentos_visibles

    hoy = date.today()
    dep_ids = [d.id for d in departamentos]
    base = Documento.objects.filter(tipo__activo=True).exclude(tipo__isnull=True)
    base = base.filter(q_documentos_visibles(usuario))
    if clasificacion:
        base = base.filter(clasificacion=clasificacion)
    base = base.filter(
        Q(afecta_todos=True)
        | Q(departamento_id__in=dep_ids)
        | Q(departamentos_afectados__id__in=dep_ids)
    ).distinct()
    return {
        'vencidos': base.filter(
            fecha_vencimiento__isnull=False, fecha_vencimiento__lte=hoy,
        ).count(),
        'vence_pronto': base.filter(
            fecha_vencimiento__gt=hoy, fecha_vencimiento__lte=hoy + timedelta(days=7),
        ).count(),
        'vigentes': base.filter(fecha_vencimiento__gt=hoy + timedelta(days=7)).count(),
        'sin_vigencia': base.filter(fecha_vencimiento__isnull=True).count(),
    }


@login_required(login_url='/login/')
def documentos_departamento(request):
    perfil = request.user.perfil
    es_admin = perfil.es_administrador
    if not (perfil.es_jefe or es_admin):
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('documentos:mis_documentos')

    departamento = perfil.departamento
    clasificacion_filter = request.GET.get('clasificacion', '')
    vigencia_filter = request.GET.get('vigencia', '')
    if es_admin:
        departamentos = list(
            Departamento.objects.filter(activo=True).order_by('codigo')
        )
        solicitudes_pendientes = SolicitudDescarga.objects.filter(estado='pendiente')
    else:
        if not departamento:
            messages.error(request, 'No tienes un departamento asignado.')
            return redirect('documentos:mis_documentos')
        departamentos = [departamento]
        solicitudes_pendientes = SolicitudDescarga.objects.filter(
            estado='pendiente',
            documento__departamento=departamento,
        )

    generales, grupos = _grupos_documentos(
        departamentos, clasificacion_filter, vigencia_filter, usuario=request.user,
    )
    conteos_vigencia = _conteos_vigencia(
        departamentos, clasificacion_filter, request.user,
    )
    return render(request, 'documentos/documentos_departamento.html', {
        'generales': generales,
        'grupos': grupos,
        'departamento': departamento,
        'es_admin': es_admin,
        'clasificacion_filter': clasificacion_filter,
        'clasificacion_choices': Documento.CLASIFICACION_CHOICES,
        'vigencia_filter': vigencia_filter,
        'conteos_vigencia': conteos_vigencia,
        'solicitudes_pendientes': solicitudes_pendientes,
        'hoy': date.today(),
        'dias7': date.today() + timedelta(days=7),
    })


@login_required(login_url='/login/')
def seleccionar_tipo(request):
    """Página/catálogo para seleccionar tipo de documento a crear"""
    es_jefe = request.user.perfil.es_jefe
    tipos_disponibles = []
    for tipo in TipoDocumento.objects.filter(activo=True).exclude(key='').order_by('codigo'):
        if tipo.solo_jefes and not es_jefe:
            continue
        tipos_disponibles.append({
            'key': tipo.key,
            'codigo': tipo.codigo,
            'nombre': tipo.nombre,
            'icono': tipo.icono or 'file',
            'color': tipo.color or 'primary',
        })
    return render(request, 'documentos/seleccionar_tipo.html', {
        'tipos': tipos_disponibles,
    })


@login_required(login_url='/login/')
def crear_documento(request, tipo):
    """Vista genérica para crear cualquier tipo de documento"""
    tipo_obj = TipoDocumento.objects.filter(key=tipo, activo=True).first()
    if not tipo_obj:
        messages.error(request, 'Tipo de documento no válido.')
        return redirect('documentos:mis_documentos')

    perfil = request.user.perfil
    if not perfil.departamento:
        messages.error(request, 'No tienes un departamento asignado. Contacta al administrador.')
        return redirect('documentos:mis_documentos')

    tipo_codigo = tipo_obj.codigo
    tipo_nombre = tipo_obj.nombre

    if tipo_obj.solo_jefes and not perfil.es_jefe:
        messages.error(request, 'Solo los jefes de departamento pueden crear este tipo de documento.')
        return redirect('documentos:seleccionar_tipo')

    sin_prefijo_depto = Documento.es_sin_prefijo_depto(tipo)

    formato_dinamico = Formato.objects.filter(
        activo=True, aplica_creacion=True
    ).order_by('codigo').first()

    respuestas_formato = {}

    if request.method == 'POST':
        accion = request.POST.get('accion', 'enviar')
        form = DocumentoForm(
            request.POST, request.FILES,
            departamento_id=perfil.departamento.id,
            tipo_documento=tipo
        )
        if form.is_valid():
            secuencia = request.POST.get('secuencia', '001')
            codigo = Documento.construir_codigo(perfil.departamento, tipo, secuencia)

            respuestas_formato, errores_formato = {}, []
            if formato_dinamico:
                respuestas_formato, errores_formato = validar_respuestas(
                    formato_dinamico, request.POST, parcial=(accion == 'borrador')
                )
                if errores_formato:
                    for err in errores_formato:
                        messages.error(request, err)

            if Documento.objects.filter(codigo=codigo).exists():
                messages.error(request, f'El código {codigo} ya existe. Usa otro número de secuencia.')
            elif not errores_formato:
                with transaction.atomic():
                    doc = form.save(commit=False)
                    doc.tipo = TipoDocumento.objects.get(key=tipo)
                    doc.propietario = request.user
                    doc.departamento = perfil.departamento
                    doc.estado = 'borrador'
                    doc.codigo = codigo
                    doc.save()
                    form.save_m2m()
                    VersionDocumento.objects.create(
                        documento=doc, numero_version='1',
                        archivo=doc.archivo or None,
                        cambios='Versión inicial', creado_por=request.user
                    )

                    sol_cambio = None
                    if formato_dinamico:
                        codigo_sol = f"SC-{timezone.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
                        sol_cambio = SolicitudCambio.objects.create(
                            codigo=codigo_sol,
                            formato=formato_dinamico,
                            operacion='creacion',
                            documento=doc,
                            solicitante=request.user,
                            departamento=perfil.departamento,
                            respuestas=respuestas_formato if respuestas_formato else {},
                            estado='borrador',
                        )

                    solicitud_aprob = None
                    if accion != 'borrador':
                        if sol_cambio:
                            sol_cambio.estado = 'en_revision'
                            sol_cambio.save(update_fields=['estado'])
                            _autollenar_envio(sol_cambio, request.user)
                        solicitud_aprob = crear_cadena_aprobacion_documento(
                            documento=doc, usuario=request.user,
                            departamento=perfil.departamento, accion='creacion',
                        )
                        doc.estado = 'en_revision'
                        doc.save(update_fields=['estado'])

                    _procesar_referencias(doc, request, perfil)

                if not request.FILES.get('archivo'):
                    messages.warning(request, 'El documento se guardó sin archivo adjunto.')

                if accion == 'borrador':
                    messages.success(request, f'{tipo_nombre} "{doc.nombre}" guardado como borrador.')
                elif solicitud_aprob and solicitud_aprob.aprobador_id:
                    messages.success(request, f'{tipo_nombre} "{doc.nombre}" creado y enviado para aprobación.')
                else:
                    messages.warning(
                        request,
                        f'{tipo_nombre} "{doc.nombre}" creado, pero no hay jefe de '
                        f'departamento asignado. Quedará en espera de evaluación.'
                    )
                    messages.info(
                        request,
                        'Se activará cuando un jefe lo evalúe y Gerencia General lo autorice.'
                    )

                return redirect('documentos:detalle', tipo=tipo, documento_id=doc.uuid)
    else:
        form = DocumentoForm(
            departamento_id=perfil.departamento.id,
            tipo_documento=tipo
        )

    codigo_preview = Documento.generar_codigo_secuencial(perfil.departamento, tipo)

    return render(request, 'documentos/formulario.html', {
        'form': form,
        'titulo': f'Nuevo {tipo_nombre}',
        'tipo': tipo,
        'auto_departamento_id': perfil.departamento.id,
        'auto_departamento_nombre': str(perfil.departamento),
        'tipo_documento_codigo': tipo_codigo,
        'codigo_preview': codigo_preview,
        'sin_prefijo_depto': sin_prefijo_depto,
        'es_dinamico': True,
        'selector_relacionados': _selector_relacionados(),
        'formato_dinamico': formato_dinamico,
        'respuestas_formato': respuestas_formato,
    })


@login_required(login_url='/login/')
def enviar_documento(request, tipo, documento_id):
    """Envía un documento en borrador a revisión (crea la cadena de aprobación)."""
    if request.method != 'POST':
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento_id)
    documento = get_documento(tipo, documento_id)
    if not documento:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    if documento.estado != 'borrador':
        messages.warning(request, 'Solo se pueden enviar documentos en borrador.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if not puede_editar_documento(request.user, documento):
        messages.error(request, 'No tienes permisos para enviar este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    perfil = request.user.perfil
    sol_cambio = SolicitudCambio.objects.filter(
        documento=documento, estado='borrador'
    ).order_by('-id').first()

    formato_dinamico = sol_cambio.formato if sol_cambio else None
    errores_formato = []
    if formato_dinamico:
        errores_formato = validar_respuestas_guardadas(formato_dinamico, sol_cambio.respuestas)
        if errores_formato:
            for err in errores_formato:
                messages.error(request, err)
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    if formato_dinamico and not request.user.get_full_name():
        tiene_nombre = any(
            c.origen == 'nombre_solicitante'
            for c in formato_dinamico.get_todos_los_campos()
        )
        nombre_guardado = sol_cambio.respuestas.get('nombre_solicitante')
        if tiene_nombre and not nombre_guardado:
            messages.error(
                request,
                'Tu usuario no tiene nombre registrado en el sistema. '
                'Edita el borrador y escribe tu nombre completo antes de enviarlo.'
            )
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    with transaction.atomic():
        documento.estado = 'en_revision'
        documento.save(update_fields=['estado'])

        if sol_cambio:
            _autollenar_envio(sol_cambio, request.user)
            sol_cambio.estado = 'en_revision'
            sol_cambio.save(update_fields=['estado'])

        sol_eval = crear_cadena_aprobacion_documento(
            documento=documento, usuario=request.user,
            departamento=perfil.departamento, accion='creacion',
        )

    if sol_eval and sol_eval.aprobador_id:
        messages.success(request, f'"{documento.nombre}" enviado a revisión.')
    else:
        messages.warning(
            request,
            f'"{documento.nombre}" enviado, pero no hay jefe de departamento '
            f'asignado. Quedará en espera de evaluación.'
        )
    return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)


@login_required(login_url='/login/')
def editar_borrador(request, tipo, documento_id):
    """Edita un documento en borrador (reabre formulario dinámico para seguir completando)."""
    documento = get_documento(tipo, documento_id)
    if not documento:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    if documento.estado != 'borrador':
        messages.warning(request, 'Solo se pueden editar documentos en borrador desde aquí.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if not puede_editar_documento(request.user, documento):
        messages.error(request, 'No tienes permisos para editar este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    perfil = request.user.perfil
    tipo_obj = TipoDocumento.objects.filter(key=tipo).first()
    tipo_codigo = tipo_obj.codigo if tipo_obj else ''
    tipo_nombre = tipo_obj.nombre if tipo_obj else tipo

    sol_cambio = SolicitudCambio.objects.filter(
        documento=documento, operacion='creacion'
    ).order_by('-id').first()

    if sol_cambio and sol_cambio.formato:
        formato_dinamico = sol_cambio.formato
    else:
        formato_dinamico = Formato.objects.filter(
            activo=True, aplica_creacion=True
        ).order_by('codigo').first()

    respuestas_formato = sol_cambio.respuestas if sol_cambio else {}

    if request.method == 'POST':
        accion = request.POST.get('accion', 'borrador')
        form = DocumentoForm(
            request.POST, request.FILES,
            instance=documento,
            departamento_id=perfil.departamento.id,
            tipo_documento=tipo
        )
        respuestas_formato_post, errores_formato = respuestas_formato, []
        if formato_dinamico:
            respuestas_formato_post, errores_formato = validar_respuestas(
                formato_dinamico, request.POST, parcial=(accion == 'borrador')
            )
        if form.is_valid():
            if errores_formato:
                for err in errores_formato:
                    messages.error(request, err)

            if not errores_formato:
                with transaction.atomic():
                    doc = form.save(commit=False)
                    doc.tipo = TipoDocumento.objects.get(key=tipo)
                    doc.save()
                    form.save_m2m()

                    if formato_dinamico:
                        respuestasGuardar = respuestas_formato_post if respuestas_formato_post else {}
                        if sol_cambio:
                            sol_cambio.respuestas = respuestasGuardar
                            sol_cambio.save(update_fields=['respuestas'])
                        else:
                            codigo_sol = f"SC-{timezone.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
                            sol_cambio = SolicitudCambio.objects.create(
                                codigo=codigo_sol,
                                formato=formato_dinamico,
                                operacion='creacion',
                                documento=doc,
                                solicitante=request.user,
                                departamento=perfil.departamento,
                                respuestas=respuestasGuardar,
                                estado='borrador',
                            )

                    _procesar_referencias(doc, request, perfil)

                    if accion != 'borrador':
                        if sol_cambio:
                            sol_cambio.estado = 'en_revision'
                            sol_cambio.save(update_fields=['estado'])
                            _autollenar_envio(sol_cambio, request.user)
                        crear_cadena_aprobacion_documento(
                            documento=doc, usuario=request.user,
                            departamento=perfil.departamento, accion='creacion',
                        )
                        doc.estado = 'en_revision'
                        doc.save(update_fields=['estado'])

                if accion == 'borrador':
                    messages.success(request, f'{tipo_nombre} "{doc.nombre}" guardado como borrador.')
                else:
                    messages.success(request, f'{tipo_nombre} "{doc.nombre}" enviado para aprobación.')

                return redirect('documentos:detalle', tipo=tipo, documento_id=doc.uuid)

        respuestas_formato = respuestas_formato_post
    else:
        form = DocumentoForm(
            instance=documento,
            departamento_id=perfil.departamento.id,
            tipo_documento=tipo
        )

    codigo_preview = documento.codigo

    return render(request, 'documentos/formulario.html', {
        'form': form,
        'titulo': f'Editar {tipo_nombre} (borrador)',
        'tipo': tipo,
        'auto_departamento_id': perfil.departamento.id,
        'auto_departamento_nombre': str(perfil.departamento),
        'tipo_documento_codigo': tipo_codigo,
        'codigo_preview': codigo_preview,
        'es_dinamico': True,
        'selector_relacionados': _selector_relacionados(documento),
        'formato_dinamico': formato_dinamico,
        'respuestas_formato': respuestas_formato,
    })


@login_required(login_url='/login/')
def obtener_siguiente_secuencia(request):
    """API para obtener la siguiente secuencia disponible"""
    departamento_id = request.GET.get('departamento_id')
    tipo_codigo = request.GET.get('tipo_codigo')
    
    if not departamento_id or not tipo_codigo:
        return JsonResponse({'error': 'Parámetros faltantes'}, status=400)
    
    try:
        departamento = Departamento.objects.get(id=departamento_id)
    except Departamento.DoesNotExist:
        return JsonResponse({'error': 'Departamento no encontrado'}, status=404)
    
    # Convertir tipo_codigo (ej: 'FOR') a tipo (ej: 'formulario')
    tipo_obj = TipoDocumento.objects.filter(codigo=tipo_codigo).first()
    tipo = tipo_obj.key if tipo_obj else None

    if not tipo:
        return JsonResponse({'error': 'Tipo de documento no válido'}, status=400)
    
    consecutivo = Documento.generar_codigo_secuencial(departamento, tipo)
    secuencia = consecutivo.split('-')[-1]
    
    return JsonResponse({'secuencia': secuencia})


@login_required(login_url='/login/')
def detalle_documento(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    if documento is None:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')

    from .permissions import puede_ver_documento
    if not puede_ver_documento(request.user, documento):
        messages.error(request, 'No tienes permisos para ver este documento.')
        return redirect('documentos:mis_documentos')

    _reconciliar_resumen_ia(documento)

    versiones = VersionDocumento.objects.filter(documento=documento)
    from .permissions import descarga_autorizada, puede_descargar
    tiene_descarga_aprobada = descarga_autorizada(documento, request.user)

    convirtiendo = False
    from .services.documentos_pdf import FORMATOS_CONVERTIBLES, pdf_disponible
    pdf_listo = pdf_disponible(documento)
    if documento.tipo_archivo in FORMATOS_CONVERTIBLES and not pdf_listo:
        # Conversión en línea: si el PDF falta o su ruta es fantasma (borrado
        # del disco), se regenera ahora y la vista sirve el PDF ya listo.
        from .services.documentos_pdf import asegurar_pdf
        pdf_listo = asegurar_pdf(documento)
        if not pdf_listo and documento.archivo:
            messages.error(
                request,
                'No se pudo generar la vista previa en PDF. '
                'Vuelve a recargar para reintentar.',
            )

    solicitud_aprobacion = SolicitudAprobacionDocumento.objects.filter(
        documento=documento, estado='pendiente', aprobador=request.user,
    ).order_by('-fecha_solicitud').first()

    puede_aprobar = bool(solicitud_aprobacion)

    solicitud_espera = SolicitudAprobacionDocumento.objects.filter(
        documento=documento, estado='esperando', aprobador__isnull=True,
    ).order_by('id').first()

    espera_msg = None
    if solicitud_espera:
        if solicitud_espera.rol_aprobador == 'evaluador':
            espera_msg = 'Pendiente de evaluación por jefe de departamento'
        else:
            evaluador_aprobado = SolicitudAprobacionDocumento.objects.filter(
                documento=documento,
                accion_solicitud=solicitud_espera.accion_solicitud,
                rol_aprobador='evaluador',
                estado='aprobada',
            ).exists()
            if evaluador_aprobado:
                espera_msg = 'Evaluado — pendiente de autorización por Gerencia General'

    documentos_relacionados = documento.documentos_relacionados.all()
    referencias_pendientes = ReferenciaDocumento.objects.filter(
        documento=documento, documento_destino__isnull=True
    )
    documentos_llamado_por = Documento.objects.filter(
        documentos_relacionados=documento
    ).exclude(id=documento.id)
    departamentos_afectados = documento.departamentos_afectados.all()
    responsables = documento.responsables.all()
    distribuciones = DistribucionDocumento.objects.filter(
        documento=documento
    ).select_related('usuario', 'distribuido_por').order_by('-fecha_distribucion')

    solicitud_pendiente = SolicitudDescarga.objects.filter(
        documento=documento, solicitado_por=request.user, estado='pendiente'
    ).exists()

    from .permissions import puede_inhabilitar
    puede_inhab, _ = puede_inhabilitar(request.user, documento)

    from django.db.models.functions import Coalesce
    historial_estados = (
        SolicitudAprobacionDocumento.objects.filter(documento=documento)
        .annotate(fecha_accion=Coalesce('fecha_respuesta', 'fecha_solicitud'))
        .order_by('-fecha_accion', '-id')
    )

    # Cadena de aprobación actual (última): evaluador primero, luego autorizador.
    aprobaciones_actuales = list(
        SolicitudAprobacionDocumento.objects.filter(documento=documento)
        .order_by('-id')[:2]
    )[::-1]

    renovaciones = RenovacionVencimiento.objects.filter(documento=documento)
    es_propietario = documento.propietario == request.user

    solicitudes_cambio = list(
        SolicitudCambio.objects.filter(documento=documento)
        .select_related('formato', 'solicitante', 'departamento')
        .order_by('-fecha_creacion')
    )
    solicitudes_por_operacion = []
    for op_key, op_label in SolicitudCambio.OPERACION_CHOICES:
        items = [sc for sc in solicitudes_cambio if sc.operacion == op_key]
        if items:
            solicitudes_por_operacion.append({
                'key': op_key, 'label': op_label,
                'solicitudes': items, 'count': len(items),
            })

    from .permissions import es_jefe_de_departamento, puede_transferir as _puede_transferir
    es_jefe_depto = es_jefe_de_departamento(request.user, documento)
    es_admin = request.user.perfil.es_administrador

    solicitudes_permitidas = {
        sc.uuid: True for sc in solicitudes_cambio
        if _puede_ver_solicitud(request.user, sc)
    }

    from datetime import date, timedelta
    hoy = date.today()
    dias7 = hoy + timedelta(days=7)

    descargas_pendientes_aprobar = AprobacionDescarga.objects.filter(
        aprobador=request.user, estado='pendiente',
        solicitud__documento=documento
    ).select_related('solicitud', 'solicitud__solicitado_por')

    codigos_relacionados = {}
    if documento.resumen_ia:
        codigos = [
            str(item.get('codigo', '')).strip()
            for item in (documento.resumen_ia.get('documentos_relacionados') or [])
            if isinstance(item, dict) and str(item.get('codigo', '')).strip()
        ]
        if codigos:
            for rel in Documento.objects.filter(codigo__in=codigos):
                codigos_relacionados[rel.codigo.upper()] = {
                    'uuid': str(rel.uuid),
                    'tipo': rel.tipo_id,
                    'puede_ver': puede_ver_documento(request.user, rel),
                }

    return render(request, 'documentos/detalle.html', {
        'documento': documento,
        'tipo': tipo,
        'codigos_relacionados': codigos_relacionados,
        'versiones': versiones,
        'puede_editar': puede_editar_documento(request.user, documento),
        'puede_eliminar': puede_eliminar_documento(request.user, documento),
        'puede_descargar': puede_descargar(request.user, documento) or tiene_descarga_aprobada,
        'puede_inhabilitar': puede_inhab,
        'puede_renovar': es_propietario or es_admin or es_jefe_depto,
        'puede_establecer_vigencia': (es_propietario or es_admin or es_jefe_depto) and documento.estado == 'activo',
        'periodos_vigencia': Periodo.objects.filter(activo=True),
        'puede_distribuir': es_admin or es_jefe_depto or es_propietario,
        'puede_revocar': puede_revocar_distribucion(request.user, documento),
        'puede_asignar': es_admin or es_jefe_depto,
        'puede_transferir_vista': _puede_transferir(request.user, documento)[0],
        'solicitud_aprobacion': solicitud_aprobacion,
        'solicitud_espera': solicitud_espera,
        'espera_msg': espera_msg,
        'puede_aprobar': puede_aprobar,
        'descargas_pendientes_aprobar': descargas_pendientes_aprobar,
        'convirtiendo': convirtiendo,
        'pdf_listo': pdf_listo,
        'documentos_relacionados': documentos_relacionados,
        'referencias_pendientes': referencias_pendientes,
        'documentos_llamado_por': documentos_llamado_por,
        'departamentos_afectados': departamentos_afectados,
        'responsables': responsables,
        'distribuciones': distribuciones,
        'historial_estados': historial_estados,
        'aprobaciones_actuales': aprobaciones_actuales,
        'renovaciones': renovaciones,
        'es_propietario': es_propietario,
        'solicitud_pendiente': solicitud_pendiente,
        'hoy': hoy,
        'dias7': dias7,
        'solicitudes_por_operacion': solicitudes_por_operacion,
        'sc_permisos': solicitudes_permitidas,
        'ia_habilitada': ai.esta_habilitado(),
        'ia_usos_hoy': ai.consumo_hoy(request.user),
        'ia_limite': ai.limite_diario(),
    })


def calcular_proxima_version(documento):
    return documento.proxima_version()


@login_required(login_url='/login/')
def editar_documento(request, tipo, documento_id):
    try:
        documento = get_documento(tipo, documento_id)
    except Http404:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    if not puede_editar_documento(request.user, documento):
        messages.error(request, 'No tienes permisos para editar este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if documento.estado == 'borrador':
        return redirect('documentos:editar_borrador', tipo=tipo, documento_id=documento.uuid)
    return redirect(f'/documentos/planilla-cambios/nueva/?tipo={tipo}&doc_uuid={documento.uuid}')


@login_required(login_url='/login/')
def eliminar_documento(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    if not puede_eliminar_documento(request.user, documento):
        messages.error(request, 'No tienes permisos para eliminar este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    formato_dinamico = Formato.objects.filter(
        activo=True, aplica_eliminacion=True
    ).order_by('codigo').first()

    respuestas_formato = {}

    if request.method == 'POST':
        respuestas_formato, errores_formato = {}, []
        if formato_dinamico:
            respuestas_formato, errores_formato = validar_respuestas(formato_dinamico, request.POST)
            if errores_formato:
                for err in errores_formato:
                    messages.error(request, err)

        if not errores_formato:
            from .permissions import puede_inhabilitar
            puede, razon = puede_inhabilitar(request.user, documento)
            if puede:
                documento.estado = 'obsoleto'
                documento.save(update_fields=['estado'])
                if formato_dinamico and respuestas_formato:
                    sol_cambio = SolicitudCambio.objects.create(
                        codigo=f"SC-{timezone.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}",
                        formato=formato_dinamico,
                        operacion='eliminacion',
                        documento=documento,
                        solicitante=request.user,
                        departamento=documento.departamento,
                        respuestas=respuestas_formato,
                        estado='aprobado',
                    )
                    _autollenar_envio(sol_cambio, request.user)
                    from .services.planillas import encolar_generacion_planilla
                    encolar_generacion_planilla(sol_cambio)
                messages.success(request, 'Documento marcado como obsoleto.')
                return redirect('documentos:mis_documentos')
            else:
                perfil = request.user.perfil
                departamento = perfil.departamento
                estado_anterior = documento.estado
                documento.estado = 'en_revision'
                documento.save(update_fields=['estado'])
                if formato_dinamico and respuestas_formato:
                    sol_cambio = SolicitudCambio.objects.create(
                        codigo=f"SC-{timezone.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}",
                        formato=formato_dinamico,
                        operacion='eliminacion',
                        documento=documento,
                        solicitante=request.user,
                        departamento=departamento,
                        respuestas=respuestas_formato,
                        estado='en_revision',
                    )
                    _autollenar_envio(sol_cambio, request.user)
                sol = crear_cadena_aprobacion_documento(
                    documento=documento, usuario=request.user,
                    departamento=departamento, accion='inhabilitar',
                    observaciones=f'Estado anterior: {estado_anterior}',
                )
                if sol and sol.aprobador_id:
                    messages.success(request, 'Solicitud de inhabilitación enviada al Jefe de Departamento para aprobación.')
                else:
                    messages.warning(request, 'Solicitud registrada. No hay jefe de departamento asignado; quedará en espera de evaluación.')
                return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    return render(request, 'documentos/confirmar_eliminar.html', {
        'documento': documento, 'tipo': tipo,
        'formato_dinamico': formato_dinamico,
        'respuestas_formato': respuestas_formato,
    })


@login_required(login_url='/login/')
def destruir_documento(request, tipo, documento_id):
    from django.utils import timezone as tz
    from django.contrib.auth.models import User
    documento = get_documento(tipo, documento_id)
    if not request.user.is_superuser:
        messages.error(request, 'Solo los administradores pueden destruir documentos.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if documento.estado != 'obsoleto':
        messages.error(request, 'Solo se pueden destruir documentos obsoletos.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if not documento.fecha_destruccion or documento.fecha_destruccion > tz.now().date():
        messages.error(request, 'Este documento aún no es elegible para destrucción.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if request.method == 'POST':
        confirmacion = request.POST.get('confirmacion', '')
        if confirmacion != 'DESTRUIR':
            messages.error(request, 'Debe escribir DESTRUIR para confirmar.')
            return render(request, 'documentos/confirmar_destruccion.html', {
                'documento': documento, 'tipo': tipo,
            })
        try:
            from auditoria.services import registrar_eliminacion_documento
            registrar_eliminacion_documento(
                usuario=request.user,
                recurso_tipo=documento.tipo_id or '',
                recurso_id=documento.id,
                codigo=documento.codigo,
                nombre=documento.nombre,
                estado_anterior=documento.estado,
            )
        except Exception:
            pass
        if documento.archivo:
            try:
                documento.archivo.delete(save=False)
            except Exception:
                pass
        if documento.archivo_pdf:
            try:
                documento.archivo_pdf.delete(save=False)
            except Exception:
                pass
        doc_codigo = documento.codigo
        doc_nombre = documento.nombre
        documento.delete()
        messages.success(request, f'Documento "{doc_nombre}" ({doc_codigo}) destruido permanentemente.')
        return redirect('documentos:mis_documentos')
    return render(request, 'documentos/confirmar_destruccion.html', {
        'documento': documento, 'tipo': tipo,
    })


def _aprobadores_descarga(documento):
    """Aprobadores ``[(usuario, rol)]`` de una descarga según clasificación.

    Normal: jefe del departamento (o admin si no hay jefe). ``restringido``:
    jefe + autorizador (admin); ISO 9001:7.5.3 (doble control).
    """
    from autenticacion.models import PerfilUsuario

    depto = getattr(documento, 'departamento', None)
    jefe = depto.jefe if depto and depto.jefe else None
    admin_perfil = PerfilUsuario.objects.filter(
        rol='administrador', usuario__is_active=True
    ).select_related('usuario').first()
    admin = admin_perfil.usuario if admin_perfil else None

    aprobadores = []
    if documento.clasificacion == 'restringido':
        if jefe:
            aprobadores.append((jefe, 'jefe'))
        if admin and (not jefe or admin.id != jefe.id):
            aprobadores.append((admin, 'autorizador'))
    else:
        if jefe:
            aprobadores.append((jefe, 'jefe'))
        elif admin:
            aprobadores.append((admin, 'autorizador'))
    return aprobadores


@login_required(login_url='/login/')
def solicitar_descarga(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    if not documento:
        raise Http404

    aprobadores = _aprobadores_descarga(documento)

    if request.method == 'POST':
        if not aprobadores:
            messages.error(
                request,
                'No se puede realizar la solicitud en este momento. '
                'Comuníquese con el administrador.'
            )
            return render(request, 'documentos/solicitar_descarga.html', {
                'form': SolicitudDescargaForm(),
                'documento': documento, 'tipo': tipo,
                'aprobadores': [], 'aprobador': None, 'aprobador_tipo': None,
            })
        from .permissions import (
            descarga_autorizada as _autorizada,
            puede_descargar as _puede_descargar,
        )
        if _puede_descargar(request.user, documento):
            messages.warning(request, 'Ya tienes permiso para descargar este documento.')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
        existe_pendiente = SolicitudDescarga.objects.filter(
            documento=documento, solicitado_por=request.user, estado='pendiente'
        ).exists()
        if existe_pendiente:
            messages.warning(request, 'Ya tienes una solicitud pendiente para este documento.')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
        if _autorizada(documento, request.user):
            messages.warning(request, 'Ya tienes una descarga aprobada. Usa el botón "Descargar".')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
        form = SolicitudDescargaForm(request.POST)
        if form.is_valid():
            solicitud = form.save(commit=False)
            solicitud.documento = documento
            solicitud.solicitado_por = request.user
            solicitud.save()
            from autenticacion.models import Notificacion
            for aprobador, rol in aprobadores:
                AprobacionDescarga.objects.create(
                    solicitud=solicitud, aprobador=aprobador, rol_aprobador=rol,
                )
                Notificacion.objects.create(
                    usuario=aprobador,
                    titulo='Nueva solicitud de descarga',
                    mensaje=(
                        f'{request.user.get_full_name() or request.user.username} '
                        f'solicita descargar "{documento.nombre}" ({documento.codigo})'
                    ),
                    tipo='solicitud_descarga',
                    url_accion=f'/documentos/solicitud/{solicitud.id}/aprobar/',
                    departamento=documento.departamento,
                    documento=documento,
                )
            messages.success(request, 'Solicitud de descarga enviada correctamente.')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    else:
        form = SolicitudDescargaForm()
    return render(request, 'documentos/solicitar_descarga.html', {
        'form': form, 'documento': documento, 'tipo': tipo,
        'aprobadores': aprobadores,
        'aprobador': aprobadores[0][0] if aprobadores else None,
        'aprobador_tipo': aprobadores[0][1] if aprobadores else None,
    })


@login_required(login_url='/login/')
def descargar_archivo(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    if not documento:
        raise Http404
    from .permissions import (
        descarga_autorizada, puede_descargar, tiene_distribucion,
    )

    autorizacion = None
    if puede_descargar(request.user, documento):
        if documento.clasificacion == 'publico':
            origen = 'publico'
        elif getattr(getattr(request.user, 'perfil', None), 'es_administrador', False):
            origen = 'admin'
        elif (documento.clasificacion == 'interno'
              and documento.propietario_id == request.user.id):
            origen = 'propietario'
        elif tiene_distribucion(documento, request.user):
            origen = 'distribucion'
        else:
            origen = 'jefe'
    else:
        autorizacion = SolicitudDescarga.objects.filter(
            documento=documento, solicitado_por=request.user, estado='aprobada',
            version_aprobada=documento.version_actual,
            fecha_expiracion__gt=timezone.now(),
        ).order_by('-fecha_resolucion').first()
        if not autorizacion:
            messages.error(request, 'No tienes permiso para descargar este documento.')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
        origen = 'solicitud'

    from .services.documentos_pdf import archivo_existe
    if not documento.archivo or not archivo_existe(documento.archivo):
        messages.error(
            request,
            'El archivo de este documento no está disponible en el almacenamiento.',
        )
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    # Copia controlada: la descarga directa del propietario de un `interno`
    # queda registrada como distribución (ISO 9001:7.5.3).
    if origen == 'propietario' and not tiene_distribucion(documento, request.user):
        try:
            dist = DistribucionDocumento.objects.create(
                documento=documento, usuario=request.user, metodo='sistema',
                distribuido_por=request.user,
                observaciones='Descarga directa del propietario (interno)',
            )
            from auditoria.middleware import get_client_ip
            from auditoria.services import registrar_distribucion
            registrar_distribucion(
                usuario=request.user,
                recurso_tipo=documento.tipo_id or '',
                recurso_id=str(documento.uuid),
                destinatario=request.user.username,
                metodo='sistema',
                codigo=documento.codigo,
                distribucion_id=dist.id,
                correlacion=f'descarga-directa-{documento.uuid}',
                ip_address=get_client_ip(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "No se pudo registrar la distribución de %s: %s",
                documento.codigo, exc,
            )

    from auditoria.middleware import get_client_ip
    from auditoria.services import registrar_descarga
    correlacion = (
        f'descarga-{autorizacion.id}' if autorizacion
        else f'descarga-directa-{documento.uuid}'
    )
    try:
        registrar_descarga(
            usuario=request.user,
            recurso_tipo=documento.tipo_id or '',
            recurso_id=str(documento.uuid),
            version=documento.version_actual,
            codigo=documento.codigo,
            detalles={
                'clasificacion': documento.clasificacion,
                'origen': origen,
                'solicitud_id': autorizacion.id if autorizacion else None,
            },
            correlation_id=correlacion,
            ip_address=get_client_ip(),
            fail_closed=settings.AUDIT_FAIL_CLOSED,
        )
    except Exception as exc:
        # Fail-closed: sin trazabilidad de la descarga no se entrega el archivo
        # (decisión ISO acordada). El fallo ya se alarmó a CRITICAL en services.
        logger.critical(
            f"Descarga BLOQUEADA por fallo de auditoría: doc={documento.codigo} "
            f"usuario={request.user.username}: {exc}"
        )
        messages.error(
            request,
            'No se pudo registrar la auditoría de la descarga. '
            'Por seguridad, la descarga queda bloqueada; contacte al administrador.'
        )
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    extension = os.path.splitext(documento.archivo.name)[1]
    nombre_archivo = f"{documento.codigo}_v{documento.version_actual}{extension}"
    response = FileResponse(documento.archivo.open('rb'), as_attachment=True, filename=nombre_archivo)
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required(login_url='/login/')
def ver_archivo(request, tipo, documento_id):
    """Sirve el archivo del documento para el visor, validando acceso."""
    documento = get_documento(tipo, documento_id)
    if not documento:
        raise Http404
    from .permissions import puede_ver_documento
    # El visor sirve el CONTENIDO del archivo: una solicitud de descarga/acceso
    # pendiente o rechazada NO debe dar acceso al archivo (solo a su detalle).
    if not puede_ver_documento(request.user, documento, requerir_aprobada=True):
        messages.error(request, 'No tienes permisos para ver este documento.')
        return redirect('documentos:mis_documentos')
    from .services.documentos_pdf import (
        FORMATOS_CONVERTIBLES, archivo_existe, asegurar_pdf,
        limpiar_pdf_inexistente, pdf_disponible,
    )
    # `archivo_pdf` guarda una ruta en BD, no garantiza que el fichero exista:
    # si el PDF se borró del disco se limpia el puntero y se regenera en línea.
    limpiar_pdf_inexistente(documento)
    archivo = documento.archivo_pdf if pdf_disponible(documento) else None
    if archivo is None and documento.tipo_archivo in FORMATOS_CONVERTIBLES:
        # No se pudo (re)generar el PDF: el visor no puede mostrar un office,
        # así que se devuelve al detalle con el aviso en lugar de servir bytes
        # que pdf.js no entiende (y sin romper con un 500).
        if not asegurar_pdf(documento):
            messages.error(
                request,
                'No se pudo generar la vista previa en PDF. '
                'Vuelve a recargar para reintentar.',
            )
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
        archivo = documento.archivo_pdf
    if archivo is None:
        archivo = documento.archivo
    if not archivo or not archivo_existe(archivo):
        messages.error(
            request,
            'El archivo de este documento no está disponible en el almacenamiento.',
        )
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    from auditoria.services import registrar_consulta
    from auditoria.middleware import get_client_ip
    try:
        registrar_consulta(
            usuario=request.user,
            recurso_tipo=documento.tipo_id or '',
            recurso_id=str(documento.uuid),
            version=documento.version_actual,
            codigo=documento.codigo,
            ip_address=get_client_ip(),
            fail_closed=settings.AUDIT_FAIL_CLOSED,
        )
    except Exception as exc:
        # Fail-closed: sin trazabilidad de la consulta no se sirve el contenido
        # (decisión ISO acordada). El fallo ya se alarmó a CRITICAL en services.
        logger.critical(
            f"Consulta BLOQUEADA por fallo de auditoría: doc={documento.codigo} "
            f"usuario={request.user.username}: {exc}"
        )
        messages.error(
            request,
            'No se pudo registrar la auditoría de la consulta. '
            'Por seguridad, la visualización queda bloqueada; contacte al administrador.'
        )
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    response = FileResponse(
        archivo.open('rb'), as_attachment=False, filename=os.path.basename(archivo.name)
    )
    # Defensa en profundidad: el navegador no debe "olfatear" un MIME distinto
    # del declarado (evita que un archivo disfrazado se ejecute como HTML/JS).
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _reconciliar_resumen_ia(documento):
    """Marca como listo/error un resumen colgado en pendiente/procesando.

    Evita que la UI sondee indefinidamente cuando el worker de Celery no procesó
    la tarea (o murió a mitad). Si ya hay un resumen guardado se da por 'listo';
    si no, se marca 'error' para que el usuario pueda reintentar.
    """
    if documento.resumen_ia_estado not in ('pendiente', 'procesando'):
        return
    from datetime import timedelta

    from django.utils import timezone
    minutos = getattr(settings, 'IA_RESUMEN_TIMEOUT_MIN', 5)
    inicio = documento.resumen_ia_solicitado
    if inicio and (timezone.now() - inicio < timedelta(minutes=minutos)):
        return
    # Sin `resumen_ia_solicitado` (pendientes previos al campo) se consideran
    # colgados; con marca de tiempo, solo si superan el umbral.
    if documento.resumen_ia:
        documento.resumen_ia_estado = 'listo'
        documento.resumen_ia_error = ''
    else:
        documento.resumen_ia_estado = 'error'
        documento.resumen_ia_error = (
            'La generación del resumen tardó demasiado o el servicio de tareas '
            'no respondió. Vuelve a intentarlo.'
        )
    documento.save(update_fields=['resumen_ia_estado', 'resumen_ia_error'])


def _estado_resumen_ia(documento, usuario=None, incluir_resumen=True):
    _reconciliar_resumen_ia(documento)
    data = {
        'estado': documento.resumen_ia_estado,
        'actualizado': documento.resumen_ia_actualizado,
        'fecha': documento.resumen_ia_fecha.strftime('%d/%m/%Y %H:%M') if documento.resumen_ia_fecha else None,
        'modelo': documento.resumen_ia_modelo,
        'error': documento.resumen_ia_error,
        'resumen': None,
        'texto': '',
    }
    if incluir_resumen and documento.resumen_ia:
        data['resumen'] = documento.resumen_ia
        data['texto'] = documento.resumen_ia_texto
    if usuario is not None:
        data['usos_hoy'] = ai.consumo_hoy(usuario)
        data['limite'] = ai.limite_diario()
    return data


def _auditar_resumen_ia(usuario, documento):
    try:
        from auditoria.services import registrar_log_iso
        from auditoria.middleware import get_client_ip
        registrar_log_iso(
            usuario=usuario,
            accion_tipo='resumen_ia',
            accion_categoria='inteligencia_artificial',
            recurso_tipo=documento.tipo_id or '',
            recurso_id=str(documento.uuid),
            recurso_codigo=documento.codigo,
            recurso_nombre=documento.nombre,
            resultado_exitoso=True,
            resultado_mensaje=f'Resumen IA solicitado para {documento.codigo}',
            ip_address=get_client_ip(),
        )
    except Exception as exc:
        logger.error("Auditoría resumen IA: %s", exc)


@login_required(login_url='/login/')
def estado_conversion(request, tipo, documento_id):
    """Indica si la conversión a PDF del documento ya está lista (para polling)."""
    documento = get_documento(tipo, documento_id)
    from .permissions import puede_ver_documento
    if not puede_ver_documento(request.user, documento):
        return JsonResponse({'error': 'No autorizado'}, status=403)
    from .services.documentos_pdf import pdf_disponible
    return JsonResponse({
        # El campo puede estar seteado con una ruta cuyo fichero ya no existe
        # (PDF borrado del disco): hay que mirar también el almacenamiento.
        'listo': pdf_disponible(documento),
        'estado': documento.estado,
    })


@login_required(login_url='/login/')
def resumen_ia(request, tipo, documento_id):
    """Devuelve el estado del resumen IA y lo genera bajo demanda (POST)."""
    documento = get_documento(tipo, documento_id)

    from .permissions import puede_ver_documento
    if not puede_ver_documento(request.user, documento):
        return JsonResponse(
            {'error': 'No tienes permisos para ver este documento.'}, status=403
        )

    from . import ai

    if request.method == 'POST':
        if not ai.esta_habilitado():
            return JsonResponse(
                {'error': 'El resumen con IA no está habilitado.'}, status=400
            )
        es_admin = request.user.perfil.es_administrador
        if documento.resumen_ia_estado not in ('pendiente', 'procesando'):
            if not es_admin and ai.limite_alcanzado(request.user):
                return JsonResponse({
                    'error': (
                        'Alcanzaste el límite diario de resúmenes IA '
                        f'({ai.limite_diario()}). Inténtalo de nuevo mañana.'
                    ),
                    'usos_hoy': ai.consumo_hoy(request.user),
                    'limite': ai.limite_diario(),
                }, status=429)

            documento.resumen_ia_estado = 'pendiente'
            documento.resumen_ia_error = ''
            documento.resumen_ia_solicitado = timezone.now()
            documento.save(update_fields=[
                'resumen_ia_estado', 'resumen_ia_error', 'resumen_ia_solicitado',
            ])
            if not es_admin:
                ai.registrar_uso(request.user)
            from .tasks import generar_resumen_ia
            try:
                generar_resumen_ia.delay(str(documento.uuid))
            except Exception as exc:
                logger.error("No se pudo encolar el resumen IA: %s", exc)
                if getattr(settings, 'IA_RESUMEN_SINCRONO', False):
                    logger.info("Ejecutando resumen IA en modo síncrono (fallback).")
                    try:
                        generar_resumen_ia(str(documento.uuid))
                    except Exception as exc2:
                        logger.error("Fallback síncrono del resumen IA falló: %s", exc2)
                        documento.resumen_ia_estado = 'error'
                        documento.resumen_ia_error = str(exc2)[:2000]
                        documento.save(update_fields=['resumen_ia_estado', 'resumen_ia_error'])
                    documento.refresh_from_db()
                    _auditar_resumen_ia(request.user, documento)
                    return JsonResponse(_estado_resumen_ia(documento, request.user))
                documento.resumen_ia_estado = 'error'
                msg = 'No se pudo iniciar la generación (servicio de tareas no disponible).'
                if settings.DEBUG:
                    msg = f'{msg} Detalle: {exc}'
                documento.resumen_ia_error = msg
                documento.save(update_fields=['resumen_ia_estado', 'resumen_ia_error'])
                documento.refresh_from_db()
                return JsonResponse(_estado_resumen_ia(documento, request.user), status=503)
            _auditar_resumen_ia(request.user, documento)
        documento.refresh_from_db()
        return JsonResponse(_estado_resumen_ia(documento, request.user))

    return JsonResponse(_estado_resumen_ia(
        documento, request.user,
        incluir_resumen=not request.GET.get('solo_estado'),
    ))


def _finalizar_descarga(solicitud, aprobador):
    """Fija vigencia/versión, crea la distribución y audita la autorización."""
    from auditoria.middleware import get_client_ip
    from auditoria.services import registrar_distribucion

    documento = solicitud.documento
    dias = getattr(settings, 'DESCARGA_VIGENCIA_DIAS', 30)
    solicitud.estado = 'aprobada'
    solicitud.version_aprobada = documento.version_actual
    solicitud.fecha_expiracion = timezone.now() + timedelta(days=dias)
    solicitud.fecha_resolucion = timezone.now()
    solicitud.save(update_fields=[
        'estado', 'version_aprobada', 'fecha_expiracion', 'fecha_resolucion',
    ])
    try:
        dist = DistribucionDocumento.objects.create(
            documento=documento,
            usuario=solicitud.solicitado_por,
            metodo='sistema',
            distribuido_por=aprobador,
            observaciones=f'Autorizado por solicitud #{solicitud.id}',
        )
        registrar_distribucion(
            usuario=aprobador,
            recurso_tipo=documento.tipo_id or '',
            recurso_id=str(documento.uuid),
            destinatario=solicitud.solicitado_por.username,
            metodo='sistema',
            codigo=documento.codigo,
            distribucion_id=dist.id,
            correlacion=f'descarga-{solicitud.id}',
            correlation_id=f'descarga-{solicitud.id}',
            ip_address=get_client_ip(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "No se pudo registrar la distribución de la solicitud #%s: %s",
            solicitud.id, exc,
        )


@login_required(login_url='/login/')
def aprobar_descarga(request, solicitud_id):
    solicitud = get_object_or_404(SolicitudDescarga, id=solicitud_id)
    documento = solicitud.documento
    aprobacion = AprobacionDescarga.objects.filter(
        solicitud=solicitud, aprobador=request.user
    ).first()
    if not aprobacion:
        messages.error(request, 'No tienes una aprobación asignada para esta solicitud.')
        return redirect('documentos:mis_documentos')
    if request.method == 'POST':
        accion = request.POST.get('accion')
        observaciones = request.POST.get('observaciones', '')
        aprobacion.observaciones = observaciones
        aprobacion.fecha_aprobacion = timezone.now()
        if accion == 'aprobar':
            aprobacion.estado = 'aprobada'
            aprobacion.save()
            pendientes = solicitud.aprobaciones.exclude(estado='aprobada').exists()
            if pendientes:
                # Doble control (restringido): falta el autorizador.
                messages.success(
                    request, 'Evaluación registrada. Pendiente de autorización final.',
                )
                return redirect('documentos:documentos_departamento')
            _finalizar_descarga(solicitud, request.user)
            messages.success(request, 'Descarga aprobada.')
            tipo_notif = 'aprobacion_descarga'
            titulo_notif = 'Descarga aprobada'
            mensaje_notif = (
                f'La solicitud de descarga de "{documento.nombre}" '
                f'({documento.codigo}) ha sido aprobada.'
            )
        else:
            aprobacion.estado = 'rechazada'
            aprobacion.save()
            solicitud.estado = 'rechazada'
            solicitud.observaciones = observaciones
            solicitud.fecha_resolucion = timezone.now()
            solicitud.save(update_fields=['estado', 'observaciones', 'fecha_resolucion'])
            messages.warning(request, 'Descarga rechazada.')
            tipo_notif = 'rechazo_descarga'
            titulo_notif = 'Descarga rechazada'
            mensaje_notif = (
                f'La solicitud de descarga de "{documento.nombre}" '
                f'({documento.codigo}) ha sido rechazada.'
            )
        from autenticacion.models import Notificacion
        Notificacion.objects.create(
            usuario=solicitud.solicitado_por,
            titulo=titulo_notif,
            mensaje=mensaje_notif,
            tipo=tipo_notif,
            url_accion=f'/documentos/{documento.tipo_id}/{documento.uuid}/',
            departamento=documento.departamento,
            documento=documento,
        )
        return redirect('documentos:documentos_departamento')
    return render(request, 'documentos/aprobar_descarga.html', {
        'solicitud': solicitud, 'documento': documento,
    })


@login_required(login_url='/login/')
def historial_versiones(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    if not documento:
        raise Http404
    from .permissions import puede_ver_documento
    if not puede_ver_documento(request.user, documento):
        messages.error(request, 'No tienes permisos para ver este documento.')
        return redirect('documentos:mis_documentos')
    versiones = VersionDocumento.objects.filter(documento=documento, activa=True)
    solicitudes_pendientes = SolicitudAprobacionDocumento.objects.filter(
        documento=documento, accion_solicitud='eliminar_version', estado='pendiente'
    ).values_list('version_a_eliminar_id', flat=True)
    return render(request, 'documentos/historial_versiones.html', {
        'documento': documento, 'tipo': tipo, 'versiones': versiones,
        'solicitudes_pendientes_ids': list(solicitudes_pendientes),
    })


@login_required(login_url='/login/')
def eliminar_version(request, tipo, documento_id, version_id):
    documento = get_documento(tipo, documento_id)
    version = get_object_or_404(VersionDocumento, id=version_id, documento=documento, activa=True)
    user = request.user
    perfil = user.perfil
    from .permissions import es_jefe_de_departamento
    # Un jefe solo actúa sobre documentos de SU departamento; admin sobre todos.
    es_jefe_o_admin = perfil.es_administrador or es_jefe_de_departamento(user, documento)
    es_propietario = documento.propietario == user
    es_creador = version.creado_por == user

    if not (es_jefe_o_admin or (es_propietario and es_creador)):
        messages.error(request, 'No tienes permisos para eliminar esta versión.')
        return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)

    if documento.estado == 'en_revision':
        messages.error(request, 'No se pueden eliminar versiones mientras el documento está en revisión.')
        return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)

    solicitud_existente = SolicitudAprobacionDocumento.objects.filter(
        documento=documento, version_a_eliminar=version, accion_solicitud='eliminar_version', estado='pendiente'
    ).exists()
    if solicitud_existente:
        messages.warning(request, 'Ya existe una solicitud pendiente para eliminar esta versión.')
        return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)

    if request.method == 'POST':
        if es_jefe_o_admin:
            solic = SolicitudAprobacionDocumento(
                documento=documento, accion_solicitud='eliminar_version',
                version_a_eliminar=version, solicitante=user,
                propietario=documento.propietario,
                aprobador=user,
                departamento_origen=documento.departamento,
                estado='aprobada',
            )
            solic.save()
            solic.aplicar_eliminacion_version(documento, version)
            solic.fecha_respuesta = timezone.now()
            solic.save(update_fields=['fecha_respuesta'])
            try:
                from auditoria.services import registrar_eliminacion_version
                registrar_eliminacion_version(
                    user, tipo, str(documento.uuid),
                    version.numero_version, documento.codigo,
                    ip_address=request.META.get('REMOTE_ADDR'),
                )
            except Exception:
                pass
            messages.success(request, f'Versión {version.numero_version} eliminada correctamente.')
        else:
            departamento = perfil.departamento
            sol = crear_cadena_aprobacion_documento(
                documento=documento, usuario=user,
                departamento=departamento, accion='eliminar_version',
                version_a_eliminar=version,
                observaciones=f'Versión {version.numero_version} de "{documento.nombre}"',
            )
            if sol and sol.aprobador_id:
                messages.success(request, f'Solicitud de eliminación de versión {version.numero_version} enviada al aprobador.')
            else:
                messages.warning(request, f'Solicitud de eliminación de versión {version.numero_version} registrada. No hay jefe de departamento asignado; quedará en espera de evaluación.')
        return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)

    return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)


@login_required(login_url='/login/')
def renovar_vencimiento(request, tipo, documento_id):
    """Renueva la fecha de vencimiento de un documento. Reactiva si está obsoleto."""
    if request.method != 'POST':
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento_id)
    documento = get_documento(tipo, documento_id)
    if not documento:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    user = request.user
    perfil = user.perfil
    es_propietario = documento.propietario == user
    es_jefe_o_admin = perfil.es_administrador or (
        perfil.es_jefe and documento.departamento == perfil.departamento
    )
    if not es_propietario and not es_jefe_o_admin:
        messages.error(request, 'No tienes permisos para renovar este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    fecha_nueva = request.POST.get('fecha_nueva')
    observacion = request.POST.get('observacion', '')
    if not fecha_nueva:
        messages.error(request, 'Debes seleccionar una nueva fecha de vencimiento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    try:
        fecha_nueva_dt = datetime.strptime(fecha_nueva, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Fecha inválida.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    fecha_anterior = documento.fecha_vencimiento
    documento.fecha_vencimiento = fecha_nueva_dt
    documento.aviso_vencimiento_enviado = False
    reactivado = False
    if documento.estado == 'obsoleto':
        documento.estado = 'activo'
        reactivado = True
    documento.save(update_fields=['fecha_vencimiento', 'aviso_vencimiento_enviado', 'estado'])
    from .models import RenovacionVencimiento
    RenovacionVencimiento.objects.create(
        documento=documento,
        fecha_anterior=fecha_anterior or fecha_nueva_dt,
        fecha_nueva=fecha_nueva_dt,
        renovado_por=user,
        observacion=observacion,
    )
    # Notificar propietario
    from autenticacion.models import Notificacion
    if documento.propietario != user:
        Notificacion.objects.create(
            usuario=documento.propietario,
            titulo='Vencimiento renovado',
            mensaje=f'{user.get_full_name() or user.username} renovó el vencimiento de "{documento.nombre}" ({documento.codigo}) hasta el {fecha_nueva_dt}.',
            tipo='renovacion_vencimiento',
            url_accion=f'/documentos/{tipo}/{documento.uuid}/',
        )
    # Notificar jefe
    if documento.departamento and documento.departamento.jefe and documento.departamento.jefe != user:
        Notificacion.objects.create(
            usuario=documento.departamento.jefe,
            titulo='Documento del departamento renovado',
            mensaje=f'{user.get_full_name() or user.username} renovó el vencimiento de "{documento.nombre}" ({documento.codigo}) hasta el {fecha_nueva_dt}.',
            tipo='renovacion_vencimiento',
            url_accion=f'/documentos/{tipo}/{documento.uuid}/',
        )
    # Auditoría
    try:
        from auditoria.services import registrar_log_iso
        from auditoria.middleware import get_client_ip
        registrar_log_iso(
            usuario=user,
            accion_tipo='renovacion',
            accion_categoria='vencimiento',
            recurso_tipo=tipo,
            recurso_id=str(documento.uuid),
            recurso_codigo=documento.codigo,
            recurso_nombre=documento.nombre,
            detalles={'fecha_anterior': str(fecha_anterior), 'fecha_nueva': str(fecha_nueva_dt)},
            resultado_exitoso=True,
            resultado_mensaje=f'Vencimiento renovado: {fecha_anterior} → {fecha_nueva_dt}',
            ip_address=get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT'),
        )
    except Exception as exc:
        logger.error("Auditoría renovación vencimiento: %s", exc)
    msg = f'Vencimiento renovado hasta el {fecha_nueva_dt}.'
    if reactivado:
        msg += ' El documento fue reactivado.'
    messages.success(request, msg)
    return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)


@login_required(login_url='/login/')
def establecer_vigencia(request, tipo, documento_id):
    """Establece o cambia el período de vigencia de un documento aprobado."""
    if request.method != 'POST':
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento_id)
    documento = get_documento(tipo, documento_id)
    if not documento:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    if documento.estado != 'activo':
        messages.error(request, 'Solo se puede establecer la vigencia de documentos aprobados.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    user = request.user
    perfil = user.perfil
    es_propietario = documento.propietario == user
    es_jefe_o_admin = perfil.es_administrador or (
        perfil.es_jefe and documento.departamento == perfil.departamento
    )
    if not es_propietario and not es_jefe_o_admin:
        messages.error(request, 'No tienes permisos para modificar la vigencia de este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    periodo_id = request.POST.get('periodo_vigencia')
    if not periodo_id:
        messages.error(request, 'Debes seleccionar un período de vigencia.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    from .models import Periodo
    try:
        periodo = Periodo.objects.get(id=periodo_id, activo=True)
    except Periodo.DoesNotExist:
        messages.error(request, 'Período de vigencia no válido.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    documento.establecer_vigencia(periodo)
    from autenticacion.models import Notificacion
    if documento.propietario != user:
        Notificacion.objects.create(
            usuario=documento.propietario,
            titulo='Vigencia del documento establecida',
            mensaje=f'{user.get_full_name() or user.username} estableció el período de vigencia de "{documento.nombre}" ({documento.codigo}) en {periodo.nombre}.',
            tipo='cambio_vigencia',
            url_accion=f'/documentos/{tipo}/{documento.uuid}/',
        )
    if documento.departamento and documento.departamento.jefe and documento.departamento.jefe != user:
        Notificacion.objects.create(
            usuario=documento.departamento.jefe,
            titulo='Documento del departamento con nueva vigencia',
            mensaje=f'{user.get_full_name() or user.username} estableció el período de vigencia de "{documento.nombre}" ({documento.codigo}) en {periodo.nombre}.',
            tipo='cambio_vigencia',
            url_accion=f'/documentos/{tipo}/{documento.uuid}/',
        )
    messages.success(request, f'Período de vigencia establecido en {periodo.nombre}. Vencimiento: {documento.fecha_vencimiento}.')
    # Auditoría
    try:
        from auditoria.services import registrar_log_iso
        from auditoria.middleware import get_client_ip
        registrar_log_iso(
            usuario=user,
            accion_tipo='cambio_vigencia',
            accion_categoria='vencimiento',
            recurso_tipo=tipo,
            recurso_id=str(documento.uuid),
            recurso_codigo=documento.codigo,
            recurso_nombre=documento.nombre,
            detalles={'periodo': periodo.nombre, 'fecha_vencimiento': str(documento.fecha_vencimiento)},
            resultado_exitoso=True,
            resultado_mensaje=f'Vigencia establecida: {periodo.nombre} (vencimiento {documento.fecha_vencimiento})',
            ip_address=get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT'),
        )
    except Exception as exc:
        logger.error("Auditoría cambio de vigencia: %s", exc)
    return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)


@login_required(login_url='/login/')
def quitar_vigencia(request, tipo, documento_id):
    """Quita el período de vigencia (motivo obligatorio + trazabilidad ISO)."""
    if request.method != 'POST':
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento_id)
    documento = get_documento(tipo, documento_id)
    if not documento:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    if documento.estado != 'activo':
        messages.error(request, 'Solo se puede modificar la vigencia de documentos aprobados.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    user = request.user
    perfil = user.perfil
    es_propietario = documento.propietario == user
    es_jefe_o_admin = perfil.es_administrador or (
        perfil.es_jefe and documento.departamento == perfil.departamento
    )
    if not es_propietario and not es_jefe_o_admin:
        messages.error(request, 'No tienes permisos para modificar la vigencia de este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    tipo_obj = getattr(documento, 'tipo', None)
    if tipo_obj and tipo_obj.requiere_vigencia:
        messages.error(
            request,
            'Este tipo de documento exige vigencia; cámbiala en lugar de quitarla.',
        )
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    motivo = (request.POST.get('motivo') or '').strip()
    if not motivo:
        messages.error(request, 'Debes indicar el motivo para quitar la vigencia.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    periodo_anterior = documento.periodo_vigencia.nombre if documento.periodo_vigencia else ''
    vencimiento_anterior = documento.fecha_vencimiento
    from django.utils import timezone as tz
    linea = (
        f"v{documento.revision} - {tz.now().strftime('%Y-%m-%d %H:%M')} - "
        f"vigencia eliminada por {user.get_full_name() or user.username}: {motivo}\n"
    )
    documento.periodo_vigencia = None
    documento.fecha_vencimiento = None
    documento.proxima_revision = None
    documento.aviso_vencimiento_enviado = False
    documento.historial_cambios = (documento.historial_cambios + linea).strip()
    documento.save(update_fields=[
        'periodo_vigencia', 'fecha_vencimiento', 'proxima_revision',
        'aviso_vencimiento_enviado', 'historial_cambios',
    ])

    from autenticacion.models import Notificacion
    if documento.propietario != user:
        Notificacion.objects.create(
            usuario=documento.propietario,
            titulo='Vigencia del documento eliminada',
            mensaje=(
                f'{user.get_full_name() or user.username} quitó la vigencia de '
                f'"{documento.nombre}" ({documento.codigo}): {motivo}'
            ),
            tipo='cambio_vigencia',
            url_accion=f'/documentos/{tipo}/{documento.uuid}/',
        )
    if documento.departamento and documento.departamento.jefe and documento.departamento.jefe != user:
        Notificacion.objects.create(
            usuario=documento.departamento.jefe,
            titulo='Documento del departamento sin vigencia',
            mensaje=(
                f'{user.get_full_name() or user.username} quitó la vigencia de '
                f'"{documento.nombre}" ({documento.codigo}): {motivo}'
            ),
            tipo='cambio_vigencia',
            url_accion=f'/documentos/{tipo}/{documento.uuid}/',
        )
    try:
        from auditoria.middleware import get_client_ip
        from auditoria.services import registrar_log_iso
        registrar_log_iso(
            usuario=user,
            accion_tipo='eliminacion_vigencia',
            accion_categoria='vencimiento',
            recurso_tipo=tipo,
            recurso_id=str(documento.uuid),
            recurso_codigo=documento.codigo,
            recurso_nombre=documento.nombre,
            detalles={
                'periodo_anterior': periodo_anterior,
                'vencimiento_anterior': str(vencimiento_anterior) if vencimiento_anterior else None,
                'motivo': motivo,
            },
            resultado_exitoso=True,
            resultado_mensaje=f'Vigencia eliminada ({periodo_anterior})',
            ip_address=get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT'),
        )
    except Exception as exc:
        logger.error("Auditoría eliminación de vigencia: %s", exc)
    messages.success(request, 'Período de vigencia eliminado.')
    return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)


@login_required(login_url='/login/')
def asignar_documento(request, tipo, documento_id):
    perfil = request.user.perfil
    from .permissions import es_jefe_de_departamento
    if not (es_jefe_de_departamento(request.user, get_documento(tipo, documento_id)) or perfil.es_administrador):
        messages.error(request, 'Solo los Jefes de Departamento y Administradores pueden asignar documentos.')
        return redirect('documentos:mis_documentos')
    documento = get_documento(tipo, documento_id)
    if request.method == 'POST':
        form = AsignacionDocumentoForm(request.POST, departamento=documento.departamento)
        if form.is_valid():
            asignacion = form.save(commit=False)
            asignacion.documento = documento
            asignacion.jefe_asignador = request.user
            asignacion.save()
            permisos = []
            if asignacion.puede_leer: permisos.append('Ver')
            if asignacion.puede_editar: permisos.append('Editar')
            if asignacion.puede_eliminar: permisos.append('Eliminar')
            from autenticacion.models import Notificacion
            Notificacion.objects.create(
                usuario=asignacion.coordinador,
                titulo='Documento asignado',
                mensaje=f'{request.user.get_full_name() or request.user.username} te ha asignado "{documento.nombre}" ({documento.codigo}) con permisos de: {", ".join(permisos)}',
                tipo='documento_asignado',
                url_accion=f'/documentos/{tipo}/{documento.uuid}/',
                departamento=documento.departamento,
                documento=documento
            )
            messages.success(request, f'Documento asignado a {asignacion.coordinador.username}.')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    else:
        form = AsignacionDocumentoForm(departamento=documento.departamento)
    hay_asignables = form.fields['coordinador'].queryset.exists()
    return render(request, 'documentos/asignar_documento.html', {
        'form': form, 'documento': documento, 'tipo': tipo,
        'hay_asignables': hay_asignables,
    })


@login_required(login_url='/login/')
def gestionar_asignaciones(request):
    perfil = request.user.perfil
    if not (perfil.es_jefe or perfil.es_administrador):
        messages.error(request, 'Solo los Jefes de Departamento y Administradores pueden gestionar asignaciones.')
        return redirect('documentos:mis_documentos')
    asignaciones = AsignacionDocumento.objects.filter(
        jefe_asignador=request.user, activa=True
    )
    return render(request, 'documentos/gestionar_asignaciones.html', {
        'asignaciones': asignaciones,
    })


@login_required(login_url='/login/')
def transferir_propiedad(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    from .permissions import puede_transferir
    puede, _ = puede_transferir(request.user, documento)
    if not puede:
        messages.error(request, 'No tienes permisos para transferir este documento.')
        return redirect('documentos:mis_documentos')

    depto_doc = getattr(documento, 'departamento', None)
    miembros_transferibles = []
    if depto_doc:
        miembros_transferibles = (
            depto_doc.miembros.select_related('usuario')
            .exclude(usuario=documento.propietario)
            .order_by('usuario__first_name', 'usuario__last_name', 'usuario__username')
        )

    if request.method == 'POST':
        nuevo_propietario_id = request.POST.get('nuevo_propietario')
        if nuevo_propietario_id:
            nuevo_propietario = get_object_or_404(User, id=nuevo_propietario_id)
            if nuevo_propietario.id == documento.propietario_id:
                messages.error(request, 'El nuevo propietario debe ser distinto al actual.')
                return redirect('documentos:transferir_propiedad', tipo=tipo, documento_id=documento.uuid)
            nuevo_perfil = getattr(nuevo_propietario, 'perfil', None)
            if not nuevo_perfil or nuevo_perfil.departamento != depto_doc:
                messages.error(request, 'El nuevo propietario debe pertenecer al mismo departamento del documento.')
                return redirect('documentos:transferir_propiedad', tipo=tipo, documento_id=documento.uuid)
            from .services.transferencia import transferir_propiedad_documento
            from auditoria.middleware import resolver_ip_cliente
            transferir_propiedad_documento(
                documento, nuevo_propietario, request.user,
                ip_address=resolver_ip_cliente(request),
            )
            messages.success(request, f'Propiedad transferida a {nuevo_propietario.username}.')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    return render(request, 'documentos/transferir_propiedad.html', {
        'documento': documento, 'tipo': tipo,
        'miembros_transferibles': miembros_transferibles,
        'hay_transferibles': bool(depto_doc) and miembros_transferibles.exists(),
    })


@login_required(login_url='/login/')
def crear_planilla(request):
    tipo_doc = request.GET.get('tipo') or request.POST.get('tipo_documento')
    doc_uuid = request.GET.get('doc_uuid') or request.POST.get('documento_uuid')
    if request.method == 'GET' and not (tipo_doc and doc_uuid):
        messages.error(request, 'Esta funcionalidad solo está disponible desde la edición de un documento existente.')
        return redirect('documentos:mis_documentos')
    documento = None
    if tipo_doc and doc_uuid:
        try:
            documento = get_documento(tipo_doc, doc_uuid)
        except Http404:
            messages.error(request, 'Documento no encontrado.')
            return redirect('documentos:mis_documentos')
        if documento.estado == 'obsoleto':
            messages.error(request, 'No se puede editar un documento obsoleto. Cree un nuevo documento.')
            return redirect('documentos:mis_documentos')
        if documento.estado == 'en_revision':
            messages.error(request, 'El documento está en revisión. Espere a que se apruebe o rechace.')
            return redirect('documentos:detalle', tipo=tipo_doc, documento_id=documento.uuid)
        # Exigir permiso de edición sobre el documento en TODOS los tipos, no solo
        # en descripción de procesos. Evita que cualquier usuario someta una planilla
        # sobre un documento ajeno y lo bloquee en estado 'en_revision'.
        if not puede_editar_documento(request.user, documento):
            messages.error(request, 'No tienes permisos para editar este documento.')
            return redirect('documentos:detalle', tipo=tipo_doc, documento_id=documento.uuid)

    respuestas_formato = {}

    if request.method == 'POST':
        accion = request.POST.get('accion', 'enviar')
        form = PlanillaCambiosForm(
            request.POST, request.FILES,
            tipo_documento=tipo_doc, documento=documento,
        )
        permitir_asociados = 'documentos_asociados_json' in form.fields

        formato_dinamico = Formato.objects.filter(
            activo=True, aplica_modificacion=True
        ).order_by('codigo').first()

        respuestas_formato = {}
        errores_formato = []
        if formato_dinamico:
            respuestas_formato, errores_formato = validar_respuestas(formato_dinamico, request.POST, parcial=(accion == 'borrador'))
            if errores_formato:
                for err in errores_formato:
                    messages.error(request, err)

        if form.is_valid() and not errores_formato:
            planilla = form.save(commit=False)
            planilla.justificacion = (
                respuestas_formato.get('descripcion_cambio')
                or respuestas_formato.get('proposito_cambio')
                or ''
            )
            planilla.responsable = request.user
            planilla.tipo_cambio = 'modificacion'
            if documento:
                planilla.documento = documento
                planilla.version_destino = calcular_proxima_version(documento)
                planilla.version_actual_snapshot = documento.version_actual
                planilla.clasificacion_actual_snapshot = documento.clasificacion
                archivo = request.FILES.get('archivo_propuesto')
                if archivo:
                    planilla.archivo_propuesto = archivo
                planilla.codigo = f"PC-{timezone.now().strftime('%Y%m%d-%H%M%S')}"

            if formato_dinamico:
                codigo_sol = f"SC-{timezone.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
                sol_cambio = SolicitudCambio.objects.create(
                    codigo=codigo_sol,
                    formato=formato_dinamico,
                    operacion='modificacion',
                    documento=documento,
                    solicitante=request.user,
                    departamento=getattr(documento, 'departamento', None),
                    respuestas=respuestas_formato,
                    estado='borrador' if accion == 'borrador' else 'en_revision',
                )
                planilla.solicitud_cambio = sol_cambio

            if accion == 'borrador':
                planilla.estado = 'borrador'
                planilla.save()
                form.save_m2m()
                _procesar_asociados_planilla(
                    planilla, request, permitir_asociados, documento, request.user
                )
                messages.success(request, f'Planilla {planilla.codigo} guardada como borrador.')
                return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
            else:
                planilla.save()
                if planilla.solicitud_cambio:
                    _autollenar_envio(planilla.solicitud_cambio, request.user)
                form.save_m2m()
                if planilla.archivo_propuesto:
                    from .tasks import convertir_a_pdf_sync
                    pdf_path = convertir_a_pdf_sync(planilla.archivo_propuesto.path)
                    if pdf_path:
                        planilla.archivo_propuesto_pdf = pdf_path
                        planilla.save(update_fields=['archivo_propuesto_pdf'])
                _procesar_asociados_planilla(
                    planilla, request, permitir_asociados, documento, request.user
                )
                if documento:
                    from autenticacion.models import Notificacion
                    planilla.estado = 'en_revision'
                    planilla.save(update_fields=['estado'])
                    documento.estado = 'en_revision'
                    documento.save(update_fields=['estado'])
                    crear_cadena_aprobacion_planilla(
                        planilla=planilla, usuario=request.user, documento=documento,
                    )
                    messages.success(request, f'Planilla {planilla.codigo} enviada a revisión.')
                    return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
                else:
                    messages.success(request, f'Planilla {planilla.codigo} creada correctamente.')
                    return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
        else:
            messages.warning(request, 'Por favor corrija los errores en el formulario.')
    else:
        initial_data = {}
        if documento:
            initial_data = {
                'nombre_propuesto': documento.nombre,
                'descripcion_propuesta': documento.descripcion,
                'participantes_propuestos': documento.participantes,
                'clasificacion_propuesta': documento.clasificacion,
            }
        form = PlanillaCambiosForm(initial=initial_data, tipo_documento=tipo_doc, documento=documento)

    proxima_version = ''
    version_actual = ''
    departamentos_seleccionados = []
    responsables_seleccionados = []
    documentos_asociados_iniciales = []
    departamentos_disponibles = Departamento.objects.filter(activo=True)
    if documento:
        proxima_version = calcular_proxima_version(documento)
        version_actual = documento.version_actual
        departamentos_seleccionados = [str(d.pk) for d in documento.departamentos_afectados.all()]
        responsables_seleccionados = [(str(d.pk), d.nombre) for d in documento.responsables.all()]
        documentos_asociados_iniciales = [
            {
                'uuid': str(d.uuid), 'codigo': d.codigo,
                'nombre': d.nombre, 'estado': d.estado, 'tipo': d.tipo_id
            }
            for d in documento.documentos_relacionados.all()
        ]

    formato_dinamico = Formato.objects.filter(
        activo=True, aplica_modificacion=True
    ).order_by('codigo').first()

    return render(request, 'documentos/formulario_planilla.html', {
        'form': form,
        'titulo': 'Nueva Planilla de Cambios' if not documento else f'Editar {documento.nombre}',
        'documento': documento,
        'tipo_documento': tipo_doc,
        'edicion': bool(documento),
        'proxima_version': proxima_version,
        'version_actual': version_actual,
        'departamentos_seleccionados': departamentos_seleccionados,
        'responsables_seleccionados': responsables_seleccionados,
        'departamentos_disponibles': departamentos_disponibles,
        'documentos_asociados_iniciales': documentos_asociados_iniciales,
        'selector_asociados': {
            'id': 'selector-asociados',
            'search_url': '/documentos/api/referenciables/',
            'exclude_id': documento.id if documento else '',
            'placeholder': 'Buscar por nombre o código...',
            'payload_mode': 'json',
            'json_name': 'documentos_asociados_json',
            'permite_pendiente': True,
            'initial_items': documentos_asociados_iniciales,
            'extra_dept_selector': '#depto-hidden input[name="departamentos_afectados"]',
            'afecta_todos_id': 'id_afecta_todos',
        },
        'formato_dinamico': formato_dinamico,
        'respuestas_formato': respuestas_formato,
    })


@login_required(login_url='/login/')
def detalle_planilla(request, planilla_uuid):
    planilla = get_object_or_404(PlanillaCambios, uuid=planilla_uuid)
    aprobaciones = AprobacionPlanilla.objects.filter(planilla=planilla)
    documento = planilla.documento if planilla.documento_id else None
    departamentos_afectados = planilla.departamentos_afectados.all()
    documentos_relacionados = documento.documentos_relacionados.all() if documento else []
    solicitud_cambio = planilla.solicitud_cambio
    solicitud_permitida = _puede_ver_solicitud(request.user, solicitud_cambio) if solicitud_cambio else False

    espera = AprobacionPlanilla.objects.filter(
        planilla=planilla, estado='esperando', aprobador__isnull=True,
    ).order_by('id').first()
    espera_msg = None
    if espera:
        if espera.rol_aprobador == 'evaluador':
            espera_msg = 'Pendiente de evaluación por jefe de departamento'
        else:
            evaluador_aprobado = AprobacionPlanilla.objects.filter(
                planilla=planilla, rol_aprobador='evaluador', estado='aprobada',
            ).exists()
            if evaluador_aprobado:
                espera_msg = 'Evaluado — pendiente de autorización por Gerencia General'

    return render(request, 'documentos/detalle_planilla.html', {
        'planilla': planilla, 'aprobaciones': aprobaciones,
        'documento': documento, 'departamentos_afectados': departamentos_afectados,
        'documentos_relacionados': documentos_relacionados,
        'solicitud_cambio': solicitud_cambio,
        'solicitud_permitida': solicitud_permitida,
        'espera_msg': espera_msg,
    })


@login_required(login_url='/login/')
@transaction.atomic
def aprobar_planilla(request, planilla_uuid):
    planilla = get_object_or_404(PlanillaCambios, uuid=planilla_uuid)
    perfil = request.user.perfil
    if not perfil.es_jefe:
        messages.error(request, 'No tienes permisos para aprobar planillas.')
        return redirect('documentos:mis_documentos')
    if request.method == 'POST':
        if planilla.estado != 'en_revision':
            messages.warning(request, 'Esta planilla ya fue procesada.')
            return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
        accion = request.POST.get('accion')
        observaciones = request.POST.get('observaciones', '')
        aprobacion = AprobacionPlanilla.objects.filter(
            planilla=planilla, aprobador=request.user, estado='pendiente'
        ).first()
        if not aprobacion:
            messages.error(request, 'No tienes una aprobación pendiente para esta planilla.')
            return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
        if accion == 'aprobar':
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
                doc, cambios = aplicar_planilla_al_documento(
                    planilla, usuario=request.user,
                )
                if doc:
                    registrar_aprobacion_planilla(
                        usuario=request.user, planilla=planilla, documento=doc,
                        cambios=cambios, observaciones=observaciones,
                    )
                from autenticacion.models import Notificacion
                Notificacion.objects.create(
                    usuario=planilla.responsable,
                    titulo='Planilla de Cambios aprobada',
                    mensaje=f'Tu planilla {planilla.codigo} ha sido aprobada. Los cambios se han aplicado al documento.',
                    tipo='aprobacion_documento',
                    url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/',
                    departamento=planilla.documento.departamento,
                    documento=planilla.documento
                )
                messages.success(request, 'Planilla aprobada y cambios aplicados al documento.')
            else:
                hermano = AprobacionPlanilla.objects.filter(
                    planilla=planilla, rol_aprobador='autorizador', estado='esperando',
                ).exclude(id=aprobacion.id).order_by('id').first()
                if hermano and hermano.aprobador_id:
                    hermano.estado = 'pendiente'
                    hermano.save(update_fields=['estado'])
                from autenticacion.models import Notificacion
                Notificacion.objects.create(
                    usuario=planilla.responsable,
                    titulo='Planilla de Cambios en autorización',
                    mensaje=f'Tu planilla {planilla.codigo} ha sido evaluada. Pendiente de autorización final.',
                    tipo='aprobacion_documento',
                    url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/',
                    departamento=planilla.documento.departamento,
                    documento=planilla.documento
                )
                messages.success(request, 'Evaluación registrada. Pendiente de autorización final.')
        else:
            aprobacion.estado = 'rechazada'
            aprobacion.observaciones = observaciones
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
                usuario=request.user, planilla=planilla, documento=doc,
                observaciones=observaciones,
            )
            from autenticacion.models import Notificacion
            Notificacion.objects.create(
                usuario=planilla.responsable,
                titulo='Planilla de Cambios rechazada',
                mensaje=f'Tu planilla {planilla.codigo} ha sido rechazada. Motivo: {observaciones}',
                tipo='rechazo_documento',
                url_accion=f'/documentos/planilla-cambios/{planilla.uuid}/',
                departamento=planilla.documento.departamento,
                documento=planilla.documento
            )
            messages.warning(request, 'Planilla rechazada.')
        from autenticacion.models import Notificacion
        Notificacion.objects.filter(
            usuario=request.user, tipo='solicitud_aprobacion',
            url_accion__contains=str(planilla.uuid)
        ).update(leida=True)
        return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)

    documento = planilla.documento if planilla.documento_id else None
    archivo_preview = None
    es_pdf = False
    from .services.documentos_pdf import pdf_disponible as _pdf_listo
    if documento and _pdf_listo(documento):
        archivo_preview = documento.archivo_pdf
        es_pdf = True
    elif planilla.archivo_propuesto_pdf:
        archivo_preview = planilla.archivo_propuesto_pdf
        es_pdf = True
    elif planilla.archivo_propuesto:
        archivo_preview = planilla.archivo_propuesto
        es_pdf = str(archivo_preview.name).lower().endswith('.pdf')
    return render(request, 'documentos/aprobar_planilla.html', {
        'planilla': planilla, 'documento': documento,
        'archivo_preview': archivo_preview, 'es_pdf': es_pdf,
    })


@login_required(login_url='/login/')
@transaction.atomic
def enviar_planilla(request, planilla_uuid):
    """Envía una planilla de borrador a en_revision"""
    planilla = get_object_or_404(PlanillaCambios, uuid=planilla_uuid)

    if planilla.estado != 'borrador':
        messages.warning(request, 'Solo se pueden enviar planillas en borrador.')
        return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)

    if planilla.responsable != request.user:
        messages.error(request, 'No tienes permisos para enviar esta planilla.')
        return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)

    documento = planilla.documento
    if documento:
        if planilla.solicitud_cambio and planilla.solicitud_cambio.formato:
            errores = validar_respuestas_guardadas(
                planilla.solicitud_cambio.formato,
                planilla.solicitud_cambio.respuestas,
            )
            if errores:
                for err in errores:
                    messages.error(request, err)
                return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)

        planilla.estado = 'en_revision'
        planilla.save(update_fields=['estado'])
        if planilla.solicitud_cambio:
            planilla.solicitud_cambio.estado = 'en_revision'
            planilla.solicitud_cambio.save(update_fields=['estado'])
            _autollenar_envio(planilla.solicitud_cambio, request.user)
        documento.estado = 'en_revision'
        documento.save(update_fields=['estado'])

        crear_cadena_aprobacion_planilla(
            planilla=planilla, usuario=request.user, documento=documento,
        )

    messages.success(request, f'Planilla {planilla.codigo} enviada a revisión.')
    return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)


# =============================================================================
# CONFIGURACIÓN
# =============================================================================

@login_required(login_url='/login/')
def configuracion(request):
    if not (request.user.perfil.es_administrador or request.user.perfil.es_jefe):
        messages.error(request, 'No tienes permisos para acceder a configuración.')
        return redirect('documentos:mis_documentos')
    return render(request, 'documentos/configuracion/dashboard.html')


@login_required(login_url='/login/')
def ciclo_vida(request):
    if not (request.user.perfil.es_administrador or request.user.perfil.es_jefe):
        messages.error(request, 'No tienes permisos para acceder a configuración.')
        return redirect('documentos:mis_documentos')
    return render(request, 'documentos/configuracion/ciclo_vida.html')


@login_required(login_url='/login/')
def departamentos_lista(request):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden gestionar departamentos.')
        return redirect('documentos:configuracion')
    departamentos = Departamento.objects.all().order_by('nombre')
    return render(request, 'documentos/configuracion/departamentos_lista.html', {
        'departamentos': departamentos,
    })


@login_required(login_url='/login/')
def departamento_crear(request):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden crear departamentos.')
        return redirect('documentos:configuracion')
    from .forms import DepartamentoForm
    if request.method == 'POST':
        form = DepartamentoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Departamento creado correctamente.')
            return redirect('documentos:departamentos_lista')
    else:
        form = DepartamentoForm()
    return render(request, 'documentos/configuracion/departamento_form.html', {
        'form': form, 'accion': 'Crear',
    })


@login_required(login_url='/login/')
def departamento_editar(request, depto_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden editar departamentos.')
        return redirect('documentos:configuracion')
    depto = get_object_or_404(Departamento, id=depto_id)
    from .forms import DepartamentoForm
    if request.method == 'POST':
        form = DepartamentoForm(request.POST, instance=depto)
        if form.is_valid():
            form.save()
            messages.success(request, 'Departamento actualizado correctamente.')
            return redirect('documentos:departamentos_lista')
    else:
        form = DepartamentoForm(instance=depto)
    return render(request, 'documentos/configuracion/departamento_form.html', {
        'form': form, 'accion': 'Editar', 'depto': depto,
    })


@login_required(login_url='/login/')
def departamento_eliminar(request, depto_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden eliminar departamentos.')
        return redirect('documentos:configuracion')
    depto = get_object_or_404(Departamento, id=depto_id)
    if request.method == 'POST':
        nombre = depto.nombre
        depto.delete()
        messages.success(request, f'Departamento "{nombre}" eliminado correctamente.')
        return redirect('documentos:departamentos_lista')
    return render(request, 'documentos/configuracion/departamento_confirmar.html', {
        'depto': depto,
    })


@login_required(login_url='/login/')
def roles_permisos(request):
    if not (request.user.perfil.es_administrador or request.user.perfil.es_jefe):
        messages.error(request, 'No tienes permisos para acceder a configuración.')
        return redirect('documentos:mis_documentos')
    codigo_evaluador = getattr(settings, 'APROBACION_EVALUADOR_DEPTO_GG', 'SGC')
    evaluador_depto = Departamento.objects.filter(codigo=codigo_evaluador, activo=True).first()
    gg_depto = Departamento.objects.filter(codigo='GG').first()
    return render(request, 'documentos/configuracion/roles_permisos.html', {
        'evaluador_depto': evaluador_depto,
        'evaluador_jefe': evaluador_depto.jefe if evaluador_depto else None,
        'gg_depto': gg_depto,
        'gg_jefe': gg_depto.jefe if gg_depto else None,
    })


@login_required(login_url='/login/')
def tipos_documento_lista(request):
    if not (request.user.perfil.es_administrador or request.user.perfil.es_jefe):
        messages.error(request, 'No tienes permisos para acceder a configuración.')
        return redirect('documentos:mis_documentos')
    tipos = TipoDocumento.objects.all().order_by('codigo')
    return render(request, 'documentos/configuracion/tipos_documento_lista.html', {'tipos': tipos})


@login_required(login_url='/login/')
def tipo_documento_crear(request):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden crear tipos de documento.')
        return redirect('documentos:tipos_documento_lista')
    from .forms import TipoDocumentoForm
    if request.method == 'POST':
        form = TipoDocumentoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Tipo de documento creado correctamente.')
            return redirect('documentos:tipos_documento_lista')
    else:
        form = TipoDocumentoForm()
    return render(request, 'documentos/configuracion/tipos_documento_form.html', {
        'form': form, 'accion': 'Crear', 'iconos_disponibles': ICONOS_DISPONIBLES,
    })


@login_required(login_url='/login/')
def tipo_documento_editar(request, tipo_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden editar tipos de documento.')
        return redirect('documentos:tipos_documento_lista')
    tipo = get_object_or_404(TipoDocumento, id=tipo_id)
    from .forms import TipoDocumentoForm
    if request.method == 'POST':
        form = TipoDocumentoForm(request.POST, instance=tipo)
        if form.is_valid():
            form.save()
            messages.success(request, 'Tipo de documento actualizado correctamente.')
            return redirect('documentos:tipos_documento_lista')
    else:
        form = TipoDocumentoForm(instance=tipo)
    return render(request, 'documentos/configuracion/tipos_documento_form.html', {
        'form': form, 'tipo': tipo, 'accion': 'Editar',
        'iconos_disponibles': ICONOS_DISPONIBLES,
    })


@login_required(login_url='/login/')
def tipo_documento_eliminar(request, tipo_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden eliminar tipos de documento.')
        return redirect('documentos:tipos_documento_lista')
    tipo = get_object_or_404(TipoDocumento, id=tipo_id)
    if request.method == 'POST':
        tipo.activo = False
        tipo.save(update_fields=['activo'])
        messages.success(request, f'Tipo "{tipo.nombre}" desactivado correctamente.')
        return redirect('documentos:tipos_documento_lista')
    return render(request, 'documentos/configuracion/tipos_documento_confirmar.html', {'tipo': tipo})


@login_required(login_url='/login/')
def tipo_documento_reactivar(request, tipo_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden reactivar tipos de documento.')
        return redirect('documentos:tipos_documento_lista')
    tipo = get_object_or_404(TipoDocumento, id=tipo_id)
    if request.method == 'POST':
        tipo.activo = True
        tipo.save(update_fields=['activo'])
        messages.success(request, f'Tipo "{tipo.nombre}" reactivado correctamente.')
    return redirect('documentos:tipos_documento_lista')


# === CRUD Períodos ===


@login_required(login_url='/login/')
def periodos_lista(request):
    if not (request.user.perfil.es_administrador or request.user.perfil.es_jefe):
        messages.error(request, 'No tienes permisos para acceder a configuración.')
        return redirect('documentos:mis_documentos')
    periodos = Periodo.objects.all().order_by('orden', 'meses')
    return render(request, 'documentos/configuracion/periodos_lista.html', {'periodos': periodos})


@login_required(login_url='/login/')
def periodo_crear(request):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden crear períodos.')
        return redirect('documentos:periodos_lista')
    from .forms import PeriodoForm
    if request.method == 'POST':
        form = PeriodoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Período creado correctamente.')
            return redirect('documentos:periodos_lista')
    else:
        form = PeriodoForm()
    return render(request, 'documentos/configuracion/periodos_form.html', {
        'form': form, 'accion': 'Crear',
    })


@login_required(login_url='/login/')
def periodo_editar(request, periodo_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden editar períodos.')
        return redirect('documentos:periodos_lista')
    periodo = get_object_or_404(Periodo, id=periodo_id)
    from .forms import PeriodoForm
    if request.method == 'POST':
        form = PeriodoForm(request.POST, instance=periodo)
        if form.is_valid():
            form.save()
            messages.success(request, 'Período actualizado correctamente.')
            return redirect('documentos:periodos_lista')
    else:
        form = PeriodoForm(instance=periodo)
    return render(request, 'documentos/configuracion/periodos_form.html', {
        'form': form, 'periodo': periodo, 'accion': 'Editar',
    })


@login_required(login_url='/login/')
def periodo_eliminar(request, periodo_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden desactivar períodos.')
        return redirect('documentos:periodos_lista')
    periodo = get_object_or_404(Periodo, id=periodo_id)
    if request.method == 'POST':
        periodo.activo = False
        periodo.save(update_fields=['activo'])
        messages.success(request, f'Período "{periodo.nombre}" desactivado correctamente.')
        return redirect('documentos:periodos_lista')
    return render(request, 'documentos/configuracion/periodos_confirmar.html', {'periodo': periodo})


@login_required(login_url='/login/')
def periodo_reactivar(request, periodo_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden reactivar períodos.')
        return redirect('documentos:periodos_lista')
    periodo = get_object_or_404(Periodo, id=periodo_id)
    if request.method == 'POST':
        periodo.activo = True
        periodo.save(update_fields=['activo'])
        messages.success(request, f'Período "{periodo.nombre}" reactivado correctamente.')
    return redirect('documentos:periodos_lista')



@login_required(login_url='/login/')
def auditoria_lista(request):
    if not (request.user.perfil.es_administrador or request.user.perfil.es_jefe):
        messages.error(request, 'No tienes permisos para acceder a auditoría.')
        return redirect('documentos:mis_documentos')
    from auditoria.services import obtener_logs, obtener_estadisticas_auditoria, get_mongo_client
    from django.core.paginator import Paginator
    from datetime import timedelta
    fecha_inicio = request.GET.get('fecha_inicio')
    fecha_fin = request.GET.get('fecha_fin')
    usuario = request.GET.get('usuario')
    accion_tipo = request.GET.get('accion_tipo')
    accion_categoria = request.GET.get('accion_categoria')
    pagina = request.GET.get('pagina', 1)
    if not fecha_inicio:
        fecha_inicio = (timezone.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    if not fecha_fin:
        fecha_fin = timezone.now().strftime('%Y-%m-%d')
    try:
        fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
        fecha_fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
    except ValueError:
        fecha_inicio_dt = timezone.now() - timedelta(days=7)
        fecha_fin_dt = timezone.now()
    if not request.user.perfil.es_administrador:
        usuario = request.user.username
    # Ping MongoDB
    mongo_disponible = False
    try:
        db = get_mongo_client()
        db.command('ping')
        mongo_disponible = True
    except Exception:
        pass
    logs = obtener_logs(
        usuario=usuario, accion_tipo=accion_tipo, accion_categoria=accion_categoria,
        fecha_inicio=fecha_inicio_dt, fecha_fin=fecha_fin_dt, limite=500
    )
    paginador = Paginator(logs, 25)
    pagina_obj = paginador.get_page(pagina)
    estadisticas = obtener_estadisticas_auditoria(
        fecha_inicio=fecha_inicio_dt, fecha_fin=fecha_fin_dt
    )
    total_logins = sum(s['count'] for s in estadisticas if s['_id'].get('accion') == 'login')
    total_creaciones = sum(s['count'] for s in estadisticas if s['_id'].get('accion') == 'creacion')
    total_ediciones = sum(s['count'] for s in estadisticas if s['_id'].get('accion') == 'edicion')
    total_descargas = sum(s['count'] for s in estadisticas if s['_id'].get('accion') in ['descarga', 'solicitud_descarga'])
    return render(request, 'documentos/configuracion/auditoria_lista.html', {
        'logs': pagina_obj, 'estadisticas': estadisticas,
        'total_logins': total_logins, 'total_creaciones': total_creaciones,
        'total_ediciones': total_ediciones, 'total_descargas': total_descargas,
        'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
        'usuario_filtro': usuario or '', 'accion_tipo': accion_tipo or '',
        'accion_categoria': accion_categoria or '',
        'mongo_disponible': mongo_disponible,
    })


@login_required(login_url='/login/')
def auditoria_detalle(request, log_id):
    if not (request.user.perfil.es_administrador or request.user.perfil.es_jefe):
        messages.error(request, 'No tienes permisos para acceder a auditoría.')
        return redirect('documentos:mis_documentos')
    from auditoria.services import get_mongo_client
    try:
        db = get_mongo_client()
        log = db.audit_logs.find_one({'log_id': log_id})
    except Exception:
        log = None
    if not log:
        messages.error(request, 'Log de auditoría no encontrado.')
        return redirect('documentos:auditoria_lista')
    return render(request, 'documentos/configuracion/auditoria_detalle.html', {'log': log})


@login_required(login_url='/login/')
def auditoria_reporte_pdf(request):
    if not (request.user.perfil.es_administrador or request.user.perfil.es_jefe):
        messages.error(request, 'No tienes permisos para generar reportes.')
        return redirect('documentos:mis_documentos')
    from auditoria.services import obtener_logs
    from django.http import HttpResponse
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter, A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_RIGHT
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph,
            Spacer, KeepTogether
        )
        from reportlab.lib.units import inch, mm
        from reportlab.platypus.doctemplate import PageTemplate, BaseDocTemplate, Frame
        from reportlab.pdfbase import pdfmetrics
    except ImportError as e:
        import sys
        msg = (
            f'La exportación a PDF no está disponible. '
            f'Python: {sys.executable}. Error: {e}. '
            f'Instale reportlab en el mismo intérprete que ejecuta el servidor.'
        )
        messages.error(request, msg)
        logger.error(msg)
        return redirect('documentos:auditoria_lista')

    # ── Parámetros GET ──
    from datetime import timedelta
    tipo_reporte = request.GET.get('tipo', 'registro')
    fecha_inicio = request.GET.get('fecha_inicio')
    fecha_fin = request.GET.get('fecha_fin')
    filtro_usuario = request.GET.get('usuario', '').strip()
    filtro_accion = request.GET.get('accion_tipo', '').strip()
    filtro_categoria = request.GET.get('accion_categoria', '').strip()
    formato = request.GET.get('formato', 'letter')
    orientacion = request.GET.get('orientacion', 'landscape')

    # Igual que auditoria_lista: un no-admin solo puede reportar sus propios logs.
    if not request.user.perfil.es_administrador:
        filtro_usuario = request.user.username

    if not fecha_inicio:
        fecha_inicio = (timezone.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    if not fecha_fin:
        fecha_fin = timezone.now().strftime('%Y-%m-%d')
    try:
        fecha_inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
        fecha_fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
    except ValueError:
        fecha_inicio_dt = timezone.now() - timedelta(days=7)
        fecha_fin_dt = timezone.now()

    # ── Configurar página ──
    if formato == 'a4':
        page = A4
    else:
        page = letter
    if orientacion == 'portrait':
        pagesize = page
    else:
        pagesize = landscape(page)

    left_margin = 0.4 * inch
    right_margin = 0.4 * inch
    top_margin = 0.45 * inch
    bottom_margin = 0.6 * inch

    # ── Logo (svglib → Drawing, fallback a None) ──
    logo_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'polidoc', 'static', 'imagenes', 'logopoliclinicatachira.svg'
    )
    logo = None
    try:
        from svglib.svglib import svg2rlg
        logo = svg2rlg(logo_path)
        if logo:
            scale = 42 / logo.height  # 42pt de altura máxima
            logo.scale(scale, scale)
    except Exception:
        logo = None

    # ── Estilos ──
    styles = getSampleStyleSheet()
    sTitle = ParagraphStyle('ReportTitle', parent=styles['Title'],
        fontName='Helvetica-Bold', fontSize=14, textColor=colors.HexColor('#1e40af'),
        spaceAfter=0, spaceBefore=0)
    sSubTitle = ParagraphStyle('ReportSubTitle', parent=styles['Normal'],
        fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#374151'),
        spaceAfter=0, spaceBefore=0)
    sSection = ParagraphStyle('Section', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=9, textColor=colors.HexColor('#1e40af'),
        spaceAfter=4, spaceBefore=8)
    sNormal = ParagraphStyle('ReportNormal', parent=styles['Normal'],
        fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#1f2937'),
        leading=10)
    sSmall = ParagraphStyle('ReportSmall', parent=styles['Normal'],
        fontName='Helvetica', fontSize=7, textColor=colors.HexColor('#6b7280'),
        leading=9)
    sCell = ParagraphStyle('Cell', parent=styles['Normal'],
        fontName='Helvetica', fontSize=7, textColor=colors.HexColor('#1f2937'),
        leading=8, wordWrap='CJK')
    sCellBold = ParagraphStyle('CellBold', parent=sCell,
        fontName='Helvetica-Bold')
    sCellOk = ParagraphStyle('CellOk', parent=sCell,
        textColor=colors.HexColor('#059669'), fontName='Helvetica-Bold')
    sCellFail = ParagraphStyle('CellFail', parent=sCell,
        textColor=colors.HexColor('#dc2626'), fontName='Helvetica-Bold')
    sFooter = ParagraphStyle('Footer', parent=styles['Normal'],
        fontName='Helvetica', fontSize=7, textColor=colors.HexColor('#9ca3af'))
    sSign = ParagraphStyle('Sign', parent=styles['Normal'],
        fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#374151'),
        leading=12)
    sBrand = ParagraphStyle('Brand', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor('#1e40af'),
        leading=12, spaceBefore=0, spaceAfter=0)
    sCode = ParagraphStyle('Code', parent=styles['Normal'],
        fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#374151'),
        leading=10, alignment=TA_RIGHT, spaceBefore=0, spaceAfter=0)

    # ── Logs ──
    logs = obtener_logs(
        usuario=filtro_usuario if filtro_usuario else None,
        fecha_inicio=fecha_inicio_dt, fecha_fin=fecha_fin_dt, limite=500
    )

    # ── Estadísticas ──
    total = len(logs)
    total_logins = sum(1 for l in logs if l.get('accion', {}).get('tipo') == 'login')
    total_creaciones = sum(1 for l in logs if l.get('accion', {}).get('tipo') == 'creacion')
    total_ediciones = sum(1 for l in logs if l.get('accion', {}).get('tipo') == 'edicion')
    total_descargas = sum(1 for l in logs if l.get('accion', {}).get('tipo') in ('descarga', 'solicitud_descarga'))
    total_fallos = sum(1 for l in logs if not l.get('resultado', {}).get('exitoso', True))

    # ── Construir documento ──
    doc_code = f"SGC-RPT-AUD-{timezone.now().strftime('%Y%m%d')}"
    now_str = timezone.now().strftime('%d/%m/%Y %H:%M')
    now_date = timezone.now().strftime('%d/%m/%Y')
    user_display = request.user.get_full_name() or request.user.username
    user_rol = request.user.perfil.get_rol_display() if hasattr(request.user, 'perfil') else ''
    user_depto = ''
    if hasattr(request.user, 'perfil') and request.user.perfil.departamento:
        user_depto = str(request.user.perfil.departamento)

    # Filtros aplicados (texto)
    filtros_texto = []
    if filtro_usuario:
        filtros_texto.append(f'Usuario: {filtro_usuario}')
    if filtro_accion:
        filtros_texto.append(f'Acción: {filtro_accion}')
    if filtro_categoria:
        filtros_texto.append(f'Categoría: {filtro_categoria}')
    filtros_str = ', '.join(filtros_texto) if filtros_texto else 'Todos'

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="SGC-RPT-AUD-{timezone.now().strftime("%Y%m%d")}.pdf"'

    # ── Numeración de páginas ──
    class NumberedCanvas:
        def __init__(self, canvas, doc):
            self._canvas = canvas
            self._pages = []
            self._doc = doc

        def showPage(self):
            self._pages.append(dict(self._canvas.__dict__))
            self._canvas.showPage()

        def save(self):
            num_pages = len(self._pages)
            for i, page in enumerate(self._pages):
                self._canvas.__dict__.update(page)
                self._draw_footer(i + 1, num_pages)
                self._canvas.showPage()
            self._canvas.save()

        def _draw_footer(self, page_num, total_pages):
            c = self._canvas
            c.saveState()
            w = pagesize[0]
            # Línea separadora
            c.setStrokeColor(colors.HexColor('#d1d5db'))
            c.setLineWidth(0.5)
            c.line(left_margin, bottom_margin - 0.1 * inch, w - right_margin, bottom_margin - 0.1 * inch)
            # Izquierda: ISO
            c.setFont('Helvetica', 6.5)
            c.setFillColor(colors.HexColor('#9ca3af'))
            c.drawString(left_margin, bottom_margin - 0.25 * inch,
                f'ISO 9001:2015 §7.5 · §9.2.2 — Información Documentada')
            # Derecha: Página + fecha
            c.drawRightString(w - right_margin, bottom_margin - 0.25 * inch,
                f'Página {page_num} de {total_pages}  ·  Generado: {now_str}')
            # Centro: retención
            c.drawCentredString(w / 2, bottom_margin - 0.25 * inch,
                'Retención: 10 años')
            c.restoreState()

    # ── Encabezado (logo + branding) ──
    logo_cell = logo if logo else ''
    brand_text = Paragraph(
        '<font name="Helvetica-Bold" size="10" color="#1e40af">'
        'POLICLÍNICA TÁCHIRA, C.A.</font><br/>'
        '<font name="Helvetica" size="7" color="#6b7280">'
        'Sistema de Gestión de la Calidad</font>', sBrand)
    code_text = Paragraph(
        '<font name="Helvetica-Bold" size="8" color="#374151">'
        f'{doc_code}</font><br/>'
        '<font name="Helvetica" size="7" color="#6b7280">'
        f'Versión 1.0 · {now_date}</font>', sCode)

    if logo:
        header_table = Table(
            [[logo_cell, brand_text, code_text]],
            colWidths=[0.65 * inch, 3.8 * inch, 2.5 * inch],
            rowHeights=[0.7 * inch]
        )
    else:
        header_table = Table(
            [[brand_text, code_text]],
            colWidths=[4.45 * inch, 2.5 * inch],
            rowHeights=[0.7 * inch]
        )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('RIGHTPADDING', (-1, -1), (-1, -1), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
    ]))

    # ── Título centrado ──
    title_center = Table(
        [[Paragraph("REPORTE DE AUDITORÍA INTERNA", sTitle)]],
        colWidths=[6.95 * inch],
        rowHeights=[0.3 * inch]
    )
    title_center.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#1e40af')),
    ]))

    # ── Bloque control documental (§7.5) ──
    ctrl_data = [
        [Paragraph('<b>Período auditado:</b>', sSmall), Paragraph(f'{fecha_inicio} — {fecha_fin}', sSmall),
         Paragraph('<b>Filtros aplicados:</b>', sSmall), Paragraph(filtros_str, sSmall)],
        [Paragraph('<b>Generado por:</b>', sSmall), Paragraph(f'{user_display} ({user_rol})', sSmall),
         Paragraph('<b>Fecha emisión:</b>', sSmall), Paragraph(now_str, sSmall)],
        [Paragraph('<b>Departamento:</b>', sSmall), Paragraph(user_depto or 'N/A', sSmall),
         Paragraph('<b>Total registros:</b>', sSmall), Paragraph(str(total), sSmall)],
    ]
    col_w = 1.7375 * inch
    ctrl_table = Table(ctrl_data, colWidths=[col_w] * 4, rowHeights=[0.18 * inch] * 3)
    ctrl_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#e5e7eb')),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f9fafb')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#f9fafb')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))

    # ── Estadísticas ──
    def stat_cell(label, value, color='#1e40af'):
        return Paragraph(
            f'<font color="{color}" size="14"><b>{value}</b></font>'
            f'<br/><font size="7" color="#6b7280">{label}</font>',
            ParagraphStyle('StatCell', parent=sNormal, alignment=1, leading=16)
        )

    stats_data = [[
        stat_cell('Total', total),
        stat_cell('Logins', total_logins),
        stat_cell('Creaciones', total_creaciones),
        stat_cell('Ediciones', total_ediciones),
        stat_cell('Descargas', total_descargas),
        stat_cell('Fallos', total_fallos, '#dc2626'),
    ]]
    stats_table = Table(stats_data, colWidths=[1.158 * inch] * 6, rowHeights=[0.45 * inch])
    stats_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#e5e7eb')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f9fafb')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))

    # ── Tabla de datos (9 columnas) ──
    usable_width = pagesize[0] - left_margin - right_margin
    col_widths = [0.85*inch, 0.85*inch, 0.85*inch, 0.85*inch, 0.75*inch, 1.0*inch, 0.8*inch, 0.7*inch, 0.75*inch]

    header_row = [
        Paragraph('<b>Fecha</b>', sCellBold),
        Paragraph('<b>Log ID</b>', sCellBold),
        Paragraph('<b>Usuario</b>', sCellBold),
        Paragraph('<b>Acción</b>', sCellBold),
        Paragraph('<b>Categoría</b>', sCellBold),
        Paragraph('<b>Recurso</b>', sCellBold),
        Paragraph('<b>IP</b>', sCellBold),
        Paragraph('<b>Hash</b>', sCellBold),
        Paragraph('<b>Resultado</b>', sCellBold),
    ]
    table_data = [header_row]

    for log in logs[:200]:
        ts = log.get('timestamp')
        fecha_str = ts.strftime('%d/%m/%Y %H:%M') if hasattr(ts, 'strftime') else '' if ts is None else str(ts)[:16]
        log_id = str(log.get('log_id') or '')[:20]
        usuario = log.get('usuario') or {}
        username = usuario.get('username') or ''
        nombre = usuario.get('nombre_completo') or ''
        usuario_str = nombre or username or ''
        accion = log.get('accion') or {}
        accion_str = (accion.get('tipo') or '').replace('_', ' ').title()
        categoria_str = (accion.get('categoria') or '').replace('_', ' ').title()
        recurso = log.get('recurso') or {}
        recurso_str = recurso.get('codigo') or recurso.get('tipo') or ''
        recurso_nombre = recurso.get('nombre') or ''
        if recurso_nombre and recurso_str:
            recurso_str = f'{recurso_str} ({recurso_nombre[:20]})'
        contexto = log.get('contexto') or {}
        ip = contexto.get('ip_address') or ''
        integridad = log.get('integridad') or {}
        hash_raw = integridad.get('hash') or ''
        hash_short = hash_raw.replace('sha256:', '')[:8] if hash_raw else ''
        resultado = log.get('resultado') or {}
        exitoso = resultado.get('exitoso')
        if exitoso is None:
            exitoso = True
        resultado_str = 'Éxito' if exitoso else 'Fallo'
        resultado_style = sCellOk if exitoso else sCellFail

        table_data.append([
            Paragraph(fecha_str, sCell),
            Paragraph(log_id, sCell),
            Paragraph(usuario_str, sCell),
            Paragraph(accion_str, sCell),
            Paragraph(categoria_str, sCell),
            Paragraph(recurso_str, sCell),
            Paragraph(ip, sCell),
            Paragraph(hash_short, sCell),
            Paragraph(resultado_str, resultado_style),
        ])

    data_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    row_bgs = [colors.white, colors.HexColor('#f3f4f6')]
    ts = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e40af')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 7),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d1d5db')),
        ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#1e40af')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ])
    for i in range(1, len(table_data)):
        ts.add('BACKGROUND', (0, i), (-1, i), row_bgs[i % 2])
    data_table.setStyle(ts)

    # ── Bloque vacío si no hay logs ──
    empty_block = []
    if total == 0:
        empty_block = [
            Spacer(1, 0.15 * inch),
            Paragraph(
                '<font color="#6b7280"><i>'
                'No hay registros disponibles en el período seleccionado. '
                'Esto puede deberse a que MongoDB no está conectado o no existen registros para los filtros indicados.'
                '</i></font>', sNormal),
            Spacer(1, 0.15 * inch),
        ]

    # ── Firmas (§7.5 aprobación) ──
    firmas_data = [
        [Paragraph(f'<b>Elaborado por:</b><br/><br/>'
                   f'{user_display}<br/>{user_rol}<br/>{now_date}', sSign),
         Paragraph(f'<b>Revisado por:</b><br/><br/><br/>_______________________<br/>Nombre y fecha', sSign),
         Paragraph(f'<b>Aprobado por:</b><br/><br/><br/>_______________________<br/>Nombre y fecha', sSign)],
    ]
    firmas_table = Table(firmas_data, colWidths=[2.32 * inch] * 3, rowHeights=[1.1 * inch])
    firmas_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#e5e7eb')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f9fafb')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))

    # ── Ensamblar todo ──
    elements = []
    elements.append(header_table)
    elements.append(Spacer(1, 0.05 * inch))
    elements.append(title_center)
    elements.append(Spacer(1, 0.12 * inch))
    elements.append(Paragraph('INFORMACIÓN DEL DOCUMENTO', sSection))
    elements.append(ctrl_table)
    elements.append(Spacer(1, 0.1 * inch))
    elements.append(Paragraph('RESUMEN ESTADÍSTICO', sSection))
    elements.append(stats_table)
    elements.append(Spacer(1, 0.12 * inch))
    elements.append(Paragraph('REGISTROS DE AUDITORÍA', sSection))
    elements.extend(empty_block)
    elements.append(data_table)
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(Paragraph('CONTROL DE APROBACIÓN — ISO 9001:2015 §7.5', sSection))
    elements.append(firmas_table)

    # ── Construir con numeración de páginas ──
    def draw_footer(canvas, doc_obj):
        canvas.saveState()
        w = pagesize[0]
        canvas.setStrokeColor(colors.HexColor('#d1d5db'))
        canvas.setLineWidth(0.5)
        canvas.line(left_margin, bottom_margin - 0.1 * inch, w - right_margin, bottom_margin - 0.1 * inch)
        canvas.setFont('Helvetica', 6.5)
        canvas.setFillColor(colors.HexColor('#9ca3af'))
        canvas.drawString(left_margin, bottom_margin - 0.25 * inch,
            'ISO 9001:2015 §7.5 · §9.2.2 — Información Documentada')
        canvas.drawCentredString(w / 2, bottom_margin - 0.25 * inch,
            'Retención: 10 años')
        canvas.drawRightString(w - right_margin, bottom_margin - 0.25 * inch,
            f'Página {doc_obj.page} de ?  ·  Generado: {now_str}')
        canvas.restoreState()

    doc = SimpleDocTemplate(
        response, pagesize=pagesize,
        leftMargin=left_margin, rightMargin=right_margin,
        topMargin=top_margin, bottomMargin=bottom_margin,
        title=f'Auditoría POLIDOC — {fecha_inicio} a {fecha_fin}',
        author=user_display,
        subject=f'Reporte de auditoría ISO 9001:2015 — Período {fecha_inicio} a {fecha_fin}',
        creator='POLIDOC v1.0',
    )
    doc.build(elements, onFirstPage=draw_footer, onLaterPages=draw_footer)

    return response


EMAIL_ATTACH_MAX_BYTES = 5 * 1024 * 1024


def _enviar_email_distribucion(distribucion, documento, tipo, request):
    """Envía el correo de una distribución. Retorna (ok, error).

    - (True, '') si se envió.
    - (False, 'sin_correo') si el destinatario no tiene email.
    - (False, '<detalle>') si falló el envío.
    """
    from django.conf import settings
    from django.core import mail
    from django.urls import reverse

    usuario = distribucion.usuario
    if not usuario.email:
        return False, 'sin_correo'

    link = request.build_absolute_uri(
        reverse('documentos:detalle', args=[tipo, documento.uuid])
    )
    asunto = f'[POLIDOC] Documento distribuido: {documento.codigo} - {documento.nombre}'
    lineas = [
        f'Hola {usuario.get_full_name() or usuario.username},',
        '',
        'Se te ha distribuido el siguiente documento:',
        '',
        f'  Código: {documento.codigo}',
        f'  Nombre: {documento.nombre}',
        f'  Versión: {documento.version_actual}',
        f'  Departamento: {documento.departamento}',
        '',
        f'Puedes verlo en: {link}',
        '',
        f'Distribuido por: {request.user.get_full_name() or request.user.username}',
    ]
    if distribucion.observaciones:
        lineas += ['', f'Observaciones: {distribucion.observaciones}']
    lineas += ['', '— POLIDOC']

    message = mail.EmailMessage(
        asunto, '\n'.join(lineas),
        settings.DEFAULT_FROM_EMAIL, [usuario.email],
    )
    archivo = documento.archivo
    if archivo and archivo.name:
        try:
            if archivo.size <= EMAIL_ATTACH_MAX_BYTES:
                message.attach_file(archivo.path)
        except (OSError, ValueError):
            pass

    try:
        mail.mailers.default.send_messages([message])
        return True, ''
    except Exception as exc:
        return False, str(exc)


def _enviar_email_revocacion(distribucion, documento, tipo, request):
    """Envía el correo de revocación de una distribución. Retorna (ok, error)."""
    from django.conf import settings
    from django.core import mail
    from django.urls import reverse

    usuario = distribucion.usuario
    if not usuario.email:
        return False, 'sin_correo'

    link = request.build_absolute_uri(
        reverse('documentos:detalle', args=[tipo, documento.uuid])
    )
    asunto = f'[POLIDOC] Distribución revocada: {documento.codigo} - {documento.nombre}'
    lineas = [
        f'Hola {usuario.get_full_name() or usuario.username},',
        '',
        'Se ha revocado tu distribución del siguiente documento:',
        '',
        f'  Código: {documento.codigo}',
        f'  Nombre: {documento.nombre}',
        f'  Versión: {documento.version_actual}',
        f'  Departamento: {documento.departamento}',
        '',
    ]
    if distribucion.motivo_revocacion:
        lineas += [f'Motivo: {distribucion.motivo_revocacion}', '']
    lineas += [
        f'Puedes verlo en: {link}',
        '',
        f'Revocado por: {request.user.get_full_name() or request.user.username}',
        '',
        '— POLIDOC',
    ]

    message = mail.EmailMessage(
        asunto, '\n'.join(lineas),
        settings.DEFAULT_FROM_EMAIL, [usuario.email],
    )

    try:
        mail.mailers.default.send_messages([message])
        return True, ''
    except Exception as exc:
        return False, str(exc)


def puede_revocar_distribucion(user, documento):
    """Solo admin, jefe del depto del documento o propietario pueden revocar."""
    perfil = user.perfil
    if perfil.es_administrador:
        return True
    if perfil.es_jefe and documento.departamento == perfil.departamento:
        return True
    return documento.propietario == user


@login_required(login_url='/login/')
def distribuir_documento(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    if not (request.user.perfil.es_administrador or
            (request.user.perfil.es_jefe and documento.departamento == request.user.perfil.departamento) or
            documento.propietario == request.user):
        messages.error(request, 'No tienes permisos para distribuir este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if documento.estado != 'activo':
        messages.error(request, 'Solo se pueden distribuir documentos activos.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    from .forms import DistribucionDocumentoForm
    from django.contrib.auth.models import User as UserModel
    from autenticacion.models import Notificacion
    if request.method == 'POST':
        form = DistribucionDocumentoForm(request.POST)
        if form.is_valid():
            metodo = form.cleaned_data['metodo']
            observaciones = form.cleaned_data['observaciones']
            usuarios = form.cleaned_data['usuarios']
            if not usuarios:
                messages.error(request, 'Debes seleccionar al menos un destinatario.')
                return redirect('documentos:distribuir_documento', tipo=tipo, documento_id=documento.uuid)
            count = 0
            sin_correo = 0
            fallidos = 0
            for usuario in usuarios:
                dist = DistribucionDocumento.objects.create(
                    documento=documento, usuario=usuario, metodo=metodo,
                    distribuido_por=request.user, observaciones=observaciones
                )
                try:
                    from auditoria.middleware import get_client_ip
                    from auditoria.services import registrar_distribucion
                    registrar_distribucion(
                        usuario=request.user,
                        recurso_tipo=documento.tipo_id or '',
                        recurso_id=str(documento.uuid),
                        destinatario=usuario.username,
                        metodo=metodo,
                        codigo=documento.codigo,
                        distribucion_id=dist.id,
                        ip_address=get_client_ip(),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("Auditoría de distribución falló: %s", exc)
                Notificacion.objects.create(
                    usuario=usuario, titulo='Documento Distribuido',
                    mensaje=f'Se le ha distribuido el documento "{documento.nombre}" ({documento.codigo}).',
                    tipo='documento_distribuido',
                    url_accion=f'/documentos/{tipo}/{documento.uuid}/',
                    departamento=documento.departamento,
                    documento=documento
                )
                if metodo == 'email':
                    ok, error = _enviar_email_distribucion(dist, documento, tipo, request)
                    if ok:
                        dist.enviado = True
                        dist.save(update_fields=['enviado'])
                    elif error == 'sin_correo':
                        sin_correo += 1
                    else:
                        dist.error = error
                        dist.save(update_fields=['error'])
                        fallidos += 1
                count += 1
            messages.success(request, f'Documento distribuido a {count} usuario(s) correctamente.')
            if metodo == 'email' and sin_correo:
                messages.warning(
                    request,
                    f'{sin_correo} destinatario(s) no tienen correo; no se les envió email.'
                )
            if metodo == 'email' and fallidos:
                messages.warning(request, f'{fallidos} correo(s) no se pudieron enviar.')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    else:
        form = DistribucionDocumentoForm()
    return render(request, 'documentos/distribuir_documento.html', {
        'form': form, 'documento': documento, 'tipo': tipo,
    })


@login_required(login_url='/login/')
def historial_distribucion(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    if documento is None:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    from .permissions import puede_ver_documento
    if not puede_ver_documento(request.user, documento):
        messages.error(request, 'No tienes permisos para ver este documento.')
        return redirect('documentos:mis_documentos')
    distribuciones = DistribucionDocumento.objects.filter(
        documento=documento
    ).order_by('-fecha_distribucion')
    return render(request, 'documentos/historial_distribucion.html', {
        'documento': documento, 'tipo': tipo, 'distribuciones': distribuciones,
        'puede_revocar': puede_revocar_distribucion(request.user, documento),
    })


@login_required(login_url='/login/')
def revocar_distribucion(request, tipo, documento_id, dist_id):
    """Revoca una distribución de documento."""
    if request.method != 'POST':
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento_id)
    documento = get_documento(tipo, documento_id)
    if not documento:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    from django.shortcuts import get_object_or_404
    distribucion = get_object_or_404(
        DistribucionDocumento, id=dist_id, documento=documento
    )
    user = request.user
    if not puede_revocar_distribucion(user, documento):
        messages.error(request, 'No tienes permisos para revocar distribuciones de este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if distribucion.revocado:
        messages.warning(request, 'Esta distribución ya fue revocada anteriormente.')
        return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento.uuid)
    from django.utils import timezone as tz
    motivo = request.POST.get('motivo', '').strip()
    distribucion.revocado = True
    distribucion.fecha_revocacion = tz.now()
    distribucion.revocado_por = user
    distribucion.motivo_revocacion = motivo
    distribucion.save(update_fields=[
        'revocado', 'fecha_revocacion', 'revocado_por', 'motivo_revocacion',
    ])
    from autenticacion.models import Notificacion
    Notificacion.objects.create(
        usuario=distribucion.usuario,
        titulo='Distribución revocada',
        mensaje=f'{user.get_full_name() or user.username} revocó la distribución del documento "{documento.nombre}" ({documento.codigo}).',
        tipo='distribucion_revocada',
    )
    Notificacion.objects.filter(
        usuario=distribucion.usuario,
        documento=documento,
        tipo='documento_distribuido',
    ).update(url_accion='')
    ok, error = _enviar_email_revocacion(distribucion, documento, tipo, request)
    if ok:
        distribucion.email_revocacion_enviado = True
        distribucion.save(update_fields=['email_revocacion_enviado'])
    elif error != 'sin_correo':
        distribucion.error_revocacion = error
        distribucion.save(update_fields=['error_revocacion'])
    try:
        from auditoria.services import registrar_log_iso
        from auditoria.middleware import get_client_ip
        registrar_log_iso(
            usuario=user,
            accion_tipo='revocacion_distribucion',
            accion_categoria='distribucion',
            recurso_tipo=tipo,
            recurso_id=str(documento.uuid),
            recurso_codigo=documento.codigo,
            recurso_nombre=documento.nombre,
            detalles={
                'dist_id': distribucion.id,
                'destinatario': distribucion.usuario.username,
                'motivo': motivo,
            },
            resultado_exitoso=True,
            resultado_mensaje=f'Distribución #{distribucion.id} revocada: {distribucion.usuario.username} {motivo}',
            ip_address=get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT'),
        )
    except Exception as exc:
        logger.error("Auditoría revocación de distribución: %s", exc)
    messages.success(
        request,
        f'Distribución a {distribucion.usuario.get_full_name() or distribucion.usuario.username} revocada correctamente.',
    )
    return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento.uuid)


# =============================================================================
# CONFIGURACIÓN: FORMATOS / FORMULARIOS (Motor configurable)
# =============================================================================

def _admin_required(request):
    if not (request.user.perfil.es_administrador or request.user.perfil.es_jefe):
        messages.error(request, 'No tienes permisos para acceder a configuración.')
        return False
    return True


@login_required(login_url='/login/')
def formatos_lista(request):
    if not _admin_required(request):
        return redirect('documentos:mis_documentos')
    formatos = Formato.objects.all().order_by('codigo')
    return render(request, 'documentos/configuracion/formatos_lista.html', {'formatos': formatos})


@login_required(login_url='/login/')
def formato_crear(request):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden crear formatos.')
        return redirect('documentos:formatos_lista')
    from .forms import FormatoForm
    if request.method == 'POST':
        form = FormatoForm(request.POST, request.FILES)
        if form.is_valid():
            formato = form.save()
            messages.success(request, f'Formato "{formato.codigo}" creado correctamente.')
            return redirect('documentos:formato_constructor', formato_id=formato.id)
    else:
        form = FormatoForm()
    return render(request, 'documentos/configuracion/formato_form.html', {
        'form': form, 'accion': 'Crear',
    })


@login_required(login_url='/login/')
def formato_editar(request, formato_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden editar formatos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    from .forms import FormatoForm
    if request.method == 'POST':
        form = FormatoForm(request.POST, request.FILES, instance=formato)
        if form.is_valid():
            form.save()
            messages.success(request, f'Formato "{formato.codigo}" actualizado correctamente.')
            return redirect('documentos:formato_constructor', formato_id=formato.id)
    else:
        form = FormatoForm(instance=formato)
    return render(request, 'documentos/configuracion/formato_form.html', {
        'form': form, 'formato': formato, 'accion': 'Editar',
    })


@login_required(login_url='/login/')
def formato_eliminar(request, formato_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden desactivar formatos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    if request.method == 'POST':
        formato.activo = False
        formato.save(update_fields=['activo'])
        messages.success(request, f'Formato "{formato.codigo}" desactivado correctamente.')
        return redirect('documentos:formatos_lista')
    return render(request, 'documentos/configuracion/formato_confirmar.html', {'formato': formato})


@login_required(login_url='/login/')
def formato_reactivar(request, formato_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Solo los administradores pueden reactivar formatos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    if request.method == 'POST':
        formato.activo = True
        formato.save(update_fields=['activo'])
        messages.success(request, f'Formato "{formato.codigo}" reactivado correctamente.')
    return redirect('documentos:formatos_lista')


@login_required(login_url='/login/')
def formato_constructor(request, formato_id):
    """Constructor de secciones y campos del formato."""
    if not _admin_required(request):
        return redirect('documentos:mis_documentos')
    formato = get_object_or_404(Formato, id=formato_id)
    secciones = formato.get_secciones()
    campos_por_seccion = {}
    for seccion in secciones:
        campos_por_seccion[seccion.id] = seccion.campos.all().order_by('orden')
    from .forms import SeccionFormatoForm, CampoFormatoForm
    seccion_form = SeccionFormatoForm()
    campo_form = CampoFormatoForm()
    campo_form.fields['tipo'].choices = CampoFormato.TIPO_CHOICES
    documentos = Documento.objects.select_related('departamento').order_by('codigo').values(
        'id', 'codigo', 'nombre', 'departamento__codigo',
    )
    documentos_formato_ids = list(
        SolicitudCambio.objects.filter(formato=formato, documento__isnull=False)
        .values_list('documento_id', flat=True).distinct()
    )
    return render(request, 'documentos/configuracion/formato_constructor.html', {
        'formato': formato,
        'secciones': secciones,
        'campos_por_seccion': campos_por_seccion,
        'seccion_form': seccion_form,
        'campo_form': campo_form,
        'documentos': documentos,
        'documentos_formato_ids': documentos_formato_ids,
    })


def _incrementar_version(valor):
    try:
        return str(int(str(valor).strip()) + 1)
    except (ValueError, TypeError):
        return '2'


@login_required(login_url='/login/')
def formato_publicar(request, formato_id):
    """Publica una nueva versión o edición del formato.

    Para 'edicion' notifica a propietario, jefe del departamento y
    administradores de los documentos seleccionados.
    """
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Sin permisos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    if request.method != 'POST':
        return redirect('documentos:formato_constructor', formato_id=formato.id)

    tipo = request.POST.get('tipo')
    if tipo not in ('version', 'edicion'):
        messages.error(request, 'Tipo de publicación inválido.')
        return redirect('documentos:formato_constructor', formato_id=formato.id)

    observaciones = request.POST.get('observaciones', '').strip()
    version_anterior = formato.version
    edicion_anterior = formato.edicion
    if tipo == 'version':
        formato.version = _incrementar_version(formato.version)
    else:
        formato.edicion = _incrementar_version(formato.edicion)
    formato.save(update_fields=['version', 'edicion'])

    publicacion = FormatoPublicacion.objects.create(
        formato=formato, tipo=tipo,
        version_anterior=version_anterior, version_nueva=formato.version,
        edicion_anterior=edicion_anterior, edicion_nueva=formato.edicion,
        observaciones=observaciones, publicado_por=request.user,
    )

    try:
        from auditoria.services import registrar_log_iso
        from auditoria.middleware import get_client_ip
        registrar_log_iso(
            usuario=request.user,
            accion_tipo='publicacion_formato',
            accion_categoria='configuracion',
            recurso_tipo='formato',
            recurso_id=str(formato.id),
            recurso_codigo=formato.codigo,
            recurso_nombre=formato.nombre,
            detalles={
                'tipo': tipo,
                'version_anterior': version_anterior,
                'version_nueva': formato.version,
                'edicion_anterior': edicion_anterior,
                'edicion_nueva': formato.edicion,
            },
            resultado_exitoso=True,
            resultado_mensaje=(
                f'{formato.codigo} {tipo}: '
                f'v{version_anterior}E{edicion_anterior} → '
                f'v{formato.version}E{formato.edicion}'
            ),
            ip_address=get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT'),
        )
    except Exception as exc:
        logger.error("Auditoría publicación formato: %s", exc)

    if tipo == 'edicion':
        from autenticacion.models import Notificacion, PerfilUsuario
        ids = request.POST.getlist('documentos')
        documentos = Documento.objects.filter(id__in=ids).select_related('departamento', 'propietario')
        admins = [
            p.usuario for p in PerfilUsuario.objects.filter(
                rol='administrador', usuario__is_active=True,
            ).select_related('usuario')
        ]
        for doc in documentos:
            destinatarios = {u for u in [doc.propietario] if u}
            jefe = doc.departamento.jefe if doc.departamento else None
            if jefe:
                destinatarios.add(jefe)
            destinatarios.update(admins)
            for usuario in destinatarios:
                Notificacion.objects.create(
                    usuario=usuario,
                    titulo='Nueva edición de formato',
                    mensaje=(
                        f'El formato {formato.codigo} cambió a la edición '
                        f'{formato.edicion} (versión {formato.version}). '
                        f'Revisa el documento "{doc.nombre}" ({doc.codigo}).'
                    ),
                    tipo='edicion_formato',
                    url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
                    departamento=doc.departamento,
                    documento=doc,
                )

    messages.success(
        request,
        f'Publicado: {formato.codigo} {publicacion.get_tipo_display()} '
        f'(v{formato.version} E{formato.edicion}).'
    )
    return redirect('documentos:formato_constructor', formato_id=formato.id)


@login_required(login_url='/login/')
def seccion_crear(request, formato_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Sin permisos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    from .forms import SeccionFormatoForm
    if request.method == 'POST':
        form = SeccionFormatoForm(request.POST)
        if form.is_valid():
            seccion = form.save(commit=False)
            seccion.formato = formato
            seccion.save()
            messages.success(request, f'Sección "{seccion.titulo}" creada.')
    return redirect('documentos:formato_constructor', formato_id=formato.id)


@login_required(login_url='/login/')
def seccion_editar(request, formato_id, seccion_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Sin permisos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    seccion = get_object_or_404(SeccionFormato, id=seccion_id, formato=formato)
    from .forms import SeccionFormatoForm
    if request.method == 'POST':
        form = SeccionFormatoForm(request.POST, instance=seccion)
        if form.is_valid():
            form.save()
            messages.success(request, f'Sección "{seccion.titulo}" actualizada.')
            return redirect('documentos:formato_constructor', formato_id=formato.id)
    else:
        form = SeccionFormatoForm(instance=seccion)
    return render(request, 'documentos/configuracion/seccion_form.html', {
        'form': form, 'formato': formato, 'seccion': seccion, 'accion': 'Editar',
    })


@login_required(login_url='/login/')
def seccion_eliminar(request, formato_id, seccion_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Sin permisos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    seccion = get_object_or_404(SeccionFormato, id=seccion_id, formato=formato)
    if request.method == 'POST':
        nombre = seccion.titulo
        seccion.delete()
        messages.success(request, f'Sección "{nombre}" eliminada.')
    return redirect('documentos:formato_constructor', formato_id=formato.id)


@login_required(login_url='/login/')
def campo_crear(request, formato_id, seccion_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Sin permisos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    seccion = get_object_or_404(SeccionFormato, id=seccion_id, formato=formato)
    from .forms import CampoFormatoForm
    if request.method == 'POST':
        form = CampoFormatoForm(request.POST)
        if form.is_valid():
            campo = form.save(commit=False)
            campo.seccion = seccion
            campo.save()
            messages.success(request, f'Campo "{campo.etiqueta}" creado.')
            return redirect('documentos:formato_constructor', formato_id=formato.id)
    else:
        form = CampoFormatoForm()
    return render(request, 'documentos/configuracion/campo_form.html', {
        'form': form, 'formato': formato, 'seccion': seccion, 'accion': 'Crear',
    })


def _puede_ver_solicitud(user, solicitud):
    """Checks if the user can view/download a SolicitudCambio."""
    if user.perfil.es_administrador:
        return True
    if solicitud.solicitante == user:
        return True
    # Un jefe solo ve solicitudes de su propio departamento (no las ajenas).
    if solicitud.documento and solicitud.documento.departamento:
        if solicitud.documento.departamento.jefe == user:
            return True
    depto_sol = getattr(solicitud, 'departamento', None)
    if depto_sol and depto_sol.jefe == user:
        return True
    return False


@login_required(login_url='/login/')
def descargar_solicitud_xlsx(request, solicitud_uuid):
    """Descarga el XLSX lleno de una solicitud de cambio."""
    from .models import SolicitudCambio
    from django.http import FileResponse
    solicitud = get_object_or_404(SolicitudCambio, uuid=solicitud_uuid)
    if not _puede_ver_solicitud(request.user, solicitud):
        messages.error(request, 'No tienes permisos para descargar esta solicitud.')
        return redirect('documentos:mis_documentos')
    if solicitud.planilla_xlsx:
        try:
            return FileResponse(
                solicitud.planilla_xlsx.open('rb'), as_attachment=True,
                filename=os.path.basename(solicitud.planilla_xlsx.name),
            )
        except Exception as e:
            logger.warning(f"Planilla XLSX guardada no disponible para {solicitud.codigo}: {e}")
    if not solicitud.formato or not solicitud.formato.plantilla_xlsx:
        messages.error(request, 'No hay plantilla Excel disponible para exportar.')
        return redirect('documentos:mis_documentos')
    try:
        from .services.exportacion import exportar_xlsx
        xlsx_bytes = exportar_xlsx(solicitud)
        nombre = f"{solicitud.codigo}_{solicitud.get_operacion_display().replace(' ', '_')}.xlsx"
        import io
        return FileResponse(io.BytesIO(xlsx_bytes), as_attachment=True, filename=nombre)
    except Exception as e:
        logger.error(f"Error exportando XLSX para solicitud {solicitud.codigo}: {e}")
        messages.error(request, 'Error al generar el archivo XLSX.')
        return redirect('documentos:mis_documentos')


@login_required(login_url='/login/')
def descargar_solicitud_pdf(request, solicitud_uuid):
    """Descarga el PDF de una solicitud de cambio (generado desde XLSX)."""
    from .models import SolicitudCambio
    from django.http import FileResponse
    solicitud = get_object_or_404(SolicitudCambio, uuid=solicitud_uuid)
    if not _puede_ver_solicitud(request.user, solicitud):
        messages.error(request, 'No tienes permisos para descargar esta solicitud.')
        return redirect('documentos:mis_documentos')
    if solicitud.planilla_pdf:
        try:
            return FileResponse(
                solicitud.planilla_pdf.open('rb'), as_attachment=True,
                filename=os.path.basename(solicitud.planilla_pdf.name),
            )
        except Exception as e:
            logger.warning(f"Planilla PDF guardada no disponible para {solicitud.codigo}: {e}")
    if not solicitud.formato or not solicitud.formato.plantilla_xlsx:
        messages.error(request, 'No hay plantilla Excel disponible para exportar.')
        return redirect('documentos:mis_documentos')
    tmp_pdf = None
    try:
        from .services.exportacion import exportar_pdf
        pdf_path = exportar_pdf(solicitud)
        if not pdf_path:
            messages.error(request, 'No se pudo generar el PDF. Verifique que LibreOffice esté instalado.')
            return redirect('documentos:mis_documentos')
        from django.conf import settings
        if not os.path.isabs(pdf_path):
            pdf_path = os.path.join(settings.MEDIA_ROOT, pdf_path)
        tmp_pdf = pdf_path
        nombre = f"{solicitud.codigo}_{solicitud.get_operacion_display().replace(' ', '_')}.pdf"
        return FileResponse(open(pdf_path, 'rb'), as_attachment=True, filename=nombre)
    except Exception as e:
        logger.error(f"Error exportando PDF para solicitud {solicitud.codigo}: {e}")
        messages.error(request, 'Error al generar el archivo PDF.')
        return redirect('documentos:mis_documentos')
    finally:
        if tmp_pdf and os.path.exists(tmp_pdf):
            try:
                os.unlink(tmp_pdf)
            except OSError:
                pass


@login_required(login_url='/login/')
def campo_editar(request, formato_id, campo_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Sin permisos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    campo = get_object_or_404(CampoFormato, id=campo_id, seccion__formato=formato)
    from .forms import CampoFormatoForm
    if request.method == 'POST':
        form = CampoFormatoForm(request.POST, instance=campo)
        if form.is_valid():
            form.save()
            messages.success(request, f'Campo "{campo.etiqueta}" actualizado.')
            return redirect('documentos:formato_constructor', formato_id=formato.id)
    else:
        form = CampoFormatoForm(instance=campo)
    return render(request, 'documentos/configuracion/campo_form.html', {
        'form': form, 'formato': formato, 'seccion': campo.seccion, 'accion': 'Editar',
    })


@login_required(login_url='/login/')
def campo_eliminar(request, formato_id, campo_id):
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Sin permisos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    campo = get_object_or_404(CampoFormato, id=campo_id, seccion__formato=formato)
    if request.method == 'POST':
        nombre = campo.etiqueta
        campo.delete()
        messages.success(request, f'Campo "{nombre}" eliminado.')
    return redirect('documentos:formato_constructor', formato_id=formato.id)


@login_required(login_url='/login/')
def formato_preview(request, formato_id):
    """Preview del formato (PDF si está disponible, sino página de vista previa)."""
    if not _admin_required(request):
        return redirect('documentos:mis_documentos')
    formato = get_object_or_404(Formato, id=formato_id)
    return render(request, 'documentos/configuracion/formato_preview.html', {'formato': formato})


@login_required(login_url='/login/')
def formato_regenerar_preview(request, formato_id):
    """Regenerar el PDF de preview desde la plantilla Excel."""
    if not request.user.perfil.es_administrador:
        messages.error(request, 'Sin permisos.')
        return redirect('documentos:formatos_lista')
    formato = get_object_or_404(Formato, id=formato_id)
    if formato.plantilla_xlsx:
        from .tasks import convertir_a_pdf_sync
        pdf_path = convertir_a_pdf_sync(formato.plantilla_xlsx.path)
        if pdf_path:
            import os
            media_root = str(formato.plantilla_xlsx.storage.location or '')
            if not media_root:
                from django.conf import settings
                media_root = str(settings.MEDIA_ROOT)
            rel = os.path.relpath(pdf_path, media_root) if pdf_path.startswith(media_root) else pdf_path
            formato.plantilla_pdf = rel
            formato.save(update_fields=['plantilla_pdf'])
            messages.success(request, 'Preview regenerado correctamente.')
        else:
            messages.error(request, 'Error al generar el PDF.')
    else:
        messages.warning(request, 'No hay plantilla Excel cargada.')
    return redirect('documentos:formato_constructor', formato_id=formato.id)



