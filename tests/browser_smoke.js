/*
 * End-to-end browser test: the real bridge (simulated receiver) + headless Chromium.
 *   npm install --no-save playwright-core        (once, in the repo folder)
 *   CHROME=/path/to/chrome node tests/browser_smoke.js     [SHOTS=dir to save screenshots]
 * Covers: v1 data migration, live IN/OUT, sync between two devices, saving points, measuring
 * monuments, hand placement, stake-out, navigation, Status/Settings, the file:// mode, and a
 * full two-monument calibration against a known truth (IN / OUT / ON LINE at 1 ft).
 */
'use strict';
let chromium;
try { ({ chromium } = require('playwright-core')); } catch (e) {
  console.log('playwright-core is not installed: run  npm install --no-save playwright-core  in the repo folder');
  process.exit(2);
}
const { spawn } = require('child_process');
const fs = require('fs'), path = require('path'), os = require('os'), net = require('net');

const REPO = path.resolve(__dirname, '..');
const CHROME = process.env.CHROME || undefined;
const PYTHON = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const SHOTS = process.env.SHOTS || null;
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

// run from a temp copy so the test never touches a real rtk_config.json or rtk_data/
const run = fs.mkdtempSync(path.join(os.tmpdir(), 'prtk-e2e-'));
fs.cpSync(path.join(REPO, 'web'), path.join(run, 'web'), { recursive: true });
fs.copyFileSync(path.join(REPO, 'rtk_bridge.py'), path.join(run, 'rtk_bridge.py'));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });

const errors = [];
let failures = 0;
function check(cond, msg) { if (cond) console.log('  ok  ' + msg); else { failures++; console.log('  FAIL ' + msg); } }
const sleep = ms => new Promise(r => setTimeout(r, ms));

// v1 data as the old app stored it: rough placement near the simulated receiver + 2 saved points
const lat0 = 39.4970, f = lat0 * Math.PI / 180;
const V1 = {
  pins: { 1: null, 2: null }, base: 'sat', navKey: '',
  cal: { lat0: lat0, lon0: -105.3050, a1e: 700, a1n: 0, s: 1200 / 3937, ang: 0.02, cos: Math.cos(0.02), sin: Math.sin(0.02),
         mLat: 111132.954 - 559.822 * Math.cos(2 * f) + 1.175 * Math.cos(4 * f), mLon: 111132.954 * Math.cos(f), platDistFt: 0, measDistM: 0 },
  saved: [{ id: '101', lat: 39.4972, lon: -105.3049, alt: 2650, acc: 0.014, fix: 4, t: 1720000000000, note: 'old well' },
          { id: '102', lat: 39.4968, lon: -105.3052, alt: 2651, acc: 0.02, fix: 4, t: 1720000100000, note: 'gate post, "north"' }]
};

