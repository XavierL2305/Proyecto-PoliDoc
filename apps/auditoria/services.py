import logging
import hashlib
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4
from pymongo import MongoClient, ASCENDING, DESCENDING
from django.conf import settings
from .middleware import get_client_ip

logger = logging.getLogger(__name__)

_client = None
_db = None

# Contador para log_ids únicos (resetea diariamente)
_log_counter = {}
_counter_date = None


def get_mongo_client():
    global _client, _db
    if _client is None:
        # Primero intentar con MONGO_URI del settings (Docker)
        mongo_uri = getattr(settings, 'MONGO_URI', '')
        
        if not mongo_uri:
            mongo_uri = os.getenv('MONGO_URI', '')
        
        if mongo_uri:
            # Usar URI completa del environment (Docker)
            _client = MongoClient(mongo_uri, serverSelectionTimeoutMS=2000)
        else:
            # Usar configuración individual (desarrollo local)
            mongo_settings = getattr(settings, 'MONGO_SETTINGS', {})
            host = mongo_settings.get('HOST', 'localhost')
            port = mongo_settings.get('PORT', 27017)
            db_name = mongo_settings.get('DB_NAME', 'polidoc_audit_logs')
            user = mongo_settings.get('USER', '')
            password = mongo_settings.get('PASSWORD', '')

            if user and password:
                uri = f"mongodb://{user}:{password}@{host}:{port}/"
            else:
                uri = f"mongodb://{host}:{port}/"

            _client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        
        db_name = getattr(settings, 'MONGO_SETTINGS', {}).get('DB_NAME', 'polidoc_audit_logs')
        _db = _client[db_name]
    return _db


# =============================================================================
# FUNCIONES UTILITARIAS ISO
# =============================================================================

def generar_log_id():
    """
    Genera un ID único legible para el registro de auditoría.
    Formato: LOG-YYYY-MM-DD-XXX
    ISO 15489: Identificación única del registro
    """
    global _log_counter, _counter_date
    today = datetime.utcnow().date()

    if _counter_date != today:
        _log_counter = {}
        _counter_date = today

    date_str = today.strftime('%Y-%m-%d')
    if date_str not in _log_counter:
        _log_counter[date_str] = 0

    _log_counter[date_str] += 1
    counter = _log_counter[date_str]

    return f"LOG-{date_str}-{counter:04d}"


def generar_correlation_id():
    """
    Genera un UUID para agrupar acciones relacionadas.
    ISO 27001: Trazabilidad de acciones relacionadas
    """
    return str(uuid4())


def calcular_hash(datos):
    """
    Calcula hash SHA-256 de un diccionario para garantizar integridad.
    ISO 27001: Integridad de registros
    """
    datos_str = json.dumps(datos, sort_keys=True, default=str)
    return hashlib.sha256(datos_str.encode()).hexdigest()


def calcular_hash_registro(log_entry):
    """
    Calcula el hash de un registro completo de auditoría.
    Excluye el campo 'integridad' para evitar recursión.
    """
    datos_para_hash = {k: v for k, v in log_entry.items() if k != 'integridad'}
    return calcular_hash(datos_para_hash)


# =============================================================================
# FUNCIÓN PRINCIPAL DE REGISTRO ISO
# =============================================================================

