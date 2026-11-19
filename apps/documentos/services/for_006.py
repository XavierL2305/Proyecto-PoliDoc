"""Lista Maestra de Documentos (SGC-FOR-006).

La lista es un **registro vivo** (``RegistroFormato``) que guarda sus filas en
JSON (``respuestas['documentos']``). No se regenera por cada cambio de la app:
las filas se insertan/actualizan con señales y con la sincronización explícita
("combinar"), y el XLSX/PDF se genera solo cuando el usuario lo pide.

Fuentes de fila:

* ``documento``  — ``Documento`` con código (origen **Int**, columna B).
* ``sin_codigo`` — ``DocumentoSinCodigo`` de ámbito ``sgc`` (origen **Ext**,
  columna A, "No. identificación" = ``N/A``).
* ``manual``     — filas agregadas a mano (no las toca la sincronización).

Cada fila lleva ``_clave`` (identidad), ``_origen_tipo`` y ``_manual`` (lista
de columnas editadas por una persona: la sincronización/el signal NO las
sobrescriben).
"""
import copy
import io
import logging
import math
import uuid

from django.db import transaction
from django.utils import timezone
from .xlsx_comun import preparar_para_pdf as _preparar_para_pdf
from .xlsx_comun import xlsx_bytes_a_pdf as _xlsx_a_pdf

logger = logging.getLogger(__name__)

#: Código del formato y hoja oficial.
FORMATO_FOR_006 = 'SGC-FOR-006'
HOJA_FOR_006 = 'SGC-FOR-006'

#: Contexto del registro vivo único.
CONTEXTO_TIPO = 'sistema'
CONTEXTO_ID = 'lista-maestra'

#: Celda del contador de páginas y rango de cabecera a repetir al imprimir.
CELDA_PAGINA = 'L6'
FILAS_CABECERA = '1:12'

#: Tipos de fila.
ORIGEN_DOCUMENTO = 'documento'
ORIGEN_SIN_CODIGO = 'sin_codigo'
ORIGEN_MANUAL = 'manual'

_EMPRESAS_POR_CODIGO = {
    'PTH': 'Policlínica Táchira Hospitalización',
    'FP': 'Farmacia',
    'FS': 'Fuente de Soda',
    'PT': 'PTCA',
}


# ---------------------------------------------------------------------------
# Formato y registro vivo
# ---------------------------------------------------------------------------

def formato_for_006():
    """``Formato`` activo del SGC-FOR-006 (o ``None``)."""
    from ..models import Formato

    return Formato.objects.filter(codigo=FORMATO_FOR_006, activo=True).first()


def campo_tabla(formato=None):
    """``CampoFormato`` de la tabla de la lista (o ``None``)."""
    from ..models import CampoFormato

    formato = formato or formato_for_006()
    if formato is None:
        return None
    campo = CampoFormato.objects.filter(
        seccion__formato=formato, clave='documentos',
    ).first()
    if campo is None:
        campo = CampoFormato.objects.filter(
            seccion__formato=formato, tipo='tabla',
        ).first()
    return campo


def columnas(formato=None):
    """Definiciones de columna (``config_tabla.columnas``)."""
    campo = campo_tabla(formato)
    if campo is None:
        return []
    return list((campo.config_tabla or {}).get('columnas') or [])


def _config(formato=None):
    campo = campo_tabla(formato)
    return dict(campo.config_tabla or {}) if campo else {}


def _responsable_por_defecto():
    """Primer administrador activo (para crear el registro vivo)."""
    from autenticacion.models import PerfilUsuario

    perfil = PerfilUsuario.objects.filter(
        rol='administrador', usuario__is_active=True,
    ).select_related('usuario').first()
    return perfil.usuario if perfil else None


