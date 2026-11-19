"""Tests de la Lista Maestra de Documentos (SGC-FOR-006).

Fase 4: integración del formato y de la configuración de columnas (JSON).
Las fases siguientes amplían este módulo (registro vivo, signals, vista).
"""
import io
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import (
    CampoFormato, Documento, DocumentoSinCodigo, Formato,
)
from documentos.services import for_006 as svc
from documentos.tests_sgc_utils import (
    crear_plantilla_for_006, verificar_preparacion_pdf,
)

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_for006_')


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseFor006Test(BaseDocumentosTest):
    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        plantilla = crear_plantilla_for_006(
            os.path.join(TEST_MEDIA, 'for006_sintetica.xlsx'),
        )
        call_command(
            'integrar_formatos_sgc', solo='006', apply=True, verbosity=0,
            plantilla=plantilla, stdout=io.StringIO(),
        )
        cls.formato = Formato.objects.get(codigo='SGC-FOR-006')

    def setUp(self):
        super().setUp()
        self._mongo = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo.start()
        self.addCleanup(self._mongo.stop)
        self.client = Client()

    def _campo(self, formato=None):
        return CampoFormato.objects.get(
            seccion__formato=formato or self.formato, clave='documentos',
        )


class IntegracionFormatoTest(BaseFor006Test):
    def test_comando_crea_formato(self):
        self.assertEqual(self.formato.nombre, 'Lista Maestra de Documentos')
        self.assertFalse(self.formato.aplica_creacion)
        self.assertFalse(self.formato.aplica_modificacion)
        self.assertFalse(self.formato.aplica_eliminacion)
        self.assertFalse(self.formato.permite_registros)
        self.assertTrue(self.formato.plantilla_xlsx)

    def test_config_tabla_13_columnas(self):
        campo = self._campo()
        self.assertEqual(campo.tipo, 'tabla')
        config = campo.config_tabla
        self.assertEqual(config['fila_inicio'], 13)
        self.assertEqual(config['fila_fin'], 34)
        self.assertEqual(config['max_filas'], 22)
        self.assertTrue(config['hoja_continua'])

        columnas = config['columnas']
        self.assertEqual(len(columnas), 13)
        claves = [c['clave'] for c in columnas]
        self.assertEqual(claves[0], 'ext_int')
        self.assertEqual(claves[-1], 'empresa')
        self.assertEqual(
            claves,
            [
                'ext_int', 'codigo', 'titulo', 'uso', 'fecha', 'edicion',
                'version', 'permanencia_activo', 'permanencia_inactivo',
                'forma_archivo', 'distribucion', 'responsable', 'empresa',
            ],
        )

    def test_columnas_origen_y_celdas(self):
        columnas = {c['clave']: c for c in self._campo().config_tabla['columnas']}
        # Ext/Int usa dos celdas (marca si/no): Int=B, Ext=A.
        self.assertEqual(columnas['ext_int']['tipo'], 'si_no')
        self.assertEqual(columnas['ext_int']['celda_si'], 'B')
        self.assertEqual(columnas['ext_int']['celda_no'], 'A')
        # Código y título automáticos.
        self.assertEqual(columnas['codigo']['celda'], 'C')
        self.assertEqual(columnas['codigo']['origen'], 'codigo')
        self.assertEqual(columnas['titulo']['origen'], 'nombre')
        # Columnas manuales (sin origen): forma de archivo y distribución.
        self.assertNotIn('origen', columnas['forma_archivo'])
        self.assertNotIn('origen', columnas['distribucion'])
        # Combos configurables con opciones.
        self.assertEqual(columnas['empresa']['tipo'], 'combo')
        self.assertIn('Empresas del Grupo', columnas['empresa']['opciones'])

    def test_dry_run_no_crea(self):
        Formato.objects.filter(codigo='SGC-FOR-006').delete()
        plantilla = os.path.join(TEST_MEDIA, 'for006_sintetica.xlsx')
        call_command(
            'integrar_formatos_sgc', solo='006', verbosity=0,
            plantilla=plantilla, stdout=io.StringIO(),
        )
        self.assertFalse(Formato.objects.filter(codigo='SGC-FOR-006').exists())

    def test_reintegrar_conserva_estructura(self):
        campo = self._campo()
        campo.config_tabla['columnas'][0]['etiqueta'] = 'Editada'
        campo.save(update_fields=['config_tabla'])
        plantilla = os.path.join(TEST_MEDIA, 'for006_sintetica.xlsx')
        call_command(
            'integrar_formatos_sgc', solo='006', apply=True, verbosity=0,
            plantilla=plantilla, stdout=io.StringIO(),
        )
        self.assertEqual(
            self._campo().config_tabla['columnas'][0]['etiqueta'], 'Editada',
        )


    def test_constructor_renderiza_editor_de_columnas(self):
        self.client.force_login(self.user_admin)
        campo = self._campo()
        url = reverse(
            'documentos:campo_editar',
            args=[self.formato.id, campo.id],
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # El editor extendido expone tipo/opciones por columna.
        self.assertContains(resp, 'col-opciones')
        self.assertContains(resp, 'Combo libre + recientes')


class ServicioTest(BaseFor006Test):
    def setUp(self):
        super().setUp()
        self.registro = svc.registro_lista_maestra(
            crear=True, responsable=self.user_admin,
        )

    def _fila(self, clave):
        registro = svc.registro_lista_maestra()
        for fila in svc.filas(registro):
            if fila.get('_clave') == clave:
                return fila
        return None

    def _guardar(self, filas):
        self.registro.respuestas = {
            **(self.registro.respuestas or {}), 'documentos': filas,
        }
        self.registro.save(update_fields=['respuestas', 'actualizado'])

    def test_valores_auto_documento(self):
        valores = svc.valores_auto(self.doc_activo)
        self.assertEqual(valores['codigo'], self.doc_activo.codigo)
        self.assertEqual(valores['titulo'], self.doc_activo.nombre)
        self.assertEqual(valores['ext_int'], 'si')
        self.assertEqual(
            valores['responsable'],
            'Jefe de Departamento — TI - Tecnología de la Información',
        )

    def test_sync_inserta_documentos(self):
        resumen = svc.sincronizar(self.registro, usuario=self.user_admin)
        self.assertGreater(resumen['agregados'], 0)
        fila = self._fila(self.doc_activo.codigo)
        self.assertIsNotNone(fila)
        self.assertEqual(fila['titulo'], self.doc_activo.nombre)
        self.assertEqual(fila['ext_int'], 'si')

    def test_sync_no_incluye_plantillas_ni_obsoletos(self):
        svc.sincronizar(self.registro, usuario=self.user_admin)
        claves = {f['_clave'] for f in svc.filas(self.registro)}
        self.assertNotIn(self.doc_obsoleto.codigo, claves)
        self.assertNotIn(self.doc_borrador.codigo, claves)

    def test_combinar_respeta_manual(self):
        svc.sincronizar(self.registro, usuario=self.user_admin)
        registro = svc.registro_lista_maestra()
        filas = svc.filas(registro)
        for fila in filas:
            if fila.get('_clave') == self.doc_activo.codigo:
                fila['forma_archivo'] = 'Digital/ Carpeta SGC (editado)'
                fila['_manual'] = ['forma_archivo']
        registro.respuestas = {
            **(registro.respuestas or {}), 'documentos': filas,
        }
        registro.save(update_fields=['respuestas', 'actualizado'])

        Documento.objects.filter(pk=self.doc_activo.pk).update(
            nombre='Nombre cambiado',
        )
        svc.sincronizar(registro, usuario=self.user_admin)

        fila = self._fila(self.doc_activo.codigo)
        self.assertEqual(fila['titulo'], 'Nombre cambiado')  # auto actualizado
        self.assertEqual(
            fila['forma_archivo'], 'Digital/ Carpeta SGC (editado)',
        )  # manual preservado

    def test_combinar_quita_obsoletos(self):
        svc.sincronizar(self.registro, usuario=self.user_admin)
        self.assertIsNotNone(self._fila(self.doc_activo.codigo))
        Documento.objects.filter(pk=self.doc_activo.pk).update(estado='obsoleto')
        resumen = svc.sincronizar(self.registro, usuario=self.user_admin)
        self.assertGreaterEqual(resumen['eliminados'], 1)
        self.assertIsNone(self._fila(self.doc_activo.codigo))

    def test_sync_incluye_soporte_sgc_como_ext(self):
        soporte = DocumentoSinCodigo.objects.create(
            titulo='Factura proveedor', ambito='sgc',
            propietario=self.user_jefe_ti, empresa='Farmacia',
            responsable_texto='Junta Directiva',
        )
        svc.sincronizar(self.registro, usuario=self.user_admin)
        fila = self._fila(svc.clave_sin_codigo(soporte))
        self.assertIsNotNone(fila)
        self.assertEqual(fila['codigo'], 'N/A')
        self.assertEqual(fila['ext_int'], 'no')
        self.assertEqual(fila['empresa'], 'Farmacia')
        self.assertEqual(fila['responsable'], 'Junta Directiva')

    def test_signal_documento_inserta_y_quita(self):
        with self.captureOnCommitCallbacks(execute=True):
            documento = Documento.objects.create(
                tipo=self.tipo_formulario, nombre='Doc señal',
                codigo='TI-EXT-900', estado='activo',
                propietario=self.user_jefe_ti, departamento=self.depto_ti,
                version_actual='1.0',
            )
        self.assertIsNotNone(self._fila('TI-EXT-900'))

        with self.captureOnCommitCallbacks(execute=True):
            documento.estado = 'obsoleto'
            documento.save(update_fields=['estado'])
        self.assertIsNone(self._fila('TI-EXT-900'))

    def test_signal_sin_codigo_inserta(self):
        soporte = DocumentoSinCodigo.objects.create(
            titulo='Ticket', ambito='sgc', propietario=self.user_jefe_ti,
        )
        with self.captureOnCommitCallbacks(execute=True):
            soporte.save()
        self.assertIsNotNone(self._fila(svc.clave_sin_codigo(soporte)))


class GeneracionTest(BaseFor006Test):
    def _registro_con(self, filas):
        registro = svc.registro_lista_maestra(
            crear=True, responsable=self.user_admin,
        )
        registro.respuestas = {'documentos': filas}
        registro.save(update_fields=['respuestas', 'actualizado'])
        return registro

    def _fila(self, **kwargs):
        base = {
            '_clave': 'X-1', '_origen_tipo': 'documento', '_manual': [],
            'ext_int': 'si', 'codigo': 'TI-PRG-001', 'titulo': 'Procedimiento',
            'fecha': '2025-10-24', 'edicion': '1', 'version': '2',
            'empresa': 'Empresas del Grupo',
        }
        base.update(kwargs)
        return base

    def test_generar_xlsx_valores(self):
        import openpyxl

        registro = self._registro_con([self._fila()])
        datos = svc.generar_xlsx(registro)
        wb = openpyxl.load_workbook(io.BytesIO(datos))
        ws = wb['SGC-FOR-006']
        self.assertEqual(ws['B13'].value, 'X')  # Int
        self.assertIsNone(ws['A13'].value)
        self.assertEqual(ws['C13'].value, 'TI-PRG-001')
        self.assertEqual(ws['D13'].value, 'Procedimiento')
        self.assertEqual(ws['F13'].value, '24/10/2025')
        self.assertEqual(ws['G13'].value, '1')
        self.assertEqual(ws['H13'].value, '2')
        self.assertIn('SGC-IDL-012', wb.sheetnames)

    def test_soporte_externo_marca_ext(self):
        import openpyxl

        registro = self._registro_con([self._fila(
            _origen_tipo='sin_codigo', ext_int='no', codigo='N/A',
        )])
        wb = openpyxl.load_workbook(io.BytesIO(svc.generar_xlsx(registro)))
        ws = wb['SGC-FOR-006']
        self.assertEqual(ws['A13'].value, 'X')  # Ext
        self.assertIsNone(ws['B13'].value)
        self.assertEqual(ws['C13'].value, 'N/A')

    def test_hoja_continua_mas_de_22(self):
        import openpyxl

        filas = [
            self._fila(_clave=f'C-{i}', codigo=f'TI-PRG-{i:03d}',
                       titulo=f'Doc {i}')
            for i in range(25)
        ]
        registro = self._registro_con(filas)
        wb = openpyxl.load_workbook(io.BytesIO(svc.generar_xlsx(registro)))
        ws = wb['SGC-FOR-006']
        # La fila 34 (última de la plantilla) y las extra 35-37 tienen datos.
        self.assertEqual(ws['C34'].value, 'TI-PRG-021')
        self.assertEqual(ws['C35'].value, 'TI-PRG-022')
        self.assertEqual(ws['C37'].value, 'TI-PRG-024')
        self.assertEqual(ws.print_title_rows, '$1:$12')

    def test_generar_y_guardar(self):
        from unittest.mock import patch

        registro = self._registro_con([self._fila()])
        with patch(
            'documentos.services.for_006._xlsx_a_pdf',
            return_value=b'%PDF-1.4\n%prueba\n',
        ):
            resultado = svc.generar_y_guardar(registro, forzar=True)
        self.assertTrue(resultado['success'])
        registro.refresh_from_db()
        self.assertTrue(registro.archivo)
        self.assertTrue(registro.archivo_pdf)

    def test_el_pdf_se_prepara_sin_instructivo(self):
        registro = self._registro_con([self._fila()])
        with patch(
            'documentos.services.for_006._xlsx_a_pdf',
            return_value=b'%PDF-1.4\n%prueba\n',
        ) as convertir:
            svc.generar_y_guardar(registro, forzar=True)

        verificar_preparacion_pdf(
            self, convertir.call_args[0][0], registro.archivo.path,
            'SGC-FOR-006', 'SGC-IDL-012',
        )


class AccesoTest(BaseFor006Test):
    def setUp(self):
        super().setUp()
        from documentos.services import lista_maestra as acceso
        self.acceso = acceso

    def test_admin_accede(self):
        self.client.force_login(self.user_admin)
        resp = self.client.get(reverse('documentos:lista_maestra'))
        self.assertEqual(resp.status_code, 200)

    def test_sin_concesion_no_accede(self):
        self.client.force_login(self.user_trabajador)
        resp = self.client.get(reverse('documentos:lista_maestra'))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(self.acceso.puede_ver_lista(self.user_trabajador))

    def test_otorgar_y_revocar(self):
        self.acceso.otorgar(self.user_trabajador, otorgado_por=self.user_admin)
        self.assertTrue(self.acceso.puede_ver_lista(self.user_trabajador))
        self.client.force_login(self.user_trabajador)
        self.assertEqual(
            self.client.get(reverse('documentos:lista_maestra')).status_code, 200,
        )
        self.acceso.revocar(self.user_trabajador)
        self.assertFalse(self.acceso.puede_ver_lista(self.user_trabajador))

    def test_multi_persona_sgc_y_gg(self):
        self.acceso.otorgar(self.user_jefe_ti, otorgado_por=self.user_admin)
        self.acceso.otorgar(self.user_jefe_gg, otorgado_por=self.user_admin)
        for usuario in (self.user_jefe_ti, self.user_jefe_gg):
            self.assertTrue(self.acceso.puede_ver_lista(usuario))
            self.client.force_login(usuario)
            self.assertEqual(
                self.client.get(reverse('documentos:lista_maestra')).status_code,
                200,
            )

    def test_pagina_acceso_solo_admin(self):
        self.client.force_login(self.user_trabajador)
        self.assertEqual(
            self.client.get(reverse('documentos:lista_maestra_acceso')).status_code,
            302,
        )
        self.client.force_login(self.user_admin)
        self.assertEqual(
            self.client.get(reverse('documentos:lista_maestra_acceso')).status_code,
            200,
        )

    def test_otorgar_y_revocar_via_vista(self):
        self.client.force_login(self.user_admin)
        resp = self.client.post(
            reverse('documentos:lista_maestra_acceso'),
            {'usuario_id': self.user_trabajador.id},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self.acceso.puede_ver_lista(self.user_trabajador))
        resp = self.client.post(reverse(
            'documentos:lista_maestra_revocar',
            args=[self.user_trabajador.id],
        ))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(self.acceso.puede_ver_lista(self.user_trabajador))

    def test_puede_ver_lista_una_sola_consulta(self):
        self.acceso.otorgar(self.user_trabajador, otorgado_por=self.user_admin)
        usuario = User.objects.select_related('perfil').get(
            pk=self.user_trabajador.pk,
        )
        with CaptureQueriesContext(connection) as ctx:
            self.assertTrue(self.acceso.puede_ver_lista(usuario))
        self.assertEqual(len(ctx.captured_queries), 1)

    def test_sidebar_lista_maestra(self):
        self.client.force_login(self.user_admin)
        html = self.client.get(reverse('documentos:mis_documentos')).content.decode()
        self.assertIn('Lista Maestra', html)

        self.client.force_login(self.user_trabajador)
        html = self.client.get(reverse('documentos:mis_documentos')).content.decode()
        self.assertNotIn('Lista Maestra', html)


class VistaListaTest(BaseFor006Test):
    def setUp(self):
        super().setUp()
        self.registro = svc.registro_lista_maestra(
            crear=True, responsable=self.user_admin,
        )
        svc.sincronizar(self.registro, usuario=self.user_admin)

    def _fila(self, clave):
        registro = svc.registro_lista_maestra()
        for fila in svc.filas(registro):
            if fila.get('_clave') == clave:
                return fila
        return None

    def test_render_lista(self):
        self.client.force_login(self.user_admin)
        resp = self.client.get(reverse('documentos:lista_maestra'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Lista Maestra de Documentos')
        self.assertContains(resp, self.doc_activo.codigo)
        self.assertContains(resp, 'dl-empresa')

    def test_editar_fila_marca_manual(self):
        self.client.force_login(self.user_admin)
        resp = self.client.post(
            reverse('documentos:lista_maestra_editar_fila',
                    args=[self.doc_activo.codigo]),
            {'valor_forma_archivo': 'Digital/ Carpeta SGC'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'ok': True})
        fila = self._fila(self.doc_activo.codigo)
        self.assertEqual(fila['forma_archivo'], 'Digital/ Carpeta SGC')
        self.assertIn('forma_archivo', fila['_manual'])

    def test_editar_valor_igual_no_marca_manual(self):
        fila = self._fila(self.doc_activo.codigo)
        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('documentos:lista_maestra_editar_fila',
                    args=[self.doc_activo.codigo]),
            {'valor_titulo': fila['titulo']},
        )
        fila = self._fila(self.doc_activo.codigo)
        self.assertNotIn('titulo', fila.get('_manual') or [])

    def test_sin_acceso_no_edita(self):
        self.client.force_login(self.user_trabajador)
        resp = self.client.post(
            reverse('documentos:lista_maestra_editar_fila',
                    args=[self.doc_activo.codigo]),
            {'valor_forma_archivo': 'x'},
        )
        self.assertEqual(resp.status_code, 403)

    def test_agregar_y_eliminar_fila_manual(self):
        self.client.force_login(self.user_admin)
        resp = self.client.post(reverse('documentos:lista_maestra_agregar_fila'))
        self.assertEqual(resp.status_code, 302)
        manuales = [
            f for f in svc.filas(svc.registro_lista_maestra())
            if f.get('_origen_tipo') == 'manual'
        ]
        self.assertEqual(len(manuales), 1)
        clave = manuales[0]['_clave']

        resp = self.client.post(reverse(
            'documentos:lista_maestra_eliminar_fila', args=[clave],
        ))
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(self._fila(clave))

    def test_no_elimina_fila_de_documento(self):
        self.client.force_login(self.user_admin)
        self.client.post(reverse(
            'documentos:lista_maestra_eliminar_fila',
            args=[self.doc_activo.codigo],
        ))
        self.assertIsNotNone(self._fila(self.doc_activo.codigo))

    def test_sincronizar_via_vista(self):
        Documento.objects.filter(pk=self.doc_activo.pk).update(
            nombre='Actualizado por sync',
        )
        self.client.force_login(self.user_admin)
        resp = self.client.post(reverse('documentos:lista_maestra_sincronizar'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            self._fila(self.doc_activo.codigo)['titulo'], 'Actualizado por sync',
        )

    def test_generar_y_descargar(self):
        from unittest.mock import patch as _patch

        self.client.force_login(self.user_admin)
        with _patch('documentos.services.for_006._xlsx_a_pdf',
                    return_value=b'%PDF-1.4\n%prueba\n'):
            resp = self.client.post(reverse('documentos:lista_maestra_generar'))
            self.assertEqual(resp.status_code, 302)
            registro = svc.registro_lista_maestra()
            self.assertTrue(registro.archivo)
            self.assertTrue(registro.archivo_pdf)

            resp = self.client.get(reverse(
                'documentos:lista_maestra_descargar', args=['xlsx'],
            ))
            self.assertEqual(resp.status_code, 200)
            resp = self.client.get(reverse(
                'documentos:lista_maestra_descargar', args=['pdf'],
            ))
            self.assertEqual(resp.status_code, 200)

    def test_datalist_respuestas_recientes(self):
        self.client.force_login(self.user_admin)
        self.client.post(
            reverse('documentos:lista_maestra_editar_fila',
                    args=[self.doc_activo.codigo]),
            {'valor_forma_archivo': 'Digital/ Ubicación única'},
        )
        html = self.client.get(reverse('documentos:lista_maestra')).content.decode()
        self.assertIn('Digital/ Ubicación única', html)
