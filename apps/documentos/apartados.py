"""Clasificación de apartados por área (SGC-PRG-001 §5.1 y §36 de CONTEXT).

Módulo **puro** (sin imports de Django ni de modelos): las funciones y
constantes se usan desde el modelo, el comando de integración y la migración
de backfill, sin riesgo de imports circulares ni de arrastrar la app.
"""

VISIBILIDAD_CHOICES = [
    ('todos', 'Todos los trabajadores (autoservicio)'),
    ('departamento', 'Solo el departamento dueño'),
    ('jefaturas', 'Jefaturas y administradores'),
    ('restringido', 'Restringido (lista blanca)'),
]

# Visibilidad por defecto: fail-closed. Un formato nuevo sin clasificar queda
# restringido hasta que un administrador lo configure.
VISIBILIDAD_POR_DEFECTO = 'restringido'

# Mapa por código de formato (fuente de verdad para el backfill y para
# `integrar_formatos_sgc`; debe mantenerse sincronizado con la migración
# congelada `MAPA_CONGELADO`, lo verifica tests_configuracion_formatos).
VISIBILIDAD_POR_CODIGO = {
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


def prefijo_departamento(codigo):
    """Prefijo de departamento de un código (`TH-FOR-001` → `TH`).

    Tolera mayúsculas/minúsculas, espacios y códigos sin prefijo
    (`MANUAL` → ``''``).
    """
    codigo = str(codigo or '').strip()
    if '-' not in codigo:
        return ''
    return codigo.split('-', 1)[0].strip()


def visibilidad_para(codigo):
    """Visibilidad por defecto del código (fallback restringido)."""
    clave = str(codigo or '').strip().upper()
    return VISIBILIDAD_POR_CODIGO.get(clave, VISIBILIDAD_POR_DEFECTO)
