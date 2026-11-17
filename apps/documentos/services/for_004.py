"""Acta de desincorporación de archivos inactivos (SGC-FOR-004).

Cuando un documento relacionado de un padre pasa a ``obsoleto``, la ficha del
padre ofrece generar el acta. El acta es **multifila** (hasta 9 registros por
hoja) y solo al completar ambas firmas SES (Elaborado + Aprobado) se retira
la relación del padre.

Este módulo tiene dos bloques:

* **Detección y ganchos** (Fase 1): qué padres tienen relacionados
  inactivos, avisos y anulaciones cuando un registro cambia de estado o se
  elimina.
* **Generación XLSX/PDF** (Fase 2): rellena la plantilla oficial sin alterar
  su layout.

La plantilla real ``SGC-FOR-004 Acta desinc Arch Inac.xlsx`` (hoja
``SGC-FOR-004``) define el layout de referencia:

=====================  ====================================================
Filas                  Contenido
=====================  ====================================================
13                     Fecha del acta (día en letras)
15                     Encabezado NOMBRE (A:E) / CARGO (F:J)
16–19                  Participantes (4 filas de datos)
21                     Propósito (texto oficial, intacto)
23–31                  Registros inactivos (9 viñetas)
33                     Departamento / Coordinación / Área
40–41                  Bloques de firma (Elaborado / Aprobado)
42                     Sello de evidencia SES (solo con ambas firmas)
=====================  ====================================================

La hoja instructiva ``SGC-IDL-010`` (con imágenes) se conserva intacta.
"""
import io
import logging
import re

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .pdf_utils import xlsx_bytes_a_pdf as _xlsx_a_pdf

logger = logging.getLogger(__name__)

#: Código y hoja de la plantilla oficial.
FORMATO_FOR_004 = 'SGC-FOR-004'
HOJA_FOR_004 = 'SGC-FOR-004'

#: Texto oficial de la finalidad (no se altera).
PROPOSITO_OFICIAL = (
    'Con el propósito de llevar a cabo el acto de desincorporación o '
    'destrucción de los siguientes registros inactivos, por haber finalizado '
    'su tiempo de retención:'
)

#: La plantilla real tiene 4 filas de datos de participantes (16–19); la
#: fila 15 es el encabezado. No se insertan filas: se respetaría el layout.
MAX_PARTICIPANTES = 4

#: 9 viñetas por hoja (filas 23–31); con más registros se agregan hojas.
MAX_REGISTROS_POR_HOJA = 9

#: Celdas de la hoja oficial (ver docstring del módulo).
CELDA_PAGINA = 'I6'
FILA_FECHA = 13
FILA_PARTICIPANTE_INICIO = 16
COL_NOMBRE = 'A'
COL_CARGO = 'F'
FILA_REGISTRO_INICIO = 23
FILA_DEPARTAMENTO = 33
FILA_FIRMA_ETIQUETA = 40
FILA_FIRMA_DATOS = 41
FILA_SELLO = 42
CELDA_SELLO_RANGO = 'A42:J42'

#: Prefijo del código secuencial del acta (``ACT-DES-2026-0001``).
CODIGO_PREFIJO = 'ACT-DES'


# ---------------------------------------------------------------------------
# Detección
# ---------------------------------------------------------------------------

def relacionados_inactivos(padre):
    """Documentos relacionados de ``padre`` que ya pasaron a ``obsoleto``.

    Excluye los que ya están cubiertos por un acta abierta: el modal solo
    ofrece crear/reanudar una vez.
    """
    if padre is None:
        return padre.documentos_relacionados.none() if padre else []
    return padre.documentos_relacionados.filter(estado='obsoleto')


def pendientes_de_desincorporar(padre):
    """Relacionados obsoletos que aún no están en un acta abierto.

    Returns:
        queryset de ``Documento`` (vacío si no hay nada que actar o si ya
        existe un acta abierto para este padre).
    """
    if padre is None:
        from ..models import Documento

        return Documento.objects.none()
    if acta_abierta(padre) is not None:
        from ..models import Documento

        return Documento.objects.none()
    return padre.documentos_relacionados.filter(estado='obsoleto')


def acta_abierta(padre):
    """Acta ``borrador``/``pendiente_firma`` del padre (o ``None``).

    Hay como máximo una por constraint parcial; si llegara a haber dos por
    un fallo de integridad, se queda con la más reciente.
    """
    from ..models import ActaDesincorporacion

    if padre is None:
        return None
    return (
        ActaDesincorporacion.objects
        .filter(padre=padre, estado__in=('borrador', 'pendiente_firma'))
        .order_by('-creado')
        .first()
    )


