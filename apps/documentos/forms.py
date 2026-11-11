import logging

from django import forms
from django.contrib.auth.models import User
from .models import (
    Documento, SolicitudDescarga, SolicitudAccesoDocumento,
    PlanillaCambios, AsignacionDocumento, Departamento, TipoDocumento, Periodo,
    Formato, SeccionFormato, CampoFormato, SolicitudCambio, RegistroFormato,
)
from .icons import ICONOS_DISPONIBLES, ICONO_POR_DEFECTO

logger = logging.getLogger(__name__)

class PlantillaForm(forms.ModelForm):
    """Formulario para subir una Plantilla (documento en blanco)."""

    class Meta:
        model = Documento
        fields = ['nombre', 'descripcion', 'archivo', 'clasificacion']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ej. Formato de Control de Documentos',
            }),
            'descripcion': forms.Textarea(attrs={
                'class': 'form-input', 'rows': 3, 'placeholder': ' ',
            }),
            'clasificacion': forms.Select(attrs={'class': 'form-input'}),
            'archivo': forms.FileInput(attrs={'class': 'form-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['nombre'].label = 'Nombre de la plantilla'
        self.fields['nombre'].help_text = (
            'Nombre con el que se identificará en el catálogo de plantillas.'
        )
        self.fields['archivo'].label = 'Archivo de la plantilla'
        self.fields['archivo'].help_text = (
            'Documento en blanco (PDF, Word o Excel, máx. 10 MB) que otros '
            'usuarios descargarán y llenarán.'
        )
        self.fields['clasificacion'].help_text = (
            '"Público" es visible para todo el personal de la Policlínica '
            'Táchira (no para el público general); los demás niveles '
            'restringen la consulta y la distribución.'
        )
        self.fields['descripcion'].required = False
        # El archivo en blanco es la razón de ser de la plantilla: sin él el
        # catálogo mostraría un "Descargar" que apunta a un FileField vacío.
        self.fields['archivo'].required = True


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
        fields = ['nombre', 'descripcion', 'archivo', 'clasificacion',
                  'periodo_vigencia', 'periodo_retencion', 'participantes', 'afecta_todos']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-input', 'placeholder': ' '}),
            'descripcion': forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': ' '}),
            'clasificacion': forms.Select(attrs={'class': 'form-input'}),
            'periodo_vigencia': forms.Select(attrs={'class': 'form-input'}),
            'periodo_retencion': forms.Select(attrs={'class': 'form-input'}),
            'archivo': forms.FileInput(attrs={'class': 'form-input'}),
            'participantes': forms.HiddenInput(),
            'afecta_todos': forms.CheckboxInput(attrs={
                'class': 'form-checkbox', 'onchange': 'toggleAfectaTodos(this)',
            }),
        }

    def __init__(self, *args, **kwargs):
        con_plantilla = kwargs.pop('con_plantilla', False)
        usuario = kwargs.pop('usuario', None)
        plantillas_queryset = kwargs.pop('plantillas_queryset', None)
        departamento_id = kwargs.pop('departamento_id', None)
        tipo_documento = kwargs.pop('tipo_documento', None)
        super().__init__(*args, **kwargs)
        ayudas = {
            'nombre': 'Nombre con el que se identificará en el catálogo. Sé claro y específico (ej. Procedimiento de Control de Documentos).',
            'descripcion': 'Resume el objetivo y el alcance: para qué sirve y a quién aplica.',
            'archivo': 'Archivo principal del documento (PDF, Word o Excel, máx. 10 MB). Es la versión que se controla y distribuye.',
            'clasificacion': '"Público" es visible para todo el personal de la Policlínica Táchira (no para el público general); los demás niveles restringen la consulta y la distribución.',
            'periodo_vigencia': 'Tiempo que el documento permanece vigente antes de requerir revisión/renovación. Puede quedar sin vigencia si el tipo lo permite.',
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
        self.fields['periodo_vigencia'].queryset = Periodo.objects.filter(activo=True)
        self.fields['periodo_vigencia'].empty_label = '— Sin vigencia —'

        tipo_obj = None
        if tipo_documento:
            if isinstance(tipo_documento, str):
                tipo_obj = TipoDocumento.objects.filter(key=tipo_documento).first()
            else:
                tipo_obj = tipo_documento
        self._tipo_obj = tipo_obj

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
            if tipo_obj and tipo_obj.periodo_vigencia and not self.instance.pk:
                self.fields['periodo_vigencia'].initial = tipo_obj.periodo_vigencia
            if tipo_obj and tipo_obj.requiere_vigencia:
                self.fields['periodo_vigencia'].required = True
        else:
            self.fields['documentos_relacionados'].queryset = Documento.objects.none()

        # Secciones opcionales configurables por tipo de documento
        if tipo_obj:
            if not tipo_obj.usa_departamentos_afectados:
                self.fields.pop('departamentos_afectados', None)
                self.fields.pop('afecta_todos', None)
            if not tipo_obj.usa_responsables:
                self.fields.pop('responsables', None)
            if not tipo_obj.usa_participantes:
                self.fields.pop('participantes', None)
            if not tipo_obj.usa_documentos_relacionados:
                self.fields.pop('documentos_relacionados', None)

        # Plantilla de origen (solo al CREAR un Documento nuevo). Exigida por
        # política: todo documento nuevo debe provenir de una plantilla activa
        # del mismo tipo. Los documentos existentes quedan exentos.
        if con_plantilla and not self.instance.pk:
            if plantillas_queryset is not None:
                # La vista calcula el queryset una sola vez (evita duplicar la
                # regla de visibilidad y posibles divergencias).
                plantillas = plantillas_queryset
            else:
                from .permissions import q_documentos_visibles

                plantillas = Documento.objects.filter(
                    es_plantilla=True, estado='activo',
                ).select_related('departamento')
                if tipo_obj:
                    plantillas = plantillas.filter(tipo=tipo_obj)
                else:
                    plantillas = plantillas.none()
                if usuario is not None:
                    plantillas = plantillas.filter(q_documentos_visibles(usuario))
            self.fields['plantilla_origen'] = forms.ModelChoiceField(
                queryset=plantillas,
                required=True,
                label='Plantilla de origen',
                empty_label='Selecciona la plantilla que usarás',
                to_field_name='uuid',
                help_text=(
                    'Documento en blanco del que parte este documento. '
                    'Debe estar activo y ser del mismo tipo.'
                ),
                widget=forms.Select(attrs={'class': 'form-input'}),
            )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('afecta_todos'):
            # "Todos" reemplaza la selección específica.
            cleaned['departamentos_afectados'] = Departamento.objects.none()
        tipo_obj = getattr(self, '_tipo_obj', None)
        if (tipo_obj and tipo_obj.requiere_vigencia
                and not cleaned.get('periodo_vigencia')):
            self.add_error(
                'periodo_vigencia',
                'Este tipo de documento exige un período de vigencia.',
            )
        return cleaned

    def clean_participantes(self):
        return self.cleaned_data.get('participantes') or []

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
    clasificacion_propuesta = forms.ChoiceField(
        choices=[('', '— Sin cambios —')] + list(Documento.CLASIFICACION_CHOICES),
        required=False,
        label='Clasificación Propuesta',
        widget=forms.Select(attrs={'class': 'form-input form-select'}),
    )

    class Meta:
        model = PlanillaCambios
        fields = [
            'nombre_propuesto', 'descripcion_propuesta', 'archivo_propuesto',
            'clasificacion_propuesta', 'departamentos_afectados',
            'participantes_propuestos', 'afecta_todos',
        ]
        widgets = {
            'nombre_propuesto': forms.TextInput(attrs={'class': 'form-input'}),
            'descripcion_propuesta': forms.Textarea(attrs={'rows': 3, 'class': 'form-input'}),
            'archivo_propuesto': forms.FileInput(attrs={'class': 'form-input'}),
            'participantes_propuestos': forms.HiddenInput(),
            'afecta_todos': forms.CheckboxInput(attrs={
                'class': 'form-checkbox', 'onchange': 'toggleAfectaTodos(this)',
            }),
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
            self.fields.pop('afecta_todos', None)
        elif tipo_key in ('formulario', 'normas'):
            self.fields['departamentos_afectados'].required = False

        if not mostrar_responsables:
            self.fields.pop('responsables_propuestos', None)
        if not mostrar_participantes:
            self.fields.pop('participantes_propuestos', None)
        if not mostrar_documentos:
            self.fields.pop('documentos_asociados_json', None)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('afecta_todos'):
            cleaned['departamentos_afectados'] = Departamento.objects.none()
        return cleaned

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
            'periodo_vigencia', 'periodo_retencion', 'requiere_vigencia',
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
                'autocomplete': 'off',
            }),
            'solo_jefes': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'sin_prefijo_depto': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'periodo_vigencia': forms.Select(attrs={'class': 'form-input form-select'}),
            'periodo_retencion': forms.Select(attrs={'class': 'form-input form-select'}),
            'requiere_vigencia': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
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
        periodos = Periodo.objects.filter(activo=True)
        self.fields['periodo_vigencia'].queryset = periodos
        self.fields['periodo_vigencia'].empty_label = '— Sin vigencia —'
        self.fields['periodo_retencion'].queryset = periodos
        self.fields['periodo_retencion'].empty_label = '— Sin retención —'
        self.fields['periodo_vigencia'].required = False
        self.fields['periodo_retencion'].required = False





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
    #: JSON ``[{nombre, email, cargo, organizacion}]`` de destinatarios sin
    #: cuenta en POLIDOC (SGC-FOR-002). Se llena con el editor de la vista.
    externos = forms.CharField(
        widget=forms.HiddenInput(), label='Destinatarios externos',
        required=False, initial='[]'
    )
    metodo = forms.ChoiceField(
        choices=[('sistema', 'Sistema'), ('email', 'Email'), ('impreso', 'Impreso')],
        widget=forms.RadioSelect(attrs={'class': 'form-radio'}),
        label='Método de distribución', initial='sistema'
    )
    #: Adjuntar el archivo al correo (apagado por defecto; la política de
    #: clasificación puede vetarlo y queda registrado).
    adjuntar_pdf = forms.BooleanField(
        required=False, initial=False,
        label='Adjuntar archivo al correo',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )
    observaciones = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': ' '}),
        label='Observaciones', required=False
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuarios_list = User.objects.filter(is_active=True).order_by('first_name', 'last_name', 'username')

    def clean_externos(self):
        """Parsea y valida el JSON de destinatarios externos.

        Cada externo requiere nombre y correo válido (el correo es la única
        vía para enviarle la invitación de firma SGC-FOR-002).
        """
        import json

        crudo = (self.data.get('externos') or '').strip()
        if not crudo:
            self.externos_parsed = []
            return []
        try:
            datos = json.loads(crudo)
        except (ValueError, TypeError):
            raise forms.ValidationError('Los destinatarios externos no son JSON válido.')
        if not isinstance(datos, list):
            raise forms.ValidationError('Los destinatarios externos deben ser una lista.')

        from .services.distribucion import DestinatarioExterno
        validados = []
        errores = []
        for i, item in enumerate(datos, start=1):
            if not isinstance(item, dict):
                errores.append(f'Externo {i}: formato inválido.')
                continue
            nombre = str(item.get('nombre') or '').strip()
            email = str(item.get('email') or '').strip()
            cargo = str(item.get('cargo') or '').strip()
            organizacion = str(item.get('organizacion') or '').strip()
            if not nombre:
                errores.append(f'Externo {i}: el nombre es obligatorio.')
                continue
            if not email:
                errores.append(f'Externo {i} ({nombre}): el correo es obligatorio.')
                continue
            from django.core.validators import validate_email
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                validate_email(email)
            except DjangoValidationError:
                errores.append(f'Externo {i} ({nombre}): el correo "{email}" no es válido.')
                continue
            validados.append(DestinatarioExterno(
                nombre=nombre, email=email, cargo=cargo,
                organizacion=organizacion,
            ))
        if errores:
            raise forms.ValidationError(errores)
        self.externos_parsed = validados
        return validados


class DepartamentoForm(forms.ModelForm):
    class Meta:
        model = Departamento
        fields = ['nombre', 'codigo', 'activo']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'form-input'}),
            'codigo': forms.TextInput(attrs={'class': 'form-input', 'maxlength': 4}),
            'activo': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
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
            'aplica_creacion', 'aplica_modificacion', 'aplica_eliminacion',
            'permite_registros', 'activo',
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

    def save(self, commit=True):
        """Al reemplazar la plantilla se invalida (y borra) el PDF de preview.

        ``convertir_formato_a_pdf_sync`` es idempotente: no regenera el PDF si
        ya existe ``plantilla_pdf``. Sin este reset la vista previa seguiría
        mostrando el XLSX anterior y el constructor mostraría textos viejos.
        """
        pdf_anterior = ''
        storage = None
        if 'plantilla_xlsx' in self.changed_data and self.instance.plantilla_pdf:
            pdf_anterior = self.instance.plantilla_pdf.name or ''
            storage = self.instance.plantilla_pdf.storage
            self.instance.plantilla_pdf = ''

        instancia = super().save(commit=commit)

        if pdf_anterior and storage:
            try:
                storage.delete(pdf_anterior)
            except Exception:  # noqa: BLE001
                logger.warning(
                    'No se pudo borrar el preview PDF anterior (%s) del formato %s',
                    pdf_anterior, instancia.codigo, exc_info=True,
                )
            # Si no hay worker de Celery, la regeneración encolada no llega:
            # se convierte en modo síncrono para que el Constructor muestre la
            # plantilla nueva sin pasar por "Regenerar PDF".
            if commit and instancia.plantilla_xlsx and not instancia.plantilla_pdf:
                from .tasks import convertir_formato_a_pdf_sync
                try:
                    convertir_formato_a_pdf_sync(instancia)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        'No se pudo generar el preview PDF de %s',
                        instancia.codigo, exc_info=True,
                    )
        return instancia


class RegistroFormatoForm(forms.ModelForm):
    """Datos propios de un registro; los campos del formato van aparte."""

    class Meta:
        model = RegistroFormato
        fields = ['titulo', 'descripcion', 'fecha', 'archivo']
        widgets = {
            'titulo': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ej. Plan de acción DPR-005',
            }),
            'descripcion': forms.Textarea(attrs={
                'class': 'form-input', 'rows': 3, 'placeholder': ' ',
            }),
            'fecha': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}),
            'archivo': forms.FileInput(attrs={'class': 'form-input'}),
        }


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
