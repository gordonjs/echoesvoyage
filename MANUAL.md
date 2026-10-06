# Parcel RTK — Owner's Manual

Parcel RTK version 2 · SparkFun RTK Surveyor · Windows laptop

Parcel RTK tells you, live and to within about an inch, whether you are standing **IN** or
**OUT** of your property and how far you are from the line. It covers two parcels:

- the 35-acre tract at 31652 Shadow Mountain Dr;
- Lodgepole Pines Lot 1.

This manual takes you from a bare laptop to a calibrated system in the field. Do the parts
in order the first time. Later, use the **Daily checklist** (Part F).

> **Important:** Parcel RTK is a field tool, not a boundary survey. For anything legal (a
> sale, a dispute, building close to a line), hire a licensed surveyor.

---

## Contents

- Part A — Install on the laptop
- Part B — Set up the SparkFun RTK Surveyor
- Part C — Pair the Surveyor with the laptop
- Part D — Turn on RTK corrections (PointPerfect)
- Part E — Calibrate: lock the map to your monuments
- Part F — Daily checklist
- Part G — Using a phone as a second screen
- Part H — Updating
- Part I — Troubleshooting
- Quick reference card

---

## What you need

| Item | Notes |
|---|---|
| Windows 10 or 11 laptop | With Bluetooth. Internet (Starlink) is needed for corrections and new map areas. |
| SparkFun RTK Surveyor | Charged. A USB power bank is handy for long days. |
| Survey pole with a bubble level | For measuring monuments. |
| u-blox PointPerfect subscription | The **NTRIP / RTCM** kind. You need its username and password. |
| This app | Downloaded in Part A. |

---

# Part A — Install on the laptop

*About 15 minutes, done once.*

### A1. Install Python

1. Go to **python.org → Downloads** and download the latest **Python 3** for Windows.
2. Run the installer. On the first screen, **tick "Add python.exe to PATH"**, then click
   **Install Now**.
3. Check that it worked:
   1. Open **PowerShell**: press the Windows key, type `powershell`, press Enter.
   2. Type the following and press Enter:
      ```
      python --version
      ```
   3. You should see `Python 3.x.x`. If you see *"not recognized"*, rerun the installer,
      choose **Modify**, and make sure *Add to PATH* is ticked.

### A2. Install the serial-port helper

In PowerShell, type:
```
python -m pip install pyserial
```
Wait for *"Successfully installed"* (or *"Requirement already satisfied"*).

*Why:* this is the one add-on Parcel RTK needs to talk to Bluetooth COM ports.

### A3. Download Parcel RTK

1. Download this ZIP:
   **https://github.com/gordonjs/echoesvoyage/archive/refs/heads/claude/rtk-surveyor-map-app-qb9n84.zip**
2. In your Downloads folder, **right-click the ZIP → Properties**. If there is an
   **Unblock** checkbox at the bottom, tick it and click OK.
   *Why:* this stops Windows from flagging the files as "from the internet".
3. Right-click the ZIP → **Extract All…**

### A4. Put it in its permanent folder

**If you already have the old version:**

1. Open the extracted folder until you can see `rtk_bridge.py` and a folder called `web`.
2. Select **everything** in there and copy it.
3. Paste it into your existing Parcel RTK folder (the one with your `start_rtk.bat`).
4. Choose **Replace the files in the destination**.

Your saved points, calibration and `start_rtk.bat` are kept.

> Can't find your old folder? In PowerShell, run:
> ```
> Get-ChildItem -Path $HOME -Recurse -Filter rtk_bridge.py -ErrorAction SilentlyContinue | Select-Object FullName
> ```
> The folder it prints (without the `\rtk_bridge.py` at the end) is the one you want.

**If this is a fresh install:**

1. Make a folder `C:\ParcelRTK`.
2. Copy the contents of the extracted folder into it, so that `C:\ParcelRTK\rtk_bridge.py`
   exists.

### A5. First start

**If you have `start_rtk.bat` from the old version:** double-click it once.

- It contains your COM port and PointPerfect login, and Parcel RTK saves them into a file
  called `rtk_config.json` on this first run.
- After that you won't need the `.bat` any more.

**If you don't have it:**

1. In PowerShell, go to the folder:
   ```
   cd C:\ParcelRTK
   ```
2. Start Parcel RTK:
   ```
   python rtk_bridge.py
   ```

