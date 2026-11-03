/* ==========================================================================
   POLIDOC - Progreso de conversión a PDF con polling.
   Reutilizado por la ficha del documento y por la preview de plantillas.
   Requiere progress-loader.js (createProgressLoader) y un elemento:
     <div id="conv-progress" data-url="/documentos/.../estado-conversion/"></div>

   `initConversionProgress(el)` se expone para inicializar contenido inyectado
   por AJAX (modal de preview). El polling se detiene si el elemento se elimina.
   ========================================================================== */
(function () {
  'use strict';

  function initConversionProgress(container, options) {
    var el = container;
    if (typeof el === 'string') {
      el = document.getElementById(el);
    }
    if (!el || !window.createProgressLoader || el.dataset.convInit === '1') {
      return;
    }

    var url = el.getAttribute('data-url');
    if (!url) return;

    options = options || {};
    var onReady = options.onReady;

    el.dataset.convInit = '1';
    var loader = window.createProgressLoader(el, [
      { pct: 10, text: 'Preparando documento…' },
      { pct: 40, text: 'Transformando documento…' },
      { pct: 70, text: 'Convirtiendo a PDF…' },
      { pct: 90, text: 'Refinando detalles…' },
    ]);
    loader.start();

    var intentos = 0;
    var max = 40;

    function poll() {
      // El modal pudo cerrarse: el contenedor ya no está en el DOM.
      if (!el.isConnected) return;
      fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!el.isConnected) return;
          if (data.listo) {
            loader.finish('Documento listo');
            if (onReady) {
              setTimeout(function () {
                if (el.isConnected) onReady();
              }, 600);
            } else {
              setTimeout(function () {
                if (el.isConnected) window.location.reload();
              }, 600);
            }
          } else if (++intentos < max) {
            setTimeout(poll, 3000);
          } else {
            loader.fail('Está tardando más de lo esperado. Recarga la página en un momento.');
          }
        })
        .catch(function () {
          if (el.isConnected) {
            loader.fail('No se pudo consultar el estado de la conversión.');
          }
        });
    }

    setTimeout(poll, 2000);
  }

  window.initConversionProgress = initConversionProgress;

  document.addEventListener('DOMContentLoaded', function () {
    initConversionProgress('conv-progress');
  });
})();
