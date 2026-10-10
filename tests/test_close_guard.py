"""The X button must not throw away a night (Stan, 2026-10-09: "i lose a whole
nights worth of content because i mustve accidentally closed it")."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

gui = pytest.importorskip("gui")


def test_what_closing_would_interrupt():
    assert gui._busy_reason(False, False) == ""
    assert gui._busy_reason(True, True) == "recording"
    assert gui._busy_reason(False, True) == "building"          # recap thread alive
    assert gui._busy_reason(False, False, "building") == "building"   # resumed build
    assert gui._busy_reason(False, False, "ready") == ""


def test_idle_closes_without_asking(monkeypatch):
    from tkinter import messagebox
    monkeypatch.setattr(messagebox, "askyesno",
                        lambda *a, **k: pytest.fail("must not ask when idle"))
    assert gui._ask_close("") is True


def _not_windows(monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "darwin")


def test_busy_asks_and_no_keeps_witness_open(monkeypatch):
    from tkinter import messagebox
    _not_windows(monkeypatch)
    seen = {}

    def ask(title, text, **k):
        seen.update(title=title, text=text, default=k.get("default"))
        return False
    monkeypatch.setattr(messagebox, "askyesno", ask)
    assert gui._ask_close("recording") is False
    assert "recording" in seen["text"] and seen["default"] == "no", \
        "No (keep running) must be the default, so a stray Enter can't close it"


def test_yes_closes(monkeypatch):
    from tkinter import messagebox
    _not_windows(monkeypatch)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    assert gui._ask_close("building") is True


def test_if_it_cannot_ask_it_stays_open(monkeypatch):
    from tkinter import messagebox
    _not_windows(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("no display")
    monkeypatch.setattr(messagebox, "askyesno", boom)
    assert gui._ask_close("recording") is False


def test_windows_prompt_defaults_to_no(monkeypatch):
    """On Windows it's a native MessageBox: Yes/No, warning icon, the SECOND
    button (No) as default, on top of the game."""
    import types
    calls = {}

    class U:
        @staticmethod
        def MessageBoxW(hwnd, text, title, flags):
            calls.update(text=text, title=title, flags=flags)
            return 7                                   # IDNO
    fake = types.SimpleNamespace(windll=types.SimpleNamespace(user32=U))
    monkeypatch.setattr(gui.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", fake)
    assert gui._ask_close("recording") is False
    assert calls["flags"] & 0x4 and calls["flags"] & 0x100, "Yes/No with No default"
    assert calls["title"] == "Close WITNESS?"


def test_both_windows_are_guarded():
    """The web window hooks pywebview's closing event (synchronous: returning
    False cancels the close), and the tk fallback hooks WM_DELETE_WINDOW."""
    src = open(gui.__file__, encoding="utf-8").read()
    assert "win.events.closing += _on_closing" in src
    assert 'r.protocol("WM_DELETE_WINDOW", _on_close)' in src
