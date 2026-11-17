"""Trazabilidad de versiones de registros vivos (``RegistroFormato``).

Cada guardado relevante crea un snapshot inmutable (``VersionRegistroFormato``)
con las respuestas y la configuración de estructura del formato en ese momento,
más un SHA-256 para integridad. El estado actual sigue viviendo en
``RegistroFormato.respuestas``.
"""
import hashlib
import json

from django.db import transaction
from django.db.models import Max


def config_snapshot(formato):
    """Estructura/config del formato (campos y sus ``config_tabla``)."""
    if formato is None:
        return {}
    from ..models import CampoFormato

    campos = CampoFormato.objects.filter(
        seccion__formato=formato,
    ).select_related('seccion').order_by('seccion__orden', 'orden', 'id')
    return {
        'formato_codigo': formato.codigo,
        'formato_version': formato.version,
        'formato_edicion': formato.edicion,
        'campos': [
            {
                'clave': campo.clave,
                'etiqueta': campo.etiqueta,
                'tipo': campo.tipo,
                'celda_excel': campo.celda_excel,
                'config_tabla': campo.config_tabla,
            }
            for campo in campos
        ],
    }


def _hash(datos):
    payload = json.dumps(
        datos, sort_keys=True, ensure_ascii=False, default=str,
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def registrar_version(registro, usuario=None, resumen=''):
    """Crea el siguiente snapshot inmutable de ``registro``.

    Debe llamarse dentro de la transacción del guardado (usa
    ``select_for_update``). Devuelve ``VersionRegistroFormato``.
    """
    from ..models import RegistroFormato, VersionRegistroFormato

    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(
            pk=registro.pk,
        )
        ultimo = VersionRegistroFormato.objects.filter(
            registro=bloqueado,
        ).aggregate(Max('numero'))['numero__max'] or 0
        datos = {
            'respuestas': bloqueado.respuestas or {},
            'config': config_snapshot(bloqueado.formato),
        }
        return VersionRegistroFormato.objects.create(
            registro=bloqueado,
            numero=ultimo + 1,
            datos=datos,
            resumen=(resumen or '')[:2000],
            hash=_hash(datos),
            creado_por=usuario,
        )


def versiones(registro):
    from ..models import VersionRegistroFormato

    return VersionRegistroFormato.objects.filter(
        registro=registro,
    ).order_by('-numero')


def ultima_version(registro):
    return versiones(registro).first()


def version_por_numero(registro, numero):
    from ..models import VersionRegistroFormato

    return VersionRegistroFormato.objects.filter(
        registro=registro, numero=numero,
    ).first()
