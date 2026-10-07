import random
from collections import Counter

from pianojuke import Rotation, in_window, NAME_RE, composer_key
from datetime import datetime


def run(rot, pool, picks, favorites=(), weight=1):
    order = []
    for _ in range(picks):
        tid = rot.choose(pool, favorites, weight)
        rot.record(tid, pool)
        order.append(tid)
    return order


def test_no_repeats_until_whole_library_has_played():
    pool = [f"t{i}" for i in range(40)]
    rot = Rotation(random.Random(7))
    order = run(rot, pool, 40 * 4)
    for c in range(4):
        cycle = order[c * 40:(c + 1) * 40]
        assert sorted(cycle) == sorted(pool), f"cycle {c} repeated or skipped something"
    assert rot.cycle == 4


def test_nothing_heard_in_the_last_few_picks_comes_back_across_cycles():
    pool = [f"t{i}" for i in range(30)]
    guard = min(Rotation.RECENT_GUARD, len(pool) // 2)
    for seed in range(30):
        rot = Rotation(random.Random(seed))
        order = run(rot, pool, 30 * 4)
        last = {}
        for i, tid in enumerate(order):
            if tid in last:
                assert i - last[tid] > guard, (seed, tid, last[tid], i)
            last[tid] = i


def test_tiny_libraries_never_stall_or_repeat_back_to_back():
    for n in (1, 2, 3):
        pool = [f"t{i}" for i in range(n)]
        rot = Rotation(random.Random(1))
        order = run(rot, pool, 20)
        assert None not in order
        if n > 1:
            assert all(a != b for a, b in zip(order, order[1:]))


def test_never_play_is_never_picked():
    ids = [f"t{i}" for i in range(20)]
    never = {"t3", "t4", "t5"}
    pool = Rotation.pool(ids, never)
    rot = Rotation(random.Random(3))
    assert not set(run(rot, pool, 100)) & never


def test_favorites_play_more_but_spaced_and_others_once_per_cycle():
    pool = [f"t{i}" for i in range(60)]
    favorites = {"t0", "t1", "t2", "t3"}
    weight = 3
    rot = Rotation(random.Random(11))
    cycles = {}
    order = []
    for _ in range(2000):
        tid = rot.choose(pool, favorites, weight)
        rot.record(tid, pool)
        order.append(tid)
        cycles.setdefault(rot.cycle, []).append(tid)
    for number, picks in list(cycles.items())[:-1]:
        counts = Counter(picks)
        assert set(counts) == set(pool), "a cycle ended before everything played"
        assert all(counts[t] == 1 for t in pool if t not in favorites)
        assert all(1 <= counts[t] <= weight for t in favorites)
    spacing = len(pool) // weight
    last = {}
    for i, tid in enumerate(order):
        if tid in favorites and tid in last:
            assert i - last[tid] >= spacing, "favorite came back too soon"
        last[tid] = i
    totals = Counter(order)
    fav_avg = sum(totals[t] for t in favorites) / len(favorites)
    other_avg = sum(totals[t] for t in pool if t not in favorites) / (len(pool) - len(favorites))
    assert fav_avg > 1.5 * other_avg


def test_weight_one_is_strict_shuffle_even_with_favorites():
    pool = [f"t{i}" for i in range(25)]
    rot = Rotation(random.Random(5))
    order = run(rot, pool, 75, favorites={"t1", "t2"}, weight=1)
    for c in range(3):
        assert len(set(order[c * 25:(c + 1) * 25])) == 25


def test_pieces_added_mid_cycle_join_the_cycle():
    pool = [f"t{i}" for i in range(10)]
    rot = Rotation(random.Random(2))
    run(rot, pool, 5)
    pool = pool + ["new1", "new2"]
    rest = run(rot, pool, 7)
    assert {"new1", "new2"} <= set(rest)
    assert rot.cycle == 1


def test_excluded_pieces_are_not_chosen():
    pool = ["a", "b", "c"]
    rot = Rotation(random.Random(0))
    for _ in range(20):
        assert rot.choose(pool, exclude={"a", "b"}) == "c"
    assert rot.choose(pool, exclude=set(pool)) is None


def test_dump_and_load_round_trip():
    pool = [f"t{i}" for i in range(10)]
    rot = Rotation(random.Random(1))
    rot.enabled = True
    run(rot, pool, 13)
    copy = Rotation()
    copy.load(rot.dump())
    assert copy.dump() == rot.dump()
    assert copy.played(pool) == rot.played(pool)


def test_windows_cross_midnight():
    at = lambda hhmm: datetime(2026, 1, 1, *map(int, hhmm.split(":")))
    assert in_window(at("23:30"), "22:00", "08:00")
    assert in_window(at("03:00"), "22:00", "08:00")
    assert not in_window(at("08:00"), "22:00", "08:00")
    assert not in_window(at("12:00"), "22:00", "08:00")
    assert in_window(at("13:00"), "12:30", "14:00")
    assert not in_window(at("12:00"), "12:00", "12:00")


def test_filename_parsing_and_composer_sort():
    m = NAME_RE.match("Johann Sebastian Bach - Prelude (BWV 846) (2004)")
    assert (m["composer"], m["title"], m["year"]) == ("Johann Sebastian Bach", "Prelude (BWV 846)", "2004")
    m = NAME_RE.match("Franz Schubert - Impromptu Op. 90 - No. 3")
    assert (m["composer"], m["title"], m["year"]) == ("Franz Schubert", "Impromptu Op. 90 - No. 3", None)
    names = ["Frédéric Chopin", "Johann Sebastian Bach", "Ludwig van Beethoven", "Alban Berg"]
    assert sorted(names, key=composer_key) == [
        "Johann Sebastian Bach", "Ludwig van Beethoven", "Alban Berg", "Frédéric Chopin"]
