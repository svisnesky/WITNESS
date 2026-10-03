"""Why is OBS black? Reading the scene's source types (F26).

Two setups got the wrong warning:
  - Game Capture is black until the game runs, so a Game Capture user who
    pressed START first got 'OBS IS BLACK - NOT RECORDING' every time.
  - A new OBS scene is empty, and the advice said to re-pick a Display Capture
    source they don't have.

Stan uses Display Capture. Anything that isn't clearly one of those two setups
must keep today's warning exactly, so his safety net never gets softer.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blackcheck as bc  # noqa: E402


def _png(fill, noise=0.0):
    import cv2
    a = np.full((180, 320, 3), fill, dtype="float32")
    if noise:
        a = a + np.random.default_rng(7).normal(0, noise, a.shape)
    return cv2.imencode(".png", np.clip(a, 0, 255).astype("uint8"))[1].tobytes()


class _Obs:
    def __init__(self, shot, kinds=None, boom=False):
        self.shot = shot
        self.kinds = kinds
        self.boom = boom
        self.asked = 0

    def get_scene_screenshot(self, width=480):
        return self.shot

    def get_scene_input_kinds(self):
        self.asked += 1
        if self.boom:
            raise RuntimeError("websocket closed")
        return self.kinds


# --- scene_setup -----------------------------------------------------------------

def test_unknown_when_obs_could_not_say():
    assert bc.scene_setup(None) == "unknown"


def test_an_empty_scene_has_no_capture():
    assert bc.scene_setup([]) == "no_capture"


def test_audio_text_and_images_are_not_capture_sources():
    assert bc.scene_setup(["wasapi_output_capture", "text_gdiplus_v3",
                           "image_source", "color_source_v3"]) == "no_capture"


def test_game_or_window_capture_only_is_hook_only():
    assert bc.scene_setup(["game_capture"]) == "hook_only"
    assert bc.scene_setup(["window_capture", "game_capture"]) == "hook_only"
    # the KillCounter text and desktop audio don't change the verdict
    assert bc.scene_setup(["game_capture", "text_gdiplus_v3",
                           "wasapi_output_capture"]) == "hook_only"


def test_display_capture_keeps_todays_warning():
    """Stan's setup. Even next to a Game Capture, it must not go soft."""
    assert bc.scene_setup(["monitor_capture"]) == "other"
    assert bc.scene_setup(["monitor_capture", "game_capture"]) == "other"


def test_anything_unrecognised_keeps_todays_warning():
    assert bc.scene_setup(["dshow_input"]) == "other"
    assert bc.scene_setup(["game_capture", "unknown"]) == "other"
    assert bc.scene_setup(["game_capture", "ffmpeg_source"]) == "other"


def test_junk_input_is_unknown_not_a_crash():
    assert bc.scene_setup(42) == "unknown"


# --- check_obs_capture ------------------------------------------------------------

def test_a_black_capture_carries_the_scene_verdict():
    obs = _Obs(_png(0), kinds=["game_capture"])
    r = bc.check_obs_capture(obs)
    assert r["blank"] is True
    assert r["scene_kinds"] == ["game_capture"] and r["scene"] == "hook_only"


def test_a_healthy_capture_does_not_ask_obs_again():
    obs = _Obs(_png(110, noise=35), kinds=["monitor_capture"])
    r = bc.check_obs_capture(obs)
    assert r["blank"] is False and obs.asked == 0
    assert r["scene"] == "unknown"


def test_a_fake_without_the_method_is_unknown():
    """test_blackcheck.py's fake only defines get_scene_screenshot."""
    class _Bare:
        def get_scene_screenshot(self, width=480):
            return _png(0)
    r = bc.check_obs_capture(_Bare())
    assert r["blank"] is True and r["scene"] == "unknown"


def test_a_failing_lookup_is_unknown_not_an_error():
    r = bc.check_obs_capture(_Obs(_png(0), boom=True))
    assert r["ok"] is True and r["blank"] is True and r["scene"] == "unknown"


# --- warning_text -----------------------------------------------------------------

def test_the_warning_covers_both_new_setups_and_keeps_the_old_fix():
    txt = bc.warning_text({"reason": "the frame is black"})
    assert ("No capture source in your scene? Sources > + > Display Capture "
            "(or Game Capture).") in txt
    assert "Using Game Capture? It stays black until the game is running." in txt
    for kept in ("re-pick the display", "Capture Method", "BLACK SCREEN"):
        assert kept in txt


def test_a_hidden_capture_source_is_named_as_a_cause():
    """get_scene_input_kinds leaves hidden items out, so a scene whose only
    Display Capture has its eye icon off reads as 'no_capture'. Saying the
    scene has NO capture source told that user to add a duplicate instead of
    unhiding the one they have."""
    lead = bc.warning_text({"reason": "x", "scene": "no_capture"})
    assert "NO capture source" not in lead
    assert "no VISIBLE capture source" in lead
    assert lead.index("eye icon") < lead.index("Sources > + > Display Capture")


def test_the_likeliest_cause_goes_first():
    empty = bc.warning_text({"reason": "x", "scene": "no_capture"})
    assert empty.index("no VISIBLE capture source") < empty.index("re-pick the display")
    hook = bc.warning_text({"reason": "x", "scene": "hook_only"})
    assert hook.index("Marathon is running") < hook.index("re-pick the display")


def test_display_capture_users_get_exactly_the_generic_warning():
    generic = bc.warning_text({"reason": "x"})
    assert bc.warning_text({"reason": "x", "scene": "other"}) == generic
    assert bc.warning_text({"reason": "x", "scene": "unknown"}) == generic


def test_the_warning_survives_a_cp1252_console():
    """The Windows console (and pythonw's redirected log) is cp1252."""
    for scene in ("no_capture", "hook_only", "other"):
        bc.warning_text({"reason": "x", "scene": scene}).encode("cp1252")
