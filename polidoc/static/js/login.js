/* ==========================================================================
   POLIDOC - LOGIN
   Feedback de envío y transición al panel en la pantalla de login.

     1) Al enviar con el formulario válido, el botón pasa a estado de carga
        (.btn--loading + aria-busy) y espera DUR.loading antes de navegar.
     2) Activa el overlay #login-transition (mismo token de fondo que el
        dashboard) y desvanece la tarjeta: la llegada se siente continua.
     3) Navega con HTMLFormElement.prototype.submit, que no dispara de nuevo
        el evento submit (sin recursión, como en button-animations.js).

   Notas:
     - La validación de Términos (script inline del template) se registra
       primero; si ya bloqueó el envío (e.defaultPrevented), aquí no se hace
       nada y el usuario ve el error existente.
     - Si el navegador no puede validar los campos requeridos, el evento
       submit nunca se dispara: el comportamiento nativo se mantiene.
     - Si prefers-reduced-motion está activo, el handler no hace nada: submit
       nativo inmediato, sin overlay (misma convención que button-animations.js).
     - Sin JS el login funciona como hoy: la entrada es CSS y el submit es
       nativo; el overlay solo lo usa este script.
     - restaurar() es la única dueña del estado ocupado del botón: si la
       navegación no ocurre (JS error upstream, submit bloqueado), libera
       disabled/aria-busy/.btn--loading y el texto original en DUR.safety.
   ========================================================================== */
(function () {
  'use strict';

  // DUR.loading = espera del estado de carga antes de la transición.
  // DUR.exit    = duración del desvanecimiento (igual que .login-transition).
  // DUR.safety  = tope de seguridad para no dejar el botón colgado.
  var DUR = { loading: 650, exit: 320, safety: 8000 };

  var reduced = false;
  try {
    reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (e) {
    reduced = false;
  }

  document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('login-form');
    if (!form) return;

    var btn = document.getElementById('submit-btn');
    var label = btn ? btn.querySelector('span') : null;
    var textoOriginal = label ? label.textContent : null;

    function restaurar() {
      if (!btn) return;
      btn.classList.remove('btn--loading');
      btn.removeAttribute('aria-busy');
      btn.disabled = false;
      if (label && textoOriginal !== null) label.textContent = textoOriginal;
    }

    form.addEventListener('submit', function (e) {
      // Términos sin aceptar: la validación inline ya bloqueó el envío.
      if (reduced || e.defaultPrevented) return;

      e.preventDefault();
      if (!btn) {
        HTMLFormElement.prototype.submit.call(form);
        return;
      }

      if (label) label.textContent = 'Verificando…';
      btn.disabled = true;
      btn.setAttribute('aria-busy', 'true');
      btn.classList.add('btn--loading');

      var safety = window.setTimeout(restaurar, DUR.safety);

      window.setTimeout(function () {
        var container = document.querySelector('.login-container');
        var overlay = document.getElementById('login-transition');
        if (container) container.classList.add('is-leaving');
        if (overlay) overlay.classList.add('is-active');

        window.setTimeout(function () {
          window.clearTimeout(safety);
          HTMLFormElement.prototype.submit.call(form);
        }, DUR.exit);
      }, DUR.loading);
    });
  });
})();