What you should see:

- **A black window** with lines like `Parcel RTK 2.0.0 … app: http://localhost:8000/`.
  This window *is* Parcel RTK; closing it stops everything.
- **The app opening** in its own browser window.
- **A firewall prompt**, if Windows shows one ("Allow Python to communicate…"). Allow it
  for **Private networks**. This is only needed if you want to use a phone (Part G).
- **A message about your old data**, if you used version 1:
  *"Brought over from the old version: … saved points, your hand placement — now saved on
  the laptop."*

### A6. Make a desktop icon

In PowerShell, in the Parcel RTK folder, run:
```
python rtk_bridge.py --install
```
A **Parcel RTK** icon appears on your desktop. **From now on, start the app with this
icon.**

*Why not `start_rtk.bat`?* The old .bat file repeats your settings on its command line
every time, which would undo any change you make later in the app's Settings screen.

> **If the app window shows the old-looking screen,** press **Ctrl+F5** once.

---

# Part B — Set up the SparkFun RTK Surveyor

*Done once. These settings stay in the Surveyor.*

The Surveyor has no screen; you change its settings through a text menu over Bluetooth.

### B1. Open the Surveyor's settings menu

1. **Close Parcel RTK** (close its black window). Only one program can use the Bluetooth
   port at a time.
2. Turn the Surveyor on. Its Bluetooth must already be paired with the laptop; if not, do
   **Part C** first, then come back.
3. Install a terminal program. **PuTTY** (putty.org) is free and simple.
4. In PuTTY, set:
   - **Connection type:** Serial
   - **Serial line:** your Surveyor's outgoing COM port, e.g. `COM8` (see C3 to find it)
   - **Speed:** 115200
5. Click **Open**. A black window opens; NMEA text may scroll by (lines starting with `$`).
6. **Press any key** (e.g. Enter). The scrolling stops and the main menu appears.

Menus are numbered. Type the number or letter shown, then Enter. **`x`** goes back and
eventually exits. **Settings save automatically when you exit.** Menu numbers differ
between firmware versions, so go by the *names* below.

### B2. Settings to check

| Menu → setting | Set it to | Why |
|---|---|---|
| **Bluetooth** (in the main or System menu) | **Classic** (SPP), not BLE | Windows makes COM ports only for Classic. |
| **Base / Rover** | **Rover** | Your Surveyor has a physical **Base/Rover switch**: it must be on **Rover**. On Base it reports one fixed position that never moves and never reaches RTK FIX. |
| **GNSS Receiver → Message rate / NMEA messages** | **GGA, RMC, GST** on (GSA and GSV may also be on) | GGA is your position, RMC your heading, **GST your real accuracy**. Without GST the app can only estimate accuracy. |
| **GNSS Receiver → Measurement rate** | **4 Hz** (0.25 s) | Smooth updates; 1 Hz also works. |
| **GNSS Receiver → Dynamic model** | **Pedestrian** or **Portable** | Either is fine for walking. |
| **NTRIP Client** (in the GNSS Receiver or Network menu) | **Disabled** | The laptop sends corrections over Bluetooth. Your Surveyor still has an old, expired PointOne login in here, which you don't need. |
| **Elevation mask, constellations** | Leave at the defaults | |

Type **`x`** until you are out of all menus, then close PuTTY.

> **Checking GST later:** in Parcel RTK, open **Menu → Status**. *Accuracy* should say
> **"(from receiver)"**. If it says "(estimated)", GST is off.

### B3. What the lights mean

| Light | Meaning |
|---|---|
| **Bluetooth** | Blinking: waiting for a connection. **Solid: Parcel RTK is connected.** |
| **PPS** (blinks once a second) | The receiver has a GPS fix. |
| **Horizontal accuracy** (100 cm / 10 cm / 1 cm) | Lower is better. **1 cm solid ≈ RTK FIX.** |

---

# Part C — Pair the Surveyor with the laptop

*Done once.*

### C1. Pair

1. Turn the Surveyor on (Bluetooth set to **Classic**, see B2).
2. On the laptop: **Settings → Bluetooth & devices → Add device → Bluetooth**.
3. Pick the Surveyor (named like `Surveyor Rover-XXXX`). If asked for a PIN, there is
   none; just accept.

### C2. Why there are two COM ports

Windows creates **two** COM ports for the Surveyor: an **outgoing** one and an
**incoming** one. Parcel RTK needs the **outgoing** port, and finds it by itself.

