"""OBS setup mistakes get named, not retried in silence.

Before: a wrong or CHANGE_ME password, and a WebSocket server that was never
switched on, all printed "OBS is still starting up — waiting for it..." for 30 s
and then raised the raw library error. An OBS that was already open got
launched a second time. Every message here starts with "OBS": the desktop
launcher shows such a message as-is in its START error banner.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _Flaky:
    """obs.connect() that raises the given errors in order, then succeeds."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def connect(self):
        self.calls += 1
        if self.errors:
            e = self.errors.pop(0)
            if e is not None:
                raise e


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(main, "OBS_CONNECT_WAIT", 0)


# --- the password ---------------------------------------------------------------

def test_change_me_is_treated_as_no_password():
    assert main._obs_password({"password": "CHANGE_ME"}) == ""
    assert main._obs_password({"password": " CHANGE_ME "}) == ""
    assert main._obs_password({"password": "hunter2"}) == "hunter2"
    assert main._obs_password({"password": None}) == ""
    assert main._obs_password({}) == ""
    assert main._obs_password({"password": 123456}) == "123456"   # YAML number


def test_setup_hands_obs_an_empty_password_for_change_me(monkeypatch):
    import obs_client
    made = {}

    class _Client:
        def __init__(self, **kw):
            made.update(kw)

        def connect(self):
            pass

        def set_counter(self, n):
            pass

        def replay_buffer_active(self):
            return True

        def get_record_directory(self):
            return ""

    monkeypatch.setattr(obs_client, "OBSClient", _Client)
    monkeypatch.setattr(main, "_ensure_obs_running", lambda cfg: "up")
    cfg = {"obs": {"password": "CHANGE_ME"}, "web_dashboard": False,
           "announcer_medals": False, "heat_streaks": False,
           "detection_canary": False}
    main._setup_session(cfg, dry_run=False)
    assert made["password"] == ""


def test_a_rejected_password_fails_after_3_tries_and_says_so(capsys):
    err = Exception("failed to identify client with the server, please check "
                    "connection settings")
    obs = _Flaky([err] * 10)
    with pytest.raises(RuntimeError) as ex:
        main._connect_obs(obs, "up", raw_password="hunter3")
    assert obs.calls == 3
    msg = str(ex.value)
    assert msg.startswith("OBS rejected the WebSocket password")
    assert "Show Connect Info" in msg and "obs.password" in msg
    assert "CHANGE_ME" not in msg
    assert "still starting up" not in capsys.readouterr().out


def test_a_rejected_change_me_password_says_it_is_still_change_me():
    err = Exception("authentication enabled but no password provided")
    with pytest.raises(RuntimeError) as ex:
        main._connect_obs(_Flaky([err] * 10), "up", raw_password="CHANGE_ME")
    assert str(ex.value).endswith("(it is still CHANGE_ME)")


def test_a_launched_obs_gets_a_little_longer_before_a_password_verdict():
    """A socket closed while OBS boots reads exactly like a wrong password."""
    err = Exception("failed to identify client with the server")
    obs = _Flaky([err, err, err, None])
    main._connect_obs(obs, "launched")          # 3 failures, then it answers
    assert obs.calls == 4


def test_websocket_off_is_named_after_the_retries():
    obs = _Flaky([ConnectionRefusedError(10061, "No connection could be made")] * 30)
    with pytest.raises(RuntimeError) as ex:
        main._connect_obs(obs, "disabled")
    assert obs.calls == main.OBS_CONNECT_ATTEMPTS
    assert str(ex.value).startswith("OBS: cannot reach the WebSocket server")
    assert "Enable" in str(ex.value)


def test_not_ready_keeps_retrying_and_only_a_launch_says_starting_up(capsys):
    not_ready = Exception("Request GetVersion returned code 207.")
    obs = _Flaky([not_ready, not_ready, None])
    main._connect_obs(obs, "launched")
    assert obs.calls == 3
    assert "OBS is still starting up" in capsys.readouterr().out

    obs = _Flaky([TimeoutError("timed out"), None])
    main._connect_obs(obs, "up")
    out = capsys.readouterr().out
    assert "still starting up" not in out and "retrying" in out


def test_anything_else_after_the_retries_still_starts_with_obs():
    obs = _Flaky([TimeoutError("timed out")] * 30)
    with pytest.raises(RuntimeError) as ex:
        main._connect_obs(obs, "up")
    assert str(ex.value).startswith("OBS")


# --- launching OBS ----------------------------------------------------------------

class _Run:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def _windows(monkeypatch, tmp_path, tasklist_out, ports=()):
    import subprocess
    exe = tmp_path / "obs64.exe"
    exe.write_bytes(b"")
    opened = set(ports)
    popen, runs = [], []

    def fake_run(args, **kw):
        runs.append((args, kw))
        return _Run(tasklist_out)

    def fake_popen(args, **kw):
        popen.append(args)
        opened.add(4455)            # OBS comes up after the launch
        return None

    monkeypatch.setattr(main, "_on_windows", lambda: True)
    monkeypatch.setattr(main, "OBS_BOOT_WAIT", 0)
    monkeypatch.setattr(main, "_obs_port_open",
                        lambda host, port, timeout=1.5: int(port) in opened)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return {"obs": {"exe_path": str(exe)}}, popen, runs


