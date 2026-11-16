"""Tests de avisos y purga de documentos personales por inactividad."""
import os
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.utils import timezone

from tests_base import BaseDocumentosTest, MONGO_PATCH_TARGET

from autenticacion.models import Notificacion
from documentos.models import (
    AvisoPurgaPersonal, DocumentoSinCodigo,
)
from documentos.services import documentos_sin_codigo as svc

TEST_MEDIA = tempfile.mkdtemp(prefix='polidoc_media_purga_')


def _archivo(nombre='doc.pdf'):
    return SimpleUploadedFile(
        nombre, b'%PDF-1.4\n%prueba\n', content_type='application/pdf',
    )


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class BasePurgaTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        # Evitar intentos de Mongo en la auditoría de la purga.
        self._mongo = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo.start()
        self.addCleanup(self._mongo.stop)
        self.client = Client()
        self.user_trabajador.email = 'trab01@example.com'
        self.user_trabajador.save(update_fields=['email'])

    # -- utilidades -----------------------------------------------------
    def _personal(self, usuario=None, n=1):
        usuario = usuario or self.user_trabajador
        docs = []
        for i in range(n):
            docs.append(DocumentoSinCodigo.objects.create(
                titulo=f'Personal {i}', ambito='personal',
                propietario=usuario, archivo=_archivo(),
            ))
        return docs

    def _sgc(self, usuario=None):
        usuario = usuario or self.user_jefe_ti
        return DocumentoSinCodigo.objects.create(
            titulo='Soporte SGC', ambito='sgc', propietario=usuario,
            archivo=_archivo(),
        )

    def _inactivo(self, usuario, dias):
        User.objects.filter(pk=usuario.pk).update(
            last_login=timezone.now() - timedelta(days=dias),
        )
        usuario.refresh_from_db()

    def _avisos(self):
        return Notificacion.objects.filter(tipo='documento_personal_aviso')


class AvisosTest(BasePurgaTest):
    def test_aviso_60_al_jefe_y_correo_usuario(self):
        self._personal(n=2)
        self._inactivo(self.user_trabajador, 61)
        mail.outbox = []

        resumen = svc.procesar_purga_personal()

        self.assertEqual(resumen['avisos'], 1)
        self.assertEqual(self._avisos().filter(usuario=self.user_jefe_ti).count(), 1)
        aviso = AvisoPurgaPersonal.objects.get(usuario=self.user_trabajador)
        self.assertIsNotNone(aviso.aviso_60_en)
        self.assertIsNone(aviso.aviso_80_en)
        self.assertTrue(DocumentoSinCodigo.objects.filter(
            propietario=self.user_trabajador,
            marcado_para_eliminar_en__isnull=False,
        ).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('trab01@example.com', mail.outbox[0].to)

    def test_no_duplica_aviso(self):
        self._personal(n=1)
        self._inactivo(self.user_trabajador, 61)
        svc.procesar_purga_personal()
        primera = self._avisos().count()
        resumen = svc.procesar_purga_personal()
        self.assertEqual(resumen['avisos'], 0)
        self.assertEqual(self._avisos().count(), primera)

    def test_segundo_aviso_80(self):
        self._personal(n=1)
        self._inactivo(self.user_trabajador, 61)
        svc.procesar_purga_personal()
        self._inactivo(self.user_trabajador, 81)

        resumen = svc.procesar_purga_personal()

        self.assertEqual(resumen['segundos'], 1)
        aviso = AvisoPurgaPersonal.objects.get(usuario=self.user_trabajador)
        self.assertIsNotNone(aviso.aviso_80_en)
        self.assertEqual(self._avisos().count(), 2)

    def test_reset_al_volver_a_iniciar_sesion(self):
        self._personal(n=1)
        self._inactivo(self.user_trabajador, 61)
        svc.procesar_purga_personal()

        # Vuelve a iniciar sesión (dispara user_logged_in).
        self.assertTrue(self.client.login(
            username='trab01', password='testpass123',
        ))
        self.assertFalse(AvisoPurgaPersonal.objects.filter(
            usuario=self.user_trabajador,
        ).exists())
        self.assertFalse(DocumentoSinCodigo.objects.filter(
            propietario=self.user_trabajador,
            marcado_para_eliminar_en__isnull=False,
        ).exists())

    def test_reset_en_la_tarea_si_ya_no_esta_inactivo(self):
        self._personal(n=1)
        self._inactivo(self.user_trabajador, 61)
        svc.procesar_purga_personal()

        User.objects.filter(pk=self.user_trabajador.pk).update(
            last_login=timezone.now(),
        )
        resumen = svc.procesar_purga_personal()
        self.assertEqual(resumen['reiniciados'], 1)
        self.assertFalse(AvisoPurgaPersonal.objects.filter(
            usuario=self.user_trabajador,
        ).exists())


class PurgaTest(BasePurgaTest):
    def test_purga_90_elimina_solo_personales(self):
        docs = self._personal(n=2)
        rutas = [d.archivo.path for d in docs]
        self._inactivo(self.user_trabajador, 91)

        resumen = svc.procesar_purga_personal()

        self.assertEqual(resumen['purgados'], 2)
        self.assertFalse(DocumentoSinCodigo.objects.filter(
            propietario=self.user_trabajador, ambito='personal',
        ).exists())
        for ruta in rutas:
            self.assertFalse(os.path.exists(ruta))
        self.assertTrue(self._avisos().filter(
            titulo='Documentos personales eliminados',
        ).exists())

    def test_no_toca_sgc(self):
        self._personal(usuario=self.user_jefe_ti, n=1)
        sgc = self._sgc(self.user_jefe_ti)
        self._inactivo(self.user_jefe_ti, 91)

        svc.procesar_purga_personal()

        self.assertTrue(DocumentoSinCodigo.objects.filter(pk=sgc.pk).exists())
        self.assertFalse(DocumentoSinCodigo.objects.filter(
            propietario=self.user_jefe_ti, ambito='personal',
        ).exists())

    def test_dry_run_no_modifica(self):
        docs = self._personal(n=1)
        self._inactivo(self.user_trabajador, 91)

        resumen = svc.procesar_purga_personal(dry_run=True)

        self.assertEqual(resumen['purgados'], 1)
        self.assertTrue(DocumentoSinCodigo.objects.filter(pk=docs[0].pk).exists())
        self.assertFalse(self._avisos().exists())
