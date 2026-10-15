"""Jefatura de departamento: natural o delegada temporalmente (vacaciones).

Un usuario **ejerce jefatura** sobre un departamento si:

- es jefe natural (``PerfilUsuario.rol == 'jefe_departamento'`` con ese
  departamento), o
- tiene una **delegación vigente** de ese departamento (``DelegacionJefatura``
  activa cuya vigencia contiene hoy).

Toda comprobación departamental de "es jefe" del sistema debe pasar por aquí
para que la delegación surta efecto. Los permisos **globales** del sistema
(configuración de tipos/períodos/roles/auditoría) NO se delegan: siguen
exigiendo jefe natural o administrador.
"""

from django.utils import timezone

from .models import DelegacionJefatura

__all__ = (
    'delegaciones_vigentes',
    'delegacion_activa',
    'es_delegado_vigente',
    'departamentos_jefados',
    'es_jefe_efectivo',
    'aprobadores_que_repr',
)


def delegaciones_vigentes(usuario):
    """Delegaciones que ``usuario`` está ejerciendo hoy (queryset)."""
    hoy = timezone.now().date()
    return DelegacionJefatura.objects.filter(
        delegado=usuario, activa=True,
        fecha_inicio__lte=hoy, fecha_fin__gte=hoy,
    ).select_related('departamento', 'jefe')


def delegacion_activa(usuario):
    """La delegación vigente que ejerce ``usuario``, o ``None``."""
    return delegaciones_vigentes(usuario).first()


def es_delegado_vigente(usuario):
    """True si ``usuario`` ejerce al menos una jefatura delegada hoy."""
    return delegaciones_vigentes(usuario).exists()


def departamentos_jefados(usuario):
    """Set de IDs de departamentos sobre los que ``usuario`` ejerce jefatura."""
    perfil = getattr(usuario, 'perfil', None)
    ids = set()
    if perfil is None:
        return ids
    if perfil.es_jefe and perfil.departamento_id:
        ids.add(perfil.departamento_id)
    ids.update(
        delegaciones_vigentes(usuario).values_list('departamento_id', flat=True)
    )
    return ids


def es_jefe_efectivo(usuario, departamento):
    """True si ``usuario`` es jefe natural **o delegado vigente** del
    departamento indicado.

    ``departamento`` admite instancia o PK. Devuelve False si no hay
    departamento o si el usuario no tiene perfil.
    """
    if departamento is None:
        return False
    perfil = getattr(usuario, 'perfil', None)
    if perfil is None:
        return False
    depto_id = getattr(departamento, 'id', departamento)
    if perfil.es_jefe and perfil.departamento_id == depto_id:
        return True
    hoy = timezone.now().date()
    return DelegacionJefatura.objects.filter(
        delegado=usuario, departamento_id=depto_id, activa=True,
        fecha_inicio__lte=hoy, fecha_fin__gte=hoy,
    ).exists()


def aprobadores_que_repr(usuario):
    """IDs de usuarios cuyas solicitudes aprobadas pendientes puede resolver
    ``usuario``: él mismo + los jefes que le delegaron vigente.

    Las solicitudes guardan ``aprobador`` = jefe natural, así que sin esto el
    delegado vería la solicitud en su bandeja pero no podría resolverla.
    """
    ids = [usuario.id]
    ids.extend(delegaciones_vigentes(usuario).values_list('jefe_id', flat=True))
    return ids
