from django.contrib import admin
from .models import (
    Departamento, SubArea, TipoDocumento, Documento, Periodo,
    VersionDocumento, AsignacionDocumento, SolicitudDescarga,
    AprobacionDescarga, SolicitudAccesoDocumento, PlanillaCambios,
    AprobacionPlanilla, DocumentoAsociadoPlanilla,
    DistribucionDocumento, SolicitudAprobacionDocumento,
    ReferenciaDocumento, Formato, SeccionFormato, CampoFormato,
    SolicitudCambio, RegistroFormato, DistribucionRegistro,
    DocumentoSinCodigo, ActaDesincorporacion, AccesoFormato,
    VersionRegistroFormato,
)


@admin.register(Departamento)
class DepartamentoAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'nombre', 'jefe', 'activo']
    list_filter = ['activo']
    search_fields = ['nombre', 'codigo']


@admin.register(SubArea)
class SubAreaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'departamento', 'activo']
    list_filter = ['activo', 'departamento']
    search_fields = ['nombre']


@admin.register(TipoDocumento)
class TipoDocumentoAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'nombre', 'activo', 'fecha_creacion']
    list_filter = ['activo']
    search_fields = ['nombre', 'codigo']


@admin.register(Periodo)
class PeriodoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'meses', 'activo', 'orden']
    list_filter = ['activo']
    ordering = ['orden', 'meses']


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'nombre', 'tipo', 'departamento', 'propietario', 'estado', 'version_actual', 'afecta_todos']
    list_filter = ['estado', 'tipo', 'departamento', 'afecta_todos']
    search_fields = ['nombre', 'codigo']
    filter_horizontal = ['departamentos_afectados', 'documentos_relacionados']


@admin.register(VersionDocumento)
class VersionDocumentoAdmin(admin.ModelAdmin):
    list_display = ['documento', 'numero_version', 'creado_por', 'fecha_creacion', 'activa']
    list_filter = ['activa']
    search_fields = ['documento__codigo']


@admin.register(AsignacionDocumento)
class AsignacionDocumentoAdmin(admin.ModelAdmin):
    list_display = ['documento', 'coordinador', 'jefe_asignador', 'puede_leer', 'puede_editar', 'puede_eliminar', 'activa']
    list_filter = ['activa']
    search_fields = ['coordinador__username', 'documento__codigo']


@admin.register(SolicitudDescarga)
class SolicitudDescargaAdmin(admin.ModelAdmin):
    list_display = ['id', 'documento', 'solicitado_por', 'estado', 'fecha_solicitud']
    list_filter = ['estado']
    search_fields = ['solicitado_por__username', 'documento__codigo']


@admin.register(AprobacionDescarga)
class AprobacionDescargaAdmin(admin.ModelAdmin):
    list_display = ['solicitud', 'aprobador', 'rol_aprobador', 'estado', 'fecha_aprobacion']
    list_filter = ['estado', 'rol_aprobador']
    search_fields = ['aprobador__username']


@admin.register(SolicitudAccesoDocumento)
class SolicitudAccesoDocumentoAdmin(admin.ModelAdmin):
    list_display = ['id', 'documento', 'solicitante', 'jefe_destino', 'estado', 'fecha_solicitud']
    list_filter = ['estado']
    search_fields = ['solicitante__username', 'documento__codigo']


@admin.register(PlanillaCambios)
class PlanillaCambiosAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'documento', 'tipo_cambio', 'responsable', 'estado', 'fecha_creacion']
    list_filter = ['estado', 'tipo_cambio']
    search_fields = ['codigo', 'documento__codigo']
    filter_horizontal = ['departamentos_afectados']


@admin.register(AprobacionPlanilla)
class AprobacionPlanillaAdmin(admin.ModelAdmin):
    list_display = ['planilla', 'aprobador', 'rol_aprobador', 'estado', 'es_final', 'fecha_aprobacion']
    list_filter = ['estado', 'rol_aprobador']
    search_fields = ['aprobador__username']


@admin.register(DocumentoAsociadoPlanilla)
class DocumentoAsociadoPlanillaAdmin(admin.ModelAdmin):
    list_display = ['planilla', 'documento']
    search_fields = ['documento__codigo']





@admin.register(DistribucionDocumento)
class DistribucionDocumentoAdmin(admin.ModelAdmin):
    list_display = ['documento', 'usuario', 'metodo', 'distribuido_por', 'fecha_distribucion']
    list_filter = ['metodo']
    search_fields = ['usuario__username', 'documento__codigo']


