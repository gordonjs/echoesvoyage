/*
 * Parcel RTK — NMEA 0183 parsing. Sentences with a bad checksum are rejected (a single
 * flipped bit over Bluetooth can otherwise teleport you hundreds of feet).
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else (root.PRTK = root.PRTK || {}).nmea = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var FIX = { 0: 'No fix', 1: 'GPS', 2: 'DGPS', 3: 'PPS', 4: 'RTK FIX', 5: 'RTK FLOAT', 6: 'Dead reckoning', 7: 'Manual', 8: 'Simulated' };

  function checksumOk(line) {
    if (!line || line.charAt(0) !== '$') return false;
    var star = line.lastIndexOf('*');
    if (star < 0 || star + 3 > line.length) return false;
    var c = 0;
    for (var i = 1; i < star; i++) c ^= line.charCodeAt(i);
    return c === parseInt(line.substr(star + 1, 2), 16);
  }
  // "3929.8271234","N" -> 39.497118723 ; string arithmetic avoids float error in ddmm
  function coord(v, hemi) {
    if (!v) return null;
    var dot = v.indexOf('.'); if (dot < 0) dot = v.length;
    if (dot < 3) return null;
    var deg = parseInt(v.slice(0, dot - 2), 10), min = parseFloat(v.slice(dot - 2));
    if (!isFinite(deg) || !isFinite(min) || min >= 60) return null;
    var d = deg + min / 60;
    return (hemi === 'S' || hemi === 'W') ? -d : d;
  }
  function num(v) { if (v === undefined || v === '') return null; var x = parseFloat(v); return isFinite(x) ? x : null; }

  function parse(line, strict) {
    line = String(line).trim();
    if (strict !== false && !checksumOk(line)) return null;
    var star = line.lastIndexOf('*');
    var f = (star > 0 ? line.slice(1, star) : line.slice(1)).split(',');
    var type = f[0].slice(-3);
    if (type === 'GGA') {
      var lat = coord(f[2], f[3]), lon = coord(f[4], f[5]);
      var q = parseInt(f[6], 10);
      var alt = num(f[9]), sep = num(f[11]);
      return { type: 'GGA', time: f[1], lat: lat, lon: lon, q: isFinite(q) ? q : 0,
               sats: num(f[7]), hdop: num(f[8]), alt: alt, sep: sep,
               h: alt != null && sep != null ? alt + sep : null,           // ellipsoid height
               age: num(f[13]), station: f[14] || null, valid: lat != null && lon != null && q > 0 };
    }
    if (type === 'RMC') {
      return { type: 'RMC', time: f[1], ok: f[2] === 'A', lat: coord(f[3], f[4]), lon: coord(f[5], f[6]),
               sogKn: num(f[7]), cog: num(f[8]), date: f[9] };
    }
    if (type === 'GST') {
      var sLat = num(f[6]), sLon = num(f[7]);
      return { type: 'GST', time: f[1], rms: num(f[2]), sLat: sLat, sLon: sLon, sAlt: num(f[8]),
               sigmaH: sLat != null && sLon != null ? Math.hypot(sLat, sLon) : null };
    }
    if (type === 'GSA') return { type: 'GSA', pdop: num(f[15]), hdop: num(f[16]), vdop: num(f[17]) };
    return { type: type };
  }

  // Horizontal 1-sigma (m). Uses GST when fresh; otherwise a conservative estimate by
  // fix type (HDOP alone badly overstates RTK error and understates autonomous error).
  function sigmaFor(gga, gst) {                       // caller passes gst only if it is fresh (<= 2 s)
    if (gst && gst.sigmaH != null) return { sigma: gst.sigmaH, est: false };
    var hd = gga.hdop || 1;
    var s = gga.q === 4 ? 0.014 : gga.q === 5 ? 0.25 : gga.q === 2 ? 0.6 * hd : gga.q === 1 ? 1.8 * hd : 10;
    return { sigma: s, est: true };
  }

  return { FIX: FIX, checksumOk: checksumOk, coord: coord, parse: parse, sigmaFor: sigmaFor };
});
