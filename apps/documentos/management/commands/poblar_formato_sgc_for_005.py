from django.core.management.base import BaseCommand
from documentos.models import Formato, SeccionFormato, CampoFormato
import shutil
import os


class Command(BaseCommand):
    help = 'Pobla el Formato SGC-FOR-005 (Planificación de los Cambios del SGC) con estructura real del Excel'

    def handle(self, *args, **options):
        formato, created = Formato.objects.update_or_create(
            codigo='SGC-FOR-005',
            defaults={
                'nombre': 'Planificación de los Cambios del SGC',
                'version': '1',
                'edicion': '1',
                'fecha_vigencia': '2025-03-18',
                'procedimiento': 'Procedimiento de Planificación Estratégica',
                'aplica_creacion': False,
                'aplica_modificacion': True,
                'aplica_eliminacion': False,
                'activo': True,
                'instructivo_html': self._instructivo_html(),
            }
        )
        action = 'CREADO' if created else 'ACTUALIZADO'
        self.stdout.write(self.style.SUCCESS(f'Formato SGC-FOR-005 {action} (id={formato.id})'))

        xlsx_source = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'fotos',
                                   'SGC-FOR-005 Planificaciòn de los Cambios SGC.xlsx')
        xlsx_source = os.path.normpath(xlsx_source)
        if os.path.exists(xlsx_source) and not formato.plantilla_xlsx:
            from django.conf import settings
            destino = os.path.join(settings.MEDIA_ROOT, 'formatos', 'plantillas', '2025', 'SGC-FOR-005.xlsx')
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(xlsx_source, destino)
            formato.plantilla_xlsx = os.path.relpath(destino, settings.MEDIA_ROOT)
            formato.save(update_fields=['plantilla_xlsx'])
            self.stdout.write(self.style.SUCCESS(f'Plantilla Excel copiada a MEDIA'))
        elif formato.plantilla_xlsx:
            self.stdout.write(self.style.WARNING('Plantilla Excel ya existe, no se sobreescribe'))

        formato.secciones.all().delete()

        secciones_data = [
            {
                'titulo': 'Datos de la Solicitud',
                'orden': 1,
                'campos': [
                    {'clave': 'nombre_solicitante', 'etiqueta': 'Nombre del Solicitante del Cambio',
                     'tipo': 'texto', 'requerido': True, 'celda_excel': 'A9', 'origen': 'nombre_solicitante',
                     'ayuda': 'Nombre de la persona que detecta la necesidad del cambio'},
                    {'clave': 'fecha_solicitud', 'etiqueta': 'Fecha de la Solicitud',
                     'tipo': 'fecha', 'requerido': True, 'celda_excel': 'I9', 'origen': 'fecha_solicitud',
                     'ayuda': 'Fecha en que se realiza la solicitud'},
                    {'clave': 'num_solicitud', 'etiqueta': 'Nº de la Solicitud del Cambio',
                     'tipo': 'texto', 'requerido': True, 'celda_excel': 'L9', 'origen': 'num_solicitud',
                     'ayuda': 'Formato: DDMMAA-XXX (ej. 080525-001)'},
                ],
            },
            {
                'titulo': 'Origen del Cambio',
                'orden': 2,
                'campos': [
                    {'clave': 'origen_cambio', 'etiqueta': 'Origen del Cambio',
                     'tipo': 'checkbox_grupo', 'requerido': True,
                     'opciones': [
                         {'valor': 'Auditoría', 'etiqueta': 'Auditoría', 'celda': 'A12', 'shape': 'Rectángulo 19'},
                         {'valor': 'Revisión por la Dirección', 'etiqueta': 'Revisión por la Dirección', 'celda': 'C12', 'shape': 'Rectángulo 20'},
                         {'valor': 'Mejora Continua', 'etiqueta': 'Mejora Continua', 'celda': 'E12', 'shape': 'Rectángulo 21'},
                         {'valor': 'Quejas/Reclamos', 'etiqueta': 'Quejas/Reclamos', 'celda': 'G12', 'shape': 'Rectángulo 22'},
                         {'valor': 'Otros', 'etiqueta': 'Otros', 'celda': 'I12', 'shape': 'Rectángulo 23'},
                     ]},
                ],
            },
            {
                'titulo': 'Elemento del SGC a Cambiar',
                'orden': 3,
                'campos': [
                    {'clave': 'elemento_sgc', 'etiqueta': 'Elemento del Sistema de Gestión de la Calidad que requiere ser Cambiado',
                     'tipo': 'checkbox_grupo', 'requerido': True,
                     'opciones': [
                         {'valor': 'Planificación Estratégica', 'etiqueta': 'Planificación Estratégica', 'celda': 'B16', 'shape': 'Rectángulo 15'},
                         {'valor': 'Plan de la Calidad', 'etiqueta': 'Plan de la Calidad', 'celda': 'D16', 'shape': 'Rectángulo 26'},
                         {'valor': 'Procesos', 'etiqueta': 'Procesos', 'celda': 'F16', 'shape': 'Rectángulo 17'},
                         {'valor': 'Roles, Responsabilidades y Autoridades en la Organización', 'etiqueta': 'Roles, Responsabilidades y Autoridades en la Organización', 'celda': 'J15', 'shape': 'Rectángulo 18'},
                         {'valor': 'Requisitos para los Productos y Servicios', 'etiqueta': 'Requisitos para los Productos y Servicios', 'celda': 'J16', 'shape': 'Rectángulo 16'},
                     ]},
                ],
            },
            {
                'titulo': 'Descripción del Cambio',
                'orden': 4,
                'campos': [
                    {'clave': 'descripcion_cambio', 'etiqueta': 'Descripción del Cambio',
                     'tipo': 'textarea', 'requerido': True, 'celda_excel': 'A19',
                     'ayuda': 'Breve resumen del cambio que se debe realizar'},
                    {'clave': 'proposito_cambio', 'etiqueta': 'Propósito del Cambio',
                     'tipo': 'textarea', 'requerido': True, 'celda_excel': 'A22',
                     'ayuda': 'Qué se desea lograr con ese cambio, por qué es importante'},
                    {'clave': 'consecuencias_potenciales', 'etiqueta': 'Consecuencias Potenciales en el SGC',
                     'tipo': 'textarea', 'requerido': True, 'celda_excel': 'A25',
                     'ayuda': 'Qué afectaría este cambio dentro del sistema de gestión de la calidad'},
                ],
            },
            {
                'titulo': 'Evaluación y Resolución del Cambio',
                'orden': 5,
                'campos': [
                    {'clave': 'evaluado_por_nombre', 'etiqueta': 'Nombre y Apellido - Evaluado Por',
                     'tipo': 'texto', 'celda_excel': 'B30', 'origen': 'evaluado_por_nombre',
                     'ayuda': 'Nombre de quien evaluó la necesidad del cambio'},
                    {'clave': 'evaluado_por_cargo', 'etiqueta': 'Cargo - Evaluado Por',
                     'tipo': 'texto', 'celda_excel': 'B31', 'origen': 'evaluado_por_cargo'},
                    {'clave': 'evaluado_por_fecha', 'etiqueta': 'Fecha - Evaluado Por',
                     'tipo': 'fecha', 'celda_excel': 'B32', 'origen': 'evaluado_por_fecha'},
                    {'clave': 'autorizado_por_nombre', 'etiqueta': 'Nombre y Apellido - Autorizado Por',
                     'tipo': 'texto', 'celda_excel': 'E30', 'origen': 'autorizado_por_nombre',
                     'ayuda': 'Nombre de quien autorizó el cambio'},
                    {'clave': 'autorizado_por_cargo', 'etiqueta': 'Cargo - Autorizado Por',
                     'tipo': 'texto', 'celda_excel': 'E31', 'origen': 'autorizado_por_cargo'},
                    {'clave': 'autorizado_por_fecha', 'etiqueta': 'Fecha - Autorizado Por',
                     'tipo': 'fecha', 'celda_excel': 'E32', 'origen': 'autorizado_por_fecha'},
                    {'clave': 'resolucion', 'etiqueta': 'Resolución',
                     'tipo': 'select', 'requerido': True, 'origen': 'resolucion',
                     'opciones': [
                         {'valor': 'Aceptado', 'etiqueta': 'Aceptado', 'shape': 'Rectángulo 24'},
                         {'valor': 'Rechazado', 'etiqueta': 'Rechazado', 'shape': 'Rectángulo 25'},
                     ],
                     'ayuda': 'Indicar si el cambio fue aceptado o rechazado'},
                ],
            },
            {
                'titulo': 'Plan de Acción',
                'orden': 6,
                'campos': [
                    {'clave': 'plan_accion', 'etiqueta': 'Plan de Acción',
                     'tipo': 'tabla', 'requerido': True, 'celda_excel': 'A34:N41',
                     'config_tabla': {
                         'fila_inicio': 36,
                         'fila_fin': 41,
                         'columnas': [
                             {'clave': 'actividad', 'etiqueta': 'Actividad', 'celda': 'A'},
                             {'clave': 'resp_ejecucion', 'etiqueta': 'Responsable de Ejecución', 'celda': 'D'},
                             {'clave': 'resp_seguimiento', 'etiqueta': 'Responsable de Seguimiento', 'celda': 'F'},
                             {'clave': 'recursos', 'etiqueta': 'Recursos', 'celda': 'H'},
                             {'clave': 'fecha_ejecucion', 'etiqueta': 'Fecha de Ejecución', 'celda': 'J'},
                             {'clave': 'fecha_seguimiento', 'etiqueta': 'Fecha de Seguimiento', 'celda': 'K'},
                             {'clave': 'observaciones', 'etiqueta': 'Observaciones', 'celda': 'L'},
                         ]
                     }},
                ],
            },
        ]

        campos_creados = 0
        for sec_data in secciones_data:
            seccion = SeccionFormato.objects.create(
                formato=formato,
                titulo=sec_data['titulo'],
                orden=sec_data['orden'],
            )
            for campo_data in sec_data['campos']:
                CampoFormato.objects.create(
                    seccion=seccion,
                    clave=campo_data['clave'],
                    etiqueta=campo_data['etiqueta'],
                    tipo=campo_data['tipo'],
                    origen=campo_data.get('origen', 'usuario'),
                    celda_excel=campo_data.get('celda_excel', ''),
                    requerido=campo_data.get('requerido', False),
                    ayuda=campo_data.get('ayuda', ''),
                    opciones=campo_data.get('opciones', []),
                    config_tabla=campo_data.get('config_tabla', {}),
                    orden=0,
                    ancho='full',
                )
                campos_creados += 1

        self.stdout.write(self.style.SUCCESS(
            f'SGC-FOR-005: {len(secciones_data)} secciones, {campos_creados} campos creados/actualizados'
        ))

    def _instructivo_html(self):
        return '''<h4>Instructivo de Llenado SGC-IDL-011</h4>
<ol>
<li><strong>Nombre del Solicitante del Cambio:</strong> Se escribe el nombre de la persona que detecta la necesidad del cambio.</li>
<li><strong>Fecha de la Solicitud:</strong> Se escribe la fecha en que se está realizando la solicitud.</li>
<li><strong>Nº de la Solicitud del Cambio:</strong> Se escribe el Nº de la Solicitud tomando en cuenta la fecha y el consecutivo de acuerdo al origen del cambio.<br>
<em>Ejemplo:</em> Al momento de la auditoría realizada el 08/05/2025 se detectó la necesidad de realizar 3 cambios; por lo tanto el número será 080525-001, 080525-002 y 080525-003.</li>
<li><strong>Origen del Cambio:</strong> Se debe seleccionar si la necesidad fue detectada a través de una auditoría, revisión por la dirección, mejora continua, quejas/reclamos u otros.</li>
<li><strong>Elemento del SGC que requiere ser cambiado:</strong> Se debe seleccionar el elemento de acuerdo a las opciones del formulario.</li>
<li><strong>Descripción del Cambio:</strong> Se realiza un breve resumen del cambio que se debe realizar.</li>
<li><strong>Propósito del Cambio:</strong> Se escribe por qué es importante el cambio, qué se desea lograr.</li>
<li><strong>Consecuencias Potenciales en el SGC:</strong> Se escribe qué afectaría este cambio dentro del sistema de gestión de la calidad.<br>
<em>Ejemplo:</em> Si se decide cambiar "Gerente General" por "Director General", la consecuencia es realizar los cambios en todos los procedimientos donde se indique Gerente General.</li>
<li><strong>Evaluación y Resolución del Cambio:</strong> Se escribe el nombre, apellido, cargo y fecha de la persona que evaluó la necesidad del cambio, y de quien autorizó el cambio, indicando si fue aceptado o rechazado.</li>
<li><strong>Plan de Acción:</strong> Se desglosan las actividades a realizar para llevar a cabo el cambio, con responsables de ejecución, responsables de seguimiento, recursos, fechas y observaciones.</li>
</ol>'''
