import io
import os
import json
import logging
import uuid
from datetime import datetime, date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, JsonResponse
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q, Count
from django.db import transaction
from django.conf import settings
from .models import (
    Documento, VersionDocumento, SolicitudDescarga, AprobacionDescarga,
    PlanillaCambios, AprobacionPlanilla, AsignacionDocumento,
    Departamento, TipoDocumento, SolicitudAprobacionDocumento,
    DocumentoAsociadoPlanilla, DistribucionDocumento, SolicitudAccesoDocumento,
    RenovacionVencimiento, ReferenciaDocumento, Formato, SeccionFormato, CampoFormato,
    SolicitudCambio, Periodo, FormatoPublicacion, respuestas_planilla_actual,
    DelegacionJefatura, RegistroFormato, FirmaElectronica,
    DistribucionRegistro, DocumentoSinCodigo,
)
from .forms import (
    DocumentoForm, PlantillaForm, SolicitudDescargaForm, PlanillaCambiosForm,
    AsignacionDocumentoForm, FiltroDocumentoForm, DistribucionDocumentoForm,
    RegistroFormatoForm, validar_respuestas, validar_respuestas_guardadas,
)
from .icons import ICONOS_DISPONIBLES
from .jefatura import es_jefe_efectivo
from .services.aprobaciones import (
    notificar_planilla_resuelta, resolver_aprobacion_planilla,
    tomar_paso_esperando,
)
from .services.firmas import ErrorFirma, VIGENCIA_INVITACION_DIAS, firmar
from .services.planillas import invalidar_planillas_documento
from .services.vigencia import calcular_conteos_vigencia
from .cache_utils import tipos_activos, periodos_activos, departamentos_activos
from . import ai

logger = logging.getLogger(__name__)


def _ip_auditoria(request):
    """IP real del cliente (proxy-aware); cae a REMOTE_ADDR sin middleware."""
    try:
        from auditoria.middleware import get_client_ip, resolver_ip_cliente

        return get_client_ip() or resolver_ip_cliente(request)
    except Exception:  # noqa: BLE001 - nunca romper por la auditoría
        return request.META.get('REMOTE_ADDR')


