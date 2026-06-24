# Parcel RTK — 31652 Shadow Mountain Drive, Conifer CO

A single-page app that takes **live data from your SparkFun RTK surveyor**, shows
your position on a map, and tells you in real time whether you are **inside or
outside your parcel** (APN 148669, Jefferson County, ~35 acres). The parcel
boundary is built from the recorded legal description (metes & bounds, including
the four curves along the county road). You georeference it to the real world by
standing on **two known corners** — the two-pin calibration.

Everything runs in the browser. Leaflet is embedded in `index.html`, so the
parcel outline and the in/out logic work **fully offline** — only the
satellite/street basemap tiles need internet.

---

## Quick start (Windows laptop — the easy path)

1. Copy this folder to the laptop. Make sure Python is installed
   (<https://www.python.org/>, tick *Add Python to PATH*).
2. Double-click **`serve.bat`**. It serves the app at
   `http://localhost:8000/index.html` and opens it in your default browser.
   Use **Chrome or Edge** (needed for USB/Web Serial).
3. Connect your receiver (see *Connecting your receiver* below).
4. Calibrate with two pins (see *Two-pin calibration*).
5. Walk the property — the banner shows **IN / OUT** and distance to the
   nearest boundary.

> **Why serve over localhost instead of opening the file?** Browsers only allow
> the USB/Web Serial API on a "secure context" (`https` or `localhost`).
> `serve.bat` provides that. Demo and Manual modes work from a plain file too.

### Android tablet
Chrome for Android supports USB (Web Serial) over a USB-OTG cable, but it still
needs a localhost server. The simplest reliable setup is the Windows laptop. If
you want the tablet, install **Termux**, run `bash serve.sh`, and open
`http://localhost:8000/index.html` in Chrome — or use the **Bridge** mode below
over Wi-Fi.

---

## Connecting your receiver

Open **⚙ Setup → Data source** and pick one:

| Mode | Use when | Notes |
|------|----------|-------|
| **USB Serial** | Receiver is plugged into the laptop's USB and presents NMEA on a COM port | Click *Connect USB*, pick the port, set the baud (SparkFun default is often **38400**; try 115200 if blank). Chrome/Edge only. |
| **Bridge (WS)** | **Bluetooth** or **TCP/Wi-Fi** NMEA | Run `rtk_bridge.py` (below), then connect to `ws://localhost:8765`. This is the most reliable path for Bluetooth. |
| **Demo** | No hardware — see how it behaves | Simulates a walk across the boundary with a temporary calibration. |
| **Manual** | Testing a specific coordinate | Type a lat/lon. |

The status bar shows fix type (**RTK FIX** / **RTK FLOAT** / DGPS / GPS),
satellite count, and horizontal accuracy (from `GST` if your receiver sends it,
otherwise estimated from HDOP). **Calibrate and stake with an RTK *Fixed*
solution** for centimetre accuracy.

### Bluetooth / TCP via the bridge

The SparkFun RTK streams NMEA over Bluetooth and (optionally) TCP, neither of
which a browser can read directly. `rtk_bridge.py` relays it over a WebSocket.

```bash
pip install pyserial          # only needed for serial/Bluetooth COM ports

# List serial / Bluetooth COM ports:
python rtk_bridge.py --list

# Bluetooth or USB COM port (Windows): pair the receiver first, note its
# "outgoing" COM port in Bluetooth settings, then:
python rtk_bridge.py --source serial:COM5:38400

# TCP NMEA server (receiver's IP/port, from the RTK firmware's TCP settings):
python rtk_bridge.py --source tcp:192.168.4.1:2948

# Synthetic data to test the app end-to-end (no hardware):
python rtk_bridge.py --source test
```

Then in the app: **Data source → Bridge (WS) → Connect bridge**
(`ws://localhost:8765`).

---

## Two-pin calibration (aligning the plat to the world)

The recorded deed gives the parcel shape and the bearings/distances between
corners, but **not** real-world lat/lon. Two measured corners fix that: they pin
down position, rotation, and scale (a similarity transform). This is exactly the
"use two pins to align it" you asked for.

1. Find a **physical monument** (rebar/pin/cap) at a known corner. The named
   corners available are **TPB** (True Point of Beginning / SW), **NW**, **NE
   (at the county road)**, and **S (at the county road)**.
2. Open **⚙ Setup → Two-pin calibration**. Under **Pin 1**, choose which corner
   you're standing on, then tap **⦿ Capture (avg 5s)** and hold the rover still
   over the mark. It averages 5 s of fixes.
3. Move to a **second, different** corner and repeat under **Pin 2**.
   Use two corners that are **far apart** (e.g. NW and S) for the best angular
   accuracy.
4. Tap **Apply calibration**. The parcel snaps onto the map and IN/OUT goes
   live.

**Calibration quality.** After applying, the panel shows:
- **Platted vs measured distance** between your two pins.
- **Scale error %** — how your measured pin distance compares to the deed.
  Under ±0.3 % is excellent; a large value usually means a wrong corner pick or
  a non-fixed solution.
- **Plat rotation** — the angle between plat bearings and true north.

Calibration and pins are saved in the browser, so you don't have to redo them
each visit (as long as you use the same browser and don't clear site data).

> Two pins assume the deed's internal geometry is accurate (it closes to ~2 ft
> over a 5,400 ft perimeter here — typical for the era). For legal boundary
> decisions, always rely on a licensed surveyor; this tool is for field
> orientation, not for setting binding corners.

---

## Using it in the field

- **IN / OUT banner** with live distance to the nearest boundary line.
- **Navigate to corner**: pick a corner under *Setup → Navigate to corner* to
  get a live distance, bearing, on-screen arrow, and a guide line on the map —
  handy for finding a buried pin.
- **Follow (◎)** keeps the map centred on you; tap once to free-pan.
- **Basemap**: Satellite (Esri), Streets (OSM), or None. Satellite/Streets need
  internet — **load the map over Wi-Fi before you head out** if the property has
  no signal; the parcel outline always draws regardless.
- **Track** breadcrumb and a raw **NMEA log** for troubleshooting.

---

## How the boundary is computed

`index.html` builds the polygon from the deed in `COURSES` (see the
*PARCEL GEOMETRY* section). Lines use quadrant bearings → azimuths; the four
right-of-way curves are densified from their radius, delta, and tangent
direction. The traverse closes to **2.25 ft** over a **5,425 ft** perimeter and
yields **~35.0 acres**, confirming the parsing. If you ever need to correct a
call, edit the `COURSES` array — it's plain, readable data.

## Files
- `index.html` — the whole app (Leaflet embedded; works offline).
- `rtk_bridge.py` — optional NMEA→WebSocket bridge for Bluetooth/TCP.
- `serve.bat` / `serve.sh` — start a localhost server and open the app.
