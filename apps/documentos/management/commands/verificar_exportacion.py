import io
import os
import shutil
import subprocess
import zipfile

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Verifica la exportación XLSX/PDF de un formato (requiere LibreOffice). '
        'No guarda nada en la base de datos.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--formato', dest='formato', default='SGC-FOR-005',
            help='Código del formato a exportar (default SGC-FOR-005).',
        )
        parser.add_argument(
            '--salida', dest='salida', default=None,
            help='Directorio donde guardar los archivos generados (opcional).',
        )

    def _respuestas_demo(self, formato):
        """Valores de ejemplo para cada campo, para ejercitar el export."""
        respuestas = {}
        for campo in formato.get_todos_los_campos():
            if campo.tipo == 'checkbox_grupo':
                opciones = campo.opciones or []
                if opciones:
                    primera = opciones[0]
                    respuestas[campo.clave] = [
                        primera.get('valor', primera) if isinstance(primera, dict) else primera
                    ]
            elif campo.tipo == 'tabla':
                columnas = (campo.config_tabla or {}).get('columnas', [])
                respuestas[campo.clave] = [
                    {col.get('clave', ''): f'Dato {i + 1}' for col in columnas}
                    for i in range(2)
                ]
            elif campo.tipo == 'fecha':
                respuestas[campo.clave] = '2025-05-08'
            elif campo.tipo == 'select':
                opciones = campo.opciones or []
                if opciones:
                    primera = opciones[0]
                    respuestas[campo.clave] = (
                        primera.get('valor', primera) if isinstance(primera, dict) else primera
                    )
            else:
                respuestas[campo.clave] = f'Prueba {campo.clave}'
        return respuestas

    def _validar_xlsx(self, xlsx_bytes):
        if not xlsx_bytes:
            raise ValueError('XLSX vacío')
        with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as zf:
            nombres = zf.namelist()
            if 'xl/worksheets/sheet1.xml' not in nombres:
                raise ValueError('El XLSX no contiene xl/worksheets/sheet1.xml')

    def handle(self, *args, **options):
        from documentos.models import Formato, SolicitudCambio
        from documentos.services.exportacion import exportar_xlsx, exportar_pdf

        todo_ok = True

        # 1. LibreOffice
        try:
            res = subprocess.run(
                ['libreoffice', '--version'], capture_output=True, text=True, timeout=30,
            )
            salida = (res.stdout or res.stderr or '').strip().splitlines()
            self.stdout.write(self.style.SUCCESS(
                f'LibreOffice: {salida[0] if salida else "presente"}'
            ))
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(
                'LibreOffice NO está instalado (el PDF no se podrá generar).'
            ))
            todo_ok = False
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'Error ejecutando LibreOffice: {exc}'))
            todo_ok = False

        # 2. Formato
        formato = Formato.objects.filter(codigo=options['formato'], activo=True).first()
        if not formato:
            self.stdout.write(self.style.ERROR(
                f'Formato {options["formato"]} no encontrado.'
            ))
            return
        if not formato.plantilla_xlsx:
            self.stdout.write(self.style.ERROR(
                f'El formato {formato.codigo} no tiene plantilla XLSX.'
            ))
            return
        self.stdout.write(
            f'Formato: {formato.codigo} v{formato.version} E{formato.edicion} '
            f'({formato.get_todos_los_campos().count()} campos)'
        )

        solicitud = SolicitudCambio(
            formato=formato,
            operacion='modificacion',
            respuestas=self._respuestas_demo(formato),
        )

        dir_salida = options.get('salida')
        if dir_salida:
            os.makedirs(dir_salida, exist_ok=True)

        # 3. XLSX
        xlsx_bytes = None
        try:
            xlsx_bytes = exportar_xlsx(solicitud)
            self._validar_xlsx(xlsx_bytes)
            self.stdout.write(self.style.SUCCESS(f'XLSX OK ({len(xlsx_bytes)} bytes)'))
            if dir_salida:
                destino = os.path.join(dir_salida, f'{formato.codigo}_prueba.xlsx')
                with open(destino, 'wb') as fh:
                    fh.write(xlsx_bytes)
                self.stdout.write(f'  guardado en {destino}')
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'XLSX FALLÓ: {exc}'))
            todo_ok = False

        # 4. PDF (XLSX → LibreOffice)
        pdf_path = None
        try:
            pdf_path = exportar_pdf(solicitud)
            if not pdf_path:
                raise ValueError('exportar_pdf devolvió None (¿LibreOffice?)')
            ruta = pdf_path if os.path.isabs(pdf_path) else os.path.join(settings.MEDIA_ROOT, pdf_path)
            with open(ruta, 'rb') as fh:
                cabecera = fh.read(5)
            tamano = os.path.getsize(ruta)
            if cabecera != b'%PDF-':
                raise ValueError(f'El archivo no es un PDF válido (cabecera={cabecera!r})')
            if tamano <= 0:
                raise ValueError('El PDF está vacío')
            self.stdout.write(self.style.SUCCESS(f'PDF OK ({tamano} bytes)'))
            if dir_salida:
                destino = os.path.join(dir_salida, f'{formato.codigo}_prueba.pdf')
                shutil.copy2(ruta, destino)
                self.stdout.write(f'  guardado en {destino}')
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'PDF FALLÓ: {exc}'))
            todo_ok = False
        finally:
            if pdf_path:
                ruta = pdf_path if os.path.isabs(pdf_path) else os.path.join(settings.MEDIA_ROOT, pdf_path)
                try:
                    os.unlink(ruta)
                except OSError:
                    pass

        self.stdout.write('')
        if todo_ok:
            self.stdout.write(self.style.SUCCESS('RESULTADO: exportación OK.'))
        else:
            self.stdout.write(self.style.ERROR('RESULTADO: hubo fallos.'))
