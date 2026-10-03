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


_YAML_TYPO = ("while scanning a simple key\n  in \"config.yaml\", line 41, column 1\n"
              "could not find expected ':'")


def test_a_failed_reload_stays_in_front_of_the_start_error(monkeypatch):
    """F29's own scenario: obs.password fixed, a YAML typo left behind. The
    reload fails, the old password is kept, OBS rejects it. The banner must
    lead with the unreadable file, not tell the user to redo the edit."""
    def broken():
        raise ValueError(_YAML_TYPO)

    def run_live(cfg, dry, ev):
        raise _OBSSDKError("failed to identify client with the server")
    _fake_app(monkeypatch, run_live, broken)
    st = _FakeState()
    sess = gui._WebSession({"obs": {"password": "old"}}, st)
    sess.start()
    sess.thread.join(5)
    assert st.last_error.startswith("config.yaml could not be read - using "
                                    "launch settings: while scanning")
    assert "line 41" in st.last_error and "\n" not in st.last_error
    assert " / OBS rejected the password" in st.last_error
    assert st.last_error.endswith("(details: logs/session_2026-10-02_20-00-00.log)")


def test_a_failed_reload_is_said_again_once_the_session_is_live(monkeypatch):
    monkeypatch.setattr(gui, "_LIVE_POLL", 0.01)
    st = _FakeState()
    seen = threading.Event()

    def broken():
        raise ValueError(_YAML_TYPO)

    def run_live(cfg, dry, ev):
        st.notices.clear()          # main.py: web.reset() wipes the feed
        st.set_running(True)
        for _ in range(300):
            if st.notices:
                break
            time.sleep(0.01)
        seen.set()
    _fake_app(monkeypatch, run_live, broken)
    sess = gui._WebSession({}, st)
    sess.start()
    sess.thread.join(5)
    assert seen.is_set()
    assert st.notices == [(gui._RELOAD_NOTICE, "alert")]
    assert len(gui._RELOAD_NOTICE) <= 60


def test_no_reload_notice_when_the_reload_worked(monkeypatch):
    monkeypatch.setattr(gui, "_LIVE_POLL", 0.01)
    st = _FakeState()

    def run_live(cfg, dry, ev):
        st.set_running(True)
        time.sleep(0.1)
    _fake_app(monkeypatch, run_live, lambda: {})
    sess = gui._WebSession({}, st)
    sess.start()
    sess.thread.join(5)
    assert st.notices == []


def test_tk_crash_after_going_live_is_not_a_start_error():
    """F01 risk (b): a crash in detection or the recap after a good night must
    not put a red COULD NOT START on the idle screen. The ERROR line is
    queued before 'stopped', so _watching is still True when it arrives."""
    panel = gui.ControlPanel.__new__(gui.ControlPanel)
    panel._last_error = ""
    shown = []
    panel._set_status = lambda text, color, sub=None: shown.append((text, color, sub))
    panel._drain_line("Detecting [popup] at 5 fps via obs. Region=None. LIVE. "
                      "Ctrl-C to stop.")
    panel._drain_line("ERROR: RuntimeError: recap build blew up "
                      "(details: logs/session_x.log)")
    assert panel._last_error == ""
    assert "COULD NOT START" not in shown[-1][0]
    assert "SESSION ENDED WITH AN ERROR" in shown[-1][0]
    assert shown[-1][1] != gui.RED
    text, color = panel._idle_note_text()
    assert "recap build blew up" in text and color != gui.RED
    # a START that never went live is still a start error, in red
    panel._watching = False
    panel._drain_line("ERROR: Can't reach OBS. (details: logs/session_y.log)")
    assert "COULD NOT START" in shown[-1][0] and shown[-1][1] == gui.RED
    assert panel._idle_note_text() == (panel._last_error, gui.RED)


def test_tk_start_failure_keeps_the_reload_error_first():
    panel = gui.ControlPanel.__new__(gui.ControlPanel)
    panel._last_error = ""
    panel._cfg_err = "config.yaml could not be read - using launch settings: " + _YAML_TYPO
    shown = []
    panel._set_status = lambda text, color, sub=None: shown.append((text, color, sub))
    panel._drain_line("ERROR: OBS rejected the password - copy it into "
                      "obs.password in config.yaml. (details: logs/session_x.log)")
    assert panel._last_error.startswith("ERROR: config.yaml could not be read")
    assert " / OBS rejected the password" in panel._last_error
    assert "\n" not in panel._last_error
    assert shown[0][1] == gui.RED
    assert panel._session_sub(75) == "SESSION  1:15   " + gui._RELOAD_NOTICE
    panel._cfg_err = ""
    assert panel._session_sub(75) == "SESSION  1:15"


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


