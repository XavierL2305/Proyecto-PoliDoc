"""Distribución de registros (SGC-FOR-010) y acuse del destinatario (P1).

Espejo de :mod:`documentos.services.distribucion` adaptado a
``RegistroFormato`` (sin lotes FOR-002):

* :func:`crear_distribuciones_registro` — crea las distribuciones, deja el
  acuse pendiente (firma SES), notifica y envía los correos.
* :func:`enviar_email_distribucion_registro` /
  :func:`enviar_invitacion_acuse` — correos multipart con banner de
  clasificación; el enlace externo permite **descargar hasta la caducidad**
  y firmar **una sola vez**.
* :func:`subir_acuse` / :func:`revocar_distribucion_registro` — acuse
  físico escaneado (impreso) y revocación (bloquea enlace y acuse).
* :func:`constancia_*` — planilla de distribución y acuses (CSV/XLSX/PDF).

Un único token por destinatario externo: se reutiliza ``InvitacionFirma``
(7 días, SHA-256) en lugar de un token propio de la distribución.
"""
import csv
import hashlib
import io
import logging
import os
import secrets
from datetime import timedelta
from typing import NamedTuple

from django.conf import settings
from django.core import mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .avisos import aviso_texto, cuerpo_email_html, clasificacion_de_documento
from .distribucion import EMAIL_ATTACH_MAX_BYTES, politica_adjunto_pdf
from .firmas import VIGENCIA_INVITACION_DIAS

logger = logging.getLogger(__name__)


class ResultadoDistribucionRegistro(NamedTuple):
    """Resultado agregado de :func:`crear_distribuciones_registro`."""

    creadas: int
    enviados: int
    fallidos: int
    sin_correo: int
    invitaciones: int
    adjuntos: int
    sin_adjunto: int
    mensajes: list


# ---------------------------------------------------------------------------
# Permisos y políticas
# ---------------------------------------------------------------------------

def clasificacion_de_registro(registro):
    """Clasificación efectiva (documento de contexto o ``interno``)."""
    from .registros import documento_de_contexto

    return clasificacion_de_documento(documento_de_contexto(registro))


def politica_adjunto_registro(registro, distribuido_por):
    """Misma política de adjunto que los documentos (contexto del registro)."""
    from .registros import documento_de_contexto

    documento = documento_de_contexto(registro)
    if documento is None:
        return True, 'Sin documento de contexto: adjunto permitido'
    return politica_adjunto_pdf(documento, distribuido_por)


def puede_gestionar_distribucion_registro(usuario, registro):
    """Admin, responsable del registro o jefe efectivo del depto de contexto."""
    from .registros import _es_admin, documento_de_contexto

    if _es_admin(usuario):
        return True
    if registro.responsable_id == usuario.id:
        return True
    documento = documento_de_contexto(registro)
    if documento is None:
        return False
    from ..jefatura import es_jefe_efectivo

    return es_jefe_efectivo(usuario, documento.departamento)


# ---------------------------------------------------------------------------
# Alta de distribuciones
# ---------------------------------------------------------------------------

