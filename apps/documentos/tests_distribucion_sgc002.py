"""Tests de la integración SGC-FOR-002 (Control de Distribución).

Cubre: lotes de 33, política de adjunto, distribución interna/externa,
planilla XLSX, firma electrónica interna (OTP + PIN) y externa (token),
vistas y el comando ``integrar_formatos_sgc``.
"""
import hashlib
import io
import os
import secrets
import tempfile
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

import openpyxl

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET
from documentos.models import (
    DistribucionDocumento, FirmaElectronica, Formato, FormatoRespuesta,
    InvitacionFirma, CampoFormato,
)
from documentos.services import distribucion as svc_dist
from documentos.services import firmas as svc_firmas
from documentos.services.distribucion import DestinatarioExterno
from documentos.tests_sgc_utils import (
    crear_plantilla_for_002, ruta_plantilla_real,
)

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_sgc002_')


def plantilla_sgc002():
    """XLSX de origen para el comando: el oficial si existe, o uno sintético.

    ``documentacion/`` no está versionado, así que en CI solo hay el sintético
    (mismo layout: cabecera fila 12 y tabla 19-51).
    """
    ruta_real = ruta_plantilla_real(settings.BASE_DIR)
    if ruta_real:
        return ruta_real
    return crear_plantilla_for_002(
        os.path.join(TEST_MEDIA, 'plantilla_sintetica_sgc002.xlsx'),
    )


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseSgc002Test(BaseDocumentosTest):
    """Crea el formato SGC-FOR-002 (comando real) antes de cada clase."""

    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        with patch('documentos.tasks.convertir_formato_a_pdf.delay', return_value=None):
            call_command(
                'integrar_formatos_sgc', solo='002', apply=True, verbosity=0,
                plantilla=plantilla_sgc002(), stdout=io.StringIO(),
            )
        cls.formato_002 = Formato.objects.get(codigo='SGC-FOR-002')

    def setUp(self):
        super().setUp()
        self.formato_002.refresh_from_db()
        self.client = Client()

    def _externo(self, nombre='Proveedor Externo', email='proveedor@example.com',
                 cargo='Gerente', organizacion='ACME'):
        return DestinatarioExterno(
            nombre=nombre, email=email, cargo=cargo, organizacion=organizacion,
        )

    def _crear(self, internos=(), externos=(), metodo='sistema', adjuntar_pdf=False):
        return svc_dist.crear_distribuciones(
            self.doc_activo,
            internos=internos, externos=externos, metodo=metodo,
            observaciones='', distribuido_por=self.user_jefe_ti,
            adjuntar_pdf=adjuntar_pdf, request=None,
            tipo='procedimientos_generales',
        )


# =============================================================================
# Lotes y política
# =============================================================================

class LotesYPoliticaTest(BaseDocumentosTest):
    def test_dividir_en_lotes_bordes(self):
        self.assertEqual(len(svc_dist.dividir_en_lotes(list(range(32)))), 1)
        self.assertEqual(len(svc_dist.dividir_en_lotes(list(range(33)))), 1)
        lotes = svc_dist.dividir_en_lotes(list(range(34)))
        self.assertEqual([len(l) for l in lotes], [33, 1])
        self.assertEqual(len(svc_dist.dividir_en_lotes(list(range(67)))), 3)
        self.assertEqual(svc_dist.dividir_en_lotes([], 33), [])

    def test_tamano_invalido(self):
        with self.assertRaises(ValueError):
            svc_dist.dividir_en_lotes([1, 2], 0)

    def test_politica_restringido_nunca(self):
        self.doc_activo.clasificacion = 'restringido'
        permitido, motivo = svc_dist.politica_adjunto_pdf(
            self.doc_activo, self.user_admin,
        )
        self.assertFalse(permitido)
        self.assertIn('restringido', motivo.lower())

    def test_politica_confidencial_solo_admin_o_jefe(self):
        self.doc_activo.clasificacion = 'confidencial'
        self.assertTrue(
            svc_dist.politica_adjunto_pdf(self.doc_activo, self.user_admin)[0]
        )
        self.assertTrue(
            svc_dist.politica_adjunto_pdf(self.doc_activo, self.user_jefe_ti)[0]
        )
        self.assertFalse(
            svc_dist.politica_adjunto_pdf(self.doc_activo, self.user_coordinador)[0]
        )

    def test_politica_interno_publico_permitido(self):
        for clasif in ('interno', 'publico'):
            self.doc_activo.clasificacion = clasif
            self.assertTrue(
                svc_dist.politica_adjunto_pdf(self.doc_activo, self.user_coordinador)[0]
            )


