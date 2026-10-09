import os
import uuid
from datetime import datetime
from django.db import models
from django.db.models import Max
from django.contrib.auth.models import User
from django.utils import timezone

from .validators import validar_archivo_documento


def generar_ruta_archivo(instance, filename):
    """Genera ruta ISO: documentos/{depto}/V-{cedula}/{codigo}_v{ver}_{fecha}.{ext}"""
    depto = getattr(instance, 'departamento', None)
    depto_codigo = depto.codigo if depto else 'SIN'
    cedula = instance.propietario.username
    extension = os.path.splitext(filename)[1] if filename else '.pdf'
    fecha = timezone.now().strftime('%Y%m%d')
    return f'documentos/{depto_codigo}/V-{cedula}/{instance.codigo}_v{instance.version_actual}_{fecha}{extension}'


def generar_ruta_version(instance, filename):
    """Genera ruta ISO para versiones: misma carpeta que el documento original"""
    try:
        doc = instance.documento
    except Exception:
        return f'versiones/{filename}'
    depto = getattr(doc, 'departamento', None)
    depto_codigo = depto.codigo if depto else 'SIN'
    cedula = doc.propietario.username
    extension = os.path.splitext(filename)[1] if filename else '.pdf'
    fecha = (instance.fecha_creacion or timezone.now()).strftime('%Y%m%d')
    return f'documentos/{depto_codigo}/V-{cedula}/{doc.codigo}_v{instance.numero_version}_{fecha}{extension}'


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
            'Clasificación de seguridad del documento. Sólo '
            '"Público" hace el documento visible para otros departamentos.'
        )
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
    def construir_codigo(cls, departamento, tipo, secuencia):
        """Construye el código según el tipo.

        - Normal:      {DEPTO}-{TIPO}-{SEC}  (ej. TI-PRG-001)
        - Sin prefijo: {TIPO}-{SEC}          (ej. DPR-001)
        """
        tipo_obj = TipoDocumento.objects.filter(key=tipo).first()
        tipo_codigo = tipo_obj.codigo if tipo_obj else tipo
        if cls.es_sin_prefijo_depto(tipo):
            return f"{tipo_codigo}-{secuencia}"
        return f"{departamento.codigo}-{tipo_codigo}-{secuencia}"

    @classmethod
    def generar_codigo_secuencial(cls, departamento, tipo):
        """Genera el siguiente código secuencial.

        Args:
            departamento: Instancia de Departamento (None si el tipo no lo usa).
            tipo: Key del tipo de documento (ej: 'formulario', 'descripcion_procesos')

        Los tipos con `sin_prefijo_depto` numeran de forma GLOBAL (todos los
        departamentos comparten la secuencia): DPR-001, DPR-002, ...
        """
        sin_prefijo = cls.es_sin_prefijo_depto(tipo)

        qs = cls.objects.filter(tipo__key=tipo)
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

        return cls.construir_codigo(departamento, tipo, f"{num:03d}")

    def save(self, *args, **kwargs):
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