def crear_distribuciones_registro(registro, *, internos=(), externos=(),
                                  metodo='sistema', observaciones='',
                                  distribuido_por, adjuntar_pdf=False,
                                  request=None):
    """Crea las distribuciones de ``registro`` y dispara acuses/correos.

    Args:
        registro: ``RegistroFormato`` en estado ``aprobado``.
        internos: iterable de ``User``.
        externos: iterable de :class:`DestinatarioExterno`.
        metodo: ``sistema`` | ``email`` | ``impreso``.
        distribuido_por: usuario que distribuye (admin/jefe/responsable).
        adjuntar_pdf: adjuntar el PDF al correo interno (la política de
            clasificación puede vetarlo y queda en ``motivo_sin_adjunto``).
        request: ``HttpRequest`` para enlaces absolutos.

    Raises:
        ValueError: sin destinatarios, método inválido o registro no
            aprobado.

    Returns:
        ResultadoDistribucionRegistro
    """
    from autenticacion.models import Notificacion

    from ..models import DistribucionRegistro
    from .distribucion import DestinatarioExterno

    internos = list(internos or [])
    externos = list(externos or [])
    if not internos and not externos:
        raise ValueError('Debe haber al menos un destinatario')
    if metodo not in ('sistema', 'email', 'impreso'):
        raise ValueError('Método de distribución no reconocido')
    if registro.estado != 'aprobado':
        raise ValueError('Solo se pueden distribuir registros aprobados.')

    from .registros import documento_de_contexto

    documento = documento_de_contexto(registro)

    creadas = enviados = fallidos = sin_correo = 0
    invitaciones = adjuntos = sin_adjunto = 0
    mensajes = []
    #: Correos/invitaciones se mandan YA COMMITADA la transacción.
    email_pendientes = []
    invitaciones_pendientes = []

    with transaction.atomic():
        for destino in internos:
            dist = DistribucionRegistro.objects.create(
                registro=registro, usuario=destino, metodo=metodo,
                distribuido_por=distribuido_por,
                observaciones=observaciones or '',
            )
            creadas += 1
            _confirmar_entrega(dist)
            _crear_firma_acuse(dist, 'interno', destino)
            _auditar_distribucion(dist, registro, distribuido_por)
            Notificacion.objects.create(
                usuario=destino, titulo='Registro Distribuido',
                mensaje=(
                    f'Se le ha distribuido el registro '
                    f'{registro.formato_codigo} - "{registro.titulo}".'
                ),
                tipo='registro_distribuido',
                url_accion=f'/documentos/registros/{registro.uuid}/',
                departamento=getattr(documento, 'departamento', None),
                documento=documento,
            )
            if metodo == 'email':
                email_pendientes.append(dist)

        for externo in externos:
            dist = DistribucionRegistro.objects.create(
                registro=registro, usuario=None, metodo=metodo,
                distribuido_por=distribuido_por,
                observaciones=observaciones or '',
                nombre_externo=externo.nombre,
                cargo_externo=externo.cargo or '',
                email_externo=(externo.email or '').strip(),
                organizacion_externa=externo.organizacion or '',
            )
            creadas += 1
            _confirmar_entrega(dist)
            _crear_firma_acuse(dist, 'externo', externo)
            _auditar_distribucion(dist, registro, distribuido_por)

            invitacion = _crear_invitacion(dist)
            if invitacion is not None:
                invitaciones_pendientes.append(
                    (invitacion, invitacion.token_claro, dist),
                )
            elif not dist.email_externo:
                sin_correo += 1

    # --- Fuera de la transacción: correos ---
    for invitacion, token, dist in invitaciones_pendientes:
        ok, error = enviar_invitacion_acuse(
            invitacion, token, dist, registro, request,
        )
        invitacion.email_enviado = ok
        invitacion.error_envio = error or ''
        invitacion.save(update_fields=['email_enviado', 'error_envio'])
        if ok:
            invitaciones += 1
        else:
            logger.warning(
                'Invitación de acuse no enviada a %s: %s',
                dist.email_externo, error,
            )
            if error == 'sin_correo':
                sin_correo += 1
            else:
                fallidos += 1

    for dist in email_pendientes:
        ok, error = enviar_email_distribucion_registro(
            dist, registro, request, adjuntar_pdf=adjuntar_pdf,
        )
        if ok:
            enviados += 1
            if dist.adjunto_pdf_enviado:
                adjuntos += 1
            else:
                sin_adjunto += 1
        elif error == 'sin_correo':
            sin_correo += 1
        else:
            fallidos += 1

    if sin_correo and metodo == 'email':
        mensajes.append(
            f'{sin_correo} destinatario(s) sin correo; no se les envió email.',
        )
    if fallidos:
        mensajes.append(f'{fallidos} correo(s) no se pudieron enviar.')

    return ResultadoDistribucionRegistro(
        creadas=creadas, enviados=enviados, fallidos=fallidos,
        sin_correo=sin_correo, invitaciones=invitaciones,
        adjuntos=adjuntos, sin_adjunto=sin_adjunto, mensajes=mensajes,
    )


