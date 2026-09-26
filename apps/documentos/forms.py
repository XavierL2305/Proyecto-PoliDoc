from django import forms
from django.contrib.auth.models import User
from .models import (
    Documento, SolicitudDescarga, SolicitudAccesoDocumento,
    PlanillaCambios, AsignacionDocumento, Departamento, TipoDocumento, Periodo,
    Formato, SeccionFormato, CampoFormato, SolicitudCambio,
)
from .icons import ICONOS_DISPONIBLES, ICONO_POR_DEFECTO

TIPO_ARCHIVO_MAP = {
    '.pdf': 'pdf', '.doc': 'doc', '.docx': 'docx',
    '.xls': 'xls', '.xlsx': 'xlsx',
}


class DocumentoForm(forms.ModelForm):
    """Formulario genérico para crear/editar cualquier tipo de documento."""
    documentos_relacionados = forms.ModelMultipleChoiceField(
        queryset=Documento.objects.none(),
        widget=forms.SelectMultiple,
        required=False,
        label='Documentos Relacionados'
    )
    departamentos_afectados = forms.ModelMultipleChoiceField(
        queryset=Departamento.objects.filter(activo=True),
        widget=forms.SelectMultiple,
        required=False,
        label='Departamentos Afectados'
    )
    responsables = forms.ModelMultipleChoiceField(
        queryset=Departamento.objects.filter(activo=True),
        widget=forms.SelectMultiple,
        required=False,
        label='Responsables (áreas)'
    )

    class Meta:
        model = Documento
        fields = ['nombre', 'descripcion', 'archivo', 'es_publico',
                  'periodo_retencion', 'participantes']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-input', 'placeholder': ' '}),
            'descripcion': forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': ' '}),
            'es_publico': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'periodo_retencion': forms.Select(attrs={'class': 'form-input'}),
            'archivo': forms.FileInput(attrs={'class': 'form-input'}),
            'participantes': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        departamento_id = kwargs.pop('departamento_id', None)
        tipo_documento = kwargs.pop('tipo_documento', None)
        super().__init__(*args, **kwargs)
        ayudas = {
            'nombre': 'Nombre con el que se identificará en el catálogo. Sé claro y específico (ej. Procedimiento de Control de Documentos).',
            'descripcion': 'Resume el objetivo y el alcance: para qué sirve y a quién aplica.',
            'archivo': 'Archivo principal del documento (PDF, Word o Excel, máx. 10 MB). Es la versión que se controla y distribuye.',
            'es_publico': 'Si lo marcas, cualquier departamento podrá verlo y solicitarlo; si no, solo el tuyo.',
            'periodo_retencion': 'Tiempo que debe conservarse antes de su disposición final (ISO 15489).',
            'departamentos_afectados': 'Áreas a las que impacta este documento.',
            'responsables': 'Áreas responsables de mantener y aplicar el documento.',
            'participantes': 'Personas, áreas o entidades que participan o están interesadas (etiquetas libres).',
            'documentos_relacionados': 'Documentos que se referencian o complementan.',
        }
        for nombre, texto in ayudas.items():
            if nombre in self.fields:
                self.fields[nombre].help_text = texto
        self.fields['periodo_retencion'].queryset = Periodo.objects.filter(activo=True)
        self.fields['periodo_retencion'].empty_label = 'Sin período'

        tipo_obj = None
        if tipo_documento:
            if isinstance(tipo_documento, str):
                tipo_obj = TipoDocumento.objects.filter(key=tipo_documento).first()
            else:
                tipo_obj = tipo_documento

        if tipo_documento:
            qs = Documento.objects.filter(
                tipo=tipo_documento,
                estado='activo',
            )
            if departamento_id:
                qs = qs.filter(departamento_id=departamento_id)
            self.fields['documentos_relacionados'].queryset = qs
            if tipo_obj and tipo_obj.periodo_retencion and not self.instance.pk:
                self.fields['periodo_retencion'].initial = tipo_obj.periodo_retencion
        else:
            self.fields['documentos_relacionados'].queryset = Documento.objects.none()

        # Secciones opcionales configurables por tipo de documento
        if tipo_obj:
            if not tipo_obj.usa_departamentos_afectados:
                self.fields.pop('departamentos_afectados', None)
            if not tipo_obj.usa_responsables:
                self.fields.pop('responsables', None)
            if not tipo_obj.usa_participantes:
                self.fields.pop('participantes', None)
            if not tipo_obj.usa_documentos_relacionados:
                self.fields.pop('documentos_relacionados', None)

    def clean_participantes(self):
        return self.cleaned_data.get('participantes') or []

    def save(self, commit=True):
        instance = super().save(commit=False)
        archivo = self.cleaned_data.get('archivo')
        if archivo:
            import os
            ext = os.path.splitext(archivo.name)[1].lower()
            instance.tipo_archivo = TIPO_ARCHIVO_MAP.get(ext, 'pdf')
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class SolicitudDescargaForm(forms.ModelForm):
    class Meta:
        model = SolicitudDescarga
        fields = ['motivo']
        widgets = {
            'motivo': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Describa el motivo de la descarga...',
                'class': 'form-input form-textarea'
            }),
        }


