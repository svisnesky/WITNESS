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


def test_manual_counts_toward_detected():
    assert main._reconcile_missed(["down", "kill", "precision"], GOOD) == 0


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