def _confirmar_entrega(dist):
    """Sistema/impreso confirman la entrega al distribuir; email al enviar."""
    if dist.metodo in ('sistema', 'impreso') and not dist.fecha_entrega:
        dist.fecha_entrega = timezone.now()
        dist.save(update_fields=['fecha_entrega'])


def _crear_firma_acuse(dist, tipo_dest, obj):
    """Registra la ``FirmaElectronica`` pendiente del acuse."""
    from .firmas import crear_firma

    if tipo_dest == 'interno':
        from .distribucion import _cargo_de

        crear_firma(
            dist, tipo='interna', usuario=obj,
            firmante_cargo=_cargo_de(obj),
        )
        return

    from ..models import FirmaElectronica

    email = (obj.email or '').strip()
    clave = (
        FirmaElectronica.clave_externa(email)
        if email else f'externo:{dist.pk}'
    )
    crear_firma(
        dist, tipo='externa',
        firmante_clave=clave,
        firmante_nombre=obj.nombre,
        firmante_email=email,
        firmante_cargo=obj.cargo or '',
        datos={'organizacion': obj.organizacion or ''},
    )


def _crear_invitacion(dist):
    """``InvitacionFirma`` (token) para el acuse externo, sin enviarla.

    Returns:
        InvitacionFirma | None: ``None`` si no hay correo o firma pendiente.
    """
    from ..models import InvitacionFirma

    if not dist.email_externo:
        return None

    firma = dist.firmas.filter(tipo='externa').first()
    if firma is None or firma.estado != 'pendiente':
        return None

    token = secrets.token_urlsafe(32)
    invitacion = InvitacionFirma(
        firma=firma,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expira=timezone.now() + timedelta(days=VIGENCIA_INVITACION_DIAS),
    )
    invitacion.token_claro = token  # no se persiste
    invitacion.save()
    return invitacion


