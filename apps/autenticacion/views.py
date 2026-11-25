import logging
import json
from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Q, Count
from .models import PerfilUsuario, Notificacion
from documentos.models import (
    Documento, SolicitudDescarga,
    VersionDocumento, AsignacionDocumento, Departamento
)
from documentos.services.vigencia import calcular_conteos_vigencia
from auditoria.services import obtener_logs
from auditoria.middleware import resolver_ip_cliente

logger = logging.getLogger('django_auth_ldap')

# Rate-limiting de intentos de login fallidos (mitiga fuerza bruta).
# Los valores viven en settings (por entorno); ver nota ahí sobre lockout de AD.
LOGIN_MAX_INTENTOS = getattr(settings, 'LOGIN_MAX_INTENTOS', 3)
LOGIN_BLOQUEO_SEGUNDOS = getattr(settings, 'LOGIN_BLOQUEO_SEGUNDOS', 300)
MENSAJE_CREDENCIALES = 'Usuario o contraseña incorrectos.'


def _ctx_login(extra=None):
    """
    Contexto base del login.

    Siempre expone la versión vigente de los Términos (settings.TERMINOS_VERSION)
    para que la plantilla pueda mostrarla en el modal.
    """
    ctx = {'terminos_version': settings.TERMINOS_VERSION}
    if extra:
        ctx.update(extra)
    return ctx



def _login_throttle_key(ip, username):
    # La IP la resuelve resolver_ip_cliente (X-Forwarded-For tras proxy de
    # confianza), NO REMOTE_ADDR crudo: detrás de un hosting/CDN/balanceador
    # REMOTE_ADDR es el proxy para todos y el throttle colapsaría a "por usuario",
    # permitiendo account-lockout DoS y una atribución por IP incorrecta.
    return f'login_fail::{ip or "sin-ip"}::{(username or "").lower()}'


# El throttle vive en la caché (Redis). Si Redis se cae o hay un pico de red,
# estas operaciones lanzarían ConnectionError y romperían el login con un 500,
# dejando la puerta de entrada inaccesible. Se degradan con gracia (fail-open):
# se registra un warning y el rate-limiting se omite, pero NUNCA se tumba el
# acceso. Perder el throttle en una caída puntual es un mal menor frente a
# dejar fuera a todos los usuarios (escenario multi-local con hosting).
def _cache_get_seguro(clave, por_defecto):
    try:
        return cache.get(clave, por_defecto)
    except Exception as exc:
        logger.warning(
            "Cache de throttle no disponible al leer (%s); se omite el "
            "rate-limiting de login (fail-open).", exc
        )
        return por_defecto


def _cache_set_seguro(clave, valor, timeout):
    try:
        cache.set(clave, valor, timeout)
    except Exception as exc:
        logger.warning(
            "Cache de throttle no disponible al escribir (%s); el intento "
            "fallido no queda contabilizado.", exc
        )


def _cache_delete_seguro(clave):
    try:
        cache.delete(clave)
    except Exception as exc:
        logger.warning(
            "Cache de throttle no disponible al limpiar (%s); se ignora.", exc
        )


