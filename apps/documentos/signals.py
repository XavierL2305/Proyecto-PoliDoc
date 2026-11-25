import logging
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save, post_delete, pre_save, pre_delete
from django.dispatch import receiver

from .models import (
    Documento, SolicitudDescarga, AprobacionDescarga,
    AsignacionDocumento, SolicitudAccesoDocumento,
    PlanillaCambios, AprobacionPlanilla,
    Departamento, DistribucionDocumento,
    TipoDocumento, Periodo, DocumentoSinCodigo,
)
from .cache_utils import (
    invalidar_tipos, invalidar_periodos, invalidar_departamentos,
)
from auditoria.services import (
    registrar_creacion_documento,
    registrar_edicion_documento,
    registrar_eliminacion_documento,
    registrar_solicitud_descarga,
    registrar_aprobacion_descarga,
    registrar_asignacion_documento,
    registrar_transferencia_propiedad
)

logger = logging.getLogger(__name__)

# =============================================================================
# SIGNALS PARA CREACIÓN/EDICIÓN DE DOCUMENTOS
# =============================================================================

@receiver(pre_save, sender=Documento)
def capturar_estado_previo(sender, instance, **kwargs):
    """Guarda estado y clasificación previos en la propia instancia para
    auditarlos en el post_save.

    Antes había dos handlers (un pre_save para la clasificación y un post_save
    para el estado) que hacían DOS SELECT por cada guardado. Ahora un único
    SELECT alimenta ambos valores, y ya no se usan diccionarios globales (que
    retenían referencias a instancias entre peticiones).
    """
    if not instance.pk:
        return
    try:
        previo = (
            sender.objects.filter(pk=instance.pk)
            .values('estado', 'clasificacion')
            .first()
        )
    except Exception:  # noqa: BLE001 - nunca romper el guardado por auditar
        previo = None
    if previo:
        instance._estado_anterior = previo['estado']
        instance._clasificacion_anterior = previo['clasificacion']


def documento_creado_o_editado(sender, instance, created, **kwargs):
    if getattr(instance, '_auditoria_explicita', False):
        return
    try:
        if created:
            registrar_creacion_documento(
                usuario=instance.propietario,
                recurso_tipo=instance.tipo_id or '',
                recurso_id=instance.id,
                codigo=instance.codigo,
                nombre=instance.nombre,
                estado_posterior=instance.estado,
                es_plantilla=instance.es_plantilla,
                asincrono=True,
            )
            logger.debug(f"Signal creación: {instance.tipo_id} #{instance.id}")
        else:
            estado_anterior = getattr(instance, '_estado_anterior', None)
            clasificacion_anterior = getattr(instance, '_clasificacion_anterior', None)
            cambios = {'estado': instance.estado}
            valores_anteriores = {}
            if (clasificacion_anterior is not None
                    and clasificacion_anterior != instance.clasificacion):
                cambios['clasificacion'] = instance.clasificacion
                valores_anteriores['clasificacion'] = clasificacion_anterior
            registrar_edicion_documento(
                usuario=instance.propietario,
                recurso_tipo=instance.tipo_id or '',
                recurso_id=instance.id,
                codigo=instance.codigo,
                nombre=instance.nombre,
                estado_anterior=estado_anterior,
                estado_posterior=instance.estado,
                version_nueva=instance.version_actual,
                cambios=cambios,
                valores_anteriores=valores_anteriores,
                asincrono=True,
            )
            logger.debug(f"Signal edición: {instance.tipo_id} #{instance.id}")
    except Exception as e:
        logger.error(f"Error en signal documento ({instance.id}): {e}")


post_save.connect(documento_creado_o_editado, sender=Documento)


# =============================================================================
# SIGNALS SGC-FOR-004 — ACTA DE DESINCORPORACIÓN
# =============================================================================

