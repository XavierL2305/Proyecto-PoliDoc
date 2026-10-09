"""Caché de catálogos poco volátiles.

`TipoDocumento`, `Periodo` y `Departamento` se leen en casi todas las vistas
(listados, formularios, paneles). Tienen pocas filas y cambian muy rara vez,
así que se cachean con TTL y se invalidan en las señales de guardado/borrado.

Nota: se cachean listas de instancias (no querysets), por lo que solo son aptas
para renderizado/iteración, NO para asignarlas a `ModelChoiceField.queryset`.
"""

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

TIPOS_KEY = 'catalog:tipos_activos'
PERIODOS_KEY = 'catalog:periodos_activos'
DEPARTAMENTOS_KEY = 'catalog:departamentos_activos'
TTL = 600  # 10 minutos


# Redis puede estar caído (fallo de red, reinicio, entorno sin Docker). Estas
# operaciones no deben tumbar el comando o la vista que las invoca: se degradan
# con gracia (fail-open), igual que el throttle de login en
# autenticacion/views.py. Si la lectura falla se consulta la BD sin caché; si la
# invalidación falla no hay nada que invalidar de verdad y el TTL de 600s purga
# las entradas obsoletas cuando Redis vuelva.
def _cache_get_seguro(key, por_defecto):
    try:
        return cache.get(key, por_defecto)
    except Exception as exc:
        logger.warning(
            "Caché de catálogos no disponible al leer %s (%s); "
            "se consulta la BD sin caché.", key, exc
        )
        return por_defecto


def _cache_set_seguro(key, valor, ttl):
    try:
        cache.set(key, valor, ttl)
    except Exception as exc:
        logger.warning(
            "Caché de catálogos no disponible al escribir %s (%s); "
            "el resultado no queda cacheado.", key, exc
        )


def _cache_delete_seguro(key):
    try:
        cache.delete(key)
    except Exception as exc:
        logger.warning(
            "Caché de catálogos no disponible al invalidar %s (%s); "
            "se ignora (el TTL la purgará).", key, exc
        )


def tipos_activos():
    data = _cache_get_seguro(TIPOS_KEY, None)
    if data is None:
        from .models import TipoDocumento
        data = list(
            TipoDocumento.objects.filter(activo=True).exclude(key='').order_by('codigo')
        )
        _cache_set_seguro(TIPOS_KEY, data, TTL)
    return data


def periodos_activos():
    data = _cache_get_seguro(PERIODOS_KEY, None)
    if data is None:
        from .models import Periodo
        data = list(Periodo.objects.filter(activo=True))
        _cache_set_seguro(PERIODOS_KEY, data, TTL)
    return data


def departamentos_activos():
    data = _cache_get_seguro(DEPARTAMENTOS_KEY, None)
    if data is None:
        from .models import Departamento
        data = list(Departamento.objects.filter(activo=True).order_by('codigo'))
        _cache_set_seguro(DEPARTAMENTOS_KEY, data, TTL)
    return data


def invalidar_tipos():
    _cache_delete_seguro(TIPOS_KEY)


def invalidar_periodos():
    _cache_delete_seguro(PERIODOS_KEY)


def invalidar_departamentos():
    _cache_delete_seguro(DEPARTAMENTOS_KEY)
