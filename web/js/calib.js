/*
 * Parcel RTK — georeferencing: tying the local parcel model (US ft) to the earth.
 *
 * A placement maps local (e, n) feet to ENU metres in its own frame:
 *     u = e*K, v = n*K                      (K = metres per US survey foot)
 *     x = A*u - B*v + tx,  y = B*u + A*v + ty
 * with A = s*cos(theta), B = s*sin(theta). s = 1 for a rigid fit (the default — plat
 * distances are ground distances and ENU is a true ground-scale plane).
 *
 * Ways a placement is made, from weakest to strongest:
 *   rough : you dragged/rotated the outline over the imagery
 *   one   : one measured monument; rotation borrowed from the rough placement
 *   fit   : 2+ measured monuments, weighted least squares. With 2+ you get residuals —
 *           the disagreement between where the monuments ARE and where the documents
 *           say they are — which v1's exact two-pin fit could never show you.
 * "Hold monuments" then bends the outline smoothly so it passes exactly through each
 * measured monument (monuments control over bearings and distances).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./geodesy.js'));
  else { var P = root.PRTK = root.PRTK || {}; P.calib = factory(P.geo); }
})(typeof self !== 'undefined' ? self : this, function (geo) {
  'use strict';
  var K = geo.US_FT, D2R = Math.PI / 180, R2D = 180 / Math.PI;
  var FT = 1 / K;

  var frames = {};
  function frameOf(pl) {
    var o = pl.origin, key = o.lat.toFixed(9) + ',' + o.lon.toFixed(9) + ',' + (o.h != null ? o.h.toFixed(3) : '');
    return frames[key] || (frames[key] = geo.frame(o));
  }
  function fwd(pl, e, n) { var u = e * K, v = n * K; return { x: pl.A * u - pl.B * v + pl.tx, y: pl.B * u + pl.A * v + pl.ty }; }
  function inv(pl, x, y) {
    var dx = x - pl.tx, dy = y - pl.ty, d = pl.A * pl.A + pl.B * pl.B;
    return { e: (pl.A * dx + pl.B * dy) / d * FT, n: (-pl.B * dx + pl.A * dy) / d * FT };
  }
  function local2geo(pl, e, n) { var p = fwd(pl, e, n), g = frameOf(pl).toGeo(p.x, p.y, 0); return { lat: g.lat, lon: g.lon }; }
  function geo2enu(pl, lat, lon, h) { return frameOf(pl).toEnu(lat, lon, h); }
  function geo2local(pl, lat, lon, h) { var q = geo2enu(pl, lat, lon, h); return inv(pl, q.x, q.y); }
  function thetaDeg(pl) { return Math.atan2(pl.B, pl.A) * R2D; }
  function scaleOf(pl) { return Math.hypot(pl.A, pl.B); }

  // rotation of a placement's local axes expressed in another frame (handles meridian
  // convergence when origins differ)
  function thetaIn(pl, f) {
    var a = local2geo(pl, 0, 0), b = local2geo(pl, 1000, 0);
    var qa = f.toEnu(a.lat, a.lon), qb = f.toEnu(b.lat, b.lon);
    return Math.atan2(qb.y - qa.y, qb.x - qa.x) * R2D;
  }

  // Place local point (e,n) at lat/lon with rotation thetaDeg (math angle of local +E axis).
  function placeAt(e, n, lat, lon, h, th) {
    var A = Math.cos(th * D2R), B = Math.sin(th * D2R), u = e * K, v = n * K;
    return { origin: { lat: lat, lon: lon, h: h != null ? h : geo.DEFAULT_H }, A: A, B: B,
             tx: -(A * u - B * v), ty: -(B * u + A * v), kind: 'rough' };
  }
  function translate(pl, dx, dy) { var p = Object.assign({}, pl); p.tx += dx; p.ty += dy; return p; }
  // rotate about local point (e,n), keeping it fixed
  function rotateAbout(pl, e, n, newThetaDeg) {
    var s = scaleOf(pl), c = fwd(pl, e, n), u = e * K, v = n * K;
    var A = s * Math.cos(newThetaDeg * D2R), B = s * Math.sin(newThetaDeg * D2R);
    return Object.assign({}, pl, { A: A, B: B, tx: c.x - (A * u - B * v), ty: c.y - (B * u + A * v) });
  }

  /*
   * Weighted least-squares fit of local points to measured lat/lon.
   * obs: [{id, e, n, lat, lon, h?, sigma? (m)}]; opts: {scale: bool, origin?, theta? (n=1)}
   */
  function fitPoints(obs, opts) {
    opts = opts || {};
    if (!obs.length) throw new Error('no observations');
    var origin = opts.origin;
    if (!origin) {
      var la = 0, lo = 0, hs = 0, hn = 0;
      obs.forEach(function (o) { la += o.lat; lo += o.lon; if (o.h != null) { hs += o.h; hn++; } });
      origin = { lat: la / obs.length, lon: lo / obs.length, h: hn ? hs / hn : geo.DEFAULT_H };
    } else if (origin.h == null) {
      var hs2 = 0, hn2 = 0; obs.forEach(function (o) { if (o.h != null) { hs2 += o.h; hn2++; } });
      origin = { lat: origin.lat, lon: origin.lon, h: hn2 ? hs2 / hn2 : geo.DEFAULT_H };
    }
    var f = geo.frame(origin);
    var P = obs.map(function (o) {
      var q = f.toEnu(o.lat, o.lon, o.h != null ? o.h : origin.h);
      var s = o.sigma > 0 ? Math.max(o.sigma, 0.005) : 0.02;
      return { id: o.id, u: o.e * K, v: o.n * K, x: q.x, y: q.y, w: 1 / (s * s), sigma: s, e: o.e, n: o.n };
    });
    var W = 0, ub = 0, vb = 0, xb = 0, yb = 0;
    P.forEach(function (p) { W += p.w; ub += p.w * p.u; vb += p.w * p.v; xb += p.w * p.x; yb += p.w * p.y; });
    ub /= W; vb /= W; xb /= W; yb /= W;
    var Sxx = 0, Sxy = 0, Suu = 0;
    P.forEach(function (p) {
      var u = p.u - ub, v = p.v - vb, x = p.x - xb, y = p.y - yb;
      Sxx += p.w * (u * x + v * y); Sxy += p.w * (u * y - v * x); Suu += p.w * (u * u + v * v);
    });
    var A, B, simScale = null;
    if (P.length === 1) {
      var th = (opts.theta || 0) * D2R; A = Math.cos(th); B = Math.sin(th);
    } else {
      simScale = Math.hypot(Sxx, Sxy) / Suu;
      if (opts.scale) { A = Sxx / Suu; B = Sxy / Suu; }
      else { var t = Math.atan2(Sxy, Sxx); A = Math.cos(t); B = Math.sin(t); }
    }
    var pl = { origin: origin, A: A, B: B, tx: xb - (A * ub - B * vb), ty: yb - (B * ub + A * vb), kind: P.length === 1 ? 'one' : 'fit' };
    var ss = 0;
    var residuals = P.map(function (p) {
      var px = A * p.u - B * p.v + pl.tx, py = B * p.u + A * p.v + pl.ty;
      var dx = p.x - px, dy = p.y - py; ss += dx * dx + dy * dy;
      return { id: p.id, dx: dx, dy: dy, r: Math.hypot(dx, dy), sigma: p.sigma, px: px, py: py, x: p.x, y: p.y };
    });
    var L = 0;
    for (var i = 0; i < P.length; i++) for (var j = i + 1; j < P.length; j++) L = Math.max(L, Math.hypot(P[i].u - P[j].u, P[i].v - P[j].v));
    return { placement: pl, residuals: residuals, rms: Math.sqrt(ss / P.length), n: P.length,
             scalePpm: simScale != null ? (simScale - 1) * 1e6 : null, rotDeg: Math.atan2(B, A) * R2D,
             baselineM: L, centroid: { x: xb, y: yb }, meanSigma: P.reduce(function (a, p) { return a + p.sigma; }, 0) / P.length };
  }

  // Inverse-distance residual field: zero far away, exactly the residual at each monument.
  function makeField(residuals) {
    var R = residuals.filter(function (r) { return r.r > 1e-6; });
    if (!R.length) return null;
    return function (x, y) {
      var sw = 0, sx = 0, sy = 0;
      for (var i = 0; i < R.length; i++) {
        var d2 = (x - R[i].px) * (x - R[i].px) + (y - R[i].py) * (y - R[i].py);
        if (d2 < 1e-8) return { dx: R[i].dx, dy: R[i].dy };
        var w = 1 / d2; sw += w; sx += w * R[i].dx; sy += w * R[i].dy;
      }
      // blend toward zero far from the monuments (a pseudo-observation of 0 at 300 m)
      var w0 = 1 / (300 * 300); sw += w0;
      return { dx: sx / sw, dy: sy / sw };
    };
  }

  /*
   * solve(model, project) -> the best georeference available right now.
   * project: {rough, control:{id:{lat,lon,h,sigma,use,corr}}, settings}
   */
  function solve(model, project) {
    var st = project.settings || {};
    var usable = [];
    Object.keys(project.control || {}).forEach(function (id) {
      var c = project.control[id];
      if (c && c.use !== false && model.points[id] && isFinite(c.lat) && isFinite(c.lon))
        usable.push({ id: id, e: model.points[id].e, n: model.points[id].n, lat: c.lat, lon: c.lon, h: c.h, sigma: c.sigma, corr: c.corr || null });
    });
    var rough = project.rough && project.rough.A != null ? project.rough : null;
    var sol = { kind: 'none', placement: null, n: usable.length, residuals: [], warnings: [], field: null };

    if (usable.length >= 2) {
      var r = fitPoints(usable, { scale: !!st.fitScale });
      Object.assign(sol, r, { kind: 'fit' });
      if (st.hold !== false) sol.field = makeField(r.residuals);
      if (usable.length === 2) {
        var a = usable[0], b = usable[1];
        var plat = Math.hypot(a.e - b.e, a.n - b.n);
        var q1 = r.residuals[0], q2 = r.residuals[1];
        var meas = Math.hypot(q1.x - q2.x, q1.y - q2.y) * FT;
        sol.distCheck = { a: a.id, b: b.id, platFt: plat, measFt: meas, diffFt: meas - plat };
      }
      r.residuals.forEach(function (q) {
        var ft = q.r * FT;
        q.level = ft > 1.0 ? 'bad' : ft > 0.3 ? 'warn' : 'ok';
        if (q.level === 'bad') sol.warnings.push(q.id + ' is ' + ft.toFixed(2) + ' ft from where the documents put it — wrong monument, disturbed, or mis-picked?');
      });
      var corrs = usable.map(function (u) { return u.corr; }).filter(Boolean);
      if (corrs.length && corrs.some(function (c) { return c !== corrs[0]; }))
        sol.warnings.push('These monuments were measured with different correction services. Different services can disagree by ~1 m (different datums); re-measure them on one service.');
    } else if (usable.length === 1) {
      var o = usable[0], f0 = geo.frame({ lat: o.lat, lon: o.lon, h: o.h != null ? o.h : geo.DEFAULT_H });
      var th = rough ? thetaIn(rough, f0) : 0;
      Object.assign(sol, fitPoints(usable, { theta: th, origin: f0.origin }), { kind: 'one', thetaFromRough: !!rough });
      if (!rough) sol.warnings.push('Rotation unknown: drag the outline to line it up, or measure a second monument.');
    } else if (rough) {
      sol.kind = 'rough'; sol.placement = rough;
    }
    sol.measured = usable.map(function (u) { return u.id; });
    sol.sigmaCal = sigmaCalFn(sol, st);
    return sol;
  }

  // 1-sigma georeference uncertainty (m) at ENU point (x,y), excluding deed/model error.
  function sigmaCalFn(sol, st) {
    if (sol.kind === 'none') return function () { return Infinity; };
    if (sol.kind === 'rough') { var s = (st.roughSigmaFt || 10) * K; return function () { return s; }; }
    if (sol.kind === 'one') {
      var p0 = sol.residuals[0], rotUnc = sol.thetaFromRough ? 1.0 * D2R : Math.PI;   // ~1° if eyeballed
      return function (x, y) { return Math.min(Math.hypot(0.02, Math.hypot(x - p0.x, y - p0.y) * rotUnc), 100); };
    }
    var base = Math.max(sol.rms, sol.meanSigma || 0.015), L = Math.max(sol.baselineM, 1), c = sol.centroid;
    return function (x, y) {
      var d = Math.hypot(x - c.x, y - c.y);
      return Math.hypot(base, sol.meanSigma * Math.SQRT2 * d / L);
    };
  }

  /*
   * Georeferenced geometry for drawing and testing.
   * Returns {pts:{id:{x,y,lat,lon,measured}}, rings:{id:{xy:[],ll:[]}}, segs:{id:{xy:[],ll:[]}}}
   */
  function geometry(model, sol) {
    if (!sol || sol.kind === 'none' || !sol.placement) return null;
    var pl = sol.placement, f = frameOf(pl), field = sol.field;
    var measured = {};
    (sol.measured || []).forEach(function (id) { measured[id] = true; });
    function map(e, n) {
      var p = fwd(pl, e, n);
      if (field) { var c = field(p.x, p.y); p = { x: p.x + c.dx, y: p.y + c.dy }; }
      var g = f.toGeo(p.x, p.y, 0);
      return { x: p.x, y: p.y, lat: g.lat, lon: g.lon };
    }
    var out = { pts: {}, rings: {}, segs: {}, frame: f, placement: pl };
    Object.keys(model.points).forEach(function (id) {
      var p = model.points[id], m = map(p.e, p.n); m.measured = !!measured[id]; out.pts[id] = m;
    });
    Object.keys(model.segments).forEach(function (id) {
      var ms = model.segments[id].verts.map(function (v) { return map(v.e, v.n); });
      out.segs[id] = { xy: ms.map(function (m) { return { x: m.x, y: m.y }; }), ll: ms.map(function (m) { return [m.lat, m.lon]; }) };
    });
    Object.keys(model.rings).forEach(function (id) {
      var ms = model.rings[id].verts.map(function (v) { var m = map(v.e, v.n); m.seg = v.seg; return m; });
      out.rings[id] = { xy: ms.map(function (m) { return { x: m.x, y: m.y, seg: m.seg }; }), ll: ms.map(function (m) { return [m.lat, m.lon]; }) };
    });
    return out;
  }

  return { K: K, FT: FT, frameOf: frameOf, fwd: fwd, inv: inv, local2geo: local2geo, geo2enu: geo2enu, geo2local: geo2local,
           thetaDeg: thetaDeg, thetaIn: thetaIn, scaleOf: scaleOf, placeAt: placeAt, translate: translate, rotateAbout: rotateAbout,
           fitPoints: fitPoints, makeField: makeField, solve: solve, geometry: geometry };
});
