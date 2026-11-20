"""Apartados por área en Formato: departamento, visibilidad y contexto.

Fase 1 del plan §36.3 de CONTEXT.txt (SGC-PRG-001). Retrocompatible: agrega
campos con defaults y hace un backfill de los formatos existentes.

El backfill es **autocontenido** (no importa `documentos.apartados`): una
migración no debe depender de código que puede evolucionar después.
"""

from django.db import migrations, models

VISIBILIDAD_CHOICES = [
    ('todos', 'Todos los trabajadores (autoservicio)'),
    ('departamento', 'Solo el departamento dueño'),
    ('jefaturas', 'Jefaturas y administradores'),
    ('restringido', 'Restringido (lista blanca)'),
]

# Copia literal de `documentos.apartados.VISIBILIDAD_POR_CODIGO` vigente al
# crear la migración. La sincronía entre ambos la verifica
# `tests_configuracion_formatos.test_mapas_sincronizados`.
MAPA_CONGELADO = {
    'SGC-FOR-001': 'jefaturas',
    'SGC-FOR-002': 'jefaturas',
    'SGC-FOR-003': 'jefaturas',
    'SGC-FOR-004': 'jefaturas',
    'SGC-FOR-005': 'jefaturas',
    'SGC-FOR-006': 'restringido',
    'SGC-FOR-007': 'restringido',
    'SGC-FOR-009': 'restringido',
    'SGC-FOR-010': 'jefaturas',
    'SGC-FOR-017': 'jefaturas',
}


def _prefijo_mayusculas(codigo):
    """`th-for-001` → `TH`; `MANUAL` → ``''`` (tolerante a espacios/case)."""
    codigo = str(codigo or '').strip()
    if '-' not in codigo:
        return ''
    return codigo.split('-', 1)[0].strip().upper()


def backfill(apps, schema_editor):
    Formato = apps.get_model('documentos', 'Formato')
    Departamento = apps.get_model('documentos', 'Departamento')

    deptos = {d.codigo.upper(): d for d in Departamento.objects.all()}
    for formato in Formato.objects.iterator():
        cambios = {}
        depto = deptos.get(_prefijo_mayusculas(formato.codigo))
        if depto is not None:
            cambios['departamento_id'] = depto.pk
        visibilidad = MAPA_CONGELADO.get(
            str(formato.codigo or '').strip().upper(),
        )
        if visibilidad:
            cambios['visibilidad'] = visibilidad
        if cambios:
            # `.update()` evita `Formato.save()` (saneo de HTML y encolado
            # de la conversión a PDF) y `auto_now`.
            Formato.objects.filter(pk=formato.pk).update(**cambios)


class Migration(migrations.Migration):

    dependencies = [
        ('documentos', '0068_accesoformato_versionregistroformato'),
    ]

    operations = [
        migrations.AddField(
            model_name='formato',
            name='departamento',
            field=models.ForeignKey(
                blank=True,
                help_text='Área dueña del formato (apartado en el que se '
                          'agrupa).',
                null=True,
                on_delete=models.SET_NULL,
                related_name='formatos',
                to='documentos.departamento',
                verbose_name='Departamento',
            ),
        ),
        migrations.AddField(
            model_name='formato',
            name='requiere_contexto_documento',
            field=models.BooleanField(
                default=True, verbose_name='Requiere documento de contexto',
            ),
        ),
        migrations.AddField(
            model_name='formato',
            name='visibilidad',
            field=models.CharField(
                choices=VISIBILIDAD_CHOICES,
                default='restringido',
                max_length=15,
                verbose_name='Visibilidad',
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
