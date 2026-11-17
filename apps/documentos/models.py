import logging
import os
import time
import uuid
from datetime import datetime
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, models, transaction
from django.db.models import Max
from django.contrib.auth.models import User
from django.utils import timezone

from .validators import validar_archivo_acuse, validar_archivo_documento

logger = logging.getLogger(__name__)


#: Extensión permitida → valor de `Documento.tipo_archivo`.
TIPO_ARCHIVO_POR_EXTENSION = {
    '.pdf': 'pdf',
    '.doc': 'doc',
    '.docx': 'docx',
    '.xls': 'xls',
    '.xlsx': 'xlsx',
    '.vsdx': 'vsdx',
}


def tipo_archivo_desde_nombre(nombre, por_defecto='pdf'):
    """Infiere `tipo_archivo` a partir de la extensión del archivo.

    Fuente única de verdad: evita que un `.xlsx` quede guardado como `pdf`
    (lo que mostraría el visor PDF con un archivo que no lo es).
    """
    ext = os.path.splitext(nombre or '')[1].lower()
    return TIPO_ARCHIVO_POR_EXTENSION.get(ext, por_defecto)


def generar_ruta_archivo(instance, filename):
    """Genera ruta ISO: documentos/{depto}/{username}/{codigo}_v{ver}_{fecha}.{ext}"""
    depto = getattr(instance, 'departamento', None)
    depto_codigo = depto.codigo if depto else 'SIN'
    username = instance.propietario.username
    extension = os.path.splitext(filename)[1] if filename else '.pdf'
    fecha = timezone.now().strftime('%Y%m%d')
    return f'documentos/{depto_codigo}/{username}/{instance.codigo}_v{instance.version_actual}_{fecha}{extension}'


def generar_ruta_version(instance, filename):
    """Genera ruta ISO para versiones: misma carpeta que el documento original"""
    try:
        doc = instance.documento
    except Exception:
        return f'versiones/{filename}'
    depto = getattr(doc, 'departamento', None)
    depto_codigo = depto.codigo if depto else 'SIN'
    username = doc.propietario.username
    extension = os.path.splitext(filename)[1] if filename else '.pdf'
    fecha = (instance.fecha_creacion or timezone.now()).strftime('%Y%m%d')
    return f'documentos/{depto_codigo}/{username}/{doc.codigo}_v{instance.numero_version}_{fecha}{extension}'


def generar_ruta_registro(instance, filename):
    """Ruta de un registro: el código del formato se repite, el uuid no."""
    extension = os.path.splitext(filename)[1] if filename else '.pdf'
    codigo = (instance.formato_codigo or 'registro').replace('/', '-')
    return f'registros/{codigo}/{instance.uuid.hex}{extension}'


# =========================================================================
# MODELOS DE SOPORTE (se mantienen)
# =========================================================================

class Departamento(models.Model):
    CODIGOS_DEPTO = [
        ('SSS', 'Servicio de Seguridad y Salud Laboral'),
        ('CHS', 'Comité de Seguridad y Salud Laboral'),
        ('AI', 'Auditoría Interna'),
        ('CJ', 'Consultoría Jurídica'),
        ('GG', 'Gerencia General'),
        ('AF', 'Administración y Finanzas'),
        ('CYT', 'Contabilidad y Tributos'),
        ('ACC', 'Atención al Cliente Crédito y Cobranza'),
        ('AD', 'Admisión'),
        ('CO', 'Cobranza'),
        ('FAC', 'Facturación'),
        ('SOD', 'Servicio de Odontología'),
        ('TH', 'Talento Humano'),
        ('SG', 'Servicios Generales'),
        ('LA', 'Lavandería'),
        ('CA', 'Compras y Almacén'),
        ('AL', 'Almacén'),
        ('COM', 'Compras'),
        ('TI', 'Tecnología de la Información'),
        ('CM', 'Coordinación Médica'),
        ('SMr', 'Servicios Médicos Residentes'),
        ('SMe', 'Servicios Médicos de Enfermería'),
        ('SMh', 'Servicios Médicos de Hospitalización'),
        ('SMg', 'Servicios Médicos de Emergencia'),
        ('SMq', 'Servicios Médicos del Área Quirúrgica'),
        ('SMu', 'Servicios Médicos Unidad de Cuidados Intensivos'),
        ('SMt', 'Servicios Médicos Terapia Respiratoria'),
        ('LAB', 'Laboratorio'),
        ('SIM', 'Servicios de Imagenología'),
        ('SFR', 'Servicio de Fisiatría y Rehabilitación'),
        ('SFh', 'Servicio de Farmacia Hospitalaria'),
        ('SNd', 'Servicio de Nutrición y Dietética'),
        ('MIR', 'Mantenimiento de Infraestructura y Resguardo'),
        ('PT', 'Policlínica Táchira, C.A'),
        ('PTH', 'Policlínica Táchira Hospitalización, C.A'),
        ('FP', 'Farmacia Policlínica, C.A'),
        ('FS', 'Fuente de Soda Policlínica Táchira, C.A'),
        ('SGC', 'Sistema de Gestión de la Calidad'),
    ]

    nombre = models.CharField(max_length=150)
    codigo = models.CharField(max_length=4, unique=True)
    jefe = models.OneToOneField(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='jefe_departamento',
        verbose_name='Jefe de Departamento'
    )
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Departamento'
        verbose_name_plural = 'Departamentos'
        ordering = ['nombre']

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class SubArea(models.Model):
    nombre = models.CharField(max_length=150, unique=True, verbose_name='Nombre del Área en AD')
    departamento = models.ForeignKey(
        Departamento, on_delete=models.CASCADE,
        related_name='sub_areas', verbose_name='Departamento Padre'
    )
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Sub-Área'
        verbose_name_plural = 'Sub-Áreas'
        ordering = ['nombre']

    def __str__(self):
        return f"{self.nombre} → {self.departamento.nombre}"


class DelegacionJefatura(models.Model):
    """Delegación temporal de la jefatura de un departamento (vacaciones).

    Permite que un miembro del mismo departamento (normalmente el
    coordinador de área) ejerza los poderes operativos del jefe mientras
    éste está ausente, dentro de una vigencia ``fecha_inicio``/``fecha_fin``.
    No delega la configuración global del sistema.
    """

    jefe = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='delegaciones_otorgadas',
        verbose_name='Jefe que delega',
    )
    delegado = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='delegaciones_recibidas',
        verbose_name='Delegado (ejerce como jefe)',
    )
    departamento = models.ForeignKey(
        Departamento, on_delete=models.CASCADE,
        related_name='delegaciones_jefatura', verbose_name='Departamento',
    )
    motivo = models.TextField(
        blank=True, default='', verbose_name='Motivo',
        help_text='Motivo de la ausencia (vacaciones, permisos, etc.).',
    )
    fecha_inicio = models.DateField(verbose_name='Fecha de inicio')
    fecha_fin = models.DateField(verbose_name='Fecha de fin')
    activa = models.BooleanField(default=True, verbose_name='Activa')
    creada_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='delegaciones_creadas', verbose_name='Creada por',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    motivo_revocacion = models.TextField(
        blank=True, default='', verbose_name='Motivo de revocación',
    )
    fecha_revocacion = models.DateTimeField(
        null=True, blank=True, verbose_name='Fecha de revocación',
    )
    revocada_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='delegaciones_revocadas', verbose_name='Revocada por',
    )

    class Meta:
        verbose_name = 'Delegación de Jefatura'
        verbose_name_plural = 'Delegaciones de Jefatura'
        ordering = ['-fecha_creacion']
        indexes = [
            models.Index(fields=['delegado', 'activa'], name='deleg_delegado_activa_idx'),
            models.Index(fields=['departamento', 'activa'], name='deleg_depto_activa_idx'),
        ]

    def __str__(self):
        return (
            f"{self.jefe.username} → {self.delegado.username} "
            f"({self.departamento.codigo})"
        )

    @property
    def vigente(self):
        """True si está activa y la vigencia incluye hoy."""
        if not self.activa:
            return False
        hoy = timezone.now().date()
        return self.fecha_inicio <= hoy <= self.fecha_fin


class Periodo(models.Model):
    """Períodos estándar para vencimiento y retención de documentos."""
    nombre = models.CharField(max_length=50, unique=True, verbose_name='Nombre del Período')
    meses = models.PositiveIntegerField(verbose_name='Duración en Meses')
    activo = models.BooleanField(default=True, verbose_name='Activo')
    orden = models.PositiveIntegerField(default=0, verbose_name='Orden')

    class Meta:
        verbose_name = 'Período'
        verbose_name_plural = 'Períodos'
        ordering = ['orden', 'meses']

    def __str__(self):
        return self.nombre


class TipoDocumento(models.Model):
    key = models.SlugField(max_length=40, unique=True, verbose_name='Clave')
    nombre = models.CharField(max_length=100, verbose_name='Nombre del Tipo')
    codigo = models.CharField(max_length=20, unique=True, verbose_name='Código del Tipo')
    descripcion = models.TextField(blank=True, default='', verbose_name='Descripción')
    activo = models.BooleanField(default=True, verbose_name='Activo')
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)
    periodo_vigencia = models.ForeignKey(
        'Periodo', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tipos_vigencia',
        verbose_name='Período de Vigencia por Defecto'
    )
    periodo_retencion = models.ForeignKey(
        'Periodo', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='tipos_retencion',
        verbose_name='Período de Retención por Defecto'
    )
    requiere_vigencia = models.BooleanField(
        default=False, verbose_name='Vigencia obligatoria',
        help_text=(
            'Si está activo, al crear/editar este tipo se exige un período de '
            'vigencia y no se permite dejar el documento sin vigencia.'
        ),
    )

    # Secciones opcionales del formulario (configurable por el admin)
    usa_departamentos_afectados = models.BooleanField(
        default=True, verbose_name='Usa Departamentos Afectados',
        help_text='Mostrar la sección de departamentos afectados al crear/editar este tipo.'
    )
    usa_responsables = models.BooleanField(
        default=True, verbose_name='Usa Responsables (áreas)',
        help_text='Mostrar la sección de áreas responsables al crear/editar este tipo.'
    )
    usa_participantes = models.BooleanField(
        default=True, verbose_name='Usa Participantes',
        help_text='Mostrar la sección de participantes al crear/editar este tipo.'
    )
    usa_documentos_relacionados = models.BooleanField(
        default=True, verbose_name='Usa Documentos Relacionados',
        help_text='Mostrar la sección de documentos relacionados al crear/editar este tipo.'
    )

    # Presentación y comportamiento
    icono = models.CharField(
        max_length=40, default='file', verbose_name='Ícono (FontAwesome)',
        help_text='Nombre del ícono FontAwesome sin el prefijo "fa-". Ej: book, file-lines.'
    )
    color = models.CharField(
        max_length=20, default='primary', verbose_name='Color',
        help_text='Color semántico: primary, success, warning, danger o info.'
    )
    solo_jefes = models.BooleanField(
        default=False, verbose_name='Solo jefes',
        help_text='Solo los jefes de departamento pueden crear este tipo.'
    )
    sin_prefijo_depto = models.BooleanField(
        default=False, verbose_name='Sin prefijo de departamento',
        help_text='El código no lleva prefijo de departamento y numera de forma global (ej. DPR-001).'
    )

    class Meta:
        verbose_name = 'Tipo de Documento'
        verbose_name_plural = 'Tipos de Documento'
        ordering = ['nombre']

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


# =========================================================================
# MODELO UNIFICADO: DOCUMENTO
# =========================================================================

class SecuenciaCodigo(models.Model):
    """Contador atómico por serie de códigos (depto + tipo + plantilla).

    Evita el patrón ``Max(codigo)+exists()``, que bajo concurrencia asigna el
    mismo consecutivo a dos altas. La clave es un texto portable:
    ``TI:formulario:1`` (plantilla) o ``GLOBAL:descripcion_procesos:0``.
    """

    clave = models.CharField(max_length=120, unique=True, verbose_name='Clave de serie')
    ultimo = models.PositiveIntegerField(default=0, verbose_name='Último consecutivo')
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Secuencia de código'
        verbose_name_plural = 'Secuencias de código'

    def __str__(self):
        return f'{self.clave} → {self.ultimo}'


