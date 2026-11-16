"""Tests del Acta de Desincorporación de Archivos Inactivos (SGC-FOR-004).

Cubre: integración de la plantilla, detección de relacionados inactivos y
avisos, alta del acta (validaciones y constraint de acta única abierta),
generación XLSX/PDF (multifila y sello SES), firma híbrida (PIN y token),
aplicación (retiro de la relación), anulación por reactivación/eliminación,
vistas y guardas contra reincorporar relaciones ya desincorporadas.
"""
import io
import os
import tempfile
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import openpyxl

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from autenticacion.models import Notificacion
from documentos.models import (
    ActaDesincorporacion, Documento, FirmaElectronica, Formato,
    InvitacionFirma, ParticipanteActa, RegistroDesincorporado,
)
from documentos.services import for_004 as svc
from documentos.services import firmas as svc_firmas
from documentos.tests_sgc_utils import crear_plantilla_for_004

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_for004_')
PDF_BYTES = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseFor004Test(BaseDocumentosTest):
    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        plantilla = crear_plantilla_for_004(
            os.path.join(TEST_MEDIA, 'for004_sintetica.xlsx'),
        )
        call_command(
            'integrar_formatos_sgc', solo='004', apply=True, verbosity=0,
            plantilla=plantilla, stdout=io.StringIO(),
        )
        cls.formato = Formato.objects.get(codigo='SGC-FOR-004')
        # Los fixtures base no traen correo; la firma externa lo necesita.
        for user in (cls.user_jefe_ti, cls.user_jefe_gg, cls.user_trabajador):
            user.email = f'{user.username}@example.com'
            user.save(update_fields=['email'])

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato.refresh_from_db()
        svc_firmas.establecer_pin(self.user_jefe_ti, '1234')
        svc_firmas.establecer_pin(self.user_jefe_gg, '1234')
        svc_firmas.establecer_pin(self.user_trabajador, '1234')

    # -- utilidades -----------------------------------------------------
    def _relacionados(self, padre=None, n=1, estado='obsoleto'):
        padre = padre or self.doc_activo
        docs = []
        for i in range(n):
            doc = Documento.objects.create(
                tipo=self.tipo_formulario,
                nombre=f'Relacionado {i}',
                codigo=f'TI-REL-{uuid.uuid4().hex[:8]}',
                estado=estado,
                propietario=self.user_jefe_ti,
                departamento=self.depto_ti,
                version_actual='1.0',
                fecha_vencimiento=date.today() - timedelta(days=1),
            )
            padre.documentos_relacionados.add(doc)
            docs.append(doc)
        return docs

    def _acta(self, padre=None, n=1, participantes=None, aprobado_por=None,
              elaborado_por=None):
        padre = padre or self.doc_activo
        rels = self._relacionados(padre, n=n)
        acta = svc.crear_acta(
            padre=padre,
            participantes=participantes or [
                {'nombre': 'Ana Altuve', 'cargo': 'Métodos y procesos'},
            ],
            registro_ids=[r.pk for r in rels],
            creado_por=self.user_jefe_ti,
            aprobado_por=aprobado_por,
            elaborado_por=elaborado_por,
        )
        return acta, rels

    def _firmar(self, acta, usuario, rol, pin='1234'):
        return svc_firmas.firmar(
            acta, usuario=usuario, pin=pin, metodo='pin',
            datos={'rol': rol}, crear=True,
        )

    def _urls(self, acta):
        return {
            'detalle': reverse('documentos:acta_desincorporacion', kwargs={
                'tipo': acta.padre.tipo_id,
                'documento_id': acta.padre.uuid,
                'acta_uuid': acta.uuid,
            }),
            'crear': reverse('documentos:crear_acta_desincorporacion', kwargs={
                'tipo': acta.padre.tipo_id,
                'documento_id': acta.padre.uuid,
            }),
        }


