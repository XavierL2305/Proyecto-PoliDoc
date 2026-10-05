"""Recorre MEDIA_ROOT y hace QA de los PDF con office hermano.

Sirve para auditar conversiones ya generadas. Con ``--reparar`` reconvierte las
que tengan maquetación rota (logo recortado, título huérfano) buscando una
disposición limpia.
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand

from documentos.services.pdf_adaptativo import diagnosticar, reparar

EXTENSIONES_ORIGEN = ('xlsx', 'xls', 'docx', 'doc')


class Command(BaseCommand):
    help = 'QA de los PDF de MEDIA_ROOT; con --reparar reconvierte los defectuosos.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reparar', action='store_true',
            help='Reconvierte los PDF con defectos buscando una disposición limpia.',
        )
        parser.add_argument(
            '--ruta', default='', help='Subdirectorio de MEDIA_ROOT a revisar.',
        )

    def _origen(self, pdf):
        raiz, nombre = os.path.split(pdf)
        base = os.path.splitext(nombre)[0]
        for ext in EXTENSIONES_ORIGEN:
            candidato = os.path.join(raiz, '%s.%s' % (base, ext))
            if os.path.exists(candidato):
                return candidato
        return None

    def handle(self, *args, **opciones):
        media = str(settings.MEDIA_ROOT)
        inicio = os.path.join(media, opciones['ruta']) if opciones['ruta'] else media
        encontrados = con_defectos = reparados = 0

        for raiz, _, archivos in os.walk(inicio):
            for nombre in sorted(archivos):
                if not nombre.lower().endswith('.pdf'):
                    continue
                pdf = os.path.join(raiz, nombre)
                origen = self._origen(pdf)
                if not origen:
                    continue
                encontrados += 1
                etiqueta = os.path.relpath(pdf, media)
                diagnostico = diagnosticar(pdf)
                if diagnostico.limpio:
                    self.stdout.write('OK  %s' % etiqueta)
                    continue
                con_defectos += 1
                self.stdout.write(self.style.WARNING(
                    'DEF %s: %s' % (etiqueta, diagnostico.resumen()),
                ))
                if not opciones['reparar']:
                    continue
                final = reparar(origen, pdf, diagnostico)
                if final.puntuacion < diagnostico.puntuacion:
                    reparados += 1
                    self.stdout.write(self.style.SUCCESS(
                        '    reparado -> %s' % final.resumen(),
                    ))
                else:
                    self.stdout.write('    sin mejora -> %s' % final.resumen())

        self.stdout.write('')
        self.stdout.write(
            'Revisados: %d | con defectos: %d | reparados: %d'
            % (encontrados, con_defectos, reparados),
        )
