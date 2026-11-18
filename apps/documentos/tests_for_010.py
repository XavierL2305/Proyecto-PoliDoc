"""Tests del Plan de Acción ante No Conformidades (SGC-FOR-010).

Cubre la integración del formato real (comando), el export por parcheo XML
(gráfico Pareto, Ishikawa y casillas), la validación numérica de incidencias,
el tope de 6 causas, los desbordes con anexo y el versionado de registros.
"""
import io
import json
import os
import tempfile
import zipfile
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock, patch

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.http import QueryDict
from django.test import Client, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.forms import validar_respuestas, validar_respuestas_guardadas
from documentos.models import (
    CampoFormato, Formato, RegistroFormato, VersionRegistroFormato,
)
from documentos.services import registros as regsvc
from documentos.tests_sgc_utils import crear_plantilla_for_010

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_for010_')
PDF = b'%PDF-1.4\n%prueba\n'


def wb_sheetnames(data):
    """Nombres de las hojas del XLSX exportado."""
    return openpyxl.load_workbook(BytesIO(data)).sheetnames


def datos_for_010(**overrides):
    datos = {
        'nc_numero': 'NC-2026-001',
        'fecha_analisis': '2026-11-18',
        'proceso_origen': 'Auditoría Interna',
        'descripcion_nc': 'No se determinaron los indicadores del proceso.',
        'deteccion': ['auditoria', 'quejas'],
        'acciones_correccion': [
            {'descripcion': 'Revisar el procedimiento',
             'fecha_inicio': '2026-11-20', 'fecha_fin': '2026-11-30',
             'responsable': 'Ana', 'avance': '50%'},
        ],
        'integrantes': [
            {'nombre': 'Ana Pérez'}, {'nombre': 'Luis Gómez'},
        ],
        'lider_equipo': 'Ana Pérez',
        'datos_problema': 'Materiales: insumos vencidos.',
        'analisis_problema': 'El proceso no evidencia seguimiento.',
        'causas': [
            {'descripcion': 'Falta de personal', 'incidencia': 87},
            {'descripcion': 'Equipo descalibrado', 'incidencia': 77},
        ],
        'acciones_correctivas': [
            {'descripcion': 'Plan de formación',
             'fecha_inicio': '2026-12-01', 'fecha_fin': '2026-12-15',
             'responsable': 'Luis', 'avance': '0%'},
        ],
        'revision_eficacia': 'Se verificó la eficacia con indicadores.',
    }
    datos.update(overrides)
    return datos


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseFor010Test(BaseDocumentosTest):
    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        plantilla = crear_plantilla_for_010(
            os.path.join(TEST_MEDIA, 'for010_sintetica.xlsx'),
        )
        with patch(
            'documentos.tasks.convertir_formato_a_pdf.delay',
            return_value=None,
        ), patch(
            'documentos.tasks.convertir_formato_a_pdf_sync',
            return_value=None,
        ):
            call_command(
                'integrar_formatos_sgc', solo='010', apply=True,
                verbosity=0, plantilla=plantilla, stdout=io.StringIO(),
            )
        cls.formato = Formato.objects.get(codigo='SGC-FOR-010')

    def setUp(self):
        super().setUp()
        self._mongo = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo.start()
        self.addCleanup(self._mongo.stop)
        self.client = Client()

    def _registro(self, respuestas=None, **overrides):
        datos = dict(overrides)
        datos.setdefault('contexto_tipo', 'documento')
        datos.setdefault('contexto_id', str(self.doc_activo.uuid))
        datos.setdefault('titulo', 'Plan de acción NC-2026-001')
        datos.setdefault('fecha', date(2026, 11, 18))
        datos.setdefault('responsable', self.user_admin)
        datos.setdefault('estado', 'borrador')
        datos.setdefault(
            'respuestas',
            datos_for_010() if respuestas is None else respuestas,
        )
        return RegistroFormato.objects.create(
            formato=self.formato,
            formato_codigo=self.formato.codigo,
            formato_version=self.formato.version,
            formato_edicion=self.formato.edicion,
            **datos,
        )

    def _xlsx(self, registro=None, para_pdf=False):
        from documentos.services.exportacion import exportar_xlsx_formato

        registro = registro or self._registro()
        return exportar_xlsx_formato(
            registro.formato, registro.respuestas or {}, para_pdf=para_pdf,
        )


