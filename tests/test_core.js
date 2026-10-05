#!/usr/bin/env node
/*
 * Core tests for the web app's math & data modules. No dependencies: `node tests/test_core.js`
 * Covers: bearings, the parcel model against the recorded documents, geodesy, calibration
 * (synthetic truth with noise/outliers), live location answers, NMEA, state ops + v1 migration.
 */
'use strict';
const path = require('path');
const J = (f) => require(path.join(__dirname, '..', 'web', 'js', f));
const cogo = J('cogo.js'), geo = J('geodesy.js'), modelLib = J('model.js'), calib = J('calib.js');
const locate = J('locate.js'), nmea = J('nmea.js'), stateLib = J('state.js'), DATA = J('data.js');

let passed = 0, failed = 0;
const fails = [];
function test(name, fn) {
  try { fn(); passed++; process.stdout.write('.'); }
  catch (e) { failed++; fails.push(name + '\n    ' + (e && e.stack ? e.stack.split('\n').slice(0, 3).join('\n    ') : e)); process.stdout.write('F'); }
}
function ok(c, msg) { if (!c) throw new Error(msg || 'assertion failed'); }
function near(a, b, tol, msg) { if (!(Math.abs(a - b) <= tol)) throw new Error((msg || 'near') + `: ${a} vs ${b} (tol ${tol})`); }
function eqDeep(a, b, msg) { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error((msg || 'eqDeep') + `\n      got ${x}\n      exp ${y}`); }
function partial(obj, exp, where) {
  for (const k of Object.keys(exp)) {
    const p = (where ? where + '.' : '') + k;
    if (exp[k] !== null && typeof exp[k] === 'object' && !Array.isArray(exp[k])) {
      ok(obj[k] && typeof obj[k] === 'object', 'missing object ' + p);
      if (Object.keys(exp[k]).length === 0) eqDeep(obj[k], {}, p); else partial(obj[k], exp[k], p);
    } else eqDeep(obj[k], exp[k], p);
  }
}

const M = modelLib.build();
const K = geo.US_FT;

// ------------------------------------------------------------------ bearings
test('parse bearings in all four quadrants and odd spacing', () => {
  near(cogo.parseBearing('N 10°27\'03" E'), 10 + 27 / 60 + 3 / 3600, 1e-12);
  near(cogo.parseBearing('S69°12\'32"E'), 180 - (69 + 12 / 60 + 32 / 3600), 1e-12);
  near(cogo.parseBearing('s 03 33 32 w'), 180 + 3 + 33 / 60 + 32 / 3600, 1e-12);
  near(cogo.parseBearing('N 89°02\'24" W'), 360 - (89 + 2 / 60 + 24 / 3600), 1e-12);
  near(cogo.parseBearing('N 0 0 0 E'), 0, 1e-12);
});
test('bad bearings and angles are rejected', () => {
  for (const b of ['X 10 E', 'N 10 27 03 Q', 'N 91 00 00 E', 'N 10 60 00 E']) {
    let threw = false; try { cogo.parseBearing(b); } catch (e) { threw = true; } ok(threw, 'accepted ' + b);
  }
});
test('format bearing round-trips', () => {
  for (const b of ['N 10°27\'36" E', 'S 76°36\'49" E', 'S 03°33\'32" W', 'N 84°58\'14" W'])
    eqDeep(cogo.fmtBearing(cogo.parseBearing(b)), b.replace(/°0/, '°0'));
});

