"""The benchmark measures what live detection runs, and its advice exists.

run_bench skipped _tune_performance, so CPU torch used every core while live
runs on ocr_threads (2) at below-normal priority: an over-optimistic verdict
on exactly the CPU/AMD machines that miss kills. Its "install the GPU build
(see QUICKSTART)" pointed at steps QUICKSTART doesn't have.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def _stub_loop(monkeypatch, order):
    import capture
    import ocr

    class _Cap:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self):
            return None

    class _Engine:
        def __init__(self, *a, **k):
            order.append("engine")

        def read_lines(self, img, max_dim=None):
            return []

    monkeypatch.setattr(capture, "make_capture", lambda cfg: _Cap())
    monkeypatch.setattr(ocr, "OCREngine", _Engine)
    monkeypatch.setattr(main, "_tune_performance", lambda cfg: order.append("tune"))


def _fake_torch(cuda):
    t = types.ModuleType("torch")
    t.cuda = types.SimpleNamespace(is_available=lambda: cuda,
                                   get_device_name=lambda i: "Fake RTX")
    return t


def test_the_bench_applies_the_live_thread_cap_first(monkeypatch, capsys):
    order = []
    _stub_loop(monkeypatch, order)
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False))
    main.run_bench({"ocr_threads": 2, "poll_fps": 5}, frames=3)
    assert order == ["tune", "engine"]
    out = capsys.readouterr().out
    assert "OCR threads: 2 (same cap as live)" in out


def test_gpu_advice_only_without_cuda_and_never_points_at_quickstart():
    slow = main._bench_verdict(avg=400, worst=500, budget_ms=200, gpu=False)
    text = "\n".join(slow)
    assert "poll_fps: 3" in text and "ocr_upscale: 2" in text
    assert "Delete the .venv folder and run START again" in text
    assert "QUICKSTART" not in text and "://" not in text
    on_gpu = "\n".join(main._bench_verdict(400, 500, 200, gpu=True))
    assert "Delete the .venv" not in on_gpu
    unknown = "\n".join(main._bench_verdict(400, 500, 200, gpu=None))
    assert "Delete the .venv" not in unknown
    fine = main._bench_verdict(avg=20, worst=30, budget_ms=200, gpu=False)
    assert len(fine) == 1 and "plenty of headroom" in fine[0]


def test_workable_also_gets_the_gpu_hint_on_cpu():
    lines = main._bench_verdict(avg=150, worst=190, budget_ms=200, gpu=False)
    assert lines[0].startswith("  VERDICT: workable")
    assert any("nvidia-smi" in ln for ln in lines)
