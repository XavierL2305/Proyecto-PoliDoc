/* === Arranque principal: índice mapa, búsqueda, versiones, lectura y guardado === */
(function (root) {
  'use strict';

  var PoliOrg = (root.PoliOrg = root.PoliOrg || {});

  function readJsonScript(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return fallback;
    }
  }

  function init() {
    var ws = document.getElementById('org-workspace');
    if (!ws || !PoliOrg.createStore || !PoliOrg.createViewport || !PoliOrg.createRenderer) {
      return;
    }

    var mode = ws.dataset.mode || 'viewer';
    var datosUrl = ws.dataset.datosUrl || '';
    var guardarUrl = ws.dataset.guardarUrl || '';
    var exportarUrl = ws.dataset.exportarUrl || '';
    var puedeExportar = ws.dataset.puedeExportar === 'true';
    var isEditor = mode === 'editor' && typeof PoliOrg.createEditor === 'function';

    var viewportEl = document.getElementById('org-viewport');
    var canvasEl = document.getElementById('org-canvas');
    var sectionsLayer = document.getElementById('org-layer-sections');
    var edgesLayer = document.getElementById('org-layer-edges');
    var nodesLayer = document.getElementById('org-layer-nodes');
    var draftLayer = document.getElementById('org-layer-draft');
    var emptyHintEl = document.getElementById('org-empty-hint');
    var loadingOverlayEl = document.getElementById('org-loading-overlay');
    var statusTextEl = document.getElementById('org-status-text');
    var zoomLabelEl = document.getElementById('org-zoom-label');

    var store = PoliOrg.createStore({
      mode: mode,
      docUuid: ws.dataset.docUuid,
      docKind: ws.dataset.docKind || 'organigrama',
      docLabel: ws.dataset.docLabel || 'Organigrama',
      docEmpresa: ws.dataset.docEmpresa || '',
      docCodigo: ws.dataset.docCodigo,
      docNombre: ws.dataset.docNombre,
      docDescripcion: ws.dataset.docDescripcion,
      docClasificacion: ws.dataset.docClasificacion,
      versionActual: ws.dataset.docVersionActual,
      proximaVersion: ws.dataset.docProximaVersion,
      versionVisualizada: ws.dataset.version,
      revisionEdicion: ws.dataset.revision,
      departamentos: readJsonScript('org-departamentos-data', []),
      empresas: readJsonScript('org-empresas-data', []),
    });

    var viewport = PoliOrg.createViewport({
      workspaceEl: ws,
      viewportEl: viewportEl,
      canvasEl: canvasEl,
      zoomLabelEl: zoomLabelEl,
    });

    var renderer = PoliOrg.createRenderer({
      store: store,
      sectionsLayer: sectionsLayer,
      edgesLayer: edgesLayer,
      nodesLayer: nodesLayer,
      draftLayer: draftLayer,
      emptyHintEl: emptyHintEl,
    });

    if (typeof PoliOrg.createMinimap === 'function') {
      PoliOrg.createMinimap({
        store: store,
        viewport: viewport,
        viewportEl: viewportEl,
        containerEl: document.getElementById('org-minimap'),
        canvasEl: document.getElementById('org-minimap-canvas'),
        toggleBtn: document.getElementById('org-minimap-toggle'),
      });
    }

    if (isEditor) {
      PoliOrg.createEditor({
        store: store,
        viewport: viewport,
        renderer: renderer,
        viewportEl: viewportEl,
        inspectorTitleEl: document.getElementById('org-inspector-title'),
        inspectorBodyEl: document.getElementById('org-inspector-body'),
      });
    } else {
      // Paneo fluido arrastrando con clic en modo visor
      var panDrag = null;
      viewportEl.classList.add('mode-pan');
      viewportEl.addEventListener('pointerdown', function (ev) {
        if (ev.button !== 0 && ev.button !== 1) return;
        var t = viewport.getTransform();
        panDrag = { x: ev.clientX, y: ev.clientY, tx: t.tx, ty: t.ty };
        viewportEl.classList.add('is-panning');
      });
      root.addEventListener('pointermove', function (ev) {
        if (!panDrag) return;
        var t = viewport.getTransform();
        viewport.setTransform(
          panDrag.tx + (ev.clientX - panDrag.x),
          panDrag.ty + (ev.clientY - panDrag.y),
          t.scale
        );
      });
      root.addEventListener('pointerup', function () {
        if (!panDrag) return;
        panDrag = null;
        viewportEl.classList.remove('is-panning');
      });
    }

    // === Índice lateral de secciones ===
    var indexContainer = document.getElementById('org-sections-index');
    var searchInput = document.getElementById('org-search-input');
    var searchResults = document.getElementById('org-search-results');

    function updateStatusPill() {
      if (!statusTextEl) return;
      var isoId = store.state.isolatedSectionId;
      var crossSuffix = store.state.showCrossEdges === false ? ' · Enlaces entre secciones ocultos' : '';
      var docLabelLower = (store.state.docMeta.label || 'Organigrama').toLowerCase();
      if (isoId) {
        var sec = store.findSection(isoId);
        statusTextEl.textContent =
          'Aislada: ' + (sec ? sec.title : 'Sección') + ' (clic en "Ver todo" para restaurar)';
      } else {
        statusTextEl.textContent = 'Mostrando ' + docLabelLower + ' completo' + crossSuffix;
      }
    }

    // === Trazado interactivo de conexiones al pasar el puntero ===
    function shortNodeName(node) {
      if (!node || !node.text) return 'Figura';
      return String(node.text).split('\n')[0].trim().slice(0, 46);
    }

    function clearTrace() {
      canvasEl.classList.remove('has-trace');
      var oldEdges = edgesLayer.querySelectorAll('.is-traced, .is-traced-in, .is-traced-out');
      for (var i = 0; i < oldEdges.length; i++) {
        oldEdges[i].classList.remove('is-traced', 'is-traced-in', 'is-traced-out');
      }
      var oldNodes = nodesLayer.querySelectorAll('.is-trace-src, .is-trace-dst, .is-trace-neighbor');
      for (var j = 0; j < oldNodes.length; j++) {
        oldNodes[j].classList.remove('is-trace-src', 'is-trace-dst', 'is-trace-neighbor');
      }
      updateStatusPill();
    }

    function promoteEdgeGroup(el) {
      if (el && el.parentNode === edgesLayer && edgesLayer.lastElementChild !== el) {
        ignoreHoverOutUntil = Date.now() + 40;
        edgesLayer.appendChild(el);
      }
    }

    function applyTrace(kind, id) {
      clearTrace();
      if (!kind || !id) return;

      if (kind === 'edge') {
        var g = edgesLayer.querySelector('.org-edge-group[data-edge-id="' + id + '"]');
        if (!g) return;
        var fromId = g.dataset.fromId;
        var toId = g.dataset.toId;
        canvasEl.classList.add('has-trace');
        g.classList.add('is-traced');
        promoteEdgeGroup(g);

        var srcEl = nodesLayer.querySelector('.org-node[data-node-id="' + fromId + '"]');
        var dstEl = nodesLayer.querySelector('.org-node[data-node-id="' + toId + '"]');
        if (srcEl) srcEl.classList.add('is-trace-src');
        if (dstEl) dstEl.classList.add('is-trace-dst');

        if (statusTextEl) {
          var srcNode = store.findNode(fromId);
          var dstNode = store.findNode(toId);
          statusTextEl.textContent =
            'Conexión: ' + shortNodeName(srcNode) + ' → ' + shortNodeName(dstNode);
        }
        return;
      }

      if (kind === 'node') {
        var targetNodeEl = nodesLayer.querySelector('.org-node[data-node-id="' + id + '"]');
        if (!targetNodeEl) return;
        var groups = edgesLayer.querySelectorAll('.org-edge-group');
        var matchCount = 0;
        var promote = [];
        for (var k = 0; k < groups.length; k++) {
          var eg = groups[k];
          if (eg.dataset.fromId === id) {
            eg.classList.add('is-traced-out');
            promote.push(eg);
            var nbOut = nodesLayer.querySelector('.org-node[data-node-id="' + eg.dataset.toId + '"]');
            if (nbOut) nbOut.classList.add('is-trace-neighbor');
            matchCount += 1;
          } else if (eg.dataset.toId === id) {
            eg.classList.add('is-traced-in');
            promote.push(eg);
            var nbIn = nodesLayer.querySelector('.org-node[data-node-id="' + eg.dataset.fromId + '"]');
            if (nbIn) nbIn.classList.add('is-trace-neighbor');
            matchCount += 1;
          }
        }
        if (matchCount === 0) return;

        canvasEl.classList.add('has-trace');
        targetNodeEl.classList.add('is-trace-src');
        for (var p = 0; p < promote.length; p++) {
          promoteEdgeGroup(promote[p]);
        }
        if (statusTextEl) {
          statusTextEl.textContent =
            shortNodeName(store.findNode(id)) + ' · ' + matchCount + ' conexión(es) directa(s)';
        }
      }
    }

    var activeHover = null;
    var ignoreHoverOutUntil = 0;
    viewportEl.addEventListener('pointerover', function (ev) {
      if (ev.buttons || !ev.target || typeof ev.target.closest !== 'function') return;
      var edgeG = ev.target.closest('.org-edge-group');
      if (edgeG) {
        var eid = edgeG.dataset.edgeId;
        if (activeHover && activeHover.kind === 'edge' && activeHover.id === eid) return;
        activeHover = { kind: 'edge', id: eid };
        applyTrace('edge', eid);
        return;
      }
      var nodeDiv = ev.target.closest('.org-node');
      if (nodeDiv) {
        var nid = nodeDiv.dataset.nodeId;
        if (activeHover && activeHover.kind === 'node' && activeHover.id === nid) return;
        activeHover = { kind: 'node', id: nid };
        applyTrace('node', nid);
      }
    });
    viewportEl.addEventListener('pointerout', function (ev) {
      if (!activeHover || Date.now() < ignoreHoverOutUntil) return;
      var nextTarget = ev.relatedTarget;
      if (
        nextTarget &&
        typeof nextTarget.closest === 'function' &&
        (nextTarget.closest('.org-edge-group') || nextTarget.closest('.org-node'))
      ) {
        return;
      }
      activeHover = null;
      var sel = store.state.selection;
      if (sel && sel.type === 'edge') {
        applyTrace('edge', sel.id);
      } else {
        clearTrace();
      }
    });
    viewportEl.addEventListener('pointerleave', function () {
      activeHover = null;
      var sel = store.state.selection;
      if (sel && sel.type === 'edge') {
        applyTrace('edge', sel.id);
      } else {
        clearTrace();
      }
    });
    store.on('selection', function (sel) {
      if (activeHover) return;
      if (sel && sel.type === 'edge') {
        applyTrace('edge', sel.id);
      } else {
        clearTrace();
      }
    });
    store.on('render', function () {
      if (activeHover) {
        applyTrace(activeHover.kind, activeHover.id);
      } else if (store.state.selection && store.state.selection.type === 'edge') {
        applyTrace('edge', store.state.selection.id);
      } else {
        updateStatusPill();
      }
    });

    function focusSection(secId, animate) {
      var bounds = store.computeBounds(secId);
      if (bounds) viewport.fitToBounds(bounds, 54, animate);
    }

    function renderSectionsIndex() {
      if (!indexContainer) return;
      indexContainer.innerHTML = '';
      var secs = store.state.datos.sections.slice().sort(function (a, b) {
        return (a.order || 0) - (b.order || 0);
      });

      if (!secs.length) {
        var empty = document.createElement('div');
        empty.className = 'org-index-empty';
        empty.textContent = 'Sin secciones creadas todavía.';
        indexContainer.appendChild(empty);
        updateStatusPill();
        return;
      }

      var groups = {};
      var orderKeys = ['PTCA', 'PTH', 'FP', 'FS', ''];
      var labels = {
        PTCA: 'PTCA — Policlínica Táchira',
        PTH: 'PTH — Hospitalización',
        FP: 'FP — Farmacia',
        FS: 'FS — Fuente de Soda',
        '': 'Corporativo / General',
      };

      for (var i = 0; i < secs.length; i++) {
        var key = secs[i].empresa || '';
        if (!groups[key]) groups[key] = [];
        groups[key].push(secs[i]);
      }

      for (var k = 0; k < orderKeys.length; k++) {
        var empKey = orderKeys[k];
        var list = groups[empKey];
        if (!list || !list.length) continue;

        var grpEl = document.createElement('div');
        grpEl.className = 'org-index-group';
        var grpTitle = document.createElement('div');
        grpTitle.className = 'org-index-group-title';
        grpTitle.textContent = labels[empKey] || empKey;
        grpEl.appendChild(grpTitle);

        for (var j = 0; j < list.length; j++) {
          (function (sec) {
            var isIso = store.state.isolatedSectionId === sec.id;
            var row = document.createElement('div');
            row.className = 'org-index-item' + (isIso ? ' is-isolated' : '');

            var dot = document.createElement('span');
            dot.className = 'org-index-dot';
            dot.style.backgroundColor = sec.color || '#2563EB';
            row.appendChild(dot);

            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'org-index-btn';
            btn.textContent = sec.title || 'Sección';
            btn.title = 'Enfocar sección: ' + (sec.title || 'Sección');
            btn.addEventListener('click', function () {
              if (store.state.isolatedSectionId && store.state.isolatedSectionId !== sec.id) {
                store.setIsolatedSection(sec.id);
              }
              focusSection(sec.id, true);
              if (isEditor) store.select('section', sec.id);
            });
            row.appendChild(btn);

            var isoBtn = document.createElement('button');
            isoBtn.type = 'button';
            isoBtn.className = 'org-index-isolate' + (isIso ? ' is-active' : '');
            isoBtn.title = isIso ? 'Mostrar todas las secciones' : 'Aislar solo esta sección';
            var eye = document.createElement('i');
            eye.className = isIso ? 'fa-solid fa-eye-slash' : 'fa-solid fa-eye';
            isoBtn.appendChild(eye);
            isoBtn.addEventListener('click', function () {
              store.setIsolatedSection(sec.id);
              if (store.state.isolatedSectionId === sec.id) {
                focusSection(sec.id, true);
              } else {
                viewport.fitToBounds(store.computeBounds(null), 54, true);
              }
            });
            row.appendChild(isoBtn);

            grpEl.appendChild(row);
          })(list[j]);
        }
        indexContainer.appendChild(grpEl);
      }
      updateStatusPill();
    }

    function renderSearchResults(q) {
      if (!searchResults) return;
      searchResults.innerHTML = '';
      if (!q) {
        searchResults.hidden = true;
        return;
      }
      var matches = store.state.datos.nodes.filter(function (n) {
        return (n.text || '').toLowerCase().indexOf(q) !== -1;
      }).slice(0, 20);

      if (!matches.length) {
        searchResults.hidden = true;
        return;
      }
      searchResults.hidden = false;
      for (var i = 0; i < matches.length; i++) {
        (function (node) {
          var item = document.createElement('button');
          item.type = 'button';
          item.className = 'org-search-item';
          var strong = document.createElement('span');
          strong.textContent = node.text || '(sin texto)';
          item.appendChild(strong);

          var sec = node.section_id ? store.findSection(node.section_id) : null;
          if (sec) {
            var sm = document.createElement('small');
            sm.textContent = (sec.empresa ? sec.empresa + ' · ' : '') + sec.title;
            item.appendChild(sm);
          }
          item.addEventListener('click', function () {
            if (store.state.isolatedSectionId && store.state.isolatedSectionId !== node.section_id) {
              store.setIsolatedSection(null);
            }
            viewport.fitToBounds(
              { minX: node.x - 100, minY: node.y - 80, maxX: node.x + node.w + 100, maxY: node.y + node.h + 80 },
              40,
              true
            );
            if (isEditor) store.select('node', node.id);
          });
          searchResults.appendChild(item);
        })(matches[i]);
      }
    }

    if (searchInput) {
      searchInput.addEventListener('input', function () {
        store.setSearchQuery(searchInput.value);
        renderSearchResults(store.state.searchQuery);
      });
    }

    store.on('sectionsChanged', renderSectionsIndex);
    store.on('isolation', renderSectionsIndex);
    store.on('loaded', renderSectionsIndex);

    // === Controles de barra superior ===
    var sidebarEl = document.getElementById('org-sidebar');
    var inspectorEl = document.getElementById('org-inspector');
    var dirtyBadge = document.getElementById('org-dirty-badge');
    var versionSelect = document.getElementById('org-version-select');

    function populateVersions() {
      if (!versionSelect) return;
      versionSelect.innerHTML = '';
      var vers = store.state.versiones || [];
      for (var i = 0; i < vers.length; i++) {
        var v = vers[i];
        var opt = document.createElement('option');
        opt.value = v.numero_version;
        opt.textContent = 'v' + v.numero_version + (v.es_vigente ? ' (Vigente)' : '');
        if (v.numero_version === store.state.versionVisualizada) opt.selected = true;
        versionSelect.appendChild(opt);
      }
    }

    store.on('loaded', function () {
      populateVersions();
      if (dirtyBadge) {
        dirtyBadge.className = 'badge badge--secondary';
        dirtyBadge.textContent = 'Sin cambios';
      }
      var hVer = document.getElementById('org-header-version');
      if (hVer) hVer.textContent = store.state.versionVisualizada;
      var bounds = store.computeBounds(null);
      if (bounds) viewport.fitToBounds(bounds, 54, false);
    });

    store.on('dirty', function (isDirty) {
      if (!dirtyBadge) return;
      dirtyBadge.className = isDirty ? 'badge badge--warning' : 'badge badge--secondary';
      dirtyBadge.textContent = isDirty ? 'Cambios sin guardar' : 'Sin cambios';
    });

    if (versionSelect) {
      versionSelect.addEventListener('change', function () {
        if (store.state.dirty && !root.confirm('Tienes cambios sin guardar. ¿Cargar otra versión y descartarlos?')) {
          versionSelect.value = store.state.versionVisualizada;
          return;
        }
        fetchDatos(versionSelect.value);
      });
    }

    var toggleIdxBtn = document.getElementById('org-btn-toggle-index');
    if (toggleIdxBtn && sidebarEl) {
      toggleIdxBtn.addEventListener('click', function () {
        sidebarEl.classList.toggle('is-collapsed');
      });
    }

    var toggleInspBtn = document.getElementById('org-btn-toggle-inspector');
    if (toggleInspBtn && inspectorEl) {
      toggleInspBtn.addEventListener('click', function () {
        inspectorEl.classList.toggle('is-collapsed');
      });
    }

    var zoomInBtn = document.getElementById('org-btn-zoom-in');
    if (zoomInBtn) zoomInBtn.addEventListener('click', function () { viewport.zoomCenter(1.18); });

    var zoomOutBtn = document.getElementById('org-btn-zoom-out');
    if (zoomOutBtn) zoomOutBtn.addEventListener('click', function () { viewport.zoomCenter(0.84); });

    function showAll() {
      if (store.state.isolatedSectionId) store.setIsolatedSection(null);
      viewport.fitToBounds(store.computeBounds(null), 54, true);
    }

    var fitBtn = document.getElementById('org-btn-fit');
    if (fitBtn) fitBtn.addEventListener('click', showAll);
    var showAllBtn = document.getElementById('org-btn-show-all');
    if (showAllBtn) showAllBtn.addEventListener('click', showAll);

    var crossEdgesBtn = document.getElementById('org-btn-toggle-cross-edges');
    if (crossEdgesBtn) {
      store.on('crossEdges', function (show) {
        crossEdgesBtn.classList.toggle('is-active', Boolean(show));
      });
      crossEdgesBtn.addEventListener('click', function () {
        store.toggleCrossEdges();
      });
    }

    var fsBtn = document.getElementById('org-btn-fullscreen');
    if (fsBtn) {
      fsBtn.addEventListener('click', function () {
        viewport.toggleFullscreen();
      });
    }

    if (isEditor) {
      var undoBtn = document.getElementById('org-btn-undo');
      var redoBtn = document.getElementById('org-btn-redo');
      function syncHistoryBtns(info) {
        var canU = info ? info.canUndo : store.canUndo();
        var canR = info ? info.canRedo : store.canRedo();
        if (undoBtn) undoBtn.disabled = !canU;
        if (redoBtn) redoBtn.disabled = !canR;
      }
      store.on('history', syncHistoryBtns);
      syncHistoryBtns();
      if (undoBtn) undoBtn.addEventListener('click', function () { store.undo(); });
      if (redoBtn) redoBtn.addEventListener('click', function () { store.redo(); });

      var toolBtns = ws.querySelectorAll('.org-tool-btn');
      function syncToolUI(tool) {
        viewportEl.classList.toggle('mode-pan', tool === 'pan');
        viewportEl.classList.toggle('mode-connect', tool === 'connect');
        for (var i = 0; i < toolBtns.length; i++) {
          var active = toolBtns[i].dataset.tool === tool;
          toolBtns[i].classList.toggle('is-active', active);
          toolBtns[i].classList.toggle('btn--primary', active);
          toolBtns[i].classList.toggle('btn--ghost', !active);
        }
      }
      store.on('tool', syncToolUI);

      for (var i = 0; i < toolBtns.length; i++) {
        (function (btn) {
          btn.addEventListener('click', function () {
            store.setTool(btn.dataset.tool);
          });
        })(toolBtns[i]);
      }

      var shapeBtns = ws.querySelectorAll('[data-add-shape]');
      for (var s = 0; s < shapeBtns.length; s++) {
        (function (btn) {
          btn.addEventListener('click', function () {
            var rect = viewportEl.getBoundingClientRect();
            var center = viewport.clientToWorld(
              rect.left + rect.width / 2,
              rect.top + rect.height / 2
            );
            store.setTool('select');
            store.addNode({
              shape: btn.dataset.addShape,
              x: center.x - 95,
              y: center.y - 38,
            });
          });
        })(shapeBtns[s]);
      }

      var addSecBtn = document.getElementById('org-btn-add-section');
      if (addSecBtn) {
        addSecBtn.addEventListener('click', function () {
          var rect = viewportEl.getBoundingClientRect();
          var center = viewport.clientToWorld(
            rect.left + rect.width / 2,
            rect.top + rect.height / 2
          );
          store.setTool('select');
          store.addSection({ x: center.x - 300, y: center.y - 200 });
        });
      }

      var snapBtn = document.getElementById('org-btn-snap');
      if (snapBtn) {
        snapBtn.addEventListener('click', function () {
          var cur = store.state.datos.settings.snapToGrid;
          store.state.datos.settings.snapToGrid = !cur;
          snapBtn.classList.toggle('is-active', !cur);
          store.markDirty();
        });
      }

      // === Modal Guardar y Versionamiento ===
      var saveBtn = document.getElementById('org-btn-save');
      var saveModal = document.getElementById('org-save-modal');
      var saveClose = document.getElementById('org-save-close');
      var saveCancel = document.getElementById('org-save-cancel');
      var saveConfirm = document.getElementById('org-save-confirm');
      var saveReload = document.getElementById('org-save-reload');
      var saveError = document.getElementById('org-save-error');
      var saveCambios = document.getElementById('org-save-cambios');

      function openSaveModal() {
        if (!saveModal) return;
        var curEl = document.getElementById('org-save-cur-ver');
        var curEl2 = document.getElementById('org-save-cur-ver-2');
        var nextEl = document.getElementById('org-save-next-ver');
        if (curEl) curEl.textContent = store.state.docMeta.version_actual;
        if (curEl2) curEl2.textContent = store.state.docMeta.version_actual;
        if (nextEl) nextEl.textContent = store.state.docMeta.proxima_version;
        if (saveError) {
          saveError.hidden = true;
          saveError.textContent = '';
        }
        if (saveReload) saveReload.hidden = true;
        saveModal.hidden = false;
      }

      function closeSaveModal() {
        if (saveModal) saveModal.hidden = true;
      }

      if (saveBtn) saveBtn.addEventListener('click', openSaveModal);
      if (saveClose) saveClose.addEventListener('click', closeSaveModal);
      if (saveCancel) saveCancel.addEventListener('click', closeSaveModal);
      if (saveReload) {
        saveReload.addEventListener('click', function () {
          closeSaveModal();
          fetchDatos(null);
        });
      }

      if (saveConfirm) {
        saveConfirm.addEventListener('click', function () {
          var csrfInput = ws.querySelector('[name=csrfmiddlewaretoken]');
          var modeRadio = saveModal.querySelector('input[name="org_version_mode"]:checked');
          var incrementar = modeRadio && modeRadio.value === 'bump';
          var payload = {
            revision_edicion: store.state.docMeta.revision_edicion,
            incrementar_version: incrementar,
            cambios: saveCambios ? saveCambios.value : '',
            nombre: store.state.docMeta.nombre,
            descripcion: store.state.docMeta.descripcion,
            clasificacion: store.state.docMeta.clasificacion,
            datos: store.state.datos,
          };

          saveConfirm.disabled = true;
          fetch(guardarUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfInput ? csrfInput.value : '',
              'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(payload),
          })
            .then(function (r) {
              return r.json().then(function (d) { return { status: r.status, ok: r.ok, data: d }; });
            })
            .then(function (res) {
              saveConfirm.disabled = false;
              if (!res.ok) {
                if (saveError) {
                  saveError.textContent = res.data.error || 'No se pudo guardar.';
                  saveError.hidden = false;
                }
                if (res.status === 409 && saveReload) saveReload.hidden = false;
                return;
              }
              store.state.docMeta.version_actual = res.data.version_actual;
              store.state.docMeta.proxima_version = res.data.proxima_version;
              store.state.docMeta.revision_edicion = res.data.revision_edicion;
              store.state.versionVisualizada = res.data.version_actual;
              store.state.versiones = res.data.versiones || store.state.versiones;
              populateVersions();
              var hVer = document.getElementById('org-header-version');
              if (hVer) hVer.textContent = res.data.version_actual;
              store.clearDirty();
              if (saveCambios) saveCambios.value = '';
              closeSaveModal();
              if (dirtyBadge) {
                dirtyBadge.className = 'badge badge--success';
                dirtyBadge.textContent = 'Guardado (v' + res.data.version_actual + ')';
              }
            })
            .catch(function () {
              saveConfirm.disabled = false;
              if (saveError) {
                saveError.textContent = 'Error de red al guardar el organigrama.';
                saveError.hidden = false;
              }
            });
        });
      }

      root.addEventListener('beforeunload', function (ev) {
        if (store.state.dirty) {
          ev.preventDefault();
          ev.returnValue = '';
        }
      });
    }

    // === Modal Exportación Filtrada ===
    var exportModal = document.getElementById('org-export-modal');
    if (puedeExportar && exportarUrl && exportModal) {
      var exportBtn = document.getElementById('org-btn-export');
      var detailExportBtn = document.getElementById('org-detail-export-btn');
      var exportClose = document.getElementById('org-export-close');
      var exportCancel = document.getElementById('org-export-cancel');
      var exportConfirm = document.getElementById('org-export-confirm');
      var exportError = document.getElementById('org-export-error');
      var exportVerInfo = document.getElementById('org-export-version-info');
      var exportDirtyWarn = document.getElementById('org-export-dirty-warn');
      var exportAllChk = document.getElementById('org-export-all');
      var exportTree = document.getElementById('org-export-tree');
      var exportFramesChk = document.getElementById('org-export-frames');
      var exportPaperGroup = document.getElementById('org-export-paper-group');
      var exportPaperSel = document.getElementById('org-export-paper');
      var exportBtnIso = document.getElementById('org-export-btn-iso');
      var exportBtnClear = document.getElementById('org-export-btn-clear');
      var looseChk = null;

      function selectedFormat() {
        var radio = exportModal.querySelector('input[name="org_export_format"]:checked');
        return radio ? radio.value : 'pdf';
      }

      function syncPaperVisibility() {
        if (exportPaperGroup) {
          exportPaperGroup.hidden = selectedFormat() !== 'pdf';
        }
      }

      var fmtRadios = exportModal.querySelectorAll('input[name="org_export_format"]');
      for (var fr = 0; fr < fmtRadios.length; fr++) {
        fmtRadios[fr].addEventListener('change', syncPaperVisibility);
      }

      function syncAllAndParentStates() {
        if (!exportTree || !exportAllChk) return;
        var groups = exportTree.querySelectorAll('.org-export-emp-group');
        var totalSec = 0;
        var checkedSec = 0;

        for (var i = 0; i < groups.length; i++) {
          var parentChk = groups[i].querySelector('.org-export-emp-chk');
          var secChks = groups[i].querySelectorAll('.org-export-sec-chk');
          if (!parentChk || !secChks.length) continue;
          var grpChecked = 0;
          for (var j = 0; j < secChks.length; j++) {
            totalSec += 1;
            if (secChks[j].checked) {
              grpChecked += 1;
              checkedSec += 1;
            }
          }
          parentChk.checked = grpChecked === secChks.length;
          parentChk.indeterminate = grpChecked > 0 && grpChecked < secChks.length;
        }

        var looseOk = !looseChk || looseChk.checked;
        if (totalSec === 0) {
          exportAllChk.indeterminate = false;
        } else if (checkedSec === totalSec && looseOk) {
          exportAllChk.checked = true;
          exportAllChk.indeterminate = false;
        } else if (checkedSec === 0 && (!looseChk || !looseChk.checked)) {
          exportAllChk.checked = false;
          exportAllChk.indeterminate = false;
        } else {
          exportAllChk.checked = false;
          exportAllChk.indeterminate = true;
        }
      }

      function setAllCheckboxes(checked) {
        if (exportTree) {
          var allChks = exportTree.querySelectorAll('input[type="checkbox"]');
          for (var i = 0; i < allChks.length; i++) {
            allChks[i].checked = checked;
            allChks[i].indeterminate = false;
          }
        }
        if (exportAllChk) {
          exportAllChk.checked = checked;
          exportAllChk.indeterminate = false;
        }
      }

      function buildExportTree() {
        if (!exportTree) return;
        exportTree.innerHTML = '';
        looseChk = null;

        var secs = (store.state.datos.sections || []).slice().sort(function (a, b) {
          return (a.order || 0) - (b.order || 0);
        });
        var nodes = store.state.datos.nodes || [];
        var counts = {};
        var looseCount = 0;
        for (var n = 0; n < nodes.length; n++) {
          var sid = nodes[n].section_id;
          if (sid) counts[sid] = (counts[sid] || 0) + 1;
          else looseCount += 1;
        }

        var groups = {};
        var orderKeys = ['PTCA', 'PTH', 'FP', 'FS', ''];
        var labels = {
          PTCA: 'PTCA — Policlínica Táchira',
          PTH: 'PTH — Hospitalización',
          FP: 'FP — Farmacia',
          FS: 'FS — Fuente de Soda',
          '': 'Corporativo / General',
        };
        for (var i = 0; i < secs.length; i++) {
          var k = secs[i].empresa || '';
          if (!groups[k]) groups[k] = [];
          groups[k].push(secs[i]);
        }

        for (var g = 0; g < orderKeys.length; g++) {
          var empKey = orderKeys[g];
          var list = groups[empKey];
          if (!list || !list.length) continue;

          (function (ek, secList) {
            var grpDiv = document.createElement('div');
            grpDiv.className = 'org-export-emp-group';

            var headLbl = document.createElement('label');
            headLbl.className = 'org-export-emp-header';
            var empChk = document.createElement('input');
            empChk.type = 'checkbox';
            empChk.className = 'form-checkbox org-export-emp-chk';
            empChk.value = ek;
            empChk.checked = true;
            var empSpan = document.createElement('span');
            empSpan.textContent = labels[ek] || ek;
            headLbl.appendChild(empChk);
            headLbl.appendChild(empSpan);
            grpDiv.appendChild(headLbl);

            var secListDiv = document.createElement('div');
            secListDiv.className = 'org-export-sec-list';
            var childChks = [];

            for (var s = 0; s < secList.length; s++) {
              var sec = secList[s];
              var sLbl = document.createElement('label');
              sLbl.className = 'org-export-sec-item';
              var sChk = document.createElement('input');
              sChk.type = 'checkbox';
              sChk.className = 'form-checkbox org-export-sec-chk';
              sChk.value = sec.id;
              sChk.checked = true;
              sChk.addEventListener('change', syncAllAndParentStates);
              childChks.push(sChk);

              var dot = document.createElement('span');
              dot.className = 'org-index-dot';
              dot.style.backgroundColor = sec.color || '#2563EB';

              var sName = document.createElement('span');
              sName.textContent = sec.title || 'Sección';

              var sCnt = document.createElement('small');
              sCnt.textContent = String(counts[sec.id] || 0);

              sLbl.appendChild(sChk);
              sLbl.appendChild(dot);
              sLbl.appendChild(sName);
              sLbl.appendChild(sCnt);
              secListDiv.appendChild(sLbl);
            }

            empChk.addEventListener('change', function () {
              for (var c = 0; c < childChks.length; c++) {
                childChks[c].checked = empChk.checked;
              }
              syncAllAndParentStates();
            });

            grpDiv.appendChild(secListDiv);
            exportTree.appendChild(grpDiv);
          })(empKey, list);
        }

        if (looseCount > 0) {
          var lGrp = document.createElement('div');
          lGrp.className = 'org-export-emp-group';
          var lLbl = document.createElement('label');
          lLbl.className = 'org-export-sec-item';
          looseChk = document.createElement('input');
          looseChk.type = 'checkbox';
          looseChk.className = 'form-checkbox';
          looseChk.checked = true;
          looseChk.addEventListener('change', syncAllAndParentStates);
          var lTxt = document.createElement('span');
          lTxt.textContent = 'Figuras generales sin sección asignada';
          var lCnt = document.createElement('small');
          lCnt.textContent = String(looseCount);
          lLbl.appendChild(looseChk);
          lLbl.appendChild(lTxt);
          lLbl.appendChild(lCnt);
          lGrp.appendChild(lLbl);
          exportTree.appendChild(lGrp);
        }

        if (!secs.length && !looseCount) {
          var emptyMsg = document.createElement('div');
          emptyMsg.className = 'org-index-empty';
          emptyMsg.textContent = 'El organigrama no tiene elementos guardados para exportar.';
          exportTree.appendChild(emptyMsg);
        }
      }

      function openExportModal() {
        buildExportTree();
        syncPaperVisibility();
        if (exportVerInfo) {
          var vigenciaTag = store.state.esVersionVigente ? 'Vigente' : 'Histórica (llevará marca OBSOLETO)';
          exportVerInfo.textContent =
            (store.state.docMeta.codigo || 'SGC-ORG-001') +
            ' · Versión v' + store.state.versionVisualizada +
            ' · ' + vigenciaTag;
        }
        if (exportDirtyWarn) {
          exportDirtyWarn.hidden = !store.state.dirty;
        }
        if (exportBtnIso) {
          exportBtnIso.hidden = !store.state.isolatedSectionId;
        }
        if (exportError) {
          exportError.hidden = true;
          exportError.textContent = '';
        }
        if (store.state.isolatedSectionId) {
          setAllCheckboxes(false);
          var isoChk = exportTree
            ? exportTree.querySelector('.org-export-sec-chk[value="' + store.state.isolatedSectionId + '"]')
            : null;
          if (isoChk) isoChk.checked = true;
          syncAllAndParentStates();
        } else {
          setAllCheckboxes(true);
        }
        exportModal.hidden = false;
      }

      function closeExportModal() {
        exportModal.hidden = true;
      }

      if (exportBtn) exportBtn.addEventListener('click', openExportModal);
      if (detailExportBtn) detailExportBtn.addEventListener('click', openExportModal);
      if (exportClose) exportClose.addEventListener('click', closeExportModal);
      if (exportCancel) exportCancel.addEventListener('click', closeExportModal);
      if (exportAllChk) {
        exportAllChk.addEventListener('change', function () {
          setAllCheckboxes(exportAllChk.checked);
        });
      }
      if (exportBtnClear) {
        exportBtnClear.addEventListener('click', function () {
          setAllCheckboxes(false);
        });
      }
      if (exportBtnIso) {
        exportBtnIso.addEventListener('click', function () {
          setAllCheckboxes(false);
          if (store.state.isolatedSectionId && exportTree) {
            var chk = exportTree.querySelector('.org-export-sec-chk[value="' + store.state.isolatedSectionId + '"]');
            if (chk) chk.checked = true;
          }
          syncAllAndParentStates();
        });
      }

      function setExportLoading(loading) {
        exportConfirm.disabled = Boolean(loading);
        var icon = exportConfirm.querySelector('i');
        var label = exportConfirm.querySelector('span');
        if (icon) {
          icon.className = loading ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-download';
        }
        if (label) {
          label.textContent = loading ? 'Generando archivo...' : 'Descargar archivo';
        }
      }

      if (exportConfirm) {
        exportConfirm.addEventListener('click', function () {
          var isAll = Boolean(exportAllChk && exportAllChk.checked && !exportAllChk.indeterminate);
          var sectionIds = [];
          if (exportTree && !isAll) {
            var secChks = exportTree.querySelectorAll('.org-export-sec-chk:checked');
            for (var i = 0; i < secChks.length; i++) {
              sectionIds.push(secChks[i].value);
            }
          }
          var incLoose = isAll ? true : Boolean(looseChk && looseChk.checked);

          if (!isAll && !sectionIds.length && !incLoose) {
            if (exportError) {
              var lblLower = (store.state.docMeta.label || 'Organigrama').toLowerCase();
              exportError.textContent = 'Selecciona al menos una sección, empresa o marca "Todo el ' + lblLower + ' completo".';
              exportError.hidden = false;
            }
            return;
          }

          var csrfInput = ws.querySelector('[name=csrfmiddlewaretoken]');
          var body = {
            formato: selectedFormat(),
            papel: exportPaperSel ? exportPaperSel.value : 'auto',
            version: store.state.versionVisualizada,
            todo: isAll,
            section_ids: sectionIds,
            incluir_sin_seccion: incLoose,
            incluir_marcos: exportFramesChk ? exportFramesChk.checked : true,
          };

          if (exportError) {
            exportError.hidden = true;
            exportError.textContent = '';
          }
          setExportLoading(true);

          fetch(exportarUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRFToken': csrfInput ? csrfInput.value : '',
              'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(body),
          })
            .then(function (r) {
              if (!r.ok) {
                return r.text().then(function (txt) {
                  var msg = 'No se pudo generar el archivo.';
                  try {
                    var d = JSON.parse(txt);
                    if (d && d.error) msg = d.error;
                  } catch (e) {}
                  throw new Error(msg);
                });
              }
              var disp = r.headers.get('Content-Disposition') || '';
              var m = /filename="([^"]+)"/.exec(disp);
              var fname = m ? m[1] : ('SGC-ORG-001.' + body.formato);
              return r.blob().then(function (blob) {
                return { blob: blob, filename: fname };
              });
            })
            .then(function (res) {
              setExportLoading(false);
              var urlObj = root.URL.createObjectURL(res.blob);
              var a = document.createElement('a');
              a.href = urlObj;
              a.download = res.filename;
              document.body.appendChild(a);
              a.click();
              document.body.removeChild(a);
              setTimeout(function () {
                root.URL.revokeObjectURL(urlObj);
              }, 1000);
              closeExportModal();
            })
            .catch(function (err) {
              setExportLoading(false);
              if (exportError) {
                exportError.textContent = err.message || 'Error de red al exportar el organigrama.';
                exportError.hidden = false;
              }
            });
        });
      }

      root.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape' && !exportModal.hidden) {
          closeExportModal();
        }
      });
    }

    // === Carga de datos JSON inicial ===
    function fetchDatos(versionOverride) {
      if (!datosUrl) return;
      if (loadingOverlayEl) loadingOverlayEl.hidden = false;
      var ver = versionOverride !== undefined ? versionOverride : ws.dataset.version;
      var url = ver ? datosUrl + '?version=' + encodeURIComponent(ver) : datosUrl;

      fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
        .then(function (r) {
          return r.json().then(function (d) { return { ok: r.ok, data: d }; });
        })
        .then(function (res) {
          if (loadingOverlayEl) loadingOverlayEl.hidden = true;
          if (!res.ok) {
            if (statusTextEl) statusTextEl.textContent = res.data.error || 'Error al cargar datos.';
            return;
          }
          store.loadPayload(res.data);
        })
        .catch(function () {
          if (loadingOverlayEl) loadingOverlayEl.hidden = true;
          if (statusTextEl) statusTextEl.textContent = 'No se pudo cargar el organigrama.';
        });
    }

    fetchDatos(ws.dataset.version);
  }

  document.addEventListener('DOMContentLoaded', init);
})(window);
