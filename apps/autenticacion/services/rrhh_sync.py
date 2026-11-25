"""Sincronización con RRHH (SQL Server): cédula, cargo y nombres.

Reglas:
* La cédula se guarda como dígitos canónicos (sin ``V-``/``E-``/puntos) y se
  compara contra ``tbl_emp_temporal.cedula`` (varchar numérico) con
  ``a.cedula = %s``, para aprovechar el índice.
* Solo empleados activos (``a.activo = 1``).
* ``pymssql`` se importa de forma **perezosa**: el módulo debe poder
  importarse sin el driver (tests, CI, entornos sin RRHH).
"""

import logging
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class RRHHConexionError(Exception):
    """SQL Server de RRHH no disponible (red, timeout o credenciales)."""


def normalizar_cedula(valor):
    """Deja solo dígitos: ``V-12.345.678`` → ``12345678``."""
    return re.sub(r'\D', '', str(valor or ''))


def _clave(cedula):
    """Clave de match: dígitos sin ceros iniciales (tolerante a legados)."""
    return normalizar_cedula(cedula).lstrip('0')


def validar_y_limpiar_cedula(valor):
    """Normaliza y valida el rango de una cédula venezolana.

    Devuelve los dígitos canónicos (sin ceros iniciales).
    """
    digitos = normalizar_cedula(valor)
    if not digitos:
        raise ValueError('La cédula debe contener números.')
    numero = int(digitos)
    if numero <= 999_999 or numero > 999_999_999:
        raise ValueError('La cédula no tiene un formato válido.')
    return str(numero)


def enmascarar_cedula(valor):
    """``***5678``: últimos 4 dígitos, para auditoría sin guardar PII completa."""
    digitos = normalizar_cedula(valor)
    return f'***{digitos[-4:]}' if digitos else ''


def rrhh_habilitado():
    """La integración solo se usa si hay host y usuario configurados."""
    return bool(
        getattr(settings, 'RRHH_SQLSERVER_HOST', '')
        and getattr(settings, 'RRHH_SQLSERVER_USER', '')
    )


def _conectar():
    import pymssql

    return pymssql.connect(
        server=settings.RRHH_SQLSERVER_HOST,
        port=settings.RRHH_SQLSERVER_PORT,
        user=settings.RRHH_SQLSERVER_USER,
        password=settings.RRHH_SQLSERVER_PASSWORD,
        database=settings.RRHH_SQLSERVER_DB,
        login_timeout=2,
        timeout=settings.RRHH_SQLSERVER_TIMEOUT,
        as_dict=True,
        charset='UTF-8',
    )


_CONSULTA_EMPLEADO = """
SELECT TOP 1
    LTRIM(RTRIM(a.nombre)) AS nombre,
    LTRIM(RTRIM(a.apellido)) AS apellido,
    LTRIM(RTRIM(a.cedula)) AS cedula,
    LTRIM(RTRIM(b.nombre)) AS cargo,
    LTRIM(RTRIM(c.nombre)) AS departamento
FROM tbl_emp_temporal AS a
INNER JOIN cargos AS b
    ON b.codigo_interno = a.cargo_id
   AND a.departamento_id = b.departamento_id
INNER JOIN departamentos AS c
    ON a.departamento_id = c.id
WHERE a.activo = 1 AND a.cedula = %s
"""

_CONSULTA_MASIVA = """
SELECT
    LTRIM(RTRIM(a.cedula)) AS cedula,
    LTRIM(RTRIM(a.nombre)) AS nombre,
    LTRIM(RTRIM(a.apellido)) AS apellido,
    LTRIM(RTRIM(b.nombre)) AS cargo,
    LTRIM(RTRIM(c.nombre)) AS departamento
FROM tbl_emp_temporal AS a
INNER JOIN cargos AS b
    ON b.codigo_interno = a.cargo_id
   AND a.departamento_id = b.departamento_id
INNER JOIN departamentos AS c
    ON a.departamento_id = c.id
WHERE a.activo = 1
"""


def _texto(valor):
    return str(valor or '').strip()


def _leer(consulta, parametros=None):
    """Ejecuta una consulta y devuelve las filas como dicts."""
    conn = None
    try:
        conn = _conectar()
        with conn.cursor() as cursor:
            cursor.execute(consulta, parametros or ())
            return cursor.fetchall()
    except Exception as exc:  # noqa: BLE001 - el driver lanza varios tipos
        logger.warning('RRHH: error de conexión/consulta: %s', exc)
        raise RRHHConexionError('No se pudo consultar RRHH.') from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def consultar_empleado_por_cedula(cedula):
    """Devuelve los datos del empleado activo o ``None`` si no existe."""
    if not rrhh_habilitado():
        return None
    digitos = normalizar_cedula(cedula)
    if not digitos:
        return None

    filas = _leer(_CONSULTA_EMPLEADO, (digitos,))
    if not filas:
        return None
    fila = filas[0]
    return {
        'nombre': _texto(fila.get('nombre')),
        'apellido': _texto(fila.get('apellido')),
        'cedula': _texto(fila.get('cedula')),
        'cargo': _texto(fila.get('cargo')),
        'departamento': _texto(fila.get('departamento')),
    }