def test_an_open_obs_with_the_websocket_off_is_not_launched_again(monkeypatch, tmp_path):
    out = b"obs64.exe                    10432 Console    1    412,000 K\r\n"
    cfg, popen, runs = _windows(monkeypatch, tmp_path, out)
    with pytest.raises(RuntimeError) as ex:
        main._ensure_obs_running(cfg)
    assert popen == [], "OBS was already running: no second launch"
    assert str(ex.value).startswith("OBS is open but its WebSocket server is off")
    assert "port 4455" in str(ex.value)
    args, kw = runs[0]
    assert args[0] == "tasklist" and "IMAGENAME eq obs64.exe" in args
    assert "creationflags" in kw            # no console flash under pythonw


def test_obs_27_on_port_4444_gets_the_version_hint(monkeypatch, tmp_path):
    cfg, popen, _ = _windows(monkeypatch, tmp_path, b"OBS64.EXE 1 Console 1 9 K",
                             ports=(4444,))
    with pytest.raises(RuntimeError) as ex:
        main._ensure_obs_running(cfg)
    assert "OBS 28" in str(ex.value)


def test_a_localized_no_tasks_line_still_launches_obs(monkeypatch, tmp_path):
    # tasklist's "no tasks" text is localized; only the image name is matched
    cfg, popen, _ = _windows(monkeypatch, tmp_path,
                             "INFORMATION : aucune tâche".encode("cp850"))
    assert main._ensure_obs_running(cfg) == "launched"
    assert len(popen) == 1


def test_up_disabled_and_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_obs_port_open", lambda host, port, timeout=1.5: True)
    assert main._ensure_obs_running({}) == "up"
    monkeypatch.setattr(main, "_obs_port_open", lambda host, port, timeout=1.5: False)
    monkeypatch.setattr(main, "_on_windows", lambda: False)
    assert main._ensure_obs_running({}) == "disabled"
    monkeypatch.setattr(main, "_on_windows", lambda: True)
    assert main._ensure_obs_running({"obs": {"auto_launch": False}}) == "disabled"
    monkeypatch.setattr(main, "_obs_process_running", lambda: False)
    missing = str(tmp_path / "nope" / "obs64.exe")
    assert main._ensure_obs_running({"obs": {"exe_path": missing}}) in ("not_found",)


def test_setup_names_a_missing_obs_instead_of_waiting(monkeypatch):
    monkeypatch.setattr(main, "_ensure_obs_running", lambda cfg: "not_found")
    with pytest.raises(RuntimeError) as ex:
        main._setup_session({"web_dashboard": False}, dry_run=False)
    assert str(ex.value).startswith("OBS is not running and obs64.exe was not found")
    assert "obs.exe_path" in str(ex.value)


# --- Replay Buffer off ------------------------------------------------------------
# Disabled in OBS settings: kills count, no clip saves, and the only trace was
# one WARNING line from obs_client in a console the window app doesn't have.

class _Client:
    def __init__(self, rb=True, saves=True):
        self.rb, self.saves = rb, saves

    def connect(self):
        pass

    def set_counter(self, n):
        pass

    def replay_buffer_active(self):
        return self.rb

    def save_replay(self):
        return self.saves

    def get_record_directory(self):
        return ""

    def get_scene_screenshot(self, width=480):
        return b""                       # inconclusive black check: no alarm


class _Web:
    def __init__(self):
        self.notices, self.record_dir = [], ""

    def reset(self):
        self.notices.clear()

    def bind_config(self, cfg, cb):
        pass

    def set_running(self, running):
        pass

    def notice(self, text, tag="alert"):
        assert len(text) <= 60, "LiveState.notice cuts at 60 chars"
        self.notices.append(text)


def _setup_with(monkeypatch, client, web):
    import obs_client
    monkeypatch.setattr(obs_client, "OBSClient", lambda **kw: client)
    monkeypatch.setattr(main, "_ensure_obs_running", lambda cfg: "up")
    monkeypatch.setattr(main, "_web_server", object())    # don't bind a port
    monkeypatch.setattr(main, "_web_state", web)
    cfg = {"announcer_medals": False, "heat_streaks": False,
           "detection_canary": False}
    return main._setup_session(cfg, dry_run=False)


def test_a_replay_buffer_that_is_off_is_reported_at_start(monkeypatch, capsys):
    web = _Web()
    monkeypatch.setattr(main, "REPLAY_BUFFER_SETTLE_STEP", 0)
    _setup_with(monkeypatch, _Client(rb=None), web)
    assert "Replay Buffer OFF in OBS - clips will NOT save" in web.notices
    out = capsys.readouterr().out
    assert "Settings > Output > Replay Buffer" in out


