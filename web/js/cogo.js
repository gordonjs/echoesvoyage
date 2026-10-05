/*
 * Parcel RTK — COGO (coordinate geometry) on a local plane.
 * Coordinates are {e, n} in US survey feet (east, north). Azimuths are degrees,
 * clockwise from north. Pure functions, no DOM; runs in the browser and in node.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else (root.PRTK = root.PRTK || {}).cogo = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var D2R = Math.PI / 180, R2D = 180 / Math.PI;

  // ---- angles & bearings -------------------------------------------------------------
  function parseAngle(s) {            // "20°34'00\"" | "20 34 00" | 20.5667  -> degrees
    if (typeof s === 'number') return s;
    var nums = String(s).match(/\d+(?:\.\d+)?/g);
    if (!nums) throw new Error('bad angle: ' + s);
    var d = +nums[0], m = +(nums[1] || 0), x = +(nums[2] || 0);
    if (m >= 60 || x >= 60) throw new Error('bad angle (minutes/seconds >= 60): ' + s);
    return d + m / 60 + x / 3600;
  }
  function parseBearing(s) {          // "N 10°27'03\" E" -> azimuth degrees
    var t = String(s).toUpperCase().replace(/\s+/g, ' ').trim();
    var q1 = t.charAt(0), q2 = t.charAt(t.length - 1);
    if ('NS'.indexOf(q1) < 0 || 'EW'.indexOf(q2) < 0) throw new Error('bad bearing: ' + s);
    var a = parseAngle(t.slice(1, -1));
    if (a > 90) throw new Error('bearing angle > 90°: ' + s);
    if (q1 === 'N' && q2 === 'E') return a;
    if (q1 === 'S' && q2 === 'E') return 180 - a;
    if (q1 === 'S' && q2 === 'W') return 180 + a;
    return (360 - a) % 360;
  }
  function norm360(a) { a %= 360; return a < 0 ? a + 360 : a; }
  function angDiff(a, b) { var d = norm360(a - b); return d > 180 ? d - 360 : d; }   // a-b in (-180,180]
  function fmtDms(deg, secDecimals) {
    var neg = deg < 0; deg = Math.abs(deg);
    var p = Math.pow(10, secDecimals || 0);
    var s = Math.round(deg * 3600 * p) / p, d = Math.floor(s / 3600 + 1e-12); s -= d * 3600;
    var m = Math.floor(s / 60 + 1e-12); s -= m * 60;
    var ss = s.toFixed(secDecimals || 0); if (+ss < 10) ss = '0' + ss;
    return (neg ? '-' : '') + d + '°' + (m < 10 ? '0' : '') + m + "'" + ss + '"';
  }
  function fmtBearing(az) {
    az = norm360(az);
    var q1, q2, a;
    if (az <= 90) { q1 = 'N'; q2 = 'E'; a = az; }
    else if (az <= 180) { q1 = 'S'; q2 = 'E'; a = 180 - az; }
    else if (az <= 270) { q1 = 'S'; q2 = 'W'; a = az - 180; }
    else { q1 = 'N'; q2 = 'W'; a = 360 - az; }
    var s = fmtDms(a);
    return q1 + ' ' + (s.indexOf('°') === 1 ? '0' : '') + s + ' ' + q2;      // "S 03°33'32\" W", as deeds write it
  }

  // ---- basic vector ops ----------------------------------------------------------------
  function pt(e, n) { return { e: e, n: n }; }
  function polar(p, az, d) { return { e: p.e + d * Math.sin(az * D2R), n: p.n + d * Math.cos(az * D2R) }; }
  function dist(a, b) { return Math.hypot(b.e - a.e, b.n - a.n); }
  function azimuth(a, b) { return norm360(Math.atan2(b.e - a.e, b.n - a.n) * R2D); }

  // ---- traverse ------------------------------------------------------------------------
  /*
   * walk(start, courses, opts) follows deed calls from `start`.
   *   courses: [{type:'line', bearing, dist} | {type:'curve', dir:'L'|'R', radius, delta,
   *              chordBearing?, arc?, chord?}]   (+ any extra fields, copied to the result)
   *   opts.rot     : degrees added to every bearing (rotates the calls into another basis)
   *   opts.densify : max chord length (ft) when sampling curves (default 5)
   * A curve without chordBearing continues tangent to the previous course.
   * Returns { verts:[{e,n,s,course}], ends:[{e,n}], length, endAz, checks:[...] }
   *   verts.s is cumulative path length — used by the compass-rule adjustment.
   */
  function walk(start, courses, opts) {
    opts = opts || {};
    var rot = opts.rot || 0, maxChord = opts.densify || 5;
    var p = pt(start.e, start.n), s = 0, prevAz = null;
    var verts = [{ e: p.e, n: p.n, s: 0, course: -1 }], ends = [], checks = [];
    courses.forEach(function (c, i) {
      if (c.type === 'line') {
        var az = parseBearing(c.bearing) + rot;
        p = polar(p, az, c.dist); s += c.dist;
        verts.push({ e: p.e, n: p.n, s: s, course: i });
        prevAz = az;
      } else if (c.type === 'curve') {
        var delta = parseAngle(c.delta), left = c.dir === 'L', R = c.radius;
        var tin;
        if (c.chordBearing) {
          var caz = parseBearing(c.chordBearing) + rot;
          tin = caz + (left ? delta / 2 : -delta / 2);
          if (prevAz !== null) checks.push({ course: i, kind: 'tangent-in vs previous course', diffSec: angDiff(tin, prevAz) * 3600 });
          if (c.chord != null) checks.push({ course: i, kind: 'chord length', diffFt: 2 * R * Math.sin(delta * D2R / 2) - c.chord });
        } else {
          if (prevAz === null) throw new Error('curve ' + i + ' has no chord bearing and no previous course');
          tin = prevAz;
        }
        var arc = R * delta * D2R;
        if (c.arc != null) checks.push({ course: i, kind: 'arc length', diffFt: arc - c.arc });
        var centerAz = tin + (left ? -90 : 90);
        var ce = p.e + R * Math.sin(centerAz * D2R), cn = p.n + R * Math.cos(centerAz * D2R);
        var a0 = Math.atan2(p.n - cn, p.e - ce), sweep = (left ? 1 : -1) * delta * D2R;
        var steps = Math.max(2, Math.ceil(arc / maxChord));
        for (var k = 1; k <= steps; k++) {
          var a = a0 + sweep * k / steps;
          verts.push({ e: ce + R * Math.cos(a), n: cn + R * Math.sin(a), s: s + arc * k / steps, course: i });
        }
        p = { e: verts[verts.length - 1].e, n: verts[verts.length - 1].n };
        s += arc;
        prevAz = tin + (left ? -delta : delta);
      } else throw new Error('unknown course type: ' + c.type);
      ends.push(pt(p.e, p.n));
    });
    return { verts: verts, ends: ends, length: s, endAz: prevAz, checks: checks };
  }

  // Compass (Bowditch) rule: spread the misclosure `to - lastVertex` along the path in
  // proportion to distance travelled. Returns a new walk result; start stays fixed.
  function compassAdjust(w, to) {
    var last = w.verts[w.verts.length - 1];
    var de = to.e - last.e, dn = to.n - last.n, L = w.length || 1;
    function fix(v) { var f = v.s / L; return { e: v.e + de * f, n: v.n + dn * f, s: v.s, course: v.course }; }
    var verts = w.verts.map(fix);
    // course ends: find the last vertex of each course
    var ends = w.ends.map(function (_, i) {
      for (var j = verts.length - 1; j >= 0; j--) if (verts[j].course === i) return pt(verts[j].e, verts[j].n);
      return null;
    });
    return { verts: verts, ends: ends, length: w.length, endAz: w.endAz, checks: w.checks,
             misclosure: Math.hypot(de, dn), misclosureVec: { e: de, n: dn } };
  }

  // ---- polygon ops (rings of {e,n} or {x,y}; we use a getter-free {e,n} convention) ---
  function ringArea(r) {              // signed area (negative = clockwise)
    var A = 0;
    for (var i = 0, j = r.length - 1; i < r.length; j = i++) A += r[j].e * r[i].n - r[i].e * r[j].n;
    return A / 2;
  }
  function pointInRing(p, r) {
    var inside = false;
    for (var i = 0, j = r.length - 1; i < r.length; j = i++) {
      var a = r[i], b = r[j];
      if (((a.n > p.n) !== (b.n > p.n)) && (p.e < (b.e - a.e) * (p.n - a.n) / (b.n - a.n) + a.e)) inside = !inside;
    }
    return inside;
  }
  // nearest point on segment ab to p; also returns t (0..1) and signed side
  // (side > 0: p is to the RIGHT of a->b).
  function nearestOnSegment(p, a, b) {
    var de = b.e - a.e, dn = b.n - a.n, L2 = de * de + dn * dn;
    var t = L2 > 0 ? ((p.e - a.e) * de + (p.n - a.n) * dn) / L2 : 0;
    t = Math.max(0, Math.min(1, t));
    var q = { e: a.e + t * de, n: a.n + t * dn };
    var cross = de * (p.n - a.n) - dn * (p.e - a.e);     // >0 : left of a->b
    return { point: q, t: t, d: Math.hypot(p.e - q.e, p.n - q.n), side: cross > 0 ? -1 : 1 };
  }
  // nearest point on a polyline (array of {e,n}); returns {d, point, index, t, side, along}
  function nearestOnPolyline(p, line) {
    var best = null, along = 0, acc = 0;
    for (var i = 0; i < line.length - 1; i++) {
      var r = nearestOnSegment(p, line[i], line[i + 1]);
      var segL = dist(line[i], line[i + 1]);
      if (!best || r.d < best.d) { best = r; best.index = i; along = acc + r.t * segL; }
      acc += segL;
    }
    if (best) { best.along = along; best.length = acc; }
    return best;
  }

  return {
    D2R: D2R, R2D: R2D,
    parseAngle: parseAngle, parseBearing: parseBearing, fmtDms: fmtDms, fmtBearing: fmtBearing,
    norm360: norm360, angDiff: angDiff,
    pt: pt, polar: polar, dist: dist, azimuth: azimuth,
    walk: walk, compassAdjust: compassAdjust,
    ringArea: ringArea, pointInRing: pointInRing,
    nearestOnSegment: nearestOnSegment, nearestOnPolyline: nearestOnPolyline
  };
});
