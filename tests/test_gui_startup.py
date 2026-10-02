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
import threading
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("tkinter")
import gui  # noqa: E402


class _Stop(BaseException):
    """Raised by the stand-in _fatal so a test can see what would be shown.
    BaseException, like the os._exit it replaces: no `except Exception` in
    the code under test may swallow it."""


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


# --- START failures reach the dashboard ---------------------------------------

class _OBSSDKError(Exception):
    """Stands in for obsws_python.error.OBSSDKError (not installed in tests)."""


def test_friendly_error_connection_refused():
    msg = gui.friendly_start_error(ConnectionRefusedError(
        "[WinError 10061] No connection could be made because the target "
        "machine actively refused it"), "/x/logs/session_2026-10-02_21-00-00.log")
    assert msg.startswith("Can't reach OBS. In OBS: Tools > WebSocket Server Settings")
    assert msg.endswith("(details: logs/session_2026-10-02_21-00-00.log)")


def test_friendly_error_10061_text_without_the_type():
    msg = gui.friendly_start_error(OSError("[WinError 10061] refused"))
    assert msg.startswith("Can't reach OBS")


def test_friendly_error_wrong_password():
    msg = gui.friendly_start_error(_OBSSDKError(
        "failed to identify client with the server, please check connection "
        "settings"))
    assert msg.startswith("OBS rejected the password - copy it into obs.password "
                          "in config.yaml.")
    assert msg.endswith("(details: logs/session_...log)")


def test_friendly_error_gpu_kernel_image():
    msg = gui.friendly_start_error(RuntimeError(
        "CUDA error: no kernel image is available for execution on the device"))
    assert msg.startswith(gui._GPU_MSG)


def test_friendly_error_cuda_out_of_memory_is_not_the_driver_message():
    msg = gui.friendly_start_error(RuntimeError(
        "CUDA out of memory. Tried to allocate 20.00 MiB"))
    assert msg.startswith(gui._VRAM_MSG)


def test_friendly_error_obs_messages_pass_through_verbatim():
    text = ("OBS is open but its WebSocket server is off: Tools > WebSocket "
            "Server Settings > Enable WebSocket server (port 4455)")
    msg = gui.friendly_start_error(RuntimeError(text), "logs/session_a.log")
    assert msg == text + " (details: logs/session_a.log)"


def test_friendly_error_config_messages_pass_through_verbatim():
    text = "config.yaml could not be read (line 7): mapping values are not allowed"
    assert gui.friendly_start_error(RuntimeError(text)).startswith(text + " (details:")


def test_friendly_error_anything_else_names_the_type():
    msg = gui.friendly_start_error(KeyError("capture_source"))
    assert msg.startswith("KeyError: 'capture_source' (details: logs/")


def test_reload_updates_the_same_dict_in_place():
    cfg = {"obs": {"password": "old"}, "poll_fps": 5, "web_port": 8000}
    same = cfg
    err = gui.reload_config_into(
        cfg, lambda: {"obs": {"password": "new"}, "poll_fps": 5, "web_port": 9000},
        lock=threading.Lock(), keep=gui._BOUND_AT_LAUNCH)
    assert err == ""
    assert cfg is same
    assert cfg["obs"]["password"] == "new"
    assert cfg["web_port"] == 8000         # the server is already bound to it


def test_reload_failure_keeps_the_old_settings():
    cfg = {"obs": {"password": "old"}}

    def broken():
        raise ValueError("while scanning a simple key (line 9)")
    err = gui.reload_config_into(cfg, broken)
    assert err.startswith("config.yaml could not be read - using launch settings:")
    assert "line 9" in err
    assert cfg == {"obs": {"password": "old"}}
    assert gui.reload_config_into(cfg, lambda: None).startswith("config.yaml")


class _FakeState:
    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self._mono = None
        self.notices = []
        self.running_calls = []

    def notice(self, text, tag="alert"):
        self.notices.append((text, tag))

    def set_running(self, v):
        self.running_calls.append(v)
        self.running = v
        if v:
            self._mono = time.monotonic()


def _fake_app(monkeypatch, run_live, load_config):
    fake = types.SimpleNamespace(run_live=run_live, load_config=load_config,
                                 _log_path="/w/logs/session_2026-10-02_20-00-00.log")
    monkeypatch.setattr(gui, "app", fake)
    return fake


def test_web_start_reloads_into_the_same_dict(monkeypatch):
    seen = []
    _fake_app(monkeypatch, lambda cfg, dry, ev: seen.append(cfg),
              lambda: {"obs": {"password": "fixed"}})
    cfg = {"obs": {"password": "CHANGE_ME"}}
    st = _FakeState()
    sess = gui._WebSession(cfg, st)
    sess.start()
    sess.thread.join(5)
    assert seen and seen[0] is cfg
    assert cfg["obs"]["password"] == "fixed"
    assert st.last_error == "" and st.starting is False


