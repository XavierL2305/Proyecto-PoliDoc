import json
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .models import Notificacion


@login_required
def api_notificaciones(request):
    """API para obtener notificaciones del usuario"""
    notificaciones = Notificacion.objects.filter(usuario=request.user).select_related('departamento', 'documento')[:20]
    no_leidas = Notificacion.objects.filter(usuario=request.user, leida=False).count()
    user_departamento = getattr(request.user.perfil, 'departamento', None)

    data = {
        'notificaciones': [
            {
                'id': n.id,
                'titulo': n.titulo,
                'mensaje': n.mensaje,
                'tipo': n.tipo,
                'leida': n.leida,
                'url_accion': n.url_accion,
                'fecha_creacion': n.fecha_creacion.strftime('%d/%m/%Y %H:%M'),
                'es_de_otro_departamento': (
                    n.departamento is not None and
                    user_departamento is not None and
                    n.departamento != user_departamento and
                    not (n.documento and n.documento.es_publico)
                ),
            }
            for n in notificaciones
        ],
        'no_leidas': no_leidas,
    }

    return JsonResponse(data)


@login_required
@require_POST
def api_marcar_leida(request, notificacion_id):
    """Marcar una notificación como leída"""
    try:
        notificacion = Notificacion.objects.get(id=notificacion_id, usuario=request.user)
        notificacion.marcar_como_leida()
        return JsonResponse({'status': 'ok'})
    except Notificacion.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Notificación no encontrada'}, status=404)


@login_required
@require_POST
def api_marcar_todas_leidas(request):
    """Marcar todas las notificaciones como leídas"""
    Notificacion.objects.filter(usuario=request.user, leida=False).update(leida=True)
    return JsonResponse({'status': 'ok'})


def crear_notificacion(usuario, titulo, mensaje, tipo='sistema', url_accion=''):
    """Función helper para crear notificaciones"""
    Notificacion.objects.create(
        usuario=usuario,
        titulo=titulo,
        mensaje=mensaje,
        tipo=tipo,
        url_accion=url_accion
    )


@login_required
@require_POST
def api_responder_solicitud_descarga(request, solicitud_id):
    """Aprobar o rechazar solicitud de descarga desde notificación"""
    from documentos.models import SolicitudDescarga, AprobacionDescarga
    from django.utils import timezone
    from django.shortcuts import get_object_or_404

    solicitud = get_object_or_404(SolicitudDescarga, id=solicitud_id)
    perfil = request.user.perfil

    if not (perfil.es_jefe or perfil.es_administrador):
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    aprobacion = AprobacionDescarga.objects.filter(
        solicitud=solicitud, aprobador=request.user
    ).first()
    if not aprobacion:
        return JsonResponse({'error': 'No eres el aprobador de esta solicitud'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    accion = data.get('accion')
    observaciones = data.get('observaciones', '')

    if accion == 'aprobar':
        aprobacion.estado = 'aprobada'
        solicitud.estado = 'aprobada'
        tipo_notif = 'aprobacion_descarga'
        titulo_notif = 'Descarga aprobada'
        mensaje_notif = f'Tu solicitud de descarga para "{solicitud.documento_tipo}" ha sido aprobada.'
    else:
        aprobacion.estado = 'rechazada'
        solicitud.estado = 'rechazada'
        tipo_notif = 'rechazo_descarga'
        titulo_notif = 'Descarga rechazada'
        mensaje_notif = f'Tu solicitud de descarga para "{solicitud.documento_tipo}" ha sido rechazada. {observaciones}'

    aprobacion.observaciones = observaciones
    aprobacion.fecha_aprobacion = timezone.now()
    aprobacion.save()
    solicitud.fecha_resolucion = timezone.now()
    solicitud.save()

    Notificacion.objects.create(
        usuario=solicitud.solicitado_por,
        titulo=titulo_notif,
        mensaje=mensaje_notif,
        tipo=tipo_notif,
        url_accion=f'/documentos/{solicitud.documento_tipo}/{solicitud.documento_uuid}/descargar/',
        departamento=solicitud.documento.departamento if solicitud.documento else None
    )

    notificacion_id = data.get('notificacion_id')
    if notificacion_id:
        try:
            notif = Notificacion.objects.get(id=notificacion_id, usuario=request.user)
            notif.marcar_como_leida()
        except Notificacion.DoesNotExist:
            pass

    return JsonResponse({'success': True, 'mensaje': f'Solicitud {solicitud.estado}'})
