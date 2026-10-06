# CLAUDE.md — notes for whoever works on Parcel RTK next

## The user and the setup
- They own two adjoining parcels in Conifer, CO: the 35-ac tract (31652 Shadow Mountain Dr,
  deed Exhibit "A") and **Lodgepole Pines Lot 1** (plat Rec. F0236484, 1996). They are
  not a developer, so give step-by-step PowerShell instructions and explain the why briefly.
- Field kit:
  - a Windows laptop on Starlink;
  - a SparkFun RTK Surveyor (ZED-F9P) paired over **Bluetooth Classic SPP**, using the
    *outgoing* COM port (it was COM8);
  - corrections from **u-blox PointPerfect `NEAR-RTCM`**. SPARTN is ignored when injected
    over Bluetooth.
- They start the bridge with a local `start_rtk.bat`:
  `python rtk_bridge.py --source serial:COM8:115200 --ntrip "ntrip://…@ppntrip.services.u-blox.com:2101/NEAR-RTCM"`.
  **That command line must keep working.** It is saved to `rtk_config.json` on first run.

## Hard rules
- **The repo is public. Never commit credentials.**
  - NTRIP logins live only in the user's `rtk_config.json` (gitignored) and `start_rtk.bat`
    (not in the repo).
  - Tests use fake credentials.
  - Before committing, grep the diff for anything that looks like a real login.
- **The NTRIP password never goes to a browser.** Use `public_config()`; a blank password
  in `setConfig` keeps the saved one, and `clearPass` forgets it.
- **Tests must never touch the real `rtk_config.json` or `rtk_data/`.**
  - Bridge paths are resolved at call time.
  - `tests/test_bridge.py` redirects them to a temp dir.
  - `tests/browser_smoke.js` runs a temp copy.
- **Branch `claude/rtk-surveyor-map-app-qb9n84` is what the in-app updater downloads.**
  Pushing there ships to the user, so run all tests first.
- **Bump `VERSION` together in two places:** `rtk_bridge.py` (the updater compares it) and
  `A.VERSION` in `web/js/app-core.js` (a mismatch shows a "reload" banner).
- **Keep the app on `http://localhost:8000` by default.** That origin's localStorage holds
  version 1's data (key `parcelRTK`), which `state.migrateV1` imports. The bridge opens the
  user's *default* browser (as an `--app` window when it's Edge, Chrome or Brave) for the
  same reason.

## Layout
```
rtk_bridge.py      receiver link (serial/BT auto-detect, TCP, test), NTRIP client (v2, RTCM
                   CRC check, 1005 base decode), HTTP+WebSocket server (one port), project
                   store, tile cache, updater, Desktop launcher. Python stdlib + pyserial.
web/index.html     page shell (element ids used by app*.js)
web/app.css        field UI (dark, big targets; bottom sheet < 820 px, side panel above)
web/js/data.js     the recorded calls, transcribed verbatim + monument descriptions + errata
web/js/cogo.js     bearings, traverses (lines/curves), compass rule, polygon ops
web/js/geodesy.js  WGS84 <-> ECEF <-> local ENU (exact tangent plane)
web/js/model.js    builds one consistent parcel model from both documents (see below)
web/js/calib.js    georeference: rough / one / fit (weighted LSQ), hold-monuments field, σ
web/js/locate.js   IN / OUT / ON LINE, distance to your line, stake-out
web/js/nmea.js     NMEA parse (checksums enforced), σ from GST or by fix type
web/js/state.js    project state + op language (mirrored in rtk_bridge.py) + v1 migration
web/js/app-core.js helpers, store + bridge sync, WebSocket, live pipeline, demo, Web Serial
web/js/app-map.js  Leaflet layers, popups, tap-a-spot, hand-placement handles
web/js/app-panels.js  Status / Calibrate / Points / Tools / Settings, measure & save dialogs, exports
web/js/app.js      live rendering, the data-act dispatcher, start-up
web/vendor/        Leaflet 1.9.4, qrcode-generator 2.0.4 (MIT)
```
The math modules are UMD and run in node too, which is how the tests use them. The app
files are classic scripts sharing `PRTK.app`; no ES modules, so `file://` keeps working.

## How the data flows
- **Receiver → app:** receiver → bridge (`nmea` messages) → `A.onNmeaLines` →
  `locate.where` → render via rAF.
- **Project edits:** the app calls `A.edit(op)`.
  - The op is applied locally at once and kept in `A.pending` (persisted).
  - It is sent as `{t:'op'}`; on `ack`/`nack` it is dropped from pending.
  - The bridge broadcasts `state` to every viewer. A new base replays any still-pending ops,
    and replay failures are ignored because ops are idempotent enough.
- **First contact with a bridge:**
  - If the bridge is empty and this browser has data, send `replaceState`; that is how v1
    data reaches the laptop.
  - Otherwise adopt the bridge's state. A browser that never synced keeps its saved points.
- **Op language** (`set` / `del` / `push` / `remove` / `patch` by id): the JS and Python
  must agree. `tests/ops_vectors.json` runs against both.
- **WebSocket messages:**
  - Server → client: `hello`, `config`, `state`, `status` (1 Hz), `nmea`, replies by `id`.
  - Client → server: `op`, `replaceState`, `setConfig` (local only), `listPorts`,
    `findBases`, `checkUpdate`, `doUpdate` (local only), `getLog`, `ping`.