# =============================================================================
# Creación de distribuciones
# =============================================================================

class CrearDistribucionesTest(BaseSgc002Test):
    def test_sin_destinatarios_error(self):
        with self.assertRaises(ValueError):
            self._crear()

    def test_externo_crea_firma_invitacion_y_planilla(self):
        resultado = self._crear(externos=[self._externo()], metodo='email')

        self.assertEqual(resultado.creadas, 1)
        self.assertEqual(resultado.invitaciones, 1)
        self.assertEqual(resultado.planillas, 1)

        dist = DistribucionDocumento.objects.get(documento=self.doc_activo)
        self.assertTrue(dist.es_externo)
        self.assertEqual(dist.nombre_externo, 'Proveedor Externo')
        self.assertEqual(dist.email_externo, 'proveedor@example.com')
        self.assertIsNotNone(dist.lote)
        self.assertEqual(dist.estado_firma, 'pendiente')

        firma = FirmaElectronica.objects.get(distribucion=dist)
        self.assertEqual(firma.tipo, 'externa')
        self.assertEqual(firma.estado, 'pendiente')
        self.assertEqual(firma.firmante_email, 'proveedor@example.com')

        invitacion = InvitacionFirma.objects.get(firma=firma)
        self.assertTrue(invitacion.email_enviado)
        self.assertEqual(len(invitacion.token_hash), 64)

        # Correo de distribución + correo de invitación.
        self.assertEqual(len(mail.outbox), 2)
        asuntos = ' '.join(m.subject for m in mail.outbox)
        self.assertIn('Invitación a firmar', asuntos)

        fr = FormatoRespuesta.objects.get(
            origen_tipo='distribucion', origen_id=str(dist.lote),
        )
        self.assertEqual(
            fr.respuestas['destinatarios'][0]['nombre'], 'Proveedor Externo',
        )

    def test_interno_y_externo_comparten_lote(self):
        usuario = self.user_trabajador
        usuario.email = 'trab@example.com'
        usuario.save(update_fields=['email'])
        resultado = self._crear(
            internos=[usuario], externos=[self._externo()], metodo='sistema',
        )
        self.assertEqual(resultado.creadas, 2)
        self.assertEqual(resultado.lotes, 1)

        dists = DistribucionDocumento.objects.filter(documento=self.doc_activo)
        self.assertEqual(dists.values('lote').distinct().count(), 1)
        self.assertEqual(
            FormatoRespuesta.objects.filter(origen_tipo='distribucion').count(), 1,
        )
        # Interno: firma interna; externo: firma externa.
        self.assertTrue(FirmaElectronica.objects.filter(tipo='interna').exists())
        self.assertTrue(FirmaElectronica.objects.filter(tipo='externa').exists())

    def test_mas_de_33_genera_varios_lotes(self):
        externos = [
            self._externo(nombre=f'Ext {i}', email=f'ext{i}@example.com')
            for i in range(34)
        ]
        resultado = self._crear(externos=externos, metodo='sistema')

        self.assertEqual(resultado.creadas, 34)
        self.assertEqual(resultado.lotes, 2)
        self.assertEqual(resultado.planillas, 2)
        self.assertTrue(any('2 planillas' in m for m in resultado.mensajes))

        self.assertEqual(
            DistribucionDocumento.objects.values('lote').distinct().count(), 2,
        )
        self.assertEqual(
            FormatoRespuesta.objects.filter(origen_tipo='distribucion').count(), 2,
        )

    def test_constancia_por_metodo(self):
        self._crear(internos=[self.user_trabajador], metodo='sistema')
        dist = DistribucionDocumento.objects.get()
        self.assertEqual(dist.constancia, 'sistema')
        self.assertIsNotNone(dist.fecha_entrega)


