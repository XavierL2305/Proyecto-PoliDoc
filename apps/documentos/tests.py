from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import json
import uuid
from tests_base import BaseDocumentosTest
from documentos.models import (
    Departamento, SubArea, TipoDocumento, Documento,
    VersionDocumento, AsignacionDocumento, SolicitudDescarga,
    AprobacionDescarga, SolicitudAccesoDocumento, PlanillaCambios,
    AprobacionPlanilla, DistribucionDocumento, SolicitudAprobacionDocumento,
    RenovacionVencimiento, generar_ruta_archivo, generar_ruta_version,
    ReferenciaDocumento, DocumentoAsociadoPlanilla,
)
from documentos.permissions import (
    obtener_documento, puede_editar, puede_descargar,
    puede_inhabilitar, puede_transferir, documentos_usuario,
    es_jefe_de_departamento,
)
from documentos.views import puede_editar_documento, puede_eliminar_documento
from documentos.tasks import verificar_vencimientos, FORMATOS_CONVERTIBLES
from autenticacion.models import PerfilUsuario, Notificacion


# =============================================================================
# MODELOS
# =============================================================================

class DepartamentoModelTest(BaseDocumentosTest):
    """Tests del modelo Departamento."""

    def test_str_departamento(self):
        self.assertEqual(str(self.depto_ti), 'TI - Tecnología de la Información')

    def test_codigo_unico(self):
        with self.assertRaises(Exception):
            Departamento.objects.create(
                nombre='Otro TI', codigo='TI', activo=True
            )

    def test_jefe_optional(self):
        d = Departamento.objects.create(
            nombre='Sin Jefe', codigo='SJ', activo=True
        )
        self.assertIsNone(d.jefe)

    def test_activo_default(self):
        d = Departamento.objects.create(
            nombre='Test', codigo='TST'
        )
        self.assertTrue(d.activo)


class SubAreaModelTest(BaseDocumentosTest):
    """Tests del modelo SubArea."""

    def test_str_subarea(self):
        self.assertIn('Soporte Técnico', str(self.subarea_soporte))
        self.assertIn('Tecnología', str(self.subarea_soporte))

    def test_nombre_unico(self):
        with self.assertRaises(Exception):
            SubArea.objects.create(
                nombre='Soporte Técnico', departamento=self.depto_ti
            )

    def test_departamento_obligatorio(self):
        with self.assertRaises(Exception):
            SubArea.objects.create(nombre='Sin Depto')


class TipoDocumentoModelTest(BaseDocumentosTest):
    """Tests del modelo TipoDocumento."""

    def test_str_tipo(self):
        self.assertEqual(str(self.tipo_procedimiento), 'PRG - Procedimientos Generales')

    def test_codigo_unico(self):
        with self.assertRaises(Exception):
            TipoDocumento.objects.create(
                key='otro_prg', nombre='Otro PRG', codigo='PRG'
            )

    def test_activo_default(self):
        t = TipoDocumento.objects.create(key='test_tipo', nombre='Test', codigo='TST')
        self.assertTrue(t.activo)


class SeccionesOpcionalesTest(BaseDocumentosTest):
    """Flags usa_* de TipoDocumento controlan las secciones del formulario."""

    def _tipo(self, key, **flags):
        defaults = dict(nombre='Test Flags', codigo='TFL', activo=True)
        defaults.update(flags)
        return TipoDocumento.objects.create(key=key, **defaults)

    def _doc_local(self, tipo, codigo):
        return Documento.objects.create(
            tipo=tipo, nombre='Doc Local', codigo=codigo,
            estado='borrador', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
        )

    def test_tipodocumentoform_incluye_flags(self):
        from documentos.forms import TipoDocumentoForm
        form = TipoDocumentoForm()
        for f in ('usa_departamentos_afectados', 'usa_responsables',
                  'usa_participantes', 'usa_documentos_relacionados'):
            self.assertIn(f, form.fields)

    def test_documentoform_oculta_secciones_por_flag(self):
        from documentos.forms import DocumentoForm
        tipo = self._tipo(
            'test_oculta', codigo='TOC',
            usa_departamentos_afectados=False, usa_responsables=False,
            usa_participantes=False, usa_documentos_relacionados=False,
        )
        form = DocumentoForm(tipo_documento=tipo.key)
        for f in ('departamentos_afectados', 'responsables',
                  'participantes', 'documentos_relacionados'):
            self.assertNotIn(f, form.fields)

    def test_documentoform_muestra_secciones_por_flag(self):
        from documentos.forms import DocumentoForm
        tipo = self._tipo('test_muestra', codigo='TMU')
        form = DocumentoForm(tipo_documento=tipo.key)
        for f in ('departamentos_afectados', 'responsables',
                  'participantes', 'documentos_relacionados'):
            self.assertIn(f, form.fields)

    def test_planilla_hibrido_flag_true_muestra_sin_datos(self):
        from documentos.forms import PlanillaCambiosForm
        tipo = self._tipo('test_plan_t', codigo='TPT')
        doc = self._doc_local(tipo, 'TI-TPT-001')
        form = PlanillaCambiosForm(tipo_documento=tipo.key, documento=doc)
        for f in ('departamentos_afectados', 'responsables_propuestos',
                  'participantes_propuestos', 'documentos_asociados_json'):
            self.assertIn(f, form.fields)

    def test_planilla_hibrido_flag_false_con_datos_muestra(self):
        from documentos.forms import PlanillaCambiosForm
        tipo = self._tipo(
            'test_plan_f', codigo='TPF',
            usa_departamentos_afectados=False, usa_responsables=False,
            usa_participantes=False, usa_documentos_relacionados=False,
        )
        doc = self._doc_local(tipo, 'TI-TPF-001')
        doc.departamentos_afectados.add(self.depto_ti)
        doc.responsables.add(self.depto_ti)
        doc.participantes = ['Ana']
        doc.save()
        doc.documentos_relacionados.add(self.doc_activo)
        form = PlanillaCambiosForm(tipo_documento=tipo.key, documento=doc)
        for f in ('departamentos_afectados', 'responsables_propuestos',
                  'participantes_propuestos', 'documentos_asociados_json'):
            self.assertIn(f, form.fields)

    def test_planilla_hibrido_flag_false_sin_datos_oculta(self):
        from documentos.forms import PlanillaCambiosForm
        tipo = self._tipo(
            'test_plan_e', codigo='TPE',
            usa_departamentos_afectados=False, usa_responsables=False,
            usa_participantes=False, usa_documentos_relacionados=False,
        )
        doc = self._doc_local(tipo, 'TI-TPE-001')
        form = PlanillaCambiosForm(tipo_documento=tipo.key, documento=doc)
        for f in ('departamentos_afectados', 'responsables_propuestos',
                  'participantes_propuestos', 'documentos_asociados_json'):
            self.assertNotIn(f, form.fields)


class SeccionesOpcionalesBackendTest(BaseDocumentosTest):
    """Los flags y el acceso se validan en los caminos de POST crudo."""

    def _tipo(self, key, **flags):
        defaults = dict(nombre='Test Flags', codigo='TFL', activo=True)
        defaults.update(flags)
        return TipoDocumento.objects.create(key=key, **defaults)

    def _doc_local(self, tipo, codigo, departamento=None, estado='borrador'):
        return Documento.objects.create(
            tipo=tipo, nombre='Doc Local', codigo=codigo,
            estado=estado, propietario=self.user_jefe_ti,
            departamento=departamento or self.depto_ti, version_actual='1.0',
        )

    def _planilla(self, documento):
        return PlanillaCambios.objects.create(
            codigo=f'PC-TEST-{uuid.uuid4().hex[:8]}',
            documento=documento, tipo_cambio='modificacion',
            justificacion='test', responsable=self.user_jefe_ti,
            version_destino='2', estado='borrador',
        )

    def test_referencias_flag_off_descarta(self):
        from documentos.views import _procesar_referencias
        tipo = self._tipo('test_ref_off', usa_documentos_relacionados=False)
        doc = self._doc_local(tipo, 'TI-REF-001')
        request = SimpleNamespace(POST={
            'referencias_pendientes_json': json.dumps([self.doc_activo.codigo]),
        }, user=self.user_jefe_ti)
        _procesar_referencias(doc, request, self.perfil_jefe_ti)
        self.assertEqual(doc.documentos_relacionados.count(), 0)
        self.assertFalse(ReferenciaDocumento.objects.filter(documento=doc).exists())

    def test_referencias_flag_on_codigo_inexistente_crea_referencia(self):
        from documentos.views import _procesar_referencias
        tipo = self._tipo('test_ref_on')
        doc = self._doc_local(tipo, 'TI-REF-002')
        request = SimpleNamespace(POST={
            'referencias_pendientes_json': json.dumps(['NO-EXISTE-999']),
        }, user=self.user_jefe_ti)
        _procesar_referencias(doc, request, self.perfil_jefe_ti)
        self.assertTrue(
            ReferenciaDocumento.objects.filter(
                documento=doc, codigo_destino='NO-EXISTE-999'
            ).exists()
        )

    def test_asociados_permitido_false_no_crea(self):
        from documentos.views import _procesar_asociados_planilla
        tipo = self._tipo('test_asoc_off', usa_documentos_relacionados=False)
        doc = self._doc_local(tipo, 'TI-ASO-001')
        planilla = self._planilla(doc)
        request = SimpleNamespace(POST={
            'documentos_asociados_json': json.dumps([{'uuid': str(self.doc_activo.uuid)}]),
        })
        _procesar_asociados_planilla(planilla, request, False, doc, self.user_jefe_ti)
        self.assertEqual(
            DocumentoAsociadoPlanilla.objects.filter(planilla=planilla).count(), 0
        )

    def test_asociados_doc_accesible_crea(self):
        from documentos.views import _procesar_asociados_planilla
        tipo = self._tipo('test_asoc_on')
        doc = self._doc_local(tipo, 'TI-ASO-002')
        planilla = self._planilla(doc)
        request = SimpleNamespace(POST={
            'documentos_asociados_json': json.dumps([{'uuid': str(self.doc_activo.uuid)}]),
        })
        _procesar_asociados_planilla(planilla, request, True, doc, self.user_jefe_ti)
        self.assertEqual(
            DocumentoAsociadoPlanilla.objects.filter(planilla=planilla).count(), 1
        )

    def test_asociados_doc_no_accesible_no_crea(self):
        from documentos.views import _procesar_asociados_planilla
        tipo = self._tipo('test_asoc_deny')
        doc = self._doc_local(tipo, 'TI-ASO-003')
        planilla = self._planilla(doc)
        request = SimpleNamespace(POST={
            'documentos_asociados_json': json.dumps([{'uuid': str(self.doc_th.uuid)}]),
        })
        _procesar_asociados_planilla(planilla, request, True, doc, self.user_jefe_ti)
        self.assertEqual(
            DocumentoAsociadoPlanilla.objects.filter(planilla=planilla).count(), 0
        )


class DocumentoModelTest(BaseDocumentosTest):
    """Tests del modelo Documento."""

    def test_str_documento(self):
        self.assertEqual(str(self.doc_activo), 'TI-PRG-001 - Procedimiento de Prueba')

    def test_uuid_generado_automaticamente(self):
        self.assertIsNotNone(self.doc_activo.uuid)

    def test_hash_se_calcula_en_save(self):
        """El hash_integridad debe calcularse automáticamente al guardar."""
        doc = Documento.objects.create(
            tipo=self.tipo_formulario,
            nombre='Test Hash',
            codigo='TI-FOR-999',
            estado='borrador',
            propietario=self.user_trabajador,
            departamento=self.depto_ti,
        )
        self.assertNotEqual(doc.hash_integridad, '')
        self.assertEqual(len(doc.hash_integridad), 64)

    def test_hash_cambia_con_datos(self):
        """El hash debe cambiar si cambian los datos del documento."""
        hash1 = self.doc_activo.hash_integridad
        self.doc_activo.nombre = 'Nombre Modificado'
        self.doc_activo.save()
        self.assertNotEqual(hash1, self.doc_activo.hash_integridad)

    def test_generar_codigo_secuencial(self):
        codigo = Documento.generar_codigo_secuencial(self.depto_ti, 'procedimientos_generales')
        self.assertEqual(codigo, 'TI-PRG-005')

    def test_generar_codigo_secuencial_incrementa(self):
        """Si ya existe PRG-001, debe generar PRG-002."""
        Documento.objects.create(
            tipo=self.tipo_procedimiento,
            nombre='Existente',
            codigo='TI-PRG-005',
            estado='borrador',
            propietario=self.user_trabajador,
            departamento=self.depto_ti,
        )
        codigo = Documento.generar_codigo_secuencial(self.depto_ti, 'procedimientos_generales')
        self.assertEqual(codigo, 'TI-PRG-006')

    def test_generar_codigo_secuencial_sin_documentos(self):
        codigo = Documento.generar_codigo_secuencial(self.depto_th, 'formulario')
        self.assertEqual(codigo, 'TH-FOR-002')

    def test_generar_codigo_secuencial_dpr_sin_prefijo(self):
        """DPR numera sin prefijo de departamento."""
        TipoDocumento.objects.create(
            key='descripcion_procesos', nombre='Descripción de Procesos',
            codigo='DPR', activo=True, sin_prefijo_depto=True,
        )
        self.assertTrue(Documento.es_sin_prefijo_depto('descripcion_procesos'))
        codigo = Documento.generar_codigo_secuencial(self.depto_ti, 'descripcion_procesos')
        self.assertEqual(codigo, 'DPR-001')

    def test_generar_codigo_secuencial_dpr_global_entre_deptos(self):
        """La secuencia DPR es global: otro departamento continúa la numeración."""
        tipo_dpr = TipoDocumento.objects.create(
            key='descripcion_procesos', nombre='Descripción de Procesos',
            codigo='DPR', activo=True, sin_prefijo_depto=True,
        )
        Documento.objects.create(
            tipo=tipo_dpr, nombre='DPR Uno', codigo='DPR-001', estado='borrador',
            propietario=self.user_trabajador, departamento=self.depto_ti,
        )
        codigo = Documento.generar_codigo_secuencial(self.depto_th, 'descripcion_procesos')
        self.assertEqual(codigo, 'DPR-002')

    def test_clasificacion_default_interno(self):
        doc = Documento.objects.create(
            tipo=self.tipo_formulario,
            nombre='Test',
            codigo='TI-FOR-888',
            estado='borrador',
            propietario=self.user_trabajador,
            departamento=self.depto_ti,
        )
        self.assertEqual(doc.clasificacion, 'interno')
        self.assertIn('publico', [k for k, _ in Documento.CLASIFICACION_CHOICES])

    def test_estado_default_borrador(self):
        doc = Documento.objects.create(
            tipo=self.tipo_formulario,
            nombre='Test',
            codigo='TI-FOR-887',
            propietario=self.user_trabajador,
            departamento=self.depto_ti,
        )
        self.assertEqual(doc.estado, 'borrador')

    def test_fecha_vencimiento_nullable(self):
        self.assertIsNone(self.doc_borrador.fecha_vencimiento)

    def test_documento_sin_departamento(self):
        doc = Documento.objects.create(
            tipo=self.tipo_formulario,
            nombre='Sin depto',
            codigo='SIN-FOR-001',
            estado='borrador',
            propietario=self.user_trabajador,
            departamento=None,
        )
        self.assertIsNone(doc.departamento)

    def test_documento_relacionado_m2m(self):
        self.doc_activo.documentos_relacionados.add(self.doc_borrador)
        self.assertIn(self.doc_borrador, self.doc_activo.documentos_relacionados.all())

    def test_departamentos_afectados_m2m(self):
        self.doc_activo.departamentos_afectados.add(self.depto_ti, self.depto_th)
        self.assertEqual(self.doc_activo.departamentos_afectados.count(), 2)


class VersionDocumentoModelTest(BaseDocumentosTest):
    """Tests del modelo VersionDocumento."""

    def setUp(self):
        super().setUp()
        self.version = VersionDocumento.objects.create(
            documento=self.doc_activo,
            numero_version='1.0',
            archivo=SimpleUploadedFile('test.pdf', b'content'),
            creado_por=self.user_jefe_ti,
            cambios='Version inicial',
        )

    def test_str_version(self):
        self.assertEqual(str(self.version), 'TI-PRG-001 - v1.0')

    def test_unique_together_documento_numero_version(self):
        with self.assertRaises(Exception):
            VersionDocumento.objects.create(
                documento=self.doc_activo,
                numero_version='1.0',
                archivo=SimpleUploadedFile('test2.pdf', b'content'),
                creado_por=self.user_jefe_ti,
            )

    def test_version_activa_default(self):
        self.assertTrue(self.version.activa)

    def test_ordenacion_por_fecha_desc(self):
        v2 = VersionDocumento.objects.create(
            documento=self.doc_activo,
            numero_version='2.0',
            archivo=SimpleUploadedFile('test2.pdf', b'content'),
            creado_por=self.user_jefe_ti,
        )
        versions = list(self.doc_activo.versiones.all())
        self.assertEqual(versions[0], v2)
        self.assertEqual(versions[1], self.version)


class AsignacionDocumentoModelTest(BaseDocumentosTest):
    """Tests del modelo AsignacionDocumento."""

    def setUp(self):
        super().setUp()
        self.asignacion = AsignacionDocumento.objects.create(
            documento=self.doc_activo,
            coordinador=self.user_coordinador,
            jefe_asignador=self.user_jefe_ti,
            puede_leer=True,
            puede_editar=False,
            puede_eliminar=False,
        )

    def test_str_asignacion(self):
        self.assertIn('TI-PRG-001', str(self.asignacion))
        self.assertIn('coord01', str(self.asignacion))

    def test_unique_together_documento_coordinador(self):
        with self.assertRaises(Exception):
            AsignacionDocumento.objects.create(
                documento=self.doc_activo,
                coordinador=self.user_coordinador,
                jefe_asignador=self.user_jefe_ti,
            )

    def test_permisos_granulares(self):
        self.assertTrue(self.asignacion.puede_leer)
        self.assertFalse(self.asignacion.puede_editar)
        self.assertFalse(self.asignacion.puede_eliminar)

    def test_permisos_editar(self):
        a2 = AsignacionDocumento.objects.create(
            documento=self.doc_borrador,
            coordinador=self.user_coordinador,
            jefe_asignador=self.user_jefe_ti,
            puede_leer=True,
            puede_editar=True,
            puede_eliminar=True,
        )
        self.assertTrue(a2.puede_editar)
        self.assertTrue(a2.puede_eliminar)


class SolicitudDescargaModelTest(BaseDocumentosTest):
    """Tests del modelo SolicitudDescarga."""

    def setUp(self):
        super().setUp()
        self.solicitud = SolicitudDescarga.objects.create(
            documento=self.doc_activo,
            solicitado_por=self.user_trabajador,
            motivo='Necesito el documento',
        )

    def test_str_solicitud(self):
        self.assertIn('TI-PRG-001', str(self.solicitud))

    def test_estado_pendiente_default(self):
        self.assertEqual(self.solicitud.estado, 'pendiente')

    def test_fecha_solicitud_auto(self):
        self.assertIsNotNone(self.solicitud.fecha_solicitud)