def actas_con_registro(acta_query):
    """Actas abiertas que contienen alguno de los registros consultados."""
    from ..models import ActaDesincorporacion

    return ActaDesincorporacion.objects.filter(
        estado__in=('borrador', 'pendiente_firma'),
    ).filter(registros__in=acta_query).distinct()


# ---------------------------------------------------------------------------
# Permisos
# ---------------------------------------------------------------------------

def puede_gerenciar(usuario, padre):
    """¿Puede crear/firmar/anular el acta de ``padre``?

    Administradores, el jefe efectivo del departamento del padre y el
    propietario del padre (mismos criterios del resto de la ficha).
    """
    if usuario is None or not getattr(usuario, 'is_authenticated', False):
        return False
    perfil = getattr(usuario, 'perfil', None)
    if perfil is None:
        return False
    if perfil.es_administrador:
        return True
    if padre is None:
        return False
    if padre.propietario_id == usuario.id:
        return True
    from ..jefatura import es_jefe_efectivo

    return es_jefe_efectivo(usuario, padre.departamento)


def usuarios_a_avisar(padre, excluir=None):
    """Destinatarios del aviso: propietario, jefe del depto y administradores.

    Returns:
        lista de ``User`` sin duplicados (y sin ``excluir``).
    """
    from autenticacion.models import PerfilUsuario

    if padre is None:
        return []
    destinatarios = []
    if padre.propietario_id:
        destinatarios.append(padre.propietario)
    if padre.departamento and padre.departamento.jefe_id:
        destinatarios.append(padre.departamento.jefe)
    destinatarios.extend(
        p.usuario for p in PerfilUsuario.objects.filter(
            rol='administrador', usuario__is_active=True,
        ).select_related('usuario')
    )
    vistos = set()
    resultado = []
    for user in destinatarios:
        if user is None or not user.is_active:
            continue
        if excluir is not None and user.id == excluir.id:
            continue
        if user.id in vistos:
            continue
        vistos.add(user.id)
        resultado.append(user)
    return resultado


# ---------------------------------------------------------------------------
# Ganchos de cambio de estado
# ---------------------------------------------------------------------------

def avisar_desincorporacion(documento, excluir=None):
    """Avisa a los responsables de cada padre que ``documento`` quedó obsoleto.

    Se llama desde las tres rutas que inactivan un documento (aprobación de
    solicitud, cron de vencimientos y eliminación de la última versión).

    Idempotente por padre: no repite el aviso si ya hay uno **sin leer** del
    mismo tipo para el mismo documento padre, ni si el padre ya tiene un acta
    abierto (en ese caso la ficha ya ofrece reanudarlo).
    """
    if documento is None:
        return 0
    from autenticacion.models import Notificacion
    from ..models import Documento

    padres = Documento.objects.filter(
        documentos_relacionados=documento,
    ).exclude(id=documento.id).select_related('departamento', 'propietario')

    enviadas = 0
    for padre in padres:
        if acta_abierta(padre) is not None:
            continue
        pendientes = list(
            padre.documentos_relacionados.filter(estado='obsoleto')
        )
        if not pendientes:
            continue
        url = f'/documentos/{padre.tipo_id}/{padre.uuid}/'
        for user in usuarios_a_avisar(padre, excluir=excluir):
            ya_aviso = Notificacion.objects.filter(
                usuario=user,
                tipo='desincorporacion_pendiente',
                documento=padre,
                leida=False,
            ).exists()
            if ya_aviso:
                continue
            Notificacion.objects.create(
                usuario=user,
                titulo='Archivos inactivos por desincorporar',
                mensaje=(
                    f'"{documento.nombre}" ({documento.codigo}) pasó a obsoleto '
                    f'y está relacionado con "{padre.nombre}" ({padre.codigo}). '
                    f'Hay {len(pendientes)} registro(s) inactivo(s) pendiente(s) '
                    f'de desincorporar mediante el acta SGC-FOR-004.'
                ),
                tipo='desincorporacion_pendiente',
                url_accion=url,
                documento=padre,
                departamento=padre.departamento,
            )
            enviadas += 1
    return enviadas


def anular_por_reactivacion(documento, motivo=None):
    """Anula los actas abiertas que listan ``documento`` como registro.

    Se llama cuando un documento vuelve a ``activo`` (reactivación o
    renovación): el acta ya no describiría la realidad.
    """
    if documento is None:
        return 0
    from ..models import ActaDesincorporacion

    motivo = motivo or (
        f'El registro "{documento.nombre}" ({documento.codigo}) fue '
        f'reactivado antes de completar el acta.'
    )
    abiertas = ActaDesincorporacion.objects.filter(
        registros__documento_relacionado=documento,
        estado__in=('borrador', 'pendiente_firma'),
    ).distinct()
    return _anular_todas(abiertas, motivo)


