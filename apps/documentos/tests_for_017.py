"""Tests de la Descripción de Procesos (SGC-FOR-017).

Cubre la integración del formato real (comando), el export por parcheo XML
(matriz con merge C:E, fila 59 creada desde el estilo de la 58, rótulos de
firma conservados, fechas dd/mm/aaaa), la validación de topes de filas, el
PDF a 2 páginas y el versionado de registros.
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
from django.core.management import call_command
from django.http import QueryDict
from django.test import Client, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.forms import validar_respuestas
from documentos.models import (
    CampoFormato, Formato, RegistroFormato, VersionRegistroFormato,
)
from documentos.tests_sgc_utils import crear_plantilla_for_017

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_for017_')


def wb_sheetnames(data):
    """Nombres de las hojas del XLSX exportado."""
    return openpyxl.load_workbook(BytesIO(data)).sheetnames


def datos_for_017(**overrides):
    datos = {
        'codigo_proceso': 'PH-DPR-01',
        'nombre_proceso': 'Descripción de Procesos',
        'tipo_proceso': 'contratado',
        'dueno_proceso': 'Ana Pérez\nGerente de Procesos',
        'participa': 'Jefe de Servicios\nCoordinador de Calidad',
        'cliente': 'Gerencia General',
        'objetivo_general': (
            'Describir los procesos de la organización para determinar su '
            'secuencia e interacción.'
        ),
        'alcance': 'Todos los procesos de la Policlínica Hospitalización.',
        'proceso': [
            {'fuentes': 'Solicitud del cliente', 'entradas': 'Requisitos',
             'secuencia': 'Recibir y registrar la solicitud',
             'responsables': 'Coordinador', 'salidas': 'Orden registrada'},
            {'fuentes': 'Normativa', 'entradas': 'Leyes aplicables',
             'secuencia': 'Verificar el cumplimiento normativo',
             'responsables': 'Auditor', 'salidas': 'Informe de verificación'},
            {'fuentes': 'Plan anual', 'entradas': 'Cronograma',
             'secuencia': 'Programar las actividades del proceso',
             'responsables': 'Planificador', 'salidas': 'Plan aprobado'},
            {'fuentes': 'Indicadores', 'entradas': 'Resultados',
             'secuencia': 'Revisar el desempeño del proceso',
             'responsables': 'Coordinador', 'salidas': 'Acciones de mejora'},
        ],
        'receptores_proceso': 'Gerencia General',
        'recursos': 'Personal, infraestructura y tecnología',
        'informacion_documentada': 'Procedimientos, formularios e '
                                   'instrucciones de trabajo',
        'interaccion_procesos': 'Interactúa con el proceso de auditoría '
                                'interna y con la revisión por la dirección.',
        'indicadores': [
            {'nombre': 'Descripciones actualizadas',
             'objetivo': 'Mantener el 100% de los procesos descritos y '
                         'vigentes',
             'formula': '(procesos descritos / procesos totales) * 100',
             'frecuencia': 'Semestral',
             'responsable': 'Coordinador de Calidad'},
            {'nombre': 'Revisión de procesos',
             'objetivo': 'Revisar todos los procesos en el año',
             'formula': 'procesos revisados / 12',
             'frecuencia': 'Mensual',
             'responsable': 'Métodos y Procesos'},
        ],
        'elaborado_nombre': 'Anna Altuve',
        'elaborado_cargo': 'Métodos y Procesos',
        'elaborado_fecha': '2026-11-18',
        'revisado_nombre': 'Sheyla Cardenas',
        'revisado_cargo': 'Gerente General',
        'revisado_fecha': '2026-11-18',
        'aprobado_nombre': 'Sheyla Cardenas',
        'aprobado_cargo': 'Gerente General',
        'aprobado_fecha': '2026-11-18',
    }
    datos.update(overrides)
    return datos


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseFor017Test(BaseDocumentosTest):
    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        plantilla = crear_plantilla_for_017(
            os.path.join(TEST_MEDIA, 'for017_sintetica.xlsx'),
        )
        with patch(
            'documentos.tasks.convertir_formato_a_pdf.delay',
            return_value=None,
        ), patch(
            'documentos.tasks.convertir_formato_a_pdf_sync',
            return_value=None,
        ):
            call_command(
                'integrar_formatos_sgc', solo='017', apply=True,
                verbosity=0, plantilla=plantilla, stdout=io.StringIO(),
            )
        cls.formato = Formato.objects.get(codigo='SGC-FOR-017')

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
        datos.setdefault('titulo', 'Descripción de procesos PH-DPR-01')
        datos.setdefault('fecha', date(2026, 11, 18))
        datos.setdefault('responsable', self.user_admin)
        datos.setdefault('estado', 'borrador')
        datos.setdefault(
            'respuestas',
            datos_for_017() if respuestas is None else respuestas,
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

    def _wb(self, data, data_only=True):
        return openpyxl.load_workbook(BytesIO(data), data_only=data_only)

    def _ws(self, data=None):
        return self._wb(self._xlsx() if data is None else data)[
            'SGC-FOR-017'
        ]

    def _merges(self, ws):
        return {str(rango) for rango in ws.merged_cells.ranges}


class IntegracionTest(BaseFor017Test):
    def test_metadata_del_formato(self):
        formato = self.formato
        self.assertEqual(formato.nombre, 'Descripción de Procesos')
        self.assertEqual(formato.version, '1')
        self.assertEqual(formato.edicion, '1')
        self.assertEqual(formato.fecha_vigencia, date(2025, 10, 24))
        self.assertEqual(
            formato.procedimiento,
            'SISTEMA DE GESTION DE LA CALIDAD Y SUS PROCESOS',
        )
        self.assertTrue(formato.permite_registros)
        self.assertFalse(formato.aplica_creacion)
        self.assertFalse(formato.aplica_modificacion)
        self.assertFalse(formato.aplica_eliminacion)
        # El HTML del instructivo viene partido en líneas: se colapsan los
        # espacios antes de buscar los literales.
        texto = ' '.join(formato.instructivo_html.split())
        self.assertIn('Instructivo de llenado', texto)
        self.assertIn('PH: Policlínica Hospitalización', texto)
        self.assertIn('DPR: Descripción del Proceso', texto)
        # Introducción ISO 9001 con los literales a) … i).
        self.assertIn('a) Determinar las entradas requeridas', texto)
        self.assertIn('i) Mantener la información documentada', texto)
        # 26 instrucciones + 9 literales de la introducción.
        self.assertGreaterEqual(formato.instructivo_html.count('<li>'), 35)

    def test_estructura_y_celdas(self):
        campos = {
            c.clave: c for c in CampoFormato.objects.filter(
                seccion__formato=self.formato,
            )
        }
        self.assertEqual(len(campos), 23)
        self.assertEqual(campos['codigo_proceso'].celda_excel, 'A9')
        self.assertEqual(campos['nombre_proceso'].celda_excel, 'C9')
        self.assertEqual(campos['dueno_proceso'].celda_excel, 'A11:B13')
        self.assertEqual(campos['objetivo_general'].celda_excel, 'A15:H16')
        self.assertEqual(campos['alcance'].celda_excel, 'A18:H20')
        self.assertEqual(campos['proceso'].celda_excel, 'A22')
        self.assertEqual(campos['receptores_proceso'].celda_excel, 'H24')
        self.assertEqual(campos['recursos'].celda_excel, 'A51:H51')
        self.assertEqual(campos['indicadores'].celda_excel, 'A58')
        self.assertEqual(campos['elaborado_nombre'].celda_excel, 'A61')
        self.assertEqual(campos['elaborado_cargo'].celda_excel, 'A63')
        self.assertEqual(campos['elaborado_fecha'].celda_excel, 'A67')
        self.assertEqual(campos['revisado_fecha'].celda_excel, 'C67')
        self.assertEqual(campos['aprobado_fecha'].celda_excel, 'F67')
        for clave in (
            'codigo_proceso', 'nombre_proceso', 'tipo_proceso',
            'dueno_proceso', 'objetivo_general', 'alcance', 'proceso',
            'receptores_proceso',
        ):
            self.assertTrue(campos[clave].requerido, clave)
        self.assertEqual(campos['tipo_proceso'].tipo, 'select')
        marcas = {
            o['valor']: o['celda_marca']
            for o in campos['tipo_proceso'].opciones
        }
        self.assertEqual(marcas, {
            'contratado': 'F9', 'no_contratado': 'G9', 'mixto': 'H9',
        })

    def test_config_tablas(self):
        proceso = CampoFormato.objects.get(
            seccion__formato=self.formato, clave='proceso',
        ).config_tabla
        self.assertEqual(proceso['fila_inicio'], 22)
        self.assertEqual(proceso['fila_fin'], 48)
        self.assertEqual(proceso['max_filas'], 27)
        self.assertTrue(proceso['limpiar_filas'])
        secuencia = [
            c for c in proceso['columnas'] if c['clave'] == 'secuencia'
        ][0]
        self.assertEqual(secuencia['celda'], 'C')
        self.assertEqual(secuencia['merge'], 'C:E')
        self.assertTrue(secuencia['wrap'])

        indicadores = CampoFormato.objects.get(
            seccion__formato=self.formato, clave='indicadores',
        ).config_tabla
        self.assertEqual(indicadores['fila_inicio'], 58)
        self.assertEqual(indicadores['max_filas'], 2)
        self.assertTrue(indicadores['limpiar_filas'])
        self.assertEqual(indicadores['estilo_fila'], 58)
        merges = {
            c['clave']: c.get('merge')
            for c in indicadores['columnas']
        }
        self.assertEqual(merges['objetivo'], 'B:C')
        self.assertEqual(merges['formula'], 'D:E')
        self.assertEqual(merges['responsable'], 'G:H')

    def test_formulario_limita_filas_en_cliente(self):
        self.client.force_login(self.user_admin)
        url = reverse('documentos:registro_crear') + (
            f'?formato={self.formato.pk}&contexto_tipo=documento'
            f'&contexto_id={self.doc_activo.uuid}'
        )
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'var maxFilas = 27')
        self.assertContains(respuesta, 'var maxFilas = 2')

    def test_dry_run_no_escribe(self):
        Formato.objects.filter(codigo='SGC-FOR-017').delete()
        ruta = os.path.join(TEST_MEDIA, 'for017_sintetica.xlsx')
        salida = io.StringIO()
        call_command(
            'integrar_formatos_sgc', solo='017', verbosity=0,
            plantilla=ruta, stdout=salida,
        )
        self.assertFalse(Formato.objects.filter(codigo='SGC-FOR-017').exists())
        self.assertIn('dry-run', salida.getvalue())


class ExportTest(BaseFor017Test):
    def test_identificacion(self):
        ws = self._ws()
        self.assertEqual(ws['A9'].value, 'PH-DPR-01')
        self.assertEqual(ws['C9'].value, 'Descripción de Procesos')
        self.assertEqual(ws['F9'].value, 'X')
        self.assertIsNone(ws['G9'].value)
        self.assertIsNone(ws['H9'].value)

    def test_tipo_proceso_marca_su_caja(self):
        for valor, celda in (
            ('no_contratado', 'G9'), ('mixto', 'H9'),
        ):
            registro = self._registro(
                respuestas=datos_for_017(tipo_proceso=valor),
            )
            ws = self._wb(self._xlsx(registro))['SGC-FOR-017']
            self.assertEqual(ws[celda].value, 'X', valor)
            otras = [c for c in ('F9', 'G9', 'H9') if c != celda]
            for ref in otras:
                self.assertIsNone(ws[ref].value, f'{valor} → {ref}')

    def test_cajas_de_tres_lineas_se_mergen_por_fila(self):
        ws = self._ws()
        merges = self._merges(ws)
        # A11:B13 no viene mergeado en la plantilla: el export une cada fila
        # del rango, aunque la última quede vacía.
        for fila in (11, 12, 13):
            self.assertIn(f'A{fila}:B{fila}', merges)
        # Una línea por fila (CA-1) mientras quepa en el rango.
        self.assertEqual(ws['A11'].value, 'Ana Pérez')
        self.assertEqual(ws['A12'].value, 'Gerente de Procesos')
        self.assertIsNone(ws['A13'].value)
        # Las cajas de la plantilla que ya venían mergeadas no cambian.
        self.assertIn('C11:E11', merges)
        self.assertIn('F13:H13', merges)
        self.assertEqual(ws['C11'].value, 'Jefe de Servicios')
        self.assertEqual(ws['C12'].value, 'Coordinador de Calidad')
        self.assertEqual(ws['F11'].value, 'Gerencia General')

    def test_objetivo_alcance_y_bloques(self):
        ws = self._ws()
        merges = self._merges(ws)
        self.assertIn('secuencia e interacción', ws['A15'].value)
        self.assertIn('Policlínica Hospitalización', ws['A18'].value)
        # Los bloques de la plantilla se conservan mergeados.
        self.assertIn('A15:H16', merges)
        self.assertIn('A18:H20', merges)
        self.assertIn('A51:H51', merges)
        self.assertIn('Personal, infraestructura', ws['A51'].value)
        self.assertIn('auditoría interna', ws['A55'].value)

    def test_matriz_con_merge_wrap_y_altura(self):
        registro = self._registro(respuestas=datos_for_017(proceso=[
            {'fuentes': 'Fuente', 'entradas': 'Entrada',
             'secuencia': (
                 'Recibir la solicitud del cliente, verificar la '
                 'documentación de soporte, registrar la orden en el '
                 'sistema y notificar al área responsable para su '
                 'tramitación oportuna'
             ),
             'responsables': 'Coordinador', 'salidas': 'Orden registrada'},
        ]))
        ws = self._wb(self._xlsx(registro))['SGC-FOR-017']
        merges = self._merges(ws)
        self.assertEqual(ws['A22'].value, 'Fuente')
        self.assertEqual(ws['B22'].value, 'Entrada')
        self.assertIn('Recibir la solicitud', ws['C22'].value)
        self.assertEqual(ws['F22'].value, 'Coordinador')
        self.assertEqual(ws['G22'].value, 'Orden registrada')
        # C:E unido en TODAS las filas de la matriz (datos y sobrantes).
        for fila in (22, 30, 48):
            self.assertIn(f'C{fila}:E{fila}', merges)
        # wrap en la celda escrita y altura crecida (no la de la plantilla).
        self.assertTrue(ws['C22'].alignment.wrapText)
        self.assertGreater(ws.row_dimensions[22].height, 15)

    def test_alturas_de_la_plantilla_no_se_aplastan(self):
        ws = self._ws()
        self.assertEqual(ws.row_dimensions[24].height, 121.5)
        self.assertEqual(ws.row_dimensions[25].height, 63.75)
        # Las filas 24/25 traen datos del registro de ejemplo.
        self.assertEqual(ws['A24'].value, 'Plan anual')
        self.assertEqual(ws['A25'].value, 'Indicadores')
        self.assertEqual(ws.row_dimensions[24].height, 121.5)
        self.assertEqual(ws.row_dimensions[25].height, 63.75)

    def test_receptores_conserva_merge_vertical_y_wrap(self):
        ws = self._ws()
        merges = self._merges(ws)
        self.assertEqual(ws['H24'].value, 'Gerencia General')
        self.assertIn('H24:H47', merges)
        self.assertTrue(ws['H24'].alignment.wrapText)

    def test_indicadores_crea_la_fila_59_con_estilos_y_merges(self):
        ws = self._ws()
        merges = self._merges(ws)
        self.assertEqual(ws['A58'].value, 'Descripciones actualizadas')
        self.assertEqual(ws['B58'].value,
                         'Mantener el 100% de los procesos descritos y vigentes')
        self.assertEqual(ws['A59'].value, 'Revisión de procesos')
        self.assertEqual(ws['D59'].value, 'procesos revisados / 12')
        self.assertEqual(ws['F59'].value, 'Mensual')
        self.assertEqual(ws['G59'].value, 'Métodos y Procesos')
        for ref in ('B59:C59', 'D59:E59', 'G59:H59'):
            self.assertIn(ref, merges)
        # La fila 59 copia el estilo (bordes) de la 58.
        for col in 'ABCDEFGH':
            self.assertEqual(
                ws[f'{col}59'].border.left.style,
                ws[f'{col}58'].border.left.style,
                col,
            )
            self.assertEqual(
                ws[f'{col}59'].border.bottom.style,
                ws[f'{col}58'].border.bottom.style,
                col,
            )

    def test_sin_indicadores_la_fila_59_queda_vacia_perfilada(self):
        registro = self._registro(
            respuestas=datos_for_017(indicadores=[]),
        )
        ws = self._wb(self._xlsx(registro))['SGC-FOR-017']
        for col in 'ABCDEFGH':
            self.assertIsNone(ws[f'{col}59'].value, col)
            self.assertEqual(
                ws[f'{col}59'].border.left.style,
                ws[f'{col}58'].border.left.style,
                col,
            )
        self.assertIn('B59:C59', self._merges(ws))

    def test_firmas_conservan_rotulo_y_fecha_dma(self):
        ws = self._ws()
        self.assertTrue(ws['A61'].value.startswith('Nombre y Apellido:'))
        self.assertIn('Anna Altuve', ws['A61'].value)
        self.assertTrue(ws['C61'].value.startswith('Nombre y Apellido:'))
        self.assertIn('Sheyla Cardenas', ws['F61'].value)
        self.assertTrue(ws['A63'].value.startswith('Cargo:'))
        self.assertIn('Métodos y Procesos', ws['A63'].value)
        self.assertIn('Gerente General', ws['C63'].value)
        # Fecha oficial dd/mm/aaaa (no ISO).
        self.assertEqual(ws['A67'].value, 'Fecha: 18/11/2026')
        self.assertEqual(ws['C67'].value, 'Fecha: 18/11/2026')
        self.assertEqual(ws['F67'].value, 'Fecha: 18/11/2026')

    def test_export_conserva_hoja_instructiva(self):
        data = self._xlsx()
        self.assertIn('SGC-IDL-023', wb_sheetnames(data))
        z = zipfile.ZipFile(BytesIO(data))
        self.assertFalse([
            n for n in z.namelist() if n.startswith('xl/externalLinks/')
        ])

    def test_export_para_pdf_oculta_idl_y_conserva_escala(self):
        data = self._xlsx(para_pdf=True)
        z = zipfile.ZipFile(BytesIO(data))
        wb = self._wb(data)
        self.assertIn('SGC-IDL-023', wb.sheetnames)
        self.assertEqual(wb['SGC-IDL-023'].sheet_state, 'hidden')
        self.assertEqual(wb['SGC-FOR-017'].sheet_state, 'visible')
        # Sin ajuste forzado: manda el pageSetup de la plantilla (2 páginas).
        wb_xml = z.read('xl/workbook.xml').decode('utf-8')
        self.assertNotIn('_xlnm.Print_Area', wb_xml)
        hoja = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
        self.assertNotIn('fitToWidth', hoja)
        self.assertIn('scale="70"', hoja)

    def test_plantilla_sintetica_sin_fila_59(self):
        ruta = os.path.join(TEST_MEDIA, 'for017_sintetica.xlsx')
        hoja = zipfile.ZipFile(ruta).read('xl/worksheets/sheet1.xml')
        self.assertNotIn(b'<row r="59"', hoja)
        self.assertIn(b'H24:H47', hoja)


class ValidacionTest(BaseFor017Test):
    def _errores(self, **valores):
        q = QueryDict('', mutable=True)
        for clave, valor in valores.items():
            q[f'campo_{clave}'] = (
                json.dumps(valor, ensure_ascii=False)
                if isinstance(valor, list) else str(valor)
            )
        _, errores = validar_respuestas(self.formato, q, parcial=True)
        return errores

    def test_tope_de_27_filas_de_proceso(self):
        filas = [
            {'fuentes': f'F{i}', 'entradas': '', 'secuencia': '',
             'responsables': '', 'salidas': ''}
            for i in range(28)
        ]
        errores = self._errores(proceso=filas)
        self.assertTrue(any('máximo 27' in e for e in errores))

    def test_veintisiete_filas_es_valido(self):
        filas = [
            {'fuentes': f'F{i}', 'entradas': '', 'secuencia': '',
             'responsables': '', 'salidas': ''}
            for i in range(27)
        ]
        self.assertFalse(any('máximo' in e for e in self._errores(proceso=filas)))

    def test_tope_de_dos_indicadores(self):
        indicadores = [
            {'nombre': f'I{i}', 'objetivo': '', 'formula': '',
             'frecuencia': '', 'responsable': ''}
            for i in range(3)
        ]
        errores = self._errores(indicadores=indicadores)
        self.assertTrue(any('máximo 2' in e for e in errores))

    def test_campos_requeridos_en_alta(self):
        q = QueryDict('', mutable=True)
        _, errores = validar_respuestas(self.formato, q, parcial=False)
        requeridos = [
            'Código del proceso', 'Nombre del proceso', 'Tipo de proceso',
            'Dueño (líder) del proceso', 'Objetivo general', 'Alcance',
            'Proceso', 'Receptores del proceso',
        ]
        for etiqueta in requeridos:
            self.assertTrue(
                any(etiqueta in e for e in errores), etiqueta,
            )


class VersionadoTest(BaseFor017Test):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user_admin)

    def _post_data(self, **overrides):
        datos = datos_for_017(**overrides)
        data = {
            'formato': self.formato.pk,
            'contexto_tipo': 'documento',
            'contexto_id': str(self.doc_activo.uuid),
            'titulo': 'Descripción de procesos PH-DPR-01',
            'descripcion': '',
            'fecha': '2026-11-18',
        }
        for clave, valor in datos.items():
            if isinstance(valor, list):
                data[f'campo_{clave}'] = json.dumps(valor, ensure_ascii=False)
            else:
                data[f'campo_{clave}'] = valor
        return data

    def _crear(self):
        respuesta = self.client.post(
            reverse('documentos:registro_crear'), self._post_data(),
        )
        self.assertEqual(respuesta.status_code, 302)
        return RegistroFormato.objects.get(formato=self.formato)

    def test_alta_crea_version_uno(self):
        registro = self._crear()
        versiones = VersionRegistroFormato.objects.filter(registro=registro)
        self.assertEqual(versiones.count(), 1)
        self.assertEqual(versiones.first().numero, 1)
        self.assertIn('Alta', versiones.first().resumen)

    def test_editar_con_cambios_versiona(self):
        registro = self._crear()
        data = self._post_data(
            alcance='Solo los procesos administrativos.',
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
        self.assertIn('administrativos', registro.respuestas['alcance'])
