from django.db.models import Q

from .models import Documento


def q_documentos_visibles(usuario):
    """Q de documentos visibles para el usuario (criterio por rol/clasificación).

    - admin: todo.
    - jefe: su departamento con clasificación ``interno``/``confidencial`` +
      propios + públicos activos + asignaciones/distribuciones. ``restringido``
      NO se ve por departamento.
    - coordinador/trabajador: propios + públicos activos + asignaciones
      (coordinador) + distribuciones no revocadas.

    Las asignaciones/distribuciones se resuelven por subconsulta (``id__in``)
    para no introducir JOINs que dupliquen resultados.
    """
    from .models import AsignacionDocumento, DistribucionDocumento

    perfil = getattr(usuario, 'perfil', None)
    if perfil and perfil.es_administrador:
        return Q()

    publicos = Q(clasificacion='publico', estado='activo')
    ids_asignados = AsignacionDocumento.objects.filter(
        coordinador=usuario, activa=True,
    ).values('documento_id')
    ids_distribuidos = DistribucionDocumento.objects.filter(
        usuario=usuario, revocado=False,
    ).values('documento_id')
    grants = Q(id__in=ids_asignados) | Q(id__in=ids_distribuidos)

    base = Q(propietario=usuario) | publicos | grants
    if perfil and perfil.es_jefe and perfil.departamento_id:
        base |= Q(
            departamento_id=perfil.departamento_id,
            clasificacion__in=['interno', 'confidencial'],
        )
    return base


def es_jefe_de_departamento(usuario, documento):
    return (usuario.perfil.es_jefe
            and documento.departamento
            and documento.departamento == usuario.perfil.departamento)


def obtener_documento(tipo, doc_id):
    """Obtiene el documento por tipo y UUID"""
    try:
        return Documento.objects.get(uuid=doc_id, tipo=tipo)
    except Documento.DoesNotExist:
        return None


def puede_editar(usuario, documento):
    """Solo el propietario puede editar"""
    return documento.propietario == usuario


def descarga_autorizada(documento, usuario):
    """True si hay una solicitud de descarga **aprobada, vigente y de la
    versión actual** del documento para ``usuario`` (ISO: autorización temporal).
    """
    from django.utils import timezone

    from .models import SolicitudDescarga

    if not documento or not getattr(usuario, 'pk', None):
        return False
    return SolicitudDescarga.objects.filter(
        documento=documento,
        solicitado_por=usuario,
        estado='aprobada',
        version_aprobada=documento.version_actual,
        fecha_expiracion__gt=timezone.now(),
    ).exists()


def tiene_distribucion(documento, usuario):
    """True si ``usuario`` tiene una distribución no revocada del documento."""
    from .models import DistribucionDocumento

    if not documento or not getattr(usuario, 'pk', None):
        return False
    return DistribucionDocumento.objects.filter(
        documento=documento, usuario=usuario, revocado=False,
    ).exists()


def puede_descargar(usuario, documento):
    """Descarga directa según clasificación (ISO 9001:7.5.3).

    admin y públicos: directo; jefe del departamento para ``interno``; el
    propietario de un ``interno``; quien tenga una distribución vigente; y
    quien tenga una solicitud aprobada vigente y de la versión actual.
    ``confidencial``/``restringido`` requieren solicitud aprobada.
    """
    if not hasattr(usuario, 'perfil'):
        return False
    if usuario.perfil.es_administrador:
        return True
    if documento.clasificacion == 'publico':
        return True
    if (documento.clasificacion == 'interno'
            and es_jefe_de_departamento(usuario, documento)):
        return True
    if documento.clasificacion == 'interno' and documento.propietario == usuario:
        return True
    if tiene_distribucion(documento, usuario):
        return True
    if descarga_autorizada(documento, usuario):
        return True
    return False


def puede_inhabilitar(usuario, documento):
    """¿Puede iniciar (o aplicar) inhabilitar/reactivar este documento?

    - ``(True, 'directo')``: lo aplica él mismo (administrador o jefe de
      Gerencia General), sin aprobación.
    - ``(True, 'solicitar')``: puede iniciarlo, pero exige UNA aprobación
      (propietario o jefe del departamento del documento).
    - ``(False, 'sin_permiso')``: no está autorizado a tocar el estado.
    """
    perfil = usuario.perfil
    from .services.cambios_estado import es_jefe_gg, puede_iniciar

    if perfil.es_administrador or es_jefe_gg(usuario):
        return True, "directo"
    if puede_iniciar(usuario, documento):
        return True, "solicitar"
    return False, "sin_permiso"


def puede_transferir(usuario, documento):
    """Solo administradores y el jefe del departamento del documento.

    Ambos transfieren de forma directa (no existe aprobación de transferencia).
    """
    if usuario.perfil.es_jefe:
        depto_doc = getattr(documento, 'departamento', None)
        if depto_doc == usuario.perfil.departamento:
            return True, "directo"
    if usuario.perfil.es_administrador:
        return True, "directo"
    return False, "sin_permiso"


