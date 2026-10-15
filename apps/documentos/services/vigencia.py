"""Conteos de estado de vigencia por conjunto de departamentos.

El panel de vigencia se usa en dos vistas: la página de Documentos por
Departamento (filtros) y el dashboard del jefe (acceso directo). El cálculo
vive aquí para que ambas vistas compartan la misma fuente de verdad sin
importarse entre sí (evita imports circulares entre `autenticacion.views` y
`documentos.views`).
"""
from datetime import date, timedelta

from django.db.models import Count, Q

from ..models import Documento


def calcular_conteos_vigencia(departamentos, clasificacion, usuario):
    """Conteos de documentos por estado de vigencia para `departamentos`.

    - `departamentos`: iterable de `Departamento` que delimitan el alcance
      (el jefe pasa el suyo; el administrador, todos).
    - `clasificacion`: valor de `Documento.clasificacion` para restringir, o
      cadena vacía para todas.
    - `usuario`: visibilidad (respeta `q_documentos_visibles`).

    Devuelve un dict con las claves `vencidos`, `vence_pronto`, `vigentes` y
    `sin_vigencia`.
    """
    from ..permissions import q_documentos_visibles

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
    )
    # Un único aggregate con filtros condicionales en vez de 4 consultas.
    # `distinct=True` compensa el JOIN con departamentos_afectados.
    return base.aggregate(
        vencidos=Count(
            'id',
            filter=Q(fecha_vencimiento__isnull=False, fecha_vencimiento__lte=hoy),
            distinct=True,
        ),
        vence_pronto=Count(
            'id',
            filter=Q(fecha_vencimiento__gt=hoy, fecha_vencimiento__lte=hoy + timedelta(days=7)),
            distinct=True,
        ),
        vigentes=Count(
            'id',
            filter=Q(fecha_vencimiento__gt=hoy + timedelta(days=7)),
            distinct=True,
        ),
        sin_vigencia=Count(
            'id',
            filter=Q(fecha_vencimiento__isnull=True),
            distinct=True,
        ),
    )
