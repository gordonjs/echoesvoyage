#!/usr/bin/env python3
"""PianoJuke: MIDI jukebox for a PianoDisc SilentDrive HD grand piano.

Serves a tablet UI and a JSON API (port 8088), plays the MIDI files in ~/midi
through mido, and can run an endless no-repeat rotation of the library.

    python3 pianojuke.py            run the jukebox (systemd unit "pianojuke")
    python3 pianojuke.py --routes   print the HTTP routes as JSON; touches no MIDI

Every stop, skip and piece-to-piece transition lifts the pedals and silences
every note before anything else happens.
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import random
import re
import signal
import sys
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import mido
from flask import Flask, Response, jsonify, request, send_from_directory

log = logging.getLogger("pianojuke")

# --------------------------------------------------------------- configuration

APP_DIR = Path(__file__).resolve().parent
MIDI_DIR = Path(os.environ.get("PIANOJUKE_MIDI_DIR", APP_DIR / "midi"))
WEB_DIR = Path(os.environ.get("PIANOJUKE_WEB_DIR", APP_DIR / "pianojuke_web"))
STATE_FILE = Path(os.environ.get("PIANOJUKE_STATE", APP_DIR / "pianojuke_state.json"))
DURATION_CACHE = Path(os.environ.get("PIANOJUKE_DURATIONS", APP_DIR / ".pianojuke_durations.json"))
# Substring of the mido output port name ("... 24:0"). "fake" records messages
# in memory and "none" discards them; both are for testing without a piano.
MIDI_PORT = os.environ.get("PIANOJUKE_PORT", "24:0")
HOST = "0.0.0.0"
PORT = int(os.environ.get("PIANOJUKE_HTTP_PORT", "8088"))

MIN_VELOCITY = 20      # floor so scaled-down notes still strike the string
MAX_VELOCITY = 127
PEDALS = (64, 66, 67)  # sustain, sostenuto, soft: passed through, lifted on every stop
PIANO_KEYS = range(21, 109)
SETTLE_SECONDS = 1.0   # silence after pedals-up/all-notes-off before a new piece starts
RESUME_DELAY = 20.0    # after a restart, let USB-MIDI settle before rotation resumes
RESCAN_EVERY = 600.0   # pick up files midicurate.py added, at most this often
HISTORY_LIMIT = 50
SAVE_EVERY = 2.0       # coalesce state writes (volume drags) to spare the SD card

DEFAULT_SETTINGS = {
    "volume": 70,             # percent; scales note velocities live
    "gap_seconds": 10,        # silence between pieces
    "favorite_weight": 2,     # favorites may play up to this many times per cycle
    "quiet_enabled": True,    # rotation does not start pieces in this window
    "quiet_start": "22:00",
    "quiet_end": "08:00",
    "night_enabled": False,   # cap note velocity in this window
    "night_start": "20:00",
    "night_end": "08:00",
    "night_ceiling": 70,
}


def _int_in(lo, hi):
    def convert(value):
        if isinstance(value, bool):
            raise ValueError("must be a number")
        number = int(round(float(value)))
        if not lo <= number <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return number
    return convert


def _bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "true", "on", "yes"):
        return True
    if text in ("0", "false", "off", "no"):
        return False
    raise ValueError("must be true or false")


def _hhmm(value):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError("must be a time like 22:00")
    return f"{int(m[1]):02d}:{m[2]}"


SETTING_TYPES = {
    "volume": _int_in(0, 100),
    "gap_seconds": _int_in(0, 600),
    "favorite_weight": _int_in(1, 5),
    "quiet_enabled": _bool,
    "quiet_start": _hhmm,
    "quiet_end": _hhmm,
    "night_enabled": _bool,
    "night_start": _hhmm,
    "night_end": _hhmm,
    "night_ceiling": _int_in(MIN_VELOCITY, MAX_VELOCITY),
}


def in_window(now, start, end):
    """True if `now` falls in [start, end), where the window may cross midnight."""
    t = now.hour * 60 + now.minute
    s, e = (int(x[:2]) * 60 + int(x[3:]) for x in (start, end))
    if s == e:
        return False
    return s <= t < e if s < e else (t >= s or t < e)


def atomic_write_json(path, data):
    """Write JSON so a power cut leaves either the old file or the new one."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def fold(text):
    """Lowercase and strip accents, for sorting and search."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


# --------------------------------------------------------------------- library

# midicurate.py names files "Composer - Title (year).mid".
NAME_RE = re.compile(r"^(?P<composer>.+?)\s+-\s+(?P<title>.+?)(?:\s*\((?P<year>\d{4})\))?$")
MIDI_EXTS = (".mid", ".midi")


class Track:
    __slots__ = ("id", "path", "composer", "title", "year", "duration", "sort_key")

    def __init__(self, tid, path):
        self.id = tid
        self.path = path
        self.duration = None
        m = NAME_RE.match(path.stem)
        if m:
            self.composer, self.title, self.year = m["composer"].strip(), m["title"].strip(), m["year"]
        else:
            self.composer, self.title, self.year = "Unknown", path.stem, None
        self.sort_key = (composer_key(self.composer), fold(self.title), self.year or "")

    def info(self):
        return {"id": self.id, "composer": self.composer, "title": self.title,
                "year": int(self.year) if self.year else None,
                "duration": round(self.duration, 1) if self.duration else None}


def composer_key(name):
    """Sort composers by surname: "Johann Sebastian Bach / Ferruccio Busoni" -> bach."""
    primary = name.split(" / ")[0].strip()
    parts = primary.split()
    return (fold(parts[-1]) if parts else "", fold(primary))


def midi_length(path):
    return mido.MidiFile(str(path)).length


class Library:
    def __init__(self, root, cache_path=None):
        self.root = Path(root)
        self.cache_path = cache_path
        self.tracks = {}
        self.version = 0
        self.scanned_at = 0.0
        self._lengths = self._load_cache()   # id -> [mtime_ns, size, seconds or None]
        self._cache_dirty = False
        self._cache_lock = threading.Lock()

    def _load_cache(self):
        if not self.cache_path:
            return {}
        try:
            data = json.loads(Path(self.cache_path).read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_cache(self):
        with self._cache_lock:
            if not (self.cache_path and self._cache_dirty):
                return
            try:
                atomic_write_json(Path(self.cache_path), dict(self._lengths))
                self._cache_dirty = False
            except OSError as e:
                log.warning("Could not save duration cache: %s", e)

    def _cached_length(self, tid, st):
        entry = self._lengths.get(tid)
        if isinstance(entry, list) and len(entry) == 3 and entry[:2] == [st.st_mtime_ns, st.st_size]:
            return True, entry[2]
        return False, None

    def scan(self):
        """Re-read the MIDI folder. Returns True if the set of pieces changed."""
        found = {}
        if self.root.is_dir():
            for path in sorted(self.root.rglob("*")):
                rel = path.relative_to(self.root)
                if path.suffix.lower() not in MIDI_EXTS or any(p.startswith(".") for p in rel.parts):
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                if not path.is_file():
                    continue
                tid = rel.as_posix()
                track = self.tracks.get(tid) or Track(tid, path)
                known, length = self._cached_length(tid, st)
                if known:
                    track.duration = length
                elif track.duration is not None:
                    track.duration = None   # file changed since we measured it
                found[tid] = track
        else:
            log.warning("MIDI folder %s does not exist", self.root)
        changed = set(found) != set(self.tracks)
        self.tracks = found
        self.scanned_at = time.monotonic()
        if changed:
            self.version += 1
            log.info("Library: %d pieces in %s", len(found), self.root)
        return changed

    def ids(self):
        return list(self.tracks)

    def get(self, tid):
        return self.tracks.get(tid)

    def remember_length(self, tid, seconds):
        track = self.tracks.get(tid)
        if track is None:
            return
        try:
            st = track.path.stat()
        except OSError:
            return
        track.duration = seconds
        with self._cache_lock:
            self._lengths[tid] = [st.st_mtime_ns, st.st_size, None if seconds is None else round(seconds, 2)]
            self._cache_dirty = True

    def needs_length(self):
        out = []
        for track in list(self.tracks.values()):
            try:
                known, _ = self._cached_length(track.id, track.path.stat())
            except OSError:
                continue
            if not known:
                out.append(track)
        return out

    def resolve(self, ref):
        """Find a piece by id, file name, or (unique) part of its name."""
        ref = str(ref).strip()
        if ref in self.tracks:
            return self.tracks[ref]
        path = Path(ref)
        if path.is_absolute():
            try:
                rel = path.relative_to(self.root).as_posix()
                if rel in self.tracks:
                    return self.tracks[rel]
            except ValueError:
                pass
        name = fold(path.name)
        stem = fold(path.stem) if path.suffix.lower() in MIDI_EXTS else name
        for track in self.tracks.values():
            if fold(track.path.name) == name or fold(track.path.stem) == stem:
                return track
        hits = [t for t in self.tracks.values() if stem and stem in fold(t.path.stem)]
        return hits[0] if len(hits) == 1 else None


# -------------------------------------------------------------------- rotation

class Rotation:
    """Endless shuffle that never repeats a piece until the whole library has played.

    A cycle ends once every eligible piece has played at least once. Favorites hold
    `weight` tickets per cycle instead of one, so they tend to come up sooner and may
    come round again in the same cycle, but never within len(pool) // weight picks of
    their last play. A new cycle never opens with something from the last few picks.
    Every piece that starts (rotation, queue or manual) counts as played.
    """

    RECENT_GUARD = 25

    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.enabled = False
        self.cycle = 1
        self.picks = 0
        self.counts = {}   # id -> plays this cycle
        self.last = {}     # id -> pick number of its latest play

    def load(self, data):
        if not isinstance(data, dict):
            return
        self.enabled = bool(data.get("enabled", False))
        self.cycle = int(data.get("cycle", 1) or 1)
        self.picks = int(data.get("picks", 0) or 0)
        self.counts = {str(k): int(v) for k, v in (data.get("counts") or {}).items()}
        self.last = {str(k): int(v) for k, v in (data.get("last") or {}).items()}

    def dump(self):
        return {"enabled": self.enabled, "cycle": self.cycle, "picks": self.picks,
                "counts": self.counts, "last": self.last}

    @staticmethod
    def pool(ids, never):
        return [t for t in ids if t not in never]

    def complete(self, pool):
        return all(self.counts.get(t, 0) for t in pool)

    def played(self, pool):
        if self.complete(pool):
            return len(pool)
        return sum(1 for t in pool if self.counts.get(t, 0))

    def choose(self, pool, favorites=(), weight=1, exclude=()):
        """Pick the next piece without recording it. None only if everything is excluded."""
        if not pool:
            return None
        counts = {} if self.complete(pool) else self.counts
        n = len(pool)
        weight = max(1, int(weight))
        spacing = max(1, n // weight)
        guard = min(self.RECENT_GUARD, n // 2)
        fresh, recent = [], []
        for t in pool:
            if t in exclude:
                continue
            plays = counts.get(t, 0)
            left = (weight if t in favorites else 1) - plays
            if left <= 0:
                continue
            last = self.last.get(t)
            since = None if last is None else self.picks - last
            if plays and since is not None and since < spacing:
                continue
            (recent if since is not None and since < guard else fresh).append((t, left))
        candidates = fresh or recent
        if not candidates:
            rest = [t for t in pool if t not in exclude]
            return self.rng.choice(rest) if rest else None
        return self.rng.choices([t for t, _ in candidates], weights=[w for _, w in candidates])[0]

    def record(self, tid, pool):
        if pool and self.complete(pool):
            self.new_cycle()
        self.picks += 1
        self.counts[tid] = self.counts.get(tid, 0) + 1
        self.last[tid] = self.picks

    def new_cycle(self):
        self.cycle += 1
        self.counts = {}


# ------------------------------------------------------------------ MIDI output

def panic_messages(held=(), channels=()):
    """Pedals up, every sounding note off, all-notes-off on every channel."""
    msgs = [mido.Message("control_change", channel=ch, control=cc, value=0)
            for ch in range(16) for cc in PEDALS]
    msgs += [mido.Message("note_off", channel=ch, note=note, velocity=0) for ch, note in sorted(held)]
    msgs += [mido.Message("control_change", channel=ch, control=123, value=0) for ch in range(16)]
    # Not every receiver honours CC123, so sweep the keyboard on the channels in use.
    msgs += [mido.Message("note_off", channel=ch, note=note, velocity=0)
             for ch in sorted(set(channels) or {0}) for note in PIANO_KEYS]
    return msgs


class MidiOut:
    """The piano's output port. Reopens it if the USB interface drops out."""

    REOPEN_EVERY = 3.0

    def __init__(self, match):
        self.match = match
        self.port = None
        self.name = None
        self.error = None
        self.fallback = False
        self._lock = threading.Lock()
        self._next_try = 0.0

    @property
    def connected(self):
        return self.port is not None

    def _open(self):
        if time.monotonic() < self._next_try:
            return False
        self._next_try = time.monotonic() + self.REOPEN_EVERY
        try:
            names = mido.get_output_names()
            name = next((n for n in names if self.match in n), None)
            fallback = name is None
            if fallback:
                name = next((n for n in names if "through" not in n.lower()), None)
            if name is None:
                raise OSError(f"no MIDI output matching {self.match!r} (found {names})")
            self.port = mido.open_output(name)
            self.name, self.error, self.fallback = name, None, fallback
            if fallback:
                log.warning("No MIDI port matching %r; using %s", self.match, name)
            log.info("MIDI output open: %s", name)
            return True
        except Exception as e:  # rtmidi raises a variety of types
            if str(e) != self.error:
                log.error("MIDI output unavailable: %s", e)
            self.port, self.error = None, str(e)
            return False

    def connect(self):
        with self._lock:
            return self.port is not None or self._open()

    def send(self, msg):
        with self._lock:
            if self.port is None and not self._open():
                return False
            try:
                self.port.send(msg)
                return True
            except Exception as e:
                log.error("MIDI send failed: %s", e)
                self.error = str(e)
                try:
                    self.port.close()
                except Exception:
                    pass
                self.port = None
                return False

    def panic(self, held=(), channels=()):
        for msg in panic_messages(held, channels):
            if not self.send(msg):
                break

    def close(self):
        with self._lock:
            if self.port is not None:
                try:
                    self.port.close()
                except Exception:
                    pass
                self.port = None


