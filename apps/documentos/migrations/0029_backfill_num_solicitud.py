# Backfill de origen_cambio y num_solicitud desde respuestas (SGC-FOR-005).

from django.db import migrations


def _primer_origen(valor):
    if isinstance(valor, (list, tuple)):
        return str(valor[0]) if valor else ''
    if isinstance(valor, str):
        return valor
    return ''


def backfill(apps, schema_editor):
    SolicitudCambio = apps.get_model('documentos', 'SolicitudCambio')
    for solicitud in SolicitudCambio.objects.all().iterator():
        respuestas = solicitud.respuestas or {}
        origen = _primer_origen(respuestas.get('origen_cambio'))
        num = respuestas.get('num_solicitud') or ''
        campos = []
        if not solicitud.origen_cambio and origen:
            solicitud.origen_cambio = origen
            campos.append('origen_cambio')
        if not solicitud.num_solicitud and num:
            solicitud.num_solicitud = str(num)[:20]
            campos.append('num_solicitud')
        if campos:
            solicitud.save(update_fields=campos)


def revertir(apps, schema_editor):
    # No se revierte el backfill (los campos se eliminan en la migración previa).
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0028_solicitudcambio_num_solicitud_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, revertir),
    ]
