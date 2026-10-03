"""The OCR engine loads BEFORE the session goes live.

EasyOCR used to load lazily on the capture loop's first read_lines(), after
_setup_session had called web.set_running(True). A first START whose model
download failed (or whose torch/easyocr was broken) then counted as a session
that went live and crashed, so the window app showed "Session crashed - see
logs/" instead of the reason in the red START box, and the dashboard read
WATCHING during the download while kills went unread.

Nothing here touches the OCR size caps or VRAM handling, and no OCR pass is
added: _preload_ocr only calls the engine's own loader."""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import capture  # noqa: E402
import main  # noqa: E402
import ocr  # noqa: E402


def _patch(monkeypatch, engine_cls, calls):
    monkeypatch.setattr(ocr, "OCREngine", engine_cls)
    monkeypatch.setattr(main, "_tune_performance", lambda cfg: None)
    monkeypatch.setattr(main, "_release_vram", lambda engine: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)

    def setup(cfg, dry_run):
        calls.append("setup_session")       # the step that sets running=True
        raise AssertionError("must not go live")
    monkeypatch.setattr(main, "_setup_session", setup)
    monkeypatch.setattr(capture, "make_capture", lambda cfg: None)


def test_an_engine_that_will_not_load_fails_start_before_going_live(monkeypatch):
    calls = []

    class Engine:
        def __init__(self, *a, **k):
            pass

        def _ensure_loaded(self):
            calls.append("load")
            raise RuntimeError("CUDA error: no kernel image is available")

    _patch(monkeypatch, Engine, calls)
    with pytest.raises(RuntimeError, match="no kernel image"):
        main._run_live_inner({"show_overlays": False}, dry_run=True)
    assert calls == ["load"] * main.OCR_STARTUP_TRIES
    assert "setup_session" not in calls


def test_a_missing_ocr_package_is_not_retried(monkeypatch):
    calls = []

    class Engine:
        def __init__(self, *a, **k):
            pass

        def _ensure_loaded(self):
            calls.append("load")
            raise ImportError("No module named 'easyocr'")

    _patch(monkeypatch, Engine, calls)
    with pytest.raises(ImportError):
        main._run_live_inner({"show_overlays": False}, dry_run=True)
    assert calls == ["load"]


def test_a_load_hiccup_that_passes_goes_on_to_setup(monkeypatch):
    calls = []

    class Engine:
        def __init__(self, *a, **k):
            pass

        def _ensure_loaded(self):
            calls.append("load")
            if len(calls) == 1:
                raise RuntimeError("CUDA out of memory")

    _patch(monkeypatch, Engine, calls)
    with pytest.raises(AssertionError, match="must not go live"):
        main._run_live_inner({"show_overlays": False}, dry_run=True)
    assert calls == ["load", "load", "setup_session"]


def test_stop_during_a_retry_ends_start(monkeypatch):
    stop = threading.Event()
    stop.set()
    calls = []

    class Engine:
        def __init__(self, *a, **k):
            pass

        def _ensure_loaded(self):
            calls.append("load")
            raise RuntimeError("driver reset")

    monkeypatch.setattr(main, "_release_vram", lambda engine: None)
    with pytest.raises(RuntimeError):
        main._preload_ocr(Engine(), stop)
    assert calls == ["load"]


def test_an_engine_without_a_loader_is_left_alone():
    main._preload_ocr(object())            # test fakes: nothing to load


def test_the_real_engine_exposes_the_loader_preload_calls():
    assert callable(getattr(ocr.OCREngine("easyocr"), "_ensure_loaded", None))