def registro_lista_maestra(crear=False, responsable=None):
    """``RegistroFormato`` vivo de la lista (o ``None`` si no hay formato)."""
    from ..models import RegistroFormato

    formato = formato_for_006()
    if formato is None:
        return None
    registro = RegistroFormato.objects.filter(
        formato=formato, contexto_tipo=CONTEXTO_TIPO, contexto_id=CONTEXTO_ID,
    ).first()
    if registro is None and crear:
        responsable = responsable or _responsable_por_defecto()
        if responsable is None:
            logger.warning(
                'No hay administrador para crear el registro de la Lista Maestra.',
            )
            return None
        registro = RegistroFormato.objects.create(
            formato=formato,
            formato_codigo=formato.codigo,
            formato_version=formato.version,
            formato_edicion=formato.edicion,
            contexto_tipo=CONTEXTO_TIPO,
            contexto_id=CONTEXTO_ID,
            titulo='Lista Maestra de Documentos',
            responsable=responsable,
            respuestas={'documentos': []},
        )
    return registro


def filas(registro):
    """Filas del registro (lista mutable copiada)."""
    if registro is None:
        return []
    return list((registro.respuestas or {}).get('documentos') or [])


# ---------------------------------------------------------------------------
# Claves e identidad de fila
# ---------------------------------------------------------------------------

def clave_documento(documento):
    return documento.codigo or f'doc:{documento.pk}'


def clave_sin_codigo(documento):
    return f'sin:{documento.uuid}'


def _origen_de(obj):
    modelo = getattr(getattr(obj, '_meta', None), 'model_name', '')
    if modelo == 'documento':
        return ORIGEN_DOCUMENTO
    if modelo == 'documentosincodigo':
        return ORIGEN_SIN_CODIGO
    return ORIGEN_MANUAL


# ---------------------------------------------------------------------------
# Mapeo de columnas automáticas (catálogo ``origen``)
# ---------------------------------------------------------------------------

def _uso(clasificacion):
    if (clasificacion or '').lower() in ('confidencial', 'restringido'):
        return 'Interno/Confidencial'
    return 'Interno/No Confidencial'


def _periodo_texto(periodo):
    """``Periodo`` → '1 año' / '5 años' / '3 meses' (o ``''``)."""
    if periodo is None:
        return ''
    meses = int(getattr(periodo, 'meses', 0) or 0)
    if not meses:
        return ''
    if meses % 12 == 0:
        anios = meses // 12
        return f'{anios} año' if anios == 1 else f'{anios} años'
    return f'{meses} mes' if meses == 1 else f'{meses} meses'


def _cargo(usuario):
    from ..models import _cargo_usuario

    return _cargo_usuario(usuario) if usuario is not None else ''


def empresa_documento(documento):
    """Empresa a partir del departamento o del prefijo del código."""
    codigos = []
    if documento.departamento_id:
        codigos.append(documento.departamento.codigo)
    if documento.codigo:
        codigos.append(documento.codigo.split('-')[0])
    for codigo in codigos:
        if codigo in _EMPRESAS_POR_CODIGO:
            return _EMPRESAS_POR_CODIGO[codigo]
    return 'Empresas del Grupo'


def _fecha_iso(valor):
    return valor.date().isoformat() if hasattr(valor, 'date') else (
        valor.isoformat() if hasattr(valor, 'isoformat') else ''
    )


def _valor_documento(origen, documento):
    if origen == 'codigo':
        return documento.codigo or ''
    if origen == 'nombre':
        return documento.nombre or ''
    if origen == 'uso':
        return _uso(documento.clasificacion)
    if origen == 'fecha_vigencia':
        return _fecha_iso(documento.fecha_vigencia)
    if origen == 'edicion':
        return documento.revision or ''
    if origen == 'version':
        return documento.version_actual or ''
    if origen == 'permanencia_activo':
        return _periodo_texto(documento.periodo_vigencia) or (
            'Hasta que cambie de versión o edición'
        )
    if origen == 'permanencia_inactivo':
        return _periodo_texto(documento.periodo_retencion) or 'N/A'
    if origen == 'responsable':
        return _cargo(documento.propietario)
    if origen == 'empresa':
        return empresa_documento(documento)
    if origen == 'ext_int':
        return 'si'  # información controlada → Int (B)
    return ''