def login_view(request):
    """
    Vista de login con autenticación LDAP.

    - GET: Muestra el formulario de login
    - POST: Intenta autenticar contra Active Directory
        - Si es exitoso: crea sesión y redirige al dashboard
        - Si falla: retorna error a la plantilla
    """
    if request.user.is_authenticated:
        return redirect('autenticacion:dashboard')

    error = None
    terminos_aceptados = False

    if request.method == 'POST':
        username = request.POST.get('usuario', '').strip()
        password = request.POST.get('password', '')
        remember = request.POST.get('remember') == 'on'
        terms = request.POST.get('terms')

        if not username or not password:
            error = 'Debes ingresar usuario y contraseña.'
        else:
            ip_cliente = resolver_ip_cliente(request) or 'sin-ip'
            throttle_key = _login_throttle_key(ip_cliente, username)
            intentos = _cache_get_seguro(throttle_key, 0)
            if intentos >= LOGIN_MAX_INTENTOS:
                logger.warning(
                    f"Login bloqueado por exceso de intentos: {username} "
                    f"desde {ip_cliente}"
                )
                error = (
                    'Demasiados intentos fallidos. '
                    'Espera unos minutos antes de volver a intentar.'
                )
                return render(
                    request,
                    'registration/login.html',
                    _ctx_login({'error': error}),
                )

            user = authenticate(request, username=username, password=password)

            if user is not None:
                _cache_delete_seguro(throttle_key)
                backend = getattr(user, 'backend', '')
                if backend.endswith('ModelBackend'):
                    # Autenticación local SIN LDAP: cuenta "break-glass".
                    # Se registra con prominencia para auditoría.
                    logger.warning(
                        f"LOGIN BREAK-GLASS vía ModelBackend (sin LDAP): "
                        f"{username} desde {ip_cliente}"
                    )
                # Verificar perfil
                version_terminos_pendiente = False
                try:
                    perfil = user.perfil
                    # Los términos sólo cuentan como aceptados si la versión
                    # aceptada coincide con la vigente (TERMINOS_VERSION):
                    # si cambió el texto, hay que volver a aceptarlos.
                    terminos_aceptados = (
                        perfil.acepto_terminos
                        and perfil.terminos_version == settings.TERMINOS_VERSION
                    )
                    version_terminos_pendiente = (
                        perfil.acepto_terminos
                        and perfil.terminos_version != settings.TERMINOS_VERSION
                    )
                    logger.info(f"Perfil encontrado para {username}: grupo={perfil.grupo_ldap}, tiene_acceso={perfil.tiene_acceso}")
                    if not perfil.tiene_acceso:
                        error = 'Tu usuario no tiene permisos para acceder a este sistema.'
                        logger.warning(
                            f"Usuario {username} autenticado pero sin grupo válido: {perfil.grupo_ldap}"
                        )
                        return render(
                            request,
                            'registration/login.html',
                            _ctx_login({'error': error}),
                        )
                except PerfilUsuario.DoesNotExist:
                    logger.info(f"Usuario {username} sin perfil LDAP, permitiendo acceso")

                # Validar aceptación de términos (server-side)
                if terms != 'on':
                    if version_terminos_pendiente:
                        error = (
                            'Se actualizaron los Términos y Condiciones '
                            f'(versión {settings.TERMINOS_VERSION}). '
                            'Debes aceptarlos para continuar.'
                        )
                    else:
                        error = 'Debes aceptar los Términos y Condiciones para continuar.'
                    return render(
                        request,
                        'registration/login.html',
                        _ctx_login({
                            'error': error,
                            'terminos_aceptados': terminos_aceptados,
                        }),
                    )

                # Login exitoso
                login(request, user)

                # Guardar aceptación de términos en el perfil, registrando la
                # versión aceptada (auditoría de consentimiento).
                try:
                    perfil = user.perfil
                    if (not perfil.acepto_terminos
                            or perfil.terminos_version != settings.TERMINOS_VERSION):
                        perfil.acepto_terminos = True
                        perfil.fecha_acepto_terminos = timezone.now()
                        perfil.terminos_version = settings.TERMINOS_VERSION
                        perfil.save(update_fields=[
                            'acepto_terminos',
                            'fecha_acepto_terminos',
                            'terminos_version',
                        ])
                except PerfilUsuario.DoesNotExist:
                    pass

                if not remember:
                    request.session.set_expiry(0)

                logger.info(f"Login exitoso: {username}")
                return redirect('autenticacion:dashboard')
            else:
                error = MENSAJE_CREDENCIALES
                _cache_set_seguro(throttle_key, intentos + 1, LOGIN_BLOQUEO_SEGUNDOS)
                logger.warning(f"Login fallido: {username} (intento {intentos + 1})")

    return render(request, 'registration/login.html', _ctx_login({'error': error}))


@require_POST
def logout_view(request):
    """
    Cierra la sesión del usuario y redirige al login.

    Solo acepta POST: un logout por GET permitiría logout-CSRF (cerrar la
    sesión de un usuario con solo cargar una imagen/enlace).
    """
    username = request.user.username if request.user.is_authenticated else 'desconocido'
    logout(request)
    logger.info(f"Logout: {username}")
    return redirect('autenticacion:login')


def _requiere_aviso_cedula(user, perfil) -> bool:
    """True si debe mostrarse el recordatorio de cédula faltante al usuario.

    Exime solo a cuentas técnicas sin perfil o superusuarios de Django.
    Los administradores de POLIDOC no son bloqueados por el middleware,
    pero sí ven el recordatorio no bloqueante hasta registrar su cédula.
    """
    if perfil is None or user.is_superuser:
        return False
    return not (perfil.cedula_identidad or '').strip()


