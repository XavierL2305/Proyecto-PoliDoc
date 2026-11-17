"""Documentos sin código (no ISO): permisos, visibilidad y consultas.

Dos ámbitos:

* ``sgc`` — soportes/registros de fuente institucionales. Los administran y
  consultan **administradores y jefes de departamento**. Se incluyen en la
  Lista Maestra (SGC-FOR-006) como origen *Ext* con ``N/A``.
* ``personal`` — archivos de uso personal. Los ve **solo su dueño** (y el
  administrador, para soporte). No se incluyen en la Lista Maestra.

Regla de menú: la **acción de crear** está siempre disponible para quien
pueda crear; la **bandeja** (listado) solo se muestra cuando hay al menos un
registro visible (ver ``tiene_personales``/``tiene_sgc``).
"""
from django.db.models import Q

from ..models import DocumentoSinCodigo

AMBITO_PERSONAL = 'personal'
AMBITO_SGC = 'sgc'


def es_administrador(usuario):
    perfil = getattr(usuario, 'perfil', None)
    return bool(perfil and perfil.es_administrador)


def es_jefe(usuario):
    perfil = getattr(usuario, 'perfil', None)
    return bool(perfil and perfil.es_jefe)


def puede_crear(usuario, ambito):
    """¿Puede crear un documento de ``ambito``?"""
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        return False
    if ambito == AMBITO_PERSONAL:
        return True
    if ambito == AMBITO_SGC:
        return es_administrador(usuario) or es_jefe(usuario)
    return False


def puede_ver(usuario, documento):
    """¿Puede ver/descargar ``documento``?"""
    if (usuario is None or not getattr(usuario, 'is_authenticated', False)
            or documento is None):
        return False
    if es_administrador(usuario):
        return True
    if documento.ambito == AMBITO_PERSONAL:
        return documento.propietario_id == usuario.id
    if documento.ambito == AMBITO_SGC:
        return es_jefe(usuario)
    return False


def puede_editar(usuario, documento):
    """Editar/eliminar: en esta fase, mismo criterio que ver."""
    return puede_ver(usuario, documento)


def puede_eliminar(usuario, documento):
    return puede_ver(usuario, documento)


def personales_de(usuario):
    """Bandeja personal: SOLO los del propio usuario (aunque sea admin)."""
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        return DocumentoSinCodigo.objects.none()
    return DocumentoSinCodigo.objects.filter(
        propietario=usuario, ambito=AMBITO_PERSONAL, activo=True,
    )


def sgc_visibles(usuario):
    """Bandeja SGC: administradores y jefes; el resto no ve nada."""
    if not (es_administrador(usuario) or es_jefe(usuario)):
        return DocumentoSinCodigo.objects.none()
    return DocumentoSinCodigo.objects.filter(
        ambito=AMBITO_SGC, activo=True,
    )


def visibles(usuario, ambito):
    """Queryset de la bandeja ``ambito`` para ``usuario``."""
    if ambito == AMBITO_PERSONAL:
        return personales_de(usuario)
    if ambito == AMBITO_SGC:
        return sgc_visibles(usuario)
    return DocumentoSinCodigo.objects.none()


def tiene_personales(usuario):
    """¿Mostrar la bandeja personal? (existe al menos uno del usuario)."""
    return personales_de(usuario).exists()


def tiene_sgc(usuario):
    """¿Mostrar la bandeja SGC? (existe al menos uno y el usuario es admin/jefe)."""
    return sgc_visibles(usuario).exists()


def context_menu(usuario):
    """Banderas para la barra lateral (sin exponer datos de documentos)."""
    return {
        'nav_docs_personales': tiene_personales(usuario),
        'nav_docs_sgc_sin_codigo': tiene_sgc(usuario),
        'nav_puede_crear_personal': puede_crear(usuario, AMBITO_PERSONAL),
        'nav_puede_crear_sgc': puede_crear(usuario, AMBITO_SGC),
    }


# ---------------------------------------------------------------------------
# Avisos y purga de documentos personales por inactividad
# ---------------------------------------------------------------------------

def _nombre(usuario):
    if usuario is None:
        return ''
    return usuario.get_full_name() or usuario.username


def base_actividad(usuario):
    """Fecha desde la que se mide la inactividad (login o alta del usuario)."""
    return usuario.last_login or usuario.date_joined