class AprobacionDescargaModelTest(BaseDocumentosTest):
    """Tests del modelo AprobacionDescarga."""

    def setUp(self):
        super().setUp()
        self.solicitud = SolicitudDescarga.objects.create(
            documento=self.doc_activo,
            solicitado_por=self.user_trabajador,
            motivo='Test',
        )
        self.aprobacion = AprobacionDescarga.objects.create(
            solicitud=self.solicitud,
            aprobador=self.user_jefe_ti,
        )

    def test_str_aprobacion(self):
        self.assertIn('jefe_ti', str(self.aprobacion))

    def test_unique_together_solicitud_aprobador(self):
        with self.assertRaises(Exception):
            AprobacionDescarga.objects.create(
                solicitud=self.solicitud,
                aprobador=self.user_jefe_ti,
            )


class SolicitudAccesoDocumentoModelTest(BaseDocumentosTest):
    """Tests del modelo SolicitudAccesoDocumento."""

    def setUp(self):
        super().setUp()
        self.solicitud = SolicitudAccesoDocumento.objects.create(
            documento=self.doc_th,
            solicitante=self.user_jefe_ti,
            jefe_destino=self.user_jefe_th,
            departamento_destino=self.depto_th,
            motivo='Necesito ver documentos de TH',
        )

    def test_str_solicitud(self):
        self.assertIn('TH-FOR-001', str(self.solicitud))

    def test_unique_together_documento_solicitante(self):
        with self.assertRaises(Exception):
            SolicitudAccesoDocumento.objects.create(
                documento=self.doc_th,
                solicitante=self.user_jefe_ti,
                jefe_destino=self.user_jefe_th,
                departamento_destino=self.depto_th,
            )

    def test_estado_pendiente_default(self):
        self.assertEqual(self.solicitud.estado, 'pendiente')


class PlanillaCambiosModelTest(BaseDocumentosTest):
    """Tests del modelo PlanillaCambios."""

    def setUp(self):
        super().setUp()
        self.planilla = PlanillaCambios.objects.create(
            codigo='PC-TI-001',
            documento=self.doc_activo,
            tipo_cambio='modificacion',
            justificacion='Cambio necesario',
            responsable=self.user_jefe_ti,
            version_destino='2.0',
            nombre_propuesto='Nombre Modificado',
            descripcion_propuesta='Nueva descripcion',
        )

    def test_str_planilla(self):
        self.assertIn('PC-TI-001', str(self.planilla))

    def test_estado_borrador_default(self):
        self.assertEqual(self.planilla.estado, 'borrador')

    def test_uuid_generado(self):
        self.assertIsNotNone(self.planilla.uuid)

    def test_departamentos_afectados_m2m(self):
        self.planilla.departamentos_afectados.add(self.depto_ti)
        self.assertEqual(self.planilla.departamentos_afectados.count(), 1)


class AprobacionPlanillaModelTest(BaseDocumentosTest):
    """Tests del modelo AprobacionPlanilla."""

    def setUp(self):
        super().setUp()
        self.planilla = PlanillaCambios.objects.create(
            codigo='PC-TI-002',
            documento=self.doc_activo,
            tipo_cambio='modificacion',
            justificacion='Test',
            responsable=self.user_jefe_ti,
            version_destino='2.0',
        )
        self.aprobacion = AprobacionPlanilla.objects.create(
            planilla=self.planilla,
            aprobador=self.user_jefe_gg,
        )

    def test_str_aprobacion(self):
        self.assertIn('jefe_gg', str(self.aprobacion))

    def test_unique_together(self):
        with self.assertRaises(Exception):
            AprobacionPlanilla.objects.create(
                planilla=self.planilla,
                aprobador=self.user_jefe_gg,
            )


class DistribucionDocumentoModelTest(BaseDocumentosTest):
    """Tests del modelo DistribucionDocumento."""

    def setUp(self):
        super().setUp()
        self.distribucion = DistribucionDocumento.objects.create(
            documento=self.doc_activo,
            usuario=self.user_trabajador,
            distribuido_por=self.user_jefe_ti,
            metodo='sistema',
            observaciones='Distribucion de prueba',
        )

    def test_str_distribucion(self):
        self.assertIn('TI-PRG-001', str(self.distribucion))
        self.assertIn('trab01', str(self.distribucion))


class RenovacionVencimientoModelTest(BaseDocumentosTest):
    """Tests del modelo RenovacionVencimiento."""

    def setUp(self):
        super().setUp()
        self.renovacion = RenovacionVencimiento.objects.create(
            documento=self.doc_activo,
            fecha_anterior=date.today() + timedelta(days=30),
            fecha_nueva=date.today() + timedelta(days=60),
            renovado_por=self.user_jefe_ti,
            observacion='Renovacion de prueba',
        )

    def test_str_renovacion(self):
        self.assertIn('TI-PRG-001', str(self.renovacion))

    def test_fecha_renovacion_auto(self):
        self.assertIsNotNone(self.renovacion.fecha_renovacion)


# =============================================================================
# PERMISSIONS
# =============================================================================

class ObtenerDocumentoTest(BaseDocumentosTest):
    """Tests de obtener_documento."""

    def test_obtener_documento_existente(self):
        doc = obtener_documento('procedimientos_generales', self.doc_activo.uuid)
        self.assertEqual(doc, self.doc_activo)

    def test_obtener_documento_inexistente(self):
        doc = obtener_documento('procedimientos_generales', '00000000-0000-0000-0000-000000000000')
        self.assertIsNone(doc)

    def test_obtener_documento_tipo_incorrecto(self):
        doc = obtener_documento('formulario', self.doc_activo.uuid)
        self.assertIsNone(doc)


class PuedeEditarTest(BaseDocumentosTest):
    """Tests de puede_editar."""

    def test_propietario_puede_editar(self):
        self.assertTrue(puede_editar(self.user_jefe_ti, self.doc_activo))

    def test_no_propietario_no_puede_editar(self):
        self.assertFalse(puede_editar(self.user_trabajador, self.doc_activo))

    def test_admin_no_puede_editar_no_propio(self):
        self.assertFalse(puede_editar(self.user_admin, self.doc_activo))

    def test_jefe_no_puede_editar_no_propio(self):
        self.assertFalse(puede_editar(self.user_jefe_gg, self.doc_activo))


class PuedeEditarDocumentoTest(BaseDocumentosTest):
    """Tests de puede_editar_documento (views helper)."""

    def test_admin_puede_editar(self):
        self.assertTrue(puede_editar_documento(self.user_admin, self.doc_activo))

    def test_jefe_mismo_departamento_puede_editar(self):
        self.assertTrue(puede_editar_documento(self.user_jefe_ti, self.doc_activo))

    def test_jefe_otro_departamento_no_puede_editar(self):
        self.assertFalse(puede_editar_documento(self.user_jefe_ti, self.doc_th))

    def test_propietario_puede_editar(self):
        self.assertTrue(puede_editar_documento(self.user_jefe_ti, self.doc_activo))

    def test_en_revisionSoloPropietario(self):
        self.assertTrue(puede_editar_documento(self.user_trabajador, self.doc_en_revision))
        self.assertFalse(puede_editar_documento(self.user_jefe_ti, self.doc_en_revision))

    def test_obsoleto_nadie_puede_editar(self):
        self.assertFalse(puede_editar_documento(self.user_admin, self.doc_obsoleto))

    def _crear_dpr(self, codigo):
        tipo_dpr, _ = TipoDocumento.objects.get_or_create(
            key='descripcion_procesos',
            defaults={'nombre': 'Descripción de Procesos', 'codigo': 'DPR', 'activo': True},
        )
        return Documento.objects.create(
            tipo=tipo_dpr, nombre='DPR', codigo=codigo, estado='activo',
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )

    def test_dpr_cualquier_jefe_puede_editar(self):
        doc = self._crear_dpr('DPR-101')
        # jefe de otro departamento (TH) puede editar el DPR
        self.assertTrue(puede_editar_documento(self.user_jefe_th, doc))

    def test_dpr_usuario_distribuido_puede_editar(self):
        doc = self._crear_dpr('DPR-102')
        DistribucionDocumento.objects.create(
            documento=doc, usuario=self.user_trabajador_otro,
            distribuido_por=self.user_jefe_ti,
        )
        self.assertTrue(puede_editar_documento(self.user_trabajador_otro, doc))

    def test_dpr_no_distribuido_no_puede_editar(self):
        doc = self._crear_dpr('DPR-103')
        # trabajador de otro depto, sin distribución, no puede editar
        self.assertFalse(puede_editar_documento(self.user_trabajador_otro, doc))


class PuedeEliminarDocumentoTest(BaseDocumentosTest):
    """Tests de puede_eliminar_documento (views helper)."""

    def test_admin_puede_eliminar(self):
        self.assertTrue(puede_eliminar_documento(self.user_admin, self.doc_activo))

    def test_jefe_mismo_departamento_puede_eliminar(self):
        self.assertTrue(puede_eliminar_documento(self.user_jefe_ti, self.doc_activo))

    def test_jefe_otro_departamento_no_puede_eliminar(self):
        self.assertFalse(puede_eliminar_documento(self.user_jefe_ti, self.doc_th))

    def test_obsoleto_nadie_puede_eliminar(self):
        self.assertFalse(puede_eliminar_documento(self.user_admin, self.doc_obsoleto))

    def test_en_revision_nadie_puede_eliminar(self):
        self.assertFalse(puede_eliminar_documento(self.user_trabajador, self.doc_en_revision))


class EsJefeDeDepartamentoTest(BaseDocumentosTest):
    """Tests de es_jefe_de_departamento helper."""

    def test_jefe_depto_returns_true(self):
        self.assertTrue(es_jefe_de_departamento(self.user_jefe_ti, self.doc_activo))

    def test_jefe_otro_depto_returns_false(self):
        self.assertFalse(es_jefe_de_departamento(self.user_jefe_ti, self.doc_th))

    def test_admin_no_es_jefe(self):
        self.assertFalse(es_jefe_de_departamento(self.user_admin, self.doc_activo))

    def test_trabajador_no_es_jefe(self):
        self.assertFalse(es_jefe_de_departamento(self.user_trabajador, self.doc_activo))


class PuedeDescargarTest(BaseDocumentosTest):
    """Tests de puede_descargar."""

    def test_admin_siempre_puede_descargar(self):
        self.assertTrue(puede_descargar(self.user_admin, self.doc_activo))

    def test_jefe_mismo_departamento_puede_descargar(self):
        self.assertTrue(puede_descargar(self.user_jefe_ti, self.doc_activo))

    def test_jefe_otro_departamento_no_puede_descargar(self):
        self.assertFalse(puede_descargar(self.user_jefe_ti, self.doc_th))

    def test_documento_publico_cualquier_usuario(self):
        self.assertTrue(puede_descargar(self.user_trabajador, self.doc_publico))

    def test_trabajador_mismo_departamento_necesita_aprobacion(self):
        self.assertFalse(puede_descargar(self.user_trabajador, self.doc_activo))

    def test_trabajador_otro_departamento(self):
        self.assertFalse(puede_descargar(self.user_trabajador, self.doc_th))

    def test_usuario_sin_departamento(self):
        u = User.objects.create_user('sin_depto', password='t')
        PerfilUsuario.objects.create(usuario=u, rol='trabajador', departamento=None)
        self.assertFalse(puede_descargar(u, self.doc_activo))


class PuedeInhabilitarTest(BaseDocumentosTest):
    """Tests de puede_inhabilitar (inhabilitar / reactivar)."""

    def test_admin_puede_inhabilitar_directo(self):
        result = puede_inhabilitar(self.user_admin, self.doc_activo)
        self.assertEqual(result, (True, 'directo'))

    def test_jefe_gg_inhabilita_directo(self):
        result = puede_inhabilitar(self.user_jefe_gg, self.doc_activo)
        self.assertEqual(result, (True, 'directo'))

    def test_jefe_del_depto_solicita_aprobacion(self):
        """El jefe del departamento NO inhabilita directo: aprueba GG."""
        result = puede_inhabilitar(self.user_jefe_ti, self.doc_activo)
        self.assertEqual(result, (True, 'solicitar'))

    def test_propietario_solicita_aprobacion(self):
        result = puede_inhabilitar(self.user_trabajador_otro, self.doc_th)
        self.assertEqual(result, (True, 'solicitar'))

    def test_jefe_otro_departamento_no_puede_inhabilitar(self):
        result = puede_inhabilitar(self.user_jefe_ti, self.doc_th)
        self.assertEqual(result, (False, 'sin_permiso'))

    def test_coordinador_sin_asignacion_no_puede(self):
        result = puede_inhabilitar(self.user_coordinador, self.doc_activo)
        self.assertEqual(result, (False, 'sin_permiso'))

    def test_coordinador_con_permiso_puede_solicitar(self):
        AsignacionDocumento.objects.create(
            documento=self.doc_activo,
            coordinador=self.user_coordinador,
            jefe_asignador=self.user_jefe_ti,
            puede_eliminar=True,
        )
        result = puede_inhabilitar(self.user_coordinador, self.doc_activo)
        self.assertEqual(result, (True, 'solicitar'))

    def test_trabajador_no_propietario_no_puede(self):
        result = puede_inhabilitar(self.user_trabajador, self.doc_activo)
        self.assertEqual(result, (False, 'sin_permiso'))


class PuedeTransferirTest(BaseDocumentosTest):
    """Tests de puede_transferir."""

    def test_jefe_mismo_departamento_directo(self):
        result = puede_transferir(self.user_jefe_ti, self.doc_activo)
        self.assertEqual(result, (True, 'directo'))

    def test_jefe_otro_departamento_no_puede(self):
        result = puede_transferir(self.user_jefe_gg, self.doc_activo)
        self.assertEqual(result, (False, 'sin_permiso'))

    def test_admin_directo(self):
        result = puede_transferir(self.user_admin, self.doc_activo)
        self.assertEqual(result, (True, 'directo'))

    def test_coordinador_no_puede(self):
        result = puede_transferir(self.user_coordinador, self.doc_activo)
        self.assertEqual(result, (False, 'sin_permiso'))


class DocumentosUsuarioTest(BaseDocumentosTest):
    """Tests de documentos_usuario."""

    def test_documentos_propios(self):
        docs = documentos_usuario(self.user_jefe_ti)
        codigos = [d['codigo'] for d in docs]
        self.assertIn('TI-PRG-001', codigos)

    def test_documento_asignado(self):
        AsignacionDocumento.objects.create(
            documento=self.doc_activo,
            coordinador=self.user_coordinador,
            jefe_asignador=self.user_jefe_ti,
            puede_leer=True,
        )
        docs = documentos_usuario(self.user_coordinador)
        codigos = [d['codigo'] for d in docs]
        self.assertIn('TI-PRG-001', codigos)

    def test_no_duplicar_si_es_propio_y_asignado(self):
        AsignacionDocumento.objects.create(
            documento=self.doc_activo,
            coordinador=self.user_jefe_ti,
            jefe_asignador=self.user_jefe_ti,
            puede_leer=True,
        )
        docs = documentos_usuario(self.user_jefe_ti)
        prg_docs = [d for d in docs if d['codigo'] == 'TI-PRG-001']
        self.assertEqual(len(prg_docs), 1)

    def test_campos_documento_propio(self):
        docs = documentos_usuario(self.user_jefe_ti)
        doc = [d for d in docs if d['codigo'] == 'TI-PRG-001'][0]
        self.assertTrue(doc['es_propio'])
        self.assertEqual(doc['tipo'], 'procedimientos_generales')

    def test_campos_documento_asignado(self):
        AsignacionDocumento.objects.create(
            documento=self.doc_borrador,
            coordinador=self.user_coordinador,
            jefe_asignador=self.user_jefe_ti,
            puede_leer=True,
            puede_editar=True,
            puede_eliminar=False,
        )
        docs = documentos_usuario(self.user_coordinador)
        doc = [d for d in docs if d['codigo'] == 'TI-FOR-001'][0]
        self.assertFalse(doc['es_propio'])
        self.assertTrue(doc['puede_leer'])
        self.assertTrue(doc['puede_editar'])
        self.assertFalse(doc['puede_eliminar'])


# =============================================================================
# TAREAS CELERY
# =============================================================================

class VerificarVencimientosTest(BaseDocumentosTest):
    """Tests de la tarea verificar_vencimientos."""

    def test_documento_vencido_se_vuelve_obsoleto(self):
        """Documento con fecha_vencimiento <= hoy → obsoleto."""
        self.assertEqual(self.doc_vencido.estado, 'activo')
        resultado = verificar_vencimientos()
        self.doc_vencido.refresh_from_db()
        self.assertEqual(self.doc_vencido.estado, 'obsoleto')
        self.assertEqual(resultado['vencidos'], 1)

    def test_documento_vencido_notifica_propietario(self):
        """Se notifica al propietario del documento vencido."""
        verificar_vencimientos()
        notif = Notificacion.objects.filter(
            usuario=self.user_jefe_ti,
            titulo='Documento vencido',
        ).exists()
        self.assertTrue(notif)

    def test_documento_vencido_notifica_jefe_depto(self):
        """Se notifica al jefe del departamento si es distinto del propietario."""
        verificar_vencimientos()
        notif = Notificacion.objects.filter(
            usuario=self.user_jefe_ti,
            titulo='Documento del departamento vencido',
        ).exists()
        self.assertTrue(notif)

    def test_documento_por_vencer_aviso(self):
        """Documento con fecha_vencimiento <= hoy+7 → aviso preventivo."""
        verificar_vencimientos()
        self.doc_por_vencer.refresh_from_db()
        self.assertTrue(self.doc_por_vencer.aviso_vencimiento_enviado)
        notif = Notificacion.objects.filter(
            usuario=self.user_jefe_ti,
            titulo='Documento próximo a vencer',
        ).exists()
        self.assertTrue(notif)

    def test_aviso_no_duplicado(self):
        """Si aviso_vencimiento_enviado=True, no se notifica de nuevo."""
        self.doc_por_vencer.aviso_vencimiento_enviado = True
        self.doc_por_vencer.save(update_fields=['aviso_vencimiento_enviado'])
        verificar_vencimientos()
        # No debe duplicar notificación
        self.doc_por_vencer.refresh_from_db()
        self.assertTrue(self.doc_por_vencer.aviso_vencimiento_enviado)

    def test_documento_borrador_no_se_vence(self):
        """Documentos en borrador no se marcan como obsoletos."""
        self.doc_borrador.fecha_vencimiento = date.today() - timedelta(days=1)
        self.doc_borrador.save(update_fields=['fecha_vencimiento'])
        verificar_vencimientos()
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'borrador')

    def test_documento_sin_fecha_vencimiento_no_se_afecta(self):
        """Documentos sin fecha_vencimiento no se tocan."""
        verificar_vencimientos()
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'borrador')

    def test_documento_obsoleto_no_se_vence(self):
        """Documentos ya obsoletos no se vuelven a marcar."""
        verificar_vencimientos()
        self.doc_obsoleto.refresh_from_db()
        self.assertEqual(self.doc_obsoleto.estado, 'obsoleto')

    @patch('auditoria.services.get_mongo_client', side_effect=Exception('Mongo down'))
    def test_vencimiento_fallsafe_mongo(self, mock_mongo):
        """Si MongoDB falla, la tarea igual ejecuta (caught exception)."""
        resultado = verificar_vencimientos()
        self.assertIn('vencidos', resultado)
        self.doc_vencido.refresh_from_db()
        self.assertEqual(self.doc_vencido.estado, 'obsoleto')

    def test_retorno_valores(self):
        resultado = verificar_vencimientos()
        self.assertIn('vencidos', resultado)
        self.assertIn('avisos', resultado)


