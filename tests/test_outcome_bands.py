"""Outcome bands: find the header at any vertical position, but NEVER read a
teammate's header (in trios each squad member has their own EXFILTRATED /
ELIMINATED banner — reading theirs would report the wrong result for you)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exfil_stats as E  # noqa: E402

ROWS = ["Combatant Eliminations 23", "Runner Eliminations 3", "Runners Downed 3",
        "Runner Damage 788", "Crew Revives 0", "Inventory Value 26,424",
        "Run Time 25:22"]


def test_stat_rows_are_never_an_outcome():
    """The taller bands see the stat rows; 'Eliminations' must not read as
    'ELIMINATED' or every exfil would be logged as a death."""
    assert E.outcome(ROWS) == ""


def test_header_wins_over_rows():
    assert E.outcome(["EXFILTRATED"] + ROWS) == "survived"
    assert E.outcome(["- ELIMINATED -"] + ROWS) == "died"


def _center_third(panel):
    """The middle of a panel — where its header text actually sits."""
    mid = panel["x"] + panel["w"] / 2.0
    return mid - panel["w"] / 6.0, mid + panel["w"] / 6.0


def test_no_band_reaches_a_teammate_header():
    for side in ("left", "right"):
        lo, hi = _center_third(E.SQUAD_PANELS[side])
        for name, f in E.OUTCOME_BANDS:
            x0, x1 = f["x"], f["x"] + f["w"]
            assert x1 <= lo or x0 >= hi, (
                f"band {name} (x {x0:.2f}-{x1:.2f}) reaches the {side} "
                f"teammate's header zone (x {lo:.2f}-{hi:.2f})")


def test_bands_cover_a_range_of_vertical_positions():
    """The header's height varies by resolution/UI scale — the bands must span
    a real range, not all sit at one y."""
    tops = {round(f["y"], 2) for _n, f in E.OUTCOME_BANDS}
    assert len(tops) >= 3
    lowest = max(f["y"] + f["h"] for _n, f in E.OUTCOME_BANDS)
    highest = min(f["y"] for _n, f in E.OUTCOME_BANDS)
    assert highest <= 0.30 and lowest >= 0.60


def test_every_band_is_a_sane_rect():
    for name, f in E.OUTCOME_BANDS:
        assert 0 <= f["x"] < 1 and 0 <= f["y"] < 1, name
        assert 0 < f["w"] <= 1 and 0 < f["h"] <= 1, name
        assert f["x"] + f["w"] <= 1.001 and f["y"] + f["h"] <= 1.001, name
