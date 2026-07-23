"""Match outcome from the exfil panel header: EXFILTRATED (survived) vs
ELIMINATED (died). The panel also carries 'Combatant Eliminations' / 'Runner
Eliminations' stat rows on BOTH screens, so a substring match on 'eliminated'
would narrate every survival as a death — the detection must match whole lines.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exfil_stats  # noqa: E402


def test_header_outcome():
    assert exfil_stats.outcome(["EXFILTRATED"]) == "survived"
    assert exfil_stats.outcome(["+ EXFILTRATED +"]) == "survived"
    assert exfil_stats.outcome(["ELIMINATED"]) == "died"
    assert exfil_stats.outcome(["- ELIMINATED -"]) == "died"
    assert exfil_stats.outcome([]) == ""


def test_elimination_stat_rows_are_not_a_death():
    # the exact false-positive Stan hit: stat rows, no header -> unknown
    rows = ["Combatant Eliminations 23", "Runner Eliminations 3",
            "Runners Downed 3", "Runner Damage 788", "Inventory Value 26424"]
    assert exfil_stats.outcome(rows) == ""


def test_header_wins_when_a_stat_row_bleeds_into_the_crop():
    assert exfil_stats.outcome(["EXFILTRATED", "Combatant Eliminations 23"]) == "survived"
    assert exfil_stats.outcome(["ELIMINATED", "Combatant Eliminations 6",
                                "Runner Eliminations 0"]) == "died"