@receiver(post_save, sender=Documento)
def anular_acta_por_reactivacion(sender, instance, **kwargs):
    """Anula los actas abiertas que listan ``instance`` como registro.

    Cubre TODAS las rutas que devuelven un documento de ``obsoleto`` a
    ``activo`` (reactivación, renovación de vencimiento, aprobación de
    planilla…): el acta ya no describiría la realidad. El pre_save
    ``capturar_estado_previo`` deja el estado anterior en la instancia.
    """
    if kwargs.get('created'):
        return
    update_fields = kwargs.get('update_fields')
    # Un guardado parcial que no toca ``estado`` no puede ser una reactivación
    # (evita anular por una instancia en memoria desactualizada).
    if update_fields is not None and 'estado' not in update_fields:
        return
    anterior = getattr(instance, '_estado_anterior', None)
    if anterior != 'obsoleto' or instance.estado == 'obsoleto':
        return
    try:
        from .services.for_004 import anular_por_reactivacion

        anular_por_reactivacion(instance)
    except Exception as exc:  # noqa: BLE001 - la anulación no rompe el guardado
        logger.error(
            'No se pudieron anular actas al reactivar %s: %s',
            instance.codigo, exc,
        )


@receiver(pre_delete, sender=Documento)
def anular_acta_por_eliminacion(sender, instance, **kwargs):
    """Anula actas abiertas cuyo padre o registro es el documento borrado.

    Se usa ``pre_delete`` (no ``post_delete``) porque Django nulifica los FK
    ``SET_NULL`` del acta **después** de ``pre_delete`` y antes de
    ``post_delete``: en ``post_delete`` ya no se podría identificar el acta.
    """
    try:
        from .services.for_004 import anular_por_eliminacion

        anular_por_eliminacion(instance)
    except Exception as exc:  # noqa: BLE001 - la anulación no rompe el borrado
        logger.error(
            'No se pudieron anular actas al eliminar %s: %s',
            instance.codigo, exc,
        )


# =============================================================================
# SIGNAL PARA SOLICITUDES DE DESCARGA
# =============================================================================

