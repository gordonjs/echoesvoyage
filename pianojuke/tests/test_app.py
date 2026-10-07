import json
import time

import pianojuke as pj
from conftest import FixedClock, NAMES, wait_for, write_midi


def ids(jb):
    return sorted(jb.library.tracks)


def panic_count(sent):
    """How many full pedals-up + all-notes-off bursts went out."""
    return sum(1 for m in sent if m.type == "control_change" and m.control == 123 and m.channel == 15)


def pedals_lifted_everywhere(msgs):
    lifted = {(m.channel, m.control) for m in msgs
              if m.type == "control_change" and m.control in pj.PEDALS and m.value == 0}
    return all((ch, cc) in lifted for ch in range(16) for cc in pj.PEDALS)


# --- library and UI


def test_library_parses_names_and_sorts_by_surname(client):
    data = client.get("/api/library").get_json()
    assert data["count"] == len(NAMES)
    assert [c["name"] for c in data["composers"]] == [
        "Johann Sebastian Bach", "Frédéric Chopin", "Claude Debussy", "Franz Liszt",
        "Wolfgang Amadeus Mozart"]
    debussy = next(t for t in data["tracks"] if t["composer"] == "Claude Debussy")
    assert debussy == {"id": "Claude Debussy - Clair de Lune (2008).mid", "composer": "Claude Debussy",
                       "title": "Clair de Lune", "year": 2008, "duration": debussy["duration"],
                       "favorite": False, "never": False}


def test_piece_lengths_are_measured_in_the_background(client):
    assert wait_for(lambda: all(t.duration for t in client.jb.library.tracks.values()))
    data = client.get("/api/library").get_json()
    assert all(abs(t["duration"] - 0.3) < 0.05 for t in data["tracks"])


def test_ui_and_pwa_files_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and b"<title>PianoJuke</title>" in page.data
    assert b"__V__" not in page.data
    manifest = client.get("/manifest.webmanifest")
    assert manifest.mimetype == "application/manifest+json"
    m = json.loads(manifest.data)
    assert m["display"] == "fullscreen" and m["orientation"] == "landscape"
    for icon in m["icons"]:
        assert client.get(icon["src"]).status_code == 200
    assert client.get("/sw.js").status_code == 200
    for asset in ("app.js", "app.css"):
        assert client.get(f"/static/{asset}").status_code == 200


# --- playback and safety


def test_play_stop_and_every_stop_lifts_pedals(client):
    jb = client.jb
    tid = ids(jb)[0]
    st = client.post("/api/play", json={"id": tid}).get_json()
    assert st["state"] == "playing" and st["track"]["id"] == tid and st["source"] == "manual"
    jb.out.sent.clear()
    st = client.post("/api/stop").get_json()
    assert st["state"] == "idle" and st["track"] is None
    assert pedals_lifted_everywhere(jb.out.sent) and panic_count(jb.out.sent) >= 1
    # Stop when nothing is playing still sends the panic.
    jb.out.sent.clear()
    client.post("/api/stop")
    assert panic_count(jb.out.sent) == 1


def test_natural_end_transition_panics_before_next_piece(make_jukebox):
    jb = make_jukebox()
    a, b = ids(jb)[:2]
    write_midi(jb.library.tracks[b].path, notes=(72, 76))
    jb.queue_add(a)
    jb.queue_add(b)
    assert wait_for(lambda: jb.phase == "idle")
    sent = list(jb.out.sent)
    on = [(i, m.note) for i, m in enumerate(sent) if m.type == "note_on" and m.velocity > 0]
    last_a = max(i for i, n in on if n in (60, 64, 67))
    first_b = min(i for i, n in on if n in (72, 76))
    # Between the last note of a and the first of b: pedals up and CC123 on every channel.
    between = sent[last_a:first_b]
    assert pedals_lifted_everywhere(between) and panic_count(between) >= 1
    assert [h["ended"] for h in jb.history] == ["finished", "finished"]


def test_skip_panics_and_moves_to_next(client):
    jb = client.jb
    a, b = ids(jb)[:2]
    write_midi(jb.library.tracks[a].path, seconds=5)
    client.post("/api/play", json={"id": a})
    client.post("/api/queue", json={"id": b})
    assert wait_for(lambda: jb.playback and jb.playback.started_at)
    jb.out.sent.clear()
    st = client.get("/api/skip").get_json()      # GET works too, for Home Assistant
    assert st["track"]["id"] == b and st["source"] == "queue"
    assert pedals_lifted_everywhere(jb.out.sent)
    assert jb.history[-2]["ended"] == "skipped"


