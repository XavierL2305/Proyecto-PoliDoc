/* ==========================================================================
   POLIDOC - PROGRESS LOADER (barra de progreso con goteo continuo)
   Simula progreso con velocidad variable (rápido al inicio, lento al final),
   micro-pausas ocasionales y tope en `cap` (95%) hasta que la operación real
   termine.

   Uso:
     const pl = createProgressLoader('mi-contenedor', [
       { pct: 15, text: 'Preparando…' },
       { pct: 45, text: 'Procesando…' },
       { pct: 90, text: 'Refinando detalles…' },
     ]);
     pl.start();
     pl.finish();          // 100%
     pl.fail('mensaje');   // error
   ========================================================================== */
(function () {
  'use strict';

  function createProgressLoader(container, stages, opts) {
    if (typeof container === 'string') {
      container = document.getElementById(container);
    }
    var noop = { start: function () {}, setStage: function () {}, finish: function () {}, fail: function () {} };
    if (!container) return noop;

    opts = opts || {};
    stages = (stages && stages.length) ? stages : [{ pct: 95, text: 'Procesando…' }];
    var cap = opts.cap || 95;
    var minDelay = opts.minDelay || 150;
    var maxDelay = opts.maxDelay || 500;
    var pauseChance = (opts.pauseChance == null) ? 0.08 : opts.pauseChance;
    var pauseMs = opts.pauseMs || 1000;

    container.classList.remove('progress-loader--success', 'progress-loader--error');
    container.classList.add('progress-loader');
    container.innerHTML =
      '<div class="progress-loader__head">' +
        '<span class="progress-loader__label"><i class="fa-solid fa-gears fa-spin"></i><span></span></span>' +
        '<span class="progress-loader__pct">0%</span>' +
      '</div>' +
      '<div class="progress-loader__track"><div class="progress-loader__fill"></div></div>';

    var labelEl = container.querySelector('.progress-loader__label span');
    var iconEl = container.querySelector('.progress-loader__label i');
    var pctEl = container.querySelector('.progress-loader__pct');
    var fillEl = container.querySelector('.progress-loader__fill');

    var tickTimer = null;
    var pct = 0;
    var finished = false;

    function clearTimer() {
      if (tickTimer) { clearTimeout(tickTimer); tickTimer = null; }
    }

    function labelForPct(value) {
      var text = stages[0].text;
      for (var i = 0; i < stages.length; i++) {
        if (value >= stages[i].pct) text = stages[i].text;
      }
      return text;
    }

    function render() {
      var rounded = Math.max(0, Math.min(100, Math.round(pct)));
      fillEl.style.width = rounded + '%';
      pctEl.textContent = rounded + '%';
      labelEl.textContent = labelForPct(pct);
    }

    function tick() {
      if (finished) return;
      var remaining = cap - pct;
      if (remaining <= 0) return; // tope alcanzado: espera a finish()/fail()

      // Velocidad proporcional a lo que falta + variación aleatoria.
      var base = Math.max(0.3, remaining * 0.06);
      var step = base * (0.35 + Math.random() * 1.3);
      pct = Math.min(cap, pct + step);
      render();

      var delay = minDelay + Math.random() * (maxDelay - minDelay);
      if (Math.random() < pauseChance) delay += pauseMs;
      tickTimer = setTimeout(tick, delay);
    }

    return {
      start: function () {
        finished = false;
        clearTimer();
        pct = 0;
        render();
        tickTimer = setTimeout(tick, 400);
      },
      setStage: function (index) {
        if (finished) return;
        var stage = stages[Math.max(0, Math.min(stages.length - 1, index))];
        if (stage) {
          pct = Math.max(pct, Math.min(cap, stage.pct));
          render();
        }
      },
      finish: function (text) {
        finished = true;
        clearTimer();
        container.classList.add('progress-loader--success');
        iconEl.className = 'fa-solid fa-circle-check';
        fillEl.style.width = '100%';
        pctEl.textContent = '100%';
        labelEl.textContent = text || '¡Listo!';
      },
      fail: function (msg) {
        finished = true;
        clearTimer();
        container.classList.add('progress-loader--error');
        iconEl.className = 'fa-solid fa-triangle-exclamation';
        labelEl.textContent = msg || 'Ocurrió un error';
        pctEl.textContent = '';
      }
    };
  }

  window.createProgressLoader = createProgressLoader;
})();
