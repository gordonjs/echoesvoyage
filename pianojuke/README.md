# PianoJuke

MIDI jukebox for the PianoDisc SilentDrive HD on the Pi "PianoPlayer" (10.0.60.75).
A Flask app on port 8088 serves a tablet UI and a JSON API, and plays `~/midi` through
mido to the USB-MIDI port (`24:0`).

- **Tablet UI**: dark, landscape, large touch targets, no hover. Screens: Now Playing,
  Browse (composers and search), Queue (with recently played) and Settings. It has a PWA
  manifest for launching fullscreen from the home screen.
- **Rotation**: endless shuffle with no repeats until the whole library has played,
  a configurable gap, quiet hours, an optional night velocity ceiling, weighted favorites
  and a never-play list. State lives in `~/pianojuke_state.json`; if rotation was on
  when the Pi went down, it resumes 20 s after the service starts.
- **Safety**: every stop, skip and transition lifts sustain/sostenuto/soft on all
  16 channels, sends note-off for every sounding note plus CC123 on all channels, and
  sweeps note-offs across the keyboard. It also does this before each piece's first note
  and when the service starts or stops.

## Files

| Path | Goes to on the Pi |
|---|---|
| `pianojuke.py` | `~/pianojuke.py` (wherever the systemd unit runs it) |
| `pianojuke_web/` | `~/pianojuke_web/` (next to `pianojuke.py`) |
| `deploy/install.sh` | run from this checkout; checks, backs up, installs, verifies, rolls back |
| `deploy/verify.sh` | the curl checks; `--with-sound` and `--with-restart` exercise playback |
| `deploy/routecheck.py` | compares the installed app's Flask routes with the new one |
| `tests/` | pytest suite against a simulated piano (`PIANOJUKE_PORT=fake`) |
| `tools/make_icons.py` | regenerates the app icons (needs Pillow) |

## Install on the Pi

```bash
ssh admin@10.0.60.75
git clone -b claude/pianojuke-tablet-rotation-gweix4 https://github.com/gordonjs/echoesvoyage.git ~/pianojuke-src
cd ~/pianojuke-src/pianojuke
./deploy/install.sh --check     # dry run: tests, route comparison, MIN_VELOCITY carry-over
./deploy/install.sh             # install, restart, verify with curl, roll back on failure
./deploy/verify.sh --with-sound --with-restart   # plays ~20 s at volume 30, restarts the service
```

`install.sh` finds the script path and Python interpreter from the `pianojuke` unit's
`ExecStart`, so a virtualenv is used if the unit uses one. It then:

1. checks that `flask` and `mido` import, and runs the tests if pytest is installed;
2. lists every route the installed `pianojuke.py` serves and **refuses to install** if
   any would disappear or lose an HTTP method. Those routes are what Home Assistant
   calls. Add them to the new file (or use `--force` if nothing calls them);
3. copies `MIN_VELOCITY` from the installed file into the new one;
4. backs up to `~/pianojuke-backup-<timestamp>/`, installs, and restarts the service;
5. runs `verify.sh`. If the service doesn't stay up or a check fails, it restores the
   backup and restarts. `./deploy/install.sh --rollback` does the same by hand.

It needs nothing new from apt or pip: Flask and mido (with python-rtmidi) are what the
existing app already uses. Tested with Python 3.11 + Flask 2.2.2 + mido 1.2.10
(Raspberry Pi OS Bookworm) and Python 3.13 + Flask 3.1 + mido 1.3, against a simulated
MIDI port; it has not run on the Pi itself yet.

## The tablet

Open `http://10.0.60.75:8088/` on the tablet.

**Fullscreen from the home screen.** Chrome only *installs* web apps served over HTTPS,
and the Pi serves plain HTTP. On an Android tablet:

1. open `chrome://flags`, enable **Insecure origins treated as secure**, add
   `http://10.0.60.75:8088`, relaunch Chrome;
2. open the page, then **⋮ → Add to Home screen → Install**.

It then launches fullscreen and landscape from its icon. On an iPad, **Share → Add to
Home Screen** works over HTTP as is (standalone, with the status bar). Without either,
the page goes fullscreen on its first tap (switchable under Settings → This tablet), and
the **Full** button in the side rail toggles it.

The browser can't keep the screen awake over plain HTTP, so set the tablet's own
screen timeout. Other screens drift back to Now Playing after 3 idle minutes.

## Behaviour

- **Stop** silences the piano and switches rotation off (so it stays off after a
  reboot). **Skip** moves to the next queued piece, else the next rotation pick, with no
  gap. **Rotation off** lets the current piece finish and then stops.
- **Order**: the queue always plays first, then rotation. Adding to the queue while
  nothing plays starts it straight away.
