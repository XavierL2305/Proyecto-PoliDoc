from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Reajusta el evaluador de las cadenas PENDIENTES cuando el solicitante '
        'es jefe (nueva regla: evalúa SGC, autoriza GG). Idempotente.'
    )

    def _es_jefe(self, usuario):
        return bool(usuario and getattr(getattr(usuario, 'perfil', None), 'es_jefe', False))

    def _aplicar(self, paso, nuevo, creador, documento, planilla=None):
        from documentos.views import _notificar_aprobador
        from autenticacion.models import Notificacion

        anterior = paso.aprobador
        paso.aprobador = nuevo
        paso.estado = 'pendiente' if nuevo else 'esperando'
        paso.save(update_fields=['aprobador', 'estado'])

        # Limpiar la notificación pendiente del evaluador anterior.
        if anterior and documento is not None:
            Notificacion.objects.filter(
                usuario=anterior, tipo='solicitud_aprobacion', leida=False,
                documento=documento,
            ).delete()

        # Renotificar al nuevo evaluador.
        if nuevo:
            _notificar_aprobador(nuevo, creador, documento, es_evaluador=True, planilla=planilla)

    def handle(self, *args, **options):
        from documentos.views import determinar_evaluador
        from documentos.models import SolicitudAprobacionDocumento, AprobacionPlanilla

        cambios = 0

        # --- Documentos ---
        pasos = SolicitudAprobacionDocumento.objects.filter(
            rol_aprobador='evaluador', estado__in=['pendiente', 'esperando'],
        ).select_related('solicitante', 'departamento_origen', 'documento', 'aprobador')
        for paso in pasos:
            if not self._es_jefe(paso.solicitante):
                continue
            nuevo = determinar_evaluador(paso.solicitante, paso.departamento_origen)
            if nuevo == paso.aprobador:
                continue
            self._aplicar(paso, nuevo, paso.solicitante, paso.documento)
            cambios += 1

        # --- Planillas ---
        pasos = AprobacionPlanilla.objects.filter(
            rol_aprobador='evaluador', estado__in=['pendiente', 'esperando'],
        ).select_related('planilla', 'planilla__responsable', 'planilla__documento', 'aprobador')
        for paso in pasos:
            planilla = paso.planilla
            responsable = planilla.responsable if planilla else None
            if not self._es_jefe(responsable):
                continue
            documento = planilla.documento if planilla else None
            departamento = documento.departamento if documento else None
            nuevo = determinar_evaluador(responsable, departamento)
            if nuevo == paso.aprobador:
                continue
            self._aplicar(paso, nuevo, responsable, documento, planilla=planilla)
            cambios += 1

        self.stdout.write(self.style.SUCCESS(f'Cadenas reajustadas: {cambios}'))
