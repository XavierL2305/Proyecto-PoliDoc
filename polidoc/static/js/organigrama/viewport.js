/* === Viewport: paneo, zoom GPU, ajuste a límites y pantalla completa === */
(function (root) {
  'use strict';

  var PoliOrg = (root.PoliOrg = root.PoliOrg || {});

  function createViewport(opts) {
    var workspaceEl = opts.workspaceEl;
    var viewportEl = opts.viewportEl;
    var canvasEl = opts.canvasEl;
    var zoomLabelEl = opts.zoomLabelEl;

    var tx = 40;
    var ty = 40;
    var scale = 1;
    var minScale = 0.15;
    var maxScale = 3.0;
    var animFrame = null;
    var changeListeners = [];

    // Respaldo para reparentar el workspace al abrir pantalla completa sin Fullscreen API
    var placeholder = document.createComment('org-workspace-anchor');
    var reparented = false;

    function clampScale(s) {
      return Math.max(minScale, Math.min(maxScale, s));
    }

    function notifyChange() {
      var snap = { tx: tx, ty: ty, scale: scale };
      for (var i = 0; i < changeListeners.length; i++) {
        changeListeners[i](snap);
      }
    }

    function applyTransform() {
      canvasEl.style.transform =
        'translate3d(' + tx + 'px, ' + ty + 'px, 0) scale(' + scale + ')';
      viewportEl.style.backgroundPosition = tx + 'px ' + ty + 'px';
      var gridStep = 20 * scale;
      if (gridStep >= 6) {
        viewportEl.style.backgroundSize = gridStep + 'px ' + gridStep + 'px';
      }
      if (zoomLabelEl) {
        zoomLabelEl.textContent = Math.round(scale * 100) + '%';
      }
      notifyChange();
    }

    function cancelAnim() {
      if (animFrame) {
        cancelAnimationFrame(animFrame);
        animFrame = null;
      }
    }

    function setTransform(nextTx, nextTy, nextScale) {
      cancelAnim();
      tx = nextTx;
      ty = nextTy;
      scale = clampScale(nextScale);
      applyTransform();
    }

    function animateTo(targetTx, targetTy, targetScale, durationMs) {
      cancelAnim();
      var startTx = tx;
      var startTy = ty;
      var startScale = scale;
      var endScale = clampScale(targetScale);
      var duration = durationMs || 280;
      var t0 = performance.now();

      function tick(now) {
        var p = Math.min(1, (now - t0) / duration);
        var ease = 1 - Math.pow(1 - p, 3);
        tx = startTx + (targetTx - startTx) * ease;
        ty = startTy + (targetTy - startTy) * ease;
        scale = startScale + (endScale - startScale) * ease;
        applyTransform();
        if (p < 1) {
          animFrame = requestAnimationFrame(tick);
        } else {
          animFrame = null;
        }
      }
      animFrame = requestAnimationFrame(tick);
    }

    function clientToWorld(clientX, clientY) {
      var rect = viewportEl.getBoundingClientRect();
      return {
        x: (clientX - rect.left - tx) / scale,
        y: (clientY - rect.top - ty) / scale,
      };
    }

    function zoomAt(clientX, clientY, factor) {
      var rect = viewportEl.getBoundingClientRect();
      var px = clientX - rect.left;
      var py = clientY - rect.top;
      var worldX = (px - tx) / scale;
      var worldY = (py - ty) / scale;
      var nextScale = clampScale(scale * factor);
      tx = px - worldX * nextScale;
      ty = py - worldY * nextScale;
      scale = nextScale;
      applyTransform();
    }

    function zoomCenter(factor) {
      var rect = viewportEl.getBoundingClientRect();
      zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
    }

    function fitToBounds(bounds, padding, animate) {
      var rect = viewportEl.getBoundingClientRect();
      if (!bounds || rect.width === 0 || rect.height === 0) {
        animateTo(40, 40, 1, 200);
        return;
      }
      var pad = padding !== undefined ? padding : 48;
      var bw = Math.max(80, bounds.maxX - bounds.minX);
      var bh = Math.max(80, bounds.maxY - bounds.minY);
      var availW = Math.max(120, rect.width - pad * 2);
      var availH = Math.max(120, rect.height - pad * 2);
      var nextScale = clampScale(Math.min(availW / bw, availH / bh, 1.25));
      var cx = (bounds.minX + bounds.maxX) / 2;
      var cy = (bounds.minY + bounds.maxY) / 2;
      var nextTx = rect.width / 2 - cx * nextScale;
      var nextTy = rect.height / 2 - cy * nextScale;
      if (animate === false) {
        setTransform(nextTx, nextTy, nextScale);
      } else {
        animateTo(nextTx, nextTy, nextScale, 280);
      }
    }

    function enterBodyOverlay() {
      if (!reparented && workspaceEl.parentNode) {
        workspaceEl.parentNode.insertBefore(placeholder, workspaceEl);
        document.body.appendChild(workspaceEl);
        reparented = true;
      }
      workspaceEl.classList.add('is-fullscreen');
    }

    function leaveBodyOverlay() {
      workspaceEl.classList.remove('is-fullscreen');
      if (reparented && placeholder.parentNode) {
        placeholder.parentNode.insertBefore(workspaceEl, placeholder);
        placeholder.parentNode.removeChild(placeholder);
        reparented = false;
      }
    }

    function isFullscreen() {
      return (
        document.fullscreenElement === workspaceEl ||
        workspaceEl.classList.contains('is-fullscreen')
      );
    }

    function toggleFullscreen() {
      if (document.fullscreenElement === workspaceEl) {
        if (document.exitFullscreen) document.exitFullscreen();
        return;
      }
      if (workspaceEl.classList.contains('is-fullscreen')) {
        leaveBodyOverlay();
        return;
      }
      if (workspaceEl.requestFullscreen) {
        workspaceEl.requestFullscreen().catch(function () {
          enterBodyOverlay();
        });
      } else {
        enterBodyOverlay();
      }
    }

    document.addEventListener('fullscreenchange', function () {
      if (document.fullscreenElement === workspaceEl) {
        workspaceEl.classList.add('is-fullscreen');
      } else if (!reparented) {
        workspaceEl.classList.remove('is-fullscreen');
      }
    });

    viewportEl.addEventListener(
      'wheel',
      function (ev) {
        ev.preventDefault();
        var factor = ev.deltaY < 0 ? 1.1 : 0.9;
        zoomAt(ev.clientX, ev.clientY, factor);
      },
      { passive: false }
    );

    applyTransform();

    return {
      getTransform: function () {
        return { tx: tx, ty: ty, scale: scale };
      },
      onChange: function (fn) {
        changeListeners.push(fn);
        return function () {
          changeListeners = changeListeners.filter(function (f) {
            return f !== fn;
          });
        };
      },
      centerOnWorld: function (worldX, worldY) {
        var rect = viewportEl.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        setTransform(
          rect.width / 2 - worldX * scale,
          rect.height / 2 - worldY * scale,
          scale
        );
      },
      setTransform: setTransform,
      animateTo: animateTo,
      clientToWorld: clientToWorld,
      zoomAt: zoomAt,
      zoomCenter: zoomCenter,
      fitToBounds: fitToBounds,
      toggleFullscreen: toggleFullscreen,
      isFullscreen: isFullscreen,
    };
  }

  PoliOrg.createViewport = createViewport;
})(window);
