"""main.py fails loudly and recovers.

The window launcher runs pythonw: no console, and gui.py printed the error
after the session log was already closed. So a failed START, or a capture loop
that died on a lock screen, left nothing behind but a dashboard still reading
WATCHING. Pinned here:
  - a session that raises leaves its traceback in logs/session_*.log;
  - a locked screen pauses detection (retry + re-open), never ends it;
  - OCR errors hand back VRAM and retry, and only a long run of them stops;
  - every exit path still saves the clip, resets the dashboard, builds the
    recap, and a failing recap cannot hide the original error;
  - log_kill survives a CSV that Excel has locked.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_a_session_crash_lands_in_the_session_log(tmp_path, monkeypatch):
    # the log goes to <app folder>/logs: point the app folder at tmp_path so
    # the test never writes into the checkout
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))

    def boom(*a, **k):
        raise RuntimeError("OBS: cannot reach the WebSocket server")

    monkeypatch.setattr(main, "_run_live_inner", boom)
    with pytest.raises(RuntimeError):
        main.run_live({})
    logs = list((tmp_path / "logs").glob("session_*.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "Traceback" in text
    assert "OBS: cannot reach the WebSocket server" in text
    assert "Session log saved" in text      # and the log was still closed out


# --- the capture loop ---------------------------------------------------------
# Driven through the REAL _run_live_inner with a stub capture and OCR engine.
# Nothing here touches OCR sizes, VRAM caps or a real screen.

import threading  # noqa: E402
import time  # noqa: E402

import capture  # noqa: E402
import ocr  # noqa: E402


class _Web:
    def __init__(self):
        self.notices, self.running = [], []

    def notice(self, text, tag="alert"):
        self.notices.append(text)

    def set_running(self, running):
        self.running.append(running)

    def pop_clip_request(self):
        return False

    def pop_kill_request(self):
        return False


class _Obs:
    def recover_replay_buffer(self):
        return False

    def save_replay(self):
        return True


def _session(cfg, web=None):
    return {"obs": _Obs(), "web": web, "count": 0, "session_tags": [],
            "match_tags": [], "session_start": time.monotonic(),
            "session_start_wall": "20:00", "session_id": "s1", "last_save": 0.0,
            "min_save": 2.0, "organize": False, "cfg": cfg, "medal_sounds": {},
            "record_dir": ""}


QUIET_LOOP = {"poll_fps": 50, "black_check_minutes": 0, "capture_exfil_stats": False,
              "auto_sweat": False, "runner_detection": False, "track_names": False,
              "show_overlays": False}


def _drive(monkeypatch, s, caps, read_lines, ended=None, stop=None):
    """Run the real loop. caps: list of capture objects handed out in order by
    make_capture. read_lines(img) is the stub OCR."""
    made = []

    def make(cfg):
        c = caps[len(made)]
        made.append(c)
        return c

    class _Engine:
        def __init__(self, *a, **k):
            pass

        def read_lines(self, img):
            return read_lines(img)

    ended = [] if ended is None else ended
    monkeypatch.setattr(capture, "make_capture", make)
    monkeypatch.setattr(ocr, "OCREngine", _Engine)
    monkeypatch.setattr(main, "_setup_session", lambda cfg, dry_run: s)
    monkeypatch.setattr(main, "_tune_performance", lambda cfg: None)
    monkeypatch.setattr(main, "_start_perf_monitor", lambda *a, **k: None)
    monkeypatch.setattr(main, "CAPTURE_RETRY_SECONDS", 0.001)
    if not getattr(main._end_session, "_stub", False):
        def fake_end(*a, **k):
            ended.append((a, k))
        fake_end._stub = True
        monkeypatch.setattr(main, "_end_session", fake_end)
    main._run_live_inner(dict(QUIET_LOOP), dry_run=True, stop_event=stop)
    return made, ended


class _FlakyCap:
    """Raises on grab while `failing[0]` > 0 (like mss on a locked screen)."""

    def __init__(self, failing, frames, stop, good_needed):
        self.failing, self.frames, self.stop = failing, frames, stop
        self.good_needed = good_needed
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def grab(self):
        if self.failing[0] > 0:
            self.failing[0] -= 1
            raise RuntimeError("ScreenShotError: gdi32.GetDIBits() failed")
        self.frames.append(time.monotonic())
        if len(self.frames) >= self.good_needed:
            self.stop.set()
        return None


def test_a_locked_screen_pauses_detection_instead_of_ending_it(monkeypatch, capsys):
    web = _Web()
    s = _session(dict(QUIET_LOOP), web=web)
    stop = threading.Event()
    failing, frames = [7], []
    caps = [_FlakyCap(failing, frames, stop, 5) for _ in range(3)]
    made, ended = _drive(monkeypatch, s, caps, lambda img: [], stop=stop)

    assert len(frames) == 5, "the loop must keep going after the screen comes back"
    # 5 failed grabs in a row: the capture was closed and re-opened (same thread)
    assert len(made) == 2 and made[0].closed and made[1].closed
    assert "Detection paused - screen unavailable" in web.notices
    assert "Detection resumed" in web.notices
    out = capsys.readouterr().out
    assert "screen capture is back after" in out
    assert "Traceback" in out                  # the cause is in the session log
    assert web.running == [False] and len(ended) == 1


def test_the_paused_notice_is_rate_limited(monkeypatch):
    web = _Web()
    s = _session(dict(QUIET_LOOP), web=web)
    stop = threading.Event()
    failing, frames = [40], []
    caps = [_FlakyCap(failing, frames, stop, 1) for _ in range(10)]
    _drive(monkeypatch, s, caps, lambda img: [], stop=stop)
    assert web.notices.count("Detection paused - screen unavailable") == 1


def test_an_error_that_ends_the_loop_still_ends_the_session(monkeypatch):
    """The finally: dashboard back to READY, pending clip saved, recap built,
    and the ORIGINAL error still reaches run_live."""
    web = _Web()
    s = _session(dict(QUIET_LOOP), web=web)
    flushed = []
    monkeypatch.setattr(main, "_flush_coalesce", lambda s_: flushed.append(1))
    stop = threading.Event()
    cap = _FlakyCap([0], [], stop, 10**6)

    def no_ocr(img):
        raise ImportError("No module named 'easyocr'")

    ended = []
    with pytest.raises(ImportError):
        _drive(monkeypatch, s, [cap], no_ocr, ended=ended, stop=stop)
    assert web.running == [False]
    assert flushed == [1]
    assert len(ended) == 1
    assert cap.closed


def test_a_failing_recap_does_not_hide_the_original_error(monkeypatch, capsys):
    web = _Web()
    s = _session(dict(QUIET_LOOP), web=web)

    def bad_end(*a, **k):
        raise ValueError("recap blew up")
    bad_end._stub = True
    monkeypatch.setattr(main, "_end_session", bad_end)

    def no_ocr(img):
        raise ImportError("No module named 'easyocr'")

    with pytest.raises(ImportError):
        _drive(monkeypatch, s, [_FlakyCap([0], [], threading.Event(), 10**6)], no_ocr)
    out = capsys.readouterr().out
    assert "[end] recap failed" in out and "recap blew up" in out
    assert web.running == [False]


def test_ocr_errors_release_vram_and_retry_then_give_up(monkeypatch):
    """A CUDA hiccup gets the cache handed back and a retry (never a CPU
    switch); an OCR that never recovers stops detection with a notice and
    still builds the recap."""
    web = _Web()
    s = _session(dict(QUIET_LOOP), web=web)
    released = []
    monkeypatch.setattr(main, "_release_vram", lambda engine: released.append(1))
    monkeypatch.setattr(main, "LOOP_GIVE_UP_AFTER", 4)
    monkeypatch.setattr(main, "time", _FastTime())
    calls = []

    def oom(img):
        calls.append(1)
        raise RuntimeError("CUDA out of memory")

    # no stop event: the loop must end by itself, and the backoff then goes
    # through time.sleep, which _FastTime skips
    made, ended = _drive(monkeypatch, s,
                         [_FlakyCap([0], [], threading.Event(), 10**6)], oom)
    assert len(calls) == 4 and len(released) == 4
    assert "Detection stopped - kept failing, see log" in web.notices
    assert web.running == [False] and len(ended) == 1


def test_one_ocr_error_does_not_stop_detection(monkeypatch):
    web = _Web()
    s = _session(dict(QUIET_LOOP), web=web)
    monkeypatch.setattr(main, "_release_vram", lambda engine: None)
    monkeypatch.setattr(main, "time", _FastTime())
    stop = threading.Event()
    frames = []
    seen = []

    def sometimes(img):
        seen.append(1)
        if len(seen) == 2:
            raise RuntimeError("CUDA error: unspecified launch failure")
        return []

    _drive(monkeypatch, s, [_FlakyCap([0], frames, stop, 6)], sometimes, stop=stop)
    assert len(frames) == 6 and len(seen) == 6
    assert not any("stopped" in n for n in web.notices)


class _FastTime:
    """time module stand-in whose sleep() returns at once (the OCR backoff)."""

    def __getattr__(self, name):
        return getattr(time, name)

    @staticmethod
    def sleep(_s):
        return None


# --- log_kill -----------------------------------------------------------------

def test_log_kill_on_a_locked_csv_prints_and_returns(tmp_path, capsys):
    """Excel holds an open CSV locked; the PermissionError used to escape
    _handle_kill and end the session."""
    from detector import KillEvent
    p = tmp_path / "session_log.csv"
    p.write_text("wall_time,count\n", encoding="utf-8")
    os.chmod(p, 0o444)
    try:
        main.log_kill({"session_log": str(p)},
                      KillEvent(0.0, "RUNNER DOWN +15", "", "", True), 1)
    finally:
        os.chmod(p, 0o644)
    assert "could not write" in capsys.readouterr().out


def test_log_kill_writes_utf8(tmp_path):
    from detector import KillEvent
    p = tmp_path / "kills.csv"
    main.log_kill({"session_log": str(p)},
                  KillEvent(0.0, "RUNNER DOWN — ÆON", "", "ÆON", True), 1)
    assert "ÆON" in p.read_text(encoding="utf-8")


# --- the session log says which build wrote it -----------------------------------

def test_the_session_log_starts_with_the_build(tmp_path, monkeypatch):
    import types
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    (tmp_path / ".app_version").write_text("1bd0911abcdef\n", encoding="utf-8")
    (tmp_path / "update_status.txt").write_text(
        "2026-10-02 19:00:00\nUp to date (1bd0911)\n", encoding="utf-8")
    fake = types.ModuleType("version")
    fake.VERSION = "0.1.0"
    monkeypatch.setitem(sys.modules, "version", fake)
    monkeypatch.setattr(main, "_run_live_inner", lambda *a, **k: None)
    main.run_live({})
    log = next((tmp_path / "logs").glob("session_*.log")).read_text(encoding="utf-8")
    head = log.splitlines()[1]
    assert head.startswith("WITNESS 0.1.0 (1bd0911) | Python ")
    assert head.endswith("| update: Up to date (1bd0911)")


def test_no_version_file_and_no_sha_still_logs_a_build_line(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    monkeypatch.setitem(sys.modules, "version", None)     # import fails
    monkeypatch.setattr(main, "_run_live_inner", lambda *a, **k: None)
    main.run_live({})
    log = next((tmp_path / "logs").glob("session_*.log")).read_text(encoding="utf-8")
    assert log.splitlines()[1].startswith("WITNESS ? (dev) | Python ")