def _registrar_auditoria(request, *, accion, categoria='documento',
                         recurso_tipo, recurso_id, recurso_codigo='',
                         recurso_nombre='', detalles=None):
    """Auditoría ISO de una acción web; nunca rompe la petición."""
    try:
        from auditoria.services import registrar_log_iso

        registrar_log_iso(
            usuario=request.user,
            accion_tipo=accion,
            accion_categoria=categoria,
            recurso_tipo=recurso_tipo,
            recurso_id=str(recurso_id),
            recurso_codigo=recurso_codigo or '',
            recurso_nombre=recurso_nombre or '',
            detalles=detalles or {},
            resultado_exitoso=True,
            resultado_mensaje=accion,
            ip_address=_ip_auditoria(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Auditoría %s: %s', accion, exc)


class _PaginatorConteoFijo(Paginator):
    """Paginator con el total ya calculado (evita un COUNT por página/tipo)."""

    def __init__(self, object_list, per_page, count, **kwargs):
        super().__init__(object_list, per_page, **kwargs)
        self._conteo_fijo = count

    @property
    def count(self):
        return self._conteo_fijo


def get_documento(tipo, documento_id):
    """Obtiene un documento por tipo y UUID"""
    try:
        return Documento.objects.select_related(
            'tipo', 'departamento', 'propietario',
            'periodo_vigencia', 'periodo_retencion', 'plantilla_origen',
        ).get(uuid=documento_id, tipo=tipo)
    except Documento.DoesNotExist:
        raise Http404("Documento no encontrado")


from .services.aprobacion_flujo import (
    _aprobador_plantilla, _crear_aprobacion_planilla_plantilla,
    _crear_solicitud_plantilla, _es_aprobacion_unica, _es_jefe,
    crear_cadena_aprobacion_documento, crear_cadena_aprobacion_planilla,
    determinar_autorizador, determinar_evaluador,
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
        from .jefatura import es_jefe_efectivo

        if es_jefe_efectivo(user, documento.departamento):
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
    from .services.cambios_estado import es_jefe_gg
    if es_jefe_gg(user):
        # Gerencia General es la autoridad máxima: inhabilita directo.
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

    codigos = []
    for ref in referencias_codigos:
        codigo = str(ref).strip().upper()
        if codigo and codigo not in codigos:
            codigos.append(codigo)
    if not codigos:
        return

    destinos = {
        d.codigo: d
        for d in Documento.objects.filter(codigo__in=codigos).select_related(
            'departamento',
        )
    }
    relacionados = []
    pendientes = []
    for codigo in codigos:
        destino = destinos.get(codigo)
        if destino is None:
            pendientes.append(codigo)
            continue
        mismo_depto = destino.departamento_id == perfil.departamento_id
        es_accesible = mismo_depto or destino.clasificacion == 'publico'
        if es_accesible and destino.estado == 'activo':
            relacionados.append(destino)
        elif destino.estado != 'activo':
            pendientes.append(codigo)
        else:
            messages.warning(
                request,
                f'Sin acceso al documento {codigo}. No se agregó la referencia.',
            )

    if relacionados:
        # SGC-FOR-004: no reenlazar registros ya retirados por un acta
        # aplicada (la evidencia del acta quedaría contradicha).
        from .services.for_004 import filtrar_relaciones_bloqueadas

        bloqueados = len(relacionados)
        relacionados = filtrar_relaciones_bloqueadas(doc, relacionados)
        bloqueados -= len(relacionados)
        if bloqueados:
            messages.warning(
                request,
                f'{bloqueados} referencia(s) ya fueron desincorporadas por un '
                f'acta SGC-FOR-004 aplicada y no se volvieron a relacionar.',
            )
        if relacionados:
            doc.documentos_relacionados.add(*relacionados)
    if pendientes:
        ReferenciaDocumento.objects.bulk_create([
            ReferenciaDocumento(
                documento=doc, codigo_destino=codigo,
                creado_por=request.user,
            )
            for codigo in pendientes
        ], ignore_conflicts=True)


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
    uuids = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get('pendiente'):
            codigo = str(item.get('codigo', '')).strip().upper()
            if codigo:
                pendientes.append(codigo)
        elif item.get('uuid'):
            uuids.append(str(item['uuid']))

    existentes = {
        str(d.uuid): d
        for d in asociables.filter(uuid__in=uuids)
    }
    nuevos = []
    for uuid_item in dict.fromkeys(uuids):
        doc_asoc = existentes.get(uuid_item)
        if doc_asoc:
            nuevos.append(DocumentoAsociadoPlanilla(
                planilla=planilla, documento=doc_asoc,
            ))
    if nuevos:
        DocumentoAsociadoPlanilla.objects.bulk_create(
            nuevos, ignore_conflicts=True,
        )
    planilla.referencias_pendientes = pendientes
    planilla.save(update_fields=['referencias_pendientes'])


@login_required(login_url='/login/')
def formularios(request):
    user = request.user
    dept = user.perfil.departamento
    # Catálogo de PLANTILLAS (documentos en blanco es_plantilla=True).
    # Se listan las activas visibles para el usuario (mismo criterio de
    # clasificación/permisos que el resto de Documento).
    from .permissions import documentos_descargables, q_documentos_visibles

    visibles = q_documentos_visibles(user)
    # Las plantillas de uso INTERNO pertenecen a su departamento: las ve
    # cualquier miembro de ese departamento (mismo criterio que
    # puede_ver_documento), no solo el propietario o el jefe. Confidencial y
    # restringido conservan su propio control de acceso.
    if not user.perfil.es_administrador and dept:
        visibles |= Q(departamento=dept, clasificacion='interno')
    docs = Documento.objects.filter(
        es_plantilla=True, estado='activo',
    ).filter(visibles).select_related('departamento', 'propietario', 'tipo')

    solicitudes = SolicitudDescarga.objects.filter(
        solicitado_por=user
    ).values_list('documento__uuid', 'estado')
    docs_pendientes = {uuid for uuid, e in solicitudes if e == 'pendiente'}

    docs = list(docs)
    docs_descargables = documentos_descargables(user, docs)

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
        'puede_subir': True,
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
    registro_filter = request.GET.get('registro', 'todos')  # todos|documentos|plantillas

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

    # Mismo conjunto base para todos los tipos: propios + (coordinador)
    # asignados. El conteo por tipo se resuelve en UNA sola consulta agrupada
    # en vez de un COUNT por cada tipo activo (antes: ~15 COUNTs por carga).
    docs_base = Documento.objects.filter(propietario=user)
    if perfil.es_coordinador:
        ids_asignados = AsignacionDocumento.objects.filter(
            coordinador=user, activa=True, puede_leer=True,
        ).values('documento_id')
        docs_base = Documento.objects.filter(
            Q(propietario=user) | Q(id__in=ids_asignados)
        )
    if buscar:
        docs_base = docs_base.filter(
            Q(nombre__icontains=buscar) | Q(codigo__icontains=buscar)
            | Q(documentos_relacionados__in=target_ids)
            | Q(referencias_salientes__codigo_destino__icontains=buscar)
        ).distinct()
    if registro_filter == 'documentos':
        docs_base = docs_base.filter(es_plantilla=False)
    elif registro_filter == 'plantillas':
        docs_base = docs_base.filter(es_plantilla=True)
    if estado_filter:
        docs_base = docs_base.filter(estado=estado_filter)
    if clasificacion_filter:
        docs_base = docs_base.filter(clasificacion=clasificacion_filter)

    conteos_por_tipo = dict(
        docs_base.order_by().values_list('tipo').annotate(total=Count('id'))
    )

    tipos_documento = []
    for tipo in tipos_activos():
        tipo_key = tipo.key
        total = conteos_por_tipo.get(tipo_key, 0)
        if not total:
            continue
        docs = docs_base.filter(tipo=tipo_key).select_related(
            'departamento', 'tipo', 'periodo_vigencia', 'propietario',
        )
        paginator = _PaginatorConteoFijo(docs, 12, count=total)
        page = paginator.get_page(request.GET.get(f'page_{tipo_key}', 1))
        tipos_documento.append({
            'key': tipo_key,
            'nombre': tipo.nombre,
            'codigo': tipo.codigo,
            'icono': tipo.icono or 'file',
            'color': tipo.color or 'primary',
            'documentos': page,
        })

    # Borradores de planilla propios: permiten retomarlos desde la tarjeta en
    # vez de crear un segundo borrador para el mismo documento.
    planillas_borrador = {
        doc_id: str(pl_uuid)
        for doc_id, pl_uuid in PlanillaCambios.objects.filter(
            responsable=user, estado='borrador', documento__isnull=False,
        ).values_list('documento_id', 'uuid').order_by('id')
    }

    return render(request, 'documentos/mis_documentos.html', {
        'tipos_documento': tipos_documento,
        'planillas_borrador': planillas_borrador,
        'buscar': buscar,
        'estado_filter': estado_filter,
        'clasificacion_filter': clasificacion_filter,
        'registro_filter': registro_filter,
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
    from .jefatura import aprobadores_que_repr, es_delegado_vigente

    if not (perfil.es_jefe or perfil.es_administrador
            or es_delegado_vigente(request.user)):
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('documentos:mis_documentos')
    filtro_estado = request.GET.get('estado', 'pendiente')
    filtro_tipo = request.GET.get('tipo_accion', '')
    dept = perfil.departamento
    aprobadores = aprobadores_que_repr(request.user)
    solicitudes_qs = (
        Q(aprobador_id__in=aprobadores) |
        Q(departamento_origen=dept, rol_aprobador='evaluador')
    )
    if perfil.es_administrador:
        # Plantillas sin jefe de departamento: quedan 'esperando' y los
        # administradores deben poder tomarlas para aprobarlas/rechazarlas.
        solicitudes_qs |= Q(estado='esperando', aprobador__isnull=True)
    solicitudes = SolicitudAprobacionDocumento.objects.filter(
        solicitudes_qs
    ).select_related('solicitante', 'departamento_origen', 'propietario', 'documento').distinct()
    estados_pendientes = ('pendiente', 'esperando')
    if filtro_estado == 'pendiente':
        solicitudes = solicitudes.filter(estado__in=estados_pendientes)
    elif filtro_estado:
        solicitudes = solicitudes.filter(estado=filtro_estado)
    if filtro_tipo:
        solicitudes = solicitudes.filter(accion_solicitud=filtro_tipo)
    from django.core.paginator import Paginator
    paginator = Paginator(solicitudes, 20)
    page = request.GET.get('page', 1)
    solicitudes_page = paginator.get_page(page)
    total_pendientes = SolicitudAprobacionDocumento.objects.filter(
        solicitudes_qs,
    ).filter(estado__in=estados_pendientes).distinct().count()
    descargas = AprobacionDescarga.objects.filter(
        aprobador_id__in=aprobadores, solicitud__estado='pendiente',
    ).select_related('solicitud', 'solicitud__documento', 'solicitud__solicitado_por')
    if filtro_estado == 'pendiente':
        descargas = descargas.filter(estado__in=estados_pendientes)
    elif filtro_estado:
        descargas = descargas.filter(estado=filtro_estado)
    total_pendientes_descarga = AprobacionDescarga.objects.filter(
        aprobador_id__in=aprobadores, estado__in=estados_pendientes,
        solicitud__estado='pendiente',
    ).count()
    planillas_qs = Q(aprobador_id__in=aprobadores)
    pendientes_qs = Q(
        aprobador_id__in=aprobadores, estado__in=estados_pendientes,
    )
    if perfil.es_administrador:
        # Planillas de plantillas sin jefe: 'esperando' y tomables por admin.
        planillas_qs |= Q(estado='esperando', aprobador__isnull=True)
        pendientes_qs |= Q(estado='esperando', aprobador__isnull=True)
    planillas = AprobacionPlanilla.objects.filter(
        planillas_qs,
    ).select_related('planilla', 'planilla__documento', 'planilla__responsable')
    if filtro_estado == 'pendiente':
        planillas = planillas.filter(estado__in=estados_pendientes)
    elif filtro_estado:
        planillas = planillas.filter(estado=filtro_estado)
    total_pendientes_planilla = AprobacionPlanilla.objects.filter(
        pendientes_qs,
    ).count()
    return render(request, 'documentos/bandeja_aprobaciones.html', {
        'solicitudes': solicitudes_page,
        'filtro_estado': filtro_estado,
        'filtro_tipo': filtro_tipo,
        'total_pendientes': total_pendientes,
        'descargas': descargas,
        'total_pendientes_descarga': total_pendientes_descarga,
        'planillas': planillas,
        'total_pendientes_planilla': total_pendientes_planilla,
    })


@login_required(login_url='/login/')
def mis_solicitudes(request):
    """Historial de lo que el usuario ha solicitado en el sistema.

    Descargas, cambios de estado y accesos, normalizados por
    ``services.solicitudes``. Visible para cualquier usuario autenticado.
    """
    from django.core.paginator import Paginator

    from .services.solicitudes import FILTRO_ESTADOS, TIPOS_LABEL
    from .services.solicitudes import mis_solicitudes as _mis_solicitudes

    filtro_estado = request.GET.get('estado', '')
    if filtro_estado not in FILTRO_ESTADOS:
        filtro_estado = ''
    filtro_tipo = request.GET.get('tipo', '')
    if filtro_tipo not in TIPOS_LABEL:
        filtro_tipo = ''

    items, totales = _mis_solicitudes(
        request.user, estado=filtro_estado, tipo=filtro_tipo,
    )
    items_page = Paginator(items, 20).get_page(request.GET.get('page', 1))
    opciones_tipo = sorted(TIPOS_LABEL.items(), key=lambda par: par[1])
    return render(request, 'documentos/mis_solicitudes.html', {
        'items': items_page,
        'totales': totales,
        'filtro_estado': filtro_estado,
        'filtro_tipo': filtro_tipo,
        'opciones_tipo': opciones_tipo,
    })


def _grupos_documentos(departamentos, clasificacion='', vigencia='', usuario=None,
                       solo_departamento=False, registro=''):
    """Agrupa documentos por departamento (propio | afectado) y por tipo, con
    consultas fijas (sin N+1). Reutilizado por jefe y admin.

    `departamentos`: lista ordenada de Departamento.
    `clasificacion`: filtro opcional por clasificación de seguridad.
    `vigencia`: filtro opcional ('vencido'|'vence_pronto'|'vigente'|'sin_vigencia').
    `usuario`: si se pasa, solo se incluyen documentos visibles para él.
    `solo_departamento`: modo estricto de la vista "Departamento" para
      jefes/delegados: solo documentos propiedad del departamento (sin
      públicos ajenos, sin afectados de otros departamentos y sin la sección
      "Todos los departamentos"). El admin no lo usa (vista de supervisión).
    `registro`: filtro opcional 'documentos'|'plantillas' (vacío = todos).
    """
    from .permissions import q_documentos_visibles

    tipos = tipos_activos()
    agrupado = {d.id: {} for d in departamentos}
    generales_por_tipo = {}

    docs = Documento.objects.filter(tipo__activo=True).exclude(tipo__isnull=True)
    if usuario is not None:
        docs = docs.filter(q_documentos_visibles(usuario))
    if solo_departamento:
        docs = docs.filter(
            departamento_id__in=[d.id for d in departamentos],
        )
    if clasificacion:
        docs = docs.filter(clasificacion=clasificacion)
    if registro == 'documentos':
        docs = docs.filter(es_plantilla=False)
    elif registro == 'plantillas':
        docs = docs.filter(es_plantilla=True)
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
    docs = docs.select_related('tipo', 'propietario')
    if not solo_departamento:
        docs = docs.prefetch_related('departamentos_afectados')
    docs = docs.order_by('fecha_vencimiento' if vigencia else '-fecha_modificacion')
    if usuario is not None:
        perfil_u = usuario.perfil
        es_admin_u = perfil_u.es_administrador
        # 1 sola query: jefaturas naturales + delegadas del usuario.
        from .jefatura import departamentos_jefados

        deptos_jefe_u = departamentos_jefados(usuario)
    for doc in docs:
        if usuario is not None:
            # Banderas de acción por documento para el card (Departamento).
            # Se calculan sobre columnas ya cargadas (departamento_id /
            # propietario_id): cero queries extra por documento.
            es_jefe_depto = bool(
                doc.departamento_id and doc.departamento_id in deptos_jefe_u
            )
            doc.puede_asignar = es_admin_u or es_jefe_depto
            doc.puede_transferir = es_admin_u or es_jefe_depto
            doc.puede_distribuir = (
                es_admin_u or es_jefe_depto or doc.propietario_id == usuario.id
            )
        if doc.afecta_todos and not solo_departamento:
            generales_por_tipo.setdefault(doc.tipo_id, []).append(doc)
            continue
        afecta = set()
        if doc.departamento_id in agrupado:
            afecta.add(doc.departamento_id)
        if not solo_departamento:
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


@login_required(login_url='/login/')
def documentos_departamento(request):
    perfil = request.user.perfil
    es_admin = perfil.es_administrador
    from .jefatura import es_delegado_vigente

    if not (perfil.es_jefe or es_admin or es_delegado_vigente(request.user)):
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('documentos:mis_documentos')

    departamento = perfil.departamento
    clasificacion_filter = request.GET.get('clasificacion', '')
    vigencia_filter = request.GET.get('vigencia', '')
    registro_filter = request.GET.get('registro', 'todos')  # todos|documentos|plantillas
    if es_admin:
        departamentos = departamentos_activos()
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
        solo_departamento=not es_admin,
        registro='' if registro_filter == 'todos' else registro_filter,
    )
    conteos_vigencia = calcular_conteos_vigencia(
        departamentos, clasificacion_filter, request.user,
        solo_departamento=not es_admin,
        registro='' if registro_filter == 'todos' else registro_filter,
    )
    return render(request, 'documentos/documentos_departamento.html', {
        'generales': generales,
        'grupos': grupos,
        'departamento': departamento,
        'es_admin': es_admin,
        'clasificacion_filter': clasificacion_filter,
        'clasificacion_choices': Documento.CLASIFICACION_CHOICES,
        'vigencia_filter': vigencia_filter,
        'registro_filter': registro_filter,
        'conteos_vigencia': conteos_vigencia,
        'solicitudes_pendientes': solicitudes_pendientes,
        'hoy': date.today(),
        'dias7': date.today() + timedelta(days=7),
    })


@login_required(login_url='/login/')
def seleccionar_tipo(request):
    """Página/catálogo para seleccionar tipo de documento a crear"""
    es_plantilla = request.GET.get('plantilla', '') in ('1', 'true', 'True')
    es_jefe = _es_jefe(request.user)
    tipos_disponibles = []
    for tipo in tipos_activos():
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
        'es_plantilla': es_plantilla,
        'sin_codigo_opciones': _opciones_sin_codigo(request.user, es_plantilla),
    })


def _opciones_sin_codigo(usuario, es_plantilla):
    """Tarjetas de "documento sin código" para el selector de tipo."""
    if es_plantilla:
        return []
    from .services import documentos_sin_codigo as svc

    opciones = []
    if svc.puede_crear(usuario, svc.AMBITO_PERSONAL):
        opciones.append({
            'ambito': svc.AMBITO_PERSONAL,
            'nombre': 'Documento personal',
            'descripcion': 'Archivo de uso personal. Solo tú lo ves; no entra '
                           'a la Lista Maestra.',
            'icono': 'user-lock',
            'color': 'info',
        })
    if svc.puede_crear(usuario, svc.AMBITO_SGC):
        opciones.append({
            'ambito': svc.AMBITO_SGC,
            'nombre': 'Documento sin código (SGC)',
            'descripcion': 'Soporte o registro de fuente institucional. Se '
                           'incluye en la Lista Maestra como Ext (N/A).',
            'icono': 'box-archive',
            'color': 'warning',
        })
    return opciones


def _contexto_preview_plantilla(request, tipo, plantilla):
    """Contexto de la preview AJAX de una plantilla (modal del formulario)."""
    from .permissions import (
        descarga_autorizada, puede_descargar, puede_ver_documento,
    )
    from .services.documentos_pdf import (
        FORMATOS_CONVERTIBLES, archivo_existe, encolar_conversion,
        pdf_disponible,
    )

    puede_ver = puede_ver_documento(
        request.user, plantilla, requerir_aprobada=True,
    )
    tiene_archivo = (
        bool(plantilla.archivo) and archivo_existe(plantilla.archivo)
    )
    pdf_listo = False
    convirtiendo = False
    if puede_ver:
        pdf_listo = pdf_disponible(plantilla)
        if (tiene_archivo and plantilla.tipo_archivo in FORMATOS_CONVERTIBLES
                and not pdf_listo):
            pdf_listo = encolar_conversion(plantilla)
            convirtiendo = not pdf_listo

    return {
        'tipo': tipo,
        'plantilla_preview': plantilla,
        'plantilla_puede_ver': puede_ver,
        'plantilla_pdf_listo': pdf_listo,
        'plantilla_convirtiendo': convirtiendo,
        'plantilla_tiene_archivo': tiene_archivo,
        'plantilla_pdf_visible': (
            pdf_listo
            or (plantilla.tipo_archivo == 'pdf' and tiene_archivo)
        ),
        'plantilla_descargable': (
            puede_descargar(request.user, plantilla)
            or descarga_autorizada(plantilla, request.user)
        ),
        'plantilla_pendiente': SolicitudDescarga.objects.filter(
            documento=plantilla, solicitado_por=request.user,
            estado='pendiente',
        ).exists(),
    }


@login_required(login_url='/login/')
def preview_plantilla(request, tipo, documento_id):
    """Partial HTML con la vista previa de una plantilla (para el modal)."""
    tipo_obj = TipoDocumento.objects.filter(key=tipo, activo=True).first()
    if not tipo_obj or (tipo_obj.solo_jefes and not _es_jefe(request.user)):
        raise Http404

    from .permissions import q_documentos_visibles

    plantilla = (
        Documento.objects.filter(
            tipo=tipo_obj, es_plantilla=True, estado='activo',
            uuid=documento_id,
        )
        .filter(q_documentos_visibles(request.user))
        .select_related('departamento')
        .first()
    )
    if plantilla is None:
        raise Http404

    return render(
        request, 'documentos/_plantilla_preview.html',
        _contexto_preview_plantilla(request, tipo, plantilla),
    )


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

    if tipo_obj.solo_jefes and not _es_jefe(request.user):
        messages.error(request, 'Solo los jefes de departamento pueden crear este tipo de documento.')
        return redirect('documentos:seleccionar_tipo')

    sin_prefijo_depto = Documento.es_sin_prefijo_depto(tipo)

    from .permissions import documentos_descargables, q_documentos_visibles

    plantillas_disponibles = Documento.objects.filter(
        tipo=tipo_obj, es_plantilla=True, estado='activo',
    ).filter(
        q_documentos_visibles(request.user)
    ).select_related('departamento', 'propietario').order_by('codigo')
    plantillas_list = list(plantillas_disponibles)
    plantillas_pendientes = set(
        SolicitudDescarga.objects.filter(
            solicitado_por=request.user, estado='pendiente',
            documento__in=plantillas_list,
        ).values_list('documento__uuid', flat=True)
    )
    plantillas_descargables = documentos_descargables(
        request.user, plantillas_list,
    )
    plantillas_meta = [
        {
            'id': str(p.uuid),
            'codigo': p.codigo,
            'nombre': p.nombre,
            'descripcion': p.descripcion or '',
            'clasificacion': p.clasificacion,
            'descarga_url': (
                reverse('documentos:descargar_archivo', args=[tipo, p.uuid])
                if p.uuid in plantillas_descargables else ''
            ),
            'solicitud_url': (
                reverse('documentos:solicitar_descarga', args=[tipo, p.uuid])
                if p.uuid not in plantillas_descargables
                and p.uuid not in plantillas_pendientes else ''
            ),
            'pendiente': p.uuid in plantillas_pendientes,
            'preview_url': reverse(
                'documentos:preview_plantilla', args=[tipo, p.uuid],
            ),
        }
        for p in plantillas_list
    ]

    formato_dinamico = Formato.objects.filter(
        activo=True, aplica_creacion=True
    ).order_by('codigo').first()

    respuestas_formato = {}

    if request.method == 'POST':
        accion = request.POST.get('accion', 'enviar')
        if not plantillas_list:
            messages.error(
                request,
                f'No hay plantillas activas de {tipo_nombre} disponibles para tu '
                f'perfil. Debes subir una plantilla antes de crear un documento '
                f'de este tipo.',
            )
        form = DocumentoForm(
            request.POST, request.FILES,
            departamento_id=perfil.departamento.id,
            tipo_documento=tipo,
            con_plantilla=True,
            usuario=request.user,
            plantillas_queryset=plantillas_disponibles,
        )
        if form.is_valid():
            secuencia = request.POST.get('secuencia', '001')
            codigo = Documento.construir_codigo(perfil.departamento, tipo, secuencia)

            respuestas_formato, errores_formato = {}, []
            if formato_dinamico:
                respuestas_formato, errores_formato = validar_respuestas(
                    formato_dinamico, request.POST, parcial=(accion == 'borrador')
                )
                respuestas_formato['descripcion'] = form.cleaned_data.get('descripcion') or ''
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
                    doc.plantilla_origen = form.cleaned_data.get('plantilla_origen')
                    try:
                        doc.clean()
                    except ValidationError as exc:
                        messages.error(request, ' '.join(exc.messages))
                        return redirect('documentos:crear_documento', tipo=tipo)
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
                    if _es_jefe(request.user):
                        messages.warning(
                            request,
                            f'{tipo_nombre} "{doc.nombre}" creado, pero no hay jefe '
                            f'Gerencia General asignado. Quedará en espera de autorización.'
                        )
                        messages.info(request, 'Se activará cuando Gerencia General lo autorice.')
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
            tipo_documento=tipo,
            con_plantilla=True,
            usuario=request.user,
            plantillas_queryset=plantillas_disponibles,
        )

    codigo_preview = Documento.generar_codigo_secuencial(perfil.departamento, tipo)

    return render(request, 'documentos/formulario.html', {
        'form': form,
        'titulo': f'Nuevo {tipo_nombre}',
        'tipo': tipo,
        'auto_departamento_id': perfil.departamento.id,
        'auto_departamento_nombre': str(perfil.departamento),
        'plantillas_meta': plantillas_meta,
        'hay_plantillas': bool(plantillas_list),
        'tipo_documento_codigo': tipo_codigo,
        'codigo_preview': codigo_preview,
        'sin_prefijo_depto': sin_prefijo_depto,
        'es_dinamico': True,
        'selector_relacionados': _selector_relacionados(),
        'formato_dinamico': formato_dinamico,
        'respuestas_formato': respuestas_formato,
    })


@login_required(login_url='/login/')
def crear_plantilla(request, tipo):
    """Alta de una PLANTILLA (documento en blanco es_plantilla=True).

    Flujo simplificado de aprobación (ver ``_crear_solicitud_plantilla``):
    jefe/delegado → auto-aprobado; sin jefe → pendiente del jefe del
    departamento; sin jefe en el depto → 'esperando' + aviso a admins.
    NO genera SolicitudCambio (SGC-FOR-001) ni usa el motor dinámico.
    """
    tipo_obj = TipoDocumento.objects.filter(key=tipo, activo=True).first()
    if not tipo_obj:
        messages.error(request, 'Tipo de documento no válido.')
        return redirect('documentos:catalogo')

    perfil = request.user.perfil
    if not perfil.departamento:
        messages.error(
            request, 'No tienes un departamento asignado. Contacta al administrador.'
        )
        return redirect('documentos:catalogo')

    if tipo_obj.solo_jefes and not _es_jefe(request.user):
        messages.error(
            request,
            'Solo los jefes de departamento pueden subir plantillas de este tipo.',
        )
        return redirect('documentos:catalogo')

    sin_prefijo_depto = Documento.es_sin_prefijo_depto(tipo)

    if request.method == 'POST':
        form = PlantillaForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                codigo = Documento.reservar_codigo_secuencial(
                    perfil.departamento, tipo, es_plantilla=True,
                )
                if codigo is None:
                    messages.error(
                        request,
                        'No se pudo asignar un código único para la plantilla. '
                        'Intenta de nuevo.',
                    )
                    return redirect('documentos:catalogo')

                doc = form.save(commit=False)
                doc.tipo = tipo_obj
                doc.propietario = request.user
                doc.departamento = perfil.departamento
                doc.es_plantilla = True
                doc.estado = 'borrador'
                doc.codigo = codigo
                doc.save()
                VersionDocumento.objects.create(
                    documento=doc, numero_version='1',
                    archivo=doc.archivo or None,
                    cambios='Versión inicial', creado_por=request.user,
                )
                sol = crear_cadena_aprobacion_documento(
                    documento=doc, usuario=request.user,
                    departamento=perfil.departamento, accion='creacion',
                    plantilla=True,
                )
                if sol.estado != 'aprobada':
                    doc.estado = 'en_revision'
                    doc.save(update_fields=['estado'])

            if sol.estado == 'aprobada':
                messages.success(
                    request,
                    f'Plantilla "{doc.nombre}" aprobada y publicada en el catálogo.',
                )
            elif sol.estado == 'esperando':
                messages.warning(
                    request,
                    f'Plantilla "{doc.nombre}" creada, pero no hay jefe de '
                    f'departamento asignado. Quedará en espera de aprobación.',
                )
                messages.info(
                    request,
                    'Los administradores fueron avisados para asignar un aprobador.',
                )
            else:
                messages.success(
                    request,
                    f'Plantilla "{doc.nombre}" enviada al jefe de departamento '
                    f'para aprobación.',
                )
            return redirect('documentos:detalle', tipo=tipo, documento_id=doc.uuid)
    else:
        form = PlantillaForm()

    codigo_preview = Documento.generar_codigo_secuencial(
        perfil.departamento, tipo, es_plantilla=True,
    )
    return render(request, 'documentos/crear_plantilla.html', {
        'form': form,
        'titulo': f'Subir Plantilla — {tipo_obj.nombre}',
        'tipo': tipo,
        'tipo_nombre': tipo_obj.nombre,
        'tipo_codigo': tipo_obj.codigo,
        'codigo_preview': codigo_preview,
        'sin_prefijo_depto': sin_prefijo_depto,
        'auto_departamento_nombre': str(perfil.departamento),
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

        solicitud_aprob = crear_cadena_aprobacion_documento(
            documento=documento, usuario=request.user,
            departamento=perfil.departamento, accion='creacion',
        )

    if solicitud_aprob and solicitud_aprob.aprobador_id:
        messages.success(request, f'"{documento.nombre}" enviado a revisión.')
    elif _es_jefe(request.user):
        messages.warning(
            request,
            f'"{documento.nombre}" enviado, pero no hay jefe de Gerencia General '
            f'asignado. Quedará en espera de autorización.'
        )
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
        form_valido = form.is_valid()
        respuestas_formato_post, errores_formato = respuestas_formato, []
        if formato_dinamico:
            respuestas_formato_post, errores_formato = validar_respuestas(
                formato_dinamico, request.POST, parcial=(accion == 'borrador')
            )
            if form_valido:
                respuestas_formato_post['descripcion'] = (
                    form.cleaned_data.get('descripcion') or ''
                )
        if form_valido:
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
    es_plantilla = request.GET.get('plantilla', '') in ('1', 'true', 'True')
    
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
    
    consecutivo = Documento.generar_codigo_secuencial(
        departamento, tipo, es_plantilla=es_plantilla,
    )
    secuencia = consecutivo.split('-')[-1]
    
    return JsonResponse({'secuencia': secuencia})


def _normalizar_version(valor):
    """Normaliza un número de versión para compararlo ('1.0' ≡ '1')."""
    try:
        return str(int(float(str(valor).strip())))
    except (TypeError, ValueError):
        return str(valor or '').strip()


def _version_vigente(versiones, documento):
    """Devuelve la ``VersionDocumento`` que corresponde a ``version_actual``."""
    objetivo = _normalizar_version(documento.version_actual)
    return next(
        (v for v in versiones if _normalizar_version(v.numero_version) == objetivo),
        None,
    )


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

    versiones = list(VersionDocumento.objects.filter(documento=documento, activa=True))
    version_vigente = _version_vigente(versiones, documento)
    # La versión vigente siempre primero; el resto, de la más reciente a la
    # más antigua (orden estable: se ordena por fecha y luego por vigencia).
    versiones.sort(key=lambda v: v.fecha_creacion, reverse=True)
    if version_vigente:
        versiones.sort(key=lambda v: v.pk != version_vigente.pk)
    from .permissions import puede_descargar
    # Todas las solicitudes de descarga de ESTE usuario para el documento, en
    # una sola consulta: de aquí se derivan la autorización vigente, la
    # solicitud pendiente, la última solicitud y su historial de aprobaciones.
    # Antes eran 4 consultas separadas a la misma tabla.
    from django.utils import timezone as _tz

    solicitudes_descarga_usuario = list(
        SolicitudDescarga.objects
        .filter(documento=documento, solicitado_por=request.user)
        .prefetch_related('aprobaciones__aprobador')
        .order_by('-fecha_solicitud')
    )
    tiene_descarga_aprobada = any(
        s.estado == 'aprobada'
        and s.version_aprobada == documento.version_actual
        and s.fecha_expiracion is not None
        and s.fecha_expiracion > _tz.now()
        for s in solicitudes_descarga_usuario
    )

    convirtiendo = False
    from .services.documentos_pdf import (
        FORMATOS_CONVERTIBLES, adquirir_candado_conversion, encolar_conversion,
        liberar_candado_conversion, pdf_disponible,
    )
    pdf_listo = pdf_disponible(documento)
    if (documento.archivo and documento.tipo_archivo in FORMATOS_CONVERTIBLES
            and not pdf_listo):
        # Conversión asíncrona: se encola y la plantilla muestra la barra de
        # progreso haciendo polling de `estado_conversion`. Si el broker no
        # responde, `encolar_conversion` degrada a la conversión en línea
        # (mismo comportamiento que antes) y devuelve el resultado.
        # El candado evita reencolar en cada apertura de la ficha mientras
        # la conversión anterior sigue en curso.
        if adquirir_candado_conversion(documento):
            pdf_listo = encolar_conversion(documento)
            if pdf_listo:
                liberar_candado_conversion(documento)
        convirtiendo = not pdf_listo

    # Todas las aprobaciones del documento en una sola consulta (con sus
    # usuarios/versión precargados): de aquí se derivan la solicitud pendiente
    # del usuario, la espera, el historial y la cadena actual. Antes se hacían
    # ~8 consultas con N+1 de auth_user.
    from .services.cambios_estado import PENDIENTES as ESTADOS_CAMBIO_PENDIENTES

    aprobaciones_documento = list(
        SolicitudAprobacionDocumento.objects.filter(documento=documento)
        .select_related('aprobador', 'solicitante', 'version_a_eliminar')
        .order_by('id')
    )

    solicitud_aprobacion = next(
        (s for s in sorted(
            (s for s in aprobaciones_documento
             if s.estado == 'pendiente' and s.aprobador_id == request.user.id),
            key=lambda s: s.fecha_solicitud, reverse=True,
        )), None,
    )

    puede_aprobar = bool(solicitud_aprobacion)

    solicitud_espera = next(
        (s for s in aprobaciones_documento
         if s.estado == 'esperando' and s.aprobador_id is None),
        None,
    )

    espera_msg = None
    if solicitud_espera:
        if solicitud_espera.rol_aprobador == 'evaluador':
            espera_msg = 'Pendiente de evaluación por jefe de departamento'
        else:
            evaluador_aprobado = any(
                s.accion_solicitud == solicitud_espera.accion_solicitud
                and s.rol_aprobador == 'evaluador'
                and s.estado == 'aprobada'
                for s in aprobaciones_documento
            )
            if evaluador_aprobado:
                espera_msg = 'Evaluado — pendiente de autorización por Gerencia General'
            else:
                # Un solo paso (solicitante jefe): no hay cadena de evaluación.
                espera_msg = 'Pendiente de autorización por Gerencia General'

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
    ).select_related(
        'usuario', 'distribuido_por', 'revocado_por',
    ).order_by('-fecha_distribucion')

    firmas_pendientes = DistribucionDocumento.objects.filter(
        documento=documento, usuario=request.user,
        estado_firma='pendiente', revocado=False,
    )

    solicitud_pendiente = any(
        s.estado == 'pendiente' for s in solicitudes_descarga_usuario
    )

    from .permissions import puede_inhabilitar
    puede_inhab, rol_inhab = puede_inhabilitar(request.user, documento)

    # SGC-FOR-004: relacionados obsoletos por desincorporar y acta en curso.
    from .models import _cargo_usuario
    from .services import for_004 as svc_for_004

    acta_desincorporacion_abierta = svc_for_004.acta_abierta(documento)
    relacionados_desincorporar = list(
        svc_for_004.pendientes_de_desincorporar(documento)
    )
    puede_gerenciar_acta = svc_for_004.puede_gerenciar(request.user, documento)
    aprobador_sugerido = svc_for_004._aprobador_por_defecto(documento, request.user)
    if relacionados_desincorporar or acta_desincorporacion_abierta:
        usuarios_firma = list(
            User.objects.filter(is_active=True).order_by(
                'first_name', 'last_name', 'username',
            )
        )
    else:
        usuarios_firma = []

    # Solicitudes de cambio de estado todavía sin resolver: si ya hay una
    # en trámite, la ficha muestra un aviso en lugar de ofrecer de nuevo
    # el botón de solicitud (el API devolvería 409 "duplicado").
    def _cambio_pendiente(accion):
        return next(
            (s for s in sorted(
                (s for s in aprobaciones_documento
                 if s.accion_solicitud == accion
                 and s.estado in ESTADOS_CAMBIO_PENDIENTES),
                key=lambda s: s.fecha_solicitud, reverse=True,
            )), None,
        )

    reactivacion_pendiente = _cambio_pendiente('reactivar')
    inhabilitacion_pendiente = _cambio_pendiente('inhabilitar')
    renovacion_pendiente = _cambio_pendiente('renovar')

    # Cadena de aprobación actual: solo la de la ACCIÓN más reciente
    # (evaluador primero, luego autorizador). Sin acotar por acción se
    # mezclaba la autorización ya aprobada de una acción anterior con la
    # pendiente de la actual, y el card mostraba filas duplicadas.
    ultima_solicitud = aprobaciones_documento[-1] if aprobaciones_documento else None
    if ultima_solicitud:
        aprobaciones_actuales = [
            s for s in aprobaciones_documento
            if s.accion_solicitud == ultima_solicitud.accion_solicitud
        ]
    else:
        aprobaciones_actuales = []

    # Historial: acción más reciente primero (fecha de respuesta, o de
    # solicitud si aún no se respondió).
    historial_estados = sorted(
        aprobaciones_documento,
        key=lambda s: (s.fecha_respuesta or s.fecha_solicitud, s.id),
        reverse=True,
    )

    renovaciones = RenovacionVencimiento.objects.filter(documento=documento)
    es_propietario = documento.propietario == request.user

    solicitudes_cambio = list(
        SolicitudCambio.objects.filter(documento=documento)
        .select_related(
            'formato', 'solicitante', 'departamento',
            'documento__tipo', 'documento__departamento__jefe',
            'documento__periodo_vigencia', 'departamento__jefe',
        )
        .order_by('-fecha_creacion')
    )
    # La planilla se muestra con los campos auto releídos de la BD: si la
    # vigencia/título/versión cambiaron después de aprobar, se ve lo actual.
    for sc in solicitudes_cambio:
        sc.respuestas_efectivas = respuestas_planilla_actual(sc)
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

    # Solo cuentan aprobaciones de solicitudes todavía en trámite: una
    # solicitud ya resuelta no debe ofrecer "Atender" de nuevo.
    from .jefatura import aprobadores_que_repr
    descargas_pendientes_aprobar = AprobacionDescarga.objects.filter(
        aprobador_id__in=aprobadores_que_repr(request.user), estado='pendiente',
        solicitud__estado='pendiente',
        solicitud__documento=documento
    ).select_related('solicitud', 'solicitud__solicitado_por')

    # Seguimiento de la última solicitud de ESTE usuario (ya precargada arriba):
    # la ficha le informa el estado, el motivo y el resultado de lo solicitado.
    ultima_solicitud_descarga = (
        solicitudes_descarga_usuario[0] if solicitudes_descarga_usuario else None
    )
    autorizacion_descarga = max(
        (s for s in solicitudes_descarga_usuario
         if s.estado == 'aprobada' and s.fecha_expiracion is not None),
        key=lambda s: s.fecha_expiracion,
        default=None,
    )

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

    # Borrador de planilla propio de este documento: permite retomarlo desde la
    # ficha en vez de crear un segundo borrador (ver crear_planilla).
    planilla_borrador = PlanillaCambios.objects.filter(
        documento=documento, estado='borrador', responsable=request.user,
    ).first()

    ia_habilitada = ai.esta_habilitado()

    # Registros del documento de contexto: si puede ver el documento, puede
    # ver su evidencia. Evita una consulta extra de visibilidad por ficha.
    registros_doc = list(
        RegistroFormato.objects.filter(
            contexto_tipo='documento', contexto_id=str(documento.uuid),
        ).select_related('formato', 'responsable')[:20]
    )
    formatos_registro = Formato.objects.filter(
        activo=True, permite_registros=True,
    ).order_by('codigo')
    puede_crear_registro = puede_editar_documento(request.user, documento)

    from .services import for_003 as svc_for_003

    cambios_for_003 = [
        svc_for_003.fila_a_dict(f)
        for f in svc_for_003.filas_historicas(documento)
    ]

    return render(request, 'documentos/detalle.html', {
        'documento': documento,
        'planilla_borrador': planilla_borrador,
        'cambios_for_003': cambios_for_003,
        'tipo': tipo,
        'codigos_relacionados': codigos_relacionados,
        'versiones': versiones,
        'version_vigente_id': version_vigente.pk if version_vigente else None,
        'puede_editar': puede_editar_documento(request.user, documento),
        'puede_eliminar': puede_eliminar_documento(request.user, documento),
        'puede_eliminar_versiones': es_admin or es_propietario,
        'versiones_eliminables': len(versiones) >= 2,
        'puede_descargar': puede_descargar(
            request.user, documento,
            descarga_autorizada_previa=tiene_descarga_aprobada,
        ),
        'puede_inhabilitar': puede_inhab,
        'puede_inhabilitar_directo': rol_inhab == 'directo',
        'reactivacion_pendiente': reactivacion_pendiente,
        'inhabilitacion_pendiente': inhabilitacion_pendiente,
        'renovacion_pendiente': renovacion_pendiente,
        'vencimiento_vencido': bool(
            documento.fecha_vencimiento and documento.fecha_vencimiento <= hoy
        ),
        'puede_renovar': es_propietario or es_admin or es_jefe_depto,
        'puede_establecer_vigencia': (es_propietario or es_admin or es_jefe_depto) and documento.estado == 'activo',
        'periodos_vigencia': periodos_activos(),
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
        'acta_desincorporacion_abierta': acta_desincorporacion_abierta,
        'relacionados_desincorporar': relacionados_desincorporar,
        'puede_gerenciar_acta': puede_gerenciar_acta,
        'aprobador_sugerido': aprobador_sugerido,
        'usuarios_firma': usuarios_firma,
        'max_participantes_acta': svc_for_004.MAX_PARTICIPANTES,
        'max_registros_acta': svc_for_004.MAX_REGISTROS_POR_HOJA,
        'participante_sugerido_cargo': _cargo_usuario(request.user),
        'documentos_llamado_por': documentos_llamado_por,
        'departamentos_afectados': departamentos_afectados,
        'responsables': responsables,
        'distribuciones': distribuciones,
        'firmas_pendientes': firmas_pendientes,
        'historial_estados': historial_estados,
        'aprobaciones_actuales': aprobaciones_actuales,
        'renovaciones': renovaciones,
        'es_propietario': es_propietario,
        'solicitud_pendiente': solicitud_pendiente,
        'ultima_solicitud_descarga': ultima_solicitud_descarga,
        'autorizacion_descarga': autorizacion_descarga,
        'hoy': hoy,
        'dias7': dias7,
        'solicitudes_por_operacion': solicitudes_por_operacion,
        'sc_permisos': solicitudes_permitidas,
        'ia_habilitada': ia_habilitada,
        'ia_usos_hoy': ai.consumo_hoy(request.user) if ia_habilitada else 0,
        'ia_limite': ai.limite_diario(),
        'registros_doc': registros_doc,
        'formatos_registro': formatos_registro,
        'puede_crear_registro': puede_crear_registro,
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
            respuestas_formato['descripcion'] = documento.descripcion or ''
            if errores_formato:
                for err in errores_formato:
                    messages.error(request, err)

        if not errores_formato:
            from .services.cambios_estado import (
                solicitar_cambio_estado, solicitud_pendiente, validar_cambio,
            )

            motivo = validar_cambio(documento, 'inhabilitar')
            if motivo:
                messages.error(request, motivo)
                return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
            if solicitud_pendiente(documento, 'inhabilitar'):
                messages.warning(request, 'Ya existe una solicitud de inhabilitación pendiente para este documento.')
                return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

            # La planilla SGC-FOR-001 es la evidencia de la disposición
            # (ISO 9001:7.5.3.2): se genera SIEMPRE que exista el formato,
            # aunque no se haya respondido ningún campo opcional.
            sol_cambio = None
            if formato_dinamico:
                sol_cambio = SolicitudCambio.objects.create(
                    codigo=f"SC-{timezone.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}",
                    formato=formato_dinamico,
                    operacion='eliminacion',
                    documento=documento,
                    solicitante=request.user,
                    departamento=documento.departamento or request.user.perfil.departamento,
                    respuestas=respuestas_formato,
                    estado='en_revision',
                )
                _autollenar_envio(sol_cambio, request.user)

            resultado = solicitar_cambio_estado(
                documento, request.user, 'inhabilitar',
                solicitud_cambio=sol_cambio,
                observaciones=f'Estado previo: {documento.estado}',
                ip_address=_ip_auditoria(request),
                user_agent=request.META.get('HTTP_USER_AGENT'),
            )
            if resultado.aplicado:
                messages.success(request, 'Documento marcado como obsoleto.')
                return redirect('documentos:mis_documentos')
            if resultado.solicitud is None:
                # Sin solicitud creada (permiso/validación/departamento):
                # no dejar huérfana la solicitud de cambio.
                if sol_cambio is not None:
                    sol_cambio.delete()
                messages.warning(request, resultado.mensaje)
            else:
                messages.success(request, resultado.mensaje)
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    from .permissions import puede_inhabilitar
    return render(request, 'documentos/confirmar_eliminar.html', {
        'documento': documento, 'tipo': tipo,
        'formato_dinamico': formato_dinamico,
        'respuestas_formato': respuestas_formato,
        'puede_inhabilitar_directo': puede_inhabilitar(request.user, documento)[1] == 'directo',
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


@login_required(login_url='/login/')
def solicitar_descarga(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    if not documento:
        raise Http404

    from .services.descargas import (
        aprobadores_descarga, bloqueo_solicitud_descarga, crear_solicitud_descarga,
    )

    pasos = aprobadores_descarga(documento)

    if request.method == 'POST':
        form = SolicitudDescargaForm(request.POST)
        if form.is_valid():
            resultado = crear_solicitud_descarga(
                documento, request.user, form.cleaned_data['motivo'],
            )
            if resultado.codigo == 'sin_departamento':
                # Sin departamento no hay jefe aprobador: se informa en la
                # propia página (200).
                messages.error(request, resultado.mensaje)
                return render(request, 'documentos/solicitar_descarga.html', {
                    'form': SolicitudDescargaForm(),
                    'documento': documento, 'tipo': tipo,
                    'pasos': pasos,
                    'solicitud_reciente': None,
                })
            if resultado.codigo != 'ok':
                messages.warning(request, resultado.mensaje)
            else:
                messages.success(request, resultado.mensaje)
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    else:
        # GET: no se vuelve a ofrecer el formulario si ya hay una solicitud
        # en trámite o una autorización vigente; se redirige a la ficha.
        bloqueo = bloqueo_solicitud_descarga(documento, request.user)
        if bloqueo and bloqueo.codigo != 'sin_departamento':
            messages.warning(request, bloqueo.mensaje)
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
        form = SolicitudDescargaForm()
    # Historial reciente del usuario en este documento (para avisar en el
    # formulario si su solicitud anterior fue rechazada o ya venció).
    solicitud_reciente = (
        SolicitudDescarga.objects
        .filter(documento=documento, solicitado_por=request.user)
        .order_by('-fecha_solicitud')
        .first()
    )
    return render(request, 'documentos/solicitar_descarga.html', {
        'form': form, 'documento': documento, 'tipo': tipo,
        'pasos': pasos,
        'solicitud_reciente': solicitud_reciente,
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
        FORMATOS_CONVERTIBLES, archivo_existe, encolar_conversion,
        limpiar_pdf_inexistente, pdf_disponible,
    )
    # `archivo_pdf` guarda una ruta en BD, no garantiza que el fichero exista:
    # si el PDF se borró del disco se limpia el puntero y se regenera.
    limpiar_pdf_inexistente(documento)
    archivo = documento.archivo_pdf if pdf_disponible(documento) else None
    if (archivo is None and documento.archivo
            and documento.tipo_archivo in FORMATOS_CONVERTIBLES):
        # El PDF aún no está listo: se encola y se vuelve al detalle (que
        # muestra la barra de progreso). Si el broker no responde, el fallback
        # síncrono lo genera aquí mismo y se sirve sin recargar.
        if encolar_conversion(documento):
            archivo = documento.archivo_pdf
        else:
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
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


@login_required(login_url='/login/')
def aprobar_descarga(request, solicitud_id):
    solicitud = get_object_or_404(
        SolicitudDescarga.objects.select_related('documento'), id=solicitud_id,
    )
    documento = solicitud.documento
    from .jefatura import aprobadores_que_repr

    aprobacion = AprobacionDescarga.objects.filter(
        solicitud=solicitud, aprobador_id__in=aprobadores_que_repr(request.user),
    ).order_by('id').first()
    if not aprobacion:
        messages.error(request, 'No tienes una aprobación asignada para esta solicitud.')
        return redirect('documentos:mis_documentos')

    if request.method == 'POST':
        from .services.descargas import resolver_aprobacion

        resultado = resolver_aprobacion(
            solicitud, request.user,
            aprobar=(request.POST.get('accion') == 'aprobar'),
            observaciones=request.POST.get('observaciones', ''),
        )
        if resultado.codigo in ('ok', 'parcial'):
            messages.success(request, resultado.mensaje)
        elif resultado.codigo == 'rechazada':
            messages.warning(request, resultado.mensaje)
        else:  # ya_resuelta / sin_pendiente / en_espera / no_existe
            messages.error(request, resultado.mensaje)
        return redirect('documentos:documentos_departamento')

    # GET: el formulario solo se ofrece mientras la solicitud siga en
    # trámite y a este usuario le quede evaluación pendiente.
    if solicitud.estado != 'pendiente':
        messages.warning(
            request,
            f'Esta solicitud ya fue {solicitud.get_estado_display().lower()}.',
        )
        return redirect('documentos:documentos_departamento')
    if aprobacion.estado == 'esperando':
        messages.warning(
            request,
            'Este paso aún no está activo: falta asignar al jefe que debe resolverlo.',
        )
        return redirect('documentos:documentos_departamento')
    if aprobacion.estado != 'pendiente':
        messages.warning(request, 'Tu evaluación ya fue registrada.')
        return redirect('documentos:documentos_departamento')
    return render(request, 'documentos/aprobar_descarga.html', {
        'solicitud': solicitud, 'documento': documento, 'aprobacion': aprobacion,
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
    versiones = list(VersionDocumento.objects.filter(documento=documento, activa=True))
    version_vigente = _version_vigente(versiones, documento)
    solicitudes_pendientes = SolicitudAprobacionDocumento.objects.filter(
        documento=documento, accion_solicitud='eliminar_version', estado='pendiente'
    ).values_list('version_a_eliminar_id', flat=True)
    # ISO: la eliminación de versión es directa y queda reservada al
    # propietario del documento y a los administradores (no al jefe del
    # departamento, que no responde por la autoría del documento).
    perfil = request.user.perfil
    puede_eliminar_versiones = perfil.es_administrador or documento.propietario == request.user
    return render(request, 'documentos/historial_versiones.html', {
        'documento': documento, 'tipo': tipo, 'versiones': versiones,
        'version_vigente_id': version_vigente.pk if version_vigente else None,
        'solicitudes_pendientes_ids': list(solicitudes_pendientes),
        'puede_eliminar_versiones': puede_eliminar_versiones,
        'versiones_eliminables': len(versiones) >= 2,
    })


@login_required(login_url='/login/')
def eliminar_version(request, tipo, documento_id, version_id):
    documento = get_documento(tipo, documento_id)
    version = get_object_or_404(VersionDocumento, id=version_id, documento=documento, activa=True)
    user = request.user
    perfil = user.perfil
    # ISO: la eliminación de versión se aplica directo (sin aprobación) y
    # solo puede iniciarla el propietario del documento o un administrador.
    es_propietario = documento.propietario == user

    if not (perfil.es_administrador or es_propietario):
        messages.error(request, 'No tienes permisos para eliminar esta versión.')
        return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)

    if documento.estado == 'en_revision':
        messages.error(request, 'No se pueden eliminar versiones mientras el documento está en revisión.')
        return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)

    if request.method != 'POST':
        return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)

    # ISO: un documento conserva siempre al menos una versión. El cómputo se
    # repite dentro de la transacción con las versiones bloqueadas para que
    # dos borrados simultáneos no dejen el documento sin historial.
    with transaction.atomic():
        activas = (
            VersionDocumento.objects
            .select_for_update()
            .filter(documento=documento, activa=True)
        )
        if activas.count() <= 1:
            messages.error(
                request, 'No se puede eliminar la única versión del documento.',
            )
            return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)

        # Solicitudes de eliminación de versión generadas antes de que este
        # flujo fuera directo: se cancelan para no dejar cadenas obsoletas
        # que podrían "restaurar" la versión tras borrarse.
        SolicitudAprobacionDocumento.objects.filter(
            documento=documento, version_a_eliminar=version,
            accion_solicitud='eliminar_version',
        ).exclude(estado__in=('aprobada', 'rechazada', 'cancelada')).update(
            estado='cancelada', fecha_respuesta=timezone.now(),
        )
        solic = SolicitudAprobacionDocumento(
            documento=documento, accion_solicitud='eliminar_version',
            version_a_eliminar=version, solicitante=user,
            propietario=documento.propietario,
            aprobador=user,
            departamento_origen=documento.departamento,
            rol_aprobador='autorizador', es_final=True,
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
                ip_address=_ip_auditoria(request),
            )
        except Exception:
            pass
        messages.success(request, f'Versión {version.numero_version} eliminada correctamente.')
        return redirect('documentos:historial_versiones', tipo=tipo, documento_id=documento.uuid)


@login_required(login_url='/login/')
def renovar_vencimiento(request, tipo, documento_id):
    """Renueva la fecha de vencimiento de un documento (ISO 9001:7.5.3.2).

    La renovación exige UNA aprobación (jefe del departamento del documento,
    o jefe de Gerencia General si el solicitante es jefe); el administrador y
    el jefe de GG la aplican directamente. Si el documento estaba obsoleto,
    al aprobarse pasa a activo (ver `services.cambios_estado`).
    """
    if request.method != 'POST':
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento_id)
    documento = get_documento(tipo, documento_id)
    if not documento:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    user = request.user
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

    from .services.cambios_estado import solicitar_cambio_estado
    estado_previo = documento.estado
    fecha_anterior = documento.fecha_vencimiento
    resultado = solicitar_cambio_estado(
        documento, user, 'renovar',
        observaciones=observacion,
        fecha_propuesta=fecha_nueva_dt,
        ip_address=_ip_auditoria(request),
        user_agent=request.META.get('HTTP_USER_AGENT'),
    )
    if resultado.aplicado:
        invalidar_planillas_documento(documento)
        _notificar_renovacion(documento, user, fecha_nueva_dt)
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
        if estado_previo == 'obsoleto':
            msg += ' El documento fue reactivado.'
        messages.success(request, msg)
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    if resultado.solicitud is None:
        messages.warning(request, resultado.mensaje)
    else:
        messages.success(request, resultado.mensaje)
    return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)


def _notificar_renovacion(documento, user, fecha_nueva_dt):
    """Avisa al propietario y al jefe del departamento de una renovación aplicada."""
    from autenticacion.models import Notificacion
    destinatarios = [documento.propietario, documento.departamento.jefe if documento.departamento else None]
    for destinatario in destinatarios:
        if not destinatario or destinatario == user:
            continue
        Notificacion.objects.create(
            usuario=destinatario,
            titulo='Vencimiento renovado',
            mensaje=(
                f'{user.get_full_name() or user.username} renovó el vencimiento de '
                f'"{documento.nombre}" ({documento.codigo}) hasta el {fecha_nueva_dt}.'
            ),
            tipo='renovacion_vencimiento',
            url_accion=f'/documentos/{documento.tipo_id}/{documento.uuid}/',
        )


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
    es_jefe_o_admin = perfil.es_administrador or es_jefe_efectivo(user, documento.departamento)
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
    # La planilla muestra la vigencia actual: el archivo guardado queda viejo.
    invalidar_planillas_documento(documento)
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
    es_jefe_o_admin = perfil.es_administrador or es_jefe_efectivo(user, documento.departamento)
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
    invalidar_planillas_documento(documento)

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
    from .jefatura import es_delegado_vigente

    if not (perfil.es_jefe or perfil.es_administrador
            or es_delegado_vigente(request.user)):
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
def transferencia_masiva(request):
    """Transferencia masiva (cobertura temporal) de un trabajador.

    Mueve de un golpe la propiedad, las asignaciones como coordinador y las
    distribuciones que recibe ``origen`` hacia ``destino`` (siempre del mismo
    departamento). Permitido a administradores y jefes de departamento.
    """
    from urllib.parse import urlencode

    from auditoria.middleware import resolver_ip_cliente
    from django.urls import reverse

    from .services.transferencia import (
        transferibles_de_usuario, transferir_documentos_usuario,
    )

    perfil = request.user.perfil
    if not (perfil.es_administrador or perfil.es_jefe):
        messages.error(request, 'No tienes permisos para transferir documentos.')
        return redirect('documentos:mis_documentos')

    # El área de configuración NO se delega: el jefe queda atado a su
    # departamento y solo un administrador puro puede elegir otro.
    es_solo_dept = bool(perfil.es_jefe and perfil.departamento_id)
    depto_elegido_id = request.POST.get('departamento') or request.GET.get('departamento')
    if es_solo_dept:
        departamentos = Departamento.objects.filter(id=perfil.departamento_id)
        depto = departamentos.first()
    else:
        departamentos = Departamento.objects.filter(activo=True)
        depto = Departamento.objects.filter(id=depto_elegido_id).first() if depto_elegido_id else None

    def _usuarios(dpto):
        if not dpto:
            return []
        return list(
            User.objects.filter(perfil__departamento=dpto)
            .select_related('perfil')
            .order_by('first_name', 'last_name', 'username')
        )

    usuarios = _usuarios(depto)

    def _volver(**extra):
        """Vuelve a la misma URL conservando el departamento elegido."""
        params = {}
        d = request.POST.get('departamento') or request.GET.get('departamento')
        if d:
            params['departamento'] = d
        params.update(extra)
        qs = urlencode(params)
        return redirect(reverse('documentos:transferencia_masiva') + (f'?{qs}' if qs else ''))

    origen_id = request.POST.get('origen') or request.GET.get('origen')
    destino_id = request.POST.get('destino') or request.GET.get('destino')

    origen = None
    propiedad, asignaciones, distribuciones = [], [], []
    if origen_id and depto:
        origen = User.objects.filter(
            id=origen_id, perfil__departamento=depto,
        ).select_related('perfil').first()
        if not origen:
            messages.error(request, 'El usuario de origen no pertenece a ese departamento.')
            return _volver()
        propiedad, asignaciones, distribuciones = transferibles_de_usuario(origen)

    candidatos = [u for u in usuarios if u.id != (origen.id if origen else None)]
    destino_sel = None
    if destino_id:
        try:
            destino_id_int = int(destino_id)
        except (TypeError, ValueError):
            destino_id_int = None
        destino_sel = next(
            (u for u in candidatos if u.id == destino_id_int), None,
        )
        destino_id = destino_id_int

    if request.method == 'POST':
        if not origen or not destino_sel:
            messages.error(request, 'Debes seleccionar el usuario de origen y el usuario destino.')
            return _volver(origen=origen.id if origen else '')

        propiedad_ids = [int(x) for x in request.POST.getlist('propiedad_ids')]
        asignacion_ids = [int(x) for x in request.POST.getlist('asignacion_ids')]
        distribucion_ids = [int(x) for x in request.POST.getlist('distribucion_ids')]

        if not (propiedad_ids or asignacion_ids or distribucion_ids):
            messages.error(request, 'Selecciona al menos un elemento a transferir.')
            return _volver(origen=origen.id)

        try:
            resumen = transferir_documentos_usuario(
                origen, destino_sel, request.user,
                propiedad_ids=propiedad_ids,
                asignacion_ids=asignacion_ids,
                distribucion_ids=distribucion_ids,
                ip_address=resolver_ip_cliente(request),
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return _volver(origen=origen.id)

        messages.success(
            request,
            f'Transferencia a {destino_sel.username} completada: '
            f'{resumen["propiedad"]} documento(s) por propiedad, '
            f'{resumen["asignaciones"]} asignación(es) y '
            f'{resumen["distribuciones"]} distribución(es).',
        )
        return _volver()

    return render(request, 'documentos/transferencia_masiva.html', {
        'departamentos': departamentos,
        'departamento': depto,
        'usuarios': usuarios,
        'origen': origen,
        'destino_id': int(destino_id) if destino_id else None,
        'candidatos': candidatos,
        'propiedad': propiedad,
        'asignaciones': asignaciones,
        'distribuciones': distribuciones,
        'total': len(propiedad) + len(asignaciones) + len(distribuciones),
        'es_solo_dept': es_solo_dept,
    })


def _delegaciones_visibles(usuario):
    """Delegaciones que ``usuario`` tiene derecho a consultar.

    El administrador ve todas; los demás solo las en las que participan
    (como jefe delegante o como delegado).
    """
    perfil = usuario.perfil
    if perfil.es_administrador:
        return DelegacionJefatura.objects.all()
    return DelegacionJefatura.objects.filter(Q(jefe=usuario) | Q(delegado=usuario))


def _miembros_delegables(departamento):
    """Miembros activos aptos para recibir una delegación de jefatura."""
    if departamento is None:
        return User.objects.none()
    return (
        User.objects.filter(
            perfil__departamento_id=departamento.id,
            is_active=True,
            # La jefatura se delega a quien aún no ejerce una (coordinador, etc.).
            perfil__rol__in=('coordinador', 'trabajador'),
        ).select_related('perfil').order_by('first_name', 'username')
    )


def _notificar_delegacion(destinatario, titulo, mensaje, departamento):
    """Notificación in-app de delegación (sin correo: decisión del usuario)."""
    try:
        from autenticacion.models import Notificacion

        Notificacion.objects.create(
            usuario=destinatario, titulo=titulo, mensaje=mensaje,
            tipo='sistema', url_accion='/documentos/delegaciones/',
            departamento=departamento,
        )
    except Exception as exc:
        logger.error("Notificación delegación: %s", exc)


def _auditar_delegacion(usuario, accion_tipo, delegacion, mensaje, anterior, posterior, detalles):
    """Auditoría ISO 15489 (fail-open) de las delegaciones de jefatura."""
    try:
        from auditoria.services import registrar_log_iso
        from auditoria.middleware import get_client_ip

        registrar_log_iso(
            usuario=usuario,
            accion_tipo=accion_tipo,
            accion_categoria='seguridad',
            recurso_tipo='delegacion_jefatura',
            recurso_id=str(delegacion.id),
            recurso_nombre=str(delegacion),
            estado_anterior=anterior,
            estado_posterior=posterior,
            resultado_exitoso=True,
            resultado_mensaje=mensaje,
            detalles=detalles,
            ip_address=get_client_ip(),
        )
    except Exception as exc:
        logger.error("Auditoría delegación de jefatura: %s", exc)


def _nombre_usuario(usuario):
    return usuario.get_full_name() or usuario.username


@login_required(login_url='/login/')
def delegaciones_jefatura(request):
    """Alta y listado de delegaciones de jefatura (vacaciones / permisos).

    El jefe natural delega su jefatura a un miembro de su mismo departamento
    dentro de un rango de vigencia (``fecha_inicio``/``fecha_fin``); pasado ese
    plazo la delegación deja de surtir efecto sola. Un delegado en curso solo
    consulta su delegación y puede renunciar. La creación queda reservada al
    jefe y al administrador: la configuración global no se delega.
    """
    perfil = request.user.perfil
    from .jefatura import es_delegado_vigente

    puede_crear = perfil.es_jefe or perfil.es_administrador
    if not (puede_crear or es_delegado_vigente(request.user)):
        messages.error(request, 'No tienes permisos para gestionar delegaciones de jefatura.')
        return redirect('documentos:mis_documentos')

    if request.method == 'POST':
        return _crear_delegacion(request, perfil, puede_crear)

    # Departamento del formulario: el jefe solo elige el suyo; el admin elige.
    if perfil.es_jefe:
        departamentos = Departamento.objects.filter(id=perfil.departamento_id)
        departamento_activo = perfil.departamento
    elif perfil.es_administrador:
        departamentos = Departamento.objects.filter(activo=True).order_by('nombre')
        elegido = request.GET.get('departamento')
        departamento_activo = (
            departamentos.filter(id=elegido).first() if elegido
            else departamentos.first()
        )
    else:
        departamentos = Departamento.objects.none()
        departamento_activo = None

    # Estado de cada delegación (el template solo lo pinta).
    hoy = timezone.now().date()
    delegaciones = []
    for d in (
        _delegaciones_visibles(request.user)
        .select_related('jefe', 'delegado', 'departamento')
        .order_by('-fecha_creacion')
    ):
        if not d.activa:
            estado, clase = 'Revocada', 'revocada'
        elif d.fecha_inicio > hoy:
            estado, clase = 'Programada', 'programada'
        elif d.fecha_fin < hoy:
            estado, clase = 'Vencida', 'vencida'
        else:
            estado, clase = 'Vigente', 'vigente'
        delegaciones.append({
            'obj': d,
            'estado': estado,
            'clase': clase,
            'en_curso': d.activa and d.fecha_inicio <= hoy <= d.fecha_fin,
            # Revocar antes del fin de la vigencia exige motivo (trazabilidad).
            'revocacion_anticipada': d.activa and hoy < d.fecha_fin,
            'puede_revocar': d.activa and (
                perfil.es_administrador
                or request.user.id in (d.jefe_id, d.delegado_id)
            ),
        })

    context = {
        'delegaciones': delegaciones,
        'departamentos': departamentos,
        'departamento_activo': departamento_activo,
        'candidatos': _miembros_delegables(departamento_activo),
        'puede_crear': puede_crear,
        'hoy': hoy,
    }
    return render(request, 'documentos/delegaciones_jefatura.html', context)


def _crear_delegacion(request, perfil, puede_crear):
    """Valida y crea una delegación de jefatura. Devuelve siempre un redirect."""
    if not puede_crear:
        messages.error(request, 'No tienes permisos para crear delegaciones.')
        return redirect('documentos:delegaciones_jefatura')

    delegado_id = request.POST.get('delegado')
    motivo = (request.POST.get('motivo') or '').strip()

    # El jefe solo delega su propio departamento; el admin elige.
    if perfil.es_jefe:
        departamento = perfil.departamento
        elegido = request.POST.get('departamento')
        if elegido and str(departamento.id if departamento else 0) != elegido:
            messages.error(request, 'Solo puedes delegar la jefatura de tu departamento.')
            return redirect('documentos:delegaciones_jefatura')
        jefe = request.user
    else:
        departamento = Departamento.objects.filter(
            id=request.POST.get('departamento')
        ).first()
        if departamento is None:
            messages.error(request, 'Selecciona un departamento.')
            return redirect('documentos:delegaciones_jefatura')
        jefe = departamento.jefe
        if jefe is None:
            messages.error(
                request,
                'El departamento no tiene jefe asignado: asígnale un jefe antes de delegar.',
            )
            return redirect('documentos:delegaciones_jefatura')

    if departamento is None:
        messages.error(request, 'Tu perfil no tiene departamento asignado.')
        return redirect('documentos:delegaciones_jefatura')

    delegado = (
        User.objects.select_related('perfil').filter(
            id=delegado_id, is_active=True
        ).first()
        if delegado_id else None
    )
    if delegado is None or delegado.id == jefe.id:
        messages.error(request, 'Selecciona a un usuario delegado válido.')
        return redirect('documentos:delegaciones_jefatura')
    if getattr(delegado.perfil, 'departamento_id', None) != departamento.id:
        messages.error(request, 'El delegado debe pertenecer al mismo departamento.')
        return redirect('documentos:delegaciones_jefatura')

    try:
        fecha_inicio = date.fromisoformat(request.POST.get('fecha_inicio') or '')
        fecha_fin = date.fromisoformat(request.POST.get('fecha_fin') or '')
    except ValueError:
        messages.error(request, 'Indica fechas de vigencia válidas (AAAA-MM-DD).')
        return redirect('documentos:delegaciones_jefatura')

    hoy = timezone.now().date()
    if fecha_fin < fecha_inicio:
        messages.error(request, 'La fecha de fin debe ser igual o posterior a la de inicio.')
        return redirect('documentos:delegaciones_jefatura')
    if fecha_fin < hoy:
        messages.error(request, 'La vigencia debe terminar hoy o más tarde.')
        return redirect('documentos:delegaciones_jefatura')

    solapada = DelegacionJefatura.objects.filter(
        delegado=delegado, departamento=departamento, activa=True,
        fecha_inicio__lte=fecha_fin, fecha_fin__gte=fecha_inicio,
    ).first()
    if solapada is not None:
        messages.error(
            request,
            f'{_nombre_usuario(delegado)} ya tiene una delegación de jefatura '
            f'vigente en {departamento.nombre} '
            f'(del {solapada.fecha_inicio:%d/%m/%Y} al {solapada.fecha_fin:%d/%m/%Y}).',
        )
        return redirect('documentos:delegaciones_jefatura')

    delegacion = DelegacionJefatura.objects.create(
        jefe=jefe, delegado=delegado, departamento=departamento,
        motivo=motivo, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
        creada_por=request.user,
    )
    detalle = (
        f'Jefe: {_nombre_usuario(jefe)}; delegado: {_nombre_usuario(delegado)}; '
        f'departamento: {departamento.nombre}; vigencia: '
        f'{fecha_inicio:%d/%m/%Y} a {fecha_fin:%d/%m/%Y}'
    )
    _notificar_delegacion(
        delegado,
        'Se te ha delegado una jefatura de departamento',
        f'{_nombre_usuario(jefe)} te delegó la jefatura de '
        f'{departamento.nombre} del {fecha_inicio:%d/%m/%Y} al {fecha_fin:%d/%m/%Y}. '
        f'Ejercerás sus poderes operativos mientras dure la vigencia.',
        departamento,
    )
    if jefe.id != request.user.id:
        _notificar_delegacion(
            jefe,
            'Se creó una delegación de tu jefatura',
            f'{_nombre_usuario(request.user)} delegó tu jefatura de '
            f'{departamento.nombre} a {_nombre_usuario(delegado)} del '
            f'{fecha_inicio:%d/%m/%Y} al {fecha_fin:%d/%m/%Y}.',
            departamento,
        )
    _auditar_delegacion(
        request.user, 'creacion', delegacion,
        f'Delegación de jefatura creada: {detalle}',
        '', 'activa',
        {'jefe_id': jefe.id, 'delegado_id': delegado.id,
         'departamento_id': departamento.id,
         'fecha_inicio': str(fecha_inicio), 'fecha_fin': str(fecha_fin),
         'motivo': motivo},
    )
    messages.success(
        request,
        f'Delegación creada: {_nombre_usuario(delegado)} ejercerá la jefatura de '
        f'{departamento.nombre} del {fecha_inicio:%d/%m/%Y} al {fecha_fin:%d/%m/%Y}.',
    )
    return redirect('documentos:delegaciones_jefatura')


@login_required(login_url='/login/')
def revocar_delegacion(request, deleg_id):
    """Revoca de inmediato una delegación (jefe delegante, delegado o admin)."""
    if request.method != 'POST':
        return redirect('documentos:delegaciones_jefatura')

    delegacion = get_object_or_404(
        DelegacionJefatura.objects.select_related('jefe', 'delegado', 'departamento'),
        id=deleg_id,
    )
    perfil = request.user.perfil
    if not (
        perfil.es_administrador
        or request.user.id in (delegacion.jefe_id, delegacion.delegado_id)
    ):
        messages.error(request, 'No tienes permisos para revocar esta delegación.')
        return redirect('documentos:delegaciones_jefatura')
    if not delegacion.activa:
        messages.info(request, 'Esta delegación ya estaba revocada.')
        return redirect('documentos:delegaciones_jefatura')

    # ISO 15489 / no repudio: revocar antes de la fecha de fin exige motivo,
    # lo haga el jefe, el delegado (renuncia) o un administrador.
    motivo = (request.POST.get('motivo_revocacion') or '').strip()
    if timezone.now().date() < delegacion.fecha_fin and len(motivo) < 10:
        messages.error(
            request,
            'Debes indicar el motivo de la revocación anticipada '
            '(mínimo 10 caracteres).',
        )
        return redirect('documentos:delegaciones_jefatura')

    delegacion.activa = False
    delegacion.fecha_revocacion = timezone.now()
    delegacion.revocada_por = request.user
    delegacion.motivo_revocacion = motivo
    delegacion.save(update_fields=[
        'activa', 'fecha_revocacion', 'revocada_por', 'motivo_revocacion',
    ])

    etiqueta = f'{delegacion.departamento.nombre} ({_nombre_usuario(delegacion.delegado)})'
    for destinatario in (delegacion.jefe, delegacion.delegado):
        if destinatario.id == request.user.id:
            continue
        _notificar_delegacion(
            destinatario,
            'Delegación de jefatura revocada',
            f'La delegación de la jefatura de {etiqueta} fue revocada por '
            f'{_nombre_usuario(request.user)}.',
            delegacion.departamento,
        )
    _auditar_delegacion(
        request.user, 'revocacion', delegacion,
        f'Delegación de jefatura revocada: {etiqueta}',
        'activa', 'revocada',
        {'jefe_id': delegacion.jefe_id, 'delegado_id': delegacion.delegado_id,
         'departamento_id': delegacion.departamento_id,
         'motivo_revocacion': delegacion.motivo_revocacion},
    )
    messages.success(request, f'Delegación revocada: {etiqueta}.')
    return redirect('documentos:delegaciones_jefatura')


@login_required(login_url='/login/')
def crear_planilla(request):
    tipo_doc = request.GET.get('tipo') or request.POST.get('tipo_documento')
    doc_uuid = request.GET.get('doc_uuid') or request.POST.get('documento_uuid')
    planilla_uuid = request.GET.get('planilla_uuid') or request.POST.get('planilla_uuid')

    # Retomar una planilla propia en borrador: el mismo formulario la edita en
    # vez de crear otra (evita borradores duplicados para el mismo documento).
    planilla_existente = None
    if planilla_uuid:
        planilla_existente = PlanillaCambios.objects.filter(
            uuid=planilla_uuid, estado='borrador', responsable=request.user,
        ).select_related('documento', 'solicitud_cambio', 'solicitud_cambio__formato').first()
        if not planilla_existente:
            messages.error(
                request,
                'No se encontró una planilla en borrador tuya con ese identificador.',
            )
            return redirect('documentos:mis_documentos')

    if request.method == 'GET' and not planilla_existente and not (tipo_doc and doc_uuid):
        messages.error(request, 'Esta funcionalidad solo está disponible desde la edición de un documento existente.')
        return redirect('documentos:mis_documentos')

    documento = None
    if planilla_existente:
        documento = planilla_existente.documento
        if not documento:
            messages.error(request, 'La planilla no tiene documento asociado.')
            return redirect('documentos:mis_documentos')
        tipo_doc = documento.tipo_id
        doc_uuid = str(documento.uuid)
    elif tipo_doc and doc_uuid:
        try:
            documento = get_documento(tipo_doc, doc_uuid)
        except Http404:
            messages.error(request, 'Documento no encontrado.')
            return redirect('documentos:mis_documentos')

    if documento:
        if documento.estado == 'obsoleto':
            messages.error(request, 'No se puede editar un documento obsoleto. Cree un nuevo documento.')
            return redirect('documentos:mis_documentos')
        if documento.estado == 'en_revision':
            messages.error(request, 'El documento está en revisión. Espere a que se apruebe o rechace.')
            return redirect('documentos:detalle', tipo=documento.tipo_id, documento_id=documento.uuid)
        # Exigir permiso de edición sobre el documento en TODOS los tipos, no solo
        # en descripción de procesos. Evita que cualquier usuario someta una planilla
        # sobre un documento ajeno y lo bloquee en estado 'en_revision'.
        if not puede_editar_documento(request.user, documento):
            messages.error(request, 'No tienes permisos para editar este documento.')
            return redirect('documentos:detalle', tipo=documento.tipo_id, documento_id=documento.uuid)

    # SGC-FOR-003 aplica a documentos controlados, no a plantillas (su
    # aprobación simplificada no autoriza la tabla de cambios).
    aplica_for_003 = bool(documento and not documento.es_plantilla)

    # Auto-reanudar el borrador propio del documento: evita duplicados y
    # pérdida de lo ya escrito (FOR-005 y filas FOR-003) al entrar de nuevo.
    if (request.method == 'GET' and documento is not None
            and planilla_existente is None and not planilla_uuid):
        borrador = PlanillaCambios.objects.filter(
            documento=documento, estado='borrador', responsable=request.user,
        ).first()
        if borrador is not None:
            from urllib.parse import urlencode

            consulta = urlencode({
                'tipo': tipo_doc,
                'doc_uuid': documento.uuid,
                'planilla_uuid': borrador.uuid,
            })
            return redirect(
                f"{reverse('documentos:crear_planilla')}?{consulta}"
            )

    respuestas_formato = {}

    if request.method == 'POST':
        accion = request.POST.get('accion', 'enviar')
        form = PlanillaCambiosForm(
            request.POST, request.FILES,
            tipo_documento=tipo_doc, documento=documento,
            instance=planilla_existente,
        )
        permitir_asociados = 'documentos_asociados_json' in form.fields

        # Al retomar un borrador se valida contra el formato de su solicitud de
        # cambio original, no contra el formato activo del momento.
        if planilla_existente and planilla_existente.solicitud_cambio_id:
            formato_dinamico = planilla_existente.solicitud_cambio.formato
        else:
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

        from .services import for_003 as svc_for_003

        try:
            cambios_crudos = json.loads(
                request.POST.get('cambios_for_003') or '[]'
            )
        except (TypeError, ValueError):
            cambios_crudos = []
        if aplica_for_003:
            cambios_for_003, errores_for_003 = svc_for_003.parsear_filas(
                cambios_crudos, parcial=(accion == 'borrador'),
            )
        else:
            cambios_for_003, errores_for_003 = [], []
        for err in errores_for_003:
            messages.error(request, err)

        if form.is_valid() and not errores_formato and not errores_for_003:
            if formato_dinamico:
                respuestas_formato['descripcion_cambio'] = (
                    form.cleaned_data.get('descripcion_propuesta') or ''
                )
            planilla = form.save(commit=False)
            nueva_justificacion = (
                respuestas_formato.get('descripcion_cambio')
                or respuestas_formato.get('proposito_cambio')
                or ''
            )
            if nueva_justificacion or not planilla_existente:
                # Sin respuestas nuevas (edición) se conserva la justificación previa.
                planilla.justificacion = nueva_justificacion
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
                if not planilla_existente:
                    planilla.codigo = f"PC-{timezone.now().strftime('%Y%m%d-%H%M%S')}"

            if formato_dinamico:
                sol_cambio = planilla.solicitud_cambio
                if sol_cambio:
                    # Reutiliza la solicitud de cambio del borrador: solo se
                    # actualizan sus respuestas (no se genera un SC- nuevo).
                    sol_cambio.respuestas = respuestas_formato
                    sol_cambio.estado = 'borrador' if accion == 'borrador' else 'en_revision'
                    sol_cambio.save(update_fields=['respuestas', 'estado'])
                else:
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
                if aplica_for_003:
                    with transaction.atomic():
                        svc_for_003.guardar_filas(planilla, cambios_for_003)
                form.save_m2m()
                _procesar_asociados_planilla(
                    planilla, request, permitir_asociados, documento, request.user
                )
                messages.success(request, f'Planilla {planilla.codigo} guardada como borrador.')
                return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
            else:
                planilla.save()
                if aplica_for_003:
                    with transaction.atomic():
                        svc_for_003.guardar_filas(planilla, cambios_for_003)
                if planilla.solicitud_cambio:
                    _autollenar_envio(planilla.solicitud_cambio, request.user)
                form.save_m2m()
                if planilla.archivo_propuesto:
                    # Conversión asíncrona de la vista previa (no bloquea el
                    # envío de la planilla). Si el broker no responde, fallback
                    # en línea.
                    from .tasks import convertir_planilla_a_pdf, convertir_a_pdf_sync
                    try:
                        convertir_planilla_a_pdf.delay(planilla.id)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "No se pudo encolar la conversión de la planilla %s: %s",
                            planilla.codigo, exc,
                        )
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
                    ap = crear_cadena_aprobacion_planilla(
                        planilla=planilla, usuario=request.user, documento=documento,
                    )
                    if ap.estado == 'aprobada':
                        messages.success(
                            request,
                            f'Planilla {planilla.codigo} aprobada y cambios '
                            f'aplicados (flujo simplificado de plantillas).',
                        )
                    else:
                        messages.success(
                            request,
                            f'Planilla {planilla.codigo} enviada a revisión.',
                        )
                    return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
                else:
                    messages.success(request, f'Planilla {planilla.codigo} creada correctamente.')
                    return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
        else:
            messages.warning(request, 'Por favor corrija los errores en el formulario.')
    else:
        if planilla_existente:
            # Retomar borrador: el ModelForm precarga desde la instancia.
            form = PlanillaCambiosForm(
                instance=planilla_existente,
                tipo_documento=tipo_doc, documento=documento,
            )
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
    departamentos_disponibles = departamentos_activos()
    if documento:
        proxima_version = calcular_proxima_version(documento)
        version_actual = documento.version_actual

    if planilla_existente:
        # Los tags del formulario se precargan desde la planilla, no del documento.
        departamentos_seleccionados = [
            str(d.pk) for d in planilla_existente.departamentos_afectados.all()
        ]
        nombres_depto = {d.pk: d.nombre for d in departamentos_disponibles}
        responsables_seleccionados = [
            (d.pk, nombres_depto.get(d.pk, str(d.pk)))
            for d in planilla_existente.responsables_propuestos.all()
        ]
        documentos_asociados_iniciales = [
            {
                'uuid': str(a.documento.uuid), 'codigo': a.documento.codigo,
                'nombre': a.documento.nombre, 'estado': a.documento.estado,
                'tipo': a.documento.tipo_id,
            }
            for a in planilla_existente.documentos_asociados.select_related('documento').all()
            if a.documento_id
        ]
        documentos_asociados_iniciales += [
            {'codigo': codigo, 'pendiente': True}
            for codigo in (planilla_existente.referencias_pendientes or [])
        ]
    elif documento:
        departamentos_seleccionados = [str(d.pk) for d in documento.departamentos_afectados.all()]
        responsables_seleccionados = [(str(d.pk), d.nombre) for d in documento.responsables.all()]
        documentos_asociados_iniciales = [
            {
                'uuid': str(d.uuid), 'codigo': d.codigo,
                'nombre': d.nombre, 'estado': d.estado, 'tipo': d.tipo_id
            }
            for d in documento.documentos_relacionados.all()
        ]

    if request.method == 'GET':
        # En POST estos dos valores vienen de la validación de arriba.
        formato_dinamico = Formato.objects.filter(
            activo=True, aplica_modificacion=True
        ).order_by('codigo').first()
        if planilla_existente and planilla_existente.solicitud_cambio_id:
            sol = planilla_existente.solicitud_cambio
            formato_dinamico = sol.formato or formato_dinamico
            respuestas_formato = sol.respuestas or {}
    else:
        formato_dinamico = Formato.objects.filter(
            activo=True, aplica_modificacion=True
        ).order_by('codigo').first()
        if planilla_existente and planilla_existente.solicitud_cambio_id:
            formato_dinamico = (
                planilla_existente.solicitud_cambio.formato or formato_dinamico
            )

    from .services import for_003 as svc_for_003

    if request.method == 'POST' and 'cambios_for_003' in locals():
        for_003_borrador = list(cambios_for_003)
        for_003_existente = True
    elif planilla_existente is not None:
        for_003_borrador = [
            svc_for_003.fila_a_dict(f)
            for f in svc_for_003.filas_de_planilla(planilla_existente)
        ]
        for_003_existente = True
    else:
        for_003_borrador = []
        for_003_existente = False

    for_003_historico = (
        [svc_for_003.fila_a_dict(f) for f in svc_for_003.filas_historicas(documento)]
        if documento else []
    )
    for_003_auto = {
        'version': proxima_version,
        'fecha': timezone.localdate().strftime('%d/%m/%Y'),
        'realizado_por': (
            request.user.get_full_name() or request.user.username
        ),
        'descripcion': '',
        'numero_pagina': '',
        'autorizado_por': '',
    }
    for_003_editable = aplica_for_003 and (
        planilla_existente is None or planilla_existente.estado == 'borrador'
    )

    return render(request, 'documentos/formulario_planilla.html', {
        'form': form,
        'planilla_existente': planilla_existente,
        'for_003_borrador': for_003_borrador,
        'for_003_historico': for_003_historico,
        'for_003_auto': for_003_auto,
        'for_003_existente': for_003_existente,
        'for_003_editable': for_003_editable,
        'titulo': (
            f'Retomar planilla {planilla_existente.codigo}'
            if planilla_existente else
            ('Nueva Planilla de Cambios' if not documento else f'Editar {documento.nombre}')
        ),
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
    from .jefatura import aprobadores_que_repr

    mi_aprobacion_pendiente = aprobaciones.filter(
        aprobador_id__in=aprobadores_que_repr(request.user), estado='pendiente',
    ).first()
    documento = planilla.documento if planilla.documento_id else None
    departamentos_afectados = planilla.departamentos_afectados.all()
    documentos_relacionados = documento.documentos_relacionados.all() if documento else []
    solicitud_cambio = planilla.solicitud_cambio
    solicitud_permitida = _puede_ver_solicitud(request.user, solicitud_cambio) if solicitud_cambio else False
    # Respuestas con los campos auto releídos de la BD (vigencia, versión, …).
    respuestas_actual = respuestas_planilla_actual(solicitud_cambio) if solicitud_cambio else None

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

    from .services import for_003 as svc_for_003

    cambios_for_003 = [
        svc_for_003.fila_a_dict(f)
        for f in svc_for_003.filas_de_planilla(planilla)
    ]

    return render(request, 'documentos/detalle_planilla.html', {
        'planilla': planilla, 'aprobaciones': aprobaciones,
        'cambios_for_003': cambios_for_003,
        'documento': documento, 'departamentos_afectados': departamentos_afectados,
        'documentos_relacionados': documentos_relacionados,
        'solicitud_cambio': solicitud_cambio,
        'respuestas_actual': respuestas_actual,
        'solicitud_permitida': solicitud_permitida,
        'espera_msg': espera_msg,
        'mi_aprobacion_pendiente': mi_aprobacion_pendiente,
    })


@login_required(login_url='/login/')
def planilla_for_003(request, planilla_uuid, formato):
    """Descarga (generando si hace falta) el FOR-003 de una planilla aprobada."""
    from .permissions import puede_ver_documento
    from .services import for_003 as svc_for_003

    planilla = get_object_or_404(PlanillaCambios, uuid=planilla_uuid)
    documento = planilla.documento
    if documento is None or not puede_ver_documento(request.user, documento):
        raise Http404
    if formato not in ('xlsx', 'pdf'):
        raise Http404
    if planilla.tipo_cambio != 'modificacion':
        messages.error(request, 'El FOR-003 solo aplica a modificaciones.')
        return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
    if planilla.estado != 'aprobado':
        messages.warning(request, 'El FOR-003 se genera al aprobar la planilla.')
        return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)

    resultado = svc_for_003.generar_y_guardar(planilla)
    if not resultado.get('success'):
        messages.error(request, resultado.get('error', 'No se pudo generar el FOR-003.'))
        return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)

    if formato == 'xlsx':
        nombre = svc_for_003.nombre_archivo(planilla, 'xlsx')
        return FileResponse(
            planilla.for_003_xlsx.open('rb'), as_attachment=True,
            filename=nombre,
        )
    if not planilla.for_003_pdf:
        svc_for_003.generar_y_guardar(planilla, forzar=True)
    if not planilla.for_003_pdf:
        messages.error(request, 'No se pudo generar el PDF del FOR-003 (LibreOffice).')
        return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
    nombre = svc_for_003.nombre_archivo(planilla, 'pdf')
    return FileResponse(
        planilla.for_003_pdf.open('rb'), as_attachment=True, filename=nombre,
    )


@login_required(login_url='/login/')
def documento_for_003(request, tipo, documento_id, formato):
    """Descarga la Tabla de cambios acumulada (FOR-003) de un documento."""
    from .permissions import puede_ver_documento
    from .services import for_003 as svc_for_003

    documento = get_documento(tipo, documento_id)
    if documento is None or not puede_ver_documento(request.user, documento):
        raise Http404
    if formato not in ('xlsx', 'pdf'):
        raise Http404

    filas = list(svc_for_003.filas_historicas(documento))
    formato_obj = svc_for_003.formato_for_003()
    if not filas or formato_obj is None:
        messages.error(
            request,
            'El documento no tiene cambios autorizados para exportar.',
        )
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    try:
        if formato == 'xlsx':
            datos = svc_for_003.generar_xlsx(formato_obj, filas)
        else:
            datos = svc_for_003.generar_pdf(formato_obj, filas)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if not datos:
        messages.error(request, 'No se pudo generar el PDF del FOR-003.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    nombre = f"SGC-FOR-003_{documento.codigo.replace(' ', '_')}.{formato}"
    return FileResponse(
        io.BytesIO(datos), as_attachment=True, filename=nombre,
    )


# ---------------------------------------------------------------------------
# SGC-FOR-004 — Acta de desincorporación de archivos inactivos
# ---------------------------------------------------------------------------

def _acta_o_404(tipo, documento_id, acta_uuid):
    """Busca el acta dentro del documento padre (404 si no coincide)."""
    from .models import ActaDesincorporacion

    documento = get_documento(tipo, documento_id)
    if documento is None:
        raise Http404
    acta = get_object_or_404(
        ActaDesincorporacion, uuid=acta_uuid, padre=documento,
    )
    return documento, acta


def _puede_ver_acta(usuario, documento, acta):
    """Ver el acta: acceso al padre o ser firmante asignado de esta acta."""
    from .permissions import puede_ver_documento

    if puede_ver_documento(usuario, documento):
        return True
    return bool(usuario and usuario.id in (
        acta.elaborado_por_id, acta.aprobado_por_id,
        acta.creado_por_id,
    ))


@login_required(login_url='/login/')
def acta_desincorporacion(request, tipo, documento_id, acta_uuid):
    """Ficha del acta SGC-FOR-004: estados, firmas y descargas."""
    from .models import FirmaCredencial
    from .services import for_004 as svc_for_004

    documento, acta = _acta_o_404(tipo, documento_id, acta_uuid)
    if not _puede_ver_acta(request.user, documento, acta):
        raise Http404

    firmas = FirmaElectronica.para(acta).order_by('-creado')
    invitaciones = []
    for firma in firmas:
        invitaciones.extend(firma.invitaciones.order_by('-creado'))

    return render(request, 'documentos/acta_desincorporacion.html', {
        'acta': acta,
        'documento': documento,
        'tipo': tipo,
        'participantes': acta.participantes.all(),
        'registros': acta.registros.order_by('orden', 'id'),
        'firmas': firmas,
        'invitaciones': invitaciones,
        'rol_actual': acta.rol_para(request.user),
        'puede_gerenciar': svc_for_004.puede_gerenciar(request.user, documento),
        'credencial': FirmaCredencial.objects.filter(
            usuario=request.user,
        ).first(),
        'pendientes': list(
            svc_for_004.pendientes_de_desincorporar(documento)
        ),
        'estados_abiertos': ('borrador', 'pendiente_firma'),
    })


@login_required(login_url='/login/')
@require_POST
def crear_acta_desincorporacion(request, tipo, documento_id):
    """Crea el acta SGC-FOR-004 desde el modal de la ficha del padre."""
    from autenticacion.models import Notificacion
    from django.db import IntegrityError

    from .models import ActaDesincorporacion
    from .permissions import puede_ver_documento
    from .services import for_004 as svc_for_004

    documento = get_documento(tipo, documento_id)
    if documento is None or not puede_ver_documento(request.user, documento):
        raise Http404
    if not svc_for_004.puede_gerenciar(request.user, documento):
        messages.error(
            request, 'No tienes permisos para generar el acta de desincorporación.',
        )
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    participantes = []
    for i in range(svc_for_004.MAX_PARTICIPANTES):
        nombre = (request.POST.get(f'participante_nombre_{i}') or '').strip()
        cargo = (request.POST.get(f'participante_cargo_{i}') or '').strip()
        if nombre:
            participantes.append({'nombre': nombre, 'cargo': cargo})

    registro_ids = []
    for valor in request.POST.getlist('registros'):
        try:
            registro_ids.append(int(valor))
        except (TypeError, ValueError):
            continue

    fecha = None
    fecha_cruda = (request.POST.get('fecha') or '').strip()
    if fecha_cruda:
        try:
            fecha = date.fromisoformat(fecha_cruda)
        except ValueError:
            messages.error(request, 'La fecha del acta no es válida.')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    aprobado_por = None
    aprobador_id = request.POST.get('aprobado_por')
    if aprobador_id:
        aprobado_por = User.objects.filter(
            pk=aprobador_id, is_active=True,
        ).first()
        if aprobado_por is None:
            messages.error(request, 'El firmante aprobador no es válido.')
            return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    try:
        acta = svc_for_004.crear_acta(
            padre=documento,
            participantes=participantes,
            registro_ids=registro_ids,
            creado_por=request.user,
            fecha=fecha,
            aprobado_por=aprobado_por,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    except IntegrityError:
        messages.warning(
            request,
            'Ya existe un acta en curso para este documento; se reanuda esa.',
        )
        abierta = svc_for_004.acta_abierta(documento)
        if abierta:
            return redirect(
                'documentos:acta_desincorporacion',
                tipo=tipo, documento_id=documento.uuid, acta_uuid=abierta.uuid,
            )
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    _registrar_auditoria(
        request, accion='acta_desincorporacion_creada',
        recurso_tipo='acta_desincorporacion', recurso_id=acta.uuid,
        recurso_codigo=acta.codigo, recurso_nombre=documento.nombre,
        detalles={'registros': acta.registros.count()},
    )

    resultado = svc_for_004.generar_y_guardar(acta)
    if not resultado.get('success'):
        logger.warning('FOR-004 no generado al crear %s: %s',
                       acta.codigo, resultado.get('error'))

    # Aviso al aprobador (si es otra persona) de que hay un acta por firmar.
    if acta.aprobado_por_id and acta.aprobado_por_id != request.user.id:
        Notificacion.objects.create(
            usuario=acta.aprobado_por,
            titulo='Acta de desincorporación por aprobar',
            mensaje=(
                f'{request.user.get_full_name() or request.user.username} creó '
                f'el acta {acta.codigo} (SGC-FOR-004) de '
                f'"{documento.nombre}" ({documento.codigo}) y requiere tu firma '
                f'como Aprobado.'
            ),
            tipo='desincorporacion_pendiente',
            url_accion=acta.url_descarga,
            documento=documento,
            departamento=documento.departamento,
        )

    messages.success(
        request,
        f'Acta {acta.codigo} creada con {acta.registros.count()} registro(s). '
        f'Faltan las firmas de Elaborado y Aprobado.',
    )
    return redirect(
        'documentos:acta_desincorporacion',
        tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
    )


@login_required(login_url='/login/')
@require_POST
def firmar_acta_desincorporacion(request, tipo, documento_id, acta_uuid):
    """Firma interna (PIN) de un bloque del acta."""
    documento, acta = _acta_o_404(tipo, documento_id, acta_uuid)

    rol = (request.POST.get('rol') or '').strip()
    if rol not in ('elaborado', 'aprobado'):
        messages.error(request, 'Rol de firma no reconocido.')
        return redirect(
            'documentos:acta_desincorporacion',
            tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
        )
    if acta.rol_para(request.user) != rol:
        messages.error(request, 'No tienes ese rol pendiente de firma.')
        return redirect(
            'documentos:acta_desincorporacion',
            tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
        )

    try:
        firmar(
            acta,
            usuario=request.user,
            pin=request.POST.get('pin', ''),
            request=request,
            metodo='pin',
            datos={'rol': rol},
            crear=True,
        )
        messages.success(request, 'Firma electrónica registrada.')
    except ErrorFirma as exc:
        messages.error(request, str(exc))
    return redirect(
        'documentos:acta_desincorporacion',
        tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
    )


@login_required(login_url='/login/')
@require_POST
def invitar_acta_desincorporacion(request, tipo, documento_id, acta_uuid):
    """Envía por correo el enlace de firma (token, 7 días) a un rol del acta."""
    from .services import for_004 as svc_for_004

    documento, acta = _acta_o_404(tipo, documento_id, acta_uuid)
    if not svc_for_004.puede_gerenciar(request.user, documento):
        messages.error(request, 'No tienes permisos para gestionar este acta.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    rol = (request.POST.get('rol') or '').strip()
    if rol not in ('elaborado', 'aprobado'):
        messages.error(request, 'Rol de firma no reconocido.')
        return redirect(
            'documentos:acta_desincorporacion',
            tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
        )

    try:
        destinatario = svc_for_004.enviar_invitacion_rol(acta, rol, request)
        messages.success(
            request,
            f'Enlace de firma enviado a {destinatario} (válido por '
            f'{VIGENCIA_INVITACION_DIAS} días, un solo uso).',
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect(
        'documentos:acta_desincorporacion',
        tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
    )


@login_required(login_url='/login/')
@require_POST
def anular_acta_desincorporacion(request, tipo, documento_id, acta_uuid):
    """Anula un acta abierto (borrador o pendiente de firma)."""
    from .services import for_004 as svc_for_004

    documento, acta = _acta_o_404(tipo, documento_id, acta_uuid)
    if not svc_for_004.puede_gerenciar(request.user, documento):
        messages.error(request, 'No tienes permisos para gestionar este acta.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if not acta.esta_abierta:
        messages.warning(request, 'Este acta ya fue cerrado.')
        return redirect(
            'documentos:acta_desincorporacion',
            tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
        )

    motivo = (request.POST.get('motivo') or '').strip()
    if not motivo:
        messages.error(request, 'Debes indicar el motivo de la anulación.')
        return redirect(
            'documentos:acta_desincorporacion',
            tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
        )
    from .models import ActaDesincorporacion

    with transaction.atomic():
        bloqueada = ActaDesincorporacion.objects.select_for_update().get(pk=acta.pk)
        if not bloqueada.esta_abierta:
            messages.warning(request, 'Este acta ya fue cerrado.')
        else:
            bloqueada.estado = 'anulada'
            bloqueada.anulada_en = timezone.now()
            bloqueada.anulada_motivo = motivo
            bloqueada.save(update_fields=[
                'estado', 'anulada_en', 'anulada_motivo', 'actualizado',
            ])
            _registrar_auditoria(
                request, accion='acta_desincorporacion_anulada',
                recurso_tipo='acta_desincorporacion', recurso_id=bloqueada.uuid,
                recurso_codigo=bloqueada.codigo,
                recurso_nombre=bloqueada.documento_nombre,
                detalles={'motivo': motivo},
            )
            messages.success(
                request,
                f'Acta {bloqueada.codigo} anulada. Puedes generar una nueva '
                f'desde la ficha del documento.',
            )
    return redirect(
        'documentos:acta_desincorporacion',
        tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
    )


@login_required(login_url='/login/')
def descarga_acta_desincorporacion(request, tipo, documento_id, acta_uuid,
                                   formato):
    """Descarga (generando si hace falta) el XLSX/PDF del acta."""
    from .services import for_004 as svc_for_004

    documento, acta = _acta_o_404(tipo, documento_id, acta_uuid)
    if not _puede_ver_acta(request.user, documento, acta):
        raise Http404
    if formato not in ('xlsx', 'pdf'):
        raise Http404

    resultado = svc_for_004.generar_y_guardar(acta)
    if not resultado.get('success'):
        messages.error(
            request,
            resultado.get('error') or 'No se pudo generar el SGC-FOR-004.',
        )
        return redirect(
            'documentos:acta_desincorporacion',
            tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
        )

    if formato == 'xlsx':
        return FileResponse(
            acta.xlsx.open('rb'), as_attachment=True,
            filename=svc_for_004.nombre_archivo(acta, 'xlsx'),
        )
    if not acta.pdf:
        svc_for_004.generar_y_guardar(acta, forzar=True)
    if not acta.pdf:
        messages.error(
            request,
            'No se pudo generar el PDF del SGC-FOR-004 (LibreOffice).',
        )
        return redirect(
            'documentos:acta_desincorporacion',
            tipo=tipo, documento_id=documento.uuid, acta_uuid=acta.uuid,
        )
    return FileResponse(
        acta.pdf.open('rb'), as_attachment=True,
        filename=svc_for_004.nombre_archivo(acta, 'pdf'),
    )


@login_required(login_url='/login/')
@transaction.atomic
def aprobar_planilla(request, planilla_uuid):
    planilla = get_object_or_404(PlanillaCambios, uuid=planilla_uuid)
    perfil = request.user.perfil
    from .jefatura import aprobadores_que_repr, es_delegado_vigente

    if not (perfil.es_jefe or es_delegado_vigente(request.user)
            or perfil.es_administrador):
        messages.error(request, 'No tienes permisos para aprobar planillas.')
        return redirect('documentos:mis_documentos')
    if request.method == 'POST':
        if planilla.estado != 'en_revision':
            messages.warning(request, 'Esta planilla ya fue procesada.')
            return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
        accion = request.POST.get('accion')
        observaciones = request.POST.get('observaciones', '')
        aprobacion = AprobacionPlanilla.objects.filter(
            planilla=planilla, aprobador_id__in=aprobadores_que_repr(request.user),
            estado='pendiente',
        ).first()
        if not aprobacion and perfil.es_administrador:
            # Planilla de plantilla sin jefe: el admin la toma de 'esperando'.
            doc_plantilla = planilla.documento
            if doc_plantilla and doc_plantilla.es_plantilla:
                aprobacion = (
                    AprobacionPlanilla.objects.select_for_update()
                    .filter(
                        planilla=planilla, aprobador__isnull=True,
                        estado='esperando',
                    )
                    .first()
                )
                if aprobacion:
                    tomar_paso_esperando(aprobacion, request.user)
        if not aprobacion:
            messages.error(request, 'No tienes una aprobación pendiente para esta planilla.')
            return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)
        pin = request.POST.get('pin', '')
        es_plantilla = bool(getattr(planilla.documento, 'es_plantilla', False))
        try:
            if accion == 'aprobar':
                if es_plantilla:
                    # Plantillas: sin firma SES ni PIN (§31.6).
                    resultado = resolver_aprobacion_planilla(
                        aprobacion, request.user, aprobada=True,
                        observaciones=observaciones, firma=None,
                    )
                else:
                    firma = firmar(
                        aprobacion, usuario=request.user, pin=pin,
                        request=request, metodo='pin', crear=True,
                        datos={'aprobada': True, 'observaciones': observaciones},
                        contexto={'request': request},
                    )
                    resultado = getattr(firma, 'resultado', {}) or {}
                if resultado.get('es_final'):
                    notificar_planilla_resuelta(planilla, True, observaciones)
                    messages.success(
                        request,
                        'Planilla aprobada y cambios aplicados al documento.',
                    )
                else:
                    messages.success(
                        request,
                        'Evaluación registrada. Pendiente de autorización final.',
                    )
            else:
                # El rechazo no se firma: no pide PIN ni imprime bloques.
                resolver_aprobacion_planilla(
                    aprobacion, request.user, aprobada=False,
                    observaciones=observaciones,
                )
                notificar_planilla_resuelta(planilla, False, observaciones)
                messages.warning(request, 'Planilla rechazada.')
        except ErrorFirma as exc:
            messages.error(request, str(exc))
            return redirect(
                'documentos:aprobar_planilla', planilla_uuid=planilla.uuid,
            )
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
    from .services import firmas as _firmas

    return render(request, 'documentos/aprobar_planilla.html', {
        'planilla': planilla, 'documento': documento,
        'archivo_preview': archivo_preview, 'es_pdf': es_pdf,
        'credencial': _firmas.obtener_credencial(request.user),
        'es_plantilla': bool(getattr(documento, 'es_plantilla', False)),
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

        # Un borrador pudo guardarse sin filas FOR-003 (parcial=True). Al
        # enviarlo a revisión se exige la tabla completa para no saltarse el
        # requisito del SGC-FOR-003 (solo documentos, no plantillas).
        if planilla.tipo_cambio == 'modificacion' and not documento.es_plantilla:
            from .services import for_003 as svc_for_003

            filas_guardadas = [
                {'numero_pagina': f.numero_pagina, 'descripcion': f.descripcion}
                for f in svc_for_003.filas_de_planilla(planilla)
            ]
            _, errores_003 = svc_for_003.parsear_filas(
                filas_guardadas, parcial=False,
            )
            if errores_003:
                for err in errores_003:
                    messages.error(request, err)
                return redirect(
                    'documentos:detalle_planilla', planilla_uuid=planilla.uuid,
                )

        planilla.estado = 'en_revision'
        planilla.save(update_fields=['estado'])
        if planilla.solicitud_cambio:
            planilla.solicitud_cambio.estado = 'en_revision'
            planilla.solicitud_cambio.save(update_fields=['estado'])
            _autollenar_envio(planilla.solicitud_cambio, request.user)
        documento.estado = 'en_revision'
        documento.save(update_fields=['estado'])

        ap = crear_cadena_aprobacion_planilla(
            planilla=planilla, usuario=request.user, documento=documento,
        )
        if ap.estado == 'aprobada':
            messages.success(
                request,
                f'Planilla {planilla.codigo} aprobada y cambios aplicados '
                f'(flujo simplificado de plantillas).',
            )
            return redirect('documentos:detalle_planilla', planilla_uuid=planilla.uuid)

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
    gg_depto = Departamento.objects.filter(codigo='GG').first()
    return render(request, 'documentos/configuracion/roles_permisos.html', {
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



#: Opciones de los filtros multi-selección de auditoría.
ACCIONES_AUDITORIA = [
    ('login', 'Login'),
    ('logout', 'Logout'),
    ('creacion', 'Creación'),
    ('edicion', 'Edición'),
    ('eliminacion', 'Eliminación'),
    ('descarga', 'Descarga'),
]
CATEGORIAS_AUDITORIA = [
    ('documento', 'Documento'),
    ('distribucion', 'Distribución'),
    ('seguridad', 'Seguridad'),
    ('automatizacion', 'Automatización'),
    ('configuracion', 'Configuración'),
    ('vencimiento', 'Vencimiento'),
    ('sistema', 'Sistema'),
    ('inteligencia_artificial', 'IA'),
]


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
    accion_tipo = request.GET.getlist('accion_tipo')
    accion_categoria = request.GET.getlist('accion_categoria')
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
        'usuario_filtro': usuario or '', 'accion_tipo': accion_tipo,
        'accion_categoria': accion_categoria,
        'acciones_disponibles': ACCIONES_AUDITORIA,
        'categorias_disponibles': CATEGORIAS_AUDITORIA,
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
    from io import BytesIO
    from hashlib import sha256
    from django.core.files.base import ContentFile
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
    filtro_accion = request.GET.getlist('accion_tipo')
    filtro_categoria = request.GET.getlist('accion_categoria')
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
        accion_tipo=filtro_accion or None,
        accion_categoria=filtro_categoria or None,
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
    ahora = timezone.now()
    doc_code = (
        f"SGC-RPT-AUD-{ahora.strftime('%Y%m%d-%H%M%S')}"
        f"-{uuid.uuid4().hex[:6].upper()}"
    )
    now_str = ahora.strftime('%d/%m/%Y %H:%M')
    now_date = ahora.strftime('%d/%m/%Y')
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
        filtros_texto.append('Acción: ' + ', '.join(filtro_accion))
    if filtro_categoria:
        filtros_texto.append('Categoría: ' + ', '.join(filtro_categoria))
    filtros_str = ', '.join(filtros_texto) if filtros_texto else 'Todos'
    filtros_dict = {
        'tipo': tipo_reporte,
        'usuario': filtro_usuario or None,
        'accion_tipo': ', '.join(filtro_accion) or None,
        'accion_categoria': ', '.join(filtro_categoria) or None,
    }

    # El PDF se genera en memoria y luego se conserva en disco (storage
    # privado): queda la constancia del informe y su hash de integridad.
    buffer = BytesIO()

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
        buffer, pagesize=pagesize,
        leftMargin=left_margin, rightMargin=right_margin,
        topMargin=top_margin, bottomMargin=bottom_margin,
        title=f'Auditoría POLIDOC — {fecha_inicio} a {fecha_fin}',
        author=user_display,
        subject=f'Reporte de auditoría ISO 9001:2015 — Período {fecha_inicio} a {fecha_fin}',
        creator='POLIDOC v1.0',
    )
    doc.build(elements, onFirstPage=draw_footer, onLaterPages=draw_footer)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    # ── Conservar el informe en disco (ISO 15489 §6.5) ──
    from auditoria.models import ReporteAuditoria
    from auditoria.middleware import get_client_ip
    from auditoria.services import registrar_log_iso

    reporte = None
    try:
        reporte = ReporteAuditoria(
            codigo=doc_code,
            tipo_reporte=tipo_reporte,
            fecha_inicio=fecha_inicio_dt.date(),
            fecha_fin=fecha_fin_dt.date(),
            filtros=filtros_dict,
            formato=formato,
            orientacion=orientacion,
            total_registros=total,
            tamano_bytes=len(pdf_bytes),
            hash_sha256=sha256(pdf_bytes).hexdigest(),
            generado_por=request.user,
        )
        reporte.archivo.save(
            f'{doc_code}.pdf', ContentFile(pdf_bytes), save=False
        )
        reporte.save()
    except Exception:
        # El informe ya está generado: se entrega igual aunque no se pueda
        # conservar; solo se deja constancia del fallo.
        logger.exception('No se pudo conservar el reporte de auditoría %s', doc_code)
        reporte = None

    if reporte is not None:
        registrar_log_iso(
            usuario=request.user,
            accion_tipo='reporte_auditoria',
            accion_categoria='sistema',
            recurso_tipo='reporte_auditoria',
            recurso_id=str(reporte.uuid),
            recurso_codigo=reporte.codigo,
            recurso_nombre=f'Reporte de auditoría de tipo {tipo_reporte}',
            detalles={
                'periodo': reporte.periodo,
                'filtros': filtros_dict,
                'total_registros': total,
                'hash_sha256': reporte.hash_sha256,
            },
            ip_address=get_client_ip(),
        )

    return FileResponse(
        BytesIO(pdf_bytes), as_attachment=True,
        filename=f'{doc_code}.pdf', content_type='application/pdf',
    )


@login_required(login_url='/login/')
def auditoria_reportes_lista(request):
    """Historial de los informes de auditoría exportados (solo administración)."""
    from auditoria.models import ReporteAuditoria

    if not request.user.perfil.es_administrador:
        messages.error(request, 'No tienes permisos para ver el historial de reportes.')
        return redirect('documentos:mis_documentos')

    reportes = ReporteAuditoria.objects.select_related('generado_por').all()[:500]
    return render(request, 'documentos/configuracion/auditoria_reportes_lista.html', {
        'reportes': reportes,
        'total': len(reportes),
        'retencion_anios': getattr(settings, 'AUDITORIA_REPORTES_RETENCION_ANIOS', 10),
    })


@login_required(login_url='/login/')
def auditoria_reporte_descarga(request, reporte_uuid):
    """Descarga un informe conservado (solo administración)."""
    from auditoria.models import ReporteAuditoria

    if not request.user.perfil.es_administrador:
        messages.error(request, 'No tienes permisos para descargar reportes.')
        return redirect('documentos:mis_documentos')

    reporte = get_object_or_404(ReporteAuditoria, uuid=reporte_uuid)
    if not reporte.archivo:
        messages.error(request, 'El archivo de este reporte ya no está disponible.')
        return redirect('documentos:auditoria_reportes_lista')

    return FileResponse(
        reporte.archivo.open('rb'), as_attachment=True,
        filename=reporte.nombre_archivo, content_type='application/pdf',
    )


def _enviar_email_revocacion(distribucion, documento, tipo, request):
    """Envía el correo de revocación de una distribución. Retorna (ok, error)."""
    from django.conf import settings
    from django.core import mail
    from django.urls import reverse

    email_destino = distribucion.destinatario_email
    if not email_destino:
        return False, 'sin_correo'

    link = request.build_absolute_uri(
        reverse('documentos:detalle', args=[tipo, documento.uuid])
    )
    asunto = f'[POLIDOC] Distribución revocada: {documento.codigo} - {documento.nombre}'
    lineas = [
        f'Hola {distribucion.destinatario_nombre},',
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
    from .services.avisos import (
        aviso_texto, clasificacion_de_documento, cuerpo_email_html,
    )

    clasificacion = clasificacion_de_documento(documento)
    lineas += [
        f'Puedes verlo en: {link}',
        '',
        f'Revocado por: {request.user.get_full_name() or request.user.username}',
        '',
        aviso_texto(clasificacion),
        '',
        '— POLIDOC',
    ]

    message = mail.EmailMultiAlternatives(
        asunto, '\n'.join(lineas),
        settings.DEFAULT_FROM_EMAIL, [email_destino],
    )
    message.attach_alternative(
        cuerpo_email_html(lineas, clasificacion), 'text/html',
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
    if es_jefe_efectivo(user, documento.departamento):
        return True
    return documento.propietario == user


@login_required(login_url='/login/')
def distribuir_documento(request, tipo, documento_id):
    documento = get_documento(tipo, documento_id)
    if not (request.user.perfil.es_administrador or
            es_jefe_efectivo(request.user, documento.departamento) or
            documento.propietario == request.user):
        messages.error(request, 'No tienes permisos para distribuir este documento.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    if documento.estado != 'activo':
        messages.error(request, 'Solo se pueden distribuir documentos activos.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)
    from .forms import DistribucionDocumentoForm
    if request.method == 'POST':
        form = DistribucionDocumentoForm(request.POST)
        if form.is_valid():
            metodo = form.cleaned_data['metodo']
            observaciones = form.cleaned_data['observaciones']
            usuarios = form.cleaned_data['usuarios']
            externos = getattr(form, 'externos_parsed', None) or []
            if not usuarios and not externos:
                messages.error(request, 'Debes seleccionar al menos un destinatario.')
                return redirect('documentos:distribuir_documento', tipo=tipo, documento_id=documento.uuid)
            from .services.distribucion import crear_distribuciones
            resultado = crear_distribuciones(
                documento,
                internos=usuarios,
                externos=externos,
                metodo=metodo,
                observaciones=observaciones,
                distribuido_por=request.user,
                adjuntar_pdf=form.cleaned_data.get('adjuntar_pdf', False),
                request=request,
                tipo=tipo,
            )
            messages.success(
                request,
                f'Documento distribuido a {resultado.creadas} destinatario(s) correctamente.'
            )
            for aviso in resultado.mensajes:
                messages.warning(request, aviso)
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
                'destinatario': distribucion.destinatario_nombre,
                'motivo': motivo,
            },
            resultado_exitoso=True,
            resultado_mensaje=f'Distribución #{distribucion.id} revocada: {distribucion.destinatario_nombre} {motivo}',
            ip_address=get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT'),
        )
    except Exception as exc:
        logger.error("Auditoría revocación de distribución: %s", exc)
    messages.success(
        request,
        f'Distribución a {distribucion.destinatario_nombre} revocada correctamente.',
    )
    return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento.uuid)


# =============================================================================
# FIRMA ELECTRÓNICA DE DISTRIBUCIÓN (SES) Y PLANILLA SGC-FOR-002
# =============================================================================

def _puede_gestionar_distribucion(user, documento):
    """Admin, jefe efectivo del depto o propietario."""
    return puede_revocar_distribucion(user, documento)


@login_required(login_url='/login/')
def descargar_planilla_lote(request, tipo, documento_id, lote):
    """Descarga el XLSX de la planilla SGC-FOR-002 de un lote de distribución."""
    from django.http import HttpResponse
    from .permissions import puede_ver_documento
    from .services.distribucion import exportar_planilla_lote

    documento = get_documento(tipo, documento_id)
    if documento is None or not puede_ver_documento(request.user, documento):
        messages.error(request, 'Documento no encontrado o sin permisos.')
        return redirect('documentos:mis_documentos')

    es_gestor = _puede_gestionar_distribucion(request.user, documento)
    es_destinatario = DistribucionDocumento.objects.filter(
        lote=lote, documento=documento, usuario=request.user,
    ).exists()
    if not (es_gestor or es_destinatario):
        messages.error(request, 'No tienes permisos para descargar esta planilla.')
        return redirect('documentos:detalle', tipo=tipo, documento_id=documento.uuid)

    try:
        contenido = exportar_planilla_lote(lote)
    except ValueError:
        messages.error(request, 'El formato SGC-FOR-002 no está disponible.')
        return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento.uuid)

    try:
        from auditoria.middleware import get_client_ip
        from auditoria.services import registrar_log_iso
        registrar_log_iso(
            usuario=request.user,
            accion_tipo='descarga',
            accion_categoria='documento',
            recurso_tipo=tipo,
            recurso_id=str(documento.uuid),
            recurso_codigo=documento.codigo,
            recurso_nombre=documento.nombre,
            detalles={'planilla_lote': str(lote)},
            resultado_exitoso=True,
            resultado_mensaje='Descarga de planilla SGC-FOR-002',
            ip_address=get_client_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT'),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Auditoría descarga planilla: %s', exc)

    nombre = f'SGC-FOR-002_{documento.codigo}_{lote}.xlsx'
    response = HttpResponse(
        contenido,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{nombre}"'
    return response


@login_required(login_url='/login/')
@login_required(login_url='/login/')
@require_POST
def subir_acuse_distribucion(request, tipo, documento_id, dist_id):
    """Sube el acuse físico escaneado de una distribución impresa (P1)."""
    from .services.distribucion import subir_acuse_documento

    documento = get_documento(tipo, documento_id)
    if documento is None:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    if not puede_revocar_distribucion(request.user, documento):
        messages.error(request, 'No tienes permisos para gestionar este acuse.')
        return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento.uuid)
    distribucion = get_object_or_404(
        DistribucionDocumento, id=dist_id, documento=documento,
    )
    archivo = request.FILES.get('acuse')
    if archivo is None:
        messages.error(request, 'Selecciona el archivo del acuse escaneado.')
        return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento.uuid)
    try:
        subir_acuse_documento(distribucion, archivo, request.user)
        messages.success(request, 'Acuse escaneado registrado.')
    except (PermissionError, ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento.uuid)


def firmar_distribucion(request, tipo, documento_id, dist_id):
    """Firma interna de una distribución: OTP al correo + PIN local."""
    from .services import firmas

    distribucion = get_object_or_404(
        DistribucionDocumento, id=dist_id, documento__uuid=documento_id,
    )
    documento = distribucion.documento
    es_admin = request.user.perfil.es_administrador
    if distribucion.usuario_id != request.user.id and not es_admin:
        messages.error(request, 'Esta distribución no te pertenece.')
        return redirect('documentos:mis_documentos')

    if distribucion.revocado:
        messages.error(request, 'La distribución fue revocada; no se puede firmar.')
        return redirect('documentos:detalle', tipo=documento.tipo_id, documento_id=documento.uuid)

    firma = distribucion.firmas.filter(tipo='interna').first()
    if firma is None:
        messages.info(request, 'Esta distribución no requiere firma electrónica.')
        return redirect('documentos:detalle', tipo=documento.tipo_id, documento_id=documento.uuid)
    if firma.estado != 'pendiente':
        messages.info(request, f'La firma ya está registrada ({firma.get_estado_display()}).')
        return redirect('documentos:detalle', tipo=documento.tipo_id, documento_id=documento.uuid)

    if request.method == 'POST':
        accion = request.POST.get('accion', '')
        try:
            if accion == 'crear_pin':
                firmas.establecer_pin(
                    request.user,
                    request.POST.get('pin', ''),
                    request.POST.get('pin2', ''),
                )
                messages.success(request, 'PIN de firma creado correctamente.')
            elif accion == 'enviar_otp':
                firmas.generar_otp(firma, request)
                messages.success(
                    request,
                    'Enviamos un código de verificación a tu correo '
                    f'(válido {firmas.OTP_TTL_MINUTOS} minutos).',
                )
            elif accion == 'firmar':
                firmas.firmar_interna(
                    firma, request.user,
                    request.POST.get('codigo', ''),
                    request.POST.get('pin', ''),
                    request,
                )
                messages.success(request, 'Firma electrónica registrada correctamente.')
                return redirect(
                    'documentos:detalle',
                    tipo=documento.tipo_id, documento_id=documento.uuid,
                )
            else:
                messages.error(request, 'Acción de firma no reconocida.')
        except firmas.ErrorFirma as exc:
            messages.error(request, str(exc))
        return redirect(
            'documentos:firmar_distribucion',
            tipo=tipo, documento_id=documento.uuid, dist_id=distribucion.id,
        )

    return render(request, 'documentos/firmar_distribucion.html', {
        'distribucion': distribucion,
        'documento': documento,
        'tipo': documento.tipo_id,
        'firma': firma,
        'credencial': firmas.obtener_credencial(request.user),
    })


def firmar_invitacion(request, token):
    """Firma externa vía invitación tokenizada (sin sesión de POLIDOC).

    Soporta distribuciones de **documentos** (SGC-FOR-002) y de
    **registros** (acuse del SGC-FOR-010); en ambos casos el mismo token
    permite descargar el archivo hasta la caducidad.
    """
    from .models import (
        ActaDesincorporacion, DistribucionDocumento, DistribucionRegistro,
    )
    from .services import firmas
    from .services.avisos import aviso_html

    invitacion = firmas.buscar_invitacion(token)
    if invitacion is None:
        return render(request, 'documentos/firmar_invitacion.html', {
            'invalida': True,
        }, status=404)

    firma = invitacion.firma
    origen = firma.origen
    acta = None
    if isinstance(origen, DistribucionDocumento):
        documento = origen.documento
        registro = None
        distribucion = origen
        revocada = origen.revocado
    elif isinstance(origen, DistribucionRegistro):
        from .services.registros import documento_de_contexto

        registro = origen.registro
        documento = documento_de_contexto(registro)
        distribucion = origen
        revocada = origen.revocado
    elif isinstance(origen, ActaDesincorporacion):
        # SGC-FOR-004: el enlace solo sirve mientras el acta siga abierta.
        acta = origen
        documento = origen.padre
        registro = None
        distribucion = None
        revocada = not origen.esta_abierta
    else:
        return render(request, 'documentos/firmar_invitacion.html', {
            'invalida': True,
        }, status=404)
    if documento is None and registro is None and acta is None:
        return render(request, 'documentos/firmar_invitacion.html', {
            'invalida': True,
        }, status=404)

    clasificacion = (
        (documento.clasificacion or 'interno') if documento else 'interno'
    ).lower()
    contexto_base = {
        'invitacion': invitacion, 'firma': firma, 'documento': documento,
        'registro': registro, 'distribucion': distribucion, 'acta': acta,
        'url_descarga': reverse('documentos:descarga_invitacion', args=[token]),
        'aviso_html': aviso_html(clasificacion),
    }

    if revocada:
        return render(request, 'documentos/firmar_invitacion.html', {
            **contexto_base, 'revocada': True,
        }, status=403)

    if not invitacion.esta_vigente:
        if not invitacion.usado and firma.estado == 'pendiente':
            firmas.marcar_expirada(invitacion)
        return render(request, 'documentos/firmar_invitacion.html', {
            **contexto_base, 'expirada': True,
        })

    error = ''
    if request.method == 'POST':
        try:
            firmas.firmar_externa(
                invitacion, request,
                consentimiento=request.POST.get('consentimiento') == 'on',
            )
            return render(request, 'documentos/firmar_invitacion.html', {
                **contexto_base, 'firmada': True,
            })
        except firmas.ErrorFirma as exc:
            error = str(exc)

    return render(request, 'documentos/firmar_invitacion.html', {
        **contexto_base, 'error': error,
    })


@login_required(login_url='/login/')
def alternar_retiro_version(request, tipo, documento_id, dist_id):
    """Marca/desmarca que el destinatario retiró la versión anterior (FOR-002)."""
    from .services.distribucion import generar_planilla_lote

    if request.method != 'POST':
        return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento_id)
    documento = get_documento(tipo, documento_id)
    if documento is None:
        messages.error(request, 'Documento no encontrado.')
        return redirect('documentos:mis_documentos')
    if not _puede_gestionar_distribucion(request.user, documento):
        messages.error(request, 'No tienes permisos para modificar la distribución.')
        return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento.uuid)

    distribucion = get_object_or_404(
        DistribucionDocumento, id=dist_id, documento=documento,
    )
    if distribucion.revocado:
        messages.error(request, 'La distribución está revocada.')
        return redirect('documentos:historial_distribucion', tipo=tipo, documento_id=documento.uuid)

    distribucion.retiro_version_anterior = not distribucion.retiro_version_anterior
    distribucion.save(update_fields=['retiro_version_anterior'])
    if distribucion.lote:
        generar_planilla_lote(distribucion.lote)
    messages.success(
        request,
        'Retiro de la versión anterior '
        + ('registrado.' if distribucion.retiro_version_anterior else 'desmarcado.'),
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
    # Un borrador puede haberse editado desde la última descarga: se regenera.
    borrador = solicitud.estado == 'borrador'
    if borrador or not solicitud.planilla_xlsx:
        if not solicitud.formato or not solicitud.formato.plantilla_xlsx:
            messages.error(request, 'No hay plantilla Excel disponible para exportar.')
            return redirect('documentos:mis_documentos')
        from .services.planillas import guardar_planilla_solicitud
        resultado = guardar_planilla_solicitud(solicitud, forzar=borrador, con_pdf=False)
        if not resultado.get('success'):
            logger.warning(
                f"No se pudo persistir la planilla XLSX de {solicitud.codigo}: "
                f"{resultado.get('error')}"
            )
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
    # Mismo criterio que el XLSX: el borrador se regenera, lo demás se sirve
    # ya persistido (y ya reparado por la conversión).
    borrador = solicitud.estado == 'borrador'
    if borrador or not solicitud.planilla_pdf:
        if not solicitud.formato or not solicitud.formato.plantilla_xlsx:
            messages.error(request, 'No hay plantilla Excel disponible para exportar.')
            return redirect('documentos:mis_documentos')
        from .services.planillas import guardar_planilla_solicitud
        resultado = guardar_planilla_solicitud(solicitud, forzar=borrador)
        if not resultado.get('pdf'):
            logger.warning(
                f"No se pudo persistir la planilla PDF de {solicitud.codigo}: "
                f"{resultado.get('error') or 'sin PDF (¿LibreOffice?)'}"
            )
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





# =============================================================================
# REGISTROS DE FORMATO (instancias llenas; el código del formato se repite)
# =============================================================================

def _contexto_registro(request):
    tipo = (
        request.GET.get('contexto_tipo')
        or request.POST.get('contexto_tipo')
        or 'documento'
    ).strip()
    ident = (
        request.GET.get('contexto_id')
        or request.POST.get('contexto_id')
        or ''
    ).strip()
    return tipo, ident


@login_required(login_url='/login/')
def registros_lista(request):
    """Listado global de registros visibles para el usuario."""
    from .services import registros as svc

    qs = svc.registros_visibles(request.user).select_related(
        'formato', 'responsable', 'aprobador',
    )
    formato_id = request.GET.get('formato') or ''
    estado = request.GET.get('estado') or ''
    buscar = (request.GET.get('q') or '').strip()
    if formato_id.isdigit():
        qs = qs.filter(formato_id=int(formato_id))
    if estado:
        qs = qs.filter(estado=estado)
    if buscar:
        qs = qs.filter(
            Q(titulo__icontains=buscar) | Q(formato_codigo__icontains=buscar),
        )
    page = Paginator(qs.order_by('-creado'), 20).get_page(
        request.GET.get('page', 1),
    )
    return render(request, 'documentos/registros_lista.html', {
        'registros': page,
        'page_obj': page,
        'formatos': Formato.objects.filter(
            activo=True, permite_registros=True,
        ).order_by('codigo'),
        'filtro_formato': formato_id,
        'filtro_estado': estado,
        'q': buscar,
        'estados': RegistroFormato.ESTADO_CHOICES,
    })


@login_required(login_url='/login/')
def registro_crear(request):
    """Crea un registro (formulario lleno) de un formato habilitado."""
    from .services import registros as svc

    formato_id = request.GET.get('formato') or request.POST.get('formato')
    contexto_tipo, contexto_id = _contexto_registro(request)
    documento_contexto = None
    if contexto_tipo == 'documento' and contexto_id:
        documento_contexto = get_object_or_404(Documento, uuid=contexto_id)
        if (not puede_editar_documento(request.user, documento_contexto)
                and not request.user.perfil.es_administrador):
            messages.error(
                request, 'No tienes permisos sobre el documento de contexto.',
            )
            return redirect('documentos:mis_documentos')

    if not formato_id:
        messages.error(request, 'Selecciona un formato que permita registros.')
        return redirect('documentos:registros_lista')
    formato = get_object_or_404(
        Formato, pk=formato_id, activo=True, permite_registros=True,
    )

    respuestas = {}
    if request.method == 'POST':
        form = RegistroFormatoForm(request.POST, request.FILES)
        respuestas, errores = validar_respuestas(
            formato, request.POST, parcial=False,
        )
        if form.is_valid() and not errores:
            registro = form.save(commit=False)
            registro.formato = formato
            registro.formato_codigo = formato.codigo
            registro.formato_version = formato.version
            registro.formato_edicion = formato.edicion
            registro.contexto_tipo = contexto_tipo or 'documento'
            registro.contexto_id = contexto_id
            registro.responsable = request.user
            registro.respuestas = respuestas
            registro.save()
            messages.success(request, 'Registro guardado correctamente.')
            return redirect(
                'documentos:registro_detalle', registro_uuid=registro.uuid,
            )
        for error in errores:
            messages.error(request, error)
    else:
        form = RegistroFormatoForm()

    return render(request, 'documentos/registro_form.html', {
        'form': form,
        'formato': formato,
        'respuestas': respuestas,
        'contexto_tipo': contexto_tipo,
        'contexto_id': contexto_id,
        'documento_contexto': documento_contexto,
        'credencial': None,
        'edicion': False,
    })


@login_required(login_url='/login/')
def registro_editar(request, registro_uuid):
    from .services import registros as svc

    registro = get_object_or_404(
        RegistroFormato.objects.select_related('formato'), uuid=registro_uuid,
    )
    if not svc.puede_editar_registro(request.user, registro):
        messages.error(request, 'Este registro no se puede editar.')
        return redirect(
            'documentos:registro_detalle', registro_uuid=registro.uuid,
        )

    formato = registro.formato
    respuestas = dict(registro.respuestas or {})
    if request.method == 'POST':
        form = RegistroFormatoForm(
            request.POST, request.FILES, instance=registro,
        )
        nuevas, errores = validar_respuestas(formato, request.POST, parcial=False)
        if form.is_valid() and not errores:
            actualizado = form.save(commit=False)
            actualizado.respuestas = nuevas
            actualizado.estado = 'borrador'
            actualizado.save()
            messages.success(request, 'Registro actualizado.')
            return redirect(
                'documentos:registro_detalle', registro_uuid=registro.uuid,
            )
        for error in errores:
            messages.error(request, error)
    else:
        form = RegistroFormatoForm(instance=registro)

    return render(request, 'documentos/registro_form.html', {
        'form': form,
        'formato': formato,
        'respuestas': respuestas,
        'contexto_tipo': registro.contexto_tipo,
        'contexto_id': registro.contexto_id,
        'documento_contexto': svc.documento_de_contexto(registro),
        'credencial': None,
        'edicion': True,
        'registro': registro,
    })


@login_required(login_url='/login/')
def registro_detalle(request, registro_uuid):
    from .services import firmas as svc_firmas
    from .services import registros as svc
    from .services.avisos import aviso_html
    from .services.distribucion_registros import (
        clasificacion_de_registro, puede_gestionar_distribucion_registro,
    )

    registro = get_object_or_404(
        RegistroFormato.objects.select_related(
            'formato', 'responsable', 'aprobador',
        ),
        uuid=registro_uuid,
    )
    if not svc.puede_ver_registro(request.user, registro):
        messages.error(request, 'No tienes acceso a este registro.')
        return redirect('documentos:registros_lista')

    puede_distribuir = (
        registro.estado == 'aprobado'
        and puede_gestionar_distribucion_registro(request.user, registro)
    )
    usuarios_distribucion = (
        User.objects.filter(is_active=True).order_by(
            'first_name', 'last_name', 'username',
        ) if puede_distribuir else []
    )
    return render(request, 'documentos/registro_detalle.html', {
        'registro': registro,
        'formato': registro.formato,
        'documento_contexto': svc.documento_de_contexto(registro),
        'puede_editar': svc.puede_editar_registro(request.user, registro),
        'puede_aprobar': svc.puede_aprobar_registro(request.user, registro),
        'credencial': svc_firmas.obtener_credencial(request.user),
        'firmas': FirmaElectronica.para(registro).order_by('-creado'),
        'distribuciones': registro.distribuciones.select_related(
            'usuario', 'firma',
        ).order_by('-fecha_distribucion'),
        'puede_distribuir': puede_distribuir,
        'usuarios_distribucion': usuarios_distribucion,
        'aviso_html': aviso_html(clasificacion_de_registro(registro)),
    })


@login_required(login_url='/login/')
@require_POST
def registro_enviar(request, registro_uuid):
    from .services import registros as svc

    registro = get_object_or_404(RegistroFormato, uuid=registro_uuid)
    try:
        svc.enviar_a_revision(registro, request.user)
        messages.success(request, 'Registro enviado a revisión.')
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)


@login_required(login_url='/login/')
@require_POST
def registro_aprobar(request, registro_uuid):
    """Aprueba un registro con firma SES (PIN)."""
    from .services import registros as svc

    registro = get_object_or_404(RegistroFormato, uuid=registro_uuid)
    if not svc.puede_aprobar_registro(request.user, registro):
        messages.error(request, 'No puedes aprobar este registro.')
        return redirect(
            'documentos:registro_detalle', registro_uuid=registro.uuid,
        )
    try:
        firmar(
            registro, usuario=request.user,
            pin=request.POST.get('pin', ''), request=request,
            metodo='pin', crear=True,
            datos={
                'aprobada': True,
                'observaciones': request.POST.get('observaciones', ''),
            },
        )
        messages.success(request, 'Registro aprobado y firmado correctamente.')
    except ErrorFirma as exc:
        messages.error(request, str(exc))
    return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)


@login_required(login_url='/login/')
@require_POST
def registro_rechazar(request, registro_uuid):
    """Rechaza un registro SIN firma: vuelve a corrección."""
    from .services import registros as svc

    registro = get_object_or_404(RegistroFormato, uuid=registro_uuid)
    try:
        svc.rechazar_registro(
            registro, request.user, request.POST.get('observaciones', ''),
        )
        messages.warning(request, 'Registro rechazado para corrección.')
    except PermissionError as exc:
        messages.error(request, str(exc))
    return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)


@login_required(login_url='/login/')
def registro_xlsx(request, registro_uuid):
    from .services import registros as svc

    registro = get_object_or_404(RegistroFormato, uuid=registro_uuid)
    if not svc.puede_ver_registro(request.user, registro):
        raise Http404
    try:
        contenido = svc.exportar_registro_xlsx(registro)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(
            'documentos:registro_detalle', registro_uuid=registro.uuid,
        )
    nombre = f'{registro.formato_codigo}_{registro.uuid.hex[:8]}.xlsx'
    return FileResponse(
        io.BytesIO(contenido), as_attachment=True, filename=nombre,
    )


@login_required(login_url='/login/')
def registro_pdf(request, registro_uuid):
    from .services import registros as svc

    registro = get_object_or_404(RegistroFormato, uuid=registro_uuid)
    if not svc.puede_ver_registro(request.user, registro):
        raise Http404
    ruta = svc.exportar_registro_pdf(registro)
    if not ruta:
        messages.error(request, 'No se pudo generar el PDF del registro.')
        return redirect(
            'documentos:registro_detalle', registro_uuid=registro.uuid,
        )
    nombre = f'{registro.formato_codigo}_{registro.uuid.hex[:8]}.pdf'
    return FileResponse(open(ruta, 'rb'), as_attachment=True, filename=nombre)


# =============================================================================
# REGISTROS: DISTRIBUCIÓN, ACUSE Y CONSTANCIA (P1)
# =============================================================================

@login_required(login_url='/login/')
def registro_distribuir(request, registro_uuid):
    """Distribuye un registro aprobado (sistema/correo/impreso) y pide acuse."""
    from .services.avisos import aviso_html
    from .services.distribucion_registros import (
        clasificacion_de_registro, crear_distribuciones_registro,
        puede_gestionar_distribucion_registro,
    )
    from .services.registros import documento_de_contexto

    registro = get_object_or_404(RegistroFormato, uuid=registro_uuid)
    if not puede_gestionar_distribucion_registro(request.user, registro):
        messages.error(request, 'No tienes permisos para distribuir este registro.')
        return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)
    if registro.estado != 'aprobado':
        messages.error(request, 'Solo se pueden distribuir registros aprobados.')
        return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)

    if request.method == 'POST':
        form = DistribucionDocumentoForm(request.POST)
        if form.is_valid():
            usuarios = form.cleaned_data['usuarios']
            externos = getattr(form, 'externos_parsed', None) or []
            if not usuarios and not externos:
                messages.error(request, 'Debes seleccionar al menos un destinatario.')
                return redirect('documentos:registro_distribuir', registro_uuid=registro.uuid)
            try:
                resultado = crear_distribuciones_registro(
                    registro,
                    internos=usuarios,
                    externos=externos,
                    metodo=form.cleaned_data['metodo'],
                    observaciones=form.cleaned_data['observaciones'],
                    distribuido_por=request.user,
                    adjuntar_pdf=form.cleaned_data.get('adjuntar_pdf', False),
                    request=request,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect('documentos:registro_distribuir', registro_uuid=registro.uuid)
            messages.success(
                request,
                f'Registro distribuido a {resultado.creadas} destinatario(s).',
            )
            for aviso in resultado.mensajes:
                messages.warning(request, aviso)
            return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)
    else:
        form = DistribucionDocumentoForm()

    return render(request, 'documentos/distribuir_registro.html', {
        'registro': registro,
        'form': form,
        'usuarios': form.usuarios_list,
        'documento_contexto': documento_de_contexto(registro),
        'aviso_html': aviso_html(clasificacion_de_registro(registro)),
    })


@login_required(login_url='/login/')
@require_POST
def registro_subir_acuse(request, registro_uuid, dist_id):
    """Sube el acuse físico escaneado de una distribución impresa."""
    from .services.distribucion_registros import (
        puede_gestionar_distribucion_registro, subir_acuse,
    )

    registro = get_object_or_404(RegistroFormato, uuid=registro_uuid)
    distribucion = get_object_or_404(
        DistribucionRegistro, id=dist_id, registro=registro,
    )
    if not puede_gestionar_distribucion_registro(request.user, registro):
        messages.error(request, 'No tienes permisos para gestionar este acuse.')
        return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)

    archivo = request.FILES.get('acuse')
    if archivo is None:
        messages.error(request, 'Selecciona el archivo del acuse escaneado.')
        return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)
    try:
        subir_acuse(distribucion, archivo, request.user)
        messages.success(request, 'Acuse escaneado registrado.')
    except (PermissionError, ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)


@login_required(login_url='/login/')
@require_POST
def registro_revocar_distribucion(request, registro_uuid, dist_id):
    """Revoca una distribución: bloquea el enlace seguro y el acuse."""
    from .services.distribucion_registros import (
        puede_gestionar_distribucion_registro, revocar_distribucion_registro,
    )

    registro = get_object_or_404(RegistroFormato, uuid=registro_uuid)
    distribucion = get_object_or_404(
        DistribucionRegistro, id=dist_id, registro=registro,
    )
    if not puede_gestionar_distribucion_registro(request.user, registro):
        messages.error(request, 'No tienes permisos para revocar esta distribución.')
        return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)
    try:
        revocar_distribucion_registro(
            distribucion, request.user,
            request.POST.get('motivo', '').strip(),
        )
        messages.success(request, 'Distribución revocada; el enlace quedó bloqueado.')
    except ValueError as exc:
        messages.warning(request, str(exc))
    return redirect('documentos:registro_detalle', registro_uuid=registro.uuid)


@login_required(login_url='/login/')
def registro_constancia(request, registro_uuid, formato):
    """Planilla de distribución y acuses del registro (``csv``/``xlsx``/``pdf``)."""
    from .services.distribucion_registros import (
        constancia_csv, constancia_pdf, constancia_xlsx,
    )
    from .services.registros import puede_ver_registro

    registro = get_object_or_404(RegistroFormato, uuid=registro_uuid)
    if not puede_ver_registro(request.user, registro):
        raise Http404
    base = f'constancia_{registro.formato_codigo}_{registro.uuid.hex[:8]}'

    if formato == 'csv':
        return FileResponse(
            io.BytesIO(constancia_csv(registro)), as_attachment=True,
            filename=f'{base}.csv',
        )
    if formato == 'xlsx':
        return FileResponse(
            io.BytesIO(constancia_xlsx(registro)), as_attachment=True,
            filename=f'{base}.xlsx',
        )
    if formato == 'pdf':
        data = constancia_pdf(registro)
        if not data:
            messages.error(request, 'No se pudo generar el PDF de la constancia.')
            return redirect(
                'documentos:registro_detalle', registro_uuid=registro.uuid,
            )
        return FileResponse(
            io.BytesIO(data), as_attachment=True, filename=f'{base}.pdf',
        )
    raise Http404


# =============================================================================
# ENLACE SEGURO: DESCARGA TOKENIZADA (documentos y registros)
# =============================================================================

def descarga_invitacion(request, token):
    """Descarga el archivo por enlace seguro (token SHA-256, 7 días).

    Permitida **hasta la caducidad** del token, incluso después de firmar;
    un solo uso aplica solo a la firma. Bloquea distribuciones revocadas.
    """
    from .services import firmas

    invitacion = firmas.buscar_invitacion(token)
    if invitacion is None or invitacion.expira <= timezone.now():
        raise Http404

    firma = invitacion.firma
    origen = firma.origen
    from .models import (
        ActaDesincorporacion, DistribucionDocumento, DistribucionRegistro,
    )

    if isinstance(origen, ActaDesincorporacion):
        # SGC-FOR-004: el enlace permite descargar el acta hasta caducar.
        if not origen.esta_abierta and origen.estado != 'aplicada':
            raise Http404
        from .services import for_004 as svc_for_004

        resultado = svc_for_004.generar_y_guardar(origen)
        if not resultado.get('success'):
            raise Http404
        archivo = origen.pdf or origen.xlsx
        if not archivo:
            raise Http404
        extension = 'pdf' if origen.pdf else 'xlsx'
        response = FileResponse(
            archivo.open('rb'), as_attachment=True,
            filename=svc_for_004.nombre_archivo(origen, extension),
        )
        response['Cache-Control'] = 'no-store, max-age=0'
        response['Pragma'] = 'no-cache'
        response['X-Content-Type-Options'] = 'nosniff'
        return response

    if isinstance(origen, DistribucionDocumento):
        if origen.revocado:
            raise Http404
        documento = origen.documento
        if documento is None:
            raise Http404
        from .services.documentos_pdf import archivo_existe, pdf_disponible

        archivo = None
        if documento.archivo_pdf and pdf_disponible(documento):
            archivo = documento.archivo_pdf
        elif documento.archivo and archivo_existe(documento.archivo):
            archivo = documento.archivo
        if archivo is None:
            raise Http404
        nombre = getattr(archivo, 'name', '') or 'documento.pdf'
        response = FileResponse(
            archivo.open('rb'), as_attachment=True,
            filename=os.path.basename(nombre),
        )
    elif isinstance(origen, DistribucionRegistro):
        if origen.revocado:
            raise Http404
        from .services.registros import exportar_registro_pdf

        ruta = exportar_registro_pdf(origen.registro)
        if not ruta:
            raise Http404
        nombre = (
            f'{origen.registro.formato_codigo}_'
            f'{origen.registro.uuid.hex[:8]}.pdf'
        )
        response = FileResponse(open(ruta, 'rb'), as_attachment=True, filename=nombre)
    else:
        raise Http404

    logger.info(
        'Descarga por enlace seguro: invitación=%s destino=%s',
        invitacion.pk, firma.firmante_email or firma.firmante_clave,
    )
    response['Cache-Control'] = 'no-store, max-age=0'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


# ---------------------------------------------------------------------------
# Documentos sin código (personal / soportes SGC)
# ---------------------------------------------------------------------------

def _lista_sin_codigo(request, ambito):
    """Listado de una bandeja de documentos sin código con filtro simple."""
    from .services import documentos_sin_codigo as svc

    if ambito not in (svc.AMBITO_PERSONAL, svc.AMBITO_SGC):
        raise Http404
    if not svc.puede_crear(request.user, ambito):
        messages.error(request, 'No tienes permisos para ver esta sección.')
        return redirect('documentos:mis_documentos')

    qs = svc.visibles(request.user, ambito)
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(titulo__icontains=q) | Q(referencia__icontains=q))
    page = Paginator(qs.order_by('-creado'), 20).get_page(request.GET.get('page', 1))
    return render(request, 'documentos/documentos_sin_codigo_lista.html', {
        'ambito': ambito,
        'es_sgc': ambito == svc.AMBITO_SGC,
        'documentos': page,
        'page_obj': page,
        'q': q,
        'puede_crear': True,
    })


