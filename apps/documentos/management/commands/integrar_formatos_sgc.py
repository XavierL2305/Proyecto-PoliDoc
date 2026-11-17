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

#: SGC-FOR-003 — Tabla de cambios y modificaciones (P1).
#: Se genera desde la planilla de modificación (multifila), no desde el flujo
#: de aprobación: ``aplica_*`` queda en False (badge "Planilla automática").
FOR_003_XLSX = 'SGC-FOR-003 Tabla de Cam y Mod.xlsx'
FOR_003_TABLA = {
    'fila_inicio': 12,
    'fila_fin': 23,
    'max_filas': 12,
    'columnas': [
        {'clave': 'numero_pagina', 'etiqueta': 'Nº de página modificada',
         'celda': 'A'},
        {'clave': 'version', 'etiqueta': 'Versión', 'celda': 'B'},
        {'clave': 'descripcion', 'etiqueta': 'Descripción del cambio',
         'celda': 'C'},
        {'clave': 'fecha', 'etiqueta': 'Fecha', 'celda': 'F'},
        {'clave': 'realizado_por', 'etiqueta': 'Realizado por', 'celda': 'G'},
        {'clave': 'autorizado_por', 'etiqueta': 'Autorizado por', 'celda': 'I'},
    ],
}

#: Celda del sello de firma electrónica (debajo de la tabla de 33 filas).
FOR_002_SELLO_CELDA = 'A52'

#: SGC-FOR-004 — Acta de desincorporación de archivos inactivos (P1).
#: Se genera desde el acta (multifila), no desde el flujo de aprobación.
FOR_004_XLSX = 'SGC-FOR-004 Acta desinc Arch Inac.xlsx'
FOR_004_PARTICIPANTES_TABLA = {
    'fila_inicio': 16,
    'fila_fin': 19,
    'max_filas': 4,
    'columnas': [
        {'clave': 'nombre', 'etiqueta': 'Nombre', 'celda': 'A'},
        {'clave': 'cargo', 'etiqueta': 'Cargo', 'celda': 'F'},
    ],
}
FOR_004_REGISTROS_TABLA = {
    'fila_inicio': 23,
    'fila_fin': 31,
    'max_filas': 9,
    'columnas': [
        {'clave': 'descripcion', 'etiqueta': 'Registro inactivo', 'celda': 'A'},
    ],
}



#: SGC-FOR-006 — Lista Maestra de Documentos (P1).
#: Se genera desde el registro vivo (multifila en una hoja continua).
FOR_006_XLSX = 'SGC-FOR-006 Lista Maestra.xlsx'
FOR_006_TABLA = {
    'fila_inicio': 13,
    'fila_fin': 34,
    'max_filas': 22,
    'hoja_continua': True,
    'columnas': [
        {'clave': 'ext_int', 'etiqueta': 'Documento de fuente',
         'tipo': 'si_no', 'celda_si': 'B', 'celda_no': 'A',
         'origen': 'ext_int'},
        {'clave': 'codigo', 'etiqueta': 'No. de identificación',
         'tipo': 'texto', 'celda': 'C', 'origen': 'codigo'},
        {'clave': 'titulo', 'etiqueta': 'Título',
         'tipo': 'texto', 'celda': 'D', 'origen': 'nombre'},
        {'clave': 'uso', 'etiqueta': 'Uso/confidencial',
         'tipo': 'combo', 'celda': 'E', 'origen': 'uso',
         'opciones': ['Interno/Confidencial', 'Interno/No Confidencial']},
        {'clave': 'fecha', 'etiqueta': 'Fecha',
         'tipo': 'fecha', 'celda': 'F', 'origen': 'fecha_vigencia'},
        {'clave': 'edicion', 'etiqueta': 'Nº Edición',
         'tipo': 'texto', 'celda': 'G', 'origen': 'edicion'},
        {'clave': 'version', 'etiqueta': 'No. Versión',
         'tipo': 'texto', 'celda': 'H', 'origen': 'version'},
        {'clave': 'permanencia_activo', 'etiqueta': 'Permanencia Activo',
         'tipo': 'combo', 'celda': 'I', 'origen': 'permanencia_activo',
         'opciones': [
             'Hasta que cambie de versión o edición', 'Permanente',
             '1 año', '2 años', '5 años', '10 años', 'N/A',
         ]},
        {'clave': 'permanencia_inactivo', 'etiqueta': 'Permanencia Inactivo',
         'tipo': 'combo', 'celda': 'J', 'origen': 'permanencia_inactivo',
         'opciones': [
             'Permanente', '1 año', '2 años', '5 años', '8 años',
             '10 años', 'N/A',
         ]},
        {'clave': 'forma_archivo', 'etiqueta': 'Forma de Archivo y Ubicación',
         'tipo': 'combo', 'celda': 'K',
         'opciones': [
             'Digital/ Carpeta SGC',
             'Físico/ Expediente del paciente',
             'Digital y Físico/ Carpeta compartida',
         ]},
        {'clave': 'distribucion', 'etiqueta': 'Distribución',
         'tipo': 'textarea', 'celda': 'L'},
        {'clave': 'responsable', 'etiqueta': 'Responsable',
         'tipo': 'combo', 'celda': 'M', 'origen': 'responsable',
         'opciones': [
             'Gerente General', 'Gerente de Talento Humano',
             'Jefe de Servicios', 'Junta Directiva',
         ]},
        {'clave': 'empresa', 'etiqueta': 'Empresa',
         'tipo': 'combo', 'celda': 'N', 'origen': 'empresa',
         'opciones': [
             'PTCA', 'Policlínica Táchira Hospitalización', 'Farmacia',
             'Fuente de Soda', 'Empresas del Grupo',
         ]},
    ],
}


