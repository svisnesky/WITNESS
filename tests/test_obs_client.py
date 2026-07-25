"""OBS client: serialized requests + Replay Buffer self-heal.

The client is genuinely used from several threads at once (the capture loop
saves replays / updates the counter while each clip-organize worker polls
get_last_replay_path() every 0.5s for up to 8s). obsws-python pairs a send with
a recv on ONE socket, so unsynchronized use crosses responses between callers.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import obs_client  # noqa: E402


class _FakeReq:
    """A socket-like client that FAILS if two requests overlap — exactly the
    failure mode a missing lock produces."""

    def __init__(self, rb_active=True):
        self.busy = False
        self.overlaps = 0
        self.rb_active = rb_active
        self.starts = 0
        self.saves = 0

    def _req(self, out):
        if self.busy:
            self.overlaps += 1
            raise RuntimeError("concurrent request on one websocket")
        self.busy = True
        try:
            time.sleep(0.001)      # widen the window a real socket would have
            return out
        finally:
            self.busy = False

    def get_version(self):
        return type("V", (), {"obs_version": "32", "obs_web_socket_version": "5"})()

    def get_last_replay_buffer_replay(self):
        return self._req(type("R", (), {"saved_replay_path": "/tmp/clip.mkv"})())

    def get_record_directory(self):
        return self._req(type("R", (), {"record_directory": "/tmp"})())

    def set_input_settings(self, **kw):
        return self._req(None)

    def save_replay_buffer(self):
        self.saves += 1
        if not self.rb_active:
            raise RuntimeError("replay buffer not active")
        return self._req(None)

    def get_replay_buffer_status(self):
        return self._req(type("S", (), {"output_active": self.rb_active})())

    def start_replay_buffer(self):
        self.starts += 1
        self.rb_active = True
        return self._req(None)


def _client(fake):
    c = obs_client.OBSClient(auto_start_replay_buffer=False)
    c._client = fake
    c._connect_client = lambda: None      # never build a real socket
    return c


def test_concurrent_requests_are_serialized():
    fake = _FakeReq()
    c = _client(fake)

    def hammer(fn, n=40):
        for _ in range(n):
            fn()

    threads = [
        threading.Thread(target=hammer, args=(c.get_last_replay_path,)),
        threading.Thread(target=hammer, args=(c.save_replay,)),
        threading.Thread(target=hammer, args=(lambda: c.set_counter(1),)),
        threading.Thread(target=hammer, args=(c.get_record_directory,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fake.overlaps == 0, f"{fake.overlaps} overlapping OBS requests"


def test_stopped_replay_buffer_is_restarted_on_failed_save():
    fake = _FakeReq(rb_active=False)
    c = _client(fake)
    assert c.save_replay() is False        # this clip is genuinely gone
    assert fake.starts == 1               # but the buffer is back
    assert c.save_replay() is True        # and the next kill records


def test_recover_is_a_noop_while_active():
    fake = _FakeReq(rb_active=True)
    c = _client(fake)
    assert c.recover_replay_buffer() is False
    assert fake.starts == 0


def test_dryrun_stub_matches_the_interface():
    d = obs_client.DryRunOBS()
    for name in ("connect", "save_replay", "get_last_replay_path",
                 "get_record_directory", "set_counter",
                 "replay_buffer_active", "recover_replay_buffer"):
        assert hasattr(d, name), name
    assert d.recover_replay_buffer() is False
