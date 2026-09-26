from django.test import TestCase
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from tests_base import BaseDocumentosTest
from auditoria.services import (
    generar_log_id, generar_correlation_id, calcular_hash,
    calcular_hash_registro, registrar_log_iso, registrar_login,
    registrar_logout, registrar_creacion_documento,
    registrar_edicion_documento, registrar_eliminacion_documento,
    registrar_solicitud_descarga, registrar_aprobacion_descarga,
    registrar_descarga, registrar_eliminacion_version,
    registrar_asignacion_documento, registrar_transferencia_propiedad,
    registrar_cambio_version, verificar_integridad_log,
    obtener_logs, obtener_logs_por_correlation_id,
    _generar_descripcion_accion, _log_counter, _counter_date,
)
from auditoria.middleware import AuditIPMiddleware, get_client_ip


# =============================================================================
# UTILIDADES ISO
# =============================================================================

class GenerarLogIdTest(TestCase):
    """Tests de generar_log_id."""

    def test_formato_log_id(self):
        log_id = generar_log_id()
        self.assertTrue(log_id.startswith('LOG-'))
        parts = log_id.split('-')
        self.assertEqual(len(parts), 5)  # LOG-YYYY-MM-DD-XXXX

    def test_log_id_incrementa(self):
        id1 = generar_log_id()
        id2 = generar_log_id()
        self.assertNotEqual(id1, id2)

    def test_log_id_cero_padded(self):
        log_id = generar_log_id()
        counter_str = log_id.split('-')[-1]
        self.assertEqual(len(counter_str), 4)

    def test_log_id_resetea_diariamente(self):
        """El contador se resetea al cambio de día."""
        import auditoria.services as svc
        # Reset the counter
        old_date = svc._counter_date
        old_counter = svc._log_counter.copy()
        svc._counter_date = datetime.utcnow().date() - timedelta(days=1)
        svc._log_counter = {}
        log_id = generar_log_id()
        counter = int(log_id.split('-')[-1])
        self.assertEqual(counter, 1)
        # Restore
        svc._counter_date = old_date
        svc._log_counter = old_counter


class GenerarCorrelationIdTest(TestCase):
    """Tests de generar_correlation_id."""

    def test_es_uuid(self):
        corr_id = generar_correlation_id()
        parts = corr_id.split('-')
        self.assertEqual(len(parts), 5)

    def test_es_unico(self):
        id1 = generar_correlation_id()
        id2 = generar_correlation_id()
        self.assertNotEqual(id1, id2)


class CalcularHashTest(TestCase):
    """Tests de calcular_hash."""

    def test_hash_determinista(self):
        datos = {'a': 1, 'b': 'test'}
        h1 = calcular_hash(datos)
        h2 = calcular_hash(datos)
        self.assertEqual(h1, h2)

    def test_hash_diferente_para_datos_diferentes(self):
        h1 = calcular_hash({'a': 1})
        h2 = calcular_hash({'a': 2})
        self.assertNotEqual(h1, h2)

    def test_hash_es_sha256(self):
        h = calcular_hash({'test': 'data'})
        self.assertEqual(len(h), 64)

    def test_hash_sorted_keys(self):
        h1 = calcular_hash({'b': 2, 'a': 1})
        h2 = calcular_hash({'a': 1, 'b': 2})
        self.assertEqual(h1, h2)

    def test_hash_con_datetime(self):
        h = calcular_hash({'fecha': datetime.utcnow()})
        self.assertEqual(len(h), 64)


