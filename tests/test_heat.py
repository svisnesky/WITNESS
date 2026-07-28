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


def test_streak_persists_between_kills():
    h = heat.HeatTracker()
    for _ in range(4):
        h.on_kill("down")
    assert h.streak == 4          # no reset between kills in a match
    assert h.peak_label() == "ON FIRE"


def test_streak_resets_each_match():
    """Regression: the streak used to persist across matches while you stayed
    alive. On 2026-07-27 Stan survived four raids in a row, so the streak ran to
    18 and APEX WITNESS (the 10-kill tier) fired on the SECOND kill of a match.
    A killstreak people recognise is per-match."""
    h = heat.HeatTracker()
    for _ in range(10):
        h.on_kill("down")
    assert h.streak == 10
    assert h.peak()[0] == "APEX WITNESS"

    h.new_match()
    assert h.streak == 0
    assert h.peak()[0] == ""          # no tier chip carried over

    # First kill of the new match is FIRST BLOOD at streak 1, nothing higher.
    labels = [e.label for e in h.on_kill("down")]
    assert labels == ["FIRST BLOOD"]
    assert h.streak == 1
    # Second kill is HEATING UP — not APEX.
    labels = [e.label for e in h.on_kill("down")]
    assert labels == ["HEATING UP"]
    # Session total still accumulates across matches.
    assert h.total == 12


def test_precision_streak_resets_between_matches():
    h = heat.HeatTracker(precision_at=3)
    h.on_kill("precision")
    h.on_kill("precision")
    h.new_match()
    assert h.prec == 0
    # Two more precisions in the new match must NOT complete the old streak.
    assert not any(e.key == "sharpshooter" for e in h.on_kill("precision"))


def test_sharpshooter_survives_precision_being_a_modifier():
    """precision popups no longer count as kills, so they never reach on_kill().
    mark_precision() keeps the SHARPSHOOTER streak alive — counted per ENEMY
    (one headshot down = one precision) rather than per popup."""
    h = heat.HeatTracker(precision_at=3)
    got = []
    for _ in range(3):
        h.on_kill("down")            # the down popup
        got += h.mark_precision()    # its PRECISION modifier, a beat later
    assert [e.key for e in got] == ["sharpshooter"]
    assert h.streak == 3             # three enemies, not six
    assert h.prec == 3


def test_a_plain_down_breaks_the_precision_streak():
    """The break is detected lazily on the NEXT kill, because a headshot's
    PRECISION modifier arrives just after its down popup."""
    h = heat.HeatTracker(precision_at=3)
    h.on_kill("down"); h.mark_precision()
    h.on_kill("down"); h.mark_precision()
    assert h.prec == 2
    h.on_kill("down")                # body shot — no modifier follows
    got = h.on_kill("down")          # next kill: the streak is broken here
    assert h.prec == 0
    assert not any(e.key == "sharpshooter" for e in got)
    got = h.mark_precision()         # and this headshot starts over at 1
    assert h.prec == 1
    assert not got
