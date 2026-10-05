"""Transferencia de propiedad de documentos (web y API)."""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


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