@login_required(login_url='/login/')
def documentos_personales(request):
    """Bandeja 'Mis documentos personales' (solo los del usuario)."""
    return _lista_sin_codigo(request, 'personal')


@login_required(login_url='/login/')
def documentos_sgc_sin_codigo(request):
    """Bandeja de soportes/registros institucionales sin código (admin/jefe)."""
    return _lista_sin_codigo(request, 'sgc')


def _form_sin_codigo(request, ambito, documento=None):
    """Alta/edición compartida de un documento sin código."""
    from .forms import DocumentoSinCodigoForm
    from .services import documentos_sin_codigo as svc

    if ambito not in (svc.AMBITO_PERSONAL, svc.AMBITO_SGC):
        raise Http404
    if not svc.puede_crear(request.user, ambito):
        messages.error(request, 'No tienes permisos para crear ese documento.')
        return redirect('documentos:mis_documentos')

    if request.method == 'POST':
        form = DocumentoSinCodigoForm(
            request.POST, request.FILES, instance=documento, ambito=ambito,
        )
        if form.is_valid():
            doc = form.save(commit=False)
            doc.ambito = ambito
            if documento is None:
                doc.propietario = request.user
                doc.creado_por = request.user
            doc.save()
            _registrar_auditoria(
                request,
                accion=('documentos_sin_codigo_editado' if documento is not None
                        else 'documentos_sin_codigo_creado'),
                recurso_tipo='documentos_sin_codigo', recurso_id=doc.uuid,
                recurso_nombre=doc.titulo,
                detalles={'ambito': ambito},
            )
            messages.success(
                request,
                'Documento guardado correctamente.'
                if documento is not None else 'Documento creado correctamente.',
            )
            return redirect(
                'documentos:documento_sin_codigo_detalle', uuid=doc.uuid,
            )
    else:
        form = DocumentoSinCodigoForm(instance=documento, ambito=ambito)

    return render(request, 'documentos/documentos_sin_codigo_form.html', {
        'form': form,
        'ambito': ambito,
        'es_sgc': ambito == svc.AMBITO_SGC,
        'documento': documento,
        'edicion': documento is not None,
    })


