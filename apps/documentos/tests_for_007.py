"""Tests del Programa de Revisión por la Dirección (SGC-FOR-007)."""
import io
import os
import tempfile
from unittest.mock import MagicMock, patch

import openpyxl

from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import Formato, RegistroFormato, VersionRegistroFormato
from documentos.services import for_007 as svc
from documentos.tests_sgc_utils import crear_plantilla_for_007

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_for007_')
PDF = b'%PDF-1.4\n%prueba\n'


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseFor007Test(BaseDocumentosTest):
    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        plantilla = crear_plantilla_for_007(
            os.path.join(TEST_MEDIA, 'for007_sintetica.xlsx'),
        )
        call_command(
            'integrar_formatos_sgc', solo='007', apply=True, verbosity=0,
            plantilla=plantilla, stdout=io.StringIO(),
        )
        cls.formato = Formato.objects.get(codigo='SGC-FOR-007')

    def setUp(self):
        super().setUp()
        self._mongo = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo.start()
        self.addCleanup(self._mongo.stop)
        self.client = Client()

    def _programa(self, anio=2026, **kwargs):
        return svc.programa_anual(anio, crear=True, usuario=self.user_admin,
                                  **kwargs)

    def _aspectos(self, registro):
        return svc.aspecto(registro)


class IntegracionTest(BaseFor007Test):
    def test_formato_y_config(self):
        self.assertEqual(
            self.formato.nombre, 'Programa de Revisión por la Dirección',
        )
        self.assertTrue(self.formato.plantilla_xlsx)
        config = svc.config_for_007(self.formato)
        self.assertEqual(len(config['seed']), 14)
        self.assertEqual(config['max_filas_hoja'], 30)
        self.assertEqual(len(config['meses']), 12)


