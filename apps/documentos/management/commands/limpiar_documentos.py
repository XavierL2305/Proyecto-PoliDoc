from django.core.management.base import BaseCommand
from django.db import transaction
from documentos.models import (
    Documento, VersionDocumento, AsignacionDocumento,
    SolicitudDescarga, AprobacionDescarga,
    SolicitudAccesoDocumento,
    PlanillaCambios, AprobacionPlanilla, DocumentoAsociadoPlanilla,
    DistribucionDocumento, SolicitudAprobacionDocumento
)
from autenticacion.models import Notificacion


class Command(BaseCommand):
    help = 'Limpia todos los documentos y sus relaciones para pruebas formales'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING('Limpiando documentos y sus relaciones...'))
        
        with transaction.atomic():
            # Contar registros antes de eliminar
            stats = {
                'documento': Documento.objects.count(),
                'version': VersionDocumento.objects.count(),
                'asignacion': AsignacionDocumento.objects.count(),
                'solicitud_descarga': SolicitudDescarga.objects.count(),
                'aprobacion_descarga': AprobacionDescarga.objects.count(),
                'solicitud_acceso': SolicitudAccesoDocumento.objects.count(),
                'planilla': PlanillaCambios.objects.count(),
                'aprobacion_planilla': AprobacionPlanilla.objects.count(),
                'documento_asociado': DocumentoAsociadoPlanilla.objects.count(),
                'distribucion': DistribucionDocumento.objects.count(),
                'solicitud_aprobacion': SolicitudAprobacionDocumento.objects.count(),
                'notificacion': Notificacion.objects.filter(documento__isnull=False).count(),
            }
            
            # Eliminar en orden para evitar problemas de FK
            self.stdout.write('Eliminando solicitudes de aprobación...')
            SolicitudAprobacionDocumento.objects.all().delete()
            
            self.stdout.write('Eliminando planillas de cambios...')
            PlanillaCambios.objects.all().delete()  # Cascade: AprobacionPlanilla, DocumentoAsociadoPlanilla
            
            self.stdout.write('Eliminando solicitudes de acceso...')
            SolicitudAccesoDocumento.objects.all().delete()
            
            self.stdout.write('Eliminando solicitudes de descarga...')
            SolicitudDescarga.objects.all().delete()  # Cascade: AprobacionDescarga
            
            self.stdout.write('Eliminando asignaciones...')
            AsignacionDocumento.objects.all().delete()
            
            self.stdout.write('Eliminando distribuciones...')
            DistribucionDocumento.objects.all().delete()
            
            self.stdout.write('Eliminando versiones...')
            VersionDocumento.objects.all().delete()
            
            self.stdout.write('Eliminando documentos...')
            Documento.objects.all().delete()
            
            # Actualizar notificaciones (documento FK se vuelve NULL)
            self.stdout.write('Actualizando notificaciones...')
            notif_actualizadas = Notificacion.objects.filter(
                documento__isnull=False
            ).update(documento=None)
        
        # Mostrar resumen
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=== LIMPIEZA COMPLETADA ==='))
        self.stdout.write('')
        self.stdout.write('Registros eliminados:')
        self.stdout.write(f'  Documentos: {stats["documento"]}')
        self.stdout.write(f'  Versiones: {stats["version"]}')
        self.stdout.write(f'  Asignaciones: {stats["asignacion"]}')
        self.stdout.write(f'  Solicitudes de descarga: {stats["solicitud_descarga"]}')
        self.stdout.write(f'  Aprobaciones de descarga: {stats["aprobacion_descarga"]}')
        self.stdout.write(f'  Solicitudes de acceso: {stats["solicitud_acceso"]}')
        self.stdout.write(f'  Planillas de cambios: {stats["planilla"]}')
        self.stdout.write(f'  Aprobaciones de planilla: {stats["aprobacion_planilla"]}')
        self.stdout.write(f'  Documentos asociados: {stats["documento_asociado"]}')
        self.stdout.write(f'  Distribuciones: {stats["distribucion"]}')
        self.stdout.write(f'  Solicitudes de aprobación: {stats["solicitud_aprobacion"]}')
        self.stdout.write(f'  Notificaciones actualizadas: {notif_actualizadas}')
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Base de datos lista para pruebas formales.'))