class IntegracionFormatoTest(BaseFor004Test):
    def test_comando_crea_formato_y_estructura(self):
        self.assertEqual(
            self.formato.nombre,
            'Acta de Desincorporación de Archivos Inactivos',
        )
        self.assertFalse(self.formato.aplica_creacion)
        self.assertFalse(self.formato.aplica_modificacion)
        self.assertFalse(self.formato.aplica_eliminacion)
        self.assertTrue(self.formato.plantilla_xlsx)
        claves = {c.clave for c in self.formato.get_todos_los_campos()}
        self.assertIn('participantes', claves)
        self.assertIn('registros', claves)

    def test_dry_run_no_crea_formato(self):
        Formato.objects.filter(codigo='SGC-FOR-004').delete()
        plantilla = os.path.join(TEST_MEDIA, 'for004_sintetica.xlsx')
        call_command(
            'integrar_formatos_sgc', solo='004', verbosity=0,
            plantilla=plantilla, stdout=io.StringIO(),
        )
        self.assertFalse(Formato.objects.filter(codigo='SGC-FOR-004').exists())


class DeteccionTest(BaseFor004Test):
    def test_relacionados_inactivos_filtra_obsoletos(self):
        self._relacionados(self.doc_activo, n=2, estado='activo')
        obs = self._relacionados(self.doc_activo, n=1, estado='obsoleto')
        activos = svc.relacionados_inactivos(self.doc_activo)
        self.assertEqual(list(activos), obs)

    def test_pendientes_vacio_si_hay_acta_abierta(self):
        self._relacionados(self.doc_activo, n=1, estado='obsoleto')
        self.assertEqual(len(svc.pendientes_de_desincorporar(self.doc_activo)), 1)
        acta, _ = self._acta(self.doc_activo)
        self.assertEqual(
            list(svc.pendientes_de_desincorporar(self.doc_activo)), [],
        )
        self.assertEqual(svc.acta_abierta(self.doc_activo), acta)

    def test_avisar_crea_notificaciones_idempotente(self):
        rel = self._relacionados(self.doc_activo, n=1, estado='obsoleto')[0]
        n1 = svc.avisar_desincorporacion(rel)
        self.assertGreaterEqual(n1, 1)
        self.assertTrue(Notificacion.objects.filter(
            tipo='desincorporacion_pendiente', documento=self.doc_activo,
        ).exists())
        n2 = svc.avisar_desincorporacion(rel)
        self.assertEqual(n2, 0)

    def test_avisar_omite_si_hay_acta_abierta(self):
        rel = self._relacionados(self.doc_activo, n=1, estado='obsoleto')[0]
        self._acta(self.doc_activo)
        Notificacion.objects.filter(tipo='desincorporacion_pendiente').delete()
        self.assertEqual(svc.avisar_desincorporacion(rel), 0)

    def test_verificar_vencimientos_avisa(self):
        Documento.objects.filter(pk=self.doc_vencido.pk).update(
            estado='activo', fecha_vencimiento=date.today() - timedelta(days=1),
        )
        self.doc_activo.documentos_relacionados.add(self.doc_vencido)
        from documentos.tasks import verificar_vencimientos

        verificar_vencimientos.apply()
        self.assertTrue(Notificacion.objects.filter(
            tipo='desincorporacion_pendiente', documento=self.doc_activo,
        ).exists())

    def test_aplicar_cambio_inhabilitar_avisa(self):
        relacionado = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='A inhabilitar',
            codigo=f'TI-REL-{uuid.uuid4().hex[:8]}', estado='activo',
            propietario=self.user_trabajador, departamento=self.depto_ti,
            version_actual='1.0',
        )
        self.doc_activo.documentos_relacionados.add(relacionado)
        from documentos.models import SolicitudAprobacionDocumento

        solicitud = SolicitudAprobacionDocumento.objects.create(
            documento=relacionado, accion_solicitud='inhabilitar',
            solicitante=self.user_trabajador,
            propietario=self.user_trabajador,
            aprobador=self.user_admin,
            departamento_origen=self.depto_ti,
            rol_aprobador='autorizador', es_final=True, estado='aprobada',
        )
        solicitud.aplicar_cambio()
        relacionado.refresh_from_db()
        self.assertEqual(relacionado.estado, 'obsoleto')
        self.assertTrue(Notificacion.objects.filter(
            tipo='desincorporacion_pendiente', documento=self.doc_activo,
        ).exists())


