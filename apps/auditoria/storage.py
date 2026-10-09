import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class ReporteAuditoriaStorage(FileSystemStorage):
    """Storage privado para los reportes de auditoría exportados a PDF.

    Vive fuera de MEDIA_ROOT, por lo que Django no lo sirve por URL: los
    reportes se entregan únicamente a través de las vistas de administración
    (auditoria_reporte_pdf y auditoria_reporte_descarga). La ubicación se lee
    en cada acceso a settings, no al instanciar, para que se pueda cambiar en
    runtime (p. ej. en tests con override_settings).
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('base_url', None)
        super().__init__(*args, **kwargs)

    @property
    def base_location(self):
        return settings.AUDITORIA_REPORTES_DIR

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None
