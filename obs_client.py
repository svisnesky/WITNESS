"""OBS control via obs-websocket v5 (obsws-python).

Two jobs:
  1. save_replay() -> tell OBS to write the Replay Buffer to a clip file.
  2. set_counter(n) -> update a Text source with the running kill count.
"""

from __future__ import annotations

from typing import Optional


class OBSClient:
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

    def connect(self):
        import obsws_python as obs

        self._client = obs.ReqClient(
            host=self.host, port=self.port, password=self.password, timeout=5
        )
        version = self._client.get_version()
        print(f"Connected to OBS {version.obs_version} (websocket {version.obs_web_socket_version}).")

        if self.auto_start_replay_buffer:
            self._ensure_replay_buffer()

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
        """Write the current Replay Buffer to a clip. Returns True on success."""
        try:
            self._client.save_replay_buffer()
            return True
        except Exception as e:
            print(f"ERROR saving replay buffer: {e}")
            return False

    def get_last_replay_path(self) -> str:
        """Path of the most recently saved Replay Buffer clip, or '' if unknown."""
        try:
            r = self._client.get_last_replay_buffer_replay()
            return getattr(r, "saved_replay_path", "") or ""
        except Exception:
            return ""

    def get_record_directory(self) -> str:
        """OBS's recording output folder (where replay clips are saved)."""
        try:
            r = self._client.get_record_directory()
            return getattr(r, "record_directory", "") or ""
        except Exception:
            return ""

    def set_counter(self, count: int) -> None:
        text = self.counter_format.format(count=count)
        try:
            self._client.set_input_settings(
                name=self.counter_source,
                settings={"text": text},
                overlay=True,
            )
        except Exception as e:
            print(f"WARNING: could not update counter source "
                  f"'{self.counter_source}': {e}")


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
