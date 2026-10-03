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