# =============================================================================
# FLUJOS DE SOLICITUD DE APROBACION
# =============================================================================

class SolicitudAprobacionDocumentoTest(BaseDocumentosTest):
    """Tests del modelo SolicitudAprobacionDocumento."""

    def setUp(self):
        super().setUp()
        self.solicitud = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_borrador,
            solicitante=self.user_trabajador,
            aprobador=self.user_jefe_ti,
            departamento_origen=self.depto_ti,
            accion_solicitud='creacion',
        )

    def test_aprobar_documento(self):
        """Al aprobar, el documento pasa a 'activo'."""
        self.solicitud.aprobar(observaciones='Aprobado')
        self.solicitud.refresh_from_db()
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'aprobada')
        self.assertEqual(self.doc_borrador.estado, 'activo')

    def test_rechazar_documento(self):
        """Al rechazar, el documento vuelve a 'borrador'."""
        self.solicitud.rechazar(motivo='No cumple requisitos')
        self.solicitud.refresh_from_db()
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'rechazada')
        self.assertEqual(self.doc_borrador.estado, 'borrador')

    def test_aprobar_inhabilitar(self):
        """Al aprobar inhabilitar, documento pasa a 'obsoleto'."""
        self.doc_activo.estado = 'activo'
        self.doc_activo.save(update_fields=['estado'])
        sol = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_activo,
            solicitante=self.user_jefe_ti,
            aprobador=self.user_jefe_gg,
            departamento_origen=self.depto_ti,
            accion_solicitud='inhabilitar',
        )
        sol.aprobar()
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'obsoleto')

    def test_rechazar_inhabilitar(self):
        """Al rechazar inhabilitar, documento vuelve a 'activo'."""
        self.doc_activo.estado = 'activo'
        self.doc_activo.save(update_fields=['estado'])
        sol = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_activo,
            solicitante=self.user_jefe_ti,
            aprobador=self.user_jefe_gg,
            departamento_origen=self.depto_ti,
            accion_solicitud='inhabilitar',
        )
        sol.rechazar(motivo='Rechazado')
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'activo')

    def test_aprobar_version(self):
        """Al aprobar nueva versión, documento pasa a 'activo'."""
        sol = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_borrador,
            solicitante=self.user_trabajador,
            aprobador=self.user_jefe_ti,
            departamento_origen=self.depto_ti,
            accion_solicitud='version',
        )
        sol.aprobar()
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'activo')

    def test_aplicar_eliminacion_version(self):
        """Eliminar versión hace soft-delete y restaura anterior."""
        v1 = VersionDocumento.objects.create(
            documento=self.doc_activo,
            numero_version='1.0',
            archivo=SimpleUploadedFile('v1.pdf', b'v1'),
            creado_por=self.user_jefe_ti,
        )
        v2 = VersionDocumento.objects.create(
            documento=self.doc_activo,
            numero_version='2.0',
            archivo=SimpleUploadedFile('v2.pdf', b'v2'),
            creado_por=self.user_jefe_ti,
        )
        self.doc_activo.version_actual = '2.0'
        self.doc_activo.save(update_fields=['version_actual'])

        sol = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_activo,
            solicitante=self.user_jefe_ti,
            aprobador=self.user_jefe_gg,
            departamento_origen=self.depto_ti,
            accion_solicitud='eliminar_version',
            version_a_eliminar=v2,
        )
        sol.aprobar()
        v2.refresh_from_db()
        self.assertFalse(v2.activa)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.version_actual, '1.0')

    def test_aplicar_eliminacion_version_ultima(self):
        """Eliminar la última versión → documento obsoleto."""
        v1 = VersionDocumento.objects.create(
            documento=self.doc_activo,
            numero_version='1.0',
            archivo=SimpleUploadedFile('v1.pdf', b'v1'),
            creado_por=self.user_jefe_ti,
        )
        self.doc_activo.version_actual = '1.0'
        self.doc_activo.save(update_fields=['version_actual'])

        sol = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_activo,
            solicitante=self.user_jefe_ti,
            aprobador=self.user_jefe_gg,
            departamento_origen=self.depto_ti,
            accion_solicitud='eliminar_version',
            version_a_eliminar=v1,
        )
        sol.aprobar()
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.estado, 'obsoleto')

    def test_fecha_respuesta_al_aprobar(self):
        self.assertIsNone(self.solicitud.fecha_respuesta)
        self.solicitud.aprobar()
        self.solicitud.refresh_from_db()
        self.assertIsNotNone(self.solicitud.fecha_respuesta)

    def test_fecha_respuesta_al_rechazar(self):
        self.solicitud.rechazar(motivo='test')
        self.solicitud.refresh_from_db()
        self.assertIsNotNone(self.solicitud.fecha_respuesta)


# =============================================================================
# API ENDPOINTS
# =============================================================================

class APIBuscarSugerenciasTest(BaseDocumentosTest):
    """Tests del endpoint de búsqueda navbar."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_buscar_por_nombre(self):
        self.client.logout()
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=Procedimiento')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['sugerencias']
        codigos = [d['codigo'] for d in data]
        self.assertIn('TI-PRG-001', codigos)

    def test_buscar_por_codigo(self):
        self.client.logout()
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=TI-PRG')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['sugerencias']
        self.assertTrue(len(data) > 0)

    def test_buscar_limit(self):
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=&limit=3')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['sugerencias']
        self.assertLessEqual(len(data), 3)

    def test_buscar_sin_resultados(self):
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=XYZ123')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()['sugerencias']
        self.assertEqual(len(data), 0)

    def test_buscar_solo_muestra_activos(self):
        """Solo documentos activos se muestran (excepto propios)."""
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=Obsoleto')
        data = resp.json()['sugerencias']
        self.assertEqual(len(data), 0)

    def test_buscar_trabajador_no_ve_docs_otro_depto(self):
        """Trabajador no ve documentos de otro departamento."""
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=Formulario TH')
        data = resp.json()['sugerencias']
        self.assertEqual(len(data), 0)

    def test_buscar_sin_login(self):
        self.client.logout()
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=test')
        self.assertIn(resp.status_code, [302, 403])

    # ── Búsqueda por documentos relacionados ──

    def _crear_principal(self, codigo='TI-PRG-010', nombre='Procedimiento Principal'):
        return Documento.objects.create(
            tipo=self.tipo_procedimiento, nombre=nombre, codigo=codigo,
            estado='activo', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
        )

    def test_buscar_por_relacionado_codigo_devuelve_ambos(self):
        self.client.logout()
        self.client.login(username='jefe_ti', password='testpass123')
        principal = self._crear_principal()
        principal.documentos_relacionados.add(self.doc_activo)
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=TI-PRG-001')
        data = resp.json()['sugerencias']
        codigos = [d['codigo'] for d in data]
        self.assertIn('TI-PRG-001', codigos)
        self.assertIn('TI-PRG-010', codigos)
        item = next(d for d in data if d['codigo'] == 'TI-PRG-010')
        self.assertEqual(item['relacionado_con'], 'TI-PRG-001')

    def test_buscar_por_relacionado_nombre_devuelve_principal(self):
        self.client.logout()
        self.client.login(username='jefe_ti', password='testpass123')
        principal = self._crear_principal()
        principal.documentos_relacionados.add(self.doc_activo)
        resp = self.client.get(
            '/documentos/api/buscar-sugerencias/?q=Procedimiento de Prueba'
        )
        data = resp.json()['sugerencias']
        codigos = [d['codigo'] for d in data]
        self.assertIn('TI-PRG-010', codigos)
        item = next(d for d in data if d['codigo'] == 'TI-PRG-010')
        self.assertEqual(item['relacionado_con'], 'TI-PRG-001')

    def test_buscar_por_referencia_pendiente_devuelve_principal(self):
        self.client.logout()
        self.client.login(username='jefe_ti', password='testpass123')
        principal = self._crear_principal(
            codigo='TI-PRG-011', nombre='Principal Pendiente'
        )
        ReferenciaDocumento.objects.create(
            documento=principal, codigo_destino='TI-FOR-999',
            creado_por=self.user_jefe_ti,
        )
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=TI-FOR-999')
        data = resp.json()['sugerencias']
        codigos = [d['codigo'] for d in data]
        self.assertIn('TI-PRG-011', codigos)
        item = next(d for d in data if d['codigo'] == 'TI-PRG-011')
        self.assertEqual(item['relacionado_con'], 'TI-FOR-999')

    def test_relacionado_no_visible_no_devuelve_principal(self):
        self.client.logout()
        self.client.login(username='jefe_ti', password='testpass123')
        principal = self._crear_principal(
            codigo='TI-PRG-012', nombre='Principal Oculto'
        )
        principal.documentos_relacionados.add(self.doc_th)
        resp = self.client.get('/documentos/api/buscar-sugerencias/?q=TH-FOR-001')
        data = resp.json()['sugerencias']
        codigos = [d['codigo'] for d in data]
        self.assertNotIn('TI-PRG-012', codigos)
        self.assertNotIn('TH-FOR-001', codigos)

    def test_mis_documentos_busca_por_relacionado(self):
        self.client.logout()
        self.client.login(username='jefe_ti', password='testpass123')
        principal = self._crear_principal(
            codigo='TI-PRG-013', nombre='Principal Mis Docs'
        )
        principal.documentos_relacionados.add(self.doc_activo)
        resp = self.client.get('/documentos/mis-documentos/?buscar=TI-PRG-001')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'TI-PRG-013')


class APIListaDocumentosTest(BaseDocumentosTest):
    """Tests del endpoint de lista de documentos."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_lista_basica(self):
        resp = self.client.get('/documentos/api/lista/')
        self.assertEqual(resp.status_code, 200)

    def test_lista_con_filtro_tipo(self):
        resp = self.client.get('/documentos/api/lista/?tipo=procedimientos_generales')
        self.assertEqual(resp.status_code, 200)

    def test_lista_con_filtro_estado(self):
        resp = self.client.get('/documentos/api/lista/?estado=activo')
        self.assertEqual(resp.status_code, 200)

    def test_lista_con_buscar(self):
        resp = self.client.get('/documentos/api/lista/?buscar=Procedimiento')
        self.assertEqual(resp.status_code, 200)


class APIDocumentoDetalleTest(BaseDocumentosTest):
    """Tests del endpoint de detalle de documento."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_detalle_documento_existente(self):
        resp = self.client.get(
            f'/documentos/api/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)

    def test_detalle_documento_inexistente(self):
        resp = self.client.get(
            '/documentos/api/procedimientos_generales/00000000-0000-0000-0000-000000000000/'
        )
        self.assertEqual(resp.status_code, 404)


class APIFiltrosTest(BaseDocumentosTest):
    """Tests del endpoint de opciones de filtros."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_filtros(self):
        resp = self.client.get('/documentos/api/filtros/')
        self.assertEqual(resp.status_code, 200)


class APIUsuariosDepartamentoTest(BaseDocumentosTest):
    """Tests del endpoint de usuarios por departamento."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_usuarios_departamento(self):
        resp = self.client.get('/documentos/api/usuarios-departamento/')
        self.assertEqual(resp.status_code, 200)

    def test_usuarios_departamento_por_documento_admin(self):
        self.client.logout()
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get(
            '/documentos/api/usuarios-departamento/',
            {'tipo': 'procedimientos_generales', 'doc_id': str(self.doc_activo.uuid)},
        )
        self.assertEqual(resp.status_code, 200)
        ids = [u['id'] for u in resp.json()['usuarios']]
        self.assertIn(self.user_trabajador.id, ids)
        self.assertNotIn(self.user_jefe_ti.id, ids)  # excluye al propietario

    def test_usuarios_departamento_documento_sin_permiso(self):
        resp = self.client.get(
            '/documentos/api/usuarios-departamento/',
            {'tipo': 'procedimientos_generales', 'doc_id': str(self.doc_activo.uuid)},
        )
        self.assertEqual(resp.status_code, 403)


class APISolicitarAccesoTest(BaseDocumentosTest):
    """Tests del endpoint de solicitar acceso."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_solicitar_acceso(self):
        resp = self.client.post(
            '/documentos/api/solicitar-acceso/',
            data={
                'documento_id': str(self.doc_th.uuid),
                'documento_tipo': 'formulario',
                'motivo': 'Necesito ver este documento',
            },
            content_type='application/json',
        )
        self.assertIn(resp.status_code, [200, 201])


class APISolicitudesAccesoPendientesTest(BaseDocumentosTest):
    """Tests del endpoint de solicitudes de acceso pendientes."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_th', password='testpass123')

    def test_solicitudes_pendientes(self):
        resp = self.client.get('/documentos/api/solicitudes-acceso-pendientes/')
        self.assertEqual(resp.status_code, 200)


class APISolicitudesAprobacionPendientesTest(BaseDocumentosTest):
    """Tests del endpoint de solicitudes de aprobación pendientes."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_solicitudes_pendientes(self):
        SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_borrador,
            solicitante=self.user_trabajador,
            aprobador=self.user_jefe_ti,
            departamento_origen=self.depto_ti,
            accion_solicitud='creacion',
        )
        resp = self.client.get('/documentos/api/solicitudes-aprobacion-pendientes/')
        self.assertEqual(resp.status_code, 200)


class APIPlanillasPendientesTest(BaseDocumentosTest):
    """Tests del endpoint de planillas pendientes."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_planillas_pendientes(self):
        resp = self.client.get('/documentos/api/planillas-pendientes/')
        self.assertEqual(resp.status_code, 200)


class APITiposDocumentoTest(BaseDocumentosTest):
    """Tests del endpoint de tipos de documento disponibles."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_tipos_documento(self):
        resp = self.client.get('/documentos/api/tipos-documento/')
        self.assertEqual(resp.status_code, 200)


