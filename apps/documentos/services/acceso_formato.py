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


# ---------------------------------------------------------------------------
# Extensión homóloga para TipoDocumento (Organigrama y Mapa de Procesos)
# ---------------------------------------------------------------------------

def _tipo_key(tipo_o_key):
    if tipo_o_key is None:
        return ''
    if isinstance(tipo_o_key, str):
        return tipo_o_key.strip()
    return getattr(tipo_o_key, 'key', '') or ''


def _resolver_tipo(tipo_o_key):
    from ..models import TipoDocumento

    if isinstance(tipo_o_key, TipoDocumento):
        return tipo_o_key
    key = _tipo_key(tipo_o_key)
    if not key:
        return None
    return TipoDocumento.objects.filter(key=key).first()


def concesion_tipo(usuario, tipo_o_key):
    """Concesión activa de ``usuario`` para ``tipo_o_key`` (o ``None``)."""
    key = _tipo_key(tipo_o_key)
    if not _autenticado(usuario) or not key:
        return None
    return AccesoFormato.objects.filter(
        tipo_documento_id=key, usuario=usuario, activo=True,
    ).first()


def puede_editar_tipo(usuario, tipo_o_key):
    """True si ``usuario`` es administrador o tiene concesión activa de edición."""
    key = _tipo_key(tipo_o_key)
    if not _autenticado(usuario) or not key:
        return False
    if es_administrador(usuario):
        return True
    return AccesoFormato.objects.filter(
        tipo_documento_id=key,
        tipo_documento__activo=True,
        usuario=usuario,
        activo=True,
        nivel=NIVEL_EDITAR,
    ).exists()


def concesiones_tipo(tipo_o_key, solo_activas=True):
    """Concesiones asociadas a un ``TipoDocumento`` (ORG / MAP)."""
    key = _tipo_key(tipo_o_key)
    if not key:
        return AccesoFormato.objects.none()
    qs = AccesoFormato.objects.filter(tipo_documento_id=key).select_related(
        'usuario', 'usuario__perfil', 'usuario__perfil__departamento',
        'creado_por', 'tipo_documento',
    )
    if solo_activas:
        qs = qs.filter(activo=True)
    return qs


def otorgar_tipo(usuario, tipo_o_key, nivel=NIVEL_EDITAR, otorgado_por=None):
    """Otorga (o reactiva/actualiza) el acceso de ``usuario`` a un ``TipoDocumento``."""
    tipo_obj = _resolver_tipo(tipo_o_key)
    if tipo_obj is None or usuario is None:
        return None
    nivel = _normalizar_nivel(nivel)
    concesion_actual, creada = AccesoFormato.objects.get_or_create(
        tipo_documento=tipo_obj, usuario=usuario,
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
        if otorgado_por is not None and concesion_actual.creado_por_id != otorgado_por.id:
            concesion_actual.creado_por = otorgado_por
            campos.append('creado_por')
        if campos:
            concesion_actual.save(update_fields=campos)
    return concesion_actual


def revocar_tipo(usuario, tipo_o_key):
    """Revoca el acceso sobre un ``TipoDocumento`` conservando la traza."""
    key = _tipo_key(tipo_o_key)
    if not key or usuario is None:
        return 0
    return AccesoFormato.objects.filter(
        tipo_documento_id=key, usuario=usuario, activo=True,
    ).update(activo=False)