def _valor_sin_codigo(origen, documento):
    if origen == 'codigo':
        return 'N/A'
    if origen == 'nombre':
        return documento.titulo or ''
    if origen == 'uso':
        return _uso(documento.clasificacion)
    if origen == 'fecha_vigencia':
        return _fecha_iso(documento.fecha)
    if origen in ('edicion', 'version'):
        return ''
    if origen == 'permanencia_activo':
        return ''
    if origen == 'permanencia_inactivo':
        return _periodo_texto(documento.periodo_retencion) or 'N/A'
    if origen == 'responsable':
        return documento.responsable_texto or _cargo(documento.propietario)
    if origen == 'empresa':
        return documento.empresa or 'Empresas del Grupo'
    if origen == 'ext_int':
        return 'no'  # soporte de fuente → Ext (A)
    return ''


def valores_auto(obj, definiciones=None):
    """Valores de las columnas con ``origen`` para ``obj``."""
    definiciones = definiciones or columnas()
    modelo = getattr(getattr(obj, '_meta', None), 'model_name', '')
    valores = {}
    for col in definiciones:
        origen = col.get('origen')
        if not origen:
            continue
        if modelo == 'documento':
            valores[col['clave']] = _valor_documento(origen, obj)
        elif modelo == 'documentosincodigo':
            valores[col['clave']] = _valor_sin_codigo(origen, obj)
        else:
            valores[col['clave']] = ''
    return valores


def fila_desde(obj):
    """Fila inicial (dict) para un ``Documento`` o ``DocumentoSinCodigo``."""
    origen_tipo = _origen_de(obj)
    if origen_tipo == ORIGEN_DOCUMENTO:
        clave = clave_documento(obj)
    else:
        clave = clave_sin_codigo(obj)
    fila = {
        '_clave': clave,
        '_origen_tipo': origen_tipo,
        '_manual': [],
        '_sin_codigo': origen_tipo == ORIGEN_SIN_CODIGO,
    }
    fila.update(valores_auto(obj))
    return fila


# ---------------------------------------------------------------------------
# Persistencia de filas (signal / sync)
# ---------------------------------------------------------------------------

def _guardar_filas(registro, nuevas):
    registro.respuestas = {**(registro.respuestas or {}),
                           'documentos': nuevas}
    registro.save(update_fields=['respuestas', 'actualizado'])


def _actualizar_fila(fila, valores):
    """Actualiza columnas automáticas respetando ``_manual``."""
    manual = set(fila.get('_manual') or [])
    cambios = False
    for clave, valor in valores.items():
        if clave in manual:
            continue
        if fila.get(clave) != valor:
            fila[clave] = valor
            cambios = True
    return cambios


def upsert_fila(registro, clave, origen_tipo, valores):
    """Inserta o actualiza una fila (idempotente, respeta ``_manual``)."""
    from ..models import RegistroFormato

    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(pk=registro.pk)
        actuales = filas(bloqueado)
        indice = next(
            (i for i, f in enumerate(actuales) if f.get('_clave') == clave),
            None,
        )
        if indice is None:
            nueva = {
                '_clave': clave,
                '_origen_tipo': origen_tipo,
                '_manual': [],
                '_sin_codigo': origen_tipo == ORIGEN_SIN_CODIGO,
            }
            nueva.update(valores)
            actuales.append(nueva)
        else:
            fila = actuales[indice]
            fila['_origen_tipo'] = origen_tipo
            fila['_sin_codigo'] = origen_tipo == ORIGEN_SIN_CODIGO
            cambiado = _actualizar_fila(fila, valores)
            if not cambiado:
                return bloqueado
            actuales[indice] = fila
        _guardar_filas(bloqueado, actuales)
        registro.respuestas = bloqueado.respuestas
        return bloqueado


def quitar_fila(registro, clave):
    """Elimina la fila con ``clave`` (idempotente)."""
    from ..models import RegistroFormato

    if registro is None:
        return None
    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(pk=registro.pk)
        actuales = filas(bloqueado)
        nuevas = [f for f in actuales if f.get('_clave') != clave]
        if len(nuevas) == len(actuales):
            return bloqueado
        _guardar_filas(bloqueado, nuevas)
        registro.respuestas = bloqueado.respuestas
        return bloqueado


