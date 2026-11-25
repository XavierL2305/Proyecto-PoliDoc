from django.db import migrations, models

TIPOS_UNICOS_POR_EMPRESA = (
    'mapa_procesos',
    'plan_calidad',
    'objetivos',
    'manual_calidad',
    'programa_seguridad',
    'politica_calidad',
)


def activar_tipos_unicos_empresa(apps, schema_editor):
    TipoDocumento = apps.get_model('documentos', 'TipoDocumento')
    TipoDocumento.objects.filter(key__in=TIPOS_UNICOS_POR_EMPRESA).update(
        usa_prefijo_empresa=True,
        unico_por_empresa=True,
        sin_prefijo_depto=False,
    )
    TipoDocumento.objects.filter(key='mapa_procesos').update(solo_jefes=True)


def revertir_tipos_unicos_empresa(apps, schema_editor):
    TipoDocumento = apps.get_model('documentos', 'TipoDocumento')
    TipoDocumento.objects.filter(key__in=TIPOS_UNICOS_POR_EMPRESA).update(
        usa_prefijo_empresa=False,
        unico_por_empresa=False,
    )
    TipoDocumento.objects.filter(key='mapa_procesos').update(solo_jefes=False)


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0072_organigrama_interactivo'),
    ]

    operations = [
        migrations.AddField(
            model_name='tipodocumento',
            name='unico_por_empresa',
            field=models.BooleanField(
                default=False,
                help_text='Solo permite un documento no obsoleto por cada empresa (PTCA, PTH, FP, FS). Requiere prefijo de empresa.',
                verbose_name='Único documento vigente por empresa',
            ),
        ),
        migrations.AddConstraint(
            model_name='documento',
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(('es_plantilla', False), ('tipo__in', TIPOS_UNICOS_POR_EMPRESA))
                    & ~models.Q(('estado', 'obsoleto'))
                    & ~models.Q(('empresa', ''))
                ),
                fields=('tipo', 'empresa'),
                name='uniq_documento_unico_por_empresa_vigente',
            ),
        ),
        migrations.RunPython(
            activar_tipos_unicos_empresa,
            revertir_tipos_unicos_empresa,
        ),
    ]