def test_two_starts_at_once_run_one_session(monkeypatch):
    """The PC window and the iPad can POST /start at the same moment, each on
    its own server thread, and the config reload sits between the is_alive
    check and the thread start. The barrier makes the overlap certain: if
    both calls get into load_config together it passes and each would start
    a session (two OCR loops, two OBS sessions). Serialised, the first call
    waits out the barrier alone, which only makes its reload fail, and the
    second finds the session already running."""
    release = threading.Event()
    calls = []
    both_in = threading.Barrier(2, timeout=0.5)

    def load():
        both_in.wait()
        return {}

    def run_live(cfg, dry, ev):
        calls.append(1)
        release.wait(5)
    _fake_app(monkeypatch, run_live, load)
    st = _FakeState()
    sess = gui._WebSession({}, st)
    callers = [threading.Thread(target=sess.start) for _ in range(2)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(5)
    time.sleep(0.1)
    release.set()
    if sess.thread:
        sess.thread.join(5)
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
    gui._abort_update_writes(b)                    # no ABORT_WRITES yet: no-op


def test_check_failure_is_a_message(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))

    def boom(base):
        raise OSError("no route to host")
    _fake_updater(monkeypatch, check_and_update=boom)
    b = gui._new_boot_state()
    gui.check_updates(b)
    assert b["done"] and not b["changed"]
    assert b["msg"].startswith("Update check skipped: no route to host")


def test_abort_sets_the_updaters_flag_until_the_check_ends(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "BASE", str(tmp_path))
    ev = threading.Event()
    seen = []

    def check(base):
        gui._abort_update_writes(b)      # the splash gives up mid-check
        seen.append(ev.is_set())         # the updater sees it before writing
        return False, "Update deferred to next launch"
    _fake_updater(monkeypatch, ABORT_WRITES=ev, check_and_update=check)
    b = gui._new_boot_state()
    gui.check_updates(b)
    assert b["can_abort"] is True and seen == [True]
    assert not ev.is_set()       # cleared: the dashboard's button works again


def test_abort_after_the_check_already_finished_leaves_no_flag(monkeypatch):
    ev = threading.Event()
    _fake_updater(monkeypatch, ABORT_WRITES=ev)
    b = gui._new_boot_state()
    b["done"] = True                     # finished between the splash's two reads
    gui._abort_update_writes(b)
    assert not ev.is_set()


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


def test_a_result_that_lands_after_the_splash_still_reaches_the_dashboard(monkeypatch):
    """The cap fired, and the check finished during the fade or `import main`:
    finish() had already read UPDATE_MSG, and the event is set by the time the
    window opens, so no late thread starts either. Files were written under
    the running app, so the dashboard must say RESTART WITNESS."""
    fake_app, wv, calls, done = _full_webview_env(
        monkeypatch, {"web_port": 8000, "web_lan": False})
    monkeypatch.setattr(gui, "UPDATE_MSG", "")
    b = gui._new_boot_state()
    b.update(msg="Updated 3 file(s) to abc1234: main.py ...", changed=True,
             done=True, aborted=True)
    b["evt"].set()
    assert gui._run_webview(b) is True
    st = fake_app._web_state
    assert st.update_msg.startswith("Updated 3 file(s)")
    assert getattr(st, "update_changed", False) is True


def test_a_failed_relaunch_still_says_restart(monkeypatch):
    fake_app, wv, calls, done = _full_webview_env(
        monkeypatch, {"web_port": 8000, "web_lan": False})
    b = gui._new_boot_state()
    b.update(msg="Updated 1 file(s)  (could not restart by itself: OSError: x)",
             done=True, restart_needed=True)
    b["evt"].set()
    monkeypatch.setattr(gui, "UPDATE_MSG", b["msg"])
    assert gui._run_webview(b) is True
    st = fake_app._web_state
    assert "could not restart by itself" in st.update_msg
    assert st.update_changed is True


def test_tk_fallback_shows_an_update_result_that_landed_late():
    panel = gui.ControlPanel.__new__(gui.ControlPanel)
    panel._last_error = ""
    logged = []
    panel._log = logged.append
    b = gui._new_boot_state()
    panel._boot = b
    panel._poll_boot()
    assert logged == []                             # still checking
    b.update(msg="Updated 2 file(s) to abc1234: gui.py", changed=True, done=True)
    b["evt"].set()
    panel._poll_boot()
    panel._poll_boot()                              # once only
    assert len(logged) == 1
    assert logged[0].startswith("Updated 2 file(s)")
    assert "restart WITNESS" in logged[0]
    assert gui._update_text({"msg": "Already up to date.", "changed": False}) \
        == "Already up to date."


# --- the tk panel's tiles come from the tally ------------------------------------