class FakeOut(MidiOut):
    """Records what would have gone to the piano."""

    def __init__(self, match="fake"):
        super().__init__(match)
        self.name = "fake"
        self.sent = collections.deque(maxlen=200_000)

    @property
    def connected(self):
        return True

    def connect(self):
        return True

    def send(self, msg):
        self.sent.append(msg)
        return True

    def close(self):
        pass


class NullOut(FakeOut):
    def send(self, msg):
        return True


def open_output(spec):
    if spec == "fake":
        return FakeOut()
    if spec == "none":
        return NullOut("none")
    out = MidiOut(spec)
    out.connect()
    return out


# -------------------------------------------------------------------- playback

class Playback:
    """Plays one MIDI file on its own thread.

    Velocities go through `velocity` as each note is sent, so volume and the night
    ceiling apply live. However the thread exits (end of piece, stop, error), it
    lifts the pedals and silences every note on the way out.
    """

    def __init__(self, out, path, velocity, settle=SETTLE_SECONDS, on_end=None):
        self.out = out
        self.path = path
        self.velocity = velocity
        self.settle = settle
        self.on_end = on_end
        self.duration = None
        self.started_at = None
        self.result = None   # "finished" | "stopped" | "error"
        self.error = None
        self._stop = threading.Event()
        self.done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="playback", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self, timeout=10.0):
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                log.error("Playback thread did not stop within %ss", timeout)

    def elapsed(self):
        if self.started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self.started_at)

    def _run(self):
        held, channels = set(), set()
        try:
            events = list(mido.MidiFile(str(self.path)))   # merged tracks, times in seconds
            self.duration = sum(m.time for m in events)
            self.out.panic()   # whatever happened before, start from pedals up and silence
            if self._stop.wait(self.settle):
                self.result = "stopped"
                return
            t0 = time.monotonic()
            self.started_at = t0
            at = 0.0
            for msg in events:
                at += msg.time
                delay = t0 + at - time.monotonic()
                if (delay > 0 and self._stop.wait(delay)) or self._stop.is_set():
                    self.result = "stopped"
                    return
                if msg.is_meta:
                    continue
                if msg.type == "note_on" and msg.velocity > 0:
                    held.add((msg.channel, msg.note))
                    channels.add(msg.channel)
                    self.out.send(msg.copy(velocity=self.velocity(msg.velocity)))
                elif msg.type in ("note_on", "note_off"):
                    held.discard((msg.channel, msg.note))
                    self.out.send(msg)
                elif msg.type == "control_change" and msg.control in PEDALS:
                    self.out.send(msg)
            self.result = "finished"
        except Exception as e:
            self.result, self.error = "error", str(e)
            log.error("Could not play %s: %s", self.path.name, e)
        finally:
            self.out.panic(held, channels)
            self.done.set()
            if self.on_end:
                self.on_end()