class PlanillaCambiosForm(forms.ModelForm):
    departamentos_afectados = forms.ModelMultipleChoiceField(
        queryset=Departamento.objects.filter(activo=True),
        widget=forms.SelectMultiple,
        required=True,
        label='Departamentos Afectados'
    )
    responsables_propuestos = forms.ModelMultipleChoiceField(
        queryset=Departamento.objects.filter(activo=True),
        widget=forms.SelectMultiple,
        required=False,
        label='Responsables Propuestos'
    )
    documentos_asociados_json = forms.CharField(
        required=False, widget=forms.HiddenInput(),
        label='Documentos Asociados (JSON)'
    )

    class Meta:
        model = PlanillaCambios
        fields = [
            'nombre_propuesto', 'descripcion_propuesta', 'archivo_propuesto',
            'departamentos_afectados', 'participantes_propuestos',
        ]
        widgets = {
            'nombre_propuesto': forms.TextInput(attrs={'class': 'form-input'}),
            'descripcion_propuesta': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            'archivo_propuesto': forms.FileInput(attrs={'class': 'form-input'}),
            'participantes_propuestos': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        tipo_documento = kwargs.pop('tipo_documento', None)
        documento = kwargs.pop('documento', None)
        super().__init__(*args, **kwargs)

        tipo_obj = None
        if tipo_documento:
            if isinstance(tipo_documento, str):
                tipo_obj = TipoDocumento.objects.filter(key=tipo_documento).first()
            else:
                tipo_obj = tipo_documento
        tipo_key = tipo_obj.key if tipo_obj else (
            tipo_documento if isinstance(tipo_documento, str) else None
        )

        tiene_deptos = bool(
            documento and documento.pk and documento.departamentos_afectados.exists()
        )
        tiene_responsables = bool(
            documento and documento.pk and documento.responsables.exists()
        )
        tiene_participantes = bool(documento and documento.pk and documento.participantes)
        tiene_documentos = bool(
            documento and documento.pk and documento.documentos_relacionados.exists()
        )

        mostrar_deptos = (tipo_obj.usa_departamentos_afectados if tipo_obj else True) or tiene_deptos
        mostrar_responsables = (tipo_obj.usa_responsables if tipo_obj else True) or tiene_responsables
        mostrar_participantes = (tipo_obj.usa_participantes if tipo_obj else True) or tiene_participantes
        mostrar_documentos = (tipo_obj.usa_documentos_relacionados if tipo_obj else True) or tiene_documentos

        if not mostrar_deptos:
            self.fields.pop('departamentos_afectados', None)
        elif tipo_key in ('formulario', 'normas'):
            self.fields['departamentos_afectados'].required = False

        if not mostrar_responsables:
            self.fields.pop('responsables_propuestos', None)
        if not mostrar_participantes:
            self.fields.pop('participantes_propuestos', None)
        if not mostrar_documentos:
            self.fields.pop('documentos_asociados_json', None)

    def clean_participantes_propuestos(self):
        return self.cleaned_data.get('participantes_propuestos') or []



class EditarDocumentoViaPlanillaForm(forms.Form):
    nombre = forms.CharField(max_length=200, label='Nombre del Documento')
    descripcion = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}), label='Descripción', required=False
    )
    departamento_origen = forms.ModelChoiceField(
        queryset=Departamento.objects.filter(activo=True), label='Departamento Origen'
    )
    tipo_cambio = forms.ChoiceField(
        choices=[('modificacion', 'Modificación'), ('creacion', 'Creación'), ('eliminacion', 'Eliminación')],
        label='Tipo de Cambio'
    )
    justificacion = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}), label='Justificación del Cambio *'
    )
    archivo = forms.FileField(required=False, label='Nuevo Archivo (opcional)')
    version_destino = forms.CharField(max_length=20, disabled=True, required=False, label='Próxima Versión')


