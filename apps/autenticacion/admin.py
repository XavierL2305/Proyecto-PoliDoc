import logging

from django.contrib import admin

from .models import PerfilUsuario, Notificacion
from .services.rrhh_sync import (
    desvincular_datos_rrhh, enmascarar_cedula, validar_y_limpiar_cedula,
)

logger = logging.getLogger(__name__)


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = [
        'usuario', 'rol', 'departamento', 'grupo_ldap',
        'cedula_identidad', 'numero_empleado', 'cargo',
        'departamento_rrhh', 'cedula_verificada_rrhh', 'ultimo_sync_rrhh',
    ]
    list_filter = [
        'rol', 'grupo_ldap', 'departamento', 'cedula_verificada_rrhh',
    ]
    search_fields = ['usuario__username', 'usuario__first_name', 'usuario__last_name', 'cedula_identidad']
    raw_id_fields = ['usuario', 'departamento']
    actions = ['desvincular_rrhh_seleccionados']

    @admin.action(description='Desvincular datos RRHH (cédula, cargo y verificación)')
    def desvincular_rrhh_seleccionados(self, request, queryset):
        desvinculados = 0
        for perfil in queryset.select_related('usuario'):
            res = desvincular_datos_rrhh(
                perfil,
                actor=request.user,
                origen='admin_action',
                limpiar_nombres=False,
            )
            if res['modificado']:
                desvinculados += 1
        self.message_user(
            request,
            f'Se desvincularon los datos de RRHH de {desvinculados} perfil(es).',
        )

    def save_model(self, request, obj, form, change):
        anterior = None
        if change and obj.pk:
            anterior = PerfilUsuario.objects.filter(pk=obj.pk).first()

        # El admin es la única vía para corregir/limpiar una cédula ya
        # verificada: se guarda en forma canónica (dígitos).
        if (obj.cedula_identidad or '').strip():
            try:
                obj.cedula_identidad = validar_y_limpiar_cedula(
                    obj.cedula_identidad,
                )
            except ValueError:
                pass  # legado inválido: el sync nocturno lo normaliza

        cedula_anterior = (
            (anterior.cedula_identidad or '') if anterior else ''
        )
        cedula_cambio = (
            anterior is not None
            and cedula_anterior != (obj.cedula_identidad or '')
        )
        if cedula_cambio:
            # Cambió la cédula: deja de estar verificada hasta el próximo
            # sync/confirmación y se limpia el snapshot de RRHH.
            obj.cedula_verificada_rrhh = False
            obj.cargo = ''
            obj.departamento_rrhh = ''
            obj.ultimo_sync_rrhh = None

        super().save_model(request, obj, form, change)

        if cedula_cambio:
            try:
                from auditoria.services import registrar_log_iso

                registrar_log_iso(
                    usuario=request.user,
                    accion_tipo='edicion',
                    accion_categoria='usuario',
                    recurso_tipo='usuario',
                    recurso_id=obj.pk,
                    recurso_nombre=obj.usuario.username,
                    estado_anterior=enmascarar_cedula(cedula_anterior),
                    estado_posterior=enmascarar_cedula(
                        obj.cedula_identidad,
                    ),
                    detalles={
                        'campo': 'cedula_identidad',
                        'origen': 'admin',
                        'reset_verificacion': True,
                    },
                    resultado_mensaje='Cédula modificada por administrador',
                )
            except Exception:  # noqa: BLE001 - la auditoría no rompe el admin
                logger.warning(
                    'No se pudo auditar el cambio de cédula del perfil %s',
                    obj.pk, exc_info=True,
                )


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ['titulo', 'usuario', 'tipo', 'leida', 'fecha_creacion']
    list_filter = ['tipo', 'leida']
    search_fields = ['titulo', 'mensaje', 'usuario__username']
    raw_id_fields = ['usuario']
