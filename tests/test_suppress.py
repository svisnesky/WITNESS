"""Frame suppression must never silently eat a detectable kill.

Suppression is a HARD block — the frame never reaches the detector, so a fuzzy
collision destroys a real kill with nothing in the log. The suppress phrase
"RUNNER DAMAGE" (exfil summary) collides with the kill trigger "RUNNER DOWN"
under OCR slips: "RUNNER DAWN" scores 82 vs the suppress phrase but 91 vs the
trigger. Whichever the frame matches better wins.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

CFG = {}   # exercise the shipped defaults


def test_real_end_screens_are_suppressed():
    assert main.is_suppressed(CFG, ["Runner Damage 788", "Crew Revives 0"])
    assert main.is_suppressed(CFG, ["Inventory Value 26,424"])
    assert main.is_suppressed(CFG, ["GIVE UP", "SELF REVIVE"])


def test_clean_kill_popup_is_never_suppressed():
    assert not main.is_suppressed(CFG, ["RUNNER DOWN +15 XP"])
    assert not main.is_suppressed(CFG, ["PRECISION DOWN +25 XP"])
    assert not main.is_suppressed(CFG, ["FINISHER +50"])


def test_ocr_slip_kill_is_not_suppressed():
    """The regression: an O->A slip made this frame look like 'RUNNER DAMAGE'
    and the kill was dropped before detection."""
    assert not main.is_suppressed(CFG, ["RUNNER DAWN +15 XP"])


def test_kill_popup_lingering_on_the_death_screen_is_processed():
    """Trading a kill as you go down: the popup is real. Double-counting is
    already prevented by the detector's edge trigger."""
    assert not main.is_suppressed(CFG, ["GIVE UP", "RUNNER DOWN +15 XP"])


def test_empty_and_noise_frames():
    assert not main.is_suppressed(CFG, [])
    assert not main.is_suppressed(CFG, ["SOUTH RELAY", "LIGHT ROUNDS 002"])


def test_custom_suppress_list_still_honored():
    cfg = {"suppress_phrases": ["LOADING"], "popup_trigger_phrases": ["RUNNER DOWN"]}
    assert main.is_suppressed(cfg, ["LOADING SCREEN"])
    assert not main.is_suppressed(cfg, ["RUNNER DOWN +15 XP"])