@admin.register(DistribucionRegistro)
class DistribucionRegistroAdmin(admin.ModelAdmin):
    list_display = [
        'registro', 'destinatario_nombre', 'metodo', 'estado_acuse',
        'fecha_distribucion', 'revocado',
    ]
    list_filter = ['metodo', 'estado_acuse', 'revocado']
    search_fields = [
        'registro__formato_codigo', 'registro__titulo', 'usuario__username',
        'nombre_externo', 'email_externo',
    ]


@admin.register(SolicitudAprobacionDocumento)
class SolicitudAprobacionDocumentoAdmin(admin.ModelAdmin):
    list_display = ['documento', 'solicitante', 'aprobador', 'rol_aprobador', 'accion_solicitud', 'estado', 'es_final', 'fecha_solicitud']
    list_filter = ['estado', 'accion_solicitud', 'rol_aprobador']
    search_fields = ['documento__codigo', 'solicitante__username']


@admin.register(ReferenciaDocumento)
class ReferenciaDocumentoAdmin(admin.ModelAdmin):
    list_display = ['documento', 'codigo_destino', 'documento_destino', 'creado_por', 'fecha_creacion']
    search_fields = ['documento__codigo', 'codigo_destino']


@admin.register(Formato)
class FormatoAdmin(admin.ModelAdmin):
    list_display = [
        'codigo', 'nombre', 'version', 'edicion', 'fecha_vigencia',
        'departamento', 'visibilidad', 'activo', 'permite_registros',
    ]
    list_filter = ['activo', 'permite_registros', 'visibilidad', 'departamento']
    search_fields = ['codigo', 'nombre']


@admin.register(SeccionFormato)
class SeccionFormatoAdmin(admin.ModelAdmin):
    list_display = ['formato', 'titulo', 'orden']
    list_filter = ['formato']
    ordering = ['formato', 'orden']


@admin.register(CampoFormato)
class CampoFormatoAdmin(admin.ModelAdmin):
    list_display = ['seccion', 'clave', 'etiqueta', 'tipo', 'requerido', 'orden']
    list_filter = ['seccion__formato', 'tipo']
    search_fields = ['clave', 'etiqueta']


@admin.register(SolicitudCambio)
class SolicitudCambioAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'formato', 'operacion', 'solicitante', 'departamento', 'estado', 'fecha_creacion']
    list_filter = ['operacion', 'estado', 'formato']
    search_fields = ['codigo', 'solicitante__username', 'documento__codigo']
    raw_id_fields = ['formato', 'documento', 'solicitante', 'departamento']
    readonly_fields = ['planilla_xlsx', 'planilla_pdf']


@admin.register(RegistroFormato)
class RegistroFormatoAdmin(admin.ModelAdmin):
    list_display = [
        'formato_codigo', 'titulo', 'contexto_tipo', 'contexto_id',
        'responsable', 'estado', 'fecha',
    ]
    list_filter = ['estado', 'formato', 'contexto_tipo']
    search_fields = ['formato_codigo', 'titulo', 'contexto_id']
    readonly_fields = ['uuid', 'hash_integridad', 'creado', 'actualizado']


@admin.register(DocumentoSinCodigo)
class DocumentoSinCodigoAdmin(admin.ModelAdmin):
    list_display = ['titulo', 'ambito', 'propietario', 'activo', 'creado']
    list_filter = ['ambito', 'activo', 'clasificacion']
    search_fields = ['titulo', 'referencia']
    readonly_fields = ['uuid', 'creado', 'actualizado']


@admin.register(ActaDesincorporacion)
class ActaDesincorporacionAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'documento_codigo', 'estado', 'fecha', 'creado_por']
    list_filter = ['estado']
    search_fields = ['codigo', 'documento_codigo', 'documento_nombre']
    readonly_fields = ['uuid', 'creado', 'actualizado']


@admin.register(AccesoFormato)
class AccesoFormatoAdmin(admin.ModelAdmin):
    list_display = ['usuario', 'formato', 'nivel', 'activo', 'fecha_creacion']
    list_filter = ['nivel', 'activo', 'formato']
    search_fields = ['usuario__username', 'formato__codigo']


@admin.register(VersionRegistroFormato)
class VersionRegistroFormatoAdmin(admin.ModelAdmin):
    list_display = ['registro', 'numero', 'creado', 'creado_por']
    readonly_fields = [campo.name for campo in VersionRegistroFormato._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