def test_web_start_survives_a_broken_config(monkeypatch):
    seen = []

    def broken():
        raise ValueError("found character '\\t' (line 4)")
    _fake_app(monkeypatch, lambda cfg, dry, ev: seen.append(cfg), broken)
    cfg = {"obs": {"password": "ok"}}
    st = _FakeState()
    sess = gui._WebSession(cfg, st)
    sess.start()
    sess.thread.join(5)
    assert seen and seen[0] is cfg and cfg == {"obs": {"password": "ok"}}
    assert st.last_error.startswith("config.yaml could not be read")
    assert st.notices and st.notices[0][1] == "alert"
    assert len(st.notices[0][0]) <= 60          # LiveState.notice cuts at 60


def test_a_failed_start_sets_last_error_not_a_notice(monkeypatch):
    def run_live(cfg, dry, ev):
        raise _OBSSDKError("failed to identify client with the server")
    _fake_app(monkeypatch, run_live, lambda: {})
    st = _FakeState()
    sess = gui._WebSession({}, st)
    sess.start()
    sess.thread.join(5)
    assert st.last_error.startswith("OBS rejected the password")
    assert "session_2026-10-02_20-00-00.log" in st.last_error
    assert st.notices == []
    assert st.running_calls == [False] and st.starting is False


def test_a_crash_after_going_live_is_a_notice_not_a_start_error(monkeypatch):
    st = _FakeState()

    def run_live(cfg, dry, ev):
        st.set_running(True)
        raise RuntimeError("recap build blew up")
    _fake_app(monkeypatch, run_live, lambda: {})
    sess = gui._WebSession({}, st)
    sess.start()
    sess.thread.join(5)
    assert st.last_error == ""
    assert st.notices == [("Session crashed - see logs/", "alert")]
    assert st.running is False and st.starting is False


def test_start_is_ignored_while_the_worker_is_alive(monkeypatch):
    release = threading.Event()
    calls = []

    def run_live(cfg, dry, ev):
        calls.append(1)
        release.wait(5)
    _fake_app(monkeypatch, run_live, lambda: {})
    st = _FakeState()
    sess = gui._WebSession({}, st)
    sess.start()
    first = sess.thread
    sess.start()                       # e.g. a double-click during the recap
    assert sess.thread is first
    release.set()
    first.join(5)
    assert calls == [1]


def test_with_the_real_live_state(monkeypatch):
    webserver = pytest.importorskip("webserver")

    def run_live(cfg, dry, ev):
        raise ConnectionRefusedError("[WinError 10061] refused")
    _fake_app(monkeypatch, run_live, lambda: {})
    st = webserver.LiveState()
    sess = gui._WebSession({}, st)
    sess.start()
    sess.thread.join(5)
    assert st.last_error.startswith("Can't reach OBS")
    assert st.starting is False and st.running is False


def test_tk_panel_keeps_an_error_line(monkeypatch):
    panel = gui.ControlPanel.__new__(gui.ControlPanel)
    panel._last_error = ""
    shown = []
    panel._set_status = lambda text, color, sub=None: shown.append((text, color, sub))
    panel._drain_line("ERROR: Can't reach OBS. (details: logs/session_x.log)")
    assert panel._last_error.startswith("ERROR: Can't reach OBS")
    assert shown and shown[0][1] == gui.RED
    panel._log("Setting saved: kill_sound = True")   # no widgets yet: no crash


# --- one copy at a time, and the port -------------------------------------------

def test_bind_host_needs_both_switches_for_the_wifi():
    assert gui._bind_host({}) == "0.0.0.0"          # Stan's config: keys absent
    assert gui._bind_host({"web_lan": True, "web_dashboard": True}) == "0.0.0.0"
    assert gui._bind_host({"web_lan": False}) == "127.0.0.1"
    assert gui._bind_host({"web_dashboard": False}) == "127.0.0.1"


@pytest.fixture
def real_witness():
    """The real dashboard server on a free port (port 0), shut down after."""
    webserver = pytest.importorskip("webserver")
    st = webserver.LiveState()
    srv = webserver.start_web(st, 0, os.path.dirname(gui.__file__), host="127.0.0.1")
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_probe_finds_a_running_witness(real_witness):
    assert gui._witness_already_running(real_witness) is True


def test_probe_ignores_some_other_server():
    import http.server

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), Other)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        assert gui._witness_already_running(srv.server_address[1]) is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_probe_on_a_closed_port_is_false():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                        # nothing listens there now
    assert gui._witness_already_running(port) is False