class Documento(models.Model):
    """
    Modelo unificado para todos los tipos de documento ISO.
    La relación jerárquica es flexible: cualquier documento puede estar
    relacionado con cualquier otro (M2M self-referential).
    """

    TIPO_ARCHIVO_CHOICES = [
        ('pdf', 'PDF'), ('doc', 'DOC'), ('docx', 'DOCX'),
        ('xls', 'XLS'), ('xlsx', 'XLSX'), ('vsdx', 'VSDX'),
    ]

    ESTADO_CHOICES = [
        ('borrador', 'Borrador'),
        ('en_revision', 'En Revisión'),
        ('activo', 'Activo'),
        ('obsoleto', 'Obsoleto'),
    ]

    # Identificación
    tipo = models.ForeignKey(
        'TipoDocumento', on_delete=models.PROTECT,
        db_column='tipo', to_field='key', related_name='documentos',
        null=True, blank=True,
        verbose_name='Tipo de Documento'
    )
    nombre = models.CharField(max_length=200)
    codigo = models.CharField(max_length=50, unique=True, verbose_name='Código del Documento')
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID')
    descripcion = models.TextField(blank=True, default='')
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='borrador')
    version_actual = models.CharField(max_length=20, default='1')

    # Seguridad
    CLASIFICACION_CHOICES = [
        ('publico', 'Público'),
        ('interno', 'Interno'),
        ('confidencial', 'Confidencial'),
        ('restringido', 'Restringido'),
    ]

    clasificacion = models.CharField(
        max_length=20,
        choices=CLASIFICACION_CHOICES,
        default='interno',
        verbose_name='Clasificación',
        help_text=(
            'Clasificación de seguridad del documento. "Público" es visible '
            'para todo el personal de la Policlínica Táchira (no para el '
            'público general); los demás niveles restringen la consulta y '
            'la distribución.'
        )
    )
    # Discriminador de Plantilla (documento en blanco subido para que otros
    # lo descarguen y llenen). NO es entidad nueva: es un Documento normal.
    es_plantilla = models.BooleanField(
        default=False, db_index=True, verbose_name='Es plantilla'
    )
    plantilla_origen = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='documentos_generados',
        verbose_name='Plantilla de origen',
    )
    fecha_retencion = models.DateField(null=True, blank=True, verbose_name='Fecha de Retención')
    fecha_destruccion = models.DateField(null=True, blank=True, verbose_name='Fecha de Destrucción')
    fecha_vencimiento = models.DateField(null=True, blank=True, verbose_name='Fecha de Vencimiento')
    aviso_vencimiento_enviado = models.BooleanField(
        default=False, verbose_name='Aviso de vencimiento enviado'
    )
    hash_integridad = models.CharField(
        max_length=64, blank=True, default='',
        verbose_name='Hash de Integridad (SHA-256)'
    )

    # Archivos
    archivo = models.FileField(
        upload_to=generar_ruta_archivo, blank=True, null=True,
        verbose_name='Archivo del Documento',
        validators=[validar_archivo_documento],
    )
    archivo_pdf = models.FileField(
        upload_to=generar_ruta_archivo, blank=True, null=True,
        verbose_name='Archivo PDF (vista previa)',
        help_text='PDF generado automáticamente para visualización'
    )
    tipo_archivo = models.CharField(
        max_length=10, choices=TIPO_ARCHIVO_CHOICES, default='pdf'
    )

    # Relaciones
    propietario = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='documentos_creados'
    )
    departamento = models.ForeignKey(
        Departamento, on_delete=models.PROTECT, related_name='documentos',
        null=True, blank=True
    )

    # Relación jerárquica flexible: cualquier documento puede apuntar a cualquier otro
    documentos_relacionados = models.ManyToManyField(
        'self', symmetrical=False, blank=True,
        related_name='documentos_relacionados_inversos',
        verbose_name='Documentos Relacionados'
    )

    # Multi-departamento
    departamentos_afectados = models.ManyToManyField(
        Departamento, blank=True, related_name='documentos_afectados'
    )
    afecta_todos = models.BooleanField(
        default=False,
        verbose_name='Afecta a todos los departamentos',
        help_text='Si está marcado, el documento aplica a todas las áreas '
                  '(incluye automáticamente los departamentos futuros).'
    )

    # Participantes y Responsables
    participantes = models.JSONField(
        default=list, blank=True, verbose_name='Participantes',
        help_text='Tags libres: departamentos, clientes, personas, etc.'
    )
    responsables = models.ManyToManyField(
        Departamento, blank=True, related_name='documentos_responsables',
        verbose_name='Responsables (áreas)'
    )

    # ISO 9001:2015 - Cláusula 7.5
    revision = models.CharField(max_length=10, default='0', verbose_name='Número de Revisión')
    aprobado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_aprobados', verbose_name='Aprobado por'
    )
    fecha_aprobacion = models.DateTimeField(null=True, blank=True, verbose_name='Fecha de Aprobación')
    fecha_vigencia = models.DateTimeField(null=True, blank=True, verbose_name='Fecha de Vigencia')
    proxima_revision = models.DateField(null=True, blank=True, verbose_name='Próxima Revisión Programada')
    historial_cambios = models.TextField(blank=True, default='', verbose_name='Historial de Cambios')

    # Períodos
    periodo_vigencia = models.ForeignKey(
        'Periodo', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_vigencia',
        verbose_name='Período de Vigencia'
    )
    periodo_retencion = models.ForeignKey(
        'Periodo', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_retencion',
        verbose_name='Período de Retención'
    )

    # Resumen con IA (Google AI Studio / Gemini)
    resumen_ia = models.JSONField(
        null=True, blank=True, verbose_name='Resumen IA',
        help_text='Resumen estructurado generado por IA'
    )
    resumen_ia_texto = models.TextField(
        blank=True, default='', verbose_name='Resumen IA (texto de respaldo)'
    )
    resumen_ia_estado = models.CharField(
        max_length=20, default='', blank=True,
        choices=[
            ('', 'Sin generar'),
            ('pendiente', 'Pendiente'),
            ('procesando', 'Procesando'),
            ('listo', 'Listo'),
            ('error', 'Error'),
        ],
        verbose_name='Estado del resumen IA',
    )
    resumen_ia_fecha = models.DateTimeField(null=True, blank=True, verbose_name='Fecha del resumen IA')
    resumen_ia_modelo = models.CharField(max_length=50, blank=True, default='', verbose_name='Modelo IA')
    resumen_ia_version = models.CharField(
        max_length=20, blank=True, default='', verbose_name='Versión del documento resumida'
    )
    resumen_ia_error = models.TextField(blank=True, default='', verbose_name='Error del resumen IA')
    resumen_ia_solicitado = models.DateTimeField(
        null=True, blank=True, verbose_name='IA solicitada',
        help_text='Cuándo se encoló la generación; permite detectar resúmenes colgados.',
    )

    # Timestamps
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Documentos'
        ordering = ['-fecha_modificacion']
        indexes = [
            models.Index(fields=['estado'], name='doc_estado_idx'),
            models.Index(fields=['clasificacion'], name='doc_clasif_idx'),
            models.Index(fields=['fecha_vencimiento'], name='doc_venc_idx'),
            models.Index(fields=['departamento', 'estado'], name='doc_depto_estado_idx'),
            models.Index(fields=['estado', '-fecha_modificacion'], name='doc_estado_fmod_idx'),
        ]

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

    def calcular_hash(self):
        import hashlib
        import json
        datos = {
            'codigo': self.codigo,
            'nombre': self.nombre,
            'tipo': self.tipo_id,
            'version': self.version_actual,
            'estado': self.estado,
        }
        datos_str = json.dumps(datos, sort_keys=True, default=str)
        return hashlib.sha256(datos_str.encode()).hexdigest()

    def proxima_version(self):
        try:
            return str(int(float(self.version_actual)) + 1)
        except (ValueError, TypeError):
            return '1'

    @classmethod
    def es_sin_prefijo_depto(cls, tipo):
        """True si el tipo genera código sin prefijo de departamento (ej. DPR)."""
        return TipoDocumento.objects.filter(
            key=tipo, sin_prefijo_depto=True
        ).exists()

    @classmethod
    def construir_codigo(cls, departamento, tipo, secuencia, es_plantilla=False):
        """Construye el código según el tipo.

        - Normal:      {DEPTO}-{TIPO}-{SEC}  (ej. TI-PRG-001)
        - Plantilla:   {DEPTO}-{TIPO}-PLT-{SEC} (ej. TI-DPR-PLT-001)
        - Sin prefijo: {TIPO}-{SEC}          (ej. DPR-001)
        - Plantilla sin prefijo: {TIPO}-PLT-{SEC} (ej. DPR-PLT-001)
        """
        tipo_obj = TipoDocumento.objects.filter(key=tipo).first()
        tipo_codigo = tipo_obj.codigo if tipo_obj else tipo
        marca = '-PLT' if es_plantilla else ''
        if cls.es_sin_prefijo_depto(tipo):
            return f"{tipo_codigo}{marca}-{secuencia}"
        return f"{departamento.codigo}-{tipo_codigo}{marca}-{secuencia}"

    @classmethod
    def generar_codigo_secuencial(cls, departamento, tipo, es_plantilla=False):
        """Genera el siguiente código secuencial.

        Args:
            departamento: Instancia de Departamento (None si el tipo no lo usa).
            tipo: Key del tipo de documento (ej: 'formulario', 'descripcion_procesos')
            es_plantilla: True si se genera código de plantilla; la serie es
                separada de la de documentos normales.

        Los tipos con `sin_prefijo_depto` numeran de forma GLOBAL (todos los
        departamentos comparten la secuencia): DPR-001, DPR-002, ...
        """
        sin_prefijo = cls.es_sin_prefijo_depto(tipo)

        qs = cls.objects.filter(tipo__key=tipo, es_plantilla=es_plantilla)
        if not sin_prefijo and departamento is not None:
            qs = qs.filter(departamento=departamento)
        max_codigo = qs.order_by('-codigo').values_list('codigo', flat=True).first()

        num = 1
        if max_codigo:
            partes = max_codigo.split('-')
            try:
                num = int(partes[-1]) + 1
            except (ValueError, IndexError):
                num = 1

        return cls.construir_codigo(departamento, tipo, f"{num:03d}",
                                    es_plantilla=es_plantilla)

    @classmethod
    def _clave_secuencia(cls, departamento, tipo, es_plantilla):
        depto = 'GLOBAL' if departamento is None or cls.es_sin_prefijo_depto(tipo) else departamento.pk
        return f"{depto}:{tipo}:{1 if es_plantilla else 0}"

    @classmethod
    def _max_secuencia(cls, departamento, tipo, es_plantilla):
        qs = cls.objects.filter(tipo__key=tipo, es_plantilla=es_plantilla)
        if not cls.es_sin_prefijo_depto(tipo) and departamento is not None:
            qs = qs.filter(departamento=departamento)
        max_codigo = qs.order_by('-codigo').values_list('codigo', flat=True).first()
        if not max_codigo:
            return 0
        try:
            return int(max_codigo.rsplit('-', 1)[-1])
        except (ValueError, IndexError):
            return 0

    @classmethod
    def reservar_codigo_secuencial(cls, departamento, tipo, es_plantilla=False):
        """Reserva el siguiente código de forma atómica.

        La fila de ``SecuenciaCodigo`` se bloquea con ``select_for_update`` y
        se incrementa dentro de la transacción del alta. Si la transacción se
        revierte, el incremento también se revierte (sin huecos por fallos).
        """
        clave = None
        for intento in range(8):
            try:
                # La clave consulta TipoDocumento (es_sin_prefijo_depto) y
                # también puede chocar con el lock de SQLite bajo concurrencia:
                # se calcula dentro del reintento.
                clave = cls._clave_secuencia(departamento, tipo, es_plantilla)
                with transaction.atomic():
                    secuencia = (
                        SecuenciaCodigo.objects.select_for_update()
                        .filter(clave=clave).first()
                    )
                    if secuencia is None:
                        inicial = cls._max_secuencia(
                            departamento, tipo, es_plantilla,
                        )
                        try:
                            with transaction.atomic():
                                secuencia = SecuenciaCodigo.objects.create(
                                    clave=clave, ultimo=inicial,
                                )
                        except IntegrityError:
                            continue
                    secuencia.ultimo += 1
                    secuencia.save(update_fields=['ultimo', 'actualizado_en'])
                    numero = secuencia.ultimo
                return cls.construir_codigo(
                    departamento, tipo, f'{numero:03d}',
                    es_plantilla=es_plantilla,
                )
            except OperationalError as exc:
                if 'locked' not in str(exc).lower() or intento >= 7:
                    raise
                time.sleep(0.05 * (intento + 1))
        return None

    def clean(self):
        """Valida la integridad de `plantilla_origen` (defensa en profundidad).

        El formulario ya limita el queryset, pero otros caminos (API, scripts,
        importaciones) podrían asignar un origen inválido. Solo se valida al
        crear (`_state.adding`) y con `tipo_id` ya asignado: la validación del
        ModelForm ocurre antes de que la vista fije el tipo, y un documento
        existente puede conservar su origen aunque la plantilla se inhabilite.

        Los documentos existentes sin plantilla_origen no se ven afectados.
        """
        super().clean()
        # Solo al crear: un documento ya existente puede conservar su
        # plantilla_origen aunque luego la plantilla se inhabilite.
        if not self._state.adding or not self.tipo_id:
            return
        # La propia plantilla no se genera desde otra plantilla.
        if self.es_plantilla:
            return
        if not self.plantilla_origen_id:
            raise ValidationError({
                'plantilla_origen': (
                    'Todo documento nuevo debe partir de una plantilla activa.'
                ),
            })
        origen = self.plantilla_origen
        if not origen.es_plantilla:
            raise ValidationError({
                'plantilla_origen': 'El documento de origen debe ser una plantilla.',
            })
        if origen.tipo_id != self.tipo_id:
            raise ValidationError({
                'plantilla_origen': 'La plantilla debe ser del mismo tipo que el documento.',
            })
        if origen.estado != 'activo':
            raise ValidationError({
                'plantilla_origen': 'La plantilla de origen debe estar activa.',
            })

    def save(self, *args, **kwargs):
        if self.archivo:
            nuevo_tipo = tipo_archivo_desde_nombre(
                self.archivo.name, self.tipo_archivo,
            )
            if nuevo_tipo != self.tipo_archivo:
                self.tipo_archivo = nuevo_tipo
                update_fields = kwargs.get('update_fields')
                if (update_fields is not None
                        and 'tipo_archivo' not in update_fields):
                    kwargs['update_fields'] = list(update_fields) + ['tipo_archivo']
        if self.codigo and self.nombre:
            self.hash_integridad = self.calcular_hash()
        super().save(*args, **kwargs)

    def establecer_vigencia(self, periodo):
        """Fija el período de vigencia y calcula vencimiento/próxima revisión."""
        from django.utils import timezone as tz
        self.periodo_vigencia = periodo
        if not self.fecha_vigencia:
            self.fecha_vigencia = tz.now()
        base = self.fecha_vigencia.date() if hasattr(self.fecha_vigencia, 'date') else tz.now().date()
        self.fecha_vencimiento = _sumar_meses(base, periodo.meses)
        self.proxima_revision = self.fecha_vencimiento
        self.aviso_vencimiento_enviado = False
        self.save(update_fields=[
            'periodo_vigencia', 'fecha_vigencia', 'fecha_vencimiento',
            'proxima_revision', 'aviso_vencimiento_enviado',
        ])

    @property
    def dias_para_vencer(self):
        """Días (enteros) hasta ``fecha_vencimiento``; ``None`` si no hay."""
        if not self.fecha_vencimiento:
            return None
        from django.utils import timezone as tz
        return (self.fecha_vencimiento - tz.localdate()).days

    @property
    def estado_vigencia(self):
        """Estado de vigencia para badges: vencido/vence_pronto/vigente/None."""
        dias = self.dias_para_vencer
        if dias is None:
            return None
        if dias <= 0:
            return 'vencido'
        if dias <= 7:
            return 'vence_pronto'
        return 'vigente'

    @property
    def clasificacion_color(self):
        """Clase de badge (sufijo tras `badge--`) para la clasificación actual."""
        return {
            'publico': 'success',
            'interno': 'secondary',
            'confidencial': 'warning',
            'restringido': 'danger',
        }.get(self.clasificacion, 'secondary')

    @property
    def resumen_ia_actualizado(self):
        """True si el resumen IA está listo y corresponde a la versión actual."""
        return bool(
            self.resumen_ia
            and self.resumen_ia_estado == 'listo'
            and self.resumen_ia_version == self.version_actual
        )


# =========================================================================
# MODELOS DE SOPORTE (adaptados al modelo unificado Documento)
# =========================================================================

class VersionDocumento(models.Model):
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name='versiones',
        null=True, blank=True
    )
    numero_version = models.CharField(max_length=20)
    archivo = models.FileField(upload_to=generar_ruta_version, blank=True, null=True, validators=[validar_archivo_documento])
    cambios = models.TextField(blank=True, default='', verbose_name='Descripción de cambios')
    creado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='versiones_creadas'
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Versión de Documento'
        verbose_name_plural = 'Versiones de Documentos'
        ordering = ['-fecha_creacion']
        unique_together = ['documento', 'numero_version']

    def __str__(self):
        return f"{self.documento.codigo} - v{self.numero_version}"


class AsignacionDocumento(models.Model):
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name='asignaciones',
        null=True, blank=True
    )
    coordinador = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='asignaciones_recibidas'
    )
    jefe_asignador = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='asignaciones_realizadas'
    )
    puede_leer = models.BooleanField(default=True)
    puede_editar = models.BooleanField(default=False)
    puede_eliminar = models.BooleanField(default=False)
    fecha_asignacion = models.DateTimeField(auto_now_add=True)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Asignación de Documento'
        verbose_name_plural = 'Asignaciones de Documentos'
        unique_together = ['documento', 'coordinador']

    def __str__(self):
        return f"{self.documento.codigo} → {self.coordinador.username}"


class SolicitudDescarga(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('aprobada', 'Aprobada'),
        ('rechazada', 'Rechazada'),
    ]

    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name='solicitudes_descarga',
        null=True, blank=True
    )
    solicitado_por = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='solicitudes_descarga'
    )
    motivo = models.TextField(blank=False, default='')
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_resolucion = models.DateTimeField(null=True, blank=True)
    observaciones = models.TextField(blank=True, default='')
    version_aprobada = models.CharField(
        max_length=20, blank=True, default='',
        verbose_name='Versión aprobada',
        help_text='Versión del documento a la que se autorizó la descarga.',
    )
    fecha_expiracion = models.DateTimeField(
        null=True, blank=True, verbose_name='Expira',
        help_text='Caduca la autorización de descarga (por defecto 30 días).',
    )

    class Meta:
        verbose_name = 'Solicitud de Descarga'
        verbose_name_plural = 'Solicitudes de Descarga'
        ordering = ['-fecha_solicitud']
        indexes = [
            models.Index(fields=['estado', 'solicitado_por'], name='sol_desc_est_user_idx'),
            models.Index(fields=['documento', 'estado'], name='sol_desc_doc_est_idx'),
        ]

    def __str__(self):
        return f"Solicitud #{self.id} - {self.documento.codigo}"

    @property
    def resolucion(self):
        """Aprobación que resolvió la solicitud (rechazada > aprobada).

        Devuelve ``None`` si la solicitud sigue pendiente. Se usa para
        mostrar al solicitante quién decidió y con qué observaciones.
        """
        resolutoras = (
            self.aprobaciones.all()
            .exclude(estado__in=('pendiente', 'cancelada'))
            .order_by('-fecha_aprobacion')
        )
        for aprobacion in resolutoras:
            if aprobacion.estado == 'rechazada':
                return aprobacion
        return resolutoras.first()

    @property
    def resolucion_observaciones(self):
        """Observaciones del aprobador que resolvió (o las de la solicitud)."""
        resolucion = self.resolucion
        if resolucion and resolucion.observaciones:
            return resolucion.observaciones
        return self.observaciones

    @property
    def resuelto_por(self):
        """Usuario que resolvió la solicitud (o ``None``)."""
        resolucion = self.resolucion
        return resolucion.aprobador if resolucion else None

    @property
    def esta_vigente(self):
        """True si la autorización sigue vigente (versión actual y no vencida)."""
        if self.estado != 'aprobada' or not self.fecha_expiracion:
            return False
        if self.fecha_expiracion <= timezone.now():
            return False
        return bool(self.documento) and self.version_aprobada == self.documento.version_actual