@receiver(post_save, sender=SolicitudDescarga)
def solicitud_descarga_creada(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        registrar_solicitud_descarga(
            usuario=instance.solicitado_por,
            recurso_tipo=instance.documento.tipo_id or '',
            recurso_id=instance.documento.id,
            solicitud_id=instance.id,
            ip_address=None,
            correlation_id=f'descarga-{instance.id}',
            asincrono=True,
        )
        logger.debug(f"Signal solicitud descarga: #{instance.id}")
    except Exception as e:
        logger.error(f"Error en signal solicitud descarga #{instance.id}: {e}")


# =============================================================================
# SIGNAL PARA APROBACIÓN DE DESCARGA
# =============================================================================

@receiver(post_save, sender=AprobacionDescarga)
def aprobacion_descarga_guardada(sender, instance, created, **kwargs):
    # Solo se audita una DECISIÓN real: la creación de pasos, la activación de
    # un paso en espera (fecha_activacion) o la reasignación no son aprobaciones.
    if created or instance.aprobador_id is None:
        return
    if instance.estado not in ('aprobada', 'rechazada'):
        return
    try:
        registrar_aprobacion_descarga(
            usuario=instance.aprobador,
            recurso_tipo=instance.solicitud.documento.tipo_id or '',
            recurso_id=instance.solicitud.documento.id,
            solicitud_id=instance.solicitud.id,
            aprobada=(instance.estado == 'aprobada'),
            observaciones=instance.observaciones,
            ip_address=None,
            correlation_id=f'descarga-{instance.solicitud_id}',
            asincrono=True,
        )
        logger.debug(f"Signal aprobación descarga: solicitud #{instance.solicitud.id}")
    except Exception as e:
        logger.error(f"Error en signal aprobación descarga: {e}")


# =============================================================================
# SIGNAL PARA ASIGNACIÓN DE DOCUMENTOS
# =============================================================================

@receiver(post_save, sender=AsignacionDocumento)
def asignacion_documento_guardada(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        registrar_asignacion_documento(
            usuario=instance.jefe_asignador,
            recurso_tipo=instance.documento.tipo_id or '',
            recurso_id=instance.documento.id,
            coordinador_username=instance.coordinador.username,
            permisos={
                'leer': instance.puede_leer,
                'editar': instance.puede_editar,
                'eliminar': instance.puede_eliminar,
            },
            ip_address=None,
            asincrono=True,
        )
        logger.debug(f"Signal asignación: #{instance.id}")
    except Exception as e:
        logger.error(f"Error en signal asignación #{instance.id}: {e}")


# =============================================================================
# SIGNAL PARA SOLICITUD DE ACCESO (ISO 27001)
# =============================================================================

@receiver(post_save, sender=SolicitudAccesoDocumento)
def solicitud_acceso_guardada(sender, instance, created, **kwargs):
    try:
        from auditoria.services import encolar_evento_mongo
        registro = {
            'solicitud_id': instance.id,
            'documento_tipo': instance.documento.tipo_id if instance.documento_id else None,
            'documento_id': instance.documento.id if instance.documento_id else None,
            'solicitado_por': instance.solicitante.username if instance.solicitante_id else None,
            'estado': instance.estado,
            'motivo': instance.motivo,
            'created': instance.fecha_solicitud.isoformat() if instance.fecha_solicitud else None,
            'timestamp': __import__('datetime').datetime.utcnow().isoformat(),
            'evento': 'solicitud_acceso_creada' if created else 'solicitud_acceso_actualizada'
        }
        filtro = None if created else {'solicitud_id': instance.id}
        encolar_evento_mongo('solicitudes_acceso', registro, filtro)
        logger.debug(f"Signal solicitud acceso ISO (outbox): #{instance.id}")
    except Exception as e:
        logger.error(f"Error en signal solicitud acceso #{instance.id}: {e}")


# =============================================================================
# SIGNAL PARA PLANILLA DE CAMBIOS (ISO 9001)
# =============================================================================

@receiver(post_save, sender=PlanillaCambios)
def planilla_cambios_guardada(sender, instance, created, **kwargs):
    try:
        from auditoria.services import encolar_evento_mongo
        registro = {
            'planilla_id': instance.id,
            'codigo': instance.codigo,
            'documento_tipo': instance.documento.tipo_id if instance.documento_id else None,
            'documento_id': instance.documento.id if instance.documento_id else None,
            'responsable': instance.responsable.username if instance.responsable_id else None,
            'estado': instance.estado,
            'justificacion': instance.justificacion,
            'clasificacion_propuesta': instance.clasificacion_propuesta,
            'clasificacion_actual_snapshot': instance.clasificacion_actual_snapshot,
            'created': instance.fecha_creacion.isoformat() if instance.fecha_creacion else None,
            'timestamp': __import__('datetime').datetime.utcnow().isoformat(),
            'evento': 'planilla_creada' if created else 'planilla_actualizada'
        }
        filtro = None if created else {'planilla_id': instance.id}
        encolar_evento_mongo('planillas_cambios', registro, filtro)
        logger.debug(f"Signal planilla cambios ISO (outbox): #{instance.id}")
    except Exception as e:
        logger.error(f"Error en signal planilla cambios #{instance.id}: {e}")


# =============================================================================
# SIGNAL PARA APROBACIÓN DE PLANILLA (ISO 9001)
# =============================================================================

@receiver(post_save, sender=AprobacionPlanilla)
def aprobacion_planilla_guardada(sender, instance, created, **kwargs):
    try:
        from auditoria.services import encolar_evento_mongo
        registro = {
            'aprobacion_id': instance.id,
            'planilla_id': instance.planilla.id if instance.planilla_id else None,
            'aprobador': instance.aprobador.username if instance.aprobador_id else None,
            'estado': instance.estado,
            'observaciones': instance.observaciones,
            'created': instance.fecha_aprobacion.isoformat() if instance.fecha_aprobacion else None,
            'timestamp': __import__('datetime').datetime.utcnow().isoformat(),
            'evento': 'aprobacion_planilla_creada' if created else 'aprobacion_planilla_actualizada'
        }
        filtro = None if created else {'aprobacion_id': instance.id}
        encolar_evento_mongo('aprobaciones_planilla', registro, filtro)
        logger.debug(f"Signal aprobación planilla ISO (outbox): #{instance.id}")
    except Exception as e:
        logger.error(f"Error en signal aprobación planilla #{instance.id}: {e}")





# =============================================================================
# SIGNAL PARA DEPARTAMENTOS (ISO 9001)
# =============================================================================

@receiver(post_save, sender=Departamento)
def departamento_guardado(sender, instance, created, **kwargs):
    try:
        from auditoria.services import encolar_evento_mongo
        registro = {
            'departamento_id': instance.id,
            'nombre': instance.nombre,
            'codigo': instance.codigo,
            'jefe_username': instance.jefe.username if instance.jefe_id else None,
            'activo': instance.activo,
            'created': instance.fecha_creacion.isoformat() if instance.fecha_creacion else None,
            'timestamp': __import__('datetime').datetime.utcnow().isoformat(),
            'evento': 'departamento_creado' if created else 'departamento_actualizado'
        }
        filtro = None if created else {'departamento_id': instance.id}
        encolar_evento_mongo('departamentos', registro, filtro)
        logger.debug(f"Signal departamento ISO (outbox): #{instance.id}")
    except Exception as e:
        logger.error(f"Error en signal departamento #{instance.id}: {e}")


# =============================================================================
# SIGNAL PARA DISTRIBUCIÓN DE DOCUMENTOS (ISO 9001)
# =============================================================================

@receiver(post_save, sender=DistribucionDocumento)
def distribucion_documento_guardada(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from auditoria.services import encolar_evento_mongo
        registro = {
            'distribucion_id': instance.id,
            'documento_tipo': instance.documento.tipo_id,
            'documento_id': instance.documento.id,
            'usuario_destino': instance.usuario.username if instance.usuario_id else None,
            'metodo': instance.metodo,
            'distribuido_por': instance.distribuido_por.username if instance.distribuido_por_id else None,
            'observaciones': instance.observaciones,
            'created': instance.fecha_distribucion.isoformat() if instance.fecha_distribucion else None,
            'timestamp': __import__('datetime').datetime.utcnow().isoformat(),
            'evento': 'documento_distribuido'
        }
        encolar_evento_mongo('distribuciones_documento', registro)
        logger.debug(f"Signal distribución documento (outbox): #{instance.id}")
    except Exception as e:
        logger.error(f"Error en signal distribución documento #{instance.id}: {e}")


# =============================================================================
# RESOLUCIÓN DE REFERENCIAS PENDIENTES (ISO)
# =============================================================================

def resolver_referencias(documento):
    """
    Cuando un documento pasa a 'activo', resuelve referencias pendientes
    de otros documentos que lo referencian por código.
    """
    from .models import ReferenciaDocumento
    referencias = ReferenciaDocumento.objects.filter(
        codigo_destino=documento.codigo,
        documento_destino__isnull=True
    )
    for ref in referencias:
        origen = ref.documento
        mismo_depto = origen.departamento_id == documento.departamento_id
        es_accesible = mismo_depto or documento.clasificacion == 'publico'
        if es_accesible:
            ref.documento_destino = documento
            ref.save(update_fields=['documento_destino'])
            origen.documentos_relacionados.add(documento)
            logger.info(
                f"Referencia resuelta: {origen.codigo} → {documento.codigo} "
                f"(ref #{ref.id})"
            )


@receiver(post_save, sender=Documento)
def documento_referencia_resuelta(sender, instance, created, **kwargs):
    """Resuelve referencias pendientes cuando un documento PASA a activo.

    Se ejecuta solo en la transición (creación ya activo, o cambio a activo),
    no en cada guardado de un documento que ya estaba activo: antes cada save
    lanzaba una consulta a ReferenciaDocumento innecesaria.
    """
    if instance.estado != 'activo':
        return
    if not created and getattr(instance, '_estado_anterior', None) == 'activo':
        return
    resolver_referencias(instance)


# =============================================================================
# INVALIDACIÓN DE CACHÉ DE CATÁLOGOS
# =============================================================================

def _invalidar_cache_tipos(sender, instance, **kwargs):
    invalidar_tipos()


def _invalidar_cache_periodos(sender, instance, **kwargs):
    invalidar_periodos()


def _invalidar_cache_departamentos(sender, instance, **kwargs):
    invalidar_departamentos()


for _sender, _handler in (
    (TipoDocumento, _invalidar_cache_tipos),
    (Periodo, _invalidar_cache_periodos),
    (Departamento, _invalidar_cache_departamentos),
):
    post_save.connect(_handler, sender=_sender)
    post_delete.connect(_handler, sender=_sender)


# =============================================================================
# SIGNAL — REINICIO DE AVISOS DE PURGA DE DOCUMENTOS PERSONALES
# =============================================================================

@receiver(user_logged_in)
def reiniciar_aviso_purga_personal(sender, request, user, **kwargs):
    """Al iniciar sesión se reinicia el contador de inactividad de personales."""
    try:
        from .models import AvisoPurgaPersonal, DocumentoSinCodigo

        borrados, _ = AvisoPurgaPersonal.objects.filter(usuario=user).delete()
        if borrados:
            DocumentoSinCodigo.objects.filter(
                propietario=user, ambito='personal',
                marcado_para_eliminar_en__isnull=False,
            ).update(marcado_para_eliminar_en=None)
    except Exception as exc:  # noqa: BLE001 - nunca romper el login
        logger.error(
            'No se pudo reiniciar el aviso de purga de %s: %s',
            getattr(user, 'username', user), exc,
        )


# =============================================================================
# SIGNALS SGC-FOR-006 — LISTA MAESTRA (registro vivo)
# =============================================================================

def _lista_maestra_habilitada():
    try:
        from .services import for_006 as svc

        return svc.formato_for_006() is not None
    except Exception as exc:  # noqa: BLE001
        logger.error('No se pudo verificar el formato FOR-006: %s', exc)
        return False


@receiver(post_save, sender=Documento)
def lista_maestra_documento_guardado(sender, instance, **kwargs):
    """Inserta/actualiza (o quita) la fila del documento en la Lista Maestra.

    Se ejecuta tras el commit y relee el documento para reflejar su estado
    final. Nunca genera el XLSX: solo actualiza el registro vivo (JSON).
    """
    if not _lista_maestra_habilitada():
        return
    from django.db import transaction

    pk = instance.pk

    def _run():
        try:
            from .models import Documento as _Documento
            from .services import for_006 as svc

            documento = _Documento.objects.filter(pk=pk).first()
            if documento is None:
                return
            if documento.es_plantilla or documento.estado != 'activo':
                svc.quitar_documento(documento)
            else:
                svc.upsert_documento(documento)
        except Exception as exc:  # noqa: BLE001
            logger.error('Lista Maestra (Documento %s): %s', pk, exc)

    transaction.on_commit(_run)


@receiver(post_delete, sender=Documento)
def lista_maestra_documento_eliminado(sender, instance, **kwargs):
    if not _lista_maestra_habilitada():
        return
    from django.db import transaction

    try:
        from .services import for_006 as svc

        clave = svc.clave_documento(instance)
    except Exception as exc:  # noqa: BLE001
        logger.error('Lista Maestra (clave Documento): %s', exc)
        return

    def _run():
        try:
            from .services import for_006 as svc

            registro = svc.registro_lista_maestra()
            if registro is not None:
                svc.quitar_fila(registro, clave)
        except Exception as exc:  # noqa: BLE001
            logger.error('Lista Maestra (eliminar %s): %s', clave, exc)

    transaction.on_commit(_run)


@receiver(post_save, sender=DocumentoSinCodigo)
def lista_maestra_sin_codigo_guardado(sender, instance, **kwargs):
    """Solo los soportes SGC activos entran en la Lista Maestra (Ext)."""
    if not _lista_maestra_habilitada():
        return
    from django.db import transaction

    pk = instance.pk

    def _run():
        try:
            from .models import DocumentoSinCodigo as _Modelo
            from .services import for_006 as svc

            documento = _Modelo.objects.filter(pk=pk).first()
            if documento is None:
                return
            if (documento.ambito == 'sgc' and documento.activo
                    and documento.estado_aprobacion == 'aprobado'):
                svc.upsert_sin_codigo(documento)
            else:
                svc.quitar_sin_codigo(documento)
        except Exception as exc:  # noqa: BLE001
            logger.error('Lista Maestra (DocumentoSinCodigo %s): %s', pk, exc)

    transaction.on_commit(_run)


@receiver(post_delete, sender=DocumentoSinCodigo)
def lista_maestra_sin_codigo_eliminado(sender, instance, **kwargs):
    if not _lista_maestra_habilitada():
        return
    from django.db import transaction

    try:
        from .services import for_006 as svc

        clave = svc.clave_sin_codigo(instance)
    except Exception as exc:  # noqa: BLE001
        logger.error('Lista Maestra (clave DocumentoSinCodigo): %s', exc)
        return

    def _run():
        try:
            from .services import for_006 as svc

            registro = svc.registro_lista_maestra()
            if registro is not None:
                svc.quitar_fila(registro, clave)
        except Exception as exc:  # noqa: BLE001
            logger.error('Lista Maestra (eliminar %s): %s', clave, exc)

    transaction.on_commit(_run)
