from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from autenticacion.models import PerfilUsuario
from documentos.models import Departamento


class Command(BaseCommand):
    help = (
        'Crea usuarios de prueba (uno por rol) para probar POLIDOC en local. '
        'Los usuarios usan el ModelBackend, por lo que pueden loguearse con '
        'usuario y contraseña sin depender de Active Directory.'
    )

    DEPARTAMENTO_BASE = 'TI'
    DEPARTAMENTO_GG = 'GG'

    # Cada entrada describe un usuario de prueba. `base=True` marca los usuarios
    # cuyo departamento puede reemplazarse con --departamento.
    USUARIOS = [
        {
            'username': 'Administrador',
            'first_name': 'Administrador',
            'last_name': '',
            'rol': 'administrador',
            'grupo_ldap': 'doc_administrador',
            'depto_codigo': None,
            'cedula': '00000000',
            'empleado': 'PRB-ADM',
            'es_admin_django': True,
            'usa_password_admin': True,
            'base': False,
        },
        {
            'username': 'lramirez',
            'first_name': 'Luis',
            'last_name': 'Ramírez',
            'rol': 'jefe_departamento',
            'grupo_ldap': 'doc_jefes',
            'depto_codigo': DEPARTAMENTO_BASE,
            'cedula': '25854753',
            'empleado': 'PRB-TI-JEF',
            'es_admin_django': False,
            'usa_password_admin': False,
            'base': True,
        },
        {
            'username': 'mrodriguez',
            'first_name': 'María',
            'last_name': 'Rodríguez',
            'rol': 'coordinador',
            'grupo_ldap': 'doc_coordinador',
            'depto_codigo': DEPARTAMENTO_BASE,
            'cedula': '8134856',
            'empleado': 'PRB-TI-COO',
            'es_admin_django': False,
            'usa_password_admin': False,
            'base': True,
        },
        {
            'username': 'jhernandez',
            'first_name': 'José',
            'last_name': 'Hernández',
            'rol': 'trabajador',
            'grupo_ldap': 'doc_trabajador',
            'depto_codigo': DEPARTAMENTO_BASE,
            'cedula': '14504437',
            'empleado': 'PRB-TI-TRA',
            'es_admin_django': False,
            'usa_password_admin': False,
            'base': True,
        },
        {
            'username': 'cdelgado',
            'first_name': 'Carmen',
            'last_name': 'Delgado',
            'rol': 'jefe_departamento',
            'grupo_ldap': 'doc_jefes',
            'depto_codigo': DEPARTAMENTO_GG,
            'cedula': '31865754',
            'empleado': 'PRB-GG-JEF',
            'es_admin_django': False,
            'usa_password_admin': False,
            'base': False,
        },
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            '--password',
            default='12345678',
            help='Contraseña de los usuarios de prueba no-admin (default: 12345678)',
        )
        parser.add_argument(
            '--admin-password',
            default='@rMag3d0n',
            help='Contraseña del usuario Administrador (default: @rMag3d0n)',
        )
        parser.add_argument(
            '--departamento',
            default=self.DEPARTAMENTO_BASE,
            help=(
                'Código del departamento para jefe/coordinador/trabajador '
                f'(default: {self.DEPARTAMENTO_BASE})'
            ),
        )
        parser.add_argument(
            '--borrar',
            action='store_true',
            help='Elimina los usuarios de prueba en vez de crearlos',
        )

    def handle(self, *args, **options):
        usernames = [u['username'] for u in self.USUARIOS]

        if options['borrar']:
            self._borrar(usernames)
            return

        depto_base = self._obtener_departamento(options['departamento'])
        depto_gg = self._obtener_departamento(self.DEPARTAMENTO_GG)

        creados = 0
        actualizados = 0
        resumen = []

        with transaction.atomic():
            for datos in self.USUARIOS:
                username = datos['username']
                existia = User.objects.filter(username=username).exists()

                depto_codigo = datos['depto_codigo']
                if datos['base']:
                    depto_codigo = options['departamento']
                if depto_codigo == self.DEPARTAMENTO_GG:
                    depto = depto_gg
                elif depto_codigo == self.DEPARTAMENTO_BASE or datos['base']:
                    depto = depto_base
                else:
                    depto = None

                password = (
                    options['admin_password']
                    if datos['usa_password_admin']
                    else options['password']
                )

                user, _ = User.objects.update_or_create(
                    username=username,
                    defaults={
                        'first_name': datos['first_name'],
                        'last_name': datos['last_name'],
                        'is_active': True,
                        'is_staff': datos['es_admin_django'],
                        'is_superuser': datos['es_admin_django'],
                    },
                )
                user.set_password(password)
                user.save()

                PerfilUsuario.objects.update_or_create(
                    usuario=user,
                    defaults={
                        'grupo_ldap': datos['grupo_ldap'],
                        'rol': datos['rol'],
                        'departamento': depto,
                        'departamento_texto': depto.nombre if depto else '',
                        'cedula_identidad': datos['cedula'],
                        'numero_empleado': datos['empleado'],
                    },
                )

                if existia:
                    actualizados += 1
                else:
                    creados += 1

                resumen.append((
                    username,
                    f"{datos['first_name']} {datos['last_name']}".strip() or '—',
                    datos['rol'],
                    depto.codigo if depto else '—',
                    password,
                ))

        self._imprimir_resumen(resumen)
        self.stdout.write('')
        self.stdout.write(
            self.style.SUCCESS(
                f'Usuarios de prueba listos: {creados} creados, '
                f'{actualizados} actualizados (total: {len(resumen)}).'
            )
        )

    def _obtener_departamento(self, codigo):
        depto = Departamento.objects.filter(codigo=codigo).first()
        if depto is None:
            raise CommandError(
                f'No existe el departamento con código "{codigo}". '
                'Ejecuta "python manage.py poblar_departamentos" primero.'
            )
        return depto

    def _borrar(self, usernames):
        eliminados = 0
        for username in usernames:
            borrados, _ = User.objects.filter(username=username).delete()
            if borrados:
                eliminados += 1
                self.stdout.write(f'  Eliminado: {username}')
        self.stdout.write(
            self.style.SUCCESS(
                f'Usuarios de prueba eliminados: {eliminados}'
            )
        )

    def _imprimir_resumen(self, filas):
        self.stdout.write('')
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                '  Usuario          Nombre                 Rol                  Depto  Contraseña'
            )
        )
        for username, nombre, rol, depto, password in filas:
            self.stdout.write(
                f'  {username:<16} {nombre:<22} {rol:<20} {depto:<6} {password}'
            )
