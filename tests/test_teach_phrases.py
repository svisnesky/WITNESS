"""Teach-a-game phrase derivation across real games' wording.

The failure that matters: many games print the VICTIM'S NAME in the kill popup
("KNOCKED DOWN xXTTVGamerXx"). Deriving the trigger from one sighting bakes that
name in, so the profile matches one player and silently never fires again —
which is how a "works with any game" promise gets called a lie.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import teach  # noqa: E402


def test_marathon_style_unchanged():
    assert teach.stable_phrase("RUNNER DOWN +15 XP") == "RUNNER DOWN"
    assert teach.common_phrase(["RUNNER DOWN +15 XP"]) == "RUNNER DOWN"


def test_victim_name_dropped_across_variants():
    """Two kills on different players reveal the invariant part."""
    seen = ["KNOCKED DOWN xXTTVGamerXx", "KNOCKED DOWN DustlineDre",
            "KNOCKED DOWN Pathfinder99"]
    group = teach.variant_group(seen[0], seen)
    assert len(group) == 3
    assert teach.common_phrase(group) == "KNOCKED DOWN"


def test_you_killed_variants():
    seen = ["YOU KILLED DustlineDre", "YOU KILLED GhostP1ng"]
    assert teach.common_phrase(teach.variant_group(seen[0], seen)) == "YOU KILLED"


def test_unrelated_lines_are_not_grouped():
    seen = ["KNOCKED DOWN Bob", "SQUAD ELIMINATED", "AMMO 24"]
    group = teach.variant_group("KNOCKED DOWN Bob", seen)
    assert "AMMO 24" not in group


def test_single_sighting_with_a_name_is_flagged():
    """One kill can't reveal the variable part — say so instead of shipping it."""
    assert teach.looks_name_bearing("KNOCKED DOWN XXTTVGAMERXX", 1) is True
    assert teach.looks_name_bearing("YOU KILLED DUSTLINEDRE", 1) is True


def test_clean_phrases_are_not_flagged():
    for good in ("RUNNER DOWN", "PRECISION DOWN", "ELIMINATED",
                 "YOU KILLED", "KNOCKED DOWN", "ENEMY DOWNED"):
        assert teach.looks_name_bearing(good, 1) is False, good
    assert teach.looks_name_bearing("KNOCKED DOWN XXGAMERXX", 3) is False


def test_points_only_popup():
    assert teach.stable_phrase("KILL +100") == "KILL"


def test_region_widens_over_variants():
    """A name-bearing popup changes width per kill; the region must cover the
    widest variant or long names fall outside it."""
    narrow = (0.40, 0.70, 0.55, 0.74)
    wide = (0.36, 0.70, 0.64, 0.74)
    r = teach.region_around([narrow, wide])
    assert r["x"] <= 0.36 and r["x"] + r["w"] >= 0.64
