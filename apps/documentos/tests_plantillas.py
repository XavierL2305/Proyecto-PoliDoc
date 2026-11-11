"""Tests del apartado Plantillas (es_plantilla / plantilla_origen).

Cubren:
- Código PLT separado de la serie normal.
- Alta de plantilla con aprobación simplificada (jefe/delegado, sin GG).
- Modificación (FOR-005) y eliminación (FOR-001) con el mismo criterio.
- Catálogo /documentos/ solo con plantillas.
- plantilla_origen obligatorio al crear un Documento.
"""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests_base import BaseDocumentosTest

from auditoria.models import AuditoriaOutbox
from autenticacion.models import Notificacion
from documentos.models import (
    AprobacionPlanilla, AsignacionDocumento, CampoFormato,
    DelegacionJefatura, DistribucionDocumento, Documento,
    FirmaElectronica, Formato, PlanillaCambios, SeccionFormato,
    SecuenciaCodigo, SolicitudAprobacionDocumento, SolicitudCambio,
    SolicitudDescarga, TipoDocumento, tipo_archivo_desde_nombre,
)
from documentos.permissions import documentos_descargables, puede_descargar
from documentos.services.aprobaciones import (
    notificar_planilla_resuelta, tomar_paso_esperando,
)
from documentos.services.cambios_estado import solicitar_cambio_estado
from documentos.views import (
    crear_cadena_aprobacion_planilla,
)


class TipoArchivoTest(BaseDocumentosTest):
    def test_infiere_por_extension(self):
        self.assertEqual(tipo_archivo_desde_nombre('a.pdf'), 'pdf')
        self.assertEqual(tipo_archivo_desde_nombre('a.DOCX'), 'docx')
        self.assertEqual(tipo_archivo_desde_nombre('a.xlsx'), 'xlsx')
        self.assertEqual(tipo_archivo_desde_nombre('a.vsdx'), 'vsdx')

    def test_fallback(self):
        self.assertEqual(tipo_archivo_desde_nombre('a.unknown'), 'pdf')
        self.assertEqual(tipo_archivo_desde_nombre('', 'docx'), 'docx')

    def test_documento_save_infiere_tipo_desde_archivo(self):
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Doc XLSX',
            codigo='TI-FOR-XLSX-1', estado='activo',
            propietario=self.user_trabajador, departamento=self.depto_ti,
            tipo_archivo='pdf',
            archivo=SimpleUploadedFile('real.xlsx', b'PK\x03\x04fake'),
        )
        doc.refresh_from_db()
        self.assertEqual(doc.tipo_archivo, 'xlsx')


class PlantillaCodigoTest(BaseDocumentosTest):
    def test_codigo_plt_separado_de_la_serie_normal(self):
        self.assertEqual(
            Documento.generar_codigo_secuencial(
                self.depto_ti, 'formulario', es_plantilla=True,
            ),
            'TI-FOR-PLT-001',
        )

    def test_codigo_normal_no_incluye_plt(self):
        self.assertEqual(
            Documento.generar_codigo_secuencial(
                self.depto_ti, 'formulario', es_plantilla=False,
            ),
            'TI-FOR-004',
        )


class CrearPlantillaTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def _post(self, **extra):
        data = {
            'nombre': 'Plantilla de Prueba',
            'descripcion': 'Documento en blanco',
            'clasificacion': 'interno',
            'archivo': SimpleUploadedFile('plantilla.pdf', b'%PDF-1.4 fake'),
        }
        data.update(extra)
        return self.client.post(
            reverse('documentos:crear_plantilla', args=['formulario']), data,
        )

    def test_xlsx_guarda_tipo_archivo_correcto(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post(
            nombre='Plantilla XLSX',
            archivo=SimpleUploadedFile(
                'plantilla.xlsx', b'PK\x03\x04contenido falso',
            ),
        )
        self.assertEqual(resp.status_code, 302)
        doc = Documento.objects.get(nombre='Plantilla XLSX')
        self.assertEqual(doc.tipo_archivo, 'xlsx')

    def test_jefe_autoaprueba_sin_gg_ni_for_001(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._post()
        self.assertEqual(resp.status_code, 302)

        doc = Documento.objects.get(codigo='TI-FOR-PLT-001')
        self.assertTrue(doc.es_plantilla)
        self.assertEqual(doc.estado, 'activo')
        self.assertIsNone(doc.plantilla_origen)

        sol = SolicitudAprobacionDocumento.objects.get(
            documento=doc, accion_solicitud='creacion',
        )
        self.assertEqual(sol.estado, 'aprobada')
        self.assertEqual(sol.aprobador, self.user_jefe_ti)
        self.assertTrue(sol.es_final)
        # Creación de plantilla NO genera FOR-001.
        self.assertFalse(
            SolicitudCambio.objects.filter(documento=doc, operacion='creacion').exists()
        )

    def test_trabajador_queda_pendiente_del_jefe(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self._post(nombre='Plantilla Trabajador')
        self.assertEqual(resp.status_code, 302)

        doc = Documento.objects.get(nombre='Plantilla Trabajador')
        self.assertEqual(doc.estado, 'en_revision')
        sol = SolicitudAprobacionDocumento.objects.get(
            documento=doc, accion_solicitud='creacion',
        )
        self.assertEqual(sol.estado, 'pendiente')
        self.assertEqual(sol.aprobador, self.user_jefe_ti)

    def test_sin_jefe_queda_esperando_y_avisa_admins(self):
        self.depto_ti.jefe = None
        self.depto_ti.save(update_fields=['jefe'])
        self.client.login(username='trab01', password='testpass123')
        resp = self._post(nombre='Plantilla Sin Jefe')
        self.assertEqual(resp.status_code, 302)

        doc = Documento.objects.get(nombre='Plantilla Sin Jefe')
        sol = SolicitudAprobacionDocumento.objects.get(
            documento=doc, accion_solicitud='creacion',
        )
        self.assertEqual(sol.estado, 'esperando')
        self.assertIsNone(sol.aprobador)
        self.assertTrue(
            Notificacion.objects.filter(
                usuario=self.user_admin,
                titulo__icontains='sin aprobador',
            ).exists()
        )

    def test_jefe_aprueba_plantilla_pendiente_via_api(self):
        self.client.login(username='trab01', password='testpass123')
        self._post(nombre='Plantilla Pendiente')
        doc = Documento.objects.get(nombre='Plantilla Pendiente')
        self.assertEqual(doc.estado, 'en_revision')

        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse('documentos:api_aprobar_creacion',
                    args=['formulario', doc.uuid]),
            data='{}', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        doc.refresh_from_db()
        self.assertEqual(doc.estado, 'activo')
        self.assertFalse(
            FirmaElectronica.objects.filter(
                origen_tipo='solicitud_aprobacion_documento',
                origen_id=str(SolicitudAprobacionDocumento.objects.get(
                    documento=doc, accion_solicitud='creacion',
                ).pk),
            ).exists(),
            'La aprobación de una plantilla no debe emitir firma SES',
        )

    def test_admin_toma_plantilla_en_esperando_via_api(self):
        self.depto_ti.jefe = None
        self.depto_ti.save(update_fields=['jefe'])
        self.client.login(username='trab01', password='testpass123')
        self._post(nombre='Plantilla Tomada por Admin')
        doc = Documento.objects.get(nombre='Plantilla Tomada por Admin')

        self.client.login(username='admin01', password='testpass123')
        resp = self.client.post(
            reverse('documentos:api_aprobar_creacion',
                    args=['formulario', doc.uuid]),
            data='{}', content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        doc.refresh_from_db()
        self.assertEqual(doc.estado, 'activo')
        sol = SolicitudAprobacionDocumento.objects.get(
            documento=doc, accion_solicitud='creacion',
        )
        self.assertEqual(sol.estado, 'aprobada')
        self.assertEqual(sol.aprobador, self.user_admin)
        self.assertFalse(FirmaElectronica.para(sol).exists())

    def test_mis_solicitudes_etiqueta_creacion_de_plantilla(self):
        self.client.login(username='trab01', password='testpass123')
        self._post(nombre='Plantilla Etiqueta')
        doc = Documento.objects.get(nombre='Plantilla Etiqueta')

        from documentos.services.solicitudes import mis_solicitudes
        items, _ = mis_solicitudes(self.user_trabajador)
        item = next(
            i for i in items
            if i.tipo == 'creacion_plantilla' and i.documento
            and i.documento.pk == doc.pk
        )
        self.assertEqual(item.tipo_label, 'Creación de plantilla')
        self.assertEqual(item.tipo, 'creacion_plantilla')

        resp = self.client.get(
            reverse('documentos:mis_solicitudes'),
            {'tipo': 'creacion_plantilla'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Plantilla Etiqueta')

    def test_bandeja_marca_plantilla(self):
        self.client.login(username='trab01', password='testpass123')
        self._post(nombre='Plantilla Bandeja')

        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(reverse('documentos:bandeja_aprobaciones'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(
            resp, '<span class="badge badge--primary">Plantilla</span>',
            html=True,
        )

    def test_bandeja_admin_incluye_plantilla_esperando_en_pendientes(self):
        self.depto_ti.jefe = None
        self.depto_ti.save(update_fields=['jefe'])
        self.client.login(username='trab01', password='testpass123')
        self._post(nombre='Plantilla Esperando Bandeja')

        self.client.login(username='admin01', password='testpass123')
        resp = self.client.get(reverse('documentos:bandeja_aprobaciones'))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_pendientes'], 1)
        self.assertContains(resp, 'Plantilla Esperando Bandeja')

    def test_coordinador_sin_delegacion_queda_pendiente_del_jefe(self):
        self.client.login(username='coord01', password='testpass123')
        resp = self._post(nombre='Plantilla Coordinador Sin Delegación')
        self.assertEqual(resp.status_code, 302)

        doc = Documento.objects.get(nombre='Plantilla Coordinador Sin Delegación')
        self.assertEqual(doc.estado, 'en_revision')
        sol = SolicitudAprobacionDocumento.objects.get(
            documento=doc, accion_solicitud='creacion',
        )
        self.assertEqual(sol.estado, 'pendiente')
        self.assertEqual(sol.aprobador, self.user_jefe_ti)

    def test_coordinador_con_delegacion_queda_activo_directo(self):
        hoy = timezone.now().date()
        DelegacionJefatura.objects.create(
            jefe=self.user_jefe_ti, delegado=self.user_coordinador,
            departamento=self.depto_ti, motivo='Vacaciones',
            fecha_inicio=hoy, fecha_fin=hoy + timedelta(days=10),
            creada_por=self.user_jefe_ti,
        )
        self.client.login(username='coord01', password='testpass123')
        resp = self._post(nombre='Plantilla Coordinador Delegado')
        self.assertEqual(resp.status_code, 302)

        doc = Documento.objects.get(nombre='Plantilla Coordinador Delegado')
        self.assertEqual(doc.estado, 'activo')
        sol = SolicitudAprobacionDocumento.objects.get(
            documento=doc, accion_solicitud='creacion',
        )
        self.assertEqual(sol.estado, 'aprobada')
        self.assertEqual(sol.aprobador, self.user_coordinador)


class CatalogoPlantillasTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_catalogo_solo_muestra_plantillas(self):
        Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla Visible',
            codigo='TI-FOR-PLT-900', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )
        Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Documento Normal Oculto',
            codigo='TI-FOR-901', estado='activo', es_plantilla=False,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Plantilla Visible')
        self.assertNotContains(resp, 'Documento Normal Oculto')
        self.assertContains(resp, 'Subir plantilla')

    def test_card_usa_icono_y_color_del_tipo(self):
        self.tipo_formulario.icono = 'clipboard'
        self.tipo_formulario.color = 'success'
        self.tipo_formulario.save(update_fields=['icono', 'color'])
        Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla con estilo',
            codigo='TI-FOR-PLT-902', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get('/documentos/')

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'fa-clipboard')
        self.assertContains(resp, '--color-success-light')

    def _crear_plantilla(self, nombre, codigo, propietario, departamento,
                         clasificacion='interno'):
        return Documento.objects.create(
            tipo=self.tipo_formulario, nombre=nombre, codigo=codigo,
            estado='activo', es_plantilla=True, clasificacion=clasificacion,
            propietario=propietario, departamento=departamento,
            version_actual='1.0',
        )

    def _todos_los_grupos(self, resp):
        return {
            g['titulo']: {doc.nombre for doc in g['documentos']}
            for g in resp.context['grupos']
        }

    def test_trabajador_ve_plantilla_interna_de_su_departamento(self):
        """Un `interno` del departamento lo ve cualquier miembro del depto."""
        doc = self._crear_plantilla(
            'Interna TI', 'TI-FOR-PLT-910',
            self.user_jefe_ti, self.depto_ti,
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')

        self.assertEqual(resp.status_code, 200)
        grupos = self._todos_los_grupos(resp)
        self.assertIn('Interna TI', grupos.get('Mi Departamento', set()))
        # La descarga sigue requiriendo aprobación: la tarjeta ofrece
        # "Solicitar", no descarga directa.
        self.assertNotIn(doc.uuid, resp.context['docs_descargables'])
        self.assertContains(resp, reverse(
            'documentos:solicitar_descarga',
            kwargs={'tipo': doc.tipo_id, 'documento_id': doc.uuid},
        ))

    def test_trabajador_no_ve_plantilla_confidencial_de_su_departamento(self):
        self._crear_plantilla(
            'Confidencial TI', 'TI-FOR-PLT-911',
            self.user_jefe_ti, self.depto_ti, clasificacion='confidencial',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')

        self.assertEqual(resp.status_code, 200)
        nombres = set().union(*self._todos_los_grupos(resp).values())
        self.assertNotIn('Confidencial TI', nombres)

    def test_trabajador_no_ve_plantilla_interna_de_otro_departamento(self):
        self._crear_plantilla(
            'Interna TH', 'TH-FOR-PLT-912',
            self.user_trabajador_otro, self.depto_th,
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')

        self.assertEqual(resp.status_code, 200)
        nombres = set().union(*self._todos_los_grupos(resp).values())
        self.assertNotIn('Interna TH', nombres)

    def test_trabajador_sigue_viendo_publicas_de_otros_departamentos(self):
        self._crear_plantilla(
            'Publica TH', 'TH-FOR-PLT-913',
            self.user_trabajador_otro, self.depto_th,
            clasificacion='publico',
        )
        self.client.login(username='trab01', password='testpass123')
        resp = self.client.get('/documentos/')

        self.assertEqual(resp.status_code, 200)
        grupos = self._todos_los_grupos(resp)
        self.assertIn('Publica TH', grupos.get('Otros Departamentos', set()))


class ModificarPlantillaTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.plantilla = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla Base',
            codigo='TI-FOR-PLT-001', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
            version_actual='1.0',
        )

    def _planilla(self, responsable):
        return PlanillaCambios.objects.create(
            codigo=f'PC-PLT-{responsable.username}',
            documento=self.plantilla,
            tipo_cambio='modificacion',
            justificacion='Cambio de prueba',
            responsable=responsable,
            version_destino='2',
            estado='en_revision',
            nombre_propuesto='Plantilla Renombrada',
        )

    def test_jefe_aprueba_directo_sin_gg(self):
        planilla = self._planilla(self.user_jefe_ti)
        self.plantilla.estado = 'en_revision'
        self.plantilla.save(update_fields=['estado'])

        ap = crear_cadena_aprobacion_planilla(
            planilla=planilla, usuario=self.user_jefe_ti, documento=self.plantilla,
        )
        ap.refresh_from_db()
        planilla.refresh_from_db()
        self.plantilla.refresh_from_db()

        self.assertEqual(ap.estado, 'aprobada')
        self.assertEqual(ap.aprobador, self.user_jefe_ti)
        self.assertEqual(planilla.estado, 'aprobado')
        self.assertEqual(self.plantilla.estado, 'activo')
        self.assertEqual(self.plantilla.nombre, 'Plantilla Renombrada')

    def test_trabajador_queda_pendiente_del_jefe(self):
        planilla = self._planilla(self.user_trabajador)
        ap = crear_cadena_aprobacion_planilla(
            planilla=planilla, usuario=self.user_trabajador, documento=self.plantilla,
        )
        ap.refresh_from_db()
        self.assertEqual(ap.estado, 'pendiente')
        self.assertEqual(ap.aprobador, self.user_jefe_ti)
        self.assertTrue(ap.es_final)


class EliminarPlantillaTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.plantilla = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla a Inhabilitar',
            codigo='TI-FOR-PLT-002', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )

    def test_jefe_aplica_directo_sin_gg(self):
        res = solicitar_cambio_estado(
            self.plantilla, self.user_jefe_ti, 'inhabilitar',
        )
        self.plantilla.refresh_from_db()
        self.assertTrue(res.aplicado)
        self.assertEqual(self.plantilla.estado, 'obsoleto')
        sol = SolicitudAprobacionDocumento.objects.get(
            documento=self.plantilla, accion_solicitud='inhabilitar',
        )
        self.assertEqual(sol.estado, 'aprobada')
        self.assertEqual(sol.aprobador, self.user_jefe_ti)

    def test_trabajador_pendiente_del_jefe_no_de_gg(self):
        # El trabajador debe ser propietario para poder iniciar la eliminación.
        self.plantilla.propietario = self.user_trabajador
        self.plantilla.save(update_fields=['propietario'])
        res = solicitar_cambio_estado(
            self.plantilla, self.user_trabajador, 'inhabilitar',
        )
        self.assertFalse(res.aplicado)
        self.assertEqual(res.aprobador, self.user_jefe_ti)
        sol = SolicitudAprobacionDocumento.objects.get(
            documento=self.plantilla, accion_solicitud='inhabilitar',
        )
        self.assertEqual(sol.estado, 'pendiente')
        self.assertEqual(sol.rol_aprobador, 'autorizador')


class DetallePlantillaTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.plantilla = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla Detalle',
            codigo='TI-FOR-PLT-030', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )

    def test_detalle_documento_muestra_plantilla_de_origen(self):
        doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Documento desde plantilla',
            codigo='TI-FOR-030', estado='activo', propietario=self.user_jefe_ti,
            departamento=self.depto_ti, plantilla_origen=self.plantilla,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(reverse(
            'documentos:detalle', args=['formulario', doc.uuid],
        ))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Plantilla de origen')
        self.assertContains(resp, self.plantilla.codigo)
        self.assertContains(resp, reverse(
            'documentos:detalle',
            args=['formulario', self.plantilla.uuid],
        ))

    def test_detalle_plantilla_muestra_acciones_de_planilla_e_inhabilitar(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(reverse(
            'documentos:detalle', args=['formulario', self.plantilla.uuid],
        ))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Planilla de Cambios')
        self.assertContains(resp, 'Inhabilitar')

    def test_vista_eliminar_plantilla_genera_for_001(self):
        formato = Formato.objects.create(
            codigo='SGC-FOR-001', nombre='Control de cambios',
            version='1', edicion='1', activo=True, aplica_eliminacion=True,
        )
        seccion = SeccionFormato.objects.create(
            formato=formato, titulo='Disposición', orden=1,
        )
        CampoFormato.objects.create(
            seccion=seccion, clave='motivo', etiqueta='Motivo',
            tipo='textarea', orden=1,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        url = reverse('documentos:eliminar', args=['formulario', self.plantilla.uuid])

        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, formato.codigo)

        resp = self.client.post(url, {})
        self.assertEqual(resp.status_code, 302)
        self.plantilla.refresh_from_db()
        self.assertEqual(self.plantilla.estado, 'obsoleto')
        self.assertTrue(SolicitudCambio.objects.filter(
            documento=self.plantilla, formato=formato,
            operacion='eliminacion',
        ).exists())


class PlantillaOrigenObligatoriaTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.plantilla = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla Origen',
            codigo='TI-FOR-PLT-010', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )

    def _payload(self, **extra):
        data = {
            'nombre': 'Documento desde plantilla',
            'descripcion': 'Llenado',
            'clasificacion': 'interno',
            'periodo_vigencia': '',
            'periodo_retencion': '',
            'secuencia': '050',
            'accion': 'borrador',
        }
        data.update(extra)
        return data

    def test_crea_documento_con_plantilla_origen(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse('documentos:crear_documento', args=['formulario']),
            self._payload(plantilla_origen=str(self.plantilla.uuid)),
        )
        self.assertEqual(resp.status_code, 302)
        doc = Documento.objects.get(codigo='TI-FOR-050')
        self.assertEqual(doc.plantilla_origen, self.plantilla)
        self.assertFalse(doc.es_plantilla)

        detalle = self.client.get(reverse(
            'documentos:detalle', args=['formulario', doc.uuid],
        ))
        self.assertContains(detalle, 'Plantilla de origen')
        self.assertContains(detalle, self.plantilla.codigo)
        self.assertContains(detalle, reverse(
            'documentos:detalle',
            args=['formulario', self.plantilla.uuid],
        ))

    def test_no_crea_sin_plantilla_origen(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse('documentos:crear_documento', args=['formulario']),
            self._payload(),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            Documento.objects.filter(codigo='TI-FOR-050').exists()
        )

    def test_bloquea_si_el_tipo_no_tiene_plantilla_activa(self):
        tipo = TipoDocumento.objects.create(
            key='tipo_sin_plantilla', nombre='Tipo sin plantilla',
            codigo='TSP', activo=True,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.post(
            reverse('documentos:crear_documento', args=[tipo.key]),
            self._payload(nombre='Doc sin plantilla'),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            Documento.objects.filter(nombre='Doc sin plantilla').exists()
        )

class PlanillaPlantillaAdminTest(BaseDocumentosTest):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.plantilla = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla Admin',
            codigo='TI-FOR-PLT-040', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )

    def _planilla(self, documento, codigo, estado='en_revision'):
        return PlanillaCambios.objects.create(
            codigo=codigo, documento=documento, tipo_cambio='modificacion',
            justificacion='Cambio de prueba', responsable=self.user_trabajador,
            version_destino='2', estado=estado,
        )

    def _aprobacion_esperando(self, planilla):
        return AprobacionPlanilla.objects.create(
            planilla=planilla, aprobador=None, rol_aprobador='autorizador',
            es_final=True, estado='esperando',
        )

    def _post(self, url, payload=None):
        # Aprobar/rechazar plantillas no pide PIN ni emite firma SES.
        return self.client.post(
            url, data=json.dumps(payload or {}),
            content_type='application/json',
        )

    def test_admin_no_aprueba_planilla_normal_en_esperando(self):
        planilla = self._planilla(self.doc_activo, 'PC-NORMAL-ADMIN')
        self._aprobacion_esperando(planilla)
        self.client.login(username='admin01', password='testpass123')

        resp = self._post(reverse(
            'documentos:api_aprobar_planilla', args=[planilla.uuid],
        ))
        self.assertEqual(resp.status_code, 404)
        planilla.refresh_from_db()
        self.assertEqual(planilla.estado, 'en_revision')

    def test_admin_rechaza_plantilla_en_esperando(self):
        planilla = self._planilla(self.plantilla, 'PC-PLT-RECHAZO')
        aprobacion = self._aprobacion_esperando(planilla)
        self.client.login(username='admin01', password='testpass123')

        resp = self._post(
            reverse('documentos:api_rechazar_planilla', args=[planilla.uuid]),
            {'motivo': 'No procede'},
        )
        self.assertEqual(resp.status_code, 200)
        planilla.refresh_from_db()
        aprobacion.refresh_from_db()
        self.assertEqual(planilla.estado, 'rechazado')
        self.assertEqual(aprobacion.estado, 'rechazada')
        self.assertEqual(aprobacion.aprobador, self.user_admin)

    def test_toma_crea_evento_outbox(self):
        planilla = self._planilla(self.plantilla, 'PC-PLT-AUDIT-OK')
        aprobacion = self._aprobacion_esperando(planilla)

        tomar_paso_esperando(aprobacion, self.user_admin)

        aprobacion.refresh_from_db()
        self.assertEqual(aprobacion.estado, 'pendiente')
        self.assertEqual(aprobacion.aprobador, self.user_admin)
        evento = AuditoriaOutbox.objects.filter(
            payload__accion_tipo='toma_aprobacion',
        ).latest('id')
        self.assertEqual(evento.estado, 'pendiente')
        self.assertEqual(evento.payload['accion_tipo'], 'toma_aprobacion')
        self.assertEqual(evento.payload['usuario_id'], self.user_admin.id)
        self.assertEqual(evento.payload['estado_anterior'], 'esperando')
        self.assertTrue(evento.payload['correlation_id'])

    def test_notificar_planilla_sin_documento_no_falla(self):
        planilla = PlanillaCambios.objects.create(
            codigo='PC-SIN-DOC', documento=None, tipo_cambio='modificacion',
            justificacion='Sin documento', responsable=self.user_trabajador,
            version_destino='2', estado='aprobado',
        )
        notificar_planilla_resuelta(planilla, True)

        notif = Notificacion.objects.get(
            usuario=self.user_trabajador,
            mensaje__contains=planilla.codigo,
        )
        self.assertIsNone(notif.documento)

    def test_admin_aprueba_plantilla_en_esperando(self):
        planilla = self._planilla(self.plantilla, 'PC-PLT-APRUEBA')
        aprobacion = self._aprobacion_esperando(planilla)
        self.client.login(username='admin01', password='testpass123')

        resp = self._post(reverse(
            'documentos:api_aprobar_planilla', args=[planilla.uuid],
        ))
        self.assertEqual(resp.status_code, 200)
        planilla.refresh_from_db()
        aprobacion.refresh_from_db()
        self.assertEqual(planilla.estado, 'aprobado')
        self.assertEqual(aprobacion.estado, 'aprobada')
        self.assertEqual(aprobacion.aprobador, self.user_admin)
        self.assertFalse(
            FirmaElectronica.para(aprobacion).exists(),
            'La planilla de una plantilla no debe emitir firma SES',
        )


class TomaPasoRollbackTest(TransactionTestCase):
    def test_toma_no_deja_outbox_si_la_transaccion_revierte(self):
        documento = SimpleNamespace(
            tipo_id='formulario', id=1, codigo='TI-FOR-X', nombre='Documento X',
        )
        aprobacion = SimpleNamespace(
            id=1, documento=documento, planilla=None,
            save=lambda **kwargs: None,
        )
        usuario = SimpleNamespace(id=1)

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                tomar_paso_esperando(aprobacion, usuario)
                raise RuntimeError('rollback')

        self.assertEqual(AuditoriaOutbox.objects.count(), 0)


class DocumentosDescargablesBulkTest(BaseDocumentosTest):
    def _doc(self, codigo, clasificacion, propietario, **extra):
        return Documento.objects.create(
            tipo=self.tipo_formulario, nombre=f'Doc {codigo}', codigo=codigo,
            estado='activo', clasificacion=clasificacion,
            propietario=propietario, departamento=self.depto_ti, **extra,
        )

    def test_paridad_con_puede_descargar(self):
        user = self.user_trabajador
        publico = self._doc('TI-FOR-BULK-1', 'publico', self.user_jefe_ti)
        interno_propio = self._doc(
            'TI-FOR-BULK-2', 'interno', self.user_trabajador)
        interno_ajeno = self._doc(
            'TI-FOR-BULK-3', 'interno', self.user_jefe_ti)
        confidencial = self._doc(
            'TI-FOR-BULK-4', 'confidencial', self.user_trabajador)
        restringido = self._doc(
            'TI-FOR-BULK-5', 'restringido', self.user_jefe_ti)
        distribuido = self._doc(
            'TI-FOR-BULK-6', 'interno', self.user_jefe_ti)
        aprobado = self._doc(
            'TI-FOR-BULK-7', 'confidencial', self.user_jefe_ti)

        DistribucionDocumento.objects.create(
            documento=distribuido, usuario=user,
            distribuido_por=self.user_jefe_ti,
        )
        SolicitudDescarga.objects.create(
            documento=aprobado, solicitado_por=user, motivo='Prueba',
            estado='aprobada', version_aprobada=aprobado.version_actual,
            fecha_expiracion=timezone.now() + timedelta(days=1),
        )

        docs = [
            publico, interno_propio, interno_ajeno, confidencial,
            restringido, distribuido, aprobado,
        ]
        resultado = documentos_descargables(user, docs)

        for doc in docs:
            self.assertEqual(
                doc.uuid in resultado, puede_descargar(user, doc),
                f'Paridad rota para {doc.codigo}',
            )

    def test_queries_constantes_respecto_al_numero_de_documentos(self):
        docs = [
            self._doc(f'TI-FOR-BULK-Q{i:02d}', 'publico', self.user_jefe_ti)
            for i in range(20)
        ]
        user = self.user_trabajador
        # Calienta la caché de perfil para medir solo las queries del helper.
        documentos_descargables(user, docs[:1])

        with CaptureQueriesContext(connection) as cinco:
            documentos_descargables(user, docs[:5])
        with CaptureQueriesContext(connection) as veinte:
            documentos_descargables(user, docs)

        self.assertEqual(len(cinco), len(veinte))


class AuditoriaAltaPlantillaTest(BaseDocumentosTest):
    def test_alta_plantilla_marca_flag_en_outbox(self):
        Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla Auditada',
            codigo='TI-FOR-PLT-970', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )

        evento = AuditoriaOutbox.objects.filter(
            payload__accion_tipo='creacion',
            payload__recurso_codigo='TI-FOR-PLT-970',
        ).latest('id')
        self.assertTrue(evento.payload['detalles']['es_plantilla'])

    def test_documento_normal_no_se_marca_como_plantilla(self):
        Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Documento Auditado',
            codigo='TI-FOR-971', estado='activo', es_plantilla=False,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )

        evento = AuditoriaOutbox.objects.filter(
            payload__accion_tipo='creacion',
            payload__recurso_codigo='TI-FOR-971',
        ).latest('id')
        self.assertFalse(evento.payload['detalles']['es_plantilla'])


class SecuenciaCodigoTest(BaseDocumentosTest):
    def test_reserva_secuencial_plantilla(self):
        c1 = Documento.reservar_codigo_secuencial(
            self.depto_ti, 'formulario', es_plantilla=True,
        )
        c2 = Documento.reservar_codigo_secuencial(
            self.depto_ti, 'formulario', es_plantilla=True,
        )
        self.assertNotEqual(c1, c2)
        self.assertTrue(c1.endswith('-001'))
        self.assertTrue(c2.endswith('-002'))

    def test_series_normal_y_plantilla_separadas(self):
        normal = Documento.reservar_codigo_secuencial(
            self.depto_ti, 'formulario', es_plantilla=False,
        )
        plantilla = Documento.reservar_codigo_secuencial(
            self.depto_ti, 'formulario', es_plantilla=True,
        )
        self.assertNotIn('PLT', normal)
        self.assertIn('PLT', plantilla)

    def test_secuencia_global_sin_prefijo(self):
        tipo = TipoDocumento.objects.create(
            key='tipo_sin_prefijo_test', nombre='Tipo sin prefijo',
            codigo='SPT', activo=True, sin_prefijo_depto=True,
        )
        c1 = Documento.reservar_codigo_secuencial(
            self.depto_ti, tipo.key, es_plantilla=True,
        )
        c2 = Documento.reservar_codigo_secuencial(
            self.depto_th, tipo.key, es_plantilla=True,
        )
        self.assertTrue(c1.startswith('SPT-PLT-'))
        self.assertTrue(c2.startswith('SPT-PLT-'))
        self.assertNotEqual(c1, c2)


class SecuenciaCodigoConcurrenteTest(TransactionTestCase):
    def setUp(self):
        from documentos.models import Departamento, TipoDocumento
        with patch('auditoria.services.get_mongo_client', return_value=None):
            self.depto = Departamento.objects.create(
                nombre='TI Concurrente', codigo='TIC', activo=True,
            )
        self.tipo = TipoDocumento.objects.create(
            key='tipo_concurrente', nombre='Tipo concurrente',
            codigo='TPC', activo=True,
        )

    def test_reservas_concurrentes_no_duplican(self):
        def reservar(_):
            close_old_connections()
            try:
                return Documento.reservar_codigo_secuencial(
                    self.depto, 'tipo_concurrente', es_plantilla=True,
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=6) as pool:
            codigos = list(pool.map(reservar, range(12)))

        self.assertEqual(len(codigos), 12)
        self.assertEqual(len(set(codigos)), 12)
        self.assertEqual(SecuenciaCodigo.objects.count(), 1)


class DocumentoCleanPlantillaTest(BaseDocumentosTest):
    def test_documento_nuevo_sin_plantilla_falla(self):
        doc = Documento(
            tipo=self.tipo_formulario, nombre='Sin plantilla',
            codigo='TI-FOR-960', estado='borrador',
            propietario=self.user_trabajador, departamento=self.depto_ti,
        )
        with self.assertRaises(ValidationError) as ctx:
            doc.clean()
        self.assertIn('plantilla_origen', ctx.exception.message_dict)

    def test_plantilla_nueva_sin_origen_es_valida(self):
        doc = Documento(
            tipo=self.tipo_formulario, nombre='Plantilla limpia',
            codigo='TI-FOR-PLT-960', estado='borrador',
            propietario=self.user_trabajador, departamento=self.depto_ti,
            es_plantilla=True,
        )
        doc.clean()

    def test_documento_existente_sin_origen_puede_actualizarse(self):
        doc = self.doc_activo
        doc.nombre = 'Procedimiento actualizado'
        doc.clean()


class PlantillaPreviewTest(BaseDocumentosTest):
    """Modal "Ver": partial de preview de plantilla y descarga."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.plantilla = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla Preview',
            codigo='TI-FOR-PLT-020', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )
        self.crear_url = reverse('documentos:crear_documento', args=['formulario'])
        self.preview_url = reverse(
            'documentos:preview_plantilla',
            args=['formulario', self.plantilla.uuid],
        )

    def _preview(self, plantilla=None, tipo='formulario'):
        return self.client.get(reverse(
            'documentos:preview_plantilla',
            args=[tipo, plantilla or self.plantilla.uuid],
        ))

    def test_crear_documento_tiene_modal_y_no_abre_pestana_nueva(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self.crear_url)

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="modal-plantilla-preview"')
        self.assertContains(resp, 'id="btn-ver-plantilla"')
        self.assertContains(resp, 'tabindex="-1"')
        self.assertContains(resp, 'aria-modal="true"')
        self.assertNotContains(resp, 'target="_blank"')
        self.assertNotIn('plantilla_preview', resp.context)

    def test_crear_documento_ya_no_procesa_query_param(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(f'{self.crear_url}?plantilla={self.plantilla.uuid}')

        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('plantilla_preview', resp.context)
        self.assertNotContains(resp, 'id="plantilla-preview"')

    def test_preview_valido_devuelve_partial_y_descarga(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._preview()

        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'documentos/_plantilla_preview.html')
        self.assertEqual(resp.context['plantilla_preview'], self.plantilla)
        self.assertContains(resp, 'Vista previa: Plantilla Preview')
        self.assertContains(resp, reverse(
            'documentos:descargar_archivo',
            args=['formulario', self.plantilla.uuid],
        ))

    def test_preview_plantilla_de_otro_tipo_404(self):
        otra = Documento.objects.create(
            tipo=self.tipo_procedimiento, nombre='Plantilla Procedimiento',
            codigo='TI-PRG-PLT-020', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._preview(otra.uuid)

        self.assertEqual(resp.status_code, 404)

    def test_preview_plantilla_no_visible_404(self):
        self.client.login(username='trab01', password='testpass123')
        resp = self._preview()

        self.assertEqual(resp.status_code, 404)

    def test_preview_uuid_inexistente_404(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._preview('00000000-0000-0000-0000-000000000000')

        self.assertEqual(resp.status_code, 404)

    def test_preview_con_pdf_muestra_iframe_del_visor(self):
        plantilla_pdf = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla PDF',
            codigo='TI-FOR-PLT-021', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
            archivo=SimpleUploadedFile('plantilla-preview.pdf', b'%PDF-1.4\n%%EOF'),
        )
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self._preview(plantilla_pdf.uuid)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['plantilla_tiene_archivo'])
        self.assertContains(resp, reverse(
            'documentos:ver_archivo',
            args=['formulario', plantilla_pdf.uuid],
        ))
        self.assertContains(resp, 'plantilla-preview__frame')

    def test_preview_refleja_solicitud_y_pendiente(self):
        plantilla = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla Asignada',
            codigo='TI-FOR-PLT-022', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )
        AsignacionDocumento.objects.create(
            documento=plantilla, coordinador=self.user_trabajador,
            jefe_asignador=self.user_jefe_ti, activa=True, puede_leer=True,
        )
        self.client.login(username='trab01', password='testpass123')

        resp = self._preview(plantilla.uuid)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['plantilla_descargable'])
        self.assertFalse(resp.context['plantilla_pendiente'])
        self.assertContains(resp, reverse(
            'documentos:solicitar_descarga',
            args=['formulario', plantilla.uuid],
        ))

        SolicitudDescarga.objects.create(
            documento=plantilla, solicitado_por=self.user_trabajador,
            motivo='Necesito la plantilla', estado='pendiente',
        )
        resp = self._preview(plantilla.uuid)
        self.assertTrue(resp.context['plantilla_pendiente'])
        self.assertContains(resp, 'Solicitud pendiente')

    def test_meta_incluye_preview_url(self):
        self.client.login(username='jefe_ti', password='testpass123')
        resp = self.client.get(self.crear_url)
        meta = next(
            m for m in resp.context['plantillas_meta']
            if m['id'] == str(self.plantilla.uuid)
        )
        self.assertEqual(meta['preview_url'], self.preview_url)


class MisDocumentosFiltroTest(BaseDocumentosTest):
    """T9: filtro Todos / Documentos / Plantillas en Mis Documentos."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.doc = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Documento Filtro',
            codigo='TI-FOR-900', estado='activo',
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )
        self.plantilla = Documento.objects.create(
            tipo=self.tipo_formulario, nombre='Plantilla Filtro',
            codigo='TI-FOR-PLT-900', estado='activo', es_plantilla=True,
            propietario=self.user_jefe_ti, departamento=self.depto_ti,
        )
        self.url = reverse('documentos:mis_documentos')

    def _nombres(self, response):
        return {
            doc.nombre
            for grupo in response.context['tipos_documento']
            for doc in grupo['documentos']
        }

    def test_filtro_todos_incluye_ambos(self):
        self.client.login(username='jefe_ti', password='testpass123')
        nombres = self._nombres(self.client.get(self.url))

        self.assertIn(self.doc.nombre, nombres)
        self.assertIn(self.plantilla.nombre, nombres)

    def test_filtro_documentos_excluye_plantillas(self):
        self.client.login(username='jefe_ti', password='testpass123')
        nombres = self._nombres(self.client.get(self.url, {'registro': 'documentos'}))

        self.assertIn(self.doc.nombre, nombres)
        self.assertNotIn(self.plantilla.nombre, nombres)

    def test_filtro_plantillas_excluye_documentos(self):
        self.client.login(username='jefe_ti', password='testpass123')
        nombres = self._nombres(self.client.get(self.url, {'registro': 'plantillas'}))

        self.assertIn(self.plantilla.nombre, nombres)
        self.assertNotIn(self.doc.nombre, nombres)
