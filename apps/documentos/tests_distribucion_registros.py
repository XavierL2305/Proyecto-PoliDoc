"""Tests de distribución de registros, acuse firmado y enlace seguro (P1).

Cubre:

* alta de distribuciones de ``RegistroFormato`` (aprobado, interno/externo),
* correos multipart con banner de clasificación,
* acuse con PIN (interno) y token + consentimiento (externo),
* descarga por enlace seguro (hasta la caducidad, incluso tras firmar),
* acuse físico escaneado (impreso), revocación y constancia.
"""
import hashlib
import io
import os
import tempfile
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import openpyxl

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from autenticacion.models import Notificacion
from documentos.models import (
    CampoFormato, DistribucionDocumento, DistribucionRegistro, Documento,
    FirmaElectronica, Formato, InvitacionFirma, RegistroFormato, SeccionFormato,
)
from documentos.services import distribucion as svc_dist
from documentos.services import distribucion_registros as svc_reg
from documentos.services import firmas as svc_firmas
from documentos.services.avisos import (
    AVISOS, COLORES, aviso_texto, banner_html,
)
from documentos.services.distribucion import DestinatarioExterno

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_dist_reg_')

PDF_BYTES = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n'


def _xlsx_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws['A1'] = ''
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BaseDistribucionRegistrosTest(BaseDocumentosTest):
    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            super().setUpTestData()
        with patch('documentos.tasks.convertir_formato_a_pdf.delay',
                   return_value=None):
            cls.formato = Formato.objects.create(
                codigo='SGC-FOR-010', nombre='Plan de Acción ante NC',
                version='1', edicion='1', activo=True, permite_registros=True,
            )
            cls.formato.plantilla_xlsx.save(
                'for-010.xlsx',
                SimpleUploadedFile('for-010.xlsx', _xlsx_bytes()),
                save=True,
            )
        seccion = SeccionFormato.objects.create(
            formato=cls.formato, titulo='Datos', orden=1,
        )
        CampoFormato.objects.create(
            seccion=seccion, clave='descripcion', etiqueta='Descripción',
            tipo='texto', origen='usuario', celda_excel='A1',
            requerido=True, orden=1,
        )

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.formato.refresh_from_db()

    # -- helpers ---------------------------------------------------------
    def _registro(self, estado='aprobado', documento=None,
                  titulo='Plan de acción'):
        documento = documento or self.doc_activo
        return RegistroFormato.objects.create(
            formato=self.formato,
            formato_codigo=self.formato.codigo,
            formato_version=self.formato.version,
            formato_edicion=self.formato.edicion,
            contexto_tipo='documento',
            contexto_id=str(documento.uuid),
            titulo=titulo,
            fecha=date(2026, 11, 10),
            responsable=self.user_trabajador,
            estado=estado,
            aprobador=self.user_jefe_ti,
            respuestas={'descripcion': titulo},
        )

    def _externo(self, nombre='Proveedor Externo',
                 email='proveedor@example.com',
                 cargo='Gerente', organizacion='ACME'):
        return DestinatarioExterno(
            nombre=nombre, email=email, cargo=cargo, organizacion=organizacion,
        )

    def _crear(self, registro=None, internos=(), externos=(),
               metodo='sistema', adjuntar_pdf=False):
        return svc_reg.crear_distribuciones_registro(
            registro or self._registro(),
            internos=internos, externos=externos, metodo=metodo,
            observaciones='', distribuido_por=self.user_trabajador,
            adjuntar_pdf=adjuntar_pdf, request=None,
        )

    def _invitacion(self, dist, segundos=7 * 24 * 3600, usada=False):
        """Crea una invitación con token conocido para probar descargas/firma."""
        firma = dist.firmas.filter(tipo='externa').first()
        token = f'tok-{uuid.uuid4().hex}'
        invitacion = InvitacionFirma.objects.create(
            firma=firma, token_hash=_hash(token),
            expira=timezone.now() + timedelta(seconds=segundos),
            usado=usada,
        )
        return invitacion, token

    def _dist_documento(self, documento=None, revocado=False):
        documento = documento or self.doc_th
        dist = DistribucionDocumento.objects.create(
            documento=documento, usuario=None, metodo='email',
            distribuido_por=self.user_admin,
            nombre_externo='Externo Doc',
            email_externo='externo@example.com',
            revocado=revocado,
        )
        firma = svc_firmas.crear_firma(
            dist, tipo='externa',
            firmante_clave=FirmaElectronica.clave_externa('externo@example.com'),
            firmante_nombre='Externo Doc',
            firmante_email='externo@example.com',
        )
        return dist, firma


