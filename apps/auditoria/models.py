import os
import uuid

from django.contrib.auth.models import User
from django.db import models

from .storage import ReporteAuditoriaStorage

# MongoDB logger service.
# Los logs de auditoría en sí se guardan en MongoDB vía services.py; aquí solo
# se modela la constancia en disco de los informes PDF que se exportan desde
# el módulo de auditoría (ISO 15489 §6.5: información documentada con
# identificador único e integridad verificable).


def generar_ruta_reporte(instance, filename):
    """Genera la ruta privada del informe: {username}/{codigo}.{ext}

    Sin el prefijo V- de las carpetas de documentos: aquí la carpeta identifica
    al usuario que generó el informe.
    """
    usuario = instance.generado_por.username if instance.generado_por_id else 'sistema'
    extension = os.path.splitext(filename)[1] if filename else '.pdf'
    return f'{usuario}/{instance.codigo}{extension}'


class ReporteAuditoria(models.Model):
    """Informe de auditoría exportado a PDF, conservado en disco (ISO 9001 §9.2).

    Cada generación crea una fila con su código único, los filtros usados, el
    total de registros incluidos y el hash SHA-256 del PDF, de modo que se
    pueda demostrar que el documento no fue alterado (ISO 27001 A.12.4).
    Los archivos se purgan cuando superan AUDITORIA_REPORTES_RETENCION_ANIOS.
    """

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    codigo = models.CharField(max_length=60, unique=True)
    archivo = models.FileField(
        upload_to=generar_ruta_reporte,
        storage=ReporteAuditoriaStorage(),
        max_length=255,
    )
    tipo_reporte = models.CharField(max_length=20, default='registro')
    fecha_inicio = models.DateField(null=True, blank=True)
    fecha_fin = models.DateField(null=True, blank=True)
    filtros = models.JSONField(default=dict, blank=True)
    formato = models.CharField(max_length=20, default='letter')
    orientacion = models.CharField(max_length=20, default='landscape')
    total_registros = models.PositiveIntegerField(default=0)
    tamano_bytes = models.PositiveIntegerField(default=0)
    hash_sha256 = models.CharField(max_length=64)
    generado_por = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='reportes_auditoria',
    )
    fecha_generacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fecha_generacion']

    def __str__(self):
        return self.codigo

    @property
    def nombre_archivo(self):
        """Nombre legible del PDF para el Content-Disposition."""
        return os.path.basename(self.archivo.name) if self.archivo.name else f'{self.codigo}.pdf'

    @property
    def periodo(self):
        """Período cubierto por el informe, en texto."""
        if self.fecha_inicio and self.fecha_fin:
            return f'{self.fecha_inicio.strftime("%d/%m/%Y")} a {self.fecha_fin.strftime("%d/%m/%Y")}'
        return '—'
