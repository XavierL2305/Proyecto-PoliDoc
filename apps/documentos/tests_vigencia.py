"""Tests de la vigencia de documentos: configuración por tipo, formulario,
estado (Vigente/Vence pronto/Vencido), eliminación trazable y panel del jefe.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tests_base import MONGO_PATCH_TARGET, BaseDocumentosTest
from documentos.forms import DocumentoForm, TipoDocumentoForm
from documentos.models import Documento, Periodo, TipoDocumento


def _fake_mongo():
    store = {}
    db = MagicMock()
    db.__getitem__.side_effect = lambda name: store.setdefault(name, MagicMock())
    db.audit_logs = store.setdefault('audit_logs', MagicMock())
    return db, store


class BaseVigenciaTest(BaseDocumentosTest):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.periodo = Periodo.objects.create(
            nombre='1 Año', meses=12, activo=True, orden=1,
        )

    def _doc(self, **extra):
        datos = dict(
            tipo=self.tipo_procedimiento, nombre='Doc Vig', codigo='TI-PRG-VIG',
            estado='activo', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, version_actual='1.0',
        )
        datos.update(extra)
        return Documento.objects.create(**datos)


class TipoDocumentoVigenciaTest(BaseVigenciaTest):

    def test_form_incluye_periodo_y_flag(self):
        form = TipoDocumentoForm()
        self.assertIn('periodo_vigencia', form.fields)
        self.assertIn('requiere_vigencia', form.fields)

    def test_guarda_flag_y_periodo(self):
        form = TipoDocumentoForm(data={
            'codigo': 'TIPO-VIG', 'nombre': 'Con vigencia',
            'descripcion': '', 'activo': 'on',
            'usa_departamentos_afectados': 'on', 'usa_responsables': 'on',
            'usa_participantes': 'on', 'usa_documentos_relacionados': 'on',
            'color': 'primary',
            'periodo_vigencia': self.periodo.id,
            'requiere_vigencia': 'on',
        })
        self.assertTrue(form.is_valid(), form.errors)
        tipo = form.save()
        self.assertTrue(tipo.requiere_vigencia)
        self.assertEqual(tipo.periodo_vigencia, self.periodo)


class DocumentoFormVigenciaTest(BaseVigenciaTest):

    def _data(self, **extra):
        datos = {
            'nombre': 'X', 'descripcion': '', 'clasificacion': 'interno',
            'periodo_vigencia': '', 'periodo_retencion': '', 'afecta_todos': '',
        }
        datos.update(extra)
        return datos

    def test_requiere_vigencia_hace_el_campo_obligatorio(self):
        tipo = TipoDocumento.objects.create(
            key='tipo_oblig', nombre='Obligatorio', codigo='TOB',
            requiere_vigencia=True,
        )
        form = DocumentoForm(data=self._data(), tipo_documento=tipo)
        self.assertFalse(form.is_valid())
        self.assertIn('periodo_vigencia', form.errors)

        valido = DocumentoForm(data=self._data(
            periodo_vigencia=self.periodo.id,
        ), tipo_documento=tipo)
        self.assertTrue(valido.is_valid(), valido.errors)

    def test_sin_flag_puede_quedar_sin_vigencia(self):
        tipo = TipoDocumento.objects.create(
            key='tipo_opc', nombre='Opcional', codigo='TOP',
        )
        form = DocumentoForm(data=self._data(), tipo_documento=tipo)
        self.assertTrue(form.is_valid(), form.errors)

    def test_precarga_periodo_por_defecto(self):
        tipo = TipoDocumento.objects.create(
            key='tipo_def', nombre='Con default', codigo='TDF',
            periodo_vigencia=self.periodo,
        )
        form = DocumentoForm(tipo_documento=tipo)
        self.assertEqual(form.fields['periodo_vigencia'].initial, self.periodo)


class EstadoVigenciaTest(BaseVigenciaTest):

    def test_estados(self):
        hoy = date.today()
        vencido = self._doc(codigo='TI-PRG-V1', fecha_vencimiento=hoy - timedelta(days=1))
        pronto = self._doc(codigo='TI-PRG-V2', fecha_vencimiento=hoy + timedelta(days=3))
        vigente = self._doc(codigo='TI-PRG-V3', fecha_vencimiento=hoy + timedelta(days=30))
        sin = self._doc(codigo='TI-PRG-V4')
        self.assertEqual(vencido.estado_vigencia, 'vencido')
        self.assertEqual(pronto.estado_vigencia, 'vence_pronto')
        self.assertEqual(vigente.estado_vigencia, 'vigente')
        self.assertIsNone(sin.estado_vigencia)
        self.assertIsNone(sin.dias_para_vencer)


class QuitarVigenciaTest(BaseVigenciaTest):

    def _doc_con_vigencia(self, **extra):
        doc = self._doc(
            periodo_vigencia=self.periodo,
            fecha_vigencia=timezone.now(),
            fecha_vencimiento=date.today() + timedelta(days=365),
            proxima_revision=date.today() + timedelta(days=365),
            **extra,
        )
        return doc

    def test_propietario_quita_con_motivo_y_audita(self):
        doc = self._doc_con_vigencia()
        db, store = _fake_mongo()
        self.client.login(username='jefe_ti', password='testpass123')
        url = reverse('documentos:quitar_vigencia', args=[doc.tipo_id, doc.uuid])
        with patch(MONGO_PATCH_TARGET, return_value=db):
            resp = self.client.post(url, {'motivo': 'Se asignó por error'})
        self.assertEqual(resp.status_code, 302)
        doc.refresh_from_db()
        self.assertIsNone(doc.periodo_vigencia)
        self.assertIsNone(doc.fecha_vencimiento)
        self.assertIsNone(doc.proxima_revision)
        self.assertIn('vigencia eliminada', doc.historial_cambios)

        registros = [c[0][0] for c in store['audit_logs'].insert_one.call_args_list]
        tipos = [r['accion']['tipo'] for r in registros]
        self.assertIn('eliminacion_vigencia', tipos)
        reg = next(r for r in registros if r['accion']['tipo'] == 'eliminacion_vigencia')
        self.assertEqual(reg['detalles']['motivo'], 'Se asignó por error')

    def test_motivo_obligatorio(self):
        doc = self._doc_con_vigencia()
        self.client.login(username='jefe_ti', password='testpass123')
        url = reverse('documentos:quitar_vigencia', args=[doc.tipo_id, doc.uuid])
        self.client.post(url, {'motivo': ''})
        doc.refresh_from_db()
        self.assertIsNotNone(doc.periodo_vigencia)

    def test_bloqueado_si_el_tipo_exige_vigencia(self):
        tipo_oblig = TipoDocumento.objects.create(
            key='tipo_oblig2', nombre='Obligatorio', codigo='TOB2',
            requiere_vigencia=True,
        )
        doc = self._doc_con_vigencia(tipo=tipo_oblig, codigo='TI-PRG-OBL')
        self.client.login(username='jefe_ti', password='testpass123')
        url = reverse('documentos:quitar_vigencia', args=[doc.tipo_id, doc.uuid])
        self.client.post(url, {'motivo': 'x'})
        doc.refresh_from_db()
        self.assertIsNotNone(doc.periodo_vigencia)

    def test_usuario_sin_permiso_denegado(self):
        doc = self._doc_con_vigencia()
        self.client.login(username='trab02', password='testpass123')
        url = reverse('documentos:quitar_vigencia', args=[doc.tipo_id, doc.uuid])
        self.client.post(url, {'motivo': 'x'})
        doc.refresh_from_db()
        self.assertIsNotNone(doc.periodo_vigencia)


class DepartamentoVigenciaTest(BaseVigenciaTest):

    def setUp(self):
        super().setUp()
        hoy = date.today()
        self.vencido = self._doc(
            nombre='Vencido', codigo='TI-PRG-101',
            fecha_vencimiento=hoy - timedelta(days=2),
        )
        self.pronto = self._doc(
            nombre='Pronto', codigo='TI-PRG-102',
            fecha_vencimiento=hoy + timedelta(days=2),
        )
        self.vigente = self._doc(
            nombre='Vigente', codigo='TI-PRG-103',
            fecha_vencimiento=hoy + timedelta(days=60),
        )

    def test_panel_conteos(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(reverse('documentos:documentos_departamento'))
        self.assertEqual(resp.status_code, 200)
        conteos = resp.context['conteos_vigencia']
        self.assertGreaterEqual(conteos['vencidos'], 1)
        self.assertGreaterEqual(conteos['vence_pronto'], 1)
        self.assertGreaterEqual(conteos['vigentes'], 1)

    def test_filtro_vencidos(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(
            reverse('documentos:documentos_departamento'), {'vigencia': 'vencido'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'TI-PRG-101')
        self.assertNotContains(resp, 'TI-PRG-103')

    def test_restringido_no_se_muestra_al_jefe(self):
        self._doc(
            nombre='Secreto', codigo='TI-PRG-SECRET',
            clasificacion='restringido', propietario=self.user_trabajador,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(reverse('documentos:documentos_departamento'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'TI-PRG-SECRET')


class DetalleVigenciaUITest(BaseVigenciaTest):

    def test_activo_no_ofrece_renovar(self):
        """En un documento activo la vigencia se gestiona en el card ISO."""
        doc = self._doc(
            periodo_vigencia=self.periodo,
            fecha_vencimiento=date.today() + timedelta(days=30),
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(reverse('documentos:detalle', args=[doc.tipo_id, doc.uuid]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Renovar')
        self.assertContains(resp, 'Cambiar')

    def test_obsoleto_ofrece_reactivar(self):
        doc = self._doc(
            estado='obsoleto',
            fecha_vencimiento=date.today() - timedelta(days=5),
            historial_cambios='v1 - alta',
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(reverse('documentos:detalle', args=[doc.tipo_id, doc.uuid]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Solicitar Reactivación / Renovación')