class CrearActaTest(BaseFor004Test):
    def test_snapshots_y_valores_por_defecto(self):
        acta, rels = self._acta(self.doc_activo, n=2)
        self.assertEqual(acta.estado, 'borrador')
        self.assertEqual(acta.documento_codigo, self.doc_activo.codigo)
        self.assertEqual(acta.departamento_nombre, str(self.depto_ti))
        self.assertEqual(acta.proposito, svc.PROPOSITO_OFICIAL)
        self.assertEqual(acta.registros.count(), 2)
        reg = acta.registros.first()
        self.assertEqual(reg.codigo, rels[0].codigo)
        self.assertEqual(reg.fecha_vencimiento, rels[0].fecha_vencimiento)
        # Por defecto: elabora el creador y aprueba el jefe del departamento.
        self.assertEqual(acta.elaborado_por_id, self.user_jefe_ti.id)
        self.assertEqual(acta.aprobado_por_id, self.user_jefe_ti.id)

    def test_requiere_participantes(self):
        rels = self._relacionados(self.doc_activo, n=1)
        with self.assertRaises(ValueError):
            svc.crear_acta(
                padre=self.doc_activo, participantes=[], registro_ids=[rels[0].pk],
                creado_por=self.user_jefe_ti,
            )

    def test_maximo_participantes(self):
        rels = self._relacionados(self.doc_activo, n=1)
        participantes = [
            {'nombre': f'P{i}', 'cargo': 'X'} for i in range(5)
        ]
        with self.assertRaises(ValueError):
            svc.crear_acta(
                padre=self.doc_activo, participantes=participantes,
                registro_ids=[rels[0].pk], creado_por=self.user_jefe_ti,
            )

    def test_requiere_registros(self):
        with self.assertRaises(ValueError):
            svc.crear_acta(
                padre=self.doc_activo,
                participantes=[{'nombre': 'A', 'cargo': 'B'}],
                registro_ids=[], creado_por=self.user_jefe_ti,
            )

    def test_rechaza_registro_no_obsoleto(self):
        activo = self._relacionados(self.doc_activo, n=1, estado='activo')[0]
        with self.assertRaises(ValueError):
            svc.crear_acta(
                padre=self.doc_activo,
                participantes=[{'nombre': 'A', 'cargo': 'B'}],
                registro_ids=[activo.pk], creado_por=self.user_jefe_ti,
            )

    def test_rechaza_segunda_acta_abierta(self):
        self._acta(self.doc_activo)
        rels = self._relacionados(self.doc_activo, n=1)
        with self.assertRaises(ValueError):
            svc.crear_acta(
                padre=self.doc_activo,
                participantes=[{'nombre': 'A', 'cargo': 'B'}],
                registro_ids=[rels[0].pk], creado_por=self.user_jefe_ti,
            )

    def test_constraint_parcial_unica_abierta(self):
        self._acta(self.doc_activo)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ActaDesincorporacion.objects.create(
                padre=self.doc_activo, codigo=svc.generar_codigo() + 'X',
                creado_por=self.user_jefe_ti,
            )

    def test_codigo_secuencial(self):
        c1 = svc.generar_codigo()
        self.assertTrue(c1.startswith(f'ACT-DES-{date.today().year}-'))
        acta, _ = self._acta(self.doc_activo)
        self.assertEqual(acta.codigo, c1)


