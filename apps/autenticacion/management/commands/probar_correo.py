"""Comando de consola para verificar el envío real o local de correos."""

from django.conf import settings
from django.core import mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Envía un correo de prueba usando el backend configurado en MAILERS.'

    def add_arguments(self, parser):
        parser.add_argument(
            'destinatario',
            help='Correo electrónico de destino para la prueba.',
        )
        parser.add_argument(
            '--asunto',
            default='Prueba de correo — POLIDOC',
            help='Asunto del correo de prueba.',
        )
        parser.add_argument(
            '--con-adjunto',
            action='store_true',
            help='Adjunta un archivo de texto pequeño para validar MIME.',
        )

    def handle(self, *args, **options):
        destinatario = (options['destinatario'] or '').strip()
        if '@' not in destinatario:
            raise CommandError('Indica una dirección de correo válida.')

        proveedor = getattr(settings, 'EMAIL_PROVIDER', 'smtp')
        backend_cfg = settings.MAILERS['default']['BACKEND']
        self.stdout.write(
            f'Proveedor activo: {proveedor} ({backend_cfg}) — '
            f'Remitente: {settings.DEFAULT_FROM_EMAIL}'
        )

        msg = mail.EmailMessage(
            subject=options['asunto'],
            body=(
                'Este es un correo de prueba enviado desde POLIDOC.\n'
                f'Proveedor configurado: {proveedor}\n'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[destinatario],
        )
        if options['con_adjunto']:
            msg.attach('prueba_polidoc.txt', b'Adjunto de prueba POLIDOC\n', 'text/plain')

        try:
            enviados = mail.mailers.default.send_messages([msg])
        except Exception as exc:
            raise CommandError(f'Error al enviar correo: {exc}') from exc

        if enviados != 1:
            raise CommandError(
                f'No se pudo confirmar el envío (enviados={enviados}).'
            )
        self.stdout.write(
            self.style.SUCCESS(f'Correo enviado correctamente a {destinatario}.')
        )
