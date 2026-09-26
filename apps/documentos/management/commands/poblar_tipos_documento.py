from django.core.management.base import BaseCommand
from documentos.models import TipoDocumento
from documentos.maps import TIPOS_INFO


class Command(BaseCommand):
    help = 'Pobla la tabla de TipoDocumento con los tipos definidos en maps.py'

    def handle(self, *args, **options):
        created = 0
        updated = 0

        for key, info in TIPOS_INFO.items():
            tipo, was_created = TipoDocumento.objects.update_or_create(
                key=key,
                defaults={
                    'codigo': info['codigo'],
                    'nombre': info['nombre'],
                    'descripcion': f'Tipo de documento: {info["nombre"]}',
                    'activo': True,
                    'icono': info.get('icono', 'file'),
                    'color': info.get('color', 'primary'),
                    'solo_jefes': info.get('solo_jefes', False),
                    'sin_prefijo_depto': info.get('sin_prefijo_depto', False),
                }
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Tipos de documento: {created} creados, {updated} actualizados (total: {created + updated})'
            )
        )