class XlsxTest(BaseFor004Test):
    def _datos(self, acta):
        return svc.generar_xlsx(acta)

    def test_celdas_rellenadas(self):
        acta, rels = self._acta(
            self.doc_activo, n=2,
            participantes=[
                {'nombre': 'Ana Altuve', 'cargo': 'Métodos y procesos'},
                {'nombre': 'Juan Pérez', 'cargo': 'Gerente General'},
            ],
            aprobado_por=self.user_jefe_gg,
        )
        wb = openpyxl.load_workbook(io.BytesIO(self._datos(acta)))
        self.assertIn('SGC-FOR-004', wb.sheetnames)
        self.assertIn('SGC-IDL-010', wb.sheetnames)
        ws = wb['SGC-FOR-004']
        self.assertIn('de', ws['A13'].value)
        self.assertIn('se reunieron los ciudadanos', ws['A13'].value)
        self.assertEqual(ws['A16'].value, 'Ana Altuve')
        self.assertEqual(ws['F16'].value, 'Métodos y procesos')
        self.assertEqual(ws['A17'].value, 'Juan Pérez')
        self.assertEqual(ws['F17'].value, 'Gerente General')
        self.assertIn('SGC - FOR - 004', ws['A6'].value)
        self.assertEqual(ws['A21'].value, svc.PROPOSITO_OFICIAL)
        self.assertTrue(ws['A23'].value.startswith('§'))
        self.assertIn(rels[0].codigo, ws['A23'].value)
        self.assertIn(rels[1].codigo, ws['A24'].value)
        self.assertIn(self.depto_ti.nombre, ws['A33'].value)
        self.assertEqual(ws['A40'].value, 'Elaborado por:')
        self.assertEqual(ws['F40'].value, 'Aprobado por:')
        self.assertIn('Nombre:', ws['A41'].value)
        self.assertIn('Nombre:', ws['F41'].value)
        # Hoja instructiva intacta.
        self.assertEqual(
            wb['SGC-IDL-010']['B1'].value,
            'INSTRUCTIVO DE LLENADO PARA EL FORMULARIO',
        )

    def test_fecha_en_letras(self):
        self.assertEqual(svc.dia_en_letras(1), 'uno')
        self.assertEqual(svc.dia_en_letras(12), 'doce')
        self.assertEqual(svc.dia_en_letras(21), 'veintiuno')
        self.assertEqual(svc.dia_en_letras(30), 'treinta')
        self.assertEqual(svc.dia_en_letras(31), 'treinta y uno')
        texto = svc.texto_fecha_acta(date(2026, 11, 12))
        self.assertIn('12', texto)
        self.assertIn('doce', texto)
        self.assertIn('noviembre', texto)
        self.assertIn('2026', texto)

    def test_multihoja_mas_de_nueve_registros(self):
        acta, rels = self._acta(self.doc_activo, n=12)
        wb = openpyxl.load_workbook(io.BytesIO(self._datos(acta)))
        hojas = [n for n in wb.sheetnames if n.startswith('SGC-FOR-004')]
        self.assertEqual(len(hojas), 2)
        self.assertIn('PAGINA:   1 DE 2', wb[hojas[0]]['I6'].value)
        self.assertIn('PAGINA:   2 DE 2', wb[hojas[1]]['I6'].value)
        # Los últimos 3 registros van a la segunda hoja.
        self.assertIn(rels[9].codigo, wb[hojas[1]]['A23'].value)
        self.assertIn(rels[11].codigo, wb[hojas[1]]['A25'].value)
        self.assertTrue(wb[hojas[1]]['A26'].value in (None, ''))

    def test_sello_solo_con_ambas_firmas(self):
        acta, _ = self._acta(
            self.doc_activo, aprobado_por=self.user_jefe_gg,
        )
        self.assertEqual(svc.sello_acta(acta), '')
        self._firmar(acta, self.user_jefe_ti, 'elaborado')
        acta.refresh_from_db()
        self.assertFalse(acta.firmas_completas)
        self.assertEqual(svc.sello_acta(acta), '')
        self._firmar(acta, self.user_jefe_gg, 'aprobado')
        acta.refresh_from_db()
        self.assertTrue(acta.firmas_completas)
        self.assertIn('Elaborado', svc.sello_acta(acta))
        self.assertIn('Aprobado', svc.sello_acta(acta))
        wb = openpyxl.load_workbook(io.BytesIO(self._datos(acta)))
        ws = wb['SGC-FOR-004']
        self.assertIn('Firmado electrónicamente', ws['A42'].value)

    def test_misma_persona_una_sola_firma_en_sello(self):
        acta, _ = self._acta(self.doc_activo)  # elabora y aprueba jefe_ti
        self._firmar(acta, self.user_jefe_ti, 'elaborado')
        acta.refresh_from_db()
        self.assertTrue(acta.firmas_completas)
        self.assertEqual(acta.firma_elaborado_id, acta.firma_aprobado_id)
        sello = svc.sello_acta(acta)
        self.assertIn('Elaborado y Aprobado', sello)
        self.assertEqual(sello.count('Firmado electrónicamente'), 1)

    def test_generar_y_guardar_persiste_archivos(self):
        acta, _ = self._acta(self.doc_activo)
        with patch('documentos.services.for_004._xlsx_a_pdf',
                   return_value=PDF_BYTES):
            resultado = svc.generar_y_guardar(acta)
        self.assertTrue(resultado['success'])
        acta.refresh_from_db()
        self.assertTrue(acta.xlsx)
        self.assertTrue(acta.pdf)


