"""Tests del QA del PDF (pdf_qa.py) y de la reparación adaptativa.

Cubren la detección de imágenes recortadas/títulos huérfanos, la detección
estructural de secciones por el logo de encabezado, el bucle de reparación
(elegir la alternativa más limpia) y el salto de título en DOCX.
"""

import os
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings

from documentos.models import ConversionPDFDiagnostico
from documentos.services import pdf_adaptativo as pa
from documentos.services import pdf_qa
from documentos.services.conversion_pdf import (
    _anclas_imagen,
    _detectar_secciones,
    _leer_items,
    _parchear_docx_xml,
    convertir_office_a_pdf,
    generar_alternativas,
)
from documentos.tests_conversion import (
    BaseTemporalTest,
    LIBREOFFICE,
    dibujos_pdf,
    xlsx_secciones_con_logo,
)


# ---------------------------------------------------------------------------
# Dobles de pdfplumber para probar el QA sin un PDF real
# ---------------------------------------------------------------------------

class _PaginaFalsa:
    def __init__(self, height, images=(), chars=(), texto=''):
        self.height = height
        self.width = 595
        self.images = list(images)
        self.chars = list(chars)
        self._texto = texto

    def extract_text(self):
        return self._texto


class _PdfFalso:
    def __init__(self, paginas):
        self.pages = paginas

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _abrir_falso(paginas):
    return patch('pdfplumber.open', return_value=_PdfFalso(paginas))


def _defecto(pagina, tipo='imagen_recortada'):
    return pdf_qa.Defecto(tipo, pagina)


class AnalizarPdfTest(unittest.TestCase):

    def test_pdf_ilegible_es_limpio(self):
        diagnostico = pdf_qa.analizar_pdf('/ruta/que/no/existe.pdf')
        self.assertTrue(diagnostico.limpio)
        self.assertEqual(diagnostico.paginas, 0)

    def test_imagen_recortada(self):
        pagina = _PaginaFalsa(842, images=[
            {'x0': 50, 'x1': 120, 'top': 784, 'bottom': 843, 'width': 70, 'height': 59},
        ])
        with _abrir_falso([pagina]):
            diagnostico = pdf_qa.analizar_pdf('x.pdf')
        self.assertEqual([d.tipo for d in diagnostico.defectos], ['imagen_recortada'])

    def test_imagen_de_cabecera_no_es_defecto(self):
        pagina = _PaginaFalsa(842, images=[
            {'x0': 50, 'x1': 120, 'top': 31, 'bottom': 101, 'width': 70, 'height': 70},
        ])
        with _abrir_falso([pagina]):
            diagnostico = pdf_qa.analizar_pdf('x.pdf')
        self.assertTrue(diagnostico.limpio)

    def test_texto_tapado_en_la_mitad_inferior(self):
        imagen = {'x0': 50, 'x1': 120, 'top': 500, 'bottom': 560, 'width': 70, 'height': 60}
        letras = [{'x0': 60, 'x1': 70, 'top': 510, 'bottom': 520, 'text': 'F'}]
        pagina = _PaginaFalsa(842, images=[imagen], chars=letras)
        with _abrir_falso([pagina]):
            diagnostico = pdf_qa.analizar_pdf('x.pdf')
        self.assertEqual([d.tipo for d in diagnostico.defectos], ['texto_tapado'])

    def test_titulo_huerfano(self):
        paginas = [
            _PaginaFalsa(842, texto='cuerpo\nTITULO'),
            _PaginaFalsa(842, texto='sigue el cuerpo'),
        ]
        with _abrir_falso(paginas):
            diagnostico = pdf_qa.analizar_pdf('x.pdf', titulos_seccion=['TITULO'])
        self.assertEqual([d.tipo for d in diagnostico.defectos], ['titulo_huerfano'])

    def test_sin_solape_desactivado(self):
        imagen = {'x0': 50, 'x1': 120, 'top': 500, 'bottom': 560, 'width': 70, 'height': 60}
        letras = [{'x0': 60, 'x1': 70, 'top': 510, 'bottom': 520, 'text': 'F'}]
        pagina = _PaginaFalsa(842, images=[imagen], chars=letras)
        with _abrir_falso([pagina]):
            diagnostico = pdf_qa.analizar_pdf('x.pdf', detectar_solape=False)
        self.assertTrue(diagnostico.limpio)

    def test_resumen_de_defectos(self):
        diagnostico = pdf_qa.Diagnostico(paginas=3, defectos=[_defecto(2), _defecto(3)])
        self.assertEqual(diagnostico.puntuacion, 2)
        self.assertIn('imagen_recortada@p2', diagnostico.resumen())
        self.assertEqual(diagnostico.to_dict()['paginas'], 3)