@login_required(login_url='/login/')
def documento_sin_codigo_crear(request, ambito):
    return _form_sin_codigo(request, ambito, documento=None)


@login_required(login_url='/login/')
def documento_sin_codigo_detalle(request, uuid):
    from .services import documentos_sin_codigo as svc

    documento = get_object_or_404(DocumentoSinCodigo, uuid=uuid, activo=True)
    if not svc.puede_ver(request.user, documento):
        raise Http404
    return render(request, 'documentos/documentos_sin_codigo_detalle.html', {
        'documento': documento,
        'es_sgc': documento.ambito == svc.AMBITO_SGC,
        'puede_editar': svc.puede_editar(request.user, documento),
    })


@login_required(login_url='/login/')
def documento_sin_codigo_editar(request, uuid):
    from .services import documentos_sin_codigo as svc

    documento = get_object_or_404(DocumentoSinCodigo, uuid=uuid, activo=True)
    if not svc.puede_editar(request.user, documento):
        raise Http404
    return _form_sin_codigo(request, documento.ambito, documento=documento)


@login_required(login_url='/login/')
@require_POST
def documento_sin_codigo_eliminar(request, uuid):
    from .services import documentos_sin_codigo as svc

    documento = get_object_or_404(DocumentoSinCodigo, uuid=uuid, activo=True)
    if not svc.puede_eliminar(request.user, documento):
        raise Http404
    documento.activo = False
    documento.eliminado_en = timezone.now()
    documento.save(update_fields=['activo', 'eliminado_en', 'actualizado'])
    _registrar_auditoria(
        request, accion='documentos_sin_codigo_eliminado',
        recurso_tipo='documentos_sin_codigo', recurso_id=documento.uuid,
        recurso_nombre=documento.titulo, detalles={'ambito': documento.ambito},
    )
    messages.success(request, 'Documento eliminado.')
    if documento.ambito == svc.AMBITO_SGC:
        return redirect('documentos:documentos_sgc_sin_codigo')
    return redirect('documentos:documentos_personales')


