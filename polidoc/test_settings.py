from polidoc.settings import *

# Disable LDAP for tests
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
]

# Fast password hashing for tests (MD5 instead of PBKDF2 ~0.3s/hash)
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Caché local en memoria para tests (no depender de Redis).
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'polidoc-tests',
    }
}

# El QA/reparación adaptativa consulta Celery y LibreOffice; se activa solo en
# los tests que lo prueban con override_settings.
PDF_QA_ADAPTATIVO = False

# Ejecutar las tareas de Celery en el proceso (sin broker) durante los tests.
# Reproduce el comportamiento previo (conversión en línea) y evita intentos de
# conexión a RabbitMQ.
CELERY_TASK_ALWAYS_EAGER = True

# Buffer de auditoría a un directorio temporal, no dentro del repo.
import tempfile as _tempfile  # noqa: E402
AUDIT_BUFFER_DIR = _tempfile.mkdtemp(prefix='polidoc_audit_buffer_')

# Los informes de auditoría exportados a PDF se conservan en disco (ISO 15489);
# en tests se redirigen a un directorio temporal para no ensuciar data/.
AUDITORIA_REPORTES_DIR = _tempfile.mkdtemp(prefix='polidoc_reportes_auditoria_')

# Estáticos: sin manifest de WhiteNoise (requiere `collectstatic`) y sin
# django-compressor, para que los tests rendericen las plantillas tal cual.
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}
COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False

# WhiteNoise avisa si STATIC_ROOT no existe (no se corre collectstatic en tests);
# se retira del pipeline en tests.
MIDDLEWARE = [m for m in MIDDLEWARE if 'whitenoise' not in m]

# Use in-memory logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'null': {
            'class': 'logging.NullHandler',
        },
    },
    'root': {
        'handlers': ['null'],
        'level': 'DEBUG',
    },
    'loggers': {
        'django_auth_ldap': {
            'handlers': ['null'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'auditoria': {
            'handlers': ['null'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'documentos': {
            'handlers': ['null'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}
