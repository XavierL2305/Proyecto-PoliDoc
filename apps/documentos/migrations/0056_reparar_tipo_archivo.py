import os

from django.db import migrations

# Mapa copiado a propósito: una migración no debe depender de constantes del
# modelo que pueden cambiar en el futuro.
TIPO_ARCHIVO_POR_EXTENSION = {
    '.pdf': 'pdf',
    '.doc': 'doc',
    '.docx': 'docx',
    '.xls': 'xls',
    '.xlsx': 'xlsx',
    '.vsdx': 'vsdx',
}


def reparar_tipo_archivo(apps, schema_editor):
    """Alinea `tipo_archivo` con la extensión real del archivo guardado.

    Corrige filas históricas (p. ej. plantillas .xlsx guardadas como 'pdf'
    porque `PlantillaForm` no infería el tipo).
    """
    Documento = apps.get_model('documentos', 'Documento')
    qs = Documento.objects.exclude(
        archivo='',
    ).exclude(
        archivo__isnull=True,
    )
    for documento in qs.iterator():
        nombre = documento.archivo.name or ''
        ext = os.path.splitext(nombre)[1].lower()
        esperado = TIPO_ARCHIVO_POR_EXTENSION.get(ext)
        if esperado and documento.tipo_archivo != esperado:
            Documento.objects.filter(pk=documento.pk).update(
                tipo_archivo=esperado,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0055_plantillas'),
    ]

    operations = [
        migrations.RunPython(
            reparar_tipo_archivo, migrations.RunPython.noop,
        ),
    ]
