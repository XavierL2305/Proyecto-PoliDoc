"""Backfill de las firmas de evaluación/autorización/resolución de planillas.

Históricamente la aprobación no volcaba estas firmas en la planilla, de modo
que las planillas ya resueltas (FOR-001 y FOR-005) quedaron con los campos
"Evaluado por", "Autorizado por" y "Resolución" vacíos. Este comando las
reconstruye desde la historia de aprobaciones:

- FOR-001 (creación/eliminación): ``SolicitudAprobacionDocumento``.
- FOR-005 (modificación): ``AprobacionPlanilla`` de la planilla enlazada; si
  no la hay, se usa la cadena documental (edición/nueva versión).

Reglas (idénticas a las del flujo en vivo):

- Un solo paso (jefe → GG): el mismo aprobador firma ambos bloques.
- Dos pasos: el evaluador firma Evaluado y el autorizador Autorizado.
- Resolución: ``Rechazado`` si el paso que resolvió rechazó; ``Aceptado``
  si se aprobó.

Usa la fecha real de la respuesta (``fecha_respuesta``/``fecha_aprobacion``),
solo rellena campos vacíos (con ``--force`` sobrescribe) y descarta el
XLSX/PDF cacheado de cada planilla tocada para que se regenere firmado.

Uso:
    manage.py reparar_planillas_evaluacion              # rellena vacíos
    manage.py reparar_planillas_evaluacion --dry-run    # solo informa
    manage.py reparar_planillas_evaluacion --force      # sobrescribe todo
"""

from django.core.management.base import BaseCommand

from documentos.models import (
    AprobacionPlanilla, PlanillaCambios, SolicitudAprobacionDocumento,
    SolicitudCambio, _valores_autorizacion, _valores_evaluacion,
    mapear_valores_auto,
)
from documentos.services.planillas import invalidar_planilla_solicitud


#: Operación de ``SolicitudCambio`` → acciones de ``SolicitudAprobacionDocumento``
#: que la resolvieron (inversa de ``OPERACION_POR_ACCION``).
ACCIONES_POR_OPERACION = {
    'creacion': ('creacion',),
    'eliminacion': ('inhabilitar',),
    'modificacion': ('edicion', 'version'),
}

#: Estados de un paso que nunca resolvió (no aportan firmas).
ESTADOS_SIN_RESPUESTA = ('esperando', 'cancelada', 'pendiente')


class Command(BaseCommand):
    help = (
        'Rellena Evaluado/Autorizado/Resolución de planillas ya resueltas '
        '(FOR-001 y FOR-005) desde la historia de aprobaciones.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', dest='dry_run', action='store_true',
            help='Solo informa lo que cambiaría, sin escribir.',
        )
        parser.add_argument(
            '--force', dest='force', action='store_true',
            help='Sobrescribe firmas ya presentes (por defecto solo se rellena vacío).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        revisadas = actualizadas = omitidas = sin_historia = 0

        solicitudes = SolicitudCambio.objects.filter(
            estado__in=('aprobado', 'rechazado'),
            documento__isnull=False,
            formato__isnull=False,
        ).select_related('documento', 'formato').order_by('id')

        for sol in solicitudes:
            revisadas += 1
            valores = self._valores_de_historia(sol)
            if not valores:
                sin_historia += 1
                continue
            resultado = self._aplicar(sol, valores, force=force, dry_run=dry_run)
            if resultado == 'actualizada':
                actualizadas += 1
            else:
                omitidas += 1

        prefijo = '[dry-run] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefijo}Planillas revisadas: {revisadas} · '
            f'firmas completadas: {actualizadas} · '
            f'ya firmadas (omitidas): {omitidas} · '
            f'sin historia de aprobación: {sin_historia}'
        ))

    # ------------------------------------------------------------------
    # Historia de aprobación → valores de firmas
    # ------------------------------------------------------------------
    def _valores_de_historia(self, sol):
        """Construye los valores de firmas desde la historia, o ``None``."""
        planilla = PlanillaCambios.objects.filter(
            solicitud_cambio=sol,
        ).order_by('-id').first()
        if planilla:
            pasos = AprobacionPlanilla.objects.filter(
                planilla=planilla,
            ).exclude(estado__in=ESTADOS_SIN_RESPUESTA).order_by(
                'fecha_aprobacion', 'id',
            )
            valores = self._reconstruir(pasos, fecha_attr='fecha_aprobacion')
            if valores:
                return valores
        # Sin planilla (o sin pasos resueltos): cadena documental.
        acciones = ACCIONES_POR_OPERACION.get(sol.operacion, ())
        if not acciones:
            return None
        pasos = SolicitudAprobacionDocumento.objects.filter(
            documento=sol.documento, accion_solicitud__in=acciones,
        ).exclude(estado__in=ESTADOS_SIN_RESPUESTA).order_by(
            'fecha_respuesta', 'id',
        )
        return self._reconstruir(pasos, fecha_attr='fecha_respuesta')

    def _reconstruir(self, pasos, fecha_attr):
        """Reglas de firmas: un paso firma ambos; dos pasos, cada cual lo suyo."""
        evaluado = next((p for p in pasos if p.rol_aprobador == 'evaluador'), None)
        autorizado = next((p for p in pasos if p.rol_aprobador == 'autorizador'), None)
        valores = {}
        if evaluado and evaluado.aprobador:
            valores.update(_valores_evaluacion(
                evaluado.aprobador, getattr(evaluado, fecha_attr, None),
            ))
        if autorizado and autorizado.aprobador:
            fecha = getattr(autorizado, fecha_attr, None)
            valores.update(_valores_autorizacion(autorizado.aprobador, fecha))
            if not evaluado:
                # Un solo paso (jefe → GG): el mismo firma ambos bloques.
                valores.update(_valores_evaluacion(autorizado.aprobador, fecha))
        resuelta = autorizado or evaluado
        if resuelta:
            valores['resolucion'] = (
                'Rechazado' if resuelta.estado == 'rechazada' else 'Aceptado'
            )
        return valores

    # ------------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------------
    def _aplicar(self, sol, valores, *, force, dry_run):
        """Vuelca las firmas vacías en la solicitud. Devuelve el resultado."""
        actuales = dict(sol.respuestas or {})
        # Solo los orígenes de firmas: el resto de campos auto no se toca.
        nuevos = mapear_valores_auto(sol.formato, valores)
        pendientes = {
            clave: valor for clave, valor in nuevos.items()
            if force or not actuales.get(clave)
        }
        if not pendientes:
            return 'omitida'
        self.stdout.write(
            f'  {sol.codigo} ({sol.get_operacion_display()}): '
            + ', '.join(sorted(pendientes))
        )
        if dry_run:
            return 'actualizada'
        actuales.update(pendientes)
        sol.respuestas = actuales
        sol.save(update_fields=['respuestas', 'fecha_modificacion'])
        # El XLSX/PDF cacheado quedó sin firmas: regenerarlo en la descarga.
        invalidar_planilla_solicitud(sol)
        return 'actualizada'