- **No repeats**: a cycle ends once every piece not on the never-play list has played.
  Every piece that starts counts, whether from rotation, the queue, Browse or Home
  Assistant. Nothing comes back within 25 picks of its last play, even across a cycle
  boundary (a smaller window for very small libraries).
- **Favorites** get *weight* tickets per cycle (Settings → "Favorites per cycle",
  default 2): they come up sooner and may play again in the same cycle, but never within
  `library size / weight` picks of their last play. Set it to 1 for a strict shuffle.
- **Never-play** keeps a piece out of rotation and drops it from the queue. Playing it
  from Browse still works.
- **Quiet hours** (default 22:00–08:00, on) hold rotation; the current piece finishes
  and anything you start yourself still plays. **Night limit** (default off) caps note
  velocity for everything that plays in its window.
- **Volume** 0–100 scales velocities live. Notes never go below `MIN_VELOCITY` and never
  above the night ceiling while it applies.
- Quiet hours and the night limit use the Pi's clock and time zone (shown in Settings).

## Home Assistant

```yaml
rest_command:
  piano_rotation_on:  { url: "http://10.0.60.75:8088/api/rotation/on",  method: post }
  piano_rotation_off: { url: "http://10.0.60.75:8088/api/rotation/off", method: post }
  piano_skip:         { url: "http://10.0.60.75:8088/api/skip",         method: post }
  piano_stop:         { url: "http://10.0.60.75:8088/api/stop",         method: post }
  piano_volume:
    url: "http://10.0.60.75:8088/api/volume"
    method: post
    content_type: "application/json"
    payload: '{"volume": {{ volume }}}'

switch:
  - platform: rest
    name: Piano rotation
    resource: http://10.0.60.75:8088/api/rotation
    body_on: '{"enabled": true}'
    body_off: '{"enabled": false}'
    is_on_template: "{{ value_json.enabled }}"
    headers: { Content-Type: application/json }

sensor:
  - platform: rest
    name: Piano now playing
    resource: http://10.0.60.75:8088/api/nowplaying
    value_template: "{{ value_json.text if value_json.text else value_json.state }}"
    json_attributes: [state, composer, title, year, elapsed, remaining, volume, up_next, rotation, message]
    scan_interval: 15
```

## API

Action endpoints accept GET or POST (Home Assistant's `rest_command` defaults to GET).
Parameters can be JSON, form fields or query strings.

| Endpoint | Does |
|---|---|
| `GET /api/nowplaying` | status: `state` (`playing`/`gap`/`quiet`/`idle`), `text`, `composer`, `title`, `year`, `track`, `source`, `elapsed`, `duration`, `remaining`, `next_in`, `up_next`, `volume`, `velocity_ceiling`, `quiet_active`, `night_active`, `rotation`, `midi`, `message`, `error` |
| `/api/play` | play now: `id` (or `file`, `name`, `path`; a unique part of the file name works). No argument plays a random piece |
| `/api/stop` | stop, pedals up, all notes off, rotation off |
| `/api/skip` | next queued piece or rotation pick |
| `/api/panic` | pedals up and all notes off, nothing else |
| `/api/volume` | read, or set with `volume` 0–100 |
| `GET/POST /api/rotation` | `{"enabled": ...}`; POST `{"enabled": true|false|"toggle"}` |
| `/api/rotation/on`, `/off`, `/toggle` | same, answering with the full status |
| `POST /api/rotation/reset` | start a new cycle |
| `GET /api/queue` | queue, recently played, and the next rotation pick |
| `POST /api/queue` | add `id`, optional `"position": "next"` |
| `POST /api/queue/remove`, `/move`, `/clear`; `DELETE /api/queue/<i>` | edit the queue |
| `GET /api/library`, `POST /api/library/rescan` | pieces and composers; re-read `~/midi` |
| `POST /api/track` | `{"id", "favorite": bool, "never": bool}` |
| `GET/POST /api/settings` | `volume`, `gap_seconds`, `favorite_weight`, `quiet_enabled/start/end`, `night_enabled/start/end/ceiling` |
| `GET /api/history` | recently played |

## Development

```bash
pip install flask mido pytest
PIANOJUKE_PORT=fake python -m pytest -q tests
PIANOJUKE_PORT=fake PIANOJUKE_MIDI_DIR=/path/to/midi python pianojuke.py   # UI at :8088
python pianojuke.py --routes    # route table as JSON; opens no MIDI port
```

Environment overrides: `PIANOJUKE_PORT` (port-name substring, `fake` or `none`),
`PIANOJUKE_MIDI_DIR`, `PIANOJUKE_STATE`, `PIANOJUKE_WEB_DIR`, `PIANOJUKE_HTTP_PORT`.
