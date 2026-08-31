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


# --- the ON-SCREEN warning ----------------------------------------------------

def _spy_overlay(monkeypatch):
    calls = []
    import main
    monkeypatch.setattr(main, "show_text_overlay",
                        lambda cfg, text, **kw: calls.append((text, kw)))
    return calls


def test_a_black_capture_shows_an_overlay_on_the_game_screen(monkeypatch):
    """Stan: "any way for it to pop on the game screen like a headshot logo does?
    that would be super useful since sometimes i get lost in the game". A console
    warning is useless mid-match."""
    import main
    calls = _spy_overlay(monkeypatch)
    main.check_obs_not_black({"announcer_medals": False},
                             _Obs(_png(_img(0))))
    assert len(calls) == 1
    text, kw = calls[0]
    assert "OBS IS BLACK" in text
    assert kw["critical"] is True
    assert kw["duration_ms"] >= 4000, "a warning must linger, unlike flair"


def test_muting_flair_does_NOT_mute_the_warning(monkeypatch):
    """show_overlays exists to silence the fun banners. It must not also silence
    a warning that footage is being lost right now."""
    import main
    calls = _spy_overlay(monkeypatch)
    main.check_obs_not_black({"show_overlays": False, "announcer_medals": False},
                             _Obs(_png(_img(0))))
    assert len(calls) == 1 and calls[0][1]["critical"] is True


def test_the_real_overlay_function_honours_critical(monkeypatch):
    """The bypass has to live in show_text_overlay itself, or critical=True is
    just a label."""
    import main
    spawned = []

    class _P:
        def __init__(self, *a, **k):
            spawned.append(a)
    monkeypatch.setattr("subprocess.Popen", _P)
    monkeypatch.setattr(os.path, "exists", lambda p: True)

    main.show_text_overlay({"show_overlays": False}, "FLAIR")
    assert not spawned, "flair must stay muted"
    main.show_text_overlay({"show_overlays": False}, "WARNING", critical=True)
    assert spawned, "a critical warning must still be shown"


def test_the_overlay_can_be_turned_off_on_its_own(monkeypatch):
    """It's deliberately intrusive, so it needs its own off switch."""
    import main
    calls = _spy_overlay(monkeypatch)
    main.check_obs_not_black({"black_overlay": False, "announcer_medals": False},
                             _Obs(_png(_img(0))))
    assert calls == []


def test_a_healthy_capture_shows_nothing(monkeypatch):
    import main
    calls = _spy_overlay(monkeypatch)
    main.check_obs_not_black({"announcer_medals": False},
                             _Obs(_png(_img(110, noise=35))))
    assert calls == []


def test_an_inconclusive_check_shows_nothing(monkeypatch):
    """No screenshot available is not evidence of a black capture."""
    import main
    calls = _spy_overlay(monkeypatch)
    main.check_obs_not_black({"announcer_medals": False}, _Obs(b""))
    assert calls == []


def test_the_overlay_sits_away_from_the_crosshair():
    """Default position is top-centre: visible without covering the fight."""
    import yaml
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    pos = cfg["black_overlay_position"]
    y = float(pos.split(",")[1])
    assert y < 0.3, f"{pos} would sit over the crosshair"


# --- sweat mode ---------------------------------------------------------------

def test_the_warning_is_held_during_a_clutch(monkeypatch):
    """Stan: "is this even on sweats mode?" It wasn't — it would have slammed a
    red banner and a voice line over him mid-1vN. He can't fix OBS during a
    clutch, so the distraction is pure cost."""
    import main
    calls = _spy_overlay(monkeypatch)
    st = {"clutch": True}
    main.check_obs_not_black({"announcer_medals": False}, _Obs(_png(_img(0))),
                             state=st)
    assert calls == [], "no overlay while clutching"
    assert st["black_pending"] is True, "but it must be remembered"


def test_the_held_warning_fires_when_the_clutch_ends(monkeypatch):
    """DEFERRED, not dropped — he still needs to know."""
    import main
    calls = _spy_overlay(monkeypatch)
    s = {"clutch": False, "black_pending": True,
         "obs": _Obs(_png(_img(0))), "web": None}
    main._fire_deferred_black({"announcer_medals": False}, s)
    assert len(calls) == 1 and "OBS IS BLACK" in calls[0][0]
    assert s["black_pending"] is False, "must not repeat forever"


def test_nothing_fires_when_there_was_nothing_held(monkeypatch):
    import main
    calls = _spy_overlay(monkeypatch)
    main._fire_deferred_black({}, {"black_pending": False, "obs": _Obs(b"")})
    assert calls == []


def test_a_capture_fixed_during_the_clutch_does_not_warn_afterwards(monkeypatch):
    """The deferred call RE-CHECKS rather than replaying a stale result."""
    import main
    calls = _spy_overlay(monkeypatch)
    s = {"black_pending": True, "web": None,
         "obs": _Obs(_png(_img(110, noise=35)))}   # healthy again
    main._fire_deferred_black({"announcer_medals": False}, s)
    assert calls == [], "no warning for a capture that's fine now"


def test_every_clutch_exit_releases_the_held_warning():
    """A deferred warning is worthless if only one of the three exits fires it:
    squad revived, you went down, or you exfiled solo."""
    import inspect

    import main
    for fn in (main._check_clutch, main._end_clutch_quietly,
               main._maybe_capture_exfil):
        assert "_fire_deferred_black" in inspect.getsource(fn), fn.__name__