# =============================================================================
# Planilla XLSX
# =============================================================================

class PlanillaXlsxTest(BaseSgc002Test):
    def _dist_externa(self):
        self._crear(externos=[self._externo()], metodo='sistema')
        return DistribucionDocumento.objects.get(documento=self.doc_activo)

    def test_cabecera_y_tabla(self):
        dist = self._dist_externa()
        contenido = svc_dist.exportar_planilla_lote(dist.lote)
        wb = openpyxl.load_workbook(io.BytesIO(contenido))
        ws = wb.active

        self.assertEqual(ws['A12'].value, 'Procedimientos Generales')
        self.assertEqual(ws['E12'].value, 'TI-PRG-001')
        self.assertEqual(ws['F12'].value, 'Procedimiento de Prueba')
        self.assertEqual(ws['J12'].value, '1.0')
        # ORIGEN = INT → X en D12; C12 queda vacía.
        self.assertEqual(ws['D12'].value, 'X')
        self.assertIsNone(ws['C12'].value)

        # Fila de datos.
        self.assertEqual(ws['A19'].value, 'Proveedor Externo')
        self.assertEqual(ws['C19'].value, 'Gerente')
        # RETIRO = NO → X en H19.
        self.assertEqual(ws['H19'].value, 'X')
        self.assertIsNone(ws['I19'].value)

    def test_retiro_cambia_marca(self):
        dist = self._dist_externa()
        dist.retiro_version_anterior = True
        dist.save(update_fields=['retiro_version_anterior'])
        svc_dist.generar_planilla_lote(dist.lote)

        contenido = svc_dist.exportar_planilla_lote(dist.lote)
        ws = openpyxl.load_workbook(io.BytesIO(contenido)).active
        self.assertEqual(ws['G19'].value, 'X')
        self.assertIsNone(ws['H19'].value)

    def test_max_33_filas(self):
        externos = [
            self._externo(nombre=f'Ext {i}', email=f'ext{i}@example.com')
            for i in range(33)
        ]
        dists = []
        self._crear(externos=externos, metodo='sistema')
        lote = DistribucionDocumento.objects.values('lote').distinct().get()['lote']
        contenido = svc_dist.exportar_planilla_lote(lote)
        ws = openpyxl.load_workbook(io.BytesIO(contenido)).active
        self.assertEqual(ws['A19'].value, 'Ext 0')
        self.assertEqual(ws['A51'].value, 'Ext 32')
        self.assertIsNone(ws['A52'].value)


# =============================================================================
# Firma interna (OTP + PIN)
# =============================================================================