class ProgramaTest(BaseFor007Test):
    def test_crear_seed_oficial(self):
        registro = self._programa(2026)
        self.assertEqual(registro.estado, 'borrador')
        self.assertEqual(len(self._aspectos(registro)), 14)
        self.assertEqual(registro.respuestas['anio'], 2026)
        self.assertEqual(svc.anio_del_programa(registro), 2026)
        self.assertEqual(
            VersionRegistroFormato.objects.filter(registro=registro).count(), 1,
        )

    def test_crear_usa_anio_anterior(self):
        r2026 = self._programa(2026)
        r2026.respuestas = {
            **r2026.respuestas,
            'aspectos': self._aspectos(r2026)[:5],
        }
        r2026.save(update_fields=['respuestas'])
        r2027 = self._programa(2027)
        self.assertEqual(len(self._aspectos(r2027)), 5)
        self.assertEqual(
            self._aspectos(r2027)[0]['etiqueta'],
            self._aspectos(r2026)[0]['etiqueta'],
        )

    def test_crear_con_anio_elegido(self):
        r2024 = self._programa(2024)
        r2024.respuestas = {
            **r2024.respuestas, 'aspectos': self._aspectos(r2024)[:3],
        }
        r2024.save(update_fields=['respuestas'])
        r2025 = self._programa(2025)
        self.assertEqual(len(self._aspectos(r2025)), 3)
        self.assertEqual(len(self._aspectos(self._programa(2026))), 3)
        # El año elegido manda.
        r2028 = self._programa(2028, seed_anio=2024)
        self.assertEqual(len(self._aspectos(r2028)), 3)

    def test_unicidad_anual(self):
        r1 = self._programa(2026)
        r2 = self._programa(2026)
        self.assertEqual(r1.pk, r2.pk)
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            RegistroFormato.objects.create(
                formato=self.formato, formato_codigo=self.formato.codigo,
                contexto_tipo='programa_revision', contexto_id='2026',
                titulo='duplicado', responsable=self.user_admin,
            )

    def test_marcas_v_implica_p(self):
        registro = self._programa(2026)
        aspectos = self._aspectos(registro)
        aspectos[0]['marcas'] = {'ENE': 'V'}
        resultado = svc.guardar_programa(
            registro, aspectos, usuario=self.user_admin,
        )
        self.assertTrue(resultado['cambio'])
        registro.refresh_from_db()
        fila = self._aspectos(registro)[0]
        self.assertEqual(fila['marcas']['ENE'], 'V')
        self.assertIn('planificado_en', fila['meta']['ENE'])
        self.assertIn('ejecutado_en', fila['meta']['ENE'])

    def test_sin_cambios_no_crea_version(self):
        registro = self._programa(2026)
        aspectos = self._aspectos(registro)
        antes = VersionRegistroFormato.objects.filter(registro=registro).count()
        resultado = svc.guardar_programa(
            registro, aspectos, usuario=self.user_admin,
        )
        self.assertFalse(resultado['cambio'])
        self.assertEqual(
            VersionRegistroFormato.objects.filter(registro=registro).count(),
            antes,
        )

    def test_publicar_y_editar_vuelve_borrador(self):
        registro = self._programa(2026)
        version = svc.publicar_programa(registro, usuario=self.user_admin)
        self.assertIsNotNone(version)
        registro.refresh_from_db()
        self.assertEqual(registro.estado, 'aprobado')

        aspectos = self._aspectos(registro)
        aspectos[0]['marcas'] = {'FEB': 'P'}
        svc.guardar_programa(registro, aspectos, usuario=self.user_admin)
        registro.refresh_from_db()
        self.assertEqual(registro.estado, 'borrador')

    def test_cargar_seed_conserva_marcas_por_clave(self):
        r2024 = self._programa(2024)
        r2024.respuestas = {
            **r2024.respuestas, 'aspectos': self._aspectos(r2024)[:3],
        }
        r2024.save(update_fields=['respuestas'])
        registro = self._programa(2026)
        # 2026 se sembró desde 2024; lo llevamos a 5 para que la copia cambie.
        base = self._aspectos(registro)
        cinco = [dict(b) for b in base]
        while len(cinco) < 5:
            cinco.append({**base[0], 'clave': f'extra_{len(cinco)}'})
        registro.respuestas = {**registro.respuestas, 'aspectos': cinco}
        registro.save(update_fields=['respuestas'])
        aspectos = self._aspectos(registro)
        aspectos[0]['marcas'] = {'ENE': 'P'}
        svc.guardar_programa(registro, aspectos, usuario=self.user_admin)
        registro.refresh_from_db()

        resultado = svc.cargar_seed(registro, 2024, usuario=self.user_admin)
        self.assertTrue(resultado['cambio'])
        registro.refresh_from_db()
        nuevos = self._aspectos(registro)
        self.assertEqual(len(nuevos), 3)
        self.assertEqual(nuevos[0]['marcas'].get('ENE'), 'P')

    def test_marcar_ejecutado(self):
        registro = self._programa(2026)
        aspectos = self._aspectos(registro)
        aspectos[0]['marcas'] = {'MAR': 'P'}
        svc.guardar_programa(registro, aspectos, usuario=self.user_admin)
        n = svc.marcar_ejecutado(2026, 'MAR', usuario=self.user_admin)
        self.assertEqual(n, 1)
        registro.refresh_from_db()
        fila = self._aspectos(registro)[0]
        self.assertEqual(fila['marcas']['MAR'], 'V')
        self.assertIn('ejecutado_en', fila['meta']['MAR'])

    def test_marcar_ejecutado_conserva_filas_nuevas(self):
        registro = self._programa(2026)
        aspectos = self._aspectos(registro)
        aspectos[0]['marcas'] = {'MAR': 'P'}
        aspectos.append({
            'clave': 'nueva', 'etiqueta': 'Aspecto agregado',
            'informacion': 'Información nueva', 'marcas': {}, 'meta': {},
        })
        svc.guardar_programa(registro, aspectos, usuario=self.user_admin)
        n = svc.marcar_ejecutado(2026, 'MAR', usuario=self.user_admin)
        self.assertEqual(n, 1)
        registro.refresh_from_db()
        claves = [a['clave'] for a in self._aspectos(registro)]
        self.assertIn('nueva', claves)

    def test_marcar_ejecutado_exige_permiso_editar(self):
        self._programa(2026)
        with self.assertRaises(PermissionError):
            svc.marcar_ejecutado(
                2026, 'MAR', usuario=self.user_trabajador,
            )


