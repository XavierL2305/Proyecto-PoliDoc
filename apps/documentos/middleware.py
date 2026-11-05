"""Middleware de caché con ámbito de petición.

Resetea al inicio de cada petición ciertas memorizaciones en memoria
(threading.local) que se usan mucho dentro de una misma vista — p. ej. las
delegaciones de jefatura que consultan permisos, visibilidad y plantillas.
Sin este reset el caché podría sobrevivir entre peticiones en un worker
reutilizado; con él, nunca se sirven datos obsoletos fuera de la petición.
"""

from .jefatura import iniciar_cache_peticion, limpiar_cache_peticion


class CachePeticionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        iniciar_cache_peticion()
        try:
            return self.get_response(request)
        finally:
            limpiar_cache_peticion()
