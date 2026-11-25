/* === Enrutador ortogonal anticolisiones (A*), carriles paralelos y cruces === */
(function (root) {
  'use strict';

  var PoliOrg = (root.PoliOrg = root.PoliOrg || {});

  var STUB_LEN = 10.0;
  var NODE_PAD = 6.0;
  var ENDPOINT_NODE_PAD = 4.0;
  var SEC_PAD = 8.0;
  var SEC_TOP_PAD = 20.0;
  var LANE_STEP = 8.0;
  var BEND_PENALTY = 180.0;
  var SHARED_BUS_PENALTY = 380.0;
  var SOFT_SECTION_PENALTY = 50000.0;
  var SOFT_NODE_PENALTY = 120000.0;

  function MinHeap() {
    this.data = [];
  }
  MinHeap.prototype.push = function (item) {
    var a = this.data;
    a.push(item);
    var i = a.length - 1;
    while (i > 0) {
      var p = (i - 1) >> 1;
      if (a[p].f <= item.f) break;
      a[i] = a[p];
      i = p;
    }
    a[i] = item;
  };
  MinHeap.prototype.pop = function () {
    var a = this.data;
    if (!a.length) return null;
    var top = a[0];
    var last = a.pop();
    if (a.length) {
      a[0] = last;
      var i = 0;
      var n = a.length;
      while (true) {
        var l = i * 2 + 1;
        var r = l + 1;
        var sm = i;
        if (l < n && a[l].f < a[sm].f) sm = l;
        if (r < n && a[r].f < a[sm].f) sm = r;
        if (sm === i) break;
        var tmp = a[i];
        a[i] = a[sm];
        a[sm] = tmp;
        i = sm;
      }
    }
    return top;
  };

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

  function boxForNode(node, pad) {
    var p = pad !== undefined ? pad : NODE_PAD;
    return [node.x - p, node.y - p, node.x + node.w + p, node.y + node.h + p];
  }

  function boxForSection(sec) {
    return [sec.x - SEC_PAD, sec.y - SEC_TOP_PAD, sec.x + sec.w + SEC_PAD, sec.y + sec.h + SEC_PAD];
  }

  function axisSegHitsBox(a, b, box) {
    var rx0 = box[0];
    var ry0 = box[1];
    var rx1 = box[2];
    var ry1 = box[3];
    if (Math.abs(a.y - b.y) < 1e-6) {
      if (!(ry0 < a.y && a.y < ry1)) return false;
      var xlo = Math.min(a.x, b.x);
      var xhi = Math.max(a.x, b.x);
      return xhi > rx0 && xlo < rx1;
    }
    if (Math.abs(a.x - b.x) < 1e-6) {
      if (!(rx0 < a.x && a.x < rx1)) return false;
      var ylo = Math.min(a.y, b.y);
      var yhi = Math.max(a.y, b.y);
      return yhi > ry0 && ylo < ry1;
    }
    return false;
  }

  function ptInBox(pt, box) {
    return pt.x > box[0] && pt.x < box[2] && pt.y > box[1] && pt.y < box[3];
  }

  function cleanPoints(pts) {
    if (!pts || !pts.length) return [];
    var dedup = [pts[0]];
    for (var i = 1; i < pts.length; i++) {
      var prev = dedup[dedup.length - 1];
      if (Math.abs(pts[i].x - prev.x) > 0.25 || Math.abs(pts[i].y - prev.y) > 0.25) {
        dedup.push({ x: pts[i].x, y: pts[i].y });
      }
    }
    if (dedup.length <= 2) return dedup;

    var out = [dedup[0]];
    for (var j = 1; j < dedup.length - 1; j++) {
      var a = out[out.length - 1];
      var b = dedup[j];
      var c = dedup[j + 1];
      var sameX = Math.abs(a.x - b.x) < 0.25 && Math.abs(b.x - c.x) < 0.25;
      var sameY = Math.abs(a.y - b.y) < 0.25 && Math.abs(b.y - c.y) < 0.25;
      if (sameX || sameY) continue;
      out.push({ x: Math.round(b.x * 100) / 100, y: Math.round(b.y * 100) / 100 });
    }
    out.push(dedup[dedup.length - 1]);
    return out;
  }

  function dedupCoords(coords, tol) {
    var t = tol !== undefined ? tol : 2.0;
    var sorted = coords
      .map(function (v) { return Math.round(v * 100) / 100; })
      .sort(function (a, b) { return a - b; });
    if (!sorted.length) return [];
    var out = [sorted[0]];
    for (var i = 1; i < sorted.length; i++) {
      if (sorted[i] - out[out.length - 1] > t) {
        out.push(sorted[i]);
      }
    }
    return out;
  }

  function fastOrthogonalPoints(src, dst, edge) {
    var fa = edge.fromAnchor || 'bottom';
    var ta = edge.toAnchor || 'top';
    var p1 = anchorPoint(src, fa);
    var p2 = anchorPoint(dst, ta);
    var d1 = anchorDir(fa);
    var d2 = anchorDir(ta);

    var gapV = Math.abs(p2.y - p1.y);
    var gapH = Math.abs(p2.x - p1.x);
    var stub1 = STUB_LEN;
    var stub2 = STUB_LEN;

    if (d1.y !== 0 && d1.y === -d2.y && (p2.y - p1.y) * d1.y > 0) {
      var halfV = Math.max(6.0, gapV / 2.0 - 1.0);
      stub1 = Math.min(STUB_LEN, halfV);
      stub2 = Math.min(STUB_LEN, halfV);
    } else if (d1.x !== 0 && d1.x === -d2.x && (p2.x - p1.x) * d1.x > 0) {
      var halfH = Math.max(6.0, gapH / 2.0 - 1.0);
      stub1 = Math.min(STUB_LEN, halfH);
      stub2 = Math.min(STUB_LEN, halfH);
    }

    var s1 = { x: p1.x + d1.x * stub1, y: p1.y + d1.y * stub1 };
    var s2 = { x: p2.x + d2.x * stub2, y: p2.y + d2.y * stub2 };
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
    return { pts: cleanPoints(pts), p1: p1, s1: s1, s2: s2, p2: p2 };
  }

  function buildBaseGrid(nodes, sections) {
    if (!nodes.length && !sections.length) return { xs: [], ys: [] };
    var items = nodes.concat(sections);
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

    var xs = [minX - 52, minX - 28, maxX + 28, maxX + 52];
    var ys = [minY - 60, minY - 36, maxY + 28, maxY + 52];

    var xSet = {};
    var ySet = {};
    for (var s = 0; s < sections.length; s++) {
      var sec = sections[s];
      xs.push(sec.x - 24, sec.x + sec.w + 24);
      ys.push(sec.y - 36, sec.y + sec.h + 24);
      xSet[sec.x] = true;
      xSet[sec.x + sec.w] = true;
      ySet[sec.y] = true;
      ySet[sec.y + sec.h] = true;
    }

    var xEdges = Object.keys(xSet).map(Number).sort(function (a, b) { return a - b; });
    for (var xi = 0; xi + 1 < xEdges.length; xi++) {
      if (xEdges[xi + 1] - xEdges[xi] >= 24) {
        xs.push((xEdges[xi] + xEdges[xi + 1]) / 2);
      }
    }
    var yEdges = Object.keys(ySet).map(Number).sort(function (a, b) { return a - b; });
    for (var yi = 0; yi + 1 < yEdges.length; yi++) {
      if (yEdges[yi + 1] - yEdges[yi] >= 24) {
        ys.push((yEdges[yi] + yEdges[yi + 1]) / 2);
      }
    }

    for (var n = 0; n < nodes.length; n++) {
      var nd = nodes[n];
      if (!nd.section_id) {
        xs.push(nd.x - 14, nd.x + nd.w + 14);
        ys.push(nd.y - 16, nd.y + nd.h + 16);
      }
    }

    return { xs: dedupCoords(xs, 2.0), ys: dedupCoords(ys, 2.0) };
  }

  function edgeLocalGrid(baseXs, baseYs, nodesBySec, secMap, src, dst, s1, s2) {
    var xs, ys;
    if (src.section_id && src.section_id === dst.section_id) {
      var ownSec = secMap[src.section_id];
      if (ownSec) {
        xs = [ownSec.x - 24, ownSec.x + ownSec.w + 24, s1.x, s2.x];
        ys = [ownSec.y - 36, ownSec.y + ownSec.h + 24, s1.y, s2.y];
      } else {
        xs = [s1.x, s2.x];
        ys = [s1.y, s2.y];
      }
    } else {
      xs = baseXs.concat([s1.x, s2.x]);
      ys = baseYs.concat([s1.y, s2.y]);
    }

    var localNodes = [];
    if (src.section_id && nodesBySec[src.section_id]) {
      localNodes = localNodes.concat(nodesBySec[src.section_id]);
    }
    if (dst.section_id && dst.section_id !== src.section_id && nodesBySec[dst.section_id]) {
      localNodes = localNodes.concat(nodesBySec[dst.section_id]);
    }
    if (!src.section_id && !dst.section_id) {
      var sk = Object.keys(nodesBySec);
      for (var i = 0; i < sk.length; i++) {
        localNodes = localNodes.concat(nodesBySec[sk[i]]);
      }
    }
    if (localNodes.indexOf(src) === -1) localNodes.push(src);
    if (localNodes.indexOf(dst) === -1) localNodes.push(dst);

    for (var j = 0; j < localNodes.length; j++) {
      var n = localNodes[j];
      xs.push(n.x - 10, n.x + n.w + 10);
      ys.push(n.y - 10, n.y + n.h + 10);
    }

    xs.push(((src.x + src.w / 2) + (dst.x + dst.w / 2)) / 2);
    ys.push(((src.y + src.h / 2) + (dst.y + dst.h / 2)) / 2);

    return { xs: dedupCoords(xs, 1.5), ys: dedupCoords(ys, 1.5) };
  }

  function findCoordIdx(sorted, target) {
    var best = 0;
    var bestD = Math.abs(sorted[0] - target);
    for (var i = 1; i < sorted.length; i++) {
      var d = Math.abs(sorted[i] - target);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    return best;
  }

  function roundKey(v) {
    return String(Math.round(v * 10) / 10);
  }

  function hasOverlap(usedMap, key, lo, hi, groupKey, minOverlap) {
    var list = usedMap[key];
    if (!list) return false;
    for (var i = 0; i < list.length; i++) {
      var u = list[i];
      if (u.group !== groupKey && Math.min(hi, u.hi) - Math.max(lo, u.lo) > minOverlap) {
        return true;
      }
    }
    return false;
  }

  function runAstar(
    xs,
    ys,
    startPt,
    goalPt,
    nodeBoxes,
    epBoxes,
    secBoxes,
    usedH,
    usedV,
    groupKey,
    allowSecCross,
    allowNodeCross
  ) {
    var sx = findCoordIdx(xs, startPt.x);
    var sy = findCoordIdx(ys, startPt.y);
    var gx = findCoordIdx(xs, goalPt.x);
    var gy = findCoordIdx(ys, goalPt.y);
    var nx = xs.length;
    var ny = ys.length;

    if (sx === gx && sy === gy) {
      return [{ x: xs[sx], y: ys[sy] }];
    }

    var allNodeBoxes = nodeBoxes.concat(epBoxes);
    var hNodes = [];
    var hSecs = [];
    for (var iy = 0; iy < ny; iy++) {
      var y = ys[iy];
      hNodes.push(allNodeBoxes.filter(function (b) { return b[1] < y && y < b[3]; }));
      hSecs.push(secBoxes.filter(function (b) { return b[1] < y && y < b[3]; }));
    }
    var vNodes = [];
    var vSecs = [];
    for (var ix = 0; ix < nx; ix++) {
      var x = xs[ix];
      vNodes.push(allNodeBoxes.filter(function (b) { return b[0] < x && x < b[2]; }));
      vSecs.push(secBoxes.filter(function (b) { return b[0] < x && x < b[2]; }));
    }

    function stepCost(x1, y1, x2, y2, ix1, iy1, ix2, iy2) {
      var dist = Math.abs(x2 - x1) + Math.abs(y2 - y1);
      var extra = 0;
      if (iy1 === iy2) {
        var rnodes = hNodes[iy1];
        var rsecs = hSecs[iy1];
        var xlo = Math.min(x1, x2);
        var xhi = Math.max(x1, x2);
        for (var a = 0; a < rnodes.length; a++) {
          if (xhi > rnodes[a][0] && xlo < rnodes[a][2]) {
            if (!allowNodeCross) return null;
            extra += SOFT_NODE_PENALTY;
          }
        }
        for (var b = 0; b < rsecs.length; b++) {
          if (xhi > rsecs[b][0] && xlo < rsecs[b][2]) {
            if (!allowSecCross) return null;
            extra += SOFT_SECTION_PENALTY;
          }
        }
        if (hasOverlap(usedH, roundKey(y1), xlo, xhi, groupKey, 2.0)) {
          extra += SHARED_BUS_PENALTY;
        }
      } else {
        var cnodes = vNodes[ix1];
        var csecs = vSecs[ix1];
        var ylo = Math.min(y1, y2);
        var yhi = Math.max(y1, y2);
        for (var c = 0; c < cnodes.length; c++) {
          if (yhi > cnodes[c][1] && ylo < cnodes[c][3]) {
            if (!allowNodeCross) return null;
            extra += SOFT_NODE_PENALTY;
          }
        }
        for (var d = 0; d < csecs.length; d++) {
          if (yhi > csecs[d][1] && ylo < csecs[d][3]) {
            if (!allowSecCross) return null;
            extra += SOFT_SECTION_PENALTY;
          }
        }
        if (hasOverlap(usedV, roundKey(x1), ylo, yhi, groupKey, 2.0)) {
          extra += SHARED_BUS_PENALTY;
        }
      }
      return dist + extra;
    }

    var totalStates = nx * ny * 3;
    var gScore = new Float64Array(totalStates);
    var cameFrom = new Int32Array(totalStates);
    for (var s = 0; s < totalStates; s++) {
      gScore[s] = Infinity;
      cameFrom[s] = -1;
    }

    function stateId(ixVal, iyVal, dVal) {
      return (iyVal * nx + ixVal) * 3 + dVal;
    }

    var startSt = stateId(sx, sy, 2);
    gScore[startSt] = 0;
    var heap = new MinHeap();
    var h0 = Math.abs(xs[gx] - xs[sx]) + Math.abs(ys[gy] - ys[sy]);
    heap.push({ f: h0, g: 0, ix: sx, iy: sy, d: 2, st: startSt });

    var goalSt = -1;
    var maxIters = Math.min(30000, nx * ny * 6);
    var iters = 0;

    while (heap.data.length && iters < maxIters) {
      iters += 1;
      var cur = heap.pop();
      if (cur.g > gScore[cur.st] + 1e-6) continue;
      if (cur.ix === gx && cur.iy === gy) {
        goalSt = cur.st;
        break;
      }

      var x1 = xs[cur.ix];
      var y1 = ys[cur.iy];
      var dirs = [
        cur.ix > 0 ? [cur.ix - 1, cur.iy, 0] : null,
        cur.ix + 1 < nx ? [cur.ix + 1, cur.iy, 0] : null,
        cur.iy > 0 ? [cur.ix, cur.iy - 1, 1] : null,
        cur.iy + 1 < ny ? [cur.ix, cur.iy + 1, 1] : null,
      ];

      for (var k = 0; k < 4; k++) {
        var nb = dirs[k];
        if (!nb) continue;
        var nix = nb[0];
        var niy = nb[1];
        var ndir = nb[2];
        var sc = stepCost(x1, y1, xs[nix], ys[niy], cur.ix, cur.iy, nix, niy);
        if (sc === null) continue;
        var bend = cur.d !== 2 && cur.d !== ndir ? BEND_PENALTY : 0;
        var ng = cur.g + sc + bend;
        var nst = stateId(nix, niy, ndir);
        if (ng < gScore[nst] - 1e-6) {
          gScore[nst] = ng;
          cameFrom[nst] = cur.st;
          var hEst = Math.abs(xs[gx] - xs[nix]) + Math.abs(ys[gy] - ys[niy]);
          heap.push({ f: ng + hEst, g: ng, ix: nix, iy: niy, d: ndir, st: nst });
        }
      }
    }

    if (goalSt === -1) return null;

    var out = [];
    var walker = goalSt;
    while (walker !== -1) {
      var cellIdx = Math.floor(walker / 3);
      var wy = Math.floor(cellIdx / nx);
      var wx = cellIdx % nx;
      out.push({ x: xs[wx], y: ys[wy] });
      walker = cameFrom[walker];
    }
    out.reverse();
    return out;
  }

  function nudgeParallelLanes(routes, metaById, otherNodesFn, otherSecsFn) {
    var axes = ['h', 'v'];
    var eids = Object.keys(routes);

    for (var ai = 0; ai < axes.length; ai++) {
      var axis = axes[ai];
      var buckets = {};
      for (var i = 0; i < eids.length; i++) {
        var eid = eids[i];
        if (metaById[eid].routing === 'straight') continue;
        var pts = routes[eid].points;
        for (var idx = 1; idx < pts.length - 2; idx++) {
          var a = pts[idx];
          var b = pts[idx + 1];
          var coord, lo, hi;
          if (axis === 'h' && Math.abs(a.y - b.y) < 0.25 && Math.abs(a.x - b.x) > 4.0) {
            coord = String(Math.round(a.y / 2) * 2);
            lo = Math.min(a.x, b.x);
            hi = Math.max(a.x, b.x);
          } else if (axis === 'v' && Math.abs(a.x - b.x) < 0.25 && Math.abs(a.y - b.y) > 4.0) {
            coord = String(Math.round(a.x / 2) * 2);
            lo = Math.min(a.y, b.y);
            hi = Math.max(a.y, b.y);
          } else {
            continue;
          }
          if (!buckets[coord]) buckets[coord] = [];
          buckets[coord].push({
            eid: eid,
            segIdx: idx,
            lo: lo,
            hi: hi,
            group: metaById[eid].group,
          });
        }
      }

      var bKeys = Object.keys(buckets);
      for (var bk = 0; bk < bKeys.length; bk++) {
        var entries = buckets[bKeys[bk]].sort(function (u, v) {
          return u.lo !== v.lo ? u.lo - v.lo : u.hi - v.hi;
        });
        var clusters = [];
        for (var ei = 0; ei < entries.length; ei++) {
          var ent = entries[ei];
          var targetCl = null;
          for (var ci = 0; ci < clusters.length; ci++) {
            var cl = clusters[ci];
            for (var cj = 0; cj < cl.length; cj++) {
              if (Math.min(ent.hi, cl[cj].hi) - Math.max(ent.lo, cl[cj].lo) > 6.0) {
                targetCl = cl;
                break;
              }
            }
            if (targetCl) break;
          }
          if (targetCl) targetCl.push(ent);
          else clusters.push([ent]);
        }

        for (var cIdx = 0; cIdx < clusters.length; cIdx++) {
          var cluster = clusters[cIdx];
          var groups = [];
          for (var gi = 0; gi < cluster.length; gi++) {
            if (groups.indexOf(cluster[gi].group) === -1) {
              groups.push(cluster[gi].group);
            }
          }
          if (groups.length <= 1) continue;
          var offsets = {};
          for (var gPos = 0; gPos < groups.length; gPos++) {
            offsets[groups[gPos]] = (gPos - (groups.length - 1) / 2) * LANE_STEP;
          }

          for (var m = 0; m < cluster.length; m++) {
            var item = cluster[m];
            var delta = offsets[item.group];
            if (Math.abs(delta) < 0.5) continue;
            var rPts = routes[item.eid].points;
            var pA = rPts[item.segIdx];
            var pB = rPts[item.segIdx + 1];
            var prevPt = rPts[item.segIdx - 1];
            var nextPt = rPts[item.segIdx + 2];
            if (!pA || !pB || !prevPt || !nextPt) continue;

            var nA, nB;
            if (axis === 'h') {
              if (Math.abs(prevPt.x - pA.x) > 1 || Math.abs(nextPt.x - pB.x) > 1) continue;
              nA = { x: pA.x, y: Math.round((pA.y + delta) * 100) / 100 };
              nB = { x: pB.x, y: Math.round((pB.y + delta) * 100) / 100 };
            } else {
              if (Math.abs(prevPt.y - pA.y) > 1 || Math.abs(nextPt.y - pB.y) > 1) continue;
              nA = { x: Math.round((pA.x + delta) * 100) / 100, y: pA.y };
              nB = { x: Math.round((pB.x + delta) * 100) / 100, y: pB.y };
            }

            var obs = otherNodesFn(item.eid).concat(otherSecsFn(item.eid));
            var blocked = false;
            var testSegs = [[prevPt, nA], [nA, nB], [nB, nextPt]];
            for (var ts = 0; ts < testSegs.length && !blocked; ts++) {
              for (var ob = 0; ob < obs.length; ob++) {
                if (axisSegHitsBox(testSegs[ts][0], testSegs[ts][1], obs[ob])) {
                  blocked = true;
                  break;
                }
              }
            }
            if (!blocked) {
              rPts[item.segIdx] = nA;
              rPts[item.segIdx + 1] = nB;
              routes[item.eid].points = cleanPoints(rPts);
            }
          }
        }
      }
    }
  }

  function detectCrossings(routes, metaById) {
    var hSegs = [];
    var vSegs = [];
    var eids = Object.keys(routes);

    for (var i = 0; i < eids.length; i++) {
      var eid = eids[i];
      var grp = metaById[eid].group;
      var pts = routes[eid].points;
      for (var idx = 0; idx < pts.length - 1; idx++) {
        var a = pts[idx];
        var b = pts[idx + 1];
        if (Math.abs(a.y - b.y) < 0.25 && Math.abs(a.x - b.x) >= 24.0) {
          hSegs.push({
            eid: eid,
            segIdx: idx,
            y: a.y,
            x0: Math.min(a.x, b.x),
            x1: Math.max(a.x, b.x),
            group: grp,
          });
        } else if (Math.abs(a.x - b.x) < 0.25 && Math.abs(a.y - b.y) >= 24.0) {
          vSegs.push({
            eid: eid,
            segIdx: idx,
            x: a.x,
            y0: Math.min(a.y, b.y),
            y1: Math.max(a.y, b.y),
            group: grp,
          });
        }
      }
    }

    for (var hi = 0; hi < hSegs.length; hi++) {
      var hs = hSegs[hi];
      var jumps = [];
      for (var vi = 0; vi < vSegs.length; vi++) {
        var vs = vSegs[vi];
        if (hs.group === vs.group) continue;
        if (hs.x0 + 12.0 < vs.x && vs.x < hs.x1 - 12.0 && vs.y0 + 10.0 < hs.y && hs.y < vs.y1 - 10.0) {
          jumps.push(Math.round(vs.x * 100) / 100);
        }
      }
      if (jumps.length) {
        jumps.sort(function (a, b) { return a - b; });
        var clean = [jumps[0]];
        for (var ji = 1; ji < jumps.length; ji++) {
          if (jumps[ji] - clean[clean.length - 1] >= 14.0) {
            clean.push(jumps[ji]);
          }
        }
        routes[hs.eid].crossings[hs.segIdx] = clean;
      }
    }
  }

  function computeLabelPos(pts, crossings) {
    if (!pts || pts.length < 2) return { x: 0, y: 0 };
    if (pts.length === 2) {
      return { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
    }
    var startI = pts.length >= 4 ? 1 : 0;
    var endI = pts.length >= 4 ? pts.length - 2 : pts.length - 1;
    var bestIdx = startI;
    var bestLen = -1;
    for (var i = startI; i < endI; i++) {
      var len = Math.hypot(pts[i + 1].x - pts[i].x, pts[i + 1].y - pts[i].y);
      if (len > bestLen) {
        bestLen = len;
        bestIdx = i;
      }
    }
    var a = pts[bestIdx];
    var b = pts[bestIdx + 1];
    var mx = (a.x + b.x) / 2;
    var my = (a.y + b.y) / 2;
    var jumps = crossings[bestIdx] || [];
    if (jumps.length && Math.abs(a.y - b.y) < 0.25) {
      for (var ji = 0; ji < jumps.length; ji++) {
        if (Math.abs(mx - jumps[ji]) < 20) {
          var shift = mx + 22 < Math.max(a.x, b.x) - 16 ? 22 : -22;
          if (Math.min(a.x, b.x) + 14 <= mx + shift && mx + shift <= Math.max(a.x, b.x) - 14) {
            mx += shift;
          }
          break;
        }
      }
    }
    return { x: Math.round(mx * 100) / 100, y: Math.round(my * 100) / 100 };
  }

  function routeAllEdges(nodes, sections, edges, opts) {
    var isolatedSec = opts && opts.isolatedSectionId ? opts.isolatedSectionId : null;
    var mostrarMarcos = !opts || opts.mostrarMarcos !== false;

    var activeNodes = isolatedSec
      ? nodes.filter(function (n) { return n.section_id === isolatedSec; })
      : nodes;
    var activeSections = isolatedSec
      ? sections.filter(function (s) { return s.id === isolatedSec; })
      : sections;

    var nodeMap = {};
    var nodeBoxesAll = {};
    var epBoxesAll = {};
    var nodesBySec = {};
    for (var i = 0; i < activeNodes.length; i++) {
      var n = activeNodes[i];
      nodeMap[n.id] = n;
      nodeBoxesAll[n.id] = boxForNode(n, NODE_PAD);
      epBoxesAll[n.id] = boxForNode(n, ENDPOINT_NODE_PAD);
      var sk = n.section_id || '__none__';
      if (!nodesBySec[sk]) nodesBySec[sk] = [];
      nodesBySec[sk].push(n);
    }

    var secMap = {};
    var secBoxesAll = {};
    for (var s = 0; s < activeSections.length; s++) {
      secMap[activeSections[s].id] = activeSections[s];
      if (mostrarMarcos) {
        secBoxesAll[activeSections[s].id] = boxForSection(activeSections[s]);
      }
    }

    var base = buildBaseGrid(activeNodes, mostrarMarcos ? activeSections : []);

    var validEdges = edges.filter(function (e) {
      return Boolean(nodeMap[e.from] && nodeMap[e.to]);
    });

    validEdges.sort(function (a, b) {
      var sa = nodeMap[a.from];
      var da = nodeMap[a.to];
      var sb = nodeMap[b.from];
      var db = nodeMap[b.to];
      var ca = sa.section_id !== da.section_id ? 1 : 0;
      var cb = sb.section_id !== db.section_id ? 1 : 0;
      if (ca !== cb) return ca - cb;
      var p1a = anchorPoint(sa, a.fromAnchor || 'bottom');
      var p2a = anchorPoint(da, a.toAnchor || 'top');
      var p1b = anchorPoint(sb, b.fromAnchor || 'bottom');
      var p2b = anchorPoint(db, b.toAnchor || 'top');
      var distA = Math.abs(p1a.x - p2a.x) + Math.abs(p1a.y - p2a.y);
      var distB = Math.abs(p1b.x - p2b.x) + Math.abs(p1b.y - p2b.y);
      if (distA !== distB) return distA - distB;
      return String(a.id || '').localeCompare(String(b.id || ''));
    });

    var routes = {};
    var metaById = {};
    var usedH = {};
    var usedV = {};

    function recordUsed(pts, groupKey) {
      for (var i = 1; i < pts.length - 2; i++) {
        var a = pts[i];
        var b = pts[i + 1];
        if (Math.abs(a.y - b.y) < 0.25) {
          var hk = roundKey(a.y);
          if (!usedH[hk]) usedH[hk] = [];
          usedH[hk].push({ lo: Math.min(a.x, b.x), hi: Math.max(a.x, b.x), group: groupKey });
        } else if (Math.abs(a.x - b.x) < 0.25) {
          var vk = roundKey(a.x);
          if (!usedV[vk]) usedV[vk] = [];
          usedV[vk].push({ lo: Math.min(a.y, b.y), hi: Math.max(a.y, b.y), group: groupKey });
        }
      }
    }

    for (var ei = 0; ei < validEdges.length; ei++) {
      var edge = validEdges[ei];
      var eid = edge.id;
      var src = nodeMap[edge.from];
      var dst = nodeMap[edge.to];
      var groupKey = edge.from + ':' + (edge.fromAnchor || 'bottom');
      var routing = edge.routing || 'orthogonal';
      metaById[eid] = { group: groupKey, routing: routing };

      if (routing === 'straight') {
        var pStart = anchorPoint(src, edge.fromAnchor || 'bottom');
        var pEnd = anchorPoint(dst, edge.toAnchor || 'top');
        var stPts = [pStart, pEnd];
        routes[eid] = {
          points: stPts,
          labelPos: computeLabelPos(stPts, {}),
          crossings: {},
        };
        continue;
      }

      var fast = fastOrthogonalPoints(src, dst, edge);
      var fastPts = fast.pts;

      var otherNodes = [];
      for (var nk = 0; nk < activeNodes.length; nk++) {
        var nid = activeNodes[nk].id;
        if (nid !== src.id && nid !== dst.id) {
          otherNodes.push(nodeBoxesAll[nid]);
        }
      }
      var isCrossSec = mostrarMarcos && src.section_id !== dst.section_id;
      var otherSecs = [];
      if (isCrossSec) {
        var secKeys = Object.keys(secBoxesAll);
        for (var sk = 0; sk < secKeys.length; sk++) {
          var sid = secKeys[sk];
          if (sid !== src.section_id && sid !== dst.section_id) {
            otherSecs.push(secBoxesAll[sid]);
          }
        }
      }

      var epSrc = epBoxesAll[src.id];
      var epDst = epBoxesAll[dst.id];
      var fastOk = !ptInBox(fast.s1, epSrc) && !ptInBox(fast.s2, epDst);

      if (fastOk) {
        for (var fi = 0; fi < fastPts.length - 1 && fastOk; fi++) {
          var segA = fastPts[fi];
          var segB = fastPts[fi + 1];
          for (var nb = 0; nb < otherNodes.length; nb++) {
            if (axisSegHitsBox(segA, segB, otherNodes[nb])) {
              fastOk = false;
              break;
            }
          }
          for (var sb = 0; sb < otherSecs.length && fastOk; sb++) {
            if (axisSegHitsBox(segA, segB, otherSecs[sb])) {
              fastOk = false;
              break;
            }
          }
        }
      }

      if (fastOk && fastPts.length >= 4) {
        for (var ii = 1; ii < fastPts.length - 2 && fastOk; ii++) {
          var ia = fastPts[ii];
          var ib = fastPts[ii + 1];
          if (axisSegHitsBox(ia, ib, epSrc) || axisSegHitsBox(ia, ib, epDst)) {
            fastOk = false;
            break;
          }
          if (Math.abs(ia.y - ib.y) < 0.25) {
            if (hasOverlap(usedH, roundKey(ia.y), Math.min(ia.x, ib.x), Math.max(ia.x, ib.x), groupKey, 4.0)) {
              fastOk = false;
            }
          } else if (Math.abs(ia.x - ib.x) < 0.25) {
            if (hasOverlap(usedV, roundKey(ia.x), Math.min(ia.y, ib.y), Math.max(ia.y, ib.y), groupKey, 4.0)) {
              fastOk = false;
            }
          }
        }
      }

      var chosen;
      if (fastOk) {
        chosen = fastPts;
      } else {
        var lg = edgeLocalGrid(base.xs, base.ys, nodesBySec, secMap, src, dst, fast.s1, fast.s2);
        var epBoxes = [epSrc, epDst];
        var astar = runAstar(
          lg.xs, lg.ys, fast.s1, fast.s2,
          otherNodes, epBoxes, otherSecs,
          usedH, usedV, groupKey, false, false
        );
        if (!astar && otherSecs.length) {
          astar = runAstar(
            lg.xs, lg.ys, fast.s1, fast.s2,
            otherNodes, epBoxes, otherSecs,
            usedH, usedV, groupKey, true, false
          );
        }
        if (!astar) {
          astar = runAstar(
            lg.xs, lg.ys, fast.s1, fast.s2,
            otherNodes, epBoxes, otherSecs,
            usedH, usedV, groupKey, true, true
          );
        }
        chosen = astar ? cleanPoints([fast.p1].concat(astar).concat([fast.p2])) : fastPts;
      }

      recordUsed(chosen, groupKey);
      routes[eid] = {
        points: chosen,
        labelPos: { x: 0, y: 0 },
        crossings: {},
      };
    }

    var edgeLookup = {};
    for (var ek = 0; ek < validEdges.length; ek++) {
      edgeLookup[validEdges[ek].id] = validEdges[ek];
    }

    nudgeParallelLanes(
      routes,
      metaById,
      function (eid) {
        var ed = edgeLookup[eid];
        var out = [];
        for (var i = 0; i < activeNodes.length; i++) {
          var nid = activeNodes[i].id;
          if (nid !== ed.from && nid !== ed.to) out.push(nodeBoxesAll[nid]);
        }
        return out;
      },
      function (eid) {
        if (!mostrarMarcos) return [];
        var ed = edgeLookup[eid];
        var ss = nodeMap[ed.from].section_id;
        var ds = nodeMap[ed.to].section_id;
        if (ss === ds) return [];
        var out = [];
        var sks = Object.keys(secBoxesAll);
        for (var i = 0; i < sks.length; i++) {
          if (sks[i] !== ss && sks[i] !== ds) out.push(secBoxesAll[sks[i]]);
        }
        return out;
      }
    );

    detectCrossings(routes, metaById);

    var rKeys = Object.keys(routes);
    for (var rk = 0; rk < rKeys.length; rk++) {
      var g = routes[rKeys[rk]];
      g.labelPos = computeLabelPos(g.points, g.crossings);
    }

    return routes;
  }

  function buildRoundedSvgPath(points, crossingsMap, cornerRadius, jumpRadius) {
    if (!points || points.length < 2) return '';
    if (points.length === 2 && !(crossingsMap && crossingsMap[0] && crossingsMap[0].length)) {
      return 'M ' + points[0].x + ' ' + points[0].y + ' L ' + points[1].x + ' ' + points[1].y;
    }

    var cr = cornerRadius !== undefined ? cornerRadius : 8;
    var jr = jumpRadius !== undefined ? jumpRadius : 6;
    var cMap = crossingsMap || {};
    var parts = ['M ' + points[0].x + ' ' + points[0].y];

    for (var i = 0; i < points.length - 1; i++) {
      var a = points[i];
      var b = points[i + 1];
      var dx = b.x - a.x;
      var dy = b.y - a.y;
      var segLen = Math.hypot(dx, dy);
      if (segLen < 0.01) continue;
      var ux = dx / segLen;
      var uy = dy / segLen;

      // Recorte al final si el siguiente segmento gira
      var trimEnd = 0;
      var nextCornerTo = null;
      if (i + 1 < points.length - 1 && cr > 0) {
        var c = points[i + 2];
        var dx2 = c.x - b.x;
        var dy2 = c.y - b.y;
        var len2 = Math.hypot(dx2, dy2);
        if (len2 > 0.01) {
          var ux2 = dx2 / len2;
          var uy2 = dy2 / len2;
          // Perpendicular
          if (Math.abs(ux * ux2 + uy * uy2) < 0.25) {
            trimEnd = Math.min(cr, segLen * 0.45, len2 * 0.45);
            nextCornerTo = {
              x: b.x + ux2 * trimEnd,
              y: b.y + uy2 * trimEnd,
            };
          }
        }
      }

      var trimStart = 0;
      if (i > 0 && cr > 0) {
        var prev = points[i - 1];
        var prevLen = Math.hypot(a.x - prev.x, a.y - prev.y);
        if (prevLen > 0.01) {
          var pux = (a.x - prev.x) / prevLen;
          var puy = (a.y - prev.y) / prevLen;
          if (Math.abs(pux * ux + puy * uy) < 0.25) {
            trimStart = Math.min(cr, prevLen * 0.45, segLen * 0.45);
          }
        }
      }

      var endPt = {
        x: b.x - ux * trimEnd,
        y: b.y - uy * trimEnd,
      };

      // Arcos de salto en cruces horizontales sobre verticales
      var jumps = cMap[i] || [];
      if (jumps.length && Math.abs(dy) < 0.25 && Math.abs(ux) > 0.5) {
        var startX = a.x + ux * trimStart;
        var endX = endPt.x;
        var ordered = jumps.slice().sort(function (u, v) { return ux > 0 ? u - v : v - u; });
        var minAllowed = Math.min(startX, endX) + jr + 1;
        var maxAllowed = Math.max(startX, endX) - jr - 1;
        var sweepFlag = ux > 0 ? 1 : 0;
        var curX = startX;

        for (var j = 0; j < ordered.length; j++) {
          var jx = ordered[j];
          if (jx <= minAllowed || jx >= maxAllowed) continue;
          var xEntry = jx - ux * jr;
          var xExit = jx + ux * jr;
          if (ux > 0 ? xEntry <= curX + 0.5 : xEntry >= curX - 0.5) continue;
          parts.push('L ' + xEntry + ' ' + a.y);
          parts.push('A ' + jr + ' ' + jr + ' 0 0 ' + sweepFlag + ' ' + xExit + ' ' + a.y);
          curX = xExit;
        }
      }

      parts.push('L ' + endPt.x + ' ' + endPt.y);

      if (nextCornerTo) {
        parts.push('Q ' + b.x + ' ' + b.y + ' ' + nextCornerTo.x + ' ' + nextCornerTo.y);
      }
    }

    return parts.join(' ');
  }

  PoliOrg.routeAllEdges = routeAllEdges;
  PoliOrg.buildRoundedSvgPath = buildRoundedSvgPath;
})(window);
