/* === Editor interactivo e inspector de propiedades === */
(function (root) {
  'use strict';

  var PoliOrg = (root.PoliOrg = root.PoliOrg || {});

  function createEditor(opts) {
    var store = opts.store;
    var viewport = opts.viewport;
    var renderer = opts.renderer;
    var viewportEl = opts.viewportEl;
    var inspectorTitleEl = opts.inspectorTitleEl;
    var inspectorBodyEl = opts.inspectorBodyEl;

    var interaction = null; // { kind: 'pan'|'drag-node'|'drag-nodes'|'drag-section'|'resize'|'connect'|'marquee', ... }
    var spaceHeld = false;
    var nudgeBurstActive = false;
    var nudgeBurstTimer = null;

    function nudgeStep(dx, dy) {
      if (!store.state.selectedNodeIds.length) return;
      if (!nudgeBurstActive) {
        nudgeBurstActive = true;
        store.commitCheckpoint();
      }
      clearTimeout(nudgeBurstTimer);
      nudgeBurstTimer = setTimeout(function () {
        nudgeBurstActive = false;
      }, 450);
      store.moveNodesDelta(store.state.selectedNodeIds, dx, dy, { skipHistory: true });
    }

    function nearestAnchor(node, worldPt) {
      var candidates = ['top', 'right', 'bottom', 'left'];
      var bestSide = 'top';
      var bestDist = Infinity;
      for (var i = 0; i < candidates.length; i++) {
        var p = renderer.anchorPoint(node, candidates[i]);
        var d = Math.hypot(p.x - worldPt.x, p.y - worldPt.y);
        if (d < bestDist) {
          bestDist = d;
          bestSide = candidates[i];
        }
      }
      return bestSide;
    }

    function startInlineEditNode(nodeId) {
      var node = store.findNode(nodeId);
      if (!node) return;
      var nodeEl = viewportEl.querySelector('.org-node[data-node-id="' + nodeId + '"]');
      if (!nodeEl) return;
      var textEl = nodeEl.querySelector('.org-node-text');
      if (!textEl) return;

      var textarea = document.createElement('textarea');
      textarea.className = 'org-node-inline-editor';
      textarea.value = node.text || '';
      textEl.style.display = 'none';
      nodeEl.appendChild(textarea);
      textarea.focus();
      textarea.select();

      var committed = false;
      function commit(save) {
        if (committed) return;
        committed = true;
        if (save && textarea.value !== node.text) {
          store.updateNode(nodeId, { text: textarea.value });
          renderInspector();
        } else {
          store.emit('render');
        }
      }

      textarea.addEventListener('pointerdown', function (ev) {
        ev.stopPropagation();
      });
      textarea.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape') {
          ev.preventDefault();
          commit(false);
        } else if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
          ev.preventDefault();
          commit(true);
        }
      });
      textarea.addEventListener('blur', function () {
        commit(true);
      });
    }

    function startInlineEditSection(secId) {
      var sec = store.findSection(secId);
      if (!sec) return;
      var headerEl = viewportEl.querySelector('[data-section-header-id="' + secId + '"]');
      if (!headerEl) return;
      var input = document.createElement('input');
      input.type = 'text';
      input.value = sec.title || '';
      input.className = 'form-input';
      input.style.height = '24px';
      input.style.fontSize = '0.78rem';
      input.style.padding = '0 0.4rem';
      headerEl.innerHTML = '';
      headerEl.appendChild(input);
      input.focus();
      input.select();

      var done = false;
      function commit(save) {
        if (done) return;
        done = true;
        var val = input.value.trim();
        if (save && val && val !== sec.title) {
          store.updateSection(secId, { title: val });
          renderInspector();
        } else {
          store.emit('render');
        }
      }
      input.addEventListener('pointerdown', function (ev) {
        ev.stopPropagation();
      });
      input.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter') commit(true);
        if (ev.key === 'Escape') commit(false);
      });
      input.addEventListener('blur', function () {
        commit(true);
      });
    }

    function handlePointerDown(ev) {
      if (ev.button !== 0 && ev.button !== 1) return;

      // Modo paneo forzado o botón central / Espacio
      if (ev.button === 1 || spaceHeld || store.state.tool === 'pan') {
        var t = viewport.getTransform();
        interaction = {
          kind: 'pan',
          startX: ev.clientX,
          startY: ev.clientY,
          origTx: t.tx,
          origTy: t.ty,
        };
        viewportEl.classList.add('is-panning');
        return;
      }

      // 1. Anchor de conexión
      var anchorEl = ev.target.closest('.org-anchor');
      if (anchorEl) {
        ev.stopPropagation();
        var srcId = anchorEl.dataset.anchorNodeId;
        var side = anchorEl.dataset.anchorSide;
        var srcNode = store.findNode(srcId);
        if (srcNode) {
          var fromPt = renderer.anchorPoint(srcNode, side);
          interaction = {
            kind: 'connect',
            fromNodeId: srcId,
            fromAnchor: side,
            fromPt: fromPt,
          };
          renderer.renderDraftLine(fromPt, fromPt);
        }
        return;
      }

      // 2. Resize handle
      var resizeEl = ev.target.closest('.org-resize-handle');
      if (resizeEl) {
        ev.stopPropagation();
        var targetType = resizeEl.dataset.resizeTarget;
        var targetId = resizeEl.dataset.resizeId;
        var dir = resizeEl.dataset.resizeDir;
        var item = targetType === 'section'
          ? store.findSection(targetId)
          : store.findNode(targetId);
        if (item) {
          interaction = {
            kind: 'resize',
            targetType: targetType,
            targetId: targetId,
            dir: dir,
            startWorld: viewport.clientToWorld(ev.clientX, ev.clientY),
            orig: { x: item.x, y: item.y, w: item.w, h: item.h },
            moved: false,
          };
        }
        return;
      }

      // 3. Arista SVG
      var edgeEl = ev.target.closest('.org-edge-group');
      if (edgeEl) {
        ev.stopPropagation();
        store.select('edge', edgeEl.dataset.edgeId);
        return;
      }

      // 4. Figura / Nodo
      var nodeEl = ev.target.closest('.org-node');
      if (nodeEl) {
        ev.stopPropagation();
        var nodeId = nodeEl.dataset.nodeId;
        if (store.state.tool === 'connect') {
          if (!store.state.connectFrom) {
            store.state.connectFrom = nodeId;
            store.select('node', nodeId);
          } else if (store.state.connectFrom !== nodeId) {
            store.addEdge(store.state.connectFrom, nodeId);
            store.setTool('select');
          }
          return;
        }
        var node = store.findNode(nodeId);
        if (node) {
          if (ev.shiftKey) {
            store.toggleNodeSelection(nodeId);
            return;
          }
          if (store.isNodeSelected(nodeId) && store.state.selectedNodeIds.length > 1) {
            var group = store.state.selectedNodeIds
              .map(function (id) {
                var item = store.findNode(id);
                return item ? { id: item.id, origX: item.x, origY: item.y } : null;
              })
              .filter(Boolean);
            interaction = {
              kind: 'drag-nodes',
              startWorld: viewport.clientToWorld(ev.clientX, ev.clientY),
              items: group,
              moved: false,
            };
            return;
          }
          store.select('node', nodeId);
          interaction = {
            kind: 'drag-node',
            nodeId: nodeId,
            startWorld: viewport.clientToWorld(ev.clientX, ev.clientY),
            origX: node.x,
            origY: node.y,
            moved: false,
          };
        }
        return;
      }

      // 5. Cabecera de sección
      var secHeaderEl = ev.target.closest('[data-section-header-id]');
      if (secHeaderEl) {
        ev.stopPropagation();
        var secId = secHeaderEl.dataset.sectionHeaderId;
        var sec = store.findSection(secId);
        if (sec) {
          store.select('section', secId);
          var children = store.state.datos.nodes
            .filter(function (n) { return n.section_id === secId; })
            .map(function (n) { return { id: n.id, x: n.x, y: n.y }; });
          interaction = {
            kind: 'drag-section',
            sectionId: secId,
            startWorld: viewport.clientToWorld(ev.clientX, ev.clientY),
            origX: sec.x,
            origY: sec.y,
            children: children,
            moved: false,
          };
        }
        return;
      }

      // 6. Marco de sección (solo borde/fondo en modo select)
      var secEl = ev.target.closest('.org-section');
      if (secEl && ev.target === secEl) {
        store.select('section', secEl.dataset.sectionId);
        return;
      }

      // 7. Fondo vacío -> selección por lazo (marquee) en herramienta Select
      if (store.state.tool === 'select') {
        var swPt = viewport.clientToWorld(ev.clientX, ev.clientY);
        interaction = {
          kind: 'marquee',
          startWorld: swPt,
          curWorld: swPt,
          additive: ev.shiftKey,
          prevIds: store.state.selectedNodeIds.slice(),
          moved: false,
        };
        if (!ev.shiftKey) {
          store.select(null, null);
        }
        return;
      }

      store.select(null, null);
      var t0 = viewport.getTransform();
      interaction = {
        kind: 'pan',
        startX: ev.clientX,
        startY: ev.clientY,
        origTx: t0.tx,
        origTy: t0.ty,
      };
      viewportEl.classList.add('is-panning');
    }

    function handlePointerMove(ev) {
      if (!interaction) return;

      if (interaction.kind === 'pan') {
        var t = viewport.getTransform();
        viewport.setTransform(
          interaction.origTx + (ev.clientX - interaction.startX),
          interaction.origTy + (ev.clientY - interaction.startY),
          t.scale
        );
        return;
      }

      var cur = viewport.clientToWorld(ev.clientX, ev.clientY);
      var dx = cur.x - interaction.startWorld.x;
      var dy = cur.y - interaction.startWorld.y;

      if (interaction.kind === 'marquee') {
        if (Math.abs(dx) < 4 && Math.abs(dy) < 4 && !interaction.moved) return;
        interaction.moved = true;
        interaction.curWorld = cur;
        renderer.renderMarquee({
          x: Math.min(interaction.startWorld.x, cur.x),
          y: Math.min(interaction.startWorld.y, cur.y),
          w: Math.abs(dx),
          h: Math.abs(dy),
        });
        return;
      }

      if (interaction.kind === 'drag-node') {
        if (Math.abs(dx) < 2 && Math.abs(dy) < 2 && !interaction.moved) return;
        if (!interaction.moved) {
          interaction.moved = true;
          store.commitCheckpoint();
        }
        store.updateNode(
          interaction.nodeId,
          { x: store.snap(interaction.origX + dx), y: store.snap(interaction.origY + dy) },
          { skipHistory: true }
        );
        return;
      }

      if (interaction.kind === 'drag-nodes') {
        if (Math.abs(dx) < 2 && Math.abs(dy) < 2 && !interaction.moved) return;
        if (!interaction.moved) {
          interaction.moved = true;
          store.commitCheckpoint();
        }
        var lead = interaction.items[0];
        if (!lead) return;
        var sdx = store.snap(lead.origX + dx) - lead.origX;
        var sdy = store.snap(lead.origY + dy) - lead.origY;
        for (var m = 0; m < interaction.items.length; m++) {
          var it = interaction.items[m];
          store.updateNode(
            it.id,
            { x: it.origX + sdx, y: it.origY + sdy },
            { silent: true, skipHistory: true }
          );
        }
        store.emit('render');
        return;
      }

      if (interaction.kind === 'drag-section') {
        if (Math.abs(dx) < 2 && Math.abs(dy) < 2 && !interaction.moved) return;
        if (!interaction.moved) {
          interaction.moved = true;
          store.commitCheckpoint();
        }
        var newSX = store.snap(interaction.origX + dx);
        var newSY = store.snap(interaction.origY + dy);
        var sdxSec = newSX - interaction.origX;
        var sdySec = newSY - interaction.origY;
        store.updateSection(interaction.sectionId, { x: newSX, y: newSY }, { silent: true, skipHistory: true });
        for (var i = 0; i < interaction.children.length; i++) {
          var ch = interaction.children[i];
          store.updateNode(ch.id, { x: ch.x + sdxSec, y: ch.y + sdySec }, { silent: true, skipHistory: true });
        }
        store.emit('render');
        return;
      }

      if (interaction.kind === 'resize') {
        var o = interaction.orig;
        var d = interaction.dir;
        var nx = o.x;
        var ny = o.y;
        var nw = o.w;
        var nh = o.h;
        var minW = interaction.targetType === 'section' ? 160 : 50;
        var minH = interaction.targetType === 'section' ? 100 : 32;

        if (d.indexOf('e') !== -1) nw = Math.max(minW, store.snap(o.w + dx));
        if (d.indexOf('s') !== -1) nh = Math.max(minH, store.snap(o.h + dy));
        if (d.indexOf('w') !== -1) {
          var candX = store.snap(o.x + dx);
          var diffX = o.x - candX;
          if (o.w + diffX >= minW) {
            nx = candX;
            nw = o.w + diffX;
          }
        }
        if (d.indexOf('n') !== -1) {
          var candY = store.snap(o.y + dy);
          var diffY = o.y - candY;
          if (o.h + diffY >= minH) {
            ny = candY;
            nh = o.h + diffY;
          }
        }

        if (nx === o.x && ny === o.y && nw === o.w && nh === o.h) return;
        if (!interaction.moved) {
          interaction.moved = true;
          store.commitCheckpoint();
        }
        if (interaction.targetType === 'section') {
          store.updateSection(interaction.targetId, { x: nx, y: ny, w: nw, h: nh }, { skipHistory: true });
        } else {
          store.updateNode(interaction.targetId, { x: nx, y: ny, w: nw, h: nh }, { skipHistory: true });
        }
        return;
      }

      if (interaction.kind === 'connect') {
        renderer.renderDraftLine(interaction.fromPt, cur);
      }
    }

    function handlePointerUp(ev) {
      if (!interaction) return;
      viewportEl.classList.remove('is-panning');

      if (interaction.kind === 'drag-node' && interaction.moved) {
        var node = store.findNode(interaction.nodeId);
        if (node) {
          var secId = store.containingSectionForPoint(
            node.x + node.w / 2,
            node.y + node.h / 2
          );
          if (secId !== node.section_id) {
            store.updateNode(node.id, { section_id: secId }, { skipHistory: true });
            renderInspector();
          }
        }
      } else if (interaction.kind === 'drag-nodes' && interaction.moved) {
        var changedSec = false;
        for (var g = 0; g < interaction.items.length; g++) {
          var gNode = store.findNode(interaction.items[g].id);
          if (!gNode) continue;
          var newSec = store.containingSectionForPoint(
            gNode.x + gNode.w / 2,
            gNode.y + gNode.h / 2
          );
          if (newSec !== gNode.section_id) {
            store.updateNode(gNode.id, { section_id: newSec }, { silent: true, skipHistory: true });
            changedSec = true;
          }
        }
        if (changedSec) {
          store.emit('render');
          renderInspector();
        }
      } else if (interaction.kind === 'marquee') {
        renderer.renderMarquee(null);
        if (interaction.moved) {
          var rx = Math.min(interaction.startWorld.x, interaction.curWorld.x);
          var ry = Math.min(interaction.startWorld.y, interaction.curWorld.y);
          var rw = Math.abs(interaction.curWorld.x - interaction.startWorld.x);
          var rh = Math.abs(interaction.curWorld.y - interaction.startWorld.y);
          var iso = store.state.isolatedSectionId;
          var matched = interaction.additive ? interaction.prevIds.slice() : [];
          var nods = store.state.datos.nodes || [];
          for (var i = 0; i < nods.length; i++) {
            var nd = nods[i];
            if (iso && nd.section_id !== iso) continue;
            var hit = !(
              nd.x + nd.w < rx ||
              nd.x > rx + rw ||
              nd.y + nd.h < ry ||
              nd.y > ry + rh
            );
            if (hit) matched.push(nd.id);
          }
          store.selectNodes(matched);
        }
      } else if (interaction.kind === 'connect') {
        renderer.renderDraftLine(null, null);
        var el = document.elementFromPoint(ev.clientX, ev.clientY);
        var targetAnchorEl = el ? el.closest('.org-anchor') : null;
        var targetNodeEl = el ? el.closest('.org-node') : null;
        var toId = targetAnchorEl
          ? targetAnchorEl.dataset.anchorNodeId
          : (targetNodeEl ? targetNodeEl.dataset.nodeId : null);
        if (toId && toId !== interaction.fromNodeId) {
          var dstNode = store.findNode(toId);
          var toAnchor = targetAnchorEl
            ? targetAnchorEl.dataset.anchorSide
            : nearestAnchor(dstNode, viewport.clientToWorld(ev.clientX, ev.clientY));
          store.addEdge(interaction.fromNodeId, toId, {
            fromAnchor: interaction.fromAnchor,
            toAnchor: toAnchor,
          });
        }
      }

      interaction = null;
    }

    function makeBurstUpdater(applyFn) {
      var snapped = false;
      return function (val) {
        if (!snapped) {
          snapped = true;
          store.commitCheckpoint();
        }
        applyFn(val);
      };
    }

    // === Constructores UI del Inspector (DOM puro sin innerHTML no seguro) ===
    function propGroup(labelText, controlEl) {
      var g = document.createElement('div');
      g.className = 'org-prop-group';
      if (labelText) {
        var lbl = document.createElement('label');
        lbl.className = 'org-prop-label';
        lbl.textContent = labelText;
        g.appendChild(lbl);
      }
      g.appendChild(controlEl);
      return g;
    }

    function makeColorField(currentVal, allowTransparent, onChange) {
      var wrap = document.createElement('div');
      wrap.className = 'org-color-field';

      var picker = document.createElement('input');
      picker.type = 'color';
      picker.className = 'org-color-input';
      picker.value = currentVal && currentVal[0] === '#' ? currentVal : '#2563EB';
      picker.addEventListener('input', function () {
        onChange(picker.value);
      });
      wrap.appendChild(picker);

      var btnAuto = document.createElement('button');
      btnAuto.type = 'button';
      btnAuto.className = 'btn btn--ghost btn--xs';
      btnAuto.textContent = 'Auto';
      btnAuto.addEventListener('click', function () {
        onChange('auto');
        renderInspector();
      });
      wrap.appendChild(btnAuto);

      if (allowTransparent) {
        var btnTr = document.createElement('button');
        btnTr.type = 'button';
        btnTr.className = 'btn btn--ghost btn--xs';
        btnTr.textContent = 'Ninguno';
        btnTr.addEventListener('click', function () {
          onChange('transparent');
          renderInspector();
        });
        wrap.appendChild(btnTr);
      }
      return wrap;
    }

    function makeSelect(options, currentVal, onChange) {
      var sel = document.createElement('select');
      sel.className = 'form-input';
      for (var i = 0; i < options.length; i++) {
        var opt = document.createElement('option');
        opt.value = options[i].value;
        opt.textContent = options[i].label;
        if (String(options[i].value) === String(currentVal)) opt.selected = true;
        sel.appendChild(opt);
      }
      sel.addEventListener('change', function () {
        onChange(sel.value);
      });
      return sel;
    }

    function makeNumber(val, min, max, step, onChange) {
      var inp = document.createElement('input');
      inp.type = 'number';
      inp.className = 'form-input';
      inp.min = String(min);
      inp.max = String(max);
      inp.step = String(step || 1);
      inp.value = String(val);
      inp.addEventListener('input', function () {
        var num = Number(inp.value);
        if (!isNaN(num) && num >= min && num <= max) {
          onChange(num);
        }
      });
      return inp;
    }

    function renderDocInspector() {
      inspectorTitleEl.textContent = 'Documento del Organigrama';
      var m = store.state.docMeta;

      var nameInp = document.createElement('input');
      nameInp.type = 'text';
      nameInp.className = 'form-input';
      nameInp.maxLength = 200;
      nameInp.value = m.nombre || '';
      nameInp.addEventListener('input', function () {
        m.nombre = nameInp.value;
        var h = document.getElementById('org-header-nombre');
        if (h) h.textContent = m.nombre;
        store.markDirty();
      });
      inspectorBodyEl.appendChild(propGroup('Nombre', nameInp));

      var descInp = document.createElement('textarea');
      descInp.className = 'form-input';
      descInp.rows = 3;
      descInp.value = m.descripcion || '';
      descInp.addEventListener('input', function () {
        m.descripcion = descInp.value;
        store.markDirty();
      });
      inspectorBodyEl.appendChild(propGroup('Descripción', descInp));

      inspectorBodyEl.appendChild(
        propGroup(
          'Visibilidad (Clasificación)',
          makeSelect(
            [
              { value: 'publico', label: 'Público (todo el personal)' },
              { value: 'interno', label: 'Interno' },
              { value: 'confidencial', label: 'Confidencial' },
              { value: 'restringido', label: 'Restringido' },
            ],
            m.clasificacion || 'publico',
            function (val) {
              m.clasificacion = val;
              store.markDirty();
            }
          )
        )
      );

      var tip = document.createElement('p');
      tip.style.fontSize = '0.78rem';
      tip.style.color = 'var(--color-muted)';
      tip.style.margin = '0.25rem 0 0';
      tip.textContent =
        'Consejo: Doble clic en una figura para editar su texto. Arrastra desde sus puntos azules para conectar.';
      inspectorBodyEl.appendChild(tip);
    }

    function renderNodeInspector(node) {
      inspectorTitleEl.textContent = 'Figura seleccionada';
      var st = node.style || {};

      var textArea = document.createElement('textarea');
      textArea.className = 'form-input';
      textArea.rows = 3;
      textArea.value = node.text || '';
      var onTextBurst = makeBurstUpdater(function (val) {
        store.updateNode(node.id, { text: val }, { skipHistory: true });
      });
      textArea.addEventListener('input', function () {
        onTextBurst(textArea.value);
      });
      inspectorBodyEl.appendChild(propGroup('Texto', textArea));

      inspectorBodyEl.appendChild(
        propGroup(
          'Forma',
          makeSelect(
            [
              { value: 'rounded', label: 'Redondeada' },
              { value: 'rect', label: 'Rectángulo' },
              { value: 'pill', label: 'Cápsula' },
              { value: 'diamond', label: 'Rombo' },
              { value: 'text', label: 'Solo texto' },
            ],
            node.shape,
            function (v) {
              store.updateNode(node.id, { shape: v });
            }
          )
        )
      );

      var onBgBurst = makeBurstUpdater(function (c) {
        store.updateNode(node.id, { style: { bgColor: c } }, { skipHistory: true });
      });
      inspectorBodyEl.appendChild(
        propGroup('Color de fondo', makeColorField(st.bgColor, true, onBgBurst))
      );
      var onTxtClrBurst = makeBurstUpdater(function (c) {
        store.updateNode(node.id, { style: { textColor: c } }, { skipHistory: true });
      });
      inspectorBodyEl.appendChild(
        propGroup('Color de texto', makeColorField(st.textColor, false, onTxtClrBurst))
      );

      var typoRow = document.createElement('div');
      typoRow.className = 'org-btn-row';
      var btnBold = document.createElement('button');
      btnBold.type = 'button';
      btnBold.className = 'btn btn--sm ' + (st.fontWeight === 'bold' ? 'btn--primary' : 'btn--ghost');
      btnBold.textContent = 'Negrita (B)';
      btnBold.addEventListener('click', function () {
        store.updateNode(node.id, {
          style: { fontWeight: st.fontWeight === 'bold' ? 'normal' : 'bold' },
        });
        renderInspector();
      });
      var btnItalic = document.createElement('button');
      btnItalic.type = 'button';
      btnItalic.className = 'btn btn--sm ' + (st.fontStyle === 'italic' ? 'btn--primary' : 'btn--ghost');
      btnItalic.textContent = 'Cursiva (I)';
      btnItalic.addEventListener('click', function () {
        store.updateNode(node.id, {
          style: { fontStyle: st.fontStyle === 'italic' ? 'normal' : 'italic' },
        });
        renderInspector();
      });
      typoRow.appendChild(btnBold);
      typoRow.appendChild(btnItalic);
      inspectorBodyEl.appendChild(propGroup('Estilo de texto', typoRow));

      var fontRow = document.createElement('div');
      fontRow.className = 'org-prop-row';
      var onFontSzBurst = makeBurstUpdater(function (v) {
        store.updateNode(node.id, { style: { fontSize: v } }, { skipHistory: true });
      });
      fontRow.appendChild(
        propGroup('Tamaño (px)', makeNumber(st.fontSize || 13, 9, 48, 1, onFontSzBurst))
      );
      fontRow.appendChild(
        propGroup(
          'Alineación',
          makeSelect(
            [
              { value: 'center', label: 'Centro' },
              { value: 'left', label: 'Izquierda' },
              { value: 'right', label: 'Derecha' },
            ],
            st.textAlign || 'center',
            function (v) {
              store.updateNode(node.id, { style: { textAlign: v } });
            }
          )
        )
      );
      inspectorBodyEl.appendChild(fontRow);

      var onBorderClrBurst = makeBurstUpdater(function (c) {
        store.updateNode(node.id, { style: { borderColor: c } }, { skipHistory: true });
      });
      inspectorBodyEl.appendChild(
        propGroup('Color de borde', makeColorField(st.borderColor, true, onBorderClrBurst))
      );

      var borderRow = document.createElement('div');
      borderRow.className = 'org-prop-row';
      var onBorderWBurst = makeBurstUpdater(function (v) {
        store.updateNode(node.id, { style: { borderWidth: v } }, { skipHistory: true });
      });
      borderRow.appendChild(
        propGroup('Grosor borde (px)', makeNumber(st.borderWidth !== undefined ? st.borderWidth : 2, 0, 12, 1, onBorderWBurst))
      );
      borderRow.appendChild(
        propGroup(
          'Tipo de borde',
          makeSelect(
            [
              { value: 'solid', label: 'Sólido' },
              { value: 'dashed', label: 'Discontinuo' },
              { value: 'dotted', label: 'Punteado' },
            ],
            st.borderStyle || 'solid',
            function (v) {
              store.updateNode(node.id, { style: { borderStyle: v } });
            }
          )
        )
      );
      inspectorBodyEl.appendChild(borderRow);

      var secOpts = [{ value: '', label: '— Sin sección —' }].concat(
        store.state.datos.sections.map(function (s) {
          return { value: s.id, label: (s.empresa ? '[' + s.empresa + '] ' : '') + s.title };
        })
      );
      inspectorBodyEl.appendChild(
        propGroup(
          'Sección asignada',
          makeSelect(secOpts, node.section_id || '', function (v) {
            store.updateNode(node.id, { section_id: v || null });
          })
        )
      );

      var actions = document.createElement('div');
      actions.className = 'org-inspector-actions';
      var btnDup = document.createElement('button');
      btnDup.type = 'button';
      btnDup.className = 'btn btn--ghost btn--sm';
      btnDup.textContent = 'Duplicar';
      btnDup.addEventListener('click', function () {
        store.duplicateSelectedNode();
      });
      var btnFront = document.createElement('button');
      btnFront.type = 'button';
      btnFront.className = 'btn btn--ghost btn--sm';
      btnFront.textContent = 'Al frente';
      btnFront.addEventListener('click', function () {
        store.updateNode(node.id, { zIndex: (node.zIndex || 1) + 10 });
      });
      var btnDel = document.createElement('button');
      btnDel.type = 'button';
      btnDel.className = 'btn btn--danger btn--sm';
      btnDel.textContent = 'Eliminar';
      btnDel.addEventListener('click', function () {
        store.deleteSelection();
      });
      actions.appendChild(btnDup);
      actions.appendChild(btnFront);
      actions.appendChild(btnDel);
      inspectorBodyEl.appendChild(actions);
    }

    function renderEdgeInspector(edge) {
      inspectorTitleEl.textContent = 'Línea de conexión';

      var lblInp = document.createElement('input');
      lblInp.type = 'text';
      lblInp.className = 'form-input';
      lblInp.maxLength = 120;
      lblInp.value = edge.label || '';
      lblInp.placeholder = 'Opcional (ej. Asesoría)';
      var onLblBurst = makeBurstUpdater(function (v) {
        store.updateEdge(edge.id, { label: v }, { skipHistory: true });
      });
      lblInp.addEventListener('input', function () {
        onLblBurst(lblInp.value);
      });
      inspectorBodyEl.appendChild(propGroup('Etiqueta', lblInp));

      var onEdgeClrBurst = makeBurstUpdater(function (c) {
        store.updateEdge(edge.id, { color: c }, { skipHistory: true });
      });
      inspectorBodyEl.appendChild(
        propGroup('Color de línea', makeColorField(edge.color, false, onEdgeClrBurst))
      );

      var lineRow = document.createElement('div');
      lineRow.className = 'org-prop-row';
      var onEdgeWBurst = makeBurstUpdater(function (v) {
        store.updateEdge(edge.id, { width: v }, { skipHistory: true });
      });
      lineRow.appendChild(
        propGroup('Grosor (px)', makeNumber(edge.width || 2, 1, 12, 1, onEdgeWBurst))
      );
      lineRow.appendChild(
        propGroup(
          'Estilo',
          makeSelect(
            [
              { value: 'solid', label: 'Sólida' },
              { value: 'dashed', label: 'Discontinua' },
              { value: 'dotted', label: 'Punteada' },
            ],
            edge.style || 'solid',
            function (v) {
              store.updateEdge(edge.id, { style: v });
            }
          )
        )
      );
      inspectorBodyEl.appendChild(lineRow);

      inspectorBodyEl.appendChild(
        propGroup(
          'Trazado',
          makeSelect(
            [
              { value: 'orthogonal', label: 'Ortogonal (codos 90°)' },
              { value: 'straight', label: 'Línea recta' },
            ],
            edge.routing || 'orthogonal',
            function (v) {
              store.updateEdge(edge.id, { routing: v });
            }
          )
        )
      );

      var arrowLbl = document.createElement('label');
      arrowLbl.style.display = 'flex';
      arrowLbl.style.alignItems = 'center';
      arrowLbl.style.gap = '0.4rem';
      arrowLbl.style.fontSize = '0.82rem';
      var arrowChk = document.createElement('input');
      arrowChk.type = 'checkbox';
      arrowChk.className = 'form-checkbox';
      arrowChk.checked = Boolean(edge.arrowEnd);
      arrowChk.addEventListener('change', function () {
        store.updateEdge(edge.id, { arrowEnd: arrowChk.checked });
      });
      arrowLbl.appendChild(arrowChk);
      var arrowTxt = document.createElement('span');
      arrowTxt.textContent = 'Mostrar flecha en destino';
      arrowLbl.appendChild(arrowTxt);
      inspectorBodyEl.appendChild(arrowLbl);

      var anchorOpts = [
        { value: 'top', label: 'Arriba' },
        { value: 'right', label: 'Derecha' },
        { value: 'bottom', label: 'Abajo' },
        { value: 'left', label: 'Izquierda' },
      ];
      var aRow = document.createElement('div');
      aRow.className = 'org-prop-row';
      aRow.appendChild(propGroup('Puerto origen', makeSelect(anchorOpts, edge.fromAnchor, function (v) {
        store.updateEdge(edge.id, { fromAnchor: v });
      })));
      aRow.appendChild(propGroup('Puerto destino', makeSelect(anchorOpts, edge.toAnchor, function (v) {
        store.updateEdge(edge.id, { toAnchor: v });
      })));
      inspectorBodyEl.appendChild(aRow);

      var actions = document.createElement('div');
      actions.className = 'org-inspector-actions';
      var btnDel = document.createElement('button');
      btnDel.type = 'button';
      btnDel.className = 'btn btn--danger btn--sm';
      btnDel.textContent = 'Eliminar conexión';
      btnDel.addEventListener('click', function () {
        store.deleteSelection();
      });
      actions.appendChild(btnDel);
      inspectorBodyEl.appendChild(actions);
    }

    function renderSectionInspector(sec) {
      inspectorTitleEl.textContent = 'Sección / Departamento';

      var titleInp = document.createElement('input');
      titleInp.type = 'text';
      titleInp.className = 'form-input';
      titleInp.maxLength = 200;
      titleInp.value = sec.title || '';
      var onSecTitleBurst = makeBurstUpdater(function (v) {
        store.updateSection(sec.id, { title: v }, { skipHistory: true });
      });
      titleInp.addEventListener('input', function () {
        onSecTitleBurst(titleInp.value);
      });
      inspectorBodyEl.appendChild(propGroup('Título de sección', titleInp));

      var empOpts = [{ value: '', label: 'General / Corporativo' }].concat(
        (store.state.empresas || []).map(function (e) {
          return { value: e.codigo, label: e.codigo + ' — ' + e.nombre };
        })
      );
      inspectorBodyEl.appendChild(
        propGroup('Empresa', makeSelect(empOpts, sec.empresa || '', function (v) {
          store.updateSection(sec.id, { empresa: v });
        }))
      );

      var deptoOpts = [{ value: '', label: '— Ninguno —' }].concat(
        (store.state.departamentos || []).map(function (d) {
          return { value: String(d.id), label: d.codigo + ' — ' + d.nombre };
        })
      );
      inspectorBodyEl.appendChild(
        propGroup(
          'Departamento vinculado',
          makeSelect(deptoOpts, sec.departamento_id ? String(sec.departamento_id) : '', function (v) {
            store.updateSection(sec.id, { departamento_id: v ? parseInt(v, 10) : null });
          })
        )
      );

      var onSecClrBurst = makeBurstUpdater(function (c) {
        store.updateSection(sec.id, { color: c === 'auto' ? '#2563EB' : c }, { skipHistory: true });
      });
      inspectorBodyEl.appendChild(
        propGroup(
          'Color del marco',
          makeColorField(sec.color || '#2563EB', false, onSecClrBurst)
        )
      );

      var onSecOrdBurst = makeBurstUpdater(function (v) {
        store.updateSection(sec.id, { order: Math.round(v) }, { skipHistory: true });
      });
      inspectorBodyEl.appendChild(
        propGroup('Orden en índice', makeNumber(sec.order || 0, 0, 1000, 1, onSecOrdBurst))
      );

      var actions = document.createElement('div');
      actions.className = 'org-inspector-actions';
      var btnDel = document.createElement('button');
      btnDel.type = 'button';
      btnDel.className = 'btn btn--danger btn--sm';
      btnDel.textContent = 'Eliminar sección';
      btnDel.addEventListener('click', function () {
        store.deleteSelection();
      });
      actions.appendChild(btnDel);
      inspectorBodyEl.appendChild(actions);
    }

    function renderMultiInspector(ids) {
      inspectorTitleEl.textContent = ids.length + ' figuras seleccionadas';

      var alignGrid = document.createElement('div');
      alignGrid.className = 'org-align-grid';
      var alignBtns = [
        { mode: 'left', label: 'Izq.', title: 'Alinear a la izquierda' },
        { mode: 'centerH', label: 'Centro H', title: 'Centrar horizontalmente' },
        { mode: 'right', label: 'Der.', title: 'Alinear a la derecha' },
        { mode: 'top', label: 'Arriba', title: 'Alinear arriba' },
        { mode: 'centerV', label: 'Centro V', title: 'Centrar verticalmente' },
        { mode: 'bottom', label: 'Abajo', title: 'Alinear abajo' },
      ];
      for (var i = 0; i < alignBtns.length; i++) {
        (function (spec) {
          var b = document.createElement('button');
          b.type = 'button';
          b.className = 'btn btn--ghost btn--xs';
          b.textContent = spec.label;
          b.title = spec.title;
          b.addEventListener('click', function () {
            store.alignSelectedNodes(spec.mode);
          });
          alignGrid.appendChild(b);
        })(alignBtns[i]);
      }
      inspectorBodyEl.appendChild(propGroup('Alinear selección', alignGrid));

      var onBatchBg = makeBurstUpdater(function (c) {
        store.updateNodesBatch(ids, { style: { bgColor: c } }, { skipHistory: true });
      });
      inspectorBodyEl.appendChild(
        propGroup('Color de fondo (grupo)', makeColorField('#2563EB', true, onBatchBg))
      );

      var onBatchTxt = makeBurstUpdater(function (c) {
        store.updateNodesBatch(ids, { style: { textColor: c } }, { skipHistory: true });
      });
      inspectorBodyEl.appendChild(
        propGroup('Color de texto (grupo)', makeColorField('#0F172A', false, onBatchTxt))
      );

      var onBatchBorder = makeBurstUpdater(function (c) {
        store.updateNodesBatch(ids, { style: { borderColor: c } }, { skipHistory: true });
      });
      inspectorBodyEl.appendChild(
        propGroup('Color de borde (grupo)', makeColorField('#CBD5E1', true, onBatchBorder))
      );

      var typoRow = document.createElement('div');
      typoRow.className = 'org-btn-row';
      var first = store.findNode(ids[0]);
      var fStyle = (first && first.style) || {};
      var bBold = document.createElement('button');
      bBold.type = 'button';
      bBold.className = 'btn btn--sm btn--ghost';
      bBold.textContent = 'Alternar Negrita';
      bBold.addEventListener('click', function () {
        var next = fStyle.fontWeight === 'bold' ? 'normal' : 'bold';
        store.updateNodesBatch(ids, { style: { fontWeight: next } });
        renderInspector();
      });
      var bItalic = document.createElement('button');
      bItalic.type = 'button';
      bItalic.className = 'btn btn--sm btn--ghost';
      bItalic.textContent = 'Alternar Cursiva';
      bItalic.addEventListener('click', function () {
        var next = fStyle.fontStyle === 'italic' ? 'normal' : 'italic';
        store.updateNodesBatch(ids, { style: { fontStyle: next } });
        renderInspector();
      });
      typoRow.appendChild(bBold);
      typoRow.appendChild(bItalic);
      inspectorBodyEl.appendChild(propGroup('Estilo de texto', typoRow));

      var secOpts = [{ value: '', label: '— Sin sección —' }].concat(
        store.state.datos.sections.map(function (s) {
          return { value: s.id, label: (s.empresa ? '[' + s.empresa + '] ' : '') + s.title };
        })
      );
      inspectorBodyEl.appendChild(
        propGroup(
          'Asignar sección al grupo',
          makeSelect(secOpts, '', function (v) {
            store.updateNodesBatch(ids, { section_id: v || null });
          })
        )
      );

      var actions = document.createElement('div');
      actions.className = 'org-inspector-actions';
      var btnDup = document.createElement('button');
      btnDup.type = 'button';
      btnDup.className = 'btn btn--ghost btn--sm';
      btnDup.textContent = 'Duplicar (' + ids.length + ')';
      btnDup.addEventListener('click', function () {
        store.duplicateSelection();
      });
      var btnDel = document.createElement('button');
      btnDel.type = 'button';
      btnDel.className = 'btn btn--danger btn--sm';
      btnDel.textContent = 'Eliminar (' + ids.length + ')';
      btnDel.addEventListener('click', function () {
        store.deleteSelection();
      });
      actions.appendChild(btnDup);
      actions.appendChild(btnDel);
      inspectorBodyEl.appendChild(actions);
    }

    function renderInspector() {
      if (!inspectorBodyEl || !inspectorTitleEl) return;
      inspectorBodyEl.innerHTML = '';
      var sel = store.state.selection;
      if (!sel) {
        renderDocInspector();
        return;
      }
      if (sel.type === 'multi') {
        renderMultiInspector(sel.ids || []);
      } else if (sel.type === 'node') {
        var n = store.findNode(sel.id);
        if (n) renderNodeInspector(n);
        else renderDocInspector();
      } else if (sel.type === 'edge') {
        var e = store.findEdge(sel.id);
        if (e) renderEdgeInspector(e);
        else renderDocInspector();
      } else if (sel.type === 'section') {
        var s = store.findSection(sel.id);
        if (s) renderSectionInspector(s);
        else renderDocInspector();
      }
    }

    viewportEl.addEventListener('pointerdown', handlePointerDown);
    root.addEventListener('pointermove', handlePointerMove);
    root.addEventListener('pointerup', handlePointerUp);

    viewportEl.addEventListener('dblclick', function (ev) {
      var nodeEl = ev.target.closest('.org-node');
      if (nodeEl) {
        ev.stopPropagation();
        startInlineEditNode(nodeEl.dataset.nodeId);
        return;
      }
      var secHeaderEl = ev.target.closest('[data-section-header-id]');
      if (secHeaderEl) {
        ev.stopPropagation();
        startInlineEditSection(secHeaderEl.dataset.sectionHeaderId);
      }
    });

    root.addEventListener('keydown', function (ev) {
      var tag = ev.target && ev.target.tagName;
      var typing =
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        (ev.target && ev.target.isContentEditable);
      var saveModal = document.getElementById('org-save-modal');
      var exportModal = document.getElementById('org-export-modal');
      var modalOpen =
        (saveModal && !saveModal.hidden) || (exportModal && !exportModal.hidden);

      if (ev.code === 'Space' && !typing && !modalOpen) {
        spaceHeld = true;
        viewportEl.classList.add('is-panning');
      }
      if (typing || modalOpen) return;

      var ctrl = ev.ctrlKey || ev.metaKey;
      var k = (ev.key || '').toLowerCase();

      if (ctrl) {
        if (k === 'z' && !ev.shiftKey) {
          ev.preventDefault();
          store.undo();
          return;
        }
        if (k === 'y' || (k === 'z' && ev.shiftKey)) {
          ev.preventDefault();
          store.redo();
          return;
        }
        if (k === 'c') {
          if (store.copySelection()) ev.preventDefault();
          return;
        }
        if (k === 'v') {
          if (store.pasteClipboard().length) ev.preventDefault();
          return;
        }
        if (k === 'd') {
          ev.preventDefault();
          store.duplicateSelection();
          return;
        }
        if (k === 'a') {
          ev.preventDefault();
          store.selectAllVisibleNodes();
          return;
        }
        if (k === 's') {
          ev.preventDefault();
          var saveBtn = document.getElementById('org-btn-save');
          if (saveBtn) saveBtn.click();
          return;
        }
      }

      if (!ctrl) {
        if (k === 'v') {
          store.setTool('select');
          return;
        }
        if (k === 'h') {
          store.setTool('pan');
          return;
        }
        if (k === 'c') {
          store.setTool('connect');
          return;
        }
      }

      if (
        store.state.selectedNodeIds.length > 0 &&
        (ev.key === 'ArrowUp' || ev.key === 'ArrowDown' || ev.key === 'ArrowLeft' || ev.key === 'ArrowRight')
      ) {
        ev.preventDefault();
        var step = ev.shiftKey
          ? 1
          : ((store.state.datos.settings && store.state.datos.settings.gridSize) || 20);
        var ndx = ev.key === 'ArrowLeft' ? -step : ev.key === 'ArrowRight' ? step : 0;
        var ndy = ev.key === 'ArrowUp' ? -step : ev.key === 'ArrowDown' ? step : 0;
        nudgeStep(ndx, ndy);
        return;
      }

      if (ev.key === 'Delete' || ev.key === 'Backspace') {
        if (store.deleteSelection()) ev.preventDefault();
      }
      if (ev.key === 'Escape') {
        store.setTool('select');
        store.select(null, null);
      }
    });

    root.addEventListener('keyup', function (ev) {
      if (ev.code === 'Space') {
        spaceHeld = false;
        viewportEl.classList.remove('is-panning');
      }
      if (
        ev.key === 'ArrowUp' ||
        ev.key === 'ArrowDown' ||
        ev.key === 'ArrowLeft' ||
        ev.key === 'ArrowRight'
      ) {
        nudgeBurstActive = false;
      }
    });

    store.on('selection', renderInspector);
    store.on('loaded', renderInspector);

    return { renderInspector: renderInspector };
  }

  PoliOrg.createEditor = createEditor;
})(window);
