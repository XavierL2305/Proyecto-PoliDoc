# Data migration: backfill de AprobacionPlanilla.fecha_activacion.
#
# Las filas existentes en estado 'pendiente' no tienen registro de cuándo se
# activaron (el campo es nuevo). Se usa ``planilla.fecha_creacion`` como
# aproximación: la cadena de aprobación se crea en el mismo momento que la
# planilla, salvo el paso del autorizador (promovido al aprobar el evaluador),
# que en la práctica es el mismo día. Suficiente para que el escalado por
# antigüedad arranque con datos reales y no con NULL.

from django.db import migrations


def backfill_fecha_activacion(apps, schema_editor):
    AprobacionPlanilla = apps.get_model('documentos', 'AprobacionPlanilla')
    pendientes = AprobacionPlanilla.objects.filter(
        estado='pendiente', fecha_activacion__isnull=True,
    ).select_related('planilla')
    for paso in pendientes.iterator():
        # ``planilla__fecha_creacion`` no sirve vía F() (join en UPDATE).
        paso.fecha_activacion = paso.planilla.fecha_creacion
        paso.save(update_fields=['fecha_activacion'])


def reverse_backfill(apps, schema_editor):
    AprobacionPlanilla = apps.get_model('documentos', 'AprobacionPlanilla')
    AprobacionPlanilla.objects.update(fecha_activacion=None)


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0051_aprobacionplanilla_fecha_activacion_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_fecha_activacion, reverse_backfill),
    ]