@login_required(login_url='/login/')
def dashboard_view(request):
    """Vista para el panel principal con datos reales"""
    user = request.user
    perfil = getattr(user, 'perfil', None)

    mis_documentos_qs = Documento.objects.filter(propietario=user)
    total_mis_documentos = mis_documentos_qs.count()
    total_activos = mis_documentos_qs.filter(estado='activo').count()
    total_borradores = mis_documentos_qs.filter(estado='borrador').count()

    # Solicitudes de descarga pendientes (para jefes, delegados y admins)
    solicitudes_pendientes = 0
    if perfil and (perfil.ejerce_jefatura or perfil.es_administrador) and perfil.departamento:
        solicitudes_pendientes = SolicitudDescarga.objects.filter(
            estado='pendiente',
            documento__departamento=perfil.departamento
        ).count()

    notificaciones_no_leidas = Notificacion.objects.filter(usuario=user, leida=False).count()

    # Panel de vigencia del departamento (jefes y delegados; el mismo cálculo
    # que la página de Documentos por Departamento, reutilizado desde services).
    conteos_vigencia = None
    if perfil and perfil.ejerce_jefatura and perfil.departamento:
        conteos_vigencia = calcular_conteos_vigencia([perfil.departamento], '', user)

    ultimos_documentos = mis_documentos_qs.select_related('departamento').order_by('-fecha_modificacion')[:10]
    ultimas_versiones = VersionDocumento.objects.filter(creado_por=user).select_related('documento')[:5]
    logs_recientes = obtener_logs(usuario=user.username, limite=5)

    context = {
        'total_mis_documentos': total_mis_documentos,
        'total_activos': total_activos,
        'total_borradores': total_borradores,
        'solicitudes_pendientes': solicitudes_pendientes,
        'notificaciones_no_leidas': notificaciones_no_leidas,
        'conteos_vigencia': conteos_vigencia,
        'ultimos_documentos': ultimos_documentos,
        'ultimas_versiones': ultimas_versiones,
        'logs_recientes': logs_recientes,
        'sin_cedula': _requiere_aviso_cedula(user, perfil),
    }

    return render(request, 'dashboard.html', context)


@login_required(login_url='/login/')
def documento_lista_view(request):
    """Vista para la lista de documentos (requiere autenticación)."""
    return render(request, 'documentos/lista.html')



def _nombres_distintos(user, datos):
    """True si RRHH trae un nombre/apellido distinto al de la cuenta."""
    def _norm(valor):
        return (valor or '').strip().lower()

    nombre = _norm(datos.get('nombre'))
    apellido = _norm(datos.get('apellido'))
    return bool(
        (_norm(user.first_name) and _norm(user.first_name) != nombre)
        or (_norm(user.last_name) and _norm(user.last_name) != apellido)
    )


def _auditar_cedula(request, perfil, anterior, nuevo, origen, mensaje=''):
    """Audita el cambio de cédula sin guardar PII completa (ISO 15489)."""
    try:
        from auditoria.services import registrar_log_iso

        from .services.rrhh_sync import enmascarar_cedula

        registrar_log_iso(
            usuario=request.user,
            accion_tipo='edicion',
            accion_categoria='usuario',
            recurso_tipo='usuario',
            recurso_id=perfil.pk,
            recurso_nombre=perfil.usuario.username,
            estado_anterior=enmascarar_cedula(anterior),
            estado_posterior=enmascarar_cedula(nuevo),
            detalles={
                'campo': 'cedula_identidad',
                'origen': origen,
                'cargo': perfil.cargo,
                'departamento_rrhh': perfil.departamento_rrhh,
                'cedula_verificada': perfil.cedula_verificada_rrhh,
            },
            resultado_mensaje=mensaje or 'Cédula actualizada',
        )
    except Exception:  # noqa: BLE001 - la auditoría no rompe el flujo
        logger.warning('No se pudo auditar el cambio de cédula', exc_info=True)


