/*
 * Parcel RTK — live spatial answers for one position (ENU metres in the placement frame).
 *
 *  - Which of your parcels you are in (or which neighbor lot).
 *  - Distance to YOUR property line: the outer edge of the land you own. The line between
 *    your two parcels is reported separately — crossing it doesn't take you off your land.
 *  - An honest IN / OUT / ON LINE call: if you are closer to the line than twice the
 *    combined uncertainty (receiver + georeference + document), it says ON LINE.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./cogo.js'), require('./geodesy.js'));
  else { var P = root.PRTK = root.PRTK || {}; P.locate = factory(P.cogo, P.geo); }
})(typeof self !== 'undefined' ? self : this, function (cogo, geo) {
  'use strict';
  var K = geo.US_FT;

  function asEN(xy) { return xy.map(function (p) { return { e: p.x, n: p.y }; }); }

  // cached per geometry object
  function prep(model, gm) {
    if (gm._prep) return gm._prep;
    var segs = {};
    Object.keys(gm.segs).forEach(function (id) { segs[id] = asEN(gm.segs[id].xy); });
    var rings = {};
    Object.keys(gm.rings).forEach(function (id) { rings[id] = asEN(gm.rings[id].xy); });
    return (gm._prep = { segs: segs, rings: rings });
  }

  function segSigmaModel(model, sol, segId, hold) {
    var s = model.segments[segId];
    if (hold && sol && sol.measured && sol.measured.indexOf(s.a) >= 0 && sol.measured.indexOf(s.b) >= 0) return 0;
    return (s.sigmaFt || 0.1) * K;
  }

  function nearestOfRole(model, P, x, y, role) {
    var best = null, q = { e: x, n: y };
    Object.keys(model.segments).forEach(function (id) {
      if (model.segments[id].role !== role) return;
      var r = cogo.nearestOnPolyline(q, P.segs[id]);
      if (r && (!best || r.d < best.d)) { best = r; best.segId = id; }
    });
    return best;
  }

  function where(model, gm, sol, x, y, sigmaPos, settings) {
    settings = settings || {};
    var P = prep(model, gm), q = { e: x, n: y };
    var inOwned = null, inNeighbor = null;
    model.order.forEach(function (rid) {
      if (cogo.pointInRing(q, P.rings[rid])) {
        if (model.rings[rid].owned) inOwned = inOwned || rid; else inNeighbor = inNeighbor || rid;
      }
    });
    var b = nearestOfRole(model, P, x, y, 'boundary');
    var i = nearestOfRole(model, P, x, y, 'internal');
    var hold = settings.hold !== false;
    var sCal = sol && sol.sigmaCal ? sol.sigmaCal(x, y) : Infinity;
    var sMod = b ? segSigmaModel(model, sol, b.segId, hold) : 0;
    var sigma = Math.sqrt(sigmaPos * sigmaPos + sCal * sCal + sMod * sMod);
    var status = !b ? 'NONE' : (b.d < 2 * sigma ? 'ONLINE' : inOwned ? 'IN' : 'OUT');
    var bearingTo = b ? cogo.azimuth(q, b.point) : null;
    return {
      inOwned: inOwned, inNeighbor: inNeighbor, status: status,
      edge: b ? { segId: b.segId, name: model.segments[b.segId].name, d: b.d, bearing: bearingTo, point: { x: b.point.e, y: b.point.n } } : null,
      internal: i ? { segId: i.segId, name: model.segments[i.segId].name, d: i.d } : null,
      sigma: sigma, sigmaParts: { pos: sigmaPos, cal: sCal, model: sMod }
    };
  }

  // Line stake-out against one segment: signed offset (+ = right of the segment's
  // stored direction a->b), distance along it, and the foot point.
  function stakeout(model, gm, segId, x, y) {
    var P = prep(model, gm), line = P.segs[segId];
    var r = cogo.nearestOnPolyline({ e: x, n: y }, line);
    // side: use the local tangent of the closest piece
    var a = line[r.index], c = line[r.index + 1];
    var cross = (c.e - a.e) * (y - a.n) - (c.n - a.n) * (x - a.e);
    return { offset: cross > 0 ? -r.d : r.d, along: r.along, length: r.length, point: { x: r.point.e, y: r.point.n },
             a: model.segments[segId].a, b: model.segments[segId].b };
  }

  return { where: where, stakeout: stakeout };
});