def registrar_log_iso(usuario, accion_tipo, accion_categoria,
                      recurso_tipo=None, recurso_id=None,
                      recurso_codigo=None, recurso_nombre=None,
                      recurso_version=None, estado_anterior=None,
                      estado_posterior=None, detalles=None,
                      resultado_exitoso=True, resultado_mensaje='',
                      correlation_id=None, ip_address=None,
                      user_agent=None, sesion_id=None):
    """
    Registra un log de auditoría con estructura completa ISO 15489/27001.

    Parámetros:
    -----------
    usuario : User o None
        Usuario que realiza la acción
    accion_tipo : str
        Tipo de acción: creacion, edicion, eliminacion, consulta, descarga, etc.
    accion_categoria : str
        Categoría: documento, usuario, sistema, seguridad
    recurso_tipo : str
        Tipo de recurso: proceso, procedimiento, formulario, usuario
    recurso_id : int
        ID del recurso
    recurso_codigo : str
        Código del documento (ej: SGC-PRG-001)
    recurso_nombre : str
        Nombre del recurso
    recurso_version : str
        Versión del recurso
    estado_anterior : str
        Estado antes de la acción
    estado_posterior : str
        Estado después de la acción
    detalles : dict
        Detalles adicionales de la acción
    resultado_exitoso : bool
        Si la acción fue exitosa
    resultado_mensaje : str
        Mensaje descriptivo del resultado
    correlation_id : str
        ID de correlación para agrupar acciones relacionadas
    ip_address : str
        IP del cliente
    user_agent : str
        User-Agent del navegador
    sesion_id : str
        ID de sesión del usuario
    """
    try:
        db = get_mongo_client()
        now = datetime.utcnow()

        # Generar IDs únicos
        log_id = generar_log_id()
        if correlation_id is None:
            correlation_id = generar_correlation_id()

        # Construir estructura del registro
        log_entry = {
            'log_id': log_id,
            'correlation_id': correlation_id,
            'timestamp': now,

            'usuario': {
                'id': usuario.id if usuario else None,
                'username': usuario.username if usuario else 'sistema',
                'nombre_completo': usuario.get_full_name() if usuario else '',
                'rol': usuario.perfil.rol if usuario and hasattr(usuario, 'perfil') else '',
                'departamento': str(usuario.perfil.departamento) if usuario and hasattr(usuario, 'perfil') and usuario.perfil.departamento else ''
            },

            'accion': {
                'tipo': accion_tipo,
                'categoria': accion_categoria,
                'descripcion': _generar_descripcion_accion(accion_tipo, recurso_tipo)
            },

            'recurso': {
                'tipo': recurso_tipo,
                'id': recurso_id,
                'codigo': recurso_codigo,
                'nombre': recurso_nombre,
                'version': recurso_version,
                'estado_anterior': estado_anterior,
                'estado_posterior': estado_posterior
            },

            'contexto': {
                'ip_address': ip_address or get_client_ip(),
                'user_agent': user_agent,
                'sistema': 'POLIDOC',
                'sesion_id': sesion_id
            },

            'detalles': detalles or {},

            'resultado': {
                'exitoso': resultado_exitoso,
                'mensaje': resultado_mensaje
            }
        }

        # Calcular hash de integridad
        hash_registro = calcular_hash_registro(log_entry)
        log_entry['integridad'] = {
            'hash': f"sha256:{hash_registro}",
            'version_registro': '1.0'
        }

        # Metadata de retención
        log_entry['metadata'] = {
            'fecha_creacion': now,
            'fecha_expiracion': now + timedelta(days=365 * 10),  # 10 años
            'retencion_anos': 10
        }

        # Insertar en MongoDB
        db.audit_logs.insert_one(log_entry)
        logger.debug(f"Audit log ISO registrado: {log_id} - {accion_tipo} por {usuario}")
        return log_id

    except Exception as e:
        logger.error(f"Error al registrar audit log ISO en MongoDB: {e}")
        return None


def _generar_descripcion_accion(accion_tipo, recurso_tipo):
    """Genera una descripción legible de la acción"""
    descripciones = {
        'creacion': f'Creación de {recurso_tipo}',
        'edicion': f'Edición de {recurso_tipo}',
        'eliminacion': f'Eliminación de {recurso_tipo}',
        'consulta': f'Consulta de {recurso_tipo}',
        'login': 'Inicio de sesión',
        'logout': 'Cierre de sesión',
        'descarga': f'Descarga de {recurso_tipo}',
        'solicitud_descarga': f'Solicitud de descarga de {recurso_tipo}',
        'aprobacion_descarga': f'Aprobación de descarga de {recurso_tipo}',
        'rechazo_descarga': f'Rechazo de descarga de {recurso_tipo}',
        'asignacion': f'Asignación de {recurso_tipo}',
        'transferencia': f'Transferencia de {recurso_tipo}',
        'cambio_version': f'Cambio de versión de {recurso_tipo}',
        'reasignacion': f'Reasignación de {recurso_tipo}',
    }
    return descripciones.get(accion_tipo, f'Acción {accion_tipo} en {recurso_tipo}')


