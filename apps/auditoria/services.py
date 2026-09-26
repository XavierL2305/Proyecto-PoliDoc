import logging
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from django.conf import settings
from .middleware import get_client_ip

logger = logging.getLogger(__name__)

_client = None
_db = None

# Contador local de respaldo para log_ids (solo se usa si el contador atómico
# de MongoDB no está disponible, p.ej. en tests con Mongo mockeado).
_log_counter = {}
_counter_date = None

# Campos que NO entran en el HMAC de integridad: 'integridad' (contiene el
# propio hash), 'metadata' y '_id' (se añaden después de firmar). Excluirlos
# permite que verificar_integridad_log() reproduzca el hash sobre el documento
# tal como lo devuelve MongoDB.
_CAMPOS_EXCLUIDOS_HASH = frozenset({'integridad', 'metadata', '_id'})


def _obtener_clave_hmac():
    """Clave para el HMAC de integridad. Vive en el entorno de la app, NUNCA en
    MongoDB: así quien solo comprometa el almacén de auditoría no puede forjar
    hashes válidos. ISO 27001 A.12.4 (protección de registros)."""
    clave = getattr(settings, 'AUDIT_HMAC_KEY', '') or getattr(settings, 'SECRET_KEY', '')
    return (clave or 'polidoc-sin-clave').encode('utf-8')


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

