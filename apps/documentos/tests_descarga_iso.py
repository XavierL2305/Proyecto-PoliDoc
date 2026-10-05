"""Tests de la descarga controlada ISO (clasificación, vigencia, distribución,
doble control, trazabilidad) y de la vista de resultados de búsqueda.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests_base import MONGO_PATCH_TARGET, BaseDocumentosTest
from documentos.models import (
    AprobacionDescarga,
    DistribucionDocumento,
    Documento,
    SolicitudDescarga,
)
from documentos.permissions import (
    descarga_autorizada,
    puede_descargar,
    puede_ver_documento,
)


class BaseDescargaTest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _doc(self, **extra):
        datos = dict(
            tipo=self.tipo_formulario, nombre='Doc ISO', codigo='TI-FOR-900',
            estado='activo', propietario=self.user_trabajador,
            departamento=self.depto_ti, version_actual='1.0',
            archivo=SimpleUploadedFile('d.pdf', b'%PDF-1.4 contenido'),
            tipo_archivo='pdf',
        )
        datos.update(extra)
        return Documento.objects.create(**datos)

    def _solicitud_aprobada(self, documento, usuario, **extra):
        datos = dict(
            documento=documento, solicitado_por=usuario, motivo='x',
            estado='aprobada', version_aprobada=documento.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=30),
        )
        datos.update(extra)
        return SolicitudDescarga.objects.create(**datos)


class DescargaPermisosTest(BaseDescargaTest):

    def test_publico_descarga_directa(self):
        self.assertTrue(puede_descargar(self.user_trabajador_otro, self.doc_publico))

    def test_propietario_interno_descarga_directa(self):
        doc = self._doc()  # propietario trabaj01, interno
        self.assertTrue(puede_descargar(self.user_trabajador, doc))

    def test_propietario_confidencial_no_descarga_directa(self):
        doc = self._doc(clasificacion='confidencial')
        self.assertTrue(puede_ver_documento(self.user_trabajador, doc))
        self.assertFalse(puede_descargar(self.user_trabajador, doc))

    def test_jefe_descarga_directa_solo_interno(self):
        interno = self._doc(codigo='TI-FOR-901', propietario=self.user_jefe_ti)
        confidencial = self._doc(
            codigo='TI-FOR-902', propietario=self.user_jefe_ti,
            clasificacion='confidencial',
        )
        self.assertTrue(puede_descargar(self.user_jefe_ti, interno))
        self.assertFalse(puede_descargar(self.user_jefe_ti, confidencial))

    def test_admin_descarga_directa_restringido(self):
        doc = self._doc(clasificacion='restringido')
        self.assertTrue(puede_descargar(self.user_admin, doc))

    def test_distribucion_vigente_da_descarga_y_revocada_no(self):
        doc = self._doc(clasificacion='confidencial')
        dist = DistribucionDocumento.objects.create(
            documento=doc, usuario=self.user_trabajador_otro,
            distribuido_por=self.user_jefe_ti,
        )
        self.assertTrue(puede_descargar(self.user_trabajador_otro, doc))
        dist.revocado = True
        dist.save(update_fields=['revocado'])
        self.assertFalse(puede_descargar(self.user_trabajador_otro, doc))

    def test_descarga_autorizada_vigente_y_de_la_version(self):
        doc = self._doc(clasificacion='restringido')
        solicitud = self._solicitud_aprobada(doc, self.user_trabajador_otro)
        self.assertTrue(descarga_autorizada(doc, self.user_trabajador_otro))

        solicitud.fecha_expiracion = timezone.now() - timedelta(days=1)
        solicitud.save(update_fields=['fecha_expiracion'])
        self.assertFalse(descarga_autorizada(doc, self.user_trabajador_otro))

        solicitud.fecha_expiracion = timezone.now() + timedelta(days=1)
        solicitud.version_aprobada = '0.9'
        solicitud.save(update_fields=['fecha_expiracion', 'version_aprobada'])
        self.assertFalse(descarga_autorizada(doc, self.user_trabajador_otro))


class SolicitarDescargaISOTest(BaseDescargaTest):

    def test_motivo_obligatorio(self):
        doc = self._doc(clasificacion='confidencial', propietario=self.user_jefe_ti)
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(
            reverse('documentos:solicitar_descarga', args=[doc.tipo_id, doc.uuid]),
            data={'motivo': ''},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SolicitudDescarga.objects.filter(documento=doc).exists())

    def test_restringido_exige_doble_aprobacion(self):
        doc = self._doc(clasificacion='restringido', propietario=self.user_jefe_ti)
        self.client.login(username='trab01', password='testpass123')
        self.client.post(
            reverse('documentos:solicitar_descarga', args=[doc.tipo_id, doc.uuid]),
            data={'motivo': 'Necesito revisarlo'},
        )
        solicitud = SolicitudDescarga.objects.get(documento=doc)
        roles = set(solicitud.aprobaciones.values_list('rol_aprobador', flat=True))
        self.assertEqual(roles, {'jefe', 'autorizador'})


class AprobarDescargaISOTest(BaseDescargaTest):

    def setUp(self):
        super().setUp()
        self.doc = self._doc(
            clasificacion='restringido', propietario=self.user_jefe_ti,
            codigo='TI-FOR-RE',
        )
        self.solicitud = SolicitudDescarga.objects.create(
            documento=self.doc, solicitado_por=self.user_trabajador,
            motivo='Auditoría interna', estado='pendiente',
        )
        AprobacionDescarga.objects.create(
            solicitud=self.solicitud, aprobador=self.user_jefe_ti, rol_aprobador='jefe',
        )
        AprobacionDescarga.objects.create(
            solicitud=self.solicitud, aprobador=self.user_admin, rol_aprobador='autorizador',
        )
        self.url = reverse('documentos:aprobar_descarga', args=[self.solicitud.id])

    def test_doble_control_y_distribucion(self):
        # Primer paso: jefe aprueba → sigue pendiente.
        self.client.login(username='jefe_ti', password='testpass123')
        self.client.post(self.url, {'accion': 'aprobar', 'observaciones': 'ok'})
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'pendiente')
        self.assertFalse(DistribucionDocumento.objects.filter(documento=self.doc).exists())

        # Segundo paso: admin autoriza → aprobada + distribución + vigencia.
        self.client.login(username='admin01', password='testpass123')
        self.client.post(self.url, {'accion': 'aprobar', 'observaciones': 'ok'})
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'aprobada')
        self.assertEqual(self.solicitud.version_aprobada, self.doc.version_actual)
        self.assertIsNotNone(self.solicitud.fecha_expiracion)
        dist = DistribucionDocumento.objects.get(documento=self.doc)
        self.assertEqual(dist.usuario, self.user_trabajador)
        self.assertFalse(dist.revocado)

    def test_rechazo_no_distribuye(self):
        self.client.login(username='jefe_ti', password='testpass123')
        self.client.post(self.url, {'accion': 'rechazar', 'observaciones': 'no'})
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'rechazada')
        self.assertFalse(DistribucionDocumento.objects.filter(documento=self.doc).exists())


class DescargarArchivoISOTest(BaseDescargaTest):

    def test_solicitud_vigente_permite_descargar(self):
        doc = self._doc(clasificacion='confidencial', propietario=self.user_jefe_ti)
        self._solicitud_aprobada(doc, self.user_trabajador)
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            reverse('documentos:descargar_archivo', args=[doc.tipo_id, doc.uuid]),
        )
        self.assertEqual(resp.status_code, 200)

    def test_solicitud_vencida_bloquea(self):
        doc = self._doc(clasificacion='confidencial', propietario=self.user_jefe_ti)
        self._solicitud_aprobada(
            doc, self.user_trabajador,
            fecha_expiracion=timezone.now() - timedelta(days=1),
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            reverse('documentos:descargar_archivo', args=[doc.tipo_id, doc.uuid]),
        )
        self.assertEqual(resp.status_code, 302)

    def test_propietario_interno_registra_distribucion(self):
        doc = self._doc()  # propietario trabaj01, interno
        self.assertFalse(DistribucionDocumento.objects.filter(documento=doc).exists())
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            reverse('documentos:descargar_archivo', args=[doc.tipo_id, doc.uuid]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(DistribucionDocumento.objects.filter(documento=doc).exists())


class BusquedaResultadosTest(BaseDescargaTest):

    def test_vista_requiere_login(self):
        resp = self.client.get(reverse('documentos:buscar'), {'q': 'algo'})
        self.assertIn(resp.status_code, [302, 403])

    def test_trabajador_encuentra_publico_de_otro_depto(self):
        doc = self._doc(
            codigo='TH-FOR-777', nombre='Formulario Publico TH',
            clasificacion='publico', propietario=self.user_jefe_th,
            departamento=self.depto_th,
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(reverse('documentos:buscar'), {'q': 'Publico TH'})
        self.assertContains(resp, doc.codigo)

    def test_trabajador_no_encuentra_interno_de_otro_depto(self):
        doc = self._doc(
            codigo='TH-FOR-778', nombre='Formulario Interno TH',
            clasificacion='interno', propietario=self.user_jefe_th,
            departamento=self.depto_th,
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(reverse('documentos:buscar'), {'q': 'Interno TH'})
        self.assertNotContains(resp, doc.codigo)

    def test_api_incluye_clasificacion(self):
        self._doc(codigo='TI-FOR-779', nombre='Doc Clasificado')
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            '/documentos/api/buscar-sugerencias/', {'q': 'Doc Clasificado'},
        )
        data = resp.json()['sugerencias']
        item = next(s for s in data if s['codigo'] == 'TI-FOR-779')
        self.assertEqual(item['clasificacion'], 'interno')
        self.assertIn('clasificacion_display', item)
        self.assertIn('clasificacion_color', item)


class TrazabilidadDescargaTest(BaseDescargaTest):
    """El ciclo de descarga comparte correlation_id y audita la distribución."""

    def _fake_mongo(self):
        store = {}
        db = MagicMock()
        db.__getitem__.side_effect = lambda name: store.setdefault(name, MagicMock())
        db.audit_logs = store.setdefault('audit_logs', MagicMock())
        return db, store

    def test_ciclo_comparte_correlacion_y_audita_distribucion(self):
        doc = self._doc(
            clasificacion='confidencial', propietario=self.user_jefe_ti,
            codigo='TI-FOR-RT',
        )
        solicitud = SolicitudDescarga.objects.create(
            documento=doc, solicitado_por=self.user_trabajador,
            motivo='Auditoría', estado='pendiente',
        )
        AprobacionDescarga.objects.create(
            solicitud=solicitud, aprobador=self.user_jefe_ti, rol_aprobador='jefe',
        )
        db, store = self._fake_mongo()
        self.client.login(username='jefe_ti', password='testpass123')
        with patch(MONGO_PATCH_TARGET, return_value=db):
            self.client.post(
                reverse('documentos:aprobar_descarga', args=[solicitud.id]),
                {'accion': 'aprobar', 'observaciones': 'ok'},
            )

        registros = [
            c[0][0] for c in store['audit_logs'].insert_one.call_args_list
        ]
        correlacion = f'descarga-{solicitud.id}'
        tipos = [r['accion']['tipo'] for r in registros]
        self.assertIn('distribucion', tipos)
        distribuciones = [r for r in registros if r['accion']['tipo'] == 'distribucion']
        self.assertEqual(distribuciones[0]['correlation_id'], correlacion)
