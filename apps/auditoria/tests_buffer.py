"""Tests del circuit breaker y el buffer de auditoría (Mongo no disponible)."""

import os
import shutil
import tempfile

from django.test import TestCase
from unittest.mock import MagicMock, patch

from auditoria.services import (
    registrar_log_iso, reprocesar_buffer, _cerrar_circuito_mongo,
)


class CircuitBreakerBufferTest(TestCase):

    def setUp(self):
        _cerrar_circuito_mongo()
        self.tmp = tempfile.mkdtemp(prefix='audit_buffer_test_')
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        # override_settings es más fiable que el env una vez settings ya cargó
        from django.test import override_settings
        self._settings_override = override_settings(AUDIT_BUFFER_DIR=self.tmp)
        self._settings_override.enable()
        self.addCleanup(self._settings_override.disable)

    def _ficheros_buffer(self):
        return [f for f in os.listdir(self.tmp) if f.endswith('.pkl')]

    def test_mongo_caido_bufferiza_y_abre_circuito(self):
        with patch('auditoria.services.get_mongo_client',
                   side_effect=Exception('Mongo down')) as mock_get:
            registrar_log_iso(usuario=None, accion_tipo='consulta',
                              accion_categoria='documento')
            # Segundo evento: con el circuito abierto NO vuelve a tocar Mongo.
            registrar_log_iso(usuario=None, accion_tipo='consulta',
                              accion_categoria='documento')
            self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(len(self._ficheros_buffer()), 2)

    def test_fail_closed_siempre_intenta_mongo(self):
        with patch('auditoria.services.get_mongo_client',
                   side_effect=Exception('Mongo down')) as mock_get:
            registrar_log_iso(usuario=None, accion_tipo='consulta',
                              accion_categoria='documento')
            self.assertEqual(mock_get.call_count, 1)
            # Acción crítica: aunque el circuito esté abierto, se intenta Mongo
            # y se propaga el error para bloquear la acción sensible.
            with self.assertRaises(Exception):
                registrar_log_iso(usuario=None, accion_tipo='descarga',
                                  accion_categoria='documento', fail_closed=True)
            self.assertEqual(mock_get.call_count, 2)

    def test_exito_cierra_circuito(self):
        from auditoria.services import _mongo_circuit_open

        with patch('auditoria.services.get_mongo_client',
                   side_effect=Exception('Mongo down')):
            registrar_log_iso(usuario=None, accion_tipo='consulta',
                              accion_categoria='documento')
        self.assertTrue(_mongo_circuit_open())

        _cerrar_circuito_mongo()
        self.assertFalse(_mongo_circuit_open())

        with patch('auditoria.services.get_mongo_client',
                   return_value=MagicMock()):
            log_id = registrar_log_iso(usuario=None, accion_tipo='consulta',
                                       accion_categoria='documento')
        self.assertTrue(log_id)
        self.assertFalse(_mongo_circuit_open())

    def test_reprocesar_buffer_reenvia_y_borra(self):
        with patch('auditoria.services.get_mongo_client',
                   side_effect=Exception('Mongo down')):
            registrar_log_iso(usuario=None, accion_tipo='consulta',
                              accion_categoria='documento')
        self.assertEqual(len(self._ficheros_buffer()), 1)

        with patch('auditoria.services.get_mongo_client',
                   return_value=MagicMock()):
            resumen = reprocesar_buffer()
        self.assertEqual(resumen['enviados'], 1)
        self.assertEqual(self._ficheros_buffer(), [])
