"""main.py fails loudly and recovers.

The window launcher runs pythonw: no console, and gui.py printed the error
after the session log was already closed. So a failed START, or a capture loop
that died on a lock screen, left nothing behind but a dashboard still reading
WATCHING. Pinned here:
  - a session that raises leaves its traceback in logs/session_*.log;
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_a_session_crash_lands_in_the_session_log(tmp_path, monkeypatch):
    # the log goes to <app folder>/logs: point the app folder at tmp_path so
    # the test never writes into the checkout
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))

    def boom(*a, **k):
        raise RuntimeError("OBS: cannot reach the WebSocket server")

    monkeypatch.setattr(main, "_run_live_inner", boom)
    with pytest.raises(RuntimeError):
        main.run_live({})
    logs = list((tmp_path / "logs").glob("session_*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "Traceback" in text
    assert "OBS: cannot reach the WebSocket server" in text
    assert "Session log saved" in text      # and the log was still closed out