def _auditar_consulta_cedula(request, cedula, encontrado):
    """Registra el acceso a datos de RRHH por cédula (trazabilidad de PII)."""
    try:
        from auditoria.services import registrar_log_iso

        from .services.rrhh_sync import enmascarar_cedula

        registrar_log_iso(
            usuario=request.user,
            accion_tipo='consulta',
            accion_categoria='usuario',
            recurso_tipo='usuario',
            recurso_nombre=request.user.username,
            detalles={
                'consulta': 'cedula_rrhh',
                'cedula_ultimos4': enmascarar_cedula(cedula)[-4:],
                'encontrado': encontrado,
            },
            resultado_mensaje=(
                'Consulta de cédula en RRHH: encontrada' if encontrado
                else 'Consulta de cédula en RRHH: no encontrada'
            ),
        )
    except Exception:  # noqa: BLE001
        logger.warning('No se pudo auditar la consulta de cédula', exc_info=True)


def _guardar_cedula(request, perfil, cedula, datos, verificado):
    """Guarda la cédula y, si hay datos, cargo/departamento/nombres."""
    anterior = perfil.cedula_identidad
    perfil.cedula_identidad = cedula
    perfil.cedula_verificada_rrhh = bool(verificado)
    if datos:
        perfil.cargo = datos.get('cargo', '')
        perfil.departamento_rrhh = datos.get('departamento', '')
        perfil.ultimo_sync_rrhh = timezone.now()
        cambios = []
        if datos.get('nombre'):
            request.user.first_name = datos['nombre']
            cambios.append('first_name')
        if datos.get('apellido'):
            request.user.last_name = datos['apellido']
            cambios.append('last_name')
        if cambios:
            request.user.save(update_fields=cambios)
    perfil.save(update_fields=[
        'cedula_identidad', 'cedula_verificada_rrhh', 'cargo',
        'departamento_rrhh', 'ultimo_sync_rrhh',
    ])
    _auditar_cedula(request, perfil, anterior, cedula, 'autoservicio')


def _confirmar_cedula(request, perfil):
    """Paso 2: confirma la cédula pendiente guardada en sesión."""
    from django.contrib import messages

    from .services.rrhh_sync import validar_y_limpiar_cedula

    pendiente = request.session.pop('cedula_pendiente_rrhh', None)
    if not pendiente:
        messages.error(
            request,
            'La verificación expiró. Vuelve a ingresar tu cédula.',
        )
        return redirect('autenticacion:perfil')

    try:
        cedula = validar_y_limpiar_cedula(pendiente.get('cedula', ''))
    except ValueError:
        messages.error(request, 'La cédula de la verificación no es válida.')
        return redirect('autenticacion:perfil')

    if (
        PerfilUsuario.objects
        .filter(cedula_identidad=cedula)
        .exclude(pk=perfil.pk)
        .exists()
    ):
        messages.error(
            request, 'Esa cédula ya está registrada por otro usuario.',
        )
        return redirect('autenticacion:perfil')

    _guardar_cedula(
        request, perfil, cedula,
        {
            'nombre': pendiente.get('nombre', ''),
            'apellido': pendiente.get('apellido', ''),
            'cargo': pendiente.get('cargo', ''),
            'departamento': pendiente.get('departamento', ''),
        },
        verificado=True,
    )
    messages.success(
        request, 'Cédula verificada y guardada correctamente.',
    )
    return redirect('autenticacion:perfil')


