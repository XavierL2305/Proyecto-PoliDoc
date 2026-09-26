from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from autenticacion.models import PerfilUsuario
from documentos.models import Departamento


class Command(BaseCommand):
    help = 'Sincroniza Departamento.jefe con los PerfilUsuario que tienen rol jefe_departamento'

    def handle(self, *args, **options):
        perfiles_jefes = PerfilUsuario.objects.filter(
            rol='jefe_departamento',
            departamento__isnull=False
        ).select_related('usuario', 'departamento')

        sync_count = 0
        for perfil in perfiles_jefes:
            dept = perfil.departamento
            user = perfil.usuario

            if dept.jefe != user:
                dept.jefe = user
                dept.save(update_fields=['jefe'])
                self.stdout.write(
                    self.style.SUCCESS(f'Sync: {user.username} → jefe de {dept.nombre}')
                )
                sync_count += 1
            else:
                self.stdout.write(
                    f'  OK: {user.username} ya es jefe de {dept.nombre}'
                )

        self.stdout.write(
            self.style.SUCCESS(f'\nTotal sincronizados: {sync_count}')
        )