# =============================================================================
# FUNCIONES DE REGISTRO ESPECÍFICAS (ACTUALIZADAS)
# =============================================================================

def registrar_login(usuario, ip_address=None, user_agent=None, sesion_id=None):
    """Registra inicio de sesión"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='login',
        accion_categoria='seguridad',
        resultado_exitoso=True,
        resultado_mensaje='Sesión iniciada correctamente',
        ip_address=ip_address,
        user_agent=user_agent,
        sesion_id=sesion_id
    )


def registrar_logout(usuario, ip_address=None, user_agent=None, sesion_id=None):
    """Registra cierre de sesión"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='logout',
        accion_categoria='seguridad',
        resultado_exitoso=True,
        resultado_mensaje='Sesión cerrada correctamente',
        ip_address=ip_address,
        user_agent=user_agent,
        sesion_id=sesion_id
    )


def registrar_creacion_documento(usuario, recurso_tipo, recurso_id,
                                 codigo=None, nombre=None,
                                 estado_posterior='borrador',
                                 ip_address=None, correlation_id=None):
    """Registra creación de un documento"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='creacion',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        recurso_nombre=nombre,
        recurso_version='1.0',
        estado_anterior=None,
        estado_posterior=estado_posterior,
        detalles={'codigo': codigo, 'nombre': nombre},
        resultado_exitoso=True,
        resultado_mensaje=f'{recurso_tipo.title()} creado correctamente',
        correlation_id=correlation_id,
        ip_address=ip_address
    )


def registrar_edicion_documento(usuario, recurso_tipo, recurso_id,
                                codigo=None, nombre=None,
                                estado_anterior=None, estado_posterior=None,
                                version_anterior=None, version_nueva=None,
                                cambios=None, ip_address=None,
                                correlation_id=None):
    """Registra edición de un documento"""
    detalles = {
        'campos_modificados': list(cambios.keys()) if cambios else [],
        'valores_anteriores': {},
        'valores_nuevos': cambios
    }

    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='edicion',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        recurso_nombre=nombre,
        recurso_version=version_nueva,
        estado_anterior=estado_anterior,
        estado_posterior=estado_posterior,
        detalles=detalles,
        resultado_exitoso=True,
        resultado_mensaje=f'{recurso_tipo.title()} editado correctamente',
        correlation_id=correlation_id,
        ip_address=ip_address
    )


def registrar_eliminacion_documento(usuario, recurso_tipo, recurso_id,
                                    codigo=None, nombre=None,
                                    estado_anterior='activo',
                                    ip_address=None, correlation_id=None):
    """Registra eliminación (cambio a obsoleto) de un documento"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='eliminacion',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        recurso_nombre=nombre,
        estado_anterior=estado_anterior,
        estado_posterior='obsoleto',
        detalles={'codigo': codigo},
        resultado_exitoso=True,
        resultado_mensaje=f'{recurso_tipo.title()} marcado como obsoleto',
        correlation_id=correlation_id,
        ip_address=ip_address
    )


def registrar_solicitud_descarga(usuario, recurso_tipo, recurso_id,
                                 solicitud_id=None, codigo=None,
                                 ip_address=None, correlation_id=None):
    """Registra solicitud de descarga"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='solicitud_descarga',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        detalles={'solicitud_id': solicitud_id},
        resultado_exitoso=True,
        resultado_mensaje='Solicitud de descarga creada',
        correlation_id=correlation_id,
        ip_address=ip_address
    )


def registrar_aprobacion_descarga(usuario, recurso_tipo, recurso_id,
                                  solicitud_id=None, aprobada=True,
                                  observaciones=None, codigo=None,
                                  ip_address=None, correlation_id=None):
    """Registra aprobación o rechazo de descarga"""
    accion = 'aprobacion_descarga' if aprobada else 'rechazo_descarga'
    resultado = 'Descarga aprobada' if aprobada else 'Descarga rechazada'

    return registrar_log_iso(
        usuario=usuario,
        accion_tipo=accion,
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        detalles={
            'solicitud_id': solicitud_id,
            'aprobada': aprobada,
            'observaciones': observaciones
        },
        resultado_exitoso=aprobada,
        resultado_mensaje=resultado,
        correlation_id=correlation_id,
        ip_address=ip_address
    )


def registrar_descarga(usuario, recurso_tipo, recurso_id,
                       version=None, codigo=None,
                       ip_address=None, correlation_id=None):
    """Registra descarga efectiva de un documento"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='descarga',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        recurso_version=version,
        detalles={'version_descargada': version},
        resultado_exitoso=True,
        resultado_mensaje='Documento descargado correctamente',
        correlation_id=correlation_id,
        ip_address=ip_address
    )