# Event lines from a real session log (2026-08-08), in order. The game's
# panels said 2 + 2 downs; the log's own tally was downs 4, precision 1,
# assists 11. The old tiles (KILL # lines only, DOWNS = down + kill) showed
# 6 / 0 / 0 / 0.
REAL_0808 = [
    "(not a kill: assist) [assist]: 'RUNNER DOIN [ASSIST] +15 XP'",
    "(not a kill: assist) [assist]: 'runner doin assist 15 xp'",
    "(not a kill: assist) [assist]: 'precision doin assist tt 25 i'",
    "(not a kill: assist) [assist]: 'runner elim assist 10 xp'",
    "(not a kill: assist) [assist]: 'RUNNER ELIM [ASSIST] +10 XP'",
    "(not a kill: assist) [assist]: 'RUNNER DOIN [ASSIST] +15 XP'",
    "KILL #1 [kill]: 'SMSTE RUNNER ELIM +10 XP'",
    "KILL #2 [down]: 'runner doin 15 xp'",
    "(not a kill: precision) [precision]: 'precision doin 5z 25'",
    "(not a kill: assist) [assist]: 'RUNNER DOWN [ASSIST] 1 +15 XP'",
    "(not a kill: assist) [assist]: 'runner elim assist 10 xp'",
    "  [exfil] match stats: 1 runner elims, 2 downs, 29 combatants, 698 dmg",
    "  [reconcile] game credited more downs than detected — added 1, count now 3",
    "(not a kill: assist) [assist]: 'RUNNER DOIN [ASSIST] +15 XP'",
    "KILL #4 [kill]: 'runner elim 10 xp'",
    "KILL #5 [down]: 'RUNNER DOIN +15 XP'",
    "(not a kill: kill) [kill]: 'runner elim 10 xp'",
    "KILL #6 [down]: 'RUNNER DOIN +15 XP'",
    "KILL #7 [kill]: 'RUNNER ELIM +10 XP'",
    "(not a kill: assist) [assist]: '9i6 RUNNER DOIN [ASSIST] +15 XP ACH ATT^ ATTACH'",
    "(not a kill: assist) [assist]: 'runner elim assist 10 xp'",
    "  [exfil] match stats: 3 runner elims, 2 downs, 16 combatants, 585 dmg",
]


def test_gui_tiles_follow_the_tally():
    tags = []
    for line in REAL_0808:
        gui.parse_console_line(line, tags)
    assert gui.tile_values(tags) == {"downs": 4, "precision": 1,
                                     "finishers": 0, "assists": 11}


def test_tiles_through_the_panels_drain(monkeypatch):
    panel = gui.ControlPanel.__new__(gui.ControlPanel)
    panel.tags, panel.recent, panel.latest, panel._last_error = [], [], None, ""
    panel._refresh_live = lambda: None
    panel._set_status = lambda *a, **k: None
    # the exfil report is ONE print() carrying several lines
    chunk = REAL_0808[11] + "\n  [audit] downs: game 2, detected 1 — missed 1"
    for line in REAL_0808[:11] + chunk.splitlines() + REAL_0808[12:]:
        panel._drain_line(line)
    assert gui.tile_values(panel.tags)["downs"] == 4
    assert [t for _, t in panel.recent] == ["kill", "down", "kill", "down",
                                            "down", "kill"]   # KILL # lines only
    assert panel.latest[0] == "kill"


def test_parse_console_line_reads_not_a_kill_and_reconcile_lines():
    tags = []
    gui.parse_console_line("(not a kill: finisher) [finisher]: 'FINISHER +50'", tags)
    gui.parse_console_line("  [reconcile] game credited more runner elims than "
                           "detected — added 2", tags)
    assert tags == ["finisher", "elim", "elim"]
    for noise in ("  [exfil] outcome: exfiltrated",
                  "  [reconcile] the game shows at least 3 runner(s) downed or "
                  "eliminated — added 1, count now 9",     # headline floor
                  "Session over: 9 kills (downs 4, elims 5)",
                  "RESULT: 3 kills", ""):
        gui.parse_console_line(noise, tags)
    assert tags == ["finisher", "elim", "elim"]


def test_reconciled_events_land_before_the_match_break():
    brk = gui._match_break()
    tags = []
    for line in ("KILL #1 [kill]: 'RUNNER ELIM +10 XP'",
                 "  [exfil] match stats: 2 runner elims, 0 downs",
                 "  [reconcile] game credited more runner elims than detected "
                 "— added 1",
                 "KILL #3 [finisher]: 'FINISHER +50'"):
        gui.parse_console_line(line, tags)
    # main.py's session_tags order: reconciled elims close the match they
    # belong to, so the next match's opening FINISHER can't fold into one
    assert tags == ["kill", "elim", brk, "finisher"]


def test_parse_console_line_without_match_break_attr_does_not_raise(monkeypatch):
    import exfil_stats
    monkeypatch.delattr(exfil_stats, "MATCH_BREAK", raising=False)
    tags = []
    gui.parse_console_line("  [exfil] match stats: 1 runner elims, 2 downs", tags)
    assert tags == ["exfil"]
    assert gui.tile_values(tags + ["down"])["downs"] == 1


