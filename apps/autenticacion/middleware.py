"""Middleware de cédula obligatoria (activado por EXIGIR_CEDULA_PERFIL)."""

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

#: Prefijos exentos (además de cualquier ruta que contenga ``/api/``).
EXENTOS = (
    '/perfil/',
    '/logout/',
    '/login/',
    '/admin/',
    '/static/',
    '/media/',
)


class CedulaObligatoriaMiddleware:
    """Redirige al perfil si el usuario autenticado no tiene cédula.

    Por defecto está apagado (``EXIGIR_CEDULA_PERFIL=False``). Exime las rutas
    de autenticación/perfil, estáticos, admin y **todas** las APIs (incluidas
    las de ``/documentos/api/...``), y además a superusuarios y
    administradores de POLIDOC.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, 'EXIGIR_CEDULA_PERFIL', False):
            return self.get_response(request)

        if not request.user.is_authenticated:
            return self.get_response(request)

        if request.method == 'OPTIONS':
            return self.get_response(request)

        if request.user.is_superuser:
            return self.get_response(request)

        if '/api/' in request.path or request.path.startswith(EXENTOS):
            return self.get_response(request)

        perfil = getattr(request.user, 'perfil', None)
        if perfil is None or getattr(perfil, 'es_administrador', False):
            return self.get_response(request)

        if (perfil.cedula_identidad or '').strip():
            return self.get_response(request)

        messages.warning(
            request,
            'Completa tu cédula de identidad para continuar.',
        )
        return redirect(reverse('autenticacion:perfil'))