// ------------------------------------------------------------------ model vs documents
const chk = Object.fromEntries(M.checks.filter(c => c.id !== 'geom' && c.id !== 'errata').map(c => [c.id, c]));
test('Lodgepole Pines closes (11 calls) to < 0.01 ft', () => ok(chk.lpClose.ft < 0.01, chk.lpClose.ft));
test('Lot 1/2 line lands on Lot 1 NE corner within 0.01 ft', () => ok(chk.lot12.ft < 0.01));
test('every curve is internally consistent (arc/chord) once errata applied', () => {
  for (const c of M.checks.filter(c => c.id === 'geom')) ok(c.ft < 0.01, c.label + ' ' + c.ft);
});
test('deed as written misses by 2.25 ft; corrected closes < 0.05 ft', () => {
  near(chk.deedWritten.ft, 2.251, 0.005); ok(chk.deedSelf.ft < 0.05, chk.deedSelf.ft);
});
test('corrected deed fits the shared monuments within 0.05 ft after ~-1\'58"', () => {
  ok(chk.deedVsSurvey.ft < 0.05, chk.deedVsSurvey.ft);
  near(M.meta.deedRotationDeg * 3600, -118, 3, 'rotation seconds');
});
test('areas match the plat: Lot 1, subdivision, Lots 2+3', () => {
  near(M.rings.LOT1.areaAc, 14.57, 0.01); near(M.rings.LOT1.areaAc + M.rings.LP23.areaAc, 35.08, 0.01);
  near(M.rings.LP23.areaAc, 20.51, 0.02); near(M.rings.P1.areaAc, 35.09, 0.05);
});
test('rings are clockwise and close without repeated vertices', () => {
  for (const id of M.order) {
    const v = M.rings[id].verts; ok(cogo.ringArea(v) < 0, id + ' not clockwise');
    for (let i = 0; i < v.length; i++) { const j = (i + 1) % v.length; ok(Math.hypot(v[i].e - v[j].e, v[i].n - v[j].n) > 1e-6, id + ' repeated vertex ' + i); }
  }
});
test('segment roles: shared parcel line is internal, neighbor lines are boundary', () => {
  eqDeep(M.segments.shI.role, 'internal'); eqDeep(M.segments.shS.role, 'boundary');
  eqDeep(M.segments.l1e.role, 'boundary'); eqDeep(M.segments.lpr1.role, 'neighbor');
});
test('LPNE is shared by the deed road and the Lodgepole road exactly', () => {
  const a = M.segments.p1b10.verts.at(-1), b = M.segments.lpr1.verts[0];
  ok(a.e === b.e && a.n === b.n);
});
test('data stays as written (errata only in the model)', () => {
  eqDeep(DATA.deed.courses[4].radius, 340.57); eqDeep(DATA.deed.errata[0].corrected, 348.57);
});

// ------------------------------------------------------------------ geodesy
test('ECEF round trip < 0.1 mm', () => {
  const e = geo.toEcef(39.5123456, -105.3123456, 2641.2), g = geo.fromEcef(...e);
  near(g.lat, 39.5123456, 1e-11); near(g.lon, -105.3123456, 1e-11); near(g.h, 2641.2, 1e-4);
});
test('ENU frame round trip < 1 mm at 1 km', () => {
  const f = geo.frame({ lat: 39.5, lon: -105.3, h: 2640 }), g = f.toGeo(812.3, -455.6, 0), q = f.toEnu(g.lat, g.lon, g.h);
  near(q.x, 812.3, 1e-3); near(q.y, -455.6, 1e-3);
});
test('ENU distances are ground distances at site height (not sea level)', () => {
  // same lat/lon pair measured at h=0 and h=2640: the 2640 m one is ~415 ppm longer
  const a = { lat: 39.50, lon: -105.30 }, b = { lat: 39.505, lon: -105.295 };
  const d0 = geo.distBearing({ ...a, h: 0 }, { ...b, h: 0 }).dist, d1 = geo.distBearing({ ...a, h: 2640 }, { ...b, h: 2640 }).dist;
  near((d1 / d0 - 1) * 1e6, 414, 3, 'ppm');
});

