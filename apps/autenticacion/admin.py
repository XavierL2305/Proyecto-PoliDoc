from django.contrib import admin
from .models import PerfilUsuario, Notificacion


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ['usuario', 'rol', 'departamento', 'grupo_ldap', 'cedula_identidad', 'numero_empleado']
    list_filter = ['rol', 'grupo_ldap', 'departamento']
    search_fields = ['usuario__username', 'usuario__first_name', 'usuario__last_name', 'cedula_identidad']
    raw_id_fields = ['usuario', 'departamento']


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ['titulo', 'usuario', 'tipo', 'leida', 'fecha_creacion']
    list_filter = ['tipo', 'leida']
    search_fields = ['titulo', 'mensaje', 'usuario__username']
    raw_id_fields = ['usuario']