class CalcularHashRegistroTest(TestCase):
    """Tests de calcular_hash_registro."""

    def test_excluye_campo_integridad(self):
        registro = {
            'log_id': 'LOG-001',
            'datos': 'test',
            'integridad': {'hash': 'debe_ser_excluido'}
        }
        hash1 = calcular_hash_registro(registro)
        registro2 = {
            'log_id': 'LOG-001',
            'datos': 'test',
            'integridad': {'hash': 'otro_hash'}
        }
        hash2 = calcular_hash_registro(registro2)
        self.assertEqual(hash1, hash2)

    def test_hash_es_determinista(self):
        registro = {'log_id': 'LOG-001', 'datos': 'test'}
        h1 = calcular_hash_registro(registro)
        h2 = calcular_hash_registro(registro)
        self.assertEqual(h1, h2)


class DescripcionAccionTest(TestCase):
    """Tests de _generar_descripcion_accion."""

    def test_tipos_conocidos(self):
        desc = _generar_descripcion_accion('creacion', 'documento')
        self.assertIn('Creación', desc)
        desc = _generar_descripcion_accion('edicion', 'documento')
        self.assertIn('Edición', desc)
        desc = _generar_descripcion_accion('eliminacion', 'documento')
        self.assertIn('Eliminación', desc)
        desc = _generar_descripcion_accion('login', None)
        self.assertIn('sesión', desc.lower())
        desc = _generar_descripcion_accion('logout', None)
        self.assertIn('sesión', desc.lower())

    def test_tipo_desconocido(self):
        desc = _generar_descripcion_accion('custom_action', 'custom_resource')
        self.assertIn('custom_action', desc)


# =============================================================================
# REGISTRO PRINCIPAL (fail-safe MongoDB)
# =============================================================================

