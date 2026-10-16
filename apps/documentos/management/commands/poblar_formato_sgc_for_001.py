from django.core.management.base import BaseCommand
from documentos.models import Formato, SeccionFormato, CampoFormato
import shutil
import os


class Command(BaseCommand):
    help = ('Pobla el Formato SGC-FOR-001 (Control de la Información Documentada) '
            'para creación y eliminación de documentos, y ajusta SGC-FOR-005 '
            'para que aplique solo a modificación.')

    def handle(self, *args, **options):
        formato, created = Formato.objects.update_or_create(
            codigo='SGC-FOR-001',
            defaults={
                'nombre': 'Control de la Información Documentada',
                'version': '1',
                'edicion': '1',
                'fecha_vigencia': '2025-03-18',
                'procedimiento': 'Procedimiento de Control de Documentos',
                'aplica_creacion': True,
                'aplica_modificacion': False,
                'aplica_eliminacion': True,
                'activo': True,
                'instructivo_html': self._instructivo_html(),
            }
        )
        action = 'CREADO' if created else 'ACTUALIZADO'
        self.stdout.write(self.style.SUCCESS(f'Formato SGC-FOR-001 {action} (id={formato.id})'))

        xlsx_source = os.path.join(
            os.path.dirname(__file__), '..', '..', '..', '..', 'fotos',
            'SGC-FOR-001 Control de Inf Doc.xlsx'
        )
        xlsx_source = os.path.normpath(xlsx_source)
        if os.path.exists(xlsx_source) and not formato.plantilla_xlsx:
            from django.conf import settings
            destino = os.path.join(
                settings.MEDIA_ROOT, 'formatos', 'plantillas', '2025', 'SGC-FOR-001.xlsx'
            )
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(xlsx_source, destino)
            formato.plantilla_xlsx = os.path.relpath(destino, settings.MEDIA_ROOT)
            formato.save(update_fields=['plantilla_xlsx'])
            self.stdout.write(self.style.SUCCESS('Plantilla Excel copiada a MEDIA'))
        elif formato.plantilla_xlsx:
            self.stdout.write(self.style.WARNING('Plantilla Excel ya existe, no se sobreescribe'))

        formato.secciones.all().delete()

        secciones_data = [
            {
                'titulo': 'Acción',
                'orden': 1,
                'campos': [
                    {'clave': 'accion', 'etiqueta': 'Acción sobre la información documentada',
                     'tipo': 'checkbox_grupo', 'origen': 'operacion', 'requerido': True,
                     'opciones': [
                         {'valor': 'Creación', 'etiqueta': 'Creación', 'shape': 'Rectángulo 2'},
                         {'valor': 'Modificación', 'etiqueta': 'Modificación', 'shape': 'Rectángulo 5'},
                         {'valor': 'Eliminación', 'etiqueta': 'Eliminación', 'shape': 'Rectángulo 4'},
                     ],
                     'ayuda': 'Seleccione si la información documentada se crea, modifica o elimina'},
                ],
            },
            {
                'titulo': 'Datos de la Información Documentada',
                'orden': 2,
                'campos': [
                    {'clave': 'tipo_informacion', 'etiqueta': 'Tipo de Información Documentada',
                     'tipo': 'texto', 'origen': 'tipo_informacion', 'celda_excel': 'C12'},
                    {'clave': 'num_identificacion', 'etiqueta': 'Nº de Identificación',
                     'tipo': 'texto', 'origen': 'num_identificacion', 'celda_excel': 'C14'},
                    {'clave': 'titulo', 'etiqueta': 'Título',
                     'tipo': 'texto', 'origen': 'titulo_documento', 'celda_excel': 'B16'},
                    {'clave': 'version', 'etiqueta': 'Versión Nº',
                     'tipo': 'texto', 'origen': 'version_documento', 'celda_excel': 'B18'},
                    {'clave': 'edicion', 'etiqueta': 'Edición Nº',
                     'tipo': 'texto', 'origen': 'edicion_documento', 'celda_excel': 'E18'},
                    {'clave': 'fecha_vigencia', 'etiqueta': 'Fecha de Vigencia',
                     'tipo': 'fecha', 'origen': 'fecha_vigencia_documento', 'celda_excel': 'H18'},
                    {'clave': 'descripcion', 'etiqueta': 'Descripción de la Creación/Modificación/Eliminación',
                     'tipo': 'textarea', 'origen': 'descripcion_solicitud', 'requerido': True,
                     'celda_excel': 'A21',
                     'ayuda': 'Breve resumen de la creación, modificación o eliminación'},
                ],
            },
            {
                'titulo': 'Evaluación y Autorización',
                'orden': 3,
                'campos': [
                    {'clave': 'evaluado_por_nombre', 'etiqueta': 'Evaluado por (Nombre)',
                     'tipo': 'texto', 'origen': 'evaluado_por_nombre',
                     'celda_excel': 'A22', 'incluir_etiqueta': True},
                    {'clave': 'evaluado_por_cargo', 'etiqueta': 'Evaluado por (Cargo)',
                     'tipo': 'texto', 'origen': 'evaluado_por_cargo',
                     'celda_excel': 'A23', 'incluir_etiqueta': True},
                    {'clave': 'evaluado_por_fecha', 'etiqueta': 'Evaluado por (Fecha)',
                     'tipo': 'fecha', 'origen': 'evaluado_por_fecha',
                     'celda_excel': 'A24', 'incluir_etiqueta': True},
                    {'clave': 'autorizado_por_nombre', 'etiqueta': 'Autorizado por (Nombre)',
                     'tipo': 'texto', 'origen': 'autorizado_por_nombre',
                     'celda_excel': 'A26', 'incluir_etiqueta': True},
                    {'clave': 'autorizado_por_cargo', 'etiqueta': 'Autorizado por (Cargo)',
                     'tipo': 'texto', 'origen': 'autorizado_por_cargo',
                     'celda_excel': 'A27', 'incluir_etiqueta': True},
                    {'clave': 'autorizado_por_fecha', 'etiqueta': 'Autorizado por (Fecha)',
                     'tipo': 'fecha', 'origen': 'autorizado_por_fecha',
                     'celda_excel': 'A28', 'incluir_etiqueta': True},
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
                    incluir_etiqueta=campo_data.get('incluir_etiqueta', False),
                    orden=0,
                    ancho='full',
                )
                campos_creados += 1

        self.stdout.write(self.style.SUCCESS(
            f'SGC-FOR-001: {len(secciones_data)} secciones, {campos_creados} campos creados'
        ))

        # SGC-FOR-005 pasa a aplicar SOLO a modificación.
        actualizados = Formato.objects.filter(codigo='SGC-FOR-005').update(
            aplica_creacion=False, aplica_eliminacion=False, aplica_modificacion=True,
        )
        if actualizados:
            self.stdout.write(self.style.SUCCESS(
                'SGC-FOR-005 actualizado: solo modificación (aplica_creacion=False, aplica_eliminacion=False)'
            ))
        else:
            self.stdout.write(self.style.WARNING('SGC-FOR-005 no existe; no se ajustó'))

    def _instructivo_html(self):
        return '''<h4>Instructivo de Llenado SGC-IDL-007</h4>
<p>La información documentada es cualquier documentación que la organización considere necesaria
para el correcto desarrollo de las actividades. Debe ser controlada en cuanto a su creación,
modificación o eliminación para garantizar su integridad y disponibilidad.</p>
<ol>
<li><strong>Creación / Modificación / Eliminación:</strong> seleccione la acción que se realizará sobre la información documentada.</li>
<li><strong>Tipo de Información Documentada:</strong> escriba el tipo (Procedimiento, Instrucción de trabajo, Formulario, Instructivo de llenado, entre otros).</li>
<li><strong>Nº de Identificación:</strong> número de identificación de la información documentada. Si es modificación o eliminación, el actual; si es creación, el asignado según el consecutivo de la lista maestra.</li>
<li><strong>Título:</strong> título que debe tener la información documentada.</li>
<li><strong>Versión:</strong> versión que tiene o tendrá la información documentada.</li>
<li><strong>Edición:</strong> edición que tiene o tendrá la información documentada.</li>
<li><strong>Fecha de Vigencia:</strong> fecha de vigencia que tiene o tendrá la información documentada.</li>
<li><strong>Descripción de la Creación/Modificación/Eliminación:</strong> breve resumen de la acción.</li>
</ol>'''
