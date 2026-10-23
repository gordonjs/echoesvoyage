/*
 * Parcel RTK — setbacks and offsets.
 *
 * Zoning setbacks are measured from the lot lines. The county's zone district gives a
 * distance per kind of line: front (the line along the road), side, and rear. This module
 * turns those into geometry:
 *   - the SETBACK LINE inside each of your parcels (where a building may start), as an
 *     inward offset of the parcel ring with a different distance per line;
 *   - the NEIGHBOR'S setback band outside your line (where they may not build);
 *   - a check of any point: how far inside or outside the setback zone it is;
 *   - which side of a boundary line is yours (for staking a fence a set distance inside).
 * Everything works on the georeferenced geometry (ENU metres), so it follows measured
 * monuments and the "hold monuments" bending exactly as the drawn outline does.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./cogo.js'), require('./geodesy.js'));
  else { var P = root.PRTK = root.PRTK || {}; P.setback = factory(P.cogo, P.geo); }
})(typeof self !== 'undefined' ? self : this, function (cogo, geo) {
  'use strict';
  var K = geo.US_FT;

  // Jefferson County Zoning Resolution, primary structures (verify with Planning & Zoning,
  // 303-271-8700 — a plat note or a later amendment can change these).
  var ZONES = {
    'A-2':  { name: 'A-2 Agricultural Two (10-ac lots)', front: 50, side: 30, rear: 50, source: 'Zoning Resolution §33 (A-1/A-2/A-35)' },
    'A-35': { name: 'A-35 Agricultural (35-ac lots)', front: 50, side: 30, rear: 50, source: 'Zoning Resolution §33 (A-1/A-2/A-35)' },
    'MR-1': { name: 'MR-1 Mountain Residential One', front: 30, side: 20, rear: 20, source: 'Zoning Resolution §31 (2018 table)' },
    'custom': { name: 'Custom (enter your own)', front: 30, side: 20, rear: 20, source: 'your numbers' }
  };
  var DEFAULTS = {
    show: true,
    parcels: { P1: { zone: 'A-2', front: 50, side: 30, rear: 50 }, LOT1: { zone: 'A-2', front: 50, side: 30, rear: 50 } },
    lines: {},                       // segId -> { type: 'front'|'side'|'rear', neighborFt: number|null }
    neighborFt: 20                   // fallback for a neighbor's side setback (MR-1 side)
  };

  function conf(settings) {
    var s = settings && settings.setbacks || {};
    var out = { show: s.show !== false, parcels: {}, lines: s.lines || {}, neighborFt: s.neighborFt != null ? s.neighborFt : DEFAULTS.neighborFt };
    Object.keys(DEFAULTS.parcels).forEach(function (id) {
      out.parcels[id] = Object.assign({}, DEFAULTS.parcels[id], s.parcels && s.parcels[id] || {});
    });
    return out;
  }

  // default kind of each line: the road frontage is the front; everything else a side
  function lineType(model, c, segId) {
    var o = c.lines[segId];
    if (o && o.type) return o.type;
    var s = model.segments[segId];
    return /Shadow Mountain/.test(s.name) ? 'front' : 'side';
  }
  // the owned ring a boundary segment belongs to (for an internal line: the one asked for)
  function ownedRingOf(model, segId, rid) {
    var s = model.segments[segId];
    if (rid && s.rings.indexOf(rid) >= 0) return rid;
    for (var i = 0; i < s.rings.length; i++) if (model.rings[s.rings[i]].owned) return s.rings[i];
    return null;
  }
  // your setback (ft) from a line, inside parcel rid
  function ownFt(model, c, segId, rid) {
    var r = ownedRingOf(model, segId, rid);
    if (!r) return 0;
    var p = c.parcels[r] || DEFAULTS.parcels.P1;
    var t = model.segments[segId].role === 'internal' ? 'side' : lineType(model, c, segId);
    return +p[t] || 0;
  }
  // the neighbor's setback (ft) on the far side of one of your boundary lines
  function neighborFt(model, c, segId) {
    var o = c.lines[segId];
    if (o && o.neighborFt != null) return +o.neighborFt;
    var s = model.segments[segId];
    // across a Lodgepole Pines line the neighbor is in the same subdivision (same zoning)
    if (s.rings.indexOf('LP23') >= 0) return (c.parcels.LOT1 && c.parcels.LOT1.side) || 30;
    return c.neighborFt;
  }

  // ---- geometry ---------------------------------------------------------------------
  // segment id of edge i of a ring (verts carry the id of the segment that produced them;
  // the closing edge belongs to the last vertex's segment)
  function edgeSeg(ring, i) { return i < ring.length - 1 ? ring[i + 1].seg : ring[ring.length - 1].seg; }

  // Offset a closed ring by distFn(segId) metres (positive = inward), mitered corners.
  // Points are {x, y, seg}. Returns [{x, y, seg}] or null when it collapses.
  function offsetRing(ring, distFn) {
    var n = ring.length;
    if (n < 3) return null;
    var area = 0;
    for (var i = 0, j = n - 1; i < n; j = i++) area += ring[j].x * ring[i].y - ring[i].x * ring[j].y;
    var inward = area < 0 ? 1 : -1;              // clockwise: interior is to the right of travel
    var lines = [];
    for (i = 0; i < n; i++) {
      var a = ring[i], b = ring[(i + 1) % n], dx = b.x - a.x, dy = b.y - a.y, L = Math.hypot(dx, dy);
      if (L < 1e-9) continue;
      var tx = dx / L, ty = dy / L, nx = inward * ty, ny = -inward * tx, d = distFn(edgeSeg(ring, i)) || 0;
      lines.push({ px: a.x + nx * d, py: a.y + ny * d, tx: tx, ty: ty, seg: edgeSeg(ring, i), d: d, vx: b.x, vy: b.y });
    }
    // original edges, to test candidate points against ("no closer than that edge's setback")
    var edges = lines.map(function (l) { return { ax: l.px - (inward * l.ty) * l.d, ay: l.py + (inward * l.tx) * l.d, bx: l.vx, by: l.vy, d: l.d }; });
    function valid(p) {
      for (var k = 0; k < edges.length; k++) {
        var e = edges[k], ex = e.bx - e.ax, ey = e.by - e.ay, L2 = ex * ex + ey * ey;
        var t = L2 > 0 ? Math.max(0, Math.min(1, ((p.x - e.ax) * ex + (p.y - e.ay) * ey) / L2)) : 0;
        if (Math.hypot(p.x - (e.ax + t * ex), p.y - (e.ay + t * ey)) < e.d - 0.02) return false;
      }
      return true;
    }
    // Corners between consecutive offset lines. Near a sharp corner the offset of one line
    // runs across the neighbor's setback: such corners are invalid, and the offset lines
    // they sit on are dropped until the ring is made only of valid corners.
    var out = null;
    for (var pass = 0; pass < 12 && lines.length >= 3; pass++) {
      var m = lines.length;
      out = [];
      for (i = 0; i < m; i++) {
        var l1 = lines[i], l2 = lines[(i + 1) % m];
        out.push(Object.assign(intersect(l1, l2, inward), { seg: l2.seg, ok: false }));
      }
      var badCount = 0;
      out.forEach(function (p) { p.ok = valid(p); if (!p.ok) badCount++; });
      if (!badCount) break;
      if (badCount === m) return null;
      // an invalid corner i sits on lines i and i+1: drop the line between two invalid corners,
      // or the one that is not shared with a valid corner
      var keep = lines.filter(function (l, k) {
        var before = out[(k - 1 + m) % m].ok, after = out[k].ok;   // corners on this line
        return before || after;
      });
      if (keep.length === lines.length) {                           // every line touches a valid corner: drop the lines of the worst corners
        keep = lines.filter(function (l, k) { return out[k].ok || out[(k - 1 + m) % m].ok && out[k].ok; });
        if (keep.length === lines.length) break;
      }
      lines = keep;
    }
    if (!out) return null;
    out = out.filter(function (p) { return p.ok; });
    // drop the ring if it turned inside out (setback bigger than the parcel)
    var a2 = 0;
    for (i = 0, j = out.length - 1; i < out.length; j = i++) a2 += out[j].x * out[i].y - out[i].x * out[j].y;
    return out.length >= 3 && (a2 < 0) === (area < 0) && Math.abs(a2) > 1 ? out : null;
  }
  // where the offset lines of two consecutive edges meet (the corner after edge l1)
  function intersect(l1, l2, inward) {
    var cross = l1.tx * l2.ty - l1.ty * l2.tx;
    var mid = { x: l1.vx + (l1.d + l2.d) / 2 * inward * (l1.ty + l2.ty) / 2, y: l1.vy - (l1.d + l2.d) / 2 * inward * (l1.tx + l2.tx) / 2 };
    if (Math.abs(cross) < 1e-6) return { x: l2.px, y: l2.py };               // straight on: start of the next offset edge
    var dx = l2.px - l1.px, dy = l2.py - l1.py;
    var t = (dx * l2.ty - dy * l2.tx) / cross;
    var p = { x: l1.px + l1.tx * t, y: l1.py + l1.ty * t };
    // a spike at a very sharp corner is limited to 4x the offset
    var lim = 4 * Math.max(l1.d, l2.d, 0.5);
    if (Math.hypot(p.x - l1.vx, p.y - l1.vy) > lim) return mid;
    return p;
  }

  // Offset one open polyline [{x,y}] by d metres to its right (+) or left (−), mitered.
  function offsetLine(pts, d) {
    var out = [], n = pts.length;
    if (n < 2) return out;
    var segs = [];
    for (var i = 0; i < n - 1; i++) {
      var dx = pts[i + 1].x - pts[i].x, dy = pts[i + 1].y - pts[i].y, L = Math.hypot(dx, dy);
      if (L < 1e-9) continue;
      var tx = dx / L, ty = dy / L;
      segs.push({ px: pts[i].x + ty * d, py: pts[i].y - tx * d, tx: tx, ty: ty, d: Math.abs(d), vx: pts[i + 1].x, vy: pts[i + 1].y });
    }
    if (!segs.length) return out;
    out.push({ x: segs[0].px, y: segs[0].py });
    for (i = 0; i < segs.length - 1; i++) out.push(intersect(segs[i], segs[i + 1], d >= 0 ? 1 : -1));
    var e = segs[segs.length - 1];
    out.push({ x: e.vx + e.ty * d, y: e.vy - e.tx * d });
    return out;
  }

  // +1 when the owned side of boundary segment segId is to the RIGHT of its a->b direction
  function ownedSide(model, gm, segId) {
    var rid = ownedRingOf(model, segId);
    if (!rid) return 1;
    var xy = gm.segs[segId].xy, k = Math.floor((xy.length - 1) / 2), a = xy[k], b = xy[k + 1];
    var dx = b.x - a.x, dy = b.y - a.y, L = Math.hypot(dx, dy) || 1;
    var q = { e: (a.x + b.x) / 2 + dy / L * 0.5, n: (a.y + b.y) / 2 - dx / L * 0.5 };   // 0.5 m to the right
    var ring = gm.rings[rid].xy.map(function (p) { return { e: p.x, n: p.y }; });
    return cogo.pointInRing(q, ring) ? 1 : -1;
  }

  // Everything the map needs: per owned parcel the setback ring; per boundary line the
  // neighbor band. Distances in metres, geometry in ENU of gm.
  function build(model, gm, settings) {
    var c = conf(settings), out = { rings: {}, neighbor: {}, conf: c };
    Object.keys(model.rings).forEach(function (rid) {
      if (!model.rings[rid].owned) return;
      var inner = offsetRing(gm.rings[rid].xy, function (segId) { return ownFt(model, c, segId, rid) * K; });
      out.rings[rid] = inner ? { xy: inner, ll: inner.map(function (p) { var g = gm.frame.toGeo(p.x, p.y, 0); return [g.lat, g.lon]; }) } : null;
    });
    Object.keys(model.segments).forEach(function (id) {
      var s = model.segments[id];
      if (s.role !== 'boundary') return;
      var ft = neighborFt(model, c, id);
      if (!(ft > 0)) return;
      var side = ownedSide(model, gm, id);                   // neighbor is on the other side
      var off = offsetLine(gm.segs[id].xy, -side * ft * K);
      var poly = gm.segs[id].xy.concat(off.slice().reverse());
      out.neighbor[id] = { ft: ft, ll: poly.map(function (p) { var g = gm.frame.toGeo(p.x, p.y, 0); return [g.lat, g.lon]; }) };
    });
    return out;
  }

  // Setback status of a point from a locate.where() answer: metres to the setback line
  // (negative = inside the setback zone), the controlling line and its required distance.
  function check(model, settings, w) {
    if (!w || !w.inOwned) return null;
    var c = conf(settings), rid = w.inOwned, best = null;
    [w.edge, w.internal].forEach(function (l) {
      if (!l) return;
      var ft = ownFt(model, c, l.segId, rid);
      if (!(ft > 0)) return;
      var margin = l.d - ft * K;
      if (!best || margin < best.margin) best = { segId: l.segId, name: model.segments[l.segId].name, requiredFt: ft, d: l.d, margin: margin,
                                                   type: model.segments[l.segId].role === 'internal' ? 'side' : lineType(model, c, l.segId) };
    });
    return best;
  }

  return { ZONES: ZONES, DEFAULTS: DEFAULTS, conf: conf, lineType: lineType, ownFt: ownFt, neighborFt: neighborFt,
           offsetRing: offsetRing, offsetLine: offsetLine, ownedSide: ownedSide, build: build, check: check };
});
