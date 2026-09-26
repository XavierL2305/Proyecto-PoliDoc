from django.core.management.base import BaseCommand
from django.utils import timezone
from documentos.models import Documento
from autenticacion.models import Notificacion


class Command(BaseCommand):
    help = 'Verifica documentos elegibles para destrucción y notifica a administradores'

    def handle(self, *args, **options):
        hoy = timezone.now().date()
        total = 0

        docs = Documento.objects.filter(
            estado='obsoleto',
            fecha_destruccion__lte=hoy,
            fecha_destruccion__isnull=False,
        )

        from django.contrib.auth.models import User
        admins = User.objects.filter(is_superuser=True, is_active=True)

        for doc in docs:
            for admin in admins:
                existe = Notificacion.objects.filter(
                    usuario=admin,
                    tipo='sistema',
                    titulo='Documento elegible para destrucción',
                    leida=False
                ).exists()
                if not existe:
                    Notificacion.objects.create(
                        usuario=admin,
                        titulo='Documento elegible para destrucción',
                        mensaje=(
                            f'El documento "{doc.nombre}" ({doc.codigo}) '
                            f'ha superado su fecha de destrucción ({doc.fecha_destruccion}). '
                            f'Puede ser eliminado permanentemente.'
                        ),
                        tipo='sistema',
                        url_accion=f'/documentos/{doc.tipo_id}/{doc.uuid}/',
                    )
                    total += 1

        self.stdout.write(self.style.SUCCESS(
            f'{total} notificaciones de destrucción enviadas a {admins.count()} administradores'
        ))