def upsert_documento(documento):
    """Signal/sincronización: inserta o actualiza la fila de un ``Documento``."""
    if formato_for_006() is None:
        return None
    registro = registro_lista_maestra(
        crear=True, responsable=documento.propietario,
    )
    if registro is None:
        return None
    return upsert_fila(
        registro, clave_documento(documento), ORIGEN_DOCUMENTO,
        valores_auto(documento),
    )


def quitar_documento(documento):
    """Quita la fila de un ``Documento`` (obsoleto/plantilla/eliminado)."""
    registro = registro_lista_maestra()
    if registro is None:
        return None
    return quitar_fila(registro, clave_documento(documento))


def upsert_sin_codigo(documento):
    """Signal/sincronización: fila de un soporte SGC (``ambito='sgc'``)."""
    if formato_for_006() is None:
        return None
    if not (documento.ambito == 'sgc' and documento.activo):
        return quitar_sin_codigo(documento)
    registro = registro_lista_maestra(
        crear=True, responsable=documento.propietario,
    )
    if registro is None:
        return None
    return upsert_fila(
        registro, clave_sin_codigo(documento), ORIGEN_SIN_CODIGO,
        valores_auto(documento),
    )


def quitar_sin_codigo(documento):
    registro = registro_lista_maestra()
    if registro is None:
        return None
    return quitar_fila(registro, clave_sin_codigo(documento))


# ---------------------------------------------------------------------------
# Sincronización explícita ("combinar" conserva ediciones manuales)
# ---------------------------------------------------------------------------

def _documentos_fuente():
    from ..models import Documento

    return Documento.objects.filter(
        estado='activo', es_plantilla=False,
    ).select_related(
        'tipo', 'departamento', 'propietario',
        'periodo_vigencia', 'periodo_retencion',
    ).order_by('tipo__codigo', 'codigo')


def _sin_codigo_fuente():
    from ..models import DocumentoSinCodigo

    return DocumentoSinCodigo.objects.filter(
        ambito='sgc', activo=True,
    ).select_related(
        'departamento', 'propietario', 'periodo_retencion',
    ).order_by('titulo')


def _fuentes(definiciones):
    """Lista ordenada de ``(clave, origen_tipo, valores)`` desde las fuentes."""
    fuentes = []
    for documento in _documentos_fuente():
        fuentes.append((
            clave_documento(documento), ORIGEN_DOCUMENTO,
            valores_auto(documento, definiciones),
        ))
    for documento in _sin_codigo_fuente():
        fuentes.append((
            clave_sin_codigo(documento), ORIGEN_SIN_CODIGO,
            valores_auto(documento, definiciones),
        ))
    return fuentes