@login_required(login_url='/login/')
def documento_sin_codigo_descargar(request, uuid):
    from .services import documentos_sin_codigo as svc

    documento = get_object_or_404(DocumentoSinCodigo, uuid=uuid, activo=True)
    if not svc.puede_ver(request.user, documento) or not documento.archivo:
        raise Http404
    _registrar_auditoria(
        request, accion='documentos_sin_codigo_descarga',
        recurso_tipo='documentos_sin_codigo', recurso_id=documento.uuid,
        recurso_nombre=documento.titulo, detalles={'ambito': documento.ambito},
    )
    response = FileResponse(
        documento.archivo.open('rb'), as_attachment=True,
        filename=os.path.basename(documento.archivo.name),
    )
    response['X-Content-Type-Options'] = 'nosniff'
    return response


# ---------------------------------------------------------------------------
# SGC-FOR-006 — Lista Maestra (vista exclusiva)
# ---------------------------------------------------------------------------

def _registro_lista(crear=False, usuario=None):
    from .services import for_006 as svc

    return svc.registro_lista_maestra(crear=crear, responsable=usuario)


def _auditar_lista(request, registro, accion, detalles=None):
    """Auditoría ISO de las acciones de la Lista Maestra (no rompe nada)."""
    _registrar_auditoria(
        request, accion=accion, recurso_tipo='lista_maestra',
        recurso_id=getattr(registro, 'pk', ''), recurso_codigo='SGC-FOR-006',
        recurso_nombre='Lista Maestra de Documentos', detalles=detalles,
    )