class ExportTest(BaseFor007Test):
    def _programa_con(self, n):
        registro = self._programa(2026)
        base = self._aspectos(registro)
        filas = []
        for i in range(n):
            original = base[i % len(base)]
            filas.append({
                **original, 'clave': f'x{i}',
                'marcas': {'ENE': 'P'} if i == 0 else {},
                'meta': {},
            })
        registro.respuestas = {**registro.respuestas, 'aspectos': filas}
        registro.save(update_fields=['respuestas'])
        return registro

    def test_n14_layout(self):
        registro = self._programa_con(14)
        wb = openpyxl.load_workbook(io.BytesIO(svc.generar_xlsx(registro)))
        self.assertEqual(wb.sheetnames, ['SGC-FOR-007', 'SGC-IDL-013'])
        ws = wb['SGC-FOR-007']
        self.assertEqual(ws['A10'].value, 'AÑO A EVALUAR: 2026')
        self.assertIn('a) El estado', ws['A14'].value)
        self.assertEqual(ws['H14'].value, 'P')
        self.assertIn('Planificado', ws['B29'].value)
        self.assertEqual(ws['A31'].value, 'CONFORME')
        self.assertIn('Presidente', ws['A34'].value)
        self.assertEqual(ws['L6'].value, 'PAGINA:   1 DE 1')

    def test_n3_mueve_leyenda_y_firmas(self):
        registro = self._programa_con(3)
        ws = openpyxl.load_workbook(
            io.BytesIO(svc.generar_xlsx(registro)),
        )['SGC-FOR-007']
        self.assertIn('Planificado', ws['B18'].value)
        self.assertEqual(ws['A20'].value, 'CONFORME')
        self.assertIn('Presidente', ws['A23'].value)

    def test_n31_continuacion(self):
        registro = self._programa_con(31)
        wb = openpyxl.load_workbook(io.BytesIO(svc.generar_xlsx(registro)))
        self.assertEqual(
            wb.sheetnames,
            ['SGC-FOR-007', 'SGC-FOR-007 (2)', 'SGC-IDL-013'],
        )
        self.assertEqual(wb['SGC-FOR-007']['L6'].value, 'PAGINA:   1 DE 2')
        self.assertEqual(wb['SGC-FOR-007 (2)']['L6'].value, 'PAGINA:   2 DE 2')
        self.assertTrue(wb['SGC-FOR-007 (2)']['A14'].value)
        self.assertIn('Planificado', wb['SGC-FOR-007 (2)']['B16'].value)
        self.assertEqual(wb['SGC-FOR-007 (2)']['A18'].value, 'CONFORME')

    def test_conforme_nombres_y_fecha(self):
        registro = self._programa_con(2)
        registro.respuestas = {
            **registro.respuestas,
            'conforme': {
                'presidente': 'Ana Altuve', 'gerente': 'Juan Pérez',
                'fecha': '2026-11-16',
            },
        }
        registro.save(update_fields=['respuestas'])
        ws = openpyxl.load_workbook(
            io.BytesIO(svc.generar_xlsx(registro)),
        )['SGC-FOR-007']
        # M=2 -> leyenda 17, CONFORME 19, firmas 20, etiquetas 22.
        self.assertEqual(ws['A20'].value, 'Ana Altuve')
        self.assertEqual(ws['E20'].value, 'Juan Pérez')
        self.assertEqual(ws['I20'].value, '16')
        self.assertEqual(ws['K20'].value, '11')
        self.assertEqual(ws['M20'].value, '2026')

    def test_generar_y_guardar(self):
        registro = self._programa_con(2)
        with patch('documentos.services.xlsx_comun.xlsx_bytes_a_pdf',
                   return_value=PDF):
            resultado = svc.generar_y_guardar(registro, forzar=True)
        self.assertTrue(resultado['success'])
        registro.refresh_from_db()
        self.assertTrue(registro.archivo)
        self.assertTrue(registro.archivo_pdf)