def dias_inactivo(usuario, ahora=None):
    """Días completos sin iniciar sesión (mínimo 0)."""
    from django.utils import timezone

    ahora = ahora or timezone.now()
    return max(0, (ahora - base_actividad(usuario)).days)


def destinatarios_aviso(usuario):
    """Jefe directo del usuario; si no hay, los administradores.

    Nunca incluye al propio usuario (está inactivo) y evita duplicados.
    """
    from autenticacion.models import PerfilUsuario

    destinatarios = []
    perfil = getattr(usuario, 'perfil', None)
    jefe = getattr(getattr(perfil, 'departamento', None), 'jefe', None)
    if jefe is not None and jefe.id != usuario.id and jefe.is_active:
        destinatarios.append(jefe)
    if not destinatarios:
        destinatarios = [
            p.usuario for p in PerfilUsuario.objects.filter(
                rol='administrador', usuario__is_active=True,
            ).select_related('usuario')
        ]
    vistos = set()
    resultado = []
    for user in destinatarios:
        if user is None or user.id in vistos:
            continue
        vistos.add(user.id)
        resultado.append(user)
    return resultado


def _enviar_correo(usuario, asunto, lineas):
    """Correo informativo al usuario; nunca rompe la tarea."""
    import logging

    from django.conf import settings
    from django.core import mail

    logger = logging.getLogger(__name__)
    email = (usuario.email or '').strip()
    if not email:
        return False
    try:
        mensaje = mail.EmailMessage(
            asunto, '\n'.join(lineas), settings.DEFAULT_FROM_EMAIL, [email],
        )
        mail.mailers.default.send_messages([mensaje])
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            'No se pudo enviar el aviso de purga a %s: %s', email, exc,
        )
        return False


def _notificar(usuario, titulo, mensaje):
    """Notificación in-app al jefe directo (o administradores)."""
    from autenticacion.models import Notificacion

    perfil = getattr(usuario, 'perfil', None)
    departamento = getattr(perfil, 'departamento', None)
    enviadas = 0
    for destinatario in destinatarios_aviso(usuario):
        Notificacion.objects.create(
            usuario=destinatario,
            titulo=titulo,
            mensaje=mensaje,
            tipo='documento_personal_aviso',
            url_accion='',
            departamento=departamento,
        )
        enviadas += 1
    return enviadas


def _avisar(usuario, dias, cantidad, dias_restantes, fecha_limite, segundo):
    etiqueta = 'Segundo aviso' if segundo else 'Aviso'
    titulo = (
        f'{etiqueta}: documentos personales por eliminar'
    )
    mensaje = (
        f'{_nombre(usuario)} ({usuario.username}) lleva {dias} días sin '
        f'iniciar sesión y tiene {cantidad} documento(s) personal(es). '
        f'Serán eliminados el {fecha_limite:%d/%m/%Y} '
        f'({dias_restantes} días restantes).'
    )
    _notificar(usuario, titulo, mensaje)
    _enviar_correo(
        usuario,
        '[POLIDOC] Tus documentos personales serán eliminados',
        [
            f'Hola {_nombre(usuario)},',
            '',
            f'Llevas {dias} días sin iniciar sesión y tienes {cantidad} '
            'documento(s) personal(es) en POLIDOC.',
            '',
            f'Serán eliminados el {fecha_limite:%d/%m/%Y} ({dias_restantes} '
            'días restantes). Si inicias sesión, el contador se reinicia.',
            '',
            '— POLIDOC',
        ],
    )


def _notificar_purga(usuario, cantidad, dias):
    mensaje = (
        f'Se eliminaron {cantidad} documento(s) personal(es) de '
        f'{_nombre(usuario)} ({usuario.username}) por inactividad '
        f'({dias} días sin iniciar sesión).'
    )
    _notificar(usuario, 'Documentos personales eliminados', mensaje)


def _purgar_documentos(docs):
    """Elimina los archivos y las filas de los documentos personales."""
    cantidad = 0
    for doc in docs:
        if doc.archivo:
            try:
                doc.archivo.delete(save=False)
            except Exception:  # noqa: BLE001 - el archivo huérfano no bloquea
                pass
        doc.delete()
        cantidad += 1
    return cantidad


