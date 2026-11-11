"""Integra los formatos SGC (XLSX en ``documentacion/``) al motor de formularios.

Por ahora solo está especificado el **SGC-FOR-002 (Control de Distribución)**:
su planilla se genera automáticamente por lote desde el flujo de distribución
(ver ``services/distribucion.py``). Los demás formatos quedan pendientes.

Uso::

    python manage.py integrar_formatos_sgc --solo=002            # dry-run
    python manage.py integrar_formatos_sgc --solo=002 --apply    # escribe
    python manage.py integrar_formatos_sgc --solo=002 --apply \\
        --plantilla=/ruta/otro.xlsx                              # otro origen

El valor por defecto es **dry-run**: no toca la base de datos ni copia archivos.
La estructura (secciones/campos) existente se **conserva** salvo que se pida
``--forzar-estructura``, de modo que las ediciones hechas desde el Constructor
de formatos no se pierden al re-integrar.

Nota: ``documentacion/`` no está versionado; sin el XLSX el comando falla (o
usa ``--plantilla``).
"""
import copy
import os
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from documentos.models import CampoFormato, Formato, SeccionFormato

#: Mapeo de la plantilla SGC-FOR-002 (verificado sobre el XLSX real).
#: Etiquetas en filas 10-11; valores en la fila 12.
FOR_002_CABECERA = [
    # clave, etiqueta, tipo, origen, celda
    ('tipo_informacion', 'Tipo de información documentada', 'texto',
     'tipo_informacion', 'A12'),
    ('num_identificacion', 'Nº de identificación', 'texto',
     'num_identificacion', 'E12'),
    ('titulo', 'Título', 'texto', 'titulo_documento', 'F12'),
    ('edicion', 'Nº de edición', 'texto', 'edicion_documento', 'I12'),
    ('version', 'Nº de versión', 'texto', 'version_documento', 'J12'),
    ('fecha_vigencia', 'Fecha de vigencia', 'fecha',
     'fecha_vigencia_documento', 'K12'),
]

#: Tabla de destinatarios: filas 19-51 (33 filas) del FOR-002.
#: ``rol`` identifica el dato semántico que llena la columna; la ``clave``
#: es solo el identificador interno (puede renombrarse desde el Constructor
#: sin romper la generación, porque el servicio mapea por ``rol``).
FOR_002_TABLA = {
    'fila_inicio': 19,
    'fila_fin': 51,
    'max_filas': 33,
    'columnas': [
        {'rol': 'nombre', 'clave': 'nombre',
         'etiqueta': 'Nombre del Usuario', 'celda': 'A'},
        {'rol': 'cargo', 'clave': 'cargo',
         'etiqueta': 'Cargo', 'celda': 'C'},
        {'rol': 'fecha', 'clave': 'fecha',
         'etiqueta': 'Fecha de Distribución', 'celda': 'E'},
        {
            'rol': 'retiro', 'clave': 'retiro',
            'etiqueta': 'Retiro de la versión anterior',
            'tipo': 'si_no', 'celda_si': 'G', 'celda_no': 'H',
        },
        {'rol': 'firma', 'clave': 'firma',
         'etiqueta': 'Firma conforme', 'celda': 'I'},
    ],
}

FOR_002_XLSX = 'SGC-FOR-002 Control de Dist Doc.xlsx'

#: Celda del sello de firma electrónica (debajo de la tabla de 33 filas).
FOR_002_SELLO_CELDA = 'A52'


