/*
 * Parcel RTK — geodesy. WGS84 geodetic <-> ECEF <-> local ENU (an exact tangent plane).
 *
 * Why ECEF/ENU instead of "metres per degree": at ~2,640 m ellipsoid height a
 * sea-level metres-per-degree conversion shrinks distances by ~415 ppm (0.8 ft over
 * the property), and the flat-earth shortcut adds a few cm of distortion at the far
 * corners. ENU built from ECEF has neither problem: horizontal ENU distances between
 * points on the ground are true ground distances, which is what the deeds measure.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else (root.PRTK = root.PRTK || {}).geo = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var A = 6378137.0, F = 1 / 298.257223563, E2 = F * (2 - F);
  var D2R = Math.PI / 180, R2D = 180 / Math.PI;
  var US_FT = 1200 / 3937;                 // metres per US survey foot (Colorado plats)
  var DEFAULT_H = 2640;                    // site ellipsoid height (m), used until the receiver reports one

  function toEcef(lat, lon, h) {
    var p = lat * D2R, l = lon * D2R, sp = Math.sin(p), cp = Math.cos(p);
    var N = A / Math.sqrt(1 - E2 * sp * sp);
    return [(N + h) * cp * Math.cos(l), (N + h) * cp * Math.sin(l), (N * (1 - E2) + h) * sp];
  }
  function fromEcef(x, y, z) {
    var lon = Math.atan2(y, x), p = Math.hypot(x, y);
    var lat = Math.atan2(z, p * (1 - E2)), h = 0;
    for (var i = 0; i < 8; i++) {
      var sp = Math.sin(lat), N = A / Math.sqrt(1 - E2 * sp * sp);
      h = p / Math.cos(lat) - N;
      lat = Math.atan2(z, p * (1 - E2 * N / (N + h)));
    }
    return { lat: lat * R2D, lon: lon * R2D, h: h };
  }

  // A local East-North-Up frame anchored at origin {lat, lon, h}.
  function frame(origin) {
    var h0 = origin.h != null ? origin.h : DEFAULT_H;
    var p = origin.lat * D2R, l = origin.lon * D2R;
    var sp = Math.sin(p), cp = Math.cos(p), sl = Math.sin(l), cl = Math.cos(l);
    var o = toEcef(origin.lat, origin.lon, h0);
    return {
      origin: { lat: origin.lat, lon: origin.lon, h: h0 },
      toEnu: function (lat, lon, h) {
        var q = toEcef(lat, lon, h != null ? h : h0);
        var dx = q[0] - o[0], dy = q[1] - o[1], dz = q[2] - o[2];
        return { x: -sl * dx + cl * dy,
                 y: -sp * cl * dx - sp * sl * dy + cp * dz,
                 z: cp * cl * dx + cp * sl * dy + sp * dz };
      },
      toGeo: function (x, y, z) {
        z = z || 0;
        var dx = -sl * x - sp * cl * y + cp * cl * z;
        var dy = cl * x - sp * sl * y + cp * sl * z;
        var dz = cp * y + sp * z;
        return fromEcef(o[0] + dx, o[1] + dy, o[2] + dz);
      }
    };
  }

  // Ground distance (m) and bearing (deg) between two geodetic points.
  function distBearing(a, b) {
    var f = frame({ lat: a.lat, lon: a.lon, h: a.h });
    var q = f.toEnu(b.lat, b.lon, b.h != null ? b.h : f.origin.h);
    var br = Math.atan2(q.x, q.y) * R2D;
    return { dist: Math.hypot(q.x, q.y), bearing: br < 0 ? br + 360 : br, dx: q.x, dy: q.y };
  }

  return { A: A, E2: E2, US_FT: US_FT, DEFAULT_H: DEFAULT_H, D2R: D2R, R2D: R2D,
           toEcef: toEcef, fromEcef: fromEcef, frame: frame, distBearing: distBearing };
});
