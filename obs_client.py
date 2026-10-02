"""OBS control via obs-websocket v5 (obsws-python).

Two jobs:
  1. save_replay() -> tell OBS to write the Replay Buffer to a clip file.
  2. set_counter(n) -> update a Text source with the running kill count.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

_REQUEST_ERROR = None


def _request_error():
    """obsws-python's OBSSDKRequestError, imported lazily (like obsws_python
    itself, so this module still imports where the library is missing). That
    class means OBS ANSWERED and refused the request: the socket is healthy."""
    global _REQUEST_ERROR
    if _REQUEST_ERROR is None:
        try:
            from obsws_python.error import OBSSDKRequestError as cls
        except Exception:
            class cls(Exception):       # never raised: no obsws-python, no OBS
                pass
        _REQUEST_ERROR = cls
    return _REQUEST_ERROR


def _quiet_obsws_logger():
    """obsws-python calls logger.exception() before re-raising every refused
    request. WITNESS configures no logging, so Python's last-resort handler
    printed a full traceback to the console / GUI log for each one, twice per
    kill when the optional KillCounter source didn't exist. The exceptions
    still propagate (main's connect retry and _call below see every one); only
    the library's own duplicate logging is muted."""
    logging.getLogger("obsws_python").setLevel(logging.CRITICAL)


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
        # True/False once connect() has checked (and if need be started) the
        # Replay Buffer; None when it wasn't checked. False means OBS refused
        # it, which almost always means Settings > Output > Replay Buffer is
        # disabled: kills will count but NO clip will save.
        self.replay_buffer_ok: Optional[bool] = None
        self._client = None
        self._closed = False
        self._bad_counter_format = False
        # Reentrant: _call may reconnect, which re-enters the lock on this thread.
        self._lock = threading.RLock()

    def connect(self):
        with self._lock:
            # main retries connect() while OBS boots; a client whose handshake
            # worked but whose first request didn't must not be leaked.
            self._drop_client()
            self._closed = False
            self._connect_client()
            version = self._client.get_version()
            print(f"Connected to OBS {version.obs_version} "
                  f"(websocket {version.obs_web_socket_version}).")
            if self.auto_start_replay_buffer:
                self.replay_buffer_ok = self._ensure_replay_buffer()

    def _connect_client(self):
        import obsws_python as obs
        _quiet_obsws_logger()
        self._client = obs.ReqClient(
            host=self.host, port=self.port, password=self.password, timeout=5
        )

    def _drop_client(self):
        """Close the current socket without waiting on it.

        Prefers the websocket's shutdown(), which closes the TCP socket at once.
        ReqClient.disconnect() does a polite close that waits up to 3 s for
        OBS's reply, and this runs on the capture thread right after a request
        failed, i.e. exactly when OBS may not be answering. Any error is
        ignored: a dead socket is the usual reason we're here."""
        old = self._client
        if old is None:
            return
        try:
            ws = getattr(getattr(old, "base_client", None), "ws", None)
            if ws is not None and callable(getattr(ws, "shutdown", None)):
                ws.shutdown()
            else:
                old.disconnect()
        except Exception:
            pass

    def _reconnect(self):
        """Silently try to re-establish the connection after a drop. The old
        client is closed first; before, every reconnect left its socket open
        for the garbage collector."""
        with self._lock:
            if self._closed:
                return False          # close() was deliberate: stay closed
            self._drop_client()
            try:
                self._connect_client()
                return True
            except Exception:
                return False

    def _call(self, fn, default=None, label="request", on_refused=None):
        """Run an OBS request; on a dropped connection, reconnect once and retry.
        Serialized — see the class docstring.

        A REFUSED request (OBSSDKRequestError: a missing source, a stopped
        output) is answered over a healthy socket, so it is never a reason to
        reconnect: it prints one line and returns `default`, or whatever
        on_refused(error) returns when that is given. Every other exception is
        treated as a dropped connection, exactly as before."""
        refused = _request_error()
        with self._lock:
            for attempt in range(2):
                try:
                    return fn()
                except refused as e:
                    if on_refused is not None:
                        return on_refused(e)
                    print(f"OBS {label} failed: {e}")
                    return default
                except Exception as e:
                    if attempt == 0 and self._reconnect():
                        continue
                    print(f"OBS {label} failed: {e}")
                    return default

    def _ensure_replay_buffer(self) -> bool:
        """Start the Replay Buffer if it is off. True when it is running."""
        try:
            status = self._client.get_replay_buffer_status()
            if not status.output_active:
                self._client.start_replay_buffer()
                print("Replay Buffer was off — started it.")
            else:
                print("Replay Buffer already running.")
            return True
        except Exception as e:
            print(f"WARNING: could not verify/start Replay Buffer: {e}")
            print("  -> In OBS: Settings > Output > Replay Buffer must be enabled.")
            return False

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
                self.replay_buffer_ok = True
                print("  [obs] Replay Buffer had STOPPED — restarted it. That "
                      "clip is lost; the rest of the session will record.")
            return bool(started)

    def get_last_replay_path(self) -> str:
        """Path of the most recently saved Replay Buffer clip, or '' if unknown."""
        return self._call(
            lambda: getattr(self._client.get_last_replay_buffer_replay(),
                            "saved_replay_path", "") or "",
            default="", label="get last replay") or ""

    def get_scene_screenshot(self, width: int = 480) -> bytes:
        """PNG bytes of what OBS's PROGRAM SCENE looks like right now, or b''.

        This is the only way to answer "will my clips be black". WITNESS reads the
        screen directly, so its own frames look fine even when OBS's Display
        Capture has lost its target — which is exactly how Stan lost a whole night
        of footage without anything looking wrong.

        Small by default: this runs at startup and periodically, and a full-res
        screenshot over the websocket is wasteful when a 480px frame answers the
        question just as well."""
        def go():
            import blackcheck
            scene = self._client.get_current_program_scene()
            name = (getattr(scene, "current_program_scene_name", None)
                    or getattr(scene, "scene_name", None))
            if not name:
                return b""
            r = self._client.get_source_screenshot(name, "png", width,
                                                   int(width * 9 / 16), -1)
            return blackcheck.decode_datauri(getattr(r, "image_data", ""))
        return self._call(go, b"", "scene screenshot")

    def get_record_directory(self) -> str:
        """OBS's recording output folder (where replay clips are saved)."""
        return self._call(
            lambda: getattr(self._client.get_record_directory(),
                            "record_directory", "") or "",
            default="", label="get record dir") or ""

    def set_counter(self, count: int) -> None:
        """Show the running kill count in the OBS text source.

        The source is OPTIONAL (README), and most new users won't have one.
        Before, the missing source raised on every kill, inside the detection
        loop: two library tracebacks, a failure line and a full websocket
        reconnect each time. Now OBS's 'no such source' answer (code 600) turns
        the counter off for the rest of the session with ONE line; counter_source
        "" turns it off from the start. Anything else (a timeout, a dropped
        socket) keeps the usual reconnect-and-retry and never turns it off."""
        with self._lock:
            name = self.counter_source
            if not name:
                return
            text = self._counter_text(count)

            def refused(e):
                if getattr(e, "code", None) == 600:
                    print(f"No OBS text source named '{name}' - on-stream kill "
                          "counter disabled (optional; see README)")
                    self.counter_source = None
                else:
                    print(f"OBS set counter failed: {e}")
                return None

            self._call(lambda: self._client.set_input_settings(
                name=name, settings={"text": text}, overlay=True),
                label="set counter", on_refused=refused)

    def _counter_text(self, count: int) -> str:
        """counter_format from config.yaml, or the default when it is broken
        (e.g. '{kills}'). A typo there used to raise out of set_counter and
        abort the session at startup, all for an optional on-stream label."""
        try:
            return str(self.counter_format).format(count=count)
        except Exception as e:
            if not self._bad_counter_format:
                self._bad_counter_format = True
                print(f"obs.counter_format {self.counter_format!r} is not "
                      f"usable ({type(e).__name__}: {e}); using 'Kills: {{count}}'")
            return f"Kills: {count}"

    def close(self) -> None:
        """Close the websocket (end of session). Safe to call more than once.
        A straggling request after this returns its default instead of quietly
        opening a new connection; connect() opens it again."""
        with self._lock:
            self._closed = True
            self._drop_client()
            self._client = None


class DryRunOBS:
    """Stand-in used by --dry-run: logs actions instead of hitting OBS."""

    replay_buffer_ok = True

    def connect(self):
        print("[dry-run] (not connecting to OBS)")

    def save_replay(self) -> bool:
        print("[dry-run] save_replay() -> would save OBS Replay Buffer clip")
        return True

    def get_last_replay_path(self) -> str:
        return ""

    def get_scene_screenshot(self, width: int = 480) -> bytes:
        return b""

    def get_record_directory(self) -> str:
        return ""

    def set_counter(self, count: int) -> None:
        print(f"[dry-run] set_counter({count})")

    def replay_buffer_active(self):
        return True

    def recover_replay_buffer(self) -> bool:
        return False

    def close(self) -> None:
        pass
