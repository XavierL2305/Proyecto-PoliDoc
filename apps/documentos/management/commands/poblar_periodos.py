from django.core.management.base import BaseCommand
from documentos.models import Periodo
from documentos.cache_utils import invalidar_periodos


PERIODOS = [
    {'nombre': '6 Meses', 'meses': 6, 'orden': 1},
    {'nombre': '1 Año', 'meses': 12, 'orden': 2},
    {'nombre': '5 Años', 'meses': 60, 'orden': 3},
    {'nombre': '10 Años', 'meses': 120, 'orden': 4},
    {'nombre': '12 Años', 'meses': 144, 'orden': 5},
]


class Command(BaseCommand):
    help = 'Pobla la tabla de Períodos estándar'

    def handle(self, *args, **options):
        creados = 0
        for datos in PERIODOS:
            _, created = Periodo.objects.get_or_create(
                meses=datos['meses'],
                defaults=datos,
            )
            if created:
                creados += 1
                self.stdout.write(f'  Creado: {datos["nombre"]}')
        self.stdout.write(self.style.SUCCESS(
            f'Períodos: {creados} creados, {len(PERIODOS) - creados} existentes'
        ))

        invalidar_periodos()