async function newPage(browser, opts, seed) {
  const ctx = await browser.newContext(opts);
  if (seed) await ctx.addInitScript(v1 => { if (!localStorage.getItem('parcelRTK.v2')) localStorage.setItem('parcelRTK', JSON.stringify(v1)); }, V1);
  const page = await ctx.newPage();
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push('[console] ' + m.text()); });
  page.on('response', r => { if (r.status() >= 400 && !/\/tiles\//.test(r.url())) errors.push('[http ' + r.status() + '] ' + r.url()); });
  page.on('pageerror', e => errors.push('[pageerror] ' + e.message));
  return page;
}
const app = (page, fn, arg) => page.evaluate(fn, arg);
const shot = (page, name) => SHOTS ? page.screenshot({ path: path.join(SHOTS, name) }) : Promise.resolve();
async function tab(page, name) {
  if (!(await page.evaluate(() => document.querySelector('#panel').classList.contains('open')))) await page.click('#btnMenu');
  await page.click(`#tabs button[data-tab="${name}"]`);
  await sleep(250);
}

(async () => {
  const PORT = await freePort();
  let blog = '', br = null;
  const startBridge = async () => {
    br = spawn(PYTHON, ['rtk_bridge.py', '--source', 'test', '--no-browser', '--port', String(PORT)], { cwd: run });
    br.stdout.on('data', d => { blog += d; });
    br.stderr.on('data', d => { blog += d; });
    for (let i = 0; i < 50; i++) {
      try { const r = await fetch(`http://127.0.0.1:${PORT}/api/version`); if (r.ok) return; } catch (e) { /* not up yet */ }
      await sleep(200);
    }
  };
  await startBridge();
  const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
  const URL = `http://127.0.0.1:${PORT}/`;
  try {
    // ---------------------------------------------------------------- phone-sized, with v1 data
    console.log('1. first load on the laptop browser with old v1 data');
    const p1 = await newPage(browser, { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 }, true);
    await p1.goto(URL);
    await p1.waitForFunction(() => /IN|OUT|ON LINE/.test(document.querySelector('#verdict .v-big').textContent), null, { timeout: 15000 });
    const toast = await p1.textContent('#toast');
    check(/old version/.test(toast) && /laptop/.test(toast), 'migration toast shown: ' + toast);
    const st1 = await app(p1, () => ({ kind: PRTK.app.sol.kind, pts: PRTK.app.proj.savedPoints.length, verdict: document.querySelector('#verdict').className,
                                       big: document.querySelector('#verdict .v-big').textContent, sub: document.querySelector('#verdict .v-sub').textContent,
                                       edge: document.querySelector('#edgeText').textContent, fix: document.querySelector('#sFix').textContent }));
    console.log('   ', JSON.stringify(st1));
    check(st1.kind === 'rough', 'v1 hand placement migrated (kind rough)');
    check(st1.pts === 2, 'v1 saved points migrated');
    check(st1.fix === 'RTK FIX', 'live RTK FIX from the simulated receiver');
    check(/to line/.test(st1.edge), 'distance to the line shown');
    await sleep(1500);
    await shot(p1, '1-phone-main.png');

    // ---------------------------------------------------------------- second device sees the same project
    console.log('2. a second device (phone) syncs');
    const p2 = await newPage(browser, { viewport: { width: 390, height: 844 } }, false);
    await p2.goto(URL);
    await p2.waitForFunction(() => PRTK.app.proj && PRTK.app.proj.savedPoints.length === 2, null, { timeout: 10000 });
    check(true, 'second device got the migrated project from the bridge');

    // ---------------------------------------------------------------- save a point with a note
    console.log('3. save a point');
    await p1.click('#btnSave');
    await p1.fill('#svNote', 'NE fence corner <b>&</b>');
    await p1.click('#svBtn');
    await p1.waitForFunction(() => PRTK.app.proj.savedPoints.length === 3, null, { timeout: 5000 });
    await p2.waitForFunction(() => PRTK.app.proj.savedPoints.length === 3, null, { timeout: 5000 });
    check(true, 'saved point reached the other device');
    const pend = await p1.waitForFunction(() => PRTK.app.pending.length === 0, null, { timeout: 4000 }).then(() => 0, () => -1);
    check(pend === 0, 'no pending edits once the bridge confirmed');

    // ---------------------------------------------------------------- points pane escapes notes
    await tab(p1, 'points');
    const html = await p1.innerHTML('[data-pane="points"]');
    check(html.includes('NE fence corner &lt;b&gt;&amp;&lt;/b&gt;'), 'notes are escaped in the list');
    await shot(p1, '2-phone-points.png');

    // ---------------------------------------------------------------- measure a monument
    console.log('4. measure a monument');
    await tab(p1, 'calib');
    await shot(p1, '3-phone-calib.png');
    await p1.click('[data-pane="calib"] [data-act="measure"][data-id="TPB"]');
    await p1.waitForSelector('[data-act="mstop"]', { timeout: 8000 });
    await shot(p1, '4-phone-measuring.png');
    await p1.click('[data-act="mstop"]');
    await p1.waitForSelector('[data-act="msave"]', { timeout: 3000 });
    const prev = await p1.textContent('#msResult');
    console.log('    preview:', prev.trim().slice(0, 160));
    await shot(p1, '5-phone-measured.png');
    await p1.click('[data-act="msave"]');
    await p1.waitForFunction(() => PRTK.app.sol.kind === 'one', null, { timeout: 5000 });
    check(true, 'one monument measured -> kind one');
    const ctl = await app(p1, () => PRTK.app.proj.control.TPB);
    check(ctl && ctl.fix === 4 && ctl.n >= 5 && ctl.sigma > 0 && ctl.corr === null, 'control saved with fix/n/sigma (' + JSON.stringify({ n: ctl.n, s: ctl.sigma }) + ')');

    // ---------------------------------------------------------------- rotate by hand about the monument
    console.log('5. rotate by hand');
    await tab(p1, 'calib');
    await p1.click('[data-pane="calib"] [data-act="adjust"]');
    await p1.waitForSelector('#adjCard:not([hidden])');
    const th0 = await app(p1, () => PRTK.app.outlineRotation());
    await p1.click('#adjCard [data-act="rot"][data-deg="1"]');
    await sleep(300);
    const th1 = await app(p1, () => PRTK.app.outlineRotation());
    check(Math.abs(th1 - th0 - 1) < 0.01, 'rotate +1° (' + th0.toFixed(3) + ' -> ' + th1.toFixed(3) + ')');
    await shot(p1, '6-phone-adjust.png');
    await p1.click('#adjCard [data-act="adjdone"]');

    // ---------------------------------------------------------------- stake out + navigation
    console.log('6. stake out, navigate');
    await app(p1, () => document.querySelector('[data-act="stake"]') || null);
    await tab(p1, 'tools');
    await p1.selectOption('#tStake', 'l1w');
    await p1.click('[data-act="stakesel"]');
    await p1.waitForSelector('#stakeCard:not([hidden])');
    await sleep(800);
    const sk = await p1.textContent('#stakeOff');
    check(/ft|ON THE LINE/.test(sk), 'stake offset shown: ' + sk);
    await shot(p1, '7-phone-stake.png');
    await p1.click('#stakeClose');
    await app(p1, () => document.querySelector('#navCard').hidden);
    await p1.evaluate(() => { const b = document.createElement('button'); b.dataset.act = 'nav'; b.dataset.kind = 'pt'; b.dataset.id = 'L1NE'; document.body.appendChild(b); b.click(); b.remove(); });
    await sleep(800);
    const nd = await p1.textContent('#navDist');
    check(/ft/.test(nd), 'navigation distance shown: ' + nd);
    await shot(p1, '8-phone-nav.png');
    await p1.click('#navClose');

    // ---------------------------------------------------------------- status + settings
    console.log('7. status and settings');
    await tab(p1, 'status');
    await sleep(1200);
    const rx = await p1.textContent('#stRx');
    check(/Simulated receiver/.test(rx), 'status shows the simulated receiver');
    await shot(p1, '9-phone-status.png');
    await tab(p1, 'settings');
    await p1.waitForSelector('#cfSrc');
    check(await p1.inputValue('#cfSrc') === 'test', 'settings form shows the test source');
    await shot(p1, '10-phone-settings.png');
    // the phone (remote) can't change settings — but over 127.0.0.1 both are local; check public config hides the password
    const cfgHasPass = await app(p1, () => JSON.stringify(PRTK.app.bridge.cfg).includes('"pass":""'));
    check(cfgHasPass, 'browser never receives the NTRIP password');

    // ---------------------------------------------------------------- settings: unsaved edits survive broadcasts; save works
    await p1.fill('#cfGga', '7');
    await sleep(1600);                                                   // status/config traffic arrives meanwhile
    check(await p1.inputValue('#cfGga') === '7', 'unsaved settings edit is not wiped by live updates');
    check(/Unsaved/.test(await p1.textContent('#cfDirty')), 'unsaved-changes marker shown');
    await p1.click('[data-act="cfgsave"]');
    await p1.waitForFunction(() => PRTK.app.bridge.cfg.ntrip.ggaInterval === 7, null, { timeout: 5000 });
    const saved = JSON.parse(fs.readFileSync(path.join(run, 'rtk_config.json'), 'utf8'));
    check(saved.ntrip.ggaInterval === 7 && saved.source.type === 'test', 'settings saved to the laptop\'s rtk_config.json');
    await p1.click('[data-act="findbases"]');
    await p1.waitForFunction(() => /could not|failed|No mountpoints|km/.test((document.getElementById('cfBases') || {}).textContent || ''), null, { timeout: 30000 }).catch(() => {});
    console.log('    find bases (no internet here):', (await p1.textContent('#cfBases')).trim().slice(0, 100));

    // ---------------------------------------------------------------- a phone on the Wi-Fi is read-only for settings
    // (this sandbox can't reach its own LAN address, so mark this page as a remote viewer; the bridge-side refusal is in test_bridge.py)
    await app(p1, () => { PRTK.app.bridge.hello.local = false; PRTK.app.renderPane('settings', 'force'); });
    check(/only be changed on the laptop/.test(await p1.textContent('[data-pane="settings"]')), 'remote viewer sees read-only laptop settings');
    await app(p1, () => { PRTK.app.bridge.hello.local = true; PRTK.app.renderPane('settings', 'force'); });

    // ---------------------------------------------------------------- units switch
    await p1.selectOption('[data-pane="settings"] select[data-key="units"]', 'm');
    await sleep(500);
    const edgeM = await p1.textContent('#edgeText');
    check(/ m to line/.test(edgeM), 'units switch to metres: ' + edgeM);
    await p1.selectOption('[data-pane="settings"] select[data-key="units"]', 'ft');

    // ---------------------------------------------------------------- the laptop goes away and comes back
    console.log('8. laptop link drops; edits made meanwhile still arrive');
    await p1.click('#pClose');
    br.kill();
    await p1.waitForFunction(() => /Lost the connection/.test(document.querySelector('#banner').textContent), null, { timeout: 15000 });
    check(true, 'banner says the laptop connection was lost');
    const nBefore = await app(p1, () => PRTK.app.proj.savedPoints.length);
    await p1.click('#btnSave');
    await p1.fill('#svNote', 'saved while offline');
    await p1.click('#svBtn');
    check(await app(p1, () => PRTK.app.pending.length) === 1, 'offline edit is kept as pending');
    await shot(p1, '8b-phone-offline.png');
    await startBridge();
    await p1.waitForFunction(() => PRTK.app.bridge.connected && PRTK.app.pending.length === 0, null, { timeout: 20000 });
    const disk = JSON.parse(fs.readFileSync(path.join(run, 'rtk_data', 'project.json'), 'utf8'));
    check(disk.savedPoints.length === nBefore + 1 && disk.savedPoints.some(p => p.note === 'saved while offline'), 'after reconnecting, the offline point is on the laptop');
    await p1.waitForFunction(() => { const b = document.querySelector('#banner'); return b.hidden || !/Lost the connection/.test(b.textContent); }, null, { timeout: 5000 });
    check(true, 'banner cleared after reconnecting');

    // ---------------------------------------------------------------- desktop layout
    console.log('9. desktop layout');
    const d = await newPage(browser, { viewport: { width: 1366, height: 800 } }, false);
    await d.goto(URL);
    await d.waitForFunction(() => /IN|OUT|ON LINE/.test(document.querySelector('#verdict .v-big').textContent), null, { timeout: 10000 });
    await sleep(1500);
    await shot(d, '11-desktop-main.png');
    await tab(d, 'calib');
    await sleep(600);
    await shot(d, '12-desktop-calib.png');
    // tap a spot on the map
    await d.click('#btnMenu');
    await d.mouse.click(500, 450);
    await sleep(500);
    const pop = await d.$('.leaflet-popup-content');
    check(!!pop, 'tapping the map opens a spot popup');
    if (pop) console.log('    spot:', (await pop.textContent()).replace(/\s+/g, ' ').slice(0, 140));
    await shot(d, '13-desktop-spot.png');

    // ---------------------------------------------------------------- file:// (no bridge)
    console.log('10. opened as a file');
    const fp = await newPage(browser, { viewport: { width: 390, height: 844 } }, false);
    await fp.goto('file://' + path.join(REPO, 'web', 'index.html'));
    await sleep(1500);
    const fv = await app(fp, () => ({ big: document.querySelector('#verdict .v-big').textContent, banner: document.querySelector('#banner').textContent, mode: PRTK.app.bridgeMode }));
    check(fv.mode === false && fv.big === 'SET UP', 'file mode starts in SET UP: ' + JSON.stringify(fv));
    await tab(fp, 'calib');
    await fp.click('[data-pane="calib"] [data-act="adjust"]');
    await sleep(500);
    const fk = await app(fp, () => PRTK.app.sol.kind);
    check(fk === 'rough', 'place the outline by hand in file mode');
    await shot(fp, '14-file-mode.png');

    // ---------------------------------------------------------------- demo walk (no receiver in file mode)
    await app(fp, () => { PRTK.app.stopAdjust(); PRTK.app.startDemo(); });
    await sleep(2500);
    const demo = await app(fp, () => ({ src: PRTK.app.live.src, big: document.querySelector('#verdict .v-big').textContent, n: PRTK.app.trackCount() }));
    check(demo.src === 'demo' && /IN|OUT|ON LINE/.test(demo.big) && demo.n > 2, 'demo walk drives the live view ' + JSON.stringify(demo));
    await app(fp, () => PRTK.app.stopDemo());

    // ---------------------------------------------------------------- full calibration against a known truth
    console.log('11. two-monument calibration against a known truth, then IN / OUT / ON LINE');
    const r = await fp.evaluate(() => {
      const A = PRTK.app, C = PRTK.calib, G = PRTK.geo, M = A.model;
      A.stopAdjust();
      const truth = C.placeAt(0, 0, 39.4970, -105.3050, 2640, 1.2);
      A.edit({ op: 'set', path: ['rough'], value: C.placeAt(10, -5, 39.4970, -105.3050, 2640, 0.7) });   // hand placement ~3 m / 0.5° off
      const geoOf = (e, n) => C.local2geo(truth, e, n);
      const feed = (ll, n) => { for (let i = 0; i < n; i++) A.onNmeaLines(A.fakeNmea(ll.lat + (Math.random() - 0.5) * 2e-8, ll.lon + (Math.random() - 0.5) * 2e-8, 2640, 4, 0.012), 'test'); };
      const out = {};
      const measure = id => {
        A.openMeasure(id);
        feed(geoOf(M.points[id].e, M.points[id].n), 12);
        A.measureAction('mstop');
        const prev = A.measure.preview;
        A.measureAction('msave');
        return { level: prev.level, text: prev.html.replace(/<[^>]+>/g, '') };
      };
      out.m1 = measure('TPB'); out.kind1 = A.sol.kind;
      out.m2 = measure('LPNE'); out.kind2 = A.sol.kind;
      out.dist = A.sol.distCheck;
      out.offL1SE = G.distBearing(A.gm.pts.L1SE, geoOf(M.points.L1SE.e, M.points.L1SE.n)).dist;
      out.offP1NE = G.distBearing(A.gm.pts.P1NE, geoOf(M.points.P1NE.e, M.points.P1NE.n)).dist;
      // stand 1 ft inside, 1 ft outside, and 0.05 ft from Lot 1's west line (W16 -> TPB; Lot 1 lies to its right)
      const W = M.points.W16, T = M.points.TPB, L = Math.hypot(T.e - W.e, T.n - W.n);
      const d = { e: (T.e - W.e) / L, n: (T.n - W.n) / L }, rgt = { e: d.n, n: -d.e }, mid = { e: (W.e + T.e) / 2, n: (W.n + T.n) / 2 };
      const at = ft => { feed(geoOf(mid.e + rgt.e * ft, mid.n + rgt.n * ft), 2); const w = A.live.where; return { status: w.status, inOwned: w.inOwned, dFt: w.edge.d * A.FT, seg: w.edge.segId, sigFt: w.sigma * A.FT }; };
      out.in1 = at(1.0); out.out1 = at(-1.0); out.on = at(0.05);
      out.verdict = document.querySelector('#verdict .v-big').textContent;
      // exports produce valid files
      const files = {};
      const dl = A.download; A.download = (name, text) => { files[name.split('.').pop()] = text; };
      ['csv', 'geojson', 'kml'].forEach(f => A.exportData(f));
      A.download = dl;
      const gj = JSON.parse(files.geojson);
      out.exports = { csvLines: files.csv.trim().split('\r\n').length, gjFeatures: gj.features.length, gjPolys: gj.features.filter(f => f.geometry.type === 'Polygon').length,
                      kmlPlacemarks: (files.kml.match(/<Placemark>/g) || []).length, kmlOk: new DOMParser().parseFromString(files.kml, 'application/xml').getElementsByTagName('parsererror').length === 0 };
      return out;
    });
    console.log('   ', JSON.stringify(r));
    check(r.kind1 === 'one' && r.kind2 === 'fit', 'one monument -> one, two -> fit');
    check(r.m2.level === 'ok' && /\+?0\.0[0-9] ft/.test(r.m2.text), 'second monument preview reports the distance check: ' + r.m2.text.slice(0, 120));
    check(Math.abs(r.dist.diffFt) < 0.02, 'measured TPB->LPNE matches the documents (' + r.dist.diffFt.toFixed(4) + ' ft)');
    check(r.offL1SE < 0.01 && r.offP1NE < 0.01, 'outline lands on the truth (L1SE ' + r.offL1SE.toFixed(4) + ' m, P1NE ' + r.offP1NE.toFixed(4) + ' m)');
    check(r.in1.status === 'IN' && r.in1.inOwned === 'LOT1' && Math.abs(r.in1.dFt - 1) < 0.02, '1 ft inside Lot 1 -> IN at 1.00 ft');
    check(r.out1.status === 'OUT' && Math.abs(r.out1.dFt - 1) < 0.02, '1 ft outside -> OUT');
    check(r.on.status === 'ONLINE', '0.05 ft from the line -> ON LINE (2σ = ' + (2 * r.on.sigFt).toFixed(3) + ' ft)');
    check(r.exports.gjPolys === 3 && r.exports.kmlOk && r.exports.csvLines >= 23, 'exports: CSV/GeoJSON/KML well-formed ' + JSON.stringify(r.exports));
    await sleep(400);
    await shot(fp, '15-file-fit.png');
  } catch (e) {
    failures++;
    console.log('  FAIL exception: ' + (e.stack || e));
  } finally {
    await browser.close();
    br.kill();
    await sleep(500);
    try { fs.rmSync(run, { recursive: true, force: true }); } catch (e) { /* still in use */ }
  }
  console.log('\nbrowser errors: ' + (errors.length ? '\n  ' + errors.join('\n  ') : 'none'));
  if (errors.length) failures++;
  if (/Traceback/.test(blog)) { failures++; console.log('bridge traceback:\n' + blog); }
  console.log(failures ? `\n${failures} FAILURE(S)` : '\nALL PASSED');
  process.exit(failures ? 1 : 0);
})();
