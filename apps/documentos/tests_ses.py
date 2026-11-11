"""Tests del motor ÚNICO de firma electrónica (SES) y de las aprobaciones.

Cubre:

* ``services.firmas.firmar`` sobre una aprobación FOR-005 y una solicitud
  FOR-001 (PIN, auto, sin PIN, PIN incorrecto y bloqueo);
* que ``resolver_*`` no permita resolver sin ``firma``;
* que la evidencia (hash, auditoría, origen) quede registrada;
* que el sello se estampe en el XLSX (FOR-002) y que las firmas de una
  solicitud se recolecten para 001/005.
"""
import io
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from documentos.models import (
    AprobacionPlanilla, CampoFormato, DistribucionDocumento, Documento,
    FirmaElectronica, Formato, PlanillaCambios, SeccionFormato,
    SolicitudAprobacionDocumento, SolicitudCambio,
)
from documentos.services import distribucion as svc_dist
from documentos.services import firmas
from documentos.services.aprobaciones import resolver_aprobacion_planilla
from documentos.services.firmas import ErrorFirma, firmar
from documentos.tests_sgc_utils import crear_plantilla_for_002, leer_celda

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_ses_')
PLANTILLA = os.path.join(TEST_MEDIA, 'plantilla_for_002.xlsx')


class BaseSesTest(BaseDocumentosTest):
    """Fixtures comunes: planilla FOR-005 y solicitud FOR-001 pendientes."""

    def _planilla_pendiente(self, codigo='PC-SES-1', aprobador=None):
        planilla = PlanillaCambios.objects.create(
            codigo=codigo, documento=self.doc_activo,
            tipo_cambio='modificacion', justificacion='Cambio SES',
            responsable=self.user_jefe_ti, version_destino='2.0',
            estado='en_revision',
        )
        paso = AprobacionPlanilla.objects.create(
            planilla=planilla, aprobador=aprobador or self.user_jefe_ti,
            rol_aprobador='autorizador', es_final=True, estado='pendiente',
        )
        return planilla, paso

    def _solicitud_pendiente(self, accion='creacion'):
        return SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision,
            accion_solicitud=accion,
            solicitante=self.user_trabajador,
            propietario=self.user_trabajador,
            aprobador=self.user_jefe_ti,
            departamento_origen=self.depto_ti,
            estado='pendiente',
            es_final=True,
        )


