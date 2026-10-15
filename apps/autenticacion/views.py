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
LOGIN_MAX_INTENTOS = 5
LOGIN_BLOQUEO_SEGUNDOS = 300
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


@login_required(login_url='/login/')
def dashboard_view(request):
    """Vista para el panel principal con datos reales"""
    user = request.user
    perfil = user.perfil

    mis_documentos_qs = Documento.objects.filter(propietario=user)
    total_mis_documentos = mis_documentos_qs.count()
    total_activos = mis_documentos_qs.filter(estado='activo').count()
    total_borradores = mis_documentos_qs.filter(estado='borrador').count()

    # Solicitudes de descarga pendientes (para jefes, delegados y admins)
    solicitudes_pendientes = 0
    if (perfil.ejerce_jefatura or perfil.es_administrador) and perfil.departamento:
        solicitudes_pendientes = SolicitudDescarga.objects.filter(
            estado='pendiente',
            documento__departamento=perfil.departamento
        ).count()

    notificaciones_no_leidas = Notificacion.objects.filter(usuario=user, leida=False).count()

    # Panel de vigencia del departamento (jefes y delegados; el mismo cálculo
    # que la página de Documentos por Departamento, reutilizado desde services).
    conteos_vigencia = None
    if perfil.ejerce_jefatura and perfil.departamento:
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
    }

    return render(request, 'dashboard.html', context)


@login_required(login_url='/login/')
def documento_lista_view(request):
    """Vista para la lista de documentos (requiere autenticación)."""
    return render(request, 'documentos/lista.html')



@login_required(login_url='/login/')
def perfil_usuario(request):
    """Ver y editar perfil del usuario"""
    from django.contrib import messages
    from .forms import EditarPerfilForm

    perfil = request.user.perfil

    if request.method == 'POST':
        form = EditarPerfilForm(request.POST, request.FILES, instance=perfil)
        if form.is_valid():
            # Eliminar foto anterior si se sube una nueva
            if 'foto_perfil' in request.FILES:
                foto_anterior = perfil.foto_perfil
                if foto_anterior and foto_anterior.storage.exists(foto_anterior.name):
                    foto_anterior.delete(save=False)

            form.save()
            request.user.email = form.cleaned_data.get('email') or ''
            request.user.save(update_fields=['email'])
            messages.success(request, 'Perfil actualizado correctamente.')
            return redirect('autenticacion:perfil')
    else:
        form = EditarPerfilForm(instance=perfil)

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

    return render(request, 'autenticacion/perfil.html', {
        'form': form,
        'perfil': perfil,
        'total_documentos': total_docs,
        'solicitudes_descarga': solicitudes_descarga,
        'mis_solicitudes_descarga': mis_solicitudes_descarga,
        'mas_solicitudes_descarga': solicitudes_descarga > len(mis_solicitudes_descarga),
        'asignaciones': asignaciones,
    })


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


@login_required(login_url='/login/')
def gestionar_usuarios(request):
    """Gestión de usuarios — solo Administradores"""
    perfil = request.user.perfil
    if not perfil.es_administrador:
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
            Q(email__icontains=buscar)
        )

    if filtro_estado == 'sin_dept':
        usuarios = usuarios.filter(perfil__departamento__isnull=True)
    elif filtro_estado == 'con_dept':
        usuarios = usuarios.filter(perfil__departamento__isnull=False)

    if filtro_depto:
        usuarios = usuarios.filter(perfil__departamento__id=filtro_depto)

    paginator = Paginator(usuarios, 20)
    page = request.GET.get('page', 1)
    usuarios_page = paginator.get_page(page)

    departamentos = Departamento.objects.filter(activo=True).order_by('nombre')

    total_usuarios = User.objects.count()
    sin_departamento = PerfilUsuario.objects.filter(departamento__isnull=True).count()

    return render(request, 'autenticacion/gestionar_usuarios.html', {
        'usuarios': usuarios_page,
        'departamentos': departamentos,
        'buscar': buscar,
        'filtro_estado': filtro_estado,
        'filtro_depto': filtro_depto,
        'total_usuarios': total_usuarios,
        'sin_departamento': sin_departamento,
    })


@login_required(login_url='/login/')
def asignar_departamento_usuario(request):
    """API: Asignar departamento a un usuario (solo Admin)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    perfil = request.user.perfil
    if not perfil.es_administrador:
        return JsonResponse({'error': 'Sin permisos'}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    usuario_id = data.get('usuario_id')
    departamento_id = data.get('departamento_id')

    if not usuario_id:
        return JsonResponse({'error': 'Faltan campos requeridos'}, status=400)

    try:
        usuario = User.objects.get(id=usuario_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

    perfil_usuario = usuario.perfil

    if departamento_id:
        try:
            dept = Departamento.objects.get(id=departamento_id)
        except Departamento.DoesNotExist:
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
def ver_usuario(request, usuario_id):
    """Vista de solo lectura para ver detalles de un usuario (solo Admin)"""
    perfil = request.user.perfil
    if not perfil.es_administrador:
        return render(request, '403.html', status=403)

    usuario = User.objects.select_related('perfil', 'perfil__departamento').get(id=usuario_id)
    perfil_usuario = usuario.perfil

    total_documentos = Documento.objects.filter(propietario=usuario).count()
    solicitudes_descarga = SolicitudDescarga.objects.filter(solicitado_por=usuario).count()
    asignaciones_activas = AsignacionDocumento.objects.filter(coordinador=usuario, activa=True).count()

    return render(request, 'autenticacion/ver_usuario.html', {
        'usuario': usuario,
        'perfil': perfil_usuario,
        'total_documentos': total_documentos,
        'solicitudes_descarga': solicitudes_descarga,
        'asignaciones_activas': asignaciones_activas,
    })
