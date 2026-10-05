/*
 * Parcel RTK — the menu (Status, Calibrate, Points, Tools, Settings), the measure and
 * save dialogs, and exports. Panes are plain HTML strings; every button carries a
 * data-act that app.js dispatches, so nothing here holds event listeners.
 */
(function () {
  'use strict';
  const P = window.PRTK, A = P.app, calib = P.calib, geo = P.geo, cogo = P.cogo;
  const model = A.model, esc = A.esc;

  // ================================================================== pane plumbing
  const panes = {};
  A.paneEl = name => A.$(`[data-pane="${name}"]`);
  A.isPanelOpen = () => A.$('#panel').classList.contains('open');
  A.activeTab = () => A.ui.tab || 'status';
  function editing(el) {
    const f = document.activeElement;
    return f && el.contains(f) && /^(INPUT|SELECT|TEXTAREA)$/.test(f.tagName) && f.type !== 'checkbox' && f.type !== 'button';
  }
  // Re-render a pane if it is showing. how: undefined = only if visible; 'show' = it just came
  // into view (render if never rendered or out of date); 'force' = always. A pane being typed
  // in, or a settings form with unsaved changes, is left alone (marked stale) instead.
  A.renderPane = function (name, how) {
    const el = A.paneEl(name);
    if (!el || !panes[name]) return;
    if (how !== 'force') {
      if (!A.isPanelOpen() || A.activeTab() !== name) { el._stale = true; return; }
      if (how === 'show' && el._rendered && !el._stale) return;
      if (editing(el) || (name === 'settings' && cfgDirty && el._rendered)) { el._stale = true; return; }
    }
    const body = A.$('#panel .p-body'), top = body.scrollTop;
    panes[name](el);
    el._rendered = true; el._stale = false;
    body.scrollTop = top;
    if (A.refreshLive) A.refreshLive(true);
  };
  A.renderStale = function () {
    const el = A.paneEl(A.activeTab());
    if (el && el._stale) A.renderPane(A.activeTab());
  };

  const pill = (text, cls) => `<span class="pill ${cls}">${esc(text)}</span>`;
  const kv = rows => '<div class="kv">' + rows.filter(Boolean).map(r => `<span>${esc(r[0])}</span><span>${r[1]}</span>`).join('') + '</div>';
  const distSpan = (lat, lon) => `<span data-live="dist" data-lat="${lat}" data-lon="${lon}">—</span>`;
  const dur = s => s == null ? '—' : s < 90 ? Math.round(s) + ' s' : s < 5400 ? Math.round(s / 60) + ' min' : (s / 3600).toFixed(1) + ' h';

  // ================================================================== STATUS
  const RX_STATE = {
    open: ['Connected', 'p-fix'], probing: ['Searching', 'p-float'], opening: ['Opening', 'p-float'], starting: ['Starting', 'p-float'],
    noport: ['No COM ports', 'p-none'], notfound: ['Not found', 'p-none'], busy: ['Port busy', 'p-none'], missing: ['Port gone', 'p-none'],
    noresponse: ['No answer', 'p-none'], silent: ['Silent', 'p-none'], dropped: ['Reconnecting', 'p-float'], error: ['Error', 'p-none'],
    off: ['Off', 'p-none'], stopped: ['Stopped', 'p-none']
  };
  const RX_HINT = {
    noport: 'No COM ports at all. Pair the receiver in Windows Bluetooth settings (set the receiver\'s Bluetooth to Classic/SPP), or plug in its USB cable.',
    notfound: 'No port is sending GPS data. Is the receiver on and in range? Close u-center or other GPS programs that might hold the port.',
    busy: 'Another program has the port open — close old bridge windows, u-center or other GPS apps.',
    missing: 'That COM port is gone. The bridge will look for the receiver on the other ports.',
    noresponse: 'The port exists but the receiver didn\'t answer. Is it on and in range? For Bluetooth the bridge needs the OUTGOING port.',
    silent: 'The receiver stopped sending. Check its battery and that it is within Bluetooth range (~10 m).',
    dropped: 'The link dropped; reconnecting automatically.'
  };
  const NT_STATE = { streaming: ['Streaming', 'p-fix'], connecting: ['Connecting', 'p-float'], starting: ['Starting', 'p-float'],
                     refused: ['Refused', 'p-none'], error: ['Error', 'p-none'], stopped: ['Stopped', 'p-idle'] };

  function rxSourceText(rx) {
    if (rx.type === 'serial') return rx.port && rx.port !== 'auto' ? rx.port + (rx.baud ? ' · ' + rx.baud + ' baud' : '') : 'COM port (automatic)';
    if (rx.type === 'tcp') return 'Network ' + (rx.detail || '');
    if (rx.type === 'test') return 'Simulated receiver (test mode)';
    return rx.type || '—';
  }
  function rxCard() {
    const L = A.live, b = A.bridge, rx = b.status && b.status.rx, g = L.gga, age = A.fixAge();
    let src = '', state = null, hint = '';
    if (A.demo) src = 'Demo walk (simulated)';
    else if (A.serial) src = 'Direct connection in this browser';
    else if (!A.bridgeMode) { src = 'None'; hint = 'Opened as a file, so there is no bridge. Start Parcel RTK on the laptop (python rtk_bridge.py), or use Tools → connect a receiver directly.'; }
    else if (!b.connected) { src = 'Laptop not reachable'; hint = 'Waiting for the Parcel RTK window on the laptop…'; }
    else if (rx) { src = rxSourceText(rx); state = RX_STATE[rx.state] || [rx.state, 'p-idle']; if (rx.state !== 'open') hint = RX_HINT[rx.state] || rx.detail || ''; }
    const fresh = age < 3;
    const fixPill = !g ? pill('No data', 'p-idle') : !fresh ? pill('No data ' + Math.round(age) + ' s', 'p-none')
      : g.valid ? pill(A.fixName(g.q), g.q === 4 ? 'p-fix' : g.q === 5 ? 'p-float' : 'p-gps') : pill('No fix', 'p-none');
    const rows = [
      ['Source', esc(src) + (state ? ' ' + pill(state[0], state[1]) : '')],
      rx && rx.state === 'open' ? ['Data', (rx.nmeaPerSec || 0) + ' sentences/s' + (rx.badChecksums ? ` · <span class="warn">${rx.badChecksums} garbled</span>` : '')] : null,
      ['Fix', fixPill],
      g && g.valid ? ['Satellites', esc(g.sats != null ? g.sats : '—') + (g.hdop != null ? ' · HDOP ' + g.hdop.toFixed(1) : '')] : null,
      L.pos ? ['Accuracy', A.fmtAcc(L.sigma) + (L.sigmaEst ? ' <span class="dim">(estimated)</span>' : ' <span class="dim">(from receiver)</span>')] : null,
      g && g.valid ? ['Correction age', g.age != null ? g.age.toFixed(1) + ' s' + (g.station && !/^0+$/.test(g.station) ? ' · station ' + esc(g.station) : '') : 'none'] : null,
      g && g.alt != null ? ['Elevation', A.fmtElev(g.alt) + ' <span class="dim">above sea level</span>'] : null
    ];
    if (fresh && g && g.q === 4 && Date.now() - L.gstSeenT > 30000)
      hint += (hint ? '<br>' : '') + 'Accuracy is estimated because the receiver isn\'t sending GST. Turn on the GST message in the receiver\'s NMEA settings to see its real accuracy.';
    if (fresh && g && g.valid && g.q !== 4 && g.q !== 5 && !(b.status && b.status.ntrip))
      hint += (hint ? '<br>' : '') + 'No RTK: set up a correction service in Settings → Corrections to get ±2 cm.';
    return `<div class="ttl">Receiver ${fixPill}</div>${kv(rows)}${hint ? `<div class="hint">${hint}</div>` : ''}`;
  }
  function ntripCard() {
    const b = A.bridge, n = b.status && b.status.ntrip, cfg = b.cfg && b.cfg.ntrip;
    if (!A.bridgeMode) return '<div class="ttl">Corrections</div><div class="hint">Corrections come through the bridge on the laptop.</div>';
    if (!b.connected) return '<div class="ttl">Corrections</div><div class="hint">Waiting for the laptop…</div>';
    if (!n) {
      return `<div class="ttl">Corrections ${pill('Off', 'p-idle')}</div><div class="hint">${cfg && cfg.enabled ? 'Turned on but the caster, mountpoint or login is missing.' : 'Not set up.'}
        Corrections (NTRIP) turn ±1 m into ±2 cm. Set yours up in Settings → Corrections.</div>
        <div class="row"><button class="btn small" data-act="tab" data-tab="settings">Open settings</button></div>`;
    }
    const st = NT_STATE[n.state] || [n.state, 'p-idle'];
    const hints = [];
    if (n.state === 'refused') hints.push('The caster said no: ' + esc(n.detail) + '. Check the username, password and mountpoint (PointPerfect: NEAR-RTCM).');
    else if (n.state === 'error') hints.push(esc(n.detail) + ' — retrying. Is the laptop online?');
    if (n.format === 'SPARTN') hints.push('This mountpoint sends SPARTN, which the receiver ignores when it arrives over Bluetooth. Use an RTCM mountpoint (PointPerfect: NEAR-RTCM).');
    if (n.base && n.base.distKm > 35) hints.push('That base station is ' + n.base.distKm + ' km away — too far for a reliable RTK FIX. Pick one within ~15 km, or use a network service.');
    const g = A.live.gga;
    if (n.state === 'streaming' && g && g.valid && g.q !== 4 && A.fixAge() < 3)
      hints.push('Corrections are arriving. RTK FIX needs open sky and can take a minute or two (FLOAT comes first).');
    return `<div class="ttl">Corrections ${pill(st[0], st[1])}</div>${kv([
      ['Service', esc((n.host || '') + ' / ' + (n.mount || ''))],
      n.state === 'streaming' ? ['Data', (n.bytesPerSec || 0) + ' bytes/s · ' + ((n.total || 0) / 1024).toFixed(0) + ' kB total'] : null,
      n.format ? ['Format', esc(n.format) + (n.types && n.types.length ? ' <span class="dim">(' + esc(n.types.slice(0, 10).join(', ')) + ')</span>' : '')] : null,
      n.base ? ['Base station', esc(n.base.station) + (n.base.distKm != null ? ' · ' + n.base.distKm + ' km away' : '')] : null,
      n.state === 'streaming' ? ['Connected for', dur(n.upSec)] : null
    ])}${hints.map(h => `<div class="hint warn">${h}</div>`).join('')}`;
  }
  // plain-words summary of how the outline is placed
  A.geoSummary = function () {
    const s = A.sol, n = (s.measured || []).length, set = A.proj.settings;
    if (s.kind === 'none') return { pill: pill('Not placed', 'p-none'),
      text: 'The outline isn\'t on the map yet. Place it by hand over the satellite photo, or stand on a monument and measure it.' };
    if (s.kind === 'rough') return { pill: pill('By hand', 'p-float'),
      text: `Placed by eye over the satellite photo — trust it to about ${A.fmtLen(set.roughSigmaFt * A.K, 0)}. Measure two monuments to lock it to the ground.` };
    if (s.kind === 'one') return { pill: pill('1 monument', 'p-float'),
      text: `Pinned to the ${esc(A.shortName(s.measured[0]))} monument. ` + (s.thetaFromRough ? 'Its rotation still comes from your hand placement — measure a second monument to fix it.'
        : 'Its rotation is unknown — rotate it by hand, or measure a second monument.') };
    let text = `Fitted to ${n} measured monuments. `;
    if (s.distCheck) {
      const d = s.distCheck, lvl = Math.abs(d.diffFt) <= 0.3 ? 'good-t' : Math.abs(d.diffFt) <= 1 ? 'warn' : 'bad';
      text += `Measured distance ${esc(A.shortName(d.a))} → ${esc(A.shortName(d.b))}: <b>${d.measFt.toFixed(2)} ft</b>; the documents say ${d.platFt.toFixed(2)} ft
        (<span class="${lvl}">${d.diffFt >= 0 ? '+' : ''}${d.diffFt.toFixed(2)} ft</span>). `;
    } else text += `They agree with the documents to ${A.fmtLen(s.rms)} (RMS). `;
    if (set.hold) text += 'The outline passes exactly through each one (monuments control).';
    return { pill: pill(n + ' monuments', s.warnings.length ? 'p-float' : 'p-fix'), text: text };
  };
  function geoCard() {
    const g = A.geoSummary();
    return `<div class="ttl">Outline ${g.pill}</div><div class="hint">${g.text}</div>
      ${A.sol.warnings.map(w => `<div class="hint warn">${esc(w)}</div>`).join('')}
      <div class="row"><button class="btn small" data-act="tab" data-tab="calib">Calibrate</button></div>`;
  }
  const qrCache = {};
  function lanCard() {
    const h = A.bridge.hello;
    if (!A.bridgeMode) return '<div class="hint">Run the bridge on the laptop to use a phone as a second screen.</div>';
    if (!h) return '<div class="hint">Waiting for the laptop…</div>';
    if (!h.local) return `<div class="hint">You're viewing from another device. Settings can only be changed on the laptop.</div>`;
    if (!h.lan || !h.lan.length) return '<div class="hint">Phone access is off, or the laptop isn\'t on a network. Turn it on in Settings → Phone access.</div>';
    return h.lan.map(u => {
      let svg = qrCache[u];
      if (svg == null) {
        try { const q = qrcode(0, 'M'); q.addData(u); q.make(); svg = q.createSvgTag({ cellSize: 4, margin: 2, scalable: true }); } catch (e) { svg = ''; }
        qrCache[u] = svg;
      }
      return `<div class="row" style="align-items:center"><div class="qr">${svg}</div><div class="grow">
        <div class="mono">${esc(u)}</div><div class="row"><button class="btn small" data-act="copytext" data-text="${esc(u)}">Copy link</button></div></div></div>`;
    }).join('') + `<div class="hint">Scan with a phone on the same Wi-Fi — it shows the same live view. If it doesn't load, allow Python through the Windows firewall (Private networks).</div>`;
  }
  function docsCard() {
    const ck = model.checks, rows = [];
    const geom = ck.filter(c => c.id === 'geom');
    ck.forEach(c => {
      if (c.id === 'geom' || c.id === 'errata') return;
      let val = '', ok = null;
      if (c.ft != null) { val = c.ft.toFixed(c.ft < 1 ? 3 : 2) + ' ft'; if (c.ratio && c.ft > 0.001) val += ' (1:' + Math.round(c.ratio).toLocaleString('en-US') + ')'; }
      if (c.ac != null) val = (c.ac >= 0 ? '+' : '') + c.ac.toFixed(3) + ' ac';
      if (c.good != null) ok = Math.abs(c.ft != null ? c.ft : c.ac) <= c.good;
      rows.push(`<div class="${ok === null ? 'hint' : ok ? 'check-ok' : 'check-warn'}" style="margin-top:5px;font-size:13px">${esc(c.label)}: <b>${val}</b></div>`);
    });
    if (geom.length) {
      const worst = Math.max.apply(null, geom.map(c => c.ft));
      rows.push(`<div class="${worst <= 0.05 ? 'check-ok' : 'check-warn'}" style="margin-top:5px;font-size:13px">All ${geom.length} curve checks (arc length, chord, tangency): worst ${worst.toFixed(3)} ft</div>`);
    }
    const er = P.data.deed.errata[0];
    return `<div class="hint">Both recorded documents were transcribed and cross-checked. Lodgepole Pines (the field survey) is held fixed; the 35-acre deed is fitted to the two monuments it shares with it.</div>
      ${rows.join('')}
      <details style="margin-top:8px"><summary class="hint">The typo in the 35-acre deed</summary>
        <div class="hint">Course ${er.course + 1}: radius written <b>${er.written}</b>, corrected to <b>${er.corrected}</b>. ${esc(er.why)}</div></details>
      <div class="hint">Lot 1 west line: ${esc(P.data.westLineExtras)}</div>`;
  }
  function aboutCard() {
    const b = A.bridge, st = b.status;
    return kv([
      ['App', 'Parcel RTK ' + esc(A.VERSION)],
      b.hello ? ['Laptop bridge', esc(b.hello.version) + (b.hello.version !== A.VERSION ? ' <span class="warn">(different version — reload)</span>' : '')] : null,
      st ? ['Viewers', esc(st.viewers)] : null,
      st ? ['Bridge running', dur(st.upSec)] : null,
      A.bridgeMode ? ['Connection', b.connected ? '<span class="good-t">connected</span>' : '<span class="bad">reconnecting…</span>'] : ['Storage', 'this browser only (opened as a file)'],
      A.pending.length ? ['Unsynced edits', '<span class="warn">' + A.pending.length + ' (sent when the laptop is reachable)</span>'] : null
    ]);
  }
  function startCard() {
    if (A.sol.kind !== 'none') return '';
    return `<div class="card" style="border-color:var(--accent)"><div class="ttl">Start here</div>
      <div class="hint">1. Open <b>Calibrate</b> and put the outline roughly in place over the satellite photo.<br>
      2. Get RTK FIX (Settings → Corrections), stand on two monuments and measure them — the outline then locks to the ground.<br>
      3. The top bar tells you IN, OUT, or ON LINE wherever you walk.</div>
      <div class="row"><button class="btn small primary" data-act="tab" data-tab="calib">Calibrate</button></div></div>`;
  }
  panes.status = function (el) {
    el.innerHTML = `${startCard()}
      <div class="card" id="stRx"></div>
      <div class="card" id="stNtrip"></div>
      <div class="card" id="stGeo"></div>
      <div class="sec" style="margin-top:14px"><h3>Phone or tablet</h3><div class="card" id="stLan"></div></div>
      <div class="sec"><h3>Survey documents</h3><div class="card">${docsCard()}</div></div>
      <div class="sec"><h3>About</h3><div class="card"><div id="stAbout"></div>
        <div class="row">${A.bridgeMode ? '<button class="btn small" data-act="showlog">Show bridge log</button><button class="btn small" data-act="reconnect">Reconnect</button>' : ''}
        <button class="btn small" data-act="reload">Reload app</button></div><div id="stLog"></div></div></div>`;
    A.updateStatusPane(true);
  };
  const lastHtml = {};
  function setHtml(id, html) {
    const el = document.getElementById(id);
    if (el && lastHtml[id] !== html) { el.innerHTML = html; lastHtml[id] = html; }
  }
  // cheap, targeted refresh (called every second while Status is showing)
  A.updateStatusPane = function (fresh) {
    if (fresh) Object.keys(lastHtml).forEach(k => { delete lastHtml[k]; });
    if (!A.isPanelOpen() || A.activeTab() !== 'status' || !document.getElementById('stRx')) return;
    setHtml('stRx', rxCard());
    setHtml('stNtrip', ntripCard());
    setHtml('stGeo', geoCard());
    setHtml('stLan', lanCard());
    setHtml('stAbout', aboutCard());
  };
  A.showLog = function () {
    A.send({ t: 'getLog' }, m => {
      const el = document.getElementById('stLog');
      if (!el) return;
      el.innerHTML = m.t === 'log' ? `<div class="log mono" style="margin-top:8px">${esc((m.lines || []).slice(-120).join('\n'))}</div>`
        : `<div class="hint bad">${esc(m.msg || 'no log')}</div>`;
      const box = el.querySelector('.log');
      if (box) box.scrollTop = box.scrollHeight;
    });
  };

  // ================================================================== CALIBRATE
  const GROUPS = [
    ['shared', 'On both parcels — measure these first', ['TPB', 'LPNE']],
    ['lot1', 'Lot 1', ['W16', 'L1NE', 'L1SE']],
    ['p1', '35-acre parcel', ['P1NW', 'P1NE']],
    ['section', 'Section corner (stable survey control)', ['S14']]
  ];
  function monCard(id) {
    const p = model.points[id], c = A.proj.control[id], g = A.gm && A.gm.pts[id];
    const res = (A.sol.residuals || []).find(q => q.id === id);
    let st;
    if (!c) st = pill('not measured', 'p-idle');
    else if (c.use === false) st = pill('ignored', 'p-float');
    else if (A.sol.kind === 'fit' && res) st = `<span class="res ${res.level}">${A.fmtLen(res.r)}</span>`;
    else st = pill('measured', 'p-fix');
    return `<div class="card mon${c && c.use !== false ? ' measured' : ''}">
      <div class="ttl">${esc(A.isMain(id) ? A.SHORT[id] : p.name)} <span class="dim" style="font-weight:500;font-size:12px">${esc(id)}</span>${st ? '<span style="margin-left:auto">' + st + '</span>' : ''}</div>
      ${A.isMain(id) ? `<div class="desc">${esc(p.name)}</div>` : ''}
      <div class="desc">${esc(p.monument || '')}</div>
      <div class="meas">${esc(A.pointStatusText(id))}${g ? ' · ' + distSpan(g.lat, g.lon) + ' from you' : ''}</div>
      <div class="row">
        ${g ? `<button class="btn small" data-act="nav" data-kind="pt" data-id="${id}">Go</button>` : ''}
        <button class="btn small good" data-act="measure" data-id="${id}">${c ? 'Re-measure' : 'Measure'}</button>
        ${c ? `<button class="btn small" data-act="ctluse" data-id="${id}" data-use="${c.use === false ? 1 : 0}">${c.use === false ? 'Use' : 'Ignore'}</button>
               <button class="btn small danger" data-act="ctldel" data-id="${id}">Remove</button>` : ''}
      </div></div>`;
  }
  panes.calib = function (el) {
    const s = A.sol, set = A.proj.settings, g = A.geoSummary(), rot = A.outlineRotation();
    let res = '';
    if (s.kind === 'fit') {
      res = '<div class="kv">' + s.residuals.map(r => `<span>${esc(A.shortName(r.id))}</span><span class="res ${r.level}">${A.fmtLen(r.r)}</span>`).join('') + '</div>';
      res += `<div class="hint">How far each monument is from where the documents put it, after the best fit. Under 0.3 ft is excellent; over 1 ft means a wrong or disturbed monument.</div>`;
    }
    const adjustBtn = A.adjustAllowed() ? `<button class="btn small ${s.kind === 'none' ? 'primary' : ''}" data-act="adjust">${s.kind === 'none' ? 'Place the outline here' : s.kind === 'one' ? 'Rotate by hand' : 'Adjust by hand'}</button>` : '';
    const roads = (prefix, title) => `<details class="grp"><summary>${title}</summary>${[1, 2, 3, 4, 5, 6, 7].map(i => monCard(prefix + i)).join('')}</details>`;
    el.innerHTML = `
      <div class="card"><div class="ttl">How the outline is placed ${g.pill}</div><div class="hint">${g.text}</div>
        ${s.placement && s.kind !== 'none' ? kv([['Plat north', cogo.fmtDms(Math.abs(rot)) + (rot >= 0 ? ' west' : ' east') + ' of true north'],
          s.kind === 'fit' && set.fitScale && s.scalePpm != null ? ['Scale', (s.scalePpm >= 0 ? '+' : '') + s.scalePpm.toFixed(0) + ' ppm'] : null]) : ''}
        ${res}
        ${s.warnings.map(w => `<div class="hint warn">${esc(w)}</div>`).join('')}
        <div class="row">${adjustBtn}<button class="btn small" data-act="fit">Zoom to parcels</button></div></div>
      <div class="card"><div class="ttl">Which monuments?</div>
        <div class="hint">Best first pair: the <b>shared corner</b> (TPB, a #4 rebar) and the <b>road corner</b> (LPNE) — both are on both documents and 1,776 ft apart.
        Then add the Lot 1 SW aluminum cap or the S¼ corner brass plate as a check: with three or more, the app shows which one disagrees.</div>
        <div class="hint">Measure only with RTK FIX, pole plumb on the monument's center. Satellite photos can be off by 3–10 ft, so trust measured monuments over the picture.</div></div>
      <div class="card"><label class="check"><input type="checkbox" data-chg="setting" data-key="hold" ${set.hold ? 'checked' : ''}>
          <span>Hold measured monuments<br><span class="hint">Bend the outline slightly so it passes exactly through each one — the way surveyors treat found monuments.</span></span></label>
        <label class="check"><input type="checkbox" data-chg="setting" data-key="fitScale" ${set.fitScale ? 'checked' : ''}>
          <span>Allow a scale change in the fit<br><span class="hint">Only for checking the documents; leave off for normal use.</span></span></label></div>
      ${GROUPS.map(gr => `<div class="sec"><h3>${esc(gr[1])}</h3>${gr[2].map(monCard).join('')}</div>`).join('')}
      <div class="sec"><h3>Points along the road</h3>
        ${roads('ROW', 'Lodgepole Pines frontage (rebar per plat note 2)')}
        ${roads('P1R', '35-acre frontage (from the deed)')}</div>
      <div class="sec"><h3>Start over</h3><div class="row">
        ${A.proj.rough ? '<button class="btn small danger" data-act="clearrough">Clear hand placement</button>' : ''}
        ${Object.keys(A.proj.control).length ? '<button class="btn small danger" data-act="clearctl">Remove all measurements</button>' : ''}</div></div>`;
  };

  // ================================================================== POINTS
  panes.points = function (el) {
    const pts = A.proj.savedPoints.slice().sort((a, b) => (b.t || 0) - (a.t || 0));
    const card = p => `<div class="card">
        <div class="ttl">${esc(p.note || '(no note)')}</div>
        <div class="hint">${esc(A.fmtDate(p.t))}${p.sigma ? ' · ' + A.fmtAcc(p.sigma) : ''}${p.fix ? ' · ' + esc(A.fixName(p.fix)) : ''}${p.n > 1 ? ' · averaged ' + p.n : ''}${p.legacy ? ' · from the old version' : ''}${p.src === 'map' ? ' · picked on the map' : ''}</div>
        ${kv([A.gm ? ['Where', esc(A.whereText(p.lat, p.lon))] : null, ['From you', distSpan(p.lat, p.lon)],
              ['Lat, lon', `<span class="mono">${A.fmtLL(p.lat, p.lon)}</span>`], p.alt != null ? ['Elevation', A.fmtElev(p.alt)] : null])}
        <div class="row">
          <button class="btn small primary" data-act="nav" data-kind="saved" data-id="${esc(p.id)}">Go</button>
          <button class="btn small" data-act="ptshow" data-id="${esc(p.id)}">Show</button>
          <button class="btn small" data-act="ptedit" data-id="${esc(p.id)}">Edit</button>
          <button class="btn small" data-act="copyll" data-lat="${p.lat.toFixed(8)}" data-lon="${p.lon.toFixed(8)}">Copy</button>
          <button class="btn small danger" data-act="ptdel" data-id="${esc(p.id)}">Delete</button></div></div>`;
    el.innerHTML = `<div class="row" style="margin:0 0 10px"><b class="grow">Saved points (${pts.length})</b>
        <button class="btn small primary" data-act="save">📍 Save my spot</button></div>
      ${pts.length ? pts.map(card).join('') : '<div class="hint">Nothing saved yet. Tap 📍 Save at the top while standing somewhere — a fence corner, a stake, a well — and add a note. Or tap the map and choose “Save as point”.</div>'}
      <div class="sec" style="margin-top:16px"><h3>Export</h3>
        <div class="row"><button class="btn small" data-act="export" data-fmt="csv">CSV (spreadsheet)</button>
          <button class="btn small" data-act="export" data-fmt="kml">KML (Google Earth)</button>
          <button class="btn small" data-act="export" data-fmt="geojson">GeoJSON (GIS)</button></div>
        <div class="hint">Includes your saved points, the monuments and the parcel outlines. Coordinates are WGS84 as delivered by your correction service.</div></div>`;
  };

  // ================================================================== TOOLS
  function stakeOptions() {
    const groups = { boundary: [], internal: [], neighbor: [] };
    Object.keys(model.segments).forEach(id => {
      const s = model.segments[id];
      groups[s.role].push(`<option value="${id}" ${A.stake && A.stake.segId === id ? 'selected' : ''}>${esc(A.segLabel(id))}</option>`);
    });
    return `<optgroup label="Your property lines">${groups.boundary.join('')}</optgroup>
      <optgroup label="Between your two parcels">${groups.internal.join('')}</optgroup>
      <optgroup label="Neighbor lines">${groups.neighbor.join('')}</optgroup>`;
  }
  A.segLabel = function (id) {
    const s = model.segments[id];
    if (/^(p1b|lpr)\d/.test(id) && s.name.indexOf('Shadow Mountain') === 0) {
      const which = id.indexOf('lpr') === 0 ? 'Lodgepole' : '35-ac';
      return `Road (${which}): ${s.call}`;
    }
    return s.name;
  };
  panes.tools = function (el) {
    const set = A.proj.settings, alerts = [0, 3, 5, 10, 25, 50];
    el.innerHTML = `
      <div class="sec"><h3>Stake out a line</h3><div class="card">
        <select class="inp" id="tStake">${stakeOptions()}</select>
        <div class="row"><button class="btn small primary" data-act="stakesel">${A.stake ? 'Switch to this line' : 'Start'}</button>
          ${A.stake ? '<button class="btn small" data-act="stakestop">Stop</button>' : ''}</div>
        <div class="hint">Shows how far left or right of the line you are and how far along it — for setting fence posts or flagging the line. Tip: tap a line on the map to pick it.</div></div></div>
      <div class="sec"><h3>Property-line alert</h3><div class="card">
        <select class="inp" data-chg="setting" data-key="edgeAlertFt" data-num="1">${alerts.map(a => `<option value="${a}" ${+set.edgeAlertFt === a ? 'selected' : ''}>${a ? 'Within ' + a + ' ft of my line' : 'Off'}</option>`).join('')}</select>
        <div class="row"><button class="btn small" data-act="testalert">Test sound</button></div>
        <div class="hint">Beeps (and vibrates on phones) when you get this close to your property line, and twice when you cross it.</div></div></div>
      <div class="sec"><h3>Your track</h3><div class="card">
        <label class="check"><input type="checkbox" data-chg="ui" data-key="track" ${A.ui.track ? 'checked' : ''}> Show where I've walked</label>
        <div class="row"><button class="btn small" data-act="clrtrack">Clear track</button></div></div></div>
      <div class="sec"><h3>Check any spot</h3><div class="card"><div class="hint" style="margin:0">Tap anywhere on the map (long-press on a phone) to see whether it's inside your land and how far it is from the line.</div></div></div>
      <div class="sec"><h3>Screen</h3><div class="card">
        <label class="check"><input type="checkbox" data-chg="ui" data-key="wake" ${A.ui.wake ? 'checked' : ''}> Keep the screen on while position data is coming in</label>
        ${A.wakeSupported() ? '' : '<div class="hint">Not available in this browser or over this connection.</div>'}</div></div>
      <div class="sec"><h3>Demo</h3><div class="card">
        <button class="btn small" data-act="${A.demo ? 'demostop' : 'demo'}">${A.demo ? 'Stop demo' : 'Start demo walk'}</button>
        <div class="hint">Pretends to walk the 35-acre line, weaving in and out, so you can see how the app responds. Runs only when no receiver is sending data.</div></div></div>
      ${A.serialSupported ? `<div class="sec"><h3>Receiver without the bridge</h3><div class="card">
        <button class="btn small" data-act="serial">${A.serial ? 'Disconnect receiver' : 'Connect a receiver directly…'}</button>
        <div class="hint">For a receiver on USB when the bridge isn't running. No RTK corrections this way — the bridge is the better option.</div></div></div>` : ''}`;
  };

  // ================================================================== SETTINGS
  let cfgDirty = false;
  A.ports = null;
  A.setCfgDirty = function (on) {
    cfgDirty = on;
    const d = document.getElementById('cfDirty');
    if (d) d.textContent = on ? 'Unsaved changes' : '';
  };
  const PRESETS = {
    pointperfect: { label: 'u-blox PointPerfect (NEAR-RTCM)', host: 'ppntrip.services.u-blox.com', port: 2101, mount: 'NEAR-RTCM',
                    hint: 'Paid service, no base station needed. Use the <b>NEAR-RTCM</b> mountpoint — NEAR-SPARTN doesn\'t work over Bluetooth. Username and password are in your Thingstream account (PointPerfect → Location thing → Credentials).' },
    rtk2go: { label: 'RTK2go (free community bases)', host: 'rtk2go.com', port: 2101, mount: '',
              hint: 'Free. Needs a base within ~15 km (35 km at most) — use “Find bases near me”. Username: your email address. Password: none.' },
    custom: { label: 'Other caster', hint: 'Any NTRIP caster that sends RTCM 3.' }
  };
  function presetOf(host) {
    for (const k in PRESETS) if (PRESETS[k].host && PRESETS[k].host === host) return k;
    return host ? 'custom' : 'pointperfect';
  }
  function displayCard() {
    const set = A.proj.settings;
    return `<div class="sec"><h3>Display</h3><div class="card">
      <label class="f"><span>Units</span><select class="inp" data-chg="setting" data-key="units">
        <option value="ft" ${set.units !== 'm' ? 'selected' : ''}>Feet (US survey)</option><option value="m" ${set.units === 'm' ? 'selected' : ''}>Metres</option></select></label>
      <label class="f"><span>Map</span><select class="inp" data-chg="setting" data-key="base">
        ${A.BASE_ORDER.map(k => `<option value="${k}" ${set.base === k ? 'selected' : ''}>${k === 'none' ? 'No map (fastest)' : esc(A.BASES[k].name)}</option>`).join('')}</select></label>
      <div class="hint">Shared by everyone using this project.</div></div></div>`;
  }
  function portOptions(cur) {
    const kinds = { 'bt-out': 'Bluetooth — outgoing (use this)', 'bt-in': 'Bluetooth — incoming (not this)', usb: 'USB', other: '' };
    const list = (A.ports || []).slice();
    if (cur && cur !== 'auto' && !list.some(p => p.device === cur)) list.unshift({ device: cur, description: 'saved port', kind: 'other' });
    if (!list.length) return '<option value="">(press Refresh)</option>';
    return list.map(p => `<option value="${esc(p.device)}" ${p.device === cur ? 'selected' : ''}>${esc(p.device)} — ${esc(p.description || '')}${kinds[p.kind] ? ' · ' + kinds[p.kind] : ''}</option>`).join('');
  }
  panes.settings = function (el) {
    const b = A.bridge, cfg = b.cfg;
    let html = displayCard();
    if (!A.bridgeMode) {
      el.innerHTML = html + '<div class="hint">Receiver and correction settings belong to the bridge on the laptop. Opened as a file, there is none — start it with <span class="mono">python rtk_bridge.py</span>.</div>';
      return;
    }
    if (!cfg || !b.connected) { el.innerHTML = html + '<div class="hint">Connecting to the laptop…</div>'; return; }
    if (!A.isLocal()) {
      const n = cfg.ntrip;
      el.innerHTML = html + `<div class="sec"><h3>Laptop settings</h3><div class="card">${kv([
        ['Receiver', esc(cfg.source.type === 'serial' ? (cfg.source.port === 'auto' ? 'automatic' : cfg.source.port) : cfg.source.type)],
        ['Corrections', esc(n.enabled ? n.host + ' / ' + n.mount : 'off')]])}
        <div class="hint">These can only be changed on the laptop running Parcel RTK.</div></div></div>`;
      return;
    }
    const s = cfg.source, n = cfg.ntrip, pre = presetOf(n.host);
    const srcSel = s.type === 'serial' ? (s.port && s.port !== 'auto' ? 'port' : 'auto') : s.type;
    html += `<div class="sec"><h3>Receiver connection</h3><div class="card">
        <label class="f"><span>Connect by</span><select class="inp" id="cfSrc">
          <option value="auto" ${srcSel === 'auto' ? 'selected' : ''}>Bluetooth or USB — find it automatically</option>
          <option value="port" ${srcSel === 'port' ? 'selected' : ''}>A specific COM port</option>
          <option value="tcp" ${srcSel === 'tcp' ? 'selected' : ''}>Network (TCP)</option>
          <option value="test" ${srcSel === 'test' ? 'selected' : ''}>Simulated receiver (testing)</option></select></label>
        <div id="cfPortBox" ${srcSel === 'port' ? '' : 'hidden'}><label class="f"><span>COM port</span><select class="inp" id="cfPort">${portOptions(s.port)}</select></label>
          <div class="row"><button class="btn small" data-act="listports">Refresh list</button></div></div>
        <div id="cfTcpBox" ${srcSel === 'tcp' ? '' : 'hidden'}><div class="row">
          <label class="f grow"><span>Host</span><input class="inp" id="cfTcpHost" value="${esc(s.host || '')}" spellcheck="false"></label>
          <label class="f" style="width:110px"><span>Port</span><input class="inp" id="cfTcpPort" inputmode="numeric" value="${esc(s.tcpPort || '')}"></label></div></div>
        <label class="f" id="cfBaudBox" ${srcSel === 'tcp' || srcSel === 'test' ? 'hidden' : ''}><span>Baud rate (USB only; Bluetooth ignores it)</span><select class="inp" id="cfBaud">
          ${[9600, 38400, 57600, 115200, 230400, 460800].map(v => `<option ${+s.baud === v ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
        <div class="hint">Bluetooth: pair the receiver in Windows first (receiver Bluetooth mode: Classic). Windows makes two COM ports; the bridge finds the outgoing one by itself.</div></div></div>
      <div class="sec"><h3>Corrections (NTRIP)</h3><div class="card">
        <label class="check"><input type="checkbox" id="cfNtripOn" ${n.enabled ? 'checked' : ''}> Get RTK corrections over the internet</label>
        <label class="f"><span>Service</span><select class="inp" id="cfPreset">
          ${Object.keys(PRESETS).map(k => `<option value="${k}" ${k === pre ? 'selected' : ''}>${esc(PRESETS[k].label)}</option>`).join('')}</select></label>
        <div class="hint" id="cfPresetHint">${PRESETS[pre].hint}</div>
        <label class="f"><span>Caster</span><input class="inp" id="cfHost" value="${esc(n.host || '')}" spellcheck="false" autocomplete="off"></label>
        <div class="row"><label class="f" style="width:110px"><span>Port</span><input class="inp" id="cfNPort" inputmode="numeric" value="${esc(n.port || 2101)}"></label>
          <label class="f grow"><span>Mountpoint</span><input class="inp" id="cfMount" value="${esc(n.mount || '')}" spellcheck="false" autocomplete="off"></label></div>
        <label class="f"><span>Username</span><input class="inp" id="cfUser" value="${esc(n.user || '')}" spellcheck="false" autocomplete="off"></label>
        <label class="f"><span>Password</span><input class="inp" id="cfPass" type="password" autocomplete="new-password"
          placeholder="${n.passSet ? 'saved on the laptop — leave blank to keep it' : ''}"></label>
        ${n.passSet ? '<label class="check"><input type="checkbox" id="cfClearPass"> Forget the saved password</label>' : ''}
        <label class="f"><span>Send my position to the caster every … seconds</span><input class="inp" id="cfGga" inputmode="numeric" value="${esc(n.ggaInterval || 10)}"></label>
        <div class="row"><button class="btn small" data-act="findbases">Find bases near me</button></div>
        <div id="cfBases"></div></div></div>
      <div class="sec"><h3>Phone access</h3><div class="card">
        <label class="check"><input type="checkbox" id="cfLan" ${cfg.web.lan ? 'checked' : ''}> Let phones and tablets on this Wi-Fi open the app</label>
        <div class="hint">Takes effect the next time Parcel RTK starts. The QR code is on the Status tab.</div></div></div>
      <div class="sec"><h3>Updates</h3><div class="card">
        ${kv([['Installed', esc(b.hello ? b.hello.version : A.VERSION)]])}
        <div class="row"><button class="btn small" data-act="chkupdate">Check for updates</button></div><div id="cfUpd"></div>
        <details style="margin-top:8px"><summary class="hint">Advanced</summary>
          <label class="f"><span>Update from branch</span><input class="inp" id="cfBranch" value="${esc(cfg.update.branch || '')}" spellcheck="false"></label>
          <label class="f"><span>Web port (restart needed)</span><input class="inp" id="cfWebPort" inputmode="numeric" value="${esc(cfg.web.port)}"></label>
          <label class="check"><input type="checkbox" id="cfLog" ${cfg.logNmea ? 'checked' : ''}> Record raw receiver data to rtk_data/logs</label></details></div></div>
      <div class="savebar"><button class="btn primary" data-act="cfgsave">Save settings</button>
        <button class="btn ghost" data-act="cfgdiscard">Discard</button><span class="hint" id="cfDirty"></span></div>`;
    el.innerHTML = html;
    A.setCfgDirty(false);
    if (A.ports === null) A.listPorts(true);
  };
  A.onSettingsInput = function (e) {
    const t = e.target;
    if (!t || !t.id || t.id.indexOf('cf') !== 0) return;
    A.setCfgDirty(true);
    if (t.id === 'cfSrc') {
      A.$('#cfPortBox').hidden = t.value !== 'port';
      A.$('#cfTcpBox').hidden = t.value !== 'tcp';
      A.$('#cfBaudBox').hidden = t.value === 'tcp' || t.value === 'test';
      if (t.value === 'port' && !A.ports) A.listPorts();
    }
    if (t.id === 'cfPreset' && e.type === 'change') {
      const p = PRESETS[t.value];
      A.$('#cfPresetHint').innerHTML = p.hint;
      if (p.host) { A.$('#cfHost').value = p.host; A.$('#cfNPort').value = p.port; A.$('#cfMount').value = p.mount; }
      if (t.value === 'rtk2go' && !A.$('#cfPass').value) A.$('#cfPass').value = 'none';
    }
  };
  A.listPorts = function (quiet) {
    A.send({ t: 'listPorts' }, m => {
      if (m.t !== 'ports') { if (!quiet) A.toast(m.msg || 'Could not list ports', true); return; }
      A.ports = m.ports || [];
      if (m.ports === null && !quiet) A.toast('pyserial is not installed on the laptop (pip install pyserial)', true);
      const sel = document.getElementById('cfPort');
      if (sel) { const cur = sel.value; sel.innerHTML = portOptions(cur || (A.bridge.cfg && A.bridge.cfg.source.port)); }
    });
  };
  A.saveSettings = function () {
    const v = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
    const chk = id => { const el = document.getElementById(id); return !!(el && el.checked); };
    const src = v('cfSrc'), num = (x, d) => { const n = parseInt(x, 10); return isFinite(n) && n > 0 ? n : d; };
    const cfg = {
      source: src === 'tcp' ? { type: 'tcp', host: v('cfTcpHost'), tcpPort: num(v('cfTcpPort'), 0) }
        : src === 'test' ? { type: 'test' }
        : { type: 'serial', port: src === 'port' ? (v('cfPort') || 'auto') : 'auto', baud: num(v('cfBaud'), 115200) },
      ntrip: { enabled: chk('cfNtripOn'), host: v('cfHost').replace(/^(ntrip|https?):\/\//i, '').replace(/\/.*$/, ''),
               port: num(v('cfNPort'), 2101), mount: v('cfMount').replace(/^\//, ''), user: v('cfUser'),
               pass: (document.getElementById('cfPass') || {}).value || '', clearPass: chk('cfClearPass'), ggaInterval: num(v('cfGga'), 10) },
      web: { lan: chk('cfLan'), port: num(v('cfWebPort'), 8000) },
      update: { branch: v('cfBranch') || (A.bridge.cfg && A.bridge.cfg.update.branch) },
      logNmea: chk('cfLog')
    };
    if (cfg.ntrip.enabled && (!cfg.ntrip.host || !cfg.ntrip.mount)) { A.toast('Corrections need a caster and a mountpoint', true); return; }
    A.send({ t: 'setConfig', cfg: cfg }, m => {
      if (m.t === 'ack') { A.setCfgDirty(false); A.toast('Saved. The laptop is reconnecting with the new settings.'); A.renderPane('settings', 'force'); }
      else A.toast('Not saved: ' + (m.msg || 'unknown error'), true);
    });
  };
  A.findBases = function () {
    const pos = A.live.pos || A.mapCenter();
    const host = v0('cfHost') || 'rtk2go.com', port = v0('cfNPort') || '2101';
    const caster = host + ':' + port, out = document.getElementById('cfBases');
    if (out) out.innerHTML = '<div class="hint">Asking ' + esc(caster) + ' for its list…</div>';
    A.send({ t: 'findBases', lat: pos.lat, lon: pos.lon, caster: caster }, m => {
      const el = document.getElementById('cfBases');
      if (!el) return;
      if (m.t !== 'bases') { el.innerHTML = `<div class="hint bad">${esc(m.msg || 'failed')}</div>`; return; }
      const rows = (m.rows || []).slice(0, 12);
      el.innerHTML = rows.length ? `<div class="kv" style="grid-template-columns:1fr auto auto">${rows.map(r =>
        `<span>${esc(r.mount)} <span class="dim">${esc((r.format || '').slice(0, 16))}</span></span>
         <span class="${r.km <= 15 ? 'good-t' : r.km <= 35 ? 'warn' : 'dim'}">${r.km} km</span>
         <span><button class="btn small" data-act="usebase" data-mount="${esc(r.mount)}">Use</button></span>`).join('')}</div>
         <div class="hint">Within 15 km is great; up to 35 km can work. Network services (like PointPerfect) don't list distances.</div>`
        : '<div class="hint">No mountpoints listed.</div>';
    });
    function v0(id) { const el = document.getElementById(id); return el ? el.value.trim() : ''; }
  };
  A.checkUpdate = function () {
    const out = () => document.getElementById('cfUpd');
    if (out()) out().innerHTML = '<div class="hint">Checking…</div>';
    A.send({ t: 'checkUpdate' }, m => {
      if (!out()) return;
      if (m.t !== 'update') { out().innerHTML = `<div class="hint bad">${esc(m.msg || 'failed')}</div>`; return; }
      if (m.error) out().innerHTML = `<div class="hint bad">Couldn't check: ${esc(m.error)}</div>`;
      else if (m.available) out().innerHTML = `<div class="hint">Version <b>${esc(m.latest)}</b> is available.</div>
        <div class="row"><button class="btn small primary" data-act="doupdate">Install update</button></div>
        <div class="hint">Your settings, saved points and calibration are kept.</div>`;
      else out().innerHTML = `<div class="hint good-t">You have the latest version (${esc(m.current)}).</div>`;
    });
  };
  A.doUpdate = function () {
    const out = document.getElementById('cfUpd');
    if (out) out.innerHTML = '<div class="hint">Downloading…</div>';
    A.send({ t: 'doUpdate' }, m => {
      const el = document.getElementById('cfUpd');
      if (!el) return;
      if (m.t === 'updated') el.innerHTML = `<div class="hint good-t">Updated ${m.changed.length + m.added.length} files. Close the Parcel RTK window on the laptop and start it again, then reload this page.</div>`;
      else el.innerHTML = `<div class="hint bad">${esc(m.msg || 'Update failed')}</div>`;
    });
  };

  // ================================================================== MEASURE A MONUMENT
  A.measure = null;
  A.openMeasure = function (id) {
    const p = model.points[id];
    if (!p) return;
    A.closePopup();
    A.measure = { id: id, samples: [], any: false, result: null, t0: Date.now(), waitQ: null };
    A.modal.open(`<h2>Measure: ${esc(A.isMain(id) ? A.SHORT[id] : p.name)}</h2>
      <div class="hint">${esc(p.name)}</div>
      <div class="hint"><b>Look for:</b> ${esc(p.monument || '')}</div>
      <div class="hint">Put the pole tip on the center of the monument, level the bubble, and hold still.</div>
      <div class="big-num" id="msN">0</div>
      <div class="meter"><div id="msBar"></div></div>
      <div id="msInfo" class="hint" style="text-align:center"></div>
      <div id="msResult"></div>
      <label class="check"><input type="checkbox" id="msAny"> Accept positions without RTK FIX (much less accurate)</label>
      <div class="row" style="justify-content:flex-end;margin-top:14px" id="msBtns"></div>`,
      { kind: 'measure', onclose: () => { A.measure = null; } });
    A.$('#msAny').onchange = e => { if (A.measure) { A.measure.any = e.target.checked; A.renderMeasure(); } };
    A.renderMeasure();
  };
  function measureStats(S) {
    const f = geo.frame({ lat: S[0].lat, lon: S[0].lon, h: S[0].h != null ? S[0].h : geo.DEFAULT_H });
    const pts = S.map(s => { const q = f.toEnu(s.lat, s.lon, s.h != null ? s.h : f.origin.h); return { x: q.x, y: q.y, s: s }; });
    const med = a => { const b = a.slice().sort((x, y) => x - y), n = b.length; return n % 2 ? b[(n - 1) / 2] : (b[n / 2 - 1] + b[n / 2]) / 2; };
    const mx = med(pts.map(p => p.x)), my = med(pts.map(p => p.y));
    const d = pts.map(p => Math.hypot(p.x - mx, p.y - my)), lim = Math.max(0.03, 3 * med(d));
    const keep = pts.filter((p, i) => d[i] <= lim);
    let sx = 0, sy = 0, sh = 0, nh = 0, sa = 0, na = 0, ss = 0, qmin = 9;
    keep.forEach(p => {
      sx += p.x; sy += p.y; ss += p.s.sigma || 0; qmin = Math.min(qmin, p.s.q === 4 ? 4 : p.s.q === 5 ? 5 : p.s.q);
      if (p.s.h != null) { sh += p.s.h; nh++; }
      if (p.s.alt != null) { sa += p.s.alt; na++; }
    });
    const n = keep.length, x = sx / n, y = sy / n;
    const spread = Math.sqrt(keep.reduce((a, p) => a + (p.x - x) * (p.x - x) + (p.y - y) * (p.y - y), 0) / n);
    const g = f.toGeo(x, y, 0), sig = ss / n;
    const allFix = keep.every(p => p.s.q === 4);
    return { n: n, rejected: S.length - n, lat: g.lat, lon: g.lon, h: nh ? sh / nh : null, alt: na ? sa / na : null,
             spread: spread, sigma: Math.sqrt(sig * sig + 0.01 * 0.01 + spread * spread / n), fix: allFix ? 4 : qmin };
  }
  // called for every new position while the dialog is open
  A.measureSample = function () {
    const M = A.measure, L = A.live, g = L.gga;
    if (!M || M.result || !g) return;
    if (!g.valid || (g.q !== 4 && !M.any)) { M.waitQ = g.valid ? g.q : 0; A.renderMeasure(); return; }
    M.waitQ = null;
    M.samples.push({ lat: g.lat, lon: g.lon, h: g.h, alt: g.alt, q: g.q, sigma: L.sigma, t: Date.now() });
    M.stats = measureStats(M.samples);
    const el = (Date.now() - M.samples[0].t) / 1000;
    if ((M.stats.n >= 10 && el >= 10 && M.stats.spread <= 0.03) || (el >= 60 && M.stats.n >= 5)) finishMeasure();
    A.renderMeasure();
  };
  function finishMeasure() {
    const M = A.measure, s = M.stats;
    M.result = { lat: s.lat, lon: s.lon, h: s.h, alt: s.alt, sigma: s.sigma, n: s.n, spread: s.spread, fix: s.fix,
                 t: Date.now(), use: true, corr: A.corrSource() };
    M.preview = measurePreview(M.id, M.result);
  }
  function measurePreview(id, val) {
    const control = Object.assign({}, A.proj.control);
    control[id] = val;
    const sol = calib.solve(model, Object.assign({}, A.proj, { control: control }));
    const out = [];
    let level = 'ok';
    if (sol.kind === 'fit') {
      if (sol.distCheck) {
        const d = sol.distCheck, other = d.a === id ? d.b : d.a, ad = Math.abs(d.diffFt);
        level = ad <= 0.3 ? 'ok' : ad <= 1 ? 'warn' : 'bad';
        out.push(`Distance to the ${esc(A.shortName(other))}: measured <b>${d.measFt.toFixed(2)} ft</b>, documents ${d.platFt.toFixed(2)} ft
          (<span class="res ${level}">${d.diffFt >= 0 ? '+' : ''}${d.diffFt.toFixed(2)} ft</span>).`);
      } else {
        const r = sol.residuals.find(q => q.id === id);
        level = r.level;
        out.push(`Fits the other ${sol.n - 1} monuments within <span class="res ${level}">${A.fmtLen(r.r)}</span>.`);
      }
      out.push(level === 'ok' ? '✓ This is the right monument and it hasn\'t moved.' : level === 'warn'
        ? 'Close, but not survey-tight: check the pole is plumb on the monument\'s center, then measure again.'
        : 'This doesn\'t fit. Wrong monument, a disturbed one, or one the documents don\'t mean? Look around for another pin.');
    } else if (sol.kind === 'one' && A.gm && A.gm.pts[id]) {
      const d = geo.distBearing(A.gm.pts[id], val).dist;
      out.push(`The outline moves <b>${A.fmtLen(d)}</b> to sit on this monument. Measure a second one to fix its rotation.`);
    }
    if (val.spread > 0.05) {
      out.push(`<span class="${val.spread > 0.3 ? 'bad' : 'warn'}">The position wandered ${A.fmtLen(val.spread, 2)} while measuring — hold the pole still and measure again.</span>`);
      level = val.spread > 0.3 ? 'bad' : level === 'ok' ? 'warn' : level;
    }
    if (val.fix !== 4) { out.push('Measured without RTK FIX — only good to a few feet.'); if (level === 'ok') level = 'warn'; }
    return { html: out.map(t => `<div class="hint">${t}</div>`).join(''), level: level };
  }
  A.renderMeasure = function () {
    const M = A.measure;
    if (!M || !document.getElementById('msN')) return;
    const s = M.stats, n = s ? s.n : 0, el = M.samples.length ? (Date.now() - M.samples[0].t) / 1000 : 0;
    A.$('#msN').textContent = n;
    A.$('#msBar').style.width = Math.min(100, M.result ? 100 : Math.max(n / 10, el / 10) * 100) + '%';
    let info;
    if (M.result) info = `Done: ${n} positions, spread ${A.fmtLen(s.spread, 3)}, accuracy ${A.fmtAcc(M.result.sigma)}`;
    else if (M.waitQ != null) info = `<span class="warn">Waiting for RTK FIX — now ${esc(M.waitQ ? A.fixName(M.waitQ) : 'no fix')}</span>`;
    else if (!A.hasFix()) info = '<span class="warn">No position data</span>';
    else info = n ? `${Math.round(el)} s · spread ${A.fmtLen(s.spread, 3)} · finishes on its own when steady` : 'Collecting…';
    A.$('#msInfo').innerHTML = info;
    A.$('#msResult').innerHTML = M.result ? M.preview.html : '';
    const btns = A.$('#msBtns');
    const want = M.result ? 'done' : n >= 5 ? 'early' : 'wait';
    if (btns.dataset.state !== want) {
      btns.dataset.state = want;
      btns.innerHTML = want === 'done'
        ? `<button class="btn ghost" data-act="mclose">Cancel</button><button class="btn" data-act="mredo">Measure again</button>
           <button class="btn ${M.preview.level === 'bad' ? 'danger' : 'good'}" data-act="msave">Save</button>`
        : `<button class="btn ghost" data-act="mclose">Cancel</button>${want === 'early' ? '<button class="btn" data-act="mstop">Finish now</button>' : ''}`;
    }
  };
  A.measureAction = function (act) {
    const M = A.measure;
    if (!M) return;
    if (act === 'mstop' && M.stats) { finishMeasure(); A.renderMeasure(); }
    else if (act === 'mredo') { A.measure = Object.assign(M, { samples: [], stats: null, result: null, preview: null }); A.renderMeasure(); }
    else if (act === 'msave' && M.result) {
      const id = M.id, val = M.result, before = A.sol.kind;
      if (A.edit({ op: 'set', path: ['control', id], value: val })) {
        A.modal.close();
        A.toast(A.shortName(id) + ' saved' + (before !== 'fit' && A.sol.kind === 'fit' ? ' — the outline is now locked to your monuments' : ''));
        if (A.nav && A.nav.kind === 'pt' && A.nav.id === id) A.stopNav();
      }
    }
  };

  // ================================================================== SAVE A POINT
  A.openSave = function (spot) {
    if (!spot && !A.hasFix()) { A.toast('No position yet — wait for the receiver', true); return; }
    A.closePopup();
    const pos = spot || A.live.pos;
    A.saving = { spot: spot || null, samples: [], t0: 0, active: false };
    A.modal.open(`<h2>${spot ? 'Save this map spot' : 'Save this spot'}</h2>
      <div class="mono" id="svLL">${A.fmtLL(pos.lat, pos.lon)}</div>
      <div class="hint" id="svAcc">${spot ? 'Picked on the map — only as accurate as the photo.' : esc(A.fixName(pos.q)) + ' · ' + A.fmtAcc(A.live.sigma)}</div>
      ${spot ? '' : `<div class="hint">${esc(A.whereText(pos.lat, pos.lon))}</div>`}
      <label class="f"><span>Note</span><textarea class="inp" id="svNote" rows="3" autofocus placeholder="e.g. NE fence corner, survey stake, well head"></textarea></label>
      ${spot ? '' : '<label class="check"><input type="checkbox" id="svAvg"> Average for 10 seconds first (more accurate — hold still)</label>'}
      <div class="meter" id="svMeter" hidden><div id="svBar"></div></div>
      <div class="row" style="justify-content:flex-end;margin-top:14px">
        <button class="btn ghost" data-act="mclose">Cancel</button><button class="btn primary" data-act="svsave" id="svBtn">Save</button></div>`,
      { kind: 'save', onclose: () => { A.saving = null; } });
  };
  A.saveSample = function () {
    const S = A.saving, g = A.live.gga;
    if (!S || !S.active || !g || !g.valid) return;
    S.samples.push({ lat: g.lat, lon: g.lon, h: g.h, alt: g.alt, q: g.q, sigma: A.live.sigma, t: Date.now() });
    const f = Math.min(1, (Date.now() - S.t0) / 10000);
    const bar = document.getElementById('svBar');
    if (bar) bar.style.width = (f * 100) + '%';
    if (f >= 1) { S.active = false; commitSave(measureStats(S.samples)); }
  };
  A.saveAction = function () {
    const S = A.saving;
    if (!S || S.active) return;
    const avg = document.getElementById('svAvg');
    if (avg && avg.checked) {
      S.active = true; S.t0 = Date.now(); S.samples = [];
      A.$('#svMeter').hidden = false;
      A.$('#svBtn').disabled = true;
      A.$('#svBtn').textContent = 'Averaging…';
      return;
    }
    if (S.spot) commitSave({ lat: S.spot.lat, lon: S.spot.lon, h: null, alt: null, sigma: null, n: 1, fix: null }, 'map');
    else {
      const p = A.live.pos;
      if (!p || !A.hasFix()) { A.toast('Lost the position — try again', true); return; }
      commitSave({ lat: p.lat, lon: p.lon, h: p.h, alt: p.alt, sigma: A.live.sigma, n: 1, fix: p.q });
    }
  };
  function commitSave(s, src) {
    const note = (document.getElementById('svNote') || {}).value || '';
    const pt = { id: A.newId(), lat: +s.lat.toFixed(9), lon: +s.lon.toFixed(9), h: s.h, alt: s.alt, sigma: s.sigma,
                 fix: s.fix, n: s.n, t: Date.now(), note: note.trim(), src: src || 'rx' };
    if (A.edit({ op: 'push', path: ['savedPoints'], value: pt })) {
      A.modal.close();
      A.toast('Saved' + (pt.note ? ': ' + pt.note : ''));
    }
  }
  A.openEditPoint = function (id) {
    const p = A.proj.savedPoints.find(x => x.id === id);
    if (!p) return;
    A.closePopup();
    A.modal.open(`<h2>Edit point</h2><div class="mono">${A.fmtLL(p.lat, p.lon)}</div><div class="hint">${esc(A.fmtDate(p.t))}</div>
      <label class="f"><span>Note</span><textarea class="inp" id="edNote" rows="3" autofocus>${esc(p.note || '')}</textarea></label>
      <div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn danger" data-act="ptdel" data-id="${esc(id)}">Delete</button>
        <span class="grow"></span><button class="btn ghost" data-act="mclose">Cancel</button>
        <button class="btn primary" data-act="ptnote" data-id="${esc(id)}">Save</button></div>`, { kind: 'edit' });
  };

  // ================================================================== EXPORT
  A.exportData = function (fmt) {
    const stamp = new Date().toISOString().slice(0, 10), gm = A.gm, pts = A.proj.savedPoints;
    const mons = gm ? P.model.controlList(model).map(p => {
      const c = A.proj.control[p.id], useC = c && c.use !== false && isFinite(c.lat);
      return { id: p.id, name: p.name, monument: p.monument || '', lat: useC ? c.lat : gm.pts[p.id].lat, lon: useC ? c.lon : gm.pts[p.id].lon,
               measured: !!useC, sigma: useC ? c.sigma : null };
    }) : [];
    if (fmt === 'csv') {
      const q = v => { const s = v == null ? '' : String(v); return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
      const rows = [['type', 'name', 'note', 'latitude', 'longitude', 'ellipsoid_height_m', 'elevation_m', 'accuracy_m', 'fix', 'time', 'where']];
      pts.forEach(p => rows.push(['saved point', '', p.note, p.lat.toFixed(9), p.lon.toFixed(9), p.h != null ? p.h.toFixed(3) : '', p.alt != null ? p.alt.toFixed(3) : '',
        p.sigma != null ? p.sigma.toFixed(3) : '', p.fix != null ? A.fixName(p.fix) : (p.src === 'map' ? 'map' : ''), p.t ? new Date(p.t).toISOString() : '', A.whereText(p.lat, p.lon)]));
      mons.forEach(m => rows.push([m.measured ? 'monument (measured)' : 'monument (from documents)', m.name, m.monument, m.lat.toFixed(9), m.lon.toFixed(9), '', '',
        m.sigma != null ? m.sigma.toFixed(3) : '', '', '', '']));
      A.download('parcel-points-' + stamp + '.csv', rows.map(r => r.map(q).join(',')).join('\r\n') + '\r\n', 'text/csv');
      return;
    }
    if (fmt === 'geojson') {
      const F = [];
      if (gm) {
        model.order.forEach(rid => {
          const r = model.rings[rid], ring = gm.rings[rid].ll.map(ll => [+ll[1].toFixed(9), +ll[0].toFixed(9)]);
          ring.push(ring[0]);
          F.push({ type: 'Feature', properties: { name: r.name, owned: r.owned, area_ac: +r.areaAc.toFixed(3) }, geometry: { type: 'Polygon', coordinates: [ring.reverse()] } });
        });
        mons.forEach(m => F.push({ type: 'Feature', properties: { kind: 'monument', id: m.id, name: m.name, description: m.monument, measured: m.measured },
                                  geometry: { type: 'Point', coordinates: [+m.lon.toFixed(9), +m.lat.toFixed(9)] } }));
      }
      pts.forEach(p => F.push({ type: 'Feature', properties: { kind: 'saved', note: p.note, time: p.t ? new Date(p.t).toISOString() : null, accuracy_m: p.sigma, fix: p.fix, elevation_m: p.alt },
                                geometry: { type: 'Point', coordinates: [+p.lon.toFixed(9), +p.lat.toFixed(9)] } }));
      A.download('parcels-' + stamp + '.geojson', JSON.stringify({ type: 'FeatureCollection', features: F }, null, 1), 'application/geo+json');
      return;
    }
    if (fmt === 'kml') {
      const x = s => String(s == null ? '' : s).replace(/[<>&'"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' })[c]);
      const kmlColor = (hex, a) => a + hex.slice(5, 7) + hex.slice(3, 5) + hex.slice(1, 3);
      let body = '';
      if (gm) model.order.forEach(rid => {
        const r = model.rings[rid], ll = gm.rings[rid].ll.concat([gm.rings[rid].ll[0]]);
        body += `<Placemark><name>${x(r.name)}</name><description>${r.areaAc.toFixed(2)} acres</description>
          <Style><LineStyle><color>${kmlColor(r.color, 'ff')}</color><width>3</width></LineStyle><PolyStyle><color>${kmlColor(r.color, '33')}</color></PolyStyle></Style>
          <Polygon><outerBoundaryIs><LinearRing><coordinates>${ll.map(p => p[1].toFixed(9) + ',' + p[0].toFixed(9) + ',0').join(' ')}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>\n`;
      });
      mons.forEach(m => { body += `<Placemark><name>${x(A.shortName(m.id))}</name><description>${x(m.name + '. ' + m.monument + (m.measured ? ' (measured)' : ''))}</description><Point><coordinates>${m.lon.toFixed(9)},${m.lat.toFixed(9)},0</coordinates></Point></Placemark>\n`; });
      pts.forEach(p => { body += `<Placemark><name>${x(p.note || 'Saved point')}</name><description>${x(A.fmtDate(p.t))}</description><Point><coordinates>${p.lon.toFixed(9)},${p.lat.toFixed(9)},0</coordinates></Point></Placemark>\n`; });
      A.download('parcels-' + stamp + '.kml', `<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Parcel RTK ${stamp}</name>\n${body}</Document></kml>\n`,
                 'application/vnd.google-earth.kml+xml');
    }
  };
})();
