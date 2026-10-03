"""A '[ASSIST' read with its closing bracket dropped is still an assist.

The KNOWN_UNDER note in tests/test_audit_mapping.py blames 07-27 m6's missing
elim on 'RUNNER ELIM [ASSIST F10 Xp': "the garbled bracket made it read as an
assist". It can't have: _normalize strips both brackets, so '[ASSIST' and
'[ASSIST]' classify the same. And acting on the note (treating a dropped
bracket as your own kill) breaks 07-27 m5, whose 'HAPA RUNNER DOIN [ASSIST 5
XP' is a real assist: downs go 1 -> 2 against the game's 1, net 0 exact
matches, downs exact 12/13 -> 11/13. This locks classify_event's bracket
handling in place, so a change made on that note fails here.

The corrected reading of 07-27 m6 (game 3 elims, we count 2): the cause is
LIKELY, not confirmed (no frames from that moment), an own RUNNER ELIM popup
the detector never fired on inside a continuous popup chain. The narrower
"bracket-dropped ELIM = own" rule fits m6 only because it was built from that
one raw (n=1); don't ship it on that. Leave classify_event's bracket handling
alone. (test_audit_mapping.py belongs to another package, so its comment is
not rewritten here; this is the wording it should carry.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import classify_event  # noqa: E402


def test_bracket_dropped_assist_reads_stay_assists():
    # the only three bracket-dropped raws in the real logs
    assert classify_event("HAPA RUNNER DOIN [ASSIST 5 XP") == "assist"     # 07-27 m5
    assert classify_event("RUNNER ELIM [ASSIST F10 Xp") == "assist"        # 07-27 m6
    assert classify_event("RUNNER DOIN [ASSIST +15 XP") == "assist"        # 07-24 m1


def test_the_bracket_cannot_change_the_tag():
    for intact, dropped in (("RUNNER ELIM [ASSIST] F10 Xp", "RUNNER ELIM [ASSIST F10 Xp"),
                            ("RUNNER ELIM [ASSIST] +10 XP", "RUNNER ELIM [ASSIST +10 XP")):
        assert classify_event(intact) == classify_event(dropped) == "assist"
    # and the solo popup is still your own elim
    assert classify_event("RUNNER ELIM +10 XP") == "kill"
