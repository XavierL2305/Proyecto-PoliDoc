"""Acceso a la vista exclusiva de la Lista Maestra (SGC-FOR-006).

El acceso se concede **por persona** desde la sesión del administrador. Quien
tenga una concesión activa puede hacer todo en la vista (ver, editar filas,
sincronizar, regenerar y descargar). El administrador siempre tiene acceso.
"""
from ..models import AccesoListaMaestra
from .for_006 import formato_for_006


def es_administrador(usuario):
    perfil = getattr(usuario, 'perfil', None)
    return bool(perfil and perfil.es_administrador)


def _concesion_activa(usuario, formato):
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        return None
    if formato is None:
        return None
    return AccesoListaMaestra.objects.filter(
        formato=formato, usuario=usuario, activo=True,
    ).first()


def puede_ver_lista(usuario):
    """Administrador o persona con concesión activa."""
    if es_administrador(usuario):
        return True
    return _concesion_activa(usuario, formato_for_006()) is not None


def puede_gestionar_acceso(usuario):
    """Solo el administrador administra las concesiones."""
    return es_administrador(usuario)


def concesiones(formato=None, solo_activas=True):
    formato = formato or formato_for_006()
    if formato is None:
        return AccesoListaMaestra.objects.none()
    qs = AccesoListaMaestra.objects.filter(formato=formato).select_related(
        'usuario', 'creado_por',
    )
    if solo_activas:
        qs = qs.filter(activo=True)
    return qs


def usuarios_con_acceso(formato=None):
    """Usuarios con concesión activa (sin administradores)."""
    from django.contrib.auth.models import User

    ids = concesiones(formato).values_list('usuario_id', flat=True)
    return User.objects.filter(id__in=list(ids), is_active=True)


def otorgar(usuario, otorgado_por=None, formato=None):
    """Otorga (o reactiva) el acceso de ``usuario`` a la lista."""
    formato = formato or formato_for_006()
    if formato is None or usuario is None:
        return None
    concesion, creada = AccesoListaMaestra.objects.get_or_create(
        formato=formato, usuario=usuario,
        defaults={'activo': True, 'creado_por': otorgado_por},
    )
    if not creada and not concesion.activo:
        concesion.activo = True
        concesion.save(update_fields=['activo'])
    return concesion


def revocar(usuario, formato=None):
    """Revoca el acceso (deja la fila inactiva para conservar la traza)."""
    formato = formato or formato_for_006()
    if formato is None or usuario is None:
        return 0
    return AccesoListaMaestra.objects.filter(
        formato=formato, usuario=usuario, activo=True,
    ).update(activo=False)


def context_menu(usuario):
    """Bandera para mostrar la entrada del menú."""
    return {'nav_lista_maestra': puede_ver_lista(usuario)}