def sincronizar(registro=None, modo='combinar', usuario=None):
    """Sincroniza el registro vivo con los documentos actuales.

    ``combinar`` (recomendado): actualiza columnas automáticas, conserva las
    ediciones manuales (``_manual``) y las filas manuales; agrega nuevas y
    quita las que dejaron de ser activas/no-plantilla.

    ``reemplazar``: reconstruye solo desde las fuentes (pierde manuales).

    Returns:
        dict con ``agregados``, ``actualizados``, ``eliminados``, ``total``.
        ``{'error': ...}`` si no hay formato o registro.
    """
    from ..models import RegistroFormato

    formato = formato_for_006()
    if formato is None:
        return {'error': 'El formato SGC-FOR-006 no está integrado.'}
    registro = registro or registro_lista_maestra(crear=True, responsable=usuario)
    if registro is None:
        return {'error': 'No se pudo crear el registro de la Lista Maestra.'}

    definiciones = columnas(formato)
    fuentes = _fuentes(definiciones)
    orden = [clave for clave, _origen, _valores in fuentes]
    deseadas = {clave: (origen, valores) for clave, origen, valores in fuentes}

    resumen = {'agregados': 0, 'actualizados': 0, 'eliminados': 0, 'total': 0}
    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(pk=registro.pk)
        actuales = filas(bloqueado)

        if modo == 'reemplazar':
            nuevas = []
            for clave in orden:
                origen, valores = deseadas[clave]
                fila = {
                    '_clave': clave, '_origen_tipo': origen,
                    '_manual': [], '_sin_codigo': origen == ORIGEN_SIN_CODIGO,
                }
                fila.update(valores)
                nuevas.append(fila)
                resumen['agregados'] += 1
        else:
            pendientes = dict(deseadas)
            nuevas = []
            for fila in actuales:
                clave = fila.get('_clave')
                if fila.get('_origen_tipo') == ORIGEN_MANUAL:
                    nuevas.append(fila)
                    continue
                if clave not in pendientes:
                    resumen['eliminados'] += 1
                    continue
                origen, valores = pendientes.pop(clave)
                fila['_origen_tipo'] = origen
                fila['_sin_codigo'] = origen == ORIGEN_SIN_CODIGO
                if _actualizar_fila(fila, valores):
                    resumen['actualizados'] += 1
                nuevas.append(fila)
            for clave in orden:
                if clave not in pendientes:
                    continue
                origen, valores = pendientes.pop(clave)
                fila = {
                    '_clave': clave, '_origen_tipo': origen,
                    '_manual': [], '_sin_codigo': origen == ORIGEN_SIN_CODIGO,
                }
                fila.update(valores)
                nuevas.append(fila)
                resumen['agregados'] += 1

        respuestas = dict(bloqueado.respuestas or {})
        respuestas['documentos'] = nuevas
        respuestas['_meta'] = {
            'sincronizado_en': timezone.now().isoformat(),
            'sincronizado_por': getattr(usuario, 'id', None),
            'modo': modo,
        }
        bloqueado.respuestas = respuestas
        bloqueado.save(update_fields=['respuestas', 'actualizado'])
        registro.respuestas = respuestas
        resumen['total'] = len(nuevas)
    logger.info('FOR-006 sincronizado (%s): %s', modo, resumen)
    return resumen


# ---------------------------------------------------------------------------
# Generación XLSX/PDF (solo cuando el usuario lo pide)
# ---------------------------------------------------------------------------

def _formatear_fecha(valor):
    if not valor:
        return ''
    texto = str(valor)
    try:
        from datetime import date as _date

        anio, mes, dia = (int(p) for p in texto[:10].split('-'))
        return _date(anio, mes, dia).strftime('%d/%m/%Y')
    except (ValueError, TypeError):
        return texto


def _escribir_columna(hoja, col, numero, fila):
    valor = fila.get(col.get('clave'), '')
    if valor is None:
        valor = ''
    if col.get('tipo') == 'si_no':
        if str(valor).strip() == '':
            return
        es_si = str(valor).strip().lower() in ('si', 'sí', '1', 'true', 'yes', 'x')
        ref = col.get('celda_si') if es_si else col.get('celda_no')
        if ref:
            hoja[f'{ref}{numero}'] = 'X'
        return
    ref = col.get('celda')
    if not ref:
        return
    if col.get('tipo') == 'fecha':
        valor = _formatear_fecha(valor)
    hoja[f'{ref}{numero}'] = str(valor)


def _copiar_estilo_fila(hoja, origen, destino, columnas_max=14):
    for columna in range(1, columnas_max + 1):
        src = hoja.cell(row=origen, column=columna)
        dst = hoja.cell(row=destino, column=columna)
        dst._style = copy.copy(src._style)
    alto = hoja.row_dimensions[origen].height
    if alto:
        hoja.row_dimensions[destino].height = alto


