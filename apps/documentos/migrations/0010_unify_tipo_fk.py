import django.db.models.deletion
from django.db import migrations, models


def populate_keys(apps, schema_editor):
    TIPOS_INFO = {
        'manual_calidad': 'MSC', 'politica_calidad': 'POL', 'objetivos': 'OBJ',
        'plan_calidad': 'PLC', 'mapa_procesos': 'MAP', 'descripcion_cargos': 'DDC',
        'programa_seguridad': 'PSS', 'organigrama': 'ORG', 'descripcion_procesos': 'DPR',
        'normas': 'NOR', 'procedimientos_generales': 'PRG', 'instructivo_llenado': 'IDL',
        'procedimientos_especificos': 'PRE', 'instrucciones_trabajo': 'IT', 'formulario': 'FOR',
    }
    TipoDocumento = apps.get_model('documentos', 'TipoDocumento')
    for key, codigo in TIPOS_INFO.items():
        TipoDocumento.objects.filter(codigo=codigo).update(key=key)


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0009_add_periodo_model'),
    ]

    operations = [
        migrations.AddField(
            model_name='tipodocumento',
            name='key',
            field=models.SlugField(default='sin_tipo', max_length=40, verbose_name='Clave'),
        ),
        migrations.RunPython(populate_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='tipodocumento',
            name='key',
            field=models.SlugField(max_length=40, unique=True, verbose_name='Clave'),
        ),
        migrations.AlterField(
            model_name='documento',
            name='tipo',
            field=models.ForeignKey(blank=True, db_column='tipo', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='documentos', to='documentos.tipodocumento', verbose_name='Tipo de Documento'),
        ),
    ]