class AprobacionDescarga(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('esperando', 'En Espera'),
        ('aprobada', 'Aprobada'),
        ('rechazada', 'Rechazada'),
        ('cancelada', 'Cancelada'),
    ]
    ROL_CHOICES = [
        ('jefe', 'Jefe de Departamento'),
        ('autorizador', 'Autorizador (Gerencia General)'),
    ]

    solicitud = models.ForeignKey(
        SolicitudDescarga, on_delete=models.CASCADE, related_name='aprobaciones'
    )
    aprobador = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='aprobaciones_descarga',
        null=True, blank=True, verbose_name='Aprobador',
    )
    rol_aprobador = models.CharField(
        max_length=20, choices=ROL_CHOICES, default='jefe',
        verbose_name='Rol del aprobador',
        help_text='Restringido: jefe del departamento (evaluador) + jefe de '
                  'Gerencia General (autorizador), en ese orden.',
    )
    es_final = models.BooleanField(
        default=True, verbose_name='Es el paso final del flujo',
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    observaciones = models.TextField(blank=True, default='')
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)
    #: Momento en que el paso pasó a ``pendiente`` (base del escalado por antigüedad).
    fecha_activacion = models.DateTimeField(null=True, blank=True, verbose_name='Activado en')
    #: Anti-duplicado del escalado automático (ver ``tasks.escalar_aprobaciones_vencidas``).
    fecha_escalado = models.DateTimeField(null=True, blank=True, verbose_name='Escalado en')

    class Meta:
        verbose_name = 'Aprobación de Descarga'
        verbose_name_plural = 'Aprobaciones de Descarga'
        unique_together = ['solicitud', 'aprobador', 'rol_aprobador']

    def __str__(self):
        aprobador = self.aprobador.username if self.aprobador_id else 'sin asignar'
        return f"{self.get_rol_aprobador_display()} {aprobador} - Solicitud #{self.solicitud.id}"


class SolicitudAccesoDocumento(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('aprobada', 'Aprobada'),
        ('rechazada', 'Rechazada'),
    ]

    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name='solicitudes_acceso',
        null=True, blank=True
    )
    solicitante = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='solicitudes_acceso'
    )
    jefe_destino = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='solicitudes_acceso_recibidas'
    )
    departamento_destino = models.ForeignKey(
        Departamento, on_delete=models.CASCADE, related_name='solicitudes_acceso_recibidas'
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    motivo = models.TextField(blank=True, default='')
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_respuesta = models.DateTimeField(null=True, blank=True)
    observaciones = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Solicitud de Acceso a Documento'
        verbose_name_plural = 'Solicitudes de Acceso a Documentos'
        unique_together = ['documento', 'solicitante']
        ordering = ['-fecha_solicitud']

    def __str__(self):
        return f"Solicitud Acceso #{self.id} - {self.documento.codigo}"


class PlanillaCambios(models.Model):
    TIPO_CAMBIO_CHOICES = [
        ('modificacion', 'Modificación'),
    ]

    ESTADO_CHOICES = [
        ('borrador', 'Borrador'),
        ('en_revision', 'Enviado a Revisión'),
        ('aprobado', 'Aprobado'),
        ('rechazado', 'Rechazado'),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID')
    codigo = models.CharField(max_length=50, unique=True)
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name='planillas_cambios',
        null=True, blank=True
    )
    tipo_cambio = models.CharField(max_length=20, choices=TIPO_CAMBIO_CHOICES)
    justificacion = models.TextField()
    departamentos_afectados = models.ManyToManyField(
        Departamento, related_name='planillas_cambios'
    )
    afecta_todos = models.BooleanField(
        default=False,
        verbose_name='Afecta a todos los departamentos'
    )
    participantes_propuestos = models.JSONField(
        default=list, blank=True, verbose_name='Participantes Propuestos'
    )
    responsables_propuestos = models.ManyToManyField(
        Departamento, blank=True, related_name='planillas_responsables',
        verbose_name='Responsables Propuestos'
    )
    responsable = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='planillas_cambios'
    )
    version_destino = models.CharField(max_length=20)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='borrador')
    archivo_adjunto = models.FileField(upload_to='planillas_cambios/%Y/%m/', blank=True, null=True, validators=[validar_archivo_documento])
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)

    # ISO 9001:2015 - Cláusula 7.5.2
    descripcion_cambio = models.TextField(blank=True, default='', verbose_name='Descripción del Cambio')
    alcance = models.TextField(blank=True, default='', verbose_name='Alcance e Impacto')
    fecha_implementacion = models.DateField(null=True, blank=True, verbose_name='Fecha Propuesta de Implementación')
    criterios_aceptacion = models.TextField(blank=True, default='', verbose_name='Criterios de Aceptación')
    version_actual_snapshot = models.CharField(max_length=20, blank=True, default='', verbose_name='Versión Actual (snapshot)')

    nombre_propuesto = models.CharField(max_length=200, blank=True, default='', verbose_name='Nombre Propuesto')
    descripcion_propuesta = models.TextField(blank=True, default='', verbose_name='Descripción Propuesta')
    clasificacion_propuesta = models.CharField(
        max_length=20, blank=True, default='',
        choices=Documento.CLASIFICACION_CHOICES,
        verbose_name='Clasificación Propuesta',
        help_text='Vacío = no se cambia la clasificación del documento.',
    )
    clasificacion_actual_snapshot = models.CharField(
        max_length=20, blank=True, default='',
        verbose_name='Clasificación Actual (snapshot)',
    )
    referencias_pendientes = models.JSONField(
        default=list, blank=True, verbose_name='Referencias pendientes',
        help_text='Códigos de documentos asociados que aún no existen; se enlazan al crearse.',
    )
    archivo_propuesto = models.FileField(
        upload_to='planillas_cambios/archivos/%Y/%m/', blank=True, null=True,
        verbose_name='Archivo Propuesto',
        validators=[validar_archivo_documento],
    )
    archivo_propuesto_pdf = models.FileField(
        upload_to='planillas_cambios/pdf/%Y/%m/', blank=True, null=True,
        verbose_name='PDF del Archivo Propuesto',
        help_text='PDF generado automáticamente del archivo propuesto para vista previa'
    )
    #: Snapshot de la Tabla de cambios y modificaciones (SGC-FOR-003)
    #: generado al aprobar la planilla (incluye todo el histórico del documento).
    for_003_xlsx = models.FileField(
        upload_to='planillas_cambios/for_003/%Y/%m/', blank=True, null=True,
        verbose_name='FOR-003 XLSX generado',
        help_text='Tabla de cambios acumulada del documento (hoja(s) SGC-FOR-003).',
    )
    for_003_pdf = models.FileField(
        upload_to='planillas_cambios/for_003/%Y/%m/', blank=True, null=True,
        verbose_name='FOR-003 PDF generado',
        help_text='PDF de la Tabla de cambios (si LibreOffice está disponible).',
    )
    solicitud_cambio = models.ForeignKey(
        'SolicitudCambio', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='planillas_cambios', verbose_name='Solicitud de Cambio asociada'
    )

    class Meta:
        verbose_name = 'Planilla de Cambios'
        verbose_name_plural = 'Planillas de Cambios'
        ordering = ['-fecha_creacion']

    def __str__(self):
        return f"{self.codigo} - {self.documento.codigo}"

    def get_clasificacion_actual_snapshot_display(self):
        """Etiqueta legible del snapshot (no tiene ``choices`` propios)."""
        return dict(Documento.CLASIFICACION_CHOICES).get(
            self.clasificacion_actual_snapshot, self.clasificacion_actual_snapshot,
        )


class AprobacionPlanilla(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('esperando', 'En Espera'),
        ('aprobada', 'Aprobada'),
        ('rechazada', 'Rechazada'),
        ('cancelada', 'Cancelada'),
    ]

    ROL_CHOICES = [
        ('evaluador', 'Evaluador'),
        ('autorizador', 'Autorizador'),
    ]

    planilla = models.ForeignKey(
        PlanillaCambios, on_delete=models.CASCADE, related_name='aprobaciones'
    )
    aprobador = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='aprobaciones_planilla',
        null=True, blank=True,
        verbose_name='Aprobador (Jefe)'
    )
    rol_aprobador = models.CharField(
        max_length=20, choices=ROL_CHOICES, default='evaluador',
        verbose_name='Rol del Aprobador'
    )
    es_final = models.BooleanField(
        default=True,
        verbose_name='Es el paso final del flujo'
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    observaciones = models.TextField(blank=True, default='')
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)
    #: Momento en que el paso pasó a ``pendiente`` (base del escalado por antigüedad).
    fecha_activacion = models.DateTimeField(null=True, blank=True, verbose_name='Activado en')
    #: Anti-duplicado del escalado automático (ver ``tasks.escalar_aprobaciones_vencidas``).
    fecha_escalado = models.DateTimeField(null=True, blank=True, verbose_name='Escalado en')

    class Meta:
        verbose_name = 'Aprobación de Planilla'
        verbose_name_plural = 'Aprobaciones de Planillas'
        unique_together = ['planilla', 'aprobador', 'rol_aprobador']

    def __str__(self):
        rol = self.get_rol_aprobador_display()
        aprobador = self.aprobador.username if self.aprobador_id else 'sin asignar'
        return f"{rol} {aprobador} - Planilla {self.planilla.codigo}"

    def es_paso_unico(self):
        """True cuando este paso es el único de la cadena (flujo de jefes).

        Mismo criterio que ``SolicitudAprobacionDocumento.es_paso_unico``: si
        no hay paso de evaluación vivo (pendiente, en espera o aprobada),
        el autorizador (GG) evalúa y autoriza, y la planilla debe quedar
        firmada en ambos bloques.
        """
        if self.rol_aprobador != 'autorizador':
            return False
        return not AprobacionPlanilla.objects.filter(
            planilla_id=self.planilla_id,
            rol_aprobador='evaluador',
        ).exclude(estado__in=('rechazada', 'cancelada')).exclude(
            pk=self.pk,
        ).exists()


class CambioDocumento(models.Model):
    """Fila de la Tabla de cambios y modificaciones (SGC-FOR-003).

    Cada modificación aporta una o más filas (multifila). Las filas de una
    planilla en borrador se editan/reemplazan; al aprobar la planilla se
    **autorizan** (nombre + fecha + firma SES) y pasan al histórico del
    documento. Las filas de planillas rechazadas quedan como evidencia pero
    no se incluyen en la tabla generada.
    """

    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE,
        related_name='cambios_for_003', verbose_name='Documento',
    )
    planilla = models.ForeignKey(
        PlanillaCambios, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cambios_for_003', verbose_name='Planilla de cambios',
    )
    numero_pagina = models.CharField(
        max_length=50, blank=True, default='',
        verbose_name='Nº de página modificada',
        help_text='Página(s) del documento afectadas por el cambio.',
    )
    version = models.CharField(
        max_length=20, verbose_name='Versión',
        help_text='Versión resultante del documento.',
    )
    descripcion = models.TextField(
        verbose_name='Descripción del cambio',
    )
    fecha = models.DateField(
        default=timezone.localdate, verbose_name='Fecha del cambio',
    )
    realizado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name='cambios_for_003_realizados', verbose_name='Realizado por',
    )
    autorizado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name='cambios_for_003_autorizados', verbose_name='Autorizado por',
    )
    fecha_autorizacion = models.DateTimeField(
        null=True, blank=True, verbose_name='Autorizado en',
    )
    firma_autorizacion = models.ForeignKey(
        'FirmaElectronica', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cambios_for_003_autorizados',
        verbose_name='Firma SES de la autorización',
    )
    orden = models.PositiveIntegerField(default=0, verbose_name='Orden')
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cambio de Documento (SGC-FOR-003)'
        verbose_name_plural = 'Cambios de Documento (SGC-FOR-003)'
        ordering = ['fecha', 'orden', 'id']
        indexes = [
            models.Index(
                fields=['documento', 'fecha'], name='cambio_doc_fecha_idx',
            ),
        ]

    def __str__(self):
        return (
            f'{self.documento.codigo} v{self.version} '
            f'(pág. {self.numero_pagina or "—"})'
        )

    @property
    def esta_autorizado(self):
        return self.autorizado_por_id is not None


class DocumentoAsociadoPlanilla(models.Model):
    planilla = models.ForeignKey(
        PlanillaCambios, on_delete=models.CASCADE, related_name='documentos_asociados'
    )
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name='asociaciones_planilla',
        null=True, blank=True
    )

    class Meta:
        unique_together = ('planilla', 'documento')
        verbose_name = 'Documento Asociado a Planilla'
        verbose_name_plural = 'Documentos Asociados a Planilla'

    def __str__(self):
        return f"{self.documento.codigo}"





class DistribucionDocumento(models.Model):
    METODO_CHOICES = [
        ('sistema', 'Sistema'),
        ('email', 'Email'),
        ('impreso', 'Impreso'),
    ]

    CONSTANCIA_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('sistema', 'Sistema'),
        ('email', 'Email'),
        ('impreso', 'Impreso'),
    ]

    ESTADO_FIRMA_CHOICES = [
        ('no_aplica', 'No aplica'),
        ('pendiente', 'Pendiente de firma'),
        ('firmada', 'Firmada'),
        ('expirada', 'Invitación expirada'),
    ]

    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name='distribuciones',
        null=True, blank=True
    )
    #: ``None`` cuando el destinatario es EXTERNO (no tiene cuenta en POLIDOC).
    usuario = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='documentos_recibidos',
        null=True, blank=True,
        verbose_name='Usuario destinatario'
    )
    #: Datos del destinatario externo (mutuamente excluyente con ``usuario``).
    nombre_externo = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Nombre (externo)'
    )
    cargo_externo = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Cargo (externo)'
    )
    email_externo = models.EmailField(
        blank=True, default='', verbose_name='Correo (externo)'
    )
    organizacion_externa = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Organización (externa)'
    )
    fecha_distribucion = models.DateTimeField(auto_now_add=True)
    metodo = models.CharField(max_length=20, choices=METODO_CHOICES, default='sistema')
    distribuido_por = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='distribuciones_realizadas',
        verbose_name='Distribuido por'
    )
    observaciones = models.TextField(blank=True, default='')
    #: Agrupa las distribuciones que comparten UNA planilla SGC-FOR-002
    #: (una planilla por lote de a lo sumo 33 destinatarios).
    lote = models.UUIDField(
        null=True, blank=True, db_index=True, verbose_name='Lote de planilla',
        help_text='Identificador del lote que comparte planilla SGC-FOR-002.'
    )
    #: Cómo se emitió la constancia de distribución (FOR-002).
    constancia = models.CharField(
        max_length=20, choices=CONSTANCIA_CHOICES, default='pendiente',
        verbose_name='Constancia'
    )
    fecha_entrega = models.DateTimeField(
        null=True, blank=True, verbose_name='Fecha de entrega',
        help_text='Momento en que se confirmó la entrega al destinatario.'
    )
    #: Estado de la firma conforme del destinatario en la planilla FOR-002.
    estado_firma = models.CharField(
        max_length=20, choices=ESTADO_FIRMA_CHOICES, default='no_aplica',
        verbose_name='Estado de firma'
    )
    #: El destinatario retiró la versión anterior del documento (FOR-002).
    retiro_version_anterior = models.BooleanField(
        default=False, verbose_name='Retiró versión anterior'
    )
    #: True si el PDF/documento se adjuntó al correo de distribución.
    #: Un adjunto ya enviado no se puede revocar, por eso queda registrado.
    adjunto_pdf_enviado = models.BooleanField(
        default=False, verbose_name='Adjunto enviado',
        help_text='True si el archivo se adjuntó al correo de distribución.'
    )
    motivo_sin_adjunto = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Motivo sin adjunto',
        help_text='Por qué no se adjuntó el archivo (política de clasificación).'
    )
    enviado = models.BooleanField(
        default=False, verbose_name='Email enviado',
        help_text='True si el correo de distribución se envió correctamente.'
    )
    error = models.TextField(
        blank=True, default='', verbose_name='Error de envío',
        help_text='Detalle si falló el envío del correo.'
    )

    revocado = models.BooleanField(
        default=False, verbose_name='Revocado',
        help_text='True si la distribución fue revocada.'
    )
    fecha_revocacion = models.DateTimeField(null=True, blank=True, verbose_name='Fecha de revocación')
    revocado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='distribuciones_revocadas',
        verbose_name='Revocado por'
    )
    motivo_revocacion = models.TextField(blank=True, default='', verbose_name='Motivo de revocación')
    email_revocacion_enviado = models.BooleanField(
        default=False, verbose_name='Email de revocación enviado',
        help_text='True si el correo de revocación se envió correctamente.'
    )
    error_revocacion = models.TextField(
        blank=True, default='', verbose_name='Error de revocación',
        help_text='Detalle si falló el envío de la revocación.'
    )
    #: Escaneo del acuse firmado en papel (método impreso; ISO 9001:7.5.3).
    acuse_archivo = models.FileField(
        upload_to='acuses/documentos/%Y/%m/', blank=True, null=True,
        validators=[validar_archivo_acuse],
        verbose_name='Acuse escaneado',
        help_text='Escaneo del acuse firmado en papel (método impreso).',
    )

    class Meta:
        verbose_name = 'Distribución de Documento'
        verbose_name_plural = 'Distribuciones de Documentos'
        ordering = ['-fecha_distribucion']
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(usuario__isnull=False, nombre_externo='')
                    | models.Q(usuario__isnull=True, nombre_externo__gt='')
                ),
                name='dist_destino_interno_o_externo',
            ),
        ]

    def __str__(self):
        if self.usuario_id:
            return f"Distribución {self.documento.codigo} a {self.usuario.username}"
        return f"Distribución {self.documento.codigo} a {self.nombre_externo}"

    @property
    def es_externo(self):
        """True cuando el destinatario no tiene cuenta en POLIDOC."""
        return self.usuario_id is None

    @property
    def destinatario_nombre(self):
        """Nombre visible del destinatario (interno o externo)."""
        if self.usuario_id:
            return self.usuario.get_full_name() or self.usuario.username
        return self.nombre_externo

    @property
    def destinatario_email(self):
        """Correo del destinatario (interno o externo), o vacío."""
        if self.usuario_id:
            return self.usuario.email or ''
        return self.email_externo or ''

    @property
    def firma_pendiente(self):
        """True si la distribución espera la firma conforme del destinatario."""
        return self.estado_firma == 'pendiente'

    @property
    def firmas(self):
        """QuerySet de ``FirmaElectronica`` de esta distribución (motor único)."""
        return FirmaElectronica.para(self)


