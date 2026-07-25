"""The wizard's verification pass: prove a profile fires BEFORE the user
trusts it. Driven with a fake screen so it runs headless."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import teach  # noqa: E402

CFG = {"poll_fps": 100, "popup_match_threshold": 85, "popup_absence_frames": 3,
       "popup_confirm_frames": 1, "popup_cooldown_seconds": 0.0}


class _Screen:
    """Replays scripted OCR reads as if watching the game."""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0

    def read_lines(self, _img, max_dim=None):
        out = self.script[self.i] if self.i < len(self.script) else []
        self.i += 1
        return out


def _run(script, phrases, reward=True, seconds=0.4):
    scr = _Screen(script)
    return teach.verify_profile(CFG, phrases, {"x": 0, "y": 0, "w": 1, "h": 1},
                                reward, seconds=seconds, engine=scr,
                                grab=lambda c: None)


def test_good_profile_confirms():
    script = [[]] * 3 + [["KNOCKED DOWN Bob"]] * 4 + [[]] * 5
    res = _run(script, ["KNOCKED DOWN"], reward=False)
    assert res["fired"] >= 1


def test_name_bearing_phrase_fails_verification():
    """The exact bug the verify step exists to catch: the phrase carries a
    victim name, so the NEXT kill (different player) never matches."""
    script = [[]] * 2 + [["KNOCKED DOWN DustlineDre"]] * 4 + [[]] * 4
    res = _run(script, ["KNOCKED DOWN XXTTVGAMERXX"], reward=False)
    assert res["fired"] == 0
    assert any("Dustline" in r for r in res["reads"]), "should report what it read"


def test_wrong_region_reports_nothing_read():
    res = _run([[]] * 12, ["KNOCKED DOWN"], reward=False)
    assert res["fired"] == 0 and res["reads"] == []


def test_require_reward_mismatch_is_caught():
    """Profile demands a reward marker the game doesn't print."""
    script = [[]] * 2 + [["ELIMINATED"]] * 4
    assert _run(script, ["ELIMINATED"], reward=True)["fired"] == 0
    assert _run(script, ["ELIMINATED"], reward=False)["fired"] >= 1


def test_counts_two_stacked_popups():
    script = [[]] * 2 + [["ELIMINATED +50 ELIMINATED +50"]] * 5
    assert _run(script, ["ELIMINATED"], reward=True)["fired"] == 2