### C3. Find the ports (if you ever need to)

With Parcel RTK closed, run this in PowerShell in the app folder:
```
python rtk_bridge.py --list
```
Example output:
```
  COM8   Standard Serial over Bluetooth link   Bluetooth (outgoing - use this one)
  COM7   Standard Serial over Bluetooth link   Bluetooth (incoming - not this one)
```

### C4. Check the connection

1. Start Parcel RTK.
2. Open **Menu → Settings → Receiver connection** and choose **"Bluetooth or USB — find it
   automatically"**. Click **Save settings**.
3. Within about 30 seconds:
   - the Surveyor's **Bluetooth light goes solid**;
   - the top bar shows **Fix: GPS** and a satellite count;
   - **Menu → Status → Receiver** says **Connected**.

*Why automatic is best:* if Windows ever renumbers the port, Parcel RTK finds the new one
and remembers it.

---

# Part D — Turn on RTK corrections (PointPerfect)

Without corrections the Surveyor is good to about 3–6 ft. With them it's good to about
1 inch.

How it works: the laptop downloads correction data over Starlink and passes it to the
Surveyor over the same Bluetooth link. The Surveyor itself needs no Wi-Fi.

### D1. Get your login

1. Log in at **portal.thingstream.io**.
2. Go to **Location Services → your PointPerfect "Location Thing" → Credentials**.
3. Note the **NTRIP** details:

| Field | Value |
|---|---|
| Hostname | `ppntrip.services.u-blox.com` |
| Port | `2101` |
| Mountpoint | **`NEAR-RTCM`** |
| Username / Password | Shown in the portal |

> ⚠️ **Use `NEAR-RTCM`, not `NEAR-SPARTN`.** The Surveyor ignores SPARTN when it comes
> over Bluetooth, so you'd get "streaming" but never RTK FIX.

### D2. Enter it in the app

1. Open **Menu → Settings → Corrections (NTRIP)**.
2. Tick **Get RTK corrections over the internet**.
3. Set **Service** to **u-blox PointPerfect (NEAR-RTCM)**. This fills in the caster, port
   and mountpoint for you.
4. Type your **Username** and **Password**.
5. Click **Save settings**.

If you came from version 1, this is probably already filled in from your old launcher. In
that case the password box says *"saved on the laptop — leave blank to keep it"*.

*Your password stays in `rtk_config.json` on this laptop. It is never sent to the browser,
to a phone, or anywhere except PointPerfect.*

### D3. Check it works

1. Go outside with a clear view of the sky.
2. Open **Menu → Status → Corrections**. It should say **Streaming**, about 300–1,000
   bytes/s, Format **RTCM 3**.
3. Watch the top-bar **Fix** pill: **GPS → RTK FLOAT → RTK FIX**. This usually takes 30
   seconds to 3 minutes.
4. At RTK FIX, **Accuracy** shows about **±0.03–0.05 ft**.

If FIX doesn't come, see Part I.

---

# Part E — Calibrate: lock the map to your monuments

The parcel outlines come from your recorded deed and plat. **Calibration** pins them to
the real ground. There are three levels:

| Level | How | Good to |
|---|---|---|
| **By hand** | Drag the outline onto the satellite photo | ~10 ft |
| **1 monument** | Measure one pin | Position exact; rotation still by hand |
| **2+ monuments** | Measure two or more pins | ~0.1 ft — **the goal** |

If you used version 1, your hand placement comes over automatically. You can skip to E2.

### E1. Place the outline by hand (rough)

1. Open **Menu → Calibrate → Place the outline here** (or **Adjust by hand**).
2. Use the two handles that appear on the map:
   - drag **✥** to move the outline;
   - drag **⟳** to turn it.
3. Line the outline up with fences, the road and clearings. Use the **1°**, **0.1°** and
   **1 ft** buttons for fine moves.
4. Tap **Done**.

> Satellite photos are often off by 3–10 ft. Get it close, then let the monuments decide.

### E2. Measure the two key monuments

Measure these two first. Both appear on **both** of your documents, and they are 1,776 ft
apart, which gives the best accuracy.

| | Monument | What to look for |
|---|---|---|
| 1 | **Shared corner (TPB)** | Lot 1 NW / 35-ac SW corner. A **#4 rebar** (≈ ½"). One pin for both parcels. |
| 2 | **Road corner (LPNE)** | On the west edge of Shadow Mountain Dr. **½" rebar with a 1" plastic cap "LS 26296"**. |

