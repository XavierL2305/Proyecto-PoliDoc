from django.core.management.base import BaseCommand

from documentos.models import Documento


class Command(BaseCommand):
    help = (
        'Reclasifica como plantilla (es_plantilla=True) los documentos de tipo '
        "'formulario' que aún no lo son. Por defecto solo muestra el reporte; "
        'usa --apply para escribir en la BD tras revisar.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true', dest='apply',
            help='Escribe es_plantilla=True en los documentos listados.',
        )

    def handle(self, *args, **options):
        candidatos = (
            Documento.objects.filter(tipo__key='formulario', es_plantilla=False)
            .select_related('departamento', 'tipo')
            .order_by('codigo')
        )
        total = candidatos.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                'No hay documentos de tipo formulario sin marcar como plantilla.'
            ))
            return

        self.stdout.write(
            f'Documentos de tipo formulario sin marcar como plantilla: {total}'
        )
        self.stdout.write(
            f'{"CÓDIGO":<24} {"NOMBRE":<50} {"ESTADO":<14} {"DEPTO":<10}'
        )
        self.stdout.write('-' * 98)
        for doc in candidatos:
            depto = doc.departamento.codigo if doc.departamento else '—'
            self.stdout.write(
                f'{doc.codigo:<24} {doc.nombre[:48]:<50} '
                f'{doc.estado:<14} {depto:<10}'
            )

        if not options['apply']:
            self.stdout.write(self.style.WARNING(
                '\nDRY-RUN: nada fue modificado. Revisa el listado y ejecuta '
                'con --apply para reclasificar estos documentos.'
            ))
            return

        actualizados = candidatos.update(es_plantilla=True)
        self.stdout.write(self.style.SUCCESS(
            f'{actualizados} documento(s) marcado(s) como plantilla.'
        ))