class FirmaTest(BaseFor004Test):
    def test_dos_firmantes_retiran_relacion_al_completar(self):
        acta, rels = self._acta(
            self.doc_activo, n=2, aprobado_por=self.user_jefe_gg,
        )
        self._firmar(acta, self.user_jefe_ti, 'elaborado')
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'pendiente_firma')
        self.assertTrue(self.doc_activo.documentos_relacionados.filter(
            pk=rels[0].pk).exists())

        self._firmar(acta, self.user_jefe_gg, 'aprobado')
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'aplicada')
        self.assertIsNotNone(acta.aplicada_en)
        self.assertFalse(self.doc_activo.documentos_relacionados.filter(
            pk__in=[r.pk for r in rels]).exists())
        self.assertTrue(Notificacion.objects.filter(
            tipo='desincorporacion_aplicada').exists())

    def test_misma_persona_aplica_con_una_firma(self):
        acta, rels = self._acta(self.doc_activo)
        self._firmar(acta, self.user_jefe_ti, 'elaborado')
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'aplicada')
        self.assertFalse(self.doc_activo.documentos_relacionados.filter(
            pk=rels[0].pk).exists())

    def test_no_puede_firmar_quien_no_esta_asignado(self):
        acta, _ = self._acta(
            self.doc_activo, aprobado_por=self.user_jefe_gg,
        )
        with self.assertRaises(svc_firmas.ErrorFirma):
            self._firmar(acta, self.user_trabajador, 'elaborado')
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'borrador')

    def test_no_puede_firmar_rol_ya_firmado(self):
        acta, _ = self._acta(
            self.doc_activo, aprobado_por=self.user_jefe_gg,
        )
        self._firmar(acta, self.user_jefe_ti, 'elaborado')
        with self.assertRaises(svc_firmas.ErrorFirma):
            self._firmar(acta, self.user_jefe_ti, 'elaborado')

    def test_firmar_con_acta_anulada_falla(self):
        acta, _ = self._acta(self.doc_activo)
        acta.estado = 'anulada'
        acta.save(update_fields=['estado'])
        with self.assertRaises(svc_firmas.ErrorFirma):
            self._firmar(acta, self.user_jefe_ti, 'elaborado')

    def test_revalidacion_registro_reactivado_bloquea_firma(self):
        acta, rels = self._acta(
            self.doc_activo, aprobado_por=self.user_jefe_gg,
        )
        Documento.objects.filter(pk=rels[0].pk).update(estado='activo')
        with self.assertRaises(svc_firmas.ErrorFirma):
            self._firmar(acta, self.user_jefe_ti, 'elaborado')
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'borrador')

    def test_firma_externa_por_token_aplica(self):
        acta, _ = self._acta(
            self.doc_activo, aprobado_por=self.user_jefe_gg,
        )
        invitacion, token = svc.crear_invitacion_rol(acta, 'aprobado')
        self.assertTrue(invitacion.firma.firmante_email)
        self.assertIsNone(invitacion.firma.firmante_id)
        svc_firmas.firmar_externa(invitacion, consentimiento=True)
        acta.refresh_from_db()
        self.assertIsNotNone(acta.firma_aprobado_id)
        self.assertEqual(acta.estado, 'pendiente_firma')
        self._firmar(acta, self.user_jefe_ti, 'elaborado')
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'aplicada')

    def test_invitacion_sin_correo_falla(self):
        self.user_jefe_gg.email = ''
        self.user_jefe_gg.save(update_fields=['email'])
        acta, _ = self._acta(self.doc_activo, aprobado_por=self.user_jefe_gg)
        with self.assertRaises(ValueError):
            svc.crear_invitacion_rol(acta, 'aprobado')


