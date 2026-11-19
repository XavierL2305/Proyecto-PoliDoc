"""Tabla de cambios y modificaciones (SGC-FOR-003).

Una modificación documental puede afectar varias páginas/ítems: la tabla es
**multifila**. Las filas viven en ``CambioDocumento`` (histórico del
documento); las de una planilla en borrador se editan y, al aprobar, se
**autorizan** con el autorizador final y su firma SES.

La generación usa la plantilla oficial (``Formato SGC-FOR-003``), sin
alterar su layout: filas 11–23 por hoja, columnas A/B/C/F/G/I (respetando
los merges C:E, G:H e I:J). Si hay más de 13 cambios, se agregan hojas
``SGC-FOR-003 (2)``, … con ``PÁGINA k DE n``. Debajo de la última tabla se
imprime el sello de evidencia de la firma electrónica.
"""
import io
import logging

from django.db import transaction
from django.utils import timezone
from .xlsx_comun import copiar_hoja as _copiar_hoja
from .xlsx_comun import preparar_para_pdf as _preparar_para_pdf
from .xlsx_comun import xlsx_bytes_a_pdf as _xlsx_a_pdf

logger = logging.getLogger(__name__)

#: Código y hoja de la plantilla oficial.
FORMATO_FOR_003 = 'SGC-FOR-003'
HOJA_FOR_003 = 'SGC-FOR-003'

#: Rango de filas de datos del formulario (12 filas por hoja).
FILA_INICIO = 12
FILA_FIN = 23
MAX_FILAS = FILA_FIN - FILA_INICIO + 1

#: Celda del contador de páginas (``PAGINA: 1 DE 1``).
CELDA_PAGINA = 'I6'

#: Filas de la tabla -> columna del XLSX (la descripción va en C:C:E, etc.).
COLUMNAS = (
    ('numero_pagina', 'A'),
    ('version', 'B'),
    ('descripcion', 'C'),
    ('fecha', 'F'),
    ('realizado_por', 'G'),
    ('autorizado_por', 'I'),
)

#: Fila donde se imprime el sello de firma electrónica (debajo de la tabla).
FILA_SELLO = 25


# ---------------------------------------------------------------------------
# Consultas y autocompletado
# ---------------------------------------------------------------------------

def formato_for_003():
    """``Formato`` activo del SGC-FOR-003 (o ``None``)."""
    from ..models import Formato

    return Formato.objects.filter(codigo=FORMATO_FOR_003, activo=True).first()


def _nombre(usuario):
    if usuario is None:
        return ''
    return usuario.get_full_name() or usuario.username


def filas_historicas(documento):
    """Filas autorizadas del documento (histórico real de cambios)."""
    from ..models import CambioDocumento

    return CambioDocumento.objects.filter(
        documento=documento, autorizado_por__isnull=False,
    ).select_related(
        'realizado_por', 'autorizado_por', 'firma_autorizacion',
    ).order_by('fecha', 'orden', 'id')


def filas_de_planilla(planilla):
    """Filas capturadas en la planilla (borrador o ya autorizadas)."""
    from ..models import CambioDocumento

    return CambioDocumento.objects.filter(planilla=planilla).select_related(
        'realizado_por', 'autorizado_por', 'firma_autorizacion',
    ).order_by('orden', 'id')


def autocompletar_fila(planilla):
    """Valores automáticos de una fila nueva de la planilla."""
    descripcion = (
        planilla.justificacion or planilla.descripcion_cambio or ''
    ).strip()
    return {
        'numero_pagina': '',
        'descripcion': descripcion,
        'version': planilla.version_destino or '',
        'fecha': timezone.localdate(),
        'realizado_por': _nombre(planilla.responsable),
        'autorizado_por': '',
    }


def parsear_filas(crudas, *, parcial):
    """Valida/normaliza las filas enviadas por el editor.

    Args:
        crudas: lista de ``{'numero_pagina':…, 'descripcion':…}``.
        parcial: ``True`` en borrador (no exige descripción).

    Returns:
        tuple: ``(filas, errores)`` — filas como dicts limpios.
    """
    filas = []
    errores = []
    if crudas is None:
        crudas = []
    if not isinstance(crudas, list):
        return [], ['La tabla de cambios no tiene un formato válido.']

    for i, item in enumerate(crudas, start=1):
        if not isinstance(item, dict):
            errores.append(f'Fila {i}: formato inválido.')
            continue
        pagina = str(item.get('numero_pagina') or '').strip()
        descripcion = str(item.get('descripcion') or '').strip()
        if not pagina and not descripcion:
            continue  # fila vacía: se ignora
        if not descripcion and not parcial:
            errores.append(
                f'Fila {i}: la descripción del cambio es obligatoria.'
            )
            continue
        filas.append({
            'numero_pagina': pagina,
            'descripcion': descripcion,
        })

    if not parcial and not filas:
        errores.append(
            'Debes registrar al menos un cambio en la Tabla SGC-FOR-003.'
        )
    return filas, errores


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------

