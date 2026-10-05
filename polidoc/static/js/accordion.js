/* Acordeón con animación fluida de altura.
 *
 * `toggleAccordion(btn)` anima la altura de `.accordion-content` para que el
 * contenedor padre (card, sección, etc.) crezca y decrezca suavemente en vez
 * de saltar. Compartido por detalle.html y mis_documentos.html (cargado en
 * base_app.html).
 */
(function () {
  'use strict';

  function prefiereMenosMovimiento() {
    return window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  function toggleAccordion(btn) {
    var accordion = btn.closest('.accordion');
    if (!accordion) return;
    var content = accordion.querySelector('.accordion-content');
    if (!content) {
      accordion.classList.toggle('active');
      return;
    }

    if (prefiereMenosMovimiento()) {
      accordion.classList.toggle('active');
      content.style.height = '';
      return;
    }

    var abriendo = !accordion.classList.contains('active');

    function alTerminar(e) {
      if (e.propertyName !== 'height') return;
      content.removeEventListener('transitionend', alTerminar);
      content.style.height = accordion.classList.contains('active') ? 'auto' : '';
    }
    content.removeEventListener('transitionend', alTerminar);
    content.addEventListener('transitionend', alTerminar);

    if (abriendo) {
      accordion.classList.add('active');
      content.style.height = '0px';
      var alto = content.scrollHeight;
      // Reflow para que el navegador registre el 0 antes de animar.
      void content.offsetHeight;
      if (alto <= 0) {
        content.style.height = 'auto';
        return;
      }
      content.style.height = alto + 'px';
    } else {
      content.style.height = content.getBoundingClientRect().height + 'px';
      void content.offsetHeight;
      accordion.classList.remove('active');
      content.style.height = '0px';
    }
  }

  window.toggleAccordion = toggleAccordion;
})();