@login_required(login_url='/login/')
def perfil_usuario(request):
    """Ver y editar perfil del usuario, incluida la cédula verificada en RRHH."""
    from django.contrib import messages

    from .forms import CedulaForm, EditarPerfilForm
    from .services.rrhh_sync import (
        RRHHConexionError, consultar_empleado_por_cedula, rrhh_habilitado,
    )

    perfil, _ = PerfilUsuario.objects.get_or_create(
        usuario=request.user,
        defaults={
            'rol': 'administrador' if request.user.is_superuser else 'trabajador',
        },
    )
    form = EditarPerfilForm(instance=perfil)
    cedula_form = CedulaForm(instance=perfil)
    cedula_pendiente = request.session.get('cedula_pendiente_rrhh')

    if request.method == 'POST':
        accion = request.POST.get('accion', '')

        if accion == 'cancelar_cedula':
            request.session.pop('cedula_pendiente_rrhh', None)
            messages.info(request, 'Verificación de cédula cancelada.')
            return redirect('autenticacion:perfil')

        if accion == 'confirmar_cedula':
            return _confirmar_cedula(request, perfil)

        if accion == 'buscar_cedula':
            cedula_form = CedulaForm(request.POST, instance=perfil)
            if cedula_form.fields['cedula_identidad'].disabled:
                messages.info(
                    request,
                    'Tu cédula ya está verificada; solo un administrador '
                    'puede modificarla.',
                )
                return redirect('autenticacion:perfil')
            request.session.pop('cedula_pendiente_rrhh', None)
            cedula_pendiente = None
            if cedula_form.is_valid():
                cedula = cedula_form.cleaned_data['cedula_identidad']
                if not rrhh_habilitado():
                    _guardar_cedula(
                        request, perfil, cedula, {}, verificado=False,
                    )
                    messages.success(
                        request,
                        'Cédula registrada (RRHH no configurado).',
                    )
                    return redirect('autenticacion:perfil')
                try:
                    datos = consultar_empleado_por_cedula(cedula)
                except RRHHConexionError:
                    _guardar_cedula(
                        request, perfil, cedula, {}, verificado=False,
                    )
                    messages.warning(
                        request,
                        'Cédula guardada. No se pudo validar contra RRHH en '
                        'este momento; se verificará automáticamente luego.',
                    )
                    return redirect('autenticacion:perfil')

                if datos is None:
                    _auditar_consulta_cedula(
                        request, cedula, encontrado=False,
                    )
                    cedula_form.add_error(
                        'cedula_identidad',
                        'No encontramos esa cédula en RRHH. '
                        'Verifica los dígitos.',
                    )
                else:
                    _auditar_consulta_cedula(
                        request, cedula, encontrado=True,
                    )
                    cedula_pendiente = {
                        'cedula': cedula,
                        'nombre': datos['nombre'],
                        'apellido': datos['apellido'],
                        'cargo': datos['cargo'],
                        'departamento': datos['departamento'],
                        'alerta': _nombres_distintos(request.user, datos),
                    }
                    request.session['cedula_pendiente_rrhh'] = (
                        cedula_pendiente
                    )
        else:
            form = EditarPerfilForm(
                request.POST, request.FILES, instance=perfil,
            )
            if form.is_valid():
                if 'foto_perfil' in request.FILES:
                    foto_anterior = perfil.foto_perfil
                    if (
                        foto_anterior
                        and foto_anterior.storage.exists(foto_anterior.name)
                    ):
                        foto_anterior.delete(save=False)

                form.save()
                request.user.email = form.cleaned_data.get('email') or ''
                request.user.save(update_fields=['email'])
                messages.success(
                    request, 'Perfil actualizado correctamente.',
                )
                return redirect('autenticacion:perfil')

    total_docs = Documento.objects.filter(propietario=request.user).count()

    solicitudes_qs = (
        SolicitudDescarga.objects
        .filter(solicitado_por=request.user)
        .select_related('documento', 'documento__departamento')
        .prefetch_related('aprobaciones__aprobador')
    )
    solicitudes_descarga = solicitudes_qs.count()
    # Resumen "Mis solicitudes de descarga": el listado completo vive en
    # documentos:mis_solicitudes, aquí solo se adelantan las 3 más recientes.
    mis_solicitudes_descarga = list(solicitudes_qs[:3])

    asignaciones = AsignacionDocumento.objects.filter(
        coordinador=request.user,
        activa=True
    ).count()

    from documentos.services.firmas import obtener_credencial

    return render(request, 'autenticacion/perfil.html', {
        'form': form,
        'cedula_form': cedula_form,
        'cedula_pendiente': cedula_pendiente,
        'cedula_bloqueada': cedula_form.fields['cedula_identidad'].disabled,
        'perfil': perfil,
        'cargo_mostrado': perfil.cargo_efectivo,
        'cargo_es_rrhh': bool(perfil.cargo_rrhh),
        'sin_cedula': _requiere_aviso_cedula(request.user, perfil),
        'credencial': obtener_credencial(request.user),
        'total_documentos': total_docs,
        'solicitudes_descarga': solicitudes_descarga,
        'mis_solicitudes_descarga': mis_solicitudes_descarga,
        'mas_solicitudes_descarga': solicitudes_descarga > len(mis_solicitudes_descarga),
        'asignaciones': asignaciones,
    })


@login_required(login_url='/login/')
def firmar_pin_perfil(request):
    """Crea o cambia el PIN local de firma electrónica (SES)."""
    from django.contrib import messages

    from documentos.services import firmas

    if request.method != 'POST':
        return redirect('autenticacion:perfil')
    try:
        firmas.establecer_pin(
            request.user,
            request.POST.get('pin', ''),
            request.POST.get('pin2', ''),
        )
        messages.success(request, 'PIN de firma guardado correctamente.')
    except firmas.ErrorFirma as exc:
        messages.error(request, str(exc))
    return redirect('autenticacion:perfil')


