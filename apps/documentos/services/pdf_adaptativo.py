"""Conversión adaptativa: convierte, verifica el PDF y repara si hace falta.

El saneo de ``conversion_pdf.py`` decide el salto antes de convertir. Aquí se
cierra el círculo: tras generar el PDF se corre el QA (``pdf_qa.py``) y, si
aparece un logo recortado o un título huérfano, se generan disposiciones
alternativas de la copia temporal, se convierten en lote y se sobrescribe el
PDF canónico con la más limpia.

El QA es barato (solo lee el PDF); la reconversión se delega a Celery para no
bloquear la petición. Si el broker no responde se repara en línea, siguiendo el
patrón ya usado en el resto del proyecto.
"""

import logging
import os
import shutil

from django.conf import settings

from .conversion_pdf import convertir_lote, generar_alternativas, resolver_margenes
from .pdf_qa import analizar_pdf

logger = logging.getLogger(__name__)

MAX_ALTERNATIVAS = 3


def _ajuste(clave, defecto):
    valor = getattr(settings, clave, defecto)
    try:
        return int(valor)
    except (TypeError, ValueError):
        return defecto


def diagnosticar(pdf):
    """QA del PDF con los márgenes configurados. Nunca lanza."""
    margen = getattr(settings, 'MARGEN_INFERIOR_PDF_CONVERSION', 0.4)
    return analizar_pdf(
        pdf,
        margen_inferior=margen if margen is not None else 0.4,
        detectar_solape=getattr(settings, 'PDF_QA_TEXTO_SOLAPE', True),
    )


def registrar_diagnostico(origen, pdf, diagnostico, estado, intentos=0):
    """Guarda/actualiza el diagnóstico del PDF. Nunca lanza."""
    try:
        from ..models import ConversionPDFDiagnostico
        ConversionPDFDiagnostico.objects.update_or_create(
            origen=origen, pdf=pdf,
            defaults={
                'defectos': diagnostico.to_dict().get('defectos', []),
                'estado': estado,
                'intentos': intentos,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning('No se pudo registrar el diagnóstico de %s: %s', pdf, exc)


def reparar(archivo, pdf, diagnostico=None):
    """Busca una disposición más limpia y, si la hay, sobrescribe ``pdf``.

    Devuelve el mejor :class:`Diagnostico`. Nunca lanza.
    """
    if diagnostico is None:
        diagnostico = diagnosticar(pdf)
    if diagnostico.limpio:
        return diagnostico

    temp_dir = None
    try:
        alternativas, temp_dir = generar_alternativas(
            archivo,
            papel=getattr(settings, 'PAPEL_PDF_CONVERSION', None),
            margenes=resolver_margenes(),
            max_alternativas=_ajuste('PDF_MAX_INTENTOS', MAX_ALTERNATIVAS),
        )
        if not alternativas:
            registrar_diagnostico(archivo, pdf, diagnostico, 'fallido')
            return diagnostico
        generados = convertir_lote(
            [alt['ruta'] for alt in alternativas],
            os.path.join(temp_dir, 'pdf'),
        )
        mejor_diagnostico = diagnostico
        mejor_pdf = None
        for alt in alternativas:
            candidato = generados.get(alt['ruta'])
            if not candidato:
                continue
            candidato_diagnostico = diagnosticar(candidato)
            if candidato_diagnostico.puntuacion < mejor_diagnostico.puntuacion:
                mejor_diagnostico = candidato_diagnostico
                mejor_pdf = candidato
        if mejor_pdf:
            temporal = pdf + '.reparando'
            shutil.copyfile(mejor_pdf, temporal)
            os.replace(temporal, pdf)
            logger.info(
                'PDF reparado (%s): %s', mejor_diagnostico.resumen(), pdf,
            )
            registrar_diagnostico(
                archivo, pdf, mejor_diagnostico, 'reparado', len(alternativas),
            )
        else:
            registrar_diagnostico(
                archivo, pdf, diagnostico, 'fallido', len(alternativas),
            )
        return mejor_diagnostico
    except Exception as exc:  # noqa: BLE001
        logger.error('Reparación adaptativa falló para %s: %s', archivo, exc)
        return diagnostico
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


def programar_reparacion(archivo, pdf):
    """Encola la reparación; si no hay broker, la ejecuta en línea."""
    if not getattr(settings, 'PDF_REPARAR_EN_CELERY', True):
        return reparar(archivo, pdf)

    from ..tasks import reparar_pdf_adaptativo

    try:
        reparar_pdf_adaptativo.delay(archivo, pdf)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning('No se pudo encolar la reparación de %s: %s', pdf, exc)
        return reparar(archivo, pdf)


def reparar_en_linea(archivo, pdf):
    """QA del PDF y, si hay defectos, reparación **síncrona**.

    Igual que :func:`verificar`, pero la reparación ocurre aquí mismo en vez de
    encolarse: es el camino de la exportación al vuelo (``exportar_pdf``), cuyo
    XLSX temporal se borra en cuanto se sirve la respuesta, de modo que una
    reparación asíncrona se perdería siempre.

    Devuelve el :class:`Diagnostico` o ``None`` si el QA está desactivado.
    Nunca lanza.
    """
    if not getattr(settings, 'PDF_QA_ADAPTATIVO', True):
        return None
    if not pdf or not os.path.exists(pdf):
        return None
    diagnostico = diagnosticar(pdf)
    if diagnostico.limpio:
        registrar_diagnostico(archivo, pdf, diagnostico, 'ok')
        return diagnostico
    logger.warning(
        'PDF con defectos de maquetación en %s; reparando en línea: %s',
        archivo, diagnostico.resumen(),
    )
    return reparar(archivo, pdf, diagnostico)


def verificar(archivo, pdf):
    """QA del PDF recién generado; programa la reparación si hay defectos.

    Devuelve el :class:`Diagnostico` o ``None`` si el QA está desactivado.
    """
    if not getattr(settings, 'PDF_QA_ADAPTATIVO', True):
        return None
    if not pdf or not os.path.exists(pdf):
        return None
    diagnostico = diagnosticar(pdf)
    if diagnostico.limpio:
        registrar_diagnostico(archivo, pdf, diagnostico, 'ok')
        return diagnostico
    logger.warning(
        'PDF con defectos de maquetación en %s: %s', archivo, diagnostico.resumen(),
    )
    registrar_diagnostico(archivo, pdf, diagnostico, 'pendiente')
    programar_reparacion(archivo, pdf)
    return diagnostico
