from django.db import migrations, models


def configurar_tipo_organigrama(apps, schema_editor):
    TipoDocumento = apps.get_model('documentos', 'TipoDocumento')
    TipoDocumento.objects.filter(key='organigrama').update(
        solo_jefes=True,
        sin_prefijo_depto=False,
        usa_prefijo_empresa=False,
    )


def revertir_tipo_organigrama(apps, schema_editor):
    TipoDocumento = apps.get_model('documentos', 'TipoDocumento')
    TipoDocumento.objects.filter(key='organigrama').update(
        solo_jefes=False,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0071_prefijo_empresa_documento'),
    ]

    operations = [
        migrations.AddField(
            model_name='versiondocumento',
            name='datos_organigrama',
            field=models.JSONField(
                blank=True,
                null=True,
                verbose_name='Datos del organigrama (JSON)',
            ),
        ),
        migrations.AddField(
            model_name='documento',
            name='revision_edicion',
            field=models.PositiveIntegerField(
                default=0,
                verbose_name='Revisión de edición',
            ),
        ),
        migrations.AddField(
            model_name='documento',
            name='organigrama_sha256',
            field=models.CharField(
                blank=True,
                default='',
                max_length=64,
                verbose_name='SHA-256 del organigrama',
            ),
        ),
        migrations.AddConstraint(
            model_name='documento',
            constraint=models.UniqueConstraint(
                condition=models.Q(('es_plantilla', False), ('tipo', 'organigrama')) & ~models.Q(('estado', 'obsoleto')),
                fields=('tipo',),
                name='uniq_organigrama_corporativo_vigente',
            ),
        ),
        migrations.RunPython(
            configurar_tipo_organigrama,
            revertir_tipo_organigrama,
        ),
    ]
