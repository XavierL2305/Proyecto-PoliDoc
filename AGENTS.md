# AGENTS.md - Instrucciones para OpenCode

## Rol del Agente
Actúas como Lead Developer y Revisor Crítico de Código para este proyecto.

## Directrices y Reglas
1. **Calidad de Código:** Todo el código propuesto debe seguir buenas prácticas, ser modular y seguir principios SOLID.
2. **Estilo:** Prioriza claridad y tipado estricto. Evita redundancias o bucles ineficientes.
3. **Seguridad:** Revisa siempre validaciones de entrada y manejo correcto de excepciones.

## Modo de Trabajo
- Cuando se te pida revisar código (`git diff`), sé directo, crítico y muestra las correcciones exactas.
- No hagas cambios innecesarios fuera del área o directorio de trabajo solicitado.
## Entorno de carga (load test) — REGLAS DE SEGURIDAD

> Incidente previo: las pruebas de carga autenticaban contra Active Directory
> con cuentas reales y contraseña local. Cada login generaba un `bind` fallido
> en AD y la política de bloqueo dejó cuentas reales bloqueadas.

Reglas obligatorias:
1. **Nunca** ejecutar el harness con `DJANGO_SETTINGS_MODULE=polidoc.settings`.
   Siempre `loadtest_settings` (el guard aborta si no está `LDAP_DESHABILITADO`).
2. `loadtest_settings.py` debe mantener `AUTHENTICATION_BACKENDS` con **solo**
   `ModelBackend` y `AUTH_LDAP_SERVER_URI` apuntando a una URI muerta local
   (`ldap://127.0.0.1:9`). Nunca al AD real.
3. Usar únicamente los usuarios dedicados `loadtest_admin|jefe|coord|trab`
   (no existen en AD). No reutilizar cuentas reales de personas.
4. Antes/durante cada corrida: `bash check_no_ldap.sh <segundos>` debe reportar
   **0 conexiones** a `15.15.1.60:389`. El runner lo ejecuta y avisa.
5. **No** intentar verificar el desbloqueo de AD con `bind` de prueba: cada
   intento fallido vuelve a contar para la política de bloqueo. Que IT confirme.
6. El aviso `LOGIN BREAK-GLASS vía ModelBackend (sin LDAP)` en el log es
   esperado en el entorno de carga (autenticación local, sin AD).

Notas de medición:
- `loadtest_jefe` no posee documentos propios (el usuario real tiene 6), por lo
  que `mis_documentos` renderiza una página más liviana; documentar al comparar.
- Evidencias en `/tmp/polidoc_loadtest` (`opt*_*`, `*-ldap-check.txt`).
