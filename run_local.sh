#!/usr/bin/env sh
# Arranque local sin Docker. Recolecta los estáticos (polidoc/static/**) y
# regenera el bundle de django-compressor antes de levantar el servidor, igual
# que el servicio `web` de docker-compose. Necesario con DEBUG=False (WhiteNoise
# + manifest); con DEBUG=True runserver sirve desde los finders, así que es
# inocuo pero garantiza que staticfiles/ nunca quede desactualizado.
set -e
python manage.py collectstatic --noinput
python manage.py compress --force || true
exec python manage.py runserver "0.0.0.0:${PORT:-8001}"
