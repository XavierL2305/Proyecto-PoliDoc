from django.db.models import Q

from .models import Documento


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


def puede_descargar(usuario, documento):
    if usuario.perfil.es_administrador:
        return True
    if es_jefe_de_departamento(usuario, documento):
        return True
    if documento.es_publico:
        return True
    return False


def puede_inhabilitar(usuario, documento):
    if usuario.perfil.es_administrador:
        return True, "directo"
    if es_jefe_de_departamento(usuario, documento):
        return True, "directo"
    return False, "requiere_aprobacion"


def puede_transferir(usuario, documento):
    if usuario.perfil.es_jefe:
        depto_doc = getattr(documento, 'departamento', None)
        if depto_doc == usuario.perfil.departamento:
            return True, "directo"
    if usuario.perfil.es_administrador:
        return True, "requiere_aprobacion"
    return False, "sin_permiso"


def puede_ver_documento(usuario, documento, requerir_aprobada=False):
    """Verifica si un usuario tiene permiso para ver un documento.

    Retorna True si ALGUNA de estas condiciones se cumple:
    - Es administrador
    - Es propietario del documento
    - Documento es público y activo
    - Mismo departamento y activo (excepto DPR)
    - Jefe del departamento del documento
    - Es jefe y el documento es DPR (global, cualquier jefe)
    - Tiene asignación activa con puede_leer
    - Tiene distribución activa (revocado=False)
    - Está involucrado en una solicitud del documento

    Con ``requerir_aprobada=True`` las solicitudes de descarga/acceso solo
    cuentan si están APROBADAS. Se usa para servir el CONTENIDO del archivo
    (visor), de modo que una solicitud pendiente no filtre el documento antes
    de su aprobación — aunque sí permita ver la página de detalle/estado.
    """
    if not hasattr(usuario, 'perfil'):
        return False

    if usuario.perfil.es_administrador:
        return True
    if documento.propietario == usuario:
        return True

    es_activo = documento.estado == 'activo'

    if documento.es_publico and es_activo:
        return True

    es_dpr = getattr(documento.tipo, 'key', None) == 'descripcion_procesos'

    if es_dpr:
        if usuario.perfil.es_jefe:
            return True
    else:
        mismo_depto = (
            documento.departamento
            and usuario.perfil.departamento
            and documento.departamento == usuario.perfil.departamento
        )
        if mismo_depto and es_activo:
            return True
        if es_jefe_de_departamento(usuario, documento):
            return True

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
    if SolicitudAprobacionDocumento.objects.filter(
        documento=documento
    ).filter(
        Q(aprobador=usuario) | Q(solicitante=usuario)
    ).exists():
        return True

    filtro_descarga = {'documento': documento, 'solicitado_por': usuario}
    if requerir_aprobada:
        filtro_descarga['estado'] = 'aprobada'
    if SolicitudDescarga.objects.filter(**filtro_descarga).exists():
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
