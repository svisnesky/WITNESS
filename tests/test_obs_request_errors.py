"""OBS refusing a request is not OBS going away.

The KillCounter text source is OPTIONAL (README), so most new users don't have
one. Every kill then hit OBS's "No source was found by the name of KillCounter"
(code 600), and the client treated that answer like a dropped connection:
obsws-python printed two full tracebacks, WITNESS printed a failure line, and a
whole websocket reconnect ran, inside the detection loop, on every kill.

A refused request arrives over a HEALTHY socket, so it must never reconnect. A
real drop (or a timeout) must still reconnect, exactly once, as before.
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import obs_client  # noqa: E402

error = pytest.importorskip("obsws_python.error")
Refused = error.OBSSDKRequestError
Timeout = error.OBSSDKTimeoutError

NO_SOURCE = "No source was found by the name of `KillCounter`."


def _client(fake, **kw):
    """An OBSClient on a fake socket that counts reconnects instead of
    building a real connection."""
    kw.setdefault("auto_start_replay_buffer", False)
    c = obs_client.OBSClient(**kw)
    c._client = fake
    c.reconnects = 0

    def fake_connect():
        c.reconnects += 1
    c._connect_client = fake_connect
    return c


class _Counter:
    """set_input_settings raises `exc` (or succeeds when None)."""

    def __init__(self, exc=None):
        self.exc = exc
        self.calls = 0

    def set_input_settings(self, **kw):
        self.calls += 1
        if self.exc is not None:
            raise self.exc


# --- the missing KillCounter source ------------------------------------------

def test_missing_counter_source_is_one_line_no_reconnect_then_off(capsys):
    fake = _Counter(Refused("SetInputSettings", 600, NO_SOURCE))
    c = _client(fake)

    c.set_counter(1)
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["No OBS text source named 'KillCounter' - on-stream kill "
                   "counter disabled (optional; see README)"]
    assert c.reconnects == 0, "a refused request must not reconnect"
    assert c.counter_source is None

    for n in (2, 3, 4):                # later kills: nothing sent, nothing said
        c.set_counter(n)
    assert fake.calls == 1
    assert capsys.readouterr().out == ""
    assert c.reconnects == 0


def test_the_real_library_error_shape_is_recognised(caplog, capsys):
    """Drive the real obsws-python ReqClient.send, so the test fails if the
    library ever stops raising OBSSDKRequestError with a .code, and check its
    logger.exception() no longer reaches a handler."""
    import obsws_python

    class _Base:
        def req(self, req_type, req_data=None):
            return {"requestType": req_type,
                    "requestStatus": {"result": False, "code": 600,
                                      "comment": NO_SOURCE}}

    rc = obsws_python.ReqClient.__new__(obsws_python.ReqClient)
    rc.logger = logging.getLogger("obsws_python.reqs").getChild("ReqClient")
    rc.base_client = _Base()

    lib = logging.getLogger("obsws_python")
    before = lib.level
    try:
        obs_client._quiet_obsws_logger()      # what _connect_client does
        c = _client(rc)
        with caplog.at_level(logging.DEBUG):
            c.set_counter(1)
        assert c.counter_source is None and c.reconnects == 0
        assert not [r for r in caplog.records if r.name.startswith("obsws_python")], \
            "the library's traceback would still reach the console"
        assert "No OBS text source named 'KillCounter'" in capsys.readouterr().out
    finally:
        lib.setLevel(before)


def test_other_refusals_keep_the_counter(capsys):
    """Only 'no such source' turns it off. Any other refusal is one line and
    the counter tries again on the next kill."""
    fake = _Counter(Refused("SetInputSettings", 604, "wrong state"))
    c = _client(fake)
    c.set_counter(1)
    c.set_counter(2)
    assert c.counter_source == "KillCounter"
    assert fake.calls == 2 and c.reconnects == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2 and all("OBS set counter failed" in ln for ln in lines)


def test_a_timeout_never_turns_the_counter_off():
    fake = _Counter(Timeout("Timeout while trying to send the request"))
    c = _client(fake)
    c.set_counter(1)
    assert c.counter_source == "KillCounter"
    assert c.reconnects == 1, "a timeout is a connection problem: reconnect once"
    assert fake.calls == 2


def test_an_empty_counter_source_sends_nothing():
    """counter_source: "" in config.yaml is the documented off switch."""
    fake = _Counter()
    c = _client(fake, counter_source="")
    c.set_counter(5)
    assert fake.calls == 0


def test_a_broken_counter_format_cannot_abort_the_session(capsys):
    """A typo like '{kills}' used to raise out of set_counter(0) at startup."""
    sent = []

    class _Rec:
        def set_input_settings(self, **kw):
            sent.append(kw["settings"]["text"])
    c = _client(_Rec(), counter_format="Kills: {kills}")
    c.set_counter(3)
    c.set_counter(4)
    assert sent == ["Kills: 3", "Kills: 4"]
    assert capsys.readouterr().out.count("counter_format") == 1


# --- _call: refused vs dropped -------------------------------------------------

def test_a_generic_error_still_reconnects_exactly_once():
    class _Dead:
        calls = 0

        def get_record_directory(self):
            _Dead.calls += 1
            raise RuntimeError("socket is already closed.")
    c = _client(_Dead())
    assert c.get_record_directory() == ""
    assert c.reconnects == 1
    assert _Dead.calls == 2, "one retry after the reconnect, no more"


def test_a_refused_save_does_not_reconnect_but_still_restarts_the_buffer():
    """Replay Buffer stopped: OBS refuses the save (code 501). The recovery
    path (save_replay -> recover_replay_buffer) must still run."""
    class _Stopped:
        starts = 0
        active = False

        def save_replay_buffer(self):
            if not self.active:
                raise Refused("SaveReplayBuffer", 501, "Replay buffer is not active.")

        def get_replay_buffer_status(self):
            return type("S", (), {"output_active": self.active})()

        def start_replay_buffer(self):
            self.starts += 1
            self.active = True
    fake = _Stopped()
    c = _client(fake)
    assert c.save_replay() is False
    assert fake.starts == 1 and c.reconnects == 0
    assert c.save_replay() is True


def test_a_disabled_replay_buffer_reads_as_unknown_without_reconnecting():
    class _Disabled:
        def get_replay_buffer_status(self):
            raise Refused("GetReplayBufferStatus", 604, "Replay buffer is not available.")
    c = _client(_Disabled())
    assert c.replay_buffer_active() is None
    assert c.reconnects == 0


# --- reconnect closes the old socket ------------------------------------------

class _Ws:
    def __init__(self, boom=False):
        self.shutdowns = 0
        self.boom = boom

    def shutdown(self):
        self.shutdowns += 1
        if self.boom:
            raise OSError("already dead")


class _Dropped:
    def __init__(self, ws):
        self.base_client = type("B", (), {"ws": ws})()

    def get_record_directory(self):
        raise ConnectionResetError("gone")


def test_reconnect_closes_the_old_socket_without_waiting():
    ws = _Ws()
    c = _client(_Dropped(ws))
    c.get_record_directory()
    assert c.reconnects == 1
    assert ws.shutdowns >= 1, "the old socket was left open"


def test_a_close_that_raises_does_not_block_the_reconnect():
    c = _client(_Dropped(_Ws(boom=True)))
    c.get_record_directory()
    assert c.reconnects == 1


def test_a_client_without_base_client_falls_back_to_disconnect():
    class _Plain:
        disconnects = 0

        def disconnect(self):
            _Plain.disconnects += 1
            raise RuntimeError("dead socket")    # must be swallowed

        def get_record_directory(self):
            raise ConnectionResetError("gone")
    c = _client(_Plain())
    c.get_record_directory()
    assert _Plain.disconnects >= 1 and c.reconnects == 1


def test_connect_silences_the_obsws_logger(monkeypatch):
    import obsws_python
    monkeypatch.setattr(obsws_python, "ReqClient", lambda **kw: object())
    lib = logging.getLogger("obsws_python")
    before = lib.level
    try:
        lib.setLevel(logging.NOTSET)
        obs_client.OBSClient()._connect_client()
        child = logging.getLogger("obsws_python.reqs.ReqClient")
        assert not child.isEnabledFor(logging.ERROR)
    finally:
        lib.setLevel(before)


def test_close_is_final_until_connect():
    """A clip worker polling after the session ended must not reopen OBS."""
    ws = _Ws()
    c = _client(_Dropped(ws))
    c.close()
    assert ws.shutdowns == 1
    assert c.get_record_directory() == ""
    assert c.reconnects == 0
    c.close()                                   # twice is fine


# --- replay_buffer_ok -----------------------------------------------------------

class _Fresh:
    def __init__(self, status):
        self.status = status
        self.starts = 0

    def get_version(self):
        return type("V", (), {"obs_version": "32", "obs_web_socket_version": "5"})()

    def get_replay_buffer_status(self):
        if isinstance(self.status, Exception):
            raise self.status
        return type("S", (), {"output_active": self.status})()

    def start_replay_buffer(self):
        self.starts += 1


def _connected(fake, auto=True):
    c = obs_client.OBSClient(auto_start_replay_buffer=auto)
    c._connect_client = lambda: setattr(c, "_client", fake)
    c.connect()
    return c


def test_replay_buffer_ok_after_connect():
    assert _connected(_Fresh(True)).replay_buffer_ok is True
    off = _Fresh(False)
    assert _connected(off).replay_buffer_ok is True and off.starts == 1
    disabled = _Fresh(Refused("GetReplayBufferStatus", 604, "not available"))
    assert _connected(disabled).replay_buffer_ok is False
    assert _connected(_Fresh(True), auto=False).replay_buffer_ok is None


# --- get_scene_input_kinds ------------------------------------------------------

def test_scene_kinds_is_none_on_a_client_without_the_method():
    """The suite's fakes only define screenshot/record requests. A missing
    method must return None BEFORE touching the socket (no reconnect)."""
    class _Old:
        def get_current_program_scene(self):
            return type("P", (), {"current_program_scene_name": "Scene"})()
    c = _client(_Old())
    assert c.get_scene_input_kinds() is None
    assert c.reconnects == 0
    assert _client(None).get_scene_input_kinds() is None


def _item(kind=None, name="", scene=False, group=False, enabled=True):
    return {"inputKind": kind, "sourceName": name,
            "sourceType": "OBS_SOURCE_TYPE_SCENE" if scene else "OBS_SOURCE_TYPE_INPUT",
            "isGroup": group if scene else None, "sceneItemEnabled": enabled}


class _Scenes:
    def __init__(self, scenes, program="Main", groups=None):
        self.scenes = scenes
        self.groups = groups or {}
        self.program = program

    def get_current_program_scene(self):
        return type("P", (), {"current_program_scene_name": self.program})()

    def get_scene_item_list(self, name):
        if name not in self.scenes:
            raise Refused("GetSceneItemList", 600, f"No source named {name}")
        return type("L", (), {"scene_items": self.scenes[name]})()

    def get_group_scene_item_list(self, name):
        return type("L", (), {"scene_items": self.groups[name]})()


def test_scene_kinds_reads_the_program_scene():
    c = _client(_Scenes({"Main": [_item("monitor_capture"),
                                  _item("text_gdiplus_v3", "KillCounter")]}))
    assert c.get_scene_input_kinds() == ["monitor_capture", "text_gdiplus_v3"]


def test_an_empty_scene_is_an_empty_list_not_none():
    c = _client(_Scenes({"Main": []}))
    assert c.get_scene_input_kinds() == []


def test_hidden_items_are_left_out():
    c = _client(_Scenes({"Main": [_item("monitor_capture", enabled=False),
                                  _item("game_capture")]}))
    assert c.get_scene_input_kinds() == ["game_capture"]


def test_nested_scenes_and_groups_are_opened_up():
    c = _client(_Scenes(
        {"Main": [_item(name="Gameplay", scene=True),
                  _item(name="Cams", scene=True, group=True)],
         "Gameplay": [_item("game_capture")]},
        groups={"Cams": [_item("dshow_input")]}))
    assert c.get_scene_input_kinds() == ["game_capture", "dshow_input"]


def test_a_scene_that_contains_itself_does_not_loop():
    c = _client(_Scenes({"Main": [_item(name="Loop", scene=True)],
                         "Loop": [_item(name="Main", scene=True),
                                  _item("game_capture")]}))
    assert c.get_scene_input_kinds() == ["unknown", "game_capture"]


def test_an_unreadable_nested_scene_counts_as_unknown():
    c = _client(_Scenes({"Main": [_item(name="Missing", scene=True),
                                  _item("game_capture")]}))
    assert c.get_scene_input_kinds() == ["unknown", "game_capture"]
    assert c.reconnects == 0


def test_a_refused_scene_lookup_is_none_without_reconnecting():
    c = _client(_Scenes({}, program="Gone"))
    assert c.get_scene_input_kinds() is None
    assert c.reconnects == 0


def test_dryrun_stub_has_the_new_interface():
    d = obs_client.DryRunOBS()
    assert d.get_scene_input_kinds() is None
    assert d.replay_buffer_ok is True
    d.close()
