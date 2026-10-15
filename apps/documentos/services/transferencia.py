"""Transferencia de propiedad de documentos (web y API).

Además de la transferencia 1-a-1 de propiedad, expone la **transferencia
masiva** usada para cubrir la ausencia temporal (vacaciones) de un
trabajador: mueve de un golpe la propiedad, las asignaciones como
coordinador y las distribuciones que recibe ese usuario.
"""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)

MOTIVO_TRANSFERENCIA = 'Cobertura temporal (transferencia masiva)'


@transaction.atomic
def transferir_propiedad_documento(documento, nuevo_propietario, actor,
                                   ip_address=None):
    """Transfiere la propiedad de un documento.

    Centraliza el flujo que antes estaba duplicado (y divergente) entre la
    vista web y la API: cambia el propietario, notifica al nuevo dueño y
    registra la acción en la auditoría ISO.

    El llamante DEBE haber validado permisos, mismo departamento y que el
    nuevo propietario no sea el actual.
    """
    propietario_anterior = documento.propietario
    documento.propietario = nuevo_propietario
    documento.save(update_fields=['propietario'])

    from autenticacion.models import Notificacion
    Notificacion.objects.create(
        usuario=nuevo_propietario,
        titulo='Propiedad transferida',
        mensaje=(
            f'Se le ha asignado la propiedad del documento '
            f'"{documento.nombre}" ({documento.codigo})'
        ),
        tipo='propiedad_transferida',
        url_accion=f'/documentos/{documento.tipo_id}/{documento.uuid}/',
        departamento=documento.departamento,
        documento=documento,
    )

    try:
        from auditoria.services import registrar_transferencia_propiedad
        registrar_transferencia_propiedad(
            usuario=actor,
            recurso_tipo=documento.tipo_id or '',
            recurso_id=documento.id,
            nuevo_propietario_username=nuevo_propietario.username,
            propietario_anterior=(
                propietario_anterior.username if propietario_anterior else None
            ),
            codigo=documento.codigo,
            ip_address=ip_address,
        )
    except Exception as exc:  # auditoría fail-open (igual que el resto)
        logger.error(
            "Error registrando auditoría de transferencia del documento %s: %s",
            documento.codigo, exc,
        )

    return documento


def transferibles_de_usuario(origen):
    """Agrupa todo lo que ``origen`` posee/recibe y puede ceder.

    Retorna ``(propiedad, asignaciones, distribuciones)`` como querysets ya
    ``select_related`` y ordenados por código, listos para previsualizar y
    para construir los IDs de la selección.
    """
    from ..models import AsignacionDocumento, DistribucionDocumento, Documento

    propiedad = Documento.objects.filter(
        propietario=origen,
    ).select_related('departamento', 'tipo').order_by('codigo')
    asignaciones = AsignacionDocumento.objects.filter(
        coordinador=origen, activa=True,
    ).select_related('documento', 'documento__departamento', 'coordinador').order_by('documento__codigo')
    distribuciones = DistribucionDocumento.objects.filter(
        usuario=origen, revocado=False,
    ).select_related('documento', 'documento__departamento').order_by('documento__codigo')
    return propiedad, asignaciones, distribuciones


def _validar_par(origen, destino):
    """Valida el par (origen, destino) y retorna el departamento común."""
    perfil_origen = getattr(origen, 'perfil', None)
    perfil_destino = getattr(destino, 'perfil', None)
    depto_origen = getattr(perfil_origen, 'departamento', None)
    depto_destino = getattr(perfil_destino, 'departamento', None)

    if origen.id == destino.id:
        raise ValueError('El usuario de origen y el destino deben ser distintos.')
    if depto_origen is None:
        raise ValueError('El usuario de origen no tiene departamento asignado.')
    if depto_destino is None:
        raise ValueError('El usuario destino no tiene departamento asignado.')
    if depto_destino.id != depto_origen.id:
        raise ValueError(
            'El usuario destino debe pertenecer al mismo departamento del origen.'
        )
    return depto_origen