class FirmaInternaTest(BaseSgc002Test):
    def setUp(self):
        super().setUp()
        self.user_trabajador.email = 'trab@example.com'
        self.user_trabajador.save(update_fields=['email'])
        self._crear(internos=[self.user_trabajador], metodo='sistema')
        self.dist = DistribucionDocumento.objects.get(documento=self.doc_activo)
        self.firma = FirmaElectronica.objects.get(distribucion=self.dist)

    def test_pin_y_firma_completa(self):
        svc_firmas.establecer_pin(self.user_trabajador, '1234', '1234')
        codigo = svc_firmas.generar_otp(self.firma)
        self.assertEqual(len(codigo), 6)
        self.assertEqual(len(mail.outbox), 1)

        svc_firmas.firmar_interna(self.firma, self.user_trabajador, codigo, '1234')

        self.firma.refresh_from_db()
        self.dist.refresh_from_db()
        self.assertEqual(self.firma.estado, 'firmada')
        self.assertEqual(self.dist.estado_firma, 'firmada')
        self.assertEqual(len(self.firma.hash_contenido), 64)
        self.assertIsNotNone(self.firma.fecha_firma)

        # La planilla se regeneró con el nombre en FIRMA CONFORME (col I).
        ws = openpyxl.load_workbook(io.BytesIO(
            svc_dist.exportar_planilla_lote(self.dist.lote)
        )).active
        self.assertEqual(ws['I19'].value, 'Trab Test')

    def test_pin_incorrecto_y_bloqueo(self):
        svc_firmas.establecer_pin(self.user_trabajador, '1234', '1234')
        for _ in range(svc_firmas.PIN_MAX_INTENTOS - 1):
            with self.assertRaises(svc_firmas.ErrorFirma):
                svc_firmas.verificar_pin(self.user_trabajador, '9999')
        with self.assertRaises(svc_firmas.ErrorFirma) as ctx:
            svc_firmas.verificar_pin(self.user_trabajador, '9999')
        self.assertIn('bloque', str(ctx.exception).lower())
        with self.assertRaises(svc_firmas.ErrorFirma) as ctx2:
            svc_firmas.verificar_pin(self.user_trabajador, '1234')
        self.assertIn('bloque', str(ctx2.exception).lower())

    def test_pin_formato_invalido(self):
        with self.assertRaises(svc_firmas.ErrorFirma):
            svc_firmas.establecer_pin(self.user_trabajador, '12', '12')
        with self.assertRaises(svc_firmas.ErrorFirma):
            svc_firmas.establecer_pin(self.user_trabajador, 'abcd', 'abcd')
        with self.assertRaises(svc_firmas.ErrorFirma):
            svc_firmas.establecer_pin(self.user_trabajador, '1234', '4321')

    def test_otp_invalido_y_limite(self):
        svc_firmas.establecer_pin(self.user_trabajador, '1234', '1234')
        svc_firmas.generar_otp(self.firma)
        for _ in range(svc_firmas.OTP_MAX_INTENTOS):
            with self.assertRaises(svc_firmas.ErrorFirma):
                svc_firmas.verificar_otp(self.firma, '000000')
        with self.assertRaises(svc_firmas.ErrorFirma) as ctx:
            svc_firmas.verificar_otp(self.firma, '000000')
        self.assertIn('Demasiados', str(ctx.exception))

    def test_otp_expirado(self):
        self.firma.otp_hash = 'x'
        self.firma.otp_expira = timezone.now() - timedelta(minutes=1)
        self.firma.save(update_fields=['otp_hash', 'otp_expira'])
        with self.assertRaises(svc_firmas.ErrorFirma) as ctx:
            svc_firmas.verificar_otp(self.firma, '123456')
        self.assertIn('expir', str(ctx.exception).lower())


# =============================================================================
# Firma externa (token)
# =============================================================================

