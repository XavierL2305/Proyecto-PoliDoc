"""Registro de objetos firmables (SES).

Cada clase declara cómo se obtiene el contenido firmado y qué efecto tiene la
firma. Agregar un documento nuevo = una clase aquí; el resto del motor
(:mod:`documentos.services.firmas`) no cambia.
"""
import json

from .firmas import ErrorFirma, Firmable, registrar_firmable


def _json_canonico(datos):
    """Bytes deterministas de una decisión (hash estable y verificable)."""
    return json.dumps(
        datos, sort_keys=True, ensure_ascii=False, default=str,
    ).encode('utf-8')


def _xlsx_solicitud(solicitud):
    """Bytes del XLSX de una ``SolicitudCambio`` enlazada (o ``b''``)."""
    if solicitud is None or not solicitud.planilla_xlsx:
        return b''
    try:
        with solicitud.planilla_xlsx.open('rb') as fh:
            return fh.read()
    except (OSError, ValueError):
        return b''


class _FirmaAprobacionMixin:
    """Datos del firmante y validación comunes a las aprobaciones."""

    def firmante(self, obj, usuario):
        info = super().firmante(obj, usuario)
        from ..models import _cargo_usuario

        info['firmante_cargo'] = _cargo_usuario(usuario)
        return info

    def _validar(self, obj, usuario):
        from ..jefatura import aprobadores_que_repr

        # Se relee el estado de la BD: la instancia pudo resolver en otra
        # petición (o la misma) y no queremos firmar un paso ya cerrado.
        actual = type(obj)._default_manager.filter(pk=obj.pk).values(
            'estado', 'aprobador_id',
        ).first()
        if not actual or actual['estado'] != 'pendiente':
            raise ErrorFirma('Esta aprobación ya fue procesada.')
        if actual['aprobador_id'] not in aprobadores_que_repr(usuario):
            raise ErrorFirma('No tienes una aprobación pendiente para este paso.')

    def puede_firmar(self, obj, usuario):
        self._validar(obj, usuario)

    def despues(self, obj, firma, contexto=None):
        aprobada = bool((firma.datos or {}).get('aprobada', True))
        observaciones = (firma.datos or {}).get('observaciones', '')
        return self._resolver(obj, firma, aprobada, observaciones, contexto)


@registrar_firmable
class FirmaDistribucion(Firmable):
    """FOR-002: firma de conformidad del destinatario de una distribución."""

    tipo = 'distribucion'
    audit_accion = 'firma_distribucion'
    audit_categoria = 'distribucion'
    #: FOR-002 interna exige OTP además del PIN (FOR-001/005 usan solo PIN).
    requiere_otp = True

    def documento(self, obj):
        return obj.documento

    def contenido(self, obj):
        """XLSX de la planilla FOR-002 del lote (versión revisada)."""
        from .distribucion import exportar_planilla_lote

        if not obj.lote:
            return b''
        try:
            return exportar_planilla_lote(obj.lote)
        except ValueError:
            return b''

    def detalles(self, obj):
        return {'distribucion_id': obj.id}

    def despues(self, obj, firma, contexto=None):
        obj.estado_firma = 'firmada'
        obj.save(update_fields=['estado_firma'])
        from .distribucion import regenerar_planilla_de_firma

        regenerar_planilla_de_firma(firma)


@registrar_firmable
class FirmaAprobacionPlanilla(_FirmaAprobacionMixin, Firmable):
    """FOR-005: evaluación/autorización de una planilla de cambios."""

    tipo = 'aprobacion_planilla'
    audit_accion = 'firma_aprobacion'
    audit_categoria = 'documento'

    def documento(self, obj):
        return getattr(obj.planilla, 'documento', None)

    def contenido(self, obj):
        solicitud = getattr(obj.planilla, 'solicitud_cambio', None)
        xlsx = _xlsx_solicitud(solicitud)
        if xlsx:
            return xlsx
        return _json_canonico({
            'flujo': self.tipo,
            'planilla': getattr(obj.planilla, 'codigo', None),
            'documento': getattr(
                getattr(obj.planilla, 'documento', None), 'codigo', None,
            ),
            'aprobacion_id': obj.id,
            'rol': obj.rol_aprobador,
            'es_final': obj.es_final,
            'aprobador': obj.aprobador_id,
        })

    def detalles(self, obj):
        return {
            'aprobacion_id': obj.id,
            'planilla': getattr(obj.planilla, 'codigo', None),
            'rol': obj.rol_aprobador,
            'es_final': obj.es_final,
        }

    def _resolver(self, obj, firma, aprobada, observaciones, contexto):
        from .aprobaciones import resolver_aprobacion_planilla

        resultado = resolver_aprobacion_planilla(
            obj, firma.firmante, aprobada=aprobada,
            observaciones=observaciones, firma=firma,
        )
        firma.resultado = resultado
        return resultado


