# POLIDOC

Sistema de gestión documental para la Policlínica Táchira, orientado a normas
ISO (9001, 27001, 15489): control de versiones, aprobaciones, vigencia,
clasificación de seguridad, descargas trazables y auditoría inmutable.

## Tecnologías

| Capa | Tecnología |
|------|------------|
| Backend | Django 6 + Django REST Framework |
| Autenticación | `django-auth-ldap` (Active Directory) + backend local |
| Base de datos | SQLite (por defecto) o PostgreSQL (`psycopg`) |
| Auditoría | MongoDB (`pymongo`) con HMAC-SHA256 y cadena de hashes |
| Tareas asíncronas | Celery + RabbitMQ (broker) + Redis (result backend / caché) |
| Documentos | LibreOffice headless (conversión a PDF), `pypdf`, `pdfplumber`, `openpyxl` |
| Resumen IA | Google Gemini (`google-genai`) |
| Estáticos | `django-compressor` (minificado/agrupado) + WhiteNoise (gzip/brotli) |
| Despliegue | Docker + Docker Compose |

## Puesta en marcha (Docker)

1. Copia las variables de entorno y ajusta los valores:

   ```bash
   cp .env.example .env
   # edita .env (SECRET_KEY, MONGO_PASSWORD, etc.)
   ```

2. Levanta los servicios:

   ```bash
   docker compose up --build
   ```

   El contenedor `web` ejecuta `collectstatic` + `compress` y luego el servidor
   en el puerto **8001**. Paneles: RabbitMQ (15672), Mongo Express (8081),
   Mailpit (8025).

3. Aplica migraciones y crea un superusuario (si hace falta):

   ```bash
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py createsuperuser
   ```

## Desarrollo local (sin Docker)

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Tests

```bash
python manage.py test auditoria documentos autenticacion --settings=polidoc.test_settings
```

Comprobaciones rápidas:

```bash
python manage.py check --settings=polidoc.test_settings
python manage.py makemigrations --check --dry-run --settings=polidoc.test_settings
```

## Variables de entorno

Todas las variables están documentadas en [`.env.example`](.env.example).
Las más relevantes:

- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`.
- `DB_ENGINE` / `DB_NAME` (SQLite o PostgreSQL).
- `MONGO_URI` (auditoría ISO).
- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CACHE_REDIS_URL`.
- `AUDIT_HMAC_KEY`, `AUDIT_FAIL_CLOSED`, `AUDIT_TRUSTED_PROXIES`.
- `PAPEL_PDF_CONVERSION`, `PDF_QA_ADAPTATIVO`, `PDF_SALTO_DOCX`.
- `IA_RESUMEN_HABILITADO`, `GEMINI_API_KEY`.

## Estructura

```
apps/
  autenticacion/   # login (LDAP/local), perfiles, notificaciones, dashboard
  documentos/      # modelos, vistas, API, formularios y servicios documentales
    services/      # conversión/QA de PDF, planillas, búsqueda, caché, ...
  auditoria/       # registro ISO en MongoDB, circuit breaker y buffer
polidoc/           # settings, URLs, Celery, estáticos y plantillas base
CONTEXT.txt        # documentación técnica y de decisiones (fuente de verdad)
```

## Notas de rendimiento

- **Conversión a PDF asíncrona**: el detalle del documento encola la conversión
  (Celery) y muestra una barra de progreso con polling; ya no bloquea la carga
  ~2-3 s esperando a LibreOffice. Si el broker no está, se degrada a modo
  síncrono.
- **Auditoría con circuit breaker**: si MongoDB no responde, se deja de
  penalizar cada evento con el timeout de selección; los logs se guardan en un
  buffer local (`data/audit_buffer/`) y una tarea de Celery Beat los reenvía.
- **Índices de BD** en los campos más filtrados/ordenados, agregados en una sola
  consulta (contadores/vigencia) y caché de catálogos.
- **Estáticos** agrupados/minificados y servidos comprimidos con WhiteNoise.

## Licencia

Proyecto privado. Todos los derechos reservados.
