/* ==========================================================================
   POLIDOC - BUTTON ANIMATIONS
   Intercepts form submissions of buttons marked with data-anim and plays a
   micro-animation BEFORE the real (native) submit, so the page navigation
   that already exists in the backend happens after the animation.

     data-anim="save"    → "sello"      (Guardar Borrador)
     data-anim="send"    → "vuelo"      (Enviar a Revisión)
     data-anim="delete"  → "papelera"   (Confirmar Eliminación)

   Notes:
     - The real submit uses HTMLFormElement.prototype.submit so no submit
       event is fired again (no recursion) and the submitter's name/value
       (e.g. name="accion") is re-injected as a hidden input.
     - DUR is the single source of truth for how long the page waits before
       submitting. It MUST be longer than the keyframes in
       css/components/button-animations.css (each @keyframes says which DUR
       it has to fit into), otherwise the navigation cuts the animation.
     - If prefers-reduced-motion is on, the handler does nothing: normal submit.
     - Without JS everything keeps working (plain buttons).
     - The final feedback after navigation is Django's flash message
       (partials/_messages.html); no toast is shown here on purpose, so we
       do not duplicate it nor show something that dies with the redirect.
     - resetBoton() is the ONLY owner of the button's busy state: if the
       navigation never happens (blocked submit, JS error upstream), it
       releases data-anim-busy/aria-busy/classes so the button is usable
       again instead of staying with pointer-events:none forever.
   ========================================================================== */