def anular_por_eliminacion(documento, motivo=None):
    """Anula actas abiertas cuyo padre o registro es ``documento``.

    La eliminación definitiva borra la evidencia que el acta referenciaba.
    """
    if documento is None:
        return 0
    from ..models import ActaDesincorporacion

    motivo = motivo or (
        f'El documento "{documento.nombre}" ({documento.codigo}) fue eliminado.'
    )
    abiertas = ActaDesincorporacion.objects.filter(
        Q(padre=documento) | Q(registros__documento_relacionado=documento),
        estado__in=('borrador', 'pendiente_firma'),
    ).distinct()
    return _anular_todas(abiertas, motivo)


def _anular_todas(queryset, motivo):
    """Anula en bloque los actas abiertas de ``queryset`` (idempotente)."""
    from ..models import ActaDesincorporacion

    anuladas = 0
    ahora = timezone.now()
    for acta in queryset:
        try:
            with transaction.atomic():
                bloqueada = (
                    ActaDesincorporacion.objects
                    .select_for_update()
                    .get(pk=acta.pk)
                )
                if bloqueada.estado not in ('borrador', 'pendiente_firma'):
                    continue
                bloqueada.estado = 'anulada'
                bloqueada.anulada_en = ahora
                bloqueada.anulada_motivo = motivo
                bloqueada.save(update_fields=[
                    'estado', 'anulada_en', 'anulada_motivo', 'actualizado',
                ])
                anuladas += 1
        except ActaDesincorporacion.DoesNotExist:
            continue
    if anuladas:
        logger.info('Actas de desincorporación anuladas: %s (%s)',
                    anuladas, motivo)
    return anuladas


# ---------------------------------------------------------------------------
# Códigos
# ---------------------------------------------------------------------------

def generar_codigo():
    """Código secuencial por año: ``ACT-DES-2026-0001``.

    Reintenta ante una colisión de carrera (constraint único) hasta 5 veces.
    """
    from ..models import ActaDesincorporacion

    anio = timezone.localdate().year
    prefijo = f'{CODIGO_PREFIJO}-{anio}-'
    for _ in range(5):
        ultimo = (
            ActaDesincorporacion.objects
            .filter(codigo__startswith=prefijo)
            .order_by('-codigo')
            .values_list('codigo', flat=True)
            .first()
        )
        consecutivo = 1
        if ultimo:
            coincidencia = re.search(r'(\d+)$', ultimo)
            if coincidencia:
                consecutivo = int(coincidencia.group(1)) + 1
        codigo = f'{prefijo}{consecutivo:04d}'
        if not ActaDesincorporacion.objects.filter(codigo=codigo).exists():
            return codigo
    # Cinco intentos agotados: un sufijo aleatorio nunca debe tumbar el alta.
    return f'{prefijo}{timezone.now().strftime("%H%M%S")}'


# ---------------------------------------------------------------------------
# Altas y validación
# ---------------------------------------------------------------------------

def formato_for_004():
    """``Formato`` activo del SGC-FOR-004 (o ``None``)."""
    from ..models import Formato

    return Formato.objects.filter(codigo=FORMATO_FOR_004, activo=True).first()


def _fecha_texto(valor):
    if hasattr(valor, 'strftime'):
        return valor.strftime('%d/%m/%Y')
    return str(valor or '')


def validar_para_firmar(acta):
    """Revalida el acta justo antes de firmar/aplicar.

    Las rutas de inactivación pueden haber cambiado un registro desde la
    creación del acta; si algo ya no corresponde, el acta se cierra en lugar
    de firmarse sobre datos falsos.

    Raises:
        ErrorFirma: acta cerrada o registro ya no inactivo.
    """
    from .firmas import ErrorFirma

    if not acta.esta_abierta:
        raise ErrorFirma('Este acta ya fue cerrada.')
    for registro in acta.registros.all():
        relacionado = registro.documento_relacionado
        if relacionado is not None and relacionado.estado != 'obsoleto':
            raise ErrorFirma(
                f'El registro {registro.codigo or relacionado.codigo} ya no '
                f'está inactivo; anula el acta y genera una nueva.'
            )


