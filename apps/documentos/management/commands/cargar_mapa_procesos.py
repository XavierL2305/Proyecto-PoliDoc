"""Comando idempotente para cargar Mapas de Procesos oficiales por empresa."""
import json

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from documentos.models import Departamento
from documentos.services.mapa_procesos_oficial import (
    MAPAS_PROCESOS_OFICIALES,
    construir_mapa_procesos_oficial,
)
from documentos.services.organigrama import (
    crear_mapa_procesos_inicial,
    guardar_organigrama,
    obtener_datos_version,
    obtener_mapa_procesos_vigente,
)


def _resolver_usuario(username, doc_existente):
    if username:
        usuario = User.objects.filter(username=username).first()
        if usuario is None:
            raise CommandError(f'No existe el usuario "{username}".')
        return usuario

    if doc_existente is not None and doc_existente.propietario_id:
        return doc_existente.propietario

    admin = (
        User.objects
        .filter(is_active=True, perfil__rol='administrador')
        .order_by('id')
        .first()
    )
    if admin is not None:
        return admin

    superu = User.objects.filter(is_active=True, is_superuser=True).order_by('id').first()
    if superu is not None:
        return superu

    raise CommandError(
        'No se encontró un usuario administrador o superusuario activo; usa --usuario.'
    )


class Command(BaseCommand):
    help = 'Construye y carga el Mapa de Procesos oficial para la empresa indicada.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--empresa',
            type=str,
            default='FS',
            help=f'Código de empresa ({", ".join(sorted(MAPAS_PROCESOS_OFICIALES))}).',
        )
        parser.add_argument(
            '--usuario',
            type=str,
            default='',
            help='Username del autor registrado en versión/auditoría.',
        )
        parser.add_argument(
            '--nueva-version',
            action='store_true',
            help='Incrementa la versión vigente (vX -> vX+1) al cargar.',
        )
        parser.add_argument(
            '--forzar',
            action='store_true',
            help='Sobrescribe la versión vigente aunque ya tenga figuras guardadas.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Construye y valida el JSON oficial sin escribir en la base de datos.',
        )

    def handle(self, *args, **options):
        empresa = (options.get('empresa') or '').strip().upper()
        if empresa not in MAPAS_PROCESOS_OFICIALES:
            disponibles = ', '.join(sorted(MAPAS_PROCESOS_OFICIALES))
            raise CommandError(
                f'Empresa no soportada: "{empresa}". Disponibles: {disponibles}.'
            )

        deptos_por_codigo = dict(
            Departamento.objects.filter(activo=True).values_list('codigo', 'id')
        )
        datos = construir_mapa_procesos_oficial(empresa, deptos_por_codigo)
        n_sec = len(datos['sections'])
        n_nod = len(datos['nodes'])
        n_edg = len(datos['edges'])
        size_bytes = len(
            json.dumps(datos, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        )

        if options['dry_run']:
            self.stdout.write(
                self.style.SUCCESS(
                    f'[DRY-RUN] Mapa de Procesos {empresa} válido: '
                    f'{n_sec} secciones, {n_nod} figuras, {n_edg} conexiones ({size_bytes} bytes).'
                )
            )
            return

        doc = obtener_mapa_procesos_vigente(empresa)
        usuario = _resolver_usuario((options.get('usuario') or '').strip(), doc)

        if doc is None:
            doc = crear_mapa_procesos_inicial(usuario, empresa, datos=datos)
            self.stdout.write(
                self.style.SUCCESS(
                    f'Mapa de Procesos {empresa} creado en {doc.codigo} v{doc.version_actual}: '
                    f'{n_sec} secciones, {n_nod} figuras, {n_edg} conexiones ({size_bytes} bytes).'
                )
            )
            return

        if doc.estado in ('en_revision', 'obsoleto'):
            raise CommandError(
                f'No se puede actualizar {doc.codigo} porque su estado es "{doc.estado}".'
            )

        _, datos_actuales = obtener_datos_version(doc)
        tiene_contenido = bool(
            (datos_actuales or {}).get('nodes') or (datos_actuales or {}).get('sections')
        )
        if tiene_contenido and not options['forzar'] and not options['nueva_version']:
            self.stdout.write(
                self.style.WARNING(
                    f'{doc.codigo} v{doc.version_actual} ya tiene contenido guardado '
                    f'({len(datos_actuales.get("nodes") or [])} figuras). '
                    f'No se realizaron cambios; usa --nueva-version o --forzar para actualizar.'
                )
            )
            return

        doc_guardado, _ = guardar_organigrama(
            documento=doc,
            usuario=usuario,
            datos_validados=datos,
            revision_esperada=doc.revision_edicion,
            incrementar_version=bool(options['nueva_version']),
            cambios=f'Carga inicial del Mapa de Procesos oficial ({empresa})',
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'Mapa de Procesos {empresa} cargado en {doc_guardado.codigo} '
                f'v{doc_guardado.version_actual} (rev {doc_guardado.revision_edicion}): '
                f'{n_sec} secciones, {n_nod} figuras, {n_edg} conexiones ({size_bytes} bytes).'
            )
        )