def test_volume_scales_velocity_live_with_floor_and_night_ceiling(make_jukebox):
    clock = FixedClock("12:00")
    jb = make_jukebox(clock=clock)
    assert jb.velocity(100) == 70                     # default volume 70 %
    jb.set_volume(50)
    assert jb.velocity(100) == 50
    assert jb.velocity(10) == pj.MIN_VELOCITY         # floor
    jb.set_volume(100)
    jb.update_settings({"night_enabled": True, "night_start": "20:00", "night_end": "07:00",
                        "night_ceiling": 60})
    assert jb.velocity(120) == 120                    # daytime: no ceiling
    clock.set("21:30")
    st = jb.status()
    assert st["night_active"] and st["velocity_ceiling"] == 60
    assert jb.velocity(120) == 60
    tid = ids(jb)[0]
    jb.out.sent.clear()
    jb.play(tid)
    assert wait_for(lambda: jb.phase == "idle")
    velocities = [m.velocity for m in jb.out.sent if m.type == "note_on" and m.velocity > 0]
    assert velocities and max(velocities) == 60


def test_volume_endpoint_accepts_common_shapes(client):
    assert client.post("/api/volume", json={"volume": 42}).get_json()["volume"] == 42
    assert client.get("/api/volume?value=150").get_json()["volume"] == 100
    assert client.post("/api/volume", data={"level": "5"}).get_json()["volume"] == 5
    assert client.post("/api/volume", json={"volume": "loud"}).status_code == 400
    assert client.get("/api/volume").get_json()["volume"] == 5


def test_play_resolves_file_names(client):
    st = client.get("/api/play?file=Claude Debussy - Clair de Lune (2008).mid").get_json()
    assert st["title"] == "Clair de Lune"
    st = client.post("/api/play", json={"name": "campanella"}).get_json()
    assert st["title"] == "La Campanella"
    assert client.post("/api/play", json={"id": "nope.mid"}).status_code == 404
    st = client.post("/api/play").get_json()        # no argument: a random piece
    assert st["state"] == "playing"


def test_corrupt_file_is_reported_and_rotation_moves_on(make_jukebox, midi_dir):
    (midi_dir / "Broken - Not A Midi File (2000).mid").write_bytes(b"garbage")
    jb = make_jukebox()
    jb.play("Broken - Not A Midi File (2000).mid")
    assert wait_for(lambda: jb.phase == "idle")
    st = jb.status()
    assert st["error"]["id"] == "Broken - Not A Midi File (2000).mid"
    assert jb.history[-1]["ended"] == "error"


# --- rotation


def test_rotation_plays_whole_library_without_repeats(client):
    jb = client.jb
    st = client.post("/api/rotation/on").get_json()
    assert st["rotation"]["enabled"] and st["state"] == "playing"
    assert wait_for(lambda: len(jb.history) >= len(NAMES) + 1, timeout=15)
    first = [h["id"] for h in jb.history[:len(NAMES)]]
    assert sorted(first) == ids(jb)
    assert jb.rotation.cycle == 2
    st = client.post("/api/stop").get_json()
    assert not st["rotation"]["enabled"] and st["state"] == "idle"


def test_rotation_endpoint_for_home_assistant_switch(client):
    assert client.get("/api/rotation").get_json()["enabled"] is False
    assert client.post("/api/rotation", json={"enabled": True}).get_json()["enabled"] is True
    assert client.post("/api/rotation", json={"enabled": "toggle"}).get_json()["enabled"] is False
    assert client.post("/api/rotation", json={}).status_code == 400
    assert client.get("/api/rotation/toggle").get_json()["rotation"]["enabled"] is True
    assert client.get("/api/rotation/off").get_json()["rotation"]["enabled"] is False


def test_gap_between_pieces(make_jukebox):
    jb = make_jukebox(settings={"gap_seconds": 2})
    jb.set_rotation(True)
    assert wait_for(lambda: jb.phase == "gap", timeout=3)
    st = jb.status()
    assert 0 < st["next_in"] <= 2 and st["up_next"]["source"] == "rotation"
    upcoming = st["up_next"]["id"]
    assert len(jb.history) == 1
    assert wait_for(lambda: len(jb.history) == 2, timeout=4)
    assert jb.history[-1]["id"] == upcoming, "the piece shown as up next is the one that played"
    # Skip during a gap starts the next piece now.
    assert wait_for(lambda: jb.phase == "gap", timeout=3)
    jb.skip()
    assert jb.phase == "playing" and len(jb.history) == 3


def test_quiet_hours_hold_rotation_but_not_manual_play(make_jukebox):
    clock = FixedClock("23:00")
    jb = make_jukebox(clock=clock, settings={"quiet_enabled": True, "quiet_start": "22:00",
                                             "quiet_end": "07:30"})
    jb.set_rotation(True)
    st = jb.status()
    assert st["state"] == "quiet" and st["quiet_until"] == "07:30" and not jb.history
    jb.play(ids(jb)[0])
    assert jb.phase == "playing"
    assert wait_for(lambda: jb.phase == "quiet")
    assert len(jb.history) == 1
    clock.set("07:30")
    assert wait_for(lambda: len(jb.history) >= 2, timeout=3)


