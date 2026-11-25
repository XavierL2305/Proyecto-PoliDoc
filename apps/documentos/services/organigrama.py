"""Servicio de dominio para el Organigrama Corporativo Interactivo (SGC-ORG-001).

Centraliza esquema JSON, validación estricta de payloads, control de acceso
(lectura pública al organigrama activo; construcción exclusiva para jefaturas,
delegados vigentes y administradores), concurrencia optimista, versionado en
``VersionDocumento`` y trazabilidad ISO 15489.
"""
import hashlib
import json
import math
import re

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

TIPO_ORGANIGRAMA = 'organigrama'
TIPO_MAPA_PROCESOS = 'mapa_procesos'
TIPOS_DIAGRAMA_INTERACTIVO = frozenset({TIPO_ORGANIGRAMA, TIPO_MAPA_PROCESOS})
CODIGO_ORGANIGRAMA = 'SGC-ORG-001'
DEPTO_CODIGO_ORG = 'SGC'
DEPTO_NOMBRE_ORG = 'Sistema de Gestión de la Calidad'

MAX_PAYLOAD_BYTES = 1_048_576  # 1 MiB
MAX_SECTIONS = 100
MAX_NODES = 1000
MAX_EDGES = 1500

SHAPES_VALIDAS = frozenset({'rect', 'rounded', 'pill', 'diamond', 'text'})
ANCHORS_VALIDOS = frozenset({'top', 'right', 'bottom', 'left'})
ROUTINGS_VALIDOS = frozenset({'orthogonal', 'straight'})
BORDER_STYLES_VALIDOS = frozenset({'solid', 'dashed', 'dotted'})
TEXT_ALIGNS_VALIDOS = frozenset({'left', 'center', 'right'})
FONT_WEIGHTS_VALIDOS = frozenset({'normal', 'bold'})
FONT_STYLES_VALIDOS = frozenset({'normal', 'italic'})
EMPRESAS_VALIDAS = frozenset({'', 'PTCA', 'PTH', 'FP', 'FS'})

COLOR_HEX_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')
COLORES_SEMANTICOS = frozenset({'auto', 'transparent'})
ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,64}$')
IDS_PROHIBIDOS = frozenset({'__proto__', 'constructor', 'prototype'})


class OrganigramaValidationError(ValueError):
    """Error de validación estructural, de tipo o seguridad en el payload."""


class ConflictoEdicionError(Exception):
    """Error de concurrencia optimista al guardar sobre una revisión obsoleta."""

    def __init__(self, mensaje, revision_actual=None, version_actual=None):
        super().__init__(mensaje)
        self.revision_actual = revision_actual
        self.version_actual = version_actual


def esquema_inicial():
    """Estructura vacía válida para inicializar un organigrama."""
    return {
        'schema_version': 1,
        'settings': {'gridSize': 20, 'snapToGrid': True},
        'sections': [],
        'nodes': [],
        'edges': [],
    }


def _rechazar_constante_no_finita(valor):
    raise OrganigramaValidationError(
        f'Constante numérica JSON no permitida: {valor!r}.'
    )


def _validar_id(valor, contexto, vistos):
    if not isinstance(valor, str) or not ID_RE.match(valor):
        raise OrganigramaValidationError(
            f'Identificador inválido en {contexto}: {valor!r}.'
        )
    if valor in IDS_PROHIBIDOS:
        raise OrganigramaValidationError(
            f'Identificador reservado no permitido en {contexto}: {valor!r}.'
        )
    if valor in vistos:
        raise OrganigramaValidationError(
            f'Identificador duplicado en {contexto}: {valor!r}.'
        )
    vistos.add(valor)
    return valor


def _validar_numero(valor, campo, minimo, maximo, entero=False):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise OrganigramaValidationError(f'El campo {campo} debe ser numérico.')
    num = float(valor)
    if not math.isfinite(num):
        raise OrganigramaValidationError(f'El campo {campo} debe ser finito.')
    if num < minimo or num > maximo:
        raise OrganigramaValidationError(
            f'El campo {campo} debe estar entre {minimo} y {maximo}.'
        )
    return int(round(num)) if entero else round(num, 2)