(function () {
  'use strict';

  // Deben ser MAYORES o IGUALES a la keyframes asociadas en
  // button-animations.css (ver comentario en cada @keyframes): save
  // 650/830 ms, send 950 ms, delete 1000 ms.
  var DUR = { save: 900, send: 1100, 'delete': 1000 };

  var reduced = false;
  try {
    reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (e) {
    reduced = false;
  }

  /* ---------------------------------------------------------------- helpers */
  function labelOf(btn) {
    return btn.querySelector('.btn-label');
  }

  function setLabel(btn, text) {
    var el = labelOf(btn);
    if (el) el.textContent = text;
  }

  function iconOf(btn) {
    return btn.querySelector('i');
  }

  /* Estado original del botón (label + icono + ancho) para restaurarlo. */
  function guardarEstado(btn) {
    var icon = iconOf(btn);
    var label = labelOf(btn);
    return {
      label: label ? label.textContent : null,
      iconClass: icon ? icon.className : null,
      minWidth: btn.style.minWidth
    };
  }

  /* Fija el ancho actual para que el cambio de label no "salte" el layout. */
  function fijarAncho(btn) {
    if (btn.offsetWidth) btn.style.minWidth = btn.offsetWidth + 'px';
  }

  /* Único punto que libera el estado "busy" del botón y del formulario. */
  function resetBoton(form, btn, estado) {
    if (form) form.removeAttribute('data-anim-busy');
    if (!btn) return;
    btn.removeAttribute('aria-busy');
    btn.classList.remove(
      'btn--animating',
      'btn--anim-saving',
      'btn--anim-sending',
      'btn--anim-saved'
    );
    if (!estado) return;
    if (estado.label !== null && estado.label !== undefined) setLabel(btn, estado.label);
    var icon = iconOf(btn);
    if (icon && estado.iconClass) icon.className = estado.iconClass;
    btn.style.minWidth = estado.minWidth || '';
  }

  /* ------------------------------------------------------------ submit real */
  function submitForm(form, submitter) {
    // Conserva name/value del botón (p. ej. name="accion" value="borrador").
    if (submitter && submitter.getAttribute('name')) {
      var hidden = document.createElement('input');
      hidden.type = 'hidden';
      hidden.name = submitter.getAttribute('name');
      hidden.value = submitter.value;
      form.appendChild(hidden);
    }
    // Nunca form.submit(): si un campo se llama "submit" se rompería.
    HTMLFormElement.prototype.submit.call(form);
  }

  /* ------------------------------------------------------------- animaciones */
  function playSave(btn) {
    var icon = iconOf(btn);
    var iconOriginal = icon ? icon.className : '';

    btn.classList.add('btn--anim-saving');
    setLabel(btn, 'Guardando…');

    window.setTimeout(function () {
      // Cambia solo el nombre del icono (fa-save → fa-circle-check) sin
      // pisar el resto de clases (fa-solid, fa-fw, …).
      if (icon) icon.className = iconOriginal.replace('fa-save', 'fa-circle-check');
      btn.classList.add('btn--anim-saved');
      setLabel(btn, 'Borrador guardado');
    }, 430);
  }

  function playSend(btn) {
    btn.classList.add('btn--anim-sending');
    setLabel(btn, 'Enviando…');
  }

  function playTrash(btn, done) {
    var rect = btn.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      done();
      return;
    }

    // Tamaños derivados SOLO del alto del botón (el ancho NO influye):
    //   bote   = h × 1.0      ·  ancho del bote = alto × 0.9 (proporción del diseño)
    //                            (mismo alto que el botón: NO se agranda)
    //   bolita = h × 0.7      ·  diámetro final de la bola de papel
    //   vuelo  = la bola aterriza en la BOCA del bote (borde del cuerpo, 8px
    //            por encima) y ahí cae detrás del borde cuando cierra la tapa.
    var h = rect.height;
    var w = rect.width;
    var canH = Math.round(h);
    var canW = Math.round(canH * 0.9);
    var ball = Math.round(h * 0.7);
    var bocaY = -(8 + canH) + canH * 0.16 + ball / 2 - 8;

    var wrap = document.createElement('div');
    wrap.className = 'trash-anim';
    wrap.setAttribute('aria-hidden', 'true');
    wrap.style.left = Math.round(rect.left) + 'px';
    wrap.style.top = Math.round(rect.top) + 'px';
    wrap.style.width = Math.round(w) + 'px';
    wrap.style.height = Math.round(h) + 'px';
    wrap.style.setProperty('--trash-can-h', canH + 'px');
    wrap.style.setProperty('--trash-can-w', canW + 'px');
    wrap.style.setProperty('--trash-fly-x', '0px');
    wrap.style.setProperty('--trash-fly-y', Math.round(bocaY - h / 2) + 'px');
    wrap.style.setProperty('--trash-ball-sx', (ball / w).toFixed(3));
    wrap.style.setProperty('--trash-ball-sy', (ball / h).toFixed(3));

    var can = document.createElement('div');
    can.className = 'trash-anim__can';
    can.innerHTML = '<div class="trash-anim__lid"></div><div class="trash-anim__body"></div>';

    var clone = btn.cloneNode(true);
    clone.removeAttribute('id');
    clone.removeAttribute('data-anim');
    clone.removeAttribute('data-anim-clicked');
    clone.classList.add('trash-anim__flying');

    wrap.appendChild(can);
    wrap.appendChild(clone);
    document.body.appendChild(wrap);

    // visibility hereda el `transition: all` de .btn y tardaría ~300ms en
    // aplicarse: se congela la transición para que el clon tome su lugar
    // SIN parpadeo (a t=0 el clon es idéntico al botón).
    btn.style.transition = 'none';
    btn.style.visibility = 'hidden';

    window.setTimeout(done, DUR['delete']);

    // Red de seguridad: si la navegación no ocurre, se retira SOLO el
    // overlay (el estado del botón lo libera resetBoton, ver submit handler).
    window.setTimeout(function () {
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
      btn.style.visibility = '';
      btn.style.transition = '';
    }, 6000);
  }

  /* --------------------------------------------------------------- listeners */
  // Rastrea qué botón fue el que se pulsó (fallback de e.submitter).
  document.addEventListener(
    'click',
    function (e) {
      var node = e.target;
      if (!node) return;
      if (node.nodeType !== 1) node = node.parentElement;
      if (!node || !node.closest) return;
      var btn = node.closest('[data-anim]');
      if (!btn) return;
      var others = document.querySelectorAll('[data-anim-clicked="1"]');
      for (var i = 0; i < others.length; i++) others[i].removeAttribute('data-anim-clicked');
      btn.setAttribute('data-anim-clicked', '1');
    },
    true
  );

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form || form.nodeName !== 'FORM') return;
    if (reduced) return;

    var submitter = e.submitter;
    if (!submitter) {
      submitter = form.querySelector('[data-anim-clicked="1"]');
      var clicked = form.querySelectorAll('[data-anim-clicked="1"]');
      for (var j = 0; j < clicked.length; j++) clicked[j].removeAttribute('data-anim-clicked');
    }
    if (!submitter || !submitter.getAttribute('data-anim')) return;
    if (form.getAttribute('data-anim-busy') === '1') return;

    e.preventDefault();

    var kind = submitter.getAttribute('data-anim');
    var estado = guardarEstado(submitter);
    fijarAncho(submitter);

    form.setAttribute('data-anim-busy', '1');
    submitter.classList.add('btn--animating');
    submitter.setAttribute('aria-busy', 'true');

    // Red de seguridad: si la navegación no ocurre, se libera el botón.
    window.setTimeout(function () {
      resetBoton(form, submitter, estado);
    }, (DUR[kind] || 0) + 4000);

    var done = function () {
      submitForm(form, submitter);
    };

    if (kind === 'save') {
      playSave(submitter);
      window.setTimeout(done, DUR.save);
    } else if (kind === 'send') {
      playSend(submitter);
      window.setTimeout(done, DUR.send);
    } else if (kind === 'delete') {
      playTrash(submitter, done);
    } else {
      done();
    }
  });
})();