class IntegracionTest(BaseFor010Test):
    def test_metadata_del_formato(self):
        formato = self.formato
        self.assertEqual(formato.nombre, 'Plan de Acción ante No Conformidades')
        self.assertEqual(formato.version, '1')
        self.assertEqual(formato.edicion, '1')
        self.assertEqual(formato.fecha_vigencia, date(2025, 3, 18))
        self.assertTrue(formato.permite_registros)
        self.assertFalse(formato.aplica_creacion)
        self.assertFalse(formato.aplica_modificacion)
        self.assertFalse(formato.aplica_eliminacion)
        self.assertIn('Instructivo de llenado', formato.instructivo_html)
        self.assertIn('10.2', formato.instructivo_html)

    def test_estructura_y_celdas(self):
        campos = {
            c.clave: c for c in CampoFormato.objects.filter(
                seccion__formato=self.formato,
            )
        }
        self.assertEqual(len(campos), 13)
        self.assertEqual(campos['nc_numero'].celda_excel, 'B7')
        self.assertEqual(campos['fecha_analisis'].celda_excel, 'G7')
        self.assertEqual(campos['proceso_origen'].celda_excel, 'E9')
        self.assertEqual(campos['descripcion_nc'].celda_excel, 'D11:G13')
        self.assertEqual(campos['lider_equipo'].celda_excel, 'E25')
        self.assertEqual(campos['analisis_problema'].celda_excel, 'A44:G49')
        self.assertTrue(campos['deteccion'].requerido)
        self.assertEqual(len(campos['deteccion'].opciones), 4)
        self.assertEqual(
            [o['shape'] for o in campos['deteccion'].opciones],
            ['Rectángulo 70', 'Rectángulo 71', 'Rectángulo 79',
             'Rectángulo 80'],
        )

    def test_config_tablas(self):
        causas = CampoFormato.objects.get(
            seccion__formato=self.formato, clave='causas',
        ).config_tabla
        self.assertEqual(causas['max_filas'], 6)
        self.assertTrue(causas['limpiar_filas'])
        self.assertEqual(causas['totales']['fila'], 58)
        incidencia = [
            c for c in causas['columnas'] if c['clave'] == 'incidencia'
        ][0]
        self.assertEqual(incidencia['tipo'], 'entero')
        self.assertEqual(incidencia['celda'], 'F')

        correcciones = CampoFormato.objects.get(
            seccion__formato=self.formato, clave='acciones_correccion',
        ).config_tabla
        self.assertEqual(correcciones['fila_inicio'], 16)
        self.assertEqual(correcciones['max_filas'], 7)
        self.assertEqual(len(correcciones['columnas']), 5)

    def test_formulario_limita_filas_en_cliente(self):
        self.client.force_login(self.user_admin)
        url = reverse('documentos:registro_crear') + (
            f'?formato={self.formato.pk}&contexto_tipo=documento'
            f'&contexto_id={self.doc_activo.uuid}'
        )
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'var maxFilas = 6')
        self.assertContains(respuesta, 'syncAddButton')

    def test_dry_run_no_escribe(self):
        Formato.objects.filter(codigo='SGC-FOR-010').delete()
        ruta = os.path.join(TEST_MEDIA, 'for010_sintetica.xlsx')
        salida = io.StringIO()
        call_command(
            'integrar_formatos_sgc', solo='010', verbosity=0,
            plantilla=ruta, stdout=salida,
        )
        self.assertFalse(Formato.objects.filter(codigo='SGC-FOR-010').exists())
        self.assertIn('dry-run', salida.getvalue())


