"""Registro de objetos firmables (SES).

Cada clase declara cómo se obtiene el contenido firmado y qué efecto tiene la
firma. Agregar un documento nuevo = una clase aquí; el resto del motor
(:mod:`documentos.services.firmas`) no cambia.
"""
import json

from django.utils import timezone

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


@registrar_firmable
class FirmaDistribucionRegistro(Firmable):
    """Acuse de conformidad del destinatario de una distribución de registro.

    Interno: PIN local. Externo: token de ``InvitacionFirma`` + consentimiento
    (el enlace también permite descargar el PDF hasta que caduca).
    """

    tipo = 'distribucion_registro'
    audit_accion = 'acuse_distribucion_registro'
    audit_categoria = 'documento'

    def documento(self, obj):
        from .registros import documento_de_contexto

        return documento_de_contexto(obj.registro)

    def puede_firmar(self, obj, usuario):
        if obj.usuario_id and obj.usuario_id != usuario.id:
            raise ErrorFirma('Esta distribución no te pertenece.')
        if obj.revocado or obj.estado_acuse == 'revocado':
            raise ErrorFirma('La distribución fue revocada.')
        if obj.estado_acuse != 'pendiente':
            raise ErrorFirma('El acuse de esta distribución ya fue registrado.')

    def contenido(self, obj):
        """PDF del registro o, en su defecto, su XLSX exportado."""
        registro = obj.registro
        if registro.archivo:
            try:
                with registro.archivo.open('rb') as fh:
                    return fh.read()
            except (OSError, ValueError):
                pass
        if registro.archivo_pdf:
            try:
                with registro.archivo_pdf.open('rb') as fh:
                    return fh.read()
            except (OSError, ValueError):
                pass
        from .registros import exportar_registro_xlsx

        return exportar_registro_xlsx(registro)

    def detalles(self, obj):
        return {
            'distribucion_registro_id': obj.id,
            'registro': obj.registro.formato_codigo,
            'destinatario': obj.destinatario_nombre,
            'metodo': obj.metodo,
        }

    def despues(self, obj, firma, contexto=None):
        obj.estado_acuse = 'acusado'
        obj.fecha_acuse = firma.fecha_firma or timezone.now()
        obj.firma = firma
        campos = ['estado_acuse', 'fecha_acuse', 'firma']
        if not obj.fecha_entrega:
            obj.fecha_entrega = obj.fecha_acuse
            campos.append('fecha_entrega')
        obj.save(update_fields=campos)