class SolicitudAprobacionDocumento(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('esperando', 'En Espera'),
        ('aprobada', 'Aprobada'),
        ('rechazada', 'Rechazada'),
        ('cancelada', 'Cancelada'),
    ]

    ROL_CHOICES = [
        ('evaluador', 'Evaluador'),
        ('autorizador', 'Autorizador'),
    ]

    ACCION_CHOICES = [
        ('creacion', 'Creación'),
        ('edicion', 'Edición'),
        ('inhabilitar', 'Inhabilitar'),
        ('reactivar', 'Reactivar'),
        ('renovar', 'Renovar Vencimiento'),
        ('version', 'Nueva Versión'),
        ('eliminar_version', 'Eliminar Versión'),
    ]

    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name='solicitudes_aprobacion',
        null=True, blank=True
    )
    version_a_eliminar = models.ForeignKey(
        'VersionDocumento', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='solicitudes_eliminacion',
        verbose_name='Versión a Eliminar'
    )
    accion_solicitud = models.CharField(
        max_length=20, choices=ACCION_CHOICES, default='creacion',
        verbose_name='Acción Solicitada'
    )
    solicitante = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='solicitudes_aprobacion_creadas',
        verbose_name='Solicitante (Creador)'
    )
    propietario = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='solicitudes_aprobacion_propietario',
        verbose_name='Propietario del Documento',
        null=True, blank=True
    )
    aprobador = models.ForeignKey(
        User, on_delete=models.CASCADE,
        related_name='solicitudes_aprobacion_recibidas',
        verbose_name='Aprobador (Jefe)',
        null=True, blank=True
    )
    departamento_origen = models.ForeignKey(
        Departamento, on_delete=models.CASCADE,
        related_name='solicitudes_aprobacion',
        verbose_name='Departamento Origen'
    )
    rol_aprobador = models.CharField(
        max_length=20, choices=ROL_CHOICES, default='evaluador',
        verbose_name='Rol del Aprobador'
    )
    es_final = models.BooleanField(
        default=True,
        verbose_name='Es el paso final del flujo'
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    observaciones = models.TextField(blank=True, default='', verbose_name='Observaciones del Aprobador')
    motivo_rechazo = models.TextField(blank=True, default='', verbose_name='Motivo de Rechazo')
    fecha_solicitud = models.DateTimeField(auto_now_add=True)
    fecha_respuesta = models.DateTimeField(null=True, blank=True)
    fecha_propuesta = models.DateField(
        null=True, blank=True,
        verbose_name='Fecha de Vencimiento Propuesta',
        help_text='Solo para renovaciones: fecha que se aplicará al aprobar.',
    )
    fecha_escalado = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Fecha de escalado a administración'
    )

    class Meta:
        verbose_name = 'Solicitud de Aprobación de Documento'
        verbose_name_plural = 'Solicitudes de Aprobación de Documentos'
        ordering = ['-fecha_solicitud']

    def __str__(self):
        return f"Aprobación {self.documento.codigo} - {self.get_estado_display()}"

    def es_paso_unico(self):
        """True cuando este paso es el único de la cadena (flujo de jefes).

        El flujo de jefes crea UN solo paso (autorizador/GG, ``es_final``).
        En ese caso el mismo aprobador evalúa y autoriza, de modo que la
        planilla debe quedar firmada en los DOS bloques (Evaluado/Autorizado
        por). En la cadena de dos pasos esto es False y cada rol firma lo suyo.

        Solo cuenta un evaluador que no murió en la cadena: si una cadena
        anterior fue rechazada/cancelada, sus filas no invalidan la regla de
        la cadena vigente.
        """
        if self.rol_aprobador != 'autorizador' or self.documento_id is None:
            return False
        return not SolicitudAprobacionDocumento.objects.filter(
            documento_id=self.documento_id,
            accion_solicitud=self.accion_solicitud,
            rol_aprobador='evaluador',
        ).exclude(estado__in=('rechazada', 'cancelada')).exclude(
            pk=self.pk,
        ).exists()

    def aprobar(self, observaciones=''):
        from django.utils import timezone as tz
        self.estado = 'aprobada'
        self.observaciones = observaciones
        self.fecha_respuesta = tz.now()
        self.save(update_fields=['estado', 'observaciones', 'fecha_respuesta'])
        valores = {}
        # En un solo paso, el autorizador también firma la evaluación.
        if self.rol_aprobador == 'evaluador' or self.es_paso_unico():
            valores.update(_valores_evaluacion(self.aprobador, self.fecha_respuesta))
        if self.es_final:
            valores.update(_valores_autorizacion(self.aprobador, self.fecha_respuesta))
            valores['resolucion'] = 'Aceptado'
        # Solo la planilla de la operación que corresponde a ESTA acción se
        # autocompleta: reactivar/renovar no tienen planilla y no deben tocar
        # la última SolicitudCambio del documento (que puede ser de otra
        # operación concurrente).
        operacion = OPERACION_POR_ACCION.get(self.accion_solicitud)
        if self.es_final:
            # Activar primero para que fecha_vigencia quede disponible y se
            # autocomplete también en las respuestas del formato dinámico.
            self.aplicar_cambio()
            if operacion:
                autocompletar_respuestas(self.documento, valores, operacion=operacion)
        else:
            if valores and operacion:
                autocompletar_respuestas(self.documento, valores, operacion=operacion)
            self._avanzar_siguiente()

    def rechazar(self, motivo=''):
        """Rechaza la solicitud y devuelve el documento a correcciones.

        El rechazo NO firma ni imprime los bloques de Evaluado/Autorizado ni
        la Resolución: la planilla queda en blanco y el propietario corrige el
        documento/planilla para reenviarlo. La trazabilidad queda en la
        auditoría ISO y en ``motivo_rechazo``/observaciones.
        """
        from django.utils import timezone as tz
        self.estado = 'rechazada'
        self.motivo_rechazo = motivo
        self.fecha_respuesta = tz.now()
        self.save(update_fields=['estado', 'motivo_rechazo', 'fecha_respuesta'])
        self._cancelar_hermano()
        self._revertir_documento()

    def _avanzar_siguiente(self):
        hermano = SolicitudAprobacionDocumento.objects.filter(
            documento=self.documento,
            accion_solicitud=self.accion_solicitud,
            rol_aprobador='autorizador',
            estado='esperando',
        ).exclude(id=self.id).order_by('id').first()
        if not hermano:
            return
        from autenticacion.models import Notificacion
        if hermano.aprobador_id is None:
            # Sin jefe de Gerencia General: queda evaluado, en espera de autorización.
            destinatario = self.propietario or self.solicitante
            if destinatario:
                Notificacion.objects.create(
                    usuario=destinatario,
                    titulo='Documento evaluado, pendiente de autorización',
                    mensaje=(
                        f'El documento "{self.documento.nombre}" '
                        f'({self.documento.codigo}) fue evaluado y está pendiente '
                        f'de autorización por Gerencia General.'
                    ),
                    tipo='aprobacion_documento',
                    url_accion=f'/documentos/{self.documento.tipo_id}/{self.documento.uuid}/',
                    departamento=self.documento.departamento,
                    documento=self.documento,
                )
            return
        hermano.estado = 'pendiente'
        hermano.save(update_fields=['estado'])
        Notificacion.objects.create(
            usuario=hermano.aprobador,
            titulo='Documento pendiente de autorización',
            mensaje=(
                f'El documento "{self.documento.nombre}" ({self.documento.codigo}) '
                f'ha sido evaluado y requiere tu autorización.'
            ),
            tipo='aprobacion_documento',
            url_accion=f'/documentos/{self.documento.tipo_id}/{self.documento.uuid}/',
            departamento=self.documento.departamento,
            documento=self.documento,
        )

    def _cancelar_hermano(self):
        hermano = SolicitudAprobacionDocumento.objects.filter(
            documento=self.documento,
            accion_solicitud=self.accion_solicitud,
        ).exclude(id=self.id).exclude(estado__in=('aprobada', 'rechazada', 'cancelada')).first()
        if hermano:
            hermano.estado = 'cancelada'
            hermano.save(update_fields=['estado'])

    def _revertir_documento(self):
        # inhabilitar/reactivar/renovar NO cambian el estado del documento
        # mientras están pendientes, así que al rechazar no hay nada que
        # restaurar (evita pisar un estado alterado por el cron de vencimientos
        # o por una planilla de cambios aprobada en paralelo).
        if self.accion_solicitud in ('creacion', 'edicion', 'version'):
            doc = self.documento
            doc.estado = 'borrador'
            doc.save(update_fields=['estado'])
        # eliminar_version: la versión queda tal cual (soft-delete no aplica)

    def aplicar_eliminacion_version(self, doc, version):
        """Soft-delete la versión y restaura la anterior si era la actual"""
        if not version:
            return
        # Un documento conserva siempre al menos una versión activa: es la
        # red de seguridad de `views.eliminar_version` por si otra ruta
        # llega a llamar esto sin haber comprobado.
        if not VersionDocumento.objects.filter(
            documento=doc, activa=True
        ).exclude(id=version.id).exists():
            return
        version.activa = False
        version.save(update_fields=['activa'])
        if version.numero_version == doc.version_actual:
            anterior = VersionDocumento.objects.filter(
                documento=doc, activa=True
            ).exclude(id=version.id).order_by('-id').first()
            if anterior:
                doc.archivo = anterior.archivo
                doc.version_actual = anterior.numero_version
                doc.archivo_pdf = None
                doc.save(update_fields=['archivo', 'version_actual', 'archivo_pdf'])
            else:
                doc.estado = 'obsoleto'
                doc.save(update_fields=['estado'])
                self._avisar_desincorporacion(doc)

    def _aplicar_renovacion(self, doc):
        """Aplica la fecha propuesta (ISO 9001:7.5.3.2, control de cambios).

        Registra ``RenovacionVencimiento`` (anterior → nueva, autorizado por)
        y deja el documento vigente: fecha futura o sin fecha para que la
        tarea ``verificar_vencimientos`` no lo vuelva a marcar obsoleto.
        """
        fecha_nueva = self.fecha_propuesta
        if not fecha_nueva:
            return
        fecha_anterior = doc.fecha_vencimiento
        doc.fecha_vencimiento = fecha_nueva
        doc.proxima_revision = fecha_nueva
        doc.aviso_vencimiento_enviado = False
        doc.fecha_retencion = None
        doc.fecha_destruccion = None
        campos = [
            'fecha_vencimiento', 'proxima_revision', 'aviso_vencimiento_enviado',
            'fecha_retencion', 'fecha_destruccion',
        ]
        if doc.estado != 'activo':
            doc.estado = 'activo'
            campos.append('estado')
        doc.save(update_fields=campos)
        RenovacionVencimiento.objects.create(
            documento=doc,
            fecha_anterior=fecha_anterior or fecha_nueva,
            fecha_nueva=fecha_nueva,
            renovado_por=self.aprobador or self.solicitante,
            observacion=self.observaciones or '',
        )

    def _avisar_desincorporacion(self, doc):
        """Avisa a los responsables de cada padre si ``doc`` quedó obsoleto.

        El aviso es parte del flujo SGC-FOR-004: sin él, un documento
        inactivado por aprobación nunca llegaría al modal de acta de su
        padre. Nunca rompe la aplicación del cambio.
        """
        try:
            from .services.for_004 import avisar_desincorporacion

            avisar_desincorporacion(
                doc, excluir=self.aprobador or self.solicitante,
            )
        except Exception as exc:  # noqa: BLE001 - el cambio no depende del aviso
            logger.error(
                'No se pudo avisar la desincorporación de %s: %s',
                getattr(doc, 'codigo', doc.pk), exc,
            )

    def aplicar_cambio(self):
        """Aplica al documento el resultado de la aprobación de esta solicitud.

        Despacha según ``accion_solicitud``: inhabilitar (pasa a obsoleto y
        programa la disposición), reactivar (vuelve a activo y limpia la
        disposición), renovar (aplica la fecha propuesta), eliminar_version
        (soft-delete) y creación/edición/version (activa y sube la revisión).
        """
        from django.utils import timezone as tz
        from datetime import timedelta
        doc = self.documento
        if self.accion_solicitud == 'inhabilitar':
            doc.estado = 'obsoleto'
            if doc.periodo_retencion:
                hoy = tz.now().date()
                doc.fecha_retencion = hoy
                doc.fecha_destruccion = _sumar_meses(hoy, doc.periodo_retencion.meses)
            doc.save(update_fields=['estado', 'fecha_retencion', 'fecha_destruccion'])
            self._avisar_desincorporacion(doc)
        elif self.accion_solicitud == 'reactivar':
            # Reactivar limpia la disposición programada: estando activo el
            # documento no debe quedar en cola para destrucción.
            doc.estado = 'activo'
            doc.fecha_retencion = None
            doc.fecha_destruccion = None
            doc.aviso_vencimiento_enviado = False
            doc.save(update_fields=[
                'estado', 'fecha_retencion', 'fecha_destruccion',
                'aviso_vencimiento_enviado',
            ])
        elif self.accion_solicitud == 'renovar':
            self._aplicar_renovacion(doc)
            return
        elif self.accion_solicitud == 'eliminar_version':
            self.aplicar_eliminacion_version(doc, self.version_a_eliminar)
            return
        elif self.accion_solicitud in ('creacion', 'edicion', 'version'):
            doc.estado = 'activo'
            doc.aprobado_por = self.aprobador
            doc.fecha_aprobacion = tz.now()
            # La entrada en vigor solo se fija al crear (o si el documento no
            # tiene ninguna): editar o subir de versión no debe reiniciarla,
            # o cada cambio empujaría el inicio de la vigencia.
            fija_vigencia = self.accion_solicitud == 'creacion' or not doc.fecha_vigencia
            if fija_vigencia:
                doc.fecha_vigencia = tz.now()
            try:
                rev = int(doc.revision)
            except (ValueError, TypeError):
                rev = 0
            doc.revision = str(rev + 1)
            historial_linea = (
                f"v{doc.revision} - {tz.now().strftime('%Y-%m-%d %H:%M')} - "
                f"{self.accion_solicitud} por {self.aprobador.get_full_name() or self.aprobador.username}\n"
            )
            doc.historial_cambios = (doc.historial_cambios + historial_linea).strip()
            if doc.periodo_vigencia:
                fecha_venc = _sumar_meses(tz.now().date(), doc.periodo_vigencia.meses)
                doc.fecha_vencimiento = fecha_venc
                doc.proxima_revision = fecha_venc
            campos = [
                'estado', 'aprobado_por', 'fecha_aprobacion',
                'revision', 'historial_cambios', 'fecha_vencimiento', 'proxima_revision',
            ]
            if fija_vigencia:
                campos.append('fecha_vigencia')
            doc.save(update_fields=campos)
            # Los campos auto de la planilla (versión/edición/…) cambiaron con
            # la aprobación: los archivos guardados quedaron desactualizados.
            from .services.planillas import invalidar_planillas_documento
            invalidar_planillas_documento(doc)