def crear_acta(*, padre, participantes, registro_ids, creado_por,
               fecha=None, aprobado_por=None, elaborado_por=None):
    """Crea el acta (borrador) con sus participantes y registros.

    Args:
        padre: documento sobre cuya ficha se abre el acta.
        participantes: lista de ``{'nombre':…, 'cargo':…}`` (máx. 4).
        registro_ids: pks de los documentos relacionados obsoletos a actar.
        creado_por: usuario que crea el acta (firmante "Elaborado").
        fecha: fecha del acta (por defecto hoy).
        aprobado_por: firmante "Aprobado" (por defecto el jefe efectivo).
        elaborado_por: firmante "Elaborado" (por defecto ``creado_por``).

    Returns:
        ActaDesincorporacion creada (estado ``borrador``).

    Raises:
        ValueError: validación de entrada.
        IntegrityError: ya existe un acta abierto para el padre.
    """
    from ..models import (
        ActaDesincorporacion, ParticipanteActa, RegistroDesincorporado,
    )

    if padre is None:
        raise ValueError('El acta necesita un documento padre.')
    if acta_abierta(padre) is not None:
        raise ValueError('Ya existe un acta en curso para este documento.')
    if not registro_ids:
        raise ValueError('Debes seleccionar al menos un registro inactivo.')

    registros = list(
        padre.documentos_relacionados.filter(
            pk__in=registro_ids, estado='obsoleto',
        ).select_related('periodo_retencion')
    )
    if len(registros) != len(set(registro_ids)):
        raise ValueError('Algunos registros seleccionados ya no están inactivos.')
    # Respetar el orden en que se seleccionaron (no el de la BD).
    por_id = {doc.pk: doc for doc in registros}
    registros = [por_id[pk] for pk in registro_ids if pk in por_id]

    filas = []
    for item in participantes or []:
        if not isinstance(item, dict):
            continue
        nombre = str(item.get('nombre') or '').strip()
        if not nombre:
            continue
        filas.append({'nombre': nombre,
                      'cargo': str(item.get('cargo') or '').strip()})
    if not filas:
        raise ValueError('Debes indicar al menos un participante.')
    if len(filas) > MAX_PARTICIPANTES:
        raise ValueError(
            f'La plantilla SGC-FOR-004 admite máximo {MAX_PARTICIPANTES} '
            f'participantes.'
        )

    aprobador = aprobado_por or _aprobador_por_defecto(padre, creado_por)
    elaborador = elaborado_por or creado_por

    with transaction.atomic():
        acta = ActaDesincorporacion.objects.create(
            codigo=generar_codigo(),
            padre=padre,
            documento_codigo=padre.codigo or '',
            documento_nombre=padre.nombre or '',
            departamento=padre.departamento,
            departamento_nombre=(
                str(padre.departamento) if padre.departamento else ''
            ),
            fecha=fecha or timezone.localdate(),
            proposito=PROPOSITO_OFICIAL,
            estado='borrador',
            creado_por=creado_por,
            elaborado_por=elaborador,
            aprobado_por=aprobador,
        )
        ParticipanteActa.objects.bulk_create([
            ParticipanteActa(
                acta=acta, nombre=p['nombre'], cargo=p['cargo'], orden=i,
            )
            for i, p in enumerate(filas)
        ])
        RegistroDesincorporado.objects.bulk_create([
            RegistroDesincorporado(
                acta=acta,
                documento_relacionado=doc,
                codigo=doc.codigo or '',
                nombre=doc.nombre or '',
                fecha_vencimiento=doc.fecha_vencimiento,
                fecha_retencion=doc.fecha_retencion,
                fecha_destruccion=doc.fecha_destruccion,
                orden=i,
            )
            for i, doc in enumerate(registros)
        ])

    return acta


def _aprobador_por_defecto(padre, creado_por):
    """Jefe efectivo del departamento; si no, el propietario; si no, el creador."""
    departamento = padre.departamento
    if departamento and departamento.jefe_id:
        return departamento.jefe
    if padre.propietario_id:
        return padre.propietario
    return creado_por


# ---------------------------------------------------------------------------
# Textos de la plantilla
# ---------------------------------------------------------------------------

_UNIDADES = (
    'cero', 'uno', 'dos', 'tres', 'cuatro', 'cinco', 'seis', 'siete',
    'ocho', 'nueve', 'diez', 'once', 'doce', 'trece', 'catorce', 'quince',
    'dieciséis', 'diecisiete', 'dieciocho', 'diecinueve', 'veinte',
    'veintiuno', 'veintidós', 'veintitrés', 'veinticuatro', 'veinticinco',
    'veintiséis', 'veintisiete', 'veintiocho', 'veintinueve',
)
_DECENAS = {
    30: 'treinta', 40: 'cuarenta', 50: 'cincuenta', 60: 'sesenta',
    70: 'setenta', 80: 'ochenta', 90: 'noventa',
}
MESES = (
    '', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
    'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
)