For each monument:

1. Wait for **RTK FIX**.
2. Open **Menu → Calibrate** and tap **Go** next to the monument. An arrow and a distance
   guide you to it.
3. Within about 15 ft, the card shows **"Look for: …"**, a description of the monument.
   Search there; a metal detector helps with buried pins.
4. Put the pole tip in the **center** of the pin and level the bubble.
5. Tap **Measure this monument** (or **Measure** in Calibrate). **Hold still.** The
   counter climbs and it finishes by itself once the readings are steady, usually 10–20
   seconds.
6. Read the result, then tap **Save**:
   - **First monument:** it tells you how far the outline moves to sit on it.
   - **Second monument:** it compares the measured distance between the two pins with the
     documents. **±0.00–0.30 ft with a ✓ means you found the right pins.**

| Result | What it means |
|---|---|
| ✓ within 0.3 ft | Excellent. The outline is now locked to the ground. |
| Amber, 0.3–1 ft | Re-check that the pole is plumb on the pin's center, then **Measure again**. |
| Red, over 1 ft | Probably the wrong pin, a disturbed one, or an extra survey marker. Look around for another one. |
| "Position wandered…" | You moved during the measurement. Measure again and hold still. |

### E3. Add a third monument as a check (recommended)

With three, the app can tell you **which** pin disagrees if one is off. Either of these
works well:

- **Lot 1 SW (W16):** a **2" aluminum cap on a #6 rebar**, stamped
  `W1/16 S5 S8 1996 LS 26296`.
- **S¼ corner (S14):** a **brass plate set in concrete**, stamped LS 865 (also a USGS
  benchmark).

After measuring, **Menu → Calibrate** lists each monument's fit: green under 0.3 ft, amber
up to 1 ft, red over 1 ft.

### E4. Leave "Hold measured monuments" on

This option (in Calibrate) bends the outline very slightly so it passes **exactly**
through each pin you measured. That is how surveyors treat found monuments: the pins in
the ground win over numbers on paper.

---

# Part F — Daily checklist

1. Turn the **Surveyor** on, at the top of the pole.
2. Double-click the **Parcel RTK** desktop icon.
3. Wait for the Surveyor's **Bluetooth light to go solid** and the top bar to show **RTK
   FIX** (1–3 minutes outdoors).
4. Walk. The big box at top left tells you:

| Display | Meaning |
|---|---|
| **IN** (green) | On your land; it says which parcel. |
| **OUT** (red) | Off your land; it names the neighbor's lot if you're in one. |
| **ON LINE** (amber) | Too close to call within the current accuracy; it says which side is *likely*. Move a little, or wait for RTK FIX. |
| *grey / dim* | The position is old (no data for a few seconds). |

The second line shows:

- the **distance to your property line**, and which line;
- an **arrow** pointing to it;
- your **coordinates and elevation**.

The line *between* your two parcels doesn't count, because you own both sides.

5. **Save a spot:** tap **📍 Save**, type a note (e.g. "NE fence corner"), then **Save**.
   Tick *Average for 10 seconds* for the most accurate result.
6. **Finish:** close the black window, then turn off the Surveyor.

### Other tools (Menu → Tools)

- **Stake out a line.** Pick a line and the screen shows **◀ 2.35 ft** (move left) or
  **2.35 ft ▶** (move right). It says **ON THE LINE** when you're on it. Great for fence
  posts. You can also tap any line on the map.
- **Property-line alert.** Beeps when you come within a chosen distance of your line, and
  twice when you cross it.
- **Check any spot.** Tap anywhere on the map to see if it's inside your land and how far
  it is from the line.
- **Track.** Shows where you've walked.
- **Demo walk.** A simulated walk, for practicing indoors without the Surveyor.

### Map buttons (right side)

| Button | What it does |
|---|---|
| **◎** | Follow me. Dragging the map turns it off; tap to turn it back on. |
| **▦** | Change the map: Satellite / Topo / Streets / None. |
| **+ / −** | Zoom. |

### Map colors

| On the map | Meaning |
|---|---|
| Red line | The 35-acre tract. |
| Blue line | Lot 1. |
| Yellow dashed | The line between your two parcels. |
| Grey dashed | The neighbors' lines. |
| White dot | A monument described in the documents. |
| Grey dot | A corner nobody described. |
| Green dot | A monument you measured. |
| Purple dot | One of your saved points. |