- **Local vs. remote:** a client counts as "local" when its socket is from 127.0.0.1.
  Phones on the LAN are viewers that can edit the project but not the settings.

## The survey model — how the two documents fit together (`model.js`)
- **Local frame:** US survey feet, origin at the shared corner **TPB** (Lot 1 NW = 35-ac SW
  corner), in the bearing basis of the 1996 Lodgepole Pines (LP) survey.
- **LP is held fixed.** It is field-measured and monumented. All 11 boundary calls close to
  0.0015 ft, the 13 curve checks agree within 0.005 ft, and the areas match the plat.
- **The 35-ac deed has a typo:** course 5, radius **340.57 → 348.57** (`data.deed.errata`).
  - The arc (97.83) and delta (16°04'50") require 348.57.
  - The delta is independently confirmed by the tangents on both sides.
  - As written the deed misses closing by 2.25 ft; corrected, 0.013 ft. The data stays
    verbatim and the correction is applied and reported.
- **Rotating the deed into the LP basis:**
  - The deed is kept rigid and rotated about TPB so its road corner lands on LP's surveyed
    road corner **LPNE**: −0°01'58".
  - That is the same record-vs-measured difference the plat shows for that line (−1'59").
  - The remaining 0.016 ft is spread by the compass rule.
- **Reported, not hidden:**
  - The deed's NW corner sits 0.7 ft off the straight extension of Lot 1's west line,
    because the documents differ by about 2'32" at TPB.
  - The LP road record bearings differ from the measured ones by a constant −6'47".
- **Segments are defined once and shared by rings.** P1 and LOT1 are owned; LP23 (Lots 2–3)
  is the neighbor. A segment's role is `boundary` (edge of owned land), `internal` (between
  owned parcels) or `neighbor`. "Distance to your line" only counts `boundary` segments.

## Geodesy, calibration, uncertainty
- **Geodesy:** ECEF → ENU tangent plane at the placement origin, so horizontal distances
  are ground distances at about 2,640 m ellipsoid height. A sea-level degree conversion
  would be 415 ppm short.
- **Why v1 looked about 6 ft off:** it scaled longitude with a cos(lat) shortcut, a 0.304%
  east-west error. `tests/test_core.js` documents it.
- **Placement:** `{origin, A, B, tx, ty, kind}` with `x = A·u − B·v + tx`,
  `y = B·u + A·v + ty`, where `u, v` are in metres.
  - `rough`: by hand.
  - `one`: one monument, with rotation borrowed from rough.
  - `fit`: two or more monuments, weighted least squares, rigid. Scale is optional and only
    for checking the documents.
  - **Hold monuments:** an inverse-distance residual field bends the outline through
    measured monuments.
  - Residual levels: ok < 0.3 ft, warn ≤ 1 ft, bad > 1 ft. With exactly two monuments,
    `distCheck` compares the measured distance with the documents.
- **Uncertainty:** `σ = √(σ_pos² + σ_cal² + σ_model²)`, and the verdict is ON LINE when the
  distance is under 2σ.
  - σ_pos comes from GST, or is estimated by fix type.
  - σ_rough defaults to 10 ft.
  - σ_model is per segment (deed 0.35 ft, LP 0.05 ft) and drops to 0 when both ends are
    measured and held.
- **Monument measurement** (`app-panels.js`):
  - Only RTK FIX samples are used, unless the user overrides.
  - It takes a robust mean and auto-finishes after 10 s once the spread is ≤ 3 cm, or at
    60 s.
  - The stored σ is `√(GST² + 1 cm² + spread²/n)`.
  - The correction source is recorded, so mixing services (which can mean different
    datums) is flagged.

## Tests — run all three before pushing
```
node tests/test_core.js                                   # math, model vs documents, ops, v1 migration
python -m unittest discover -s tests                      # bridge (no network; uses a pty for serial)
npm install --no-save playwright-core                     # once
CHROME=/path/to/chrome node tests/browser_smoke.js        # end-to-end: real bridge + headless Chromium
```
The browser test covers:
- v1 migration and two-device sync;
- an offline edit surviving a bridge restart;
- measure / save / stake / navigate;
- the settings form, file:// mode and the demo;
- a full two-monument calibration against a known truth: IN at 1 ft, OUT at 1 ft, ON LINE at
  0.05 ft.

Set `SHOTS=dir` to get screenshots. Read them before saying UI work is done.

## Known limits / ideas
- Phones reach the app over plain http on the LAN, so there's no screen wake lock,
  service worker or clipboard API there (a copy fallback exists).
- Web Serial (a direct receiver connection without the bridge) carries no corrections.
- Elevations are the receiver's MSL (its geoid model). The project stores ellipsoid height
  `h` with measurements.
## Future plan (next revision — the user will add more items)
- **Frozen-position warning.** If the receiver reports a valid fix but lat/lon stay identical
  (to 1e-8°) for ~30 s, show a banner: "Receiver may be in Base mode — set the switch to
  Rover." Real case (Oct 2026): the user's Surveyor has a physical Base/Rover switch; on Base
  it kept sending one fixed position (DGPS, HDOP 99.99) while corrections streamed fine, and
  never got RTK FIX. Add a Status-card hint as well and cover it in tests/browser_smoke.js.

- Possible next steps:
  - fence-line offsets (stake a line parallel to the boundary);
  - importing a surveyor's corner coordinates as control;
  - a per-device choice of map layer (it is shared in project settings now).
