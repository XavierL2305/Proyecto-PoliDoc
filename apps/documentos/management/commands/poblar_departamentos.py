from django.core.management.base import BaseCommand
from documentos.models import Departamento
from documentos.cache_utils import invalidar_departamentos


class Command(BaseCommand):
    help = 'Pobla la tabla de Departamentos con los sectores oficiosos de POLIDOC'

    def handle(self, *args, **options):
        departamentos = [
            (1, 'Atención al Cliente Crédito y Cobranza', 'ACC', True),
            (2, 'Almacén', 'AL', True),
            (3, 'Servicios Médicos de Hospitalización', 'SMh', True),
            (4, 'Servicios Generales', 'SG', True),
            (5, 'Laboratorio', 'LAB', True),
            (6, 'Talento Humano', 'TH', True),
            (7, 'Auditoría Interna', 'AI', True),
            (8, 'Consultoría Jurídica', 'CJ', True),
            (9, 'Contabilidad y Tributos', 'CYT', True),
            (10, 'Administración y Finanzas', 'AF', True),
            (11, 'Servicio de Farmacia Hospitalaria', 'SFh', True),
            (12, 'Fuente de Soda Policlínica Táchira, C.A', 'FS', True),
            (13, 'Servicio de Seguridad y Salud Laboral', 'SSS', True),
            (14, 'Gerencia General', 'GG', True),
            (15, 'Tecnología de la Información', 'TI', True),
            (16, 'Comité de Seguridad y Salud Laboral', 'CHS', True),
            (17, 'Admisión', 'AD', True),
            (18, 'Cobranza', 'CO', True),
            (19, 'Facturación', 'FAC', True),
            (20, 'Servicio de Odontología', 'SOD', True),
            (21, 'Lavandería', 'LA', True),
            (22, 'Compras y Almacén', 'CA', True),
            (23, 'Compras', 'COM', True),
            (24, 'Coordinación Médica', 'CM', True),
            (25, 'Servicios Médicos Residentes', 'SMr', True),
            (26, 'Servicios Médicos de Enfermería', 'SMe', True),
            (27, 'Servicios Médicos de Emergencia', 'SMg', True),
            (28, 'Servicios Médicos del Área Quirúrgica', 'SMq', True),
            (29, 'Servicios Médicos Unidad de Cuidados Intensivos', 'SMu', True),
            (30, 'Servicios Médicos Terapia Respiratoria', 'SMt', True),
            (31, 'Servicios de Imagenología', 'SIM', True),
            (32, 'Servicio de Fisiatría y Rehabilitación', 'SFR', True),
            (33, 'Servicio de Nutrición y Dietética', 'SNd', True),
            (34, 'Mantenimiento de Infraestructura y Resguardo', 'MIR', True),
            (35, 'Policlínica Táchira, C.A', 'PT', True),
            (36, 'Policlínica Táchira Hospitalización, C.A', 'PTH', True),
            (37, 'Farmacia Policlínica, C.A', 'FP', True),
            (38, 'Sistema de Gestión de la Calidad', 'SGC', True),
        ]

        created = 0
        updated = 0

        for dept_data in departamentos:
            id_, nombre, codigo, activo = dept_data
            dept, created_flag = Departamento.objects.update_or_create(
                id=id_,
                defaults={
                    'nombre': nombre,
                    'codigo': codigo,
                    'activo': activo,
                }
            )
            if created_flag:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Departamentos poblados: {created} creados, {updated} actualizados (total: {created + updated})'
            )
        )

        invalidar_departamentos()