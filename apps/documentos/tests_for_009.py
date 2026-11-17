"""Tests de la Minuta de reunión (SGC-FOR-009)."""
import io
import json
import os
import tempfile
import uuid
from datetime import date
from unittest.mock import MagicMock, patch

import openpyxl

from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import (
    AccesoFormato, Formato, RegistroFormato, VersionRegistroFormato,
)
from documentos.services import for_007 as for_007
from documentos.services import for_009 as svc
from documentos.services import registros as regsvc
from documentos.services import versiones as versiones_svc
from documentos.tests_sgc_utils import (
    crear_plantilla_for_007, crear_plantilla_for_009,
)

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_for009_')
PDF = b'%PDF-1.4\n%prueba\n'


def datos_minuta(**overrides):
    datos = {
        'reunion_no': '001',
        'objetivo': 'Revisar el SGC del primer trimestre.',
        'lugar': 'Sala de reuniones',
        'responsable_elaboracion': 'Ana Altuve',
        'duracion': '2 horas',
        'tipo_reunion': 'revision',
        'anio': '2026',
        'mes': 'MAR',
        'asistentes': [
            {'nombre': 'Ana Altuve', 'cargo': 'Gerente General',
             'departamento': 'GG'},
            {'nombre': 'Sheyla Cardenas', 'cargo': 'Coordinadora SGC',
             'departamento': 'GG'},
            {'nombre': 'Luis Pérez', 'cargo': 'Jefe TI',
             'departamento': 'TI'},
        ],
        'tema': 'Indicadores y hallazgos del trimestre.',
        'conclusiones': 'El SGC se mantiene eficaz.',
        'acuerdos_cerrados': 'Cerrar hallazgo 01.',
        'acuerdos_pendientes': 'Actualizar la política de calidad.',
        'responsables_aprobacion': 'Ana Altuve / Sheyla Cardenas',
    }
    datos.update(overrides)
    return datos


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseFor009Test(BaseDocumentosTest):
    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        p7 = crear_plantilla_for_007(
            os.path.join(TEST_MEDIA, 'for007_sintetica.xlsx'),
        )
        p9 = crear_plantilla_for_009(
            os.path.join(TEST_MEDIA, 'for009_sintetica.xlsx'),
        )
        call_command(
            'integrar_formatos_sgc', solo='007', apply=True, verbosity=0,
            plantilla=p7, stdout=io.StringIO(),
        )
        call_command(
            'integrar_formatos_sgc', solo='009', apply=True, verbosity=0,
            plantilla=p9, stdout=io.StringIO(),
        )
        cls.formato = Formato.objects.get(codigo='SGC-FOR-009')

    def setUp(self):
        super().setUp()
        self._mongo = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo.start()
        self.addCleanup(self._mongo.stop)
        self.client = Client()

    def _crear(self, **overrides):
        return svc.crear_minuta(
            fecha=overrides.pop('fecha', date(2026, 3, 15)),
            respuestas=datos_minuta(**overrides),
            usuario=self.user_admin,
        )

    def _post_data(self, **overrides):
        datos = datos_minuta(**overrides)
        data = {'fecha': '2026-03-15'}
        for clave, valor in datos.items():
            if clave == 'asistentes':
                data['campo_asistentes'] = json.dumps(valor)
            else:
                data[f'campo_{clave}'] = valor
        return data


