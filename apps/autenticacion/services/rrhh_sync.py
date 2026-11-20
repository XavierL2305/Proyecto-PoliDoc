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
    """Deja solo dígitos: ``V-31.097.865`` → ``31097865``."""
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
    """``***7865``: últimos 4 dígitos, para auditoría sin guardar PII completa."""
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
