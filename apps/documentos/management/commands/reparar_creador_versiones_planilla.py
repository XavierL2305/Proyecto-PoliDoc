"""Corrige el autor (``creado_por``) de las versiones generadas por planillas.

Antes de este cambio, ``aplicar_planilla_al_documento`` guardaba como creador
de la nueva ``VersionDocumento`` al usuario que **aprobaba** la planilla
(p. ej. el jefe de Gerencia General) en lugar de a quien la elaboró
(``PlanillaCambios.responsable``). Este comando reconstruye ese dato desde la
planilla aprobada y es idempotente.

Uso:
    manage.py reparar_creador_versiones_planilla --dry-run
    manage.py reparar_creador_versiones_planilla
"""

from django.core.management.base import BaseCommand

from documentos.models import PlanillaCambios, VersionDocumento


class Command(BaseCommand):
    help = (
        'Corrige el creador de las versiones generadas por planillas: debe '
        'ser el responsable de la planilla, no quien la aprueba.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', dest='dry_run', action='store_true',
            help='Solo informa lo que cambiaría, sin escribir.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Versión creada por cada planilla aprobada. Se identifica por el
        # texto de cambios (incluye el código de la planilla) y, si no
        # aparece, por el número de versión destino.
        objetivos = {}
        planillas = PlanillaCambios.objects.filter(
            estado='aprobado', documento__isnull=False,
        ).select_related('documento', 'responsable').order_by(
            'fecha_aprobacion', 'id',
        )
        for planilla in planillas:
            version = VersionDocumento.objects.filter(
                documento=planilla.documento,
                cambios__contains=f'Planilla de Cambios {planilla.codigo}',
            ).order_by('fecha_creacion').first()
            if version is None and planilla.version_destino:
                version = VersionDocumento.objects.filter(
                    documento=planilla.documento,
                    numero_version=planilla.version_destino,
                ).order_by('fecha_creacion').first()
            if version is not None:
                objetivos[version.pk] = (version, planilla)

        revisadas = len(objetivos)
        corregidas = omitidas = 0
        for version, planilla in objetivos.values():
            if version.creado_por_id == planilla.responsable_id:
                omitidas += 1
                continue
            self.stdout.write(
                f'  {planilla.documento.codigo} v{version.numero_version}: '
                f'{version.creado_por.username} → {planilla.responsable.username} '
                f'(planilla {planilla.codigo})'
            )
            if not dry_run:
                version.creado_por = planilla.responsable
                version.save(update_fields=['creado_por'])
            corregidas += 1

        prefijo = '[dry-run] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefijo}Versiones de planilla revisadas: {revisadas} · '
            f'corregidas: {corregidas} · ya correctas: {omitidas}'
        ))
