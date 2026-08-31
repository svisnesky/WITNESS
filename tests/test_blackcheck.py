"""Catching OBS recording a black screen — and NOT crying wolf on a dark scene.

Stan: "i played all of last night and it was all black. I wouldve had some good
clips." WITNESS couldn't tell, because it reads the screen directly: its own
frames were perfect while every clip OBS wrote was empty.

The false-alarm tests matter as much as the detection ones. Marathon has night
maps and dark interiors; an alarm that fires on those gets ignored, and then the
real one does nothing.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blackcheck as bc  # noqa: E402


def _img(fill, noise=0.0, size=(180, 320, 3)):
    a = np.full(size, fill, dtype="float32")
    if noise:
        rng = np.random.default_rng(7)
        a = a + rng.normal(0, noise, size)
    return np.clip(a, 0, 255).astype("uint8")


# --- must catch --------------------------------------------------------------

def test_a_pure_black_frame_is_blank():
    r = bc.analyse(_img(0))
    assert r["blank"] is True
    assert "black" in r["reason"]


def test_near_black_with_encoder_noise_is_still_blank():
    """A real 'black' capture isn't mathematically zero — it has codec dither."""
    assert bc.analyse(_img(4, noise=1.2))["blank"] is True


def test_a_flat_solid_colour_is_blank():
    """A stuck grey or a solid-colour source is just as useless as black, and
    wouldn't be caught by a brightness threshold alone."""
    r = bc.analyse(_img(128, noise=0.2))
    assert r["blank"] is True
    assert "flat" in r["reason"]


# --- must NOT cry wolf --------------------------------------------------------

def test_a_dark_but_real_game_scene_is_fine():
    """THE false alarm to avoid: Marathon night maps and dark interiors."""
    assert bc.analyse(_img(10, noise=9))["blank"] is False


def test_a_normal_bright_scene_is_fine():
    assert bc.analyse(_img(120, noise=40))["blank"] is False


def test_a_mostly_dark_frame_with_a_bright_hud_is_fine():
    a = _img(6, noise=2)
    a[10:40, 10:120] = 220           # a HUD element
    assert bc.analyse(a)["blank"] is False


# --- inconclusive is not the same as broken -----------------------------------

def test_no_image_is_not_reported_as_broken():
    """An inconclusive check must never be shown as 'your capture is black'."""
    r = bc.analyse(None)
    assert r["blank"] is False and r["reason"]


def test_greyscale_input_works():
    assert bc.analyse(np.full((90, 160), 0, dtype="uint8"))["blank"] is True


def test_alpha_channel_is_ignored():
    a = np.zeros((90, 160, 4), dtype="uint8")
    a[..., 3] = 255                  # fully opaque, but the picture is black
    assert bc.analyse(a)["blank"] is True


# --- the OBS round trip -------------------------------------------------------

def test_datauri_decodes():
    import base64
    raw = b"\x89PNG\r\n\x1a\n-not-a-real-png"
    uri = "data:image/png;base64," + base64.b64encode(raw).decode()
    assert bc.decode_datauri(uri) == raw


def test_a_junk_datauri_returns_empty_not_an_exception():
    for bad in ("", None, "data:image/png,notbase64", "garbage"):
        assert bc.decode_datauri(bad) == b""


class _Obs:
    def __init__(self, shot):
        self.shot = shot

    def get_scene_screenshot(self, width=480):
        return self.shot


def _png(img):
    import cv2
    return cv2.imencode(".png", img)[1].tobytes()


def test_check_reports_a_black_program_scene():
    r = bc.check_obs_capture(_Obs(_png(_img(0))))
    assert r["ok"] is True and r["blank"] is True


def test_check_passes_a_real_scene():
    r = bc.check_obs_capture(_Obs(_png(_img(110, noise=35))))
    assert r["ok"] is True and r["blank"] is False


def test_an_obs_that_cannot_screenshot_is_inconclusive_not_broken():
    """Old OBS, or a websocket hiccup. ok=False, blank=False — the caller must
    stay quiet rather than accuse the capture."""
    r = bc.check_obs_capture(_Obs(b""))
    assert r["ok"] is False and r["blank"] is False

    class Boom:
        def get_scene_screenshot(self, width=480):
            raise RuntimeError("websocket closed")
    r = bc.check_obs_capture(Boom())
    assert r["ok"] is False and r["blank"] is False


def test_the_warning_names_the_actual_fix():
    """'capture looks black' without 'go re-pick your display' is half a warning."""
    txt = bc.warning_text({"reason": "the frame is black"})
    assert "re-pick the display" in txt
    assert "Capture Method" in txt
    assert "BLACK SCREEN" in txt


def test_main_treats_an_inconclusive_check_as_fine():
    import main
    assert main.check_obs_not_black({}, _Obs(b"")) is True


def test_main_returns_false_on_a_black_capture():
    import main
    assert main.check_obs_not_black({}, _Obs(_png(_img(0)))) is False


def test_the_check_can_be_turned_off():
    import main
    assert main.check_obs_not_black({"check_black_capture": False},
                                    _Obs(_png(_img(0)))) is True
