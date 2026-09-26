from django.contrib import admin
from .models import (
    Departamento, SubArea, TipoDocumento, Documento, Periodo,
    VersionDocumento, AsignacionDocumento, SolicitudDescarga,
    AprobacionDescarga, SolicitudAccesoDocumento, PlanillaCambios,
    AprobacionPlanilla, DocumentoAsociadoPlanilla,
    DistribucionDocumento, SolicitudAprobacionDocumento,
    ReferenciaDocumento, Formato, SeccionFormato, CampoFormato,
    SolicitudCambio,
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
    list_display = ['codigo', 'nombre', 'tipo', 'departamento', 'propietario', 'estado', 'version_actual']
    list_filter = ['estado', 'tipo', 'departamento']
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
    list_display = ['solicitud', 'aprobador', 'estado', 'fecha_aprobacion']
    list_filter = ['estado']
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
    list_display = ['codigo', 'nombre', 'version', 'edicion', 'fecha_vigencia', 'activo']
    list_filter = ['activo']
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