def test_never_play_and_favorites(client):
    jb = client.jb
    all_ids = ids(jb)
    for tid in all_ids[:-1]:
        r = client.post("/api/track", json={"id": tid, "never": True}).get_json()
        assert r["never"] is True
    r = client.post("/api/track", json={"id": all_ids[-1], "favorite": True}).get_json()
    assert r["favorite"] is True
    st = client.post("/api/rotation/on").get_json()
    assert st["track"]["id"] == all_ids[-1]
    assert client.post("/api/track", json={"id": "missing.mid", "favorite": True}).status_code == 404
    view = client.get("/api/settings").get_json()
    assert len(view["never"]) == len(all_ids) - 1


def test_queue_operations(client):
    jb = client.jb
    a, b, c, d = ids(jb)[:4]
    write_midi(jb.library.tracks[a].path, seconds=5)
    r = client.post("/api/queue", json={"id": a}).get_json()
    assert r["started"] is True and jb.current["id"] == a      # idle jukebox starts at once
    for tid in (b, c):
        assert client.post("/api/queue", json={"id": tid}).get_json()["started"] is False
    client.post("/api/queue", json={"id": d, "position": "next"})
    q = client.get("/api/queue").get_json()
    assert [i["id"] for i in q["queue"]] == [d, b, c]
    q = client.post("/api/queue/move", json={"from": 0, "to": 2}).get_json()
    assert [i["id"] for i in q["queue"]] == [b, c, d]
    q = client.post("/api/queue/remove", json={"index": 1, "id": c}).get_json()
    assert [i["id"] for i in q["queue"]] == [b, d]
    q = client.delete("/api/queue/0").get_json()
    assert [i["id"] for i in q["queue"]] == [d]
    assert client.post("/api/queue/remove", json={"index": 7}).status_code == 404
    st = client.get("/api/nowplaying").get_json()
    assert st["up_next"]["id"] == d and st["queue_length"] == 1
    q = client.post("/api/queue/clear").get_json()
    assert q["queue"] == []


def test_settings_validation(client):
    r = client.post("/api/settings", json={"gap_seconds": 30, "quiet_start": "9:15"})
    assert r.status_code == 200
    assert r.get_json()["settings"]["quiet_start"] == "09:15"
    bad = client.post("/api/settings", json={"gap_seconds": -1, "quiet_end": "25:00", "bogus": 1})
    assert bad.status_code == 400
    assert set(bad.get_json()["errors"]) == {"gap_seconds", "quiet_end", "bogus"}
    low = client.post("/api/settings", json={"night_ceiling": pj.MIN_VELOCITY - 1})
    assert low.status_code == 400


# --- persistence


def test_state_survives_restart_and_rotation_resumes(make_jukebox, tmp_path):
    jb = make_jukebox(settings={"gap_seconds": 7})
    a = ids(jb)[0]
    jb.set_flags(a, favorite=True)
    jb.set_volume(33)
    jb.set_rotation(True)
    assert wait_for(lambda: jb.phase == "gap")
    played_before = jb.rotation.picks
    jb.shutdown()                               # systemctl stop / reboot
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["rotation"]["enabled"] is True and saved["settings"]["volume"] == 33

    jb2 = make_jukebox(restore=True, resume_delay=0.5)
    st = jb2.status()
    assert st["state"] == "gap" and st["resuming"] and st["rotation"]["enabled"]
    assert jb2.settings["gap_seconds"] == 7 and a in jb2.favorites
    assert jb2.rotation.picks == played_before
    assert wait_for(lambda: jb2.phase == "playing", timeout=3)


def test_stopped_rotation_stays_off_after_restart(make_jukebox):
    jb = make_jukebox()
    jb.set_rotation(True)
    jb.stop()
    jb.shutdown()
    jb2 = make_jukebox(restore=True)
    assert jb2.phase == "idle" and not jb2.rotation.enabled
    time.sleep(1.2)
    assert jb2.phase == "idle"


def test_corrupt_state_file_is_set_aside(make_jukebox, tmp_path):
    (tmp_path / "state.json").write_text("{not json")
    jb = make_jukebox(restore=True)
    assert jb.phase == "idle"
    assert list(tmp_path.glob("state.json.corrupt-*"))


def test_route_listing_needs_no_midi():
    routes = {r["path"]: r["methods"] for r in pj.route_table(pj.create_app(None))}
    for path in ("/api/nowplaying", "/api/skip", "/api/rotation/on", "/api/rotation/off"):
        assert path in routes
    assert routes["/api/skip"] == ["GET", "POST"]
