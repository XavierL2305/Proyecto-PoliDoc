"""Tests de la transferencia masiva (cobertura temporal por vacaciones).

Cubre el servicio que mueve propiedad, asignaciones y distribuciones de un
trabajador hacia un compañero del mismo departamento, y la vista de
configuración que la orquesta.
"""

from django.urls import reverse

from tests_base import BaseDocumentosTest

from documentos.models import (
    AsignacionDocumento, DistribucionDocumento, Documento,
)
from documentos.services.transferencia import (
    transferibles_de_usuario,
    transferir_documentos_usuario,
)


class _BaseTransferenciaMasiva(BaseDocumentosTest):
    """Fixtures locales: documentos/asignaciones/distribuciones del origen."""

    def setUp(self):
        super().setUp()
        # Documento ajeno al origen sobre el que recibe asignación/distribución.
        self.doc_ajeno = Documento.objects.create(
            tipo=self.tipo_procedimiento, nombre='Procedimiento Ajeno',
            codigo='TI-PRG-510', estado='activo', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
        )
        # El origen (trabajador) es coordinador de ese documento.
        self.asig_origen = AsignacionDocumento.objects.create(
            documento=self.doc_ajeno, coordinador=self.user_trabajador,
            jefe_asignador=self.user_jefe_ti,
            puede_leer=True, puede_editar=True, puede_eliminar=False,
            activa=True,
        )
        # Y tiene una distribución vigente del mismo documento.
        self.dist_origen = DistribucionDocumento.objects.create(
            documento=self.doc_ajeno, usuario=self.user_trabajador,
            distribuido_por=self.user_jefe_ti, metodo='sistema',
            observaciones='Copia de trabajo',
        )
        # Los dos documentos propios del origen (del fixture base).
        self.propios = list(
            Documento.objects.filter(
                propietario=self.user_trabajador, departamento=self.depto_ti,
            ).order_by('codigo')
        )


class TransferenciaMasivaServicioTest(_BaseTransferenciaMasiva):

    def test_transferibles_de_usuario_agrupa_los_tres_tipos(self):
        propiedad, asignaciones, distribuciones = transferibles_de_usuario(
            self.user_trabajador,
        )
        codigos_propios = {d.codigo for d in propiedad}
        self.assertIn('TI-FOR-001', codigos_propios)
        self.assertIn('TI-PRG-002', codigos_propios)
        self.assertEqual([a.id for a in asignaciones], [self.asig_origen.id])
        self.assertEqual([d.id for d in distribuciones], [self.dist_origen.id])

    def test_transfiere_propiedad_asignacion_y_distribucion(self):
        propiedad_ids = [d.id for d in self.propios]

        resumen = transferir_documentos_usuario(
            self.user_trabajador, self.user_coordinador, self.user_jefe_ti,
            propiedad_ids=propiedad_ids,
            asignacion_ids=[self.asig_origen.id],
            distribucion_ids=[self.dist_origen.id],
        )

        self.assertEqual(resumen['propiedad'], 2)
        self.assertEqual(resumen['asignaciones'], 1)
        self.assertEqual(resumen['asignaciones_fusionadas'], 0)
        self.assertEqual(resumen['distribuciones'], 1)
        self.assertEqual(resumen['total'], 4)

        # Propiedad movida.
        for doc in self.propios:
            doc.refresh_from_db()
            self.assertEqual(doc.propietario_id, self.user_coordinador.id)

        # Asignación reasignada al destino.
        self.asig_origen.refresh_from_db()
        self.assertEqual(self.asig_origen.coordinador_id, self.user_coordinador.id)
        self.assertTrue(self.asig_origen.activa)

        # Distribución: la del origen queda revocada y nace una nueva para el destino.
        self.dist_origen.refresh_from_db()
        self.assertTrue(self.dist_origen.revocado)
        self.assertEqual(self.dist_origen.revocado_por_id, self.user_jefe_ti.id)
        nueva = DistribucionDocumento.objects.filter(
            documento=self.doc_ajeno, usuario=self.user_coordinador,
            revocado=False,
        )
        self.assertEqual(nueva.count(), 1)
        self.assertEqual(nueva.first().distribuido_por_id, self.user_jefe_ti.id)
        self.assertEqual(nueva.first().observaciones, 'Copia de trabajo')

    def test_fusiona_asignacion_si_el_destino_ya_tiene_una(self):
        existente = AsignacionDocumento.objects.create(
            documento=self.doc_ajeno, coordinador=self.user_coordinador,
            jefe_asignador=self.user_jefe_ti,
            puede_leer=True, puede_editar=False, puede_eliminar=True,
            activa=True,
        )

        resumen = transferir_documentos_usuario(
            self.user_trabajador, self.user_coordinador, self.user_jefe_ti,
            asignacion_ids=[self.asig_origen.id],
        )

        self.assertEqual(resumen['asignaciones'], 1)
        self.assertEqual(resumen['asignaciones_fusionadas'], 1)
        # Fila del destino fusionada con OR de permisos.
        existente.refresh_from_db()
        self.assertTrue(existente.puede_leer)
        self.assertTrue(existente.puede_editar)
        self.assertTrue(existente.puede_eliminar)
        # La del origen queda inactiva como historial.
        self.asig_origen.refresh_from_db()
        self.assertFalse(self.asig_origen.activa)

    def test_notifica_al_destino_y_al_origen(self):
        transferir_documentos_usuario(
            self.user_trabajador, self.user_coordinador, self.user_jefe_ti,
            distribucion_ids=[self.dist_origen.id],
        )
        self.assertTrue(
            self.user_coordinador.notificaciones.filter(
                tipo='documento_distribuido',
            ).exists()
        )
        self.assertTrue(
            self.user_trabajador.notificaciones.filter(
                tipo='distribucion_revocada',
            ).exists()
        )

    def test_rechaza_mismo_usuario(self):
        with self.assertRaises(ValueError):
            transferir_documentos_usuario(
                self.user_trabajador, self.user_trabajador, self.user_jefe_ti,
                propiedad_ids=[d.id for d in self.propios],
            )
        # Nada se mueve.
        for doc in self.propios:
            doc.refresh_from_db()
            self.assertEqual(doc.propietario_id, self.user_trabajador.id)

    def test_rechaza_destino_en_otro_departamento(self):
        with self.assertRaises(ValueError):
            transferir_documentos_usuario(
                self.user_trabajador, self.user_trabajador_otro,
                self.user_jefe_ti,
                propiedad_ids=[d.id for d in self.propios],
            )
        for doc in self.propios:
            doc.refresh_from_db()
            self.assertEqual(doc.propietario_id, self.user_trabajador.id)

    def test_con_ids_vacios_no_hace_nada(self):
        resumen = transferir_documentos_usuario(
            self.user_trabajador, self.user_coordinador, self.user_jefe_ti,
        )
        self.assertEqual(resumen['total'], 0)
        self.asig_origen.refresh_from_db()
        self.assertEqual(self.asig_origen.coordinador_id, self.user_trabajador.id)
        self.dist_origen.refresh_from_db()
        self.assertFalse(self.dist_origen.revocado)