@login_required(login_url='/login/')
def eliminar_foto_perfil(request):
    """Eliminar foto de perfil del usuario"""
    from django.http import JsonResponse

    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)

    perfil = request.user.perfil
    if perfil.foto_perfil:
        # Eliminar archivo del disco
        if perfil.foto_perfil.storage.exists(perfil.foto_perfil.name):
            perfil.foto_perfil.delete(save=False)
        perfil.foto_perfil = None
        perfil.save(update_fields=['foto_perfil'])

    return JsonResponse({'ok': True})


def _es_admin_usuarios(user) -> bool:
    """True si el usuario puede administrar usuarios (admin POLIDOC o superuser)."""
    if user.is_superuser:
        return True
    perfil = getattr(user, 'perfil', None)
    return bool(perfil and perfil.es_administrador)


@login_required(login_url='/login/')
def gestionar_usuarios(request):
    """Gestión de usuarios — solo Administradores"""
    if not _es_admin_usuarios(request.user):
        from django.contrib import messages
        messages.error(request, 'No tienes permisos para acceder a esta sección.')
        return redirect('autenticacion:dashboard')

    buscar = request.GET.get('buscar', '').strip()
    filtro_estado = request.GET.get('estado', '')
    filtro_depto = request.GET.get('departamento', '')

    usuarios = User.objects.select_related('perfil', 'perfil__departamento').all()

    if buscar:
        usuarios = usuarios.filter(
            Q(username__icontains=buscar) |
            Q(first_name__icontains=buscar) |
            Q(last_name__icontains=buscar) |
            Q(email__icontains=buscar) |
            Q(perfil__cedula_identidad__icontains=buscar) |
            Q(perfil__cargo__icontains=buscar)
        )

    if filtro_estado == 'sin_dept':
        usuarios = usuarios.filter(perfil__departamento__isnull=True)
    elif filtro_estado == 'con_dept':
        usuarios = usuarios.filter(perfil__departamento__isnull=False)
    elif filtro_estado == 'sin_cedula':
        usuarios = usuarios.filter(perfil__cedula_identidad='')
    elif filtro_estado == 'verificada_rrhh':
        usuarios = usuarios.filter(perfil__cedula_verificada_rrhh=True)

    if filtro_depto:
        usuarios = usuarios.filter(perfil__departamento__id=filtro_depto)

    paginator = Paginator(usuarios, 20)
    page = request.GET.get('page', 1)
    usuarios_page = paginator.get_page(page)

    departamentos = Departamento.objects.filter(activo=True).order_by('nombre')

    total_usuarios = User.objects.count()
    sin_departamento = PerfilUsuario.objects.filter(departamento__isnull=True).count()
    sin_cedula_count = PerfilUsuario.objects.filter(cedula_identidad='').count()

    return render(request, 'autenticacion/gestionar_usuarios.html', {
        'usuarios': usuarios_page,
        'departamentos': departamentos,
        'buscar': buscar,
        'filtro_estado': filtro_estado,
        'filtro_depto': filtro_depto,
        'total_usuarios': total_usuarios,
        'sin_departamento': sin_departamento,
        'sin_cedula_count': sin_cedula_count,
    })


def _parse_json_usuario_admin(request, *, require_perfil: bool = True):
    """Valida POST, permisos de administración, JSON y resuelve usuario/perfil."""
    if request.method != 'POST':
        return None, JsonResponse({'error': 'Método no permitido'}, status=405)

    if not _es_admin_usuarios(request.user):
        return None, JsonResponse({'error': 'Sin permisos'}, status=403)

    try:
        data = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return None, JsonResponse({'error': 'JSON inválido'}, status=400)

    if not isinstance(data, dict):
        return None, JsonResponse({'error': 'JSON inválido'}, status=400)

    raw_id = data.get('usuario_id')
    if isinstance(raw_id, bool) or raw_id in (None, ''):
        return None, JsonResponse({'error': 'Faltan campos requeridos'}, status=400)
    try:
        usuario_id = int(raw_id)
    except (TypeError, ValueError):
        return None, JsonResponse({'error': 'usuario_id inválido'}, status=400)

    usuario = User.objects.select_related('perfil').filter(id=usuario_id).first()
    perfil_obj = getattr(usuario, 'perfil', None) if usuario else None
    if usuario is None or (require_perfil and perfil_obj is None):
        return None, JsonResponse({'error': 'Usuario no encontrado'}, status=404)

    return (data, usuario, perfil_obj), None