// ------------------------------------------------------------------ calibration
function truthPl(theta, lat, lon, h) { return calib.placeAt(0, 0, lat, lon, h, theta); }
function measure(pl, id, noise) {
  const p = M.points[id], q = calib.fwd(pl, p.e, p.n), f = calib.frameOf(pl);
  const g = f.toGeo(q.x + (noise ? noise[0] : 0), q.y + (noise ? noise[1] : 0), 0);
  return { lat: g.lat, lon: g.lon, h: g.h, sigma: 0.012, use: true };
}
const TRUTH = truthPl(17.3, 39.5512, -105.2987, 2641);
test('4-monument fit recovers a known placement exactly', () => {
  const control = {}; for (const id of ['TPB', 'W16', 'LPNE', 'P1NE']) control[id] = measure(TRUTH, id);
  const sol = calib.solve(M, { control, settings: {} });
  eqDeep(sol.kind, 'fit'); ok(sol.rms < 1e-6, 'rms ' + sol.rms);
  for (const id of ['L1SE', 'S14', 'P1NW']) {
    const a = calib.local2geo(sol.placement, M.points[id].e, M.points[id].n), b = calib.local2geo(TRUTH, M.points[id].e, M.points[id].n);
    ok(geo.distBearing({ ...a, h: 2641 }, { ...b, h: 2641 }).dist < 1e-4, id);
  }
  near(sol.scalePpm, 0, 0.5, 'scale ppm');
});
test('noise of ~1 cm shows up as ~1 cm rms', () => {
  const control = {}, n = [[0.01, -0.004], [-0.008, 0.009], [0.004, 0.012], [-0.006, -0.011], [0.011, 0.002]];
  ['TPB', 'W16', 'LPNE', 'P1NE', 'S14'].forEach((id, i) => control[id] = measure(TRUTH, id, n[i]));
  const sol = calib.solve(M, { control, settings: {} });
  ok(sol.rms > 0.004 && sol.rms < 0.02, 'rms ' + sol.rms);
  ok(sol.residuals.every(r => r.level === 'ok'));
});
test('a disturbed monument (0.6 m off) is flagged bad', () => {
  const control = {};
  for (const id of ['TPB', 'W16', 'LPNE', 'P1NE']) control[id] = measure(TRUTH, id, id === 'P1NE' ? [0.6, 0] : null);
  const sol = calib.solve(M, { control, settings: {} });
  const r = Object.fromEntries(sol.residuals.map(x => [x.id, x]));
  eqDeep(r.P1NE.level, 'bad'); ok(sol.warnings.length >= 1);
});
test('two monuments give a distance check against the plat', () => {
  const W = M.points.W16, T = M.points.TPB, ux = (T.e - W.e), uy = (T.n - W.n), L = Math.hypot(ux, uy);
  const control = { W16: measure(TRUTH, 'W16'), TPB: measure(TRUTH, 'TPB') };
  // push TPB 0.10 ft further along the line (in true ENU, rotate local dir by truth theta)
  const th = 17.3 * Math.PI / 180, dx = (ux / L) * Math.cos(th) - (uy / L) * Math.sin(th), dy = (ux / L) * Math.sin(th) + (uy / L) * Math.cos(th);
  control.TPB = measure(TRUTH, 'TPB', [dx * 0.10 * K, dy * 0.10 * K]);
  const sol = calib.solve(M, { control, settings: {} });
  near(sol.distCheck.diffFt, 0.10, 0.002); near(sol.distCheck.platFt, 1040.08, 0.01);
});
test('hold monuments: outline passes exactly through each measured monument', () => {
  const control = {};
  for (const id of ['TPB', 'W16', 'LPNE', 'P1NE']) control[id] = measure(TRUTH, id, id === 'LPNE' ? [0.05, -0.03] : null);
  const sol = calib.solve(M, { control, settings: { hold: true } }), gm = calib.geometry(M, sol);
  const f = calib.frameOf(sol.placement);
  for (const id of Object.keys(control)) {
    const c = control[id], q = f.toEnu(c.lat, c.lon, c.h), g = gm.pts[id];
    ok(Math.hypot(q.x - g.x, q.y - g.y) < 1e-3, id + ' off by ' + Math.hypot(q.x - g.x, q.y - g.y));
  }
  // shared corner identical in both of your parcels' outlines
  const a = gm.rings.P1.xy.find(v => v.seg === 'p1b1'), b = gm.rings.LOT1.xy[0];
  ok(Math.hypot(a.x - b.x, a.y - b.y) < 1e-9, 'TPB differs between rings');
});
test('one monument + rough rotation: exact at the monument, rotation kept', () => {
  const rough = truthPl(17.3 + 0.0, 39.5512, -105.2987, 2641);
  const rough2 = calib.translate(rough, 3.0, -2.0);                    // rough is 3.6 m off
  const sol = calib.solve(M, { rough: rough2, control: { W16: measure(TRUTH, 'W16') }, settings: {} });
  eqDeep(sol.kind, 'one');
  const g = calib.local2geo(sol.placement, M.points.W16.e, M.points.W16.n), t = calib.local2geo(TRUTH, M.points.W16.e, M.points.W16.n);
  ok(geo.distBearing({ ...g, h: 2641 }, { ...t, h: 2641 }).dist < 1e-4);
  const far = calib.local2geo(sol.placement, M.points.P1NE.e, M.points.P1NE.n), farT = calib.local2geo(TRUTH, M.points.P1NE.e, M.points.P1NE.n);
  ok(geo.distBearing({ ...far, h: 2641 }, { ...farT, h: 2641 }).dist < 0.01, 'rotation not carried over');
});
test('rotateAbout keeps the pivot fixed', () => {
  const p = calib.rotateAbout(TRUTH, 500, -300, 25), a = calib.fwd(p, 500, -300), b = calib.fwd(TRUTH, 500, -300);
  near(a.x, b.x, 1e-9); near(a.y, b.y, 1e-9); near(calib.thetaDeg(p), 25, 1e-9);
});

