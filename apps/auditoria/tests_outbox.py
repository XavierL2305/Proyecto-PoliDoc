"""Tests del outbox SQL de auditoría (entrega diferida a MongoDB)."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import AuditoriaOutbox
from .services import (
    _cerrar_circuito_mongo, drenar_outbox, encolar_evento_auditoria,
    encolar_evento_mongo,
)


class AuditoriaOutboxDrainTest(TestCase):
    def tearDown(self):
        _cerrar_circuito_mongo()

    def _evento(self, **overrides):
        payload = {
            'usuario_id': None,
            'accion_tipo': 'toma_aprobacion',
            'accion_categoria': 'documento',
            'recurso_tipo': 'formulario',
            'recurso_id': 1,
            'recurso_codigo': 'TI-FOR-001',
            'recurso_nombre': 'Formulario',
            'estado_anterior': 'esperando',
            'estado_posterior': 'pendiente',
            'detalles': {'flujo': 'aprobacion'},
            'resultado_exitoso': True,
            'resultado_mensaje': 'Administrador tomó el paso en espera',
            'correlation_id': 'corr-test',
        }
        payload.update(overrides)
        return encolar_evento_auditoria(payload)

    def test_drenar_envia_y_marca_enviado(self):
        evento = self._evento()

        with patch(
            'auditoria.services.get_mongo_client',
            return_value=MagicMock(),
        ):
            resumen = drenar_outbox()

        evento.refresh_from_db()
        self.assertEqual(resumen['enviados'], 1)
        self.assertEqual(evento.estado, 'enviado')
        self.assertIsNotNone(evento.enviado_en)
        self.assertEqual(evento.intentos, 0)

    def test_drenar_falla_deja_pendiente_y_cuenta_intento(self):
        evento = self._evento()

        with patch(
            'auditoria.services.get_mongo_client',
            side_effect=RuntimeError('Mongo down'),
        ):
            resumen = drenar_outbox()

        evento.refresh_from_db()
        self.assertEqual(resumen['fallidos'], 1)
        self.assertEqual(evento.estado, 'pendiente')
        self.assertEqual(evento.intentos, 1)
        self.assertTrue(evento.ultimo_error)

    @override_settings(AUDITORIA_OUTBOX_MAX_INTENTOS=1)
    def test_drenar_marca_error_al_agotar_intentos(self):
        evento = self._evento()

        with patch(
            'auditoria.services.get_mongo_client',
            side_effect=RuntimeError('Mongo down'),
        ):
            drenar_outbox()

        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'error')
        self.assertEqual(evento.intentos, 1)

    def test_drenar_evento_coleccion_inserta(self):
        encolar_evento_mongo('planillas_cambios', {'planilla_id': 7})

        db = MagicMock()
        with patch('auditoria.services.get_mongo_client', return_value=db):
            resumen = drenar_outbox()

        self.assertEqual(resumen['enviados'], 1)
        db.__getitem__.assert_called_with('planillas_cambios')
        db.__getitem__.return_value.insert_one.assert_called_once_with(
            {'planilla_id': 7},
        )

    def test_drenar_evento_coleccion_actualiza(self):
        encolar_evento_mongo(
            'aprobaciones_planilla', {'aprobacion_id': 9},
            filtro={'aprobacion_id': 9},
        )

        db = MagicMock()
        with patch('auditoria.services.get_mongo_client', return_value=db):
            resumen = drenar_outbox()

        self.assertEqual(resumen['enviados'], 1)
        db.__getitem__.assert_called_with('aprobaciones_planilla')
        db.__getitem__.return_value.update_one.assert_called_once_with(
            {'aprobacion_id': 9}, {'$set': {'aprobacion_id': 9}},
        )

    @override_settings(AUDITORIA_OUTBOX_RETENCION_DIAS=1)
    def test_drenar_purga_enviados_antiguos(self):
        evento = self._evento()
        AuditoriaOutbox.objects.filter(pk=evento.pk).update(
            estado='enviado',
            enviado_en=timezone.now() - timedelta(days=2),
        )

        drenar_outbox()

        self.assertFalse(AuditoriaOutbox.objects.filter(pk=evento.pk).exists())

    def test_drenar_recupera_fila_procesando_vencida(self):
        evento = self._evento()
        AuditoriaOutbox.objects.filter(pk=evento.pk).update(
            estado='procesando',
            actualizado_en=timezone.now() - timedelta(hours=1),
        )

        with patch(
            'auditoria.services.get_mongo_client',
            return_value=MagicMock(),
        ):
            drenar_outbox()

        evento.refresh_from_db()
        self.assertEqual(evento.estado, 'enviado')