def registrar_eliminacion_version(usuario, recurso_tipo, recurso_id,
                                 numero_version=None, codigo=None,
                                 ip_address=None, correlation_id=None):
    """Registra eliminación de versión de documento"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='eliminacion_version',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        recurso_version=numero_version,
        detalles={'version_eliminada': numero_version},
        resultado_exitoso=True,
        resultado_mensaje=f'Versión {numero_version} eliminada correctamente',
        correlation_id=correlation_id,
        ip_address=ip_address
    )


def registrar_asignacion_documento(usuario, recurso_tipo, recurso_id,
                                   coordinador_username=None, permisos=None,
                                   codigo=None, ip_address=None,
                                   correlation_id=None):
    """Registra asignación de documento a coordinador"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='asignacion',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        detalles={
            'coordinador_asignado': coordinador_username,
            'permisos_otorgados': permisos
        },
        resultado_exitoso=True,
        resultado_mensaje=f'Documento asignado a {coordinador_username}',
        correlation_id=correlation_id,
        ip_address=ip_address
    )


def registrar_transferencia_propiedad(usuario, recurso_tipo, recurso_id,
                                      nuevo_propietario_username=None,
                                      propietario_anterior=None,
                                      codigo=None, ip_address=None,
                                      correlation_id=None):
    """Registra transferencia de propiedad"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='transferencia',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        detalles={
            'propietario_anterior': propietario_anterior,
            'nuevo_propietario': nuevo_propietario_username
        },
        resultado_exitoso=True,
        resultado_mensaje=f'Propiedad transferida a {nuevo_propietario_username}',
        correlation_id=correlation_id,
        ip_address=ip_address
    )


def registrar_cambio_version(usuario, recurso_tipo, recurso_id,
                             version_anterior=None, version_nueva=None,
                             cambios=None, codigo=None,
                             ip_address=None, correlation_id=None):
    """Registra cambio de versión de documento"""
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='cambio_version',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        recurso_version=version_nueva,
        estado_anterior=f'v{version_anterior}',
        estado_posterior=f'v{version_nueva}',
        detalles={
            'version_anterior': version_anterior,
            'version_nueva': version_nueva,
            'cambios': cambios
        },
        resultado_exitoso=True,
        resultado_mensaje=f'Versión {version_nueva} creada',
        correlation_id=correlation_id,
        ip_address=ip_address
    )


# =============================================================================
# FUNCIÓN DE VERIFICACIÓN DE INTEGRIDAD
# =============================================================================

def verificar_integridad_log(log_id):
    """
    Verifica que el hash de un registro no haya sido alterado.
    ISO 27001: Integridad de registros

    Retorna:
        dict: {'valido': bool, 'mensaje': str}
    """
    try:
        db = get_mongo_client()
        log_entry = db.audit_logs.find_one({'log_id': log_id})

        if not log_entry:
            return {'valido': False, 'mensaje': 'Registro no encontrado'}

        if 'integridad' not in log_entry:
            return {'valido': False, 'mensaje': 'Registro sin hash de integridad'}

        hash_almacenado = log_entry['integridad']['hash'].replace('sha256:', '')
        hash_calculado = calcular_hash_registro(log_entry)

        if hash_almacenado == hash_calculado:
            return {'valido': True, 'mensaje': 'Integridad verificada correctamente'}
        else:
            return {'valido': False, 'mensaje': '¡ADVERTENCIA! Hash no coincide - posible alteración'}

    except Exception as e:
        logger.error(f"Error al verificar integridad: {e}")
        return {'valido': False, 'mensaje': f'Error al verificar: {str(e)}'}


# =============================================================================
# CONSULTA DE LOGS (ACTUALIZADA)
# =============================================================================

def obtener_logs(usuario=None, recurso_tipo=None, recurso_id=None,
                 accion_tipo=None, accion_categoria=None,
                 fecha_inicio=None, fecha_fin=None,
                 correlation_id=None, limite=100):
    """
    Consulta logs de auditoría con filtros opcionales.
    Retorna logs ordenados por timestamp descendente.
    """
    try:
        db = get_mongo_client()
        query = {}

        if usuario:
            query['usuario.username'] = usuario
        if recurso_tipo:
            query['recurso.tipo'] = recurso_tipo
        if recurso_id:
            query['recurso.id'] = recurso_id
        if accion_tipo:
            query['accion.tipo'] = accion_tipo
        if accion_categoria:
            query['accion.categoria'] = accion_categoria
        if correlation_id:
            query['correlation_id'] = correlation_id
        if fecha_inicio or fecha_fin:
            query['timestamp'] = {}
            if fecha_inicio:
                query['timestamp']['$gte'] = fecha_inicio
            if fecha_fin:
                query['timestamp']['$lte'] = fecha_fin

        logs = list(db.audit_logs.find(query).sort('timestamp', -1).limit(limite))
        return logs

    except Exception as e:
        logger.error(f"Error al consultar audit logs: {e}")
        return []


def obtener_logs_por_correlation_id(correlation_id):
    """
    Obtiene todos los registros relacionados por correlation_id.
    Útil para trazabilidad completa de una acción.
    """
    try:
        db = get_mongo_client()
        logs = list(db.audit_logs.find(
            {'correlation_id': correlation_id}
        ).sort('timestamp', 1))
        return logs

    except Exception as e:
        logger.error(f"Error al consultar logs por correlation_id: {e}")
        return []


def obtener_estadisticas_auditoria(fecha_inicio=None, fecha_fin=None):
    """
    Obtiene estadísticas de auditoría para reportes.
    """
    try:
        db = get_mongo_client()
        query = {}

        if fecha_inicio or fecha_fin:
            query['timestamp'] = {}
            if fecha_inicio:
                query['timestamp']['$gte'] = fecha_inicio
            if fecha_fin:
                query['timestamp']['$lte'] = fecha_fin

        pipeline = [
            {'$match': query},
            {'$group': {
                '_id': {
                    'accion': '$accion.tipo',
                    'categoria': '$accion.categoria'
                },
                'count': {'$sum': 1}
            }},
            {'$sort': {'count': -1}}
        ]

        stats = list(db.audit_logs.aggregate(pipeline))
        return stats

    except Exception as e:
        logger.error(f"Error al obtener estadísticas: {e}")
        return []


# =============================================================================
# CONFIGURACIÓN DE ÍNDICES
# =============================================================================

def crear_indices_mongo():
    """
    Crea los índices necesarios para búsquedas eficientes.
    Ejecutar una vez después de crear la base de datos.
    """
    try:
        db = get_mongo_client()
        collection = db.audit_logs

        # Índice único para log_id
        collection.create_index('log_id', unique=True, name='idx_log_id')

        # Índice para correlation_id (búsquedas de trazabilidad)
        collection.create_index('correlation_id', name='idx_correlation_id')

        # Índice para timestamp (rango de fechas)
        collection.create_index([('timestamp', DESCENDING)], name='idx_timestamp')

        # Índice para usuario
        collection.create_index('usuario.username', name='idx_usuario')

        # Índice compuesto para recurso
        collection.create_index([
            ('recurso.tipo', ASCENDING),
            ('recurso.id', ASCENDING)
        ], name='idx_recurso')

        # Índice para tipo de acción
        collection.create_index('accion.tipo', name='idx_accion_tipo')

        # Índice para categoría
        collection.create_index('accion.categoria', name='idx_accion_categoria')

        # Índice TTL para expiración automática (10 años)
        collection.create_index(
            'metadata.fecha_expiracion',
            expireAfterSeconds=0,
            name='idx_ttl_expiracion'
        )

        logger.info("Índices de MongoDB creados correctamente")
        return True

    except Exception as e:
        logger.error(f"Error al crear índices de MongoDB: {e}")
        return False
