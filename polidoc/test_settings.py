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