def _validar_color(valor, campo, default='auto', permitir_transparent=True):
    if valor is None or valor == '':
        return default
    if not isinstance(valor, str):
        raise OrganigramaValidationError(f'El campo {campo} debe ser un texto de color.')
    limpio = valor.strip()
    lower = limpio.lower()
    if lower == 'auto':
        return 'auto'
    if lower == 'transparent':
        if not permitir_transparent:
            raise OrganigramaValidationError(f'El campo {campo} no admite transparente.')
        return 'transparent'
    if not COLOR_HEX_RE.match(limpio):
        raise OrganigramaValidationError(
            f'Color inválido en {campo}: {valor!r}. Usa formato #RRGGBB o "auto".'
        )
    return limpio.upper()


def _validar_texto(valor, campo, max_len, default=''):
    if valor is None:
        return default
    if not isinstance(valor, str):
        raise OrganigramaValidationError(f'El campo {campo} debe ser una cadena.')
    if len(valor) > max_len:
        raise OrganigramaValidationError(
            f'El campo {campo} excede el máximo de {max_len} caracteres.'
        )
    return valor


def _validar_enum(valor, campo, permitidos, default):
    if valor is None or valor == '':
        return default
    if not isinstance(valor, str) or valor not in permitidos:
        raise OrganigramaValidationError(
            f'Valor inválido en {campo}: {valor!r}.'
        )
    return valor


def parsear_json_estricto(raw_bytes):
    """Decodifica JSON verificando límite de bytes ANTES de parsear y rechazando NaN/Inf."""
    if isinstance(raw_bytes, str):
        raw_bytes = raw_bytes.encode('utf-8')
    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise OrganigramaValidationError('El cuerpo de la petición debe ser binario o JSON UTF-8.')
    if len(raw_bytes) > MAX_PAYLOAD_BYTES:
        raise OrganigramaValidationError(
            f'El payload excede el límite máximo de {MAX_PAYLOAD_BYTES} bytes.'
        )
    try:
        texto = bytes(raw_bytes).decode('utf-8')
        return json.loads(texto, parse_constant=_rechazar_constante_no_finita)
    except OrganigramaValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrganigramaValidationError(f'JSON inválido: {exc}') from exc