class AnulacionTest(BaseFor004Test):
    def test_reactivacion_anula_acta(self):
        acta, rels = self._acta(self.doc_activo, aprobado_por=self.user_jefe_gg)
        rels[0].estado = 'activo'
        rels[0].save(update_fields=['estado'])  # dispara post_save
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'anulada')
        self.assertTrue(acta.anulada_motivo)

    def test_eliminar_registro_anula_acta(self):
        acta, rels = self._acta(self.doc_activo, aprobado_por=self.user_jefe_gg)
        rels[0].delete()
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'anulada')

    def test_eliminar_padre_anula_acta(self):
        acta, _ = self._acta(self.doc_activo, aprobado_por=self.user_jefe_gg)
        self.doc_activo.delete()
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'anulada')

    def test_vista_anular(self):
        acta, _ = self._acta(self.doc_activo)
        self.client.force_login(self.user_jefe_ti)
        url = reverse('documentos:anular_acta_desincorporacion', kwargs={
            'tipo': acta.padre.tipo_id, 'documento_id': acta.padre.uuid,
            'acta_uuid': acta.uuid,
        })
        resp = self.client.post(url, {'motivo': 'Duplicada'})
        self.assertEqual(resp.status_code, 302)
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'anulada')
        self.assertEqual(acta.anulada_motivo, 'Duplicada')

    def test_anular_requiere_motivo(self):
        acta, _ = self._acta(self.doc_activo)
        self.client.force_login(self.user_jefe_ti)
        url = reverse('documentos:anular_acta_desincorporacion', kwargs={
            'tipo': acta.padre.tipo_id, 'documento_id': acta.padre.uuid,
            'acta_uuid': acta.uuid,
        })
        self.client.post(url, {'motivo': ''})
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'borrador')


class VistaTest(BaseFor004Test):
    def test_detalle_muestra_modal_con_inactivos(self):
        self._relacionados(self.doc_activo, n=1, estado='obsoleto')
        self.client.force_login(self.user_jefe_ti)
        url = reverse('documentos:detalle', kwargs={
            'tipo': self.doc_activo.tipo_id, 'documento_id': self.doc_activo.uuid,
        })
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'modal-acta-desincorporacion')
        self.assertContains(resp, 'Generar acta')

    def test_crear_via_post(self):
        rels = self._relacionados(self.doc_activo, n=2)
        self.client.force_login(self.user_jefe_ti)
        url = reverse('documentos:crear_acta_desincorporacion', kwargs={
            'tipo': self.doc_activo.tipo_id, 'documento_id': self.doc_activo.uuid,
        })
        with patch('documentos.services.for_004._xlsx_a_pdf',
                   return_value=PDF_BYTES):
            resp = self.client.post(url, {
                'fecha': date.today().isoformat(),
                'registros': [str(r.pk) for r in rels],
                'participante_nombre_0': 'Ana Altuve',
                'participante_cargo_0': 'Métodos',
                'aprobado_por': str(self.user_jefe_gg.pk),
            })
        self.assertEqual(resp.status_code, 302)
        acta = ActaDesincorporacion.objects.get()
        self.assertEqual(acta.registros.count(), 2)
        self.assertEqual(acta.aprobado_por_id, self.user_jefe_gg.pk)
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_jefe_gg, tipo='desincorporacion_pendiente',
        ).exists())

    def test_crear_sin_permiso(self):
        self._relacionados(self.doc_activo, n=1)
        self.client.force_login(self.user_trabajador)
        url = reverse('documentos:crear_acta_desincorporacion', kwargs={
            'tipo': self.doc_activo.tipo_id, 'documento_id': self.doc_activo.uuid,
        })
        self.client.post(url, {'fecha': date.today().isoformat()})
        self.assertFalse(ActaDesincorporacion.objects.exists())

    def test_firmar_via_post(self):
        acta, _ = self._acta(self.doc_activo, aprobado_por=self.user_jefe_gg)
        self.client.force_login(self.user_jefe_ti)
        url = reverse('documentos:firmar_acta_desincorporacion', kwargs={
            'tipo': acta.padre.tipo_id, 'documento_id': acta.padre.uuid,
            'acta_uuid': acta.uuid,
        })
        resp = self.client.post(url, {'rol': 'elaborado', 'pin': '1234'})
        self.assertEqual(resp.status_code, 302)
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'pendiente_firma')

    def test_firmante_asignado_ve_acta_sin_permiso_de_padre(self):
        # jefe_gg (aprobador) no comparte departamento con el documento TI.
        acta, _ = self._acta(self.doc_activo, aprobado_por=self.user_jefe_gg)
        self.client.force_login(self.user_jefe_gg)
        url = reverse('documentos:acta_desincorporacion', kwargs={
            'tipo': acta.padre.tipo_id, 'documento_id': acta.padre.uuid,
            'acta_uuid': acta.uuid,
        })
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_descarga_xlsx(self):
        acta, _ = self._acta(self.doc_activo)
        with patch('documentos.services.for_004._xlsx_a_pdf',
                   return_value=PDF_BYTES):
            svc.generar_y_guardar(acta)
        acta.refresh_from_db()
        self.client.force_login(self.user_jefe_ti)
        url = reverse('documentos:descarga_acta_desincorporacion', kwargs={
            'tipo': acta.padre.tipo_id, 'documento_id': acta.padre.uuid,
            'acta_uuid': acta.uuid, 'formato': 'xlsx',
        })
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            'spreadsheet', resp['Content-Type'],
        )