# =============================================================================
# Avisos de clasificación
# =============================================================================

class AvisosTest(BaseDocumentosTest):
    def test_cada_clasificacion_tiene_aviso_y_color(self):
        for clasificacion in ('publico', 'interno', 'confidencial', 'restringido'):
            self.assertIn(clasificacion, AVISOS)
            self.assertIn(clasificacion, COLORES)

    def test_textos_clave(self):
        self.assertIn(
            'no implica autorización', aviso_texto('publico'),
        )
        self.assertIn('uso interno', aviso_texto('interno'))
        self.assertIn('enlace seguro', aviso_texto('restringido'))
        self.assertNotIn('<', aviso_texto('confidencial'))

    def test_banner_html_usa_color_y_titulo(self):
        html = str(banner_html('confidencial'))
        self.assertIn('#e65100', html)
        self.assertIn('Documento confidencial.', html)
        self.assertIn('border-left:6px solid', html)

    def test_clasificacion_desconocida_cae_en_interno(self):
        self.assertEqual(aviso_texto('otra'), aviso_texto('interno'))
        self.assertIn('#1565c0', str(banner_html('otra')))


# =============================================================================
# Alta de distribuciones
# =============================================================================

class CreacionDistribucionRegistroTest(BaseDistribucionRegistrosTest):
    def test_solo_registros_aprobados(self):
        registro = self._registro(estado='en_revision')
        with self.assertRaises(ValueError):
            self._crear(registro=registro)

    def test_sin_destinatarios_es_error(self):
        with self.assertRaises(ValueError):
            self._crear()

    def test_interno_sistema_deja_acuse_pendiente_y_notifica(self):
        resultado = self._crear(internos=[self.user_coordinador])

        self.assertEqual(resultado.creadas, 1)
        dist = DistribucionRegistro.objects.get()
        self.assertFalse(dist.es_externo)
        self.assertEqual(dist.estado_acuse, 'pendiente')
        self.assertIsNotNone(dist.fecha_entrega)
        firma = dist.firmas.get()
        self.assertEqual(firma.tipo, 'interna')
        self.assertEqual(firma.origen_tipo, 'distribucion_registro')
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_coordinador, tipo='registro_distribuido',
        ).exists())

    def test_externo_email_crea_invitacion_y_envia_correo_con_banner(self):
        resultado = self._crear(
            externos=[self._externo()], metodo='email',
        )

        self.assertEqual(resultado.creadas, 1)
        self.assertEqual(resultado.invitaciones, 1)
        dist = DistribucionRegistro.objects.get()
        self.assertTrue(dist.es_externo)
        firma = dist.firmas.get()
        self.assertEqual(firma.tipo, 'externa')
        invitacion = InvitacionFirma.objects.get(firma=firma)
        self.assertTrue(invitacion.email_enviado)
        self.assertEqual(len(invitacion.token_hash), 64)

        self.assertEqual(len(mail.outbox), 1)
        mensaje = mail.outbox[0]
        self.assertIn('Acuse de distribución', mensaje.subject)
        html = mensaje.alternatives[0][0]
        self.assertEqual(mensaje.alternatives[0][1], 'text/html')
        self.assertIn('#1565c0', html)  # banner interno (contexto sin restricción)
        self.assertIn('descargar el archivo', mensaje.body)

    def test_interno_email_adjunta_html_y_marca_enviado(self):
        self.user_coordinador.email = 'coord@example.com'
        self.user_coordinador.save(update_fields=['email'])

        self._crear(internos=[self.user_coordinador], metodo='email')

        dist = DistribucionRegistro.objects.get()
        self.assertTrue(dist.enviado)
        self.assertFalse(dist.adjunto_pdf_enviado)
        self.assertEqual(dist.motivo_sin_adjunto, 'Adjunto no solicitado')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].alternatives[0][1], 'text/html')

    def test_adjunto_restringido_no_se_envia(self):
        documento = Documento.objects.create(
            tipo=self.tipo_procedimiento, nombre='Restringido',
            codigo='TI-PRG-099', estado='activo',
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
            clasificacion='restringido', version_actual='1.0',
        )
        registro = self._registro(documento=documento)
        self.user_coordinador.email = 'coord@example.com'
        self.user_coordinador.save(update_fields=['email'])

        self._crear(
            registro=registro, internos=[self.user_coordinador],
            metodo='email', adjuntar_pdf=True,
        )

        dist = DistribucionRegistro.objects.get()
        self.assertFalse(dist.adjunto_pdf_enviado)
        self.assertIn('restringido', dist.motivo_sin_adjunto)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('#b71c1c', html)  # banner restringido

    def test_permiso_gestion(self):
        registro = self._registro()
        self.assertTrue(
            svc_reg.puede_gestionar_distribucion_registro(
                self.user_trabajador, registro,
            ),
        )
        self.assertTrue(
            svc_reg.puede_gestionar_distribucion_registro(
                self.user_admin, registro,
            ),
        )
        self.assertTrue(
            svc_reg.puede_gestionar_distribucion_registro(
                self.user_jefe_ti, registro,
            ),
        )
        self.assertFalse(
            svc_reg.puede_gestionar_distribucion_registro(
                self.user_coordinador, registro,
            ),
        )