def _cargo_usuario(usuario):
    """Genera texto de cargo: 'Rol — Departamento'."""
    perfil = getattr(usuario, 'perfil', None)
    if not perfil:
        return ''
    rol = perfil.get_rol_display() or perfil.rol
    depto = str(perfil.departamento) if perfil.departamento else ''
    return f"{rol} — {depto}" if depto else rol


def _fecha_respuesta_iso(fecha):
    """Normaliza la fecha real de una respuesta a ISO (AAAA-MM-DD).

    Las firmas de evaluación/autorización deben reflejar cuándo se respondió
    (``fecha_respuesta``/``fecha_aprobacion``), no el momento en que se
    rellena la planilla: la regeneración posterior no debe mover la fecha.
    """
    if not fecha:
        fecha = timezone.now()
    return timezone.localtime(fecha).date().isoformat()


def _valores_evaluacion(usuario, fecha=None):
    """Diccionario con los campos de evaluación para autorrellenar.

    ``fecha`` es la fecha real de la aprobación; si se omite, se usa la
    actual. El rechazo no firma (ver ``SolicitudAprobacionDocumento.rechazar``).
    """
    return {
        'evaluado_por_nombre': usuario.get_full_name() or usuario.username,
        'evaluado_por_cargo': _cargo_usuario(usuario),
        'evaluado_por_fecha': _fecha_respuesta_iso(fecha),
    }


def _valores_autorizacion(usuario, fecha=None):
    """Diccionario con los campos de autorización para autorrellenar.

    ``fecha`` es la fecha real de la aprobación; si se omite, se usa la
    actual. El rechazo no firma (ver ``SolicitudAprobacionDocumento.rechazar``).
    """
    return {
        'autorizado_por_nombre': usuario.get_full_name() or usuario.username,
        'autorizado_por_cargo': _cargo_usuario(usuario),
        'autorizado_por_fecha': _fecha_respuesta_iso(fecha),
    }


OPERACION_LABELS = {
    'creacion': 'Creación',
    'modificacion': 'Modificación',
    'eliminacion': 'Eliminación',
}

#: Acción de ``SolicitudAprobacionDocumento`` → operación de la
#: ``SolicitudCambio`` (planilla SGC-FOR-001) que la acompaña. ``None`` =
#: sin planilla (reactivar/renovar/eliminar_version no la generan), y por lo
#: tanto no se autocompleta ni se resuelve ninguna planilla al aprobar.
OPERACION_POR_ACCION = {
    'creacion': 'creacion',
    'edicion': 'modificacion',
    'version': 'modificacion',
    'inhabilitar': 'eliminacion',
    'eliminar_version': None,
    'reactivar': None,
    'renovar': None,
}


def valores_documento(documento):
    """Valores derivados del documento para los campos auto del motor dinámico."""
    if not documento:
        return {}
    tipo = ''
    if documento.tipo_id:
        try:
            tipo = documento.tipo.nombre
        except Exception:
            tipo = ''
    fecha_vig = ''
    if documento.fecha_vigencia:
        fecha_vig = documento.fecha_vigencia.date().isoformat()
    return {
        'tipo_informacion': tipo,
        'num_identificacion': documento.codigo or '',
        'titulo_documento': documento.nombre or '',
        'version_documento': documento.version_actual or '',
        'edicion_documento': documento.revision or '',
        'fecha_vigencia_documento': fecha_vig,
    }


def valores_auto_solicitud(sol):
    """Valores auto por ORIGEN para una SolicitudCambio (documento + operación)."""
    valores = dict(valores_documento(sol.documento if sol else None))
    if sol:
        valores['operacion'] = OPERACION_LABELS.get(sol.operacion, sol.operacion)
    return valores


def mapear_valores_auto(formato, valores):
    """Mapea valores por ORIGEN a {campo.clave: valor} para los campos auto.

    Solo incluye campos cuyo origen esté en `valores` y con valor no vacío.
    """
    resultado = {}
    if not formato:
        return resultado
    for campo in formato.get_todos_los_campos().exclude(origen='usuario'):
        valor = valores.get(campo.origen)
        if valor not in (None, '', []):
            if campo.tipo == 'checkbox_grupo' and not isinstance(valor, list):
                valor = [valor]
            resultado[campo.clave] = valor
    return resultado


def autocompletar_respuestas_solicitud(sol, valores):
    """Actualiza las respuestas de una ``SolicitudCambio`` concreta.

    A diferencia de :func:`autocompletar_respuestas` (que elige la última
    solicitud del documento por operación), apunta a ESTA solicitud: es el
    camino de las planillas FOR-005, donde cada ``PlanillaCambios`` enlaza su
    propia ``SolicitudCambio`` y no debe tocar otras planillas concurrentes
    del mismo documento.

    Tras escribir, descarta el XLSX/PDF cacheado de la solicitud para que la
    siguiente descarga lo regenere con los valores nuevos (p. ej. las firmas
    de evaluación/autorización).
    """
    if not sol or not sol.formato:
        return
    valores_auto = valores_auto_solicitud(sol)
    valores_auto.update(valores or {})
    nuevos = mapear_valores_auto(sol.formato, valores_auto)
    if not nuevos:
        return
    respuestas = dict(sol.respuestas)
    respuestas.update(nuevos)
    sol.respuestas = respuestas
    sol.save(update_fields=['respuestas'])
    from .services.planillas import invalidar_planilla_solicitud
    invalidar_planilla_solicitud(sol)


def autocompletar_respuestas(documento, valores, operacion=None):
    """Actualiza las respuestas del SolicitudCambio con valores de sistema.

    Con ``operacion`` se limita a la planilla de esa operación: la
    aprobación de una reactivación/renovación no debe escribir en la última
    SolicitudCambio del documento, que puede pertenecer a otra operación.
    """
    solicitudes = SolicitudCambio.objects.filter(documento=documento)
    if operacion:
        solicitudes = solicitudes.filter(operacion=operacion)
    sol = solicitudes.order_by('-id').first()
    if not sol:
        return
    autocompletar_respuestas_solicitud(sol, valores)


def respuestas_planilla_actual(sol):
    """Respuestas de la planilla con los campos auto releídos desde la BD.

    Los campos auto derivados del documento (código, tipo, título, versión,
    edición, fecha de vigencia y operación) se recalculan en cada llamada con
    :func:`valores_auto_solicitud`, de modo que la planilla siempre refleje el
    estado actual del documento aunque haya cambiado después de aprobarse.

    Un origen cuyo valor actual es vacío se elimina de las respuestas: la
    planilla muestra el campo vacío en lugar de un valor congelado.

    El resto de las respuestas se conserva tal cual: lo escrito por el usuario
    y los bloques de evaluación/autorización/resolución, que son histórico de
    la aprobación y no dependen del documento.
    """
    if not sol:
        return {}
    respuestas = dict(sol.respuestas or {})
    formato = sol.formato
    if not formato:
        return respuestas
    valores = valores_auto_solicitud(sol)
    for campo in formato.get_todos_los_campos().exclude(origen='usuario'):
        if campo.origen not in valores:
            continue
        valor = valores[campo.origen]
        if valor in (None, '', []):
            respuestas.pop(campo.clave, None)
        elif campo.tipo == 'checkbox_grupo' and not isinstance(valor, list):
            respuestas[campo.clave] = [valor]
        else:
            respuestas[campo.clave] = valor
    return respuestas


def _sumar_meses(fecha, meses):
    """Suma N meses a una fecha, ajustando el día al último válido del mes."""
    from calendar import monthrange
    mes = fecha.month - 1 + meses
    anio = fecha.year + mes // 12
    mes = mes % 12 + 1
    dia = min(fecha.day, monthrange(anio, mes)[1])
    return fecha.replace(year=anio, month=mes, day=dia)


class RenovacionVencimiento(models.Model):
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE,
        related_name='renovaciones_vencimiento'
    )
    fecha_anterior = models.DateField(verbose_name='Fecha de Vencimiento Anterior')
    fecha_nueva = models.DateField(verbose_name='Fecha de Vencimiento Nueva')
    renovado_por = models.ForeignKey(
        'auth.User', on_delete=models.PROTECT,
        related_name='renovaciones_realizadas'
    )
    fecha_renovacion = models.DateTimeField(auto_now_add=True)
    observacion = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Renovación de Vencimiento'
        verbose_name_plural = 'Renovaciones de Vencimiento'
        ordering = ['-fecha_renovacion']

    def __str__(self):
        return f"Renovación {self.documento.codigo}: {self.fecha_anterior} → {self.fecha_nueva}"


# =============================================================================
# REFERENCIAS A DOCUMENTOS (con soporte para enlaces pendientes)
# =============================================================================

