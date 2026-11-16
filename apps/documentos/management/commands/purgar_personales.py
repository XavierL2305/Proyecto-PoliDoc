"""Avisa y purga documentos personales por inactividad del usuario.

Uso::

    python manage.py purgar_personales --dry-run   # solo informa
    python manage.py purgar_personales             # aplica avisos/purga

Regla: avisos al jefe directo (o administradores) a los 60 y 80 días; purga
definitiva a los 90. Solo ``ambito='personal'``.
"""
from django.core.management.base import BaseCommand

from documentos.services.documentos_sin_codigo import procesar_purga_personal


class Command(BaseCommand):
    help = (
        'Avisa (60/80 días) y purga (90) los documentos personales de usuarios '
        'inactivos. Solo documentos personales; nunca SGC ni ISO.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='No modifica nada: solo informa lo que haría.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        resumen = procesar_purga_personal(dry_run=dry_run)
        prefijo = '[dry-run] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefijo}Purga de documentos personales finalizada: '
            f"avisos={resumen['avisos']} "
            f"segundos={resumen['segundos']} "
            f"purgados={resumen['purgados']} "
            f"reiniciados={resumen['reiniciados']}"
        ))