# ---------------------------------------------------------------------------
# Detección estructural de secciones
# ---------------------------------------------------------------------------

class DeteccionSeccionesTest(BaseTemporalTest):

    def test_detecta_la_seccion_por_el_logo_reutilizado(self):
        origen = xlsx_secciones_con_logo(self.ruta('logos.xlsx'))
        orden, items = _leer_items(origen)
        secciones = _detectar_secciones(orden, items)
        self.assertEqual(secciones['xl/worksheets/sheet1.xml']['filas'], [29])

    def test_sin_logo_de_cabecera_no_detecta(self):
        origen = xlsx_secciones_con_logo(self.ruta('sin.xlsx'), encabezado=False)
        orden, items = _leer_items(origen)
        self.assertEqual(_detectar_secciones(orden, items), {})

    def test_anclas_con_y_sin_prefijo(self):
        ns_r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        xml = (
            '<w xmlns:xdr="http://x" xmlns:r="%s">'
            '<xdr:oneCellAnchor><xdr:from><xdr:row>3</xdr:row></xdr:from>'
            '<a:blip xmlns:a="http://a" r:embed="rId1"/></xdr:oneCellAnchor>'
            '<oneCellAnchor><from><row>7</row></from>'
            '<blip xmlns:r="%s" r:embed="rId2"/></oneCellAnchor>'
            '</w>'
        ) % (ns_r, ns_r)
        anclas = _anclas_imagen(xml.encode('utf-8'))
        self.assertEqual([a['fila'] for a in anclas], [3, 7])
        self.assertEqual([a['embed'] for a in anclas], ['rId1', 'rId2'])

    def test_dibujo_corrupto_devuelve_vacio(self):
        self.assertEqual(_anclas_imagen(b'no soy xml'), [])


class AlternativasTest(BaseTemporalTest):

    def test_mueve_el_logo_de_seccion_una_fila(self):
        origen = xlsx_secciones_con_logo(self.ruta('alt.xlsx'))
        alternativas, temp_dir = generar_alternativas(
            origen, 'A4', max_alternativas=1,
        )
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        self.assertEqual([a['nombre'] for a in alternativas], ['logo1'])

        with zipfile.ZipFile(alternativas[0]['ruta']) as z:
            anclas = _anclas_imagen(z.read('xl/drawings/drawing1.xml'))
        filas_onecell = [a['fila'] for a in anclas if a['tipo'] == 'oneCellAnchor']
        self.assertEqual(filas_onecell, [30])
        # el logo de cabecera (fila 0) no se mueve
        self.assertIn(0, [a['fila'] for a in anclas if a['tipo'] == 'twoCellAnchor'])

    def test_sin_secciones_no_hay_alternativas(self):
        from documentos.tests_conversion import crear_xlsx
        origen = crear_xlsx(self.ruta('simple.xlsx'), ['<pageSetup paperSize="9"/>'])
        alternativas, temp_dir = generar_alternativas(origen, 'A4')
        self.assertEqual(alternativas, [])
        self.assertIsNone(temp_dir)


# ---------------------------------------------------------------------------
# Bucle de reparación
# ---------------------------------------------------------------------------

