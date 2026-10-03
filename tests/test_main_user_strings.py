"""Text a stranger sees is written for a stranger.

The session log ended "(send me this file)" (to whom?), and the dashboard's
+1 KILL / SAVE CLIP presses were logged as "from iPad" whatever the device.
The +1 raw line must keep its 'MANUAL' prefix: classify_event keys on it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_the_session_log_points_at_the_issue_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setattr(main, "_run_live_inner", lambda *a, **k: None)
    main.run_live({})
    log = next((tmp_path / "logs").glob("session_*.log")).read_text(encoding="utf-8")
    assert "attach it to a bug report: github.com/svisnesky/WITNESS/issues" in log
    assert "send me" not in log and "://" not in log


def test_a_dashboard_plus_one_is_still_a_manual_kill(monkeypatch, capsys):
    got = []
    monkeypatch.setattr(main, "_handle_kill",
                        lambda cfg, ev, s, on_count=None: got.append(ev.raw_line))

    class _Web:
        def pop_kill_request(self):
            return True

    main._check_manual_kill({}, {"web": _Web()})
    assert got == ["MANUAL +1 (added from the dashboard)"]
    assert main.classify_event(got[0]) == "manual_kill"
    out = capsys.readouterr().out
    assert "from the dashboard" in out and "iPad" not in out
