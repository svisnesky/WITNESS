"""One code default for capture_source, the one live detection uses.

calibrate._grab defaulted to "screen" while capture.make_capture and
diagnose default to "obs_virtualcam" (main's behaviour, which old configs
without the key rely on). With the key absent, calibrate measured the region
on a desktop screenshot while detection read the Virtual Camera frame."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import calibrate  # noqa: E402
import capture  # noqa: E402
import diagnose  # noqa: E402


def test_the_shared_default_is_the_live_loops():
    assert capture.DEFAULT_SOURCE == "obs_virtualcam"


def _grabbed(monkeypatch, eff):
    got = []
    monkeypatch.setattr(capture, "grab_full_virtualcam",
                        lambda cam_index=0: got.append("virtualcam") or "frame")
    monkeypatch.setattr(capture, "grab_full_screenshot",
                        lambda monitor_index=1: got.append("screen") or "frame")
    calibrate._grab(eff)
    return got


def test_calibrate_without_the_key_grabs_what_detection_reads(monkeypatch, capsys):
    assert _grabbed(monkeypatch, {}) == ["virtualcam"]


def test_calibrate_follows_an_explicit_key(monkeypatch, capsys):
    assert _grabbed(monkeypatch, {"capture_source": "screen"}) == ["screen"]
    assert _grabbed(monkeypatch, {"capture_source": "obs_virtualcam"}) == ["virtualcam"]


def test_make_capture_and_diagnose_use_the_same_default(monkeypatch):
    made = []
    monkeypatch.setattr(capture, "VirtualCamCapture",
                        lambda *a, **k: made.append("virtualcam"))
    capture.make_capture({})
    assert made == ["virtualcam"]
    assert "capture.DEFAULT_SOURCE" in inspect.getsource(diagnose)