class RegistrarLogIsoTest(BaseDocumentosTest):
    """Tests de registrar_log_iso con MongoDB mock."""

    @patch('auditoria.services.get_mongo_client')
    def test_registrar_log_iso_exitoso(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_log_iso(
            usuario=self.user_trabajador,
            accion_tipo='creacion',
            accion_categoria='documento',
            recurso_tipo='formulario',
            recurso_id=1,
            recurso_codigo='TI-FOR-001',
            recurso_nombre='Test',
        )
        self.assertIsNotNone(log_id)
        self.assertTrue(log_id.startswith('LOG-'))
        db.audit_logs.insert_one.assert_called_once()

    @patch('auditoria.services.get_mongo_client')
    def test_registrar_log_iso_estructura(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        registrar_log_iso(
            usuario=self.user_trabajador,
            accion_tipo='creacion',
            accion_categoria='documento',
            recurso_tipo='formulario',
            recurso_id=1,
            recurso_codigo='TI-FOR-001',
            recurso_nombre='Test',
            recurso_version='1.0',
            estado_anterior=None,
            estado_posterior='borrador',
            correlation_id='test-correlation-123',
            ip_address='192.168.1.1',
        )
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['log_id'][:4], 'LOG-')
        self.assertEqual(call_args['correlation_id'], 'test-correlation-123')
        self.assertEqual(call_args['usuario']['id'], self.user_trabajador.id)
        self.assertEqual(call_args['usuario']['username'], 'trab01')
        self.assertEqual(call_args['accion']['tipo'], 'creacion')
        self.assertEqual(call_args['accion']['categoria'], 'documento')
        self.assertEqual(call_args['recurso']['tipo'], 'formulario')
        self.assertEqual(call_args['recurso']['codigo'], 'TI-FOR-001')
        self.assertEqual(call_args['contexto']['ip_address'], '192.168.1.1')
        self.assertEqual(call_args['contexto']['sistema'], 'POLIDOC')
        self.assertIn('sha256:', call_args['integridad']['hash'])
        self.assertEqual(call_args['integridad']['version_registro'], '1.0')
        self.assertEqual(call_args['metadata']['retencion_anos'], 10)

    @patch('auditoria.services.get_mongo_client', side_effect=Exception('MongoDB no disponible'))
    def test_registrar_log_iso_fail_safe(self, mock_mongo):
        """Si MongoDB no está disponible, retorna None sin crashear."""
        log_id = registrar_log_iso(
            usuario=self.user_trabajador,
            accion_tipo='creacion',
            accion_categoria='documento',
        )
        self.assertIsNone(log_id)

    @patch('auditoria.services.get_mongo_client')
    def test_registrar_log_iso_usuario_none(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_log_iso(
            usuario=None,
            accion_tipo='sistema',
            accion_categoria='sistema',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['usuario']['username'], 'sistema')

    @patch('auditoria.services.get_mongo_client')
    def test_registrar_log_iso_genera_correlation_id(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        registrar_log_iso(
            usuario=self.user_trabajador,
            accion_tipo='creacion',
            accion_categoria='documento',
        )
        call_args = db.audit_logs.insert_one.call_args[0][0]
        corr_id = call_args['correlation_id']
        parts = corr_id.split('-')
        self.assertEqual(len(parts), 5)


# =============================================================================
# FUNCIONES ESPECIFICAS DE REGISTRO
# =============================================================================

class RegistrarLoginTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_login(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_login(
            self.user_trabajador,
            ip_address='10.0.0.1',
            user_agent='Mozilla/5.0',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'login')
        self.assertEqual(call_args['accion']['categoria'], 'seguridad')
        self.assertTrue(call_args['resultado']['exitoso'])


class RegistrarLogoutTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_logout(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_logout(
            self.user_trabajador,
            ip_address='10.0.0.1',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'logout')
        self.assertEqual(call_args['accion']['categoria'], 'seguridad')


class RegistrarCreacionDocumentoTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_creacion(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_creacion_documento(
            self.user_trabajador,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_borrador.uuid),
            codigo='TI-FOR-001',
            nombre='Test',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'creacion')
        self.assertEqual(call_args['recurso']['estado_posterior'], 'borrador')


class RegistrarEdicionDocumentoTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_edicion(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_edicion_documento(
            self.user_trabajador,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_borrador.uuid),
            codigo='TI-FOR-001',
            nombre='Test',
            estado_anterior='borrador',
            estado_posterior='borrador',
            cambios={'nombre': 'Nuevo nombre'},
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'edicion')
        self.assertIn('nombre', call_args['detalles']['campos_modificados'])


class RegistrarEliminacionDocumentoTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_eliminacion(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_eliminacion_documento(
            self.user_jefe_ti,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_borrador.uuid),
            codigo='TI-FOR-001',
            nombre='Test',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'eliminacion')
        self.assertEqual(call_args['recurso']['estado_posterior'], 'obsoleto')


class RegistrarSolicitudDescargaTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_solicitud_descarga(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_solicitud_descarga(
            self.user_trabajador,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_activo.uuid),
            solicitud_id=1,
            codigo='TI-PRG-001',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'solicitud_descarga')


class RegistrarAprobacionDescargaTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_aprobacion_descarga(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_aprobacion_descarga(
            self.user_jefe_ti,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_activo.uuid),
            solicitud_id=1,
            aprobada=True,
            codigo='TI-PRG-001',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'aprobacion_descarga')
        self.assertTrue(call_args['resultado']['exitoso'])

    @patch('auditoria.services.get_mongo_client')
    def test_registrar_rechazo_descarga(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_aprobacion_descarga(
            self.user_jefe_ti,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_activo.uuid),
            solicitud_id=1,
            aprobada=False,
            codigo='TI-PRG-001',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'rechazo_descarga')
        self.assertFalse(call_args['resultado']['exitoso'])


class RegistrarDescargaTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_descarga(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_descarga(
            self.user_trabajador,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_activo.uuid),
            version='1.0',
            codigo='TI-PRG-001',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'descarga')


class RegistrarEliminacionVersionTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_eliminacion_version(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_eliminacion_version(
            self.user_jefe_ti,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_activo.uuid),
            numero_version='2.0',
            codigo='TI-PRG-001',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'eliminacion_version')


class RegistrarAsignacionDocumentoTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_asignacion(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_asignacion_documento(
            self.user_jefe_ti,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_activo.uuid),
            coordinador_username='coord01',
            permisos={'leer': True, 'editar': False},
            codigo='TI-PRG-001',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'asignacion')
        self.assertEqual(call_args['detalles']['coordinador_asignado'], 'coord01')


class RegistrarTransferenciaPropiedadTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_transferencia(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_transferencia_propiedad(
            self.user_jefe_ti,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_activo.uuid),
            nuevo_propietario_username='trab01',
            propietario_anterior='jefe_ti',
            codigo='TI-PRG-001',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'transferencia')


class RegistrarCambioVersionTest(BaseDocumentosTest):
    @patch('auditoria.services.get_mongo_client')
    def test_registrar_cambio_version(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        log_id = registrar_cambio_version(
            self.user_jefe_ti,
            recurso_tipo='formulario',
            recurso_id=str(self.doc_activo.uuid),
            version_anterior='1.0',
            version_nueva='2.0',
            cambios='Actualización',
            codigo='TI-PRG-001',
        )
        self.assertIsNotNone(log_id)
        call_args = db.audit_logs.insert_one.call_args[0][0]
        self.assertEqual(call_args['accion']['tipo'], 'cambio_version')
        self.assertEqual(call_args['recurso']['version'], '2.0')


# =============================================================================
# VERIFICACION DE INTEGRIDAD
# =============================================================================

class VerificarIntegridadLogTest(TestCase):
    """Tests de verificar_integridad_log."""

    @patch('auditoria.services.get_mongo_client')
    def test_registro_no_encontrado(self, mock_mongo):
        db = MagicMock()
        db.audit_logs.find_one.return_value = None
        mock_mongo.return_value = db
        resultado = verificar_integridad_log('LOG-9999-99-9999')
        self.assertFalse(resultado['valido'])
        self.assertIn('no encontrado', resultado['mensaje'])

    @patch('auditoria.services.get_mongo_client')
    def test_registro_sin_hash(self, mock_mongo):
        db = MagicMock()
        db.audit_logs.find_one.return_value = {'log_id': 'LOG-001', 'datos': 'test'}
        mock_mongo.return_value = db
        resultado = verificar_integridad_log('LOG-001')
        self.assertFalse(resultado['valido'])
        self.assertIn('sin hash', resultado['mensaje'])

    @patch('auditoria.services.get_mongo_client')
    def test_hash_valido(self, mock_mongo):
        from auditoria.services import calcular_hash_registro
        registro = {
            'log_id': 'LOG-001',
            'datos': 'test',
        }
        hash_val = calcular_hash_registro(registro)
        registro['integridad'] = {'hash': f'sha256:{hash_val}'}
        db = MagicMock()
        db.audit_logs.find_one.return_value = registro
        mock_mongo.return_value = db
        resultado = verificar_integridad_log('LOG-001')
        self.assertTrue(resultado['valido'])

    @patch('auditoria.services.get_mongo_client')
    def test_hash_alterado(self, mock_mongo):
        registro = {
            'log_id': 'LOG-001',
            'datos': 'test',
            'integridad': {'hash': 'sha256:hash_falso'}
        }
        db = MagicMock()
        db.audit_logs.find_one.return_value = registro
        mock_mongo.return_value = db
        resultado = verificar_integridad_log('LOG-001')
        self.assertFalse(resultado['valido'])
        self.assertIn('alteración', resultado['mensaje'])

    @patch('auditoria.services.get_mongo_client', side_effect=Exception('MongoDB caído'))
    def test_fail_safe_mongo(self, mock_mongo):
        resultado = verificar_integridad_log('LOG-001')
        self.assertFalse(resultado['valido'])
        self.assertIn('Error', resultado['mensaje'])


# =============================================================================
# CONSULTA DE LOGS
# =============================================================================

class ObtenerLogsTest(TestCase):
    """Tests de obtener_logs."""

    @patch('auditoria.services.get_mongo_client')
    def test_obtener_logs_vacio(self, mock_mongo):
        db = MagicMock()
        db.audit_logs.find.return_value.sort.return_value.limit.return_value = []
        mock_mongo.return_value = db
        logs = obtener_logs()
        self.assertEqual(logs, [])

    @patch('auditoria.services.get_mongo_client')
    def test_obtener_logs_con_filtros(self, mock_mongo):
        db = MagicMock()
        db.audit_logs.find.return_value.sort.return_value.limit.return_value = []
        mock_mongo.return_value = db
        logs = obtener_logs(
            usuario='trab01',
            recurso_tipo='formulario',
            accion_tipo='creacion',
            fecha_inicio=datetime(2026, 1, 1),
            fecha_fin=datetime(2026, 12, 31),
        )
        self.assertEqual(logs, [])

    @patch('auditoria.services.get_mongo_client', side_effect=Exception('MongoDB down'))
    def test_obtener_logs_fail_safe(self, mock_mongo):
        logs = obtener_logs()
        self.assertEqual(logs, [])


class ObtenerLogsPorCorrelationIdTest(TestCase):
    """Tests de obtener_logs_por_correlation_id."""

    @patch('auditoria.services.get_mongo_client')
    def test_obtener_por_correlation_id(self, mock_mongo):
        db = MagicMock()
        db.audit_logs.find.return_value.sort.return_value = []
        mock_mongo.return_value = db
        logs = obtener_logs_por_correlation_id('test-uuid-123')
        self.assertEqual(logs, [])

    @patch('auditoria.services.get_mongo_client', side_effect=Exception('MongoDB down'))
    def test_fail_safe(self, mock_mongo):
        logs = obtener_logs_por_correlation_id('test-uuid-123')
        self.assertEqual(logs, [])


# =============================================================================
# MIDDLEWARE
# =============================================================================

class AuditIPMiddlewareTest(TestCase):
    """Tests del middleware de auditoría IP."""

    def test_middleware_remote_addr(self):
        """IP desde REMOTE_ADDR."""
        from auditoria.middleware import AuditIPMiddleware, _thread_locals
        request = MagicMock()
        request.META = {'REMOTE_ADDR': '192.168.1.100'}
        middleware = AuditIPMiddleware(lambda r: MagicMock())
        middleware(request)
        self.assertEqual(_thread_locals.ip_address, '192.168.1.100')

    def test_middleware_x_forwarded_for(self):
        """IP desde X-Forwarded-For (primera)."""
        from auditoria.middleware import AuditIPMiddleware, _thread_locals
        request = MagicMock()
        request.META = {
            'HTTP_X_FORWARDED_FOR': '10.0.0.1, 10.0.0.2',
            'REMOTE_ADDR': '192.168.1.100',
        }
        middleware = AuditIPMiddleware(lambda r: MagicMock())
        middleware(request)
        self.assertEqual(_thread_locals.ip_address, '10.0.0.1')

    def test_middleware_x_real_ip(self):
        """IP desde X-Real-IP."""
        from auditoria.middleware import AuditIPMiddleware, _thread_locals
        request = MagicMock()
        request.META = {
            'HTTP_X_REAL_IP': '172.16.0.1',
            'REMOTE_ADDR': '192.168.1.100',
        }
        middleware = AuditIPMiddleware(lambda r: MagicMock())
        middleware(request)
        self.assertEqual(_thread_locals.ip_address, '172.16.0.1')

    def test_get_client_ip_after_middleware(self):
        """get_client_ip retorna la IP configurada por el middleware."""
        from auditoria.middleware import AuditIPMiddleware, get_client_ip, _thread_locals
        request = MagicMock()
        request.META = {'REMOTE_ADDR': '127.0.0.1'}
        middleware = AuditIPMiddleware(lambda r: MagicMock())
        middleware(request)
        self.assertEqual(get_client_ip(), '127.0.0.1')

    def test_get_client_ip_no_middleware(self):
        """get_client_ip retorna None si el middleware no ha corrido."""
        from auditoria.middleware import get_client_ip, _thread_locals
        _thread_locals.ip_address = None
        self.assertIsNone(get_client_ip())


# =============================================================================
# ESTRUCTURA ISO COMPLETA
# =============================================================================

class EstructuraISOCompletaTest(BaseDocumentosTest):
    """Tests que la estructura del log cumple ISO 15489/27001."""

    @patch('auditoria.services.get_mongo_client')
    def test_campos_iso_presentes(self, mock_mongo):
        db = MagicMock()
        mock_mongo.return_value = db
        registrar_log_iso(
            usuario=self.user_trabajador,
            accion_tipo='creacion',
            accion_categoria='documento',
            recurso_tipo='formulario',
            recurso_id=1,
            recurso_codigo='TI-FOR-001',
            recurso_nombre='Test',
            recurso_version='1.0',
            estado_anterior=None,
            estado_posterior='borrador',
            detalles={'campo': 'valor'},
            resultado_exitoso=True,
            resultado_mensaje='OK',
            correlation_id='corr-123',
            ip_address='10.0.0.1',
            user_agent='Mozilla/5.0',
            sesion_id='session-abc',
        )
        log = db.audit_logs.insert_one.call_args[0][0]

        # Campos ISO obligatorios
        self.assertIn('log_id', log)
        self.assertIn('correlation_id', log)
        self.assertIn('timestamp', log)
        self.assertIn('usuario', log)
        self.assertIn('accion', log)
        self.assertIn('recurso', log)
        self.assertIn('contexto', log)
        self.assertIn('detalles', log)
        self.assertIn('resultado', log)
        self.assertIn('integridad', log)
        self.assertIn('metadata', log)

        # Estructura usuario
        for key in ['id', 'username', 'nombre_completo', 'rol', 'departamento']:
            self.assertIn(key, log['usuario'])

        # Estructura acción
        for key in ['tipo', 'categoria', 'descripcion']:
            self.assertIn(key, log['accion'])

        # Estructura recurso
        for key in ['tipo', 'id', 'codigo', 'nombre', 'version', 'estado_anterior', 'estado_posterior']:
            self.assertIn(key, log['recurso'])

        # Estructura contexto
        for key in ['ip_address', 'user_agent', 'sistema', 'sesion_id']:
            self.assertIn(key, log['contexto'])

        # Estructura resultado
        for key in ['exitoso', 'mensaje']:
            self.assertIn(key, log['resultado'])

        # Integridad
        self.assertTrue(log['integridad']['hash'].startswith('sha256:'))
        self.assertEqual(log['integridad']['version_registro'], '1.0')

        # Metadata retención
        self.assertIn('fecha_creacion', log['metadata'])
        self.assertIn('fecha_expiracion', log['metadata'])
        self.assertEqual(log['metadata']['retencion_anos'], 10)

    @patch('auditoria.services.get_mongo_client')
    def test_hash_cambia_si_datos_cambian(self, mock_mongo):
        """El hash debe ser diferente si cambian los datos del log."""
        db = MagicMock()
        mock_mongo.return_value = db

        registrar_log_iso(
            usuario=self.user_trabajador,
            accion_tipo='creacion',
            accion_categoria='documento',
            recurso_codigo='TI-FOR-001',
        )
        log1 = db.audit_logs.insert_one.call_args_list[0][0][0]

        registrar_log_iso(
            usuario=self.user_trabajador,
            accion_tipo='edicion',
            accion_categoria='documento',
            recurso_codigo='TI-FOR-002',
        )
        log2 = db.audit_logs.insert_one.call_args_list[1][0][0]

        self.assertNotEqual(log1['integridad']['hash'], log2['integridad']['hash'])