// ------------------------------------------------------------------ live location answers
const solT = calib.solve(M, { rough: TRUTH, control: {}, settings: { roughSigmaFt: 0.01 } });
const gmT = calib.geometry(M, solT);
function at(e, n) { const q = calib.fwd(TRUTH, e, n); return locate.where(M, gmT, solT, q.x, q.y, 0.014, { hold: true }); }
function centroid(rid) { const v = M.rings[rid].verts; let x = 0, y = 0; v.forEach(p => { x += p.e; y += p.n; }); return [x / v.length, y / v.length]; }
test('inside Lot 1 -> IN Lot 1', () => { const w = at(...centroid('LOT1')); eqDeep(w.inOwned, 'LOT1'); eqDeep(w.status, 'IN'); });
test('inside the 35-ac -> IN, and the line to Lot 1 is internal, not your boundary', () => {
  const T = M.points.TPB, N = M.points.L1NE, mid = [(T.e + N.e) / 2, (T.n + N.n) / 2 + 20];   // 20 ft north of the shared line
  const w = at(...mid);
  eqDeep(w.inOwned, 'P1'); eqDeep(w.status, 'IN');
  // the line runs S76°36'49"E, so 20 ft due north is 20*cos(13.39°) ft away perpendicular
  near(w.internal.d / K, 20 * Math.cos((76 + 36 / 60 + 49 / 3600 - 90) * Math.PI / 180), 0.01, 'internal line distance');
  ok(w.edge.segId !== 'shI'); ok(w.edge.d / K > 100);
});
test('in a neighbor lot -> OUT and names it', () => { const w = at(...centroid('LP23')); eqDeep(w.status, 'OUT'); eqDeep(w.inNeighbor, 'LP23'); });
test('right at the line -> ON LINE', () => {
  const W = M.points.W16, T = M.points.TPB; const w = at((W.e + T.e) / 2 + 0.02, (W.n + T.n) / 2);
  eqDeep(w.status, 'ONLINE');
});
test('stake-out offset sign: east of Lot 1 west line (drawn S->N) is +right', () => {
  const W = M.points.W16, T = M.points.TPB, mid = [(W.e + T.e) / 2 + 3, (W.n + T.n) / 2];
  const q = calib.fwd(TRUTH, ...mid), s = locate.stakeout(M, gmT, 'l1w', q.x, q.y);
  ok(s.offset > 0, 'offset ' + s.offset); near(s.offset / K, 3 * Math.cos(10.46 * Math.PI / 180), 0.05);
  near(s.length / K, 1040.08, 0.01);
});

// ------------------------------------------------------------------ NMEA
function withCs(body) { let c = 0; for (const ch of body) c ^= ch.charCodeAt(0); return '$' + body + '*' + c.toString(16).toUpperCase().padStart(2, '0'); }
test('GGA with high-precision coordinates, ellipsoid height, correction age', () => {
  const g = nmea.parse(withCs('GNGGA,173501.00,3933.07261234,N,10517.92218765,W,4,18,0.55,2655.123,M,-15.432,M,1.0,0000'));
  near(g.lat, 39 + 33.07261234 / 60, 1e-12); near(g.lon, -(105 + 17.92218765 / 60), 1e-12);
  eqDeep(g.q, 4); near(g.h, 2639.691, 1e-9); eqDeep(g.age, 1); ok(g.valid);
});
test('bad checksum is rejected', () => {
  const s = withCs('GNGGA,173501.00,3933.07261234,N,10517.92218765,W,4,18,0.55,2655.1,M,-15.4,M,1.0,0000');
  ok(nmea.parse(s.replace('3933', '3934')) === null);
});
test('GST sigma and the fix-type fallback', () => {
  const s = nmea.parse(withCs('GNGST,173501.00,0.010,0.009,0.007,45.0,0.008,0.006,0.015'));
  near(s.sigmaH, 0.01, 1e-12);
  const g = nmea.parse(withCs('GNGGA,1,3933.0,N,10517.9,W,4,18,0.55,2655.1,M,-15.4,M,1.0,0000'));
  eqDeep(nmea.sigmaFor(g, s), { sigma: 0.01, est: false }); eqDeep(nmea.sigmaFor(g, null).est, true);
});
test('empty GGA (no fix yet) is parsed as not valid', () => {
  const g = nmea.parse(withCs('GNGGA,173501.00,,,,,0,00,99.99,,,,,,'));
  ok(!g.valid);
});