def _historial_lista(registro):
    try:
        from auditoria.services import obtener_logs

        return obtener_logs(
            recurso_tipo='lista_maestra', recurso_id=str(registro.pk), limite=15,
        )
    except Exception:  # noqa: BLE001
        return []


def _iso_fecha(valor):
    texto = str(valor or '').strip()
    if not texto:
        return ''
    if len(texto) >= 10 and texto[4] == '-':
        return texto[:10]
    partes = texto.split('/')
    if len(partes) == 3:
        try:
            return (
                f'{int(partes[2]):04d}-{int(partes[1]):02d}-{int(partes[0]):02d}'
            )
        except (TypeError, ValueError):
            return texto
    return texto


def _sugerencias(filas, definiciones):
    """Valores recientes por columna (más recientes primero) + opciones."""
    sugerencias = {}
    for col in definiciones:
        clave = col.get('clave')
        if not clave:
            continue
        vistos = []
        vistos_set = set()
        for opcion in (col.get('opciones') or []):
            opcion = str(opcion)
            if opcion and opcion not in vistos_set:
                vistos_set.add(opcion)
                vistos.append(opcion)
        for fila in reversed(filas):
            valor = fila.get(clave)
            if valor is None:
                continue
            valor = str(valor)
            if valor and valor not in vistos_set:
                vistos_set.add(valor)
                vistos.append(valor)
        sugerencias[clave] = vistos[:50]
    return sugerencias


