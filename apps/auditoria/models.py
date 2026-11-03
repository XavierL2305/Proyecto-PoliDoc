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


class AuditoriaOutbox(models.Model):
    """Evento de auditoría pendiente de enviar a MongoDB.

    Se escribe en la MISMA transacción SQL que la acción auditada, de modo que
    la traza no se pierde si el proceso cae justo después del commit (el
    worker de Celery la reintenta). Es un patrón outbox: la entrega es
    at-least-once; ``payload`` incluye ``correlation_id`` para agrupar posibles
    duplicados.
    """

    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('procesando', 'Procesando'),
        ('enviado', 'Enviado'),
        ('error', 'Error'),
    ]

    evento_id = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False,
        verbose_name='ID del evento',
    )
    payload = models.JSONField(verbose_name='Payload de auditoría')
    estado = models.CharField(
        max_length=12, choices=ESTADO_CHOICES, default='pendiente',
        db_index=True, verbose_name='Estado',
    )
    intentos = models.PositiveIntegerField(default=0, verbose_name='Intentos')
    ultimo_error = models.TextField(blank=True, default='', verbose_name='Último error')
    creado_en = models.DateTimeField(auto_now_add=True, db_index=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    enviado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Evento de auditoría en outbox'
        verbose_name_plural = 'Eventos de auditoría en outbox'
        ordering = ['creado_en']
        indexes = [
            models.Index(fields=['estado', 'creado_en'], name='aud_outbox_estado_idx'),
        ]

    def __str__(self):
        return f'{self.evento_id} ({self.estado})'
