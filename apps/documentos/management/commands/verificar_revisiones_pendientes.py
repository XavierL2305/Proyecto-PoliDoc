"""
REGLA ISO 9001:2015 - Cláusula 7.5.2
Verifica documentos que requieren revisión periódica y envía notificaciones.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from documentos.models import Documento
from autenticacion.models import Notificacion


class Command(BaseCommand):
    help = 'Verifica documentos que requieren revisión periódica (ISO 9001)'

    def handle(self, *args, **options):
        hoy = timezone.now().date()
        total_notificaciones = 0

        docs = Documento.objects.filter(
            estado='activo',
            proxima_revision__lte=hoy
        )

        for doc in docs:
            existe_notificacion = Notificacion.objects.filter(
                usuario=doc.propietario,
                tipo='sistema',
                titulo='Revisión periódica requerida',
                leida=False
            ).exists()

            if not existe_notificacion:
                Notificacion.objects.create(
                    usuario=doc.propietario,
                    titulo='Revisión periódica requerida',
                    mensaje=f'El documento "{doc.nombre}" ({doc.codigo}) requiere revisión periódica. Fecha límite: {doc.proxima_revision}',
                    tipo='sistema',
                    url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
                    departamento=doc.departamento,
                    documento=doc
                )
                total_notificaciones += 1

        self.stdout.write(
            self.style.SUCCESS(f'Se crearon {total_notificaciones} notificaciones de revisión pendiente')
        )
