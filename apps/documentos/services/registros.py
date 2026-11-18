"""Registros de formato (instancias llenas) — servicios de negocio.

Un ``RegistroFormato`` es la evidencia de haber llenado un ``Formato`` para un
contexto (primer caso: un documento/DPR). El código del formato se repite; la
identidad es el ``uuid``. Ver ``CONTEXT.txt`` §32-§33.
"""
import os
import tempfile

from django.db.models import Q


def documento_de_contexto(registro):
    """``Documento`` del contexto, o ``None`` si el contexto no es documento."""
    if registro is None or registro.contexto_tipo != 'documento':
        return None
    from ..models import Documento

    return Documento.objects.filter(uuid=registro.contexto_id).first()


def _es_admin(usuario):
    return bool(
        getattr(getattr(usuario, 'perfil', None), 'es_administrador', False)
    )


#: Contextos de "registro vivo" con vista exclusiva propia (FOR-006/FOR-007).
#: No deben aparecer ni editarse desde el editor genérico de registros.
CONTEXTOS_VISTA_EXCLUSIVA = ('sistema', 'programa_revision', 'minuta')


def registros_visibles(usuario):
    """QuerySet de registros que el usuario puede ver."""
    from ..models import Documento, RegistroFormato
    from ..permissions import q_documentos_visibles

    qs = RegistroFormato.objects.exclude(
        contexto_tipo__in=CONTEXTOS_VISTA_EXCLUSIVA,
    )
    if _es_admin(usuario):
        return qs
    uuids = [
        str(u) for u in Documento.objects.filter(
            q_documentos_visibles(usuario),
        ).values_list('uuid', flat=True)
    ]
    return qs.filter(
        Q(responsable=usuario)
        | Q(aprobador=usuario)
        | Q(contexto_tipo='documento', contexto_id__in=uuids)
    ).distinct()


def puede_ver_registro(usuario, registro):
    if registro.contexto_tipo in CONTEXTOS_VISTA_EXCLUSIVA:
        return False
    return _es_admin(usuario) or registros_visibles(usuario).filter(
        pk=registro.pk,
    ).exists()


def puede_editar_registro(usuario, registro):
    if registro.contexto_tipo in CONTEXTOS_VISTA_EXCLUSIVA:
        return False
    if registro.estado not in ('borrador', 'rechazado'):
        return False
    return registro.responsable_id == usuario.id or _es_admin(usuario)


def puede_aprobar_registro(usuario, registro):
    if registro.contexto_tipo in CONTEXTOS_VISTA_EXCLUSIVA:
        return False
    if registro.estado != 'en_revision':
        return False
    if _es_admin(usuario):
        return True
    # Sin aprobador asignado solo administran los admins (evita que
    # cualquier usuario firme un registro huérfano).
    return bool(registro.aprobador_id) and registro.aprobador_id == usuario.id


def _auditar(registro, usuario, accion, mensaje):
    try:
        from auditoria.services import registrar_log_iso

        documento = documento_de_contexto(registro)
        registrar_log_iso(
            usuario=usuario,
            accion_tipo=accion,
            accion_categoria='documento',
            recurso_tipo=(documento.tipo_id if documento else ''),
            recurso_id=str(registro.uuid),
            recurso_codigo=registro.formato_codigo,
            recurso_nombre=registro.titulo,
            detalles={
                'registro_id': registro.id,
                'formato': registro.formato_codigo,
                'contexto': f'{registro.contexto_tipo}:{registro.contexto_id}',
            },
            resultado_exitoso=True,
            resultado_mensaje=mensaje,
        )
    except Exception:  # noqa: BLE001 - la auditoría nunca rompe el flujo
        pass


def enviar_a_revision(registro, usuario):
    """Pasa el registro a revisión y asigna aprobador si faltaba.

    Sin firma: la firma SES ocurre al aprobar (PIN)."""
    if registro.estado not in ('borrador', 'rechazado'):
        raise ValueError('Solo se pueden enviar registros en borrador o rechazados.')
    if not (registro.responsable_id == usuario.id or _es_admin(usuario)):
        raise PermissionError('No tienes permisos para enviar este registro.')
    if not registro.aprobador_id:
        documento = documento_de_contexto(registro)
        registro.aprobador = getattr(
            getattr(documento, 'departamento', None), 'jefe', None,
        )
    registro.estado = 'en_revision'
    registro.save(update_fields=['estado', 'aprobador', 'actualizado'])
    _auditar(registro, usuario, 'registro_enviado', 'Registro enviado a revisión')
    return registro


def rechazar_registro(registro, usuario, observaciones=''):
    """Rechaza el registro SIN firma: vuelve a corrección (estado rechazado)."""
    if not puede_aprobar_registro(usuario, registro):
        raise PermissionError('No puedes rechazar este registro.')
    registro.estado = 'rechazado'
    registro.observaciones = observaciones or ''
    registro.save(update_fields=['estado', 'observaciones', 'actualizado'])
    _auditar(
        registro, usuario, 'registro_rechazado',
        f'Registro rechazado: {observaciones}',
    )
    return registro


def exportar_registro_xlsx(registro, para_pdf=False):
    """Bytes XLSX del registro (archivo subido o generado desde el formato)."""
    archivo = registro.archivo
    if archivo and str(archivo.name).lower().endswith('.xlsx'):
        try:
            with archivo.open('rb') as fh:
                return fh.read()
        except (OSError, ValueError):
            pass
    if not registro.formato_id or not registro.formato.plantilla_xlsx:
        raise ValueError('El registro no tiene archivo ni plantilla XLSX')
    from .exportacion import exportar_xlsx_formato

    return exportar_xlsx_formato(
        registro.formato, registro.respuestas or {}, para_pdf=para_pdf,
    )


def exportar_registro_pdf(registro):
    """Ruta del PDF del registro (lo genera y lo persiste si hace falta)."""
    if registro.archivo_pdf:
        return registro.archivo_pdf.path
    contenido = exportar_registro_xlsx(registro, para_pdf=True)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp.write(contenido)
        xlsx_path = tmp.name
    try:
        from django.core.files.base import ContentFile

        from ..tasks import convertir_a_pdf_sync

        pdf_path = convertir_a_pdf_sync(xlsx_path, reparar_sincrono=True)
        if not pdf_path:
            return None
        with open(pdf_path, 'rb') as fh:
            registro.archivo_pdf.save(
                f'{registro.uuid.hex}.pdf', ContentFile(fh.read()), save=True,
            )
        try:
            os.unlink(pdf_path)
        except OSError:
            pass
        return registro.archivo_pdf.path
    finally:
        try:
            os.unlink(xlsx_path)
        except OSError:
            pass