def dia_en_letras(numero):
    """Número (1–31) en letras para la línea de fecha del acta."""
    numero = int(numero)
    if 1 <= numero <= 29:
        return _UNIDADES[numero]
    if numero == 30:
        return 'treinta'
    if numero == 31:
        return 'treinta y uno'
    return str(numero)


def texto_fecha_acta(fecha):
    """Línea 13 de la plantilla, con la fecha del acta rellenada."""
    if fecha is None:
        fecha = timezone.localdate()
    return (
        f'En el día de hoy {fecha.day} ( {dia_en_letras(fecha.day)} ) '
        f'de {MESES[fecha.month]} de {fecha.year}, se reunieron los ciudadanos:'
    )


def texto_registro(registro):
    """Viñeta de la lista de registros inactivos (filas 23–31)."""
    partes = []
    if registro.fecha_vencimiento:
        partes.append(f'vencimiento {_fecha_texto(registro.fecha_vencimiento)}')
    if registro.fecha_retencion:
        partes.append(f'retención {_fecha_texto(registro.fecha_retencion)}')
    if registro.fecha_destruccion:
        partes.append(f'destrucción {_fecha_texto(registro.fecha_destruccion)}')
    texto = f'§  {registro.codigo} — {registro.nombre}'.rstrip(' —')
    if partes:
        texto += f' ({", ".join(partes)})'
    return texto


def _linea_firma(etiqueta, firma):
    partes = [f'Firmado electrónicamente por {firma.firmante_nombre or firma.firmante_clave}']
    partes.append(etiqueta)
    if firma.fecha_firma:
        partes.append(
            timezone.localtime(firma.fecha_firma).strftime('%d/%m/%Y %H:%M')
        )
    partes.append(f'SES #{firma.pk}')
    if firma.hash_contenido:
        partes.append(f'SHA-256 {firma.hash_contenido[:16]}…')
    return ' — '.join(partes)


def sello_acta(acta):
    """Línea(s) de evidencia SES para la fila 42 (vacío sin ambas firmas)."""
    if not acta.firmas_completas:
        return ''
    firmada_elab = acta.firma_elaborado
    firmada_aprob = acta.firma_aprobado
    if (firmada_elab_id := getattr(firmada_elab, 'id', None)) == getattr(
        firmada_aprob, 'id', None
    ):
        # Misma persona en ambos roles: UNA sola firma, un solo bloque.
        return _linea_firma('Elaborado y Aprobado', firmada_elab)
    lineas = [_linea_firma('Elaborado', firmada_elab)]
    if firmada_aprob is not None:
        lineas.append(_linea_firma('Aprobado', firmada_aprob))
    return '\n'.join(lineas)


# ---------------------------------------------------------------------------
# Generación XLSX/PDF
# ---------------------------------------------------------------------------

def _copiar_hoja(wb, base):
    """Copia la hoja base conservando merges e imágenes (openpyxl no las copia)."""
    import copy as _copy

    nueva = wb.copy_worksheet(base)
    existentes = {str(rango) for rango in nueva.merged_cells.ranges}
    for rango in base.merged_cells.ranges:
        if str(rango) not in existentes:
            nueva.merge_cells(str(rango))
    for imagen in base._images:
        nueva._images.append(_copy.deepcopy(imagen))
    return nueva


def _rellenar_cabecera(hoja, acta, indice, total):
    """Rellena todo lo común a todas las hojas del acta."""
    hoja[CELDA_PAGINA] = f'PAGINA:   {indice} DE {total}'
    hoja[f'A{FILA_FECHA}'] = texto_fecha_acta(acta.fecha)
    for i, participante in enumerate(acta.participantes.all()):
        if i >= MAX_PARTICIPANTES:
            break
        fila = FILA_PARTICIPANTE_INICIO + i
        hoja[f'{COL_NOMBRE}{fila}'] = participante.nombre
        hoja[f'{COL_CARGO}{fila}'] = participante.cargo
    hoja[f'A{21}'] = acta.proposito or PROPOSITO_OFICIAL
    hoja[f'A{FILA_DEPARTAMENTO}'] = (
        f'Pertenecientes al Departamento/ Coordinación / Área: '
        f'{acta.departamento_nombre}'
    )
    elabora = acta.elaborado_por
    aprueba = acta.aprobado_por
    hoja[f'A{FILA_FIRMA_ETIQUETA}'] = 'Elaborado por:'
    hoja[f'F{FILA_FIRMA_ETIQUETA}'] = 'Aprobado por:'
    hoja[f'A{FILA_FIRMA_DATOS}'] = _texto_firmante(elabora)
    hoja[f'F{FILA_FIRMA_DATOS}'] = _texto_firmante(aprueba)