@transaction.atomic
def transferir_documentos_usuario(origen, destino, actor, *, propiedad_ids=(),
                                  asignacion_ids=(), distribucion_ids=(),
                                  ip_address=None, request=None):
    """Transfiere de un golpe todo lo que posee/recibe ``origen`` hacia ``destino``.

    Usada para la cobertura temporal (vacaciones) de un trabajador. Mueve:

    - **Propiedad**: ``Documento.propietario`` (reutiliza
      ``transferir_propiedad_documento`` → notifica y audita por documento).
    - **Asignaciones**: ``AsignacionDocumento`` activas como coordinador. Si
      el destino ya tiene una asignación del mismo documento se **fusionan**
      los permisos (OR de puede_leer/editar/eliminar) y la del origen se
      marca inactiva (queda como historial); si no, se reasigna
      ``coordinador`` al destino.
    - **Distribuciones**: ``DistribucionDocumento`` no revocadas se **revocan**
      (a nombre del origen) y se crea una **nueva** a favor del destino.

    Todo ocurre en **una sola transacción**: si algo falla no queda nada a
    medias. El llamante ya validó permisos; aquí solo se valida el par
    origen/destino (mismo departamento). El correo se omite a propósito:
    la notificación in-app es la fuente de verdad y evita un envío masivo
    durante la vacación.
    """
    from django.utils import timezone as tz

    from autenticacion.models import Notificacion

    from ..models import AsignacionDocumento, DistribucionDocumento, Documento

    _validar_par(origen, destino)

    resumen = {
        'propiedad': 0,
        'asignaciones': 0,
        'asignaciones_fusionadas': 0,
        'distribuciones': 0,
        'total': 0,
    }

    # 1) Propiedad de los documentos creados por el origen.
    for documento in Documento.objects.filter(id__in=list(propiedad_ids)):
        transferir_propiedad_documento(
            documento, destino, actor, ip_address=ip_address,
        )
        resumen['propiedad'] += 1

    # 2) Asignaciones como coordinador.
    for asignacion in AsignacionDocumento.objects.filter(
        id__in=list(asignacion_ids), activa=True,
    ).select_related('documento'):
        documento = asignacion.documento
        existente = AsignacionDocumento.objects.filter(
            documento=documento, coordinador=destino,
        ).first()
        if existente:
            # Fusiona permisos (OR) en la fila del destino y deja la del
            # origen inactiva como historial (unique_together lo impide).
            existente.puede_leer = existente.puede_leer or asignacion.puede_leer
            existente.puede_editar = existente.puede_editar or asignacion.puede_editar
            existente.puede_eliminar = existente.puede_eliminar or asignacion.puede_eliminar
            campos = ['puede_leer', 'puede_editar', 'puede_eliminar']
            if not existente.activa:
                existente.activa = True
                campos.append('activa')
            existente.save(update_fields=campos)
            asignacion.activa = False
            asignacion.save(update_fields=['activa'])
            resumen['asignaciones_fusionadas'] += 1
        else:
            asignacion.coordinador = destino
            asignacion.save(update_fields=['coordinador'])
            Notificacion.objects.create(
                usuario=destino,
                titulo='Asignación transferida',
                mensaje=(
                    f'Se le transfirió la asignación del documento '
                    f'"{documento.nombre}" ({documento.codigo})'
                ),
                tipo='documento_asignado',
                url_accion=f'/documentos/{documento.tipo_id}/{documento.uuid}/',
                departamento=documento.departamento,
                documento=documento,
            )
        resumen['asignaciones'] += 1

    # 3) Distribuciones: revoca la del origen y crea una nueva para el destino.
    for distribucion in DistribucionDocumento.objects.filter(
        id__in=list(distribucion_ids), revocado=False,
    ).select_related('documento'):
        documento = distribucion.documento
        distribucion.revocado = True
        distribucion.fecha_revocacion = tz.now()
        distribucion.revocado_por = actor
        distribucion.motivo_revocacion = MOTIVO_TRANSFERENCIA
        distribucion.save(update_fields=[
            'revocado', 'fecha_revocacion', 'revocado_por', 'motivo_revocacion',
        ])
        nueva = DistribucionDocumento.objects.create(
            documento=documento,
            usuario=destino,
            metodo=distribucion.metodo,
            distribuido_por=actor,
            observaciones=distribucion.observaciones,
        )
        Notificacion.objects.create(
            usuario=destino,
            titulo='Documento Distribuido',
            mensaje=(
                f'Se le ha distribuido el documento '
                f'"{documento.nombre}" ({documento.codigo}) por cobertura temporal.'
            ),
            tipo='documento_distribuido',
            url_accion=f'/documentos/{documento.tipo_id}/{documento.uuid}/',
            departamento=documento.departamento,
            documento=documento,
        )
        Notificacion.objects.create(
            usuario=origen,
            titulo='Distribución revocada',
            mensaje=(
                f'Tu distribución del documento "{documento.nombre}" '
                f'({documento.codigo}) fue reasignada por cobertura temporal.'
            ),
            tipo='distribucion_revocada',
            documento=documento,
        )
        try:
            from auditoria.middleware import get_client_ip
            from auditoria.services import registrar_distribucion

            registrar_distribucion(
                usuario=actor,
                recurso_tipo=documento.tipo_id or '',
                recurso_id=str(documento.uuid),
                destinatario=destino.username,
                metodo=distribucion.metodo,
                codigo=documento.codigo,
                distribucion_id=nueva.id,
                ip_address=ip_address or get_client_ip(),
            )
        except Exception as exc:  # auditoría fail-open
            logger.error(
                "Auditoría de distribución masiva falló para %s: %s",
                documento.codigo, exc,
            )
        resumen['distribuciones'] += 1

    resumen['total'] = (
        resumen['propiedad'] + resumen['asignaciones'] + resumen['distribuciones']
    )
    return resumen
