from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from unittest.mock import patch, MagicMock
from datetime import date, timedelta
from documentos.models import (
    Departamento, SubArea, TipoDocumento, Documento,
    VersionDocumento, AsignacionDocumento, SolicitudDescarga,
    AprobacionDescarga, SolicitudAccesoDocumento, PlanillaCambios,
    AprobacionPlanilla, DistribucionDocumento, SolicitudAprobacionDocumento,
    RenovacionVencimiento,
)
from autenticacion.models import PerfilUsuario, Notificacion

# Único punto de acceso a MongoDB: todos los signals de auditoría terminan
# llamando a get_mongo_client() vía lookup global en auditoria.services.
MONGO_PATCH_TARGET = 'auditoria.services.get_mongo_client'


class BaseDocumentosTest(TestCase):
    """Fixtures compartidos para todas las suites de tests.

    Los fixtures se crean UNA vez por clase (setUpTestData) para que la
    suite sea rápida, y en cada setUp se refrescan desde la DB para
    deshacer mutaciones en memoria de tests anteriores.

    El único parche de MongoDB se aplica sobre get_mongo_client(), el punto
    de estrangulamiento común de todos los signals de auditoría.
    """

    @classmethod
    def setUpTestData(cls):
        with patch(MONGO_PATCH_TARGET, return_value=MagicMock()):
            cls._create_fixtures()

    @classmethod
    def _create_fixtures(cls):
        cls._fixture_objs = []

        # Departamentos
        cls.depto_ti = Departamento.objects.create(
            nombre='Tecnología de la Información', codigo='TI', activo=True
        )
        cls.depto_gg = Departamento.objects.create(
            nombre='Gerencia General', codigo='GG', activo=True
        )
        cls.depto_th = Departamento.objects.create(
            nombre='Talento Humano', codigo='TH', activo=True
        )
        cls._fixture_objs += [cls.depto_ti, cls.depto_gg, cls.depto_th]

        # Sub-área
        cls.subarea_soporte = SubArea.objects.create(
            nombre='Soporte Técnico', departamento=cls.depto_ti, activo=True
        )
        cls._fixture_objs.append(cls.subarea_soporte)

        # Tipos de documento
        cls.tipo_procedimiento = TipoDocumento.objects.create(
            key='procedimientos_generales', nombre='Procedimientos Generales', codigo='PRG', activo=True
        )
        cls.tipo_formulario = TipoDocumento.objects.create(
            key='formulario', nombre='Formularios', codigo='FOR', activo=True
        )
        cls._fixture_objs += [cls.tipo_procedimiento, cls.tipo_formulario]

        # Usuarios (PASSWORD_HASHERS en test_settings usa MD5: hashing rápido)
        from django.contrib.auth.hashers import make_password

        def crear_usuario(username, first, last, rol, grupo, depto):
            user = User.objects.create(
                username=username, password=make_password('testpass123'),
                first_name=first, last_name=last,
            )
            perfil = PerfilUsuario.objects.create(
                usuario=user, rol=rol, grupo_ldap=grupo, departamento=depto
            )
            cls._fixture_objs += [user, perfil]
            return user, perfil

        cls.user_admin, _ = crear_usuario(
            'admin01', 'Admin', 'Test', 'administrador',
            'doc_administrador', cls.depto_gg,
        )
        cls.user_jefe_ti, cls.perfil_jefe_ti = crear_usuario(
            'jefe_ti', 'Jefe', 'TI', 'jefe_departamento',
            'doc_jefes', cls.depto_ti,
        )
        cls.user_jefe_gg, _ = crear_usuario(
            'jefe_gg', 'Jefe', 'GG', 'jefe_departamento',
            'doc_jefes', cls.depto_gg,
        )
        cls.user_jefe_th, _ = crear_usuario(
            'jefe_th', 'Jefe', 'TH', 'jefe_departamento',
            'doc_jefes', cls.depto_th,
        )
        cls.user_coordinador, _ = crear_usuario(
            'coord01', 'Coord', 'Test', 'coordinador',
            'doc_coordinador', cls.depto_ti,
        )
        cls.user_trabajador, _ = crear_usuario(
            'trab01', 'Trab', 'Test', 'trabajador',
            'doc_trabajador', cls.depto_ti,
        )
        cls.user_trabajador_otro, _ = crear_usuario(
            'trab02', 'Trab2', 'Test', 'trabajador',
            'doc_trabajador', cls.depto_th,
        )

        # Asignar jefes (el signal sync_departamento_jefe_from_profile ya
        # los asigna al crear el perfil, esto garantiza el estado final)
        cls.depto_ti.jefe = cls.user_jefe_ti
        cls.depto_ti.save(update_fields=['jefe'])
        cls.depto_gg.jefe = cls.user_jefe_gg
        cls.depto_gg.save(update_fields=['jefe'])
        cls.depto_th.jefe = cls.user_jefe_th
        cls.depto_th.save(update_fields=['jefe'])

        # Documentos
        cls.doc_activo = Documento.objects.create(
            tipo=cls.tipo_procedimiento, nombre='Procedimiento de Prueba',
            codigo='TI-PRG-001', estado='activo', propietario=cls.user_jefe_ti,
            departamento=cls.depto_ti, clasificacion='interno', version_actual='1.0',
            fecha_vencimiento=date.today() + timedelta(days=30),
        )
        cls.doc_borrador = Documento.objects.create(
            tipo=cls.tipo_formulario, nombre='Formulario Borrador',
            codigo='TI-FOR-001', estado='borrador', propietario=cls.user_trabajador,
            departamento=cls.depto_ti, version_actual='1.0',
        )
        cls.doc_en_revision = Documento.objects.create(
            tipo=cls.tipo_procedimiento, nombre='Procedimiento En Revision',
            codigo='TI-PRG-002', estado='en_revision', propietario=cls.user_trabajador,
            departamento=cls.depto_ti, version_actual='1.0',
        )
        cls.doc_obsoleto = Documento.objects.create(
            tipo=cls.tipo_formulario, nombre='Formulario Obsoleto',
            codigo='TI-FOR-002', estado='obsoleto', propietario=cls.user_jefe_ti,
            departamento=cls.depto_ti, version_actual='1.0',
        )
        cls.doc_publico = Documento.objects.create(
            tipo=cls.tipo_formulario, nombre='Formulario Publico',
            codigo='TI-FOR-003', estado='activo', propietario=cls.user_jefe_ti,
            departamento=cls.depto_ti, clasificacion='publico', version_actual='1.0',
        )
        cls.doc_th = Documento.objects.create(
            tipo=cls.tipo_formulario, nombre='Formulario TH',
            codigo='TH-FOR-001', estado='activo', propietario=cls.user_trabajador_otro,
            departamento=cls.depto_th, version_actual='1.0',
            archivo=SimpleUploadedFile('test_th.pdf', b'Th content'),
            tipo_archivo='pdf',
        )
        cls.doc_vencido = Documento.objects.create(
            tipo=cls.tipo_procedimiento, nombre='Procedimiento Vencido',
            codigo='TI-PRG-003', estado='activo', propietario=cls.user_jefe_ti,
            departamento=cls.depto_ti, fecha_vencimiento=date.today() - timedelta(days=1),
            version_actual='1.0',
        )
        cls.doc_por_vencer = Documento.objects.create(
            tipo=cls.tipo_procedimiento, nombre='Procedimiento Por Vencer',
            codigo='TI-PRG-004', estado='activo', propietario=cls.user_jefe_ti,
            departamento=cls.depto_ti,
            fecha_vencimiento=date.today() + timedelta(days=5),
            aviso_vencimiento_enviado=False, version_actual='1.0',
        )
        cls._fixture_objs += [
            cls.doc_activo, cls.doc_borrador, cls.doc_en_revision,
            cls.doc_obsoleto, cls.doc_publico, cls.doc_th,
            cls.doc_vencido, cls.doc_por_vencer,
        ]

    def setUp(self):
        # La caché (LocMem en tests) es global al proceso y no se limpia sola
        # entre tests: sin esto, los catálogos cacheados (tipos/periodos/
        # departamentos) filtrarían instancias de un test a otro.
        from django.core.cache import cache
        cache.clear()
        # Cerrar el circuit breaker de auditoría: estado de módulo/caché que de
        # otro modo filtraría entre tests tras uno que simula Mongo caído.
        from auditoria.services import _cerrar_circuito_mongo
        _cerrar_circuito_mongo()
        self._mongo_patch = patch(MONGO_PATCH_TARGET, return_value=MagicMock())
        self._mongo_patch.start()
        self._refresh_fixtures()

    def _refresh_fixtures(self):
        for obj in type(self)._fixture_objs:
            try:
                obj.refresh_from_db()
            except ObjectDoesNotExist:
                fresh = type(obj).objects.get(pk=obj.pk)
                obj.__dict__ = fresh.__dict__
                obj._state = fresh._state

    def tearDown(self):
        self._mongo_patch.stop()