def _texto_firmante(usuario):
    if usuario is None:
        return 'Nombre: \nCargo: '
    from ..models import _cargo_usuario

    return (
        f'Nombre: {usuario.get_full_name() or usuario.username}\n'
        f'Cargo: {_cargo_usuario(usuario) or "—"}'
    )


def generar_xlsx(acta):
    """Bytes del XLSX del acta (9 registros por hoja; hojas extra si hace falta).

    Raises:
        ValueError: si el formato SGC-FOR-004 no está integrado.
    """
    import openpyxl

    formato = formato_for_004()
    if formato is None or not formato.plantilla_xlsx:
        raise ValueError(
            'El formato SGC-FOR-004 no está integrado o no tiene plantilla.'
        )

    with formato.plantilla_xlsx.open('rb') as fh:
        plantilla = fh.read()
    wb = openpyxl.load_workbook(io.BytesIO(plantilla))

    base = wb[HOJA_FOR_004] if HOJA_FOR_004 in wb.sheetnames else wb.worksheets[0]
    registros = list(acta.registros.order_by('orden', 'id'))
    trozos = [
        registros[i:i + MAX_REGISTROS_POR_HOJA]
        for i in range(0, len(registros), MAX_REGISTROS_POR_HOJA)
    ] or [[]]
    total = len(trozos)

    # Se copian las hojas ANTES de rellenar (copia de la base vacía).
    hojas = [base]
    for idx in range(1, total):
        nueva = _copiar_hoja(wb, base)
        nueva.title = f'{HOJA_FOR_004} ({idx + 1})'
        hojas.append(nueva)

    for indice, (hoja, trozo) in enumerate(zip(hojas, trozos), start=1):
        _rellenar_cabecera(hoja, acta, indice, total)
        for offset, registro in enumerate(trozo):
            hoja[f'A{FILA_REGISTRO_INICIO + offset}'] = texto_registro(registro)
        # La plantilla trae viñetas vacías: se limpian las filas no usadas de
        # esta hoja para que el acta no muestre registros fantasma.
        for offset in range(len(trozo), MAX_REGISTROS_POR_HOJA):
            hoja[f'A{FILA_REGISTRO_INICIO + offset}'] = None

    # Sello de evidencia solo en la última hoja y solo con ambas firmas.
    sello = sello_acta(acta)
    if sello:
        ultima = hojas[-1]
        if CELDA_SELLO_RANGO not in {str(r) for r in ultima.merged_cells.ranges}:
            ultima.merge_cells(CELDA_SELLO_RANGO)
        celda = ultima[f'A{FILA_SELLO}']
        celda.value = sello
        celda.alignment = openpyxl.styles.Alignment(
            horizontal='left', vertical='center', wrap_text=True,
        )
        celda.font = openpyxl.styles.Font(size=8, italic=True)
        ultima.row_dimensions[FILA_SELLO].height = 15 * (sello.count('\n') + 1)

    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


def generar_pdf(acta):
    """Bytes del PDF del acta (o ``None`` si LibreOffice no está disponible)."""
    return _xlsx_a_pdf(generar_xlsx(acta))


def nombre_archivo(acta, extension):
    codigo = (acta.documento_codigo or 'SGC').replace(' ', '_')
    return f'SGC-FOR-004_{codigo}_{acta.uuid.hex[:8]}.{extension}'


def generar_y_guardar(acta, forzar=False):
    """Genera y persiste el XLSX/PDF del acta (idempotente).

    Nunca lanza: devuelve ``{'success': bool, 'xlsx': str|None,
    'pdf': str|None, 'error': str}``.
    """
    from django.core.files.base import ContentFile

    if not forzar and acta.xlsx:
        return {
            'success': True, 'xlsx': acta.xlsx.name,
            'pdf': acta.pdf.name or None, 'error': '',
        }
    try:
        xlsx = generar_xlsx(acta)
    except Exception as exc:  # noqa: BLE001
        logger.error('No se pudo generar el XLSX FOR-004: %s', exc)
        return {'success': False, 'xlsx': None, 'pdf': None, 'error': str(exc)}

    if forzar:
        for campo in ('xlsx', 'pdf'):
            archivo = getattr(acta, campo)
            if archivo:
                try:
                    archivo.delete(save=False)
                except Exception:  # noqa: BLE001
                    pass

    acta.xlsx.save(nombre_archivo(acta, 'xlsx'), ContentFile(xlsx), save=False)
    acta.pdf = ''
    try:
        pdf = _xlsx_a_pdf(xlsx)
    except Exception as exc:  # noqa: BLE001
        logger.warning('No se pudo generar el PDF FOR-004: %s', exc)
        pdf = None
    if pdf:
        acta.pdf.save(nombre_archivo(acta, 'pdf'), ContentFile(pdf), save=False)
    acta.save(update_fields=['xlsx', 'pdf', 'actualizado'])
    return {
        'success': True, 'xlsx': acta.xlsx.name,
        'pdf': acta.pdf.name or None, 'error': '',
    }