class ExportTest(BaseFor010Test):
    def _wb(self, data):
        return openpyxl.load_workbook(BytesIO(data), data_only=True)

    def test_identificacion_y_tablas(self):
        data = self._xlsx()
        wb = self._wb(data)
        ws = wb['SGC-FOR-010']
        self.assertEqual(ws['B7'].value, 'NC-2026-001')
        self.assertEqual(ws['G7'].value, '2026-11-18')
        self.assertEqual(ws['E9'].value, 'Auditoría Interna')
        self.assertIn('indicadores', ws['D11'].value)
        self.assertEqual(ws['A16'].value, 'Revisar el procedimiento')
        self.assertEqual(ws['D16'].value, '2026-11-20')
        self.assertEqual(ws['A25'].value, 'Ana Pérez')
        self.assertEqual(ws['A26'].value, 'Luis Gómez')
        self.assertEqual(ws['E25'].value, 'Ana Pérez')
        self.assertEqual(ws['A60'].value, 'Plan de formación')

    def test_causas_numericas_y_totales(self):
        data = self._xlsx()
        ws = self._wb(data)['SGC-FOR-010']
        self.assertEqual(ws['A52'].value, 'Falta de personal')
        self.assertEqual(ws['A53'].value, 'Equipo descalibrado')
        # Celda numérica real (no texto): el Pareto necesita números.
        self.assertEqual(ws['F52'].value, 87)
        self.assertIsInstance(ws['F52'].value, int)
        self.assertAlmostEqual(ws['G52'].value, 87 / 164)
        self.assertAlmostEqual(ws['G53'].value, 77 / 164)
        self.assertEqual(ws['F58'].value, 164)
        self.assertEqual(ws['G58'].value, 1)

    def test_limpieza_de_causas_de_ejemplo(self):
        data = self._xlsx()
        ws = self._wb(data)['SGC-FOR-010']
        for fila in range(54, 58):
            self.assertIsNone(ws[f'A{fila}'].value)
            self.assertIsNone(ws[f'F{fila}'].value)
            self.assertFalse(ws[f'G{fila}'].value)
        # Ninguna causa de ejemplo de la plantilla sobrevive.
        textos = {
            str(celda.value) for fila in ws.iter_rows()
            for celda in fila if celda.value
        }
        self.assertNotIn('MATERIALES DE MALA CALIDAD', textos)
        self.assertNotIn(
            'FALTA DE CONOCIMIENTO DE LA METODOLOGIA', textos,
        )

    def test_sin_causas_deja_todo_limpio(self):
        registro = self._registro(
            respuestas=datos_for_010(causas=[]),
        )
        ws = self._wb(self._xlsx(registro))['SGC-FOR-010']
        for fila in range(52, 58):
            self.assertIsNone(ws[f'A{fila}'].value)
            self.assertIsNone(ws[f'F{fila}'].value)
        self.assertEqual(ws['F58'].value, 0)
        self.assertFalse(ws['G58'].value)

    def test_bloque_corto_limpia_filas_sobrantes(self):
        registro = self._registro(
            respuestas=datos_for_010(descripcion_nc='Descripción breve.'),
        )
        ws = self._wb(self._xlsx(registro))['SGC-FOR-010']
        self.assertEqual(ws['D11'].value, 'Descripción breve.')
        # D12/D13 traen texto de ejemplo en la plantilla sintética.
        self.assertIsNone(ws['D12'].value)
        self.assertIsNone(ws['D13'].value)

    def test_desborde_marca_continuacion_en_anexo(self):
        muchas = '\n'.join(f'Línea {i}' for i in range(120))
        registro = self._registro(
            respuestas=datos_for_010(descripcion_nc=muchas),
        )
        data = self._xlsx(registro)
        ws = self._wb(data)['SGC-FOR-010']
        self.assertIn('ver anexo', str(ws['D13'].value))
        self.assertIn('SGC-FOR-010 (Anexo)', wb_sheetnames(data))

    def test_fila_parcial_no_conserva_el_ejemplo(self):
        registro = self._registro(respuestas=datos_for_010(causas=[
            {'descripcion': 'Causa sin incidencia', 'incidencia': ''},
        ]))
        data = self._xlsx(registro)
        ws = self._wb(data)['SGC-FOR-010']
        self.assertEqual(ws['A52'].value, 'Causa sin incidencia')
        self.assertIsNone(ws['F52'].value)
        self.assertEqual(ws['F58'].value, 0)
        # La fórmula de porcentaje de la fila usada sigue intacta.
        wb = openpyxl.load_workbook(BytesIO(data))
        self.assertTrue(
            str(wb['SGC-FOR-010']['G52'].value).startswith('=IFERROR'),
        )

    def test_plantilla_sintetica_incluye_vinculo_externo(self):
        ruta = os.path.join(TEST_MEDIA, 'for010_sintetica.xlsx')
        with zipfile.ZipFile(ruta) as z:
            self.assertTrue(
                any('externalLinks/' in n for n in z.namelist()),
            )

    def test_export_quita_vinculos_externos(self):
        z = zipfile.ZipFile(BytesIO(self._xlsx()))
        self.assertFalse([
            n for n in z.namelist() if n.startswith('xl/externalLinks/')
        ])
        wb_xml = z.read('xl/workbook.xml').decode('utf-8')
        self.assertNotIn('externalReferences', wb_xml)
        self.assertNotIn('[1]', wb_xml)
        rels = z.read('xl/_rels/workbook.xml.rels').decode('utf-8')
        self.assertNotIn('externalLink', rels)
        tipos = z.read('[Content_Types].xml').decode('utf-8')
        self.assertNotIn('externalLink', tipos)
        self.assertIn('SGC-IDL-016', wb_sheetnames(self._xlsx()))

    def test_bloque_largo_reparte_y_ajusta_altura(self):
        largo = (
            'El proceso de auditoría interna no evidencia la determinación '
            'de indicadores de gestión ni el seguimiento de los hallazgos '
            'previos. '
        ) * 10
        registro = self._registro(
            respuestas=datos_for_010(descripcion_nc=largo),
        )
        wb = openpyxl.load_workbook(
            BytesIO(self._xlsx(registro)), data_only=True,
        )
        ws = wb['SGC-FOR-010']
        self.assertIn('D11:G11', {str(r) for r in ws.merged_cells.ranges})
        self.assertGreater(ws.row_dimensions[11].height, 15)
        unido = ' '.join(
            ' '.join(str(ws[f'D{fila}'].value or '').split())
            for fila in (11, 12, 13)
        )
        self.assertIn('determinación de indicadores', unido)

    def test_desborde_va_al_anexo_sin_truncar(self):
        enorme = 'Hallazgo repetido. ' * 800  # ~15.200 caracteres
        registro = self._registro(
            respuestas=datos_for_010(descripcion_nc=enorme),
        )
        data = self._xlsx(registro)
        wb = openpyxl.load_workbook(BytesIO(data))
        anexo = [
            nombre for nombre in wb.sheetnames
            if nombre.endswith('(Anexo)')
        ]
        self.assertEqual(anexo, ['SGC-FOR-010 (Anexo)'])
        ws = wb[anexo[0]]
        completo = '\n'.join(
            str(ws[f'A{fila}'].value or '')
            for fila in range(1, ws.max_row + 1)
        )
        self.assertIn(enorme, completo)

    def test_casillas_de_deteccion(self):
        data = self._xlsx()
        z = zipfile.ZipFile(BytesIO(data))
        dibujo = z.read('xl/drawings/drawing1.xml').decode('utf-8')
        self.assertEqual(dibujo.count('00A651'), 2)
        for nombre in (
            'Rectángulo 70', 'Rectángulo 71', 'Rectángulo 79',
            'Rectángulo 80',
        ):
            self.assertIn(nombre, dibujo)

    def test_grafico_pareto_y_idl_preservados(self):
        data = self._xlsx()
        z = zipfile.ZipFile(BytesIO(data))
        self.assertIn('xl/charts/chart1.xml', z.namelist())
        self.assertIn(b'PARETO', z.read('xl/charts/chart1.xml'))
        self.assertIn('xl/drawings/drawing1.xml', z.namelist())
        wb = openpyxl.load_workbook(BytesIO(data))
        self.assertIn('SGC-IDL-016', wb.sheetnames)
        self.assertIn('PAGINA', wb['SGC-IDL-016']['B2'].value)

    def test_export_para_pdf_oculta_instructivo_y_fija_area(self):
        data = self._xlsx(para_pdf=True)
        z = zipfile.ZipFile(BytesIO(data))
        wb = openpyxl.load_workbook(BytesIO(data))
        self.assertIn('SGC-IDL-016', wb.sheetnames)
        self.assertEqual(wb['SGC-IDL-016'].sheet_state, 'hidden')
        self.assertEqual(wb['SGC-FOR-010'].sheet_state, 'visible')
        wb_xml = z.read('xl/workbook.xml').decode('utf-8')
        self.assertIn('_xlnm.Print_Area', wb_xml)
        self.assertIn('fullCalcOnLoad="1"', wb_xml)
        hoja = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
        self.assertIn('fitToPage="1"', hoja)
        self.assertIn('fitToWidth="1"', hoja)

    def test_servicio_pdf_guarda_archivo(self):
        registro = self._registro()
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(PDF)
            pdf_path = tmp.name
        try:
            with patch(
                'documentos.tasks.convertir_a_pdf_sync',
                return_value=pdf_path,
            ):
                ruta = regsvc.exportar_registro_pdf(registro)
        finally:
            if os.path.exists(pdf_path):
                os.unlink(pdf_path)
        registro.refresh_from_db()
        self.assertTrue(ruta)
        self.assertTrue(registro.archivo_pdf.name)

    def test_servicio_pdf_resuelve_ruta_relativa_a_media(self):
        registro = self._registro()
        relativa = os.path.join('registros', 'relativo.pdf')
        absoluta = os.path.join(TEST_MEDIA, relativa)
        os.makedirs(os.path.dirname(absoluta), exist_ok=True)
        with open(absoluta, 'wb') as fh:
            fh.write(PDF)
        with patch(
            'documentos.tasks.convertir_a_pdf_sync',
            return_value=relativa,
        ):
            ruta = regsvc.exportar_registro_pdf(registro)
        registro.refresh_from_db()
        self.assertTrue(ruta)
        with registro.archivo_pdf.open('rb') as fh:
            self.assertEqual(fh.read(), PDF)
        self.assertFalse(os.path.exists(absoluta))


