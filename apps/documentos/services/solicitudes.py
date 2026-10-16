"""Historial "Mis solicitudes".

Normaliza en una sola lista ordenada lo que un usuario ha pedido en el
sistema --descargas, cambios de estado y accesos-- con un esquema común
para que la vista ``mis_solicitudes`` y su plantilla no tengan que
conocer los detalles de cada modelo. Este módulo **solo consulta**: no
crea ni resuelve nada.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from ..models import (
    SolicitudAprobacionDocumento,
    SolicitudAccesoDocumento,
    SolicitudDescarga,
)

#: estados que siguen en trámite
PENDIENTES = ('pendiente', 'esperando')

#: icono de cada tipo de solicitud
ICONOS = {
    'descarga': 'fa-download',
    'creacion': 'fa-plus',
    'edicion': 'fa-pen',
    'inhabilitar': 'fa-ban',
    'reactivar': 'fa-rotate-left',
    'renovar': 'fa-calendar-check',
    'version': 'fa-code-branch',
    'eliminar_version': 'fa-trash-can',
    'acceso': 'fa-door-open',
}

#: etiqueta visible de cada tipo
TIPOS_LABEL = {
    'descarga': 'Descarga',
    'creacion': 'Creación',
    'edicion': 'Edición',
    'inhabilitar': 'Inhabilitar',
    'reactivar': 'Reactivar',
    'renovar': 'Renovar vencimiento',
    'version': 'Nueva versión',
    'eliminar_version': 'Eliminar versión',
    'acceso': 'Acceso a documento',
}

ROLES_LABEL = {
    'jefe': 'Jefe de departamento',
    'autorizador': 'Autorizador',
    'evaluador': 'Evaluador',
}

ESTADO_LABELS = {
    'pendiente': 'Pendiente',
    'esperando': 'En espera',
    'aprobada': 'Aprobada',
    'rechazada': 'Rechazada',
    'cancelada': 'Cancelada',
    'vencida': 'Vencida',
}

#: equivalencia entre el valor del filtro de la vista y los estados
FILTRO_ESTADOS = {
    '': None,
    'pendiente': ('pendiente', 'esperando'),
    'aprobada': ('aprobada',),
    'rechazada': ('rechazada',),
    'cancelada': ('cancelada',),
    'vencida': ('vencida',),
}

#: familia de la que proviene un ítem (para las acciones)
CLAVE_DESCARGA = 'descarga'
CLAVE_CAMBIO = 'cambio'
CLAVE_ACCESO = 'acceso'


@dataclass
class ItemSolicitud:
    """Vista normalizada de una solicitud propia del usuario."""

    id: int
    clave: str
    tipo: str
    tipo_label: str
    icono: str
    estado: str
    estado_label: str
    fecha_solicitud: datetime
    documento: object = None
    version: str = ''
    departamento: str = ''
    motivo: str = ''
    observaciones: str = ''
    aprobadores: list = field(default_factory=list)
    fecha_resolucion: Optional[datetime] = None
    fecha_vence: Optional[datetime] = None
    fecha_propuesta: Optional[date] = None
    url_detalle: str = ''
    url_accion: str = ''
    url_accion_label: str = ''

    @property
    def es_pendiente(self):
        return self.estado in PENDIENTES

    @property
    def es_vencida(self):
        return self.estado == 'vencida'

    @property
    def nombre_documento(self):
        if not self.documento:
            return 'Documento no disponible'
        return self.documento.nombre

    @property
    def codigo_documento(self):
        if not self.documento:
            return ''
        return self.documento.codigo


def _url_detalle(documento):
    if documento is None or not documento.tipo_id or not documento.uuid:
        return ''
    return f'/documentos/{documento.tipo_id}/{documento.uuid}/'


def _nombre_usuario(usuario):
    if usuario is None:
        return ''
    return usuario.get_full_name() or usuario.username


def _aprobador_item(usuario, rol, estado, fecha=None):
    return {
        'nombre': _nombre_usuario(usuario) or 'Pendiente de asignar',
        'rol': rol,
        'rol_label': ROLES_LABEL.get(rol, rol.title() if rol else '—'),
        'estado': estado,
        'estado_label': ESTADO_LABELS.get(estado, estado.title()),
        'fecha': fecha,
    }


def _item_descarga(solicitud):
    """Ítem para :class:`models.SolicitudDescarga`."""
    documento = solicitud.documento
    aprobaciones = list(solicitud.aprobaciones.all())
    estado = solicitud.estado
    if estado == 'aprobada' and not solicitud.esta_vigente:
        estado = 'vencida'
    elif estado == 'pendiente' and aprobaciones and not any(
        a.estado == 'pendiente' for a in aprobaciones
    ):
        # Ningún paso accionable: la solicitud está esperando que se asigne
        # al jefe que debe resolverla.
        estado = 'esperando'
    aprobadores = [
        _aprobador_item(a.aprobador, a.rol_aprobador, a.estado, a.fecha_aprobacion)
        for a in aprobaciones
    ]
    url_detalle = _url_detalle(documento)
    url_accion = ''
    url_accion_label = ''
    if estado in ('vencida', 'rechazada') and url_detalle:
        # GET sobre el formulario rechaza si ya hay trámite/autorización,
        # así que siempre es un camino seguro para volver a pedir.
        url_accion = f'{url_detalle}solicitar-descarga/'
        url_accion_label = 'Solicitar de nuevo'
    elif estado == 'aprobada' and solicitud.esta_vigente and url_detalle:
        url_accion = f'{url_detalle}descargar/'
        url_accion_label = 'Descargar'
    return ItemSolicitud(
        id=solicitud.id,
        clave=CLAVE_DESCARGA,
        tipo='descarga',
        tipo_label=TIPOS_LABEL['descarga'],
        icono=ICONOS['descarga'],
        estado=estado,
        estado_label=ESTADO_LABELS[estado],
        fecha_solicitud=solicitud.fecha_solicitud,
        documento=documento,
        version=solicitud.version_aprobada,
        departamento=getattr(documento.departamento, 'nombre', '') if documento else '',
        motivo=solicitud.motivo,
        observaciones=solicitud.resolucion_observaciones,
        aprobadores=aprobadores,
        fecha_resolucion=solicitud.fecha_resolucion,
        fecha_vence=solicitud.fecha_expiracion,
        url_detalle=url_detalle,
        url_accion=url_accion,
        url_accion_label=url_accion_label,
    )


def _item_cambio(solicitud):
    """Ítem para :class:`models.SolicitudAprobacionDocumento`."""
    documento = solicitud.documento
    aprobadores = [
        _aprobador_item(solicitud.aprobador, solicitud.rol_aprobador, solicitud.estado,
                        solicitud.fecha_respuesta),
    ]
    observaciones = solicitud.motivo_rechazo or solicitud.observaciones
    return ItemSolicitud(
        id=solicitud.id,
        clave=CLAVE_CAMBIO,
        tipo=solicitud.accion_solicitud,
        tipo_label=TIPOS_LABEL.get(solicitud.accion_solicitud,
                                   solicitud.get_accion_solicitud_display()),
        icono=ICONOS.get(solicitud.accion_solicitud, 'fa-file-signature'),
        estado=solicitud.estado,
        estado_label=ESTADO_LABELS.get(solicitud.estado,
                                       solicitud.get_estado_display()),
        fecha_solicitud=solicitud.fecha_solicitud,
        documento=documento,
        version=getattr(solicitud.version_a_eliminar, 'version', '') or '',
        departamento=getattr(solicitud.departamento_origen, 'nombre', ''),
        observaciones=observaciones,
        aprobadores=aprobadores,
        fecha_resolucion=solicitud.fecha_respuesta,
        fecha_propuesta=solicitud.fecha_propuesta,
        url_detalle=_url_detalle(documento),
    )


def _item_acceso(solicitud):
    """Ítem para :class:`models.SolicitudAccesoDocumento`."""
    documento = solicitud.documento
    aprobadores = [
        _aprobador_item(solicitud.jefe_destino, 'jefe', solicitud.estado,
                        solicitud.fecha_respuesta),
    ]
    return ItemSolicitud(
        id=solicitud.id,
        clave=CLAVE_ACCESO,
        tipo='acceso',
        tipo_label=TIPOS_LABEL['acceso'],
        icono=ICONOS['acceso'],
        estado=solicitud.estado,
        estado_label=ESTADO_LABELS.get(solicitud.estado,
                                       solicitud.get_estado_display()),
        fecha_solicitud=solicitud.fecha_solicitud,
        documento=documento,
        departamento=getattr(solicitud.departamento_destino, 'nombre', ''),
        motivo=solicitud.motivo,
        observaciones=solicitud.observaciones,
        aprobadores=aprobadores,
        fecha_resolucion=solicitud.fecha_respuesta,
        url_detalle=_url_detalle(documento),
    )


def mis_solicitudes(usuario, *, estado='', tipo=''):
    """Lista normalizada de las solicitudes de ``usuario``.

    Devuelve ``(items, totales)``: los ítems ordenados por fecha
    descendente (con los filtros aplicados) y un contador por estado para
    las pestañas de la vista. Los filtros ``estado``/``tipo`` usan los
    mismos valores que la interfaz.
    """
    items = []

    descargas = (
        SolicitudDescarga.objects
        .filter(solicitado_por=usuario)
        .select_related('documento', 'documento__departamento')
        .prefetch_related('aprobaciones__aprobador')
    )
    items.extend(_item_descarga(s) for s in descargas)

    cambios = (
        SolicitudAprobacionDocumento.objects
        .filter(solicitante=usuario)
        .select_related('documento', 'departamento_origen', 'version_a_eliminar')
    )
    items.extend(_item_cambio(s) for s in cambios)

    accesos = (
        SolicitudAccesoDocumento.objects
        .filter(solicitante=usuario)
        .select_related('documento', 'departamento_destino', 'jefe_destino')
    )
    items.extend(_item_acceso(s) for s in accesos)

    items.sort(key=lambda i: i.fecha_solicitud, reverse=True)

    # Contadores por estado (siempre sobre el conjunto completo, no
    # sobre los ya filtrados, para que la interfaz no cambie de cuenta).
    totales = {'todas': len(items)}
    for item in items:
        clave = 'pendiente' if item.es_pendiente else item.estado
        totales[clave] = totales.get(clave, 0) + 1

    estados_filtro = FILTRO_ESTADOS.get(estado)
    if estados_filtro is not None:
        items = [i for i in items if i.estado in estados_filtro]
    if tipo:
        items = [i for i in items if i.tipo == tipo]
    return items, totales