def _auditar_distribucion(dist, registro, distribuido_por):
    """Auditoría ISO de la copia controlada (mismo espejo que documentos)."""
    try:
        from auditoria.middleware import get_client_ip
        from auditoria.services import registrar_distribucion
        from .registros import documento_de_contexto

        documento = documento_de_contexto(registro)
        registrar_distribucion(
            usuario=distribuido_por,
            recurso_tipo=(documento.tipo_id if documento else 'registro'),
            recurso_id=str(registro.uuid),
            destinatario=dist.destinatario_nombre,
            metodo=dist.metodo,
            codigo=registro.formato_codigo,
            distribucion_id=dist.id,
            ip_address=get_client_ip(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Auditoría de distribución de registro falló: %s', exc)


# ---------------------------------------------------------------------------
# Correos (multipart con banner de clasificación)
# ---------------------------------------------------------------------------

def enviar_email_distribucion_registro(distribucion, registro, request,
                                       adjuntar_pdf=False):
    """Correo de distribución a un destinatario INTERNO (enlace en la app).

    Returns:
        tuple: ``(ok, error)`` — ``(False, 'sin_correo')`` sin correo.
    """
    email_destino = distribucion.destinatario_email
    if not email_destino:
        distribucion.motivo_sin_adjunto = 'Sin correo del destinatario'
        distribucion.save(update_fields=['motivo_sin_adjunto'])
        return False, 'sin_correo'

    clasificacion = clasificacion_de_registro(registro)
    url = reverse('documentos:registro_detalle', args=[registro.uuid])
    link = request.build_absolute_uri(url) if request else url
    nombre = distribucion.destinatario_nombre
    asunto = (
        f'[POLIDOC] Registro distribuido: '
        f'{registro.formato_codigo} - {registro.titulo}'
    )
    lineas = [
        f'Hola {nombre},',
        '',
        'Se te ha distribuido el siguiente registro del Sistema de Gestión '
        'de la Calidad:',
        '',
        f'  Código: {registro.formato_codigo}',
        f'  Título: {registro.titulo}',
        f'  Fecha del registro: {registro.fecha:%d/%m/%Y}',
        '',
        f'Puedes consultarlo en: {link}',
        '',
        f'Distribuido por: {distribucion.distribuido_por.get_full_name() or distribucion.distribuido_por.username}',
    ]
    if distribucion.observaciones:
        lineas += ['', f'Observaciones: {distribucion.observaciones}']
    lineas += ['', aviso_texto(clasificacion)]

    message = mail.EmailMultiAlternatives(
        asunto, '\n'.join(lineas),
        settings.DEFAULT_FROM_EMAIL, [email_destino],
    )
    message.attach_alternative(
        cuerpo_email_html(lineas, clasificacion), 'text/html',
    )

    adjunto_ok = False
    if not adjuntar_pdf:
        motivo_final = 'Adjunto no solicitado'
    else:
        permitido, motivo_politica = politica_adjunto_registro(
            registro, distribucion.distribuido_por,
        )
        if not permitido:
            motivo_final = motivo_politica
        else:
            motivo_final = ''
            ruta = None
            try:
                from .registros import exportar_registro_pdf

                ruta = exportar_registro_pdf(registro)
            except Exception:  # noqa: BLE001
                ruta = None
            if not ruta:
                motivo_final = 'No se pudo generar el PDF del registro'
            else:
                try:
                    if os.path.getsize(ruta) <= EMAIL_ATTACH_MAX_BYTES:
                        message.attach_file(ruta)
                        adjunto_ok = True
                    else:
                        motivo_final = 'Archivo excede el límite de 5 MB'
                except (OSError, ValueError) as exc:
                    motivo_final = f'No se pudo adjuntar: {exc}'

    try:
        mail.mailers.default.send_messages([message])
    except Exception as exc:
        distribucion.enviado = False
        distribucion.error = str(exc)
        distribucion.adjunto_pdf_enviado = False
        distribucion.motivo_sin_adjunto = motivo_final[:200]
        distribucion.save(update_fields=[
            'enviado', 'error', 'adjunto_pdf_enviado', 'motivo_sin_adjunto',
        ])
        return False, str(exc)

    distribucion.enviado = True
    distribucion.adjunto_pdf_enviado = adjunto_ok
    distribucion.motivo_sin_adjunto = motivo_final[:200]
    campos = ['enviado', 'adjunto_pdf_enviado', 'motivo_sin_adjunto']
    if not distribucion.fecha_entrega:
        distribucion.fecha_entrega = timezone.now()
        campos.append('fecha_entrega')
    distribucion.save(update_fields=campos)
    return True, ''


def enviar_invitacion_acuse(invitacion, token, distribucion, registro,
                            request):
    """Correo de invitación de acuse externo (descarga + firma por token).

    Returns:
        tuple: ``(ok, error)``.
    """
    email_destino = distribucion.email_externo
    if not email_destino:
        return False, 'sin_correo'

    url_firma = reverse('documentos:firmar_invitacion', args=[token])
    link_firma = request.build_absolute_uri(url_firma) if request else url_firma
    url_descarga = reverse('documentos:descarga_invitacion', args=[token])
    link_descarga = (
        request.build_absolute_uri(url_descarga) if request else url_descarga
    )

    clasificacion = clasificacion_de_registro(registro)
    asunto = (
        f'[POLIDOC] Acuse de distribución de registro: '
        f'{registro.formato_codigo} - {registro.titulo}'
    )
    lineas = [
        f'Hola {distribucion.nombre_externo},',
        '',
        'Se te ha distribuido el siguiente registro y necesitamos tu acuse '
        'de conformidad:',
        '',
        f'  Código: {registro.formato_codigo}',
        f'  Título: {registro.titulo}',
        f'  Fecha del registro: {registro.fecha:%d/%m/%Y}',
        f'  Organización: {distribucion.organizacion_externa or "—"}',
        '',
        'Para descargar el archivo ingresa a:',
        link_descarga,
        '',
        'Para firmar el acuse ingresa a (válido por '
        f'{VIGENCIA_INVITACION_DIAS} días; la firma es de un solo uso):',
        link_firma,
        '',
        f'Distribuido por: {distribucion.distribuido_por.get_full_name() or distribucion.distribuido_por.username}',
        '',
        aviso_texto(clasificacion),
    ]

    message = mail.EmailMultiAlternatives(
        asunto, '\n'.join(lineas),
        settings.DEFAULT_FROM_EMAIL, [email_destino],
    )
    message.attach_alternative(
        cuerpo_email_html(lineas, clasificacion), 'text/html',
    )
    try:
        mail.mailers.default.send_messages([message])
        return True, ''
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ---------------------------------------------------------------------------
# Acuse escaneado / revocación
# ---------------------------------------------------------------------------

def subir_acuse(distribucion, archivo, usuario):
    """Registra el acuse físico escaneado (método impreso).

    Raises:
        PermissionError: distribución revocada.
        ValueError: acuse ya registrado.
        ValidationError: archivo inválido (extensión/contenido/tamaño).
    """
    from django.core.exceptions import ValidationError

    if distribucion.revocado or distribucion.estado_acuse == 'revocado':
        raise PermissionError('La distribución fue revocada.')
    if distribucion.estado_acuse == 'acusado':
        raise ValueError('El acuse de esta distribución ya fue registrado.')

    distribucion.acuse_archivo = archivo
    distribucion.estado_acuse = 'acusado'
    distribucion.fecha_acuse = timezone.now()
    campos = ['acuse_archivo', 'estado_acuse', 'fecha_acuse']
    if not distribucion.fecha_entrega:
        distribucion.fecha_entrega = distribucion.fecha_acuse
        campos.append('fecha_entrega')
    distribucion.full_clean()
    distribucion.save(update_fields=campos)

    from .registros import _auditar

    _auditar(
        distribucion.registro, usuario, 'acuse_registro',
        f'Acuse escaneado de {distribucion.destinatario_nombre} '
        f'(distribución {distribucion.id}).',
    )
    return distribucion


def revocar_distribucion_registro(distribucion, usuario, motivo=''):
    """Revoca la distribución: bloquea el enlace seguro y el acuse pendiente.

    Un acuse ya firmado se conserva (es evidencia); solo cambia el estado
    si el acuse estaba pendiente o vencido.
    """
    if distribucion.revocado:
        raise ValueError('Esta distribución ya fue revocada.')

    distribucion.revocado = True
    distribucion.fecha_revocacion = timezone.now()
    distribucion.revocado_por = usuario
    distribucion.motivo_revocacion = motivo or ''
    campos = [
        'revocado', 'fecha_revocacion', 'revocado_por', 'motivo_revocacion',
    ]
    if distribucion.estado_acuse in ('pendiente', 'vencido'):
        distribucion.estado_acuse = 'revocado'
        campos.append('estado_acuse')
    distribucion.save(update_fields=campos)

    from .registros import _auditar

    _auditar(
        distribucion.registro, usuario, 'distribucion_revocada',
        f'Distribución revocada ({distribucion.destinatario_nombre}): '
        f'{motivo or "sin motivo"}',
    )
    return distribucion


# ---------------------------------------------------------------------------
# Constancia de distribución y acuses (CSV / XLSX / PDF)
# ---------------------------------------------------------------------------

#: Encabezados de la planilla de constancia.
CONSTANCIA_CABECERAS = (
    'Destinatario', 'Correo', 'Tipo', 'Método', 'Distribuido en',
    'Entrega', 'Estado del acuse', 'Acusado en', 'Firma SES (id/hash)',
    'Revocado',
)

#: Máximo de caracteres del aviso incrustado en la planilla.
_AVISO_MAX = 900


def constancia_filas(registro):
    """Filas de la constancia (dict por distribución, ordenadas)."""
    filas = []
    for dist in registro.distribuciones.select_related(
        'usuario', 'firma', 'revocado_por',
    ):
        firma = dist.firma
        filas.append({
            'destinatario': dist.destinatario_nombre,
            'correo': dist.destinatario_email or '—',
            'tipo': 'Externo' if dist.es_externo else 'Interno',
            'metodo': dist.get_metodo_display(),
            'distribuido_en': timezone.localtime(
                dist.fecha_distribucion,
            ).strftime('%d/%m/%Y %H:%M'),
            'entrega': (
                timezone.localtime(dist.fecha_entrega).strftime('%d/%m/%Y %H:%M')
                if dist.fecha_entrega else '—'
            ),
            'estado_acuse': dist.get_estado_acuse_display(),
            'acusado_en': (
                timezone.localtime(dist.fecha_acuse).strftime('%d/%m/%Y %H:%M')
                if dist.fecha_acuse else '—'
            ),
            'firma': (
                f'#{firma.pk} {firma.hash_contenido[:16]}…'
                if firma and firma.hash_contenido else '—'
            ),
            'revocado': 'Sí' if dist.revocado else 'No',
        })
    return filas


def constancia_csv(registro):
    """Bytes CSV (UTF-8 con BOM para Excel) de la constancia."""
    from .registros import documento_de_contexto

    buffer = io.StringIO()
    escritor = csv.writer(buffer)
    escritor.writerow(['Constancia de distribución y acuses'])
    escritor.writerow(['Registro', f'{registro.formato_codigo} - {registro.titulo}'])
    escritor.writerow(['Fecha del registro', registro.fecha.strftime('%d/%m/%Y')])
    escritor.writerow(['Responsable', registro.responsable.get_full_name() or registro.responsable.username])
    documento = documento_de_contexto(registro)
    escritor.writerow(['Documento de contexto', str(documento.codigo) if documento else '—'])
    escritor.writerow([])
    escritor.writerow(list(CONSTANCIA_CABECERAS))
    for fila in constancia_filas(registro):
        escritor.writerow([fila[k] for k in (
            'destinatario', 'correo', 'tipo', 'metodo', 'distribuido_en',
            'entrega', 'estado_acuse', 'acusado_en', 'firma', 'revocado',
        )])
    escritor.writerow([])
    aviso = aviso_texto(clasificacion_de_registro(registro)).replace('\n', ' ')
    escritor.writerow(['Aviso', aviso[:_AVISO_MAX]])
    # BOM UTF-8: Excel abre el CSV con acentos correctamente.
    return b'\xef\xbb\xbf' + buffer.getvalue().encode('utf-8')


def constancia_xlsx(registro):
    """Bytes XLSX de la constancia (cabecera, filas y aviso)."""
    import openpyxl
    from openpyxl.styles import Alignment, Font

    from .registros import documento_de_contexto

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Constancia'

    ws.append(['Constancia de distribución y acuses'])
    ws['A1'].font = Font(bold=True, size=13)
    documento = documento_de_contexto(registro)
    for etiqueta, valor in (
        ('Registro', f'{registro.formato_codigo} - {registro.titulo}'),
        ('Fecha del registro', registro.fecha.strftime('%d/%m/%Y')),
        ('Responsable', registro.responsable.get_full_name() or registro.responsable.username),
        ('Documento de contexto', str(documento.codigo) if documento else '—'),
        ('Generada el', timezone.localtime().strftime('%d/%m/%Y %H:%M')),
    ):
        ws.append([etiqueta, valor])
    ws.append([])
    ws.append(list(CONSTANCIA_CABECERAS))
    for celda in ws[ws.max_row]:
        celda.font = Font(bold=True)
    for fila in constancia_filas(registro):
        ws.append([fila[k] for k in (
            'destinatario', 'correo', 'tipo', 'metodo', 'distribuido_en',
            'entrega', 'estado_acuse', 'acusado_en', 'firma', 'revocado',
        )])
    ws.append([])
    aviso = aviso_texto(clasificacion_de_registro(registro))
    celda_aviso = ws.cell(row=ws.max_row + 1, column=1, value=aviso[:_AVISO_MAX])
    celda_aviso.alignment = Alignment(wrap_text=True, vertical='top')

    anchos = (28, 30, 10, 12, 18, 18, 18, 18, 26, 10)
    for idx, ancho in enumerate(anchos, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = ancho

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def constancia_pdf(registro):
    """Bytes del PDF de la constancia (XLSX → conversor del proyecto).

    Returns:
        bytes | None: ``None`` si la conversión falla.
    """
    xlsx = constancia_xlsx(registro)
    from .xlsx_comun import xlsx_bytes_a_pdf

    return xlsx_bytes_a_pdf(xlsx)