def validar_datos_organigrama(datos):
    """Valida y normaliza un diccionario de esquema de organigrama (v1)."""
    if not isinstance(datos, dict):
        raise OrganigramaValidationError('Los datos del organigrama deben ser un objeto JSON.')

    schema_version = datos.get('schema_version', 1)
    if isinstance(schema_version, bool) or schema_version != 1:
        raise OrganigramaValidationError('Versión de esquema no soportada (se espera schema_version=1).')

    raw_settings = datos.get('settings')
    if raw_settings is None:
        raw_settings = {}
    if not isinstance(raw_settings, dict):
        raise OrganigramaValidationError('settings debe ser un objeto JSON.')
    grid_size = _validar_numero(
        raw_settings.get('gridSize', 20), 'settings.gridSize', 5, 100, entero=True,
    )
    snap_to_grid = raw_settings.get('snapToGrid', True)
    if not isinstance(snap_to_grid, bool):
        raise OrganigramaValidationError('settings.snapToGrid debe ser booleano.')

    raw_sections = datos.get('sections')
    if raw_sections is None:
        raw_sections = []
    if not isinstance(raw_sections, list):
        raise OrganigramaValidationError('sections debe ser una lista.')
    if len(raw_sections) > MAX_SECTIONS:
        raise OrganigramaValidationError(f'Máximo permitido de secciones: {MAX_SECTIONS}.')

    sections = []
    section_ids = set()
    for idx, sec in enumerate(raw_sections):
        if not isinstance(sec, dict):
            raise OrganigramaValidationError(f'sections[{idx}] debe ser un objeto.')
        sec_id = _validar_id(sec.get('id'), f'sections[{idx}].id', section_ids)
        title = _validar_texto(sec.get('title', ''), f'sections[{idx}].title', 200).strip()

        raw_emp = sec.get('empresa', '')
        if raw_emp is None:
            raw_emp = ''
        if not isinstance(raw_emp, str):
            raise OrganigramaValidationError(f'sections[{idx}].empresa debe ser texto.')
        emp_norm = raw_emp.strip().upper()
        if emp_norm == 'PT':
            emp_norm = 'PTCA'
        if emp_norm not in EMPRESAS_VALIDAS:
            raise OrganigramaValidationError(
                f'Empresa inválida en sections[{idx}]: {raw_emp!r}.'
            )

        depto_id = sec.get('departamento_id')
        if depto_id == '' or depto_id is None:
            depto_id = None
        else:
            if isinstance(depto_id, bool) or not isinstance(depto_id, int) or depto_id <= 0:
                raise OrganigramaValidationError(
                    f'sections[{idx}].departamento_id debe ser un entero positivo o null.'
                )

        sections.append({
            'id': sec_id,
            'title': title or f'Sección {idx + 1}',
            'empresa': emp_norm,
            'departamento_id': depto_id,
            'x': _validar_numero(sec.get('x', 0), f'sections[{idx}].x', -50000, 50000),
            'y': _validar_numero(sec.get('y', 0), f'sections[{idx}].y', -50000, 50000),
            'w': _validar_numero(sec.get('w', 600), f'sections[{idx}].w', 80, 20000),
            'h': _validar_numero(sec.get('h', 400), f'sections[{idx}].h', 60, 20000),
            'color': _validar_color(
                sec.get('color', '#2563EB'), f'sections[{idx}].color',
                default='#2563EB', permitir_transparent=False,
            ),
            'order': _validar_numero(sec.get('order', idx), f'sections[{idx}].order', 0, 100000, entero=True),
        })

    raw_nodes = datos.get('nodes')
    if raw_nodes is None:
        raw_nodes = []
    if not isinstance(raw_nodes, list):
        raise OrganigramaValidationError('nodes debe ser una lista.')
    if len(raw_nodes) > MAX_NODES:
        raise OrganigramaValidationError(f'Máximo permitido de figuras: {MAX_NODES}.')

    nodes = []
    node_ids = set()
    for idx, nodo in enumerate(raw_nodes):
        if not isinstance(nodo, dict):
            raise OrganigramaValidationError(f'nodes[{idx}] debe ser un objeto.')
        node_id = _validar_id(nodo.get('id'), f'nodes[{idx}].id', node_ids)
        sec_ref = nodo.get('section_id')
        if sec_ref == '' or sec_ref is None:
            sec_ref = None
        elif not isinstance(sec_ref, str) or sec_ref not in section_ids:
            raise OrganigramaValidationError(
                f'nodes[{idx}].section_id apunta a una sección inexistente: {sec_ref!r}.'
            )

        raw_style = nodo.get('style')
        if raw_style is None:
            raw_style = {}
        if not isinstance(raw_style, dict):
            raise OrganigramaValidationError(f'nodes[{idx}].style debe ser un objeto.')

        style = {
            'bgColor': _validar_color(
                raw_style.get('bgColor', 'auto'), f'nodes[{idx}].style.bgColor', default='auto',
            ),
            'textColor': _validar_color(
                raw_style.get('textColor', 'auto'), f'nodes[{idx}].style.textColor',
                default='auto', permitir_transparent=False,
            ),
            'borderColor': _validar_color(
                raw_style.get('borderColor', 'auto'), f'nodes[{idx}].style.borderColor', default='auto',
            ),
            'borderWidth': _validar_numero(
                raw_style.get('borderWidth', 2), f'nodes[{idx}].style.borderWidth', 0, 12,
            ),
            'borderStyle': _validar_enum(
                raw_style.get('borderStyle', 'solid'), f'nodes[{idx}].style.borderStyle',
                BORDER_STYLES_VALIDOS, 'solid',
            ),
            'borderRadius': _validar_numero(
                raw_style.get('borderRadius', 10), f'nodes[{idx}].style.borderRadius', 0, 60, entero=True,
            ),
            'fontWeight': _validar_enum(
                raw_style.get('fontWeight', 'normal'), f'nodes[{idx}].style.fontWeight',
                FONT_WEIGHTS_VALIDOS, 'normal',
            ),
            'fontStyle': _validar_enum(
                raw_style.get('fontStyle', 'normal'), f'nodes[{idx}].style.fontStyle',
                FONT_STYLES_VALIDOS, 'normal',
            ),
            'fontSize': _validar_numero(
                raw_style.get('fontSize', 13), f'nodes[{idx}].style.fontSize', 9, 48, entero=True,
            ),
            'textAlign': _validar_enum(
                raw_style.get('textAlign', 'center'), f'nodes[{idx}].style.textAlign',
                TEXT_ALIGNS_VALIDOS, 'center',
            ),
        }

        nodes.append({
            'id': node_id,
            'section_id': sec_ref,
            'shape': _validar_enum(nodo.get('shape', 'rounded'), f'nodes[{idx}].shape', SHAPES_VALIDAS, 'rounded'),
            'x': _validar_numero(nodo.get('x', 0), f'nodes[{idx}].x', -50000, 50000),
            'y': _validar_numero(nodo.get('y', 0), f'nodes[{idx}].y', -50000, 50000),
            'w': _validar_numero(nodo.get('w', 180), f'nodes[{idx}].w', 40, 2000),
            'h': _validar_numero(nodo.get('h', 72), f'nodes[{idx}].h', 28, 2000),
            'text': _validar_texto(nodo.get('text', ''), f'nodes[{idx}].text', 2000),
            'zIndex': _validar_numero(nodo.get('zIndex', 1), f'nodes[{idx}].zIndex', -1000, 10000, entero=True),
            'style': style,
        })

    raw_edges = datos.get('edges')
    if raw_edges is None:
        raw_edges = []
    if not isinstance(raw_edges, list):
        raise OrganigramaValidationError('edges debe ser una lista.')
    if len(raw_edges) > MAX_EDGES:
        raise OrganigramaValidationError(f'Máximo permitido de conexiones: {MAX_EDGES}.')

    edges = []
    edge_ids = set()
    for idx, edge in enumerate(raw_edges):
        if not isinstance(edge, dict):
            raise OrganigramaValidationError(f'edges[{idx}] debe ser un objeto.')
        edge_id = _validar_id(edge.get('id'), f'edges[{idx}].id', edge_ids)
        src = edge.get('from')
        dst = edge.get('to')
        if not isinstance(src, str) or src not in node_ids:
            raise OrganigramaValidationError(
                f'edges[{idx}].from referencia una figura inexistente: {src!r}.'
            )
        if not isinstance(dst, str) or dst not in node_ids:
            raise OrganigramaValidationError(
                f'edges[{idx}].to referencia una figura inexistente: {dst!r}.'
            )
        if src == dst:
            raise OrganigramaValidationError(
                f'edges[{idx}] no puede conectar una figura consigo misma ({src!r}).'
            )
        arrow_end = edge.get('arrowEnd', True)
        if not isinstance(arrow_end, bool):
            raise OrganigramaValidationError(f'edges[{idx}].arrowEnd debe ser booleano.')

        edges.append({
            'id': edge_id,
            'from': src,
            'to': dst,
            'fromAnchor': _validar_enum(
                edge.get('fromAnchor', 'bottom'), f'edges[{idx}].fromAnchor', ANCHORS_VALIDOS, 'bottom',
            ),
            'toAnchor': _validar_enum(
                edge.get('toAnchor', 'top'), f'edges[{idx}].toAnchor', ANCHORS_VALIDOS, 'top',
            ),
            'routing': _validar_enum(
                edge.get('routing', 'orthogonal'), f'edges[{idx}].routing', ROUTINGS_VALIDOS, 'orthogonal',
            ),
            'color': _validar_color(
                edge.get('color', 'auto'), f'edges[{idx}].color',
                default='auto', permitir_transparent=False,
            ),
            'width': _validar_numero(edge.get('width', 2), f'edges[{idx}].width', 1, 12),
            'style': _validar_enum(
                edge.get('style', 'solid'), f'edges[{idx}].style', BORDER_STYLES_VALIDOS, 'solid',
            ),
            'arrowEnd': arrow_end,
            'label': _validar_texto(edge.get('label', ''), f'edges[{idx}].label', 120),
        })

    return {
        'schema_version': 1,
        'settings': {'gridSize': grid_size, 'snapToGrid': snap_to_grid},
        'sections': sections,
        'nodes': nodes,
        'edges': edges,
    }


