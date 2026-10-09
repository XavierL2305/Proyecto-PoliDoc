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
                    not (n.documento and n.documento.clasificacion == 'publico')
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