@registrar_firmable
class FirmaActaDesincorporacion(Firmable):
    """SGC-FOR-004: firma del acta de desincorporación (Elaborado/Aprobado).

    Firma **híbrida**: interna con PIN (``metodo='pin'``) o externa con el
    token de ``InvitacionFirma`` (``metodo='token'``). El rol de cada firma
    viaja explícito en ``FirmaElectronica.datos['rol']``: no se infiere del
    usuario, de modo que la misma persona puede ocupar ambos roles con UNA
    sola firma (el motor la enlaza a los dos bloques del acta).

    El contenido firmado es el **JSON canónico del acta** (no el XLSX/PDF,
    que se regenera tras cada firma): así el hash es estable y verificable.
    """

    tipo = 'acta_desincorporacion'
    audit_accion = 'firma_acta_desincorporacion'
    audit_categoria = 'documento'

    def documento(self, obj):
        return obj.padre

    def firmante(self, obj, usuario):
        info = super().firmante(obj, usuario)
        from ..models import _cargo_usuario

        info['firmante_cargo'] = _cargo_usuario(usuario)
        return info

    def puede_firmar(self, obj, usuario):
        from ..models import ActaDesincorporacion
        from .for_004 import validar_para_firmar

        actual = ActaDesincorporacion.objects.filter(pk=obj.pk).first()
        if actual is None:
            raise ErrorFirma('Este acta ya no existe.')
        validar_para_firmar(actual)
        if not actual.rol_para(usuario):
            raise ErrorFirma('No tienes un rol pendiente de firma en este acta.')

    def contenido(self, obj):
        return _json_canonico({
            'flujo': self.tipo,
            'acta': obj.codigo,
            'padre': obj.documento_codigo,
            'fecha': str(obj.fecha),
            'proposito': obj.proposito,
            'departamento': obj.departamento_nombre,
            'participantes': [
                {'nombre': p.nombre, 'cargo': p.cargo}
                for p in obj.participantes.order_by('orden', 'id')
            ],
            'registros': [
                {
                    'codigo': r.codigo,
                    'nombre': r.nombre,
                    'fecha_vencimiento': str(r.fecha_vencimiento or ''),
                    'fecha_retencion': str(r.fecha_retencion or ''),
                    'fecha_destruccion': str(r.fecha_destruccion or ''),
                }
                for r in obj.registros.order_by('orden', 'id')
            ],
            'elaborado_por': obj.elaborado_por_id,
            'aprobado_por': obj.aprobado_por_id,
        })

    def detalles(self, obj):
        return {
            'acta_id': obj.id,
            'acta': obj.codigo,
            'padre': obj.documento_codigo,
            'registros': obj.registros.count(),
        }

    def despues(self, obj, firma, contexto=None):
        """Asigna la firma a su rol, cierra el acta y la aplica si completa.

        Se relee el acta con ``select_for_update``: la validación se hace
        contra el estado real, y cualquier violación (rol tomado, registro ya
        no inactivo, acta cerrada) revierte la firma entera.
        """
        from ..models import ActaDesincorporacion
        from .for_004 import aplicar_acta, regenerar_tras_firma, validar_para_firmar

        rol = (firma.datos or {}).get('rol')
        if rol not in ('elaborado', 'aprobado'):
            raise ErrorFirma('Rol de firma no reconocido para el acta.')

        actual = ActaDesincorporacion.objects.select_for_update().get(pk=obj.pk)
        validar_para_firmar(actual)

        asignado = actual.firmante_asignado(rol)
        if asignado is None:
            raise ErrorFirma('Este acta no tiene firmante asignado para ese rol.')
        if not self._firmante_coincide(asignado, firma):
            raise ErrorFirma('Esta firma no corresponde al firmante asignado.')

        if rol == 'elaborado':
            if actual.firma_elaborado_id:
                raise ErrorFirma('El bloque Elaborado ya fue firmado.')
            actual.firma_elaborado = firma
            if actual.elaborado_por_id == actual.aprobado_por_id:
                actual.firma_aprobado = firma
        else:
            if actual.firma_aprobado_id:
                raise ErrorFirma('El bloque Aprobado ya fue firmado.')
            actual.firma_aprobado = firma
            if actual.elaborado_por_id == actual.aprobado_por_id:
                actual.firma_elaborado = firma

        actual.estado = 'pendiente_firma'
        actual.save(update_fields=[
            'firma_elaborado', 'firma_aprobado', 'estado', 'actualizado',
        ])
        # Devolver el estado a la instancia que usó el llamador.
        obj.firma_elaborado = actual.firma_elaborado
        obj.firma_aprobado = actual.firma_aprobado
        obj.estado = actual.estado

        if actual.firmas_completas:
            aplicar_acta(actual)
        regenerar_tras_firma(actual)

    @staticmethod
    def _firmante_coincide(asignado, firma):
        """El firmante de la firma debe ser el usuario asignado al rol.

        Interno: ``firmante`` = usuario. Externo: el correo de la firma debe
        coincidir con el del asignado (la invitación se emite a su correo).
        """
        if firma.firmante_id:
            return firma.firmante_id == asignado.id
        email_firma = (firma.firmante_email or '').strip().lower()
        email_asignado = (asignado.email or '').strip().lower()
        return bool(email_firma) and email_firma == email_asignado