# =============================================================================
# Acuse con PIN (interno) y con token (externo)
# =============================================================================

class AcuseTest(BaseDistribucionRegistrosTest):
    def test_acuse_interno_con_pin(self):
        self._crear(internos=[self.user_coordinador])
        dist = DistribucionRegistro.objects.get()
        self.crear_pin_firma(self.user_coordinador)

        firma = svc_firmas.firmar(
            dist, usuario=self.user_coordinador, pin='1234', metodo='pin',
        )

        dist.refresh_from_db()
        self.assertEqual(firma.estado, 'firmada')
        self.assertEqual(firma.origen_tipo, 'distribucion_registro')
        self.assertEqual(dist.estado_acuse, 'acusado')
        self.assertIsNotNone(dist.fecha_acuse)
        self.assertEqual(dist.firma_id, firma.id)
        self.assertEqual(len(firma.hash_contenido), 64)

    def test_acuse_interno_sin_pin_falla(self):
        self._crear(internos=[self.user_coordinador])
        dist = DistribucionRegistro.objects.get()
        with self.assertRaises(svc_firmas.ErrorFirma):
            svc_firmas.firmar(
                dist, usuario=self.user_coordinador, pin='', metodo='pin',
            )
        dist.refresh_from_db()
        self.assertEqual(dist.estado_acuse, 'pendiente')

    def test_otro_interno_no_puede_acusar(self):
        self._crear(internos=[self.user_coordinador])
        dist = DistribucionRegistro.objects.get()
        self.crear_pin_firma(self.user_trabajador_otro)
        with self.assertRaises(svc_firmas.ErrorFirma):
            svc_firmas.firmar(
                dist, usuario=self.user_trabajador_otro, pin='1234',
                metodo='pin',
            )

    def test_acuse_externo_por_token_con_consentimiento(self):
        self._crear(externos=[self._externo()], metodo='email')
        dist = DistribucionRegistro.objects.get()
        invitacion, token = self._invitacion(dist)

        pagina = self.client.get(reverse(
            'documentos:firmar_invitacion', args=[token],
        ))
        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, 'Acuse de conformidad')

        respuesta = self.client.post(
            reverse('documentos:firmar_invitacion', args=[token]),
            {'consentimiento': 'on'},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Firma registrada')

        dist.refresh_from_db()
        invitacion.refresh_from_db()
        self.assertTrue(invitacion.usado)
        self.assertEqual(dist.estado_acuse, 'acusado')
        self.assertIsNotNone(dist.firma_id)

    def test_firma_externa_sin_consentimiento_falla(self):
        self._crear(externos=[self._externo()], metodo='email')
        dist = DistribucionRegistro.objects.get()
        _, token = self._invitacion(dist)

        respuesta = self.client.post(
            reverse('documentos:firmar_invitacion', args=[token]), {},
        )
        self.assertContains(respuesta, 'Debes aceptar')
        dist.refresh_from_db()
        self.assertEqual(dist.estado_acuse, 'pendiente')


# =============================================================================
# Enlace seguro de descarga
# =============================================================================

class EnlaceSeguroTest(BaseDistribucionRegistrosTest):
    def test_descarga_registro_y_sigue_disponible_tras_firmar(self):
        self._crear(externos=[self._externo()], metodo='email')
        dist = DistribucionRegistro.objects.get()
        dist.registro.archivo_pdf.save(
            'registro.pdf', SimpleUploadedFile('registro.pdf', PDF_BYTES),
            save=True,
        )
        _, token = self._invitacion(dist)

        descarga = self.client.get(reverse(
            'documentos:descarga_invitacion', args=[token],
        ))
        self.assertEqual(descarga.status_code, 200)
        self.assertEqual(descarga['Content-Disposition'].split(';')[0],
                         'attachment')
        self.assertIn('no-store', descarga['Cache-Control'])

        self.client.post(
            reverse('documentos:firmar_invitacion', args=[token]),
            {'consentimiento': 'on'},
        )
        posterior = self.client.get(reverse(
            'documentos:descarga_invitacion', args=[token],
        ))
        self.assertEqual(posterior.status_code, 200)

    def test_descarga_documento(self):
        dist, _ = self._dist_documento()
        _, token = self._invitacion(dist)

        descarga = self.client.get(reverse(
            'documentos:descarga_invitacion', args=[token],
        ))
        self.assertEqual(descarga.status_code, 200)
        self.assertEqual(descarga['X-Content-Type-Options'], 'nosniff')

    def test_token_invalido_404(self):
        self.assertEqual(self.client.get(reverse(
            'documentos:descarga_invitacion', args=['no-existe'],
        )).status_code, 404)

    def test_token_expirado_404_y_marca_vencido(self):
        self._crear(externos=[self._externo()], metodo='email')
        dist = DistribucionRegistro.objects.get()
        _, token = self._invitacion(dist, segundos=-60)

        self.assertEqual(self.client.get(reverse(
            'documentos:descarga_invitacion', args=[token],
        )).status_code, 404)

        pagina = self.client.get(reverse(
            'documentos:firmar_invitacion', args=[token],
        ))
        self.assertContains(pagina, 'expiró')
        dist.refresh_from_db()
        self.assertEqual(dist.estado_acuse, 'vencido')

    def test_revocada_bloquea_descarga_y_firma(self):
        self._crear(externos=[self._externo()], metodo='email')
        dist = DistribucionRegistro.objects.get()
        _, token = self._invitacion(dist)
        dist.revocado = True
        dist.save(update_fields=['revocado'])

        self.assertEqual(self.client.get(reverse(
            'documentos:descarga_invitacion', args=[token],
        )).status_code, 404)
        pagina = self.client.get(reverse(
            'documentos:firmar_invitacion', args=[token],
        ))
        self.assertEqual(pagina.status_code, 403)
        self.assertContains(pagina, 'revocada', status_code=403)


# =============================================================================
# Acuse escaneado, revocación y constancia
# =============================================================================

class AcuseEscaneadoTest(BaseDistribucionRegistrosTest):
    def test_subir_acuse_pdf_marca_acusado(self):
        self._crear(externos=[self._externo()], metodo='impreso')
        dist = DistribucionRegistro.objects.get()

        svc_reg.subir_acuse(
            dist, SimpleUploadedFile('acuse.pdf', PDF_BYTES),
            self.user_trabajador,
        )

        dist.refresh_from_db()
        self.assertEqual(dist.estado_acuse, 'acusado')
        self.assertIsNotNone(dist.fecha_acuse)
        self.assertTrue(dist.acuse_archivo.name.endswith('.pdf'))

    def test_acuse_invalido_se_rechaza(self):
        self._crear(externos=[self._externo()], metodo='impreso')
        dist = DistribucionRegistro.objects.get()

        with self.assertRaises(ValidationError):
            svc_reg.subir_acuse(
                dist, SimpleUploadedFile('acuse.pdf', b'no soy un pdf'),
                self.user_trabajador,
            )
        dist.refresh_from_db()
        self.assertEqual(dist.estado_acuse, 'pendiente')

    def test_acuse_documento_impreso(self):
        svc_dist.crear_distribuciones(
            self.doc_th, internos=[self.user_trabajador_otro],
            metodo='impreso', distribuido_por=self.user_admin,
            tipo='formulario',
        )
        dist = DistribucionDocumento.objects.get(documento=self.doc_th)

        svc_dist.subir_acuse_documento(
            dist, SimpleUploadedFile('acuse.pdf', PDF_BYTES),
            self.user_admin,
        )
        dist.refresh_from_db()
        self.assertTrue(dist.acuse_archivo.name.endswith('.pdf'))

    def test_acuse_documento_no_aplica_a_correo(self):
        svc_dist.crear_distribuciones(
            self.doc_th, internos=[self.user_trabajador_otro],
            metodo='sistema', distribuido_por=self.user_admin,
            tipo='formulario',
        )
        dist = DistribucionDocumento.objects.get(documento=self.doc_th)
        with self.assertRaises(ValueError):
            svc_dist.subir_acuse_documento(
                dist, SimpleUploadedFile('acuse.pdf', PDF_BYTES),
                self.user_admin,
            )


class RevocacionTest(BaseDistribucionRegistrosTest):
    def test_revocar_pendiente_bloquea(self):
        self._crear(externos=[self._externo()], metodo='email')
        dist = DistribucionRegistro.objects.get()

        svc_reg.revocar_distribucion_registro(
            dist, self.user_trabajador, 'Error de envío',
        )
        dist.refresh_from_db()
        self.assertTrue(dist.revocado)
        self.assertEqual(dist.estado_acuse, 'revocado')
        self.assertEqual(dist.motivo_revocacion, 'Error de envío')

        with self.assertRaises(ValueError):
            svc_reg.revocar_distribucion_registro(
                dist, self.user_trabajador, 'otra vez',
            )

    def test_revocar_acuse_ya_firmado_conserva_evidencia(self):
        self._crear(internos=[self.user_coordinador])
        dist = DistribucionRegistro.objects.get()
        self.crear_pin_firma(self.user_coordinador)
        svc_firmas.firmar(
            dist, usuario=self.user_coordinador, pin='1234', metodo='pin',
        )

        svc_reg.revocar_distribucion_registro(dist, self.user_admin, '')
        dist.refresh_from_db()
        self.assertTrue(dist.revocado)
        self.assertEqual(dist.estado_acuse, 'acusado')
        self.assertIsNotNone(dist.firma_id)


class ConstanciaTest(BaseDistribucionRegistrosTest):
    def _con_distribuciones(self):
        registro = self._registro()
        self._crear(
            registro=registro, internos=[self.user_coordinador],
            externos=[self._externo()], metodo='email',
        )
        return registro

    def test_csv_incluye_destinatarios_y_aviso(self):
        registro = self._con_distribuciones()
        contenido = svc_reg.constancia_csv(registro).decode('utf-8-sig')
        self.assertIn('Constancia de distribución y acuses', contenido)
        self.assertIn('Proveedor Externo', contenido)
        self.assertIn('Aviso', contenido)
        self.assertIn('Destinatario', contenido)

    def test_xlsx_legible(self):
        registro = self._con_distribuciones()
        contenido = svc_reg.constancia_xlsx(registro)
        ws = openpyxl.load_workbook(io.BytesIO(contenido)).active
        valores = [
            celda.value for fila in ws.iter_rows() for celda in fila
        ]
        self.assertIn('Destinatario', valores)
        self.assertTrue(any(
            isinstance(v, str) and 'Proveedor Externo' in v for v in valores
        ))

    def test_vista_constancia_csv_y_pdf(self):
        registro = self._con_distribuciones()
        self.client.login(username='trab01', password='testpass123')

        csv_resp = self.client.get(reverse(
            'documentos:registro_constancia',
            args=[registro.uuid, 'csv'],
        ))
        self.assertEqual(csv_resp.status_code, 200)

        def _convertir(path, reparar_sincrono=False):
            salida = f'{path}.pdf'
            with open(salida, 'wb') as fh:
                fh.write(PDF_BYTES)
            return salida

        with patch('documentos.tasks.convertir_a_pdf_sync',
                   side_effect=_convertir):
            pdf_resp = self.client.get(reverse(
                'documentos:registro_constancia',
                args=[registro.uuid, 'pdf'],
            ))
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertIn('attachment', pdf_resp['Content-Disposition'])


# =============================================================================
# Vistas de distribución
# =============================================================================

class VistasDistribucionTest(BaseDistribucionRegistrosTest):
    def test_detalle_muestra_boton_y_historial(self):
        registro = self._registro()
        self.client.login(username='trab01', password='testpass123')
        svc_reg.crear_distribuciones_registro(
            registro, internos=[self.user_coordinador], metodo='sistema',
            observaciones='', distribuido_por=self.user_trabajador,
        )

        respuesta = self.client.get(reverse(
            'documentos:registro_detalle', args=[registro.uuid],
        ))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Distribuir registro')
        self.assertContains(respuesta, 'Distribución y acuses')
        self.assertContains(respuesta, 'Constancia')

    def test_sin_permiso_no_ve_boton(self):
        registro = self._registro()
        # coord01 puede VER el registro por la distribución del documento
        # de contexto, pero no gestionar su distribución.
        svc_dist.crear_distribuciones(
            self.doc_activo, internos=[self.user_coordinador],
            metodo='sistema', distribuido_por=self.user_jefe_ti,
            tipo='procedimientos_generales',
        )
        self.client.login(username='coord01', password='testpass123')
        respuesta = self.client.get(reverse(
            'documentos:registro_detalle', args=[registro.uuid],
        ))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'Distribuir registro')

    def test_post_distribuir_crea_acuse(self):
        registro = self._registro()
        self.client.login(username='trab01', password='testpass123')

        respuesta = self.client.post(
            reverse('documentos:registro_distribuir', args=[registro.uuid]),
            {
                'usuarios': [str(self.user_coordinador.pk)],
                'externos': '[]',
                'metodo': 'sistema',
                'observaciones': 'prueba',
            },
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(DistribucionRegistro.objects.filter(
            registro=registro,
        ).exists())

    def test_post_acuse_escaneado_por_vista(self):
        self._crear(externos=[self._externo()], metodo='impreso')
        dist = DistribucionRegistro.objects.get()
        self.client.login(username='trab01', password='testpass123')

        respuesta = self.client.post(
            reverse('documentos:registro_subir_acuse', args=[
                dist.registro.uuid, dist.id,
            ]),
            {'acuse': SimpleUploadedFile('acuse.pdf', PDF_BYTES)},
        )
        self.assertEqual(respuesta.status_code, 302)
        dist.refresh_from_db()
        self.assertEqual(dist.estado_acuse, 'acusado')

    def test_revocar_por_vista(self):
        self._crear(externos=[self._externo()], metodo='email')
        dist = DistribucionRegistro.objects.get()
        self.client.login(username='trab01', password='testpass123')

        self.client.post(
            reverse('documentos:registro_revocar_distribucion', args=[
                dist.registro.uuid, dist.id,
            ]),
            {'motivo': 'duplicado'},
        )
        dist.refresh_from_db()
        self.assertTrue(dist.revocado)
