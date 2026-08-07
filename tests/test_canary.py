"""The detection canary: shout when the game proves detection is broken, and
stay silent otherwise.

A false "your app is broken" on a genuinely quiet night is worse than no warning
at all — it teaches you to ignore the one alarm that matters. So the
no-false-alarm tests here are as important as the detection ones, and two of them
replay Stan's REAL sessions.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import canary  # noqa: E402


def _busy(c, frames=4000, text=True):
    for _ in range(frames):
        c.note_frame(["RUNNER DOWN +15 XP"] if text else [])


# --- it must SHOUT -----------------------------------------------------------

def test_game_credited_kills_but_we_saw_none():
    """THE failure this exists for: Bungie renames the popup, nothing errors."""
    c = canary.DetectionCanary()
    _busy(c)
    c.note_audit(4, 2, 0, 0)
    level, msg = c.verdict()
    assert level == "broken", (level, msg)
    assert "6" in msg and "0" in msg


def test_detecting_less_than_half_is_broken_too():
    c = canary.DetectionCanary()
    _busy(c)
    c.note_audit(4, 2, 1, 0)      # game 6, us 1
    c.note_audit(3, 1, 0, 1)      # game 4, us 1
    assert c.verdict()[0] == "broken"


def test_a_blank_region_reports_blind_not_broken():
    """No text at all is a capture/region problem, and needs a different fix
    than changed popup wording — so it must not be reported as the same thing."""
    c = canary.DetectionCanary()
    _busy(c, frames=1500, text=False)
    level, msg = c.verdict()
    assert level == "blind"
    assert "Test Screenshot" in msg


def test_text_but_never_a_trigger_is_only_suspicious():
    c = canary.DetectionCanary()
    _busy(c, frames=4000)
    level, _ = c.verdict()
    assert level == "quiet", "no ground truth to be sure — must not cry broken"


# --- it must STAY QUIET ------------------------------------------------------

def test_a_real_good_session_is_silent():
    """2026-07-29: game 12 downs / 6 elims, detected the same."""
    c = canary.DetectionCanary()
    _busy(c, frames=45000)
    c.note_trigger(20)
    for gd, ge, od, oe in [(1, 0, 1, 0), (5, 3, 5, 3), (2, 2, 2, 2), (2, 1, 2, 1)]:
        c.note_audit(gd, ge, od, oe)
    assert c.verdict()[0] == "ok", c.verdict()


def test_the_earlier_session_with_a_known_miss_is_silent():
    """2026-07-27: one elim OCR'd as an assist. One miss is not a broken app."""
    c = canary.DetectionCanary()
    _busy(c, frames=45000)
    c.note_trigger(18)
    for gd, ge, od, oe in [(4, 2, 4, 2), (1, 1, 1, 1), (2, 3, 2, 2)]:
        c.note_audit(gd, ge, od, oe)
    assert c.verdict()[0] == "ok", c.verdict()


def test_a_genuinely_bad_night_is_not_an_alarm():
    """Zero kills because you kept dying. The game credits nothing either, so
    there is no evidence of breakage — and this is the false alarm that would
    destroy trust in the warning."""
    c = canary.DetectionCanary()
    _busy(c, frames=2000)
    for _ in range(5):
        c.note_audit(0, 0, 0, 0)
    assert c.verdict()[0] == "ok", c.verdict()


def test_one_unlucky_match_is_not_enough_evidence():
    """A single miscounted panel must not trip it."""
    c = canary.DetectionCanary()
    _busy(c)
    c.note_audit(2, 0, 0, 0)      # below MIN_GAME_EVENTS
    assert c.verdict()[0] == "ok"


def test_a_short_session_is_never_blind_or_quiet():
    """Ten minutes of menus shouldn't trigger anything."""
    c = canary.DetectionCanary()
    _busy(c, frames=200, text=False)
    assert c.verdict()[0] == "ok"


def test_summary_always_reports_the_counts():
    c = canary.DetectionCanary()
    _busy(c, frames=10)
    c.note_trigger(2)
    c.note_audit(3, 1, 3, 1)
    s = c.summary()
    assert "10 frames" in s and "2 trigger" in s and "game 4" in s


def test_fresh_canary_is_ok():
    assert canary.DetectionCanary().verdict()[0] == "ok"
