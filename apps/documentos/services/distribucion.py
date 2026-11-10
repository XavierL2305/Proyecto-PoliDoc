"""Distribución de documentos y planilla SGC-FOR-002 (Control de Distribución).

Servicio extraído de la vista ``distribuir_documento``:

* ``crear_distribuciones`` — crea las copias controladas (internas y
  externas), las agrupa en lotes de hasta :data:`FILAS_POR_PLANILLA`,
  genera la planilla FOR-002 de cada lote, envía correos y notifica.
* ``politica_adjunto_pdf`` — decide si el archivo puede viajar por correo
  (mismos controles que la descarga: restringido nunca; confidencial solo
  admin o jefe efectivo, con registro).
* ``generar_planilla_lote`` / ``exportar_planilla_lote`` — construyen o
  regeneran la ``FormatoRespuesta`` del lote y su XLSX.
"""
import logging
import uuid
from datetime import timedelta
from typing import NamedTuple

from django.conf import settings
from django.core import mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .firmas import VIGENCIA_INVITACION_DIAS

logger = logging.getLogger(__name__)

#: Filas de destinatarios por planilla FOR-002 (filas 19-51 de la plantilla).
#: Es el valor por defecto: si el Constructor cambia ``max_filas`` de la
#: tabla del formato, manda ese (:func:`filas_por_planilla`).
FILAS_POR_PLANILLA = 33

#: Código del formato de Control de Distribución.
FORMATO_FOR_002 = 'SGC-FOR-002'

#: ORIGEN de la información por defecto en la planilla (INT = interna).
ORIGEN_POR_DEFECTO = 'INT'

#: Tamaño máximo del archivo adjunto a un correo (5 MB).
EMAIL_ATTACH_MAX_BYTES = 5 * 1024 * 1024


class DestinatarioExterno(NamedTuple):
    """Destinatario sin cuenta en POLIDOC."""

    nombre: str
    email: str
    cargo: str = ''
    organizacion: str = ''


class ResultadoDistribucion(NamedTuple):
    """Resultado agregado de ``crear_distribuciones``."""

    creadas: int
    lotes: int
    planillas: int
    sin_correo: int
    fallidos: int
    adjuntos: int
    sin_adjunto: int
    invitaciones: int
    mensajes: list


# ---------------------------------------------------------------------------
# Política de adjunto
# ---------------------------------------------------------------------------

def politica_adjunto_pdf(documento, distribuido_por):
    """Decide si el archivo del documento puede adjuntarse a un correo.

    Espejo de los controles de descarga:

    * ``restringido`` → nunca por correo (portal/vínculo seguro).
    * ``confidencial`` → solo administrador o jefe efectivo del departamento
      (queda registrado en ``motivo_sin_adjunto``).
    * ``publico`` / ``interno`` → permitido.

    Returns:
        tuple: ``(permitido, motivo)`` — el motivo explica la decisión y se
        persiste en ``DistribucionDocumento.motivo_sin_adjunto``.
    """
    clasif = (documento.clasificacion or 'interno').lower()
    if clasif == 'restringido':
        return False, 'Documento restringido: no se adjunta por correo'
    if clasif == 'confidencial':
        perfil = getattr(distribuido_por, 'perfil', None)
        es_admin = bool(perfil and perfil.es_administrador)
        if es_admin:
            return True, 'Confidencial: adjunto autorizado (administrador)'
        from ..jefatura import es_jefe_efectivo
        if es_jefe_efectivo(distribuido_por, documento.departamento):
            return True, 'Confidencial: adjunto autorizado (jefe de departamento)'
        return False, 'Confidencial: requiere administrador o jefe del departamento'
    return True, clasif


# ---------------------------------------------------------------------------
# Lotes
# ---------------------------------------------------------------------------

def dividir_en_lotes(destinatarios, tamano=FILAS_POR_PLANILLA):
    """Parte ``destinatarios`` en sublistas de a lo sumo ``tamano``.

    La planilla SGC-FOR-002 tiene 33 filas de datos (19-51); cada sublista
    genera su propia planilla (``FormatoRespuesta``).
    """
    if tamano < 1:
        raise ValueError('El tamaño del lote debe ser >= 1')
    return [
        destinatarios[i:i + tamano]
        for i in range(0, len(destinatarios), tamano)
    ]


def _formato_for_002():
    from ..models import Formato
    return Formato.objects.filter(codigo=FORMATO_FOR_002, activo=True).first()


