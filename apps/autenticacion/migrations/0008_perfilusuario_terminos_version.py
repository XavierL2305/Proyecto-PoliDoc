from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('autenticacion', '0007_alter_notificacion_tipo'),
    ]

    operations = [
        migrations.AddField(
            model_name='perfilusuario',
            name='terminos_version',
            field=models.CharField(
                blank=True,
                default='',
                max_length=20,
                verbose_name='Versión de los Términos Aceptados',
            ),
        ),
    ]