def puede_ver_documento(usuario, documento, requerir_aprobada=False):
    """Verifica si un usuario tiene permiso para ver un documento.

    Retorna True si ALGUNA de estas condiciones se cumple:
    - Es administrador
    - Es propietario del documento
    - Documento público y activo
    - ``interno``: mismo departamento y activo, o jefe del departamento, o DPR
      (cualquier jefe)
    - ``confidencial``: jefe del departamento
    - ``restringido``: solo admin/propietario/grants (abajo)
    - Tiene asignación activa con puede_leer
    - Tiene distribución activa (revocado=False)
    - Está involucrado en una solicitud del documento

    Con ``requerir_aprobada=True`` las solicitudes de descarga/acceso solo
    cuentan si están APROBADAS (y, en la descarga, vigentes y de la versión
    actual). Se usa para servir el CONTENIDO del archivo (visor), de modo que
    una solicitud pendiente no filtre el documento antes de su aprobación —
    aunque sí permita ver la página de detalle/estado.
    """
    if not hasattr(usuario, 'perfil'):
        return False

    if usuario.perfil.es_administrador:
        return True
    if documento.propietario == usuario:
        return True

    es_activo = documento.estado == 'activo'
    clasificacion = documento.clasificacion

    if clasificacion == 'publico' and es_activo:
        return True

    es_dpr = getattr(documento.tipo, 'key', None) == 'descripcion_procesos'

    if es_dpr:
        # DPR es global para jefes (cualquiera); el resto entra por grants.
        if usuario.perfil.es_jefe:
            return True
    elif clasificacion == 'interno':
        mismo_depto = (
            documento.departamento
            and usuario.perfil.departamento
            and documento.departamento == usuario.perfil.departamento
        )
        if mismo_depto and es_activo:
            return True
        if es_jefe_de_departamento(usuario, documento):
            return True
    elif clasificacion == 'confidencial':
        # Need-to-know dentro del departamento: solo el jefe (y los grants).
        if es_jefe_de_departamento(usuario, documento):
            return True
    # restringido: sin acceso por departamento; solo admin/propietario/grants.

    from .models import AsignacionDocumento, DistribucionDocumento
    if AsignacionDocumento.objects.filter(
        documento=documento, coordinador=usuario, activa=True, puede_leer=True
    ).exists():
        return True

    if DistribucionDocumento.objects.filter(
        documento=documento, usuario=usuario, revocado=False
    ).exists():
        return True

    from .models import (
        SolicitudAprobacionDocumento,
        SolicitudDescarga, AprobacionDescarga,
        SolicitudAccesoDocumento,
    )
    # Intencionadamente SIN filtro de estado: los participantes del flujo de
    # aprobación (aprobador/solicitante de la creación, y el jefe que aprueba una
    # descarga) necesitan ver el contenido ANTES de resolver, incluso con
    # requerir_aprobada=True. Solo a los SOLICITANTES ajenos al flujo se les exige
    # una solicitud aprobada (filtros de estado más abajo).
    if SolicitudAprobacionDocumento.objects.filter(
        documento=documento
    ).filter(
        Q(aprobador=usuario) | Q(solicitante=usuario)
    ).exists():
        return True

    if requerir_aprobada:
        # El contenido solo se sirve con una autorización aprobada, vigente y
        # de la versión actual.
        if descarga_autorizada(documento, usuario):
            return True
    elif SolicitudDescarga.objects.filter(
        documento=documento, solicitado_por=usuario
    ).exists():
        return True
    if AprobacionDescarga.objects.filter(
        solicitud__documento=documento, aprobador=usuario
    ).exists():
        return True
    if requerir_aprobada:
        q_acceso = Q(jefe_destino=usuario, estado='aprobada')
        q_acceso |= Q(solicitante=usuario, estado='aprobada')
    else:
        q_acceso = Q(jefe_destino=usuario) | Q(solicitante=usuario)
    if SolicitudAccesoDocumento.objects.filter(documento=documento).filter(q_acceso).exists():
        return True

    return False


def documentos_usuario(usuario):
    """Retorna todos los documentos del usuario (propios + asignados)"""
    from .models import AsignacionDocumento

    documentos = []
    ids_vistos = set()

    # 1. Documentos propios
    for doc in Documento.objects.filter(propietario=usuario):
        tipo_archivo = getattr(doc, 'tipo_archivo', None)
        documentos.append({
            'tipo': doc.tipo_id,
            'id': str(doc.uuid),
            'nombre': doc.nombre,
            'codigo': doc.codigo,
            'tipo_archivo': tipo_archivo.upper() if tipo_archivo else 'N/A',
            'estado': doc.estado,
            'propietario': f"{doc.propietario.get_full_name() or doc.propietario.username}",
            'departamento': doc.departamento.codigo if doc.departamento else '',
            'fecha_modificacion': doc.fecha_modificacion.strftime('%d/%m/%Y'),
            'version': doc.version_actual,
            'es_propio': True,
        })
        ids_vistos.add(doc.id)

    # 2. Documentos asignados (como coordinador)
    asignaciones = AsignacionDocumento.objects.filter(
        coordinador=usuario,
        activa=True
    ).select_related('documento', 'documento__propietario', 'documento__departamento')

    for asignacion in asignaciones:
        doc = asignacion.documento
        if doc and doc.id not in ids_vistos:
            tipo_archivo = getattr(doc, 'tipo_archivo', None)
            documentos.append({
            'tipo': doc.tipo_id,
                'id': str(doc.uuid),
                'nombre': doc.nombre,
                'codigo': doc.codigo,
                'tipo_archivo': tipo_archivo.upper() if tipo_archivo else 'N/A',
                'estado': doc.estado,
                'propietario': f"{doc.propietario.get_full_name() or doc.propietario.username}",
                'departamento': doc.departamento.codigo if doc.departamento else '',
                'fecha_modificacion': doc.fecha_modificacion.strftime('%d/%m/%Y'),
                'version': doc.version_actual,
                'es_propio': False,
                'puede_leer': asignacion.puede_leer,
                'puede_editar': asignacion.puede_editar,
                'puede_eliminar': asignacion.puede_eliminar,
            })
            ids_vistos.add(doc.id)

    return documentos