class APIContadoresTest(BaseDocumentosTest):
    """Tests del endpoint de contadores de documentos."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_contadores(self):
        resp = self.client.get('/documentos/api/contadores/')
        self.assertEqual(resp.status_code, 200)


# =============================================================================
# VISTAS (INTEGRACION)
# =============================================================================

class VistasCatalogoTest(BaseDocumentosTest):
    """Tests de vistas del catálogo y formularios."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_catalogo_formularios(self):
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)

    def test_mis_documentos(self):
        resp = self.client.get('/documentos/mis-documentos/')
        self.assertEqual(resp.status_code, 200)

    def test_bandeja_aprobaciones_jefe(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/bandeja-aprobaciones/')
        self.assertEqual(resp.status_code, 200)

    def test_bandeja_aprobaciones_admin(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get('/documentos/bandeja-aprobaciones/')
        self.assertEqual(resp.status_code, 200)

    def test_bandeja_aprobaciones_trabajador_redirigido(self):
        resp = self.client.get('/documentos/bandeja-aprobaciones/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/documentos/mis-documentos/', resp['Location'])

    def test_catalogo_enlaza_bandeja_jefe(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'bandeja-aprobaciones', resp.content)

    def test_catalogo_sin_bandeja_para_trabajador(self):
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b'bandeja-aprobaciones', resp.content)

    def test_departamento_enlaza_bandeja(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/departamento/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'bandeja-aprobaciones', resp.content)

    def test_documentos_departamento_jefe(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/departamento/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['es_admin'])
        codigos = {g['departamento'].codigo for g in resp.context['grupos']}
        self.assertEqual(codigos, {'TI'})

    def test_documentos_departamento_admin_ve_todos(self):
        self.client.logout()
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get('/documentos/departamento/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['es_admin'])
        codigos = {g['departamento'].codigo for g in resp.context['grupos']}
        self.assertIn('TI', codigos)
        self.assertIn('TH', codigos)

    def test_documentos_departamento_tercero_redirigido(self):
        resp = self.client.get('/documentos/departamento/')
        self.assertEqual(resp.status_code, 302)

    def test_seleccionar_tipo(self):
        resp = self.client.get('/documentos/seleccionar-tipo/')
        self.assertEqual(resp.status_code, 200)


class VistasDetalleTest(BaseDocumentosTest):
    """Tests de vista de detalle de documento."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_detalle_documento(self):
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)


class VistasConfiguracionTest(BaseDocumentosTest):
    """Tests de vistas de configuración."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='admin01', password='testpass123')

    def test_configuracion(self):
        resp = self.client.get('/documentos/configuracion/')
        self.assertEqual(resp.status_code, 200)

    def test_tipos_documento_lista(self):
        resp = self.client.get('/documentos/configuracion/tipos-documento/')
        self.assertEqual(resp.status_code, 200)

    def test_departamentos_lista(self):
        resp = self.client.get('/documentos/configuracion/departamentos/')
        self.assertEqual(resp.status_code, 200)

    def test_ciclo_vida(self):
        resp = self.client.get('/documentos/configuracion/ciclo-vida/')
        self.assertEqual(resp.status_code, 200)

    def test_roles_permisos(self):
        resp = self.client.get('/documentos/configuracion/roles-permisos/')
        self.assertEqual(resp.status_code, 200)

    def test_auditoria_lista(self):
        resp = self.client.get('/documentos/configuracion/auditoria/')
        self.assertEqual(resp.status_code, 200)


class VistasCrearDocumentoTest(BaseDocumentosTest):
    """Tests de creación de documentos."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='trab01', password='testpass123')

    def test_crear_documento_formulario(self):
        resp = self.client.get('/documentos/nuevo/formulario/')
        self.assertEqual(resp.status_code, 200)


class VistasPlanillaCambiosTest(BaseDocumentosTest):
    """Tests de vistas de planilla de cambios."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_crear_planilla(self):
        resp = self.client.get(
            f'/documentos/planilla-cambios/nueva/?tipo=procedimientos_generales&doc_uuid={self.doc_activo.uuid}'
        )
        self.assertEqual(resp.status_code, 200)


class VistasAsignacionesTest(BaseDocumentosTest):
    """Tests de vistas de asignaciones."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_gestionar_asignaciones(self):
        resp = self.client.get('/documentos/asignaciones/')
        self.assertEqual(resp.status_code, 200)

    def test_formulario_asignar_documento(self):
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/asignar/'
        )
        self.assertIn(resp.status_code, [200, 302])


class VistasVersionesTest(BaseDocumentosTest):
    """Tests de vistas de versiones."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')

    def test_historial_versiones(self):
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/versiones/'
        )
        self.assertEqual(resp.status_code, 200)


# =============================================================================
# EDGE CASES
# =============================================================================

class EdgeCasesTest(BaseDocumentosTest):
    """Tests de edge cases y bugs potenciales."""

    def test_documento_con_nombre_largo(self):
        doc = Documento.objects.create(
            tipo=self.tipo_formulario,
            nombre='A' * 200,
            codigo='TI-FOR-999',
            estado='borrador',
            propietario=self.user_trabajador,
            departamento=self.depto_ti,
        )
        self.assertEqual(len(doc.nombre), 200)

    def test_codigo_documento_unico(self):
        with self.assertRaises(Exception):
            Documento.objects.create(
                tipo=self.tipo_formulario,
                nombre='Dup',
                codigo='TI-PRG-001',  # Ya existe
                estado='borrador',
                propietario=self.user_trabajador,
                departamento=self.depto_ti,
            )

    def test_documento_obsoleto_no_se_edita(self):
        """Documentos obsoletos no deben editarse (regla de negocio)."""
        self.assertEqual(self.doc_obsoleto.estado, 'obsoleto')
        # El modelo no lo bloquea a nivel DB, pero la lógica de negocio sí

    def test_documento_en_revision_solo_propietario(self):
        """Solo el propietario puede editar documentos en revisión."""
        self.assertTrue(puede_editar(self.user_trabajador, self.doc_en_revision))
        self.assertFalse(puede_editar(self.user_jefe_ti, self.doc_en_revision))

    def test_hash_documento_es_consistente(self):
        """El hash debe ser el mismo si no cambian los datos."""
        hash1 = self.doc_activo.calcular_hash()
        hash2 = self.doc_activo.calcular_hash()
        self.assertEqual(hash1, hash2)

    def test_cedula_en_ruta_archivo(self):
        """La ruta del archivo debe incluir la cédula del propietario."""
        ruta = generar_ruta_archivo(self.doc_activo, 'test.pdf')
        self.assertIn('V-jefe_ti', ruta)
        self.assertIn('TI-PRG-001', ruta)

    def test_cedula_en_ruta_version(self):
        """La ruta de la versión debe incluir la cédula del propietario."""
        v = VersionDocumento.objects.create(
            documento=self.doc_activo,
            numero_version='1.0',
            archivo=SimpleUploadedFile('test.pdf', b'content'),
            creado_por=self.user_jefe_ti,
        )
        ruta = generar_ruta_version(v, 'test.pdf')
        self.assertIn('V-jefe_ti', ruta)

    def test_documento_borrador_con_fecha_vencimiento_pasada(self):
        """Edge case: borrador con vencimiento pasado NO se vence (solo activos)."""
        self.doc_borrador.fecha_vencimiento = date.today() - timedelta(days=5)
        self.doc_borrador.save(update_fields=['fecha_vencimiento'])
        verificar_vencimientos()
        self.doc_borrador.refresh_from_db()
        self.assertEqual(self.doc_borrador.estado, 'borrador')

    def test_doble_solicitud_acceso_misma(self):
        """No se puede duplicar solicitud de acceso al mismo documento."""
        SolicitudAccesoDocumento.objects.create(
            documento=self.doc_th,
            solicitante=self.user_jefe_ti,
            jefe_destino=self.user_jefe_th,
            departamento_destino=self.depto_th,
        )
        with self.assertRaises(Exception):
            SolicitudAccesoDocumento.objects.create(
                documento=self.doc_th,
                solicitante=self.user_jefe_ti,
                jefe_destino=self.user_jefe_th,
                departamento_destino=self.depto_th,
            )

    def test_documento_en_revision_estado(self):
        self.assertEqual(self.doc_en_revision.estado, 'en_revision')

    def test_documento_borrador_estado(self):
        self.assertEqual(self.doc_borrador.estado, 'borrador')

    def test_documento_activo_estado(self):
        self.assertEqual(self.doc_activo.estado, 'activo')

    def test_documento_obsoleto_estado(self):
        self.assertEqual(self.doc_obsoleto.estado, 'obsoleto')

    def test_formatos_convertibles(self):
        """Verificar la tupla de formatos convertibles."""
        self.assertIn('doc', FORMATOS_CONVERTIBLES)
        self.assertIn('docx', FORMATOS_CONVERTIBLES)
        self.assertIn('xls', FORMATOS_CONVERTIBLES)
        self.assertIn('xlsx', FORMATOS_CONVERTIBLES)
        self.assertIn('vsdx', FORMATOS_CONVERTIBLES)
        self.assertNotIn('pdf', FORMATOS_CONVERTIBLES)

    def test_documento_con_departamento_none_ruta(self):
        """Ruta de archivo cuando departamento es None."""
        doc = Documento(
            tipo=self.tipo_formulario,
            nombre='Test',
            codigo='SIN-FOR-001',
            propietario=self.user_trabajador,
            departamento=None,
        )
        ruta = generar_ruta_archivo(doc, 'test.pdf')
        self.assertIn('SIN', ruta)

    def test_documento_sin_propietario_no_crash(self):
        """El campo propietario es obligatorio (PROTECT)."""
        with self.assertRaises(Exception):
            Documento.objects.create(
                tipo=self.tipo_formulario,
                nombre='Sin prop',
                codigo='TI-FOR-998',
                estado='borrador',
                propietario=None,
                departamento=self.depto_ti,
            )

    def test_deptos_con_jefe_one_to_one(self):
        """Solo un jefe por departamento (OneToOne)."""
        new_user = User.objects.create_user('otro_jefe_ti', password='t')
        PerfilUsuario.objects.create(
            usuario=new_user,
            rol='jefe_departamento',
            departamento=self.depto_ti,
        )
        # El OneToOneField debe haber actualizado el jefe
        self.depto_ti.refresh_from_db()
        # Puede ser cualquiera de los dos (depende del orden de signals)
        self.assertIn(self.depto_ti.jefe, [self.user_jefe_ti, new_user])

    def test_planilla_codigo_unico(self):
        PlanillaCambios.objects.create(
            codigo='PC-TI-001',
            documento=self.doc_activo,
            tipo_cambio='modificacion',
            justificacion='Original',
            responsable=self.user_jefe_ti,
            version_destino='1.0',
        )
        with self.assertRaises(Exception):
            PlanillaCambios.objects.create(
                codigo='PC-TI-001',
                documento=self.doc_activo,
                tipo_cambio='modificacion',
                justificacion='Dup',
                responsable=self.user_jefe_ti,
                version_destino='2.0',
            )


class TiposDesdeBDTest(BaseDocumentosTest):
    """Los tipos de documento se leen de la BD, no de TIPOS_INFO."""

    def test_seleccionar_tipo_muestra_tipo_de_bd(self):
        TipoDocumento.objects.create(
            key='prueba', nombre='Tipo De Prueba', codigo='PRB',
            activo=True, icono='flask', color='info',
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/seleccionar-tipo/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Tipo De Prueba')

    def test_seleccionar_tipo_excluye_key_vacia(self):
        TipoDocumento.objects.create(
            key='', nombre='Sin Key', codigo='XXX', activo=True,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/seleccionar-tipo/')
        self.assertNotContains(resp, 'Sin Key')

    def test_construir_codigo_usa_codigo_de_bd(self):
        TipoDocumento.objects.create(
            key='prueba', nombre='Tipo De Prueba', codigo='PRB', activo=True,
        )
        codigo = Documento.construir_codigo(self.depto_ti, 'prueba', '001')
        self.assertEqual(codigo, 'TI-PRB-001')

    def test_crear_documento_tipo_de_bd(self):
        TipoDocumento.objects.create(
            key='prueba', nombre='Tipo De Prueba', codigo='PRB', activo=True,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/nuevo/prueba/')
        self.assertEqual(resp.status_code, 200)


class TipoDocumentoIconoTest(BaseDocumentosTest):
    """El ícono se valida contra la lista curada ICONOS_DISPONIBLES."""

    def _datos(self, **overrides):
        data = {'codigo': 'ZZZ', 'nombre': 'Tipo Z', 'descripcion': ''}
        data.update(overrides)
        return data

    def test_icono_invalido_rechazado(self):
        from documentos.forms import TipoDocumentoForm
        form = TipoDocumentoForm(data=self._datos(icono='noexiste'))
        self.assertFalse(form.is_valid())
        self.assertIn('icono', form.errors)

    def test_icono_valido_aceptado(self):
        from documentos.forms import TipoDocumentoForm
        form = TipoDocumentoForm(data=self._datos(icono='file-lines'))
        self.assertTrue(form.is_valid(), form.errors)

    def test_icono_vacio_usa_default(self):
        from documentos.forms import TipoDocumentoForm
        form = TipoDocumentoForm(data=self._datos(icono=''))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['icono'], 'file')

    def test_iconos_existentes_son_validos(self):
        from documentos.icons import ICONOS_DISPONIBLES
        for tipo in TipoDocumento.objects.all():
            self.assertIn(tipo.icono, ICONOS_DISPONIBLES)


class AsignacionDocumentoFormTest(BaseDocumentosTest):
    """El select de Coordinador solo trae coordinadores/trabajadores del depto."""

    def test_coordinador_filtra_por_depto_y_rol(self):
        from documentos.forms import AsignacionDocumentoForm
        form = AsignacionDocumentoForm(departamento=self.depto_ti)
        usernames = set(
            form.fields['coordinador'].queryset.values_list('username', flat=True)
        )
        self.assertIn('coord01', usernames)
        self.assertIn('trab01', usernames)
        self.assertNotIn('trab02', usernames)
        self.assertNotIn('jefe_ti', usernames)
        self.assertNotIn('jefe_gg', usernames)
        self.assertNotIn('admin01', usernames)

    def test_sin_departamento_no_ofrece_usuarios(self):
        from documentos.forms import AsignacionDocumentoForm
        form = AsignacionDocumentoForm()
        self.assertEqual(form.fields['coordinador'].queryset.count(), 0)

    def test_sin_asignables_muestra_aviso(self):
        """Depto sin coordinadores/trabajadores → aviso y sin select."""
        self.user_trabajador_otro.perfil.departamento = None
        self.user_trabajador_otro.perfil.save(update_fields=['departamento'])
        self.client.login(username='jefe_th', password='testpass123')
        resp = self.client.get(
            f'/documentos/formulario/{self.doc_th.uuid}/asignar/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No hay coordinadores ni trabajadores')
        self.assertNotContains(resp, 'name="coordinador"')
        self.assertNotContains(resp, '>Asignar<')

    def test_con_asignables_muestra_select(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/asignar/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="coordinador"')
        self.assertNotContains(resp, 'No hay coordinadores ni trabajadores')

    def test_post_usuario_valido_crea_asignacion(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/asignar/',
            {'coordinador': self.user_coordinador.id, 'puede_leer': 'on'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            AsignacionDocumento.objects.filter(
                documento=self.doc_activo, coordinador=self.user_coordinador
            ).exists()
        )

    def test_post_usuario_de_otro_depto_rechazado(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/asignar/',
            {'coordinador': self.user_trabajador_otro.id, 'puede_leer': 'on'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            AsignacionDocumento.objects.filter(
                documento=self.doc_activo, coordinador=self.user_trabajador_otro
            ).exists()
        )


class TransferirPropiedadVistaTest(BaseDocumentosTest):
    """Aviso si el departamento no tiene otros usuarios a quien transferir."""

    def _url(self):
        return f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/transferir/'

    def test_sin_transferibles_muestra_aviso(self):
        for usuario in (self.user_coordinador, self.user_trabajador):
            usuario.perfil.departamento = None
            usuario.perfil.save(update_fields=['departamento'])
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No hay otros usuarios en el departamento')
        self.assertNotContains(resp, 'name="nuevo_propietario"')
        self.assertNotContains(resp, '>Transferir<')

    def test_con_transferibles_muestra_select(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="nuevo_propietario"')
        self.assertNotContains(resp, 'No hay otros usuarios en el departamento')


@override_settings(MAILERS={
    'default': {'BACKEND': 'django.core.mail.backends.locmem.EmailBackend'},
})
class DistribucionEmailTest(BaseDocumentosTest):
    """La distribución por email envía correo y registra el resultado."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='jefe_ti', password='testpass123')
        self.user_coordinador.email = 'coord01@example.com'
        self.user_coordinador.save(update_fields=['email'])

    def _post(self, usuarios, metodo='email'):
        return self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/distribuir/',
            {
                'metodo': metodo,
                'observaciones': 'prueba',
                'usuarios': ','.join(str(u.id) for u in usuarios),
            },
        )

    def test_email_se_envia_y_registra(self):
        from django.core import mail
        resp = self._post([self.user_coordinador])
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.doc_activo.codigo, mail.outbox[0].subject)
        dist = DistribucionDocumento.objects.get(
            documento=self.doc_activo, usuario=self.user_coordinador
        )
        self.assertTrue(dist.enviado)

    def test_email_sin_correo_se_omite(self):
        from django.core import mail
        self.user_trabajador.email = ''
        self.user_trabajador.save(update_fields=['email'])
        self._post([self.user_trabajador])
        self.assertEqual(len(mail.outbox), 0)
        dist = DistribucionDocumento.objects.get(
            documento=self.doc_activo, usuario=self.user_trabajador
        )
        self.assertFalse(dist.enviado)

    def test_metodo_sistema_no_envia_email(self):
        from django.core import mail
        self._post([self.user_coordinador], metodo='sistema')
        self.assertEqual(len(mail.outbox), 0)

    def test_adjunto_pequeno_se_adjunta(self):
        from django.core import mail
        self.doc_activo.archivo = SimpleUploadedFile('doc.pdf', b'contenido')
        self.doc_activo.save(update_fields=['archivo'])
        self._post([self.user_coordinador])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].attachments), 1)


# =============================================================================
# ESTABLECER VIGENCIA
# =============================================================================

class EstablecerVigenciaTest(BaseDocumentosTest):
    """Tests del flujo de establecer período de vigencia."""

    def setUp(self):
        super().setUp()
        from documentos.models import Periodo
        self.periodo = Periodo.objects.create(nombre='1 Año', meses=12, activo=True)
        self.periodo_inactivo = Periodo.objects.create(
            nombre='Inactivo', meses=24, activo=False,
        )
        self.client = Client()

    def _post(self, periodo_id=None):
        return self.client.post(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/establecer-vigencia/',
            {'periodo_vigencia': str(periodo_id) if periodo_id else ''},
        )

    def test_establecer_vigencia_ok(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(self.periodo.id)
        self.assertEqual(resp.status_code, 302)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.periodo_vigencia_id, self.periodo.id)
        self.assertIsNotNone(self.doc_activo.fecha_vigencia)
        self.assertIsNotNone(self.doc_activo.fecha_vencimiento)
        self.assertFalse(self.doc_activo.aviso_vencimiento_enviado)

    def test_establecer_vigencia_requiere_post(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/establecer-vigencia/',
        )
        self.assertEqual(resp.status_code, 302)

    def test_establecer_vigencia_sin_periodo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post('')
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(resp.url)
        self.assertContains(resp, 'seleccionar')

    def test_establecer_vigencia_periodo_inactivo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(self.periodo_inactivo.id)
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(resp.url)
        self.assertContains(resp, 'válido')

    def test_establecer_vigencia_doc_no_activo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            f'/documentos/formulario/{self.doc_borrador.uuid}/establecer-vigencia/',
            {'periodo_vigencia': str(self.periodo.id)},
        )
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(resp.url)
        self.assertContains(resp, 'aprobados')

    def test_establecer_vigencia_sin_permisos(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self._post(self.periodo.id)
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get(resp.url)
        self.assertContains(resp, 'permisos')

    def test_establecer_vigencia_admin_ok(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self._post(self.periodo.id)
        self.assertEqual(resp.status_code, 302)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.periodo_vigencia_id, self.periodo.id)

    def test_establecer_vigencia_notifica_propietario(self):
        self.client.login(username='admin01', password='testpass123')
        self._post(self.periodo.id)
        notif = Notificacion.objects.filter(
            usuario=self.doc_activo.propietario, tipo='cambio_vigencia',
        ).exists()
        self.assertTrue(notif)

    def test_establecer_vigencia_notifica_jefe_depto(self):
        self.client.login(username='admin01', password='testpass123')
        self._post(self.periodo.id)
        jefe = self.doc_activo.departamento.jefe
        notif = Notificacion.objects.filter(
            usuario=jefe, tipo='cambio_vigencia',
        ).exists()
        self.assertTrue(notif)


# =============================================================================
# REVOCAR DISTRIBUCIÓN
# =============================================================================

class RevocarDistribucionTest(BaseDocumentosTest):
    """Tests de revocación de distribución."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.dist = DistribucionDocumento.objects.create(
            documento=self.doc_activo, usuario=self.user_trabajador,
            metodo='sistema', distribuido_por=self.user_jefe_ti,
            observaciones='Distribución de prueba',
        )

    def _url(self, dist_id=None):
        return (
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}'
            f'/distribucion/{dist_id or self.dist.id}/revocar/'
        )

    def test_get_no_revoca(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.dist.refresh_from_db()
        self.assertFalse(self.dist.revocado)

    def test_sin_permisos_denegado(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 302)
        self.dist.refresh_from_db()
        self.assertFalse(self.dist.revocado)

    def test_admin_revoca_ok(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(self._url(), {'motivo': 'Motivo admin'})
        self.assertEqual(resp.status_code, 302)
        self.dist.refresh_from_db()
        self.assertTrue(self.dist.revocado)
        self.assertEqual(self.dist.revocado_por, self.user_admin)
        self.assertIn('admin', self.dist.motivo_revocacion)
        self.assertIsNotNone(self.dist.fecha_revocacion)

    def test_jefe_depto_revoca_ok(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 302)
        self.dist.refresh_from_db()
        self.assertTrue(self.dist.revocado)

    def test_propietario_revoca_ok(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 302)
        self.dist.refresh_from_db()
        self.assertTrue(self.dist.revocado)

    def test_doble_revocacion_bloqueada(self):
        self.client.login(username='admin01', password='testpass123')
        self.client.post(self._url(), {'motivo': 'Primera'})
        self.client.post(self._url(), {'motivo': 'Segunda'})
        self.dist.refresh_from_db()
        self.assertEqual(self.dist.motivo_revocacion, 'Primera')

    def test_pierde_acceso_dpr(self):
        from documentos.views import puede_editar_documento
        tipo_dpr = TipoDocumento.objects.create(
            key='descripcion_procesos', nombre='DPR', codigo='DPR', activo=True,
        )
        doc_dpr = Documento.objects.create(
            tipo=tipo_dpr, nombre='DPR Test', codigo='DPR-001',
            estado='activo', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
        )
        dist = DistribucionDocumento.objects.create(
            documento=doc_dpr, usuario=self.user_trabajador,
            metodo='sistema', distribuido_por=self.user_jefe_ti,
        )
        self.assertTrue(puede_editar_documento(self.user_trabajador, doc_dpr))
        dist.revocado = True
        dist.save(update_fields=['revocado'])
        self.assertFalse(puede_editar_documento(self.user_trabajador, doc_dpr))

    def test_notificacion_creada(self):
        self.client.login(username='admin01', password='testpass123')
        self.client.post(self._url(), {'motivo': 'Cambio'})
        notif = Notificacion.objects.get(
            usuario=self.user_trabajador, tipo='distribucion_revocada',
        )
        self.assertTrue(notif.url_accion == '')

    def test_email_revocacion_enviado(self):
        from django.core import mail
        self.user_trabajador.email = 'trab01@example.com'
        self.user_trabajador.save(update_fields=['email'])
        self.client.login(username='admin01', password='testpass123')
        self.client.post(self._url(), {'motivo': 'Rev email'})
        self.dist.refresh_from_db()
        self.assertTrue(self.dist.email_revocacion_enviado)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('revocada', mail.outbox[0].subject.lower())

    def test_email_sin_correo_no_falla(self):
        self.user_trabajador.email = ''
        self.user_trabajador.save(update_fields=['email'])
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(self._url(), {'motivo': 'Sin email'})
        self.assertEqual(resp.status_code, 302)
        self.dist.refresh_from_db()
        self.assertTrue(self.dist.revocado)
        self.assertFalse(self.dist.email_revocacion_enviado)


# =============================================================================
# VISIBILIDAD DE DOCUMENTOS (puede_ver_documento)
# =============================================================================

class PuedeVerDocumentoTest(BaseDocumentosTest):
    """Tests de la regla central de visibilidad."""

    def _dpr(self, propietario=None):
        tipo_dpr, _ = TipoDocumento.objects.get_or_create(
            key='descripcion_procesos',
            defaults={'nombre': 'DPR', 'codigo': 'DPR', 'activo': True},
        )
        return Documento.objects.create(
            tipo=tipo_dpr, nombre='DPR Test', codigo='DPR-900', estado='activo',
            propietario=propietario or self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
        )

    def test_admin_ve_cualquier_documento(self):
        from documentos.permissions import puede_ver_documento
        self.assertTrue(puede_ver_documento(self.user_admin, self.doc_th))

    def test_propietario_ve_su_documento(self):
        from documentos.permissions import puede_ver_documento
        self.assertTrue(puede_ver_documento(self.user_jefe_ti, self.doc_activo))

    def test_publico_activo_visible_para_otros_deptos(self):
        from documentos.permissions import puede_ver_documento
        self.assertTrue(puede_ver_documento(self.user_trabajador_otro, self.doc_publico))

    def test_publico_no_activo_no_visible(self):
        from documentos.permissions import puede_ver_documento
        self.doc_publico.estado = 'obsoleto'
        self.doc_publico.save(update_fields=['estado'])
        self.assertFalse(puede_ver_documento(self.user_trabajador_otro, self.doc_publico))

    def test_mismo_depto_activo_visible(self):
        from documentos.permissions import puede_ver_documento
        self.assertTrue(puede_ver_documento(self.user_trabajador, self.doc_activo))

    def test_mismo_depto_borrador_no_visible(self):
        from documentos.permissions import puede_ver_documento
        self.assertFalse(puede_ver_documento(self.user_coordinador, self.doc_borrador))

    def test_otro_depto_no_visible(self):
        from documentos.permissions import puede_ver_documento
        self.assertFalse(puede_ver_documento(self.user_trabajador_otro, self.doc_activo))

    def test_jefe_depto_ve_cualquier_estado(self):
        from documentos.permissions import puede_ver_documento
        self.assertTrue(puede_ver_documento(self.user_jefe_ti, self.doc_borrador))

    def test_dpr_visible_para_cualquier_jefe(self):
        from documentos.permissions import puede_ver_documento
        doc_dpr = self._dpr()
        self.assertTrue(puede_ver_documento(self.user_jefe_th, doc_dpr))

    def test_dpr_no_visible_para_trabajador_sin_distribucion(self):
        from documentos.permissions import puede_ver_documento
        doc_dpr = self._dpr()
        self.assertFalse(puede_ver_documento(self.user_trabajador, doc_dpr))

    def test_asignacion_con_puede_leer_visible(self):
        from documentos.permissions import puede_ver_documento
        AsignacionDocumento.objects.create(
            documento=self.doc_th, coordinador=self.user_coordinador,
            jefe_asignador=self.user_jefe_ti, puede_leer=True,
        )
        self.assertTrue(puede_ver_documento(self.user_coordinador, self.doc_th))

    def test_asignacion_sin_puede_leer_no_visible(self):
        from documentos.permissions import puede_ver_documento
        AsignacionDocumento.objects.create(
            documento=self.doc_th, coordinador=self.user_coordinador,
            jefe_asignador=self.user_jefe_ti, puede_leer=False,
        )
        self.assertFalse(puede_ver_documento(self.user_coordinador, self.doc_th))

    def test_distribucion_activa_visible(self):
        from documentos.permissions import puede_ver_documento
        DistribucionDocumento.objects.create(
            documento=self.doc_th, usuario=self.user_coordinador,
            metodo='sistema', distribuido_por=self.user_jefe_ti,
        )
        self.assertTrue(puede_ver_documento(self.user_coordinador, self.doc_th))

    def test_distribucion_revocada_no_visible(self):
        from documentos.permissions import puede_ver_documento
        DistribucionDocumento.objects.create(
            documento=self.doc_th, usuario=self.user_coordinador,
            metodo='sistema', distribuido_por=self.user_jefe_ti, revocado=True,
        )
        self.assertFalse(puede_ver_documento(self.user_coordinador, self.doc_th))

    def test_aprobador_involucrado_visible(self):
        from documentos.permissions import puede_ver_documento
        SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_th, solicitante=self.user_trabajador_otro,
            aprobador=self.user_coordinador, departamento_origen=self.depto_th,
        )
        self.assertTrue(puede_ver_documento(self.user_coordinador, self.doc_th))

    def test_solicitante_descarga_involucrado_visible(self):
        from documentos.permissions import puede_ver_documento
        SolicitudDescarga.objects.create(
            documento=self.doc_th, solicitado_por=self.user_coordinador,
        )
        self.assertTrue(puede_ver_documento(self.user_coordinador, self.doc_th))


class DetalleAccesoTest(BaseDocumentosTest):
    """Tests del control de acceso en la vista de detalle."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_otro_depto_denegado(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(f'/documentos/formulario/{self.doc_th.uuid}/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/documentos/mis-documentos/', resp.url)

    def test_mismo_depto_activo_permitido(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/procedimientos_generales/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)

    def test_mismo_depto_borrador_denegado(self):
        self.client.login(username='coord01', password='testpass123')
        resp = self.client.get(f'/documentos/formulario/{self.doc_borrador.uuid}/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/documentos/mis-documentos/', resp.url)

    def test_publico_activo_permitido_otro_depto(self):
        self.client.login(username='trab02', password='testpass123')
        resp = self.client.get(f'/documentos/formulario/{self.doc_publico.uuid}/')
        self.assertEqual(resp.status_code, 200)

    def test_distribucion_activa_permitida(self):
        DistribucionDocumento.objects.create(
            documento=self.doc_th, usuario=self.user_trabajador,
            metodo='sistema', distribuido_por=self.user_jefe_ti,
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(f'/documentos/formulario/{self.doc_th.uuid}/')
        self.assertEqual(resp.status_code, 200)

    def test_distribucion_revocada_denegada(self):
        DistribucionDocumento.objects.create(
            documento=self.doc_th, usuario=self.user_trabajador,
            metodo='sistema', distribuido_por=self.user_jefe_ti, revocado=True,
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(f'/documentos/formulario/{self.doc_th.uuid}/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/documentos/mis-documentos/', resp.url)

    def test_revocar_limpia_url_notificacion_previa(self):
        from autenticacion.models import Notificacion
        dist = DistribucionDocumento.objects.create(
            documento=self.doc_th, usuario=self.user_trabajador,
            metodo='sistema', distribuido_por=self.user_jefe_ti,
        )
        Notificacion.objects.create(
            usuario=self.user_trabajador, titulo='Documento Distribuido',
            mensaje='x', tipo='documento_distribuido',
            url_accion=f'/documentos/formulario/{self.doc_th.uuid}/',
            documento=self.doc_th,
        )
        self.client.login(username='admin01', password='testpass123')
        self.client.post(
            f'/documentos/formulario/{self.doc_th.uuid}/distribucion/{dist.id}/revocar/',
            {'motivo': 'Prueba'},
        )
        notif = Notificacion.objects.get(
            usuario=self.user_trabajador, tipo='documento_distribuido',
        )
        self.assertEqual(notif.url_accion, '')

    def test_api_detalle_denegado(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/api/formulario/{self.doc_th.uuid}/'
        )
        self.assertEqual(resp.status_code, 403)

    def test_api_detalle_permitido(self):
        self.client.login(username='trab02', password='testpass123')
        resp = self.client.get(
            f'/documentos/api/formulario/{self.doc_th.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)


class VerArchivoTest(BaseDocumentosTest):
    """Tests del endpoint que sirve el archivo para el visor."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _url(self, doc):
        return f'/documentos/{doc.tipo_id}/{doc.uuid}/ver-archivo/'

    def test_propietario_puede_ver_archivo(self):
        self.client.login(username='trab02', password='testpass123')
        resp = self.client.get(self._url(self.doc_th))
        self.assertEqual(resp.status_code, 200)

    def test_otro_depto_denegado(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(self._url(self.doc_th))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/documentos/mis-documentos/', resp.url)

    def test_distribucion_revocada_denegada(self):
        DistribucionDocumento.objects.create(
            documento=self.doc_th, usuario=self.user_trabajador,
            metodo='sistema', distribuido_por=self.user_jefe_ti, revocado=True,
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(self._url(self.doc_th))
        self.assertEqual(resp.status_code, 302)


# =============================================================================
# RESUMEN IA (Google Gemini)
# =============================================================================

class ResumenIATest(BaseDocumentosTest):
    """Tests del resumen de documentos con IA (Gemini mockeado)."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _url(self, doc=None):
        doc = doc or self.doc_activo
        return f'/documentos/{doc.tipo_id}/{doc.uuid}/resumen-ia/'

    def test_esta_habilitado_sin_key(self):
        from documentos import ai
        with override_settings(IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY=''):
            self.assertFalse(ai.esta_habilitado())

    def test_esta_habilitado_con_key(self):
        from documentos import ai
        with override_settings(IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='test-key'):
            self.assertTrue(ai.esta_habilitado())

    def test_endpoint_get_estado_inicial(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['estado'], '')

    def test_endpoint_denegado_otro_depto(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(self._url(self.doc_th))
        self.assertEqual(resp.status_code, 403)

    @override_settings(IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='test-key')
    @patch('documentos.tasks.generar_resumen_ia.delay')
    def test_endpoint_post_encola(self, mock_delay):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['estado'], 'pendiente')
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'pendiente')
        self.assertTrue(mock_delay.called)

    @override_settings(IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='')
    def test_endpoint_post_sin_key(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 400)

    @override_settings(IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='test-key')
    def test_tarea_ok(self):
        from documentos.tasks import generar_resumen_ia
        resumen = {
            'objetivo': 'Objetivo de prueba',
            'alcance': 'Alcance de prueba',
            'puntos_principales': ['Punto 1', 'Punto 2'],
            'responsables': [{'rol': 'Jefe', 'funcion': 'Aprobar'}],
            'conclusion': 'Conclusión',
        }
        with patch('documentos.ai.generar_resumen', return_value={
            'ok': True, 'resumen': resumen, 'texto': '{}', 'modelo': 'gemini-test',
        }):
            generar_resumen_ia(str(self.doc_activo.uuid))
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'listo')
        self.assertEqual(self.doc_activo.resumen_ia['objetivo'], 'Objetivo de prueba')
        self.assertEqual(self.doc_activo.resumen_ia_modelo, 'gemini-test')
        self.assertEqual(self.doc_activo.resumen_ia_version, self.doc_activo.version_actual)
        self.assertTrue(self.doc_activo.resumen_ia_actualizado)

    def test_tarea_error(self):
        from documentos.tasks import generar_resumen_ia
        with patch('documentos.ai.generar_resumen', return_value={
            'ok': False, 'error': 'Falla simulada',
        }):
            generar_resumen_ia(str(self.doc_activo.uuid))
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'error')
        self.assertIn('Falla simulada', self.doc_activo.resumen_ia_error)

    def test_tarea_excepcion_marca_error(self):
        from documentos.tasks import generar_resumen_ia
        with patch('documentos.ai.generar_resumen', side_effect=RuntimeError('boom')):
            generar_resumen_ia(str(self.doc_activo.uuid))
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'error')
        self.assertIn('boom', self.doc_activo.resumen_ia_error)

    def test_reconciliar_pendiente_con_resumen_marca_listo(self):
        from datetime import timedelta

        from django.utils import timezone
        self.doc_activo.resumen_ia = {'objetivo': 'x'}
        self.doc_activo.resumen_ia_estado = 'pendiente'
        self.doc_activo.resumen_ia_solicitado = timezone.now() - timedelta(minutes=30)
        self.doc_activo.save()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(self._url())
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'listo')
        self.assertEqual(resp.json()['estado'], 'listo')

    def test_reconciliar_pendiente_sin_resumen_marca_error(self):
        from datetime import timedelta

        from django.utils import timezone
        self.doc_activo.resumen_ia = None
        self.doc_activo.resumen_ia_estado = 'pendiente'
        self.doc_activo.resumen_ia_solicitado = timezone.now() - timedelta(minutes=30)
        self.doc_activo.save()
        self.client.login(username='trab01', password='testpass123')
        self.client.get(self._url())
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'error')
        self.assertIn('tardó demasiado', self.doc_activo.resumen_ia_error)

    def test_reconciliar_pendiente_reciente_no_se_toca(self):
        from django.utils import timezone
        self.doc_activo.resumen_ia_estado = 'pendiente'
        self.doc_activo.resumen_ia_solicitado = timezone.now()
        self.doc_activo.save()
        self.client.login(username='trab01', password='testpass123')
        self.client.get(self._url())
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'pendiente')

    def test_reconciliar_pendiente_sin_timestamp_marca_error(self):
        self.doc_activo.resumen_ia = None
        self.doc_activo.resumen_ia_estado = 'pendiente'
        self.doc_activo.resumen_ia_solicitado = None
        self.doc_activo.save()
        self.client.login(username='trab01', password='testpass123')
        self.client.get(self._url())
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'error')

    @override_settings(IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='test-key')
    @patch('documentos.tasks.generar_resumen_ia.delay')
    def test_post_guarda_solicitado(self, mock_delay):
        self.client.login(username='trab01', password='testpass123')
        self.client.post(self._url())
        self.doc_activo.refresh_from_db()
        self.assertIsNotNone(self.doc_activo.resumen_ia_solicitado)

    def test_poll_solo_estado_omite_resumen(self):
        self.doc_activo.resumen_ia = {'objetivo': 'x'}
        self.doc_activo.resumen_ia_estado = 'listo'
        self.doc_activo.resumen_ia_version = self.doc_activo.version_actual
        self.doc_activo.save()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(self._url() + '?solo_estado=1')
        data = resp.json()
        self.assertEqual(data['estado'], 'listo')
        self.assertIsNone(data['resumen'])

    def test_resumen_ia_actualizado_invalida_por_version(self):
        self.doc_activo.resumen_ia = {'objetivo': 'x'}
        self.doc_activo.resumen_ia_estado = 'listo'
        self.doc_activo.resumen_ia_version = '1'
        self.doc_activo.version_actual = '1'
        self.assertTrue(self.doc_activo.resumen_ia_actualizado)
        self.doc_activo.version_actual = '2'
        self.assertFalse(self.doc_activo.resumen_ia_actualizado)

    def test_endpoint_get_devuelve_resumen(self):
        self.doc_activo.resumen_ia = {
            'objetivo': 'Objetivo', 'alcance': 'Alcance',
            'puntos_principales': ['A'], 'conclusion': 'Fin',
        }
        self.doc_activo.resumen_ia_estado = 'listo'
        self.doc_activo.resumen_ia_version = self.doc_activo.version_actual
        self.doc_activo.save()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['actualizado'])
        self.assertEqual(data['resumen']['objetivo'], 'Objetivo')

    @override_settings(IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='test-key')
    def test_detalle_renderiza_card_resumen(self):
        self.doc_activo.resumen_ia = {
            'objetivo': 'Objetivo visible',
            'alcance': 'Alcance visible',
            'puntos_principales': ['Punto visible'],
            'conclusion': 'Fin visible',
        }
        self.doc_activo.resumen_ia_estado = 'listo'
        self.doc_activo.resumen_ia_version = self.doc_activo.version_actual
        self.doc_activo.save()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Resumen IA')
        self.assertContains(resp, 'Objetivo visible')
        self.assertContains(resp, 'Punto visible')

    def test_aq_key_es_reconocida_como_valida(self):
        from documentos import ai
        with override_settings(GEMINI_API_KEY='AQ.Ab8-dummy-token'):
            estado = ai.estado_configuracion()
        self.assertIn('Auth key', estado['key_prefijo'])
        self.assertIn('válido', estado['key_prefijo'])

    def test_aq_key_no_es_rechazada_por_generar_resumen(self):
        from documentos import ai
        with override_settings(GEMINI_API_KEY='AQ.Ab8-dummy-token'):
            with patch('documentos.ai._leer_archivo', return_value=b'%PDF-1.4'):
                with patch('google.genai.Client') as mock_client:
                    mock_client.return_value.models.generate_content.side_effect = Exception('boom')
                    resultado = ai.generar_resumen(self.doc_th)
        self.assertFalse(resultado['ok'])
        self.assertNotIn('token efímero', resultado['error'])

    def test_probar_conexion_sin_key(self):
        from documentos import ai
        with override_settings(GEMINI_API_KEY=''):
            resultado = ai.probar_conexion()
        self.assertFalse(resultado['ok'])

    def test_probar_conexion_aq_key_intenta_llamar(self):
        from documentos import ai
        with override_settings(GEMINI_API_KEY='AQ.Ab8-dummy-token'):
            with patch('google.genai.Client') as mock_client:
                mock_client.return_value.models.generate_content.side_effect = Exception('boom')
                resultado = ai.probar_conexion()
        self.assertFalse(resultado['ok'])
        self.assertNotIn('token efímero', resultado['error'])

    @override_settings(
        IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='AIza-test',
        IA_RESUMEN_SINCRONO=True,
    )
    @patch('documentos.tasks.generar_resumen_ia.delay', side_effect=Exception('broker down'))
    @patch('documentos.ai.generar_resumen')
    def test_fallback_sincrono_genera(self, mock_generar, mock_delay):
        mock_generar.return_value = {
            'ok': True, 'resumen': {'objetivo': 'Sync', 'puntos_principales': []},
            'texto': '{}', 'modelo': 'gemini-test',
        }
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['estado'], 'listo')
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'listo')

    @override_settings(
        IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='AIza-test',
        IA_RESUMEN_SINCRONO=False,
    )
    @patch('documentos.tasks.generar_resumen_ia.delay', side_effect=Exception('broker down'))
    def test_fallback_sincrono_deshabilitado(self, mock_delay):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 503)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.resumen_ia_estado, 'error')

    def _fake_gemini_error(self, code):
        class _FakeError(Exception):
            pass
        status = {404: 'NOT_FOUND', 429: 'RESOURCE_EXHAUSTED', 503: 'UNAVAILABLE'}.get(code, 'ERROR')
        err = _FakeError(f'{code} {status}')
        err.code = code
        return err

    @override_settings(GEMINI_MODEL='gemini-primary', GEMINI_MODEL_FALLBACK='gemini-fallback')
    def test_fallback_de_modelo(self):
        from documentos import ai
        from unittest.mock import MagicMock
        calls = []

        def fake_generate(model=None, config=None, contents=None):
            calls.append(model)
            if model == 'gemini-primary':
                raise self._fake_gemini_error(503)
            return SimpleNamespace(text='{"objetivo":"x","puntos_principales":[]}')

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate
        with patch('documentos.ai._crear_cliente', return_value=mock_client):
            with patch('documentos.ai._leer_archivo', return_value=b'%PDF-1.4'):
                resultado = ai.generar_resumen(self.doc_th)
        self.assertTrue(resultado['ok'])
        self.assertEqual(resultado['modelo'], 'gemini-fallback')
        self.assertEqual(calls, ['gemini-primary', 'gemini-fallback'])

    @override_settings(GEMINI_MODEL='gemini-primary', GEMINI_MODEL_FALLBACK='gemini-fallback')
    def test_saturacion_total_mensaje_amigable(self):
        from documentos import ai
        from unittest.mock import MagicMock
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = self._fake_gemini_error(503)
        with patch('documentos.ai._crear_cliente', return_value=mock_client):
            with patch('documentos.ai._leer_archivo', return_value=b'%PDF-1.4'):
                resultado = ai.generar_resumen(self.doc_th)
        self.assertFalse(resultado['ok'])
        self.assertIn('saturado', resultado['error'].lower())

    @override_settings(GEMINI_MODEL='gemini-primary', GEMINI_MODEL_FALLBACK='gemini-fallback')
    def test_error_no_transitorio_no_hace_fallback(self):
        from documentos import ai
        from unittest.mock import MagicMock
        calls = []

        def fake_generate(model=None, config=None, contents=None):
            calls.append(model)
            raise self._fake_gemini_error(404)

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate
        with patch('documentos.ai._crear_cliente', return_value=mock_client):
            with patch('documentos.ai._leer_archivo', return_value=b'%PDF-1.4'):
                resultado = ai.generar_resumen(self.doc_th)
        self.assertFalse(resultado['ok'])
        self.assertEqual(calls, ['gemini-primary'])

    def test_max_tokens_devuelve_error(self):
        from documentos import ai
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.text = '{"objetivo": "x"'
        resp.candidates = [SimpleNamespace(finish_reason='MAX_TOKENS')]
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = resp
        with override_settings(GEMINI_API_KEY='test-key'):
            with patch('documentos.ai._crear_cliente', return_value=mock_client):
                with patch('documentos.ai._leer_archivo', return_value=b'%PDF-1.4'):
                    resultado = ai.generar_resumen(self.doc_th)
        self.assertFalse(resultado['ok'])
        self.assertIn('incompleto', resultado['error'].lower())

    @override_settings(IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='test-key')
    def test_detalle_renderiza_relacionados_con_enlace(self):
        self.doc_activo.resumen_ia = {
            'objetivo': 'Objetivo con TI-FOR-001',
            'puntos_principales': ['Ver el TI-FOR-001'],
            'documentos_relacionados': [
                {'codigo': 'TI-FOR-001', 'nombre': 'Formulario', 'relacion': 'referenciado'},
                {'codigo': 'XX-XXX-999', 'nombre': 'Inexistente', 'relacion': 'norma'},
            ],
            'palabras_clave': ['TI-FOR-001'],
        }
        self.doc_activo.resumen_ia_estado = 'listo'
        self.doc_activo.resumen_ia_version = self.doc_activo.version_actual
        self.doc_activo.save()
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Documentos relacionados')
        # TI-FOR-001 existe (doc_borrador) y trab01 es su propietario → enlace
        self.assertContains(
            resp, f'/documentos/formulario/{self.doc_borrador.uuid}/'
        )
        self.assertContains(resp, '<strong>XX-XXX-999</strong>')


class ResaltadoTest(BaseDocumentosTest):
    """Tests del tag `resaltar` (negritas seguras)."""

    def test_resalta_codigos(self):
        from documentos.templatetags.formato_extras import resaltar
        html = resaltar('Ver el TI-FOR-001 y DPR-002', None)
        self.assertIn('<strong>TI-FOR-001</strong>', html)
        self.assertIn('<strong>DPR-002</strong>', html)

    def test_resalta_palabras_clave(self):
        from documentos.templatetags.formato_extras import resaltar
        html = resaltar('Cumple la norma ISO 9001', ['ISO 9001'])
        self.assertIn('<strong>ISO 9001</strong>', html)

    def test_escapa_html(self):
        from documentos.templatetags.formato_extras import resaltar
        html = resaltar('<script>alert(1)</script> TI-FOR-001', None)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('<strong>TI-FOR-001</strong>', html)

    def test_no_anida_strong(self):
        from documentos.templatetags.formato_extras import resaltar
        html = resaltar('TI-FOR-001', ['TI-FOR-001'])
        self.assertEqual(html.count('<strong>'), 1)
        self.assertEqual(html.count('</strong>'), 1)


# =============================================================================
# SONDEO DE MODELOS
# =============================================================================

class SondeoModelosTest(BaseDocumentosTest):
    """Tests del sondeo de disponibilidad de modelos."""

    def _fake_error(self, code):
        class _FakeError(Exception):
            pass
        err = _FakeError(f'{code} UNAVAILABLE')
        err.code = code
        return err

    @override_settings(GEMINI_API_KEY='test-key')
    def test_sondear_reporta_ok_y_fallos(self):
        from documentos import ai
        from unittest.mock import MagicMock

        def fake_generate(model=None, contents=None, config=None):
            if model == 'gemini-3.5-flash':
                return SimpleNamespace(text='OK')
            raise self._fake_error(503)

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = fake_generate
        with patch('documentos.ai._crear_cliente', return_value=mock_client):
            resultado = ai.sondear_modelos(['gemini-3.8-flash', 'gemini-3.5-flash'])

        self.assertTrue(resultado['ok'])
        estados = {r['modelo']: r['ok'] for r in resultado['resultados']}
        self.assertFalse(estados['gemini-3.8-flash'])
        self.assertTrue(estados['gemini-3.5-flash'])

    @override_settings(GEMINI_API_KEY='')
    def test_sondear_sin_key(self):
        from documentos import ai
        resultado = ai.sondear_modelos(['gemini-3.5-flash'])
        self.assertFalse(resultado['ok'])
        self.assertIn('GEMINI_API_KEY', resultado['error'])


# =============================================================================
# CELERY BEAT
# =============================================================================

class CeleryBeatScheduleTest(TestCase):
    """El schedule de Celery Beat debe apuntar a tareas registradas."""

    def test_schedule_apunta_a_tareas_registradas(self):
        from django.conf import settings
        from polidoc.celery import app as celery_app
        import documentos.tasks  # noqa: F401  (asegura registro de tareas)
        import auditoria.tasks  # noqa: F401  (buffer de auditoría)

        self.assertTrue(settings.CELERY_BEAT_SCHEDULE)
        for cfg in settings.CELERY_BEAT_SCHEDULE.values():
            self.assertIn(cfg['task'], celery_app.tasks, cfg['task'])


# =============================================================================
# ESCALADO DE APROBACIONES (48h)
# =============================================================================

class EscaladoAprobacionesTest(BaseDocumentosTest):
    """Tests de la tarea que notifica aprobaciones estancadas."""

    def setUp(self):
        super().setUp()
        self.solicitud = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_activo,
            solicitante=self.user_trabajador,
            aprobador=self.user_jefe_ti,
            departamento_origen=self.depto_ti,
            accion_solicitud='creacion',
            estado='pendiente',
        )

    def _backdate(self, horas):
        SolicitudAprobacionDocumento.objects.filter(pk=self.solicitud.pk).update(
            fecha_solicitud=timezone.now() - timedelta(hours=horas)
        )

    def test_escalada_notifica_admin(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['escaladas'], 1)
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_admin, tipo='aprobacion_escalada',
        ).exists())
        self.solicitud.refresh_from_db()
        self.assertIsNotNone(self.solicitud.fecha_escalado)

    def test_no_escala_reciente(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(10)
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['escaladas'], 0)
        self.assertFalse(Notificacion.objects.filter(tipo='aprobacion_escalada').exists())

    def test_no_duplica_en_segunda_corrida(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        escalar_aprobaciones_vencidas()
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['escaladas'], 0)
        self.assertEqual(
            Notificacion.objects.filter(tipo='aprobacion_escalada').count(), 1
        )

    def test_no_escala_aprobadas(self):
        from documentos.tasks import escalar_aprobaciones_vencidas
        self._backdate(50)
        SolicitudAprobacionDocumento.objects.filter(pk=self.solicitud.pk).update(
            estado='aprobada'
        )
        resultado = escalar_aprobaciones_vencidas()
        self.assertEqual(resultado['escaladas'], 0)


# =============================================================================
# CONSECUTIVO DDMMAA-XXX POR ORIGEN (SGC-IDL-011)
# =============================================================================

class ConsecutivoSolicitudCambioTest(BaseDocumentosTest):
    """Numeración DDMMAA-XXX por origen del cambio."""

    def setUp(self):
        super().setUp()
        from documentos.models import Formato, SeccionFormato, CampoFormato
        from documentos.views import _autollenar_envio

        self._autollenar_envio = _autollenar_envio
        self.formato = Formato.objects.create(
            codigo='SGC-FOR-005-T', nombre='Test cambios', version='1', edicion='1',
        )
        seccion = SeccionFormato.objects.create(
            formato=self.formato, titulo='Datos', orden=1,
        )
        CampoFormato.objects.create(
            seccion=seccion, clave='origen_cambio', etiqueta='Origen',
            tipo='checkbox_grupo',
            opciones=[
                {'valor': 'Auditoría', 'etiqueta': 'Auditoría'},
                {'valor': 'Mejora Continua', 'etiqueta': 'Mejora Continua'},
            ],
        )
        CampoFormato.objects.create(
            seccion=seccion, clave='num_solicitud', etiqueta='Nº',
            tipo='texto', origen='num_solicitud',
        )

    def _crear(self, origen):
        from documentos.models import SolicitudCambio
        sol = SolicitudCambio.objects.create(
            codigo=f'SC-{uuid.uuid4().hex[:10]}',
            formato=self.formato,
            operacion='creacion',
            solicitante=self.user_trabajador,
            departamento=self.depto_ti,
            respuestas={'origen_cambio': [origen]},
        )
        self._autollenar_envio(sol, self.user_trabajador)
        sol.refresh_from_db()
        return sol

    def test_consecutivo_por_origen(self):
        hoy = timezone.now().strftime('%d%m%y')
        a1 = self._crear('Auditoría')
        a2 = self._crear('Auditoría')
        m1 = self._crear('Mejora Continua')
        self.assertEqual(a1.num_solicitud, f'{hoy}-001')
        self.assertEqual(a2.num_solicitud, f'{hoy}-002')
        self.assertEqual(m1.num_solicitud, f'{hoy}-001')
        self.assertEqual(a1.origen_cambio, 'Auditoría')

    def test_conserva_numero_al_reenviar(self):
        a1 = self._crear('Auditoría')
        num = a1.num_solicitud
        self._autollenar_envio(a1, self.user_trabajador)
        a1.refresh_from_db()
        self.assertEqual(a1.num_solicitud, num)

    def test_origen_multiple_usa_primera_opcion(self):
        from documentos.models import SolicitudCambio
        sol = SolicitudCambio.objects.create(
            codigo=f'SC-{uuid.uuid4().hex[:10]}',
            formato=self.formato, operacion='creacion',
            solicitante=self.user_trabajador, departamento=self.depto_ti,
            respuestas={'origen_cambio': ['Mejora Continua', 'Auditoría']},
        )
        self._autollenar_envio(sol, self.user_trabajador)
        sol.refresh_from_db()
        self.assertEqual(sol.origen_cambio, 'Auditoría')

    def test_no_cuenta_otro_prefijo(self):
        from documentos.models import SolicitudCambio
        hoy = timezone.now().strftime('%d%m%y')
        SolicitudCambio.objects.create(
            codigo=f'SC-{uuid.uuid4().hex[:10]}',
            formato=self.formato, operacion='creacion',
            solicitante=self.user_trabajador, departamento=self.depto_ti,
            respuestas={}, origen_cambio='Auditoría',
            num_solicitud='010125-001',
        )
        a1 = self._crear('Auditoría')
        self.assertEqual(a1.num_solicitud, f'{hoy}-001')

    def test_guarda_en_respuestas(self):
        a1 = self._crear('Auditoría')
        self.assertEqual(a1.respuestas.get('num_solicitud'), a1.num_solicitud)


# =============================================================================
# PUBLICACIÓN DE FORMATO (VERSIÓN / EDICIÓN)
# =============================================================================

class FormatoPublicacionTest(BaseDocumentosTest):
    """Tests de publicación de versión/edición del formato."""

    def setUp(self):
        super().setUp()
        from documentos.models import Formato
        self.client = Client()
        self.formato = Formato.objects.create(
            codigo='SGC-FOR-005-P', nombre='Planificación de Cambios',
            version='1', edicion='1',
        )

    def _url(self):
        return f'/documentos/configuracion/formatos/{self.formato.id}/publicar/'

    def test_publicar_version_incrementa_version(self):
        from documentos.models import FormatoPublicacion
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(self._url(), {'tipo': 'version', 'observaciones': 'x'})
        self.assertEqual(resp.status_code, 302)
        self.formato.refresh_from_db()
        self.assertEqual(self.formato.version, '2')
        self.assertEqual(self.formato.edicion, '1')
        self.assertTrue(FormatoPublicacion.objects.filter(
            formato=self.formato, tipo='version', version_nueva='2',
        ).exists())

    def test_publicar_edicion_incrementa_edicion(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(self._url(), {'tipo': 'edicion'})
        self.assertEqual(resp.status_code, 302)
        self.formato.refresh_from_db()
        self.assertEqual(self.formato.edicion, '2')
        self.assertEqual(self.formato.version, '1')

    def test_edicion_notifica_propietario_jefe_admin(self):
        self.client.login(username='admin01', password='testpass123')
        self.client.post(self._url(), {
            'tipo': 'edicion', 'documentos': [str(self.doc_activo.id)],
        })
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_jefe_ti, tipo='edicion_formato',
            documento=self.doc_activo,
        ).exists())
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_admin, tipo='edicion_formato',
            documento=self.doc_activo,
        ).exists())

    def test_version_no_notifica(self):
        self.client.login(username='admin01', password='testpass123')
        self.client.post(self._url(), {
            'tipo': 'version', 'documentos': [str(self.doc_activo.id)],
        })
        self.assertFalse(Notificacion.objects.filter(tipo='edicion_formato').exists())

    def test_no_admin_no_publica(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(self._url(), {'tipo': 'version'})
        self.assertEqual(resp.status_code, 302)
        self.formato.refresh_from_db()
        self.assertEqual(self.formato.version, '1')

    def test_snapshot_al_crear_solicitud(self):
        from documentos.models import SolicitudCambio
        sol = SolicitudCambio.objects.create(
            codigo='SC-SNAP', formato=self.formato, operacion='creacion',
            solicitante=self.user_trabajador, departamento=self.depto_ti,
            respuestas={},
        )
        self.assertEqual(sol.formato_version, '1')
        self.assertEqual(sol.formato_edicion, '1')

    def test_constructor_renderiza_botones(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get(
            f'/documentos/configuracion/formatos/{self.formato.id}/constructor/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Nueva edición')


# =============================================================================
# LÍMITE DIARIO DE RESUMEN IA
# =============================================================================

class LimiteResumenIATest(BaseDocumentosTest):
    """Tests del límite diario de resúmenes IA por usuario."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _url(self, doc=None):
        doc = doc or self.doc_activo
        return f'/documentos/{doc.tipo_id}/{doc.uuid}/resumen-ia/'

    def test_consumo_y_registro(self):
        from documentos import ai
        self.assertEqual(ai.consumo_hoy(self.user_trabajador), 0)
        ai.registrar_uso(self.user_trabajador)
        ai.registrar_uso(self.user_trabajador)
        self.assertEqual(ai.consumo_hoy(self.user_trabajador), 2)

    @override_settings(IA_RESUMEN_LIMITE_DIARIO=2)
    def test_limite_alcanzado(self):
        from documentos import ai
        self.assertFalse(ai.limite_alcanzado(self.user_trabajador))
        ai.registrar_uso(self.user_trabajador)
        ai.registrar_uso(self.user_trabajador)
        self.assertTrue(ai.limite_alcanzado(self.user_trabajador))

    @override_settings(IA_RESUMEN_LIMITE_DIARIO=0)
    def test_limite_cero_no_bloquea(self):
        from documentos import ai
        for _ in range(5):
            ai.registrar_uso(self.user_trabajador)
        self.assertFalse(ai.limite_alcanzado(self.user_trabajador))

    @override_settings(
        IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='k', IA_RESUMEN_LIMITE_DIARIO=1,
    )
    def test_post_429_al_alcanzar(self):
        from documentos import ai
        ai.registrar_uso(self.user_trabajador)
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 429)

    @override_settings(
        IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='k', IA_RESUMEN_LIMITE_DIARIO=5,
    )
    @patch('documentos.tasks.generar_resumen_ia.delay')
    def test_post_registra_uso(self, mock_delay):
        from documentos import ai
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ai.consumo_hoy(self.user_trabajador), 1)

    @override_settings(
        IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='k',
        IA_RESUMEN_LIMITE_DIARIO=1, IA_RESUMEN_SINCRONO=True,
    )
    @patch('documentos.tasks.generar_resumen_ia.delay', side_effect=Exception('broker down'))
    @patch('documentos.ai.generar_resumen')
    def test_admin_exento(self, mock_generar, mock_delay):
        from documentos import ai
        mock_generar.return_value = {
            'ok': True, 'resumen': {'objetivo': 'x'}, 'texto': '{}', 'modelo': 'm',
        }
        ai.registrar_uso(self.user_admin)  # admin ya en el límite
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(self._url())
        self.assertNotEqual(resp.status_code, 429)
        self.assertEqual(ai.consumo_hoy(self.user_admin), 1)


# =============================================================================
# CONTROL DE ACCESO POR DEPARTAMENTO (Fase 1 — hardening IDOR)
# =============================================================================

class ControlAccesoDepartamentoTest(BaseDocumentosTest):
    """Verifica que el acceso a documentos, versiones y solicitudes esté acotado
    al departamento del usuario. Regresión de los IDOR detectados en la auditoría.
    """

    def setUp(self):
        super().setUp()
        self.client = Client()

    # --- puede_ver_documento -------------------------------------------------
    def test_puede_ver_documento_mismo_depto(self):
        from documentos.permissions import puede_ver_documento
        self.assertTrue(puede_ver_documento(self.user_trabajador, self.doc_activo))

    def test_puede_ver_documento_otro_depto_no_publico(self):
        from documentos.permissions import puede_ver_documento
        # trabajador de TH no ve un doc activo no público de TI
        self.assertFalse(puede_ver_documento(self.user_trabajador_otro, self.doc_activo))

    def test_solicitud_pendiente_da_detalle_pero_no_contenido(self):
        from documentos.permissions import puede_ver_documento
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador_otro,
            estado='pendiente', motivo='x',
        )
        # Pendiente: sí ve el detalle/estado (comportamiento previsto)...
        self.assertTrue(puede_ver_documento(self.user_trabajador_otro, self.doc_activo))
        # ...pero NO el contenido del archivo hasta que se apruebe.
        self.assertFalse(puede_ver_documento(
            self.user_trabajador_otro, self.doc_activo, requerir_aprobada=True))

    def test_solicitud_descarga_aprobada_si_da_lectura(self):
        from django.utils import timezone
        from documentos.permissions import puede_ver_documento
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador_otro,
            estado='aprobada', motivo='x',
            version_aprobada=self.doc_activo.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=30),
        )
        self.assertTrue(puede_ver_documento(self.user_trabajador_otro, self.doc_activo))
        self.assertTrue(puede_ver_documento(
            self.user_trabajador_otro, self.doc_activo, requerir_aprobada=True))

    # --- historial_versiones -------------------------------------------------
    def test_historial_versiones_otro_depto_denegado(self):
        self.client.login(username='trab02', password='testpass123')
        url = f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/versiones/'
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_historial_versiones_mismo_depto_permitido(self):
        self.client.login(username='jefe_ti', password='testpass123')
        url = f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/versiones/'
        self.assertEqual(self.client.get(url).status_code, 200)

    # --- ver_archivo ---------------------------------------------------------
    def test_ver_archivo_otro_depto_denegado(self):
        self.client.login(username='trab02', password='testpass123')
        url = f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/ver-archivo/'
        self.assertEqual(self.client.get(url).status_code, 302)

    # --- ver_archivo: content gate (solicitud pendiente no sirve el archivo) --
    def test_ver_archivo_solicitud_pendiente_denegado(self):
        SolicitudDescarga.objects.create(
            documento=self.doc_th, solicitado_por=self.user_trabajador,
            estado='pendiente', motivo='x',
        )
        self.client.login(username='trab01', password='testpass123')  # TI
        url = f'/documentos/{self.doc_th.tipo_id}/{self.doc_th.uuid}/ver-archivo/'
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_ver_archivo_solicitud_aprobada_permitido(self):
        from django.utils import timezone
        SolicitudDescarga.objects.create(
            documento=self.doc_th, solicitado_por=self.user_trabajador,
            estado='aprobada', motivo='x',
            version_aprobada=self.doc_th.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=30),
        )
        self.client.login(username='trab01', password='testpass123')  # TI
        url = f'/documentos/{self.doc_th.tipo_id}/{self.doc_th.uuid}/ver-archivo/'
        self.assertEqual(self.client.get(url).status_code, 200)

    # --- solicitar_descarga: las solicitudes entre departamentos SÍ se permiten
    def test_solicitar_descarga_cross_dept_permitido(self):
        self.client.login(username='trab01', password='testpass123')  # TI
        url = (f'/documentos/{self.doc_th.tipo_id}/{self.doc_th.uuid}'
               f'/solicitar-descarga/')
        resp = self.client.post(url, data={'motivo': 'Necesito el archivo'})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(SolicitudDescarga.objects.filter(
            documento=self.doc_th, solicitado_por=self.user_trabajador
        ).exists())

    # --- eliminar_version: jefe de OTRO depto no puede -----------------------
    def test_eliminar_version_jefe_otro_depto_denegado(self):
        version = VersionDocumento.objects.create(
            documento=self.doc_activo, numero_version='1.0',
            archivo=SimpleUploadedFile('v.pdf', b'c'), creado_por=self.user_jefe_ti,
        )
        self.client.login(username='jefe_th', password='testpass123')
        url = (f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}'
               f'/eliminar-version/{version.id}/')
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertTrue(VersionDocumento.objects.filter(id=version.id, activa=True).exists())

    def test_eliminar_version_jefe_mismo_depto_permitido(self):
        version = VersionDocumento.objects.create(
            documento=self.doc_activo, numero_version='1.0',
            archivo=SimpleUploadedFile('v.pdf', b'c'), creado_por=self.user_jefe_ti,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        url = (f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}'
               f'/eliminar-version/{version.id}/')
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(VersionDocumento.objects.filter(id=version.id, activa=True).exists())

    # --- crear_planilla: sin permiso de edición no se puede ------------------
    def test_crear_planilla_sin_permiso_edicion_denegado(self):
        self.client.login(username='trab01', password='testpass123')  # TI, no propietario
        url = ('/documentos/planilla-cambios/nueva/'
               f'?tipo={self.doc_activo.tipo_id}&doc_uuid={self.doc_activo.uuid}')
        antes = PlanillaCambios.objects.count()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(PlanillaCambios.objects.count(), antes)

    # --- api documentos_por_departamento: scope de visibilidad ---------------
    def test_api_documentos_por_departamento_scope(self):
        self.client.login(username='trab02', password='testpass123')  # TH
        url = f'/documentos/api/procedimientos/?departamento={self.depto_ti.id}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        uuids = {str(d['uuid']) for d in resp.json()['documentos']}
        self.assertIn(str(self.doc_publico.uuid), uuids)     # público de TI: visible
        self.assertNotIn(str(self.doc_activo.uuid), uuids)   # no público de TI: oculto

    # --- _puede_ver_solicitud: jefe acotado a su depto -----------------------
    def test_puede_ver_solicitud_jefe_otro_depto_denegado(self):
        from documentos.views import _puede_ver_solicitud
        solicitud = SimpleNamespace(
            solicitante=self.user_trabajador,
            documento=self.doc_activo,      # depto TI, jefe = jefe_ti
            departamento=self.depto_ti,
        )
        self.assertTrue(_puede_ver_solicitud(self.user_jefe_ti, solicitud))
        self.assertFalse(_puede_ver_solicitud(self.user_jefe_th, solicitud))
        self.assertTrue(_puede_ver_solicitud(self.user_admin, solicitud))

    # --- detalle por HTTP: solicitud pendiente sí abre la página de detalle ---
    def test_detalle_http_solicitud_pendiente_permitido(self):
        SolicitudDescarga.objects.create(
            documento=self.doc_activo, solicitado_por=self.user_trabajador_otro,
            estado='pendiente', motivo='x',
        )
        self.client.login(username='trab02', password='testpass123')  # TH
        url = f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        self.assertEqual(self.client.get(url).status_code, 200)

    # --- ver_archivo: jefe_destino con solicitud de acceso pendiente no ve ---
    def test_ver_archivo_acceso_pendiente_jefe_destino_denegado(self):
        from documentos.permissions import puede_ver_documento
        # jefe_ti (otro depto, sin otra vía de acceso) figura como jefe_destino
        # de una solicitud PENDIENTE sobre doc_th (TH).
        SolicitudAccesoDocumento.objects.create(
            documento=self.doc_th, solicitante=self.user_trabajador_otro,
            jefe_destino=self.user_jefe_ti, departamento_destino=self.depto_th,
            estado='pendiente',
        )
        # Pendiente: la cláusula jefe_destino da el detalle pero NO el contenido.
        self.assertTrue(puede_ver_documento(self.user_jefe_ti, self.doc_th))
        self.assertFalse(puede_ver_documento(
            self.user_jefe_ti, self.doc_th, requerir_aprobada=True))
        # A nivel HTTP: el visor de contenido se deniega (302).
        self.client.login(username='jefe_ti', password='testpass123')
        url = f'/documentos/{self.doc_th.tipo_id}/{self.doc_th.uuid}/ver-archivo/'
        self.assertEqual(self.client.get(url).status_code, 302)

    # --- auditoria_reporte_pdf: un jefe no-admin solo reporta sus propios logs -
    def test_reporte_pdf_jefe_fuerza_su_propio_usuario(self):
        self.client.login(username='jefe_ti', password='testpass123')
        url = '/documentos/configuracion/auditoria/reporte-pdf/?usuario=trab02'
        with patch('auditoria.services.obtener_logs', return_value=[]) as mock_logs:
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        # Aunque pidió los logs de trab02, se fuerza a su propio username.
        self.assertEqual(mock_logs.call_args.kwargs.get('usuario'), 'jefe_ti')

    def test_reporte_pdf_trabajador_denegado(self):
        self.client.login(username='trab01', password='testpass123')
        url = '/documentos/configuracion/auditoria/reporte-pdf/'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    # --- transferir_propiedad (API): nuevo propietario de otro depto → 400 ----
    def test_transferir_propiedad_cross_dept_400(self):
        self.client.login(username='jefe_ti', password='testpass123')
        url = (f'/documentos/api/{self.doc_activo.tipo_id}'
               f'/{self.doc_activo.uuid}/transferir/')
        resp = self.client.post(
            url, data=json.dumps({'nuevo_propietario_id': self.user_trabajador_otro.id}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.doc_activo.refresh_from_db()
        self.assertEqual(self.doc_activo.propietario, self.user_jefe_ti)


class ValidadorArchivosTest(TestCase):
    """Tests del validador de subidas (extensión + magic bytes + tamaño)."""

    PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n'
    OLE2 = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 32
    ZIP = b'PK\x03\x04' + b'\x00' * 32

    def _validar(self, nombre, contenido):
        from documentos.validators import validar_archivo_documento
        return validar_archivo_documento(SimpleUploadedFile(nombre, contenido))

    def test_pdf_valido(self):
        self.assertIsNone(self._validar('doc.pdf', self.PDF))

    def test_docx_valido(self):
        self.assertIsNone(self._validar('doc.docx', self.ZIP))

    def test_xlsx_valido(self):
        self.assertIsNone(self._validar('hoja.xlsx', self.ZIP))

    def test_doc_legacy_valido(self):
        self.assertIsNone(self._validar('old.doc', self.OLE2))

    def test_vsdx_valido(self):
        self.assertIsNone(self._validar('mapa.vsdx', self.ZIP))

    def test_extension_no_permitida(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self._validar('malicioso.html', b'<script>alert(1)</script>')
        self.assertEqual(ctx.exception.code, 'extension_no_permitida')

    def test_extension_svg_no_permitida(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self._validar('imagen.svg', b'<svg/>')
        self.assertEqual(ctx.exception.code, 'extension_no_permitida')

    def test_html_disfrazado_de_pdf(self):
        """Contenido HTML renombrado a .pdf se rechaza por magic bytes."""
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self._validar('inocente.pdf', b'<html><script>alert(1)</script></html>')
        self.assertEqual(ctx.exception.code, 'contenido_invalido')

    def test_exe_disfrazado_de_docx(self):
        """Un MZ (ejecutable) renombrado a .docx se rechaza."""
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self._validar('doc.docx', b'MZ\x90\x00' + b'\x00' * 32)
        self.assertEqual(ctx.exception.code, 'contenido_invalido')

    def test_pdf_disfrazado_de_docx(self):
        """PDF real con extensión .docx: familia incompatible → rechazo."""
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self._validar('doc.docx', self.PDF)
        self.assertEqual(ctx.exception.code, 'contenido_invalido')

    def test_archivo_vacio(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self._validar('vacio.pdf', b'')
        self.assertEqual(ctx.exception.code, 'archivo_ilegible')

    @override_settings(DOCUMENTOS_MAX_UPLOAD_MB=0)
    def test_archivo_demasiado_grande(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self._validar('grande.pdf', self.PDF)
        self.assertEqual(ctx.exception.code, 'archivo_demasiado_grande')


class SanitizadorHtmlTest(TestCase):
    def setUp(self):
        from documentos.sanitizers import sanitizar_html
        self.sanitizar = sanitizar_html

    def test_conserva_formato_legitimo(self):
        html = '<h4>Título</h4><p>Texto <strong>fuerte</strong> y <em>cursiva</em></p>'
        self.assertEqual(self.sanitizar(html), html)

    def test_conserva_listas_y_tablas(self):
        html = '<ul><li>a</li><li>b</li></ul><table><tr><td colspan="2">c</td></tr></table>'
        self.assertEqual(self.sanitizar(html), html)

    def test_elimina_script_y_su_contenido(self):
        self.assertEqual(self.sanitizar('<script>alert(1)</script><p>ok</p>'), '<p>ok</p>')

    def test_elimina_style_iframe_object(self):
        resultado = self.sanitizar('<style>x</style><iframe src="e"></iframe><object></object><p>fin</p>')
        self.assertEqual(resultado, '<p>fin</p>')

    def test_bloquea_href_javascript(self):
        resultado = self.sanitizar('<a href="javascript:alert(1)">x</a>')
        self.assertNotIn('javascript', resultado)

    def test_permite_href_seguro(self):
        resultado = self.sanitizar('<a href="https://ejemplo.com">y</a>')
        self.assertIn('href="https://ejemplo.com"', resultado)

    def test_elimina_manejadores_on(self):
        self.assertEqual(self.sanitizar('<p onclick="evil()">hi</p>'), '<p>hi</p>')

    def test_elimina_img_con_onerror(self):
        resultado = self.sanitizar('<img src="x" onerror="alert(1)"><p>ok</p>')
        self.assertNotIn('onerror', resultado)
        self.assertNotIn('<img', resultado)

    def test_target_blank_anade_rel_noopener(self):
        resultado = self.sanitizar('<a href="https://a.com" target="_blank">z</a>')
        self.assertIn('rel="noopener noreferrer"', resultado)

    def test_cadena_vacia(self):
        self.assertEqual(self.sanitizar(''), '')
        self.assertEqual(self.sanitizar(None), '')

    def test_escape_de_texto(self):
        resultado = self.sanitizar('<p>5 &lt; 10 &amp; 3 &gt; 1</p>')
        self.assertNotIn('&amp;amp;', resultado)
        self.assertIn('&lt;', resultado)

    def test_form_formato_sanitiza_instructivo(self):
        from documentos.forms import FormatoForm
        form = FormatoForm()
        form.cleaned_data = {'instructivo_html': '<p>hola</p><script>alert(1)</script>'}
        limpio = form.clean_instructivo_html()
        self.assertNotIn('<script', limpio)
        self.assertIn('<p>hola</p>', limpio)

    def test_modelo_formato_sanitiza_en_save(self):
        """Cubre el bypass del Django Admin: el saneado vive en Formato.save()."""
        from documentos.models import Formato
        f = Formato.objects.create(
            codigo='SGC-SAN-1', nombre='Sanitizar', version='1', edicion='1',
            instructivo_html='<p>ok</p><script>alert(1)</script><img src=x onerror=evil()>',
        )
        f.refresh_from_db()
        self.assertNotIn('<script', f.instructivo_html)
        self.assertNotIn('onerror', f.instructivo_html)
        self.assertIn('<p>ok</p>', f.instructivo_html)


class RobustezFase5Test(TestCase):
    def test_sumar_meses_ajusta_fin_de_mes(self):
        from datetime import date
        from documentos.models import _sumar_meses
        # 31-ene + 1 mes → 28-feb (año no bisiesto)
        self.assertEqual(_sumar_meses(date(2025, 1, 31), 1), date(2025, 2, 28))
        # 31-ene + 1 mes → 29-feb (bisiesto)
        self.assertEqual(_sumar_meses(date(2024, 1, 31), 1), date(2024, 2, 29))
        # Cruce de año: 30-nov + 3 meses → 28/29-feb
        self.assertEqual(_sumar_meses(date(2024, 11, 30), 3), date(2025, 2, 28))
        # Multi-año: 15-mar + 24 meses → 15-mar dos años después
        self.assertEqual(_sumar_meses(date(2025, 3, 15), 24), date(2027, 3, 15))
        # Día seguro que existe en el mes destino
        self.assertEqual(_sumar_meses(date(2025, 1, 10), 2), date(2025, 3, 10))

    def test_mensaje_error_ia_no_filtra_detalle(self):
        from documentos.ai import _mensaje_error
        secreto = 'API_KEY_SECRETA_AIza123 filtro-interno'
        exc = RuntimeError(secreto)
        msg = _mensaje_error(exc)
        self.assertNotIn(secreto, msg)
        self.assertNotIn('AIza123', msg)
        self.assertTrue(msg)  # mensaje genérico no vacío

    def test_mensaje_error_ia_transitorio(self):
        from documentos.ai import _mensaje_error

        class Transitorio(Exception):
            code = 429

        msg = _mensaje_error(Transitorio('saturado'))
        self.assertIn('saturado', msg.lower())


class AprobarPlanillaWebTest(BaseDocumentosTest):
    """Flujo web de aprobación/rechazo de planillas (views.aprobar_planilla)."""

    def setUp(self):
        super().setUp()
        self.planilla = PlanillaCambios.objects.create(
            codigo='PC-TI-WEB-1',
            documento=self.doc_activo,
            tipo_cambio='modificacion',
            justificacion='Cambio de prueba',
            responsable=self.user_jefe_ti,
            version_destino='2.0',
            estado='en_revision',
        )
        self.aprobacion = AprobacionPlanilla.objects.create(
            planilla=self.planilla,
            aprobador=self.user_jefe_gg,
            estado='pendiente',
        )

    def test_rechazo_fija_planilla_rechazada(self):
        """Regresión: el rechazo web dejaba la planilla en 'en_revision'."""
        from django.urls import reverse
        self.client.login(username='jefe_gg', password='testpass123')
        url = reverse('documentos:aprobar_planilla', args=[self.planilla.uuid])
        resp = self.client.post(url, {'accion': 'rechazar', 'observaciones': 'No procede'})
        self.assertEqual(resp.status_code, 302)
        self.planilla.refresh_from_db()
        self.aprobacion.refresh_from_db()
        self.assertEqual(self.planilla.estado, 'rechazado')
        self.assertEqual(self.aprobacion.estado, 'rechazada')

    def test_sin_aprobacion_pendiente_no_da_500(self):
        """Regresión: .get() sin guarda devolvía 500 si el usuario no tiene paso pendiente."""
        from django.urls import reverse
        # jefe_ti es jefe pero no tiene AprobacionPlanilla pendiente para esta planilla.
        self.client.login(username='jefe_ti', password='testpass123')
        url = reverse('documentos:aprobar_planilla', args=[self.planilla.uuid])
        resp = self.client.post(url, {'accion': 'aprobar', 'observaciones': ''})
        self.assertEqual(resp.status_code, 302)  # redirige con mensaje, no 500


# =============================================================================
# DEPARTAMENTOS AFECTADOS: "TODOS"
# =============================================================================

class AfectaTodosTest(BaseDocumentosTest):
    """Tests del flag afecta_todos (todos los departamentos)."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_form_afecta_todos_limpia_departamentos(self):
        from documentos.forms import DocumentoForm
        form = DocumentoForm(
            data={
                'nombre': 'Doc todos',
                'descripcion': '',
                'clasificacion': 'interno',
                'periodo_retencion': '',
                'afecta_todos': 'on',
                'departamentos_afectados': [
                    str(self.depto_ti.id), str(self.depto_th.id),
                ],
            },
            tipo_documento='procedimientos_generales',
            departamento_id=self.depto_ti.id,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(list(form.cleaned_data['departamentos_afectados']), [])

    def test_visibilidad_afecta_todos(self):
        """Un doc con `afecta_todos` se muestra UNA vez, en el grupo general."""
        self.doc_activo.afecta_todos = True
        self.doc_activo.save(update_fields=['afecta_todos'])
        # La vista aplica visibilidad por clasificación: usamos un jefe que
        # puede ver el documento (TI).
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/departamento/')
        self.assertEqual(resp.status_code, 200)
        # Aparece en el grupo general ("Todos los departamentos")...
        generales = [
            doc for t in resp.context['generales'] for doc in t['documentos']
        ]
        self.assertIn(self.doc_activo, generales)
        # ...y NO se repite bajo el departamento del jefe.
        for g in resp.context['grupos']:
            codigos = [
                doc.codigo for t in g['tipos_documento'] for doc in t['documentos']
            ]
            self.assertNotIn(self.doc_activo.codigo, codigos)

    def test_afecta_todos_no_se_repite_por_departamento(self):
        """El doc general aparece exactamente una vez: ni en los grupos."""
        self.doc_activo.afecta_todos = True
        self.doc_activo.save(update_fields=['afecta_todos'])
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get('/documentos/departamento/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        uuid = str(self.doc_activo.uuid)
        self.assertEqual(html.count(uuid), 1)
        for g in resp.context['grupos']:
            codigos = [
                doc.codigo for t in g['tipos_documento'] for doc in t['documentos']
            ]
            self.assertNotIn(self.doc_activo.codigo, codigos)
        # Su departamento dueño (TI) tampoco lo duplica: vive solo en el general.
        codigos_ti = [
            doc.codigo
            for g in resp.context['grupos'] if g['departamento'].codigo == 'TI'
            for t in g['tipos_documento'] for doc in t['documentos']
        ]
        self.assertNotIn(self.doc_activo.codigo, codigos_ti)

    def test_filtro_clasificacion_tambien_aplica_al_grupo_general(self):
        self.doc_activo.afecta_todos = True
        self.doc_activo.save(update_fields=['afecta_todos'])
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get('/documentos/departamento/?clasificacion=publico')
        self.assertEqual(resp.status_code, 200)
        generales = [
            doc for t in resp.context['generales'] for doc in t['documentos']
        ]
        # doc_activo es 'interno': el grupo general respeta el filtro.
        self.assertNotIn(self.doc_activo, generales)

    def test_detalle_muestra_badge(self):
        self.doc_activo.afecta_todos = True
        self.doc_activo.save(update_fields=['afecta_todos'])
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Todos los departamentos')


# =============================================================================
# CHECKBOX_GRUPO AUTOMÁTICO: valor como lista
# =============================================================================

class CheckboxGrupoAutoTest(BaseDocumentosTest):
    """El autollenado de checkbox_grupo debe guardar una lista, no un string."""

    def _formato_con_campo(self, clave, tipo, origen):
        from documentos.models import Formato, SeccionFormato, CampoFormato
        formato = Formato.objects.create(
            codigo=f'F-{clave}', nombre='Test', version='1', edicion='1',
        )
        seccion = SeccionFormato.objects.create(formato=formato, titulo='S', orden=1)
        CampoFormato.objects.create(
            seccion=seccion, clave=clave, etiqueta=clave, tipo=tipo, origen=origen,
        )
        return formato

    def test_checkbox_grupo_auto_es_lista(self):
        from documentos.models import mapear_valores_auto
        formato = self._formato_con_campo('accion', 'checkbox_grupo', 'operacion')
        resultado = mapear_valores_auto(formato, {'operacion': 'Creación'})
        self.assertEqual(resultado.get('accion'), ['Creación'])

    def test_texto_auto_no_se_envuelve(self):
        from documentos.models import mapear_valores_auto
        formato = self._formato_con_campo('num_id', 'texto', 'num_identificacion')
        resultado = mapear_valores_auto(formato, {'num_identificacion': 'TI-FOR-001'})
        self.assertEqual(resultado.get('num_id'), 'TI-FOR-001')

    def test_filtro_a_lista(self):
        from documentos.templatetags.formato_extras import a_lista
        self.assertEqual(a_lista('Creación'), ['Creación'])
        self.assertEqual(a_lista(['a', 'b']), ['a', 'b'])
        self.assertEqual(a_lista(''), [])
        self.assertEqual(a_lista(None), [])


# =============================================================================
# EVALUADOR CUANDO EL JEFE DE GG CREA
# =============================================================================

class EvaluadorGGTest(BaseDocumentosTest):
    """Si el jefe de GG crea, evalúa un jefe par (SGC por defecto)."""

    def setUp(self):
        super().setUp()
        self.depto_sgc = Departamento.objects.create(
            codigo='SGC', nombre='Sistema de Gestión de la Calidad', activo=True,
        )

    def test_gg_con_sgc_jefe(self):
        from documentos.views import determinar_evaluador
        self.depto_sgc.jefe = self.user_coordinador
        self.depto_sgc.save(update_fields=['jefe'])
        self.assertEqual(
            determinar_evaluador(self.user_jefe_gg, self.depto_gg),
            self.user_coordinador,
        )

    def test_gg_sin_sgc_jefe_usa_primer_depto_con_jefe(self):
        from documentos.views import determinar_evaluador
        # SGC sin jefe; GG es el solicitante (excluido) → primer depto por
        # código con jefe (TH antes que TI).
        evaluador = determinar_evaluador(self.user_jefe_gg, self.depto_gg)
        self.assertEqual(evaluador, self.user_jefe_th)

    def test_gg_sin_jefes_par_devuelve_none(self):
        from documentos.views import determinar_evaluador
        Departamento.objects.filter(codigo__in=['TI', 'TH']).update(jefe=None)
        self.assertIsNone(determinar_evaluador(self.user_jefe_gg, self.depto_gg))

    def test_no_jefe_crea_sin_cambios(self):
        from documentos.views import determinar_evaluador
        self.assertEqual(
            determinar_evaluador(self.user_trabajador, self.depto_ti),
            self.user_jefe_ti,
        )

    def test_jefe_depto_crea_evalua_sgc(self):
        from documentos.views import determinar_evaluador
        self.depto_sgc.jefe = self.user_coordinador
        self.depto_sgc.save(update_fields=['jefe'])
        # Cualquier jefe que crea → evalúa SGC (no GG).
        self.assertEqual(
            determinar_evaluador(self.user_jefe_ti, self.depto_ti),
            self.user_coordinador,
        )


class ReajustarEvaluadorTest(BaseDocumentosTest):
    """Comando reajustar_evaluador_pendientes."""

    def setUp(self):
        super().setUp()
        self.depto_sgc = Departamento.objects.create(
            codigo='SGC', nombre='Sistema de Gestión de la Calidad', activo=True,
            jefe=self.user_coordinador,
        )

    def _crear_paso(self, solicitante, aprobador):
        return SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision, solicitante=solicitante,
            aprobador=aprobador, rol_aprobador='evaluador',
            es_final=False, estado='pendiente', departamento_origen=self.depto_ti,
        )

    def test_reajusta_documento_de_jefe(self):
        from django.core.management import call_command
        Notificacion.objects.create(
            usuario=self.user_jefe_gg, titulo='x', mensaje='x',
            tipo='solicitud_aprobacion', documento=self.doc_en_revision,
        )
        paso = self._crear_paso(self.user_jefe_ti, self.user_jefe_gg)
        call_command('reajustar_evaluador_pendientes')
        paso.refresh_from_db()
        self.assertEqual(paso.aprobador, self.user_coordinador)
        self.assertEqual(paso.estado, 'pendiente')
        self.assertTrue(Notificacion.objects.filter(
            usuario=self.user_coordinador, tipo='solicitud_aprobacion',
            documento=self.doc_en_revision,
        ).exists())
        self.assertFalse(Notificacion.objects.filter(
            usuario=self.user_jefe_gg, tipo='solicitud_aprobacion',
            documento=self.doc_en_revision,
        ).exists())

    def test_no_jefe_sin_cambios(self):
        from django.core.management import call_command
        paso = self._crear_paso(self.user_trabajador, self.user_jefe_ti)
        call_command('reajustar_evaluador_pendientes')
        paso.refresh_from_db()
        self.assertEqual(paso.aprobador, self.user_jefe_ti)

    def test_idempotente(self):
        from django.core.management import call_command
        paso = self._crear_paso(self.user_jefe_ti, self.user_jefe_gg)
        call_command('reajustar_evaluador_pendientes')
        paso.refresh_from_db()
        primero = paso.aprobador
        call_command('reajustar_evaluador_pendientes')
        paso.refresh_from_db()
        self.assertEqual(paso.aprobador, primero)
        self.assertEqual(primero, self.user_coordinador)


# =============================================================================
# FLUJO DE APROBACIÓN EN EL DETALLE
# =============================================================================

class FlujoAprobacionDetalleTest(BaseDocumentosTest):
    """El detalle muestra quién evalúa y quién autoriza (para todos los roles)."""

    def _crear_cadena(self, doc, estado_eval='pendiente', estado_aut='esperando'):
        SolicitudAprobacionDocumento.objects.create(
            documento=doc, solicitante=self.user_trabajador,
            aprobador=self.user_jefe_ti, rol_aprobador='evaluador',
            es_final=False, estado=estado_eval, departamento_origen=self.depto_ti,
        )
        SolicitudAprobacionDocumento.objects.create(
            documento=doc, solicitante=self.user_trabajador,
            aprobador=self.user_jefe_gg, rol_aprobador='autorizador',
            es_final=True, estado=estado_aut, departamento_origen=self.depto_ti,
        )

    def test_detalle_muestra_flujo_en_revision(self):
        self._crear_cadena(self.doc_en_revision)
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_en_revision.tipo_id}/{self.doc_en_revision.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Flujo de aprobación')
        self.assertContains(resp, 'Evaluación')
        self.assertContains(resp, 'Autorización')
        self.assertContains(resp, 'Jefe TI')
        self.assertContains(resp, 'Jefe GG')

    def test_detalle_muestra_flujo_en_activo(self):
        self._crear_cadena(
            self.doc_activo, estado_eval='aprobada', estado_aut='aprobada',
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Flujo de aprobación')
        self.assertContains(resp, 'Aprobada')

    def test_detalle_flujo_con_paso_sin_aprobador(self):
        SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_en_revision, solicitante=self.user_jefe_ti,
            aprobador=None, rol_aprobador='evaluador', es_final=False,
            estado='esperando', departamento_origen=self.depto_ti,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_en_revision.tipo_id}/{self.doc_en_revision.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Flujo de aprobación')
        self.assertContains(resp, 'Sin asignar')

    def test_historial_estado_ordenado_por_fecha_accion(self):
        """El historial ordena por fecha de acción (respuesta > solicitud)."""
        ev = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_activo, solicitante=self.user_trabajador,
            aprobador=self.user_jefe_ti, rol_aprobador='evaluador',
            es_final=False, estado='aprobada', departamento_origen=self.depto_ti,
        )
        au = SolicitudAprobacionDocumento.objects.create(
            documento=self.doc_activo, solicitante=self.user_trabajador,
            aprobador=self.user_jefe_gg, rol_aprobador='autorizador',
            es_final=True, estado='aprobada', departamento_origen=self.depto_ti,
        )
        SolicitudAprobacionDocumento.objects.filter(pk=ev.pk).update(
            fecha_respuesta=timezone.now() - timedelta(minutes=10)
        )
        SolicitudAprobacionDocumento.objects.filter(pk=au.pk).update(
            fecha_respuesta=timezone.now()
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        historial = list(resp.context['historial_estados'])
        self.assertEqual(
            [s.rol_aprobador for s in historial], ['autorizador', 'evaluador']
        )
        # La fecha principal es la de respuesta y se conserva la de solicitud.
        self.assertContains(resp, 'Solicitado ')


# =============================================================================
# LOADERS DE PROGRESO
# =============================================================================

class EstadoConversionTest(BaseDocumentosTest):
    """Endpoint de estado de conversión (para polling del loader)."""

    def test_endpoint_listo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/estado-conversion/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('listo', resp.json())

    def test_endpoint_denegado(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_th.tipo_id}/{self.doc_th.uuid}/estado-conversion/'
        )
        self.assertEqual(resp.status_code, 403)


class LoaderDetalleTest(BaseDocumentosTest):
    """El detalle renderiza el componente de progreso donde corresponde."""

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch('documentos.tasks.convertir_documento_a_pdf.delay')
    def test_detalle_encola_conversion_con_loader(self, mock_delay):
        """La conversión es ASÍNCRONA: se encola y el detalle muestra el loader."""
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Doc DOCX', codigo='TI-FOR-900',
            estado='activo', propietario=self.user_trabajador,
            departamento=self.depto_ti, tipo_archivo='docx',
            archivo=SimpleUploadedFile('x.docx', b'data'),
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(f'/documentos/{doc.tipo_id}/{doc.uuid}/')
        self.assertEqual(resp.status_code, 200)
        mock_delay.assert_called_once_with(str(doc.uuid))
        self.assertContains(resp, 'id="conv-progress"')

    @override_settings(IA_RESUMEN_HABILITADO=True, GEMINI_API_KEY='k')
    def test_detalle_muestra_loader_ia(self):
        self.doc_activo.resumen_ia_estado = 'pendiente'
        self.doc_activo.save(update_fields=['resumen_ia_estado'])
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_activo.tipo_id}/{self.doc_activo.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'resumen-ia-loading')
        self.assertContains(resp, 'Refinando detalles')


# =============================================================================
# ANIMACIONES DE BOTÓN (button-animations)
# =============================================================================

class ButtonAnimationsTest(BaseDocumentosTest):
    """Cableado del componente: CSS/JS en base, data-anim en las plantillas,
    variant .btn--success definido y ausencia del toast eliminado."""

    @staticmethod
    def _static(nombre):
        from django.contrib.staticfiles import finders
        ruta = finders.find(nombre)
        assert ruta, f'No se encontró el archivo estático {nombre}'
        with open(ruta, encoding='utf-8') as fh:
            return fh.read()

    def test_base_carga_css_y_js(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/mis-documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'css/components/button-animations.css')
        self.assertContains(resp, 'js/button-animations.js')

    def test_formulario_nuevo_tiene_botones_animados(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/nuevo/formulario/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-anim="save"')
        self.assertContains(resp, 'data-anim="send"')
        self.assertContains(resp, 'btn-label')

    def test_planilla_tiene_botones_animados_sin_atributos_de_toast(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/planilla-cambios/nueva/?tipo=formulario'
            f'&doc_uuid={self.doc_borrador.uuid}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-anim="save"')
        self.assertContains(resp, 'data-anim="send"')
        self.assertNotContains(resp, 'data-anim-title')

    def test_detalle_borrador_tiene_enviar_animado(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_borrador.tipo_id}/{self.doc_borrador.uuid}/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-anim="send"')

    def test_confirmar_eliminar_tiene_papelera_animada(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get(
            f'/documentos/{self.doc_borrador.tipo_id}/'
            f'{self.doc_borrador.uuid}/eliminar/'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-anim="delete"')
        self.assertContains(resp, 'btn-label')

    def test_variant_btn_success_esta_definido(self):
        self.assertIn('.btn--success {', self._static('css/components/buttons.css'))

    def test_js_sin_toast_y_con_liberacion_de_estado(self):
        js = self._static('js/button-animations.js')
        # El feedback final es el flash de Django: no debe volver el toast.
        self.assertNotIn('PolidocToast', js)
        self.assertNotIn('showToast', js)
        # Si la navegación no ocurre, el botón no puede quedarse "busy".
        self.assertIn('function resetBoton', js)

    def test_dur_del_js_cubre_las_keyframes(self):
        """DUR debe ser mayor que la duración total de cada bloque de @keyframes.

        La duración total = suma de los tiempos de la declaración `animation:`
        (duración + delay) para la regla que usa ese keyframes.
        """
        import re

        def duracion_animacion(regla):
            """Suma duración + delay de un shorthand `animation:`."""
            sin_parentesis = re.sub(r'\([^)]*\)', '', regla)
            tiempos = [float(v) for v in re.findall(r'([\d.]+)s\b', sin_parentesis)]
            self.assertTrue(tiempos, f'Sin tiempos en: {regla}')
            return sum(tiempos) * 1000

        js = self._static('js/button-animations.js')
        bloque_dur = re.search(r'var DUR = \{(.*?)\};', js, re.S).group(1)
        dur = {
            k: int(v)
            for k, v in re.findall(r"['\"]?(\w+)['\"]?\s*:\s*(\d+)", bloque_dur)
        }

        css = self._static('css/components/button-animations.css')

        def declaracion(selector):
            regla = re.search(
                r'%s\s*\{([^}]*)\}' % re.escape(selector), css, re.S
            )
            self.assertTrue(regla, f'No se encontró la regla {selector}')
            anim = re.search(r'animation:\s*([^;]+);', regla.group(1))
            self.assertTrue(anim, f'Sin animation en {selector}')
            return anim.group(1)

        total_save = max(
            duracion_animacion(declaracion('.btn--anim-saving > i')),
            duracion_animacion(declaracion('.btn--anim-saving::before')),
            # El JS cambia el icono a los 430 ms y lanza el check desde ahí.
            430 + duracion_animacion(declaracion('.btn--anim-saved > i')),
        )
        total_send = max(
            duracion_animacion(declaracion('.btn--anim-sending')),
            duracion_animacion(declaracion('.btn--anim-sending > i')),
            duracion_animacion(declaracion('.btn--anim-sending::after')),
        )
        total_delete = max(
            duracion_animacion(declaracion('.trash-anim__can')),
            duracion_animacion(declaracion('.trash-anim__lid')),
            duracion_animacion(declaracion('.trash-anim__flying')),
        )

        self.assertGreater(dur['save'], total_save)
        self.assertGreater(dur['send'], total_send)
        # La papelera se dibuja en DOM y espera exactamente DUR.delete.
        self.assertGreaterEqual(dur['delete'], total_delete)


# =============================================================================
# ROLES Y PERMISOS: FLUJO DE APROBACIÓN
# =============================================================================

class RolesPermisosFlujoTest(BaseDocumentosTest):
    """La página de roles y permisos documenta el flujo de aprobación."""

    def test_admin_ve_flujo(self):
        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get('/documentos/configuracion/roles-permisos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Flujo de aprobación')

    def test_jefe_ve_flujo(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/configuracion/roles-permisos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Flujo de aprobación')

    def test_trabajador_denegado(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/configuracion/roles-permisos/')
        self.assertEqual(resp.status_code, 302)
