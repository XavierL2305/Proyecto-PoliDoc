import logging
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.db.models.signals import post_save, pre_save
from django.contrib.auth.models import User

from auditoria.services import registrar_login, registrar_logout
from auditoria.middleware import get_client_ip
from .models import PerfilUsuario
from documentos.models import Departamento

logger = logging.getLogger(__name__)


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    """Signal para registrar login de usuario"""
    try:
        # Obtener IP: primero del middleware, luego del request como fallback
        ip = get_client_ip()
        if not ip and request:
            x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded:
                ip = x_forwarded.split(',')[0].strip()
            else:
                ip = request.META.get('REMOTE_ADDR')
        
        registrar_login(
            usuario=user,
            ip_address=ip,
            user_agent=request.META.get('HTTP_USER_AGENT') if request else None
        )
        logger.debug(f"Signal login: {user.username} | IP: {ip}")
    except Exception as e:
        logger.error(f"Error en signal login para {user.username}: {e}")


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    """Signal para registrar logout de usuario"""
    try:
        # Obtener IP: primero del middleware, luego del request como fallback
        ip = get_client_ip()
        if not ip and request:
            x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded:
                ip = x_forwarded.split(',')[0].strip()
            else:
                ip = request.META.get('REMOTE_ADDR')
        
        registrar_logout(
            usuario=user,
            ip_address=ip
        )
        logger.debug(f"Signal logout: {user.username} | IP: {ip}")
    except Exception as e:
        logger.error(f"Error en signal logout para {user.username}: {e}")


@receiver(post_save, sender=User)
def sync_departamento_jefe(sender, instance, **kwargs):
    """
    Cuando se guarda un usuario, sincronizar Departamento.jefe
    con su PerfilUsuario.rol.
    """
    try:
        perfil = instance.perfil
    except Exception:
        return  # No tiene perfil

    dept = perfil.departamento
    if dept is None:
        return  # No tiene departamento

    if perfil.rol == 'jefe_departamento':
        # Este usuario es jefe → asignarlo como jefe del departamento
        if dept.jefe != instance:
            dept.jefe = instance
            dept.save(update_fields=['jefe'])
            logger.info(f"Sync: {instance.username} asignado como jefe de {dept.nombre}")
    else:
        # Este usuario ya no es jefe → verificar si era el jefe actual
        if dept.jefe == instance:
            dept.jefe = None
            dept.save(update_fields=['jefe'])
            logger.info(f"Sync: {instance.username} removido como jefe de {dept.nombre}")


@receiver(pre_save, sender=PerfilUsuario)
def capture_old_departamento(sender, instance, **kwargs):
    """Capture the old departamento_id and rol before saving."""
    if instance.pk:
        try:
            old = PerfilUsuario.objects.get(pk=instance.pk)
            instance._old_departamento_id = old.departamento_id
            instance._old_rol = old.rol
        except PerfilUsuario.DoesNotExist:
            instance._old_departamento_id = None
            instance._old_rol = None
    else:
        instance._old_departamento_id = None
        instance._old_rol = None


@receiver(post_save, sender=PerfilUsuario)
def sync_departamento_jefe_from_profile(sender, instance, **kwargs):
    """
    Sincroniza Departamento.jefe con el rol del perfil y gestiona la cadena
    de aprobación cuando cambian los jefes.

    Casos:
      - Sale del depto/rol: si era jefe de un depto, los pasos evaluador
        pendientes se reasignan al jefe restante o quedan en espera. Si era
        jefe de GG, los pasos autorizador vuelven a espera.
      - Entra como jefe: se activan los pasos en espera (evaluador si es de
        depto, autorizador si es de GG).

    Idempotente: en logins normales (_old_rol == rol, depto sin cambio) no hace nada.
    """
    from documentos.services import (
        reasignar_pendientes_departamento,
        reasignar_autorizadores_gg,
        desasignar_autorizadores_gg,
    )

    user = instance.usuario
    dept = instance.departamento
    old_id = getattr(instance, '_old_departamento_id', None)
    old_rol = getattr(instance, '_old_rol', None)
    new_dept_id = dept.id if dept else None

    # --- CASO 1: Sale del depto anterior (cambió depto o dejó de ser jefe) ---
    dejo_de_ser_jefe = (old_rol == 'jefe_departamento' and instance.rol != 'jefe_departamento')
    cambio_depto_anterior = (old_id and old_id != new_dept_id and old_rol == 'jefe_departamento')
    if old_id and (dejo_de_ser_jefe or cambio_depto_anterior):
        try:
            old_dept = Departamento.objects.filter(pk=old_id).select_related('jefe').first()
            if old_dept:
                if old_dept.codigo == 'GG':
                    desasignar_autorizadores_gg(user, user)
                else:
                    destino = old_dept.jefe
                    if destino is None or destino == user:
                        destino = None
                    reasignar_pendientes_departamento(old_dept, destino, user)
                logger.info(f'Signal: {user.username} salió de {old_dept.codigo}')
        except Exception as e:
            logger.error(f'Error gestionando salida de jefe: {e}')

    # --- Sincronizar Departamento.jefe ---
    if old_id and old_id != new_dept_id:
        Departamento.objects.filter(pk=old_id, jefe=user).update(jefe=None)
        logger.info(f'Sync profile: {user.username} removido como jefe del depto anterior (id={old_id})')

    if dept is None:
        return

    if instance.rol == 'jefe_departamento':
        if dept.jefe != user:
            dept.jefe = user
            dept.save(update_fields=['jefe'])
            logger.info(f'Sync profile: {user.username} asignado como jefe de {dept.nombre}')
    else:
        if dept.jefe == user:
            dept.jefe = None
            dept.save(update_fields=['jefe'])
            logger.info(f'Sync profile: {user.username} removido como jefe de {dept.nombre}')

    # --- CASO 2: Entra como jefe del depto actual ---
    entro_a_jefe = (old_rol != 'jefe_departamento')
    cambio_depto = (old_id != new_dept_id)
    if instance.rol == 'jefe_departamento' and (entro_a_jefe or cambio_depto):
        try:
            if dept.codigo == 'GG':
                reasignar_autorizadores_gg(user, user)
            else:
                reasignar_pendientes_departamento(dept, user, user)
            logger.info(
                f'Signal: {user.username} entró como jefe de {dept.codigo}, '
                f'cadena actualizada'
            )
        except Exception as e:
            logger.error(f'Error reasignando al nuevo jefe: {e}')