class ValidacionTest(BaseFor010Test):
    def _errores(self, causas, parcial=True):
        q = QueryDict('', mutable=True)
        q['campo_causas'] = json.dumps(causas, ensure_ascii=False)
        _, errores = validar_respuestas(self.formato, q, parcial=parcial)
        return errores

    def test_tope_de_seis_causas(self):
        causas = [
            {'descripcion': f'Causa {i}', 'incidencia': 1}
            for i in range(7)
        ]
        errores = self._errores(causas)
        self.assertTrue(any('máximo 6' in e for e in errores))

    def test_seis_causas_es_valido(self):
        causas = [
            {'descripcion': f'Causa {i}', 'incidencia': i}
            for i in range(6)
        ]
        self.assertFalse(any('máximo' in e for e in self._errores(causas)))

    def test_incidencia_no_numerica(self):
        errores = self._errores([
            {'descripcion': 'Causa', 'incidencia': 'abc'},
        ])
        self.assertTrue(any('entero mayor o igual a 0' in e for e in errores))

    def test_incidencia_negativa_o_decimal(self):
        for valor in ('-5', '2.5'):
            errores = self._errores([
                {'descripcion': 'Causa', 'incidencia': valor},
            ])
            self.assertTrue(
                any('entero mayor o igual a 0' in e for e in errores),
                valor,
            )

    def test_incidencia_valida_se_normaliza(self):
        q = QueryDict('', mutable=True)
        q['campo_causas'] = json.dumps([
            {'descripcion': 'Causa', 'incidencia': ' 42 '},
        ])
        respuestas, errores = validar_respuestas(self.formato, q, parcial=True)
        self.assertFalse([
            e for e in errores if 'entero' in e or 'máximo' in e
        ])
        self.assertEqual(respuestas['causas'][0]['incidencia'], 42)

    def test_deteccion_obligatoria(self):
        q = QueryDict('', mutable=True)
        _, errores = validar_respuestas(self.formato, q, parcial=False)
        self.assertTrue(any('Detección' in e for e in errores))

    def test_validar_guardadas_detecta_tope(self):
        causas = [
            {'descripcion': f'Causa {i}', 'incidencia': 1}
            for i in range(7)
        ]
        errores = validar_respuestas_guardadas(
            self.formato, {'causas': causas},
        )
        self.assertTrue(any('máximo 6' in e for e in errores))