@login_required(login_url='/login/')
def asignar_departamento_usuario(request):
    """API: Asignar departamento a un usuario (solo Admin)"""
    parsed, error_resp = _parse_json_usuario_admin(request)
    if error_resp is not None:
        return error_resp
    data, usuario, perfil_usuario = parsed
    departamento_id = data.get('departamento_id')

    if departamento_id:
        try:
            dept = Departamento.objects.get(id=departamento_id)
        except (Departamento.DoesNotExist, TypeError, ValueError):
            return JsonResponse({'error': 'Departamento no encontrado'}, status=404)
        perfil_usuario.departamento = dept
        perfil_usuario.departamento_texto = dept.nombre
        perfil_usuario.save(update_fields=['departamento', 'departamento_texto'])
        return JsonResponse({
            'success': True,
            'mensaje': f'Departamento "{dept.nombre}" asignado a {usuario.get_full_name() or usuario.username}'
        })
    else:
        perfil_usuario.departamento = None
        perfil_usuario.departamento_texto = ''
        perfil_usuario.save(update_fields=['departamento', 'departamento_texto'])
        return JsonResponse({
            'success': True,
            'mensaje': f'Departamento removido a {usuario.get_full_name() or usuario.username}'
        })


@login_required(login_url='/login/')
def desvincular_rrhh_usuario(request):
    """API: Limpia cédula, cargo y verificación RRHH de un usuario (solo Admin)."""
    parsed, error_resp = _parse_json_usuario_admin(request)
    if error_resp is not None:
        return error_resp
    data, usuario, perfil_obj = parsed
    limpiar_nombres = bool(data.get('limpiar_nombres', False))

    from .services.rrhh_sync import desvincular_datos_rrhh

    res = desvincular_datos_rrhh(
        perfil_obj,
        actor=request.user,
        origen='gestion_usuarios',
        limpiar_nombres=limpiar_nombres,
    )
    if usuario.pk == request.user.pk:
        request.session.pop('cedula_pendiente_rrhh', None)

    if not res['modificado']:
        return JsonResponse({
            'success': True,
            'sin_cambios': True,
            'mensaje': 'El usuario no tenía datos de RRHH vinculados.',
        })

    return JsonResponse({
        'success': True,
        'sin_cambios': False,
        'mensaje': f'Datos de RRHH desvinculados para {usuario.username}.',
    })


@login_required(login_url='/login/')
def vincular_rrhh_usuario(request):
    """API: Valida y vincula cédula/datos RRHH de un usuario desde detalle (solo Admin)."""
    parsed, error_resp = _parse_json_usuario_admin(request)
    if error_resp is not None:
        return error_resp
    data, usuario, perfil_obj = parsed

    from .services.rrhh_sync import (
        RRHHConexionError,
        consultar_empleado_por_cedula,
        rrhh_habilitado,
        validar_y_limpiar_cedula,
        vincular_datos_rrhh,
    )

    try:
        cedula = validar_y_limpiar_cedula(data.get('cedula', ''))
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    if (
        PerfilUsuario.objects
        .filter(cedula_identidad=cedula)
        .exclude(pk=perfil_obj.pk)
        .exists()
    ):
        return JsonResponse(
            {'error': 'Esa cédula ya está registrada por otro usuario.'},
            status=400,
        )

    confirmar = bool(data.get('confirmar', False))
    if not rrhh_habilitado():
        vincular_datos_rrhh(
            perfil_obj,
            cedula=cedula,
            datos=None,
            verificado=False,
            actor=request.user,
            origen='detalle_usuario',
        )
        return JsonResponse({
            'success': True,
            'guardado': True,
            'mensaje': 'Cédula registrada (RRHH no configurado).',
        })

    try:
        datos = consultar_empleado_por_cedula(cedula)
    except RRHHConexionError:
        return JsonResponse(
            {'error': 'No se pudo conectar con RRHH en este momento. Intenta más tarde.'},
            status=503,
        )

    if datos is None:
        _auditar_consulta_cedula(request, cedula, encontrado=False)
        return JsonResponse(
            {'error': 'No encontramos esa cédula activa en RRHH.'},
            status=404,
        )

    if not confirmar:
        _auditar_consulta_cedula(request, cedula, encontrado=True)
        return JsonResponse({
            'success': True,
            'guardado': False,
            'preview': {
                'cedula': cedula,
                'nombre': datos['nombre'],
                'apellido': datos['apellido'],
                'cargo': datos['cargo'],
                'departamento': datos['departamento'],
            },
        })

    vincular_datos_rrhh(
        perfil_obj,
        cedula=cedula,
        datos=datos,
        verificado=True,
        actor=request.user,
        origen='detalle_usuario',
    )
    return JsonResponse({
        'success': True,
        'guardado': True,
        'mensaje': f'Cédula y datos de RRHH vinculados para {usuario.username}.',
    })


