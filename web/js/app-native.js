/*
 * Parcel RTK — running inside the Android app. The Android shell (android/) injects
 * window.PRTKNative and does what the laptop bridge does: the Bluetooth link to the
 * receiver, the correction stream, and the tile cache. This file is the glue: it feeds the
 * shell's data into the same pipeline the bridge uses, and gives the Settings tab a form for
 * the shell's settings. The project (calibration, saved points) lives in this app's own
 * storage; "Export project" / "Import project" move it to and from the laptop.
 */
(function () {
  'use strict';
  const P = window.PRTK, A = P.app, esc = A.esc, $ = A.$, ST = P.state;
  const N = window.PRTKNative;
  if (!N) { A.native = null; return; }

  A.native = { cfg: null, devices: [], status: null, version: (function () { try { return N.version(); } catch (e) { return '?'; } })(), log: [] };
  A.bridgeMode = false;
  A.detectBridge = () => Promise.resolve(false);

  // ---- data from the shell ----------------------------------------------------------
  A.native.onNmea = function (lines) { if (Array.isArray(lines)) A.onNmeaLines(lines, 'android'); };
  A.native.onStatus = function (st) {
    A.native.status = st;
    // the status cards read A.bridge.status; the shell is our "bridge"
    A.bridge.status = st; A.bridge.connected = true;
    A.bridge.hello = A.bridge.hello || { app: 'parcel-rtk', version: A.native.version, local: true, native: true, lan: [] };
    A.emit('status');
  };
  A.native.onConfig = function (cfg, devices) {
    A.native.cfg = cfg; A.native.devices = devices || [];
    A.bridge.cfg = cfg; A.emit('config');
  };
  A.native.onLog = function (line) { A.native.log.push(line); if (A.native.log.length > 300) A.native.log.shift(); };
  A.native.onProjectFile = function (text) {
    let st;
    try { st = JSON.parse(text); } catch (e) { A.toast('That file is not a Parcel RTK project', true); return; }
    if (!st || st.schema !== 2) { A.toast('That file is not a Parcel RTK project (rtk_data/project.json)', true); return; }
    A.confirm(`Replace the project on this tablet with the imported one?<div class="hint">${(st.savedPoints || []).length} saved points, ${Object.keys(st.control || {}).length} measured monuments${st.rough ? ', hand placement' : ''}.</div>`, 'Import', true)
      .then(ok => { if (ok) A.importProject(st); });
  };

  // ---- the shell's settings ----------------------------------------------------------
  A.native.settingsHtml = function () {
    const cfg = A.native.cfg;
    if (!cfg) return '<div class="hint">Starting the receiver link…</div>';
    const s = cfg.source, n = cfg.ntrip, devs = A.native.devices;
    const devOpts = ['<option value="">Any paired receiver (finds "Surveyor" by name)</option>']
      .concat(devs.map(d => `<option value="${esc(d.name)}" ${d.name === s.device ? 'selected' : ''}>${esc(d.name)} <span class="dim">${esc(d.address)}</span></option>`));
    if (s.device && !devs.some(d => d.name === s.device)) devOpts.push(`<option value="${esc(s.device)}" selected>${esc(s.device)} (not paired now)</option>`);
    return `<div class="sec"><h3>Receiver (Bluetooth)</h3><div class="card">
        <label class="f"><span>Paired receiver</span><select class="inp" id="cfDevice">${devOpts.join('')}</select></label>
        <div class="row"><button class="btn small" data-act="ndevices">Refresh list</button><button class="btn small" data-act="nbtsettings">Open Bluetooth settings</button></div>
        <div class="hint">Pair the Surveyor once in the tablet's Bluetooth settings (its Bluetooth set to <b>Classic</b>, switch on <b>Rover</b>). The app connects to it by itself from then on.</div></div></div>
      <div class="sec"><h3>Corrections (NTRIP)</h3><div class="card">
        <label class="check"><input type="checkbox" id="cfNtripOn" ${n.enabled ? 'checked' : ''}> Get RTK corrections over the internet</label>
        <label class="f"><span>Service</span><select class="inp" id="cfPreset">
          <option value="pointperfect" ${/u-blox/.test(n.host) ? 'selected' : ''}>u-blox PointPerfect (NEAR-RTCM)</option>
          <option value="rtk2go" ${/rtk2go/.test(n.host) ? 'selected' : ''}>RTK2go (free community bases)</option>
          <option value="custom" ${n.host && !/u-blox|rtk2go/.test(n.host) ? 'selected' : ''}>Other caster</option></select></label>
        <label class="f"><span>Caster</span><input class="inp" id="cfHost" value="${esc(n.host || '')}" spellcheck="false" autocomplete="off"></label>
        <div class="row"><label class="f" style="width:110px"><span>Port</span><input class="inp" id="cfNPort" inputmode="numeric" value="${esc(n.port || 2101)}"></label>
          <label class="f grow"><span>Mountpoint</span><input class="inp" id="cfMount" value="${esc(n.mount || '')}" spellcheck="false" autocomplete="off"></label></div>
        <label class="f"><span>Username</span><input class="inp" id="cfUser" value="${esc(n.user || '')}" spellcheck="false" autocomplete="off"></label>
        <label class="f"><span>Password</span><input class="inp" id="cfPass" type="password" autocomplete="new-password" placeholder="${n.passSet ? 'saved on this tablet — leave blank to keep it' : ''}"></label>
        ${n.passSet ? '<label class="check"><input type="checkbox" id="cfClearPass"> Forget the saved password</label>' : ''}
        <div class="hint">Uses the tablet's internet (cell data or a phone hotspot). PointPerfect: use the <b>NEAR-RTCM</b> mountpoint; the login is in your Thingstream account under Credentials.</div></div></div>
      <div class="sec"><h3>Project file</h3><div class="card">
        <div class="row"><button class="btn small" data-act="nexport">Export project</button><button class="btn small" data-act="nimport">Import project…</button></div>
        <div class="hint">The project (calibration and saved points) is kept on this tablet. To use the laptop's calibration here, copy its <span class="mono">rtk_data\\project.json</span> to the tablet and import it; to take this tablet's work to the laptop, export it and replace that file while the laptop program is closed.</div></div></div>
      <div class="sec"><h3>About</h3><div class="card">${A.native.version ? 'Parcel RTK for Android ' + esc(A.native.version) + ' · app ' + esc(A.VERSION) : ''}
        <div class="row"><button class="btn small" data-act="nlog">Show log</button></div><div id="nLog"></div></div></div>
      <div class="savebar"><button class="btn primary" data-act="ncfgsave">Save settings</button>
        <button class="btn ghost" data-act="cfgdiscard">Discard</button><span class="hint" id="cfDirty"></span></div>`;
  };
  A.native.onPreset = function (v) {
    const presets = { pointperfect: ['ppntrip.services.u-blox.com', 2101, 'NEAR-RTCM'], rtk2go: ['rtk2go.com', 2101, ''] };
    const p = presets[v];
    if (p) { $('#cfHost').value = p[0]; $('#cfNPort').value = p[1]; $('#cfMount').value = p[2]; }
  };
  A.native.saveSettings = function () {
    const v = id => { const el = $('#' + id); return el ? el.value.trim() : ''; };
    const chk = id => { const el = $('#' + id); return !!(el && el.checked); };
    const num = (x, d) => { const n = parseInt(x, 10); return isFinite(n) && n > 0 ? n : d; };
    const cfg = { source: { type: 'bluetooth', device: v('cfDevice') },
      ntrip: { enabled: chk('cfNtripOn'), host: v('cfHost').replace(/^(ntrip|https?):\/\//i, '').replace(/\/.*$/, ''), port: num(v('cfNPort'), 2101),
               mount: v('cfMount').replace(/^\//, ''), user: v('cfUser'), pass: ($('#cfPass') || {}).value || '', clearPass: chk('cfClearPass'), ggaInterval: 10 } };
    if (cfg.ntrip.enabled && (!cfg.ntrip.host || !cfg.ntrip.mount)) { A.toast('Corrections need a caster and a mountpoint', true); return; }
    N.setConfig(JSON.stringify(cfg));
    A.setCfgDirty(false);
    A.toast('Saved. Reconnecting with the new settings…');
  };

  // ---- project import / export -------------------------------------------------------
  A.exportProject = function () {
    const st = Object.assign({ schema: 2, rev: A.proj.rev || 0 }, { rough: A.proj.rough, control: A.proj.control, settings: A.proj.settings, savedPoints: A.proj.savedPoints });
    N.saveFile('project.json', JSON.stringify(st, null, 1), 'application/json');
  };
  A.importProject = function (st) {
    const base = ST.empty();
    ['rough', 'control', 'settings', 'savedPoints'].forEach(k => { if (st[k] != null) base[k] = st[k]; });
    base.settings = Object.assign({}, ST.DEFAULT_SETTINGS, base.settings || {});
    A.base = base; A.pending = [];
    A.store.set('parcelRTK.v2', base); A.store.set('parcelRTK.v2.pending', []);
    A.recompute();
    A.toast('Project imported');
  };

  // files the app offers (CSV, KML, GeoJSON) go through the tablet's file picker
  A.download = function (name, text, type) { N.saveFile(name, text, type || 'text/plain'); };
  A.updateWakeLock = function () { try { N.keepScreenOn(!!A.ui.wake); } catch (e) { /* ignore */ } };
  A.wakeSupported = () => true;

  // tell the shell we're ready for config + status
  const ready = () => { try { N.ready(); } catch (e) { /* ignore */ } };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(ready, 50));
  else setTimeout(ready, 50);
})();