class VersionadoTest(BaseFor010Test):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user_admin)

    def _post_data(self, **overrides):
        datos = datos_for_010(**overrides)
        data = {
            'formato': self.formato.pk,
            'contexto_tipo': 'documento',
            'contexto_id': str(self.doc_activo.uuid),
            'titulo': 'Plan de acción NC-2026-001',
            'descripcion': '',
            'fecha': '2026-11-18',
        }
        for clave, valor in datos.items():
            if isinstance(valor, list):
                data[f'campo_{clave}'] = json.dumps(valor, ensure_ascii=False)
            else:
                data[f'campo_{clave}'] = valor
        data['campo_deteccion'] = list(datos['deteccion'])
        return data

    def _crear(self):
        respuesta = self.client.post(
            reverse('documentos:registro_crear'), self._post_data(),
        )
        self.assertEqual(respuesta.status_code, 302)
        return RegistroFormato.objects.get(formato=self.formato)

    def test_alta_crea_version_uno_y_la_muestra(self):
        registro = self._crear()
        versiones = VersionRegistroFormato.objects.filter(registro=registro)
        self.assertEqual(versiones.count(), 1)
        self.assertEqual(versiones.first().numero, 1)
        self.assertIn('Alta', versiones.first().resumen)
        detalle = self.client.get(reverse(
            'documentos:registro_detalle', args=[registro.uuid],
        ))
        self.assertContains(detalle, 'Historial de versiones')

    def test_editar_sin_cambios_no_versiona(self):
        registro = self._crear()
        respuesta = self.client.post(
            reverse('documentos:registro_editar', args=[registro.uuid]),
            self._post_data(),
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            VersionRegistroFormato.objects.filter(registro=registro).count(),
            1,
        )

    def test_editar_con_cambios_versiona(self):
        registro = self._crear()
        data = self._post_data(
            descripcion_nc='Se corrigió la descripción del hallazgo.',
        )
        respuesta = self.client.post(
            reverse('documentos:registro_editar', args=[registro.uuid]),
            data,
        )
        self.assertEqual(respuesta.status_code, 302)
        versiones = VersionRegistroFormato.objects.filter(
            registro=registro,
        ).order_by('numero')
        self.assertEqual(versiones.count(), 2)
        self.assertIn('Edición', versiones.last().resumen)
        registro.refresh_from_db()
        self.assertIn('corrigió', registro.respuestas['descripcion_nc'])

    def test_editar_invalida_xlsx_subido(self):
        registro = self._crear()
        registro.archivo.save(
            'manual.xlsx',
            SimpleUploadedFile('manual.xlsx', b'PK\x03\x04contenido'),
            save=True,
        )
        self.assertTrue(registro.archivo)
        data = self._post_data(
            descripcion_nc='Otra descripción con cambios.',
        )
        self.client.post(
            reverse('documentos:registro_editar', args=[registro.uuid]),
            data,
        )
        registro.refresh_from_db()
        self.assertFalse(registro.archivo)
        self.assertFalse(registro.archivo_pdf)

    def test_editar_con_archivo_nuevo_y_cambios_conserva_el_nuevo(self):
        registro = self._crear()
        registro.archivo.save(
            'anterior.pdf',
            SimpleUploadedFile('anterior.pdf', PDF), save=True,
        )
        viejo = registro.archivo.name
        data = self._post_data(
            descripcion_nc='Otra descripción con cambios.',
        )
        data['archivo'] = SimpleUploadedFile(
            'nuevo.xlsx', b'PK\x03\x04nuevo',
        )
        respuesta = self.client.post(
            reverse('documentos:registro_editar', args=[registro.uuid]),
            data,
        )
        self.assertEqual(respuesta.status_code, 302)
        registro.refresh_from_db()
        self.assertTrue(registro.archivo.name.endswith('.xlsx'))
        with registro.archivo.open('rb') as fh:
            self.assertEqual(fh.read(), b'PK\x03\x04nuevo')
        # El archivo anterior no queda huérfano en el almacenamiento.
        self.assertFalse(registro.archivo.storage.exists(viejo))
        self.assertFalse(registro.archivo_pdf)
        self.assertEqual(
            VersionRegistroFormato.objects.filter(registro=registro).count(),
            2,
        )
