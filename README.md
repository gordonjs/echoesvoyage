# Parcel RTK

Walk your land with a SparkFun RTK Surveyor and see, live and to the inch, whether you are
**IN** or **OUT** of your property, and how far you are from the line.

It covers both of your parcels in Conifer, CO:

- **31652 Shadow Mountain Dr**, the 35-acre tract from the Exhibit "A" legal description.
- **Lodgepole Pines Lot 1** (14.57 ac), from the recorded plat (Rec. F0236484, 1996).

The two share a corner (one rebar marks both), so a single calibration places them both.

> Field tool, not a boundary survey. For anything legal (a fence dispute, a sale, building
> close to a line) hire a licensed surveyor. This app shows you where the documents and
> your monuments put the lines. That is very good orientation, but it is not a survey.

---

## Install or update (Windows laptop)

1. **Python 3** from python.org (tick *Add Python to PATH*), then in PowerShell:
   `pip install pyserial`
2. **Download** the latest ZIP:
   <https://github.com/gordonjs/echoesvoyage/archive/refs/heads/claude/rtk-surveyor-map-app-qb9n84.zip>
   and extract it **over your existing Parcel RTK folder**, so that `rtk_bridge.py` and the
   `web` folder sit next to each other. Your `start_rtk.bat` is not in the ZIP and is not
   touched.
3. **Start it**: double-click your `start_rtk.bat` once. Its receiver and correction
   settings are saved to `rtk_config.json` on that first run. From then on all you need is
   `python rtk_bridge.py`.
4. **Desktop launcher** (optional): `python rtk_bridge.py --install` puts *Parcel RTK* on
   your desktop.

Later updates come from inside the app: **Menu → Settings → Updates → Install update**.
Your settings, calibration and saved points are never overwritten.

**Coming from the first version?** The first time the new app opens, it imports your old
hand placement and saved points from the browser and shows a message saying so. If the old
screen still appears, press **Ctrl+F5**.

One black window does everything. It talks to the receiver over Bluetooth, feeds it RTK
corrections over the laptop's internet (Starlink), serves the app, and keeps your project in
`rtk_data\project.json`. Closing that window stops everything.

## First time out

1. **Receiver.** Pair the Surveyor in Windows Bluetooth settings, with the receiver's
   Bluetooth set to *Classic*. Windows creates two COM ports, and the bridge finds the
   right one (the *outgoing* one) by itself.
2. **Corrections.** Open **Menu → Settings → Corrections**, choose *u-blox PointPerfect
   (NEAR-RTCM)*, and enter your username and password. *Status* should say **Streaming**,
   and the Fix pill goes FLOAT → **RTK FIX** within a minute or two of open sky.
3. **Put the outline on the map.** If you used the first version, your placement comes over
   automatically. Otherwise use **Calibrate → Place the outline here**: drag ✥ to move it,
   drag ⟳ to turn it, until it sits on the satellite photo. That gets you within about
   10 ft.
4. **Lock it to the ground with monuments.** Measure the two monuments that appear on both
   documents first:
   - **TPB**: the shared corner.
   - **LPNE**: the road corner, 1,776 ft away.

   To measure one:
   - Tap **Go**. An arrow and the distance lead you to it, and when you're close it tells
     you what to look for.
   - Set the pole on it with **RTK FIX** and tap **Measure**. It averages the position and
     finishes on its own once the readings are steady.
   - After the second monument it compares the distance you measured with the documents.
     A few hundredths of a foot means you found the right pins.
5. **Add a third as a check**: either the Lot 1 SW aluminum cap (W16) or the section-corner
   brass plate (S14). With three or more, the app shows which monument disagrees, if one
   does.

## Reading the screen

| What you see | Meaning |
|---|---|
| **IN** (green) | Inside one of your parcels; the line below says which. |
| **OUT** (red) | Outside; it names the neighbor's lot if you're in one. |
| **ON LINE** (amber) | Too close to the line to call, given the combined uncertainty of your fix and the outline. It also says which side you're *likely* on. |
| `23.41 ft to line · West line` | Distance to **your property line**, meaning the outer edge of everything you own. The arrow points to it. The line between your two parcels doesn't count, because you own both sides. |
| Fix / Accuracy / Sats / Corr. age | Receiver state. Accuracy comes from the receiver's GST message (marked `~` when estimated). |
| 📍 **Save** | Saves the spot with a note; you can average it for 10 s first. Your saved points are listed under **Menu → Points**, with CSV, KML (Google Earth) and GeoJSON export. |

On the map:

- **Solid lines** are your property lines: red for the 35-acre tract, blue for Lot 1.
- **Dashed yellow** is the line between your two parcels.
- **Grey dashed** lines are the neighbors' lines.
- **Monument dots** are white when described in the documents, grey when not, and green
  once measured.