class IntegracionTest(BaseFor009Test):
    def test_formato_y_config(self):
        self.assertEqual(self.formato.nombre, 'Minuta de Reunión')
        self.assertTrue(self.formato.plantilla_xlsx)
        config = svc.config_asistentes(self.formato)
        self.assertEqual(config['max_filas'], 7)
        self.assertEqual(len(config['columnas']), 3)
        self.assertEqual(len(svc.MESES), 12)
        from documentos.models import CampoFormato

        campo = CampoFormato.objects.get(
            seccion__formato=self.formato, clave='mes',
        )
        self.assertEqual(len(campo.opciones), 12)

    def test_crear_minuta(self):
        registro = self._crear()
        self.assertEqual(registro.contexto_tipo, 'minuta')
        self.assertEqual(registro.estado, 'borrador')
        self.assertEqual(registro.titulo, 'Minuta 001 — 15/03/2026')
        self.assertEqual(
            registro.respuestas['responsables_aprobacion'],
            'Ana Altuve / Sheyla Cardenas',
        )

    def test_reunion_no_invalido(self):
        with self.assertRaises(ValueError):
            svc.validar_reunion_no('abc')
        with self.assertRaises(ValueError):
            svc.validar_reunion_no('12A')
        self.assertEqual(svc.validar_reunion_no('7'), '7')
        self.assertEqual(svc.validar_reunion_no('001 - npr'), '001 - NPR')
        self.assertEqual(svc.validar_reunion_no('001-NPR'), '001 - NPR')

    def test_responsables_sugeridos(self):
        self.assertEqual(svc.responsables_sugeridos(), '')
        self._crear()
        self.assertEqual(
            svc.responsables_sugeridos(), 'Ana Altuve / Sheyla Cardenas',
        )
        self._crear(
            fecha=date(2026, 4, 1), reunion_no='002',
            responsables_aprobacion='Otro responsable',
        )
        self.assertEqual(svc.responsables_sugeridos(), 'Otro responsable')


