"""OBS control via obs-websocket v5 (obsws-python).

Two jobs:
  1. save_replay() -> tell OBS to write the Replay Buffer to a clip file.
  2. set_counter(n) -> update a Text source with the running kill count.
"""

from __future__ import annotations

import threading
from typing import Optional


class OBSClient:
    """Thread-safe wrapper around one obs-websocket connection.

    SERIALIZED ON PURPOSE: obsws-python's ReqClient sends a request and waits
    for its response on a single socket, so it is not safe to use from two
    threads at once. This class is genuinely called concurrently — the capture
    loop saves replays and updates the counter while each clip-organize worker
    polls get_last_replay_path() every 0.5s for up to 8s — and unsynchronized
    use crosses responses between callers (a save reporting another call's
    result, spurious failures, or a clip never getting organized).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4455,
        password: str = "",
        counter_source: str = "KillCounter",
        counter_format: str = "Kills: {count}",
        auto_start_replay_buffer: bool = True,
    ):
        self.host = host
        self.port = port
        self.password = password
        self.counter_source = counter_source
        self.counter_format = counter_format
        self.auto_start_replay_buffer = auto_start_replay_buffer
        self._client = None
        # Reentrant: _call may reconnect, which re-enters the lock on this thread.
        self._lock = threading.RLock()

    def connect(self):
        with self._lock:
            self._connect_client()
            version = self._client.get_version()
            print(f"Connected to OBS {version.obs_version} "
                  f"(websocket {version.obs_web_socket_version}).")
            if self.auto_start_replay_buffer:
                self._ensure_replay_buffer()

    def _connect_client(self):
        import obsws_python as obs
        self._client = obs.ReqClient(
            host=self.host, port=self.port, password=self.password, timeout=5
        )

    def _reconnect(self):
        """Silently try to re-establish the connection after a drop."""
        with self._lock:
            try:
                self._connect_client()
                return True
            except Exception:
                return False

    def _call(self, fn, default=None, label="request"):
        """Run an OBS request; on a dropped connection, reconnect once and retry.
        Serialized — see the class docstring."""
        with self._lock:
            for attempt in range(2):
                try:
                    return fn()
                except Exception as e:
                    if attempt == 0 and self._reconnect():
                        continue
                    print(f"OBS {label} failed: {e}")
                    return default

    def _ensure_replay_buffer(self):
        try:
            status = self._client.get_replay_buffer_status()
            if not status.output_active:
                self._client.start_replay_buffer()
                print("Replay Buffer was off — started it.")
            else:
                print("Replay Buffer already running.")
        except Exception as e:
            print(f"WARNING: could not verify/start Replay Buffer: {e}")
            print("  -> In OBS: Settings > Output > Replay Buffer must be enabled.")

    def save_replay(self) -> bool:
        """Write the current Replay Buffer to a clip. Returns True on success.

        If the save fails, check whether the Replay Buffer has STOPPED and
        restart it. Without this, one stop (an OBS restart, a stray hotkey, an
        encoder/disk error) silently costs every remaining clip of the session:
        _ensure_replay_buffer only ran at connect time, so nothing ever brought
        it back. This kill's footage is genuinely gone, but the rest of the
        night records."""
        if self._call(lambda: (self._client.save_replay_buffer(), True)[1],
                      default=False, label="save replay"):
            return True
        self.recover_replay_buffer()
        return False

    def replay_buffer_active(self) -> Optional[bool]:
        """True/False if known, None if the status couldn't be read."""
        return self._call(
            lambda: bool(getattr(self._client.get_replay_buffer_status(),
                                 "output_active", False)),
            default=None, label="replay buffer status")

    def recover_replay_buffer(self) -> bool:
        """Restart the Replay Buffer if it has stopped. True if it was restarted."""
        with self._lock:
            if self.replay_buffer_active() is not False:
                return False        # active, or status unreadable — leave it be
            started = self._call(
                lambda: (self._client.start_replay_buffer(), True)[1],
                default=False, label="restart replay buffer")
            if started:
                print("  [obs] Replay Buffer had STOPPED — restarted it. That "
                      "clip is lost; the rest of the session will record.")
            return bool(started)

    def get_last_replay_path(self) -> str:
        """Path of the most recently saved Replay Buffer clip, or '' if unknown."""
        return self._call(
            lambda: getattr(self._client.get_last_replay_buffer_replay(),
                            "saved_replay_path", "") or "",
            default="", label="get last replay") or ""

    def get_record_directory(self) -> str:
        """OBS's recording output folder (where replay clips are saved)."""
        return self._call(
            lambda: getattr(self._client.get_record_directory(),
                            "record_directory", "") or "",
            default="", label="get record dir") or ""

    def set_counter(self, count: int) -> None:
        text = self.counter_format.format(count=count)
        self._call(lambda: self._client.set_input_settings(
            name=self.counter_source, settings={"text": text}, overlay=True),
            label="set counter")


class DryRunOBS:
    """Stand-in used by --dry-run: logs actions instead of hitting OBS."""

    def connect(self):
        print("[dry-run] (not connecting to OBS)")

    def save_replay(self) -> bool:
        print("[dry-run] save_replay() -> would save OBS Replay Buffer clip")
        return True

    def get_last_replay_path(self) -> str:
        return ""

    def get_record_directory(self) -> str:
        return ""

    def set_counter(self, count: int) -> None:
        print(f"[dry-run] set_counter({count})")

    def replay_buffer_active(self):
        return True

    def recover_replay_buffer(self) -> bool:
        return False