def _mapa_rrhh():
    """Un solo SELECT masivo → ``{clave_cedula: datos}``."""
    mapa = {}
    for fila in _leer(_CONSULTA_MASIVA):
        clave = _clave(fila.get('cedula'))
        if not clave:
            continue
        mapa[clave] = {
            'nombre': _texto(fila.get('nombre')),
            'apellido': _texto(fila.get('apellido')),
            'cargo': _texto(fila.get('cargo')),
            'departamento': _texto(fila.get('departamento')),
        }
    return mapa


def sincronizar_todo_rrhh(dry_run=False):
    """Actualiza cargo/departamento/nombres de los perfiles con cédula.

    Usa ``bulk_update`` (sin señales) para no disparar la cadena de
    reasignación de aprobadores en cada perfil.
    """
    from ..models import PerfilUsuario

    resumen = {
        'dry_run': dry_run,
        'total_rrhh': 0,
        'perfiles_actualizados': 0,
        'nombres_actualizados': 0,
        'sin_match': 0,
        'sin_cedula': 0,
        'errores': 0,
        'mensaje': '',
    }
    if not rrhh_habilitado():
        resumen['errores'] = 1
        resumen['mensaje'] = 'RRHH no configurado.'
        return resumen

    try:
        mapa = _mapa_rrhh()
    except RRHHConexionError as exc:
        resumen['errores'] = 1
        resumen['mensaje'] = str(exc)
        return resumen

    resumen['total_rrhh'] = len(mapa)
    ahora = timezone.now()
    perfiles = list(
        PerfilUsuario.objects
        .select_related('usuario')
    )
    cambios_perfil = []
    cambios_user = []
    for perfil in perfiles:
        clave = _clave(perfil.cedula_identidad)
        if not clave:
            resumen['sin_cedula'] += 1
            continue
        datos = mapa.get(clave)
        if datos is None:
            resumen['sin_match'] += 1
            continue

        if perfil.cedula_identidad != clave:
            # Se guarda la forma canónica (dígitos, sin ceros iniciales) para
            # que coincida con RRHH en futuras consultas.
            perfil.cedula_identidad = clave
        perfil.cargo = datos['cargo']
        perfil.departamento_rrhh = datos['departamento']
        perfil.cedula_verificada_rrhh = True
        perfil.ultimo_sync_rrhh = ahora
        cambios_perfil.append(perfil)

        user = perfil.usuario
        if (
            user.first_name != datos['nombre']
            or user.last_name != datos['apellido']
        ):
            user.first_name = datos['nombre']
            user.last_name = datos['apellido']
            cambios_user.append(user)

    if not dry_run and cambios_perfil:
        with transaction.atomic():
            PerfilUsuario.objects.bulk_update(
                cambios_perfil,
                [
                    'cedula_identidad', 'cargo', 'departamento_rrhh',
                    'cedula_verificada_rrhh', 'ultimo_sync_rrhh',
                ],
                batch_size=500,
            )
            if cambios_user:
                from django.contrib.auth.models import User

                User.objects.bulk_update(
                    cambios_user, ['first_name', 'last_name'],
                    batch_size=500,
                )

    resumen['perfiles_actualizados'] = len(cambios_perfil)
    resumen['nombres_actualizados'] = len(cambios_user)
    return resumen


def tiene_datos_rrhh(perfil) -> bool:
    """True si el perfil conserva cédula, cargo, departamento RRHH o verificación."""
    if perfil is None:
        return False
    return bool(
        (perfil.cedula_identidad or '').strip()
        or (perfil.cargo or '').strip()
        or (perfil.departamento_rrhh or '').strip()
        or perfil.cedula_verificada_rrhh
        or perfil.ultimo_sync_rrhh is not None
    )


