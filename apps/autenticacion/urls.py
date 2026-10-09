from django.urls import path
from . import views
from . import api

app_name = 'autenticacion'

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('inicio/', views.dashboard_view, name='dashboard'),
    path('perfil/', views.perfil_usuario, name='perfil'),
    path('perfil/eliminar-foto/', views.eliminar_foto_perfil, name='eliminar_foto_perfil'),

    # Gestión de usuarios (Admin)
    path('usuarios/', views.gestionar_usuarios, name='gestionar_usuarios'),
    path('usuarios/<int:usuario_id>/', views.ver_usuario, name='ver_usuario'),
    path('api/asignar-departamento/', views.asignar_departamento_usuario, name='asignar_departamento_usuario'),

    # API Notificaciones
    path('api/notificaciones/', api.api_notificaciones, name='api_notificaciones'),
    path('api/notificaciones/<int:notificacion_id>/leer/', api.api_marcar_leida, name='api_marcar_leida'),
    path('api/notificaciones/leer-todas/', api.api_marcar_todas_leidas, name='api_marcar_todas_leidas'),
]