### Points (Menu → Points)

All your saved spots are here, with **Go** (navigate to it), **Edit**, **Copy** and
**Delete**. **Export** saves them as:

- **CSV** for Excel;
- **KML** for Google Earth;
- **GeoJSON** for GIS programs.

### Offline maps

The laptop keeps every map tile you've looked at. To prepare for a day without internet,
pan and zoom around the property once while online. The parcel outlines always show, even
with no map.

---

# Part G — Using a phone as a second screen

1. The phone must be on the **same Wi-Fi** as the laptop.
2. On the laptop, open **Menu → Status → Phone or tablet** and scan the **QR code** with
   the phone's camera.
3. The phone shows the same live view and can save points too. Settings can only be
   changed on the laptop.

If the phone can't load the page, allow Python through the Windows firewall:

1. Open **Windows Security → Firewall & network protection → Allow an app through
   firewall**.
2. Tick **Python** under **Private**.
3. Make sure your Wi-Fi is set as a **Private** network.

Phones can't keep the screen awake for this kind of page, so set the phone's screen
timeout longer while you work.

---

# Part H — Updating

1. Open **Menu → Settings → Updates → Check for updates**.
2. If an update is offered, tap **Install update**.
3. Close the black window and start Parcel RTK again with the desktop icon.

Your settings, password, calibration and saved points are never touched by updates.

---

# Part I — Troubleshooting

| Problem | What to do |
|---|---|
| **"Receiver: Not found"** | Turn the Surveyor on and check its Bluetooth is **Classic** (B2). Stay within ~30 ft. Make sure PuTTY or u-center isn't open. |
| **"Port busy"** | Another program has the port: an old black window, PuTTY or u-center. Close it. Restarting the laptop clears it for certain. |
| **Bluetooth light keeps blinking** | Wait 30 seconds; the app retries by itself. Otherwise remove the Surveyor in Windows Bluetooth settings and pair again (C1). |
| **No COM ports at all** | The Surveyor isn't paired, or its Bluetooth is set to BLE. Set it to Classic (B2), then pair again (C1). |
| **Corrections: "Refused"** | Wrong username or password, the wrong mountpoint, or the subscription has lapsed. Re-enter them (D2) and check the Thingstream portal. |
| **Corrections streaming, but stuck on GPS** | The mountpoint is probably SPARTN. Use **NEAR-RTCM** (D1). |
| **Stuck on RTK FLOAT** | Move away from trees and buildings and give it 2–3 minutes. Hold the pole upright. |
| **"Can't reach Parcel RTK on the laptop"** | The black window was closed. Start it again from the desktop icon. |
| **Coordinates never change, stuck on DGPS** | The Surveyor's **Base/Rover switch** is on Base. Set it to **Rover**; within a minute or two you should see FLOAT, then FIX. |
| **Accuracy says "(estimated)"** | Turn on the GST message in the Surveyor (B2). |
| **Map is blank** | You're offline in an area you haven't viewed before (see Offline maps). The outline still works. |
| **Outline sits a few feet off the photo** | Normal; the photos themselves are off. Measure monuments (Part E). |
| **Old screen after updating** | Press **Ctrl+F5**. |
| **Something else** | Open **Menu → Status → Show bridge log** and send a photo of it. |

---

# Quick reference card

```
START     Surveyor on → Parcel RTK desktop icon → wait for RTK FIX
READ      IN / OUT / ON LINE · distance + arrow to your line
SAVE      📍 Save → note → Save
FIND PIN  Menu → Calibrate → Go → "Look for…" → Measure → Save
FENCE     Menu → Tools → Stake out a line  (◀ move left · ▶ move right)
PHONE     Menu → Status → scan the QR code (same Wi-Fi)
STOP      close the black window → Surveyor off

Surveyor settings: Rover · Bluetooth Classic · NMEA GGA+RMC+GST · 4 Hz · its own NTRIP client OFF
Corrections:       ppntrip.services.u-blox.com : 2101 / NEAR-RTCM
Measure first:     TPB (shared corner, #4 rebar) + LPNE (road corner, 1" plastic cap LS 26296)
Check with:        W16 (2" aluminum cap) or S14 (brass plate in concrete)
```