def _filas_por_planilla(formato):
    """Filas de datos disponibles en la plantilla FOR-002 (``formato``).

    Lee ``max_filas`` de la tabla configurada en el Constructor (o lo deriva
    de ``fila_fin - fila_inicio + 1``). Si el formato está ausente o la
    configuración es incompleta devuelve :data:`FILAS_POR_PLANILLA`.
    """
    if formato is None:
        return FILAS_POR_PLANILLA
    for campo in formato.get_todos_los_campos():
        if campo.tipo != 'tabla':
            continue
        config = campo.config_tabla or {}
        filas = config.get('max_filas')
        if not filas:
            inicio = config.get('fila_inicio')
            fin = config.get('fila_fin')
            if inicio and fin and fin >= inicio:
                filas = fin - inicio + 1
        try:
            filas = int(filas)
        except (TypeError, ValueError):
            continue
        if filas > 0:
            return filas
    return FILAS_POR_PLANILLA


def filas_por_planilla():
    """Filas por planilla según el formato FOR-002 activo (33 por defecto)."""
    return _filas_por_planilla(_formato_for_002())


# ---------------------------------------------------------------------------
# Creación de distribuciones
# ---------------------------------------------------------------------------

def crear_distribuciones(documento, *, internos=(), externos=(), metodo='sistema',
                         observaciones='', distribuido_por, adjuntar_pdf=False,
                         request=None, tipo=''):
    """Crea las distribuciones de ``documento`` agrupadas en lotes FOR-002.

    Args:
        documento: ``Documento`` activo a distribuir.
        internos: iterable de ``User`` con cuenta en POLIDOC.
        externos: iterable de :class:`DestinatarioExterno`.
        metodo: ``sistema`` | ``email`` | ``impreso``.
        observaciones: texto libre por distribución.
        distribuido_por: usuario que distribuye (admin/jefe/propietario).
        adjuntar_pdf: intención de adjuntar el archivo al correo (checkbox
            del formulario, apagado por defecto). La política de clasificación
            puede vetarlo y queda registrado en ``motivo_sin_adjunto``.
        request: ``HttpRequest`` para construir enlaces absolutos.
        tipo: identificador de tipo de documento (``'proceso'``…) para URLs.

    Returns:
        ResultadoDistribucion
    """
    from autenticacion.models import Notificacion

    internos = list(internos or [])
    externos = list(externos or [])
    if not internos and not externos:
        raise ValueError('Debe haber al menos un destinatario')

    formato = _formato_for_002()
    requiere_firma = formato is not None
    filas = _filas_por_planilla(formato)

    # (tipo_dest, objeto) en el orden dado por la vista.
    fila_destinatarios = [('interno', u) for u in internos]
    fila_destinatarios += [('externo', e) for e in externos]

    lotes = dividir_en_lotes(fila_destinatarios, filas)

    creadas = sin_correo = fallidos = adjuntos = sin_adjunto = invitaciones = 0
    planillas = 0
    mensajes = []
    #: Correos pendientes de enviar: se mandan YA COMMITADA la transacción
    #: para que un rollback no deje mensajes huérfanos en la bandeja.
    email_pendientes = []
    invitaciones_pendientes = []

    with transaction.atomic():
        for chunk in lotes:
            lote_id = uuid.uuid4()
            for tipo_dest, obj in chunk:
                dist = _crear_una_distribucion(
                    documento=documento, tipo_dest=tipo_dest, obj=obj,
                    lote_id=lote_id, metodo=metodo,
                    observaciones=observaciones,
                    distribuido_por=distribuido_por,
                    requiere_firma=requiere_firma,
                )
                creadas += 1

                _registrar_auditoria(dist, documento, distribuido_por)

                if tipo_dest == 'interno':
                    Notificacion.objects.create(
                        usuario=obj, titulo='Documento Distribuido',
                        mensaje=(
                            f'Se le ha distribuido el documento '
                            f'"{documento.nombre}" ({documento.codigo}).'
                        ),
                        tipo='documento_distribuido',
                        url_accion=f'/documentos/{tipo}/{documento.uuid}/',
                        departamento=documento.departamento,
                        documento=documento,
                    )

                if requiere_firma and tipo_dest == 'externo':
                    invitacion = _crear_invitacion_externa(dist)
                    if invitacion is not None:
                        invitaciones_pendientes.append(
                            (invitacion, invitacion.token_claro, dist)
                        )

                if metodo == 'email':
                    email_pendientes.append(dist)

            if requiere_firma:
                fr, _resp = generar_planilla_lote(lote_id)
                if fr is not None:
                    planillas += 1

    # --- Fuera de la transacción: correos ---
    for invitacion, token, dist in invitaciones_pendientes:
        ok, error = enviar_invitacion_firma(
            invitacion, token, dist, dist.documento, tipo, request,
        )
        invitacion.email_enviado = ok
        invitacion.error_envio = error or ''
        invitacion.save(update_fields=['email_enviado', 'error_envio'])
        if ok:
            invitaciones += 1
        else:
            logger.warning(
                'Invitación de firma no enviada a %s: %s',
                dist.email_externo, error,
            )

    for dist in email_pendientes:
        ok, error = enviar_email_distribucion(
            dist, dist.documento, tipo, request, adjuntar_pdf=adjuntar_pdf,
        )
        if ok:
            adjuntos += 1 if dist.adjunto_pdf_enviado else 0
            if not dist.adjunto_pdf_enviado:
                sin_adjunto += 1
        elif error == 'sin_correo':
            sin_correo += 1
        else:
            fallidos += 1

    if len(lotes) > 1:
        mensajes.append(
            f'Se generaron {len(lotes)} planillas SGC-FOR-002 '
            f'({filas} destinatarios por planilla).'
        )
    if metodo == 'email' and sin_correo:
        mensajes.append(
            f'{sin_correo} destinatario(s) no tienen correo; no se les envió email.'
        )
    if metodo == 'email' and fallidos:
        mensajes.append(f'{fallidos} correo(s) no se pudieron enviar.')

    return ResultadoDistribucion(
        creadas=creadas, lotes=len(lotes), planillas=planillas,
        sin_correo=sin_correo, fallidos=fallidos, adjuntos=adjuntos,
        sin_adjunto=sin_adjunto, invitaciones=invitaciones, mensajes=mensajes,
    )


