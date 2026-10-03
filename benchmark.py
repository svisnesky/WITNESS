"""Detection benchmark — measures how fast the OCR pipeline actually runs on
this machine, on demand (no game/session needed).

It OCRs a representative popup-sized frame N times through the real OCREngine
(same upscale + size cap as live detection) and reports average latency,
throughput, the compute device (GPU/CPU), and VRAM in use. The dashboard
surfaces this in the DETECTION panel and behind the BENCHMARK button.
"""

from __future__ import annotations

import time


def _sample_image():
    """A dark, popup-sized frame with reward-popup-like text, so OCR does the
    same work it does live (blank frames would benchmark unrealistically fast)."""
    import cv2
    import numpy as np
    # ~1080p detect-region size; the engine's size cap handles higher res.
    img = np.full((260, 730, 3), 14, dtype=np.uint8)
    cv2.putText(img, "PRECISION DOWN", (24, 120),
                cv2.FONT_HERSHEY_SIMPLEX, 2.0, (210, 200, 255), 5, cv2.LINE_AA)
    cv2.putText(img, "+25 XP", (24, 205),
                cv2.FONT_HERSHEY_SIMPLEX, 1.6, (200, 200, 210), 4, cv2.LINE_AA)
    return img


def _device_and_vram():
    device, vram = "CPU", 0.0
    try:
        import torch
        if torch.cuda.is_available():
            device = "GPU"
            vram = torch.cuda.memory_reserved() / (1024 * 1024)
    except Exception:
        pass
    return device, vram


def apply_thread_cap(cfg) -> int:
    """Cap torch/OpenCV CPU threads at ocr_threads, the same cap live mode
    applies (main._tune_performance). Without it the benchmark ran OCR on every
    core while live detection gets 2 threads, so CPU-only and AMD machines got
    a verdict that was too optimistic. Set directly rather than importing main;
    the setting is process-wide, which is fine because live sets the same."""
    try:
        n = max(1, int(cfg.get("ocr_threads", 2)))
    except (TypeError, ValueError):
        n = 2
    try:
        import torch
        torch.set_num_threads(n)
    except Exception:
        pass
    try:
        import cv2
        cv2.setNumThreads(n)
    except Exception:
        pass
    return n


def run_benchmark(cfg, n: int = 20) -> dict:
    """OCR a sample frame `n` times and return a result dict. Never raises —
    returns {'error': ...} if the engine can't load."""
    cfg = cfg or {}
    poll = int(cfg.get("poll_fps", 5) or 5)
    threads = apply_thread_cap(cfg)   # before the engine is built, as live does
    try:
        from ocr import OCREngine
        engine = OCREngine(cfg.get("ocr_engine", "easyocr"),
                           cfg.get("ocr_upscale", 3),
                           max_dim=cfg.get("ocr_max_dim", 800))
        img = _sample_image()
        engine.read_lines(img)   # warm-up: model load + first-call allocation
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "device": _device_and_vram()[0]}

    t0 = time.perf_counter()
    for _ in range(max(1, n)):
        engine.read_lines(img)
    dt = time.perf_counter() - t0

    avg_ms = dt / max(1, n) * 1000.0
    throughput = (1000.0 / avg_ms) if avg_ms > 0 else 0.0
    device, vram = _device_and_vram()
    # effective fps is capped by the poll rate — that's what you actually get
    eff = min(throughput, poll)
    return {
        "avg_ms": round(avg_ms, 1),
        "throughput_fps": round(throughput, 1),
        "eff_fps": round(eff, 1),
        "poll_fps": poll,
        "device": device,
        "engine": cfg.get("ocr_engine", "easyocr"),
        "vram_mb": round(vram),
        "ocr_threads": threads,
        "n": int(n),
        # headroom = OCR can keep up with the poll rate with margin to spare
        "keeps_up": throughput >= poll,
        "comfortable": throughput >= poll * 1.5,
    }