class MotorAprobacionesTest(BaseSesTest):
    def test_firmar_aprobacion_con_pin_resuelve_y_registra_evidencia(self):
        planilla, paso = self._planilla_pendiente()
        self.crear_pin_firma(self.user_jefe_ti)

        firma = firmar(
            paso, usuario=self.user_jefe_ti, pin='1234', metodo='pin',
            crear=True,
            datos={'aprobada': True, 'observaciones': 'ok'},
        )

        self.assertEqual(firma.estado, 'firmada')
        self.assertEqual(firma.tipo, 'interna')
        self.assertEqual(firma.origen_tipo, 'aprobacion_planilla')
        self.assertEqual(firma.origen_id, str(paso.pk))
        self.assertEqual(firma.origen, paso)
        self.assertEqual(len(firma.hash_contenido), 64)
        paso.refresh_from_db()
        planilla.refresh_from_db()
        self.assertEqual(paso.estado, 'aprobada')
        self.assertEqual(planilla.estado, 'aprobado')
        self.assertEqual(FirmaElectronica.para(paso).count(), 1)

    def test_sin_pin_no_resuelve(self):
        _planilla, paso = self._planilla_pendiente()
        with self.assertRaises(ErrorFirma):
            firmar(
                paso, usuario=self.user_jefe_ti, pin='', metodo='pin',
                crear=True, datos={'aprobada': True},
            )
        paso.refresh_from_db()
        self.assertEqual(paso.estado, 'pendiente')
        self.assertEqual(FirmaElectronica.para(paso).count(), 0)

    def test_pin_incorrecto_no_resuelve(self):
        _planilla, paso = self._planilla_pendiente()
        self.crear_pin_firma(self.user_jefe_ti, '1234')
        with self.assertRaises(ErrorFirma):
            firmar(
                paso, usuario=self.user_jefe_ti, pin='9999', metodo='pin',
                crear=True, datos={'aprobada': True},
            )
        paso.refresh_from_db()
        self.assertEqual(paso.estado, 'pendiente')
        self.assertFalse(FirmaElectronica.para(paso).exists())

    def test_bloqueo_tras_intentos_fallidos(self):
        self.crear_pin_firma(self.user_jefe_ti, '1234')
        for _ in range(firmas.PIN_MAX_INTENTOS):
            with self.assertRaises(ErrorFirma):
                firmas.verificar_pin(self.user_jefe_ti, '9999')
        with self.assertRaises(ErrorFirma) as ctx:
            firmas.verificar_pin(self.user_jefe_ti, '1234')
        self.assertIn('bloqueado', str(ctx.exception).lower())

    def test_rechazo_planilla_no_exige_pin_ni_crea_firma(self):
        planilla, paso = self._planilla_pendiente('PC-SES-RECH')
        resolver_aprobacion_planilla(
            paso, self.user_jefe_ti, aprobada=False, observaciones='no',
        )
        paso.refresh_from_db()
        planilla.refresh_from_db()
        self.assertEqual(paso.estado, 'rechazada')
        self.assertEqual(planilla.estado, 'rechazado')
        self.assertFalse(FirmaElectronica.para(paso).exists())

    def test_rechazo_solicitud_no_exige_pin_ni_crea_firma(self):
        from documentos.services.aprobacion_flujo import (
            resolver_solicitud_documento,
        )

        solicitud = self._solicitud_pendiente()
        resolver_solicitud_documento(
            None, solicitud, aprobada=False, observaciones='no',
        )
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'rechazada')
        self.assertFalse(FirmaElectronica.para(solicitud).exists())

    def test_resolver_sin_firma_es_imposible(self):
        _planilla, paso = self._planilla_pendiente()
        with self.assertRaises(ErrorFirma):
            resolver_aprobacion_planilla(
                paso, self.user_jefe_ti, aprobada=True,
            )
        paso.refresh_from_db()
        self.assertEqual(paso.estado, 'pendiente')

    def test_auto_aprobacion_firma_como_sistema(self):
        planilla, paso = self._planilla_pendiente()
        firma = firmar(
            paso, usuario=self.user_jefe_ti, metodo='auto', crear=True,
            datos={'aprobada': True, 'observaciones': 'auto'},
        )
        self.assertEqual(firma.tipo, 'sistema')
        paso.refresh_from_db()
        planilla.refresh_from_db()
        self.assertEqual(paso.estado, 'aprobada')
        self.assertEqual(planilla.estado, 'aprobado')

    def test_aprobacion_de_plantilla_no_emite_firma(self):
        from documentos.services.aprobacion_flujo import (
            resolver_solicitud_documento,
        )

        plantilla = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla SES',
            codigo='TI-FOR-PLT-SES', es_plantilla=True,
            estado='en_revision', propietario=self.user_jefe_ti,
            departamento=self.depto_ti,
        )
        solicitud = SolicitudAprobacionDocumento.objects.create(
            documento=plantilla, accion_solicitud='creacion',
            solicitante=self.user_trabajador,
            propietario=self.user_trabajador,
            aprobador=self.user_jefe_ti,
            departamento_origen=self.depto_ti,
            estado='pendiente', es_final=True,
        )

        # Las plantillas se resuelven sin firma ni PIN (§31.6).
        resolver_solicitud_documento(
            None, solicitud, aprobada=True, observaciones='ok',
        )

        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'aprobada')
        self.assertFalse(FirmaElectronica.para(solicitud).exists())

    def test_solicitud_for_001_firma_y_resuelve(self):
        solicitud = self._solicitud_pendiente()
        self.crear_pin_firma(self.user_jefe_ti)

        firma = firmar(
            solicitud, usuario=self.user_jefe_ti, pin='1234', metodo='pin',
            crear=True,
            datos={'aprobada': True, 'observaciones': 'ok'},
            contexto={'request': None},
        )

        self.assertEqual(firma.origen_tipo, 'solicitud_aprobacion_documento')
        self.assertEqual(firma.origen, solicitud)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'aprobada')

    def test_no_se_puede_firmar_dos_veces_el_mismo_paso(self):
        _planilla, paso = self._planilla_pendiente()
        self.crear_pin_firma(self.user_jefe_ti)
        firmar(
            paso, usuario=self.user_jefe_ti, pin='1234', metodo='pin',
            crear=True, datos={'aprobada': True},
        )
        with self.assertRaises(ErrorFirma):
            firmar(
                paso, usuario=self.user_jefe_ti, pin='1234', metodo='pin',
                crear=True, datos={'aprobada': True},
            )
        self.assertEqual(FirmaElectronica.para(paso).count(), 1)


