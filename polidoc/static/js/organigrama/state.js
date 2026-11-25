/* === Estado centralizado del Organigrama Interactivo === */
(function (root) {
  'use strict';

  var PoliOrg = (root.PoliOrg = root.PoliOrg || {});

  function uid(prefix) {
    return (
      prefix +
      '_' +
      Date.now().toString(36) +
      '_' +
      Math.random().toString(36).slice(2, 7)
    );
  }

  function clone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function createStore(options) {
    var listeners = {};
    var history = { past: [], future: [], limit: 60 };
    var clipboard = null;
    var pasteStep = 0;

    var state = {
      mode: (options && options.mode) || 'viewer',
      docMeta: {
        uuid: (options && options.docUuid) || '',
        kind: (options && options.docKind) || 'organigrama',
        label: (options && options.docLabel) || 'Organigrama',
        empresa: (options && options.docEmpresa) || '',
        codigo: (options && options.docCodigo) || '',
        nombre: (options && options.docNombre) || '',
        descripcion: (options && options.docDescripcion) || '',
        clasificacion: (options && options.docClasificacion) || 'publico',
        version_actual: (options && options.versionActual) || '1',
        proxima_version: (options && options.proximaVersion) || '2',
        revision_edicion: Number((options && options.revisionEdicion) || 0),
      },
      versionVisualizada: (options && options.versionVisualizada) || '1',
      esVersionVigente: true,
      versiones: [],
      departamentos: (options && options.departamentos) || [],
      empresas: (options && options.empresas) || [],
      datos: {
        schema_version: 1,
        settings: { gridSize: 20, snapToGrid: true },
        sections: [],
        nodes: [],
        edges: [],
      },
      selection: null, // { type: 'node'|'multi'|'edge'|'section', id?: string, ids?: string[] }
      selectedNodeIds: [],
      tool: 'select', // 'select'|'pan'|'connect'
      connectFrom: null,
      isolatedSectionId: null,
      showCrossEdges: true,
      searchQuery: '',
      dirty: false,
    };

    function emit(eventName, detail) {
      var list = listeners[eventName] || [];
      for (var i = 0; i < list.length; i++) {
        list[i](detail, state);
      }
      var allList = listeners['*'] || [];
      for (var j = 0; j < allList.length; j++) {
        allList[j](eventName, detail, state);
      }
    }

    function on(eventName, fn) {
      if (!listeners[eventName]) listeners[eventName] = [];
      listeners[eventName].push(fn);
      return function () {
        listeners[eventName] = (listeners[eventName] || []).filter(function (f) {
          return f !== fn;
        });
      };
    }

    function emitHistory() {
      emit('history', {
        canUndo: history.past.length > 0,
        canRedo: history.future.length > 0,
      });
    }

    function commitCheckpoint() {
      if (state.mode !== 'editor') return;
      var serialized = JSON.stringify(state.datos);
      var last = history.past.length ? history.past[history.past.length - 1] : null;
      if (last && last.raw === serialized) return;
      history.past.push({ raw: serialized });
      if (history.past.length > history.limit) {
        history.past.shift();
      }
      history.future = [];
      emitHistory();
    }

    function sanitizeSelectionAfterRestore() {
      if (state.selectedNodeIds.length) {
        var validIds = state.selectedNodeIds.filter(function (id) {
          return Boolean(findNode(id));
        });
        state.selectedNodeIds = validIds;
        if (!validIds.length) {
          state.selection = null;
        } else if (validIds.length === 1) {
          state.selection = { type: 'node', id: validIds[0] };
        } else {
          state.selection = { type: 'multi', ids: validIds.slice() };
        }
      } else if (state.selection) {
        if (state.selection.type === 'edge' && !findEdge(state.selection.id)) {
          state.selection = null;
        } else if (state.selection.type === 'section' && !findSection(state.selection.id)) {
          state.selection = null;
        }
      }
      if (state.isolatedSectionId && !findSection(state.isolatedSectionId)) {
        state.isolatedSectionId = null;
      }
    }

    function undo() {
      if (!history.past.length) return false;
      history.future.push({ raw: JSON.stringify(state.datos) });
      var prev = history.past.pop();
      state.datos = JSON.parse(prev.raw);
      sanitizeSelectionAfterRestore();
      markDirty();
      emit('sectionsChanged');
      emit('selection', state.selection);
      emitHistory();
      emit('render');
      return true;
    }

    function redo() {
      if (!history.future.length) return false;
      history.past.push({ raw: JSON.stringify(state.datos) });
      var nxt = history.future.pop();
      state.datos = JSON.parse(nxt.raw);
      sanitizeSelectionAfterRestore();
      markDirty();
      emit('sectionsChanged');
      emit('selection', state.selection);
      emitHistory();
      emit('render');
      return true;
    }

    function snap(val) {
      var grid = (state.datos.settings && state.datos.settings.gridSize) || 20;
      if (!state.datos.settings || !state.datos.settings.snapToGrid) {
        return Math.round(val);
      }
      return Math.round(val / grid) * grid;
    }

    function findSection(id) {
      return state.datos.sections.find(function (s) {
        return s.id === id;
      }) || null;
    }

    function findNode(id) {
      return state.datos.nodes.find(function (n) {
        return n.id === id;
      }) || null;
    }

    function findEdge(id) {
      return state.datos.edges.find(function (e) {
        return e.id === id;
      }) || null;
    }

    function loadPayload(payload) {
      if (payload.documento) {
        state.docMeta = Object.assign({}, state.docMeta, payload.documento);
      }
      state.versionVisualizada =
        payload.version_visualizada || state.docMeta.version_actual;
      state.esVersionVigente = payload.es_version_vigente !== false;
      state.versiones = payload.versiones || [];
      if (payload.departamentos) state.departamentos = payload.departamentos;
      if (payload.empresas) state.empresas = payload.empresas;
      var d = payload.datos || {};
      state.datos = {
        schema_version: 1,
        settings: Object.assign({ gridSize: 20, snapToGrid: true }, d.settings || {}),
        sections: clone(d.sections || []),
        nodes: clone(d.nodes || []),
        edges: clone(d.edges || []),
      };
      history.past = [];
      history.future = [];
      state.selection = null;
      state.selectedNodeIds = [];
      state.connectFrom = null;
      state.dirty = false;
      emit('loaded', state);
      emit('selection', state.selection);
      emitHistory();
      emit('render');
    }

    function markDirty() {
      if (!state.dirty) {
        state.dirty = true;
        emit('dirty', true);
      }
    }

    function clearDirty() {
      state.dirty = false;
      emit('dirty', false);
    }

    function setTool(tool) {
      state.tool = tool;
      state.connectFrom = null;
      emit('tool', tool);
    }

    function select(type, id) {
      if (!type || !id) {
        state.selection = null;
        state.selectedNodeIds = [];
      } else if (type === 'node') {
        state.selectedNodeIds = [id];
        state.selection = { type: 'node', id: id };
      } else {
        state.selectedNodeIds = [];
        state.selection = { type: type, id: id };
      }
      emit('selection', state.selection);
      emit('render');
    }

    function selectNodes(ids) {
      var seen = {};
      var valid = [];
      for (var i = 0; i < (ids || []).length; i++) {
        var id = ids[i];
        if (id && !seen[id] && findNode(id)) {
          seen[id] = true;
          valid.push(id);
        }
      }
      if (!valid.length) {
        select(null, null);
      } else if (valid.length === 1) {
        select('node', valid[0]);
      } else {
        state.selectedNodeIds = valid;
        state.selection = { type: 'multi', ids: valid.slice() };
        emit('selection', state.selection);
        emit('render');
      }
    }

    function toggleNodeSelection(id) {
      if (!findNode(id)) return;
      var cur = state.selectedNodeIds.slice();
      var idx = cur.indexOf(id);
      if (idx === -1) {
        cur.push(id);
      } else {
        cur.splice(idx, 1);
      }
      selectNodes(cur);
    }

    function isNodeSelected(id) {
      return state.selectedNodeIds.indexOf(id) !== -1;
    }

    function selectAllVisibleNodes() {
      var iso = state.isolatedSectionId;
      var ids = state.datos.nodes
        .filter(function (n) {
          return !iso || n.section_id === iso;
        })
        .map(function (n) {
          return n.id;
        });
      selectNodes(ids);
    }

    function setIsolatedSection(secId) {
      state.isolatedSectionId =
        state.isolatedSectionId === secId ? null : secId;
      emit('isolation', state.isolatedSectionId);
      emit('render');
    }

    function toggleCrossEdges() {
      state.showCrossEdges = !state.showCrossEdges;
      emit('crossEdges', state.showCrossEdges);
      emit('render');
    }

    function setSearchQuery(q) {
      state.searchQuery = (q || '').trim().toLowerCase();
      emit('search', state.searchQuery);
      emit('render');
    }

    function containingSectionForPoint(cx, cy) {
      var best = null;
      var bestArea = Infinity;
      for (var i = 0; i < state.datos.sections.length; i++) {
        var s = state.datos.sections[i];
        if (cx >= s.x && cx <= s.x + s.w && cy >= s.y && cy <= s.y + s.h) {
          var area = s.w * s.h;
          if (area < bestArea) {
            bestArea = area;
            best = s;
          }
        }
      }
      return best ? best.id : null;
    }

    function addSection(opts, options) {
      if (!options || !options.skipHistory) commitCheckpoint();
      var id = uid('sec');
      var idx = state.datos.sections.length;
      var sec = Object.assign(
        {
          id: id,
          title: 'Nueva Sección ' + (idx + 1),
          empresa: state.docMeta.empresa || '',
          departamento_id: null,
          x: snap((opts && opts.x) || 60 + idx * 40),
          y: snap((opts && opts.y) || 60 + idx * 40),
          w: 640,
          h: 420,
          color: '#2563EB',
          order: idx,
        },
        opts || {}
      );
      state.datos.sections.push(sec);
      markDirty();
      select('section', sec.id);
      emit('sectionsChanged');
      return sec;
    }

    function createNodeRecord(opts) {
      var id = uid('node');
      var shape = (opts && opts.shape) || 'rounded';
      var isText = shape === 'text';
      var defaultW = isText ? 180 : 190;
      var defaultH = isText ? 48 : shape === 'diamond' ? 110 : 76;
      var node = {
        id: id,
        section_id: (opts && opts.section_id) || null,
        shape: shape,
        x: snap((opts && opts.x) || 120),
        y: snap((opts && opts.y) || 120),
        w: snap((opts && opts.w) || defaultW),
        h: snap((opts && opts.h) || defaultH),
        text: (opts && opts.text) || (isText ? 'Etiqueta de texto' : 'Nuevo cargo / unidad'),
        zIndex: (state.datos.nodes.length ? state.datos.nodes.length + 1 : 1),
        style: Object.assign(
          {
            bgColor: isText ? 'transparent' : 'auto',
            textColor: 'auto',
            borderColor: isText ? 'transparent' : 'auto',
            borderWidth: isText ? 0 : 2,
            borderStyle: 'solid',
            borderRadius: shape === 'pill' ? 40 : shape === 'rect' ? 2 : 10,
            fontWeight: isText ? 'bold' : 'normal',
            fontStyle: 'normal',
            fontSize: 13,
            textAlign: 'center',
          },
          (opts && opts.style) || {}
        ),
      };
      if (!node.section_id) {
        node.section_id = containingSectionForPoint(
          node.x + node.w / 2,
          node.y + node.h / 2
        );
      }
      return node;
    }

    function addNode(opts, options) {
      if (!options || !options.skipHistory) commitCheckpoint();
      var node = createNodeRecord(opts);
      state.datos.nodes.push(node);
      markDirty();
      select('node', node.id);
      return node;
    }

    function addEdge(fromId, toId, opts, options) {
      if (!fromId || !toId || fromId === toId) return null;
      if (!options || !options.skipHistory) commitCheckpoint();
      var edge = Object.assign(
        {
          id: uid('edge'),
          from: fromId,
          to: toId,
          fromAnchor: 'bottom',
          toAnchor: 'top',
          routing: 'orthogonal',
          color: 'auto',
          width: 2,
          style: 'solid',
          arrowEnd: true,
          label: '',
        },
        opts || {}
      );
      state.datos.edges.push(edge);
      markDirty();
      select('edge', edge.id);
      return edge;
    }

    function updateNode(id, patch, options) {
      var n = findNode(id);
      if (!n) return null;
      if (!options || !options.skipHistory) commitCheckpoint();
      if (patch.style) {
        n.style = Object.assign({}, n.style, patch.style);
        patch = Object.assign({}, patch);
        delete patch.style;
      }
      Object.assign(n, patch);
      if (!options || !options.skipDirty) markDirty();
      if (!options || !options.silent) emit('render');
      return n;
    }

    function moveNodesDelta(ids, dx, dy, options) {
      if (!ids || !ids.length) return false;
      if (!options || !options.skipHistory) commitCheckpoint();
      var changed = false;
      for (var i = 0; i < ids.length; i++) {
        var n = findNode(ids[i]);
        if (n) {
          n.x = Math.round(n.x + dx);
          n.y = Math.round(n.y + dy);
          changed = true;
        }
      }
      if (changed) {
        if (!options || !options.skipDirty) markDirty();
        if (!options || !options.silent) emit('render');
      }
      return changed;
    }

    function updateNodesBatch(ids, patch, options) {
      if (!ids || !ids.length) return false;
      if (!options || !options.skipHistory) commitCheckpoint();
      for (var i = 0; i < ids.length; i++) {
        var n = findNode(ids[i]);
        if (!n) continue;
        if (patch.style) {
          n.style = Object.assign({}, n.style, patch.style);
        }
        var rest = Object.assign({}, patch);
        delete rest.style;
        Object.assign(n, rest);
      }
      if (!options || !options.skipDirty) markDirty();
      if (!options || !options.silent) emit('render');
      return true;
    }

    function alignSelectedNodes(mode) {
      var nodes = state.selectedNodeIds.map(findNode).filter(Boolean);
      if (nodes.length < 2) return false;
      commitCheckpoint();

      var minX = Infinity;
      var maxX = -Infinity;
      var minY = Infinity;
      var maxY = -Infinity;
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (n.x < minX) minX = n.x;
        if (n.x + n.w > maxX) maxX = n.x + n.w;
        if (n.y < minY) minY = n.y;
        if (n.y + n.h > maxY) maxY = n.y + n.h;
      }
      var midX = (minX + maxX) / 2;
      var midY = (minY + maxY) / 2;

      for (var j = 0; j < nodes.length; j++) {
        var item = nodes[j];
        if (mode === 'left') item.x = minX;
        else if (mode === 'right') item.x = maxX - item.w;
        else if (mode === 'centerH') item.x = Math.round(midX - item.w / 2);
        else if (mode === 'top') item.y = minY;
        else if (mode === 'bottom') item.y = maxY - item.h;
        else if (mode === 'centerV') item.y = Math.round(midY - item.h / 2);
      }
      markDirty();
      emit('render');
      return true;
    }

    function updateEdge(id, patch, options) {
      var e = findEdge(id);
      if (!e) return null;
      if (!options || !options.skipHistory) commitCheckpoint();
      Object.assign(e, patch);
      if (!options || !options.skipDirty) markDirty();
      if (!options || !options.silent) emit('render');
      return e;
    }

    function updateSection(id, patch, options) {
      var s = findSection(id);
      if (!s) return null;
      if (!options || !options.skipHistory) commitCheckpoint();
      Object.assign(s, patch);
      if (!options || !options.skipDirty) markDirty();
      if (!options || !options.silent) {
        emit('render');
        emit('sectionsChanged');
      }
      return s;
    }

    function deleteSelection() {
      if (state.selectedNodeIds.length > 0) {
        commitCheckpoint();
        var toDelete = {};
        for (var i = 0; i < state.selectedNodeIds.length; i++) {
          toDelete[state.selectedNodeIds[i]] = true;
        }
        state.datos.nodes = state.datos.nodes.filter(function (n) {
          return !toDelete[n.id];
        });
        state.datos.edges = state.datos.edges.filter(function (e) {
          return !toDelete[e.from] && !toDelete[e.to];
        });
        markDirty();
        select(null, null);
        return true;
      }

      if (!state.selection) return false;
      var type = state.selection.type;
      var id = state.selection.id;
      commitCheckpoint();
      if (type === 'edge') {
        state.datos.edges = state.datos.edges.filter(function (e) {
          return e.id !== id;
        });
      } else if (type === 'section') {
        state.datos.sections = state.datos.sections.filter(function (s) {
          return s.id !== id;
        });
        state.datos.nodes.forEach(function (n) {
          if (n.section_id === id) n.section_id = null;
        });
        if (state.isolatedSectionId === id) state.isolatedSectionId = null;
        emit('sectionsChanged');
      }
      markDirty();
      select(null, null);
      return true;
    }

    function buildSelectionSnapshot() {
      if (!state.selectedNodeIds.length) return null;
      var setIds = {};
      var nodes = [];
      for (var i = 0; i < state.selectedNodeIds.length; i++) {
        var n = findNode(state.selectedNodeIds[i]);
        if (n) {
          setIds[n.id] = true;
          nodes.push(clone(n));
        }
      }
      if (!nodes.length) return null;
      var edges = state.datos.edges
        .filter(function (e) {
          return setIds[e.from] && setIds[e.to];
        })
        .map(clone);
      return { nodes: nodes, edges: edges };
    }

    function insertClonedBatch(snapObj, offsetPx) {
      if (!snapObj || !snapObj.nodes.length) return [];
      commitCheckpoint();
      var idMap = {};
      var newIds = [];
      for (var i = 0; i < snapObj.nodes.length; i++) {
        var orig = snapObj.nodes[i];
        var oldId = orig.id;
        var copy = clone(orig);
        delete copy.id;
        copy.x = snap(orig.x + offsetPx);
        copy.y = snap(orig.y + offsetPx);
        var created = createNodeRecord(copy);
        state.datos.nodes.push(created);
        idMap[oldId] = created.id;
        newIds.push(created.id);
      }
      for (var j = 0; j < (snapObj.edges || []).length; j++) {
        var e = snapObj.edges[j];
        var nf = idMap[e.from];
        var nt = idMap[e.to];
        if (nf && nt && nf !== nt) {
          var ce = clone(e);
          ce.id = uid('edge');
          ce.from = nf;
          ce.to = nt;
          state.datos.edges.push(ce);
        }
      }
      markDirty();
      selectNodes(newIds);
      return newIds;
    }

    function copySelection() {
      var snapObj = buildSelectionSnapshot();
      if (!snapObj) return false;
      clipboard = snapObj;
      pasteStep = 0;
      return true;
    }

    function pasteClipboard() {
      if (!clipboard || !clipboard.nodes.length) return [];
      pasteStep += 1;
      var off = snap(24 * pasteStep) || 24 * pasteStep;
      return insertClonedBatch(clipboard, off);
    }

    function duplicateSelection() {
      var snapObj = buildSelectionSnapshot();
      if (!snapObj) return [];
      return insertClonedBatch(snapObj, snap(24) || 24);
    }

    function duplicateSelectedNode() {
      var ids = duplicateSelection();
      return ids.length ? findNode(ids[0]) : null;
    }

    function computeBounds(sectionId) {
      var secs = sectionId
        ? state.datos.sections.filter(function (s) {
            return s.id === sectionId;
          })
        : state.datos.sections;
      var nods = sectionId
        ? state.datos.nodes.filter(function (n) {
            return n.section_id === sectionId;
          })
        : state.datos.nodes;

      var items = secs.concat(nods);
      if (!items.length) return null;

      var minX = Infinity;
      var minY = Infinity;
      var maxX = -Infinity;
      var maxY = -Infinity;
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (it.x < minX) minX = it.x;
        if (it.y < minY) minY = it.y;
        if (it.x + it.w > maxX) maxX = it.x + it.w;
        if (it.y + it.h > maxY) maxY = it.y + it.h;
      }
      return { minX: minX, minY: minY, maxX: maxX, maxY: maxY };
    }

    return {
      state: state,
      on: on,
      emit: emit,
      snap: snap,
      commitCheckpoint: commitCheckpoint,
      undo: undo,
      redo: redo,
      canUndo: function () {
        return history.past.length > 0;
      },
      canRedo: function () {
        return history.future.length > 0;
      },
      loadPayload: loadPayload,
      markDirty: markDirty,
      clearDirty: clearDirty,
      setTool: setTool,
      select: select,
      selectNodes: selectNodes,
      toggleNodeSelection: toggleNodeSelection,
      isNodeSelected: isNodeSelected,
      selectAllVisibleNodes: selectAllVisibleNodes,
      setIsolatedSection: setIsolatedSection,
      toggleCrossEdges: toggleCrossEdges,
      setSearchQuery: setSearchQuery,
      containingSectionForPoint: containingSectionForPoint,
      findSection: findSection,
      findNode: findNode,
      findEdge: findEdge,
      addSection: addSection,
      addNode: addNode,
      addEdge: addEdge,
      updateNode: updateNode,
      moveNodesDelta: moveNodesDelta,
      updateNodesBatch: updateNodesBatch,
      alignSelectedNodes: alignSelectedNodes,
      updateEdge: updateEdge,
      updateSection: updateSection,
      deleteSelection: deleteSelection,
      copySelection: copySelection,
      pasteClipboard: pasteClipboard,
      duplicateSelection: duplicateSelection,
      duplicateSelectedNode: duplicateSelectedNode,
      computeBounds: computeBounds,
    };
  }

  PoliOrg.createStore = createStore;
})(window);