class ReferenciaDocumento(models.Model):
    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE,
        related_name='referencias_salientes'
    )
    codigo_destino = models.CharField(max_length=50, verbose_name='Código del documento destino')
    documento_destino = models.ForeignKey(
        Documento, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='referencias_entrantes',
        verbose_name='Documento destino (resuelto)'
    )
    creado_por = models.ForeignKey(
        'auth.User', on_delete=models.PROTECT,
        related_name='referencias_creadas'
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Referencia a Documento'
        verbose_name_plural = 'Referencias a Documentos'
        unique_together = ('documento', 'codigo_destino')
        ordering = ['-fecha_creacion']

    def __str__(self):
        estado = 'resuelta' if self.documento_destino else 'pendiente'
        return f"{self.documento.codigo} → {self.codigo_destino} ({estado})"

    @property
    def esta_resuelta(self):
        return self.documento_destino is not None


# =============================================================================
# MOTOR DE FORMULARIOS CONFIGURABLES (SGC-FOR-005)
# =============================================================================

class Formato(models.Model):
    """Plantilla configurable de un formulario (ej. SGC-FOR-005)."""
    codigo = models.CharField(max_length=30, unique=True, verbose_name='Código')
    nombre = models.CharField(max_length=200, verbose_name='Nombre')
    version = models.CharField(max_length=20, default='1', verbose_name='Versión')
    edicion = models.CharField(max_length=20, default='1', verbose_name='Edición')
    fecha_vigencia = models.DateField(null=True, blank=True, verbose_name='Fecha de Vigencia')
    procedimiento = models.CharField(max_length=300, blank=True, default='', verbose_name='Procedimiento')
    instructivo_html = models.TextField(blank=True, default='', verbose_name='Instructivo (HTML)')
    plantilla_xlsx = models.FileField(
        upload_to='formatos/plantillas/%Y/', blank=True, null=True,
        verbose_name='Plantilla Excel',
        validators=[validar_archivo_documento],
    )
    plantilla_pdf = models.FileField(
        upload_to='formatos/pdf/', blank=True, null=True,
        verbose_name='Preview PDF'
    )
    aplica_creacion = models.BooleanField(default=True, verbose_name='Aplica para Creación')
    aplica_modificacion = models.BooleanField(default=True, verbose_name='Aplica para Modificación')
    aplica_eliminacion = models.BooleanField(default=True, verbose_name='Aplica para Eliminación')
    activo = models.BooleanField(default=True, verbose_name='Activo')
    #: Habilita el flujo de Registros (instancias llenas) para este formato.
    permite_registros = models.BooleanField(
        default=False, verbose_name='Permite registros',
        help_text=(
            'Si está activo, se pueden crear registros (formularios llenos) '
            'de este formato ligados a un documento/contexto.'
        ),
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Formato'
        verbose_name_plural = 'Formatos'
        ordering = ['codigo']

    def __str__(self):
        return f"{self.codigo} v{self.version} E{self.edicion}"

    def save(self, *args, **kwargs):
        # Saneado defensivo en la capa de modelo: cubre TODAS las rutas de
        # escritura (FormatoForm, Django Admin y management commands), de modo
        # que nunca se persista HTML ejecutable que luego se renderiza con |safe.
        from .sanitizers import sanitizar_html
        self.instructivo_html = sanitizar_html(self.instructivo_html)
        super().save(*args, **kwargs)
        if self.plantilla_xlsx and not self.plantilla_pdf:
            # Vista previa en PDF de forma asíncrona: guardar el Formato (p.ej.
            # el constructor de formatos) ya no espera 2-3 s a LibreOffice. Si
            # el broker no responde, se degrada al modo síncrono.
            from .tasks import convertir_formato_a_pdf, convertir_formato_a_pdf_sync
            try:
                convertir_formato_a_pdf.delay(self.pk)
            except Exception:
                try:
                    convertir_formato_a_pdf_sync(self)
                except Exception:
                    pass

    def get_secciones(self):
        # Lista evaluada con los campos ya precargados: las plantillas iteran
        # `seccion.campos.all` y así no se lanza una consulta por sección al
        # pintar la ficha o el formulario dinámico. Se memoriza en la instancia
        # porque la ficha invoca el método más de una vez por render.
        cache = getattr(self, '_secciones_precargadas', None)
        if cache is None:
            cache = list(
                self.secciones.all().order_by('orden').prefetch_related('campos')
            )
            self._secciones_precargadas = cache
        return cache

    def get_todos_los_campos(self):
        return CampoFormato.objects.filter(seccion__formato=self).select_related('seccion').order_by('seccion__orden', 'orden')


class SeccionFormato(models.Model):
    """Sección dentro de un Formato."""
    formato = models.ForeignKey(
        Formato, on_delete=models.CASCADE, related_name='secciones'
    )
    titulo = models.CharField(max_length=200, verbose_name='Título')
    descripcion = models.TextField(blank=True, default='', verbose_name='Descripción')
    orden = models.PositiveIntegerField(default=0, verbose_name='Orden')

    class Meta:
        verbose_name = 'Sección del Formato'
        verbose_name_plural = 'Secciones del Formato'
        ordering = ['orden']

    def __str__(self):
        return f"{self.formato.codigo} → {self.titulo}"


class CampoFormato(models.Model):
    """Campo configurable dentro de una sección."""
    TIPO_CHOICES = [
        ('texto', 'Texto'),
        ('textarea', 'Área de texto'),
        ('fecha', 'Fecha'),
        ('select', 'Selección (desplegable)'),
        ('checkbox_grupo', 'Casillas de verificación'),
        ('tabla', 'Tabla / Plan de Acción'),
        ('sello', 'Sello de firma electrónica'),
    ]

    ANCHO_CHOICES = [
        ('full', 'Ancho completo'),
        ('half', 'Mitad'),
    ]

    ORIGEN_CHOICES = [
        ('usuario', 'Usuario (lo llena el creador)'),
        ('nombre_solicitante', 'Nombre del solicitante (auto)'),
        ('fecha_solicitud', 'Fecha de solicitud (auto)'),
        ('num_solicitud', 'Nº de solicitud (auto)'),
        ('evaluado_por_nombre', 'Nombre evaluador (auto)'),
        ('evaluado_por_cargo', 'Cargo evaluador (auto)'),
        ('evaluado_por_fecha', 'Fecha evaluación (auto)'),
        ('autorizado_por_nombre', 'Nombre autorizador (auto)'),
        ('autorizado_por_cargo', 'Cargo autorizador (auto)'),
        ('autorizado_por_fecha', 'Fecha autorización (auto)'),
        ('resolucion', 'Resolución (auto)'),
        ('operacion', 'Operación del cambio (auto)'),
        ('tipo_informacion', 'Tipo de información documentada (auto)'),
        ('num_identificacion', 'Nº de identificación del documento (auto)'),
        ('titulo_documento', 'Título del documento (auto)'),
        ('version_documento', 'Versión del documento (auto)'),
        ('edicion_documento', 'Edición del documento (auto)'),
        ('fecha_vigencia_documento', 'Fecha de vigencia del documento (auto)'),
        ('descripcion_solicitud', 'Descripción de la solicitud (auto)'),
        ('sello_firma', 'Sello de firma electrónica (auto)'),
    ]

    seccion = models.ForeignKey(
        SeccionFormato, on_delete=models.CASCADE, related_name='campos'
    )
    clave = models.SlugField(max_length=60, verbose_name='Clave interna')
    etiqueta = models.CharField(max_length=300, verbose_name='Etiqueta')
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, verbose_name='Tipo')
    origen = models.CharField(
        max_length=30, choices=ORIGEN_CHOICES, default='usuario',
        verbose_name='Origen del valor'
    )
    ayuda = models.TextField(blank=True, default='', verbose_name='Texto de ayuda')
    opciones = models.JSONField(
        default=list, blank=True,
        verbose_name='Opciones',
        help_text='Para select: lista de valores. Para checkbox_grupo: lista de {valor, etiqueta}. Para tabla: columnas [{clave, etiqueta, ancho}].'
    )
    requerido = models.BooleanField(default=False, verbose_name='Requerido')
    ancho = models.CharField(
        max_length=10, choices=ANCHO_CHOICES, default='full', verbose_name='Ancho'
    )
    orden = models.PositiveIntegerField(default=0, verbose_name='Orden')
    celda_excel = models.CharField(
        max_length=30, blank=True, default='',
        verbose_name='Celda Excel (export)',
        help_text='Ej. B8, A19, etc.'
    )
    config_tabla = models.JSONField(
        default=dict, blank=True,
        verbose_name='Configuración de tabla',
        help_text='fila_inicio, fila_fin, columnas con mapeo de celdas.'
    )
    incluir_etiqueta = models.BooleanField(
        default=False,
        verbose_name='Incluir etiqueta en export',
        help_text='Si está marcado, el export escribe "Etiqueta: valor" en la celda.'
    )

    class Meta:
        verbose_name = 'Campo del Formato'
        verbose_name_plural = 'Campos del Formato'
        ordering = ['seccion__orden', 'orden']
        unique_together = ('seccion', 'clave')

    def __str__(self):
        return f"{self.seccion.formato.codigo} → {self.etiqueta}"


# =============================================================================
# SOLICITUD DE CAMBIO (respuestas al formato dinámico)
# =============================================================================

class SolicitudCambio(models.Model):
    OPERACION_CHOICES = [
        ('creacion', 'Creación'),
        ('modificacion', 'Modificación'),
        ('eliminacion', 'Eliminación'),
    ]

    ESTADO_CHOICES = [
        ('borrador', 'Borrador'),
        ('en_revision', 'En Revisión'),
        ('aprobado', 'Aprobado'),
        ('rechazado', 'Rechazado'),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID')
    codigo = models.CharField(max_length=50, unique=True, verbose_name='Código')
    formato = models.ForeignKey(
        Formato, on_delete=models.PROTECT, related_name='solicitudes',
        verbose_name='Formato'
    )
    operacion = models.CharField(max_length=20, choices=OPERACION_CHOICES, verbose_name='Operación')
    documento = models.ForeignKey(
        'Documento', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='solicitudes_cambio', verbose_name='Documento asociado'
    )
    solicitante = models.ForeignKey(
        'auth.User', on_delete=models.PROTECT, related_name='solicitudes_cambio',
        verbose_name='Solicitante'
    )
    departamento = models.ForeignKey(
        'Departamento', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='solicitudes_cambio', verbose_name='Departamento'
    )
    respuestas = models.JSONField(
        default=dict, blank=True,
        verbose_name='Respuestas',
        help_text='Respuestas al formato: {clave_campo: valor}'
    )
    origen_cambio = models.CharField(
        max_length=100, blank=True, default='',
        verbose_name='Origen del cambio (para numeración)'
    )
    num_solicitud = models.CharField(
        max_length=20, blank=True, default='',
        verbose_name='Nº de solicitud (DDMMAA-XXX)'
    )
    formato_version = models.CharField(
        max_length=20, blank=True, default='',
        verbose_name='Versión del formato (snapshot)'
    )
    formato_edicion = models.CharField(
        max_length=20, blank=True, default='',
        verbose_name='Edición del formato (snapshot)'
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='borrador')
    archivo_adjunto = models.FileField(
        upload_to='solicitudes_cambio/adjuntos/%Y/%m/', blank=True, null=True,
        verbose_name='Archivo adjunto',
        validators=[validar_archivo_documento],
    )
    planilla_xlsx = models.FileField(
        upload_to='solicitudes_cambio/planillas/%Y/%m/', blank=True, null=True,
        verbose_name='Planilla XLSX generada',
        help_text='Planilla del formato (SGC-FOR-001) rellenada, generada al aprobar.',
    )
    planilla_pdf = models.FileField(
        upload_to='solicitudes_cambio/planillas/%Y/%m/', blank=True, null=True,
        verbose_name='Planilla PDF generada',
        help_text='PDF de la planilla generada (si LibreOffice está disponible).',
    )
    observaciones = models.TextField(blank=True, default='', verbose_name='Observaciones')
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Solicitud de Cambio'
        verbose_name_plural = 'Solicitudes de Cambio'
        ordering = ['-fecha_creacion']
        constraints = [
            models.UniqueConstraint(
                fields=['formato', 'origen_cambio', 'num_solicitud'],
                condition=~models.Q(num_solicitud=''),
                name='unique_num_solicitud_por_formato_origen',
            ),
        ]

    def __str__(self):
        return f"{self.codigo} ({self.get_operacion_display()})"

    def save(self, *args, **kwargs):
        # Snapshot de la versión/edición del formato al crear la solicitud.
        if self.formato_id and (not self.formato_version or not self.formato_edicion):
            if not self.formato_version:
                self.formato_version = self.formato.version or ''
            if not self.formato_edicion:
                self.formato_edicion = self.formato.edicion or ''
            update_fields = kwargs.get('update_fields')
            if update_fields is not None:
                kwargs['update_fields'] = list(
                    set(update_fields) | {'formato_version', 'formato_edicion'}
                )
        super().save(*args, **kwargs)

    def get_respuesta(self, clave, default=None):
        return self.respuestas.get(clave, default)

    def get_resumen(self):
        """Retorna un resumen legible de las respuestas clave."""
        resumen = {}
        for campo in self.formato.get_todos_los_campos():
            valor = self.get_respuesta(campo.clave)
            if valor is not None:
                resumen[campo.etiqueta] = valor
        return resumen


class FormatoPublicacion(models.Model):
    """Historial de publicaciones (nueva versión / nueva edición) de un Formato."""

    TIPO_CHOICES = [
        ('version', 'Nueva Versión'),
        ('edicion', 'Nueva Edición'),
    ]

    formato = models.ForeignKey(
        Formato, on_delete=models.CASCADE, related_name='publicaciones',
        verbose_name='Formato'
    )
    tipo = models.CharField(max_length=10, choices=TIPO_CHOICES, verbose_name='Tipo')
    version_anterior = models.CharField(max_length=20, blank=True, default='')
    version_nueva = models.CharField(max_length=20, blank=True, default='')
    edicion_anterior = models.CharField(max_length=20, blank=True, default='')
    edicion_nueva = models.CharField(max_length=20, blank=True, default='')
    observaciones = models.TextField(blank=True, default='', verbose_name='Observaciones')
    publicado_por = models.ForeignKey(
        'auth.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='publicaciones_formato', verbose_name='Publicado por'
    )
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Publicación de Formato'
        verbose_name_plural = 'Publicaciones de Formato'
        ordering = ['-fecha']

    def __str__(self):
        return f"{self.formato.codigo} → {self.get_tipo_display()} ({self.fecha:%d/%m/%Y})"


class FormatoRespuesta(models.Model):
    """Respuestas de un formato para un origen que NO es ``SolicitudCambio``.

    Se usa para la planilla SGC-FOR-002 generada desde la distribución: una
    fila por lote (``origen_tipo='distribucion'``, ``origen_id=<uuid del lote>``).
    El par ``(origen_tipo, origen_id)`` es único, de modo que regenerar la
    planilla de un lote actualiza la misma fila en lugar de duplicarla.
    """

    formato = models.ForeignKey(
        Formato, on_delete=models.CASCADE, related_name='respuestas',
        verbose_name='Formato'
    )
    origen_tipo = models.CharField(
        max_length=30, verbose_name='Tipo de origen',
        help_text="Ej. 'distribucion'."
    )
    origen_id = models.CharField(
        max_length=64, verbose_name='Id de origen',
        help_text="Ej. uuid del lote de distribución."
    )
    respuestas = models.JSONField(
        default=dict, blank=True, verbose_name='Respuestas'
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Respuesta de Formato'
        verbose_name_plural = 'Respuestas de Formatos'
        constraints = [
            models.UniqueConstraint(
                fields=['origen_tipo', 'origen_id'],
                name='formato_resp_origen_unico',
            ),
        ]

    def __str__(self):
        return f"{self.formato.codigo} ← {self.origen_tipo}:{self.origen_id}"


class RegistroFormato(models.Model):
    """Instancia LLENA de un ``Formato`` (registro/evidencia), con contexto.

    El código visible es el del formato (``SGC-FOR-010``) y **se repite** por
    diseño: dos registros para DPR-005 y DPR-006 comparten código. La
    identidad es ``uuid``. No es un ``Documento`` controlado (CONTEXT §32).
    """

    ESTADO_CHOICES = [
        ('borrador', 'Borrador'),
        ('en_revision', 'En Revisión'),
        ('aprobado', 'Aprobado'),
        ('rechazado', 'Rechazado'),
        ('obsoleto', 'Obsoleto'),
    ]

    uuid = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID'
    )
    formato = models.ForeignKey(
        Formato, on_delete=models.PROTECT, related_name='registros',
        verbose_name='Formato',
    )
    #: Snapshot del formato al crear el registro (el código no cambia, pero
    #: se conserva por trazabilidad de edición/versión).
    formato_codigo = models.CharField(max_length=30, verbose_name='Código del formato')
    formato_version = models.CharField(
        max_length=20, blank=True, default='', verbose_name='Versión del formato'
    )
    formato_edicion = models.CharField(
        max_length=20, blank=True, default='', verbose_name='Edición del formato'
    )
    #: Contexto genérico (primer caso: 'documento' + uuid del DPR).
    contexto_tipo = models.CharField(
        max_length=30, db_index=True, verbose_name='Tipo de contexto'
    )
    contexto_id = models.CharField(
        max_length=64, db_index=True, verbose_name='Id del contexto'
    )
    titulo = models.CharField(max_length=200, verbose_name='Título')
    descripcion = models.TextField(blank=True, default='', verbose_name='Descripción')
    fecha = models.DateField(default=timezone.localdate, verbose_name='Fecha')
    responsable = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='registros_formato',
        verbose_name='Responsable',
    )
    estado = models.CharField(
        max_length=20, choices=ESTADO_CHOICES, default='borrador',
        verbose_name='Estado',
    )
    respuestas = models.JSONField(default=dict, blank=True, verbose_name='Respuestas')
    archivo = models.FileField(
        upload_to=generar_ruta_registro, blank=True, null=True,
        validators=[validar_archivo_documento], verbose_name='Archivo',
    )
    archivo_pdf = models.FileField(
        upload_to=generar_ruta_registro, blank=True, null=True,
        verbose_name='Archivo PDF',
    )
    hash_integridad = models.CharField(
        max_length=64, blank=True, default='',
        verbose_name='Hash de integridad (SHA-256)',
    )
    aprobador = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='registros_formato_aprobados',
        verbose_name='Aprobador',
    )
    fecha_aprobacion = models.DateTimeField(
        null=True, blank=True, verbose_name='Aprobado en'
    )
    observaciones = models.TextField(
        blank=True, default='', verbose_name='Observaciones'
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Registro de Formato'
        verbose_name_plural = 'Registros de Formatos'
        ordering = ['-creado']
        indexes = [
            models.Index(
                fields=['contexto_tipo', 'contexto_id'],
                name='registro_ctx_idx',
            ),
            models.Index(
                fields=['formato', 'estado'], name='registro_fmt_estado_idx',
            ),
        ]
        constraints = [
            # Un único programa anual por formato (FOR-007). El filtro por
            # contexto_tipo evita afectar a los demás registros (FOR-010).
            models.UniqueConstraint(
                fields=['formato', 'contexto_id'],
                condition=models.Q(contexto_tipo='programa_revision'),
                name='registro_programa_anual_unico',
            ),
        ]

    def __str__(self):
        return f"{self.formato_codigo} · {self.titulo} ({self.uuid})"

    @property
    def codigo(self):
        """Código visible = el del formato (puede repetirse)."""
        return self.formato_codigo or (
            self.formato.codigo if self.formato_id else ''
        )


class VersionRegistroFormato(models.Model):
    """Snapshot inmutable de un ``RegistroFormato`` (traza ISO).

    Guarda el estado de ``respuestas`` y de la **estructura/config** del
    formato en ese momento, con hash SHA-256 para integridad. Se usa para la
    traza de versiones de registros vivos (p. ej. el programa FOR-007); el
    estado actual sigue viviendo en ``RegistroFormato.respuestas``.
    """

    registro = models.ForeignKey(
        RegistroFormato, on_delete=models.CASCADE, related_name='versiones',
        verbose_name='Registro',
    )
    numero = models.PositiveIntegerField(verbose_name='Número de versión')
    datos = models.JSONField(
        default=dict, blank=True, verbose_name='Snapshot',
        help_text='Respuestas y configuración de estructura del momento.',
    )
    resumen = models.TextField(
        blank=True, default='', verbose_name='Resumen de cambios',
    )
    hash = models.CharField(
        max_length=64, blank=True, default='', verbose_name='SHA-256',
    )
    creado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='versiones_registro_formato',
        verbose_name='Creado por',
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Versión de Registro de Formato'
        verbose_name_plural = 'Versiones de Registros de Formato'
        ordering = ['-numero']
        constraints = [
            models.UniqueConstraint(
                fields=['registro', 'numero'],
                name='version_registro_numero_unico',
            ),
        ]

    def __str__(self):
        return f'v{self.numero} · {self.registro_id}'


class ResumenIAUso(models.Model):
    """Contador diario de resúmenes IA generados por usuario."""

    usuario = models.ForeignKey(
        'auth.User', on_delete=models.CASCADE, related_name='resumenes_ia_uso',
        verbose_name='Usuario'
    )
    fecha = models.DateField(verbose_name='Fecha')
    contador = models.PositiveIntegerField(default=0, verbose_name='Contador')

    class Meta:
        verbose_name = 'Uso de Resumen IA'
        verbose_name_plural = 'Usos de Resumen IA'
        unique_together = ['usuario', 'fecha']
        ordering = ['-fecha']

    def __str__(self):
        return f"{self.usuario.username} {self.fecha}: {self.contador}"


class ConversionPDFDiagnostico(models.Model):
    """Resultado del QA de maquetación de un PDF convertido.

    Desacoplado del artefacto (documento, planilla, plantilla…) a propósito:
    se identifica por las rutas de origen y PDF, de modo que el conversor
    genérico pueda registrar y reparar sin conocer el modelo de turno.
    """

    origen = models.CharField(max_length=500, db_index=True, verbose_name='Archivo origen')
    pdf = models.CharField(max_length=500, db_index=True, verbose_name='PDF generado')
    defectos = models.JSONField(default=list, blank=True, verbose_name='Defectos')
    estado = models.CharField(
        max_length=20, default='ok',
        choices=[
            ('ok', 'Limpio'),
            ('pendiente', 'Reparación pendiente'),
            ('reparado', 'Reparado'),
            ('fallido', 'No se pudo reparar'),
        ],
        verbose_name='Estado',
    )
    intentos = models.PositiveSmallIntegerField(default=0, verbose_name='Intentos')
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Diagnóstico de conversión PDF'
        verbose_name_plural = 'Diagnósticos de conversión PDF'
        ordering = ['-actualizado']

    def __str__(self):
        return f"{self.origen} → {self.get_estado_display()}"


# =============================================================================
# FIRMA ELECTRÓNICA (SES) — MOTOR ÚNICO
# =============================================================================
#
# Un solo modelo y una sola función de firma (``services/firmas.firmar``). El
# documento firmado se identifica con ``origen_tipo`` + ``origen_id``, así que
# agregar un documento firmable no requiere otra tabla ni otro flujo.
#
# * Interna: OTP por correo + PIN local (``FirmaCredencial``). El PIN se
#   guarda con ``make_password`` y JAMÁS se re-valida contra AD (riesgo de
#   bloqueo de cuenta real).
# * Externa: invitación tokenizada (``InvitacionFirma``), 7 días, un solo uso.
#   Solo se guarda el HASH del token: si la BD se filtra no se puede firmar.
# * Sistema: auto-aprobaciones (no hay un acto humano que firmar).
#
# La firma interna es ASÍNCRONA: no bloquea el cierre de la distribución. El
# FOR-002 simplemente muestra la fila como ``pendiente`` hasta firmar.

#: ``_meta.model_name`` → ``origen_tipo`` de la firma.
FIRMA_ORIGEN_POR_MODELO = {
    'distribuciondocumento': 'distribucion',
    'distribucionregistro': 'distribucion_registro',
    'aprobacionplanilla': 'aprobacion_planilla',
    'solicitudaprobaciondocumento': 'solicitud_aprobacion_documento',
    'registroformato': 'registro_formato',
    'actadesincorporacion': 'acta_desincorporacion',
}

#: ``origen_tipo`` → ``_meta.model_name``.
FIRMA_MODELO_POR_ORIGEN = {
    origen: modelo for modelo, origen in FIRMA_ORIGEN_POR_MODELO.items()
}


class FirmaCredencial(models.Model):
    """PIN local de firma electrónica de un usuario interno."""

    usuario = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='firma_credencial',
        verbose_name='Usuario'
    )
    pin_hash = models.CharField(
        max_length=128, verbose_name='Hash del PIN',
        help_text='PIN hasheado con make_password; nunca en texto plano.'
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    intentos_fallidos = models.PositiveSmallIntegerField(
        default=0, verbose_name='Intentos fallidos'
    )
    bloqueado_hasta = models.DateTimeField(
        null=True, blank=True, verbose_name='Bloqueado hasta',
        help_text='Bloqueo temporal tras intentos fallidos de PIN.'
    )

    class Meta:
        verbose_name = 'Credencial de Firma'
        verbose_name_plural = 'Credenciales de Firma'

    def __str__(self):
        return f"PIN de {self.usuario.username}"

    @property
    def esta_bloqueada(self):
        from django.utils import timezone
        if not self.bloqueado_hasta:
            return False
        if self.bloqueado_hasta <= timezone.now():
            return False
        return True


class FirmaElectronica(models.Model):
    """Firma electrónica de UN objeto firmable por UN firmante.

    ``firmante_clave`` normaliza internos (``user:<id>``) y externos
    (``email:<correo>``) para que la unicidad (origen + firmante) aplique igual
    a los dos casos (un externo no puede firmar dos veces la misma planilla).

    El objeto firmado se identifica con ``origen_tipo`` + ``origen_id``
    (migración 0059). Ver ``FIRMA_ORIGEN_POR_MODELO``; usar
    :meth:`para` para consultar las firmas de una instancia.
    """

    TIPO_CHOICES = [
        ('interna', 'Interna (OTP + PIN)'),
        ('externa', 'Externa (invitación)'),
        ('sistema', 'Sistema (automática)'),
    ]
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('firmada', 'Firmada'),
        ('expirada', 'Expirada'),
        ('rechazada', 'Rechazada'),
    ]

    #: Tipo del objeto firmado (ver ``FIRMA_ORIGEN_POR_MODELO``).
    origen_tipo = models.CharField(
        max_length=30, db_index=True, verbose_name='Tipo de origen'
    )
    #: ``str(pk)`` del objeto firmado.
    origen_id = models.CharField(
        max_length=64, verbose_name='Id de origen'
    )
    tipo = models.CharField(
        max_length=10, choices=TIPO_CHOICES, verbose_name='Tipo de firma'
    )
    #: Clave única del firmante dentro del origen (ver docstring).
    firmante_clave = models.CharField(
        max_length=220, verbose_name='Clave de firmante'
    )
    #: Interno: el usuario que firma. Externo: ``None``.
    firmante = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='firmas_electronicas',
        null=True, blank=True, verbose_name='Usuario firmante'
    )
    firmante_nombre = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Nombre del firmante'
    )
    firmante_email = models.EmailField(
        blank=True, default='', verbose_name='Correo del firmante'
    )
    firmante_cargo = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Cargo del firmante'
    )
    estado = models.CharField(
        max_length=20, choices=ESTADO_CHOICES, default='pendiente',
        verbose_name='Estado'
    )
    #: OTP vigente (hash) para firmas internas.
    otp_hash = models.CharField(
        max_length=128, blank=True, default='', verbose_name='Hash del OTP'
    )
    otp_expira = models.DateTimeField(
        null=True, blank=True, verbose_name='OTP expira en'
    )
    otp_intentos = models.PositiveSmallIntegerField(
        default=0, verbose_name='Intentos de OTP'
    )
    fecha_firma = models.DateTimeField(
        null=True, blank=True, verbose_name='Firmada en'
    )
    #: SHA-256 de lo firmado (XLSX de la planilla FOR-002 del lote).
    hash_contenido = models.CharField(
        max_length=64, blank=True, default='', verbose_name='Hash del contenido'
    )
    algoritmo = models.CharField(
        max_length=30, default='sha256', verbose_name='Algoritmo'
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True, verbose_name='IP'
    )
    user_agent = models.TextField(blank=True, default='', verbose_name='User-Agent')
    datos = models.JSONField(
        default=dict, blank=True, verbose_name='Datos de la firma',
        help_text='Extras: consentimiento, método, etc.'
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Firma Electrónica'
        verbose_name_plural = 'Firmas Electrónicas'
        ordering = ['-creado']
        constraints = [
            models.UniqueConstraint(
                fields=['origen_tipo', 'origen_id', 'firmante_clave'],
                name='firma_origen_firmante_unico',
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} {self.firmante_nombre or self.firmante_clave} ({self.estado})"

    @classmethod
    def para(cls, obj):
        """QuerySet de firmas del objeto ``obj`` (vacío si no es firmable)."""
        if obj is None or obj.pk is None:
            return cls.objects.none()
        tipo = FIRMA_ORIGEN_POR_MODELO.get(obj._meta.model_name)
        if tipo is None:
            return cls.objects.none()
        return cls.objects.filter(origen_tipo=tipo, origen_id=str(obj.pk))

    @property
    def origen(self):
        """Instancia firmada (``None`` si ya no existe o el tipo es inválido)."""
        from django.apps import apps

        modelo = FIRMA_MODELO_POR_ORIGEN.get(self.origen_tipo)
        if not modelo:
            return None
        try:
            return apps.get_model('documentos', modelo)._default_manager.filter(
                pk=self.origen_id,
            ).first()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def clave_interna(usuario):
        """Clave de firmante para un usuario interno."""
        return f'user:{usuario.pk}'

    @staticmethod
    def clave_externa(email):
        """Clave de firmante para un externo (correo normalizado)."""
        return f'email:{(email or "").strip().lower()}'


class InvitacionFirma(models.Model):
    """Invitación tokenizada para firmar una distribución (uso externo).

    El token vive 7 días y es de UN SOLO USO: al usarse se marca ``usado`` y
    la firma queda ``firmada``. Solo se persiste su SHA-256.
    """

    firma = models.ForeignKey(
        FirmaElectronica, on_delete=models.CASCADE, related_name='invitaciones',
        verbose_name='Firma'
    )
    token_hash = models.CharField(
        max_length=64, unique=True, verbose_name='Hash del token',
        help_text='SHA-256 del token; el token en claro solo va en el correo.'
    )
    creado = models.DateTimeField(auto_now_add=True)
    expira = models.DateTimeField(verbose_name='Expira en')
    usado = models.BooleanField(default=False, verbose_name='Usado')
    fecha_uso = models.DateTimeField(
        null=True, blank=True, verbose_name='Usada en'
    )
    email_enviado = models.BooleanField(
        default=False, verbose_name='Email enviado'
    )
    error_envio = models.TextField(
        blank=True, default='', verbose_name='Error de envío'
    )

    class Meta:
        verbose_name = 'Invitación de Firma'
        verbose_name_plural = 'Invitaciones de Firma'
        ordering = ['-creado']

    def __str__(self):
        estado = 'usada' if self.usado else f'vence {self.expira:%d/%m/%Y}'
        return f"Invitación {self.firma_id} ({estado})"

    @property
    def esta_vigente(self):
        """True si la invitación sigue usable (no usada y no vencida)."""
        from django.utils import timezone
        if self.usado:
            return False
        return self.expira > timezone.now()


class DistribucionRegistro(models.Model):
    """Distribución de un ``RegistroFormato`` aprobado y su acuse.

    Espejo liviano de :class:`DistribucionDocumento` (P1): destinatario
    interno (cuenta en POLIDOC) o externo (datos de contacto), método de
    entrega y estado del acuse. La firma del acuse y el enlace seguro de
    descarga reutilizan ``FirmaElectronica`` + ``InvitacionFirma`` con
    origen ``distribucion_registro`` (no hay un token propio: una sola
    cadena de tokens, revocable y con caducidad de 7 días).
    """

    METODO_CHOICES = [
        ('sistema', 'Sistema'),
        ('email', 'Email'),
        ('impreso', 'Impreso'),
    ]

    ESTADO_ACUSE_CHOICES = [
        ('pendiente', 'Acuse pendiente'),
        ('acusado', 'Acusado'),
        ('vencido', 'Invitación vencida'),
        ('revocado', 'Revocado'),
    ]

    registro = models.ForeignKey(
        RegistroFormato, on_delete=models.CASCADE,
        related_name='distribuciones', verbose_name='Registro',
    )
    #: ``None`` cuando el destinatario es EXTERNO (no tiene cuenta en POLIDOC).
    usuario = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True,
        related_name='registros_recibidos', verbose_name='Usuario destinatario',
    )
    nombre_externo = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Nombre (externo)'
    )
    cargo_externo = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Cargo (externo)'
    )
    email_externo = models.EmailField(
        blank=True, default='', verbose_name='Correo (externo)'
    )
    organizacion_externa = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Organización (externa)'
    )
    fecha_distribucion = models.DateTimeField(auto_now_add=True)
    metodo = models.CharField(max_length=20, choices=METODO_CHOICES, default='sistema')
    distribuido_por = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name='registros_distribuidos', verbose_name='Distribuido por',
    )
    observaciones = models.TextField(blank=True, default='')
    #: Entrega confirmada (sistema/impreso al distribuir; email al enviarse).
    fecha_entrega = models.DateTimeField(
        null=True, blank=True, verbose_name='Fecha de entrega',
    )
    #: Estado del acuse de conformidad del destinatario.
    estado_acuse = models.CharField(
        max_length=20, choices=ESTADO_ACUSE_CHOICES, default='pendiente',
        verbose_name='Estado del acuse',
    )
    fecha_acuse = models.DateTimeField(
        null=True, blank=True, verbose_name='Acusado en',
    )
    #: Acuse firmado (SES). La IP/UA del acuse viven en la firma; la
    #: eliminación de la firma está protegida para no perder evidencia.
    firma = models.ForeignKey(
        'FirmaElectronica', on_delete=models.PROTECT, null=True, blank=True,
        related_name='acuses_registro', verbose_name='Firma del acuse',
    )
    #: Escaneo del acuse firmado en papel (método impreso).
    acuse_archivo = models.FileField(
        upload_to='acuses/registros/%Y/%m/', blank=True, null=True,
        validators=[validar_archivo_acuse],
        verbose_name='Acuse escaneado',
        help_text='Escaneo del acuse firmado en papel (método impreso).',
    )
    adjunto_pdf_enviado = models.BooleanField(
        default=False, verbose_name='Adjunto enviado',
        help_text='True si el PDF del registro se adjuntó al correo.',
    )
    motivo_sin_adjunto = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Motivo sin adjunto',
        help_text='Por qué no se adjuntó el archivo (política de clasificación).',
    )
    enviado = models.BooleanField(
        default=False, verbose_name='Email enviado',
        help_text='True si el correo de distribución se envió correctamente.',
    )
    error = models.TextField(
        blank=True, default='', verbose_name='Error de envío',
        help_text='Detalle si falló el envío del correo.',
    )
    revocado = models.BooleanField(
        default=False, verbose_name='Revocado',
        help_text='True si la distribución fue revocada (bloquea el enlace).',
    )
    fecha_revocacion = models.DateTimeField(
        null=True, blank=True, verbose_name='Fecha de revocación',
    )
    revocado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='registros_distribuciones_revocadas',
        verbose_name='Revocado por',
    )
    motivo_revocacion = models.TextField(
        blank=True, default='', verbose_name='Motivo de revocación',
    )

    class Meta:
        verbose_name = 'Distribución de Registro'
        verbose_name_plural = 'Distribuciones de Registros'
        ordering = ['-fecha_distribucion']
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(usuario__isnull=False, nombre_externo='')
                    | models.Q(usuario__isnull=True, nombre_externo__gt='')
                ),
                name='reg_dist_destino_interno_o_externo',
            ),
        ]

    def __str__(self):
        if self.usuario_id:
            return f"Acuse de {self.registro.formato_codigo} a {self.usuario.username}"
        return f"Acuse de {self.registro.formato_codigo} a {self.nombre_externo}"

    @property
    def es_externo(self):
        """True cuando el destinatario no tiene cuenta en POLIDOC."""
        return self.usuario_id is None

    @property
    def destinatario_nombre(self):
        if self.usuario_id:
            return self.usuario.get_full_name() or self.usuario.username
        return self.nombre_externo

    @property
    def destinatario_email(self):
        if self.usuario_id:
            return self.usuario.email or ''
        return self.email_externo or ''

    @property
    def acuse_pendiente(self):
        """True si la distribución espera el acuse del destinatario."""
        return self.estado_acuse == 'pendiente'

    @property
    def firmas(self):
        """QuerySet de ``FirmaElectronica`` de esta distribución."""
        return FirmaElectronica.para(self)


