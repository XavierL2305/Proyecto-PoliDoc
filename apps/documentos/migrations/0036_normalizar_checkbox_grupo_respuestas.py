# Normaliza respuestas de campos checkbox_grupo guardadas como string → lista.

from django.db import migrations


def normalizar(apps, schema_editor):
    SolicitudCambio = apps.get_model('documentos', 'SolicitudCambio')
    CampoFormato = apps.get_model('documentos', 'CampoFormato')

    for sol in SolicitudCambio.objects.all().iterator():
        respuestas = sol.respuestas
        if not isinstance(respuestas, dict) or not sol.formato_id:
            continue
        claves = list(
            CampoFormato.objects.filter(
                seccion__formato_id=sol.formato_id, tipo='checkbox_grupo',
            ).values_list('clave', flat=True)
        )
        cambio = False
        for clave in claves:
            valor = respuestas.get(clave)
            if isinstance(valor, str) and valor:
                respuestas[clave] = [valor]
                cambio = True
        if cambio:
            sol.respuestas = respuestas
            sol.save(update_fields=['respuestas'])


def revertir(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0035_documento_afecta_todos_planillacambios_afecta_todos'),
    ]

    operations = [
        migrations.RunPython(normalizar, revertir),
    ]
