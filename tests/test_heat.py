"""The heat / killstreak engine."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import heat  # noqa: E402


def keys(events):
    return [e.key for e in events]


def test_first_blood_then_tiers():
    h = heat.HeatTracker()
    assert keys(h.on_kill("down")) == ["firstblood"]     # 1st kill (streak 1)
    assert keys(h.on_kill("down")) == ["heatingup"]      # streak 2
    assert keys(h.on_kill("down")) == ["onfire"]         # streak 3
    assert keys(h.on_kill("down")) == []                 # 4
    assert keys(h.on_kill("down")) == ["rampage"]        # 5
    for _ in range(1):
        h.on_kill("down")                                # 6
    assert keys(h.on_kill("down")) == ["menace"]         # 7
    for _ in range(2):
        h.on_kill("down")                                # 8, 9
    assert keys(h.on_kill("down")) == ["apex"]           # 10


def test_first_blood_is_per_match():
    h = heat.HeatTracker()
    assert "firstblood" in keys(h.on_kill("down"))       # match 1, first kill
    assert "firstblood" not in keys(h.on_kill("down"))   # same match, no repeat
    h.new_match()
    assert "firstblood" in keys(h.on_kill("down"))       # match 2 re-arms it


def test_precision_streak_sharpshooter():
    h = heat.HeatTracker()
    h.on_kill("precision")   # 1 (firstblood + prec1)
    h.on_kill("precision")   # prec2
    ev = h.on_kill("precision")   # prec3 -> sharpshooter (also streak 3 -> hotstreak)
    assert "sharpshooter" in keys(ev)
    # a non-precision kill breaks the precision streak
    h.on_kill("down")
    h.on_kill("precision")
    h.on_kill("precision")
    ev2 = h.on_kill("precision")
    assert "sharpshooter" in keys(ev2)


def test_death_resets_and_mourns_a_real_streak():
    h = heat.HeatTracker()
    for _ in range(5):
        h.on_kill("down")
    assert h.streak == 5
    ended = h.on_death()
    assert ended is not None and ended.key == "streakend" and ended.streak == 5
    assert h.streak == 0
    # a tiny streak isn't mourned
    h.on_kill("down"); h.on_kill("down")
    assert h.on_death() is None


def test_streak_persists_until_death():
    h = heat.HeatTracker()
    for _ in range(4):
        h.on_kill("down")
    assert h.streak == 4          # no reset between kills
    assert h.peak_label() == "ON FIRE"