# ---------------------------------------------------------------------------
# SGC-FOR-004 — Acta de desincorporación de archivos inactivos
# ---------------------------------------------------------------------------

#: Texto oficial de la finalidad (plantilla real, no se altera).
PROPOSITO_FOR_004 = (
    'Con el propósito de llevar a cabo el acto de desincorporación o '
    'destrucción de los siguientes registros inactivos, por haber finalizado '
    'su tiempo de retención:'
)

#: Estados en los que un acta "abierta" ocupa la única plaza por documento.
ESTADOS_ACTA_ABIERTA = ('borrador', 'pendiente_firma')


class ActaDesincorporacion(models.Model):
    """Acta de desincorporación de archivos inactivos (SGC-FOR-004).

    Se genera desde la ficha de un documento **padre** cuando uno de sus
    ``documentos_relacionados`` pasa a ``obsoleto``. La relación solo se
    retira del padre cuando el acta queda ``aplicada`` (ambas firmas SES
    registradas).

    Un solo acta "abierta" por padre (constraint parcial): si ya hay una en
    borrador/pendiente de firma, la vista ofrece reanudarla en lugar de
    crear una segunda.
    """

    ESTADO_CHOICES = [
        ('borrador', 'Borrador'),
        ('pendiente_firma', 'Pendiente de firma'),
        ('firmada', 'Firmada'),
        ('aplicada', 'Aplicada'),
        ('anulada', 'Anulada'),
    ]

    uuid = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID',
    )
    codigo = models.CharField(
        max_length=40, unique=True, verbose_name='Código del acta',
    )
    #: Documento padre sobre cuya ficha se abre el acta. ``None`` si el padre
    #: fue eliminado: el acta conserva los snapshots y sigue siendo evidencia.
    padre = models.ForeignKey(
        Documento, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='actas_desincorporacion', verbose_name='Documento padre',
    )
    #: Instantánea del padre al crear el acta (el código nunca se imprime,
    #: pero el nombre sí forma parte del registro).
    documento_codigo = models.CharField(
        max_length=50, blank=True, default='', verbose_name='Código (snapshot)',
    )
    documento_nombre = models.CharField(
        max_length=300, blank=True, default='', verbose_name='Nombre (snapshot)',
    )
    departamento = models.ForeignKey(
        Departamento, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='actas_desincorporacion', verbose_name='Departamento',
    )
    departamento_nombre = models.CharField(
        max_length=200, blank=True, default='',
        verbose_name='Departamento (snapshot)',
    )
    fecha = models.DateField(
        default=timezone.localdate, verbose_name='Fecha del acta',
        help_text='Día en que se realiza la desincorporación.',
    )
    proposito = models.TextField(
        default=PROPOSITO_FOR_004, blank=True, verbose_name='Propósito',
    )
    estado = models.CharField(
        max_length=20, choices=ESTADO_CHOICES, default='borrador',
        verbose_name='Estado',
    )
    #: Rol de cada firma en ``FirmaElectronica.datos`` (``elaborado`` y
    #: ``aprobado``): la clave no se infiere del usuario para que la misma
    #: persona pueda ocupar ambos roles con UNA sola firma.
    firma_elaborado = models.ForeignKey(
        'FirmaElectronica', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='actas_elaboradas', verbose_name='Firma (Elaborado)',
    )
    firma_aprobado = models.ForeignKey(
        'FirmaElectronica', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='actas_aprobadas', verbose_name='Firma (Aprobado)',
    )
    xlsx = models.FileField(
        upload_to='actas_desincorporacion/xlsx/%Y/%m/', blank=True, null=True,
        validators=[validar_archivo_documento],
        verbose_name='XLSX generado',
    )
    pdf = models.FileField(
        upload_to='actas_desincorporacion/pdf/%Y/%m/', blank=True, null=True,
        verbose_name='PDF generado',
    )
    creado_por = models.ForeignKey(
        User, on_delete=models.PROTECT,
        related_name='actas_desincorporacion_creadas', verbose_name='Creado por',
    )
    #: Quién firma el bloque "Elaborado" (por defecto el creador).
    elaborado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name='actas_elaboradas', verbose_name='Elabora',
    )
    #: Quién firma el bloque "Aprobado" (por defecto el jefe efectivo del
    #: departamento; editable al crear el acta).
    aprobado_por = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True,
        related_name='actas_aprobadas', verbose_name='Aprueba',
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    aplicada_en = models.DateTimeField(
        null=True, blank=True, verbose_name='Aplicada en',
        help_text='Momento en que se retiró la relación (ambas firmas).',
    )
    anulada_en = models.DateTimeField(null=True, blank=True, verbose_name='Anulada en')
    anulada_motivo = models.TextField(
        blank=True, default='', verbose_name='Motivo de anulación',
    )

    class Meta:
        verbose_name = 'Acta de Desincorporación (SGC-FOR-004)'
        verbose_name_plural = 'Actas de Desincorporación (SGC-FOR-004)'
        ordering = ['-creado']
        indexes = [
            models.Index(fields=['padre', 'estado'], name='acta_desinc_padre_estado_idx'),
        ]
        constraints = [
            # Una sola acta "abierta" por padre. Los actas firmadas/aplicadas/
            # anuladas no compiten (histórico ilimitado).
            models.UniqueConstraint(
                fields=['padre'],
                condition=models.Q(estado__in=ESTADOS_ACTA_ABIERTA),
                name='acta_desinc_unica_abierta',
            ),
        ]

    def __str__(self):
        return f'{self.codigo} ({self.get_estado_display()})'

    @property
    def esta_abierta(self):
        """True mientras el acta puede editarse/firmarse (no cerrada)."""
        return self.estado in ESTADOS_ACTA_ABIERTA

    @property
    def firmas_completas(self):
        """True cuando ambas firmas SES están registradas."""
        return bool(self.firma_elaborado_id and self.firma_aprobado_id)

    @property
    def rol_firmante(self):
        """Rol de la última firma registrada (para saber qué falta)."""
        if not self.firma_elaborado_id:
            return 'elaborado'
        if not self.firma_aprobado_id:
            return 'aprobado'
        return ''

    def rol_para(self, usuario):
        """Rol que ``usuario`` aún puede firmar (o ``''`` si ninguno).

        La misma persona en ambos roles firma UNA sola vez: el motor enlaza
        esa firma con los dos bloques (ver ``services/firmables.py``).
        """
        if usuario is None or not self.esta_abierta:
            return ''
        mismo_elabora = self.elaborado_por_id == usuario.id
        mismo_aprueba = self.aprobado_por_id == usuario.id
        if mismo_elabora and not self.firma_elaborado_id:
            return 'elaborado'
        if mismo_aprueba and not self.firma_aprobado_id:
            return 'aprobado'
        return ''

    def firmante_asignado(self, rol):
        """Usuario asignado al ``rol`` (``elaborado``/``aprobado``)."""
        if rol == 'elaborado':
            return self.elaborado_por
        if rol == 'aprobado':
            return self.aprobado_por
        return None

    @property
    def url_descarga(self):
        if not self.padre_id:
            return ''
        return (
            f'/documentos/{self.padre.tipo_id}/{self.padre.uuid}/'
            f'acta-desincorporacion/{self.uuid}/'
        )