class AsignacionDocumentoForm(forms.ModelForm):
    class Meta:
        model = AsignacionDocumento
        fields = ['coordinador', 'puede_leer', 'puede_editar', 'puede_eliminar']
        widgets = {
            'coordinador': forms.Select(attrs={'class': 'form-input'}),
            'puede_leer': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'puede_editar': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'puede_eliminar': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def __init__(self, *args, **kwargs):
        departamento = kwargs.pop('departamento', None)
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(
            is_active=True,
            perfil__rol__in=['coordinador', 'trabajador'],
        )
        if departamento is not None:
            qs = qs.filter(perfil__departamento=departamento)
        else:
            qs = User.objects.none()
        self.fields['coordinador'].queryset = qs


class FiltroDocumentoForm(forms.Form):
    busqueda = forms.CharField(required=False, widget=forms.TextInput(attrs={'placeholder': 'Buscar documento...'}))
    tipo = forms.ChoiceField(
        choices=[('', 'Todos')],
        required=False
    )
    estado = forms.ChoiceField(
        choices=[('', 'Todos')] + [
            ('borrador', 'Borrador'), ('en_revision', 'En Revisión'),
            ('activo', 'Activo'), ('obsoleto', 'Obsoleto'),
        ],
        required=False
    )
    departamento = forms.ModelChoiceField(
        queryset=Departamento.objects.filter(activo=True),
        required=False, empty_label='Todos los departamentos'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tipo'].choices = [('', 'Todos')] + [
            (t.key, t.nombre)
            for t in TipoDocumento.objects.filter(activo=True).exclude(key='').order_by('codigo')
        ]


class TipoDocumentoForm(forms.ModelForm):
    color = forms.ChoiceField(
        choices=[
            ('primary', 'Primary'), ('success', 'Success'),
            ('warning', 'Warning'), ('danger', 'Danger'), ('info', 'Info'),
        ],
        required=False,
        widget=forms.Select(attrs={'class': 'form-input'}),
    )

    class Meta:
        model = TipoDocumento
        fields = [
            'codigo', 'nombre', 'descripcion', 'activo',
            'usa_departamentos_afectados', 'usa_responsables',
            'usa_participantes', 'usa_documentos_relacionados',
            'icono', 'color', 'solo_jefes', 'sin_prefijo_depto',
        ]
        widgets = {
            'codigo': forms.TextInput(attrs={'class': 'form-input', 'placeholder': ' '}),
            'nombre': forms.TextInput(attrs={'class': 'form-input', 'placeholder': ' '}),
            'descripcion': forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': ' '}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'usa_departamentos_afectados': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'usa_responsables': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'usa_participantes': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'usa_documentos_relacionados': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'icono': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'file',
                'list': 'iconos-disponibles',
                'autocomplete': 'off',
            }),
            'solo_jefes': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'sin_prefijo_depto': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def clean_icono(self):
        icono = (self.cleaned_data.get('icono') or '').strip()
        if not icono:
            return ICONO_POR_DEFECTO
        if icono not in ICONOS_DISPONIBLES:
            raise forms.ValidationError(
                'Ícono no válido. Elige uno de la lista de sugerencias.'
            )
        return icono

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['icono'].required = False