// ------------------------------------------------------------------ state ops (shared vectors)
const vectors = require(path.join(__dirname, 'ops_vectors.json'));
for (const v of vectors) test('ops: ' + v.name, () => {
  let s = stateLib.empty(), threw = null;
  try { for (const op of v.ops) s = stateLib.apply(s, op); } catch (e) { threw = e; }
  if (v.error) { ok(threw, 'expected an error'); return; }
  if (threw) throw threw;
  partial(s, v.expect);
});
test('ops never mutate the input state', () => {
  const s0 = stateLib.empty(), s1 = stateLib.apply(s0, { op: 'set', path: ['settings', 'hold'], value: false });
  eqDeep(s0.settings.hold, true); eqDeep(s1.settings.hold, false);
});

// ------------------------------------------------------------------ v1 migration
test('v1 had a 0.30% east-west scale error (why it "looked a bit off")', () => {
  const f = 39.55 * Math.PI / 180, v1 = 111132.954 * Math.cos(f);
  const p = geo.toEcef(39.55, 0, 0), q = geo.toEcef(39.55, 0.001, 0), tru = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]) / 0.001;
  near((tru / v1 - 1) * 100, 0.304, 0.002);
});
test('v1 rough placement migrates to the best rigid-plus-scale fit (within 0.6 m)', () => {
  const v1cal = { lat0: 39.55, lon0: -105.30, a1e: 120.5, a1n: -300.25, s: 0.3048, ang: 0.4, cos: Math.cos(0.4), sin: Math.sin(0.4),
                  mLat: 111132.954 - 559.822 * Math.cos(2 * 39.55 * Math.PI / 180) + 1.175 * Math.cos(4 * 39.55 * Math.PI / 180),
                  mLon: 111132.954 * Math.cos(39.55 * Math.PI / 180), platDistFt: 0, measDistM: 0 };
  const v1 = { pins: { 1: null, 2: null }, base: 'street', navKey: '', cal: v1cal,
               saved: [{ id: '17', lat: 39.551, lon: -105.301, alt: 2650, acc: 0.014, fix: 4, t: 1720000000000, note: 'NW pin' }] };
  const st = stateLib.migrateV1(v1, { model: M, calib });
  eqDeep(st.settings.base, 'street'); eqDeep(st.savedPoints.length, 1); eqDeep(st.savedPoints[0].note, 'NW pin');
  ok(st.rough && st.rough.kind === 'rough');
  // v1 drew space stretched 0.3% east-west, so no rotation+scale reproduces it exactly;
  // the migrated placement is the best fit and lands within ~0.6 m of where v1 drew it.
  for (const id of ['TPB', 'L1NE', 'L1SE', 'W16', 'LPNE']) {
    const p = M.points[id], de = p.e - v1cal.a1e, dn = p.n - v1cal.a1n;
    const x = v1cal.s * (de * v1cal.cos - dn * v1cal.sin), y = v1cal.s * (de * v1cal.sin + dn * v1cal.cos);
    const want = { lat: v1cal.lat0 + y / v1cal.mLat, lon: v1cal.lon0 + x / v1cal.mLon, h: 2640 };
    const got = calib.local2geo(st.rough, p.e, p.n);
    const d = geo.distBearing(want, { ...got, h: 2640 }).dist;
    ok(d < 0.6, id + ' ' + d);
  }
});
test('v1 pinned calibration migrates to control points', () => {
  const v1 = { pins: { 1: { corner: { key: 'p2:SW' }, lat: 39.54, lon: -105.31, n: 25 }, 2: { corner: { key: 'p1:NE' }, lat: 39.545, lon: -105.30, n: 25 } },
               cal: { lat0: 39.54, lon0: -105.31, s: 0.3048, cos: 1, sin: 0, a1e: 0, a1n: 0, mLat: 111000, mLon: 85000, pin1: {}, pin2: {} } };
  const st = stateLib.migrateV1(v1, { model: M, calib });
  ok(st.control.W16 && st.control.P1NE && st.control.W16.use === true && !st.rough);
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) { console.log('\n' + fails.join('\n\n')); process.exit(1); }
