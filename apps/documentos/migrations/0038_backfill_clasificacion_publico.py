from django.db import migrations


def _marcar_publicos(apps, schema_editor):
    """Backfill: los documentos que ya eran públicos pasan a 'publico'."""
    Documento = apps.get_model('documentos', 'Documento')
    Documento.objects.filter(es_publico=True).update(clasificacion='publico')


def _deshacer(apps, schema_editor):
    """Reversible a la inversa: 'publico' -> es_publico=True."""
    Documento = apps.get_model('documentos', 'Documento')
    Documento.objects.filter(clasificacion='publico').update(es_publico=True)


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0037_documento_clasificacion'),
    ]

    operations = [
        migrations.RunPython(_marcar_publicos, _deshacer),
    ]
