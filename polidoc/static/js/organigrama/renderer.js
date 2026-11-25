/* === Renderizador DOM + SVG único para Organigrama POLIDOC === */
(function (root) {
  'use strict';

  var PoliOrg = (root.PoliOrg = root.PoliOrg || {});
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var ANCHORS = ['top', 'right', 'bottom', 'left'];
  var RESIZE_DIRS = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

  function resolveColor(color, role) {
    if (color === 'transparent') return 'transparent';
    if (!color || color === 'auto') {
      if (role === 'bg') return 'var(--color-surface)';
      if (role === 'text') return 'var(--color-text)';
      if (role === 'border') return 'var(--color-border)';
      if (role === 'edge') return 'var(--color-muted)';
    }
    return color;
  }

  function hexToRgba(hex, alpha) {
    if (!hex || typeof hex !== 'string' || hex[0] !== '#' || hex.length !== 7) {
      return 'rgba(37, 99, 235, ' + alpha + ')';
    }
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    return 'rgba(' + r + ', ' + g + ', ' + b + ', ' + alpha + ')';
  }

  function anchorPoint(node, anchor) {
    if (anchor === 'top') return { x: node.x + node.w / 2, y: node.y };
    if (anchor === 'right') return { x: node.x + node.w, y: node.y + node.h / 2 };
    if (anchor === 'left') return { x: node.x, y: node.y + node.h / 2 };
    return { x: node.x + node.w / 2, y: node.y + node.h };
  }

  function anchorDir(anchor) {
    if (anchor === 'top') return { x: 0, y: -1 };
    if (anchor === 'right') return { x: 1, y: 0 };
    if (anchor === 'left') return { x: -1, y: 0 };
    return { x: 0, y: 1 };
  }

  function buildEdgeGeometry(srcNode, dstNode, edge) {
    var p1 = anchorPoint(srcNode, edge.fromAnchor);
    var p2 = anchorPoint(dstNode, edge.toAnchor);

    if (edge.routing === 'straight') {
      return {
        d: 'M ' + p1.x + ' ' + p1.y + ' L ' + p2.x + ' ' + p2.y,
        labelPos: { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 },
      };
    }

    var d1 = anchorDir(edge.fromAnchor);
    var d2 = anchorDir(edge.toAnchor);
    var stub = 22;
    var s1 = { x: p1.x + d1.x * stub, y: p1.y + d1.y * stub };
    var s2 = { x: p2.x + d2.x * stub, y: p2.y + d2.y * stub };

    var pts = [p1, s1];
    var v1 = d1.y !== 0;
    var v2 = d2.y !== 0;

    if (v1 && v2) {
      var midY = (s1.y + s2.y) / 2;
      if ((d1.y > 0 && p2.y < s1.y) || (d1.y < 0 && p2.y > s1.y)) {
        var sideX = Math.abs(s1.x - s2.x) < 40 ? s1.x + 80 : (s1.x + s2.x) / 2;
        pts.push({ x: sideX, y: s1.y }, { x: sideX, y: s2.y });
      } else {
        pts.push({ x: s1.x, y: midY }, { x: s2.x, y: midY });
      }
    } else if (!v1 && !v2) {
      var midX = (s1.x + s2.x) / 2;
      if ((d1.x > 0 && p2.x < s1.x) || (d1.x < 0 && p2.x > s1.x)) {
        var sideY = Math.abs(s1.y - s2.y) < 40 ? s1.y + 80 : (s1.y + s2.y) / 2;
        pts.push({ x: s1.x, y: sideY }, { x: s2.x, y: sideY });
      } else {
        pts.push({ x: midX, y: s1.y }, { x: midX, y: s2.y });
      }
    } else if (v1 && !v2) {
      pts.push({ x: s1.x, y: s2.y });
    } else {
      pts.push({ x: s2.x, y: s1.y });
    }

    pts.push(s2, p2);

    var d = 'M ' + pts[0].x + ' ' + pts[0].y;
    for (var i = 1; i < pts.length; i++) {
      d += ' L ' + pts[i].x + ' ' + pts[i].y;
    }

    var midIdx = Math.floor(pts.length / 2);
    var a = pts[midIdx - 1];
    var b = pts[midIdx];
    return {
      d: d,
      labelPos: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
    };
  }

  function svgEl(tag, attrs) {
    var el = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      var keys = Object.keys(attrs);
      for (var i = 0; i < keys.length; i++) {
        el.setAttribute(keys[i], String(attrs[keys[i]]));
      }
    }
    return el;
  }

  function createRenderer(opts) {
    var store = opts.store;
    var sectionsLayer = opts.sectionsLayer;
    var edgesLayer = opts.edgesLayer;
    var nodesLayer = opts.nodesLayer;
    var draftLayer = opts.draftLayer;
    var emptyHintEl = opts.emptyHintEl;

    function markerIdForColor(resolvedStroke) {
      return 'org-arr-' + resolvedStroke.replace(/[^a-zA-Z0-9_-]/g, '_');
    }

    function ensureMarker(defsEl, resolvedStroke) {
      var id = markerIdForColor(resolvedStroke);
      if (defsEl.querySelector('#' + id)) return id;
      var marker = svgEl('marker', {
        id: id,
        viewBox: '0 0 10 10',
        refX: '9',
        refY: '5',
        markerWidth: '7',
        markerHeight: '7',
        orient: 'auto-start-reverse',
        markerUnits: 'strokeWidth',
      });
      var path = svgEl('path', { d: 'M 0 1 L 10 5 L 0 9 z' });
      path.style.fill = resolvedStroke;
      marker.appendChild(path);
      defsEl.appendChild(marker);
      return id;
    }

    function renderSections(state) {
      sectionsLayer.innerHTML = '';
      var isEditor = state.mode === 'editor';
      var isolated = state.isolatedSectionId;

      var sorted = state.datos.sections.slice().sort(function (a, b) {
        return (a.order || 0) - (b.order || 0);
      });

      for (var i = 0; i < sorted.length; i++) {
        var s = sorted[i];
        var el = document.createElement('div');
        el.className = 'org-section';
        el.dataset.sectionId = s.id;
        if (state.selection && state.selection.type === 'section' && state.selection.id === s.id) {
          el.classList.add('is-selected');
        }
        if (isolated && isolated !== s.id) {
          el.classList.add('is-dimmed');
        }
        el.style.left = s.x + 'px';
        el.style.top = s.y + 'px';
        el.style.width = s.w + 'px';
        el.style.height = s.h + 'px';
        el.style.borderColor = s.color || '#2563EB';
        el.style.backgroundColor = hexToRgba(s.color || '#2563EB', 0.06);

        var header = document.createElement('div');
        header.className = 'org-section-header';
        header.dataset.sectionHeaderId = s.id;
        header.style.backgroundColor = s.color || '#2563EB';

        if (s.empresa) {
          var empTag = document.createElement('span');
          empTag.className = 'org-section-emp';
          empTag.textContent = s.empresa;
          header.appendChild(empTag);
        }
        var titleSpan = document.createElement('span');
        titleSpan.textContent = s.title || 'Sección';
        header.appendChild(titleSpan);
        el.appendChild(header);

        if (isEditor && state.selection && state.selection.type === 'section' && state.selection.id === s.id) {
          for (var r = 0; r < RESIZE_DIRS.length; r++) {
            var handle = document.createElement('div');
            handle.className = 'org-resize-handle org-resize-handle--' + RESIZE_DIRS[r];
            handle.dataset.resizeTarget = 'section';
            handle.dataset.resizeId = s.id;
            handle.dataset.resizeDir = RESIZE_DIRS[r];
            el.appendChild(handle);
          }
        }
        sectionsLayer.appendChild(el);
      }
    }

    function renderEdges(state) {
      edgesLayer.innerHTML = '';
      var defs = svgEl('defs');
      edgesLayer.appendChild(defs);

      var nodeMap = {};
      for (var i = 0; i < state.datos.nodes.length; i++) {
        var n = state.datos.nodes[i];
        nodeMap[n.id] = n;
      }

      var isolated = state.isolatedSectionId;
      var showCross = state.showCrossEdges !== false;

      var visibleNodes = isolated
        ? state.datos.nodes.filter(function (nd) { return nd.section_id === isolated; })
        : state.datos.nodes;
      var visibleSections = isolated
        ? state.datos.sections.filter(function (sc) { return sc.id === isolated; })
        : state.datos.sections;

      var visibleEdges = [];
      for (var eIdx = 0; eIdx < state.datos.edges.length; eIdx++) {
        var candidate = state.datos.edges[eIdx];
        var cSrc = nodeMap[candidate.from];
        var cDst = nodeMap[candidate.to];
        if (!cSrc || !cDst) continue;
        if (isolated && (cSrc.section_id !== isolated || cDst.section_id !== isolated)) {
          continue;
        }
        if (!showCross && cSrc.section_id !== cDst.section_id) {
          continue;
        }
        visibleEdges.push(candidate);
      }

      var routedMap =
        typeof PoliOrg.routeAllEdges === 'function'
          ? PoliOrg.routeAllEdges(visibleNodes, visibleSections, visibleEdges, {
              isolatedSectionId: isolated,
              mostrarMarcos: true,
            })
          : null;

      for (var j = 0; j < visibleEdges.length; j++) {
        var edge = visibleEdges[j];
        var src = nodeMap[edge.from];
        var dst = nodeMap[edge.to];
        if (!src || !dst) continue;

        var geom;
        if (routedMap && routedMap[edge.id] && typeof PoliOrg.buildRoundedSvgPath === 'function') {
          var rGeom = routedMap[edge.id];
          geom = {
            d: PoliOrg.buildRoundedSvgPath(rGeom.points, rGeom.crossings, 8, 6),
            labelPos: rGeom.labelPos,
          };
        } else {
          geom = buildEdgeGeometry(src, dst, edge);
        }

        var selected =
          state.selection && state.selection.type === 'edge' && state.selection.id === edge.id;
        var isCross = src.section_id !== dst.section_id;

        var g = svgEl('g', {
          class:
            'org-edge-group' +
            (selected ? ' is-selected' : '') +
            (isCross ? ' is-cross-edge' : ''),
          'data-edge-id': edge.id,
          'data-from-id': edge.from,
          'data-to-id': edge.to,
          'data-cross': isCross ? '1' : '0',
        });

        var hitPath = svgEl('path', { d: geom.d, class: 'org-edge-hit' });
        g.appendChild(hitPath);

        var wVal = Number(edge.width || 2);
        var casingPath = svgEl('path', {
          d: geom.d,
          class: 'org-edge-casing',
          'stroke-width': wVal + 4,
        });
        g.appendChild(casingPath);

        var strokeColor = selected
          ? 'var(--color-primary)'
          : resolveColor(edge.color, 'edge');
        var attrs = {
          d: geom.d,
          class: 'org-edge-path',
          'stroke-width': wVal,
        };
        if (edge.style === 'dashed') {
          attrs['stroke-dasharray'] = '8 5';
        } else if (edge.style === 'dotted') {
          attrs['stroke-dasharray'] = '2 4';
        }
        if (edge.arrowEnd) {
          var mId = ensureMarker(defs, strokeColor);
          attrs['marker-end'] = 'url(#' + mId + ')';
        }
        var linePath = svgEl('path', attrs);
        linePath.style.stroke = strokeColor;
        g.appendChild(linePath);

        if (edge.label) {
          var textW = Math.max(28, edge.label.length * 7 + 12);
          var bg = svgEl('rect', {
            x: geom.labelPos.x - textW / 2,
            y: geom.labelPos.y - 10,
            width: textW,
            height: 20,
            rx: 4,
            class: 'org-edge-label-bg',
          });
          var lbl = svgEl('text', {
            x: geom.labelPos.x,
            y: geom.labelPos.y,
            class: 'org-edge-label-text',
          });
          lbl.textContent = edge.label;
          g.appendChild(bg);
          g.appendChild(lbl);
        }

        edgesLayer.appendChild(g);
      }
    }

    function renderNodes(state) {
      nodesLayer.innerHTML = '';
      var isEditor = state.mode === 'editor';
      var isolated = state.isolatedSectionId;
      var query = state.searchQuery || '';

      var sorted = state.datos.nodes.slice().sort(function (a, b) {
        return (a.zIndex || 1) - (b.zIndex || 1);
      });

      for (var i = 0; i < sorted.length; i++) {
        var n = sorted[i];
        var st = n.style || {};
        var el = document.createElement('div');
        el.className = 'org-node org-node--' + (n.shape || 'rounded');
        el.dataset.nodeId = n.id;

        var selected = store.isNodeSelected(n.id);
        if (selected) el.classList.add('is-selected');
        if (isolated && n.section_id !== isolated) el.classList.add('is-dimmed');
        if (query && (n.text || '').toLowerCase().indexOf(query) !== -1) {
          el.classList.add('is-search-match');
        }

        el.style.left = n.x + 'px';
        el.style.top = n.y + 'px';
        el.style.width = n.w + 'px';
        el.style.height = n.h + 'px';
        el.style.zIndex = String((n.zIndex || 1) + 5);
        el.style.color = resolveColor(st.textColor, 'text');

        var bWidth = st.borderWidth !== undefined ? Number(st.borderWidth) : 2;
        var bStyle = st.borderStyle || 'solid';
        if (n.shape === 'diamond') {
          var vw = Math.max(40, n.w || 180);
          var vh = Math.max(28, n.h || 110);
          var pad = bWidth > 0 ? Math.max(1, bWidth / 2) : 0;
          var pts = [
            vw / 2 + ',' + pad,
            vw - pad + ',' + vh / 2,
            vw / 2 + ',' + (vh - pad),
            pad + ',' + vh / 2,
          ].join(' ');
          var shapeSvg = svgEl('svg', {
            class: 'org-node-shape-svg',
            viewBox: '0 0 ' + vw + ' ' + vh,
            preserveAspectRatio: 'none',
            'aria-hidden': 'true',
          });
          var polyAttrs = {
            points: pts,
            'stroke-width': bWidth,
            'stroke-linejoin': 'round',
          };
          if (bStyle === 'dashed') polyAttrs['stroke-dasharray'] = '8 5';
          else if (bStyle === 'dotted') polyAttrs['stroke-dasharray'] = '2 4';
          var poly = svgEl('polygon', polyAttrs);
          poly.style.fill = resolveColor(st.bgColor, 'bg');
          poly.style.stroke = bWidth > 0 ? resolveColor(st.borderColor, 'border') : 'none';
          shapeSvg.appendChild(poly);
          el.appendChild(shapeSvg);
        } else {
          el.style.backgroundColor = resolveColor(st.bgColor, 'bg');
          el.style.borderColor = resolveColor(st.borderColor, 'border');
          el.style.borderWidth = bWidth + 'px';
          el.style.borderStyle = bStyle;
          if (n.shape === 'rounded') {
            el.style.borderRadius = (st.borderRadius !== undefined ? st.borderRadius : 10) + 'px';
          }
        }

        var textEl = document.createElement('div');
        textEl.className = 'org-node-text';
        textEl.textContent = n.text || '';
        textEl.style.fontWeight = st.fontWeight || 'normal';
        textEl.style.fontStyle = st.fontStyle || 'normal';
        textEl.style.fontSize = (st.fontSize || 13) + 'px';
        textEl.style.textAlign = st.textAlign || 'center';
        el.appendChild(textEl);

        if (isEditor) {
          for (var a = 0; a < ANCHORS.length; a++) {
            var anchor = document.createElement('div');
            anchor.className = 'org-anchor org-anchor--' + ANCHORS[a];
            anchor.dataset.anchorNodeId = n.id;
            anchor.dataset.anchorSide = ANCHORS[a];
            anchor.title = 'Arrastrar para conectar';
            el.appendChild(anchor);
          }
          if (selected && store.state.selectedNodeIds.length === 1) {
            for (var r = 0; r < RESIZE_DIRS.length; r++) {
              var rh = document.createElement('div');
              rh.className = 'org-resize-handle org-resize-handle--' + RESIZE_DIRS[r];
              rh.dataset.resizeTarget = 'node';
              rh.dataset.resizeId = n.id;
              rh.dataset.resizeDir = RESIZE_DIRS[r];
              el.appendChild(rh);
            }
          }
        }
        nodesLayer.appendChild(el);
      }
    }

    function renderDraftLine(fromPt, toPt) {
      if (!draftLayer) return;
      draftLayer.innerHTML = '';
      if (!fromPt || !toPt) return;
      var line = svgEl('line', {
        x1: fromPt.x,
        y1: fromPt.y,
        x2: toPt.x,
        y2: toPt.y,
        'stroke-width': 2.5,
        'stroke-dasharray': '6 4',
      });
      line.style.stroke = 'var(--color-primary)';
      draftLayer.appendChild(line);
    }

    function renderMarquee(rect) {
      if (!draftLayer) return;
      draftLayer.innerHTML = '';
      if (!rect || rect.w <= 0 || rect.h <= 0) return;
      draftLayer.appendChild(
        svgEl('rect', {
          x: rect.x,
          y: rect.y,
          width: rect.w,
          height: rect.h,
          class: 'org-marquee-rect',
        })
      );
    }

    function render() {
      var state = store.state;
      renderSections(state);
      renderEdges(state);
      renderNodes(state);
      if (emptyHintEl) {
        var empty = !state.datos.nodes.length && !state.datos.sections.length;
        emptyHintEl.hidden = !empty;
      }
    }

    store.on('render', render);

    return {
      render: render,
      renderDraftLine: renderDraftLine,
      renderMarquee: renderMarquee,
      anchorPoint: anchorPoint,
    };
  }

  PoliOrg.createRenderer = createRenderer;
})(window);