class VistaTest(BaseFor007Test):
    def _crear(self, anio=2026):
        self.client.force_login(self.user_admin)
        return self.client.post(
            reverse('documentos:programa_revision_crear'), {'anio': anio},
        )

    def test_sin_acceso_redirige(self):
        self.client.force_login(self.user_trabajador)
        resp = self.client.get(
            reverse('documentos:programa_revision'), {'anio': 2026},
        )
        self.assertEqual(resp.status_code, 302)

    def test_admin_ve_crear_y_crea(self):
        self.client.force_login(self.user_admin)
        resp = self.client.get(
            reverse('documentos:programa_revision'), {'anio': 2026},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Crear Programa')

        resp = self._crear(2026)
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(
            reverse('documentos:programa_revision'), {'anio': 2026},
        )
        self.assertContains(resp, 'Programa de Revisión por la Dirección')
        self.assertContains(resp, 'aspectos_json')

    def test_guardar_via_post(self):
        self._crear(2026)
        import json as _json

        aspectos = [
            {'clave': 'k1', 'etiqueta': 'Aspecto A', 'informacion': 'Doc A',
             'marcas': {'ENE': 'V'}},
        ]
        resp = self.client.post(
            reverse('documentos:programa_revision_guardar'), {
                'anio': 2026,
                'aspectos_json': _json.dumps(aspectos),
                'presidente': 'Ana', 'gerente': 'Juan',
                'fecha': '2026-11-16', 'accion': 'guardar',
            },
        )
        self.assertEqual(resp.status_code, 302)
        registro = svc.programa_anual(2026)
        filas = svc.aspecto(registro)
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['marcas']['ENE'], 'V')
        self.assertEqual(
            registro.respuestas['conforme']['presidente'], 'Ana',
        )
        self.assertGreaterEqual(
            VersionRegistroFormato.objects.filter(registro=registro).count(), 2,
        )

    def test_publicar_via_post(self):
        self._crear(2026)
        import json as _json

        with patch('documentos.services.xlsx_comun.xlsx_bytes_a_pdf',
                   return_value=PDF):
            resp = self.client.post(
                reverse('documentos:programa_revision_guardar'), {
                    'anio': 2026,
                    'aspectos_json': _json.dumps([
                        {'clave': 'k1', 'etiqueta': 'A', 'informacion': 'B',
                         'marcas': {}},
                    ]),
                    'accion': 'publicar',
                },
            )
        self.assertEqual(resp.status_code, 302)
        registro = svc.programa_anual(2026)
        self.assertEqual(registro.estado, 'aprobado')

    def test_copiar_anio(self):
        r2024 = self._programa(2024)
        r2024.respuestas = {
            **r2024.respuestas, 'aspectos': svc.aspecto(r2024)[:3],
        }
        r2024.save(update_fields=['respuestas'])
        self._crear(2026)
        resp = self.client.post(
            reverse('documentos:programa_revision_copiar'),
            {'anio': 2026, 'seed_anio': 2024},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(svc.aspecto(svc.programa_anual(2026))), 3)

    def test_parametros_admin(self):
        self.client.force_login(self.user_admin)
        resp = self.client.get(reverse('documentos:programa_revision_parametros'))
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(
            reverse('documentos:programa_revision_parametros'), {
                'seed_etiqueta_0': 'Nuevo aspecto',
                'seed_informacion_0': 'Nueva info',
                'seed_etiqueta_1': 'Otro',
                'seed_informacion_1': '',
                'max_filas_hoja': 25,
            },
        )
        self.assertEqual(resp.status_code, 302)
        config = svc.config_for_007(self.formato)
        self.assertEqual(len(config['seed']), 2)
        self.assertEqual(config['max_filas_hoja'], 25)

        self.client.force_login(self.user_trabajador)
        resp = self.client.get(reverse('documentos:programa_revision_parametros'))
        self.assertEqual(resp.status_code, 302)

    def test_acceso_ver_no_edita(self):
        self._crear(2026)
        self.client.force_login(self.user_admin)
        resp = self.client.post(
            reverse('documentos:programa_revision_acceso'),
            {'usuario_id': self.user_trabajador.id, 'nivel': 'ver'},
        )
        self.assertEqual(resp.status_code, 302)

        self.client.force_login(self.user_trabajador)
        resp = self.client.get(
            reverse('documentos:programa_revision'), {'anio': 2026},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="form-programa"')

        import json as _json

        resp = self.client.post(
            reverse('documentos:programa_revision_guardar'), {
                'anio': 2026,
                'aspectos_json': _json.dumps([
                    {'clave': 'x', 'etiqueta': 'X', 'informacion': '',
                     'marcas': {}},
                ]),
                'accion': 'guardar',
            },
        )
        self.assertEqual(resp.status_code, 302)
        # No cambió (sin permiso de edición).
        self.assertEqual(len(svc.aspecto(svc.programa_anual(2026))), 14)

    def test_version_solo_lectura(self):
        self._crear(2026)
        import json as _json

        self.client.post(
            reverse('documentos:programa_revision_guardar'), {
                'anio': 2026,
                'aspectos_json': _json.dumps([
                    {'clave': 'k1', 'etiqueta': 'A', 'informacion': 'B',
                     'marcas': {'ENE': 'P'}},
                ]),
                'accion': 'guardar',
            },
        )
        registro = svc.programa_anual(2026)
        from documentos.services import versiones as sv
        version = sv.ultima_version(registro)
        resp = self.client.get(
            reverse('documentos:programa_revision_version',
                    args=[version.numero]),
            {'anio': 2026},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'Versión {version.numero}')
        self.assertNotContains(resp, 'id="form-programa"')

    def test_descargar_xlsx(self):
        self._crear(2026)
        with patch('documentos.services.xlsx_comun.xlsx_bytes_a_pdf',
                   return_value=PDF):
            self.client.post(
                reverse('documentos:programa_revision_generar'),
                {'anio': 2026},
            )
            resp = self.client.get(
                reverse('documentos:programa_revision_descargar',
                        args=['xlsx']),
                {'anio': 2026},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('attachment', resp['Content-Disposition'])


class HardeningFor007Test(BaseFor007Test):
    def test_ver_no_puede_regenerar(self):
        self._programa(2026)
        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('documentos:programa_revision_acceso'),
            {'usuario_id': self.user_trabajador.id, 'nivel': 'ver'},
        )
        self.client.force_login(self.user_trabajador)
        resp = self.client.get(
            reverse('documentos:programa_revision'), {'anio': 2026},
        )
        self.assertNotContains(resp, 'Regenerar')

        resp = self.client.post(
            reverse('documentos:programa_revision_generar'), {'anio': 2026},
        )
        self.assertEqual(resp.status_code, 302)
        registro = svc.programa_anual(2026)
        self.assertFalse(registro.archivo)

    def test_anio_invalido_no_crea_programa(self):
        self.client.force_login(self.user_admin)
        resp = self.client.post(
            reverse('documentos:programa_revision_guardar'), {
                'anio': 99999, 'aspectos_json': '[]', 'accion': 'guardar',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(
            RegistroFormato.objects.filter(contexto_id='99999').exists(),
        )
        # Tampoco se creó el año actual implícitamente.
        self.assertFalse(
            RegistroFormato.objects.filter(
                formato=self.formato, contexto_tipo='programa_revision',
            ).exists(),
        )

    def test_ver_sin_programa_del_anio_abre_ultimo(self):
        self._programa(2024)
        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('documentos:programa_revision_acceso'),
            {'usuario_id': self.user_trabajador.id, 'nivel': 'ver'},
        )
        self.client.force_login(self.user_trabajador)
        resp = self.client.get(reverse('documentos:programa_revision'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '2024')

    def test_ver_sin_ningun_programa_no_entra_en_bucle(self):
        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('documentos:programa_revision_acceso'),
            {'usuario_id': self.user_trabajador.id, 'nivel': 'ver'},
        )
        self.client.force_login(self.user_trabajador)
        resp = self.client.get(reverse('documentos:programa_revision'))
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('/programa-revision/', resp.url)

    def test_guardar_invalida_archivos(self):
        self._programa(2026)
        with patch('documentos.services.xlsx_comun.xlsx_bytes_a_pdf',
                   return_value=PDF):
            registro = svc.programa_anual(2026)
            svc.generar_y_guardar(registro, forzar=True)
        registro.refresh_from_db()
        self.assertTrue(registro.archivo)

        import json as _json

        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('documentos:programa_revision_guardar'), {
                'anio': 2026,
                'aspectos_json': _json.dumps([
                    {'clave': 'k1', 'etiqueta': 'A', 'informacion': 'B',
                     'marcas': {'ENE': 'P'}},
                ]),
                'accion': 'guardar',
            },
        )
        registro.refresh_from_db()
        self.assertFalse(registro.archivo)
        self.assertFalse(registro.archivo_pdf)

    def test_descarga_ver_sin_archivo_no_genera(self):
        self._programa(2026)
        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('documentos:programa_revision_acceso'),
            {'usuario_id': self.user_trabajador.id, 'nivel': 'ver'},
        )
        self.client.force_login(self.user_trabajador)
        resp = self.client.get(
            reverse('documentos:programa_revision_descargar', args=['xlsx']),
            {'anio': 2026},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(svc.programa_anual(2026).archivo)

    def test_parametros_indice_alto(self):
        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('documentos:programa_revision_parametros'), {
                'seed_etiqueta_900': 'Aspecto 900',
                'seed_informacion_900': 'Info 900',
                'max_filas_hoja': 30,
            },
        )
        config = svc.config_for_007(self.formato)
        self.assertEqual(len(config['seed']), 1)
        self.assertEqual(config['seed'][0]['etiqueta'], 'Aspecto 900')

    def test_parametros_sin_campo_no_rompe(self):
        from documentos.models import CampoFormato
        CampoFormato.objects.filter(
            seccion__formato=self.formato, clave='aspectos',
        ).delete()
        self.client.force_login(self.user_admin)
        resp = self.client.get(reverse('documentos:programa_revision_parametros'))
        self.assertEqual(resp.status_code, 302)

    def test_nosniff_descarga(self):
        self._programa(2026)
        with patch('documentos.services.xlsx_comun.xlsx_bytes_a_pdf',
                   return_value=PDF):
            registro = svc.programa_anual(2026)
            svc.generar_y_guardar(registro, forzar=True)
        self.client.force_login(self.user_admin)
        resp = self.client.get(
            reverse('documentos:programa_revision_descargar', args=['xlsx']),
            {'anio': 2026},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')
