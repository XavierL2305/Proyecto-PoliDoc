import threading
import logging

logger = logging.getLogger(__name__)

_thread_locals = threading.local()


def get_client_ip():
    """Obtener IP del request actual desde cualquier parte del código"""
    return getattr(_thread_locals, 'ip_address', None)


class AuditIPMiddleware:
    """
    Middleware para capturar la IP del cliente automáticamente.
    ISO 9001:9.2 - Trazabilidad de acciones en el sistema.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        x_real_ip = request.META.get('HTTP_X_REAL_IP')
        
        if x_forwarded:
            ip = x_forwarded.split(',')[0].strip()
        elif x_real_ip:
            ip = x_real_ip
        else:
            ip = request.META.get('REMOTE_ADDR')
        
        _thread_locals.ip_address = ip
        logger.debug(f"[AuditIP] IP capturada: {ip} | X-Forwarded-For: {x_forwarded} | X-Real-IP: {x_real_ip}")

        response = self.get_response(request)
        return response
