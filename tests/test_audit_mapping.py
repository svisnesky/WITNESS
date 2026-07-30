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

from exfil_stats import accumulate_accuracy, accuracy_summary, count_events
from main import _kill_count, _kill_counts

# (name, detected tags in order, game's runners_downed, game's runner_elims)
# Taken verbatim from the session logs. ORDER IS SIGNIFICANT: Marathon prints a
# modifier just after the event it describes, and count_events folds it onto that
# event (PRECISION onto a down, a trailing FINISHER onto an elim).
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

    # --- 2026-07-29. These are the matches that exposed the FINISHER fold:
    # counting every finisher as its own elim was +1 over on all three.
    # "0 runner elims, 1 downs"
    ("0729 m1", ["down", "assist", "assist"], 1, 0),
    # "3 runner elims, 5 downs"
    ("0729 m5", ["down", "down", "assist", "kill", "down", "assist",
                 "down", "kill", "finisher", "down", "precision", "kill"], 5, 3),
    # "2 runner elims, 2 downs"
    ("0729 m6", ["assist", "kill", "down", "kill", "finisher", "down",
                 "assist"], 2, 2),
    # "1 runner elims, 2 downs"
    ("0729 m7", ["down", "assist", "down", "kill", "finisher"], 2, 1),
]

# Matches where our count is honestly LOWER than the game's, with a known cause.
# 0727 m6's third elim popup OCR'd as 'RUNNER ELIM [ASSIST F10 Xp' — the garbled
# bracket made it read as an assist, so it scored nothing. Listed rather than
# quietly excluded, and it under-counts, which is the safe direction.
KNOWN_UNDER = {"m6"}


def _counts(tags):
    return count_events(tags)


def test_finisher_folds_onto_the_elim_it_belongs_to():
    """RUNNER ELIM + FINISHER is one runner melee-finished, not two elims. But a
    FINISHER with no elim in front of it IS its own elim."""
    assert count_events(["kill", "finisher"]) == (0, 1)
    assert count_events(["finisher"]) == (0, 1)
    assert count_events(["kill", "finisher", "kill"]) == (0, 2)
    assert count_events(["kill", "finisher", "finisher"]) == (0, 2)
    # The real sequence from 0729 m5: 4 elim-ish popups, 3 actual elims.
    assert count_events(["kill", "kill", "finisher", "kill"]) == (0, 3)


def test_order_matters_to_the_counter():
    """A Counter would lose this distinction — hence the ordered list."""
    assert count_events(["kill", "finisher"]) != count_events(["finisher", "kill"])
    assert count_events(["finisher", "kill"]) == (0, 2)


def test_audit_mapping_matches_the_game_exactly():
    for name, tags, game_downs, game_elims in REAL_MATCHES:
        if name in KNOWN_UNDER:
            continue
        downs, elims = _counts(tags)
        assert downs == game_downs, f"{name}: downs {downs} != game {game_downs}"
        assert elims == game_elims, f"{name}: elims {elims} != game {game_elims}"


def test_kill_counts_agrees_with_the_canonical_counter():
    """_kill_counts (live) and count_events (audit) must never drift apart."""
    for name, tags, _gd, _ge in REAL_MATCHES:
        assert _kill_counts(tags) == _counts(tags), name


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
                        ["down"] * 13 + ["kill"] * 12)
    line = accuracy_summary(acc)
    assert "100%" not in line, line
    assert "over" in line, line


def test_accuracy_summary_is_100_percent_only_when_exact():
    acc = {}
    exact = [m for m in REAL_MATCHES if m[0] not in KNOWN_UNDER]
    for _name, tags, gd, ge in exact:
        accumulate_accuracy(acc, {"runners_downed": gd, "runner_elims": ge}, tags)
    line = accuracy_summary(acc)
    tot_d = sum(m[2] for m in exact)
    tot_e = sum(m[3] for m in exact)
    assert f"downs: detected {tot_d}/{tot_d} (100%)" in line, line
    assert f"elims: detected {tot_e}/{tot_e} (100%)" in line, line
