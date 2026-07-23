"""The end-of-night WITNESS Report dossier."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import witness_report as wr  # noqa: E402


def _session(total, **kw):
    base = {"date": "2026-07-22", "duration_min": 90, "total": total,
            "precision": 0, "finisher": 0, "assist": 0, "down": total, "kpm": 0.1}
    base.update(kw)
    return base


def test_report_has_written_and_spoken():
    r = wr.build_report(_session(11, precision=4, finisher=2, assist=3),
                        victims=[("GhostP1ng", 4, "22:41")],
                        killers=[("xNovaByte", 3, "22:38")],
                        player="Mister Viz Nasty")
    assert set(r) == {"title", "lines", "speech"}
    text = "\n".join(r["lines"])
    assert "CONFIRMED KILLS" in text and "11" in text
    assert "GhostP1ng" in text and "xNovaByte" in text
    # name keeps its casing in the spoken line (no str.capitalize flattening)
    assert "Mister Viz Nasty" in r["speech"]
    assert "eleven confirmed" in r["speech"]


def test_quiet_night_is_handled():
    r = wr.build_report(_session(0, down=0), [], [], player="Viz")
    text = "\n".join(r["lines"])
    assert "CONFIRMED KILLS" in text and "0" in text
    assert "no kills" in r["speech"].lower()   # quiet-night phrasing in the spoken line


def test_no_boards_no_crash():
    r = wr.build_report(_session(3, precision=1), None, None, player="")
    assert "CONFIRMED KILLS" in "\n".join(r["lines"])
