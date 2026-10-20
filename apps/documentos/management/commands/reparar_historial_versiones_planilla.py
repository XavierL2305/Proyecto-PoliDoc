"""Repara el rastro documental de las versiones creadas por planillas.

Cubre dos defectos del flujo de aprobación de planillas (SGC-FOR-005):

1. La nueva ``VersionDocumento`` no se registraba en
   ``Documento.historial_cambios``: el detalle seguía mostrando solo la
   versión de creación.
2. Si la planilla reemplazaba el archivo, ``Documento.archivo_pdf`` seguía
   apuntando al PDF de la versión anterior y la vista previa servía contenido
   obsoleto. Se limpia para que ``asegurar_pdf`` lo regenere desde el archivo
   vigente.

Idempotente y con ``--dry-run``.

Uso:
    manage.py reparar_historial_versiones_planilla --dry-run
    manage.py reparar_historial_versiones_planilla
"""

from django.core.management.base import BaseCommand

from documentos.models import (
    AprobacionPlanilla, PlanillaCambios, VersionDocumento,
)
from documentos.services.planillas import (
    agregar_linea_historial, linea_historial_planilla,
)


class Command(BaseCommand):
    help = (
        'Registra en el historial del documento las versiones creadas por '
        'planillas y descarta el PDF de vista previa que quedó obsoleto.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', dest='dry_run', action='store_true',
            help='Solo informa lo que cambiaría, sin escribir.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        planillas = PlanillaCambios.objects.filter(
            estado='aprobado', documento__isnull=False,
        ).select_related('documento', 'responsable').order_by(
            'fecha_aprobacion', 'id',
        )

        revisadas = historial_agregado = pdf_limpiado = sin_cambios = 0
        for planilla in planillas:
            documento = planilla.documento
            version = VersionDocumento.objects.filter(
                documento=documento,
                cambios__contains=f'Planilla de Cambios {planilla.codigo}',
            ).order_by('fecha_creacion').first()
            if version is None and planilla.version_destino:
                version = VersionDocumento.objects.filter(
                    documento=documento,
                    numero_version=planilla.version_destino,
                ).order_by('fecha_creacion').first()
            if version is None:
                continue
            revisadas += 1
            cambios = 0

            # 1) Historial de cambios del documento.
            if f'Planilla {planilla.codigo}' in (documento.historial_cambios or ''):
                pass
            else:
                aprobacion = AprobacionPlanilla.objects.filter(
                    planilla=planilla, rol_aprobador='autorizador',
                    estado='aprobada',
                ).select_related('aprobador').first()
                linea = linea_historial_planilla(
                    planilla, version.numero_version,
                    aprobacion.aprobador if aprobacion else None,
                )
                self.stdout.write(
                    f'  {documento.codigo}: historial += "{linea}"'
                )
                if not dry_run:
                    agregar_linea_historial(documento, linea)
                    documento.save(update_fields=['historial_cambios'])
                historial_agregado += 1
                cambios += 1

            # 2) PDF de vista previa obsoleto (la planilla cambió el archivo).
            pdf = documento.archivo_pdf
            if (planilla.archivo_propuesto and pdf
                    and f'_v{documento.version_actual}_' not in pdf.name):
                self.stdout.write(
                    f'  {documento.codigo}: se descarta el PDF {pdf.name} '
                    f'(versión vigente: {documento.version_actual})'
                )
                if not dry_run:
                    documento.archivo_pdf = ''
                    documento.save(update_fields=['archivo_pdf'])
                    try:
                        pdf.delete(save=False)
                    except Exception as exc:  # noqa: BLE001
                        self.stderr.write(
                            f'    No se pudo borrar el fichero {pdf.name}: {exc}'
                        )
                pdf_limpiado += 1
                cambios += 1

            if not cambios:
                sin_cambios += 1

        prefijo = '[dry-run] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefijo}Planillas revisadas: {revisadas} · '
            f'historial añadido: {historial_agregado} · '
            f'PDF obsoleto limpiado: {pdf_limpiado} · '
            f'sin cambios: {sin_cambios}'
        ))