def generar_log_id(db=None):
    """
    Genera un ID único legible para el registro de auditoría.
    Formato: LOG-YYYY-MM-DD-XXX
    ISO 15489: Identificación única del registro.

    Si se pasa ``db`` (cliente Mongo) usa un contador ATÓMICO ($inc) compartido
    entre procesos y reinicios, evitando colisiones que harían fallar el índice
    único idx_log_id (y perder el registro). Sin ``db`` (o si el contador falla)
    recurre a un contador local en memoria, suficiente para tests.
    """
    today = datetime.utcnow().date()
    date_str = today.strftime('%Y-%m-%d')

    counter = None
    if db is not None:
        try:
            doc = db.counters.find_one_and_update(
                {'_id': f'audit_log_{date_str}'},
                {'$inc': {'seq': 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            seq = doc.get('seq') if isinstance(doc, dict) else None
            if isinstance(seq, int):
                counter = seq
        except Exception as e:
            logger.warning(f"Contador atómico de log_id no disponible, uso local: {e}")
            counter = None

    if counter is None:
        counter = _next_local_counter(date_str)

    return f"LOG-{date_str}-{counter:04d}"


def _next_local_counter(date_str):
    """Contador local en memoria (respaldo / tests). No es seguro entre procesos."""
    global _log_counter, _counter_date
    today = datetime.utcnow().date()
    if _counter_date != today:
        _log_counter = {}
        _counter_date = today
    if date_str not in _log_counter:
        _log_counter[date_str] = 0
    _log_counter[date_str] += 1
    return _log_counter[date_str]


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
    Calcula el HMAC-SHA256 de un registro completo de auditoría.

    Excluye los campos que NO forman parte de la carga firmada canónica:
    'integridad' (contiene el propio hash), 'metadata' y '_id' (ambos se
    añaden DESPUÉS de calcular el hash: metadata en la inserción y _id lo
    genera MongoDB). Excluirlos aquí hace que el hash recalculado al VERIFICAR
    un registro leído de Mongo coincida con el firmado al insertarlo.

    Se usa HMAC (no SHA-256 plano) con una clave que vive en el entorno de la
    app: quien pueda escribir en MongoDB pero no conozca la clave NO puede
    recalcular un hash válido tras alterar el registro → la manipulación se
    detecta. ISO 27001 A.12.4.2. El encadenado ``prev_hash`` (incluido en el
    registro y por tanto cubierto por el HMAC) añade evidencia de cadena.
    """
    datos_para_hash = {
        k: v for k, v in log_entry.items()
        if k not in _CAMPOS_EXCLUIDOS_HASH
    }
    datos_str = json.dumps(datos_para_hash, sort_keys=True, default=str)
    return hmac.new(_obtener_clave_hmac(), datos_str.encode(), hashlib.sha256).hexdigest()


def _obtener_prev_hash(db):
    """Devuelve el hash de integridad del último registro insertado (cadena).

    Retorna '' si no hay registros previos, si ``db`` es un mock (tests) o si la
    consulta falla. Nota: en escrituras simultáneas dos registros podrían leer
    el mismo prev_hash (bifurcación de cadena); el volumen de una clínica es
    bajo y se prioriza no bloquear la escritura. ISO 27001 A.12.4.
    """
    try:
        ultimo = db.audit_logs.find_one(sort=[('timestamp', DESCENDING)])
    except Exception:
        return ''
    if not isinstance(ultimo, dict):
        return ''
    integridad = ultimo.get('integridad')
    if isinstance(integridad, dict):
        h = integridad.get('hash', '')
        return h if isinstance(h, str) else ''
    return ''


def _extraer_hash(integridad_hash):
    """Separa el prefijo de algoritmo del valor: 'sha256:abc' -> 'abc'."""
    if not isinstance(integridad_hash, str):
        return ''
    return integridad_hash.split(':', 1)[1] if ':' in integridad_hash else integridad_hash


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
                      user_agent=None, sesion_id=None, fail_closed=False):
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
        # BSON almacena datetime con precisión de MILISEGUNDOS. Truncamos 'now'
        # para que el timestamp firmado al insertar sea idéntico al que se lee
        # de Mongo al verificar; si no, el HMAC recalculado nunca coincidiría.
        now = datetime.utcnow()
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)

        # Generar IDs únicos (contador atómico si hay Mongo real)
        log_id = generar_log_id(db)
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

        # Encadenado: hash del registro inmediatamente anterior. Va a nivel
        # superior (no dentro de 'integridad') para quedar cubierto por el HMAC
        # y enlazar criptográficamente la cadena. Si no se puede obtener (p.ej.
        # primer registro, Mongo mockeado o fallo), se deja vacío.
        log_entry['prev_hash'] = _obtener_prev_hash(db)

        # Calcular hash de integridad (HMAC-SHA256 con clave del entorno)
        hash_registro = calcular_hash_registro(log_entry)
        log_entry['integridad'] = {
            'hash': f"sha256:{hash_registro}",
            'algoritmo': 'hmac-sha256',
            'version_registro': '1.1'
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
        # Un fallo de auditoría es siempre un incidente: se alarma a CRITICAL.
        logger.critical(
            f"FALLO AUDITORÍA ISO action={accion_tipo}/{accion_categoria} "
            f"usuario={getattr(usuario, 'username', usuario)} fail_closed={fail_closed}: {e}",
            exc_info=True,
        )
        if fail_closed:
            # Acción sensible (descarga/visor): NO continuar sin trazabilidad.
            raise
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
                       ip_address=None, correlation_id=None, fail_closed=False):
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
        ip_address=ip_address,
        fail_closed=fail_closed,
    )


def registrar_consulta(usuario, recurso_tipo, recurso_id,
                       version=None, codigo=None,
                       ip_address=None, correlation_id=None, fail_closed=False):
    """Registra la visualización del CONTENIDO de un documento en el visor.

    ISO 27001 A.12.4: el acceso de lectura a documentos controlados debe quedar
    trazado, igual que la descarga.
    """
    return registrar_log_iso(
        usuario=usuario,
        accion_tipo='consulta',
        accion_categoria='documento',
        recurso_tipo=recurso_tipo,
        recurso_id=recurso_id,
        recurso_codigo=codigo,
        recurso_version=version,
        detalles={'version_consultada': version, 'medio': 'visor'},
        resultado_exitoso=True,
        resultado_mensaje='Documento visualizado en el visor',
        correlation_id=correlation_id,
        ip_address=ip_address,
        fail_closed=fail_closed,
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

def _es_registro_hmac(integridad):
    """True si el registro se firmó con el esquema HMAC actual (>= 1.1).

    Los registros anteriores a la Fase 3 se firmaron con SHA-256 plano y sin
    prev_hash; no son reproducibles con el HMAC actual y deben tratarse como
    "esquema antiguo" en vez de "alterados".
    """
    if not isinstance(integridad, dict):
        return False
    return (integridad.get('algoritmo') == 'hmac-sha256'
            or integridad.get('version_registro') == '1.1')


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

        if not _es_registro_hmac(log_entry.get('integridad')):
            return {
                'valido': False,
                'esquema_antiguo': True,
                'mensaje': ('Registro firmado con esquema anterior a 1.1 '
                            '(SHA-256 plano); no verificable con el HMAC actual'),
            }

        hash_almacenado = _extraer_hash(log_entry['integridad'].get('hash'))
        hash_calculado = calcular_hash_registro(log_entry)

        if hmac.compare_digest(hash_almacenado, hash_calculado):
            return {'valido': True, 'mensaje': 'Integridad verificada correctamente'}
        else:
            return {'valido': False, 'mensaje': '¡ADVERTENCIA! Hash no coincide - posible alteración'}

    except Exception as e:
        logger.error(f"Error al verificar integridad: {e}")
        return {'valido': False, 'mensaje': f'Error al verificar: {str(e)}'}


def verificar_cadena_auditoria(limite=1000):
    """Verifica integridad y encadenado prev_hash de los registros MÁS RECIENTES.

    Toma los últimos ``limite`` registros (orden descendente) y los recorre en
    orden cronológico comprobando, para cada uno: (a) que su HMAC de integridad
    sea válido (no fue alterado) y (b) que su ``prev_hash`` coincida con el hash
    del registro anterior de la ventana. Detecta alteraciones, eliminaciones o
    reordenamientos en la cadena de auditoría.

    Limitación conocida: los registros con ``prev_hash`` vacío (primeros de la
    cadena o escritos con Mongo mockeado) no validan el enlace, y dos escrituras
    simultáneas pueden bifurcar la cadena. Aceptable para el volumen de una
    clínica; el HMAC sí garantiza que cada registro individual no fue alterado.

    Retorna: dict {'valido', 'revisados', 'primera_rotura', 'mensaje'}
    """
    try:
        db = get_mongo_client()
        # DESC + limit toma los MÁS RECIENTES; se invierte para recorrerlos en
        # orden cronológico y poder validar el enlace prev_hash → anterior.
        logs = list(db.audit_logs.find().sort('timestamp', DESCENDING).limit(limite))
        logs.reverse()
    except Exception as e:
        logger.error(f"Error al verificar cadena de auditoría: {e}")
        return {'valido': False, 'revisados': 0, 'primera_rotura': None,
                'mensaje': f'Error al consultar: {str(e)}'}

    hash_anterior = ''
    revisados = 0
    for log in logs:
        if not isinstance(log, dict):
            continue
        revisados += 1
        log_id = log.get('log_id')

        # (a) Integridad individual: el HMAC almacenado debe reproducirse.
        # Solo aplica a registros del esquema actual (>= 1.1); los anteriores a
        # la Fase 3 se firmaron con SHA-256 plano y no son reproducibles aquí,
        # por lo que se omiten (no se reportan como alterados).
        integridad = log.get('integridad', {})
        if _es_registro_hmac(integridad):
            hash_almacenado = _extraer_hash(integridad.get('hash'))
            if hash_almacenado and not hmac.compare_digest(hash_almacenado, calcular_hash_registro(log)):
                return {'valido': False, 'revisados': revisados, 'primera_rotura': log_id,
                        'mensaje': f'Hash inválido (registro alterado) en {log_id}'}

        # (b) Enlace con el anterior.
        prev = log.get('prev_hash', '')
        if prev and hash_anterior and prev != hash_anterior:
            return {'valido': False, 'revisados': revisados, 'primera_rotura': log_id,
                    'mensaje': f'Cadena rota en {log_id}'}
        hash_anterior = integridad.get('hash', '') if isinstance(integridad, dict) else ''

    return {'valido': True, 'revisados': revisados, 'primera_rotura': None,
            'mensaje': 'Cadena de auditoría íntegra'}


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
