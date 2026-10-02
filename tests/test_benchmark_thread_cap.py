"""The benchmark must measure OCR under the same CPU thread cap live mode uses.

Live detection caps torch/OpenCV at ocr_threads (2 by default) so the game keeps
its cores. The benchmark skipped that, so on CPU-only and AMD machines it timed
OCR on every core and said "keeps up" for machines that then missed kills. No
real torch or OCR model is loaded here: both are stand-ins that record calls.
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import benchmark  # noqa: E402


def _fakes(monkeypatch):
    calls = {"torch": [], "cv2": [], "threads_at_engine_build": None}
    torch = types.SimpleNamespace(
        set_num_threads=lambda n: calls["torch"].append(n),
        cuda=types.SimpleNamespace(is_available=lambda: False,
                                   memory_reserved=lambda: 0))
    monkeypatch.setitem(sys.modules, "torch", torch)

    import cv2
    monkeypatch.setattr(cv2, "setNumThreads", lambda n: calls["cv2"].append(n))

    import ocr

    class FakeEngine:
        def __init__(self, *a, **k):
            calls["threads_at_engine_build"] = calls["torch"][-1] if calls["torch"] else None

        def read_lines(self, img):
            return ["PRECISION DOWN", "+25 XP"]

    monkeypatch.setattr(ocr, "OCREngine", FakeEngine)
    return calls


def test_benchmark_applies_ocr_threads_before_building_the_engine(monkeypatch):
    calls = _fakes(monkeypatch)
    out = benchmark.run_benchmark({"ocr_threads": 3, "poll_fps": 5}, n=2)
    assert "error" not in out, out
    assert calls["torch"] == [3] and calls["cv2"] == [3]
    assert calls["threads_at_engine_build"] == 3, "cap must be set before the engine"
    assert out["ocr_threads"] == 3


def test_default_cap_matches_live_default(monkeypatch):
    """Absent key -> 2, the same default main._tune_performance uses."""
    calls = _fakes(monkeypatch)
    benchmark.run_benchmark({}, n=1)
    assert calls["torch"] == [2] and calls["cv2"] == [2]


def test_bad_value_does_not_break_the_benchmark(monkeypatch):
    calls = _fakes(monkeypatch)
    out = benchmark.run_benchmark({"ocr_threads": None}, n=1)
    assert "error" not in out
    assert calls["torch"] == [2]
    benchmark.run_benchmark({"ocr_threads": 0}, n=1)
    assert calls["torch"][-1] == 1


def test_benchmark_does_not_import_main():
    with open(os.path.join(ROOT, "benchmark.py"), encoding="utf-8") as f:
        src = f.read()
    assert "import main" not in src and "from main" not in src