# --------------------------------------------------------------------- jukebox

class SettingsError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


class StateStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self):
        try:
            data = json.loads(self.path.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.error("State file %s unreadable (%s); starting fresh", self.path, e)
            try:
                self.path.rename(self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}"))
            except OSError:
                pass
            return {}

    def save(self, data):
        with self._lock:
            atomic_write_json(self.path, data)


class Jukebox:
    """Owns what plays when. One controller thread makes every transition.

    phase: "playing"  a piece is on (including the settle second before its first note)
           "gap"      between pieces; the next one starts at next_at
           "quiet"    rotation is on but quiet hours hold it back
           "idle"     nothing playing and nothing scheduled
    """

    def __init__(self, out, library, store, *, settle=SETTLE_SECONDS,
                 resume_delay=RESUME_DELAY, clock=datetime.now, rng=None):
        self.out = out
        self.library = library
        self.store = store
        self.settle = settle
        self.clock = clock
        self.settings = dict(DEFAULT_SETTINGS)
        self.favorites = set()
        self.never = set()
        self.queue = []
        self.history = []
        self.rotation = Rotation(rng)
        self.playback = None
        self.current = None      # {"id", "source", "started"}
        self.phase = "idle"
        self.next_at = None
        self.gap_from = None
        self.resuming = False
        self.last_error = None
        self.ceiling = MAX_VELOCITY
        self.rev = 0             # bumps on every change the UI may want to refetch
        self._upcoming = None
        self._dirty = False
        self._saved_at = 0.0
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._halt = threading.Event()
        self._lengths_wake = threading.Event()
        self._threads = []
        self._restore(resume_delay)

    # --- persistence

    def _restore(self, resume_delay):
        data = self.store.load()
        for key, value in (data.get("settings") or {}).items():
            if key in SETTING_TYPES:
                try:
                    self.settings[key] = SETTING_TYPES[key](value)
                except (TypeError, ValueError):
                    log.warning("Ignoring saved setting %s=%r", key, value)
        self.favorites = {t for t in data.get("favorites", []) if isinstance(t, str)}
        self.never = {t for t in data.get("never", []) if isinstance(t, str)}
        self.queue = [t for t in data.get("queue", []) if isinstance(t, str)]
        self.history = [h for h in data.get("history", []) if isinstance(h, dict)][-HISTORY_LIMIT:]
        self.rotation.load(data.get("rotation"))
        self._update_ceiling()
        if self.rotation.enabled:
            self.phase, self.resuming = "gap", True
            self.gap_from = time.monotonic()
            self.next_at = self.gap_from + resume_delay
            log.info("Rotation was on before the restart; resuming in %.0fs", resume_delay)

    def _snapshot(self):
        return {
            "version": 1,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "settings": self.settings,
            "favorites": sorted(self.favorites),
            "never": sorted(self.never),
            "queue": self.queue,
            "rotation": self.rotation.dump(),
            "history": self.history,
        }

    def _changed(self):
        """Something persistent changed: save soon, invalidate the rotation preview."""
        self.rev += 1
        self._upcoming = None
        self._dirty = True
        self._wake.set()

    def flush(self, force=True):
        with self._lock:
            if not self._dirty or (not force and time.monotonic() - self._saved_at < SAVE_EVERY):
                return
            data = json.loads(json.dumps(self._snapshot()))
            self._dirty = False
            self._saved_at = time.monotonic()
        try:
            self.store.save(data)
        except OSError as e:
            log.error("Could not save state: %s", e)
            self._dirty = True

    # --- threads

    def start(self):
        self.out.panic()   # clean slate after a crash or power cut
        for target, name in ((self._loop, "controller"), (self._measure_lengths, "lengths")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def shutdown(self):
        """Stop the piano for a service stop or reboot. Rotation stays as it was."""
        self._halt.set()
        self._wake.set()
        self._lengths_wake.set()
        with self._lock:
            if self.playback is not None:
                self.playback.stop()
                self._finish("shutdown")
            else:
                self.out.panic()
        self.flush()
        self.library.save_cache()
        self.out.close()

    def _loop(self):
        while not self._halt.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            if self._halt.is_set():
                break
            try:
                with self._lock:
                    self._tick()
            except Exception:
                log.exception("Controller tick failed")
            self.flush(force=False)

    def _measure_lengths(self):
        """Fill in piece lengths for the browser, only while the piano is not playing."""
        while not self._halt.is_set():
            for track in self.library.needs_length():
                while self.phase == "playing" and not self._halt.is_set():
                    self._halt.wait(5)
                if self._halt.is_set():
                    return
                try:
                    length = midi_length(track.path)
                except Exception as e:
                    log.warning("Unreadable MIDI file %s: %s", track.path.name, e)
                    length = None
                self.library.remember_length(track.id, length)
            self.library.save_cache()
            self._lengths_wake.wait()
            self._lengths_wake.clear()

    # --- the controller

    def quiet_now(self):
        s = self.settings
        return s["quiet_enabled"] and in_window(self.clock(), s["quiet_start"], s["quiet_end"])

    def night_now(self):
        s = self.settings
        return s["night_enabled"] and in_window(self.clock(), s["night_start"], s["night_end"])

    def _update_ceiling(self):
        self.ceiling = self.settings["night_ceiling"] if self.night_now() else MAX_VELOCITY

    def velocity(self, v):
        scaled = round(v * self.settings["volume"] / 100)
        return max(MIN_VELOCITY, min(self.ceiling, scaled))

    def _tick(self):
        self._update_ceiling()
        pb = self.playback
        if self.phase == "playing" and pb is not None and pb.done.is_set():
            self._finish(pb.result)
            self._after_piece()
        if self.phase == "gap" and time.monotonic() >= self.next_at:
            self._advance()
        elif self.phase in ("idle", "quiet") and self.rotation.enabled:
            self._advance()

    def _pool(self):
        return self.rotation.pool(self.library.ids(), self.never)

    def _rotation_next(self):
        pool = self._pool()
        exclude = set(self.queue)
        if self.current:
            exclude.add(self.current["id"])
        pick = self._upcoming
        if pick is None or pick not in self.library.tracks or pick in self.never or pick in exclude:
            pick = self.rotation.choose(pool, self.favorites, self.settings["favorite_weight"], exclude)
            self._upcoming = pick
        return pick

    def _advance(self):
        """Start whatever comes next: the queue first, then rotation."""
        self.resuming = False
        while self.queue:
            tid = self.queue.pop(0)
            self._changed()
            if tid in self.library.tracks:
                return self._start(tid, "queue")
            log.warning("Queued piece %s is no longer in the library", tid)
        if self.rotation.enabled:
            if self.quiet_now():
                if self.phase != "quiet":
                    log.info("Quiet hours: rotation waits until %s", self.settings["quiet_end"])
                self.phase, self.next_at = "quiet", None
                return
            if time.monotonic() - self.library.scanned_at > RESCAN_EVERY:
                self._rescan()
            tid = self._rotation_next()
            if tid is not None:
                return self._start(tid, "rotation")
            if self.phase != "idle":
                log.warning("Rotation has nothing to play (empty library or everything is never-play)")
        self.phase, self.next_at = "idle", None

    def _start(self, tid, source):
        track = self.library.tracks[tid]
        self._halt_piece("replaced")
        self.rotation.record(tid, self._pool())
        self.playback = Playback(self.out, track.path, self.velocity, self.settle, self._wake.set)
        self.playback.start()
        self.current = {"id": tid, "source": source, "started": time.time()}
        self.history.append({"id": tid, "composer": track.composer, "title": track.title,
                             "source": source,
                             "started": datetime.now().isoformat(timespec="seconds"),
                             "ended": None})
        del self.history[:-HISTORY_LIMIT]
        self.phase, self.next_at = "playing", None
        log.info("Playing [%s] %s", source, tid)
        self._changed()

    def _finish(self, reason):
        pb = self.playback
        if pb is not None and pb.result == "error":
            reason = "error"
            self.last_error = {"id": self.current["id"] if self.current else None,
                               "message": pb.error, "at": time.time()}
        if self.current:
            if pb is not None and pb.result == "finished" and pb.duration:
                self.library.remember_length(self.current["id"], pb.duration)
            if self.history and self.history[-1]["id"] == self.current["id"] and not self.history[-1]["ended"]:
                self.history[-1]["ended"] = reason
            log.info("Ended (%s): %s", reason, self.current["id"])
        self.current, self.playback = None, None
        self._changed()

    def _halt_piece(self, reason):
        """Stop the current piece. Its thread lifts the pedals and silences every note."""
        if self.playback is not None:
            self.playback.stop()
            self._finish(reason)
            return True
        return False

    def _after_piece(self):
        if self.queue or self.rotation.enabled:
            self.phase = "gap"
            self.gap_from = time.monotonic()
            self.next_at = self.gap_from + self.settings["gap_seconds"]
        else:
            self.phase, self.next_at = "idle", None

    def _rescan(self):
        if self.library.scan():
            self._changed()
            self._lengths_wake.set()

    # --- actions (all safe to call from request threads)

    def play(self, tid, source="manual"):
        with self._lock:
            if tid not in self.library.tracks:
                raise KeyError(tid)
            self._start(tid, source)

    def play_random(self):
        with self._lock:
            pool = self._pool() or self.library.ids()
            exclude = {self.current["id"]} if self.current else set()
            tid = self.rotation.choose(pool, self.favorites, self.settings["favorite_weight"], exclude)
            if tid is not None:
                self._start(tid, "manual")
            return tid

    def stop(self):
        """Silence the piano and switch rotation off."""
        with self._lock:
            if self.rotation.enabled:
                log.info("Rotation off (stop)")
            self.rotation.enabled = False
            if not self._halt_piece("stopped"):
                self.out.panic()
            self.phase, self.next_at, self.resuming = "idle", None, False
            self._changed()

    def skip(self):
        with self._lock:
            if not self._halt_piece("skipped"):
                self.out.panic()
            if self.queue or self.rotation.enabled:
                self._advance()
            else:
                self.phase, self.next_at = "idle", None
            self._changed()

    def panic(self):
        with self._lock:
            self.out.panic()

    def set_rotation(self, enabled):
        with self._lock:
            enabled = bool(enabled)
            if enabled != self.rotation.enabled:
                log.info("Rotation %s", "on" if enabled else "off")
            self.rotation.enabled = enabled
            if enabled and self.phase == "idle":
                self._advance()
            elif not enabled:
                self.resuming = False
                if self.phase == "quiet" or (self.phase == "gap" and not self.queue):
                    self.phase, self.next_at = "idle", None
            self._changed()

    def reset_cycle(self):
        with self._lock:
            self.rotation.new_cycle()
            self._changed()

    def set_volume(self, value):
        with self._lock:
            self.settings["volume"] = max(0, min(100, int(round(float(value)))))
            self._changed()
            return self.settings["volume"]

    def update_settings(self, changes):
        clean, errors = {}, {}
        for key, value in changes.items():
            if key not in SETTING_TYPES:
                errors[key] = "unknown setting"
                continue
            try:
                clean[key] = SETTING_TYPES[key](value)
            except (TypeError, ValueError) as e:
                errors[key] = str(e) or "invalid value"
        if errors:
            raise SettingsError(errors)
        with self._lock:
            self.settings.update(clean)
            if "gap_seconds" in clean and self.phase == "gap" and not self.resuming:
                self.next_at = self.gap_from + clean["gap_seconds"]
            self._update_ceiling()
            if clean:
                log.info("Settings: %s", clean)
            self._changed()
            return dict(self.settings)

    def set_flags(self, tid, favorite=None, never=None):
        with self._lock:
            if tid not in self.library.tracks:
                raise KeyError(tid)
            if favorite is not None:
                (self.favorites.add if favorite else self.favorites.discard)(tid)
            if never is not None:
                (self.never.add if never else self.never.discard)(tid)
                if never:
                    self.queue = [t for t in self.queue if t != tid]
            self._changed()
            return self.track_info(tid)

    def queue_add(self, tid, position="end"):
        with self._lock:
            if tid not in self.library.tracks:
                raise KeyError(tid)
            if position == "next":
                self.queue.insert(0, tid)
            else:
                self.queue.append(tid)
            self._changed()
            if self.phase in ("idle", "quiet"):
                self._advance()   # an idle jukebox starts on the first coin
                return True
            return False

    def queue_remove(self, index, tid=None):
        with self._lock:
            if tid is not None and not (0 <= index < len(self.queue) and self.queue[index] == tid):
                index = self.queue.index(tid) if tid in self.queue else -1
            if not 0 <= index < len(self.queue):
                raise IndexError(index)
            self.queue.pop(index)
            self._changed()

    def queue_move(self, src, dst):
        with self._lock:
            if not (0 <= src < len(self.queue) and 0 <= dst < len(self.queue)):
                raise IndexError(src)
            self.queue.insert(dst, self.queue.pop(src))
            self._changed()

    def queue_clear(self):
        with self._lock:
            self.queue.clear()
            if self.phase == "gap" and not self.rotation.enabled:
                self.phase, self.next_at = "idle", None
            self._changed()

    def rescan(self):
        with self._lock:
            changed = self.library.scan()
            if changed:
                self._changed()
                self._lengths_wake.set()
            return changed

    # --- views

    def track_info(self, tid):
        track = self.library.tracks.get(tid)
        if track is None:
            return None
        info = track.info()
        info["favorite"] = tid in self.favorites
        info["never"] = tid in self.never
        return info

    def rotation_status(self):
        with self._lock:
            pool = self._pool()
            return {"enabled": self.rotation.enabled, "cycle": self.rotation.cycle,
                    "played": self.rotation.played(pool), "total": len(pool),
                    "favorites": len(self.favorites), "never": len(self.never)}

    def status(self):
        with self._lock:
            self._update_ceiling()
            s = self.settings
            track = self.track_info(self.current["id"]) if self.current else None
            pb = self.playback
            elapsed = pb.elapsed() if pb else 0.0
            duration = (pb.duration if pb and pb.duration else None) or (track or {}).get("duration")
            remaining = max(0.0, duration - elapsed) if duration else None
            next_in = max(0.0, self.next_at - time.monotonic()) if self.phase == "gap" else None

            up_next = None
            if self.queue and self.queue[0] in self.library.tracks:
                up_next = dict(self.track_info(self.queue[0]), source="queue")
            elif self.rotation.enabled:
                tid = self._rotation_next()
                if tid:
                    up_next = dict(self.track_info(tid), source="rotation")

            quiet, night = self.quiet_now(), self.night_now()
            if self.phase == "gap":
                message = ("Resuming rotation after restart" if self.resuming
                           else f"Next piece in {int(next_in + 0.99)}s")
            elif self.phase == "quiet":
                message = f"Quiet hours until {s['quiet_end']}; rotation continues then"
            elif self.phase == "idle":
                message = ("Rotation is on but has nothing to play"
                           if self.rotation.enabled else "Stopped")
            else:
                message = ""
            if not self.out.connected:
                message = "Piano MIDI port not connected" + (f". {message}" if message else "")

            return {
                "state": self.phase,
                "text": f"{track['composer']} - {track['title']}" if track else "",
                "composer": track["composer"] if track else None,
                "title": track["title"] if track else None,
                "year": track["year"] if track else None,
                "track": track,
                "source": self.current["source"] if self.current else None,
                "running": bool(pb and pb.started_at is not None),
                "elapsed": round(elapsed, 1),
                "duration": round(duration, 1) if duration else None,
                "remaining": round(remaining, 1) if remaining is not None else None,
                "next_in": round(next_in, 1) if next_in is not None else None,
                "resuming": self.resuming,
                "up_next": up_next,
                "queue_length": len(self.queue),
                "volume": s["volume"],
                "velocity_ceiling": self.ceiling,
                "min_velocity": MIN_VELOCITY,
                "quiet_active": quiet,
                "quiet_until": s["quiet_end"] if quiet else None,
                "night_active": night,
                "rotation": self.rotation_status(),
                "midi": {"port": self.out.name, "connected": self.out.connected,
                         "fallback": self.out.fallback, "error": self.out.error},
                "message": message,
                "error": self.last_error,
                "library_version": self.library.version,
                "rev": self.rev,
                "server_time": self.clock().strftime("%H:%M"),
            }

    def queue_view(self):
        with self._lock:
            items = []
            for i, tid in enumerate(self.queue):
                info = self.track_info(tid) or {"id": tid, "title": tid, "composer": "Missing file"}
                items.append(dict(info, index=i))
            nxt = None
            if self.rotation.enabled:
                tid = self._rotation_next()
                nxt = self.track_info(tid) if tid else None
            return {"queue": items, "rotation_enabled": self.rotation.enabled,
                    "rotation_next": nxt, "history": self.history[::-1], "rev": self.rev}

    def library_view(self):
        with self._lock:
            tracks = sorted(self.library.tracks.values(), key=lambda t: t.sort_key)
            out = [dict(t.info(), favorite=t.id in self.favorites, never=t.id in self.never)
                   for t in tracks]
            counts = collections.Counter(t.composer for t in tracks)
            composers = [{"name": name, "count": counts[name]}
                         for name in sorted(counts, key=composer_key)]
            return {"version": self.library.version, "count": len(out),
                    "tracks": out, "composers": composers}

    def settings_view(self):
        with self._lock:
            return {"settings": dict(self.settings),
                    "min_velocity": MIN_VELOCITY, "max_velocity": MAX_VELOCITY,
                    "never": [self.track_info(t) for t in sorted(self.never) if t in self.library.tracks],
                    "library_count": len(self.library.tracks),
                    "midi_dir": str(self.library.root),
                    "server_time": self.clock().strftime("%H:%M"),
                    "rotation": self.rotation_status(),
                    "midi": {"port": self.out.name, "connected": self.out.connected,
                             "fallback": self.out.fallback, "error": self.out.error}}


# ------------------------------------------------------------------------- web

ACTION = ["GET", "POST"]   # Home Assistant's rest_command defaults to GET


def _params():
    data = request.args.to_dict()
    data.update(request.form.to_dict())
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        data.update(body)
    return data


def _pick(data, *names):
    for name in names:
        if data.get(name) not in (None, ""):
            return data[name]
    return None


def _error(message, code=400, **extra):
    return jsonify(dict(extra, error=message)), code


def _asset_version():
    try:
        return str(max(int(p.stat().st_mtime) for p in WEB_DIR.rglob("*") if p.is_file()))
    except ValueError:
        return "0"


def create_app(jb):
    app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="/static")
    try:
        app.json.sort_keys = False
    except AttributeError:
        app.config["JSON_SORT_KEYS"] = False

    def status():
        return jsonify(jb.status())

    # --- tablet UI

    @app.get("/")
    def index():
        html = (WEB_DIR / "index.html").read_text("utf-8").replace("__V__", _asset_version())
        return Response(html, mimetype="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(WEB_DIR, "manifest.webmanifest",
                                   mimetype="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        resp = send_from_directory(WEB_DIR, "sw.js", mimetype="text/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # --- playback

    @app.get("/api/nowplaying")
    def api_nowplaying():
        return status()

    @app.route("/api/play", methods=ACTION)
    def api_play():
        ref = _pick(_params(), "id", "file", "filename", "name", "path", "track", "title")
        if ref is None:
            if jb.play_random() is None:
                return _error("the library is empty", 404)
            return status()
        track = jb.library.resolve(ref)
        if track is None:
            return _error(f"no piece matches {ref!r}", 404)
        jb.play(track.id)
        return status()

    @app.route("/api/stop", methods=ACTION)
    def api_stop():
        jb.stop()
        return status()

    @app.route("/api/skip", methods=ACTION)
    def api_skip():
        jb.skip()
        return status()

    @app.route("/api/panic", methods=ACTION)
    def api_panic():
        jb.panic()
        return jsonify({"ok": True})

    @app.route("/api/volume", methods=ACTION)
    def api_volume():
        value = _pick(_params(), "volume", "value", "level", "v")
        if value is not None:
            try:
                jb.set_volume(value)
            except (TypeError, ValueError):
                return _error("volume must be a number from 0 to 100")
        return jsonify({"volume": jb.settings["volume"], "velocity_ceiling": jb.ceiling,
                        "min_velocity": MIN_VELOCITY})

    # --- rotation

    @app.route("/api/rotation", methods=["GET", "POST"])
    def api_rotation():
        if request.method == "POST":
            value = _pick(_params(), "enabled", "state", "on")
            if value is None:
                return _error('send {"enabled": true} or {"enabled": false}')
            try:
                enabled = (not jb.rotation.enabled) if str(value).lower() == "toggle" else _bool(value)
            except ValueError as e:
                return _error(f"enabled {e}")
            jb.set_rotation(enabled)
        return jsonify(jb.rotation_status())

    @app.route("/api/rotation/on", methods=ACTION)
    def api_rotation_on():
        jb.set_rotation(True)
        return status()

    @app.route("/api/rotation/off", methods=ACTION)
    def api_rotation_off():
        jb.set_rotation(False)
        return status()

    @app.route("/api/rotation/toggle", methods=ACTION)
    def api_rotation_toggle():
        jb.set_rotation(not jb.rotation.enabled)
        return status()

    @app.post("/api/rotation/reset")
    def api_rotation_reset():
        jb.reset_cycle()
        return jsonify(jb.rotation_status())

    # --- queue

    @app.get("/api/queue")
    def api_queue():
        return jsonify(jb.queue_view())

    @app.post("/api/queue")
    def api_queue_add():
        data = _params()
        track = jb.library.resolve(_pick(data, "id", "file", "name") or "")
        if track is None:
            return _error("no such piece", 404)
        started = jb.queue_add(track.id, "next" if data.get("position") == "next" else "end")
        return jsonify(dict(jb.queue_view(), started=started))

    @app.post("/api/queue/remove")
    def api_queue_remove():
        data = _params()
        try:
            jb.queue_remove(int(data.get("index", -1)), data.get("id"))
        except (TypeError, ValueError, IndexError):
            return _error("no such queue entry", 404)
        return jsonify(jb.queue_view())

    @app.delete("/api/queue/<int:index>")
    def api_queue_delete(index):
        try:
            jb.queue_remove(index)
        except IndexError:
            return _error("no such queue entry", 404)
        return jsonify(jb.queue_view())

    @app.post("/api/queue/move")
    def api_queue_move():
        data = _params()
        try:
            jb.queue_move(int(data["from"]), int(data["to"]))
        except (KeyError, TypeError, ValueError, IndexError):
            return _error("bad queue move")
        return jsonify(jb.queue_view())

    @app.post("/api/queue/clear")
    def api_queue_clear():
        jb.queue_clear()
        return jsonify(jb.queue_view())

    @app.get("/api/history")
    def api_history():
        return jsonify({"history": jb.queue_view()["history"]})

    # --- library, favorites, never-play

    @app.get("/api/library")
    def api_library():
        return jsonify(jb.library_view())

    @app.post("/api/library/rescan")
    def api_rescan():
        changed = jb.rescan()
        return jsonify({"changed": changed, "count": len(jb.library.tracks),
                        "version": jb.library.version})

    @app.post("/api/track")
    def api_track():
        data = _params()
        try:
            fav = None if data.get("favorite") is None else _bool(data["favorite"])
            never = None if data.get("never") is None else _bool(data["never"])
        except ValueError as e:
            return _error(str(e))
        try:
            return jsonify(jb.set_flags(str(data.get("id", "")), favorite=fav, never=never))
        except KeyError:
            return _error("no such piece", 404)

    # --- settings

    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings():
        if request.method == "POST":
            body = request.get_json(silent=True)
            changes = body if isinstance(body, dict) else request.form.to_dict()
            try:
                jb.update_settings(changes)
            except SettingsError as e:
                return _error(str(e), errors=e.errors)
        return jsonify(jb.settings_view())

    @app.errorhandler(404)
    @app.errorhandler(405)
    def api_errors(e):
        if request.path.startswith("/api/"):
            return _error(e.description, e.code)
        return e

    return app


def route_table(app):
    rules = [r for r in app.url_map.iter_rules() if r.endpoint != "static"]
    return sorted(({"path": r.rule, "methods": sorted(r.methods - {"HEAD", "OPTIONS"})} for r in rules),
                  key=lambda r: r["path"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--routes", action="store_true", help="print the HTTP routes as JSON and exit")
    args = parser.parse_args(argv)
    if args.routes:
        print(json.dumps(route_table(create_app(None)), indent=1))
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(threadName)s: %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)   # no line per status poll

    library = Library(MIDI_DIR, DURATION_CACHE)
    library.scan()
    jb = Jukebox(open_output(MIDI_PORT), library, StateStore(STATE_FILE))
    jb.start()

    def on_signal(signum, _frame):
        log.info("Shutting down (signal %d)", signum)
        jb.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    log.info("PianoJuke on http://%s:%d", HOST, PORT)
    create_app(jb).run(host=HOST, port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
