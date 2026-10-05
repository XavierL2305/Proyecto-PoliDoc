"""Context processors globales del proyecto."""
import os

from django.conf import settings


def static_versions(request):
    """Versiones por mtime para cache-busting de los estáticos volátiles.

    El navegador cachea agresivamente los CSS/JS servidos desde disco y
    servía hojas viejas de `button-animations` (se veía el bote inflarse
    aunque ya no se escala). Con `?v=<mtime>` cada edición invalida la
    copia en caché sin tocar manifest ni collectstatic.
    """
    base = os.path.join(settings.BASE_DIR, 'polidoc', 'static')
    files = {
        'button_css': 'css/components/button-animations.css',
        'button_js': 'js/button-animations.js',
    }

    versions = {}
    for key, rel in files.items():
        try:
            versions[key] = int(os.path.getmtime(os.path.join(base, rel)))
        except OSError:
            versions[key] = 0

    return {'static_versions': versions}