def desvincular_datos_rrhh(
    perfil,
    *,
    actor,
    origen: str = 'gestion_usuarios',
    limpiar_nombres: bool = False,
) -> dict:
    """Revierte la cédula, cargo, dpto RRHH y verificación de un perfil.

    Usa ``QuerySet.update`` en ``transaction.atomic`` para no disparar señales
    no relacionadas en ``PerfilUsuario``. Deja traza en auditoría ISO 15489.
    """
    from django.contrib.auth.models import User

    from ..models import PerfilUsuario

    if not tiene_datos_rrhh(perfil):
        return {'modificado': False}

    cedula_anterior = (perfil.cedula_identidad or '').strip()
    cargo_anterior = (perfil.cargo or '').strip()
    depto_anterior = (perfil.departamento_rrhh or '').strip()

    with transaction.atomic():
        PerfilUsuario.objects.filter(pk=perfil.pk).update(
            cedula_identidad='',
            cargo='',
            departamento_rrhh='',
            cedula_verificada_rrhh=False,
            ultimo_sync_rrhh=None,
        )
        perfil.cedula_identidad = ''
        perfil.cargo = ''
        perfil.departamento_rrhh = ''
        perfil.cedula_verificada_rrhh = False
        perfil.ultimo_sync_rrhh = None

        if limpiar_nombres:
            User.objects.filter(pk=perfil.usuario_id).update(
                first_name='',
                last_name='',
            )
            perfil.usuario.first_name = ''
            perfil.usuario.last_name = ''

    try:
        from auditoria.services import registrar_log_iso

        registrar_log_iso(
            usuario=actor,
            accion_tipo='edicion',
            accion_categoria='usuario',
            recurso_tipo='usuario',
            recurso_id=perfil.pk,
            recurso_nombre=perfil.usuario.username,
            estado_anterior=enmascarar_cedula(cedula_anterior),
            estado_posterior='',
            detalles={
                'campo': 'cedula_identidad',
                'accion': 'desvincular_rrhh',
                'origen': origen,
                'cargo_anterior': cargo_anterior,
                'departamento_rrhh_anterior': depto_anterior,
                'limpiar_nombres': bool(limpiar_nombres),
            },
            resultado_mensaje='Datos de RRHH desvinculados por administrador',
        )
    except Exception:  # noqa: BLE001 - la auditoría no rompe el panel admin
        logger.warning(
            'No se pudo auditar la desvinculación RRHH del perfil %s',
            perfil.pk,
            exc_info=True,
        )

    return {'modificado': True, 'cedula_anterior': cedula_anterior}


def vincular_datos_rrhh(
    perfil,
    *,
    cedula: str,
    datos: dict | None,
    verificado: bool,
    actor,
    origen: str = 'detalle_usuario',
) -> dict:
    """Asigna cédula y (si hay match RRHH) cargo/departamento/nombres al perfil.

    Usa ``QuerySet.update`` en ``transaction.atomic`` para evitar señales no
    relacionadas y deja traza en auditoría ISO 15489.
    """
    from django.contrib.auth.models import User

    from ..models import PerfilUsuario

    cedula_anterior = (perfil.cedula_identidad or '').strip()
    cambios_perfil = {'cedula_identidad': cedula}

    if datos:
        cambios_perfil.update({
            'cargo': datos.get('cargo', ''),
            'departamento_rrhh': datos.get('departamento', ''),
            'cedula_verificada_rrhh': bool(verificado),
            'ultimo_sync_rrhh': timezone.now(),
        })
    else:
        cambios_perfil['cedula_verificada_rrhh'] = False
        if cedula_anterior != cedula:
            cambios_perfil.update({
                'cargo': '',
                'departamento_rrhh': '',
                'ultimo_sync_rrhh': None,
            })

    with transaction.atomic():
        PerfilUsuario.objects.filter(pk=perfil.pk).update(**cambios_perfil)
        for campo, valor in cambios_perfil.items():
            setattr(perfil, campo, valor)

        if datos:
            cambios_user = {}
            if datos.get('nombre'):
                cambios_user['first_name'] = datos['nombre']
            if datos.get('apellido'):
                cambios_user['last_name'] = datos['apellido']
            if cambios_user:
                User.objects.filter(pk=perfil.usuario_id).update(**cambios_user)
                for campo, valor in cambios_user.items():
                    setattr(perfil.usuario, campo, valor)

    try:
        from auditoria.services import registrar_log_iso

        registrar_log_iso(
            usuario=actor,
            accion_tipo='edicion',
            accion_categoria='usuario',
            recurso_tipo='usuario',
            recurso_id=perfil.pk,
            recurso_nombre=perfil.usuario.username,
            estado_anterior=enmascarar_cedula(cedula_anterior),
            estado_posterior=enmascarar_cedula(cedula),
            detalles={
                'campo': 'cedula_identidad',
                'accion': 'vincular_rrhh',
                'origen': origen,
                'cargo': perfil.cargo,
                'departamento_rrhh': perfil.departamento_rrhh,
                'cedula_verificada': perfil.cedula_verificada_rrhh,
            },
            resultado_mensaje='Cédula vinculada por administrador',
        )
    except Exception:  # noqa: BLE001 - la auditoría no rompe el panel admin
        logger.warning(
            'No se pudo auditar la vinculación RRHH del perfil %s',
            perfil.pk,
            exc_info=True,
        )

    return {
        'cedula': cedula,
        'cargo': perfil.cargo,
        'departamento_rrhh': perfil.departamento_rrhh,
        'verificado': perfil.cedula_verificada_rrhh,
    }