class RepararTest(BaseTemporalTest):

    def setUp(self):
        super().setUp()
        self.origen = self.ruta('origen.xlsx')
        with open(self.origen, 'wb') as f:
            f.write(b'xlsx')
        self.canonico = self.ruta('canonico.pdf')
        with open(self.canonico, 'wb') as f:
            f.write(b'base')
        self.alternativa = self.ruta('alternativa.pdf')
        with open(self.alternativa, 'wb') as f:
            f.write(b'mejor')
        self.alt_tmp = tempfile.mkdtemp(prefix='polidoc-alt-')
        self.addCleanup(shutil.rmtree, self.alt_tmp, ignore_errors=True)

    def _diag(self):

        def _diagnosticar(ruta):
            if ruta == self.alternativa:
                return pdf_qa.Diagnostico(paginas=3)
            return pdf_qa.Diagnostico(paginas=3, defectos=[_defecto(2)])

        return _diagnosticar

    def test_repara_con_la_alternativa_limpia(self):
        inicial = pdf_qa.Diagnostico(paginas=3, defectos=[_defecto(2)])
        with patch.object(pa, 'generar_alternativas',
                          return_value=([{'nombre': 'logo1', 'ruta': '/tmp/x.xlsx'}], self.alt_tmp)), \
                patch.object(pa, 'convertir_lote',
                             return_value={'/tmp/x.xlsx': self.alternativa}), \
                patch.object(pa, 'diagnosticar', side_effect=self._diag()), \
                patch.object(pa, 'registrar_diagnostico'):
            final = pa.reparar(self.origen, self.canonico, inicial)

        self.assertTrue(final.limpio)
        with open(self.canonico, 'rb') as f:
            self.assertEqual(f.read(), b'mejor')

    def test_sin_mejora_conserva_el_pdf(self):
        inicial = pdf_qa.Diagnostico(paginas=3, defectos=[_defecto(2)])

        def siempre_defectuoso(ruta):
            return pdf_qa.Diagnostico(paginas=3, defectos=[_defecto(2)])

        with patch.object(pa, 'generar_alternativas',
                          return_value=([{'nombre': 'logo1', 'ruta': '/tmp/x.xlsx'}], self.alt_tmp)), \
                patch.object(pa, 'convertir_lote',
                             return_value={'/tmp/x.xlsx': self.alternativa}), \
                patch.object(pa, 'diagnosticar', side_effect=siempre_defectuoso), \
                patch.object(pa, 'registrar_diagnostico'):
            final = pa.reparar(self.origen, self.canonico, inicial)

        self.assertFalse(final.limpio)
        with open(self.canonico, 'rb') as f:
            self.assertEqual(f.read(), b'base')

    def test_pdf_limpio_no_se_toca(self):
        limpio = pdf_qa.Diagnostico(paginas=3)
        with patch.object(pa, 'generar_alternativas') as m:
            final = pa.reparar(self.origen, self.canonico, limpio)
        self.assertTrue(final.limpio)
        m.assert_not_called()

    @override_settings(PDF_REPARAR_EN_CELERY=False)
    def test_sin_celery_repara_en_linea(self):
        with patch.object(pa, 'reparar', return_value=pdf_qa.Diagnostico()) as mock:
            pa.programar_reparacion(self.origen, self.canonico)
        mock.assert_called_once()


class VerificarTest(TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='polidoc-qa-')
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.pdf = os.path.join(self.dir, 'doc.pdf')
        with open(self.pdf, 'wb') as f:
            f.write(b'%PDF-1.4')
        self.origen = os.path.join(self.dir, 'doc.xlsx')
        with open(self.origen, 'wb') as f:
            f.write(b'xlsx')

    @override_settings(PDF_QA_ADAPTATIVO=True)
    def test_programa_reparacion_y_registra(self):
        defectuoso = pdf_qa.Diagnostico(paginas=3, defectos=[_defecto(2)])
        with patch.object(pa, 'diagnosticar', return_value=defectuoso), \
                patch.object(pa, 'programar_reparacion') as mock:
            pa.verificar(self.origen, self.pdf)

        mock.assert_called_once()
        registro = ConversionPDFDiagnostico.objects.get()
        self.assertEqual(registro.estado, 'pendiente')
        self.assertEqual(registro.defectos[0]['tipo'], 'imagen_recortada')

    @override_settings(PDF_QA_ADAPTATIVO=True)
    def test_pdf_limpio_registra_ok(self):
        with patch.object(pa, 'diagnosticar', return_value=pdf_qa.Diagnostico(paginas=3)), \
                patch.object(pa, 'programar_reparacion') as mock:
            pa.verificar(self.origen, self.pdf)

        mock.assert_not_called()
        self.assertEqual(ConversionPDFDiagnostico.objects.get().estado, 'ok')

    @override_settings(PDF_QA_ADAPTATIVO=False)
    def test_desactivado_no_hace_nada(self):
        with patch.object(pa, 'diagnosticar') as mock:
            self.assertIsNone(pa.verificar(self.origen, self.pdf))
        mock.assert_not_called()
        self.assertEqual(ConversionPDFDiagnostico.objects.count(), 0)


