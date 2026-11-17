"""Acceso por persona a las vistas exclusivas de un formato (registro vivo).

Un ``AccesoFormato`` activo concede **ver**; si además su ``nivel`` es
``editar``, permite modificar la vista (agregar/quitar filas, marcas,
sincronizar, regenerar). El administrador siempre tiene acceso total.

Las consultas por código (``puede_ver_codigo``) resuelven en **una sola
consulta** (join a ``Formato``) para no penalizar el context processor.
"""
from ..models import AccesoFormato

NIVEL_VER = 'ver'
NIVEL_EDITAR = 'editar'
NIVELES = (NIVEL_VER, NIVEL_EDITAR)


def es_administrador(usuario):
    perfil = getattr(usuario, 'perfil', None)
    return bool(perfil and perfil.es_administrador)


def _autenticado(usuario):
    return usuario is not None and getattr(usuario, 'is_authenticated', False)


def concesion(usuario, formato):
    """Concesión activa de ``usuario`` para ``formato`` (o ``None``)."""
    if not _autenticado(usuario) or formato is None:
        return None
    return AccesoFormato.objects.filter(
        formato=formato, usuario=usuario, activo=True,
    ).first()


def puede_ver(usuario, formato):
    if not _autenticado(usuario):
        return False
    if es_administrador(usuario):
        return True
    return concesion(usuario, formato) is not None


def puede_editar(usuario, formato):
    if not _autenticado(usuario):
        return False
    if es_administrador(usuario):
        return True
    concesion_actual = concesion(usuario, formato)
    return bool(concesion_actual and concesion_actual.nivel == NIVEL_EDITAR)


def puede_ver_codigo(usuario, codigo):
    """Ver por código de formato en una sola consulta."""
    if not _autenticado(usuario):
        return False
    if es_administrador(usuario):
        return True
    return AccesoFormato.objects.filter(
        formato__codigo=codigo,
        formato__activo=True,
        usuario=usuario,
        activo=True,
    ).exists()


def puede_editar_codigo(usuario, codigo):
    """Editar por código de formato en una sola consulta."""
    if not _autenticado(usuario):
        return False
    if es_administrador(usuario):
        return True
    return AccesoFormato.objects.filter(
        formato__codigo=codigo,
        formato__activo=True,
        usuario=usuario,
        activo=True,
        nivel=NIVEL_EDITAR,
    ).exists()


def concesiones(formato, solo_activas=True):
    if formato is None:
        return AccesoFormato.objects.none()
    qs = AccesoFormato.objects.filter(formato=formato).select_related(
        'usuario', 'creado_por',
    )
    if solo_activas:
        qs = qs.filter(activo=True)
    return qs


def usuarios_con_acceso(formato):
    from django.contrib.auth.models import User

    ids = concesiones(formato).values_list('usuario_id', flat=True)
    return User.objects.filter(id__in=list(ids), is_active=True)


def _normalizar_nivel(nivel):
    return nivel if nivel in NIVELES else NIVEL_EDITAR


def otorgar(usuario, formato, nivel=NIVEL_EDITAR, otorgado_por=None):
    """Otorga (o reactiva/actualiza) el acceso de ``usuario`` al formato."""
    if formato is None or usuario is None:
        return None
    nivel = _normalizar_nivel(nivel)
    concesion_actual, creada = AccesoFormato.objects.get_or_create(
        formato=formato, usuario=usuario,
        defaults={
            'activo': True, 'nivel': nivel, 'creado_por': otorgado_por,
        },
    )
    if not creada:
        campos = []
        if not concesion_actual.activo:
            concesion_actual.activo = True
            campos.append('activo')
        if concesion_actual.nivel != nivel:
            concesion_actual.nivel = nivel
            campos.append('nivel')
        if campos:
            concesion_actual.save(update_fields=campos)
    return concesion_actual


def revocar(usuario, formato):
    """Revoca el acceso (deja la fila inactiva para conservar la traza)."""
    if formato is None or usuario is None:
        return 0
    return AccesoFormato.objects.filter(
        formato=formato, usuario=usuario, activo=True,
    ).update(activo=False)