class InvitacionVistaTest(BaseFor004Test):
    def test_firmar_invitacion_acta_por_token(self):
        acta, _ = self._acta(self.doc_activo, aprobado_por=self.user_jefe_gg)
        _, token = svc.crear_invitacion_rol(acta, 'aprobado')
        url = reverse('documentos:firmar_invitacion', args=[token])
        # GET muestra el formulario de firma.
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Firmar acta')
        # POST firma con consentimiento.
        resp = self.client.post(url, {'consentimiento': 'on'})
        self.assertEqual(resp.status_code, 200)
        acta.refresh_from_db()
        self.assertIsNotNone(acta.firma_aprobado_id)

    def test_invitacion_de_acta_anulada_bloqueada(self):
        acta, _ = self._acta(self.doc_activo, aprobado_por=self.user_jefe_gg)
        _, token = svc.crear_invitacion_rol(acta, 'aprobado')
        acta.estado = 'anulada'
        acta.save(update_fields=['estado'])
        resp = self.client.get(
            reverse('documentos:firmar_invitacion', args=[token]),
        )
        self.assertEqual(resp.status_code, 403)

    def test_descarga_invitacion_acta(self):
        acta, _ = self._acta(self.doc_activo, aprobado_por=self.user_jefe_gg)
        with patch('documentos.services.for_004._xlsx_a_pdf',
                   return_value=PDF_BYTES):
            svc.generar_y_guardar(acta)
        acta.refresh_from_db()
        _, token = svc.crear_invitacion_rol(acta, 'aprobado')
        resp = self.client.get(
            reverse('documentos:descarga_invitacion', args=[token]),
        )
        self.assertEqual(resp.status_code, 200)


class GuardaReincorporacionTest(BaseFor004Test):
    def test_filtrar_relaciones_bloqueadas(self):
        acta, rels = self._acta(self.doc_activo)
        self._firmar(acta, self.user_jefe_ti, 'elaborado')
        acta.refresh_from_db()
        self.assertEqual(acta.estado, 'aplicada')
        permitidos = svc.filtrar_relaciones_bloqueadas(
            self.doc_activo, rels,
        )
        self.assertEqual(permitidos, [])

    def test_relacion_no_bloqueada_pasa(self):
        otro = self._relacionados(self.doc_activo, n=1, estado='activo')[0]
        permitidos = svc.filtrar_relaciones_bloqueadas(
            self.doc_activo, [otro],
        )
        self.assertEqual(permitidos, [otro])
