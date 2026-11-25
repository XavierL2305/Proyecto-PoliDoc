/* === Minimapa interactivo en tiempo real para el Organigrama === */
(function (root) {
  'use strict';

  var PoliOrg = (root.PoliOrg = root.PoliOrg || {});

  function createMinimap(opts) {
    var store = opts.store;
    var viewport = opts.viewport;
    var viewportEl = opts.viewportEl;
    var containerEl = opts.containerEl;
    var canvasEl = opts.canvasEl;
    var toggleBtn = opts.toggleBtn;

    if (!canvasEl || !viewportEl || !containerEl) return null;

    var ctx = canvasEl.getContext('2d');
    var cssW = 184;
    var cssH = 120;
    var lastMap = null;
    var dragging = false;

    function computeMapping() {
      var bounds = store.computeBounds(store.state.isolatedSectionId || null);
      if (!bounds) return null;
      var pad = 60;
      var minX = bounds.minX - pad;
      var minY = bounds.minY - pad;
      var maxX = bounds.maxX + pad;
      var maxY = bounds.maxY + pad;
      var bw = Math.max(120, maxX - minX);
      var bh = Math.max(80, maxY - minY);
      var innerPad = 6;
      var scale = Math.min(
        (cssW - innerPad * 2) / bw,
        (cssH - innerPad * 2) / bh
      );
      var offX = (cssW - bw * scale) / 2 - minX * scale;
      var offY = (cssH - bh * scale) / 2 - minY * scale;
      return { scale: scale, offX: offX, offY: offY };
    }

    function draw() {
      if (containerEl.classList.contains('is-collapsed')) return;
      var dpr = root.devicePixelRatio || 1;
      var targetW = Math.round(cssW * dpr);
      var targetH = Math.round(cssH * dpr);
      if (canvasEl.width !== targetW || canvasEl.height !== targetH) {
        canvasEl.width = targetW;
        canvasEl.height = targetH;
      }
      ctx.save();
      ctx.scale(dpr, dpr);

      var dark = document.documentElement.getAttribute('data-theme') === 'dark';
      ctx.fillStyle = dark ? '#0F172A' : '#F8FAFC';
      ctx.fillRect(0, 0, cssW, cssH);

      var map = computeMapping();
      lastMap = map;
      if (!map) {
        ctx.fillStyle = dark ? '#64748B' : '#94A3B8';
        ctx.font = '11px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('Mapa vacío', cssW / 2, cssH / 2 + 4);
        ctx.restore();
        return;
      }

      var s = map.scale;
      var ox = map.offX;
      var oy = map.offY;
      var iso = store.state.isolatedSectionId;
      var query = store.state.searchQuery || '';

      // 1. Secciones
      var sections = store.state.datos.sections || [];
      for (var i = 0; i < sections.length; i++) {
        var sec = sections[i];
        if (iso && sec.id !== iso) continue;
        var sx = sec.x * s + ox;
        var sy = sec.y * s + oy;
        var sw = sec.w * s;
        var sh = sec.h * s;
        ctx.fillStyle = dark ? 'rgba(37, 99, 235, 0.14)' : 'rgba(37, 99, 235, 0.08)';
        ctx.strokeStyle = sec.color || '#2563EB';
        ctx.lineWidth = 1;
        ctx.fillRect(sx, sy, sw, sh);
        ctx.strokeRect(sx, sy, sw, sh);
      }

      // 2. Aristas
      var nodeMap = {};
      var nodes = store.state.datos.nodes || [];
      for (var n = 0; n < nodes.length; n++) {
        nodeMap[nodes[n].id] = nodes[n];
      }
      var edges = store.state.datos.edges || [];
      ctx.strokeStyle = dark ? '#475569' : '#94A3B8';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (var e = 0; e < edges.length; e++) {
        var edge = edges[e];
        var src = nodeMap[edge.from];
        var dst = nodeMap[edge.to];
        if (!src || !dst) continue;
        if (iso && (src.section_id !== iso || dst.section_id !== iso)) continue;
        ctx.moveTo((src.x + src.w / 2) * s + ox, (src.y + src.h / 2) * s + oy);
        ctx.lineTo((dst.x + dst.w / 2) * s + ox, (dst.y + dst.h / 2) * s + oy);
      }
      ctx.stroke();

      // 3. Nodos
      for (var j = 0; j < nodes.length; j++) {
        var node = nodes[j];
        if (iso && node.section_id !== iso) continue;
        var nx = node.x * s + ox;
        var ny = node.y * s + oy;
        var nw = Math.max(2, node.w * s);
        var nh = Math.max(2, node.h * s);
        var isSel = store.isNodeSelected(node.id);
        var isMatch = query && (node.text || '').toLowerCase().indexOf(query) !== -1;

        var bg = (node.style && node.style.bgColor) || 'auto';
        if (bg === 'auto' || bg === 'transparent') {
          ctx.fillStyle = dark ? '#334155' : '#FFFFFF';
        } else {
          ctx.fillStyle = bg;
        }
        ctx.fillRect(nx, ny, nw, nh);

        if (isMatch) {
          ctx.strokeStyle = '#F59E0B';
          ctx.lineWidth = 1.5;
        } else if (isSel) {
          ctx.strokeStyle = '#2563EB';
          ctx.lineWidth = 1.5;
        } else {
          ctx.strokeStyle = dark ? '#64748B' : '#94A3B8';
          ctx.lineWidth = 0.8;
        }
        ctx.strokeRect(nx, ny, nw, nh);
      }

      // 4. Rectángulo del viewport visible actual
      var vRect = viewportEl.getBoundingClientRect();
      if (vRect.width > 0 && vRect.height > 0) {
        var tl = viewport.clientToWorld(vRect.left, vRect.top);
        var br = viewport.clientToWorld(vRect.right, vRect.bottom);
        var vx = tl.x * s + ox;
        var vy = tl.y * s + oy;
        var vw = (br.x - tl.x) * s;
        var vh = (br.y - tl.y) * s;
        ctx.fillStyle = 'rgba(37, 99, 235, 0.14)';
        ctx.strokeStyle = '#2563EB';
        ctx.lineWidth = 1.5;
        ctx.fillRect(vx, vy, vw, vh);
        ctx.strokeRect(vx, vy, vw, vh);
      }

      ctx.restore();
    }

    function navigateFromPointer(ev) {
      if (!lastMap) return;
      var rect = canvasEl.getBoundingClientRect();
      var cx = ev.clientX - rect.left;
      var cy = ev.clientY - rect.top;
      var worldX = (cx - lastMap.offX) / lastMap.scale;
      var worldY = (cy - lastMap.offY) / lastMap.scale;
      viewport.centerOnWorld(worldX, worldY);
    }

    canvasEl.addEventListener('pointerdown', function (ev) {
      if (ev.button !== 0) return;
      ev.stopPropagation();
      dragging = true;
      navigateFromPointer(ev);
    });
    root.addEventListener('pointermove', function (ev) {
      if (!dragging) return;
      navigateFromPointer(ev);
    });
    root.addEventListener('pointerup', function () {
      dragging = false;
    });

    if (toggleBtn) {
      toggleBtn.addEventListener('click', function () {
        var collapsed = containerEl.classList.toggle('is-collapsed');
        var icon = toggleBtn.querySelector('i');
        if (icon) {
          icon.className = collapsed ? 'fa-solid fa-expand' : 'fa-solid fa-compress';
        }
        if (!collapsed) draw();
      });
    }

    store.on('render', draw);
    store.on('loaded', draw);
    viewport.onChange(draw);

    if (typeof root.ResizeObserver === 'function') {
      new root.ResizeObserver(draw).observe(viewportEl);
    } else {
      root.addEventListener('resize', draw);
    }

    draw();

    return { draw: draw };
  }

  PoliOrg.createMinimap = createMinimap;
})(window);