class VistasTest(BaseFor009Test):
    def test_crear_desde_vista(self):
        self.client.force_login(self.user_admin)
        response = self.client.post(
            reverse('documentos:minuta_crear'), self._post_data(),
        )
        self.assertEqual(response.status_code, 302)
        registro = RegistroFormato.objects.get(contexto_tipo='minuta')
        self.assertEqual(registro.titulo, 'Minuta 001 — 15/03/2026')
        self.assertEqual(
            response.url,
            reverse('documentos:minuta_detalle',
                    kwargs={'minuta_uuid': registro.uuid}),
        )

    def test_reunion_no_invalido_en_vista(self):
        self.client.force_login(self.user_admin)
        response = self.client.post(
            reverse('documentos:minuta_crear'),
            self._post_data(reunion_no='XYZ'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            RegistroFormato.objects.filter(contexto_tipo='minuta').exists(),
        )

    def test_requiere_asistentes(self):
        self.client.force_login(self.user_admin)
        response = self.client.post(
            reverse('documentos:minuta_crear'),
            self._post_data(asistentes=[]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            RegistroFormato.objects.filter(contexto_tipo='minuta').exists(),
        )

    def test_marcar_ejecutado_for_007(self):
        programa = for_007.programa_anual(
            2026, crear=True, usuario=self.user_admin,
        )
        aspectos = for_007.aspecto(programa)
        aspectos[0].setdefault('marcas', {})['MAR'] = 'P'
        for_007.guardar_programa(programa, aspectos, usuario=self.user_admin)

        self.client.force_login(self.user_admin)
        data = self._post_data()
        data['marcar_ejecutado'] = '1'
        response = self.client.post(reverse('documentos:minuta_crear'), data)
        self.assertEqual(response.status_code, 302)
        programa.refresh_from_db()
        self.assertEqual(
            for_007.aspecto(programa)[0]['marcas']['MAR'], 'V',
        )

    def test_lista_y_detalle(self):
        registro = self._crear()
        self.client.force_login(self.user_admin)
        response = self.client.get(reverse('documentos:minutas_lista'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Minuta 001')
        response = self.client.get(
            reverse('documentos:minuta_detalle',
                    kwargs={'minuta_uuid': registro.uuid}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ana Altuve')

    def test_acceso_ver_y_editar(self):
        self.client.force_login(self.user_trabajador)
        response = self.client.get(reverse('documentos:minutas_lista'))
        self.assertEqual(response.status_code, 302)

        from documentos.services import acceso_formato

        acceso_formato.otorgar(
            self.user_trabajador, self.formato, nivel='ver',
            otorgado_por=self.user_admin,
        )
        response = self.client.get(reverse('documentos:minutas_lista'))
        self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse('documentos:minuta_crear'))
        self.assertEqual(response.status_code, 302)

        acceso_formato.otorgar(
            self.user_trabajador, self.formato, nivel='editar',
            otorgado_por=self.user_admin,
        )
        response = self.client.get(reverse('documentos:minuta_crear'))
        self.assertEqual(response.status_code, 200)

    def test_no_aparece_en_registros_genericos(self):
        self._crear()
        visibles = regsvc.registros_visibles(self.user_admin)
        self.assertFalse(
            visibles.filter(contexto_tipo='minuta').exists(),
        )


class ExportTest(BaseFor009Test):
    def _libro(self, registro, forzar=True):
        if forzar:
            registro.refresh_from_db()
        return openpyxl.load_workbook(io.BytesIO(svc.generar_xlsx(registro)))

    def test_valores_y_merges(self):
        registro = self._crear()
        wb = self._libro(registro)
        hoja = wb['SGC-FOR-009']
        self.assertEqual(hoja['B10'].value, '001')
        self.assertEqual(
            hoja['C13'].value, 'Revisar el SGC del primer trimestre.',
        )
        self.assertEqual(hoja['B16'].value, 'Sala de reuniones')
        self.assertEqual(hoja['B18'].value, '15/03/2026')
        self.assertEqual(hoja['B20'].value, 'Ana Altuve')
        self.assertEqual(hoja['B22'].value, '2 horas')
        self.assertEqual(hoja['A26'].value, 'Ana Altuve')
        self.assertEqual(hoja['D26'].value, 'Gerente General')
        self.assertEqual(hoja['G26'].value, 'GG')
        self.assertIn('Indicadores', hoja['A33'].value)
        self.assertIn('eficaz', hoja['A38'].value)
        self.assertIn('hallazgo', hoja['A46'].value)
        self.assertIn('política', hoja['A54'].value)
        self.assertIn('Sheyla', hoja['A65'].value)
        self.assertEqual(hoja['I6'].value, 'PAGINA: 1 DE 1')
        self.assertIn('C13:J14', {str(r) for r in hoja.merged_cells.ranges})
        self.assertIn('B16:F16', {str(r) for r in hoja.merged_cells.ranges})
        self.assertIn('SGC-IDL-015', wb.sheetnames)

    def test_continuacion_asistentes(self):
        asistentes = [
            {'nombre': f'Persona {i}', 'cargo': 'Cargo', 'departamento': 'GG'}
            for i in range(1, 11)
        ]
        registro = self._crear(asistentes=asistentes)
        wb = self._libro(registro)
        self.assertEqual(wb.sheetnames[:2],
                         ['SGC-FOR-009', 'SGC-FOR-009 (2)'])
        primera, segunda = wb['SGC-FOR-009'], wb['SGC-FOR-009 (2)']
        self.assertEqual(primera['A26'].value, 'Persona 1')
        self.assertEqual(primera['A32'].value, 'Persona 7')
        self.assertEqual(segunda['A26'].value, 'Persona 8')
        self.assertEqual(segunda['A28'].value, 'Persona 10')
        self.assertEqual(primera['I6'].value, 'PAGINA: 1 DE 2')
        self.assertEqual(segunda['I6'].value, 'PAGINA: 2 DE 2')
        self.assertEqual(segunda['B10'].value, '001')
        self.assertIn('Continuación', segunda['A33'].value)
        self.assertEqual(wb['SGC-IDL-015']['B2'].value, 'PAGINA: 1 DE 2')

    def test_generar_y_guardar_con_pdf(self):
        registro = self._crear()
        with patch(
            'documentos.services.xlsx_comun.xlsx_bytes_a_pdf',
            return_value=PDF,
        ):
            resultado = svc.generar_y_guardar(registro, forzar=True)
        self.assertTrue(resultado['success'])
        registro.refresh_from_db()
        self.assertTrue(registro.archivo.name)
        self.assertTrue(registro.archivo_pdf.name)

    def test_guardar_invalida_archivos(self):
        registro = self._crear()
        with patch(
            'documentos.services.xlsx_comun.xlsx_bytes_a_pdf',
            return_value=PDF,
        ):
            svc.generar_y_guardar(registro, forzar=True)
        registro.refresh_from_db()
        self.assertTrue(registro.archivo)
        svc.guardar_minuta(
            registro, datos_minuta(objetivo='Objetivo actualizado'),
            fecha=date(2026, 3, 16), usuario=self.user_admin,
        )
        registro.refresh_from_db()
        self.assertFalse(registro.archivo)
        self.assertFalse(registro.archivo_pdf)

    def test_descarga_genera_para_editor(self):
        registro = self._crear()
        self.client.force_login(self.user_admin)
        with patch(
            'documentos.services.xlsx_comun.xlsx_bytes_a_pdf',
            return_value=PDF,
        ):
            response = self.client.get(
                reverse('documentos:minuta_descargar',
                        kwargs={'minuta_uuid': registro.uuid,
                                'formato': 'pdf'}),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_ver_sin_archivo_no_genera(self):
        registro = self._crear()
        from documentos.services import acceso_formato

        acceso_formato.otorgar(
            self.user_trabajador, self.formato, nivel='ver',
            otorgado_por=self.user_admin,
        )
        self.client.force_login(self.user_trabajador)
        response = self.client.get(
            reverse('documentos:minuta_descargar',
                    kwargs={'minuta_uuid': registro.uuid, 'formato': 'xlsx'}),
        )
        self.assertEqual(response.status_code, 302)
        registro.refresh_from_db()
        self.assertFalse(registro.archivo)


class AccesoAdminTest(BaseFor009Test):
    def test_acceso_page(self):
        self.client.force_login(self.user_admin)
        response = self.client.get(reverse('documentos:minuta_acceso'))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse('documentos:minuta_acceso'),
            {'usuario_id': self.user_trabajador.id, 'nivel': 'ver'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AccesoFormato.objects.filter(
                usuario=self.user_trabajador, formato=self.formato,
                nivel='ver', activo=True,
            ).exists(),
        )

    def test_no_admin_no_gestiona(self):
        self.client.force_login(self.user_trabajador)
        response = self.client.get(reverse('documentos:minuta_acceso'))
        self.assertEqual(response.status_code, 302)


class VersionadoTest(BaseFor009Test):
    def test_crear_versiona(self):
        registro = self._crear()
        self.assertEqual(
            VersionRegistroFormato.objects.filter(registro=registro).count(),
            1,
        )
        self.assertEqual(versiones_svc.ultima_version(registro).numero, 1)

    def test_editar_versiona_y_noop_no_duplica(self):
        registro = self._crear()
        svc.guardar_minuta(
            registro, datos_minuta(objetivo='Objetivo actualizado'),
            fecha=date(2026, 3, 16), usuario=self.user_admin,
        )
        self.assertEqual(
            VersionRegistroFormato.objects.filter(registro=registro).count(),
            2,
        )
        svc.guardar_minuta(
            registro, datos_minuta(objetivo='Objetivo actualizado'),
            fecha=date(2026, 3, 16), usuario=self.user_admin,
        )
        self.assertEqual(
            VersionRegistroFormato.objects.filter(registro=registro).count(),
            2,
        )

    def test_detalle_muestra_historial(self):
        registro = self._crear()
        self.client.force_login(self.user_admin)
        response = self.client.get(
            reverse('documentos:minuta_detalle',
                    kwargs={'minuta_uuid': registro.uuid}),
        )
        self.assertContains(response, 'Historial de versiones')
        self.assertContains(response, 'Alta de minuta')


class PermisoHookTest(BaseFor009Test):
    def _programa_con_planificado(self):
        programa = for_007.programa_anual(
            2026, crear=True, usuario=self.user_admin,
        )
        aspectos = for_007.aspecto(programa)
        aspectos[0].setdefault('marcas', {})['MAR'] = 'P'
        for_007.guardar_programa(programa, aspectos, usuario=self.user_admin)
        return programa

    def test_editor_de_minuta_sin_for_007_no_modifica_programa(self):
        programa = self._programa_con_planificado()
        from documentos.services import acceso_formato

        acceso_formato.otorgar(
            self.user_trabajador, self.formato, nivel='editar',
            otorgado_por=self.user_admin,
        )
        self.client.force_login(self.user_trabajador)
        response = self.client.get(reverse('documentos:minuta_crear'))
        self.assertNotContains(response, 'name="marcar_ejecutado"')

        data = self._post_data()
        data['marcar_ejecutado'] = '1'
        response = self.client.post(reverse('documentos:minuta_crear'), data)
        self.assertEqual(response.status_code, 302)
        programa.refresh_from_db()
        self.assertEqual(
            for_007.aspecto(programa)[0]['marcas']['MAR'], 'P',
        )

    def test_hook_audita_programa(self):
        self._programa_con_planificado()
        self.client.force_login(self.user_admin)
        data = self._post_data()
        data['marcar_ejecutado'] = '1'
        with patch('documentos.views._auditar_programa') as audit:
            response = self.client.post(
                reverse('documentos:minuta_crear'), data,
            )
        self.assertEqual(response.status_code, 302)
        audit.assert_called_once()
        args = audit.call_args.args
        self.assertEqual(args[2], 'programa_revision_ejecutado_desde_minuta')
        self.assertIn('minuta_uuid', args[3])


class MergeSeguroTest(BaseFor009Test):
    def test_merge_existente_no_se_solapa(self):
        import openpyxl

        wb = openpyxl.Workbook()
        hoja = wb.active
        hoja.merge_cells('B18:D18')
        svc._merge_si_falta(hoja, 'B18:C18')
        rangos = {str(r) for r in hoja.merged_cells.ranges}
        self.assertIn('B18:D18', rangos)
        self.assertNotIn('B18:C18', rangos)

    def test_no_escribe_en_merged_cell(self):
        import openpyxl

        wb = openpyxl.Workbook()
        hoja = wb.active
        hoja.merge_cells('A1:B1')
        self.assertIsNone(svc._escribir_celda(hoja, 'B1', 'x'))
        self.assertIsNone(hoja['A1'].value)


class TextoLargoTest(BaseFor009Test):
    def test_altura_de_bloque_crece(self):
        registro = self._crear(tema=' '.join(['detalle'] * 250))
        wb = openpyxl.load_workbook(
            io.BytesIO(svc.generar_xlsx(registro)),
        )
        hoja = wb['SGC-FOR-009']
        self.assertGreater(hoja.row_dimensions[36].height or 15, 15)
        self.assertIn('detalle', hoja['A33'].value)

    def test_desborde_va_a_anexo(self):
        registro = self._crear(
            responsables_aprobacion=' '.join(['palabra'] * 3000),
        )
        wb = openpyxl.load_workbook(
            io.BytesIO(svc.generar_xlsx(registro)),
        )
        self.assertIn('SGC-FOR-009 (Anexo)', wb.sheetnames)
        anexo = wb['SGC-FOR-009 (Anexo)']
        texto = ' '.join(
            str(celda.value or '')
            for fila in anexo.iter_rows() for celda in fila
        )
        self.assertIn('palabra', texto)


class OrdenPermisosAnioTest(BaseFor009Test):
    def test_sin_acceso_no_enumera(self):
        registro = self._crear()
        self.client.force_login(self.user_trabajador)
        for identificador in (registro.uuid, uuid.uuid4()):
            response = self.client.get(
                reverse('documentos:minuta_detalle',
                        kwargs={'minuta_uuid': identificador}),
            )
            self.assertEqual(response.status_code, 302)
            response = self.client.get(
                reverse('documentos:minuta_descargar',
                        kwargs={'minuta_uuid': identificador,
                                'formato': 'xlsx'}),
            )
            self.assertEqual(response.status_code, 404)
            response = self.client.get(
                reverse('documentos:minuta_editar',
                        kwargs={'minuta_uuid': identificador}),
            )
            self.assertEqual(response.status_code, 302)

    def test_anio_fuera_de_rango(self):
        self.client.force_login(self.user_admin)
        for anio in ('1999', '2101', 'abc'):
            response = self.client.post(
                reverse('documentos:minuta_crear'),
                self._post_data(anio=anio),
            )
            self.assertEqual(response.status_code, 200)
        self.assertFalse(
            RegistroFormato.objects.filter(contexto_tipo='minuta').exists(),
        )
        self.assertEqual(svc.validar_anio('2026'), 2026)
        with self.assertRaises(ValueError):
            svc.validar_anio('1800')