#: SGC-FOR-007 — Programa de Revisión por la Dirección (P1).
#: La matriz es dinámica (N aspectos variables); la plantilla oficial solo
#: aporta el layout/estilos. ``seed`` es el punto de partida de los aspectos.
FOR_007_XLSX = 'SGC-FOR-007 Programa de Rev. Direc.xlsx'
FOR_007_CONFIG = {
    'fila_inicio': 14,
    'fila_fin': 27,
    'max_filas_hoja': 30,
    'meses': [
        {'clave': 'ENE', 'celda': 'H', 'etiqueta': 'ENE'},
        {'clave': 'FEB', 'celda': 'I', 'etiqueta': 'FEB'},
        {'clave': 'MAR', 'celda': 'J', 'etiqueta': 'MAR'},
        {'clave': 'ABR', 'celda': 'K', 'etiqueta': 'ABR'},
        {'clave': 'MAY', 'celda': 'L', 'etiqueta': 'MAY'},
        {'clave': 'JUN', 'celda': 'M', 'etiqueta': 'JUN'},
        {'clave': 'JUL', 'celda': 'N', 'etiqueta': 'JUL'},
        {'clave': 'AGO', 'celda': 'O', 'etiqueta': 'AGO'},
        {'clave': 'SEP', 'celda': 'P', 'etiqueta': 'SEP'},
        {'clave': 'OCT', 'celda': 'Q', 'etiqueta': 'OCT'},
        {'clave': 'NOV', 'celda': 'R', 'etiqueta': 'NOV'},
        {'clave': 'DIC', 'celda': 'S', 'etiqueta': 'DIC'},
    ],
    'celdas': {
        'anio': 'A10',
        'leyenda': 'B29',
        'conforme': 'A31',
        'presidente': 'A32',
        'gerente': 'E32',
        'fecha_dia': 'I32',
        'fecha_mes': 'K32',
        'fecha_anio': 'M32',
        'pagina': 'L6',
    },
    'marcas': {'planificado': 'P', 'ejecutado': 'V'},
    'columnas': [
        {'clave': 'aspecto',
         'etiqueta': 'Aspectos del SGC a evaluar', 'celda': 'A'},
        {'clave': 'informacion',
         'etiqueta': 'Información documentada a revisar', 'celda': 'E'},
    ],
    'seed': [
        {'clave': 'seed_1',
         'etiqueta': 'a) El estado de las acciones de las revisiones por la direcciòn previas',
         'informacion': 'Minuta de la Revisiòn por la Direcciòn anterior'},
        {'clave': 'seed_2',
         'etiqueta': 'b) Los cambios en las cuestiones externas e internas que sean pertinentes al sistema de gestiòn de la calidad',
         'informacion': 'Foda'},
        {'clave': 'seed_3',
         'etiqueta': 'c) Los cambios en las necesidades y expectativas de las partes interesadas',
         'informacion': 'PARTES INTERESADAS/ REQUISITOS DE LAS PARTES INTERESADAS'},
        {'clave': 'seed_4',
         'etiqueta': 'd) La informaciòn sobre el desempeño y la eficacia del sistema de gestiòn de la calidad incluidas las tendencias relativas a:',
         'informacion': ''},
        {'clave': 'seed_5',
         'etiqueta': '1. Satisfacciòn del cliente y la retroalimentaciòn de las partes interesadas pertinentes',
         'informacion': 'Analisis y Evaluaciòn de la Encuesta de Satisfacciòn'},
        {'clave': 'seed_6',
         'etiqueta': '2. Grado en que se han logrado los objetivos de la calidad',
         'informacion': 'Objetivos de la Calidad y planificaciòn para lograrlos'},
        {'clave': 'seed_7',
         'etiqueta': '3. Desempeño de los procesos y conformidad de los productos y servicios;',
         'informacion': 'Indicadores de Gestiòn y/o de salud / Gestiòn de residuos/ Planes de acciòn ante no conformidades que se generen de los indicadores'},
        {'clave': 'seed_8',
         'etiqueta': '4. Las no conformidades y acciones correctivas ',
         'informacion': 'Planes de acciòn ante no conformidades que se generen del sistema de gestion de la calidad'},
        {'clave': 'seed_9',
         'etiqueta': '5. Los resultados de seguimiento y mediciòn',
         'informacion': 'Resultados de los planes de acciòn '},
        {'clave': 'seed_10',
         'etiqueta': '6. Los resultados de las auditorias',
         'informacion': 'Informes de auditoria'},
        {'clave': 'seed_11',
         'etiqueta': '7. Desempeño de los proveedores externos',
         'informacion': 'Evaluaciòn de los proveedores'},
        {'clave': 'seed_12',
         'etiqueta': 'd) La adecuaciòn de los recursos',
         'informacion': 'Presupuestos'},
        {'clave': 'seed_13',
         'etiqueta': 'e) La eficacia de las acciones tomadas para abordar riesgos y oportunidades',
         'informacion': 'Plan de accion para tratamientos de riesgos y plan de acciòn para aborgar oportunidades'},
        {'clave': 'seed_14',
         'etiqueta': 'f) Las oportunidades de mejora',
         'informacion': 'Proyectos o diseños y desarrollos de servicios'},
    ],
}

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

        implementados = {
            'SGC-FOR-002': self._integrar_for_002,
            'SGC-FOR-003': self._integrar_for_003,
            'SGC-FOR-004': self._integrar_for_004,
            'SGC-FOR-006': self._integrar_for_006,
            'SGC-FOR-007': self._integrar_for_007,
        }
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
        implementados = [
            'SGC-FOR-002', 'SGC-FOR-003', 'SGC-FOR-004', 'SGC-FOR-006',
            'SGC-FOR-007',
        ]
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

    # ------------------------------------------------------------------
    # SGC-FOR-003
    # ------------------------------------------------------------------

    def _integrar_for_003(self, aplicar, forzar, forzar_estructura=False,
                          plantilla=''):
        self.stdout.write(self.style.HTTP_INFO(
            '\n=== SGC-FOR-003 — Tabla de cambios y modificaciones ==='
        ))

        origen = plantilla or self._ruta_plantilla(FOR_003_XLSX)
        if not os.path.exists(origen):
            raise CommandError(
                f'No se encontró la plantilla del FOR-003: {origen}\n'
                + (
                    'Revisa la ruta indicada con --plantilla.'
                    if plantilla else
                    'Coloca el XLSX en documentacion/ o indica su ruta con '
                    '--plantilla.'
                )
            )

        existente = Formato.objects.filter(codigo='SGC-FOR-003').first()
        estado = 'ACTUALIZAR' if existente else 'CREAR'
        self.stdout.write(f'Formato: {estado} (plantilla {os.path.basename(origen)})')
        self.stdout.write(
            f'  Tabla: {FOR_003_TABLA["max_filas"]} filas '
            f'({FOR_003_TABLA["fila_inicio"]}-{FOR_003_TABLA["fila_fin"]})'
        )

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '  [dry-run] No se modificó la base de datos. '
                'Repite con --apply para escribir.'
            ))
            return

        formato, _creado = Formato.objects.update_or_create(
            codigo='SGC-FOR-003',
            defaults={
                'nombre': 'Tabla de Cambios y Modificaciones',
                'version': '1',
                'edicion': '1',
                # Se genera desde la planilla de modificación.
                'aplica_creacion': False,
                'aplica_modificacion': False,
                'aplica_eliminacion': False,
                'activo': True,
                'instructivo_html': '',
            },
        )

        if forzar or not formato.plantilla_xlsx:
            destino = self._destino_media('SGC-FOR-003')
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

        self._recrear_estructura_003(formato, forzar=forzar_estructura)
        self.stdout.write(self.style.SUCCESS(
            '  Estructura y metadatos de SGC-FOR-003 listos.'
        ))

    def _recrear_estructura_003(self, formato, forzar=False):
        """Una sección con la tabla multifila (columnas y rango del XLSX)."""
        if not forzar and CampoFormato.objects.filter(
            seccion__formato=formato, clave='cambios',
        ).exists():
            self.stdout.write('  Estructura existente conservada.')
            return

        if forzar:
            formato.secciones.all().delete()

        seccion, _ = SeccionFormato.objects.update_or_create(
            formato=formato, titulo='Cambios y modificaciones',
            defaults={'orden': 1},
        )
        CampoFormato.objects.update_or_create(
            seccion=seccion, clave='cambios',
            defaults={
                'etiqueta': 'Tabla de cambios y modificaciones',
                'tipo': 'tabla',
                'origen': 'usuario',
                'celda_excel': 'A12',
                'requerido': True,
                'orden': 1,
                'config_tabla': FOR_003_TABLA,
                'ayuda': (
                    'Una fila por página o ítem modificado. La versión, '
                    'fecha, realizado por y autorizado por se completan '
                    'automáticamente desde la planilla.'
                ),
            },
        )

    # ------------------------------------------------------------------
    # SGC-FOR-004
    # ------------------------------------------------------------------

    def _integrar_for_004(self, aplicar, forzar, forzar_estructura=False,
                          plantilla=''):
        self.stdout.write(self.style.HTTP_INFO(
            '\n=== SGC-FOR-004 — Acta de desincorporación de archivos inactivos ==='
        ))

        origen = plantilla or self._ruta_plantilla(FOR_004_XLSX)
        if not os.path.exists(origen):
            raise CommandError(
                f'No se encontró la plantilla del FOR-004: {origen}\n'
                + (
                    'Revisa la ruta indicada con --plantilla.'
                    if plantilla else
                    'Coloca el XLSX en documentacion/ o indica su ruta con '
                    '--plantilla.'
                )
            )

        existente = Formato.objects.filter(codigo='SGC-FOR-004').first()
        estado = 'ACTUALIZAR' if existente else 'CREAR'
        self.stdout.write(f'Formato: {estado} (plantilla {os.path.basename(origen)})')
        self.stdout.write(
            f'  Participantes: {FOR_004_PARTICIPANTES_TABLA["max_filas"]} filas '
            f'({FOR_004_PARTICIPANTES_TABLA["fila_inicio"]}-'
            f'{FOR_004_PARTICIPANTES_TABLA["fila_fin"]}); '
            f'registros: {FOR_004_REGISTROS_TABLA["max_filas"]} filas '
            f'({FOR_004_REGISTROS_TABLA["fila_inicio"]}-'
            f'{FOR_004_REGISTROS_TABLA["fila_fin"]})'
        )

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '  [dry-run] No se modificó la base de datos. '
                'Repite con --apply para escribir.'
            ))
            return

        formato, _creado = Formato.objects.update_or_create(
            codigo='SGC-FOR-004',
            defaults={
                'nombre': 'Acta de Desincorporación de Archivos Inactivos',
                'version': '1',
                'edicion': '1',
                # Se genera desde el acta de desincorporación.
                'aplica_creacion': False,
                'aplica_modificacion': False,
                'aplica_eliminacion': False,
                'activo': True,
                'instructivo_html': '',
            },
        )

        if forzar or not formato.plantilla_xlsx:
            destino = self._destino_media('SGC-FOR-004')
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

        self._recrear_estructura_004(formato, forzar=forzar_estructura)
        self.stdout.write(self.style.SUCCESS(
            '  Estructura y metadatos de SGC-FOR-004 listos.'
        ))

    def _recrear_estructura_004(self, formato, forzar=False):
        """Secciones informativas (fecha, participantes, registros, área).

        La generación real la hace ``services/for_004.py`` sobre la plantilla;
        esta estructura mantiene coherente al Constructor de formatos.
        """
        if not forzar and CampoFormato.objects.filter(
            seccion__formato=formato, clave='participantes',
        ).exists():
            self.stdout.write('  Estructura existente conservada.')
            return

        if forzar:
            formato.secciones.all().delete()

        seccion, _ = SeccionFormato.objects.update_or_create(
            formato=formato, titulo='Acta de desincorporación',
            defaults={'orden': 1},
        )
        CampoFormato.objects.update_or_create(
            seccion=seccion, clave='fecha',
            defaults={
                'etiqueta': 'Fecha del acta', 'tipo': 'fecha',
                'origen': 'usuario', 'celda_excel': 'A13',
                'requerido': True, 'orden': 1,
            },
        )
        CampoFormato.objects.update_or_create(
            seccion=seccion, clave='participantes',
            defaults={
                'etiqueta': 'Participantes', 'tipo': 'tabla',
                'origen': 'usuario', 'celda_excel': 'A16',
                'requerido': True, 'orden': 2,
                'config_tabla': copy.deepcopy(FOR_004_PARTICIPANTES_TABLA),
            },
        )
        CampoFormato.objects.update_or_create(
            seccion=seccion, clave='proposito',
            defaults={
                'etiqueta': 'Propósito', 'tipo': 'texto',
                'origen': 'usuario', 'celda_excel': 'A21',
                'requerido': False, 'orden': 3,
            },
        )
        CampoFormato.objects.update_or_create(
            seccion=seccion, clave='registros',
            defaults={
                'etiqueta': 'Registros inactivos', 'tipo': 'tabla',
                'origen': 'usuario', 'celda_excel': 'A23',
                'requerido': True, 'orden': 4,
                'config_tabla': copy.deepcopy(FOR_004_REGISTROS_TABLA),
            },
        )
        CampoFormato.objects.update_or_create(
            seccion=seccion, clave='departamento',
            defaults={
                'etiqueta': 'Departamento / Coordinación / Área',
                'tipo': 'texto', 'origen': 'usuario',
                'celda_excel': 'A33', 'requerido': False, 'orden': 5,
            },
        )

    # ------------------------------------------------------------------
    # SGC-FOR-006
    # ------------------------------------------------------------------

    def _integrar_for_006(self, aplicar, forzar, forzar_estructura=False,
                          plantilla=''):
        self.stdout.write(self.style.HTTP_INFO(
            '\n=== SGC-FOR-006 — Lista Maestra de Documentos ==='
        ))

        origen = plantilla or self._ruta_plantilla(FOR_006_XLSX)
        if not os.path.exists(origen):
            raise CommandError(
                f'No se encontró la plantilla del FOR-006: {origen}\n'
                + (
                    'Revisa la ruta indicada con --plantilla.'
                    if plantilla else
                    'Coloca el XLSX en documentacion/ o indica su ruta con '
                    '--plantilla.'
                )
            )

        existente = Formato.objects.filter(codigo='SGC-FOR-006').first()
        estado = 'ACTUALIZAR' if existente else 'CREAR'
        self.stdout.write(f'Formato: {estado} (plantilla {os.path.basename(origen)})')
        self.stdout.write(
            f'  Tabla: {FOR_006_TABLA["max_filas"]} filas '
            f'({FOR_006_TABLA["fila_inicio"]}-{FOR_006_TABLA["fila_fin"]}), '
            f'{len(FOR_006_TABLA["columnas"])} columnas (hoja continua)'
        )

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '  [dry-run] No se modificó la base de datos. '
                'Repite con --apply para escribir.'
            ))
            return

        formato, _creado = Formato.objects.update_or_create(
            codigo='SGC-FOR-006',
            defaults={
                'nombre': 'Lista Maestra de Documentos',
                'version': '1',
                'edicion': '1',
                # Se genera desde un registro vivo (no desde el flujo de
                # aprobación). La vista es propia, no el formulario genérico.
                'aplica_creacion': False,
                'aplica_modificacion': False,
                'aplica_eliminacion': False,
                'permite_registros': False,
                'activo': True,
                'instructivo_html': '',
            },
        )

        if forzar or not formato.plantilla_xlsx:
            destino = self._destino_media('SGC-FOR-006')
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

        self._recrear_estructura_006(formato, forzar=forzar_estructura)
        self.stdout.write(self.style.SUCCESS(
            '  Estructura y metadatos de SGC-FOR-006 listos.'
        ))

    def _recrear_estructura_006(self, formato, forzar=False):
        """Una sección con la tabla multifila de la Lista Maestra."""
        if not forzar and CampoFormato.objects.filter(
            seccion__formato=formato, clave='documentos',
        ).exists():
            self.stdout.write('  Estructura existente conservada.')
            return

        if forzar:
            formato.secciones.all().delete()

        seccion, _ = SeccionFormato.objects.update_or_create(
            formato=formato, titulo='Lista Maestra',
            defaults={'orden': 1},
        )
        CampoFormato.objects.update_or_create(
            seccion=seccion, clave='documentos',
            defaults={
                'etiqueta': 'Lista de documentos',
                'tipo': 'tabla',
                'origen': 'usuario',
                'celda_excel': 'A13',
                'requerido': False,
                'orden': 1,
                'config_tabla': copy.deepcopy(FOR_006_TABLA),
                'ayuda': (
                    'Una fila por documento. Las columnas con “origen” se '
                    'autocompletan desde el registro de documentos; las demás '
                    'se editan a mano (con respuestas recientes).'
                ),
            },
        )

    # ------------------------------------------------------------------
    # SGC-FOR-007
    # ------------------------------------------------------------------

    def _integrar_for_007(self, aplicar, forzar, forzar_estructura=False,
                          plantilla=''):
        self.stdout.write(self.style.HTTP_INFO(
            '\n=== SGC-FOR-007 — Programa de Revisión por la Dirección ==='
        ))

        origen = plantilla or self._ruta_plantilla(FOR_007_XLSX)
        if not os.path.exists(origen):
            raise CommandError(
                f'No se encontró la plantilla del FOR-007: {origen}\n'
                + (
                    'Revisa la ruta indicada con --plantilla.'
                    if plantilla else
                    'Coloca el XLSX en documentacion/ o indica su ruta con '
                    '--plantilla.'
                )
            )

        existente = Formato.objects.filter(codigo='SGC-FOR-007').first()
        estado = 'ACTUALIZAR' if existente else 'CREAR'
        self.stdout.write(f'Formato: {estado} (plantilla {os.path.basename(origen)})')
        self.stdout.write(
            f'  Matriz dinámica; seed de {len(FOR_007_CONFIG["seed"])} aspectos; '
            f'máx. {FOR_007_CONFIG["max_filas_hoja"]} filas/hoja'
        )

        if not aplicar:
            self.stdout.write(self.style.WARNING(
                '  [dry-run] No se modificó la base de datos. '
                'Repite con --apply para escribir.'
            ))
            return

        formato, _creado = Formato.objects.update_or_create(
            codigo='SGC-FOR-007',
            defaults={
                'nombre': 'Programa de Revisión por la Dirección',
                'version': '1',
                'edicion': '1',
                'aplica_creacion': False,
                'aplica_modificacion': False,
                'aplica_eliminacion': False,
                'permite_registros': False,
                'activo': True,
                'instructivo_html': '',
            },
        )

        if forzar or not formato.plantilla_xlsx:
            destino = self._destino_media('SGC-FOR-007')
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

        self._recrear_estructura_007(formato, forzar=forzar_estructura)
        self.stdout.write(self.style.SUCCESS(
            '  Estructura y metadatos de SGC-FOR-007 listos.'
        ))

    def _recrear_estructura_007(self, formato, forzar=False):
        """Una sección con el campo de configuración de la matriz."""
        if not forzar and CampoFormato.objects.filter(
            seccion__formato=formato, clave='aspectos',
        ).exists():
            self.stdout.write('  Estructura existente conservada.')
            return

        if forzar:
            formato.secciones.all().delete()

        seccion, _ = SeccionFormato.objects.update_or_create(
            formato=formato, titulo='Programa de Revisión',
            defaults={'orden': 1},
        )
        CampoFormato.objects.update_or_create(
            seccion=seccion, clave='aspectos',
            defaults={
                'etiqueta': 'Aspectos del SGC a evaluar',
                'tipo': 'tabla',
                'origen': 'usuario',
                'celda_excel': 'A14',
                'requerido': False,
                'orden': 1,
                'config_tabla': copy.deepcopy(FOR_007_CONFIG),
                'ayuda': (
                    'Matriz anual: los aspectos y su información documentada '
                    'son editables por año; las marcas P/V se guardan por mes.'
                ),
            },
        )