class TareaRepararTest(TestCase):

    def test_tarea_llama_a_reparar(self):
        from documentos.tasks import reparar_pdf_adaptativo

        with patch('documentos.services.pdf_adaptativo.reparar',
                   return_value=pdf_qa.Diagnostico(paginas=3)) as mock:
            resultado = reparar_pdf_adaptativo('a.xlsx', 'a.pdf')

        mock.assert_called_once()
        self.assertTrue(resultado['success'])


# ---------------------------------------------------------------------------
# Salto de título en DOCX
# ---------------------------------------------------------------------------

class DocxSaltoTest(unittest.TestCase):

    NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

    def _docx(self, textos):
        parrafos = ''.join(
            '<w:p><w:r><w:t>%s</w:t></w:r></w:p>' % t for t in textos
        )
        return (
            '<?xml version="1.0"?><w:document xmlns:w="%s"><w:body>'
            '%s<w:sectPr/></w:body></w:document>' % (self.NS, parrafos)
        ).encode('utf-8')

    def test_marca_salto_antes_del_parrafo_repetido(self):
        salida, ok = _parchear_docx_xml(
            self._docx(['TITULO', 'cuerpo', 'TITULO']), 12240, 15840,
        )
        self.assertTrue(ok)
        self.assertEqual(salida.count(b'pageBreakBefore'), 1)

    def test_sin_repeticion_no_marca(self):
        salida, _ = _parchear_docx_xml(
            self._docx(['TITULO', 'cuerpo', 'otra cosa']), 12240, 15840,
        )
        self.assertEqual(salida.count(b'pageBreakBefore'), 0)

    def test_salto_docx_se_puede_desactivar(self):
        salida, _ = _parchear_docx_xml(
            self._docx(['TITULO', 'TITULO']), 12240, 15840, salto_titulo=False,
        )
        self.assertEqual(salida.count(b'pageBreakBefore'), 0)


# ---------------------------------------------------------------------------
# Regresión real: la plantilla SGC-FOR-005 se repara sin tocarla
# ---------------------------------------------------------------------------

RUTA_FOR005 = os.path.join(
    str(settings.MEDIA_ROOT), 'formatos', 'plantillas', '2025', 'SGC-FOR-005.xlsx',
)


@unittest.skipUnless(LIBREOFFICE and os.path.exists(RUTA_FOR005),
                     'LibreOffice o la plantilla SGC-FOR-005 no están disponibles')
class ReparacionReal005Test(BaseTemporalTest):

    def test_repara_el_logo_recortado_sin_tocar_la_plantilla(self):
        xlsx = self.ruta('SGC-FOR-005.xlsx')
        shutil.copyfile(RUTA_FOR005, xlsx)
        original = open(RUTA_FOR005, 'rb').read()

        base = convertir_office_a_pdf(xlsx, outdir=self.ruta('out'))
        self.assertTrue(base)
        inicial = pa.diagnosticar(base)
        self.assertFalse(inicial.limpio, 'el caso real debía salir con defecto')

        final = pa.reparar(xlsx, base, inicial)

        self.assertTrue(final.limpio, final.resumen())
        dibujos = dibujos_pdf(base)
        por_pagina = {}
        for pagina, _, _ in dibujos:
            por_pagina[pagina] = por_pagina.get(pagina, 0) + 1
        self.assertEqual(sorted(por_pagina.values()), [1, 1, 1])
        self.assertEqual(open(RUTA_FOR005, 'rb').read(), original)
