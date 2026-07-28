"""Exfil ground-truth reconciliation — count matches the scoreboard."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

GOOD = {"runners_downed": 3, "runner_elims": 2, "runner_damage": 500,
        "inventory_value": 900}


def test_missed_kills_added():
    # game says 3 downs, we detected 1 down + 1 assist -> 2 missing
    assert main._reconcile_missed(["down", "assist"], GOOD) == 2


def test_precision_and_elims_do_not_count_as_downs():
    """The game's "Runners Downed" must be compared against DOWN events only.
    Counting precision (a modifier on the same runner) and kill (an elim) on our
    side inflated us past the game's number, so `missed` was always negative and
    this whole function was dead code — it could never credit a real miss."""
    # 1 down detected, game says 3 downs -> 2 genuinely missed.
    assert main._reconcile_missed(["down", "kill", "precision"], GOOD) == 2


def test_manual_plus_one_is_not_credited_twice():
    """Tapping +1 KILL exists to fix a down the OCR missed. It arrives tagged
    'manual_kill' (NOT 'kill', which is a RUNNER ELIM) so it counts on the down
    side. Otherwise reconciliation would still see a missing down and add the
    same kill again."""
    # Game says 3 downs. OCR got 1, Stan pressed +1 twice for the other two.
    assert main._reconcile_missed(
        ["down", "manual_kill", "manual_kill"], GOOD) == 0
    # And a manual press is classified apart from a real elim popup.
    assert main.classify_event("MANUAL +1 (added from iPad)") == "manual_kill"
    assert main.classify_event("RUNNER ELIM +10 XP") == "kill"
    # It still counts as a real kill.
    assert main._kill_count(["manual_kill"]) == 1


def test_never_subtracts():
    # we detected MORE than the game says (game screen misread?) -> no change
    assert main._reconcile_missed(["down"] * 5, GOOD) == 0


def test_weak_panel_read_is_ignored():
    assert main._reconcile_missed([], {"runners_downed": 3}) == 0            # 1 label
    assert main._reconcile_missed([], {"runners_downed": 3, "outcome": "died",
                                       "runner_damage": 1}) == 0             # 2 + outcome


def test_insane_values_ignored():
    bad = dict(GOOD, runners_downed=88)   # OCR glitch
    assert main._reconcile_missed([], bad) == 0
    assert main._reconcile_missed([], dict(GOOD, runners_downed=None)) == 0