class TransferenciaMasivaVistaTest(_BaseTransferenciaMasiva):

    def setUp(self):
        super().setUp()
        self.url = reverse('documentos:transferencia_masiva')

    def test_jefe_puede_acceder(self):
        self.client.force_login(self.user_jefe_ti)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Transferencia Masiva')

    def test_admin_puede_acceder(self):
        self.client.force_login(self.user_admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_trabajador_no_puede_acceder(self):
        self.client.force_login(self.user_trabajador)
        resp = self.client.get(self.url)
        self.assertRedirects(
            resp, '/documentos/mis-documentos/',
            fetch_redirect_response=False,
        )

    def test_previsualizacion_muestra_los_grupos(self):
        self.client.force_login(self.user_jefe_ti)
        resp = self.client.get(self.url, {'origen': self.user_trabajador.id})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Propiedad de documentos')
        self.assertContains(resp, 'Asignaciones como coordinador')
        self.assertContains(resp, 'Distribuciones recibidas')

    def test_post_transfiere_lo_seleccionado(self):
        doc = Documento.objects.get(codigo='TI-FOR-001')
        self.client.force_login(self.user_jefe_ti)

        resp = self.client.post(self.url, {
            'origen': self.user_trabajador.id,
            'destino': self.user_coordinador.id,
            'propiedad_ids': [doc.id],
        })

        self.assertEqual(resp.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.propietario_id, self.user_coordinador.id)

    def test_post_sin_seleccion_no_transfiere_nada(self):
        doc = Documento.objects.get(codigo='TI-FOR-001')
        self.client.force_login(self.user_jefe_ti)

        resp = self.client.post(self.url, {
            'origen': self.user_trabajador.id,
            'destino': self.user_coordinador.id,
        })

        self.assertEqual(resp.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.propietario_id, self.user_trabajador.id)

    def test_post_con_destino_en_otro_depto_no_transfiere(self):
        doc = Documento.objects.get(codigo='TI-FOR-001')
        self.client.force_login(self.user_jefe_ti)

        resp = self.client.post(self.url, {
            'origen': self.user_trabajador.id,
            'destino': self.user_trabajador_otro.id,
            'propiedad_ids': [doc.id],
        })

        self.assertEqual(resp.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.propietario_id, self.user_trabajador.id)

    def test_jefe_no_ve_usuarios_de_otro_depto(self):
        self.client.force_login(self.user_jefe_ti)
        resp = self.client.get(self.url)
        # Solo los miembros de TI aparecen en el selector.
        self.assertNotContains(resp, 'trab02')


class EnlaceDashboardTest(_BaseTransferenciaMasiva):
    """El enlace de la tarjeta del dashboard de configuración resuelve."""

    def test_dashboard_de_configuracion_incluye_la_tarjeta(self):
        self.client.force_login(self.user_admin)
        resp = self.client.get(reverse('documentos:configuracion'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Transferencia Masiva')
        self.assertContains(resp, reverse('documentos:transferencia_masiva'))
