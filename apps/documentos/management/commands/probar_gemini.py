from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Prueba la configuración de Gemini (key/conexión), sondea modelos o resume un documento.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--documento',
            dest='documento',
            default=None,
            help='UUID del documento a resumir (si se omite, solo prueba la conexión).',
        )
        parser.add_argument(
            '--listar-modelos',
            dest='listar_modelos',
            action='store_true',
            help='Lista los modelos disponibles para la API key y termina.',
        )
        parser.add_argument(
            '--probar-modelos',
            dest='probar_modelos',
            action='store_true',
            help='Sondea varios modelos con una petición mínima y reporta cuáles responden.',
        )
        parser.add_argument(
            '--modelos',
            dest='modelos',
            default=None,
            help='Lista separada por comas de modelos a sondear (con --probar-modelos).',
        )
        parser.add_argument(
            '--reintentos',
            dest='reintentos',
            type=int,
            default=1,
            help='Reintentos por modelo en el sondeo (default 1).',
        )
        parser.add_argument(
            '--delay',
            dest='delay',
            type=float,
            default=1.5,
            help='Segundos de espera entre modelos en el sondeo (default 1.5).',
        )

    def _mostrar_config(self, estado):
        self.stdout.write('Configuración de IA:')
        self.stdout.write(f"  Habilitado:   {estado['habilitado']}")
        self.stdout.write(f"  API key:      {estado['key_prefijo']}")
        self.stdout.write(f"  Modelo:       {estado['modelo']}")
        self.stdout.write(f"  Respaldo:     {estado.get('modelo_fallback') or '(ninguno)'}")
        self.stdout.write(f"  Reintentos:   {estado.get('reintentos', '?')}")
        self.stdout.write(f"  Límite:       {estado['max_mb']} MB")
        self.stdout.write(f"  SDK genai:    {estado.get('sdk_version', '?')}")
        self.stdout.write('')

    def _sondear(self, ai, options):
        modelos = None
        if options.get('modelos'):
            modelos = [m.strip() for m in options['modelos'].split(',') if m.strip()]

        self.stdout.write('Sondeando modelos (petición mínima)...')
        resultado = ai.sondear_modelos(
            modelos=modelos,
            intentos=max(1, options.get('reintentos', 1)),
            delay=max(0.0, options.get('delay', 1.5)),
        )
        if not resultado.get('ok'):
            self.stdout.write(self.style.ERROR(f"ERROR: {resultado.get('error')}"))
            return

        self.stdout.write(f"{'MODELO':<28}{'ESTADO':<8}{'CÓDIGO':<8}MS")
        primer_ok = None
        for r in resultado['resultados']:
            estado = 'OK' if r['ok'] else 'FAIL'
            estilo = self.style.SUCCESS if r['ok'] else self.style.ERROR
            self.stdout.write(
                f"{r['modelo']:<28}{estilo(f'{estado:<8}')}{str(r['code'] or '-'):<8}{r['ms']}"
            )
            if r['ok'] and primer_ok is None:
                primer_ok = r['modelo']

        self.stdout.write('')
        if primer_ok:
            self.stdout.write(self.style.SUCCESS(
                f"Recomendado (disponible ahora): {primer_ok}"
            ))
            self.stdout.write('Para usarlo, edita .env:')
            self.stdout.write(f'  GEMINI_MODEL={primer_ok}')
        else:
            self.stdout.write(self.style.WARNING(
                'Ningún modelo respondió. Puede ser saturación general del tier '
                'gratuito; considera activar facturación o reintentar más tarde.'
            ))

    def handle(self, *args, **options):
        from documentos import ai

        estado = ai.estado_configuracion()
        self._mostrar_config(estado)

        if options.get('listar_modelos'):
            self.stdout.write('Consultando modelos disponibles...')
            resultado = ai.listar_modelos()
            if resultado.get('ok'):
                self.stdout.write(self.style.SUCCESS(f"{len(resultado['modelos'])} modelos:"))
                for nombre in resultado['modelos']:
                    self.stdout.write(f'  - {nombre}')
            else:
                self.stdout.write(self.style.ERROR(f"ERROR: {resultado.get('error')}"))
            return

        if options.get('probar_modelos'):
            self._sondear(ai, options)
            return

        if not estado['habilitado']:
            self.stdout.write(self.style.WARNING(
                'La IA no está habilitada (IA_RESUMEN_HABILITADO o GEMINI_API_KEY).'
            ))
            return

        documento_uuid = options.get('documento')
        if documento_uuid:
            from documentos.models import Documento
            try:
                documento = Documento.objects.get(uuid=documento_uuid)
            except Documento.DoesNotExist:
                raise CommandError(f'No existe el documento {documento_uuid}')

            self.stdout.write(f"Generando resumen de {documento.codigo} - {documento.nombre}...")
            resultado = ai.generar_resumen(documento)
        else:
            self.stdout.write('Probando conexión con Gemini...')
            resultado = ai.probar_conexion()

        if resultado.get('ok'):
            self.stdout.write(self.style.SUCCESS('OK'))
            if 'texto' in resultado:
                self.stdout.write(resultado['texto'])
            if resultado.get('resumen'):
                import json
                self.stdout.write(json.dumps(resultado['resumen'], ensure_ascii=False, indent=2))
        else:
            self.stdout.write(self.style.ERROR(f"ERROR: {resultado.get('error')}"))