class CommaSeparatedUserField(forms.ModelMultipleChoiceField):
    """Acepta IDs separados por comas desde el frontend JS."""
    def clean(self, value):
        if isinstance(value, str):
            value = [v.strip() for v in value.split(',') if v.strip()]
        return super().clean(value)


class DistribucionDocumentoForm(forms.Form):
    usuarios = CommaSeparatedUserField(
        queryset=User.objects.filter(is_active=True).order_by('first_name', 'last_name', 'username'),
        widget=forms.HiddenInput(), label='Destinatarios', required=False
    )
    metodo = forms.ChoiceField(
        choices=[('sistema', 'Sistema'), ('email', 'Email'), ('impreso', 'Impreso')],
        widget=forms.RadioSelect(attrs={'class': 'form-radio'}),
        label='Método de distribución', initial='sistema'
    )
    observaciones = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': ' '}),
        label='Observaciones', required=False
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuarios_list = User.objects.filter(is_active=True).order_by('first_name', 'last_name', 'username')


class DepartamentoForm(forms.ModelForm):
    class Meta:
        model = Departamento
        fields = ['nombre', 'codigo', 'activo']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-input'}),
            'codigo': forms.TextInput(attrs={'class': 'form-input', 'maxlength': 4}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-check'}),
        }


class PeriodoForm(forms.ModelForm):
    class Meta:
        model = Periodo
        fields = ['nombre', 'meses', 'activo', 'orden']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-input', 'placeholder': ' '}),
            'meses': forms.NumberInput(attrs={'class': 'form-input', 'min': 1}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'orden': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
        }

    def clean_meses(self):
        meses = self.cleaned_data.get('meses')
        if meses is not None and meses < 1:
            raise forms.ValidationError('La duración debe ser de al menos 1 mes.')
        return meses


class FormatoForm(forms.ModelForm):
    class Meta:
        model = Formato
        fields = [
            'codigo', 'nombre', 'version', 'edicion', 'fecha_vigencia',
            'procedimiento', 'instructivo_html', 'plantilla_xlsx',
            'aplica_creacion', 'aplica_modificacion', 'aplica_eliminacion', 'activo',
        ]
        widgets = {
            'codigo': forms.TextInput(attrs={'class': 'form-input'}),
            'nombre': forms.TextInput(attrs={'class': 'form-input'}),
            'version': forms.TextInput(attrs={'class': 'form-input'}),
            'edicion': forms.TextInput(attrs={'class': 'form-input'}),
            'fecha_vigencia': forms.DateInput(attrs={'type': 'date', 'class': 'form-input'}),
            'procedimiento': forms.TextInput(attrs={'class': 'form-input'}),
            'instructivo_html': forms.Textarea(attrs={'class': 'form-input', 'rows': 10}),
            'plantilla_xlsx': forms.FileInput(attrs={'class': 'form-input'}),
        }

    def clean_instructivo_html(self):
        from .sanitizers import sanitizar_html
        return sanitizar_html(self.cleaned_data.get('instructivo_html', ''))