def generar_xlsx(registro=None):
    """Bytes del XLSX de la Lista Maestra (hoja continua).

    Raises:
        ValueError: si falta el formato/plantilla o el registro.
    """
    import openpyxl

    formato = registro.formato if registro is not None else formato_for_006()
    if formato is None or not formato.plantilla_xlsx:
        raise ValueError(
            'El formato SGC-FOR-006 no está integrado o no tiene plantilla.'
        )
    config = _config(formato)
    definiciones = list(config.get('columnas') or [])
    fila_inicio = int(config.get('fila_inicio', 13))
    max_filas = max(1, int(config.get('max_filas', 22)))
    datos = filas(registro)

    with formato.plantilla_xlsx.open('rb') as fh:
        plantilla = fh.read()
    wb = openpyxl.load_workbook(io.BytesIO(plantilla))
    hoja = wb[HOJA_FOR_006] if HOJA_FOR_006 in wb.sheetnames else wb.worksheets[0]

    ultima_plantilla = fila_inicio + max_filas - 1
    for indice, fila in enumerate(datos):
        numero = fila_inicio + indice
        if numero > ultima_plantilla:
            _copiar_estilo_fila(hoja, ultima_plantilla, numero)
        for col in definiciones:
            _escribir_columna(hoja, col, numero, fila)

    total_paginas = max(1, math.ceil(len(datos) / max_filas))
    hoja[CELDA_PAGINA] = f'PAGINA:   1 DE {total_paginas}'
    try:
        hoja.print_title_rows = FILAS_CABECERA
    except Exception:  # noqa: BLE001 - plantilla sin soporte de print titles
        pass
    try:
        hoja.oddFooter.center.text = 'Página &P de &N'
        hoja.oddFooter.center.size = 8
    except Exception:  # noqa: BLE001
        pass

    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


def generar_pdf(registro=None):
    """Bytes del PDF de la lista (o ``None`` si LibreOffice no está)."""
    return _xlsx_a_pdf(_preparar_para_pdf(generar_xlsx(registro)))


def nombre_archivo(registro, extension='xlsx'):
    fecha = timezone.localdate().strftime('%Y%m%d')
    return f'SGC-FOR-006_Lista_Maestra_{fecha}.{extension}'


def generar_y_guardar(registro, forzar=False):
    """Genera y guarda XLSX/PDF en el registro (idempotente).

    Nunca lanza: devuelve ``{'success', 'xlsx', 'pdf', 'error'}``.
    """
    from django.core.files.base import ContentFile

    if not forzar and registro.archivo:
        return {
            'success': True, 'xlsx': registro.archivo.name,
            'pdf': registro.archivo_pdf.name or None, 'error': '',
        }
    try:
        xlsx = generar_xlsx(registro)
    except Exception as exc:  # noqa: BLE001
        logger.error('No se pudo generar el XLSX FOR-006: %s', exc)
        return {'success': False, 'xlsx': None, 'pdf': None, 'error': str(exc)}

    if forzar:
        for campo in ('archivo', 'archivo_pdf'):
            archivo = getattr(registro, campo)
            if archivo:
                try:
                    archivo.delete(save=False)
                except Exception:  # noqa: BLE001
                    pass

    registro.archivo.save(
        nombre_archivo(registro, 'xlsx'), ContentFile(xlsx), save=False,
    )
    registro.archivo_pdf = ''
    try:
        pdf = _xlsx_a_pdf(_preparar_para_pdf(xlsx))
    except Exception as exc:  # noqa: BLE001
        logger.warning('No se pudo generar el PDF FOR-006: %s', exc)
        pdf = None
    if pdf:
        registro.archivo_pdf.save(
            nombre_archivo(registro, 'pdf'), ContentFile(pdf), save=False,
        )
    registro.save(update_fields=['archivo', 'archivo_pdf', 'actualizado'])
    return {
        'success': True, 'xlsx': registro.archivo.name,
        'pdf': registro.archivo_pdf.name or None, 'error': '',
    }


# ---------------------------------------------------------------------------
# Filas manuales (agregar y proteger)
# ---------------------------------------------------------------------------

def agregar_fila_manual(registro, valores, usuario=None):
    """Agrega una fila manual (no se toca en la sincronización)."""
    definiciones = columnas(getattr(registro, 'formato', None))
    fila = {
        '_clave': f'man:{uuid.uuid4().hex}',
        '_origen_tipo': ORIGEN_MANUAL,
        '_manual': [c.get('clave') for c in definiciones if c.get('clave')],
        '_sin_codigo': True,
    }
    for col in definiciones:
        clave = col.get('clave')
        if clave:
            fila[clave] = valores.get(clave, '')
    from ..models import RegistroFormato

    with transaction.atomic():
        bloqueado = RegistroFormato.objects.select_for_update().get(
            pk=registro.pk,
        )
        actuales = filas(bloqueado)
        actuales.append(fila)
        _guardar_filas(bloqueado, actuales)
        registro.respuestas = bloqueado.respuestas
    return fila