class ParticipanteActa(models.Model):
    """Fila de la tabla NOMBRE/CARGO del acta (filas 16–19 de la plantilla).

    La plantilla real tiene **4** filas de datos (la fila 15 es el encabezado);
    ese es el techo del formulario (no se insertan filas para no alterar el
    layout oficial).
    """

    acta = models.ForeignKey(
        ActaDesincorporacion, on_delete=models.CASCADE,
        related_name='participantes', verbose_name='Acta',
    )
    nombre = models.CharField(max_length=200, verbose_name='Nombre')
    cargo = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Cargo',
    )
    orden = models.PositiveIntegerField(default=0, verbose_name='Orden')

    class Meta:
        verbose_name = 'Participante del Acta'
        verbose_name_plural = 'Participantes del Acta'
        ordering = ['orden', 'id']

    def __str__(self):
        return f'{self.nombre} — {self.cargo}'


class RegistroDesincorporado(models.Model):
    """Fila de la lista de registros inactivos (filas 23–31 de la plantilla).

    9 filas por hoja; con más registros se agregan hojas ``SGC-FOR-004 (2)…``
    (mismo criterio que el FOR-003). Los datos se **snapshotan** al crear el
    acta: si el documento relacionado se elimina, el acta conserva código,
    nombre y fechas (``documento_relacionado`` pasa a ``NULL``).
    """

    acta = models.ForeignKey(
        ActaDesincorporacion, on_delete=models.CASCADE,
        related_name='registros', verbose_name='Acta',
    )
    documento_relacionado = models.ForeignKey(
        Documento, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='registros_desincorporacion',
        verbose_name='Documento relacionado',
    )
    codigo = models.CharField(
        max_length=50, blank=True, default='', verbose_name='Código (snapshot)',
    )
    nombre = models.CharField(
        max_length=300, blank=True, default='', verbose_name='Nombre (snapshot)',
    )
    fecha_vencimiento = models.DateField(
        null=True, blank=True, verbose_name='Fecha de vencimiento (snapshot)',
    )
    fecha_retencion = models.DateField(
        null=True, blank=True, verbose_name='Fecha de retención (snapshot)',
    )
    fecha_destruccion = models.DateField(
        null=True, blank=True, verbose_name='Fecha de destrucción (snapshot)',
    )
    orden = models.PositiveIntegerField(default=0, verbose_name='Orden')

    class Meta:
        verbose_name = 'Registro Desincorporado'
        verbose_name_plural = 'Registros Desincorporados'
        ordering = ['orden', 'id']

    def __str__(self):
        return f'{self.codigo} {self.nombre}'.strip()


# ---------------------------------------------------------------------------
# Documentos sin código (no forman parte del catálogo ISO)
# ---------------------------------------------------------------------------

def generar_ruta_documento_sin_codigo(instance, filename):
    """Ruta de un documento sin código (identidad = uuid, no hay código)."""
    extension = os.path.splitext(filename)[1] if filename else '.pdf'
    return f'documentos_sin_codigo/{instance.uuid.hex}{extension}'


class DocumentoSinCodigo(models.Model):
    """Documento que NO es información documentada controlada por ISO.

    Dos ámbitos:

    * ``sgc`` — soportes/registros de fuente institucionales (facturas,
      órdenes, tickets, etc.). Se incluyen en la Lista Maestra (SGC-FOR-006)
      como origen **Ext** con ``N/A`` en el número de identificación.
    * ``personal`` — archivos de uso personal del usuario. Privados del
      dueño (y del administrador), **no** se incluyen en la Lista Maestra.

    No tiene ``codigo`` (la identidad es el ``uuid``) y no entra en el
    catálogo de documentos ISO (`Documento`) ni en ``mis_documentos``.
    """

    AMBITO_CHOICES = [
        ('sgc', 'Institucional / SGC'),
        ('personal', 'Personal'),
    ]

    uuid = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, verbose_name='UUID',
    )
    titulo = models.CharField(max_length=200, verbose_name='Título')
    descripcion = models.TextField(blank=True, default='', verbose_name='Descripción')
    archivo = models.FileField(
        upload_to=generar_ruta_documento_sin_codigo, blank=True, null=True,
        validators=[validar_archivo_documento], verbose_name='Archivo',
    )
    tipo_archivo = models.CharField(
        max_length=10, choices=Documento.TIPO_ARCHIVO_CHOICES, default='pdf',
        verbose_name='Tipo de archivo',
    )
    #: Referencia externa opcional (nº de factura, ticket, orden…).
    referencia = models.CharField(
        max_length=200, blank=True, default='', verbose_name='Referencia externa',
    )
    ambito = models.CharField(
        max_length=20, choices=AMBITO_CHOICES, default='personal',
        verbose_name='Ámbito',
    )
    clasificacion = models.CharField(
        max_length=20, choices=Documento.CLASIFICACION_CHOICES,
        default='interno', verbose_name='Clasificación',
    )
    empresa = models.CharField(
        max_length=150, blank=True, default='', verbose_name='Empresa',
        help_text='Empresa o razón social a la que pertenece el documento.',
    )
    departamento = models.ForeignKey(
        Departamento, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_sin_codigo', verbose_name='Departamento',
    )
    propietario = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='documentos_sin_codigo',
        verbose_name='Propietario',
    )
    #: Responsable como texto libre (puede no ser un usuario, p. ej.
    #: "Junta Directiva"). Para la Lista Maestra alimenta la columna
    #: Responsable.
    responsable_texto = models.CharField(
        max_length=300, blank=True, default='', verbose_name='Responsable (texto)',
    )
    fecha = models.DateField(null=True, blank=True, verbose_name='Fecha del documento')
    periodo_retencion = models.ForeignKey(
        Periodo, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_sin_codigo_retencion',
        verbose_name='Período de retención',
    )
    #: "Forma de archivo y ubicación" (texto libre).
    ubicacion = models.CharField(
        max_length=300, blank=True, default='', verbose_name='Forma de archivo y ubicación',
    )
    activo = models.BooleanField(default=True, verbose_name='Activo')
    #: Purga de personales: fecha en que se avisó/marcó para eliminar.
    marcado_para_eliminar_en = models.DateTimeField(
        null=True, blank=True, verbose_name='Marcado para eliminar en',
    )
    eliminado_en = models.DateTimeField(
        null=True, blank=True, verbose_name='Eliminado en',
    )
    creado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='documentos_sin_codigo_creados', verbose_name='Creado por',
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Igual que ``Documento``: el tipo se infiere de la extensión real
        # (evita que un .xlsx/.docx quede como 'pdf').
        if self.archivo:
            nuevo_tipo = tipo_archivo_desde_nombre(
                self.archivo.name, self.tipo_archivo,
            )
            if nuevo_tipo != self.tipo_archivo:
                self.tipo_archivo = nuevo_tipo
                update_fields = kwargs.get('update_fields')
                if (update_fields is not None
                        and 'tipo_archivo' not in update_fields):
                    kwargs['update_fields'] = list(update_fields) + ['tipo_archivo']
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'Documento sin código'
        verbose_name_plural = 'Documentos sin código'
        ordering = ['-creado']
        indexes = [
            models.Index(fields=['ambito', 'activo'], name='doc_sincod_ambito_idx'),
            models.Index(
                fields=['propietario', 'activo'], name='doc_sincod_prop_idx',
            ),
        ]

    def __str__(self):
        return f'{self.titulo} ({self.get_ambito_display()})'

    @property
    def es_personal(self):
        """True si es un documento personal (privado del dueño)."""
        return self.ambito == 'personal'

    @property
    def es_sgc(self):
        """True si es un soporte/registro institucional del SGC."""
        return self.ambito == 'sgc'

    @property
    def incluir_en_lista_maestra(self):
        """Solo los soportes SGC activos entran en la Lista Maestra (Ext)."""
        return self.ambito == 'sgc' and self.activo


class AvisoPurgaPersonal(models.Model):
    """Estado de avisos de purga para los documentos personales de un usuario.

    Una fila por usuario (OneToOne). Se crea al enviar el primer aviso y se
    elimina si el usuario vuelve a iniciar sesión antes del umbral (el contador
    de inactividad se reinicia con ``User.last_login``).
    """

    usuario = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='aviso_purga_personal',
        verbose_name='Usuario',
    )
    aviso_60_en = models.DateTimeField(
        null=True, blank=True, verbose_name='Aviso de 60 días enviado en',
    )
    aviso_80_en = models.DateTimeField(
        null=True, blank=True, verbose_name='Segundo aviso (80 días) enviado en',
    )

    class Meta:
        verbose_name = 'Aviso de purga de documentos personales'
        verbose_name_plural = 'Avisos de purga de documentos personales'

    def __str__(self):
        return f'Avisos de {self.usuario.username}'


class AccesoFormato(models.Model):
    """Concesión de acceso a la vista exclusiva de un formato (registro vivo).

    Se administra desde la sesión del administrador (por PERSONA, no por rol).
    El **nivel** decide si la persona solo puede ver o también editar
    (agregar/quitar filas, marcas, sincronizar, regenerar). El administrador
    siempre tiene acceso total.
    """

    NIVEL_CHOICES = [
        ('ver', 'Ver'),
        ('editar', 'Editar'),
    ]

    formato = models.ForeignKey(
        Formato, on_delete=models.PROTECT, related_name='accesos_formato',
        verbose_name='Formato',
    )
    usuario = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='accesos_formato',
        verbose_name='Usuario',
    )
    nivel = models.CharField(
        max_length=10, choices=NIVEL_CHOICES, default='editar',
        verbose_name='Nivel de acceso',
    )
    activo = models.BooleanField(default=True, verbose_name='Activo')
    creado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='accesos_formato_creados', verbose_name='Creado por',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Acceso a Formato'
        verbose_name_plural = 'Accesos a Formato'
        ordering = ['usuario__first_name', 'usuario__last_name', 'usuario__username']
        constraints = [
            models.UniqueConstraint(
                fields=['formato', 'usuario'],
                name='acceso_formato_usuario_unico',
            ),
        ]

    def __str__(self):
        return (
            f'{self.usuario.username} → {self.formato.codigo} '
            f'({self.get_nivel_display()})'
        )
