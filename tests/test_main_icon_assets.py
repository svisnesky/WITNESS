"""No Marathon game art at runtime (owner decision 10).

marathon_skull.png is Marathon game art. The tray icon fell back to it, and
the overlay's soft upgrade only swapped an old config's marathon_skull.png
when the new file existed. Both now land on WITNESS art and never need the
Marathon file.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_the_tray_icon_falls_back_to_the_witness_logo(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGBA", (32, 32), (255, 0, 0, 255)).save(tmp_path / "witness_logo.png")
    (tmp_path / "marathon_skull.png").write_bytes(b"not used")   # would not decode
    icon = main._tray_icon_image(str(tmp_path), {"app_icon": "missing.png"})
    assert icon.getpixel((32, 32)) == (255, 0, 0, 255)


def test_an_old_overlay_image_setting_gets_the_witness_skull(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    (tmp_path / "overlay.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "witness_headshot.png").write_bytes(b"png")
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: launched.append(args))
    main.show_overlay({"overlay_image": "marathon_skull.png"})
    assert len(launched) == 1
    assert os.path.basename(launched[0][2]) == "witness_headshot.png"
