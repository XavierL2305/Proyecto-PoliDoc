from django import forms
from django.core.exceptions import ValidationError
from .models import PerfilUsuario


class CedulaForm(forms.Form):
    """Paso 1 de la verificación de cédula contra RRHH.

    Se mantiene separado de ``EditarPerfilForm`` para que buscar/confirmar la
    cédula no descarte los demás datos que el usuario estaba editando.
    """

    cedula_identidad = forms.CharField(
        label='Cédula de identidad',
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'inputmode': 'numeric',
            'autocomplete': 'off',
            'placeholder': 'Ej: 12345678',
        }),
    )

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        super().__init__(*args, **kwargs)
        if instance is None or not instance.pk:
            return
        if instance.cedula_identidad:
            self.fields['cedula_identidad'].initial = instance.cedula_identidad
        usuario = instance.usuario
        es_admin = getattr(instance, 'es_administrador', False) or (
            usuario is not None and usuario.is_superuser
        )
        if instance.cedula_verificada_rrhh and not es_admin:
            self.fields['cedula_identidad'].disabled = True
            self.fields['cedula_identidad'].help_text = (
                'Verificada en RRHH: solo un administrador puede modificarla.'
            )

    def clean_cedula_identidad(self):
        from .services.rrhh_sync import validar_y_limpiar_cedula

        try:
            digitos = validar_y_limpiar_cedula(
                self.cleaned_data.get('cedula_identidad'),
            )
        except ValueError as exc:
            raise ValidationError(str(exc))

        existente = PerfilUsuario.objects.filter(cedula_identidad=digitos)
        if self.instance is not None and self.instance.pk:
            existente = existente.exclude(pk=self.instance.pk)
        if existente.exists():
            raise ValidationError(
                'Esa cédula ya está registrada por otro usuario.',
            )
        return digitos


class EditarPerfilForm(forms.ModelForm):
    email = forms.EmailField(
        required=False,
        label='Correo electrónico',
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'correo@dominio.com'
        }),
    )

    class Meta:
        model = PerfilUsuario
        fields = ['telefono', 'biografia', 'foto_perfil']
        widgets = {
            'telefono': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '+58 412 1234567'
            }),
            'biografia': forms.Textarea(attrs={
                'class': 'form-input form-textarea',
                'rows': 4,
                'placeholder': 'Cuéntanos sobre ti...'
            }),
            'foto_perfil': forms.FileInput(attrs={
                'accept': 'image/jpeg,image/jpg,image/png,image/gif,image/webp'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['email'].initial = self.instance.usuario.email

    def clean_foto_perfil(self):
        foto = self.cleaned_data.get('foto_perfil')
        if not foto:
            return foto

        if foto.size > 5 * 1024 * 1024:
            raise ValidationError('La imagen excede 5MB. Use una imagen más pequeña.')

        allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
        import os
        ext = os.path.splitext(foto.name)[1].lower()
        if ext not in allowed_extensions:
            raise ValidationError('Formato no permitido. Use JPG, JPEG, PNG, GIF o WEBP.')

        return foto
