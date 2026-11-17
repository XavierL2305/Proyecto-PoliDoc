# Migración hecha a mano: renombra AccesoListaMaestra -> AccesoFormato conservando
# la tabla (RenameModel con db_table idéntica), añade nivel ver/editar, crea el
# versionado y el constraint de un programa anual por formato.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0067_accesolistamaestra'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(
            old_name='AccesoListaMaestra',
            new_name='AccesoFormato',
        ),
        migrations.AddField(
            model_name='accesoformato',
            name='nivel',
            field=models.CharField(
                choices=[('ver', 'Ver'), ('editar', 'Editar')],
                default='editar', max_length=10, verbose_name='Nivel de acceso',
            ),
        ),
        migrations.AlterField(
            model_name='accesoformato',
            name='formato',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='accesos_formato', to='documentos.formato',
                verbose_name='Formato',
            ),
        ),
        migrations.AlterField(
            model_name='accesoformato',
            name='usuario',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='accesos_formato',
                to=settings.AUTH_USER_MODEL, verbose_name='Usuario',
            ),
        ),
        migrations.AlterField(
            model_name='accesoformato',
            name='creado_por',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='accesos_formato_creados',
                to=settings.AUTH_USER_MODEL, verbose_name='Creado por',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='accesoformato',
            name='acceso_lista_formato_usuario_unico',
        ),
        migrations.AddConstraint(
            model_name='accesoformato',
            constraint=models.UniqueConstraint(
                fields=('formato', 'usuario'),
                name='acceso_formato_usuario_unico',
            ),
        ),
        migrations.AlterModelOptions(
            name='accesoformato',
            options={
                'ordering': [
                    'usuario__first_name', 'usuario__last_name',
                    'usuario__username',
                ],
                'verbose_name': 'Acceso a Formato',
                'verbose_name_plural': 'Accesos a Formato',
            },
        ),
        migrations.CreateModel(
            name='VersionRegistroFormato',
            fields=[
                (
                    'id', models.BigAutoField(
                        auto_created=True, primary_key=True,
                        serialize=False, verbose_name='ID',
                    ),
                ),
                ('numero', models.PositiveIntegerField(
                    verbose_name='Número de versión',
                )),
                ('datos', models.JSONField(
                    blank=True, default=dict,
                    help_text='Respuestas y configuración de estructura del momento.',
                    verbose_name='Snapshot',
                )),
                ('resumen', models.TextField(
                    blank=True, default='', verbose_name='Resumen de cambios',
                )),
                ('hash', models.CharField(
                    blank=True, default='', max_length=64, verbose_name='SHA-256',
                )),
                ('creado', models.DateTimeField(auto_now_add=True)),
                (
                    'creado_por', models.ForeignKey(
                        blank=True, null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='versiones_registro_formato',
                        to=settings.AUTH_USER_MODEL, verbose_name='Creado por',
                    ),
                ),
                (
                    'registro', models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='versiones', to='documentos.registroformato',
                        verbose_name='Registro',
                    ),
                ),
            ],
            options={
                'ordering': ['-numero'],
                'verbose_name': 'Versión de Registro de Formato',
                'verbose_name_plural': 'Versiones de Registros de Formato',
            },
        ),
        migrations.AddConstraint(
            model_name='versionregistroformato',
            constraint=models.UniqueConstraint(
                fields=('registro', 'numero'),
                name='version_registro_numero_unico',
            ),
        ),
        migrations.AddConstraint(
            model_name='registroformato',
            constraint=models.UniqueConstraint(
                condition=models.Q(('contexto_tipo', 'programa_revision')),
                fields=('formato', 'contexto_id'),
                name='registro_programa_anual_unico',
            ),
        ),
    ]