def regenerar_tras_firma(acta):
    """Regenera XLSX/PDF tras cada firma; nunca lanza."""
    def _generar():
        try:
            from ..tasks import generar_for_004_acta

            generar_for_004_acta.delay(acta.pk, True)
        except Exception as exc:  # noqa: BLE001 - broker caído: fallback
            logger.warning(
                'No se pudo encolar la generación FOR-004 de %s: %s',
                acta.codigo, exc,
            )
            try:
                generar_y_guardar(acta, forzar=True)
            except Exception as exc2:  # noqa: BLE001
                logger.error(
                    'Generación FOR-004 en línea falló para %s: %s',
                    acta.codigo, exc2,
                )

    transaction.on_commit(_generar)


# ---------------------------------------------------------------------------
# Aplicación (retiro de la relación) y avisos
# ---------------------------------------------------------------------------

def aplicar_acta(acta, forzar=False):
    """Retira del padre las relaciones de los registros y cierra el acta.

    Idempotente y segura ante concurrencia (``select_for_update``): solo se
    aplica con ambas firmas registradas y una vez.

    Returns:
        bool: ``True`` si el acta pasó a ``aplicada`` en esta llamada.
    """
    from ..models import ActaDesincorporacion

    with transaction.atomic():
        bloqueada = (
            ActaDesincorporacion.objects
            .select_for_update()
            .get(pk=acta.pk)
        )
        if bloqueada.estado == 'aplicada':
            return False
        if not bloqueada.firmas_completas:
            if forzar:
                raise ValueError('El acta no tiene las dos firmas requeridas.')
            return False
        validar_para_firmar(bloqueada)

        padre = bloqueada.padre
        if padre is not None:
            relaciones = [
                r.documento_relacionado_id
                for r in bloqueada.registros.all()
                if r.documento_relacionado_id
            ]
            if relaciones:
                padre.documentos_relacionados.remove(*relaciones)

        bloqueada.estado = 'aplicada'
        bloqueada.aplicada_en = timezone.now()
        bloqueada.save(update_fields=['estado', 'aplicada_en', 'actualizado'])

    logger.info('Acta %s aplicada (relación retirada).', bloqueada.codigo)
    notificar_aplicada(bloqueada)
    return True


def notificar_aplicada(acta):
    """Avisa al creador, a los participantes y al aprobador del acta cerrada."""
    from autenticacion.models import Notificacion

    if not acta.padre_id:
        return 0
    url = acta.url_descarga
    destinatarios = {}
    for user in (
        acta.creado_por, acta.elaborado_por, acta.aprobado_por,
        acta.padre.propietario if acta.padre else None,
    ):
        if user is not None and user.is_active:
            destinatarios[user.id] = user

    enviadas = 0
    for user in destinatarios.values():
        Notificacion.objects.create(
            usuario=user,
            titulo='Acta de desincorporación aplicada',
            mensaje=(
                f'El acta {acta.codigo} (SGC-FOR-004) de '
                f'"{acta.documento_nombre}" quedó firmada y aplicada: '
                f'se retiraron {acta.registros.count()} relación(es) '
                f'inactiva(s).'
            ),
            tipo='desincorporacion_aplicada',
            url_accion=url,
            documento=acta.padre,
            departamento=acta.departamento,
        )
        enviadas += 1
    return enviadas


def filtrar_relaciones_bloqueadas(padre, documentos):
    """Excluye relaciones que retiró un acta SGC-FOR-004 ya aplicada.

    Un acta aplicado es evidencia cerrada: volver a enlazar el registro
    desincorporado la contradice. La planilla/edición sigue sin esas
    relaciones (se registra el bloqueo en el log).
    """
    from ..models import ActaDesincorporacion

    documentos = list(documentos)
    if padre is None or not documentos:
        return documentos
    bloqueados = set(
        ActaDesincorporacion.objects
        .filter(padre=padre, estado='aplicada')
        .values_list('registros__documento_relacionado_id', flat=True)
    )
    bloqueados.discard(None)
    if not bloqueados:
        return documentos
    permitidos = [d for d in documentos if d.pk not in bloqueados]
    if len(permitidos) != len(documentos):
        retirados = len(documentos) - len(permitidos)
        logger.warning(
            'FOR-004: se bloquearon %s relación(es) ya desincorporadas '
            'para el documento %s.',
            retirados, getattr(padre, 'codigo', padre.pk),
        )
    return permitidos