def test_configured_port_reads_the_files(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))
    assert gui._configured_port() == 8000
    (tmp_path / "config.yaml").write_bytes(
        "﻿# WITNESS — config\nweb_port: 8123\n".encode("utf-8"))
    assert gui._configured_port() == 8123
    (tmp_path / "settings_override.yaml").write_text("web_port: 8124\n",
                                                     encoding="utf-8")
    assert gui._configured_port() == 8124


def _webview_env(monkeypatch, cfg):
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    monkeypatch.setattr(gui, "app", types.SimpleNamespace(load_config=lambda: cfg))


def test_port_in_use_is_a_message_not_a_hop_or_the_tk_panel(monkeypatch, fatal):
    webserver = pytest.importorskip("webserver")
    _webview_env(monkeypatch, {"web_port": 8000})
    monkeypatch.setattr(gui, "_witness_already_running", lambda *a, **k: False)
    ports = []

    def busy(state, port, base, host="0.0.0.0"):
        ports.append(port)
        raise OSError(10048, "Only one usage of each socket address")
    monkeypatch.setattr(webserver, "start_web", busy)
    with pytest.raises(_Stop):
        gui._run_webview()
    assert ports == [8000]                       # tried once, no 8001..8010
    assert fatal[0].startswith("Port 8000 is in use. WITNESS may already be "
                               "running, or change web_port in config.yaml.")


def test_second_copy_stops_before_binding(monkeypatch, fatal):
    webserver = pytest.importorskip("webserver")
    _webview_env(monkeypatch, {"web_port": 8000})
    monkeypatch.setattr(gui, "_witness_already_running", lambda *a, **k: True)
    monkeypatch.setattr(webserver, "start_web",
                        lambda *a, **k: pytest.fail("bound a second server"))
    with pytest.raises(_Stop):
        gui._run_webview()
    assert fatal[0].startswith("WITNESS is already running.")


def test_web_dashboard_false_binds_loopback_and_keeps_the_window_fed(monkeypatch, fatal):
    webserver = pytest.importorskip("webserver")
    cfg = {"web_port": 8000, "web_lan": True, "web_dashboard": False}
    _webview_env(monkeypatch, cfg)
    monkeypatch.setattr(gui, "_witness_already_running", lambda *a, **k: False)
    hosts = []

    def capture(state, port, base, host="0.0.0.0"):
        hosts.append(host)
        raise OSError(98, "stop here")
    monkeypatch.setattr(webserver, "start_web", capture)
    with pytest.raises(_Stop):
        gui._run_webview()
    assert hosts == ["127.0.0.1"]
    assert cfg["web_dashboard"] is True


def test_boot_checks_for_a_running_copy_before_the_update_check():
    src = inspect.getsource(gui._boot)
    assert "_witness_already_running(_configured_port())" in src
    assert src.index("_witness_already_running(") < src.index("tk.Tk()")


# --- auto-update at launch -------------------------------------------------------

def test_boot_starts_the_update_check_and_drops_the_debug_text():
    src = inspect.getsource(gui._boot)
    assert "threading.Thread(target=check_updates" in src
    assert src.index("threading.Thread(target=check_updates") < src.index("_show_splash(")
    full = inspect.getsource(gui)
    assert "(no update message)" not in full
    # staging only: the relaunching helper replaces the process from a thread
    assert "update_and_relaunch_if_needed(" not in full


def test_relaunch_only_from_the_splash_before_import_main():
    src = inspect.getsource(gui._boot)
    hold = src[src.index("def hold():"):]
    assert "_relaunch_updated(" in hold
    assert "_relaunch_updated(" not in src[src.index("def finish():"):src.index("def hold():")]
    assert "_relaunch_updated" not in inspect.getsource(gui._run_webview)
    assert "_relaunch_updated" not in inspect.getsource(gui._late_update_msg)


def _boot_state(**kw):
    b = gui._new_boot_state()
    b.update(kw)
    return b


@pytest.mark.parametrize("elapsed,intro,state,want", [
    (0.5, False, {}, "wait"),                                  # intro playing
    (2.0, True, {"done": True}, "leave"),
    (0.5, False, {"done": True}, "wait"),                      # let it play
    (0.5, False, {"done": True, "changed": True}, "relaunch"),  # no need to wait
    (3.0, True, {"done": True, "changed": True}, "relaunch"),
    (5.0, True, {"can_abort": True}, "wait"),                  # check in flight
    (gui.UPDATE_HOLD_CAP, True, {"can_abort": True}, "abort"),
    # pip adding a package for files already written: hold longer
    (gui.UPDATE_HOLD_CAP + 5, True, {"can_abort": True, "phase": "installing"}, "wait"),
    (gui.LONG_HOLD_CAP, True, {"can_abort": True, "phase": "installing"}, "abort"),
    # an updater without ABORT_WRITES can't be stopped from writing late
    (gui.UPDATE_HOLD_CAP + 5, True, {}, "wait"),
    (gui.LONG_HOLD_CAP, True, {}, "abort"),
])
def test_splash_step(elapsed, intro, state, want):
    assert gui._splash_step(elapsed, intro, _boot_state(**state)) == want