class FirmaExternaTest(BaseSgc002Test):
    def setUp(self):
        super().setUp()
        self._crear(externos=[self._externo()], metodo='sistema')
        self.dist = DistribucionDocumento.objects.get(documento=self.doc_activo)
        self.firma = FirmaElectronica.objects.get(distribucion=self.dist)

    def _invitacion(self, token=None, expira=None):
        token = token or secrets.token_urlsafe(24)
        inv = InvitacionFirma.objects.create(
            firma=self.firma,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expira=expira or (timezone.now() + timedelta(days=7)),
        )
        return inv, token

    def test_requiere_consentimiento(self):
        inv, _ = self._invitacion()
        with self.assertRaises(svc_firmas.ErrorFirma):
            svc_firmas.firmar_externa(inv, consentimiento=False)

    def test_firma_y_un_solo_uso(self):
        inv, _ = self._invitacion()
        svc_firmas.firmar_externa(inv, consentimiento=True)
        inv.refresh_from_db()
        self.firma.refresh_from_db()
        self.assertTrue(inv.usado)
        self.assertEqual(self.firma.estado, 'firmada')
        self.assertEqual(self.firma.datos.get('metodo'), 'invitacion_token')

        with self.assertRaises(svc_firmas.ErrorFirma):
            svc_firmas.firmar_externa(inv, consentimiento=True)

    def test_expirada(self):
        inv, _ = self._invitacion(expira=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(svc_firmas.ErrorFirma) as ctx:
            svc_firmas.firmar_externa(inv, consentimiento=True)
        self.assertIn('expir', str(ctx.exception).lower())
        self.firma.refresh_from_db()
        self.assertEqual(self.firma.estado, 'expirada')

    def test_buscar_invitacion_por_token(self):
        inv, token = self._invitacion()
        encontrada = svc_firmas.buscar_invitacion(token)
        self.assertEqual(encontrada.id, inv.id)
        self.assertIsNone(svc_firmas.buscar_invitacion('token-falso'))


# =============================================================================
# Vistas
# =============================================================================

class VistasSgc002Test(BaseSgc002Test):
    def setUp(self):
        super().setUp()
        self.user_trabajador.email = 'trab@example.com'
        self.user_trabajador.save(update_fields=['email'])
        self.client.login(username='jefe_ti', password='testpass123')

    def test_get_distribuir_muestra_externos_y_adjunto(self):
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/distribuir/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id_externos')
        self.assertContains(resp, 'id_adjuntar_pdf')

    def test_post_externo_crea_distribucion(self):
        import json
        externos = json.dumps([{
            'nombre': 'Ente Externo', 'email': 'ente@example.com',
            'cargo': 'Director', 'organizacion': 'Ente',
        }])
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/distribuir/',
            {
                'usuarios': '', 'metodo': 'sistema',
                'externos': externos, 'observaciones': '',
            },
        )
        self.assertEqual(resp.status_code, 302)
        dist = DistribucionDocumento.objects.get(documento=self.doc_activo)
        self.assertEqual(dist.nombre_externo, 'Ente Externo')
        self.assertTrue(FirmaElectronica.objects.filter(distribucion=dist).exists())

    def test_post_externo_email_invalido(self):
        import json
        externos = json.dumps([{'nombre': 'X', 'email': 'no-es-correo'}])
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/distribuir/',
            {'usuarios': '', 'metodo': 'sistema', 'externos': externos},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(DistribucionDocumento.objects.exists())

    def test_descargar_planilla_como_gestor(self):
        self._crear(internos=[self.user_trabajador], metodo='sistema')
        dist = DistribucionDocumento.objects.get(documento=self.doc_activo)
        resp = self.client.get(reverse(
            'documentos:descargar_planilla_lote',
            args=['procedimientos_generales', self.doc_activo.uuid, dist.lote],
        ))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp['Content-Type'])
        self.assertTrue(resp.content.startswith(b'PK'))

    def test_descargar_planilla_sin_permiso(self):
        self._crear(externos=[self._externo()], metodo='sistema')
        dist = DistribucionDocumento.objects.get(documento=self.doc_activo)
        self.client.logout()
        self.client.login(username='trab02', password='testpass123')
        resp = self.client.get(reverse(
            'documentos:descargar_planilla_lote',
            args=['procedimientos_generales', self.doc_activo.uuid, dist.lote],
        ))
        self.assertEqual(resp.status_code, 302)

    def test_alternar_retiro(self):
        self._crear(internos=[self.user_trabajador], metodo='sistema')
        dist = DistribucionDocumento.objects.get(documento=self.doc_activo)
        self.assertFalse(dist.retiro_version_anterior)
        resp = self.client.post(reverse(
            'documentos:alternar_retiro_version',
            args=['procedimientos_generales', self.doc_activo.uuid, dist.id],
        ))
        self.assertEqual(resp.status_code, 302)
        dist.refresh_from_db()
        self.assertTrue(dist.retiro_version_anterior)

    def test_pagina_firmar_interna(self):
        self._crear(internos=[self.user_trabajador], metodo='sistema')
        dist = DistribucionDocumento.objects.get(documento=self.doc_activo)
        self.client.logout()
        self.client.login(username='trab01', password='testpass123')

        resp = self.client.get(reverse(
            'documentos:firmar_distribucion',
            args=['procedimientos_generales', self.doc_activo.uuid, dist.id],
        ))
        self.assertEqual(resp.status_code, 200)

        # Crear PIN desde la vista.
        resp = self.client.post(reverse(
            'documentos:firmar_distribucion',
            args=['procedimientos_generales', self.doc_activo.uuid, dist.id],
        ), {'accion': 'crear_pin', 'pin': '1234', 'pin2': '1234'})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(hasattr(self.user_trabajador, 'firma_credencial'))

        # Firmar usando un OTP generado por el servicio.
        firma = FirmaElectronica.objects.get(distribucion=dist)
        codigo = svc_firmas.generar_otp(firma)
        resp = self.client.post(reverse(
            'documentos:firmar_distribucion',
            args=['procedimientos_generales', self.doc_activo.uuid, dist.id],
        ), {'accion': 'firmar', 'codigo': codigo, 'pin': '1234'})
        self.assertEqual(resp.status_code, 302)
        firma.refresh_from_db()
        self.assertEqual(firma.estado, 'firmada')

    def test_firmar_invitacion_publica(self):
        self._crear(externos=[self._externo()], metodo='sistema')
        dist = DistribucionDocumento.objects.get(documento=self.doc_activo)
        firma = FirmaElectronica.objects.get(distribucion=dist)
        token = secrets.token_urlsafe(24)
        InvitacionFirma.objects.create(
            firma=firma,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expira=timezone.now() + timedelta(days=7),
        )

        self.client.logout()
        url = reverse('documentos:firmar_invitacion', args=[token])
        self.assertEqual(self.client.get(url).status_code, 200)
        resp = self.client.post(url, {'consentimiento': 'on'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Firma registrada')
        firma.refresh_from_db()
        self.assertEqual(firma.estado, 'firmada')

    def test_firmar_invitacion_token_invalido(self):
        resp = self.client.get(
            reverse('documentos:firmar_invitacion', args=['no-existe'])
        )
        self.assertEqual(resp.status_code, 404)


# =============================================================================
# Comando integrar_formatos_sgc
# =============================================================================

@override_settings(MEDIA_ROOT=TEST_MEDIA)
class ComandoIntegrarFormatosTest(BaseDocumentosTest):
    def test_dry_run_no_escribe(self):
        for codigo in ('SGC-FOR-002',):
            Formato.objects.filter(codigo=codigo).delete()
        out = io.StringIO()
        call_command(
            'integrar_formatos_sgc', solo='002',
            plantilla=plantilla_sgc002(), stdout=out,
        )
        self.assertFalse(Formato.objects.filter(codigo='SGC-FOR-002').exists())
        self.assertIn('DRY-RUN', out.getvalue())

    def test_apply_crea_formato_y_estructura(self):
        Formato.objects.filter(codigo='SGC-FOR-002').delete()
        with patch('documentos.tasks.convertir_formato_a_pdf.delay', return_value=None):
            call_command(
                'integrar_formatos_sgc', solo='002', apply=True, verbosity=0,
                plantilla=plantilla_sgc002(), stdout=io.StringIO(),
            )

        formato = Formato.objects.get(codigo='SGC-FOR-002')
        self.assertTrue(formato.activo)
        self.assertFalse(formato.aplica_creacion)
        self.assertFalse(formato.aplica_modificacion)
        self.assertFalse(formato.aplica_eliminacion)
        self.assertTrue(formato.plantilla_xlsx)
        self.assertTrue(os.path.exists(formato.plantilla_xlsx.path))

        claves = set(
            CampoFormato.objects.filter(seccion__formato=formato)
            .values_list('clave', flat=True)
        )
        self.assertTrue({
            'tipo_informacion', 'origen_informacion', 'num_identificacion',
            'titulo', 'edicion', 'version', 'fecha_vigencia', 'destinatarios',
        } <= claves)
        tabla = CampoFormato.objects.get(
            seccion__formato=formato, clave='destinatarios',
        )
        self.assertEqual(tabla.config_tabla['max_filas'], 33)
        self.assertEqual(tabla.config_tabla['fila_inicio'], 19)

    def test_apply_es_idempotente(self):
        with patch('documentos.tasks.convertir_formato_a_pdf.delay', return_value=None):
            call_command(
                'integrar_formatos_sgc', solo='002', apply=True, verbosity=0,
                plantilla=plantilla_sgc002(), stdout=io.StringIO(),
            )
            primero = Formato.objects.get(codigo='SGC-FOR-002').id
            call_command(
                'integrar_formatos_sgc', solo='002', apply=True, verbosity=0,
                plantilla=plantilla_sgc002(), stdout=io.StringIO(),
            )
        self.assertEqual(Formato.objects.filter(codigo='SGC-FOR-002').count(), 1)
        self.assertEqual(Formato.objects.get(codigo='SGC-FOR-002').id, primero)
        # Sin duplicar campos.
        self.assertEqual(
            CampoFormato.objects.filter(
                seccion__formato_id=primero, clave='destinatarios',
            ).count(), 1,
        )
