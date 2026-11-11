"""Unifica la firma electrónica en un solo motor (origen genérico).

``FirmaElectronica`` deja de colgar de ``DistribucionDocumento`` y pasa a
identificar el objeto firmado con ``origen_tipo`` + ``origen_id`` (mismo
patrón que ``FormatoRespuesta``). Esto permite firmar distribuciones,
aprobaciones de planillas (FOR-005) y solicitudes (FOR-001) con el mismo
modelo y la misma función de firma.

Backfill: las firmas existentes quedan como ``origen_tipo='distribucion'``
con ``origen_id=str(distribucion_id)``.
"""
from django.db import migrations, models


ORIGEN_DISTRIBUCION = 'distribucion'


def backfill_origen(apps, schema_editor):
    """Copia la FK ``distribucion`` al origen genérico antes de eliminarla."""
    FirmaElectronica = apps.get_model('documentos', 'FirmaElectronica')
    for firma in FirmaElectronica.objects.all().only(
        'id', 'distribucion_id',
    ).iterator():
        if firma.distribucion_id is None:
            continue
        firma.origen_tipo = ORIGEN_DISTRIBUCION
        firma.origen_id = str(firma.distribucion_id)
        firma.save(update_fields=['origen_tipo', 'origen_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0058_firmacredencial_firmaelectronica_formatorespuesta_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='firmaelectronica',
            name='origen_tipo',
            field=models.CharField(
                db_index=True, default='', max_length=30,
                verbose_name='Tipo de origen',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='firmaelectronica',
            name='origen_id',
            field=models.CharField(
                default='', max_length=64, verbose_name='Id de origen',
            ),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_origen, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='firmaelectronica',
            name='tipo',
            field=models.CharField(
                choices=[
                    ('interna', 'Interna (OTP + PIN)'),
                    ('externa', 'Externa (invitación)'),
                    ('sistema', 'Sistema (automática)'),
                ],
                max_length=10, verbose_name='Tipo de firma',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='firmaelectronica',
            name='firma_dist_firmante_unico',
        ),
        migrations.RemoveField(
            model_name='firmaelectronica',
            name='distribucion',
        ),
        migrations.AddConstraint(
            model_name='firmaelectronica',
            constraint=models.UniqueConstraint(
                fields=('origen_tipo', 'origen_id', 'firmante_clave'),
                name='firma_origen_firmante_unico',
            ),
        ),
    ]