@registrar_firmable
class FirmaSolicitudAprobacion(_FirmaAprobacionMixin, Firmable):
    """FOR-001: evaluación/autorización de creación o eliminación."""

    tipo = 'solicitud_aprobacion_documento'
    audit_accion = 'firma_aprobacion'
    audit_categoria = 'documento'

    def documento(self, obj):
        return obj.documento

    def contenido(self, obj):
        documento = obj.documento
        return _json_canonico({
            'flujo': self.tipo,
            'documento': getattr(documento, 'codigo', None),
            'documento_id': str(getattr(documento, 'uuid', '')),
            'accion': obj.accion_solicitud,
            'solicitud_id': obj.id,
            'rol': obj.rol_aprobador,
            'es_final': obj.es_final,
            'aprobador': obj.aprobador_id,
        })

    def detalles(self, obj):
        return {
            'solicitud_id': obj.id,
            'accion': obj.accion_solicitud,
            'rol': obj.rol_aprobador,
            'es_final': obj.es_final,
        }

    def _resolver(self, obj, firma, aprobada, observaciones, contexto):
        from .aprobacion_flujo import resolver_solicitud_documento

        contexto = contexto or {}
        if contexto.get('ya_resuelto'):
            # El llamador aplica el cambio por su cuenta (auto-aprobación de
            # plantillas); aquí solo se registra la evidencia.
            firma.resultado = None
            return None
        request = contexto.get('request')
        resultado = resolver_solicitud_documento(
            request, obj, aprobada=aprobada,
            observaciones=observaciones, firma=firma,
        )
        firma.resultado = resultado
        return resultado


@registrar_firmable
class FirmaRegistroFormato(Firmable):
    """Aprobación de un registro de formato (instancia llena) con PIN."""

    tipo = 'registro_formato'
    audit_accion = 'firma_registro'
    audit_categoria = 'documento'

    def documento(self, obj):
        if obj.contexto_tipo != 'documento':
            return None
        from ..models import Documento

        return Documento.objects.filter(uuid=obj.contexto_id).first()

    def firmante(self, obj, usuario):
        info = super().firmante(obj, usuario)
        from ..models import _cargo_usuario

        info['firmante_cargo'] = _cargo_usuario(usuario)
        return info

    def puede_firmar(self, obj, usuario):
        actual = type(obj)._default_manager.filter(pk=obj.pk).values(
            'estado', 'aprobador_id',
        ).first()
        if not actual or actual['estado'] != 'en_revision':
            raise ErrorFirma('Este registro no está pendiente de aprobación.')
        aprobador_id = actual['aprobador_id']
        es_admin = bool(
            getattr(getattr(usuario, 'perfil', None), 'es_administrador', False)
        )
        if aprobador_id and aprobador_id != usuario.id and not es_admin:
            raise ErrorFirma('No eres el aprobador de este registro.')

    def contenido(self, obj):
        if obj.archivo:
            try:
                with obj.archivo.open('rb') as fh:
                    return fh.read()
            except (OSError, ValueError):
                pass
        if obj.formato_id and obj.formato.plantilla_xlsx:
            from .exportacion import exportar_xlsx_formato

            return exportar_xlsx_formato(obj.formato, obj.respuestas or {})
        return _json_canonico({
            'flujo': self.tipo,
            'formato': obj.formato_codigo,
            'contexto': f'{obj.contexto_tipo}:{obj.contexto_id}',
            'registro_id': obj.id,
            'titulo': obj.titulo,
            'respuestas': obj.respuestas or {},
        })

    def detalles(self, obj):
        return {
            'registro_id': obj.id,
            'formato': obj.formato_codigo,
            'contexto': f'{obj.contexto_tipo}:{obj.contexto_id}',
        }

    def despues(self, obj, firma, contexto=None):
        from django.utils import timezone

        obj.estado = 'aprobado'
        obj.aprobador = firma.firmante
        obj.fecha_aprobacion = firma.fecha_firma or timezone.now()
        obj.hash_integridad = firma.hash_contenido
        observaciones = (firma.datos or {}).get('observaciones')
        if observaciones:
            obj.observaciones = observaciones
        obj.save(update_fields=[
            'estado', 'aprobador', 'fecha_aprobacion', 'hash_integridad',
            'observaciones', 'actualizado',
        ])