@login_required(login_url='/login/')
def lista_maestra(request):
    from .services import for_006 as svc
    from .services import lista_maestra as acceso

    if not acceso.puede_ver_lista(request.user):
        messages.error(request, 'No tienes acceso a la Lista Maestra.')
        return redirect('documentos:mis_documentos')
    formato = svc.formato_for_006()
    if formato is None:
        messages.error(
            request, 'La Lista Maestra (SGC-FOR-006) no está integrada.',
        )
        return redirect('documentos:formatos_lista')

    registro = _registro_lista(crear=True, usuario=request.user)
    if registro is None:
        messages.error(request, 'No se pudo abrir el registro de la Lista Maestra.')
        return redirect('documentos:mis_documentos')

    definiciones = svc.columnas(formato)
    todas = svc.filas(registro)

    q = (request.GET.get('q') or '').strip()
    visibles = todas
    if q:
        buscado = q.lower()
        claves = [c.get('clave') for c in definiciones if c.get('clave')]
        visibles = [
            fila for fila in todas
            if any(buscado in str(fila.get(k, '')).lower() for k in claves)
        ]
    page = Paginator(visibles, 50).get_page(request.GET.get('page', 1))

    # Convertir fechas (dd/mm/aaaa legacy) a ISO para el input type=date.
    filas_render = []
    for fila in page.object_list:
        copia = dict(fila)
        for col in definiciones:
            if col.get('tipo') == 'fecha':
                clave = col.get('clave')
                copia[clave] = _iso_fecha(fila.get(clave))
        filas_render.append(copia)

    return render(request, 'documentos/lista_maestra.html', {
        'registro': registro,
        'formato': formato,
        'definiciones': definiciones,
        'filas': filas_render,
        'page_obj': page,
        'q': q,
        'sugerencias': _sugerencias(todas, definiciones),
        'es_admin': acceso.es_administrador(request.user),
        'puede_gestionar_acceso': acceso.puede_gestionar_acceso(request.user),
        'historial': _historial_lista(registro),
        'total_filas': len(todas),
    })


