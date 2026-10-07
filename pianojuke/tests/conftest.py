import os
import sys
import time
from datetime import datetime
from pathlib import Path

import mido
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PIANOJUKE_PORT", "fake")

import pianojuke as pj  # noqa: E402


def write_midi(path, seconds=0.3, notes=(60, 64, 67), velocity=100, channel=0, pedal=True):
    """A short piece: sustain down, a few notes, sustain up."""
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))   # 1 beat = 0.5 s
    ticks = int(seconds / 0.5 * 480)
    step = max(1, ticks // (len(notes) + 1))
    if pedal:
        track.append(mido.Message("control_change", channel=channel, control=64, value=127, time=0))
    for n in notes:
        track.append(mido.Message("note_on", channel=channel, note=n, velocity=velocity, time=0))
        track.append(mido.Message("note_off", channel=channel, note=n, velocity=0, time=step))
    if pedal:
        track.append(mido.Message("control_change", channel=channel, control=64, value=0, time=0))
    track.append(mido.MetaMessage("end_of_track", time=max(0, ticks - step * len(notes))))
    path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(path))
    return path


NAMES = [
    "Johann Sebastian Bach - Prelude and Fugue in C Major, BWV 846 (2004)",
    "Frédéric Chopin - Ballade No. 1 in G Minor, Op. 23 (2009)",
    "Frédéric Chopin - Nocturne in E-flat Major, Op. 9 No. 2 (2011)",
    "Claude Debussy - Clair de Lune (2008)",
    "Franz Liszt - La Campanella (2006)",
    "Wolfgang Amadeus Mozart - Sonata in A Major, K. 331 (2013)",
]


class FixedClock:
    def __init__(self, hhmm="12:00"):
        self.set(hhmm)

    def set(self, hhmm):
        h, m = map(int, hhmm.split(":"))
        self.now = datetime(2026, 10, 7, h, m)

    def __call__(self):
        return self.now


def wait_for(cond, timeout=5.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


@pytest.fixture
def midi_dir(tmp_path):
    root = tmp_path / "midi"
    for name in NAMES:
        write_midi(root / f"{name}.mid")
    return root


@pytest.fixture
def make_jukebox(tmp_path, midi_dir):
    made = []

    def make(start=True, clock=None, resume_delay=0.0, settings=None, seed=1, restore=False):
        """restore=True keeps whatever settings the state file holds (a "reboot")."""
        library = pj.Library(midi_dir, tmp_path / "durations.json")
        library.scan()
        jb = pj.Jukebox(pj.FakeOut(), library, pj.StateStore(tmp_path / "state.json"),
                        settle=0.0, resume_delay=resume_delay, clock=clock or FixedClock("12:00"),
                        rng=__import__("random").Random(seed))
        if not restore:
            jb.update_settings(dict({"gap_seconds": 0, "quiet_enabled": False}, **(settings or {})))
        if start:
            jb.start()
        made.append(jb)
        return jb

    yield make
    for jb in made:
        if not jb._halt.is_set():
            jb.shutdown()


@pytest.fixture
def client(make_jukebox):
    jb = make_jukebox()
    app = pj.create_app(jb)
    app.testing = True
    c = app.test_client()
    c.jb = jb
    return c