# --- small fixes ----------------------------------------------------------------

def test_every_text_open_in_gui_names_its_encoding():
    """config.yaml is UTF-8 with em dashes; Windows' default code page can't
    decode it on CJK systems (cp932...), and mangles it everywhere else."""
    import ast
    with open(gui.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    bad = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            mode = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg == "mode"), None)
            binary = (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                      and "b" in mode.value)
            if not binary and not any(k.arg == "encoding" for k in node.keywords):
                bad.append(node.lineno)
    assert not bad, f"open() without encoding= at gui.py lines {bad}"


def test_read_yaml_accepts_a_notepad_bom(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes("﻿# WITNESS — configuration\nsplash_sound: false\n"
                  .encode("utf-8"))
    assert gui._read_yaml(str(p)) == {"splash_sound": False}


def test_help_text_no_longer_points_at_a_log_pane():
    assert "activity log" not in gui.HELP_TEXT
    assert "printed" not in gui.HELP_TEXT
    assert "shows the address" in gui.HELP_TEXT


def test_tk_panel_shows_the_phone_address():
    panel = gui.ControlPanel.__new__(gui.ControlPanel)
    panel._last_error = ""
    scheme = "http"
    panel._drain_line(f"Live view: {scheme}://192.168.1.20:8000  "
                      "(open in your iPad/phone browser on the same Wi-Fi)")
    assert panel._phone_addr.endswith("192.168.1.20:8000")
    assert "(open" not in panel._phone_addr


def test_tk_panel_shows_no_lan_address_when_web_lan_is_false():
    """main.py prints the LAN 'Live view' address whatever web_lan says, but
    with web_lan: false it binds 127.0.0.1: that address refuses the iPad."""
    panel = gui.ControlPanel.__new__(gui.ControlPanel)
    panel._last_error = ""
    panel.cfg = {"web_lan": False}
    scheme = "http"
    panel._drain_line(f"Live view: {scheme}://192.168.1.20:8000  "
                      "(open in your iPad/phone browser on the same Wi-Fi)")
    assert "192.168" not in panel._phone_addr
    assert "this PC only" in panel._phone_addr and "web_lan" in panel._phone_addr
    panel.cfg = {}                                   # Stan's config: key absent
    panel._drain_line(f"Live view: {scheme}://192.168.1.20:8000")
    assert panel._phone_addr.endswith("192.168.1.20:8000")


# --- Bonjour in window mode ------------------------------------------------------

def _full_webview_env(monkeypatch, cfg):
    webserver = pytest.importorskip("webserver")
    wv = types.ModuleType("webview")
    wv.windows = []
    wv.create_window = lambda *a, **k: wv.windows.append((a, k))
    wv.start = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webview", wv)
    fake_app = types.SimpleNamespace(
        load_config=lambda: cfg, save_setting_overrides=lambda d: None,
        cached_record_dir=lambda: "", resume_unfinished_recap=lambda st: None,
        _web_state=None, _web_server=None, _advertiser=None)
    monkeypatch.setattr(gui, "app", fake_app)
    monkeypatch.setattr(gui, "_witness_already_running", lambda *a, **k: False)
    monkeypatch.setattr(webserver, "start_web", lambda st, port, base, host="": object())
    monkeypatch.setattr(webserver, "local_ip", lambda: "192.168.1.20")
    calls, done = [], threading.Event()
    disc = types.ModuleType("discovery")

    def advertise(port, ip, version="", api=1):
        calls.append((port, ip, api))
        done.set()
        return "ADVERTISER"
    disc.advertise = advertise
    monkeypatch.setitem(sys.modules, "discovery", disc)
    return fake_app, wv, calls, done


def test_window_mode_advertises_for_the_ipad_app(monkeypatch):
    fake_app, wv, calls, done = _full_webview_env(
        monkeypatch, {"web_port": 8000, "web_lan": True})
    assert gui._run_webview() is True
    assert done.wait(5)
    assert calls[0][:2] == (8000, "192.168.1.20")
    for _ in range(100):
        if fake_app._advertiser:
            break
        time.sleep(0.01)
    assert fake_app._advertiser == "ADVERTISER"   # main.py won't advertise twice
    assert wv.windows and wv.windows[0][0][1].endswith(":8000")


@pytest.mark.parametrize("cfg", [
    {"web_lan": False},
    {"web_dashboard": False},
    {"bonjour_discovery": False},
])
def test_no_advertising_when_not_served_on_the_lan(monkeypatch, cfg):
    fake_app, wv, calls, done = _full_webview_env(monkeypatch, dict(cfg, web_port=8000))
    assert gui._run_webview() is True
    assert not done.wait(0.3)
    assert calls == []