@login_required(login_url='/login/')
def toggle_activo_usuario(request):
    """API: Activa o desactiva la cuenta de un usuario (solo Admin)."""
    parsed, error_resp = _parse_json_usuario_admin(request, require_perfil=False)
    if error_resp is not None:
        return error_resp
    _, usuario, _ = parsed

    if usuario.pk == request.user.pk:
        return JsonResponse(
            {'error': 'No puedes cambiar el estado de tu propia cuenta.'},
            status=400,
        )
    if usuario.is_superuser:
        return JsonResponse(
            {'error': 'No se puede cambiar el estado de un superusuario desde este panel.'},
            status=403,
        )

    estado_anterior = bool(usuario.is_active)
    nuevo_estado = not estado_anterior
    User.objects.filter(pk=usuario.pk).update(is_active=nuevo_estado)

    try:
        from auditoria.services import registrar_log_iso

        registrar_log_iso(
            usuario=request.user,
            accion_tipo='edicion',
            accion_categoria='usuario',
            recurso_tipo='usuario',
            recurso_id=usuario.pk,
            recurso_nombre=usuario.username,
            estado_anterior='activo' if estado_anterior else 'inactivo',
            estado_posterior='activo' if nuevo_estado else 'inactivo',
            detalles={'campo': 'is_active', 'origen': 'detalle_usuario'},
            resultado_mensaje=(
                'Cuenta activada por administrador' if nuevo_estado
                else 'Cuenta desactivada por administrador'
            ),
        )
    except Exception:  # noqa: BLE001 - la auditoría no rompe la gestión admin
        logger.warning(
            'No se pudo auditar el cambio de estado de %s',
            usuario.username,
            exc_info=True,
        )

    return JsonResponse({
        'success': True,
        'is_active': nuevo_estado,
        'mensaje': (
            f'Cuenta de {usuario.username} activada.' if nuevo_estado
            else f'Cuenta de {usuario.username} desactivada.'
        ),
    })


@login_required(login_url='/login/')
def ver_usuario(request, usuario_id):
    """Vista de detalle de un usuario para administración (solo Admin)"""
    if not _es_admin_usuarios(request.user):
        return render(request, '403.html', status=403)

    from .services.rrhh_sync import rrhh_habilitado, tiene_datos_rrhh

    usuario = User.objects.select_related('perfil', 'perfil__departamento').get(id=usuario_id)
    perfil_usuario = getattr(usuario, 'perfil', None)

    total_documentos = Documento.objects.filter(propietario=usuario).count()
    solicitudes_descarga = SolicitudDescarga.objects.filter(solicitado_por=usuario).count()
    asignaciones_activas = AsignacionDocumento.objects.filter(coordinador=usuario, activa=True).count()
    departamentos = Departamento.objects.filter(activo=True).order_by('nombre')

    return render(request, 'autenticacion/ver_usuario.html', {
        'usuario': usuario,
        'perfil': perfil_usuario,
        'tiene_datos_rrhh': tiene_datos_rrhh(perfil_usuario),
        'cargo_mostrado': perfil_usuario.cargo_efectivo if perfil_usuario else '',
        'cargo_es_rrhh': bool(perfil_usuario.cargo_rrhh) if perfil_usuario else False,
        'departamentos': departamentos,
        'puede_toggle_activo': usuario.pk != request.user.pk and not usuario.is_superuser,
        'puede_transferir': total_documentos > 0 or asignaciones_activas > 0,
        'rrhh_configurado': rrhh_habilitado(),
        'total_documentos': total_documentos,
        'solicitudes_descarga': solicitudes_descarga,
        'asignaciones_activas': asignaciones_activas,
    })
