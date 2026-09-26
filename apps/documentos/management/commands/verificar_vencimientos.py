from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Verifica vencimientos de documentos: marca obsoletos y envía avisos'

    def handle(self, *args, **options):
        from documentos.tasks import verificar_vencimientos
        resultado = verificar_vencimientos()
        self.stdout.write(
            self.style.SUCCESS(
                f'Verificación completada: {resultado["vencidos"]} docs vencidos → obsoleto, '
                f'{resultado["avisos"]} avisos de vencimiento enviado.'
            )
        )