def _crear_una_distribucion(*, documento, tipo_dest, obj, lote_id, metodo,
                            observaciones, distribuido_por, requiere_firma):
    """Crea una ``DistribucionDocumento`` (+ ``FirmaElectronica`` si aplica)."""
    from ..models import DistribucionDocumento, FirmaElectronica

    if tipo_dest == 'interno':
        dist = DistribucionDocumento.objects.create(
            documento=documento, usuario=obj, metodo=metodo,
            distribuido_por=distribuido_por, observaciones=observaciones,
            lote=lote_id,
        )
    else:
        dist = DistribucionDocumento.objects.create(
            documento=documento, usuario=None, metodo=metodo,
            distribuido_por=distribuido_por, observaciones=observaciones,
            lote=lote_id,
            nombre_externo=obj.nombre,
            cargo_externo=obj.cargo or '',
            email_externo=obj.email or '',
            organizacion_externa=obj.organizacion or '',
        )

    # Constancia: la entrega queda confirmada al momento de distribuir por
    # sistema/impreso; por correo se confirma cuando el envío termina bien.
    if metodo == 'impreso':
        dist.constancia = 'impreso'
        dist.fecha_entrega = timezone.now()
        dist.save(update_fields=['constancia', 'fecha_entrega'])
    elif metodo == 'sistema':
        dist.constancia = 'sistema'
        dist.fecha_entrega = timezone.now()
        dist.save(update_fields=['constancia', 'fecha_entrega'])

    if requiere_firma:
        dist.estado_firma = 'pendiente'
        dist.save(update_fields=['estado_firma'])
        _crear_firma_pendiente(dist, tipo_dest, obj)

    return dist


def _crear_firma_pendiente(dist, tipo_dest, obj):
    """Registra la ``FirmaElectronica`` pendiente de la distribución."""
    from ..models import FirmaElectronica

    if tipo_dest == 'interno':
        FirmaElectronica.objects.create(
            distribucion=dist, tipo='interna',
            firmante_clave=FirmaElectronica.clave_interna(obj),
            firmante=obj,
            firmante_nombre=obj.get_full_name() or obj.username,
            firmante_email=obj.email or '',
            firmante_cargo=_cargo_de(obj),
        )
    else:
        email = (obj.email or '').strip()
        clave = (
            FirmaElectronica.clave_externa(email)
            if email else f'externo:{dist.pk}'
        )
        FirmaElectronica.objects.create(
            distribucion=dist, tipo='externa',
            firmante_clave=clave,
            firmante_nombre=obj.nombre,
            firmante_email=email,
            firmante_cargo=obj.cargo or '',
            datos={'organizacion': obj.organizacion or ''},
        )