class AprobacionDescarga(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('aprobada', 'Aprobada'),
        ('rechazada', 'Rechazada'),
    ]
    ROL_CHOICES = [
        ('jefe', 'Jefe de Departamento'),
        ('autorizador', 'Autorizador (Administrador)'),
    ]

    solicitud = models.ForeignKey(
        SolicitudDescarga, on_delete=models.CASCADE, related_name='aprobaciones'
    )
    aprobador = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='aprobaciones_descarga'
    )
    rol_aprobador = models.CharField(
        max_length=20, choices=ROL_CHOICES, default='jefe',
        verbose_name='Rol del aprobador',
        help_text='Para documentos restringidos se exige jefe + autorizador.',
    )
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendiente')
    observaciones = models.TextField(blank=True, default='')
    fecha_aprobacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Aprobación de Descarga'
        verbose_name_plural = 'Aprobaciones de Descarga'
        unique_together = ['solicitud', 'aprobador']

    def __str__(self):
        return f"Aprobación de {self.aprobador.username} - Solicitud #{self.solicitud.id}"


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

    class Meta:
        verbose_name = 'Aprobación de Planilla'
        verbose_name_plural = 'Aprobaciones de Planillas'
        unique_together = ['planilla', 'aprobador', 'rol_aprobador']

    def __str__(self):
        rol = self.get_rol_aprobador_display()
        aprobador = self.aprobador.username if self.aprobador_id else 'sin asignar'
        return f"{rol} {aprobador} - Planilla {self.planilla.codigo}"


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

    documento = models.ForeignKey(
        Documento, on_delete=models.CASCADE, related_name='distribuciones',
        null=True, blank=True
    )
    usuario = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='documentos_recibidos',
        verbose_name='Usuario destinatario'
    )
    fecha_distribucion = models.DateTimeField(auto_now_add=True)
    metodo = models.CharField(max_length=20, choices=METODO_CHOICES, default='sistema')
    distribuido_por = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='distribuciones_realizadas',
        verbose_name='Distribuido por'
    )
    observaciones = models.TextField(blank=True, default='')
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
        related_name='distribuciones_revocadas', verbose_name='Revocado por'
    )
    motivo_revocacion = models.TextField(blank=True, default='', verbose_name='Motivo de revocación')
    email_revocacion_enviado = models.BooleanField(
        default=False, verbose_name='Email de revocación enviado',
        help_text='True si el correo de revocación se envió correctamente.'
    )
    error_revocacion = models.TextField(
        blank=True, default='', verbose_name='Error de revocación',
        help_text='Detalle si falló el envío del correo de revocación.'
    )

    class Meta:
        verbose_name = 'Distribución de Documento'
        verbose_name_plural = 'Distribuciones de Documentos'
        ordering = ['-fecha_distribucion']

    def __str__(self):
        return f"Distribución {self.documento.codigo} a {self.usuario.username}"


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

    def aprobar(self, observaciones=''):
        from django.utils import timezone as tz
        self.estado = 'aprobada'
        self.observaciones = observaciones
        self.fecha_respuesta = tz.now()
        self.save(update_fields=['estado', 'observaciones', 'fecha_respuesta'])
        valores = {}
        if self.rol_aprobador == 'evaluador':
            valores.update(_valores_evaluacion(self.aprobador))
        if self.es_final:
            valores.update(_valores_autorizacion(self.aprobador))
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
        from django.utils import timezone as tz
        self.estado = 'rechazada'
        self.motivo_rechazo = motivo
        self.fecha_respuesta = tz.now()
        self.save(update_fields=['estado', 'motivo_rechazo', 'fecha_respuesta'])
        if self.rol_aprobador == 'autorizador':
            valores = _valores_autorizacion(self.aprobador)
        else:
            valores = _valores_evaluacion(self.aprobador)
        valores['resolucion'] = 'Rechazado'
        operacion = OPERACION_POR_ACCION.get(self.accion_solicitud)
        if operacion:
            autocompletar_respuestas(self.documento, valores, operacion=operacion)
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


def _valores_evaluacion(usuario):
    """Diccionario con los campos de evaluación para autorrellenar."""
    return {
        'evaluado_por_nombre': usuario.get_full_name() or usuario.username,
        'evaluado_por_cargo': _cargo_usuario(usuario),
        'evaluado_por_fecha': timezone.now().date().isoformat(),
    }


def _valores_autorizacion(usuario):
    """Diccionario con los campos de autorización para autorrellenar."""
    return {
        'autorizado_por_nombre': usuario.get_full_name() or usuario.username,
        'autorizado_por_cargo': _cargo_usuario(usuario),
        'autorizado_por_fecha': timezone.now().date().isoformat(),
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
        return self.secciones.all().order_by('orden')

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