def fila_a_dict(fila):
    """Representación para plantilla/JSON de una fila FOR-003."""
    return {
        'numero_pagina': fila.numero_pagina,
        'version': fila.version,
        'descripcion': fila.descripcion,
        'fecha': _fecha_texto(fila.fecha),
        'realizado_por': _nombre(fila.realizado_por),
        'autorizado_por': _nombre(fila.autorizado_por),
    }


def guardar_filas(planilla, filas):
    """Reemplaza las filas de la planilla con las capturadas en el editor.

    El llamador controla cuándo: creación/edición de un borrador o el envío
    inmediato. Una planilla ya aprobada/rechazada nunca pasa por aquí.
    """
    from ..models import CambioDocumento

    if planilla.estado in ('aprobado', 'rechazado'):
        raise ValueError('No se pueden editar los cambios de una planilla cerrada.')
    if not planilla.documento_id:
        raise ValueError('La planilla no tiene documento asociado.')

    CambioDocumento.objects.filter(planilla=planilla).delete()
    creadas = []
    for orden, fila in enumerate(filas):
        creadas.append(CambioDocumento(
            documento=planilla.documento,
            planilla=planilla,
            numero_pagina=fila.get('numero_pagina', ''),
            version=planilla.version_destino or '',
            descripcion=fila.get('descripcion', ''),
            fecha=timezone.localdate(),
            realizado_por=planilla.responsable,
            orden=orden,
        ))
    CambioDocumento.objects.bulk_create(creadas)
    return creadas


#: Alias de compatibilidad con el nombre usado al diseñar el flujo.
guardar_borrador = guardar_filas


def autorizar_filas(planilla, usuario, firma=None, fecha=None):
    """Marca como autorizadas las filas de la planilla aprobada.

    Returns:
        int: cantidad de filas autorizadas.
    """
    from ..models import CambioDocumento

    fecha = fecha or timezone.now()
    return CambioDocumento.objects.filter(
        planilla=planilla, autorizado_por__isnull=True,
    ).update(
        autorizado_por=usuario,
        fecha_autorizacion=fecha,
        firma_autorizacion=firma,
        fecha=fecha.date(),
        actualizado=fecha,
    )


def autorizar_y_programar(planilla, usuario, firma=None):
    """Autoriza las filas y programa la generación del FOR-003 tras el commit.

    No rompe la aprobación si la generación falla (se registra y se puede
    reintentar desde la descarga, que genera al vuelo).
    """
    autorizar_filas(planilla, usuario, firma=firma)

    def _generar():
        try:
            from ..tasks import generar_for_003_planilla

            generar_for_003_planilla.delay(planilla.pk)
        except Exception as exc:  # noqa: BLE001 - broker caído: fallback
            logger.warning(
                'No se pudo encolar la generación FOR-003 de %s: %s',
                planilla.codigo, exc,
            )
            try:
                generar_y_guardar(planilla, forzar=True)
            except Exception as exc2:  # noqa: BLE001
                logger.error(
                    'Generación FOR-003 en línea falló para %s: %s',
                    planilla.codigo, exc2,
                )

    transaction.on_commit(_generar)


# ---------------------------------------------------------------------------
# Generación XLSX/PDF
# ---------------------------------------------------------------------------

def _fecha_texto(valor):
    if hasattr(valor, 'strftime'):
        return valor.strftime('%d/%m/%Y')
    return str(valor or '')


def _sello_texto(filas):
    """Evidencia de la firma SES del último cambio autorizado."""
    firmadas = [f for f in filas if f.firma_autorizacion_id]
    if not firmadas:
        return ''
    fila = firmadas[-1]
    firma = fila.firma_autorizacion
    partes = [
        f'Firmado electrónicamente por {_nombre(fila.autorizado_por)}',
        f'SES #{firma.pk}',
    ]
    if firma.hash_contenido:
        partes.append(f'SHA-256 {firma.hash_contenido[:16]}…')
    if fila.fecha_autorizacion:
        partes.append(
            timezone.localtime(fila.fecha_autorizacion).strftime('%d/%m/%Y %H:%M')
        )
    return ' — '.join(partes)


