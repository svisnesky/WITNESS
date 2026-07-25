"""The exfil pass is EDGE-TRIGGERED on the summary screen appearing.

It used to be gated by a flat 180s timeout, so a match that ended within three
minutes of the previous one (an early death) silently skipped its whole
end-of-match pass: stats, outcome/W/L row, kill reconciliation, match reel,
match_tags reset and the FIRST BLOOD re-arm.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exfil_stats  # noqa: E402
import main  # noqa: E402

EXFIL_LINES = ["Runner Damage 788", "Inventory Value 26,424"]
GAME_LINES = ["RUNNER DOWN +15 XP"]


class _Obs:
    def get_record_directory(self):
        return ""

    def set_counter(self, n):
        pass


def _session():
    return {"count": 0, "session_tags": [], "match_tags": [], "match_clips": [],
            "match_num": 0, "obs": _Obs(), "web": None, "cfg": {}, "heat": None,
            "session_id": "s1", "clutch": False, "organize": False,
            "last_save": 0, "min_save": 2.0, "medal_sounds": {}}


def _patch(monkeypatch, captures):
    """Neutralize the heavy end-of-match work; count the capture passes."""
    def fake_capture(cfg, engine, save_dir="", retries=3):
        captures.append(save_dir)
        return {"runners_downed": 1, "runner_damage": 10,
                "inventory_value": 20, "crew_revives": 0}, [], "died"
    monkeypatch.setattr(exfil_stats, "capture_exfil_stats", fake_capture)
    monkeypatch.setattr(exfil_stats, "log_outcome", lambda *a, **k: None)
    monkeypatch.setattr(exfil_stats, "read_outcome", lambda *a, **k: "died")
    monkeypatch.setattr(exfil_stats, "report", lambda *a, **k: "")
    monkeypatch.setattr(exfil_stats, "accumulate_accuracy", lambda *a, **k: None)
    monkeypatch.setattr(exfil_stats, "log_match_stats", lambda *a, **k: None)
    monkeypatch.setattr(main, "_build_match_reel_async", lambda *a, **k: None)


def test_fires_once_per_appearance(monkeypatch):
    caps = []
    _patch(monkeypatch, caps)
    s = _session()
    cfg = {"exfil_reconcile": False}
    # screen up for many frames -> exactly one capture
    for i in range(12):
        main._maybe_capture_exfil(cfg, None, EXFIL_LINES, s, float(i))
    assert len(caps) == 1


def test_two_matches_close_together_both_capture(monkeypatch):
    """The regression: the second match's pass must NOT be skipped just because
    it ended soon after the first."""
    caps = []
    _patch(monkeypatch, caps)
    s = _session()
    cfg = {"exfil_reconcile": False}
    t = 0.0
    for _ in range(6):                      # match 1 summary screen
        main._maybe_capture_exfil(cfg, None, EXFIL_LINES, s, t); t += 0.2
    for _ in range(main.EXFIL_REARM_FRAMES):  # back in game (screen gone)
        main._maybe_capture_exfil(cfg, None, GAME_LINES, s, t); t += 0.2
    for _ in range(6):                      # match 2 summary, ~10s later
        main._maybe_capture_exfil(cfg, None, EXFIL_LINES, s, t); t += 0.2
    assert len(caps) == 2, "second match's exfil pass was skipped"


def test_brief_ocr_flicker_does_not_recapture(monkeypatch):
    """A frame or two where the panel doesn't read must not re-arm and
    double-capture the same screen."""
    caps = []
    _patch(monkeypatch, caps)
    s = _session()
    cfg = {"exfil_reconcile": False}
    t = 0.0
    for _ in range(4):
        main._maybe_capture_exfil(cfg, None, EXFIL_LINES, s, t); t += 0.2
    for _ in range(main.EXFIL_REARM_FRAMES - 1):     # flicker, just under re-arm
        main._maybe_capture_exfil(cfg, None, [], s, t); t += 0.2
    for _ in range(4):
        main._maybe_capture_exfil(cfg, None, EXFIL_LINES, s, t); t += 0.2
    assert len(caps) == 1


def test_match_tags_reset_and_first_blood_rearm(monkeypatch):
    """Both live in the exfil pass — a skipped pass polluted the next match's
    audit and never re-armed FIRST BLOOD."""
    import heat
    caps = []
    _patch(monkeypatch, caps)
    s = _session()
    s["heat"] = heat.HeatTracker()
    s["heat"].on_kill("down")                     # consumes this match's first blood
    s["match_tags"] = ["down", "assist"]
    main._maybe_capture_exfil({"exfil_reconcile": False}, None, EXFIL_LINES, s, 1.0)
    assert s["match_tags"] == []
    assert s["heat"].first_blood_armed is True