class Command(BaseCommand):
    help = (
        'Integra los formatos SGC (documentacion/*.xlsx) al motor de '
        'formularios. Por defecto hace dry-run; usa --apply para escribir.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--solo', default='',
            help=(
                'Códigos separados por coma para procesar (ej. "002" o '
                '"SGC-FOR-002"). Vacío = todos los implementados.'
            ),
        )
        parser.add_argument(
            '--apply', action='store_true',
            help='Escribe en la base de datos y copia la plantilla a MEDIA.',
        )
        parser.add_argument(
            '--forzar-plantilla', action='store_true',
            help='Sobrescribe la plantilla XLSX aunque ya exista.',
        )
        parser.add_argument(
            '--forzar-estructura', action='store_true',
            help=(
                'Recrea secciones y campos aunque ya existan (borra los '
                'cambios hechos desde el Constructor de formatos). Por '
                'defecto la estructura existente se conserva.'
            ),
        )
        parser.add_argument(
            '--plantilla', default='',
            help=(
                'Ruta alternativa al XLSX de origen (por defecto '
                'documentacion/<nombre>). Útil para pruebas.'
            ),
        )

    def handle(self, *args, **options):
        codigos = self._codigos(options['solo'])
        aplicar = options['apply']
        forzar = options['forzar_plantilla']
        forzar_estructura = options['forzar_estructura']
        plantilla = (options.get('plantilla') or '').strip()

        self.stdout.write(self.style.MIGRATE_HEADING(
            'Integración de formatos SGC — '
            + ('APPLY (escribe)' if aplicar else 'DRY-RUN (no escribe)')
        ))

        implementados = {'SGC-FOR-002': self._integrar_for_002}
        for codigo in codigos:
            if codigo not in implementados:
                self.stdout.write(self.style.WARNING(
                    f'{codigo}: aún no tiene especificación implementada; se omite.'
                ))
                continue
            implementados[codigo](
                aplicar=aplicar, forzar=forzar,
                forzar_estructura=forzar_estructura,
                plantilla=plantilla,
            )

        self.stdout.write(self.style.SUCCESS('Proceso finalizado.'))

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _codigos(self, crudo):
        crudo = (crudo or '').strip()
        implementados = ['SGC-FOR-002']
        if not crudo:
            return implementados
        resultado = []
        for parte in crudo.split(','):
            parte = parte.strip()
            if not parte:
                continue
            codigo = parte.upper()
            if codigo.isdigit():
                codigo = f'SGC-FOR-{int(codigo):03d}'
            elif not codigo.startswith('SGC-FOR-'):
                codigo = f'SGC-FOR-{codigo}'
            resultado.append(codigo)
        return resultado or implementados

    def _ruta_plantilla(self, nombre):
        return os.path.join(settings.BASE_DIR, 'documentacion', nombre)

    def _destino_media(self, codigo):
        return os.path.join(
            settings.MEDIA_ROOT, 'formatos', 'plantillas',
            timezone.now().strftime('%Y'), f'{codigo}.xlsx',
        )

    # ------------------------------------------------------------------
    # SGC-FOR-002
    # ------------------------------------------------------------------

    def _integrar_for_002(self, aplicar, forzar, forzar_estructura=False,
                          plantilla=''):
        self.stdout.write(self.style.HTTP_INFO('\n=== SGC-FOR-002 — Control de Distribución ==='))

        origen = plantilla or self._ruta_plantilla(FOR_002_XLSX)
        if not os.path.exists(origen):
            raise CommandError(
                f'No se encontró la plantilla del FOR-002: {origen}\n'
                + (
                    'Revisa la ruta indicada con --plantilla.'
                    if plantilla else
                    'Coloca el XLSX en documentacion/ o indica su ruta con '
                    '--plantilla.'
                )
            )

        existente = Formato.objects.filter(codigo='SGC-FOR-002').first()
        estado = 'ACTUALIZAR' if existente else 'CREAR'
        self.stdout.write(f'Formato: {estado} (plantilla {os.path.basename(origen)})')
        self.stdout.write(
            f'  Campos de cabecera: {len(FOR_002_CABECERA)}; '
            f'tabla: {FOR_002_TABLA["max_filas"]} filas '
            f'({FOR_002_TABLA["fila_inicio"]}-{FOR_002_TABLA["fila_fin"]})'
        )

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '  [dry-run] No se modificó la base de datos. '
                'Repite con --apply para escribir.'
            ))
            return

        formato, _creado = Formato.objects.update_or_create(
            codigo='SGC-FOR-002',
            defaults={
                'nombre': 'Control de Distribución de Documentos',
                'version': '1',
                'edicion': '1',
                # Sin flujo de aprobación: se genera desde la distribución.
                'aplica_creacion': False,
                'aplica_modificacion': False,
                'aplica_eliminacion': False,
                'activo': True,
                'instructivo_html': '',
            },
        )

        if forzar or not formato.plantilla_xlsx:
            destino = self._destino_media('SGC-FOR-002')
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(origen, destino)
            formato.plantilla_xlsx = os.path.relpath(destino, settings.MEDIA_ROOT)
            formato.save(update_fields=['plantilla_xlsx'])
            self.stdout.write(self.style.SUCCESS(
                f'  Plantilla copiada a MEDIA: {formato.plantilla_xlsx}'
            ))
        else:
            self.stdout.write(
                '  Plantilla ya existe; no se sobrescribe (usa --forzar-plantilla).'
            )

        self._recrear_estructura_002(formato, forzar=forzar_estructura)
        self.stdout.write(self.style.SUCCESS(
            f'  FOR-002 integrado (id={formato.id}).'
        ))

    def _recrear_estructura_002(self, formato, forzar=False):
        """Crea (o recrea si ``forzar``) secciones y campos del FOR-002.

        Por defecto no pisa la estructura existente: así los cambios hechos
        desde el Constructor (etiquetas, celdas, tabla) se conservan.
        """
        if formato.secciones.exists() and not forzar:
            self.stdout.write(self.style.NOTICE(
                '  Estructura existente conservada (usa --forzar-estructura '
                'para recrearla desde la especificación).'
            ))
            self._asegurar_roles(formato)
            self._asegurar_sello(formato)
            return

        formato.secciones.all().delete()

        seccion_cabecera = SeccionFormato.objects.create(
            formato=formato, titulo='Datos del documento', orden=1,
        )
        for i, (clave, etiqueta, tipo, origen, celda) in enumerate(FOR_002_CABECERA):
            CampoFormato.objects.create(
                seccion=seccion_cabecera, clave=clave, etiqueta=etiqueta,
                tipo=tipo, origen=origen, celda_excel=celda,
                requerido=False, orden=i,
            )

        # ORIGEN EXT/INT: se marca con una X en C12 (EXT) o D12 (INT).
        CampoFormato.objects.create(
            seccion=seccion_cabecera, clave='origen_informacion',
            etiqueta='Origen de la información', tipo='select', origen='usuario',
            celda_excel='', requerido=False, orden=len(FOR_002_CABECERA),
            opciones=[
                {'valor': 'EXT', 'etiqueta': 'Externa', 'celda_marca': 'C12'},
                {'valor': 'INT', 'etiqueta': 'Interna', 'celda_marca': 'D12'},
            ],
        )

        seccion_tabla = SeccionFormato.objects.create(
            formato=formato, titulo='Control de distribución', orden=2,
        )
        CampoFormato.objects.create(
            seccion=seccion_tabla, clave='destinatarios',
            etiqueta='Destinatarios', tipo='tabla', origen='usuario',
            requerido=False, orden=0,
            config_tabla=copy.deepcopy(FOR_002_TABLA),
        )
        # Sello de evidencia SES debajo de la tabla (una línea por firma).
        CampoFormato.objects.create(
            seccion=seccion_tabla, clave='sello_firma',
            etiqueta='Sello de firma electrónica', tipo='sello',
            origen='sello_firma', celda_excel=FOR_002_SELLO_CELDA,
            requerido=False, orden=1,
        )

    def _asegurar_sello(self, formato):
        """Agrega el campo ``sello`` si la estructura previa no lo tenía."""
        if CampoFormato.objects.filter(
            seccion__formato=formato, tipo='sello',
        ).exists():
            return
        seccion = formato.secciones.order_by('-orden').first()
        if seccion is None:
            return
        CampoFormato.objects.create(
            seccion=seccion, clave='sello_firma',
            etiqueta='Sello de firma electrónica', tipo='sello',
            origen='sello_firma', celda_excel=FOR_002_SELLO_CELDA,
            requerido=False, orden=99,
        )

    def _asegurar_roles(self, formato):
        """Completa ``rol`` en columnas de tabla creadas por versiones previas.

        Operación no destructiva: solo agrega la clave ``rol`` faltante,
        conservando todo lo editado desde el Constructor.
        """
        por_clave = {col['clave']: col['rol'] for col in FOR_002_TABLA['columnas']}
        campos = CampoFormato.objects.filter(
            tipo='tabla', seccion__formato=formato,
        )
        for campo in campos:
            config = campo.config_tabla or {}
            columnas = config.get('columnas') or []
            cambiado = False
            for columna in columnas:
                if not isinstance(columna, dict) or columna.get('rol'):
                    continue
                rol = por_clave.get(columna.get('clave'))
                if rol:
                    columna['rol'] = rol
                    cambiado = True
            if cambiado:
                campo.config_tabla = config
                campo.save(update_fields=['config_tabla'])
                self.stdout.write(self.style.SUCCESS(
                    f'  Columnas de "{campo.clave}": agregado campo "rol".'
                ))
