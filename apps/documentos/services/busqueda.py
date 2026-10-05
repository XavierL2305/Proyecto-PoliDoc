"""Búsqueda de documentos visibles (navbar y página de resultados).

Centraliza el matching que antes vivía en ``api.buscar_sugerencias``: nombre,
código, propietario, documentos relacionados y referencias pendientes, siempre
filtrado por ``q_documentos_visibles`` (permisos por rol/clasificación).
"""

from django.db.models import Q


def buscar_documentos_visibles(usuario, q):
    """``(queryset, target_ids)`` de documentos visibles que casan con ``q``.

    ``target_ids`` son los ids que casan por nombre/código; se usan para marcar
    los documentos encontrados a través de un relacionado.
    """
    from ..models import Documento
    from ..permissions import q_documentos_visibles

    permiso = q_documentos_visibles(usuario)

    target_ids = list(
        Documento.objects.filter(
            permiso,
            Q(nombre__icontains=q) | Q(codigo__icontains=q),
        ).values_list('id', flat=True)
    )

    propios = (
        Q(nombre__icontains=q)
        | Q(codigo__icontains=q)
        | Q(propietario__first_name__icontains=q)
        | Q(propietario__last_name__icontains=q)
        | Q(propietario__username__icontains=q)
    )
    relacionados = Q(documentos_relacionados__in=target_ids)
    referencias = Q(referencias_salientes__codigo_destino__icontains=q)

    queryset = (
        Documento.objects.filter(permiso & (propios | relacionados | referencias))
        .select_related('departamento', 'propietario', 'tipo')
        .distinct()
    )
    return queryset, target_ids