def _fake_updater(monkeypatch, **attrs):
    mod = types.ModuleType("updater")

    def never(*a, **k):
        raise AssertionError("the launch check must not relaunch from a thread")
    mod.update_and_relaunch_if_needed = never
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, "updater", mod)
    return mod


def test_check_stages_and_installs_new_requirements(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))
    calls = []
    _fake_updater(
        monkeypatch,
        check_and_update=lambda base: (True, "Updated 2 file(s) to abc1234"),
        LAST_CHANGED_FILES=["main.py", "requirements.txt"],
        ensure_requirements=lambda base: calls.append(base) or (True, "installed zeroconf"))
    b = gui._new_boot_state()
    gui.check_updates(b)
    assert b["done"] and b["evt"].is_set() and b["changed"] is True
    assert calls == [str(tmp_path)] and b["note"] == "installed zeroconf"
    assert "Updated 2 file(s)" in (tmp_path / "update_status.txt").read_text(
        encoding="utf-8")


def test_check_works_with_the_updater_before_it_learns_new_tricks(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))
    _fake_updater(monkeypatch, check_and_update=lambda base: (False, "Up to date (abc1234)."))
    b = gui._new_boot_state()
    gui.check_updates(b)
    assert b["done"] and not b["changed"] and b["msg"] == "Up to date (abc1234)."
    assert b["can_abort"] is False
    gui._abort_update_writes()                     # no ABORT_WRITES yet: no-op


def test_check_failure_is_a_message(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))

    def boom(base):
        raise OSError("no route to host")
    _fake_updater(monkeypatch, check_and_update=boom)
    b = gui._new_boot_state()
    gui.check_updates(b)
    assert b["done"] and not b["changed"]
    assert b["msg"].startswith("Update check skipped: no route to host")


def test_abort_sets_the_updaters_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))
    ev = threading.Event()
    _fake_updater(monkeypatch, ABORT_WRITES=ev,
                  check_and_update=lambda base: (False, "Up to date (abc1234)."))
    b = gui._new_boot_state()
    gui.check_updates(b)
    assert b["can_abort"] is True
    gui._abort_update_writes()
    assert ev.is_set()


def test_relaunch_carries_the_marker_and_the_note(monkeypatch):
    seen = {}

    def fake_popen(argv, cwd=None, env=None, **k):
        seen.update(argv=argv, cwd=cwd, env=env)
    monkeypatch.setattr(gui.subprocess, "Popen", fake_popen)

    def fake_exit(code):
        raise _Stop(code)
    monkeypatch.setattr(gui.os, "_exit", fake_exit)
    with pytest.raises(_Stop):
        gui._relaunch_updated("installed zeroconf")
    assert seen["argv"] == [sys.executable] + list(sys.argv)
    assert seen["cwd"] == gui.BASE
    assert seen["env"]["MKR_UPDATED"] == "1"
    assert seen["env"]["MKR_UPDATE_NOTE"] == "installed zeroconf"


def test_relaunch_that_cannot_start_returns_instead_of_exiting(monkeypatch):
    def broken(*a, **k):
        raise OSError("access denied")
    monkeypatch.setattr(gui.subprocess, "Popen", broken)
    monkeypatch.setattr(gui.os, "_exit", lambda code: pytest.fail("exited"))
    assert "access denied" in gui._relaunch_updated()


def test_relaunched_copy_skips_the_check(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))
    monkeypatch.setenv("MKR_UPDATED", "1")
    monkeypatch.setenv("MKR_UPDATE_NOTE", "installed zeroconf")
    b = gui._new_boot_state()
    assert gui._take_relaunch_marker(b) is True
    assert "MKR_UPDATED" not in os.environ and "MKR_UPDATE_NOTE" not in os.environ
    assert b["done"] and b["msg"].startswith("Restarted on the latest version")
    assert "installed zeroconf" in b["msg"]
    assert gui._take_relaunch_marker(gui._new_boot_state()) is False


def test_late_result_reaches_the_dashboard():
    st = _FakeState()
    b = gui._new_boot_state()
    t = threading.Thread(target=gui._late_update_msg, args=(st, b), daemon=True)
    t.start()
    for _ in range(100):
        if getattr(st, "_updating", False):
            break
        time.sleep(0.01)
    assert st._updating is True                      # Check for update disabled
    b.update(msg="Updated 1 file(s) to abc1234: gui.py", changed=True)
    b["done"] = True
    b["evt"].set()
    t.join(5)
    assert st.update_msg.startswith("Updated 1 file(s)")
    assert st.update_changed is True and st._updating is False
