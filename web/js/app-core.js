/*
 * Parcel RTK — app core: helpers, the project store and its sync with the bridge, the
 * bridge connection, and the live receiver pipeline (plus demo walk, direct Web Serial
 * receiver, screen wake lock and alert sound).
 *
 * Where the project lives
 *   Served by the bridge (http://…): the bridge's rtk_data/project.json is the master copy.
 *   Every edit is applied here at once, sent to the bridge as an op, and kept in a
 *   "pending" list until the bridge confirms it — so nothing is lost if Wi-Fi drops.
 *   This browser keeps a copy too, so the map keeps working while disconnected.
 *   Opened as a file (file://): this browser's localStorage is the only copy.
 */
(function () {
  'use strict';
  const P = window.PRTK, A = P.app = P.app || {};
  const ST = P.state, calib = P.calib, nmea = P.nmea, geo = P.geo, locate = P.locate;

  A.VERSION = '2.0.0';
  A.model = P.model.build(P.data);
  A.K = geo.US_FT;                       // metres per US survey foot
  A.FT = 1 / geo.US_FT;                  // US survey feet per metre
  A.bridgeMode = /^https?:$/.test(location.protocol);

  // ------------------------------------------------------------------ tiny event bus
  const handlers = {};
  A.on = (ev, fn) => { (handlers[ev] = handlers[ev] || []).push(fn); };
  A.emit = (ev, data) => {
    (handlers[ev] || []).forEach(fn => { try { fn(data); } catch (e) { console.error('[' + ev + ']', e); } });
  };

  // ------------------------------------------------------------------ DOM & formatting
  A.$ = (s, r) => (r || document).querySelector(s);
  A.$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  A.esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ESC[c]);

  A.metric = () => !!(A.proj && A.proj.settings.units === 'm');
  // a length given in metres, in the user's units
  A.fmtLen = function (m, dec) {
    if (m == null || !isFinite(m)) return '—';
    if (A.metric()) {
      const d = dec != null ? dec : Math.abs(m) < 30 ? 2 : Math.abs(m) < 300 ? 1 : 0;
      return m.toFixed(d) + ' m';
    }
    const ft = m * A.FT, a = Math.abs(ft);
    const d = dec != null ? dec : a < 100 ? 2 : a < 1000 ? 1 : 0;
    return (d === 0 ? Math.round(ft).toLocaleString('en-US') : ft.toFixed(d)) + ' ft';
  };
  A.fmtAcc = function (m) {
    if (m == null || !isFinite(m)) return '—';
    if (A.metric()) return '±' + (m < 1 ? (m * 100).toFixed(m < 0.1 ? 1 : 0) + ' cm' : m.toFixed(1) + ' m');
    const ft = m * A.FT;
    return '±' + (ft < 1 ? ft.toFixed(2) : ft < 10 ? ft.toFixed(1) : ft.toFixed(0)) + ' ft';
  };
  A.fmtElev = m => m == null || !isFinite(m) ? '' :
    A.metric() ? m.toFixed(1) + ' m' : Math.round(m * A.FT).toLocaleString('en-US') + ' ft';
  A.card = az => ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round((((az % 360) + 360) % 360) / 45) % 8];
  A.fmtDate = t => t ? new Date(t).toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
  A.fmtLL = (lat, lon, d) => lat.toFixed(d || 8) + ', ' + lon.toFixed(d || 8);
  A.FIX = { 0: 'No fix', 1: 'GPS', 2: 'DGPS', 3: 'PPS', 4: 'RTK FIX', 5: 'RTK FLOAT', 6: 'Dead reck.', 7: 'Manual', 8: 'Simulated' };
  A.fixName = q => A.FIX[q] || ('Q' + q);
  A.newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);

  // ------------------------------------------------------------------ storage (never throws)
  A.store = {
    get(k, def) { try { const v = localStorage.getItem(k); return v == null ? def : JSON.parse(v); } catch (e) { return def; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (e) { return false; } }
  };
  const KEY = 'parcelRTK.v2', KEY_PENDING = 'parcelRTK.v2.pending', KEY_UI = 'parcelRTK.v2.ui', KEY_V1 = 'parcelRTK';
  A.ui = Object.assign({ follow: true, track: true, tab: 'status', wake: true, everSynced: false },
                       A.store.get(KEY_UI, {}));
  A.saveUi = () => A.store.set(KEY_UI, A.ui);

  // ------------------------------------------------------------------ toast, modal, copy, download
  let toastTimer = null;
  A.toast = function (msg, err, ms) {
    const t = A.$('#toast');
    t.textContent = msg;
    t.className = 'show' + (err ? ' err' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = ''; }, ms || (err ? 6000 : 2800));
  };
  A.modal = {
    kind: null, onclose: null,
    open(html, opts) {
      opts = opts || {};
      if (!A.$('#modal').hidden) this.close();
      A.$('#mCard').innerHTML = html;
      A.$('#modal').hidden = false;
      this.kind = opts.kind || null;
      this.onclose = opts.onclose || null;
      const f = A.$('#mCard [autofocus]');
      if (f) setTimeout(() => f.focus(), 60);
    },
    close() {
      if (A.$('#modal').hidden) return;
      A.$('#modal').hidden = true;
      A.$('#mCard').innerHTML = '';
      const cb = this.onclose;
      this.onclose = null; this.kind = null;
      if (cb) cb();
    },
    isOpen() { return !A.$('#modal').hidden; }
  };
  A.confirm = function (html, okLabel, danger) {
    return new Promise(resolve => {
      A.modal.open(`<div>${html}</div>
        <div class="row" style="justify-content:flex-end;margin-top:16px">
          <button class="btn ghost" data-act="mclose">Cancel</button>
          <button class="btn ${danger ? 'danger' : 'primary'}" id="cOk">${A.esc(okLabel || 'OK')}</button></div>`,
        { kind: 'confirm', onclose: () => resolve(false) });
      A.$('#cOk').onclick = () => { A.modal.onclose = null; A.modal.close(); resolve(true); };
    });
  };
  A.copy = function (text) {
    const ok = () => A.toast('Copied ' + text);
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;left:-1000px;top:0;opacity:0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); ok(); } catch (e) { A.toast('Could not copy', true); }
      ta.remove();
    };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(ok, fallback);
    else fallback();
  };
  A.download = function (name, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type: type || 'text/plain' }));
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  };

  // ================================================================== project store
  A.base = null;      // last state confirmed by the bridge (or the local state without one)
  A.pending = [];     // [{cid, op}] edits the bridge has not confirmed yet
  A.proj = null;      // what the app shows: base + pending
  A.sol = null;       // georeference solution (calib.solve)
  A.gm = null;        // georeferenced geometry (calib.geometry)

  A.loadProject = function () {
    let base = A.store.get(KEY, null);
    if (!base || base.schema !== 2) {
      const v1 = A.store.get(KEY_V1, null);
      base = ST.empty();
      if (v1 && typeof v1 === 'object') {
        try {
          base = ST.migrateV1(v1, { model: A.model, calib: calib });
          A.migrated = { points: base.savedPoints.length, pins: Object.keys(base.control).length, rough: !!base.rough };
        } catch (e) { console.warn('could not import the old version\'s data', e); }
      }
      A.store.set(KEY, base);
    }
    A.base = base;
    const pend = A.store.get(KEY_PENDING, []);
    A.pending = Array.isArray(pend) ? pend.filter(p => p && p.op) : [];
    A.recompute();
  };

  A.migrationText = function () {
    const m = A.migrated;
    if (!m) return '';
    const parts = [];
    if (m.points) parts.push(m.points + ' saved point' + (m.points === 1 ? '' : 's'));
    if (m.pins) parts.push(m.pins + ' pin' + (m.pins === 1 ? '' : 's'));
    if (m.rough) parts.push('your hand placement');
    return parts.length ? 'Brought over from the old version: ' + parts.join(', ') : '';
  };

  A.recompute = function () {
    let view = A.base || ST.empty();
    A.pending.forEach(p => {
      try { view = ST.apply(view, p.op); } catch (e) { /* already part of the base, or superseded */ }
    });
    view = ST.clone(view);
    view.settings = Object.assign({}, ST.DEFAULT_SETTINGS, view.settings || {});
    view.control = view.control && typeof view.control === 'object' ? view.control : {};
    view.savedPoints = Array.isArray(view.savedPoints) ? view.savedPoints : [];
    A.proj = view;
    try {
      A.sol = calib.solve(A.model, view);
    } catch (e) {
      console.error(e);
      A.sol = { kind: 'none', n: 0, residuals: [], measured: [], warnings: ['The calibration could not be computed: ' + e.message],
                sigmaCal: () => Infinity };
    }
    A.gm = calib.geometry(A.model, A.sol);
    A.relocate();
    A.emit('project');
  };

  // Apply an edit now; with a bridge, send it and keep it pending until confirmed.
  A.edit = function (op) {
    try { ST.apply(A.proj, op); } catch (e) { A.toast('Could not save that: ' + e.message, true); return false; }
    if (A.bridgeMode) {
      const p = { cid: A.newId(), op: op };
      A.pending.push(p);
      A.store.set(KEY_PENDING, A.pending);
      sendOp(p, false);
    } else {
      A.base = ST.apply(A.base, op);
      A.store.set(KEY, A.base);
    }
    A.recompute();
    return true;
  };

  function sendOp(p, replay) {
    if (!A.bridge.ready) return;                         // sent when the bridge is back
    A.send({ t: 'op', op: p.op }, m => {
      const i = A.pending.indexOf(p);
      if (i >= 0) { A.pending.splice(i, 1); A.store.set(KEY_PENDING, A.pending); }
      if (m.t === 'nack' && !replay) A.toast('The laptop refused a change: ' + m.msg, true);
      A.recompute();
    }, true);
  }
  function replayPending() { A.pending.slice().forEach(p => sendOp(p, true)); }
  function setBase(st) { A.base = st; A.store.set(KEY, st); A.recompute(); }

  // First contact with a bridge whose project differs from ours.
  function adopt(st) {
    if (!A.ui.everSynced && !ST.isEmpty(st)) {
      // this browser has never synced: keep any saved points the bridge doesn't have
      const have = new Set((st.savedPoints || []).map(p => p.id));
      (A.proj.savedPoints || []).forEach(p => {
        const queued = A.pending.some(q => q.op.op === 'push' && q.op.value && q.op.value.id === p.id);
        if (!have.has(p.id) && !queued) A.pending.push({ cid: A.newId(), op: { op: 'push', path: ['savedPoints'], value: p } });
      });
      A.store.set(KEY_PENDING, A.pending);
    }
    if (A.migrationText() && !A.ui.everSynced)
      A.toast(A.migrationText() + (ST.isEmpty(st) ? '' : ' (the laptop already had a project — its calibration is kept)'), false, 8000);
    A.ui.everSynced = true; A.saveUi();
    setBase(st);
    replayPending();
  }

  function onBridgeState(st) {
    const b = A.bridge;
    if (!st || st.schema !== 2) return;
    if (b.ready) { setBase(st); return; }
    b.ready = true;
    if (ST.isEmpty(st) && !ST.isEmpty(A.proj)) {
      // The bridge has no project (first run of this version, or its data was lost): give it ours.
      const mine = { schema: 2, rough: A.proj.rough, control: A.proj.control, settings: A.proj.settings, savedPoints: A.proj.savedPoints };
      A.send({ t: 'replaceState', state: mine, onlyIfEmpty: true }, m => {
        if (m.t === 'ack') {
          A.pending = []; A.store.set(KEY_PENDING, []);
          A.ui.everSynced = true; A.saveUi();
          A.toast((A.migrationText() || 'Your project') + ' — now saved on the laptop', false, 7000);
          A.recompute();
        } else adopt(st);
      });
      return;                                            // the broadcast that follows becomes our base
    }
    adopt(st);
  }

  // ================================================================== bridge connection
  A.bridge = { ws: null, connected: false, ready: false, hello: null, cfg: null, status: null,
               waiting: {}, seq: 0, backoff: 800, lastMsg: 0, everConnected: false, since: Date.now() };

  // send a message; cb(reply) gets the bridge's answer (ack/nack/...). keep: an op that must
  // survive a dropped connection (it stays pending and is re-sent).
  A.send = function (msg, cb, keep) {
    const b = A.bridge;
    if (!b.ws || b.ws.readyState !== 1) {
      if (cb && !keep) cb({ t: 'nack', msg: 'not connected to the laptop' });
      return false;
    }
    if (cb) { msg.id = 'c' + (++b.seq); b.waiting[msg.id] = { cb: cb, keep: !!keep, t: Date.now() }; }
    try { b.ws.send(JSON.stringify(msg)); } catch (e) { return false; }
    return true;
  };

  function connect() {
    const b = A.bridge;
    if (!A.bridgeMode || b.ws) return;
    let ws;
    try { ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws'); }
    catch (e) { scheduleReconnect(); return; }
    b.ws = ws;
    ws.onopen = () => { b.connected = true; b.lastMsg = Date.now(); b.backoff = 800; };
    ws.onmessage = e => {
      b.lastMsg = Date.now();
      let m;
      try { m = JSON.parse(e.data); } catch (x) { return; }
      onBridgeMessage(m);
    };
    ws.onclose = () => dropSocket(ws);
    ws.onerror = () => {};
  }
  function dropSocket(ws) {
    const b = A.bridge;
    if (b.ws !== ws) return;
    ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
    try { ws.close(); } catch (e) { /* already closed */ }
    const was = b.connected;
    b.ws = null; b.connected = false; b.ready = false;
    if (was) b.since = Date.now();
    Object.keys(b.waiting).forEach(id => {
      const w = b.waiting[id];
      delete b.waiting[id];
      if (!w.keep) w.cb({ t: 'nack', msg: 'the connection to the laptop was lost' });
    });
    A.emit('bridge');
    scheduleReconnect();
  }
  function scheduleReconnect() {
    const b = A.bridge;
    clearTimeout(b.timer);
    b.timer = setTimeout(connect, b.backoff);
    b.backoff = Math.min(b.backoff * 1.6, 8000);
  }
  A.reconnectNow = function () {
    const b = A.bridge;
    if (b.ws) dropSocket(b.ws);
    clearTimeout(b.timer); b.backoff = 800; connect();
  };
  // The bridge sends status every second; silence means a dead link (phones on flaky Wi-Fi).
  A.bridgeWatchdog = function () {
    const b = A.bridge;
    if (b.ws && b.connected && Date.now() - b.lastMsg > 7000) dropSocket(b.ws);
  };
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && A.bridgeMode && !A.bridge.ws) { clearTimeout(A.bridge.timer); connect(); }
  });

  function onBridgeMessage(m) {
    const b = A.bridge;
    if (m.id && b.waiting[m.id]) {
      const w = b.waiting[m.id];
      delete b.waiting[m.id];
      w.cb(m);
      if (m.t === 'ack' || m.t === 'nack') return;
    }
    switch (m.t) {
      case 'hello': b.hello = m; b.everConnected = true; A.emit('bridge'); break;
      case 'config': b.cfg = m.cfg; A.emit('config'); break;
      case 'state': onBridgeState(m.state); break;
      case 'status': b.status = m; A.emit('status'); break;
      case 'nmea': if (Array.isArray(m.l)) A.onNmeaLines(m.l, 'bridge'); break;
      case 'nack': if (m.msg) A.toast(m.msg, true); break;
    }
  }
  A.isLocal = () => !!(A.bridge.hello && A.bridge.hello.local);

  // ================================================================== live receiver pipeline
  A.live = { gga: null, ggaT: 0, gst: null, gstT: 0, gstSeenT: 0, rmc: null, rmcT: 0, pos: null,
             xy: null, where: null, sigma: null, sigmaEst: true, src: null, srcT: 0, lines: 0, bad: 0 };

  A.onNmeaLines = function (lines, src) {
    const L = A.live, now = Date.now();
    if (A.demo && src !== 'demo') A.stopDemo('Receiver data arrived — demo stopped');
    for (let i = 0; i < lines.length; i++) {
      const m = nmea.parse(lines[i]);
      if (!m) { L.bad++; continue; }
      L.lines++;
      if (m.type === 'GGA') onGga(m, now);
      else if (m.type === 'GST') { L.gst = m; L.gstT = now; if (m.sigmaH != null) L.gstSeenT = now; }
      else if (m.type === 'RMC') { L.rmc = m; L.rmcT = now; }
      else if (m.type === 'GSA') L.gsa = m;
    }
    L.src = src; L.srcT = now;
  };

  function onGga(m, now) {
    const L = A.live;
    L.gga = m; L.ggaT = now;
    if (!m.valid) { L.pos = null; L.xy = null; L.where = null; A.emit('fix'); return; }
    const gst = L.gst && now - L.gstT < 2500 ? L.gst : null;       // GST of this or the previous epoch
    const sg = nmea.sigmaFor(m, gst);
    L.sigma = sg.sigma; L.sigmaEst = sg.est;
    L.pos = { lat: m.lat, lon: m.lon, h: m.h, alt: m.alt, q: m.q, sats: m.sats, t: now, sigma: sg.sigma };
    A.relocate();
    A.emit('fix');
  }

  // Where the current position is relative to the parcels (re-run when the outline moves).
  A.relocate = function () {
    const L = A.live;
    if (!L.pos || !A.gm) { L.xy = null; L.where = null; return; }
    const q = A.gm.frame.toEnu(L.pos.lat, L.pos.lon, L.pos.h);
    L.xy = { x: q.x, y: q.y };
    L.where = locate.where(A.model, A.gm, A.sol, q.x, q.y, L.sigma, A.proj.settings);
  };
  A.fixAge = () => A.live.ggaT ? (Date.now() - A.live.ggaT) / 1000 : Infinity;
  A.hasFix = () => !!A.live.pos && A.fixAge() < 5;

  // Which correction service the receiver is using right now (stored with measurements so
  // monuments measured on different services — different datums — can be flagged).
  A.corrSource = function () {
    const n = A.bridge.status && A.bridge.status.ntrip;
    if (n && n.state === 'streaming') return (n.host || '') + '/' + (n.mount || '');
    const g = A.live.gga;
    if (g && g.age != null && g.station && !/^0+$/.test(g.station)) return 'station ' + g.station;
    return null;
  };

  // ================================================================== demo walk
  // Walks around the 35-acre outline, weaving in and out across the line, at 4 Hz.
  A.demo = null;
  function nmeaLine(body) {
    let c = 0;
    for (let i = 0; i < body.length; i++) c ^= body.charCodeAt(i);
    return '$' + body + '*' + (c < 16 ? '0' : '') + c.toString(16).toUpperCase();
  }
  function ddmm(v, w) {
    const a = Math.abs(v);
    let d = Math.floor(a), m = ((a - d) * 60).toFixed(7);
    if (+m >= 60) { d += 1; m = (0).toFixed(7); }
    return String(d).padStart(w, '0') + (+m < 10 ? '0' : '') + m;
  }
  A.fakeNmea = function (lat, lon, h, q, sigma) {
    const d = new Date(), p2 = n => String(n).padStart(2, '0');
    const tm = p2(d.getUTCHours()) + p2(d.getUTCMinutes()) + p2(d.getUTCSeconds()) + '.' + p2(Math.floor(d.getUTCMilliseconds() / 10));
    const sep = -15.4, s = (sigma || 0.012) / Math.SQRT2;
    return [
      nmeaLine(`GNGGA,${tm},${ddmm(lat, 2)},${lat >= 0 ? 'N' : 'S'},${ddmm(lon, 3)},${lon < 0 ? 'W' : 'E'},${q || 4},20,0.6,${(h - sep).toFixed(3)},M,${sep.toFixed(3)},M,1.0,0000`),
      nmeaLine(`GNGST,${tm},0.012,${s.toFixed(3)},${s.toFixed(3)},0.0,${s.toFixed(3)},${s.toFixed(3)},0.020`)
    ];
  };
  A.startDemo = function () {
    if (!A.gm) { A.toast('Place the outline first (Menu → Calibrate).', true); return; }
    if (A.live.src && A.live.src !== 'demo' && Date.now() - A.live.srcT < 3000) {
      A.toast('A receiver is sending data — the demo only runs without one.', true); return;
    }
    const ring = A.gm.rings.P1.xy.concat([A.gm.rings.P1.xy[0]]);
    const cum = [0];
    for (let i = 1; i < ring.length; i++) cum.push(cum[i - 1] + Math.hypot(ring[i].x - ring[i - 1].x, ring[i].y - ring[i - 1].y));
    const total = cum[cum.length - 1], f = A.gm.frame;
    let s = 0, k = 1;
    A.demo = { timer: setInterval(() => {
      s = (s + 1.8 * 0.25) % total;                    // 1.8 m/s
      while (k > 1 && cum[k - 1] > s) k--;
      while (k < ring.length - 1 && cum[k] < s) k++;
      const a = ring[k - 1], b = ring[k], L = cum[k] - cum[k - 1] || 1, t = (s - cum[k - 1]) / L;
      const tx = (b.x - a.x) / L, ty = (b.y - a.y) / L;
      const off = 6 * Math.sin(2 * Math.PI * s / 80);  // weave ±6 m across the line
      const x = a.x + (b.x - a.x) * t - ty * off, y = a.y + (b.y - a.y) * t + tx * off;
      const g = f.toGeo(x, y, 0);
      A.onNmeaLines(A.fakeNmea(g.lat, g.lon, f.origin.h, 4, 0.012), 'demo');
    }, 250) };
    A.emit('demo');
    A.toast('Demo: walking the 35-acre line. Stop it in Tools.');
  };
  A.stopDemo = function (msg) {
    if (!A.demo) return;
    clearInterval(A.demo.timer);
    A.demo = null;
    A.emit('demo');
    if (msg) A.toast(msg);
  };

  // ================================================================== direct receiver (Web Serial)
  A.serial = null;
  A.serialSupported = 'serial' in navigator;
  A.serialConnect = async function (baud) {
    if (!A.serialSupported) {
      A.toast('This browser can\'t open serial ports — use Chrome or Edge on a computer, or run the bridge.', true);
      return;
    }
    if (A.serial) { A.serialDisconnect(); return; }
    let port;
    try {
      port = await navigator.serial.requestPort();
      await port.open({ baudRate: baud || 115200 });
    } catch (e) {
      if (e && e.name !== 'NotFoundError') A.toast('Could not open the port: ' + e.message, true);
      return;
    }
    const S = A.serial = { port: port, reader: null, buf: '', closing: false };
    A.emit('serial');
    A.toast('Receiver connected directly (no corrections this way)');
    try {
      while (port.readable && !S.closing) {
        S.reader = port.readable.getReader();
        const dec = new TextDecoder();
        try {
          for (;;) {
            const r = await S.reader.read();
            if (r.done) break;
            S.buf += dec.decode(r.value, { stream: true });
            const parts = S.buf.split(/\r?\n/);
            S.buf = parts.pop();
            if (S.buf.length > 4096) S.buf = '';
            const lines = parts.map(x => x.trim()).filter(x => x.charAt(0) === '$');
            if (lines.length) A.onNmeaLines(lines, 'serial');
          }
        } catch (e) {
          if (!S.closing) A.toast('Receiver link error: ' + e.message, true);
        } finally {
          try { S.reader.releaseLock(); } catch (e) { /* ignore */ }
        }
      }
    } finally {
      try { await port.close(); } catch (e) { /* ignore */ }
      if (A.serial === S) A.serial = null;
      A.emit('serial');
    }
  };
  A.serialDisconnect = function () {
    const S = A.serial;
    if (!S) return;
    S.closing = true;
    if (S.reader) S.reader.cancel().catch(() => {});
  };

  // ================================================================== keep the screen on
  let wake = null, wakeBusy = false;
  A.updateWakeLock = function () {
    if (!('wakeLock' in navigator) || wakeBusy) return;
    const want = A.ui.wake && document.visibilityState === 'visible' && A.fixAge() < 15;
    if (want && !wake) {
      wakeBusy = true;
      navigator.wakeLock.request('screen').then(l => {
        wake = l;
        l.addEventListener('release', () => { wake = null; });
      }).catch(() => {}).then(() => { wakeBusy = false; });
    } else if (!want && wake) {
      wakeBusy = true;
      wake.release().catch(() => {}).then(() => { wake = null; wakeBusy = false; });
    }
  };
  A.wakeSupported = () => 'wakeLock' in navigator;

  // ================================================================== alert sound
  let actx = null;
  function audio() {
    if (!actx) {
      const C = window.AudioContext || window.webkitAudioContext;
      if (C) { try { actx = new C(); } catch (e) { actx = null; } }
    }
    if (actx && actx.state === 'suspended') actx.resume().catch(() => {});
    return actx;
  }
  // browsers only allow sound after a tap: unlock on the first one
  document.addEventListener('pointerdown', () => { if (A.proj && A.proj.settings.edgeAlertFt) audio(); }, { passive: true });
  A.beep = function (n, hz) {
    n = n || 1;
    const c = audio();
    if (c) {
      for (let i = 0; i < n; i++) {
        const o = c.createOscillator(), g = c.createGain(), t0 = c.currentTime + i * 0.22;
        o.frequency.value = hz || 880;
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(0.3, t0 + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.16);
        o.connect(g); g.connect(c.destination);
        o.start(t0); o.stop(t0 + 0.18);
      }
    }
    if (navigator.vibrate) navigator.vibrate(n > 1 ? [160, 90, 160] : 220);
  };

  A.startBridge = function () { if (A.bridgeMode) connect(); };
})();
