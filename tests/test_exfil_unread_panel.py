"""An exfil pass whose stats panel could not be read.

main.py always sets stats_d["outcome"] before calling exfil_stats.report(),
so an unread panel reached report() as {"outcome": "died"}, never {}. The
"couldn't read" branch was unreachable: the user saw a raw Python dict
("match stats: {'outcome': 'died'}"), a blank row went into
stats/match_stats.csv, and the canary counted an audit with no numbers."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exfil_stats  # noqa: E402
import main  # noqa: E402

EXFIL_LINES = ["Runner Damage 788", "Inventory Value 26,424"]


def test_outcome_alone_is_an_unread_panel():
    assert not exfil_stats.panel_read({"outcome": "died"})
    assert not exfil_stats.panel_read({})
    assert not exfil_stats.panel_read(None)
    assert exfil_stats.panel_read({"outcome": "died", "runners_downed": 2})


def test_report_says_it_could_not_read_instead_of_printing_a_dict():
    line = exfil_stats.report({"outcome": "died"}, ["kill"])
    assert "couldn't read the stats panel" in line
    assert "{" not in line
    ok = exfil_stats.report({"outcome": "extracted", "runners_downed": 2}, ["down"])
    assert "match stats: 2 downs" in ok


class _Canary:
    def __init__(self):
        self.audits = []

    def note_audit(self, *a):
        self.audits.append(a)


class _Obs:
    def get_record_directory(self):
        return ""

    def set_counter(self, n):
        pass


def _run(monkeypatch, panel):
    logged = []
    monkeypatch.setattr(exfil_stats, "capture_exfil_stats",
                        lambda cfg, engine, save_dir="", retries=3: (dict(panel), [], "died"))
    monkeypatch.setattr(exfil_stats, "log_outcome", lambda *a, **k: None)
    monkeypatch.setattr(exfil_stats, "log_match_stats",
                        lambda *a, **k: logged.append(a))
    monkeypatch.setattr(main, "_build_match_reel_async", lambda *a, **k: None)
    canary = _Canary()
    s = {"count": 0, "session_tags": [], "match_tags": ["down"], "match_clips": [],
         "match_num": 0, "obs": _Obs(), "web": None, "cfg": {}, "heat": None,
         "session_id": "s1", "clutch": False, "organize": False, "last_save": 0,
         "min_save": 2.0, "medal_sounds": {}, "canary": canary}
    main._maybe_capture_exfil({"exfil_reconcile": False}, None, EXFIL_LINES, s, 0.0)
    return logged, canary.audits


def test_an_unread_panel_logs_no_row_and_no_audit(monkeypatch, capsys):
    logged, audits = _run(monkeypatch, {})
    assert logged == [] and audits == []
    out = capsys.readouterr().out
    assert "couldn't read the stats panel" in out
    assert "match stats: {" not in out


def test_a_read_panel_still_logs_and_audits(monkeypatch, capsys):
    logged, audits = _run(monkeypatch, {"runners_downed": 1, "runner_damage": 10,
                                        "inventory_value": 20})
    assert len(logged) == 1 and len(audits) == 1
