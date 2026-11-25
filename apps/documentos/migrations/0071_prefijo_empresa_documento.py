from django.db import migrations, models


def activar_prefijo_empresa_dpr(apps, schema_editor):
    TipoDocumento = apps.get_model('documentos', 'TipoDocumento')
    TipoDocumento.objects.filter(key='descripcion_procesos').update(
        usa_prefijo_empresa=True,
        sin_prefijo_depto=False,
    )


def revertir_prefijo_empresa_dpr(apps, schema_editor):
    TipoDocumento = apps.get_model('documentos', 'TipoDocumento')
    TipoDocumento.objects.filter(key='descripcion_procesos').update(
        usa_prefijo_empresa=False,
        sin_prefijo_depto=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0070_documentosincodigo_aprobacion'),
    ]

    operations = [
        migrations.AddField(
            model_name='tipodocumento',
            name='usa_prefijo_empresa',
            field=models.BooleanField(
                default=False,
                help_text='El código lleva prefijo de empresa (PTCA, PTH, FP, FS) con secuencia independiente por empresa (ej. PTCA-DPR-001).',
                verbose_name='Usa prefijo de empresa',
            ),
        ),
        migrations.AddField(
            model_name='documento',
            name='empresa',
            field=models.CharField(
                blank=True,
                choices=[
                    ('PTCA', 'Policlínica Táchira, C.A.'),
                    ('PTH', 'Policlínica Táchira Hospitalización, C.A.'),
                    ('FP', 'Farmacia Policlínica, C.A.'),
                    ('FS', 'Fuente de Soda Policlínica Táchira, C.A.'),
                ],
                db_index=True,
                default='',
                help_text='Empresa del grupo a la que pertenece el documento (cuando el tipo usa prefijo de empresa).',
                max_length=10,
                verbose_name='Empresa',
            ),
        ),
        migrations.RunPython(
            activar_prefijo_empresa_dpr,
            revertir_prefijo_empresa_dpr,
        ),
    ]
