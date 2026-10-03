"""MATCH_BREAK: a match boundary in the session-wide tag list.

count_events folds a FINISHER onto a pending RUNNER ELIM (same runner, melee
finished). Over a whole session that fold could reach across an exfil: match 1
ends on RUNNER ELIM, match 2 opens with a standalone FINISHER, and the session
count loses an elim. count_events resets on MATCH_BREAK and counts nothing for
it. Appending it to the session's tag list at each exfil is the caller's job
(main.py), so these tests drive count_events directly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exfil_stats  # noqa: E402
from exfil_stats import MATCH_BREAK, count_events  # noqa: E402


def test_match_break_stops_a_pending_elim_leaking_into_the_next_match():
    assert count_events(["kill", "exfil", "finisher"]) == (0, 2)
    # without the break, today's fold (unchanged inside one match)
    assert count_events(["kill", "finisher"]) == (0, 1)


def test_match_break_counts_nothing():
    assert count_events([MATCH_BREAK]) == (0, 0)
    assert count_events([MATCH_BREAK] * 3) == (0, 0)
    assert count_events(["down", MATCH_BREAK, "down"]) == (2, 0)
    assert count_events(["down", "precision", MATCH_BREAK]) == (1, 0)


def test_synthetic_cases_from_the_detection_review():
    # [kill] | [finisher]                (0,1) -> (0,2)
    assert count_events(["kill", MATCH_BREAK, "finisher"]) == (0, 2)
    # kill | finisher | kill, finisher   (0,2) -> (0,3)
    assert count_events(["kill", MATCH_BREAK, "finisher", MATCH_BREAK,
                         "kill", "finisher"]) == (0, 3)


def test_a_fold_inside_one_match_still_folds_after_a_break():
    assert count_events([MATCH_BREAK, "kill", "finisher"]) == (0, 1)
    assert count_events(["down", MATCH_BREAK, "kill", "finisher", "kill"]) == (1, 2)


def test_live_counter_agrees():
    """main._kill_counts delegates to count_events, so it inherits the break."""
    from main import _kill_counts
    tags = ["kill", MATCH_BREAK, "finisher", "down"]
    assert _kill_counts(tags) == count_events(tags) == (1, 2)


def test_the_marker_value_is_stable_and_never_a_popup_tag():
    """A tag parser elsewhere (gui.py) may key on this value, so keep it
    stable. And it must never collide with a tag that classify_event can
    produce."""
    assert exfil_stats.MATCH_BREAK == "exfil"
    import main
    popup_tags = {"down", "manual_kill", "kill", "assist", "precision",
                  "finisher"}
    popup_tags |= set(getattr(main, "ELIM_TAGS", ()))
    popup_tags |= set(getattr(main, "NON_KILL_TAGS", ()))
    assert MATCH_BREAK not in popup_tags
    for raw in ("RUNNER DOWNED +15 XP", "RUNNER ELIM +10 XP", "FINISHER +5 XP",
                "PRECISION DOWNED +25", "RUNNER ELIM [ASSIST] +5 XP",
                "EXFILTRATED"):
        assert main.classify_event(raw) != MATCH_BREAK