# ---------------------------------------------------------------------------
# Firma híbrida: enlace token para un rol (PIN interno se hace en la vista)
# ---------------------------------------------------------------------------

def crear_invitacion_rol(acta, rol):
    """Crea (o reutiliza) la firma externa y su invitación token para ``rol``.

    El correo de la firma es el del firmante ASIGNADO al rol: la invitación
    solo puede firmar en nombre de esa persona (``_firmante_coincide`` del
    spec lo revalida al firmar).

    Returns:
        tuple: ``(invitacion, token)`` — el token en claro **no se persiste**.

    Raises:
        ValueError: rol inválido, sin correo, o rol ya firmado.
    """
    import hashlib
    import secrets
    from datetime import timedelta

    from ..models import FirmaElectronica, InvitacionFirma
    from .firmas import VIGENCIA_INVITACION_DIAS, crear_firma

    if rol not in ('elaborado', 'aprobado'):
        raise ValueError('Rol de firma no reconocido.')
    validar_para_firmar(acta)
    asignado = acta.firmante_asignado(rol)
    if asignado is None:
        raise ValueError('Este acta no tiene firmante asignado para ese rol.')
    email = (asignado.email or '').strip().lower()
    if not email:
        raise ValueError(
            'El firmante asignado no tiene correo configurado para recibir '
            'el enlace de firma.'
        )

    firma = FirmaElectronica.para(acta).filter(
        firmante_clave=FirmaElectronica.clave_externa(email),
    ).first()
    if firma is None:
        from ..models import _cargo_usuario

        firma = crear_firma(
            acta, tipo='externa',
            firmante_clave=FirmaElectronica.clave_externa(email),
            firmante_nombre=asignado.get_full_name() or asignado.username,
            firmante_email=email,
            firmante_cargo=_cargo_usuario(asignado),
            datos={'rol': rol},
        )
    elif firma.estado != 'pendiente':
        raise ValueError('Ese bloque del acta ya fue firmado.')

    token = secrets.token_urlsafe(32)
    invitacion = InvitacionFirma.objects.create(
        firma=firma,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expira=timezone.now() + timedelta(days=VIGENCIA_INVITACION_DIAS),
    )
    return invitacion, token


def enviar_invitacion_rol(acta, rol, request=None):
    """Crea la invitación y envía el correo con el enlace de firma.

    Returns:
        str: correo del destinatario.

    Raises:
        ValueError: sin correo / rol inválido / ya firmado.
        Exception: fallo de envío (el llamador la muestra al usuario).
    """
    from django.conf import settings
    from django.core import mail
    from django.urls import reverse

    from .avisos import aviso_texto, cuerpo_email_html
    from .firmas import VIGENCIA_INVITACION_DIAS

    invitacion, token = crear_invitacion_rol(acta, rol)
    asignado = acta.firmante_asignado(rol)
    destinatario = (asignado.email or '').strip()

    url_firma = reverse('documentos:firmar_invitacion', args=[token])
    link_firma = request.build_absolute_uri(url_firma) if request else url_firma
    clasificacion = (
        (acta.padre.clasificacion or 'interno') if acta.padre else 'interno'
    ).lower()
    etiqueta_rol = 'Elaborado' if rol == 'elaborado' else 'Aprobado'

    asunto = f'[POLIDOC] Acta de desincorporación {acta.codigo} — firma {etiqueta_rol}'
    lineas = [
        f'Hola {asignado.get_full_name() or asignado.username},',
        '',
        f'Se te asignó como "{etiqueta_rol}" del acta de desincorporación '
        f'de archivos inactivos:',
        '',
        f'  Acta: {acta.codigo} (SGC-FOR-004)',
        f'  Documento: {acta.documento_codigo} — {acta.documento_nombre}',
        f'  Fecha del acta: {acta.fecha:%d/%m/%Y}',
        f'  Registros a desincorporar: {acta.registros.count()}',
        '',
        'Para firmar ingresa a (válido por '
        f'{VIGENCIA_INVITACION_DIAS} días; la firma es de un solo uso):',
        link_firma,
        '',
        aviso_texto(clasificacion),
    ]
    message = mail.EmailMultiAlternatives(
        asunto, '\n'.join(lineas), settings.DEFAULT_FROM_EMAIL, [destinatario],
    )
    message.attach_alternative(cuerpo_email_html(lineas, clasificacion), 'text/html')
    mail.mailers.default.send_messages([message])
    return destinatario