def parsear_y_validar_payload(raw_bytes):
    """Parsea JSON estricto y valida el esquema completo de organigrama."""
    obj = parsear_json_estricto(raw_bytes)
    return validar_datos_organigrama(obj)


def hash_organigrama(datos):
    """SHA-256 canónico del diccionario del organigrama."""
    serializado = json.dumps(
        datos or {}, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
    )
    return hashlib.sha256(serializado.encode('utf-8')).hexdigest()


def puede_construir_organigrama(user, documento=None, tipo_id=None):
    """True si `user` es admin o tiene concesión activa de edición y el doc es editable."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    apartado = documento.tipo_id if documento is not None else (tipo_id or TIPO_ORGANIGRAMA)
    if apartado not in TIPOS_DIAGRAMA_INTERACTIVO:
        return False
    if documento is not None and documento.estado in ('en_revision', 'obsoleto'):
        return False
    from . import acceso_formato

    return acceso_formato.puede_editar_tipo(user, apartado)


def puede_construir_mapa_procesos(user, documento=None):
    """True si `user` es admin o tiene permiso de edición para Mapa de Procesos."""
    return puede_construir_organigrama(
        user, documento=documento, tipo_id=TIPO_MAPA_PROCESOS,
    )


def puede_ver_organigrama(user, documento, requerir_aprobada=False):
    """Editores asignados/admins ven siempre; el resto según visibilidad del documento."""
    if not user or not getattr(user, 'is_authenticated', False) or documento is None:
        return False
    from . import acceso_formato

    if acceso_formato.puede_editar_tipo(user, documento.tipo_id):
        return True
    from ..permissions import puede_ver_documento
    return puede_ver_documento(user, documento, requerir_aprobada=requerir_aprobada)


def puede_exportar_organigrama(user, documento):
    """True si `user` puede exportar/descargar el organigrama según clasificación ISO."""
    if not puede_ver_organigrama(user, documento, requerir_aprobada=True):
        return False
    from ..permissions import puede_descargar

    if puede_descargar(user, documento):
        return True
    if (
        puede_construir_organigrama(user, documento)
        and documento.clasificacion in ('publico', 'interno')
    ):
        return True
    return False


def es_tipo_diagrama_interactivo(tipo_o_doc):
    """True si la clave o instancia corresponde a un diagrama de lienzo JSON (ORG o MAP)."""
    if isinstance(tipo_o_doc, str):
        return tipo_o_doc in TIPOS_DIAGRAMA_INTERACTIVO
    tipo_id = getattr(tipo_o_doc, 'tipo_id', None)
    return tipo_id in TIPOS_DIAGRAMA_INTERACTIVO


def obtener_organigrama_vigente():
    """Devuelve el único documento Organigrama corporativo no obsoleto (o ``None``)."""
    from ..models import Documento
    return (
        Documento.objects
        .filter(tipo_id=TIPO_ORGANIGRAMA, es_plantilla=False)
        .exclude(estado='obsoleto')
        .select_related('departamento', 'propietario', 'tipo')
        .order_by('-fecha_modificacion')
        .first()
    )


def _normalizar_empresa_mapa(empresa):
    from ..models import Documento
    emp = (empresa or '').strip().upper()
    if emp == 'PT':
        emp = 'PTCA'
    if emp not in Documento.EMPRESAS_VALIDAS:
        raise OrganigramaValidationError(
            f'Empresa inválida para Mapa de Procesos: {empresa!r}.'
        )
    return emp


def obtener_mapa_procesos_vigente(empresa):
    """Devuelve el único Mapa de Procesos no obsoleto de ``empresa`` (o ``None``)."""
    from ..models import Documento
    emp = _normalizar_empresa_mapa(empresa)
    return (
        Documento.objects
        .filter(tipo_id=TIPO_MAPA_PROCESOS, empresa=emp, es_plantilla=False)
        .exclude(estado='obsoleto')
        .select_related('departamento', 'propietario', 'tipo')
        .order_by('-fecha_modificacion')
        .first()
    )


def listar_mapas_procesos_vigentes():
    """Devuelve dict ordenado ``{empresa_cod: doc_o_none}`` en una sola query."""
    from ..models import Documento
    docs_por_emp = {}
    qs = (
        Documento.objects
        .filter(
            tipo_id=TIPO_MAPA_PROCESOS,
            es_plantilla=False,
            empresa__in=Documento.EMPRESAS_VALIDAS,
        )
        .exclude(estado='obsoleto')
        .select_related('departamento', 'propietario', 'tipo')
        .order_by('-fecha_modificacion')
    )
    for d in qs:
        docs_por_emp.setdefault(d.empresa, d)
    return {emp: docs_por_emp.get(emp) for emp, _ in Documento.EMPRESA_CHOICES}


def _asegurar_soporte_tipo(tipo_key, defaults):
    """Garantiza la existencia de `Departamento('SGC')` y el `TipoDocumento` indicado."""
    from ..models import Departamento, TipoDocumento

    depto_sgc, _ = Departamento.objects.get_or_create(
        codigo=DEPTO_CODIGO_ORG,
        defaults={'nombre': DEPTO_NOMBRE_ORG, 'activo': True},
    )
    tipo_obj, creado = TipoDocumento.objects.get_or_create(key=tipo_key, defaults=defaults)
    updates = []
    if not creado:
        for campo in ('solo_jefes', 'usa_prefijo_empresa', 'unico_por_empresa', 'sin_prefijo_depto'):
            if campo in defaults and getattr(tipo_obj, campo) != defaults[campo]:
                setattr(tipo_obj, campo, defaults[campo])
                updates.append(campo)
        if updates:
            tipo_obj.save(update_fields=updates)
    return tipo_obj, depto_sgc


def _asegurar_soporte_organigrama():
    """Garantiza la existencia de `TipoDocumento('organigrama')` y `Departamento('SGC')`."""
    return _asegurar_soporte_tipo(TIPO_ORGANIGRAMA, {
        'codigo': 'ORG',
        'nombre': 'Organigrama',
        'descripcion': 'Organigrama institucional interactivo de las empresas del grupo.',
        'activo': True,
        'icono': 'building',
        'color': 'success',
        'solo_jefes': True,
        'sin_prefijo_depto': False,
        'usa_prefijo_empresa': False,
        'unico_por_empresa': False,
    })


def _asegurar_soporte_mapa_procesos():
    """Garantiza la existencia de `TipoDocumento('mapa_procesos')` y `Departamento('SGC')`."""
    return _asegurar_soporte_tipo(TIPO_MAPA_PROCESOS, {
        'codigo': 'MAP',
        'nombre': 'Mapa de Procesos',
        'descripcion': 'Mapa de procesos interactivo por empresa del grupo.',
        'activo': True,
        'icono': 'diagram-project',
        'color': 'info',
        'solo_jefes': True,
        'sin_prefijo_depto': False,
        'usa_prefijo_empresa': True,
        'unico_por_empresa': True,
    })


def crear_mapa_procesos_inicial(
    usuario,
    empresa,
    nombre=None,
    descripcion=None,
    clasificacion='publico',
    datos=None,
    ip_address=None,
):
    """Crea o retorna el Mapa de Procesos vigente único para ``empresa`` (``{EMP}-MAP-001``)."""
    from ..models import Documento, VersionDocumento
    from auditoria.services import registrar_creacion_documento

    emp = _normalizar_empresa_mapa(empresa)
    existente = obtener_mapa_procesos_vigente(emp)
    if existente is not None:
        return existente

    nombre_emp = dict(Documento.EMPRESA_CHOICES).get(emp, emp)
    nombre_final = (nombre or f'Mapa de Procesos — {nombre_emp}').strip()
    desc_final = (descripcion or f'Mapa de procesos SGC de {nombre_emp}.').strip()
    datos_limpios = validar_datos_organigrama(datos) if datos is not None else esquema_inicial()
    sha = hash_organigrama(datos_limpios)

    try:
        with transaction.atomic():
            existente = obtener_mapa_procesos_vigente(emp)
            if existente is not None:
                return existente
            tipo_map, depto_sgc = _asegurar_soporte_mapa_procesos()

            codigo = f'{emp}-MAP-001'
            if Documento.objects.filter(codigo=codigo).exists():
                codigo = (
                    Documento.reservar_codigo_secuencial(
                        depto_sgc, TIPO_MAPA_PROCESOS, empresa=emp,
                    )
                    or codigo
                )

            doc = Documento(
                tipo=tipo_map,
                nombre=nombre_final,
                codigo=codigo,
                empresa=emp,
                descripcion=desc_final,
                estado='activo',
                clasificacion=clasificacion,
                version_actual='1',
                fecha_vigencia=timezone.now(),
                propietario=usuario,
                departamento=depto_sgc,
                revision_edicion=1,
                organigrama_sha256=sha,
            )
            doc._auditoria_explicita = True
            doc.save()

            VersionDocumento.objects.create(
                documento=doc,
                numero_version='1',
                cambios=f'Versión inicial del mapa de procesos ({emp})',
                creado_por=usuario,
                datos_organigrama=datos_limpios,
            )

            registrar_creacion_documento(
                usuario=usuario,
                recurso_tipo=TIPO_MAPA_PROCESOS,
                recurso_id=doc.id,
                codigo=doc.codigo,
                nombre=doc.nombre,
                estado_posterior=doc.estado,
                es_plantilla=False,
                ip_address=ip_address,
                asincrono=True,
            )
            return doc
    except IntegrityError:
        existente = obtener_mapa_procesos_vigente(emp)
        if existente is not None:
            return existente
        raise


def crear_organigrama_inicial(
    usuario,
    nombre='Organigrama Corporativo',
    descripcion='Organigrama institucional de las empresas del Grupo Policlínica.',
    clasificacion='publico',
    datos=None,
    ip_address=None,
):
    """Crea o retorna el documento corporativo único `SGC-ORG-001`."""
    existente = obtener_organigrama_vigente()
    if existente is not None:
        return existente

    from ..models import Documento, VersionDocumento
    from auditoria.services import registrar_creacion_documento

    datos_limpios = validar_datos_organigrama(datos) if datos is not None else esquema_inicial()
    sha = hash_organigrama(datos_limpios)

    try:
        with transaction.atomic():
            existente = obtener_organigrama_vigente()
            if existente is not None:
                return existente
            tipo_org, depto_sgc = _asegurar_soporte_organigrama()

            codigo = CODIGO_ORGANIGRAMA
            if Documento.objects.filter(codigo=codigo).exists():
                codigo = Documento.reservar_codigo_secuencial(depto_sgc, TIPO_ORGANIGRAMA) or CODIGO_ORGANIGRAMA

            doc = Documento(
                tipo=tipo_org,
                nombre=nombre,
                codigo=codigo,
                descripcion=descripcion,
                estado='activo',
                clasificacion=clasificacion,
                version_actual='1',
                fecha_vigencia=timezone.now(),
                propietario=usuario,
                departamento=depto_sgc,
                revision_edicion=1,
                organigrama_sha256=sha,
            )
            doc._auditoria_explicita = True
            doc.save()

            VersionDocumento.objects.create(
                documento=doc,
                numero_version='1',
                cambios='Versión inicial del organigrama interactivo',
                creado_por=usuario,
                datos_organigrama=datos_limpios,
            )

            registrar_creacion_documento(
                usuario=usuario,
                recurso_tipo=TIPO_ORGANIGRAMA,
                recurso_id=doc.id,
                codigo=doc.codigo,
                nombre=doc.nombre,
                estado_posterior=doc.estado,
                es_plantilla=False,
                ip_address=ip_address,
                asincrono=True,
            )
            return doc
    except IntegrityError:
        existente = obtener_organigrama_vigente()
        if existente is not None:
            return existente
        raise


def _normalizar_num_version(valor):
    try:
        return str(int(float(str(valor).strip())))
    except (TypeError, ValueError):
        return str(valor or '').strip()


def obtener_datos_version(documento, version_str=None):
    """Obtiene `(VersionDocumento, datos_dict)` sin cargar blobs JSON de otras versiones."""
    from ..models import VersionDocumento

    objetivo = _normalizar_num_version(version_str or documento.version_actual)
    candidatos = list(
        VersionDocumento.objects
        .filter(documento=documento, activa=True)
        .defer('datos_organigrama')
        .select_related('creado_por')
        .order_by('-id')
    )
    elegida = next(
        (v for v in candidatos if _normalizar_num_version(v.numero_version) == objetivo),
        None,
    )
    if elegida is None and not version_str:
        elegida = candidatos[0] if candidatos else None
    if elegida is None:
        return None, None

    datos_brutos = (
        VersionDocumento.objects
        .filter(pk=elegida.pk)
        .values_list('datos_organigrama', flat=True)
        .first()
    )
    return elegida, (datos_brutos if isinstance(datos_brutos, dict) else esquema_inicial())


def guardar_organigrama(
    documento,
    usuario,
    datos_validados,
    revision_esperada,
    incrementar_version=False,
    cambios='',
    nombre=None,
    descripcion=None,
    clasificacion=None,
    ip_address=None,
):
    """Guarda el organigrama con bloqueo optimista, versionado opcional y auditoría ISO."""
    from ..models import Documento, VersionDocumento
    from auditoria.services import registrar_edicion_documento

    if documento.tipo_id not in TIPOS_DIAGRAMA_INTERACTIVO:
        raise OrganigramaValidationError('El documento indicado no corresponde a un diagrama interactivo.')
    if documento.estado in ('en_revision', 'obsoleto'):
        raise OrganigramaValidationError(
            'No se puede modificar el diagrama mientras está en revisión u obsoleto.'
        )
    if isinstance(revision_esperada, bool) or not isinstance(revision_esperada, int):
        raise OrganigramaValidationError('revision_edicion debe ser un entero.')

    cambios_limpios = _validar_texto(cambios or '', 'cambios', 1000).strip()

    with transaction.atomic():
        filas = (
            Documento.objects
            .filter(pk=documento.pk, revision_edicion=revision_esperada)
            .update(revision_edicion=F('revision_edicion') + 1)
        )
        if filas == 0:
            actual = (
                Documento.objects
                .filter(pk=documento.pk)
                .values('revision_edicion', 'version_actual')
                .first()
            )
            raise ConflictoEdicionError(
                'El organigrama fue guardado por otro usuario mientras editabas. '
                'Recarga los datos actuales antes de guardar.',
                revision_actual=actual['revision_edicion'] if actual else None,
                version_actual=actual['version_actual'] if actual else None,
            )

        documento.refresh_from_db()
        version_anterior = documento.version_actual
        nuevo_sha = hash_organigrama(datos_validados)

        if incrementar_version:
            nueva_version = documento.proxima_version()
            version_obj = VersionDocumento.objects.create(
                documento=documento,
                numero_version=nueva_version,
                cambios=cambios_limpios or f'Actualización a versión {nueva_version}',
                creado_por=usuario,
                datos_organigrama=datos_validados,
            )
            documento.version_actual = nueva_version
            documento.fecha_vigencia = timezone.now()
        else:
            versiones_activas = list(
                VersionDocumento.objects
                .filter(documento=documento, activa=True)
                .defer('datos_organigrama')
                .order_by('-id')
            )
            objetivo = _normalizar_num_version(documento.version_actual)
            version_obj = next(
                (v for v in versiones_activas if _normalizar_num_version(v.numero_version) == objetivo),
                None,
            )
            if version_obj is None:
                version_obj = VersionDocumento.objects.create(
                    documento=documento,
                    numero_version=_normalizar_num_version(documento.version_actual) or '1',
                    cambios=cambios_limpios or 'Actualización de organigrama',
                    creado_por=usuario,
                    datos_organigrama=datos_validados,
                )
            else:
                version_obj.datos_organigrama = datos_validados
                campos_ver = ['datos_organigrama']
                if cambios_limpios:
                    version_obj.cambios = cambios_limpios
                    campos_ver.append('cambios')
                version_obj.save(update_fields=campos_ver)

        if isinstance(nombre, str) and nombre.strip():
            nombre_limpio = nombre.strip()
            if len(nombre_limpio) > 200:
                raise OrganigramaValidationError('El nombre no puede superar 200 caracteres.')
            documento.nombre = nombre_limpio

        if isinstance(descripcion, str):
            if len(descripcion) > 5000:
                raise OrganigramaValidationError('La descripción no puede superar 5000 caracteres.')
            documento.descripcion = descripcion.strip()

        clasif_validas = {c for c, _ in Documento.CLASIFICACION_CHOICES}
        if isinstance(clasificacion, str) and clasificacion.strip():
            if clasificacion.strip() not in clasif_validas:
                raise OrganigramaValidationError('Clasificación no válida.')
            documento.clasificacion = clasificacion.strip()

        documento.organigrama_sha256 = nuevo_sha
        documento._auditoria_explicita = True
        documento.save()

        registrar_edicion_documento(
            usuario=usuario,
            recurso_tipo=documento.tipo_id,
            recurso_id=documento.id,
            codigo=documento.codigo,
            nombre=documento.nombre,
            estado_anterior=documento.estado,
            estado_posterior=documento.estado,
            version_anterior=version_anterior,
            version_nueva=documento.version_actual,
            cambios={
                'secciones': len(datos_validados.get('sections', [])),
                'nodos': len(datos_validados.get('nodes', [])),
                'conexiones': len(datos_validados.get('edges', [])),
                'nueva_version': bool(incrementar_version),
                'descripcion_cambios': cambios_limpios,
            },
            ip_address=ip_address,
            asincrono=True,
        )

        return documento, version_obj
