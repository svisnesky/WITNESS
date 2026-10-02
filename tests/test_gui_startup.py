"""The desktop launcher (gui.py) must never fail silently.

The recommended launcher runs `pythonw gui.py`: no console, sys.stdout and
sys.stderr are None, and Tk swallows callback errors. A half-finished install,
a typo in config.yaml, OBS with its WebSocket server off, or a wrong OBS
password all used to end in either an invisible pythonw.exe or a START button
that quietly flipped back after 12 seconds.

None of these tests opens a window: they drive the pure functions, call the
classes with stand-in roots/states, and read the source where only a Windows
run can exercise the real thing.
"""

import inspect
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("tkinter")
import gui  # noqa: E402


class _Stop(Exception):
    """Raised by the stand-in _fatal so a test can see what would be shown."""


@pytest.fixture
def fatal(monkeypatch):
    shown = []

    def fake(message, *a, **k):
        shown.append(message)
        raise _Stop(message)
    monkeypatch.setattr(gui, "_fatal", fake)
    return shown


# --- crash visibility ---------------------------------------------------------

def test_main_wraps_the_boot_and_writes_a_crash_log():
    src = inspect.getsource(gui.main)
    assert "_ensure_stderr()" in src
    assert "_boot()" in src
    assert "crash_" in src and "_fatal(" in src


def test_boot_sets_report_callback_exception():
    assert "root.report_callback_exception = _boot_callback_error(root)" in \
        inspect.getsource(gui._boot)
    assert "report_callback_exception = _panel_callback_error(r)" in \
        inspect.getsource(gui._run_tk_fallback)


def test_import_main_failure_is_shown_not_swallowed():
    src = inspect.getsource(gui._boot)
    body = src[src.index("def finish():"):src.index("def leave():")]
    assert "import main as app_module" in body
    assert "startup_error.log" in body
    assert "Some components are missing (setup did not finish)" in body
    assert "root.quit()" in body and "os._exit(1)" in body


def test_stderr_goes_to_a_log_under_pythonw(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))
    monkeypatch.setattr(sys, "stderr", None)
    gui._ensure_stderr()
    try:
        assert sys.stderr is not None
        sys.stderr.write("boom\n")
        path = tmp_path / "logs" / "pythonw_stderr.log"
        assert "boom" in path.read_text(encoding="utf-8")
    finally:
        sys.stderr.close()


def test_stderr_left_alone_when_it_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))
    before = sys.stderr
    gui._ensure_stderr()
    assert sys.stderr is before
    assert not (tmp_path / "logs" / "pythonw_stderr.log").exists()


def test_write_log_lands_in_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))
    p = gui._write_log("startup_error.log", "Traceback: x — y")
    assert p == str(tmp_path / "logs" / "startup_error.log")
    assert "—" in open(p, encoding="utf-8").read()


def test_control_panel_reports_a_bad_config(monkeypatch, fatal):
    def bad():
        raise ValueError("mapping values are not allowed here (line 12)")
    monkeypatch.setattr(gui, "app", types.SimpleNamespace(load_config=bad))
    panel = gui.ControlPanel.__new__(gui.ControlPanel)
    with pytest.raises(_Stop):
        gui.ControlPanel.__init__(panel, root=object())
    assert "config.yaml" in fatal[0] and "line 12" in fatal[0]


def test_webview_reports_a_bad_config_instead_of_falling_back(monkeypatch, fatal):
    def bad():
        raise ValueError("bad indentation (line 3)")
    monkeypatch.setattr(gui, "app", types.SimpleNamespace(load_config=bad))
    # never let the test reach the real pywebview / its pip auto-install
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    with pytest.raises(_Stop):
        gui._run_webview()
    assert "config.yaml" in fatal[0] and "line 3" in fatal[0]