def generar_xlsx(formato, filas):
    """Bytes del XLSX FOR-003 con todas las filas (multi-hoja si hace falta).

    Args:
        formato: ``Formato`` con ``plantilla_xlsx``.
        filas: iterable de ``CambioDocumento`` (o dicts con las mismas claves).

    Raises:
        ValueError: si el formato no tiene plantilla.
    """
    import openpyxl

    if not formato or not formato.plantilla_xlsx:
        raise ValueError('El formato SGC-FOR-003 no tiene plantilla XLSX.')

    with formato.plantilla_xlsx.open('rb') as fh:
        plantilla = fh.read()
    wb = openpyxl.load_workbook(io.BytesIO(plantilla))

    base = wb[HOJA_FOR_003] if HOJA_FOR_003 in wb.sheetnames else wb.worksheets[0]
    filas = list(filas)
    chunks = [
        filas[i:i + MAX_FILAS] for i in range(0, len(filas), MAX_FILAS)
    ] or [[]]
    total = len(chunks)

    # Crear las hojas extra ANTES de rellenar (se copian de la base vacía;
    # si se copiara después, arrastraría las filas de la hoja anterior).
    hojas = [base]
    for idx in range(1, total):
        nueva = _copiar_hoja(wb, base)
        nueva.title = f'{HOJA_FOR_003} ({idx + 1})'
        hojas.append(nueva)

    for idx, (hoja, chunk) in enumerate(zip(hojas, chunks)):
        hoja[CELDA_PAGINA] = f'PAGINA: {idx + 1} DE {total}'

        for offset, fila in enumerate(chunk):
            numero = FILA_INICIO + offset
            valores = {
                'numero_pagina': getattr(fila, 'numero_pagina', ''),
                'version': getattr(fila, 'version', ''),
                'descripcion': getattr(fila, 'descripcion', ''),
                'fecha': _fecha_texto(getattr(fila, 'fecha', '')),
                'realizado_por': _nombre(getattr(fila, 'realizado_por', None)),
                'autorizado_por': _nombre(getattr(fila, 'autorizado_por', None)),
            }
            for campo, columna in COLUMNAS:
                hoja[f'{columna}{numero}'] = valores[campo]

    # Sello de evidencia en la última hoja (después de su tabla).
    sello = _sello_texto(filas)
    if sello:
        ultima = hojas[-1]
        rango = f'A{FILA_SELLO}:J{FILA_SELLO}'
        if rango not in {str(r) for r in ultima.merged_cells.ranges}:
            ultima.merge_cells(rango)
        celda = ultima[f'A{FILA_SELLO}']
        celda.value = sello
        celda.font = openpyxl.styles.Font(size=8, italic=True)

    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


def generar_pdf(formato, filas):
    """Bytes del PDF FOR-003 (o ``None`` si LibreOffice no está disponible)."""
    return _xlsx_a_pdf(_preparar_para_pdf(generar_xlsx(formato, filas)))


def nombre_archivo(planilla, extension):
    documento = planilla.documento
    codigo = (documento.codigo if documento else 'SGC').replace(' ', '_')
    return f'SGC-FOR-003_{codigo}_{planilla.uuid.hex[:8]}.{extension}'


def generar_y_guardar(planilla, forzar=False):
    """Genera y persiste el XLSX/PDF FOR-003 de la planilla (idempotente).

    Never raises: devuelve ``{'success': bool, 'xlsx': str|None,
    'pdf': str|None, 'error': str}``.
    """
    from django.core.files.base import ContentFile

    if planilla.tipo_cambio != 'modificacion' or not planilla.documento_id:
        return {
            'success': False, 'xlsx': None, 'pdf': None,
            'error': 'El FOR-003 solo aplica a modificaciones con documento.',
        }

    formato = formato_for_003()
    if formato is None or not formato.plantilla_xlsx:
        return {
            'success': False, 'xlsx': None, 'pdf': None,
            'error': 'El formato SGC-FOR-003 no está integrado o no tiene plantilla.',
        }

    if not forzar and planilla.for_003_xlsx:
        return {
            'success': True, 'xlsx': planilla.for_003_xlsx.name,
            'pdf': planilla.for_003_pdf.name or None, 'error': '',
        }

    filas = list(filas_historicas(planilla.documento))
    if not filas:
        return {
            'success': False, 'xlsx': None, 'pdf': None,
            'error': 'El documento no tiene cambios autorizados para exportar.',
        }

    try:
        xlsx = generar_xlsx(formato, filas)
    except Exception as exc:  # noqa: BLE001
        logger.error('No se pudo generar el XLSX FOR-003: %s', exc)
        return {'success': False, 'xlsx': None, 'pdf': None, 'error': str(exc)}

    if forzar:
        for campo in ('for_003_xlsx', 'for_003_pdf'):
            archivo = getattr(planilla, campo)
            if archivo:
                try:
                    archivo.delete(save=False)
                except Exception:  # noqa: BLE001
                    pass

    planilla.for_003_xlsx.save(
        nombre_archivo(planilla, 'xlsx'), ContentFile(xlsx), save=False,
    )
    planilla.for_003_pdf = ''
    try:
        pdf = _xlsx_a_pdf(_preparar_para_pdf(xlsx))
    except Exception as exc:  # noqa: BLE001
        logger.warning('No se pudo generar el PDF FOR-003: %s', exc)
        pdf = None
    if pdf:
        planilla.for_003_pdf.save(
            nombre_archivo(planilla, 'pdf'), ContentFile(pdf), save=False,
        )
    planilla.save(update_fields=['for_003_xlsx', 'for_003_pdf'])
    return {
        'success': True, 'xlsx': planilla.for_003_xlsx.name,
        'pdf': planilla.for_003_pdf.name or None, 'error': '',
    }