class SuperficieApiTest(BaseSesTest):
    def test_api_pide_pin_y_firma(self):
        planilla, _paso = self._planilla_pendiente('PC-SES-API')
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')
        url = reverse('documentos:api_aprobar_planilla', args=[planilla.uuid])

        sin_pin = self.client.post(
            url, data=json.dumps({}), content_type='application/json',
        )
        self.assertEqual(sin_pin.status_code, 400)
        self.assertTrue(sin_pin.json()['requiere_pin'])
        planilla.refresh_from_db()
        self.assertEqual(planilla.estado, 'en_revision')

        self.crear_pin_firma(self.user_jefe_ti)
        con_pin = self.client.post(
            url, data=json.dumps({'pin': '1234'}),
            content_type='application/json',
        )
        self.assertEqual(con_pin.status_code, 200)
        planilla.refresh_from_db()
        self.assertEqual(planilla.estado, 'aprobado')

    def test_api_solicitud_for_001_pide_pin(self):
        solicitud = self._solicitud_pendiente()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')
        url = reverse(
            'documentos:api_aprobar_creacion',
            args=[self.doc_en_revision.tipo_id, self.doc_en_revision.uuid],
        )
        sin_pin = self.client.post(
            url, data=json.dumps({}), content_type='application/json',
        )
        self.assertEqual(sin_pin.status_code, 400)

        self.crear_pin_firma(self.user_jefe_ti)
        con_pin = self.client.post(
            url, data=json.dumps({'pin': '1234'}),
            content_type='application/json',
        )
        self.assertEqual(con_pin.status_code, 200)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'aprobada')

    def test_api_rechazo_planilla_sin_pin(self):
        planilla, paso = self._planilla_pendiente('PC-SES-RECH-API')
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse('documentos:api_rechazar_planilla', args=[planilla.uuid]),
            data=json.dumps({'motivo': 'no procede'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        planilla.refresh_from_db()
        self.assertEqual(planilla.estado, 'rechazado')
        self.assertFalse(FirmaElectronica.para(paso).exists())

    def test_api_rechazo_solicitud_sin_pin(self):
        solicitud = self._solicitud_pendiente()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse(
                'documentos:api_rechazar_creacion',
                args=[self.doc_en_revision.tipo_id, self.doc_en_revision.uuid],
            ),
            data=json.dumps({'motivo': 'no procede'}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'rechazada')
        self.assertFalse(FirmaElectronica.para(solicitud).exists())


class ModalPinTest(BaseDocumentosTest):
    def test_modal_oculto_por_defecto(self):
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(reverse('autenticacion:dashboard'))
        html = resp.content.decode()
        self.assertIn('id="pin-firma-modal" hidden', html)
        self.assertIn(
            '#pin-firma-modal[hidden] { display: none !important; }', html,
        )
        self.assertIn('Ir a Mi perfil', html)


class SelloEvidenciaTest(BaseSesTest):
    def test_texto_sello_incluye_nombre_hash_e_id(self):
        _planilla, paso = self._planilla_pendiente()
        self.crear_pin_firma(self.user_jefe_ti)
        firma = firmar(
            paso, usuario=self.user_jefe_ti, pin='1234', metodo='pin',
            crear=True, datos={'aprobada': True},
        )
        texto = firmas.texto_sello(firma)
        self.assertIn('Firma electrónica', texto)
        self.assertIn(firma.firmante_nombre, texto)
        self.assertIn(firma.hash_contenido[:16], texto)
        self.assertIn(f'ID {firma.id}', texto)

    def test_firmas_de_solicitud_incluye_pasos_firmados(self):
        planilla, paso = self._planilla_pendiente('PC-SES-SOL')
        solicitud = SolicitudCambio.objects.create(
            codigo='SC-SES-1', formato=self._formato('SGC-SES-T'),
            operacion='modificacion', documento=self.doc_activo,
            solicitante=self.user_jefe_ti, departamento=self.depto_ti,
        )
        planilla.solicitud_cambio = solicitud
        planilla.save(update_fields=['solicitud_cambio'])
        self.crear_pin_firma(self.user_jefe_ti)
        firma = firmar(
            paso, usuario=self.user_jefe_ti, pin='1234', metodo='pin',
            crear=True, datos={'aprobada': True},
        )

        obtenidas = firmas.firmas_de_solicitud(solicitud)
        self.assertEqual([f.id for f in obtenidas], [firma.id])

    def test_respuestas_con_sello_llena_el_campo(self):
        formato = self._formato('SGC-SES-SELLO')
        seccion = SeccionFormato.objects.create(
            formato=formato, titulo='Firma', orden=1,
        )
        campo = CampoFormato.objects.create(
            seccion=seccion, clave='sello_firma', etiqueta='Sello',
            tipo='sello', origen='sello_firma', celda_excel='A29',
        )
        _planilla, paso = self._planilla_pendiente('PC-SES-SELLO')
        self.crear_pin_firma(self.user_jefe_ti)
        firma = firmar(
            paso, usuario=self.user_jefe_ti, pin='1234', metodo='pin',
            crear=True, datos={'aprobada': True},
        )

        respuestas = firmas.respuestas_con_sello(formato, [firma], {})
        self.assertEqual(len(respuestas[campo.clave]), 1)
        self.assertIn('Firma electrónica', respuestas[campo.clave][0])

    def _formato(self, codigo):
        with patch('documentos.tasks.convertir_a_pdf_sync', return_value=None):
            return Formato.objects.create(
                codigo=codigo, nombre='Formato SES', version='1',
                edicion='1', activo=True,
            )


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class SelloXlsxFor002Test(BaseDocumentosTest):
    """El sello se escribe en el XLSX del FOR-002 (celda A52)."""

    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        crear_plantilla_for_002(PLANTILLA)
        with patch('documentos.tasks.convertir_formato_a_pdf.delay',
                   return_value=None):
            call_command(
                'integrar_formatos_sgc', solo='002', apply=True,
                plantilla=PLANTILLA, verbosity=0, stdout=io.StringIO(),
            )

    def test_sello_aparece_en_la_planilla(self):
        svc_dist.crear_distribuciones(
            self.doc_activo, internos=[self.user_jefe_ti],
            metodo='sistema', distribuido_por=self.user_jefe_ti,
            tipo='procedimientos_generales',
        )
        dist = DistribucionDocumento.objects.filter(
            documento=self.doc_activo,
        ).order_by('-id').first()
        firma = FirmaElectronica.para(dist).get(
            firmante_clave=FirmaElectronica.clave_interna(self.user_jefe_ti),
        )
        firma.estado = 'firmada'
        firma.fecha_firma = timezone.now()
        firma.hash_contenido = 'a' * 64
        firma.save(update_fields=['estado', 'fecha_firma', 'hash_contenido'])
        dist.estado_firma = 'firmada'
        dist.save(update_fields=['estado_firma'])

        xlsx = svc_dist.exportar_planilla_lote(dist.lote)
        sello = leer_celda(xlsx, 'A52')
        self.assertIsNotNone(sello)
        self.assertIn('Firma electrónica', sello)
        self.assertIn('Jefe TI', sello)