class _SlowStartClient(_Client):
    """connect() just asked OBS to start the buffer; OBS 28+ runs that on its
    UI thread, so the status reads inactive for the first couple of polls."""
    def __init__(self, inactive_reads=2):
        super().__init__(rb=False)
        self.reads, self.inactive_reads = 0, inactive_reads

    def replay_buffer_active(self):
        self.reads += 1
        return self.reads > self.inactive_reads


def test_a_buffer_witness_just_started_is_not_reported_off(monkeypatch, capsys):
    web = _Web()
    slept = []
    monkeypatch.setattr(main.time, "sleep", lambda n: slept.append(n))
    client = _SlowStartClient(inactive_reads=2)
    _setup_with(monkeypatch, client, web)
    assert not any("Replay Buffer" in n for n in web.notices)
    assert "clips will NOT save" not in capsys.readouterr().out
    assert client.reads == 3 and sum(slept) <= 3.0


def test_the_settle_wait_is_bounded_at_about_three_seconds(monkeypatch):
    slept = []
    monkeypatch.setattr(main.time, "sleep", lambda n: slept.append(n))
    assert main._replay_buffer_ok(_Client(rb=False)) is False
    assert 2.5 <= sum(slept) <= 3.5


def test_a_running_replay_buffer_says_nothing(monkeypatch):
    web = _Web()
    _setup_with(monkeypatch, _Client(rb=True), web)
    assert not any("Replay Buffer" in n for n in web.notices)


def test_a_failed_clip_save_is_reported_once_a_minute(monkeypatch, capsys):
    web = _Web()
    s = {"obs": _Client(saves=False), "web": web, "organize": False,
         "cfg": {"announcer_medals": False}, "medal_sounds": {}, "last_save": 0.0}
    for n in (1, 2):
        s["_coalesce_pending"] = [{"tag": "down", "count": n}]
        main._flush_coalesce(s)
    assert web.notices == ["CLIP NOT SAVED - check OBS Replay Buffer"]
    assert capsys.readouterr().out.count("OBS did not save a replay") == 2
    s["_clip_fail_notice_at"] -= 61                    # a minute later
    s["_coalesce_pending"] = [{"tag": "down", "count": 3}]
    main._flush_coalesce(s)
    assert len(web.notices) == 2


def test_manual_and_watchlist_saves_report_a_failure_too(monkeypatch):
    web = _Web()
    web.pop_clip_request = lambda: True
    s = {"obs": _Client(saves=False), "web": web, "organize": False,
         "cfg": {}, "count": 0, "last_save": 0.0, "min_save": 0.0, "clutch": False}
    main._check_manual_clip(s)
    assert web.notices == ["CLIP NOT SAVED - check OBS Replay Buffer"]
    s["_clip_fail_notice_at"] = -1e9
    monkeypatch.setattr(main, "show_text_overlay", lambda *a, **k: None)
    main._streamer_alert({"announcer_medals": False}, s, "killed_by", "SOMESTREAMER")
    assert web.notices.count("CLIP NOT SAVED - check OBS Replay Buffer") == 2


def test_an_obs_that_is_still_booting_gets_a_moment(monkeypatch, tmp_path):
    """OBS opened a second before START: the process exists, the port opens a
    few seconds later. That is not 'WebSocket server off'."""
    import subprocess
    checks = []

    def port_open(host, port, timeout=1.5):
        checks.append(port)
        return len(checks) >= 3            # closed, closed, then up

    monkeypatch.setattr(main, "_on_windows", lambda: True)
    monkeypatch.setattr(main, "OBS_BOOT_WAIT", 5)
    monkeypatch.setattr(main, "_obs_port_open", port_open)
    monkeypatch.setattr(main, "_obs_process_running", lambda: True)
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen.append(a))
    assert main._ensure_obs_running({}) == "up"
    assert popen == []


def test_a_slow_booting_obs_gets_the_full_launch_wait(monkeypatch, capsys):
    """A plugin-heavy OBS opened just before START can take longer than 10 s to
    open its port. It gets the same 30 s as an OBS we launch, with a visible
    'waiting' line, instead of a false 'WebSocket server is off'."""
    import subprocess
    checks, slept = [], []

    def port_open(host, port, timeout=1.5):
        checks.append(port)
        return len(checks) >= 15           # first check + 13 s of waiting, then up

    monkeypatch.setattr(main, "_on_windows", lambda: True)
    monkeypatch.setattr(main, "_obs_port_open", port_open)
    monkeypatch.setattr(main, "_obs_process_running", lambda: True)
    monkeypatch.setattr(main.time, "sleep", lambda n: slept.append(n))
    popen = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen.append(a))
    assert main.OBS_BOOT_WAIT >= 30
    assert main._ensure_obs_running({}) == "up"
    assert popen == []
    assert "waiting up to 30 s" in capsys.readouterr().out
