"""Shorts badge rendering bits (the parts testable without ffmpeg)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shorts import _drawtext, _ff_color, _parse_name  # noqa: E402


def test_parse_name():
    assert _parse_name("003_down+finisher_19-27-52.mkv") == (3, "DOWN + FINISHER")
    assert _parse_name("012_precision_09-01-00.mp4") == (12, "PRECISION")
    assert _parse_name("highlights.mp4") == (None, "")


def test_ff_color():
    assert _ff_color("#9c58da") == "0x9c58da"
    assert _ff_color("#FF9D2B") == "0xFF9D2B"
    assert _ff_color("red;}") == "0x9c58da"        # malformed -> safe default
    assert _ff_color("") == "0x9c58da"


def test_drawtext_badge_layout():
    chain = _drawtext("FINISHER", "MARATHON",
                      "C:/Windows/Fonts/arialbd.ttf", "0xff9d2b")
    assert "boxcolor=0xff9d2b" in chain            # themed chip
    assert "y=1336" in chain and "y=1442" in chain  # below the footage,
    assert "y=180" not in chain                    # NOT the old top slot
    assert chain.count("drawtext") == 2
