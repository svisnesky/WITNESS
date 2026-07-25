"""The OCR size cap bounds the 5 fps DETECTION loop only.

One-off reads (teach wizard full frame, gamertag / runner / exfil scans) must
run at full resolution: capping a 4K frame to 800px turns 34px UI text into
7px and NOTHING reads. The cap was added to fix in-game frame drops and
silently broke those paths — this locks the split in place.
"""
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ocr  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_cap_resolution():
    e = ocr.OCREngine(max_dim=800)
    assert e._cap(None) == 800     # engine default (detection loop)
    assert e._cap(0) == 0          # explicit uncapped
    assert e._cap(1600) == 1600


def test_uncapped_keeps_full_resolution():
    img = np.zeros((2160, 3840, 3), np.uint8)
    assert ocr.preprocess(img, 1, False, 0).shape[:2] == (2160, 3840)
    assert ocr.preprocess(img, 1, False, 800).shape[:2] == (450, 800)


def test_detection_loop_still_capped():
    """The perf fix must survive: a 3x-upscaled popup crop stays bounded."""
    img = np.zeros((259, 730, 3), np.uint8)          # 4K detect region
    out = ocr.preprocess(img, 3, False, 800)
    assert max(out.shape[:2]) == 800


def _src(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_one_off_readers_pass_max_dim_zero():
    """Guards against a future edit dropping the override and silently
    re-breaking the wizard / name reads at high resolution."""
    for mod, needle in (
        ("teach.py", "read_boxes(frame, max_dim=0)"),
        ("teach_gui.py", "read_boxes(frame, max_dim=0)"),
        ("runner_detect.py", "read_lines(frame, max_dim=0)"),
    ):
        assert needle in _src(mod), f"{mod} lost its full-resolution read"
    enc = _src("encounters.py")
    assert "max_dim=0" in enc, "encounters feed read lost full resolution"
    ex = _src("exfil_stats.py")
    assert ex.count("max_dim=0") >= 3, "exfil panel/name/outcome reads capped"


def test_main_loop_reader_uses_the_config_cap():
    m = _src("main.py")
    assert 'max_dim=cfg.get("ocr_max_dim", 800)' in m
