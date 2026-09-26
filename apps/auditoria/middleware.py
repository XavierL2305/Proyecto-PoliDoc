import ipaddress
import threading
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_thread_locals = threading.local()


def get_client_ip():
    """Obtener IP del request actual desde cualquier parte del código"""
    return getattr(_thread_locals, 'ip_address', None)


def _es_ip_valida(valor):
    try:
        ipaddress.ip_address(valor)
        return True
    except (ValueError, TypeError):
        return False


def resolver_ip_cliente(request):
    """Resuelve la IP real del cliente de forma segura.

    X-Forwarded-For/X-Real-IP los puede falsificar cualquiera, así que SOLO se
    tienen en cuenta cuando REMOTE_ADDR es un proxy inverso de confianza
    (settings.AUDIT_TRUSTED_PROXIES). En caso contrario se usa REMOTE_ADDR.

    Con proxy de confianza se aplica el algoritmo "primer IP no confiado desde
    la derecha" sobre X-Forwarded-For: un proxy correcto (p.ej. nginx con
    $proxy_add_x_forwarded_for) AÑADE la IP del cliente que realmente conectó
    al final de la lista, de modo que los valores de la izquierda pueden ser
    inventados por el cliente. Recorriendo de derecha a izquierda y saltando las
    IPs que son proxies de confianza se obtiene la primera IP que NO controla la
    infraestructura → el cliente real. Así una IP pre-añadida por el cliente no
    cuela en los logs de auditoría.
    """
    remote = request.META.get('REMOTE_ADDR')
    trusted = set(getattr(settings, 'AUDIT_TRUSTED_PROXIES', []) or [])

    if remote and remote in trusted:
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded:
            for candidato in reversed([p.strip() for p in x_forwarded.split(',')]):
                if not _es_ip_valida(candidato):
                    continue
                if candidato not in trusted:
                    return candidato
        x_real_ip = request.META.get('HTTP_X_REAL_IP')
        if x_real_ip and _es_ip_valida(x_real_ip.strip()):
            return x_real_ip.strip()

    return remote


class AuditIPMiddleware:
    """
    Middleware para capturar la IP del cliente automáticamente.
    ISO 9001:9.2 - Trazabilidad de acciones en el sistema.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip = resolver_ip_cliente(request)
        _thread_locals.ip_address = ip
        logger.debug(f"[AuditIP] IP capturada: {ip}")

        response = self.get_response(request)
        return response
