# Backfill del snapshot de versión/edición del formato en SolicitudCambio.

from django.db import migrations


def backfill(apps, schema_editor):
    SolicitudCambio = apps.get_model('documentos', 'SolicitudCambio')
    for solicitud in SolicitudCambio.objects.select_related('formato').iterator():
        formato = solicitud.formato
        if not formato:
            continue
        campos = []
        if not solicitud.formato_version:
            solicitud.formato_version = formato.version or ''
            campos.append('formato_version')
        if not solicitud.formato_edicion:
            solicitud.formato_edicion = formato.edicion or ''
            campos.append('formato_edicion')
        if campos:
            solicitud.save(update_fields=campos)


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0030_solicitudcambio_formato_edicion_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, revertir),
    ]
