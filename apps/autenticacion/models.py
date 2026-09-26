from django.db import models
from django.contrib.auth.models import User


class PerfilUsuario(models.Model):
    """
    Perfil extendido del usuario sincronizado desde Active Directory.
    Se enlaza al User de Django mediante OneToOneField.

    Grupo AD: Seguridad PoliDoc
    Roles AD → Django:
      - doc_administrador → administrador
      - doc_jefes → jefe_departamento
      - doc_coordinador → coordinador
      - doc_trabajador → trabajador
    """
    ROL_CHOICES = [
        ('administrador', 'Administrador'),
        ('jefe_departamento', 'Jefe de Departamento'),
        ('coordinador', 'Coordinador'),
        ('trabajador', 'Trabajador'),
    ]

    GRUPOS_LDAP = [
        ('doc_administrador', 'doc_administrador'),
        ('doc_jefes', 'doc_jefes'),
        ('doc_coordinador', 'doc_coordinador'),
        ('doc_trabajador', 'doc_trabajador'),
        ('sin_grupo', 'Sin Grupo Asignado'),
    ]

    usuario = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='perfil'
    )
    grupo_ldap = models.CharField(
        max_length=50,
        choices=GRUPOS_LDAP,
        default='sin_grupo',
        verbose_name='Grupo de Active Directory'
    )
    rol = models.CharField(
        max_length=30,
        choices=ROL_CHOICES,
        default='trabajador',
        verbose_name='Rol en el Sistema'
    )
    departamento_texto = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name='Departamento (Legacy)'
    )
    departamento = models.ForeignKey(
        'documentos.Departamento',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='miembros',
        verbose_name='Departamento'
    )
    cedula_identidad = models.CharField(
        max_length=20,
        blank=True,
        default='',
        verbose_name='Cédula de Identidad'
    )
    numero_empleado = models.CharField(
        max_length=30,
        blank=True,
        default='',
        verbose_name='Número de Empleado'
    )
    ultimo_sync = models.DateTimeField(
        auto_now=True,
        verbose_name='Última sincronización'
    )
    telefono = models.CharField(
        max_length=20,
        blank=True,
        default='',
        verbose_name='Teléfono'
    )
    biografia = models.TextField(
        blank=True,
        default='',
        verbose_name='Biografía'
    )
    foto_perfil = models.ImageField(
        upload_to='perfiles/',
        blank=True,
        null=True,
        verbose_name='Foto de Perfil'
    )
    acepto_terminos = models.BooleanField(
        default=False,
        verbose_name='Aceptó Términos y Condiciones'
    )
    fecha_acepto_terminos = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Fecha de Aceptación de Términos'
    )

    class Meta:
        verbose_name = 'Perfil de Usuario'
        verbose_name_plural = 'Perfiles de Usuarios'

    def __str__(self):
        return f"{self.usuario.username} - {self.get_rol_display()}"

    @property
    def tiene_acceso(self):
        """Verifica si el usuario tiene un grupo válido (no es 'sin_grupo')."""
        return self.grupo_ldap != 'sin_grupo'

    @property
    def es_administrador(self):
        return self.rol == 'administrador'

    @property
    def es_jefe(self):
        return self.rol == 'jefe_departamento'

    @property
    def es_coordinador(self):
        return self.rol == 'coordinador'

    @property
    def es_trabajador(self):
        return self.rol == 'trabajador'

    @property
    def puede_aprobar(self):
        return self.rol in ('administrador', 'jefe_departamento')


class Notificacion(models.Model):
    TIPO_CHOICES = [
        ('solicitud_descarga', 'Solicitud de Descarga'),
        ('aprobacion_descarga', 'Descarga Aprobada'),
        ('rechazo_descarga', 'Descarga Rechazada'),
        ('documento_asignado', 'Documento Asignado'),
        ('solicitud_aprobacion', 'Documento Pendiente de Aprobación'),
        ('aprobacion_documento', 'Documento Aprobado'),
        ('rechazo_documento', 'Documento Rechazado'),
        ('planilla_pendiente', 'Planilla de Cambios Pendiente'),
        ('nueva_version', 'Nueva Versión Disponible'),
        ('propiedad_transferida', 'Propiedad Transferida'),
        ('documento_distribuido', 'Documento Distribuido'),
        ('distribucion_revocada', 'Distribución Revocada'),
        ('cambio_vigencia', 'Vigencia Establecida'),
        ('aprobacion_escalada', 'Aprobación Escalada'),
        ('edicion_formato', 'Nueva Edición de Formato'),
        ('sistema', 'Sistema'),
    ]

    usuario = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notificaciones'
    )
    titulo = models.CharField(max_length=200)
    mensaje = models.TextField()
    tipo = models.CharField(max_length=30, choices=TIPO_CHOICES, default='sistema')
    leida = models.BooleanField(default=False)
    url_accion = models.CharField(max_length=500, blank=True, default='')
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    departamento = models.ForeignKey(
        'documentos.Departamento',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='Departamento origen'
    )
    documento = models.ForeignKey(
        'documentos.Documento',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        verbose_name='Documento relacionado'
    )

    class Meta:
        verbose_name = 'Notificación'
        verbose_name_plural = 'Notificaciones'
        ordering = ['-fecha_creacion']

    def __str__(self):
        return f"{self.titulo} - {self.usuario.username}"

    def marcar_como_leida(self):
        self.leida = True
        self.save(update_fields=['leida'])