class SeccionFormatoForm(forms.ModelForm):
    class Meta:
        model = SeccionFormato
        fields = ['titulo', 'descripcion', 'orden']
        widgets = {
            'titulo': forms.TextInput(attrs={'class': 'form-input'}),
            'descripcion': forms.Textarea(attrs={'class': 'form-input', 'rows': 2}),
            'orden': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['titulo'].help_text = "Nombre de la sección tal como aparecerá en el formulario (ej. 'Datos del Cambio')."
        self.fields['orden'].help_text = "Posición de la sección: las de menor número aparecen más arriba (1, 2, 3…)."
        self.fields['descripcion'].help_text = "Texto opcional que se muestra bajo el título para explicar el contenido de la sección."


class CampoFormatoForm(forms.ModelForm):
    class Meta:
        model = CampoFormato
        fields = [
            'clave', 'etiqueta', 'tipo', 'origen', 'ayuda', 'opciones',
            'requerido', 'incluir_etiqueta', 'ancho', 'orden', 'celda_excel',
            'config_tabla',
        ]
        widgets = {
            'clave': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'nombre_interno'}),
            'etiqueta': forms.TextInput(attrs={'class': 'form-input'}),
            'tipo': forms.Select(attrs={'class': 'form-input'}),
            'ayuda': forms.Textarea(attrs={'class': 'form-input', 'rows': 2}),
            'opciones': forms.HiddenInput(),
            'origen': forms.Select(attrs={'class': 'form-input'}),
            'ancho': forms.Select(attrs={'class': 'form-input'}),
            'orden': forms.NumberInput(attrs={'class': 'form-input', 'min': 0}),
            'celda_excel': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ej. B8'}),
            'config_tabla': forms.HiddenInput(),
        }


class SolicitudCambioForm(forms.ModelForm):
    class Meta:
        model = SolicitudCambio
        fields = ['formato', 'operacion', 'documento', 'archivo_adjunto', 'observaciones']
        widgets = {
            'formato': forms.Select(attrs={'class': 'form-input'}),
            'operacion': forms.Select(attrs={'class': 'form-input'}),
            'documento': forms.HiddenInput(),
            'archivo_adjunto': forms.ClearableFileInput(attrs={'class': 'form-input'}),
            'observaciones': forms.Textarea(attrs={'class': 'form-input', 'rows': 3}),
        }


def validar_respuestas(formato, data, parcial=False):
    """Valida las respuestas del formulario dinámico contra el formato.

    Args:
        formato: Objeto Formato con campos.
        data: QueryDict con los datos POST.
        parcial: Si True, no exige campos requeridos (modo borrador).

    Returns:
        (respuestas_dict, errores_list)
    """
    respuestas = {}
    errores = []
    campos = formato.get_todos_los_campos()

    for campo in campos:
        valor = data.get(f'campo_{campo.clave}', '').strip()

        if campo.tipo == 'checkbox_grupo':
            valores = data.getlist(f'campo_{campo.clave}')
            valor = [v for v in valores if v]

        if campo.tipo == 'tabla':
            import json
            raw = data.get(f'campo_{campo.clave}', '[]')
            try:
                valor = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                valor = []

        es_auto = campo.origen != 'usuario'

        if campo.requerido and not valor and not parcial and not es_auto:
            errores.append(f'El campo "{campo.etiqueta}" es obligatorio.')
            continue

        if valor and campo.tipo == 'select' and campo.opciones:
            opt_vals = []
            for o in campo.opciones:
                opt_vals.append(o.get('valor', o) if isinstance(o, dict) else o)
            if valor not in opt_vals:
                errores.append(f'El valor "{valor}" no es válido para "{campo.etiqueta}". Opciones: {", ".join(str(v) for v in opt_vals)}')

        if valor:
            respuestas[campo.clave] = valor

    return respuestas, errores


def validar_respuestas_guardadas(formato, respuestas):
    """Valida respuestas guardadas (dict) contra el formato. Solo requiere campos de usuario.

    Returns:
        errores_list
    """
    errores = []
    campos = formato.get_todos_los_campos()

    for campo in campos:
        if campo.origen != 'usuario':
            continue
        valor = respuestas.get(campo.clave, '')
        if isinstance(valor, list):
            valor = [v for v in valor if v]
        if not isinstance(valor, list) and campo.tipo == 'checkbox_grupo':
            valor = []

        if campo.requerido and not valor:
            errores.append(f'El campo "{campo.etiqueta}" es obligatorio.')
            continue

        if valor and campo.tipo == 'select' and campo.opciones:
            opt_vals = []
            for o in campo.opciones:
                opt_vals.append(o.get('valor', o) if isinstance(o, dict) else o)
            if valor not in opt_vals:
                errores.append(f'El valor "{valor}" no es válido para "{campo.etiqueta}".')

    return errores
