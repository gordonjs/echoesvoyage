/*
 * Parcel RTK — the map: base layers (through the bridge's tile cache when it is there),
 * parcels drawn segment by segment, monuments, saved points, your position and track,
 * popups, "tap anywhere" spot checks, and the handles for placing the outline by hand.
 */
(function () {
  'use strict';
  const P = window.PRTK, A = P.app, calib = P.calib, cogo = P.cogo, geo = P.geo, locate = P.locate;
  const model = A.model, esc = A.esc;
  const DEFAULT_VIEW = [39.4970, -105.3050];             // Conifer, CO — until there is an outline or a fix
  const BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
  const BASES = {
    sat: { name: 'Satellite', native: 19, attr: 'Imagery © Esri, Maxar, Earthstar Geographics',
           url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}' },
    topo: { name: 'Topo (USGS)', native: 16, attr: 'USGS The National Map',
            url: 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}' },
    street: { name: 'Streets', native: 19, attr: '© OpenStreetMap contributors',
              url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png' },
    none: null
  };
  A.BASES = BASES;
  A.BASE_ORDER = ['sat', 'topo', 'street', 'none'];
  const SHORT = { TPB: 'Shared corner', W16: 'Lot 1 SW', L1NE: 'Lot 1 NE', L1SE: 'Lot 1 SE',
                  LPNE: 'Road corner', P1NW: '35-ac NW', P1NE: '35-ac NE', S14: 'S¼ corner' };
  A.SHORT = SHORT;
  A.shortName = id => SHORT[id] || (model.points[id] ? model.points[id].name : id);
  A.isMain = id => !!SHORT[id];
  const CONF = { plat: 'drawn on the plat', note2: 'set per plat note 2', none: 'not described — look for any pin' };
  A.CONF = CONF;

  let map, renderer, baseLayer = null, baseKey = null, lastPopupClose = 0;
  const lyr = {};

  A.initMap = function () {
    map = A.map = L.map('map', { zoomControl: false, maxZoom: 22, zoomSnap: 0.5, wheelPxPerZoomLevel: 90 });
    map.attributionControl.setPrefix(false);
    renderer = L.canvas({ padding: 0.4, tolerance: 9 });
    ['fill', 'segs', 'labels', 'resid', 'pts', 'ptLabels', 'road', 'saved', 'track', 'stake', 'nav', 'pos', 'adjust']
      .forEach(k => { lyr[k] = L.layerGroup().addTo(map); });
    map.setView(DEFAULT_VIEW, 15);
    map.on('zoomend', zoomLayers);
    map.on('dragstart', () => { if (A.ui.follow) A.setFollow(false); });
    map.on('click contextmenu', onMapClick);
    map.on('popupclose', () => { lastPopupClose = Date.now(); });
    map.on('popupopen', () => { if (A.refreshLive) A.refreshLive(true); });
    trackLine = L.polyline([], { renderer, color: '#4ea1ff', weight: 2.5, opacity: 0.7, interactive: false }).addTo(lyr.track);
    zoomLayers();
  };

  A.setBaseLayer = function (key) {
    if (!Object.prototype.hasOwnProperty.call(BASES, key)) key = 'sat';
    if (key === baseKey) return;
    if (baseLayer) { map.removeLayer(baseLayer); baseLayer = null; }
    baseKey = key;
    const b = BASES[key];
    if (b) {
      // through the bridge, tiles are cached on the laptop and keep working without internet
      const url = A.bridgeMode ? 'tiles/' + key + '/{z}/{x}/{y}' : b.url;
      baseLayer = L.tileLayer(url, { maxNativeZoom: b.native, maxZoom: 22, attribution: b.attr, errorTileUrl: BLANK, keepBuffer: 3 });
      baseLayer.addTo(map);
      baseLayer.bringToBack();
    }
  };

  function zoomLayers() {
    const z = map.getZoom();
    const show = (l, on) => { if (on && !map.hasLayer(l)) map.addLayer(l); else if (!on && map.hasLayer(l)) map.removeLayer(l); };
    show(lyr.road, z >= 18);
    show(lyr.ptLabels, z >= 17);
    show(lyr.labels, z >= 14);
  }

  // ------------------------------------------------------------------ parcels
  function segStyle(s) {
    if (s.role === 'boundary') {
      const rid = s.rings.find(r => model.rings[r].owned);
      return { color: model.rings[rid].color, weight: 3.5, opacity: 0.95 };
    }
    if (s.role === 'internal') return { color: '#ffd166', weight: 2.5, opacity: 0.9, dashArray: '10 7' };
    return { color: '#c4ccd4', weight: 1.8, opacity: 0.75, dashArray: '4 6' };
  }
  function ringCentroid(gm, rid) {
    const r = gm.rings[rid].xy;
    let a = 0, cx = 0, cy = 0;
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const f = r[j].x * r[i].y - r[i].x * r[j].y;
      a += f; cx += (r[j].x + r[i].x) * f; cy += (r[j].y + r[i].y) * f;
    }
    const g = gm.frame.toGeo(cx / (3 * a), cy / (3 * a), 0);
    return [g.lat, g.lon];
  }
  A.segLengthM = function (id) {
    const v = model.segments[id].verts;
    let s = 0;
    for (let i = 1; i < v.length; i++) s += Math.hypot(v[i].e - v[i - 1].e, v[i].n - v[i - 1].n);
    return s * A.K;
  };
  const ROLE = { boundary: 'Your property line', internal: 'Line between your two parcels — you own both sides',
                 neighbor: 'A neighbor\'s line (not yours)' };

  function drawParcels(gm, temp) {
    lyr.fill.clearLayers(); lyr.segs.clearLayers(); lyr.labels.clearLayers();
    if (!gm) return;
    model.order.forEach(rid => {
      const r = model.rings[rid];
      L.polygon(gm.rings[rid].ll, { renderer, stroke: false, fillColor: r.color, fillOpacity: r.owned ? 0.13 : 0.05, interactive: false })
        .addTo(lyr.fill);
      L.marker(ringCentroid(gm, rid), { interactive: false, keyboard: false,
        icon: L.divIcon({ className: 'lbl-wrap', iconSize: [0, 0],
                          html: `<div class="lbl">${esc(r.short)}<br><span class="dim">${r.areaAc.toFixed(2)} ac</span></div>` }) })
        .addTo(lyr.labels);
    });
    Object.keys(model.segments).forEach(id => {
      const pl = L.polyline(gm.segs[id].ll, Object.assign({ renderer, interactive: !temp, bubblingMouseEvents: false }, segStyle(model.segments[id])));
      if (!temp) pl.bindPopup(() => segPopup(id), { maxWidth: 320 });
      pl.addTo(lyr.segs);
    });
  }
  function segPopup(id) {
    const s = model.segments[id];
    return `<b>${esc(s.name)}</b><div class="hint">${esc(ROLE[s.role])}</div>
      <div class="kv"><span>Call</span><span>${esc(s.call || '')}</span>
        <span>Length</span><span>${A.fmtLen(A.segLengthM(id))}</span>
        <span>Runs</span><span>${esc(A.shortName(s.a))} → ${esc(A.shortName(s.b))}</span></div>
      <div class="row"><button class="btn small primary" data-act="stake" data-id="${id}">Stake out this line</button></div>`;
  }

  // ------------------------------------------------------------------ monuments
  function drawPoints() {
    lyr.pts.clearLayers(); lyr.ptLabels.clearLayers(); lyr.road.clearLayers(); lyr.resid.clearLayers();
    const gm = A.gm;
    if (!gm) return;
    P.model.controlList(model).forEach(p => {
      const g = gm.pts[p.id], c = A.proj.control[p.id], main = A.isMain(p.id);
      const fill = c ? (c.use !== false ? '#2ecc71' : '#f5b82e') : p.conf === 'none' ? '#93a1b0' : '#ffffff';
      L.circleMarker([g.lat, g.lon], { renderer, radius: main ? 7 : 4.5, color: '#0f1419', weight: 2, fillColor: fill, fillOpacity: 1,
                                       bubblingMouseEvents: false })
        .bindPopup(() => pointPopup(p.id), { maxWidth: 330 })
        .addTo(main ? lyr.pts : lyr.road);
      if (main) {
        L.marker([g.lat, g.lon], { interactive: false, keyboard: false,
          icon: L.divIcon({ className: 'plbl-wrap', iconSize: [0, 0], html: `<div class="lbl sm">${esc(SHORT[p.id])}</div>` }) })
          .addTo(lyr.ptLabels);
      }
      // where it was measured, if the outline doesn't pass exactly through it
      if (c && isFinite(c.lat)) {
        const off = geo.distBearing({ lat: g.lat, lon: g.lon }, { lat: c.lat, lon: c.lon }).dist;
        if (off > 0.02) {
          L.polyline([[g.lat, g.lon], [c.lat, c.lon]], { renderer, color: '#2ecc71', weight: 2, dashArray: '3 4', interactive: false }).addTo(lyr.resid);
          L.circleMarker([c.lat, c.lon], { renderer, radius: 3.5, color: '#2ecc71', weight: 2, fillOpacity: 0, interactive: false }).addTo(lyr.resid);
        }
      }
    });
  }
  A.pointStatusText = function (id) {
    const c = A.proj.control[id];
    if (!c) return 'Not measured yet';
    const r = (A.sol.residuals || []).find(q => q.id === id);
    let s = c.legacy ? 'Pin from the old version (not re-measured)'
                     : 'Measured ' + A.fmtDate(c.t) + (c.sigma ? ' · ' + A.fmtAcc(c.sigma) : '');
    if (c.use === false) s += ' · ignored';
    else if (r && A.sol.kind === 'fit') s += ' · off by ' + A.fmtLen(r.r);
    return s;
  };
  function pointPopup(id) {
    const p = model.points[id], g = A.gm.pts[id], c = A.proj.control[id];
    return `<b>${esc(p.name)}</b>
      <div class="hint">${esc(p.monument || '')}</div>
      <div class="kv"><span>Monument</span><span>${esc(CONF[p.conf] || '')}</span>
        <span>From you</span><span data-live="dist" data-lat="${g.lat}" data-lon="${g.lon}">—</span>
        <span>Status</span><span>${esc(A.pointStatusText(id))}</span></div>
      <div class="row">
        <button class="btn small primary" data-act="nav" data-kind="pt" data-id="${id}">Go here</button>
        <button class="btn small good" data-act="measure" data-id="${id}">${c ? 'Re-measure' : 'Measure'}</button>
        <button class="btn small" data-act="copyll" data-lat="${g.lat.toFixed(8)}" data-lon="${g.lon.toFixed(8)}">Copy</button></div>`;
  }

  // ------------------------------------------------------------------ saved points
  function drawSaved() {
    lyr.saved.clearLayers();
    A.proj.savedPoints.forEach(p => {
      if (!isFinite(p.lat) || !isFinite(p.lon)) return;
      L.circleMarker([p.lat, p.lon], { renderer, radius: 6, color: '#0f1419', weight: 2, fillColor: '#b07cff', fillOpacity: 1,
                                       bubblingMouseEvents: false })
        .bindPopup(() => savedPopup(p.id), { maxWidth: 320 })
        .addTo(lyr.saved);
    });
  }
  A.whereText = function (lat, lon) {
    if (!A.gm) return '';
    const q = A.gm.frame.toEnu(lat, lon), w = locate.where(model, A.gm, A.sol, q.x, q.y, 0, A.proj.settings);
    const inTxt = w.inOwned ? 'In ' + model.rings[w.inOwned].short : w.inNeighbor ? 'Out — ' + model.rings[w.inNeighbor].short : 'Outside your land';
    return inTxt + ' · ' + A.fmtLen(w.edge.d) + ' from the line' + (w.status === 'ONLINE' ? ' (too close to call)' : '');
  };
  function savedPopup(id) {
    const p = A.proj.savedPoints.find(x => x.id === id);
    if (!p) return 'Deleted';
    return `<b>${esc(p.note || '(no note)')}</b>
      <div class="hint">${esc(A.fmtDate(p.t))}${p.sigma ? ' · ' + A.fmtAcc(p.sigma) : ''}${p.fix ? ' · ' + A.fixName(p.fix) : ''}</div>
      <div class="kv"><span>Where</span><span>${esc(A.whereText(p.lat, p.lon))}</span>
        <span>From you</span><span data-live="dist" data-lat="${p.lat}" data-lon="${p.lon}">—</span>
        <span>Coordinates</span><span class="mono">${A.fmtLL(p.lat, p.lon)}</span></div>
      <div class="row">
        <button class="btn small primary" data-act="nav" data-kind="saved" data-id="${esc(p.id)}">Go here</button>
        <button class="btn small" data-act="ptedit" data-id="${esc(p.id)}">Edit note</button>
        <button class="btn small" data-act="copyll" data-lat="${p.lat.toFixed(8)}" data-lon="${p.lon.toFixed(8)}">Copy</button></div>`;
  }

  // ------------------------------------------------------------------ tap anywhere: what's here?
  function onMapClick(e) {
    if (A.adjust || Date.now() - lastPopupClose < 400) return;
    const lat = e.latlng.lat, lon = e.latlng.lng;
    let html = '';
    if (A.gm) {
      const q = A.gm.frame.toEnu(lat, lon), w = locate.where(model, A.gm, A.sol, q.x, q.y, 0, A.proj.settings);
      const verdict = w.inOwned ? `<span class="good-t">Inside ${esc(model.rings[w.inOwned].short)}</span>`
        : w.inNeighbor ? `<span class="bad">Outside — ${esc(model.rings[w.inNeighbor].short)}</span>` : '<span class="bad">Outside your land</span>';
      html += `<b>${verdict}</b><div class="kv">
        <span>Property line</span><span>${A.fmtLen(w.edge.d)}</span>
        <span>Nearest line</span><span>${esc(w.edge.name)}</span>
        ${w.status === 'ONLINE' ? `<span>Careful</span><span class="warn">within the outline's uncertainty (${A.fmtAcc(w.sigma)})</span>` : ''}`;
    } else html += '<b>This spot</b><div class="kv">';
    html += `<span>From you</span><span data-live="dist" data-lat="${lat}" data-lon="${lon}">—</span>
        <span>Coordinates</span><span class="mono">${A.fmtLL(lat, lon, 7)}</span></div>
      <div class="row">
        <button class="btn small primary" data-act="nav" data-kind="ll" data-lat="${lat}" data-lon="${lon}">Go here</button>
        <button class="btn small" data-act="savespot" data-lat="${lat}" data-lon="${lon}">Save as point</button>
        <button class="btn small" data-act="copyll" data-lat="${lat.toFixed(7)}" data-lon="${lon.toFixed(7)}">Copy</button></div>`;
    L.popup({ maxWidth: 320 }).setLatLng(e.latlng).setContent(html).openOn(map);
  }

  // ------------------------------------------------------------------ position, track, nav, stake
  let posDot = null, accCircle = null, trackLine = null, navLine = null, stakeHi = null, stakeFoot = null;
  const track = [];
  A.drawPosition = function () {
    const L0 = A.live, pos = L0.pos;
    if (!pos) {
      if (posDot) { lyr.pos.clearLayers(); posDot = accCircle = null; }
      return;
    }
    const ll = [pos.lat, pos.lon], stale = A.fixAge() > 3;
    const color = stale ? '#93a1b0' : pos.q === 4 ? '#2ecc71' : pos.q === 5 ? '#f5b82e' : '#4ea1ff';
    if (!posDot) {
      accCircle = L.circle(ll, { renderer, radius: 1, color: color, weight: 1, fillColor: color, fillOpacity: 0.15, interactive: false }).addTo(lyr.pos);
      posDot = L.circleMarker(ll, { renderer, radius: 8, color: '#fff', weight: 3, fillColor: color, fillOpacity: 1, interactive: false }).addTo(lyr.pos);
    }
    posDot.setLatLng(ll).setStyle({ fillColor: color });
    accCircle.setLatLng(ll).setRadius(Math.max(0.05, 2 * (L0.sigma || 1))).setStyle({ color: color, fillColor: color });
  };
  A.trackPush = function (pos) {
    if (!pos) return;
    const last = track[track.length - 1];
    if (last) {
      const dy = (pos.lat - last.lat) * 111132, dx = (pos.lon - last.lng) * 111132 * Math.cos(pos.lat * Math.PI / 180);
      if (Math.hypot(dx, dy) < 0.3 && pos.t - last.t < 10000) return;
    }
    const p = L.latLng(pos.lat, pos.lon); p.t = pos.t;
    track.push(p);
    if (track.length > 6000) track.splice(0, 1500);
    if (A.ui.track) trackLine.setLatLngs(track);
  };
  A.showTrack = function (on) { trackLine.setLatLngs(on ? track : []); };
  A.clearTrack = function () { track.length = 0; trackLine.setLatLngs([]); };
  A.trackCount = () => track.length;

  A.drawNav = function (target) {
    const pos = A.live.pos;
    if (!target || !pos) { if (navLine) { lyr.nav.clearLayers(); navLine = null; } return; }
    const lls = [[pos.lat, pos.lon], [target.lat, target.lon]];
    if (!navLine) navLine = L.polyline(lls, { renderer, color: '#2ecc71', weight: 3, dashArray: '8 8', interactive: false }).addTo(lyr.nav);
    else navLine.setLatLngs(lls);
  };
  A.drawStake = function (segId, foot) {
    if (!segId || !A.gm) { lyr.stake.clearLayers(); stakeHi = stakeFoot = null; return; }
    if (!stakeHi || stakeHi._segId !== segId) {
      lyr.stake.clearLayers(); stakeFoot = null;
      stakeHi = L.polyline(A.gm.segs[segId].ll, { renderer, color: '#ffe14d', weight: 7, opacity: 0.6, interactive: false }).addTo(lyr.stake);
      stakeHi._segId = segId;
    } else stakeHi.setLatLngs(A.gm.segs[segId].ll);
    const pos = A.live.pos;
    if (foot && pos) {
      const lls = [[pos.lat, pos.lon], [foot.lat, foot.lon]];
      if (!stakeFoot) stakeFoot = L.polyline(lls, { renderer, color: '#ffe14d', weight: 2.5, dashArray: '4 5', interactive: false }).addTo(lyr.stake);
      else stakeFoot.setLatLngs(lls);
    }
  };

  // ------------------------------------------------------------------ view
  A.setFollow = function (on) {
    A.ui.follow = !!on; A.saveUi();
    A.$('#fFollow').classList.toggle('on', A.ui.follow);
    if (on && A.live.pos) map.panTo([A.live.pos.lat, A.live.pos.lon]);
  };
  A.followTick = function () {
    const pos = A.live.pos;
    if (!A.ui.follow || !pos || A.adjust) return;
    const p = map.latLngToContainerPoint([pos.lat, pos.lon]), s = map.getSize();
    if (Math.abs(p.x - s.x / 2) > s.x * 0.18 || Math.abs(p.y - s.y / 2) > s.y * 0.18) map.panTo([pos.lat, pos.lon], { animate: true, duration: 0.4 });
  };
  A.ownedBounds = function () {
    if (!A.gm) return null;
    let b = null;
    model.order.forEach(rid => { if (model.rings[rid].owned) { const rb = L.latLngBounds(A.gm.rings[rid].ll); b = b ? b.extend(rb) : rb; } });
    return b;
  };
  A.fitParcels = function () {
    const b = A.ownedBounds();
    if (!b) return false;
    const top = (A.$('#top').offsetHeight || 0) + (A.$('#line2').offsetHeight || 0);
    map.fitBounds(b, { paddingTopLeft: [24, top + 16], paddingBottomRight: [70, 70], maxZoom: 18 });
    return true;
  };
  A.mapCenter = () => { const c = map.getCenter(); return { lat: c.lat, lon: c.lng }; };
  // show all these points, clear of the top bars and the bottom card
  A.fitPoints = function (lls, maxZoom) {
    const top = (A.$('#top').offsetHeight || 0) + (A.$('#line2').offsetHeight || 0);
    map.fitBounds(L.latLngBounds(lls), { paddingTopLeft: [40, top + 40], paddingBottomRight: [80, 220], maxZoom: maxZoom || 20 });
  };
  A.zoomTo = function (lat, lon, z) { map.setView([lat, lon], Math.max(map.getZoom(), z || 18)); };
  A.closePopup = () => map.closePopup();

  // ------------------------------------------------------------------ hand placement ("rough")
  // Two handles: ✥ drags the outline, ⟳ rotates it about ✥. With one measured monument the
  // outline is pinned there, so only rotation is offered.
  A.adjust = null;
  function ownedCentroidLocal() {
    let a = 0, cx = 0, cy = 0;
    model.order.forEach(rid => {
      if (!model.rings[rid].owned) return;
      const r = model.rings[rid].verts;
      for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
        const f = r[j].e * r[i].n - r[i].e * r[j].n;
        a += f; cx += (r[j].e + r[i].e) * f; cy += (r[j].n + r[i].n) * f;
      }
    });
    return { e: cx / (3 * a), n: cy / (3 * a) };
  }
  function pivotLocal() {
    if (A.sol.kind === 'one') { const p = model.points[A.sol.measured[0]]; return { e: p.e, n: p.n }; }
    return ownedCentroidLocal();
  }
  A.adjustAllowed = () => A.sol.kind !== 'fit';
  A.startAdjust = function () {
    if (!A.adjustAllowed()) {
      A.toast('Measured monuments place the outline now. Set them to “Ignore” to move it by hand.', true);
      return;
    }
    if (!A.proj.rough) {
      let pl;
      if (A.sol.placement) pl = Object.assign({}, A.sol.placement, { kind: 'rough' });
      else {
        const c = A.live.pos && A.fixAge() < 10 ? A.live.pos : A.mapCenter(), ct = ownedCentroidLocal();
        pl = calib.placeAt(ct.e, ct.n, c.lat, c.lon, null, 0);
      }
      delete pl.migratedFromV1;
      if (!A.edit({ op: 'set', path: ['rough'], value: pl })) return;
      if (!A.sol.measured.length) A.fitParcels();
    }
    A.adjust = { mode: A.sol.kind === 'one' ? 'rotate' : 'both' };
    if (A.closePanel) A.closePanel();
    map.closePopup();
    drawAdjust();
    A.emit('adjust');
  };
  A.stopAdjust = function () {
    if (!A.adjust) return;
    A.adjust = null;
    lyr.adjust.clearLayers();
    A.emit('adjust');
  };
  A.adjRotate = function (deg) {
    const r = A.proj.rough;
    if (!r) return;
    const pv = pivotLocal();
    A.edit({ op: 'set', path: ['rough'], value: calib.rotateAbout(r, pv.e, pv.n, calib.thetaDeg(r) + deg) });
  };
  A.adjNudge = function (dxFt, dyFt) {
    const r = A.proj.rough;
    if (!r || A.sol.kind === 'one') return;
    A.edit({ op: 'set', path: ['rough'], value: calib.translate(r, dxFt * A.K, dyFt * A.K) });
  };
  A.adjCenterOnMe = function () {
    const pos = A.live.pos, r = A.proj.rough;
    if (!pos || !r) { A.toast('No position yet', true); return; }
    const ct = ownedCentroidLocal();
    A.edit({ op: 'set', path: ['rough'], value: calib.placeAt(ct.e, ct.n, pos.lat, pos.lon, pos.h, calib.thetaIn(r, geo.frame({ lat: pos.lat, lon: pos.lon, h: pos.h }))) });
  };
  // rotation of the shown outline vs true north, for display (positive = plat north turned west)
  A.outlineRotation = () => A.sol && A.sol.placement ? calib.thetaIn(A.sol.placement, geo.frame(A.sol.placement.origin)) : 0;

  let preview = null, previewQueued = false;
  function showPreview(rough) {
    preview = rough;
    if (previewQueued) return;
    previewQueued = true;
    requestAnimationFrame(() => {
      previewQueued = false;
      if (!preview || !A.adjust) return;
      const sol = calib.solve(model, Object.assign({}, A.proj, { rough: preview }));
      A.adjust.previewSol = sol;
      drawParcels(calib.geometry(model, sol), true);
      placeHandles(sol);
    });
  }
  function commit() {
    const r = preview;
    preview = null;
    if (A.adjust) A.adjust.previewSol = null;
    if (r) A.edit({ op: 'set', path: ['rough'], value: r });
    else drawAdjust();
  }
  let hMove = null, hRot = null;
  function handlePositions(sol) {
    const pl = sol.placement, pv = pivotLocal();
    const ext = A.adjust && A.adjust.arm || 600;
    return { c: calib.local2geo(pl, pv.e, pv.n), r: calib.local2geo(pl, pv.e, pv.n + ext) };
  }
  function placeHandles(sol) {
    const hp = handlePositions(sol);
    if (hMove && !hMove._dragging) hMove.setLatLng([hp.c.lat, hp.c.lon]);
    if (hRot && !hRot._dragging) hRot.setLatLng([hp.r.lat, hp.r.lon]);
  }
  function angleAt(ll, sol) {
    const f = calib.frameOf(sol.placement), hp = handlePositions(sol);
    const c = f.toEnu(hp.c.lat, hp.c.lon), q = f.toEnu(ll.lat, ll.lng);
    return Math.atan2(q.y - c.y, q.x - c.x) * 180 / Math.PI;
  }
  function drawAdjust() {
    lyr.adjust.clearLayers();
    hMove = hRot = null;
    if (!A.adjust || !A.sol.placement) return;
    // keep the rotate handle a sensible distance out at this zoom
    const m = map.getSize(), mpp = 40075016 * Math.cos(map.getCenter().lat * Math.PI / 180) / Math.pow(2, map.getZoom() + 8);
    A.adjust.arm = Math.max(60, Math.min(900, Math.min(m.x, m.y) * 0.28 * mpp * A.FT));
    const hp = handlePositions(A.sol);
    if (A.adjust.mode === 'both') {
      hMove = L.marker([hp.c.lat, hp.c.lon], { draggable: true, zIndexOffset: 1000, keyboard: false,
        icon: L.divIcon({ className: '', html: '<div class="handle">✥</div>', iconSize: [30, 30], iconAnchor: [15, 15] }) }).addTo(lyr.adjust);
      hMove.on('dragstart', () => {
        hMove._dragging = A.adjust.dragging = true;
        A.adjust.start = { ll: hMove.getLatLng(), rough: A.proj.rough };
      });
      hMove.on('drag', () => {
        const st = A.adjust && A.adjust.start;
        if (!st) return;
        const f = calib.frameOf(st.rough), a = f.toEnu(st.ll.lat, st.ll.lng), b = f.toEnu(hMove.getLatLng().lat, hMove.getLatLng().lng);
        showPreview(calib.translate(st.rough, b.x - a.x, b.y - a.y));
      });
      hMove.on('dragend', () => { hMove._dragging = A.adjust.dragging = false; commit(); });
    }
    hRot = L.marker([hp.r.lat, hp.r.lon], { draggable: true, zIndexOffset: 1000, keyboard: false,
      icon: L.divIcon({ className: '', html: '<div class="handle rot">⟳</div>', iconSize: [30, 30], iconAnchor: [15, 15] }) }).addTo(lyr.adjust);
    hRot.on('dragstart', () => {
      hRot._dragging = A.adjust.dragging = true;
      A.adjust.start = { rough: A.proj.rough, sol: A.sol, a0: angleAt(hRot.getLatLng(), A.sol) };
    });
    hRot.on('drag', () => {
      const st = A.adjust && A.adjust.start;
      if (!st) return;
      const pv = pivotLocal(), d = angleAt(hRot.getLatLng(), st.sol) - st.a0;
      showPreview(calib.rotateAbout(st.rough, pv.e, pv.n, calib.thetaDeg(st.rough) + d));
    });
    hRot.on('dragend', () => { hRot._dragging = A.adjust.dragging = false; commit(); });
  }

  // ------------------------------------------------------------------ redraw everything
  A.drawAll = function () {
    if (!map) return;
    A.setBaseLayer(A.proj.settings.base);
    if (A.adjust && A.adjust.dragging) { drawPoints(); drawSaved(); return; }   // don't pull the handle away mid-drag
    drawParcels(A.gm, false);
    drawPoints();
    drawSaved();
    if (A.adjust && !A.adjustAllowed()) A.stopAdjust();
    drawAdjust();
  };
})();
