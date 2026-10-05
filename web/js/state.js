/*
 * Parcel RTK — project state and the operations that change it.
 *
 * The same small op language is implemented by the bridge (rtk_bridge.py, ProjectStore)
 * so the laptop, a phone, and offline use all apply identical edits.
 * tests/ops_vectors.json is run against BOTH implementations.
 *
 *   {op:'set',    path:[...], value}        set (creating parent objects)
 *   {op:'del',    path:[...]}               delete a key
 *   {op:'push',   path:[...], value}        append to an array (value.id must be unique)
 *   {op:'remove', path:[...], id}           remove the array element with that id
 *   {op:'patch',  path:[...], id, value}    shallow-merge into the array element with that id
 *
 * path[0] must be one of TOP. Every successful op increments rev.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else (root.PRTK = root.PRTK || {}).state = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var TOP = ['rough', 'control', 'settings', 'savedPoints'];
  var DEFAULT_SETTINGS = { hold: true, fitScale: false, units: 'ft', base: 'sat', edgeAlertFt: 0, roughSigmaFt: 10 };

  function empty() {
    return { schema: 2, rev: 0, rough: null, control: {}, settings: clone(DEFAULT_SETTINGS), savedPoints: [] };
  }
  function clone(x) { return x == null ? x : JSON.parse(JSON.stringify(x)); }
  function isEmpty(s) {
    return !s || (!s.rough && Object.keys(s.control || {}).length === 0 && (s.savedPoints || []).length === 0);
  }
  function fail(msg) { throw new Error('bad op: ' + msg); }

  function apply(state, op) {
    var s = clone(state) || empty();
    if (!op || typeof op !== 'object') fail('not an object');
    var path = op.path;
    if (!Array.isArray(path) || path.length === 0) fail('path');
    if (TOP.indexOf(path[0]) < 0) fail('path must start with one of ' + TOP.join(','));
    path.forEach(function (k) { if (typeof k !== 'string' || k === '__proto__' || k === 'constructor' || k === 'prototype') fail('key'); });
    var parent = s, i;
    for (i = 0; i < path.length - 1; i++) {
      if (Array.isArray(parent[path[i]])) fail('array in path');
      if (parent[path[i]] == null || typeof parent[path[i]] !== 'object') {
        if (op.op === 'set') parent[path[i]] = {}; else fail('missing ' + path.slice(0, i + 1).join('.'));
      }
      parent = parent[path[i]];
    }
    var key = path[path.length - 1];
    switch (op.op) {
      case 'set': parent[key] = clone(op.value); break;
      case 'del': if (parent && Object.prototype.hasOwnProperty.call(parent, key)) delete parent[key]; else fail('missing ' + key); break;
      case 'push':
        if (!Array.isArray(parent[key])) fail('not an array');
        if (!op.value || op.value.id == null) fail('value.id');
        if (parent[key].some(function (x) { return x && x.id === op.value.id; })) fail('duplicate id');
        parent[key].push(clone(op.value)); break;
      case 'remove': case 'patch':
        if (!Array.isArray(parent[key])) fail('not an array');
        if (op.id == null) fail('id');
        var idx = -1;
        parent[key].forEach(function (x, j) { if (x && x.id === op.id) idx = j; });
        if (idx < 0) fail('no element ' + op.id);
        if (op.op === 'remove') parent[key].splice(idx, 1);
        else { if (!op.value || typeof op.value !== 'object' || op.value.id != null) fail('patch value'); Object.assign(parent[key][idx], clone(op.value)); }
        break;
      default: fail('unknown op ' + op.op);
    }
    s.rev = (state && state.rev || 0) + 1;
    return s;
  }

  // ---- migration from v1 (localStorage key 'parcelRTK') ------------------------------
  // v1 stored: {pins:{1,2}, base, navKey, saved:[...], cal:{lat0,lon0,s,cos,sin,a1e,a1n,mLat,mLon,pin1?,pin2?}}
  var V1_KEYS = { 'p1:TPB': 'TPB', 'p1:NW': 'P1NW', 'p1:NE': 'P1NE', 'p1:S': 'LPNE',
                  'p2:NW': 'TPB', 'p2:NE': 'L1NE', 'p2:SE': 'L1SE', 'p2:SW': 'W16',
                  'TPB': 'TPB', 'NW': 'P1NW', 'NE': 'P1NE', 'S': 'LPNE' };

  function migrateV1(v1, deps) {
    var st = empty();
    if (!v1 || typeof v1 !== 'object') return st;
    (v1.saved || []).forEach(function (p, i) {
      if (!p || !isFinite(p.lat) || !isFinite(p.lon)) return;
      st.savedPoints.push({ id: String(p.id || ('v1-' + i)), lat: p.lat, lon: p.lon, alt: p.alt != null ? p.alt : null,
                            sigma: p.acc != null ? p.acc : null, fix: p.fix != null ? p.fix : null,
                            t: p.t || 0, note: p.note || '', legacy: true });
    });
    var cal = v1.cal;
    var pinned = cal && cal.pin1 && cal.pin2;
    [1, 2].forEach(function (k) {
      var pin = v1.pins && v1.pins[k];
      if (!pin || !pin.corner || !isFinite(pin.lat)) return;
      var id = V1_KEYS[pin.corner.key];
      if (!id) return;
      st.control[id] = { lat: pin.lat, lon: pin.lon, h: null, sigma: null, n: pin.n || 1, fix: null,
                         t: 0, use: !!pinned, legacy: true };
    });
    if (cal && !pinned && deps && isFinite(cal.lat0) && isFinite(cal.s)) st.rough = roughFromV1(cal, deps);
    if (v1.base) st.settings.base = v1.base;
    return st;
  }
  // Re-express a v1 placement in the v2 form. v1's degree-scaling stretched east-west
  // distances by 0.30%, so this is a best fit over points whose local coordinates are the
  // same (or within a foot) in v1 and v2, spanning both parcels.
  function roughFromV1(cal, deps) {
    var model = deps.model, calib = deps.calib;
    function v1ll(e, n) {
      var de = e - cal.a1e, dn = n - cal.a1n;
      var x = cal.s * (de * cal.cos - dn * cal.sin), y = cal.s * (de * cal.sin + dn * cal.cos);
      return { lat: cal.lat0 + y / cal.mLat, lon: cal.lon0 + x / cal.mLon };
    }
    var ids = ['TPB', 'L1NE', 'L1SE', 'W16', 'LPNE'];
    var obs = ids.map(function (id) {
      var p = model.points[id], ll = v1ll(p.e, p.n);
      return { id: id, e: p.e, n: p.n, lat: ll.lat, lon: ll.lon, h: null };
    });
    var r = calib.fitPoints(obs, { scale: true, origin: { lat: cal.lat0, lon: cal.lon0 } });
    var pl = r.placement; pl.kind = 'rough'; pl.migratedFromV1 = true;
    return pl;
  }

  return { TOP: TOP, DEFAULT_SETTINGS: DEFAULT_SETTINGS, empty: empty, clone: clone, isEmpty: isEmpty,
           apply: apply, migrateV1: migrateV1, V1_KEYS: V1_KEYS };
});
