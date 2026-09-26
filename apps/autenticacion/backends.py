import logging
import ldap
from ldap.filter import escape_filter_chars
import unicodedata
from django.contrib.auth.models import User
from django.conf import settings
from .models import PerfilUsuario
from documentos.models import Departamento, SubArea

logger = logging.getLogger('django_auth_ldap')


class LDAPBackend:
    """
    Backend LDAP personalizado para POLIDOC.

    Flujo:
    1. Conectar al servidor LDAP
    2. Bind con PTH\\usuario + contraseña
    3. Si falla → credenciales incorrectas
    4. Si éxito → buscar datos del usuario y su grupo
    5. Crear/actualizar User + PerfilUsuario en Django

    Grupo AD: Seguridad PoliDoc
    Roles AD → Django:
      - doc_administrador → administrador
      - doc_jefes → jefe_departamento
      - doc_coordinador → coordinador
      - doc_trabajador → trabajador
    """

    # Grupo unico de AD para acceder a POLIDOC
    GRUPO_POLIDOC = 'Seguridad PoliDoc'

    GRUPOS_VALIDOS = [
        'doc_administrador',
        'doc_jefes',
        'doc_coordinador',
        'doc_trabajador',
    ]

    # Mapeo de grupo AD → rol Django
    MAPEO_ROLES = {
        'doc_administrador': 'administrador',
        'doc_jefes': 'jefe_departamento',
        'doc_coordinador': 'coordinador',
        'doc_trabajador': 'trabajador',
    }

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        try:
            # 1. Conectar al servidor LDAP
            conn = ldap.initialize(settings.AUTH_LDAP_SERVER_URI)
            conn.protocol_version = ldap.VERSION3
            conn.set_option(ldap.OPT_REFERRALS, 0)
            # Timeouts: evita que un AD caído o lento deje hilos colgados.
            conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 10)
            conn.set_option(ldap.OPT_TIMEOUT, 10)

            # 2. Bind con las credenciales del usuario (formato PTH\usuario)
            user_dn = f"PTH\\{username}"
            conn.simple_bind_s(user_dn, password)
            logger.debug(f"LDAP bind exitoso para: {username}")

            # 3. Buscar datos del usuario en el LDAP
            base_dn = settings.AUTH_LDAP_USER_DN
            user_filter = f'(&(sAMAccountName={escape_filter_chars(username)}))'
            user_results = conn.search_s(
                base_dn,
                ldap.SCOPE_SUBTREE,
                user_filter,
                ['givenName', 'sn', 'mail', 'department', 'employeeID', 'cn', 'memberOf']
            )

            if not user_results:
                logger.warning(f"Usuario {username} no encontrado en LDAP")
                conn.unbind_s()
                return None

            # Extraer atributos del usuario
            user_entry = user_results[0]
            user_dn_full = user_entry[0]
            user_attrs = user_entry[1]

            # Verificar que user_attrs sea un dict
            if not isinstance(user_attrs, dict):
                logger.error(f"user_attrs no es dict: {type(user_attrs)} - {user_attrs}")
                conn.unbind_s()
                return None

            first_name = self._extract_attr(user_attrs, 'givenName')
            last_name = self._extract_attr(user_attrs, 'sn')
            email = self._extract_attr(user_attrs, 'mail')
            departamento = self._extract_attr(user_attrs, 'department')
            employee_id = self._extract_attr(user_attrs, 'employeeID')

            # 4. Detectar grupo del usuario
            grupo = self._detect_grupo(conn, base_dn, username, user_attrs, user_dn_full)
            conn.unbind_s()

            # 5. Mapear grupo AD a rol Django
            rol = self.MAPEO_ROLES.get(grupo, 'trabajador')

            # 6. Crear o actualizar usuario en Django
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'email': email,
                }
            )

            if not created:
                user.first_name = first_name
                user.last_name = last_name
                if email:
                    user.email = email
                user.save()

            # 7. Crear o actualizar perfil
            # Buscar departamento con matching normalizado (tildes + case)
            dept_instance = self._match_departamento(departamento)

            perfil, perfil_created = PerfilUsuario.objects.get_or_create(usuario=user)

            # Solo sobrescribir departamento si es nuevo O si AD trae uno válido
            if perfil_created or dept_instance:
                perfil.departamento = dept_instance

            perfil.grupo_ldap = grupo
            perfil.rol = rol
            perfil.departamento_texto = departamento
            perfil.numero_empleado = employee_id
            perfil.save()

            accion = "creado" if created else "logueado"
            logger.info(f"Login exitoso: {username} ({accion}, grupo={grupo}, rol={rol})")

            return user

        except ldap.INVALID_CREDENTIALS:
            logger.debug(f"Credenciales invalidas para: {username}")
            return None
        except ldap.SERVER_DOWN as e:
            logger.error(f"Servidor LDAP no disponible: {e}")
            return None
        except ldap.LDAPError as e:
            logger.error(f"Error LDAP para {username}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error inesperado para {username}: {e}", exc_info=True)
            return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

    def _detect_grupo(self, conn, base_dn, username, user_attrs, user_dn_full=None):
        """
        Detecta el grupo AD del usuario usando dos estrategias:
        1. Buscar en el atributo memberOf del usuario (más rápido)
        2. Buscar en los grupos válidos por member (fallback)
        """
        try:
            # Estrategia 1: Usar memberOf del usuario si existe
            member_of = user_attrs.get('memberOf', [])
            if not isinstance(member_of, list):
                member_of = []

            todos_los_grupos = []
            for dn in member_of:
                if isinstance(dn, bytes):
                    dn = dn.decode('utf-8')
                nombre = self._extraer_cn(dn)
                if nombre:
                    todos_los_grupos.append(nombre)

            # Los grupos completos del usuario son información sensible: se
            # registran solo en DEBUG, nunca en INFO.
            if todos_los_grupos:
                logger.debug(f"Grupos de {username}: {todos_los_grupos}")
            else:
                logger.warning(f"Usuario {username} no tiene atributo memberOf o esta vacio")

            # Buscar si tiene alguno de los grupos validos
            for grupo_nombre in todos_los_grupos:
                if grupo_nombre in self.GRUPOS_VALIDOS:
                    logger.info(f"Grupo valido encontrado: {grupo_nombre}")
                    return grupo_nombre

            # Estrategia 2: Buscar solo en los grupos validos (fallback)
            user_dn_norm = (user_dn_full or '').strip().lower()
            for grupo_nombre in self.GRUPOS_VALIDOS:
                filter_str = f'(&(objectClass=group)(cn={escape_filter_chars(grupo_nombre)}))'
                results = conn.search_s(
                    base_dn,
                    ldap.SCOPE_SUBTREE,
                    filter_str,
                    ['member']
                )

                if not results:
                    continue

                for group_dn, group_attrs in results:
                    if not isinstance(group_attrs, dict):
                        continue

                    members = group_attrs.get('member', [])
                    if not isinstance(members, list):
                        continue

                    for member_dn in members:
                        if isinstance(member_dn, bytes):
                            member_dn = member_dn.decode('utf-8')
                        # Comparación EXACTA del DN: un `in` por substring hacía
                        # que "an" coincidiera con "CN=Ana,...". Solo se acepta
                        # si el DN del miembro es idéntico al DN del usuario.
                        if user_dn_norm and member_dn.strip().lower() == user_dn_norm:
                            logger.info(f"Usuario {username} encontrado en grupo: {grupo_nombre}")
                            return grupo_nombre

            logger.warning(f"Usuario {username} no encontrado en ningun grupo valido")
            return 'sin_grupo'

        except Exception as e:
            logger.error(f"Error detectando grupo: {e}", exc_info=True)
            return 'sin_grupo'

    def _extraer_grupo_de_dn(self, dn):
        """
        Extrae el CN (nombre del grupo) de un DN como:
        CN=acceso_estacionamiento,OU=Grupos,DC=pth,DC=local
        """
        try:
            for part in dn.split(','):
                part = part.strip()
                if part.upper().startswith('CN='):
                    nombre = part[3:]
                    if nombre in self.GRUPOS_VALIDOS:
                        return nombre
            return None
        except Exception:
            return None

    def _extraer_cn(self, dn):
        """Extrae el CN de un DNLDAP."""
        try:
            for part in dn.split(','):
                part = part.strip()
                if part.upper().startswith('CN='):
                    return part[3:]
            return None
        except Exception:
            return None

    def _extract_attr(self, attrs, attr_name):
        """Extraer un atributo LDAP de forma segura."""
        value = attrs.get(attr_name, [b''])[0]
        if isinstance(value, bytes):
            return value.decode('utf-8')
        return value or ''

    @staticmethod
    def _normalize_text(text):
        """Normaliza texto eliminando tildes y convirtiendo a minúsculas."""
        if not text:
            return ''
        nfkd = unicodedata.normalize('NFKD', text)
        return ''.join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()

    def _match_departamento(self, departamento_ldap):
        """
        Busca el departamento en la BD comparando nombres normalizados.
        1. Busca en SubArea (área específica de AD)
        2. Si encuentra → retorna el departamento padre
        3. Si no → busca departamento por nombre (fallback)
        4. Si no → busca por código ISO (3 primeras letras)
        5. Si no → retorna None (sin departamento)
        """
        if not departamento_ldap:
            return None

        dept_normalized = self._normalize_text(departamento_ldap)

        # 1. Buscar en SubArea primero (área específica de AD)
        subareas = SubArea.objects.filter(activo=True).select_related('departamento')
        for sub in subareas:
            if self._normalize_text(sub.nombre) == dept_normalized:
                return sub.departamento

        # 2. Fallback: buscar departamento por nombre normalizado
        for dept in Departamento.objects.filter(activo=True):
            if self._normalize_text(dept.nombre) == dept_normalized:
                return dept

        # 3. Fallback: código ISO (2 o más letras)
        if len(departamento_ldap) >= 2:
            dept = Departamento.objects.filter(
                codigo__iexact=departamento_ldap[:3]
            ).first()
            if dept:
                return dept

        return None
