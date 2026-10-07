"""Caché de catálogos poco volátiles.

`TipoDocumento`, `Periodo` y `Departamento` se leen en casi todas las vistas
(listados, formularios, paneles). Tienen pocas filas y cambian muy rara vez,
así que se cachean con TTL y se invalidan en las señales de guardado/borrado.

Nota: se cachean listas de instancias (no querysets), por lo que solo son aptas
para renderizado/iteración, NO para asignarlas a `ModelChoiceField.queryset`.
"""

from django.core.cache import cache

TIPOS_KEY = 'catalog:tipos_activos'
PERIODOS_KEY = 'catalog:periodos_activos'
DEPARTAMENTOS_KEY = 'catalog:departamentos_activos'
TTL = 600  # 10 minutos


def tipos_activos():
    data = cache.get(TIPOS_KEY)
    if data is None:
        from .models import TipoDocumento
        data = list(
            TipoDocumento.objects.filter(activo=True).exclude(key='').order_by('codigo')
        )
        cache.set(TIPOS_KEY, data, TTL)
    return data


def periodos_activos():
    data = cache.get(PERIODOS_KEY)
    if data is None:
        from .models import Periodo
        data = list(Periodo.objects.filter(activo=True))
        cache.set(PERIODOS_KEY, data, TTL)
    return data


def departamentos_activos():
    data = cache.get(DEPARTAMENTOS_KEY)
    if data is None:
        from .models import Departamento
        data = list(Departamento.objects.filter(activo=True).order_by('codigo'))
        cache.set(DEPARTAMENTOS_KEY, data, TTL)
    return data


def invalidar_tipos():
    cache.delete(TIPOS_KEY)


def invalidar_periodos():
    cache.delete(PERIODOS_KEY)


def invalidar_departamentos():
    cache.delete(DEPARTAMENTOS_KEY)
