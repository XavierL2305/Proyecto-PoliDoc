from django.core.management.base import BaseCommand

from autenticacion.services.rrhh_sync import (
    rrhh_habilitado, sincronizar_todo_rrhh,
)

CAMPOS_RESUMEN = (
    'total_rrhh',
    'perfiles_actualizados',
    'nombres_actualizados',
    'sin_match',
    'sin_cedula',
    'errores',
)


class Command(BaseCommand):
    help = (
        'Sincroniza cargo/departamento/nombres desde RRHH (SQL Server). '
        'Por defecto es dry-run: usa --apply para escribir.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Escribe en la base de datos (por defecto solo informa).',
        )

    def handle(self, *args, **options):
        aplicar = options['apply']
        if not rrhh_habilitado():
            self.stdout.write(self.style.WARNING(
                'RRHH no configurado (RRHH_SQLSERVER_HOST/USER vacíos); '
                'no se intenta ninguna conexión.'
            ))
            return

        resumen = sincronizar_todo_rrhh(dry_run=not aplicar)
        estado = 'APPLY (escribe)' if aplicar else 'DRY-RUN (no escribe)'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Sincronización RRHH — {estado}'
        ))
        for clave in CAMPOS_RESUMEN:
            self.stdout.write(f'  {clave}: {resumen.get(clave, 0)}')

        if resumen.get('errores'):
            self.stdout.write(self.style.ERROR(
                resumen.get('mensaje') or 'Error al sincronizar.'
            ))
        elif not aplicar:
            self.stdout.write(self.style.WARNING(
                '  [dry-run] No se escribió nada. Repite con --apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Sincronización aplicada.'))
