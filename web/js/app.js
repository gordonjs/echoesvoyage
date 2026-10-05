/*
 * Parcel RTK — main: live rendering (IN / OUT / ON LINE, distances, navigation and
 * stake-out cards, alerts), button handling, and start-up.
 *
 * Files: app-core.js (data, sync, receiver) · app-map.js (map) · app-panels.js (menu,
 * dialogs) · this file. Pure survey math lives in cogo/geodesy/model/calib/locate.
 */
(function () {
  'use strict';
  const P = window.PRTK, A = P.app, geo = P.geo, cogo = P.cogo, locate = P.locate;
  const model = A.model, esc = A.esc, $ = A.$;

  // ------------------------------------------------------------------ errors are shown, not hidden
  const shownErrors = {};
  window.addEventListener('error', e => {
    const msg = (e && e.message) || 'unknown error';
    if (shownErrors[msg]) return;
    shownErrors[msg] = true;
    try { A.toast('Something went wrong: ' + msg, true, 8000); } catch (x) { /* page not ready */ }
  });

  // ------------------------------------------------------------------ layout
  A.layout = function () {
    const top = $('#top').offsetHeight, l2 = top + $('#line2').offsetHeight, ban = $('#banner');
    $('#line2').style.top = top + 'px';
    ban.style.top = (l2 + 8) + 'px';
    $('#toast').style.top = (l2 + 8 + (ban.hidden ? 0 : ban.offsetHeight + 8)) + 'px';   // under the bars, clear of the cards
  };

  // ------------------------------------------------------------------ live rendering
  let rafQueued = false;
  A.scheduleRender = function () {
    if (rafQueued) return;
    rafQueued = true;
    requestAnimationFrame(() => { rafQueued = false; renderLive(); });
  };
  function renderLive() {
    renderVerdict();
    renderStats();
    renderLine2();
    renderBanner();
    A.drawPosition();
    renderNav();
    renderStake();
    A.followTick();
    A.refreshLive(false);
    if (A.measure) A.renderMeasure();
  }

  function renderVerdict() {
    const el = $('#verdict'), L = A.live, w = L.where, age = A.fixAge();
    let cls = 'none', big = '—', sub = '';
    if (!A.gm) { big = 'SET UP'; sub = 'place the outline'; }
    else if (!L.gga || age > 15) {
      sub = A.bridgeMode && !A.bridge.connected && !A.demo && !A.serial ? 'laptop not reachable'
        : !A.bridgeMode && !A.demo && !A.serial ? 'no receiver' : 'no position yet';
    } else if (!L.gga.valid) { big = 'NO FIX'; sub = 'receiver has no fix'; }
    else if (w) {
      if (w.status === 'IN') { cls = 'in'; big = 'IN'; sub = model.rings[w.inOwned].short; }
      else if (w.status === 'OUT') { cls = 'out'; big = 'OUT'; sub = w.inNeighbor ? model.rings[w.inNeighbor].short : 'not your land'; }
      else { cls = 'online'; big = 'ON LINE'; sub = (w.inOwned ? 'likely in' : 'likely out') + ' · ±' + A.fmtLen(2 * w.sigma, 1); }
    }
    if (cls !== 'none' && age > 3) { cls += ' stale'; sub = 'last fix ' + Math.round(age) + ' s ago'; }
    if (el.className !== cls) el.className = cls;
    setText(el.querySelector('.v-big'), big);
    setText(el.querySelector('.v-sub'), sub);
  }
  function setText(el, t) { if (el.textContent !== t) el.textContent = t; }

  function renderStats() {
    const L = A.live, g = L.gga, fresh = A.fixAge() < 3, pill = $('#sFix');
    let cls = 'pill p-idle', txt = '—';
    if (g && !fresh) { cls = 'pill p-none'; txt = 'NO DATA'; }
    else if (g && !g.valid) { cls = 'pill p-none'; txt = 'NO FIX'; }
    else if (g) {
      txt = A.fixName(g.q).toUpperCase();
      cls = 'pill ' + (g.q === 4 ? 'p-fix' : g.q === 5 ? 'p-float' : 'p-gps');
    }
    if (pill.className !== cls) pill.className = cls;
    setText(pill, txt);
    setText($('#sAcc'), L.pos && fresh ? (L.sigmaEst ? '~' : '') + A.fmtAcc(L.sigma) : '—');
    setText($('#sSats'), g && fresh && g.sats != null ? String(g.sats) : '—');
    const n = A.bridge.status && A.bridge.status.ntrip;
    let corr = 'none', warn = false;
    if (g && fresh && g.valid && g.age != null) { corr = g.age.toFixed(1) + ' s'; warn = g.age > 10; }
    else if (n) { corr = n.state === 'streaming' ? 'arriving' : n.state; warn = n.state !== 'streaming'; }
    const c = $('#sCorr');
    setText(c, corr);
    c.classList.toggle('warn', warn);
  }

  const lineName = id => model.segments[id].name.split(/\s[—(]/)[0];
  function renderLine2() {
    const L = A.live, w = L.where, fresh = A.fixAge() < 15, arrow = $('#edgeArrow');
    if (w && fresh && w.edge) {
      arrow.style.visibility = 'visible';
      arrow.style.transform = 'rotate(' + w.edge.bearing.toFixed(0) + 'deg)';
      let t = A.fmtLen(w.edge.d) + ' to line · ' + lineName(w.edge.segId);
      if (w.inOwned && w.internal && w.internal.d < w.edge.d) t += ' · ' + A.fmtLen(w.internal.d) + ' to your inner line';
      setText($('#edgeText'), t);
      $('#edgeText').title = w.edge.name;
    } else {
      arrow.style.visibility = 'hidden';
      setText($('#edgeText'), A.gm ? 'Waiting for position…' : 'Outline not placed yet');
    }
    if (L.pos && fresh) {
      setText($('#cLat'), L.pos.lat.toFixed(8) + ',');
      setText($('#cLon'), L.pos.lon.toFixed(8));
      setText($('#cAlt'), L.pos.alt != null ? '⛰ ' + A.fmtElev(L.pos.alt) : '');
    } else { setText($('#cLat'), '—'); setText($('#cLon'), ''); setText($('#cAlt'), ''); }
  }

  // one banner at a time, most important first
  const dismissed = {};
  function bannerItem() {
    const b = A.bridge, rx = b.connected && b.status && b.status.rx;
    if (A.bridgeMode && !b.connected && (b.everConnected || Date.now() - A.startT > 4000))
      return { key: 'bridge', html: b.everConnected ? 'Lost the connection to the laptop — reconnecting…'
        : 'Can\'t reach Parcel RTK on the laptop. Is its window still open?', btn: ['Retry', 'reconnect'] };
    if (b.hello && b.hello.version !== A.VERSION)
      return { key: 'ver', cls: 'info', html: 'Parcel RTK was updated on the laptop.', btn: ['Reload', 'reload'] };
    if (rx && rx.state !== 'open' && !A.demo && !A.serial) {
      const soft = { probing: 'Looking for the receiver…', opening: 'Opening the receiver port…', starting: 'Starting the receiver link…',
                     dropped: 'Receiver link dropped — reconnecting…' }[rx.state];
      return { key: 'rx', cls: soft ? 'info' : '', html: soft || 'Receiver: ' + esc(rx.detail || rx.state), btn: ['Details', 'tab', 'status'] };
    }
    if (rx && rx.state === 'open' && A.live.ggaT && A.fixAge() > 5 && !A.demo)
      return { key: 'nodata', html: 'No position from the receiver.', btn: ['Details', 'tab', 'status'] };
    if (A.sol.warnings.length && !dismissed.cal)
      return { key: 'cal', html: esc(A.sol.warnings[0]), btn: ['Calibrate', 'tab', 'calib'], x: true };
    if (A.sol.kind === 'none' && !dismissed.setup)
      return { key: 'setup', cls: 'info', html: 'Start by putting your property outline on the map.', btn: ['Calibrate', 'tab', 'calib'], x: true };
    if (A.sol.kind === 'rough' && !dismissed.rough && !A.adjust)
      return { key: 'rough', cls: 'info', html: 'Outline placed by eye (±' + A.fmtLen(A.proj.settings.roughSigmaFt * A.K, 0) + '). Measure two monuments to lock it to the ground.',
               btn: ['How', 'tab', 'calib'], x: true };
    return null;
  }
  function renderBanner() {
    const it = bannerItem(), el = $('#banner'), sig = it ? it.key + '|' + it.html : '';
    if (sig === el._sig) return;
    el._sig = sig;
    if (!it) { el.hidden = true; el.innerHTML = ''; A.layout(); return; }
    el.className = it.cls || '';
    el.innerHTML = `<span class="b-txt">${it.html}</span>` +
      (it.btn ? `<button class="btn small" data-act="${it.btn[1]}"${it.btn[2] ? ` data-tab="${it.btn[2]}"` : ''}>${it.btn[0]}</button>` : '') +
      (it.x ? `<button class="x" data-act="bannerx" data-key="${it.key}" title="Hide">✕</button>` : '');
    el.hidden = false;
    A.layout();
  }

  // ------------------------------------------------------------------ navigation
  A.nav = null;                       // {kind:'pt'|'saved'|'ll', id?, lat?, lon?, name?}
  function navTarget() {
    const n = A.nav;
    if (!n) return null;
    if (n.kind === 'pt') { const g = A.gm && A.gm.pts[n.id]; return g ? { lat: g.lat, lon: g.lon, name: A.shortName(n.id) } : null; }
    if (n.kind === 'saved') { const p = A.proj.savedPoints.find(x => x.id === n.id); return p ? { lat: p.lat, lon: p.lon, name: p.note || 'saved point' } : null; }
    return { lat: n.lat, lon: n.lon, name: n.name || 'map spot' };
  }
  function startNav(d) {
    A.nav = d.kind === 'll' ? { kind: 'll', lat: +d.lat, lon: +d.lon } : { kind: d.kind, id: d.id };
    stopStake(); A.stopAdjust();
    A.closePopup(); A.closePanelOnMobile();
    const t = navTarget();
    if (!t) { A.nav = null; return; }
    if (A.hasFix()) A.fitPoints([[A.live.pos.lat, A.live.pos.lon], [t.lat, t.lon]], 20);
    else A.zoomTo(t.lat, t.lon, 18);
    A.scheduleRender();
  }
  A.stopNav = function () { A.nav = null; A.drawNav(null); A.scheduleRender(); };
  function renderNav() {
    const card = $('#navCard'), t = navTarget();
    if (!t) { if (!card.hidden) card.hidden = true; A.drawNav(null); if (A.nav) A.nav = null; return; }
    card.hidden = false;
    setText($('#navName'), t.name);
    const pos = A.live.pos;
    if (!pos || A.fixAge() > 15) {
      setText($('#navDist'), '—'); setText($('#navSub'), 'waiting for position');
      $('#navArrow').style.visibility = 'hidden'; $('#navHere').hidden = true; A.drawNav(null);
      return;
    }
    const db = geo.distBearing(pos, t), arrow = $('#navArrow');
    arrow.style.visibility = 'visible';
    arrow.style.transform = 'rotate(' + db.bearing.toFixed(0) + 'deg)';
    setText($('#navDist'), A.fmtLen(db.dist));
    setText($('#navSub'), A.card(db.bearing) + ' · ' + Math.round(db.bearing) + '° true' + (db.dist < 2 * A.live.sigma ? ' · you\'re there (within GPS accuracy)' : ''));
    const here = A.nav.kind === 'pt' && db.dist < 15 * A.K;
    $('#navHere').hidden = !here;
    if (here) {
      setText($('#navLook'), 'Look for: ' + (model.points[A.nav.id].monument || 'a pin or monument'));
      $('#navMeasure').dataset.id = A.nav.id;
    }
    A.drawNav(t);
  }

  // ------------------------------------------------------------------ stake out a line
  A.stake = null;                     // {segId}
  function startStake(id) {
    if (!A.gm || !model.segments[id]) { A.toast('Place the outline first', true); return; }
    A.stake = { segId: id };
    A.nav = null; A.drawNav(null); A.stopAdjust();
    A.closePopup(); A.closePanelOnMobile();
    A.renderPane('tools');
    A.scheduleRender();
  }
  function stopStake() {
    if (!A.stake) return;
    A.stake = null;
    A.drawStake(null);
    A.renderPane('tools');
    A.scheduleRender();
  }
  function renderStake() {
    const card = $('#stakeCard');
    if (!A.stake || !A.gm) { if (!card.hidden) card.hidden = true; A.drawStake(null); return; }
    card.hidden = false;
    const id = A.stake.segId, L0 = A.live;
    setText($('#stakeName'), A.segLabel(id));
    const off = $('#stakeOff');
    if (!L0.xy || A.fixAge() > 15) {
      setText(off, '—'); off.classList.remove('on'); setText($('#stakeSub'), 'waiting for position');
      A.drawStake(id, null);
      return;
    }
    const r = locate.stakeout(model, A.gm, id, L0.xy.x, L0.xy.y), d = Math.abs(r.offset);
    const on = d < Math.max(0.03, L0.sigma || 0);
    setText(off, on ? 'ON THE LINE' : r.offset > 0 ? '◀ ' + A.fmtLen(d) : A.fmtLen(d) + ' ▶');
    off.classList.toggle('on', on);
    const side = r.offset > 0 ? 'right' : 'left';
    setText($('#stakeSub'), (on ? '' : `You're ${side} of the line (facing ${A.shortName(r.b)}) — move ${side === 'right' ? 'left' : 'right'}. `) +
      `${A.fmtLen(r.along)} along, of ${A.fmtLen(r.length)} from ${A.shortName(r.a)}.`);
    const g = A.gm.frame.toGeo(r.point.x, r.point.y, 0);
    A.drawStake(id, { lat: g.lat, lon: g.lon });
  }

  // ------------------------------------------------------------------ hand-placement card
  function renderAdjCard() {
    const c = $('#adjCard');
    if (!A.adjust) { c.hidden = true; c.innerHTML = ''; A.layout(); return; }
    const both = A.adjust.mode === 'both', rot = A.outlineRotation();
    c.innerHTML = `<div class="nc-row"><div class="grow">
        <div class="nc-title">${both ? 'Line the outline up with the photo' : 'Rotate the outline about your measured monument'}</div>
        <div class="nc-sub">${both ? 'Drag ✥ to move it, drag ⟳ to turn it.' : 'Drag ⟳ to turn it.'} Plat north is ${cogo.fmtDms(Math.abs(rot))} ${rot >= 0 ? 'west' : 'east'} of true north.</div></div>
        <button class="x" data-act="adjdone" title="Done">✕</button></div>
      <div class="row adj-row">
        <button class="btn small" data-act="rot" data-deg="1">⟲ 1°</button><button class="btn small" data-act="rot" data-deg="0.1">⟲ 0.1°</button>
        <button class="btn small" data-act="rot" data-deg="-0.1">0.1° ⟳</button><button class="btn small" data-act="rot" data-deg="-1">1° ⟳</button></div>
      ${both ? `<div class="row adj-row">
        <button class="btn small" data-act="nudge" data-dx="-1" data-dy="0">← 1 ft</button><button class="btn small" data-act="nudge" data-dx="0" data-dy="1">↑ 1 ft</button>
        <button class="btn small" data-act="nudge" data-dx="0" data-dy="-1">↓ 1 ft</button><button class="btn small" data-act="nudge" data-dx="1" data-dy="0">1 ft →</button></div>` : ''}
      <div class="row adj-row">${both && A.hasFix() ? '<button class="btn small" data-act="adjme">Center on me</button>' : ''}
        <button class="btn small good" data-act="adjdone">Done</button></div>
      <div class="hint">Satellite photos can be off by 3–10 ft. Get it close, then measure monuments for the real answer.</div>`;
    c.hidden = false;
  }

  // ------------------------------------------------------------------ distances in panels and popups
  let lastLive = 0;
  A.refreshLive = function (force) {
    const now = Date.now();
    if (!force && now - lastLive < 500) return;
    lastLive = now;
    const pos = A.hasFix() ? A.live.pos : null;
    A.$$('[data-live="dist"]').forEach(el => {
      if (!pos) { setText(el, '—'); return; }
      const db = geo.distBearing(pos, { lat: +el.dataset.lat, lon: +el.dataset.lon });
      setText(el, A.fmtLen(db.dist) + ' ' + A.card(db.bearing));
    });
  };

  // ------------------------------------------------------------------ property-line alert
  const alertSt = { zone: false, inside: null };
  function edgeAlert() {
    const lim = +A.proj.settings.edgeAlertFt, w = A.live.where;
    if (!lim || !w || !w.edge || A.fixAge() > 3) { alertSt.zone = false; alertSt.inside = null; return; }
    const ft = w.edge.d * A.FT;
    if (!alertSt.zone && ft <= lim) { alertSt.zone = true; A.beep(1); }
    else if (alertSt.zone && ft > lim + Math.max(1, lim * 0.2)) alertSt.zone = false;
    // a crossing counts once you're clearly on the other side (no chatter while standing on the line)
    if (w.edge.d > Math.max(0.03, A.live.sigma || 0)) {
      const inside = !!w.inOwned;
      if (alertSt.inside !== null && inside !== alertSt.inside) A.beep(2, inside ? 1046 : 587);
      alertSt.inside = inside;
    }
  }

  // ------------------------------------------------------------------ panel
  function setTab(name) {
    A.ui.tab = name; A.saveUi();
    A.$$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
    A.$$('.p-body > section').forEach(s => { s.hidden = s.dataset.pane !== name; });
  }
  A.openPanel = function (tab) {
    if (tab && tab !== A.activeTab()) { setTab(tab); $('#panel .p-body').scrollTop = 0; }
    $('#panel').classList.add('open');
    document.body.classList.add('panel-open');
    A.layout();
    A.renderPane(A.activeTab(), 'show');
    A.updateStatusPane(true);
  };
  A.closePanel = function () {
    $('#panel').classList.remove('open');
    document.body.classList.remove('panel-open');
    A.layout();
  };
  A.closePanelOnMobile = function () { if (window.innerWidth < 820) A.closePanel(); };

  // ------------------------------------------------------------------ actions (every data-act button)
  const ACT = {
    tab: d => A.openPanel(d.tab),
    nav: d => startNav(d),
    navstop: () => A.stopNav(),
    measure: d => A.openMeasure(d.id),
    mclose: () => A.modal.close(),
    mstop: () => A.measureAction('mstop'),
    mredo: () => A.measureAction('mredo'),
    msave: () => A.measureAction('msave'),
    save: () => A.openSave(),
    svsave: () => A.saveAction(),
    savespot: d => A.openSave({ lat: +d.lat, lon: +d.lon }),
    copyll: d => A.copy(d.lat + ', ' + d.lon),
    copytext: d => A.copy(d.text),
    stake: d => startStake(d.id),
    stakesel: () => startStake($('#tStake').value),
    stakestop: () => stopStake(),
    ptedit: d => A.openEditPoint(d.id),
    ptnote: d => {
      const note = ($('#edNote') || {}).value || '';
      if (A.edit({ op: 'patch', path: ['savedPoints'], id: d.id, value: { note: note.trim() } })) { A.modal.close(); A.toast('Saved'); }
    },
    ptdel: async d => {
      const p = A.proj.savedPoints.find(x => x.id === d.id);
      if (!p || !(await A.confirm(`Delete <b>${esc(p.note || 'this point')}</b>?`, 'Delete', true))) return;
      if (A.edit({ op: 'remove', path: ['savedPoints'], id: d.id })) {
        if (A.nav && A.nav.kind === 'saved' && A.nav.id === d.id) A.stopNav();
        A.closePopup();
        A.toast('Deleted');
      }
    },
    ptshow: d => {
      const p = A.proj.savedPoints.find(x => x.id === d.id);
      if (p) { A.closePanelOnMobile(); A.zoomTo(p.lat, p.lon, 19); }
    },
    ctluse: d => A.edit({ op: 'set', path: ['control', d.id, 'use'], value: d.use === '1' }),
    ctldel: async d => {
      if (await A.confirm(`Remove the measurement of the <b>${esc(A.shortName(d.id))}</b>?`, 'Remove', true))
        A.edit({ op: 'del', path: ['control', d.id] });
    },
    clearrough: async () => {
      if (await A.confirm('Clear the hand placement of the outline?', 'Clear', true)) { A.stopAdjust(); A.edit({ op: 'set', path: ['rough'], value: null }); }
    },
    clearctl: async () => {
      if (await A.confirm('Remove <b>all</b> monument measurements? The outline falls back to your hand placement.', 'Remove all', true))
        A.edit({ op: 'set', path: ['control'], value: {} });
    },
    adjust: () => { A.nav = null; stopStake(); A.startAdjust(); },
    adjdone: () => A.stopAdjust(),
    rot: d => A.adjRotate(+d.deg),
    nudge: d => A.adjNudge(+d.dx, +d.dy),
    adjme: () => A.adjCenterOnMe(),
    fit: () => { A.closePanelOnMobile(); if (!A.fitParcels()) A.toast('The outline isn\'t placed yet', true); },
    export: d => A.exportData(d.fmt),
    demo: () => A.startDemo(),
    demostop: () => A.stopDemo('Demo stopped'),
    serial: () => A.serialConnect(),
    clrtrack: () => { A.clearTrack(); A.toast('Track cleared'); },
    testalert: () => A.beep(2),
    showlog: () => A.showLog(),
    reconnect: () => A.reconnectNow(),
    reload: () => location.reload(),
    bannerx: d => { dismissed[d.key] = true; renderBanner(); A.layout(); },
    listports: () => A.listPorts(),
    findbases: () => A.findBases(),
    usebase: d => { $('#cfMount').value = d.mount; A.setCfgDirty(true); A.toast('Mountpoint ' + d.mount + ' — now press Save settings'); },
    chkupdate: () => A.checkUpdate(),
    doupdate: () => A.doUpdate(),
    cfgsave: () => A.saveSettings(),
    cfgdiscard: () => { A.setCfgDirty(false); A.renderPane('settings', 'force'); }
  };

  function bind() {
    document.addEventListener('click', e => {
      const el = e.target.closest('[data-act]');
      if (!el || el.disabled) return;
      const fn = ACT[el.dataset.act];
      if (!fn) return;
      e.preventDefault();
      Promise.resolve(fn(el.dataset, el)).catch(err => { console.error(err); A.toast(String(err.message || err), true); });
    });
    document.addEventListener('change', e => {
      const el = e.target.closest('[data-chg]');
      if (!el) return;
      const key = el.dataset.key;
      if (el.dataset.chg === 'setting') {
        let v = el.type === 'checkbox' ? el.checked : el.value;
        if (el.dataset.num) v = +v;
        A.edit({ op: 'set', path: ['settings', key], value: v });
      } else if (el.dataset.chg === 'ui') {
        A.ui[key] = el.checked; A.saveUi();
        if (key === 'track') A.showTrack(el.checked);
        if (key === 'wake') A.updateWakeLock();
      }
    });
    const sp = A.paneEl('settings');
    sp.addEventListener('input', A.onSettingsInput);
    sp.addEventListener('change', A.onSettingsInput);
    $('#panel').addEventListener('focusout', () => setTimeout(A.renderStale, 250));

    $('#tabs').addEventListener('click', e => {
      const b = e.target.closest('button[data-tab]');
      if (b) A.openPanel(b.dataset.tab);
    });
    $('#btnMenu').onclick = () => { if (A.isPanelOpen()) A.closePanel(); else A.openPanel(); };
    $('#pClose').onclick = A.closePanel;
    $('#btnSave').onclick = () => A.openSave();
    $('#fFollow').onclick = () => A.setFollow(!A.ui.follow);
    $('#fZoomIn').onclick = () => A.map.zoomIn();
    $('#fZoomOut').onclick = () => A.map.zoomOut();
    $('#fLayer').onclick = () => {
      const o = A.BASE_ORDER, next = o[(o.indexOf(A.proj.settings.base) + 1) % o.length];
      A.edit({ op: 'set', path: ['settings', 'base'], value: next });
      A.toast(next === 'none' ? 'No map' : A.BASES[next].name);
    };
    $('#navClose').dataset.act = 'navstop';
    $('#navMeasure').dataset.act = 'measure';
    $('#stakeClose').dataset.act = 'stakestop';
    $('#modal').addEventListener('click', e => {
      if (e.target.id !== 'modal') return;                     // a tap outside the dialog
      if (A.modal.kind === 'measure' && A.measure && A.measure.samples.length) return;
      if (A.modal.kind === 'save' && A.saving && A.saving.active) return;
      A.modal.close();
    });
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      if (A.modal.isOpen()) A.modal.close();
      else if (A.adjust) A.stopAdjust();
      else if (A.isPanelOpen()) A.closePanel();
    });
    window.addEventListener('resize', A.layout);
    if (window.ResizeObserver) { const ro = new ResizeObserver(A.layout); ro.observe($('#top')); ro.observe($('#line2')); }
  }

  // ------------------------------------------------------------------ events from the core
  let firstFix = true;
  A.on('project', () => {
    A.drawAll();
    ['status', 'calib', 'points', 'tools', 'settings'].forEach(n => A.renderPane(n));
    if (A.adjust) renderAdjCard();
    A.scheduleRender();
  });
  A.on('fix', () => {
    const pos = A.live.pos;
    A.trackPush(pos);
    if (A.measure) A.measureSample();
    if (A.saving) A.saveSample();
    edgeAlert();
    if (firstFix && pos) {
      firstFix = false;
      if (!A.gm) A.zoomTo(pos.lat, pos.lon, 17);
    }
    A.scheduleRender();
  });
  A.on('status', () => { A.updateStatusPane(); A.scheduleRender(); });
  A.on('config', () => { A.renderPane('settings'); A.updateStatusPane(); });
  A.on('bridge', () => { A.renderPane('settings'); A.updateStatusPane(); A.scheduleRender(); });
  A.on('adjust', () => { renderAdjCard(); A.renderPane('calib'); A.scheduleRender(); });
  A.on('demo', () => { A.renderPane('tools'); A.scheduleRender(); });
  A.on('serial', () => { A.renderPane('tools'); A.scheduleRender(); });

  // ------------------------------------------------------------------ start
  function init() {
    A.startT = Date.now();
    A.initMap();
    bind();
    A.loadProject();                   // draws the map through the 'project' event
    if (!A.fitParcels()) A.map.setView([39.4970, -105.3050], 15);
    $('#fFollow').classList.toggle('on', A.ui.follow);
    setTab(A.ui.tab || 'status');
    A.showTrack(A.ui.track);
    A.layout();
    A.startBridge();
    setInterval(() => {
      A.bridgeWatchdog();
      A.updateWakeLock();
      A.updateStatusPane();
      A.scheduleRender();
    }, 1000);
    A.scheduleRender();
    if (!A.bridgeMode && A.migrationText()) A.toast(A.migrationText(), false, 7000);   // with a bridge: said on first sync
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