@login_required(login_url='/login/')
@require_POST
def lista_maestra_editar_fila(request, clave):
    from django.db import transaction
    from .models import RegistroFormato
    from .services import for_006 as svc
    from .services import lista_maestra as acceso

    if not acceso.puede_ver_lista(request.user):
        return JsonResponse({'ok': False, 'error': 'sin_permiso'}, status=403)
    registro = _registro_lista(crear=True, usuario=request.user)
    if registro is None:
        return JsonResponse({'ok': False, 'error': 'sin_registro'}, status=400)

    definiciones = svc.columnas(registro.formato)
    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(pk=registro.pk)
        filas = svc.filas(bloqueado)
        fila = next(
            (f for f in filas if f.get('_clave') == clave), None,
        )
        if fila is None:
            return JsonResponse({'ok': False, 'error': 'no_encontrada'}, status=404)
        manual = set(fila.get('_manual') or [])
        for col in definiciones:
            nombre = col.get('clave')
            if not nombre:
                continue
            nuevo = request.POST.get(f'valor_{nombre}')
            if nuevo is None:
                continue
            nuevo = nuevo.strip() if isinstance(nuevo, str) else nuevo
            if fila.get(nombre) != nuevo:
                fila[nombre] = nuevo
                manual.add(nombre)
        fila['_manual'] = sorted(manual)
        bloqueado.respuestas = {
            **(bloqueado.respuestas or {}), 'documentos': filas,
        }
        bloqueado.save(update_fields=['respuestas', 'actualizado'])

    _auditar_lista(request, registro, 'lista_maestra_edicion', {'clave': clave})
    return JsonResponse({'ok': True})


@login_required(login_url='/login/')
@require_POST
def lista_maestra_agregar_fila(request):
    from .services import for_006 as svc
    from .services import lista_maestra as acceso

    if not acceso.puede_ver_lista(request.user):
        messages.error(request, 'No tienes acceso a la Lista Maestra.')
        return redirect('documentos:mis_documentos')
    registro = _registro_lista(crear=True, usuario=request.user)
    if registro is not None:
        valores = {}
        for col in svc.columnas(registro.formato):
            clave = col.get('clave')
            if clave and request.POST.get(f'valor_{clave}') is not None:
                valores[clave] = (request.POST.get(f'valor_{clave}') or '').strip()
        svc.agregar_fila_manual(registro, valores, usuario=request.user)
        _auditar_lista(request, registro, 'lista_maestra_fila_nueva', valores)
        messages.success(request, 'Fila agregada. Complétala y guarda los cambios.')
    return redirect('documentos:lista_maestra')


@login_required(login_url='/login/')
@require_POST
def lista_maestra_eliminar_fila(request, clave):
    from .services import for_006 as svc
    from .services import lista_maestra as acceso

    if not acceso.puede_ver_lista(request.user):
        messages.error(request, 'No tienes acceso a la Lista Maestra.')
        return redirect('documentos:mis_documentos')
    registro = _registro_lista()
    if registro is not None:
        fila = next(
            (f for f in svc.filas(registro) if f.get('_clave') == clave), None,
        )
        if fila is None or fila.get('_origen_tipo') != svc.ORIGEN_MANUAL:
            messages.warning(
                request,
                'Solo se pueden eliminar filas agregadas a mano; las que vienen '
                'de documentos se quitan al marcarlos como obsoletos.',
            )
        else:
            svc.quitar_fila(registro, clave)
            _auditar_lista(request, registro, 'lista_maestra_fila_eliminada',
                           {'clave': clave})
            messages.success(request, 'Fila eliminada.')
    return redirect('documentos:lista_maestra')


@login_required(login_url='/login/')
@require_POST
def lista_maestra_sincronizar(request):
    from .services import for_006 as svc
    from .services import lista_maestra as acceso

    if not acceso.puede_ver_lista(request.user):
        messages.error(request, 'No tienes acceso a la Lista Maestra.')
        return redirect('documentos:mis_documentos')
    registro = _registro_lista(crear=True, usuario=request.user)
    if registro is None:
        messages.error(request, 'No se pudo abrir la Lista Maestra.')
        return redirect('documentos:formatos_lista')
    resumen = svc.sincronizar(registro, modo='combinar', usuario=request.user)
    if resumen.get('error'):
        messages.error(request, resumen['error'])
    else:
        _auditar_lista(request, registro, 'lista_maestra_sincronizacion', resumen)
        messages.success(
            request,
            f"Lista sincronizada: {resumen['agregados']} agregados, "
            f"{resumen['actualizados']} actualizados, "
            f"{resumen['eliminados']} retirados.",
        )
    return redirect('documentos:lista_maestra')


@login_required(login_url='/login/')
@require_POST
def lista_maestra_generar(request):
    from .services import for_006 as svc
    from .services import lista_maestra as acceso

    if not acceso.puede_ver_lista(request.user):
        messages.error(request, 'No tienes acceso a la Lista Maestra.')
        return redirect('documentos:mis_documentos')
    registro = _registro_lista(crear=True, usuario=request.user)
    if registro is None:
        messages.error(request, 'No se pudo abrir la Lista Maestra.')
        return redirect('documentos:formatos_lista')
    resultado = svc.generar_y_guardar(registro, forzar=True)
    if not resultado.get('success'):
        messages.error(
            request,
            resultado.get('error') or 'No se pudo generar la Lista Maestra.',
        )
    else:
        _auditar_lista(request, registro, 'lista_maestra_generacion')
        messages.success(request, 'XLSX/PDF actualizados.')
    return redirect('documentos:lista_maestra')


@login_required(login_url='/login/')
def lista_maestra_descargar(request, formato):
    from .services import for_006 as svc
    from .services import lista_maestra as acceso

    if not acceso.puede_ver_lista(request.user):
        raise Http404
    if formato not in ('xlsx', 'pdf'):
        raise Http404
    registro = _registro_lista(crear=True, usuario=request.user)
    if registro is None:
        raise Http404

    if formato == 'xlsx':
        if not registro.archivo:
            svc.generar_y_guardar(registro, forzar=True)
        if not registro.archivo:
            messages.error(request, 'No se pudo generar el XLSX.')
            return redirect('documentos:lista_maestra')
        return FileResponse(
            registro.archivo.open('rb'), as_attachment=True,
            filename=svc.nombre_archivo(registro, 'xlsx'),
        )

    if not registro.archivo_pdf:
        svc.generar_y_guardar(registro, forzar=True)
    if not registro.archivo_pdf:
        messages.error(
            request, 'No se pudo generar el PDF (LibreOffice).',
        )
        return redirect('documentos:lista_maestra')
    return FileResponse(
        registro.archivo_pdf.open('rb'), as_attachment=True,
        filename=svc.nombre_archivo(registro, 'pdf'),
    )


@login_required(login_url='/login/')
def lista_maestra_acceso(request):
    from django.contrib.auth.models import User
    from .services import lista_maestra as acceso

    if not acceso.puede_gestionar_acceso(request.user):
        messages.error(request, 'Solo el administrador gestiona estos accesos.')
        return redirect('documentos:lista_maestra')
    registro = _registro_lista(crear=True, usuario=request.user)

    if request.method == 'POST':
        usuario = User.objects.filter(
            pk=request.POST.get('usuario_id'), is_active=True,
        ).first()
        if usuario is None:
            messages.error(request, 'Selecciona un usuario válido.')
        else:
            acceso.otorgar(usuario, otorgado_por=request.user)
            _auditar_lista(request, registro, 'lista_maestra_acceso_otorgado', {
                'usuario_id': usuario.id, 'username': usuario.username,
            })
            messages.success(
                request,
                f'Acceso otorgado a {usuario.get_full_name() or usuario.username}.',
            )
        return redirect('documentos:lista_maestra_acceso')

    concesiones = acceso.concesiones()
    otorgados = set(concesiones.values_list('usuario_id', flat=True))
    usuarios = User.objects.filter(is_active=True).exclude(
        id__in=list(otorgados),
    ).order_by('first_name', 'last_name', 'username')
    return render(request, 'documentos/lista_maestra_acceso.html', {
        'registro': registro,
        'concesiones': concesiones,
        'usuarios': usuarios,
    })


@login_required(login_url='/login/')
@require_POST
def lista_maestra_revocar(request, usuario_id):
    from django.contrib.auth.models import User
    from .services import lista_maestra as acceso

    if not acceso.puede_gestionar_acceso(request.user):
        messages.error(request, 'Solo el administrador gestiona estos accesos.')
        return redirect('documentos:lista_maestra')
    usuario = get_object_or_404(User, pk=usuario_id)
    if acceso.revocar(usuario):
        registro = _registro_lista()
        _auditar_lista(request, registro, 'lista_maestra_acceso_revocado', {
            'usuario_id': usuario.id, 'username': usuario.username,
        })
        messages.success(
            request,
            f'Acceso revocado a {usuario.get_full_name() or usuario.username}.',
        )
    return redirect('documentos:lista_maestra_acceso')
