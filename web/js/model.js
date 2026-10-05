/*
 * Parcel RTK — builds one consistent parcel model from the two recorded documents.
 *
 * Local frame: US survey feet, origin (0,0) at the SHARED CORNER (Lot 1 NW = 35-ac TPB),
 * bearings in the basis of the 1996 Lodgepole Pines survey.
 *
 * How the documents are fitted together (see the analysis in CLAUDE.md):
 *  1. Lodgepole Pines is a field-measured, monumented survey that closes to 0.001 ft,
 *     so its monuments and lines are held fixed.
 *  2. The 35-ac deed contains a typo (curve radius 340.57 for 348.57 — see data.errata).
 *     Corrected, it closes on itself to 0.01 ft, so it is kept as a rigid shape.
 *  3. The deed shares two monuments with Lodgepole: the shared corner (TPB) and the
 *     corner at the road (LPNE). The deed is rotated about TPB so its road corner lands
 *     on the surveyed one — a basis-of-bearing difference of about -0°01'59", the same
 *     one the plat itself shows between record and measured for that line. Whatever is
 *     left (a few hundredths of a foot) is spread by the compass rule.
 *  4. The two documents disagree by ~2'32" on the angle at the shared corner, so the
 *     deed's NW corner falls ~0.7 ft off the straight extension of Lot 1's west line.
 *     That is reported, not hidden; measuring that monument settles it.
 *  5. In the field, measured monuments override all of this (see calib.js).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./cogo.js'), require('./data.js'));
  else { var P = root.PRTK = root.PRTK || {}; P.model = factory(P.cogo, P.data); }
})(typeof self !== 'undefined' ? self : this, function (cogo, DATA) {
  'use strict';

  var ROAD = 'Shadow Mountain Dr — west right-of-way';

  function build(data) {
    data = data || DATA;
    var lp = data.lp, deed = data.deed;
    var points = {}, segments = {}, checks = [];

    function put(id, p, meta) {
      points[id] = Object.assign({ id: id, e: p.e, n: p.n }, data.points[id] || {}, meta || {});
    }
    function P(id) { return { e: points[id].e, n: points[id].n }; }
    function seg(id, a, b, verts, meta) {
      segments[id] = Object.assign({ id: id, a: a, b: b, verts: verts.map(function (v) { return { e: v.e, n: v.n }; }), rings: [] }, meta);
    }
    function lineSeg(id, a, b, meta) { seg(id, a, b, [P(a), P(b)], Object.assign({ kind: 'line' }, meta)); }

    // ---- 1. Lodgepole Pines, survey basis -------------------------------------------
    var azW = cogo.parseBearing(lp.westLine.bearing);     // W16 -> TPB
    var azS = cogo.parseBearing(lp.southLine.bearing);    // S14 -> W16
    var azN = cogo.parseBearing(lp.northLine.bearing);    // TPB -> road
    var TPB = cogo.pt(0, 0);
    var W16 = cogo.polar(TPB, azW + 180, lp.westLine.dist);
    put('TPB', TPB);
    put('W16', W16);
    put('S14', cogo.polar(W16, azS + 180, lp.southLine.dist));
    put('L1SE', cogo.polar(W16, azS + 180, lp.lot1.southDist));
    put('L1NE', cogo.polar(TPB, azN, lp.lot1.northDist));
    put('LPNE', cogo.polar(TPB, azN, lp.northLine.dist));

    var lot12 = cogo.polar(P('L1SE'), cogo.parseBearing(lp.lot1.eastLine.bearing), lp.lot1.eastLine.dist);
    checks.push({ id: 'lot12', label: 'Lot 1/2 line (N 10°35\'01" E 946.69) lands on the Lot 1 NE corner', ft: cogo.dist(lot12, P('L1NE')), good: 0.05 });

    var rowRaw = cogo.walk(P('LPNE'), lp.row);
    var row = cogo.compassAdjust(rowRaw, P('S14'));
    checks.push({ id: 'lpClose', label: 'Lodgepole Pines boundary closes (all 11 calls)', ft: row.misclosure, good: 0.05 });
    for (var i = 0; i < 7; i++) {
      put('ROW' + (i + 1), row.ends[i], {
        name: 'Road point ' + (i + 1) + ' (Lodgepole frontage)',
        monument: 'Point of curve/tangency on the west right-of-way of Shadow Mountain Dr. 1/2" rebar with 1" plastic cap "LS 26296" (plat note 2).',
        conf: 'note2', group: 'road'
      });
    }

    // ---- 2. 35-acre deed: correct errata, keep rigid, rotate onto the shared monuments --
    var asWritten = cogo.walk(TPB, deed.courses);
    var writtenMis = cogo.dist(asWritten.verts[asWritten.verts.length - 1], TPB);
    var courses = applyErrata(deed.courses, deed.errata);
    (deed.errata || []).forEach(function (er) {
      checks.push({ id: 'errata', label: 'Deed typo corrected: course ' + (er.course + 1) + ' ' + er.field + ' ' + er.written + ' → ' + er.corrected,
                    why: er.why, info: true });
    });
    var selfWalk = cogo.walk(TPB, courses);
    var selfMis = cogo.dist(selfWalk.verts[selfWalk.verts.length - 1], TPB);
    checks.push({ id: 'deedWritten', label: '35-ac deed closes on itself — as written', ft: writtenMis, ratio: selfWalk.length / writtenMis, info: true });
    checks.push({ id: 'deedSelf', label: '35-ac deed closes on itself — typo corrected', ft: selfMis, ratio: selfWalk.length / selfMis, good: 0.05 });

    var body = courses.slice(0, 10);                                      // TPB ... -> road corner (drop the closing call)
    var dRaw0 = cogo.walk(TPB, body);
    var rot = cogo.angDiff(cogo.azimuth(TPB, P('LPNE')), cogo.azimuth(TPB, dRaw0.verts[dRaw0.verts.length - 1]));
    var dRaw = cogo.walk(TPB, body, { rot: rot });
    var dAdj = cogo.compassAdjust(dRaw, P('LPNE'));
    checks.push({ id: 'deedVsSurvey', label: '35-ac deed vs the surveyed road corner, after rotating ' + cogo.fmtDms(rot), ft: dAdj.misclosure, good: 0.25 });
    put('P1NW', dAdj.ends[0]);
    put('P1NE', dAdj.ends[1]);
    var wl = cogo.nearestOnSegment(P('P1NW'), P('W16'), cogo.polar(P('W16'), azW, 5000));
    checks.push({ id: 'p1nwLine', label: '35-ac NW corner vs the straight extension of Lot 1\'s west line', ft: wl.d, good: 1.0, info: true });
    for (i = 1; i <= 7; i++) {
      put('P1R' + i, dAdj.ends[i + 1], {
        name: 'Road point ' + i + ' (35-ac frontage)',
        monument: 'Point of curve/tangency on the west right-of-way of Shadow Mountain Dr, from the deed. Monument not described.',
        conf: 'none', group: 'p1'
      });
    }
    // curve-geometry self checks (arc vs R·Δ, chord, tangency) for both documents
    rowRaw.checks.concat(dRaw.checks).forEach(function (c) {
      if (c.diffFt != null) checks.push({ id: 'geom', label: c.kind, ft: Math.abs(c.diffFt), good: 0.05, info: true });
    });

    // ---- 3. segments (each boundary piece defined once) -----------------------------
    function walkSegs(prefix, w, startId, endIds, courses, nameFn, sigma) {
      var prev = startId;
      courses.forEach(function (c, ci) {
        var verts = [P(prev)];
        w.verts.forEach(function (v) { if (v.course === ci) verts.push(v); });
        verts[verts.length - 1] = P(endIds[ci]);                       // snap exactly to the shared point
        seg(prefix + (ci + 1), prev, endIds[ci], verts, {
          kind: c.type === 'curve' ? 'arc' : 'line', name: nameFn(ci, c), call: callText(c), sigmaFt: sigma
        });
        prev = endIds[ci];
      });
    }
    walkSegs('p1b', dAdj, 'TPB', ['P1NW', 'P1NE', 'P1R1', 'P1R2', 'P1R3', 'P1R4', 'P1R5', 'P1R6', 'P1R7', 'LPNE'], body,
      function (ci) { return ci === 0 ? 'West line — 35-ac (W line of E½ SW¼ Sec 5)' : ci === 1 ? 'North line — 35-ac' : ROAD; }, 0.35);
    lineSeg('shS', 'L1NE', 'LPNE', { name: 'South line — 35-ac / Lodgepole Lots 2–3', call: callText(lp.northLine) + ' (part)', sigmaFt: 0.05 });
    lineSeg('shI', 'TPB', 'L1NE', { name: 'Line between your two parcels (35-ac / Lot 1)', call: callText(lp.northLine) + ' (first 641.00)', sigmaFt: 0.05 });
    lineSeg('l1e', 'L1SE', 'L1NE', { name: 'Lot 1 east line (Lot 1 / Lot 2)', call: callText(lp.lot1.eastLine), sigmaFt: 0.05 });
    lineSeg('l1s', 'W16', 'L1SE', { name: 'Lot 1 south line (section line; Black Mtn Ranch Est.)', call: 'S 84°58\'14" E 641.00 (along the basis of bearing)', sigmaFt: 0.05 });
    lineSeg('l1w', 'W16', 'TPB', { name: 'Lot 1 west line (Black Mtn Ranch Est. Fil. 4)', call: callText(lp.westLine), sigmaFt: 0.05 });
    walkSegs('lpr', row, 'LPNE', ['ROW1', 'ROW2', 'ROW3', 'ROW4', 'ROW5', 'ROW6', 'ROW7', 'S14'], lp.row,
      function () { return ROAD; }, 0.05);
    lineSeg('lps', 'L1SE', 'S14', { name: 'Lots 2–3 south line (section line)', call: 'S 84°58\'14" E 681.54 (along the basis of bearing)', sigmaFt: 0.05 });

    // ---- 4. rings (clockwise), ownership --------------------------------------------
    var rings = {
      P1: { name: '35-acre parcel — 31652 Shadow Mountain Dr', short: '35-ac parcel', owned: true, color: '#ff6b5b',
            segs: [['p1b1', 1], ['p1b2', 1], ['p1b3', 1], ['p1b4', 1], ['p1b5', 1], ['p1b6', 1], ['p1b7', 1], ['p1b8', 1], ['p1b9', 1], ['p1b10', 1], ['shS', -1], ['shI', -1]] },
      LOT1: { name: 'Lodgepole Pines Lot 1', short: 'Lot 1', owned: true, color: '#4ea1ff',
              segs: [['shI', 1], ['l1e', -1], ['l1s', -1], ['l1w', 1]] },
      LP23: { name: 'Lodgepole Pines Lots 2 & 3 (neighbors)', short: 'Lots 2–3 (neighbor)', owned: false, color: '#9aa7b4',
              segs: [['shS', 1], ['lpr1', 1], ['lpr2', 1], ['lpr3', 1], ['lpr4', 1], ['lpr5', 1], ['lpr6', 1], ['lpr7', 1], ['lpr8', 1], ['lps', -1], ['l1e', 1]] }
    };
    Object.keys(rings).forEach(function (rid) {
      var r = rings[rid]; r.id = rid; r.verts = [];
      var last = null;
      r.segs.forEach(function (sd) {
        var s = segments[sd[0]]; s.rings.push(rid);
        var from = sd[1] > 0 ? s.a : s.b;
        if (last !== null && last !== from) throw new Error('ring ' + rid + ' breaks at ' + sd[0]);
        var vs = sd[1] > 0 ? s.verts : s.verts.slice().reverse();
        vs.forEach(function (v, k) { if (k > 0 || r.verts.length === 0) r.verts.push({ e: v.e, n: v.n, seg: sd[0] }); });
        last = sd[1] > 0 ? s.b : s.a;
      });
      var s0 = segments[r.segs[0][0]], startId = r.segs[0][1] > 0 ? s0.a : s0.b;
      if (last !== startId) throw new Error('ring ' + rid + ' does not close');
      r.verts.pop();                                                     // last vertex repeats the first
      r.areaAc = Math.abs(cogo.ringArea(r.verts)) / 43560;
    });
    // segment roles: internal (between two owned parcels), boundary (edge of your land), neighbor-only
    Object.keys(segments).forEach(function (id) {
      var s = segments[id], owned = s.rings.filter(function (r) { return rings[r].owned; }).length;
      s.role = owned >= 2 ? 'internal' : owned === 1 ? 'boundary' : 'neighbor';
    });

    var A = lp.areasAc;
    checks.push({ id: 'aLot1', label: 'Lot 1 area vs plat (' + A.lot1 + ' ac)', ac: rings.LOT1.areaAc - A.lot1, good: 0.01 });
    checks.push({ id: 'aLP', label: 'Lodgepole Pines area vs plat (' + A.subdivision + ' ac)', ac: rings.LOT1.areaAc + rings.LP23.areaAc - A.subdivision, good: 0.01 });
    checks.push({ id: 'aLP23', label: 'Lots 2 + 3 area vs plat (' + (A.lot2 + A.lot3).toFixed(2) + ' ac)', ac: rings.LP23.areaAc - (A.lot2 + A.lot3), good: 0.02 });

    return {
      points: points, segments: segments, rings: rings, order: ['P1', 'LOT1', 'LP23'],
      checks: checks,
      meta: {
        units: 'US survey feet', origin: 'TPB',
        deedRotationDeg: rot,
        recordVsMeasured: recordRotations(lp)
      }
    };
  }

  function applyErrata(courses, errata) {
    var out = courses.map(function (c) { return Object.assign({}, c); });
    (errata || []).forEach(function (er) {
      var c = out[er.course];
      if (!c || c[er.field] !== er.written) throw new Error('erratum does not match the data: course ' + er.course);
      c[er.field] = er.corrected; c.corrected = true;
    });
    return out;
  }

  function callText(c) {
    if (!c) return '';
    if (c.type === 'curve') {
      return 'Curve ' + (c.dir === 'L' ? 'left' : 'right') + ', R ' + c.radius.toFixed(2) + ', Δ ' + c.delta +
             (c.arc != null ? ', arc ' + c.arc.toFixed(2) : '') + (c.chordBearing ? ', chord ' + c.chordBearing : '');
    }
    return c.bearing + ' ' + c.dist.toFixed(2);
  }
  function recordRotations(lp) {   // record -> measured rotation (seconds), per line
    function d(m, r) { return cogo.angDiff(cogo.parseBearing(m), cogo.parseBearing(r)) * 3600; }
    var road = lp.rowRecord.map(function (rr) {
      var c = lp.row[rr.i];
      return rr.chordBearing ? d(c.chordBearing, rr.chordBearing) : d(c.bearing, rr.bearing);
    });
    return { westSec: d(lp.westLine.bearing, lp.westLine.record.bearing),
             northSec: d(lp.northLine.bearing, lp.northLine.record.bearing),
             roadSec: road };
  }

  // All points as a list in a stable, useful order for the calibration UI.
  function controlList(model) {
    var order = ['TPB', 'W16', 'L1NE', 'L1SE', 'LPNE', 'P1NW', 'P1NE', 'S14',
                 'ROW1', 'ROW2', 'ROW3', 'ROW4', 'ROW5', 'ROW6', 'ROW7',
                 'P1R1', 'P1R2', 'P1R3', 'P1R4', 'P1R5', 'P1R6', 'P1R7'];
    return order.filter(function (id) { return model.points[id]; }).map(function (id) { return model.points[id]; });
  }

  return { build: build, controlList: controlList, callText: callText };
});
