"""
Centralizado de tipos de documento → metadata.
Reemplaza los diccionarios duplicados que referenciaban modelos separados.
"""

from .models import Documento


TIPOS_INFO = {
    'manual_calidad': {'nombre': 'Manual de la Calidad', 'codigo': 'MSC', 'icono': 'book', 'color': 'info'},
    'politica_calidad': {'nombre': 'Política de la Calidad', 'codigo': 'POL', 'icono': 'scroll', 'color': 'primary'},
    'objetivos': {'nombre': 'Objetivos', 'codigo': 'OBJ', 'icono': 'bullseye', 'color': 'success'},
    'plan_calidad': {'nombre': 'Plan de la Calidad', 'codigo': 'PLC', 'icono': 'calendar-check', 'color': 'warning'},
    'mapa_procesos': {'nombre': 'Mapa de Procesos', 'codigo': 'MAP', 'icono': 'diagram-project', 'color': 'info'},
    'descripcion_cargos': {'nombre': 'Descripción de Cargos', 'codigo': 'DDC', 'icono': 'id-badge', 'color': 'primary'},
    'programa_seguridad': {'nombre': 'Programa de Seguridad y Salud Laboral', 'codigo': 'PSS', 'icono': 'shield-halved', 'color': 'danger'},
    'organigrama': {'nombre': 'Organigrama', 'codigo': 'ORG', 'icono': 'building', 'color': 'success'},
    'descripcion_procesos': {'nombre': 'Descripción de Procesos', 'codigo': 'DPR', 'icono': 'file-lines', 'color': 'warning', 'sin_prefijo_depto': True, 'solo_jefes': True},
    'normas': {'nombre': 'Normas', 'codigo': 'NOR', 'icono': 'gavel', 'color': 'info'},
    'procedimientos_generales': {'nombre': 'Procedimientos Generales', 'codigo': 'PRG', 'icono': 'list-ol', 'color': 'primary'},
    'instructivo_llenado': {'nombre': 'Instructivo de Llenado', 'codigo': 'IDL', 'icono': 'pencil', 'color': 'success'},
    'procedimientos_especificos': {'nombre': 'Procedimientos Específicos', 'codigo': 'PRE', 'icono': 'list-check', 'color': 'warning'},
    'instrucciones_trabajo': {'nombre': 'Instrucciones de Trabajo', 'codigo': 'IT', 'icono': 'wrench', 'color': 'primary'},
    'formulario': {'nombre': 'Formularios', 'codigo': 'FOR', 'icono': 'file-circle-check', 'color': 'warning'},
}

# Compatibilidad: estos nombres se usan en muchos archivos
DOCUMENTO_MODELS = None  # Ya no aplica — usar Documento directamente
DOCUMENTO_TIPOS_INFO = TIPOS_INFO
TIPO_DOCUMENTO_MAP = {k: v['codigo'] for k, v in TIPOS_INFO.items()}


def obtener_documento(tipo, doc_id):
    """Obtiene un documento por tipo (key) y UUID."""
    from .models import Documento
    try:
        return Documento.objects.get(uuid=doc_id, tipo__key=tipo)
    except Documento.DoesNotExist:
        return None


def obtener_modelo(tipo):
    """Retorna None — ya no hay modelos separados. Usar Documento directamente."""
    return None


def todos_los_tipos():
    """Retorna lista de todos los keys de tipos de documento."""
    return list(TIPOS_INFO.keys())
