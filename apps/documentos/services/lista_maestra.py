"""Acceso a la vista exclusiva de la Lista Maestra (SGC-FOR-006).

Envoltorio fino sobre el servicio genérico :mod:`services.acceso_formato`,
fijado al formato ``SGC-FOR-006``. El acceso se concede **por persona** desde
la sesión del administrador; ``editar`` permite hacer todo y ``ver`` solo
consultar/descargar.
"""
from . import acceso_formato
from .acceso_formato import NIVEL_EDITAR, NIVEL_VER
from .for_006 import FORMATO_FOR_006, formato_for_006


def es_administrador(usuario):
    return acceso_formato.es_administrador(usuario)


def puede_ver_lista(usuario):
    """Administrador o persona con concesión activa (ver)."""
    return acceso_formato.puede_ver_codigo(usuario, FORMATO_FOR_006)


def puede_editar_lista(usuario):
    """Administrador o concesión activa con nivel ``editar``."""
    return acceso_formato.puede_editar_codigo(usuario, FORMATO_FOR_006)


def puede_gestionar_acceso(usuario):
    """Solo el administrador administra las concesiones."""
    return acceso_formato.es_administrador(usuario)


def concesiones(solo_activas=True):
    return acceso_formato.concesiones(
        formato_for_006(), solo_activas=solo_activas,
    )


def usuarios_con_acceso():
    return acceso_formato.usuarios_con_acceso(formato_for_006())


def otorgar(usuario, otorgado_por=None, nivel=NIVEL_EDITAR):
    return acceso_formato.otorgar(
        usuario, formato_for_006(), nivel=nivel, otorgado_por=otorgado_por,
    )


def revocar(usuario):
    return acceso_formato.revocar(usuario, formato_for_006())


def context_menu(usuario):
    return {'nav_lista_maestra': puede_ver_lista(usuario)}
