from django import forms
from django.core.exceptions import ValidationError
from .models import PerfilUsuario


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
