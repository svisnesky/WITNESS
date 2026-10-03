"""'2 - Test Screenshot' reads what the live loop reads: the detect region.

run_test_image OCR'd the whole screenshot. The 800 px OCR cap then shrank a
4K frame about 0.21x (popup text ~7 px), so real kill screenshots reported
'no kill detected', and the hint sent people to ocr_upscale. Nothing here
touches the OCR caps: the stub engine just records what it was handed.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

FRAC = {"x": 0.375, "y": 0.71, "w": 0.19, "h": 0.12}


@pytest.fixture
def engine(monkeypatch):
    import ocr
    seen = []

    class _Engine:
        engine_name = "stub"

        def __init__(self, *a, **k):
            pass

        def read_lines(self, img, max_dim=None):
            seen.append(img.shape)
            return []

    monkeypatch.setattr(ocr, "OCREngine", _Engine)
    return seen


def _png(tmp_path, w, h, name="shot.png"):
    import cv2
    p = tmp_path / name
    assert cv2.imwrite(str(p), np.zeros((h, w, 3), dtype=np.uint8))
    return str(p)


def test_a_4k_screenshot_reaches_ocr_as_the_region(tmp_path, engine, capsys):
    path = _png(tmp_path, 3840, 2160)
    main.run_test_image({"detect_region_frac": FRAC}, path)
    assert engine == [(int(0.12 * 2160), int(0.19 * 3840), 3)]
    out = capsys.readouterr().out
    assert "Cropped 3840x2160 screenshot to the detect region" in out
    assert (tmp_path / "shot_region.png").exists()
    assert "shot_region.png" in out and "ocr_upscale" not in out


def test_a_small_precropped_image_is_not_cut_again(tmp_path, engine, capsys):
    path = _png(tmp_path, 730, 260, "popup.png")
    main.run_test_image({"detect_region_frac": FRAC}, path)
    assert engine == [(260, 730, 3)]
    assert "looks already cropped" in capsys.readouterr().out


def test_no_crop_reads_the_whole_image(tmp_path, engine, capsys):
    path = _png(tmp_path, 1920, 1080)
    main.run_test_image({"detect_region_frac": FRAC}, path, crop=False)
    assert engine == [(1080, 1920, 3)]
    assert "--no-crop" in capsys.readouterr().out


def test_a_pixel_region_is_used_when_there_is_no_frac(tmp_path, engine):
    path = _png(tmp_path, 1920, 1080)
    main.run_test_image({"detect_region": {"x": 700, "y": 760, "w": 520, "h": 140}},
                        path)
    assert engine == [(140, 520, 3)]


def test_a_region_that_doesnt_fit_reads_the_whole_image(tmp_path, engine, capsys):
    # a 4K pixel region against a 1080p screenshot
    path = _png(tmp_path, 1920, 1080)
    main.run_test_image({"detect_region": {"x": 1440, "y": 1533, "w": 729, "h": 259}},
                        path)
    assert engine == [(1080, 1920, 3)]
    assert "run Calibrate" in capsys.readouterr().out