def _auditar_purga(usuario, cantidad, dias):
    try:
        from auditoria.services import registrar_log_iso

        registrar_log_iso(
            usuario=None,
            accion_tipo='purga_documentos_personales',
            accion_categoria='documento',
            recurso_tipo='documentos_sin_codigo',
            recurso_id=str(usuario.id),
            recurso_codigo='',
            recurso_nombre=usuario.username,
            detalles={
                'cantidad': cantidad,
                'dias_inactivo': dias,
                'ambito': AMBITO_PERSONAL,
            },
            resultado_exitoso=True,
            resultado_mensaje=(
                f'Purga de {cantidad} documento(s) personal(es) por inactividad'
            ),
            ip_address='127.0.0.1',
            user_agent='Celery Beat',
        )
    except Exception:  # noqa: BLE001 - la auditoría no bloquea la purga
        import logging
        logging.getLogger(__name__).error(
            'No se pudo auditar la purga de personales de %s', usuario.username,
        )


def procesar_purga_personal(ahora=None, dry_run=False):
    """Avisa (60/80 días) y purga (90) los documentos personales inactivos.

    Solo toca ``ambito='personal'``. Los soportes ``sgc`` y los documentos ISO
    nunca se ven afectados. Idempotente: usa ``AvisoPurgaPersonal`` para no
    repetir avisos y se reinicia al iniciar sesión.

    Returns:
        dict: ``{'avisos', 'segundos', 'purgados', 'reiniciados'}``.
    """
    from datetime import timedelta

    from django.conf import settings
    from django.contrib.auth.models import User
    from django.utils import timezone

    from ..models import AvisoPurgaPersonal, DocumentoSinCodigo

    ahora = ahora or timezone.now()
    aviso_dias = getattr(settings, 'PERSONAL_DOCS_AVISO_DIAS', 60)
    segundo_dias = getattr(settings, 'PERSONAL_DOCS_SEGUNDO_AVISO_DIAS', 80)
    purge_dias = getattr(settings, 'PERSONAL_DOCS_PURGE_DIAS', 90)

    resumen = {'avisos': 0, 'segundos': 0, 'purgados': 0, 'reiniciados': 0}
    propietarios = list(
        DocumentoSinCodigo.objects
        .filter(ambito=AMBITO_PERSONAL, activo=True)
        .values_list('propietario_id', flat=True)
        .distinct()
    )
    usuarios = User.objects.filter(pk__in=propietarios).select_related(
        'perfil__departamento__jefe',
    )

    for usuario in usuarios:
        docs = DocumentoSinCodigo.objects.filter(
            propietario=usuario, ambito=AMBITO_PERSONAL, activo=True,
        )
        dias = dias_inactivo(usuario, ahora)

        # Volvió a iniciar sesión: se reinicia el estado de avisos.
        if dias < aviso_dias:
            if AvisoPurgaPersonal.objects.filter(usuario=usuario).exists():
                resumen['reiniciados'] += 1
                if not dry_run:
                    AvisoPurgaPersonal.objects.filter(usuario=usuario).delete()
                    docs.filter(
                        marcado_para_eliminar_en__isnull=False,
                    ).update(marcado_para_eliminar_en=None)
            continue

        # Purga definitiva.
        if dias >= purge_dias:
            cantidad = docs.count()
            if not cantidad:
                continue
            resumen['purgados'] += cantidad
            if not dry_run:
                _purgar_documentos(docs)
                AvisoPurgaPersonal.objects.filter(usuario=usuario).delete()
                _notificar_purga(usuario, cantidad, dias)
                _auditar_purga(usuario, cantidad, dias)
            continue

        aviso, _ = AvisoPurgaPersonal.objects.get_or_create(usuario=usuario)
        restantes = purge_dias - dias
        fecha_limite = base_actividad(usuario) + timedelta(days=purge_dias)

        if dias >= segundo_dias and not aviso.aviso_80_en:
            resumen['segundos'] += 1
            if not dry_run:
                _avisar(usuario, dias, docs.count(), restantes, fecha_limite, True)
                aviso.aviso_80_en = ahora
                aviso.save(update_fields=['aviso_80_en'])
                docs.filter(marcado_para_eliminar_en__isnull=True).update(
                    marcado_para_eliminar_en=ahora,
                )
        elif dias >= aviso_dias and not aviso.aviso_60_en:
            resumen['avisos'] += 1
            if not dry_run:
                _avisar(usuario, dias, docs.count(), restantes, fecha_limite, False)
                aviso.aviso_60_en = ahora
                aviso.save(update_fields=['aviso_60_en'])
                docs.filter(marcado_para_eliminar_en__isnull=True).update(
                    marcado_para_eliminar_en=ahora,
                )

    return resumen