- **Tap anywhere** on the map to see whether that spot is inside your land and how far it
  is from the line. You can also navigate to it or save it.

**Menu → Tools** has more:

- **Stake out a line.** Shows how far left or right of a line you are, which helps when
  setting fence posts.
- **Property-line alert.** Beeps (and vibrates a phone) when you get within a set distance
  of your line, and twice when you cross it.
- **Your track.**
- **Demo walk.** A simulated walk, for when no receiver is connected.

## The monuments

| Id | Corner | What's there (per the documents) |
|---|---|---|
| **TPB** | Shared corner: Lot 1 NW / 35-ac SW | Found #4 rebar (shown on the plat). One monument for both parcels. |
| **LPNE** | Road corner: 35-ac S / Lodgepole NE | ½" rebar with 1" plastic cap "LS 26296" (plat note 2), on the west right-of-way of Shadow Mountain Dr. |
| **W16** | Lot 1 SW (W 1/16 corner, Sec 5/8) | **2" aluminum cap on #6 rebar stamped "W1/16 S5 S8 1996 LS 26296"**. It replaced a found #3 rebar. |
| **L1NE**, **L1SE** | Lot 1 east corners | ½" rebar with 1" plastic cap "LS 26296" (plat note 2). |
| **S14** | S ¼ corner of Section 5 | Brass plate in concrete, LS 865; also a USGS benchmark. Very stable control. |
| **P1NW**, **P1NE** | 35-acre north corners | Not described in the deed; look for any pin or pipe. |
| ROW1–7, P1R1–7 | Curve points along the road | Shown when zoomed in. |

## Phone or tablet

**Menu → Status** shows a QR code. Scan it with a phone on the same Wi-Fi and the phone
gets the same live view, and can save points too. Settings stay on the laptop. If the phone
can't load the page, allow Python through the Windows firewall for *Private* networks.

Over Wi-Fi the phone can't hold its screen awake (browsers only allow that on secure
pages), so set its screen timeout longer while you work.

## Troubleshooting

| Symptom | Fix |
|---|---|
| *Receiver: Not found / Port busy* | Turn the receiver on and keep it within ~30 ft. Close u-center and any other copy of the bridge. |
| *Corrections: Refused* | Check the username, password and mountpoint. For PointPerfect use **NEAR-RTCM**: NEAR-SPARTN can't be used over Bluetooth. |
| Stuck on FLOAT | Corrections are arriving but the receiver needs a clearer sky. Give it a minute or two away from trees. |
| *Can't reach Parcel RTK on the laptop* | The black window was closed, or the phone is on a different network. |
| Blank map in the field | Map tiles are cached on the laptop. Pan around the property once while online and they work offline afterward. The outline itself always draws. |
| Outline looks a few feet off the photo | Normal: satellite photos are often off by 3–10 ft. Measured monuments are the real answer. |

## What the documents say

The geometry comes straight from the recorded calls (transcribed in `web/js/data.js`) and
was checked hard:

- **Lodgepole Pines plat**: all 11 boundary calls close to 0.0015 ft, every curve checks
  (arc, chord, tangency), and the areas match the plat (Lot 1: 14.575 vs 14.57 ac).
- **The 35-acre deed has a typo.** Course 5 says *radius 340.57*, but its arc (97.83) and
  delta (16°04'50") require **348.57**. The tangents on both sides confirm the delta. As
  written the deed misses closing by 2.25 ft; corrected, it closes to 0.013 ft. The app
  uses the corrected value and shows the reasoning under **Status → Survey documents**.
- **The two documents fit together** through their two shared monuments. The deed's
  bearings are about 1'58" off from the plat's measured bearings, which is the same
  record-vs-measured difference the plat itself shows. With that rotation applied, the deed
  lands on the surveyed road corner within 0.016 ft.
- **One honest disagreement.** The deed puts the 35-acre NW corner 0.7 ft off the straight
  extension of Lot 1's west line. Measuring that corner settles it.

**Why version 1 "looked a bit off":** it converted coordinates with a sea-level
shortcut that stretched east-west distances by 0.3% (about 6 ft across the property). It
also used the deed typo as written. Version 2 uses exact earth-centered geometry and ground
distances at your elevation.

## Files

| Path | What |
|---|---|
| `rtk_bridge.py` | The program you run (receiver link, corrections, web server, updater). |
| `web/` | The app. |
| `rtk_config.json` | Your settings, **including your NTRIP password**. It stays on this laptop: never uploaded, never sent to the browser, never overwritten by updates. |
| `rtk_data/` | Your project (calibration and saved points), map-tile cache, optional NMEA logs. |
| `tests/` | Automated tests (see `CLAUDE.md`). |

The first version is still available as
<https://github.com/gordonjs/echoesvoyage/archive/fe5d563.zip>.
