"""Ground-truth regression: the popup tags we detect must reproduce the two
numbers Marathon prints on its own exfil panel.

Every case here is REAL data from the 2026-07-27 session log, paired with what
the game's summary screen said for that same match. This is the strongest test
in the suite: it is not my opinion of what a kill is, it is the game's.

The bug this locks out: 'precision' was counted as a second down (Marathon prints
"RUNNER DOWNED +15 XP" *and* "PRECISION DOWNED +25" for one headshot), and the
audit filed 'kill' under downs and 'assist' under elims. Result: 18 "kills" on a
night with 10 downs, plus false DOUBLE KILLs and a heat ladder running double
speed.
"""

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exfil_stats import AUDIT_PAIRS, accumulate_accuracy, accuracy_summary
from main import _kill_count, _kill_counts

# (name, detected tags in order, game's runners_downed, game's runner_elims)
# Taken verbatim from session_2026-07-27_20-10-09.log.
REAL_MATCHES = [
    # "match stats: 2 runner elims, 4 downs" — kills #1-#8.
    # Two headshot downs (each printing down+precision) + two plain downs + 2 elims.
    ("night marsh m3",
     ["down", "precision", "down", "precision", "kill", "kill", "down", "down"],
     4, 2),
    # "match stats: 1 runner elims, 1 downs" — kills #14-#15 plus 4 assist popups.
    ("m5", ["down", "assist", "assist", "assist", "kill", "assist"], 1, 1),
    # "match stats: 3 runner elims, 2 downs" — kills #16-#18, 2 finishers, 6 assists.
    ("m6", ["assist", "down", "kill", "finisher", "down", "assist", "finisher",
            "assist", "assist", "assist", "assist"], 2, 3),
]


def _counts(tags):
    """Detected (downs, elims) using the audit's own tag mapping."""
    c = Counter(tags)
    out = {}
    for label, _key, mapped in AUDIT_PAIRS:
        out[label] = sum(c.get(t, 0) for t in mapped)
    return out["downs"], out["elims"]


def test_audit_mapping_matches_the_game_exactly():
    for name, tags, game_downs, game_elims in REAL_MATCHES:
        downs, elims = _counts(tags)
        assert downs == game_downs, f"{name}: downs {downs} != game {game_downs}"
        assert elims == game_elims, f"{name}: elims {elims} != game {game_elims}"


def test_kill_counts_agrees_with_the_audit_mapping():
    """_kill_counts (used live) and AUDIT_PAIRS (used at exfil) must not drift."""
    for name, tags, game_downs, game_elims in REAL_MATCHES:
        assert _kill_counts(tags) == _counts(tags) == (game_downs, game_elims), name


def test_precision_is_a_modifier_not_a_kill():
    # One headshot down = ONE kill, not two.
    assert _kill_count(["down", "precision"]) == 1
    assert _kill_counts(["down", "precision"]) == (1, 0)
    # Three headshot downs = three kills (a real triple), not six.
    assert _kill_count(["down", "precision"] * 3) == 3


def test_assists_count_for_nothing():
    assert _kill_count(["assist"]) == 0
    assert _kill_counts(["assist", "assist", "assist"]) == (0, 0)
    # An assist mixed in with a real down must not inflate it.
    assert _kill_count(["down", "assist"]) == 1


def test_manual_save_clip_is_not_a_kill():
    assert _kill_count(["manual"]) == 0


def test_finishing_your_own_down_is_one_kill():
    # You down a runner then finish them: one enemy, one kill.
    assert _kill_count(["down", "kill"]) == 1
    assert _kill_count(["down", "precision", "kill"]) == 1
    # But finishing someone a teammate downed IS your kill on top of your own.
    assert _kill_count(["down", "kill", "kill"]) == 2
    # A standalone finisher with no down of yours still counts.
    assert _kill_count(["finisher"]) == 1


def test_accuracy_summary_does_not_report_100_percent_when_over_detecting():
    """The old formula capped at 100%, hiding exactly this failure."""
    acc = {}
    # The OLD behaviour on real data: 13 detected downs against the game's 7.
    accumulate_accuracy(acc, {"runners_downed": 7, "runner_elims": 6},
                        Counter({"down": 13, "kill": 12}))
    line = accuracy_summary(acc)
    assert "100%" not in line, line
    assert "over" in line, line


def test_accuracy_summary_is_100_percent_only_when_exact():
    acc = {}
    for _name, tags, gd, ge in REAL_MATCHES:
        accumulate_accuracy(acc, {"runners_downed": gd, "runner_elims": ge},
                            Counter(tags))
    line = accuracy_summary(acc)
    # 7/7 downs and 6/6 elims across the three audited matches.
    assert "downs: detected 7/7 (100%)" in line, line
    assert "elims: detected 6/6 (100%)" in line, line