def _cargo_de(usuario):
    """Cargo del interno para la planilla: 'Rol — Departamento'."""
    perfil = getattr(usuario, 'perfil', None)
    if not perfil:
        return ''
    rol = perfil.get_rol_display() or perfil.rol
    depto = str(perfil.departamento) if perfil.departamento else ''
    return f'{rol} — {depto}' if depto else rol


def _registrar_auditoria(dist, documento, distribuido_por):
    """Auditoría ISO por distribución (interna o externa)."""
    try:
        from auditoria.middleware import get_client_ip
        from auditoria.services import registrar_distribucion
        registrar_distribucion(
            usuario=distribuido_por,
            recurso_tipo=documento.tipo_id or '',
            recurso_id=str(documento.uuid),
            destinatario=dist.destinatario_nombre,
            metodo=dist.metodo,
            codigo=documento.codigo,
            distribucion_id=dist.id,
            ip_address=get_client_ip(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Auditoría de distribución falló: %s', exc)


# ---------------------------------------------------------------------------
# Correo de distribución
# ---------------------------------------------------------------------------

def enviar_email_distribucion(distribucion, documento, tipo, request,
                              adjuntar_pdf=False):
    """Envía el correo de una distribución (interna o externa).

    Returns:
        tuple: ``(ok, error)`` — ``(True, '')`` si se envió;
        ``(False, 'sin_correo')`` si el destinatario no tiene correo;
        ``(False, '<detalle>')`` si falló el envío.

    El resultado del intento (adjunto sí/no y su motivo) queda registrado en
    ``adjunto_pdf_enviado`` / ``motivo_sin_adjunto``.
    """
    email_destino = distribucion.destinatario_email
    if not email_destino:
        distribucion.motivo_sin_adjunto = 'Sin correo del destinatario'
        distribucion.save(update_fields=['motivo_sin_adjunto'])
        return False, 'sin_correo'

    link = request.build_absolute_uri(
        reverse('documentos:detalle', args=[tipo, documento.uuid])
    ) if request else ''

    nombre = distribucion.destinatario_nombre
    asunto = f'[POLIDOC] Documento distribuido: {documento.codigo} - {documento.nombre}'
    lineas = [
        f'Hola {nombre},',
        '',
        'Se te ha distribuido el siguiente documento:',
        '',
        f'  Código: {documento.codigo}',
        f'  Nombre: {documento.nombre}',
        f'  Versión: {documento.version_actual}',
        f'  Departamento: {documento.departamento}',
        '',
    ]
    if link:
        lineas += [f'Puedes verlo en: {link}', '']
    lineas += [f'Distribuido por: {distribucion.distribuido_por.get_full_name() or distribucion.distribuido_por.username}']
    if distribucion.observaciones:
        lineas += ['', f'Observaciones: {distribucion.observaciones}']
    if distribucion.es_externo and distribucion.firma_pendiente:
        lineas += [
            '',
            'Debes firmar la conformidad de esta distribución; '
            'recibirás una invitación de firma con un enlace.',
        ]
    lineas += ['', '— POLIDOC']

    message = mail.EmailMessage(
        asunto, '\n'.join(lineas),
        settings.DEFAULT_FROM_EMAIL, [email_destino],
    )

    # Decisión de adjunto (intención + política), sin tocar la BD todavía.
    adjunto_ok = False
    if not adjuntar_pdf:
        motivo_final = 'Adjunto no solicitado'
    else:
        permitido, motivo_politica = politica_adjunto_pdf(
            documento, distribucion.distribuido_por,
        )
        if not permitido:
            motivo_final = motivo_politica
        else:
            motivo_final = ''
            archivo = documento.archivo
            if archivo and archivo.name:
                try:
                    if archivo.size <= EMAIL_ATTACH_MAX_BYTES:
                        message.attach_file(archivo.path)
                        adjunto_ok = True
                    else:
                        motivo_final = 'Archivo excede el límite de 5 MB'
                except (OSError, ValueError) as exc:
                    motivo_final = f'No se pudo adjuntar: {exc}'
            else:
                motivo_final = 'El documento no tiene archivo'

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
    if distribucion.constancia == 'pendiente':
        distribucion.constancia = 'email'
        distribucion.fecha_entrega = timezone.now()
        campos += ['constancia', 'fecha_entrega']
    distribucion.save(update_fields=campos)
    return True, ''


# ---------------------------------------------------------------------------
# Invitación de firma externa
# ---------------------------------------------------------------------------

def _crear_invitacion_externa(distribucion):
    """Crea la ``InvitacionFirma`` (token) sin enviarla.

    El token en claro queda solo en memoria (atributo no persistido
    ``token_claro``): la BD guarda su SHA-256. El envío lo hace el llamador
    fuera de la transacción con :func:`enviar_invitacion_firma`.

    Returns:
        InvitacionFirma | None: ``None`` si el destinatario no tiene correo
        o la firma no está pendiente.
    """
    import hashlib
    import secrets

    from ..models import InvitacionFirma

    if not distribucion.email_externo:
        return None

    firma = distribucion.firmas.filter(tipo='externa').first()
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


def enviar_invitacion_firma(invitacion, token, distribucion, documento,
                            tipo, request):
    """Correo de invitación de firma externa (token de un solo uso, 7 días).

    Returns:
        tuple: ``(ok, error)``.
    """
    email_destino = distribucion.email_externo
    if not email_destino:
        return False, 'sin_correo'

    url = reverse('documentos:firmar_invitacion', args=[token])
    link = request.build_absolute_uri(url) if request else url

    asunto = (
        f'[POLIDOC] Invitación a firmar distribución: '
        f'{documento.codigo} - {documento.nombre}'
    )
    lineas = [
        f'Hola {distribucion.nombre_externo},',
        '',
        'Se te ha distribuido el siguiente documento y necesitamos tu '
        'firma de conformidad en la planilla de control (SGC-FOR-002):',
        '',
        f'  Código: {documento.codigo}',
        f'  Nombre: {documento.nombre}',
        f'  Versión: {documento.version_actual}',
        f'  Organización: {distribucion.organizacion_externa or "—"}',
        '',
        'Para firmar ingresa al siguiente enlace (válido por '
        f'{VIGENCIA_INVITACION_DIAS} días y de un solo uso):',
        link,
        '',
        f'Distribuido por: {distribucion.distribuido_por.get_full_name() or distribucion.distribuido_por.username}',
        '',
        '— POLIDOC',
    ]

    message = mail.EmailMessage(
        asunto, '\n'.join(lineas),
        settings.DEFAULT_FROM_EMAIL, [email_destino],
    )
    try:
        mail.mailers.default.send_messages([message])
        return True, ''
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ---------------------------------------------------------------------------
# Planilla FOR-002 del lote
# ---------------------------------------------------------------------------

def generar_planilla_lote(lote_id):
    """Crea/actualiza la ``FormatoRespuesta`` del lote (una por lote).

    Los valores de cabecera (tipo, código, título, edición, versión,
    vigencia) se releen del documento; las filas de destinatarios se
    reconstruyen desde las distribuciones del lote (incluidas las firmas).

    Returns:
        tuple: ``(formato_respuesta, respuestas)`` o ``(None, None)`` si el
        formato SGC-FOR-002 no está activo o el lote no existe.
    """
    from ..models import DistribucionDocumento, FormatoRespuesta

    formato = _formato_for_002()
    if formato is None:
        return None, None

    dists = list(
        DistribucionDocumento.objects.filter(lote=lote_id)
        .select_related('usuario', 'usuario__perfil', 'documento')
        .prefetch_related('firmas')
        .order_by('id')
    )
    if not dists:
        return None, None

    documento = dists[0].documento
    respuestas = _respuestas_lote(documento, dists, formato=formato)

    fr, _creada = FormatoRespuesta.objects.update_or_create(
        origen_tipo='distribucion',
        origen_id=str(lote_id),
        defaults={'formato': formato, 'respuestas': respuestas},
    )
    return fr, respuestas


def _respuestas_lote(documento, dists, formato=None):
    """Dict de respuestas de la planilla FOR-002 para un lote.

    Se construye **desde el formato configurado** en el Constructor (campos y
    columnas), no desde claves fijas: renombrar la ``clave`` de un campo o de
    una columna no rompe la generación de la planilla.

    * Cabecera → valores automáticos del documento por ``origen``.
    * ORIGEN EXT/INT → el ``select`` con opciones ``celda_marca`` recibe
      :data:`ORIGEN_POR_DEFECTO`.
    * Tabla → una fila por distribución, con las columnas declaradas en
      ``config_tabla`` (identificadas por su ``rol``).
    """
    from ..models import mapear_valores_auto, valores_documento

    formato = formato or _formato_for_002()
    if formato is None:
        return {}

    respuestas = mapear_valores_auto(formato, valores_documento(documento))
    for campo in formato.get_todos_los_campos():
        if campo.tipo == 'select' and _es_marca_de_origen(campo):
            respuestas[campo.clave] = _origen_por_defecto(campo)
        elif campo.tipo == 'tabla':
            respuestas[campo.clave] = [
                _fila_destinatario(d, campo.config_tabla or {}) for d in dists
            ]
    return respuestas


def _es_marca_de_origen(campo):
    """¿Es un ``select`` que marca una celda de la plantilla (EXT/INT)?"""
    for opcion in campo.opciones or []:
        if isinstance(opcion, dict) and str(opcion.get('celda_marca') or '').strip():
            return True
    return False


def _origen_por_defecto(campo):
    """Valor con el que se marca el ORIGEN (``INT`` salvo que cambie en UI)."""
    opciones = [o for o in (campo.opciones or []) if isinstance(o, dict)]
    if not opciones:
        return ORIGEN_POR_DEFECTO
    for opcion in opciones:
        valor = str(opcion.get('valor') or '').strip()
        if valor.lower() == ORIGEN_POR_DEFECTO.lower():
            return valor
    # Sin "INT" explícito se asume que la última opción es la interna
    # (en la plantilla EXT va primero y INT después).
    return opciones[-1].get('valor') or ORIGEN_POR_DEFECTO


def _fila_destinatario(dist, config_tabla=None):
    """Fila de la tabla FOR-002 para una distribución.

    Cada columna se llena por su ``rol`` (identificador semántico: nombre,
    cargo, fecha, retiro, firma). Si la columna no lo declara se usa su
    ``clave`` como rol, de modo que las configuraciones antiguas siguen
    funcionando y renombrar claves no vacía la planilla.
    """
    columnas = (config_tabla or {}).get('columnas') or []
    if not columnas:
        columnas = [{'clave': clave, 'rol': clave} for clave in _VALORES_FILA]

    fila = {}
    for columna in columnas:
        if not isinstance(columna, dict):
            continue
        clave = str(columna.get('clave') or '').strip()
        if not clave:
            continue
        rol = str(columna.get('rol') or clave).strip()
        obtener = _VALORES_FILA.get(rol)
        fila[clave] = obtener(dist) if obtener else ''
    return fila


def _texto_firma(dist):
    """Texto de la columna FIRMA CONFORME (vacío mientras no se firme)."""
    if dist.estado_firma != 'firmada':
        return ''
    for firma in dist.firmas.all():
        if firma.estado == 'firmada':
            return firma.firmante_nombre or firma.firmante_clave
    return ''


#: Valor de cada columna de la tabla del FOR-002, por ``rol``.
#: Se declara después de sus funciones para leerse en tiempo de llamada.
_VALORES_FILA = {
    'nombre': lambda d: d.destinatario_nombre,
    'cargo': lambda d: (
        _cargo_de(d.usuario) if d.usuario_id else (d.cargo_externo or '')
    ),
    'fecha': lambda d: timezone.localtime(d.fecha_distribucion).date().isoformat(),
    'retiro': lambda d: 'si' if d.retiro_version_anterior else 'no',
    'firma': _texto_firma,
}


def exportar_planilla_lote(lote_id):
    """XLSX de la planilla FOR-002 del lote (regenerándola antes).

    Returns:
        bytes: contenido XLSX.

    Raises:
        ValueError: si el lote no tiene planilla (formato inactivo).
    """
    from .exportacion import exportar_xlsx_formato

    fr, respuestas = generar_planilla_lote(lote_id)
    if fr is None:
        raise ValueError('El lote no tiene planilla SGC-FOR-002')
    return exportar_xlsx_formato(fr.formato, respuestas)


# ---------------------------------------------------------------------------
# Regeneración tras una firma
# ---------------------------------------------------------------------------

def regenerar_planilla_de_firma(firma):
    """Regenera la planilla del lote tras una firma (columna FIRMA).

    No es un error si el formato no está activo: la firma ya quedó
    registrada en ``FirmaElectronica``.
    """
    dist = firma.distribucion
    if not dist.lote:
        return None
    fr, _ = generar_planilla_lote(dist.lote)
    return fr
