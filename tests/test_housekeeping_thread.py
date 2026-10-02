"""The 60-s housekeeping and the black-capture re-check run OFF the detection loop.

On the real 07-29 log, 9 mid-match windows with zero OCR (320 s, 3.6-4.0% of the
session) started on a 60.13-s grid: the period of the inline Replay Buffer /
disk-space check. A clip drive whose disk_usage hangs (or a slow OBS reply)
froze the loop for that long, and popups shown meanwhile were never read.

Pinned here:
  - the real _run_live_inner keeps grabbing frames while a check hangs;
  - a busy check makes the next due one SKIP, not queue behind it;
  - a run slower than 1 s prints which step was slow.
Nothing here touches OCR, image size or VRAM.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import capture  # noqa: E402
import main  # noqa: E402
import ocr  # noqa: E402


class _Obs:
    def __init__(self):
        self.rb_calls = 0

    def recover_replay_buffer(self):
        self.rb_calls += 1
        return False


def _session(cfg):
    return {"obs": _Obs(), "web": None, "count": 0, "session_tags": [],
            "match_tags": [], "session_start": time.monotonic(),
            "session_start_wall": "20:00", "session_id": "s1", "last_save": 0.0,
            "min_save": 2.0, "organize": False, "cfg": cfg, "medal_sounds": {},
            "record_dir": "/nonexistent-clip-drive"}


def test_housekeeping_runs_off_loop_thread(monkeypatch):
    """Drive the REAL loop with a disk check that hangs: frames keep coming."""
    cfg = {"poll_fps": 50, "black_check_minutes": 0, "capture_exfil_stats": False,
           "auto_sweat": False, "runner_detection": False, "track_names": False,
           "show_overlays": False}
    s = _session(cfg)
    # due on the first frame (time.monotonic() can start near 0 in a fresh
    # process on macOS; on Windows it is time since boot)
    s["_last_rb_check"] = time.monotonic() - 3600
    stop = threading.Event()
    frames = []
    hang = {}
    n_frames = 40

    class _Cap:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self):
            frames.append(time.monotonic())
            if len(frames) >= n_frames:
                stop.set()
            return None

    class _Engine:
        def __init__(self, *a, **k):
            pass

        def read_lines(self, img):
            return []

    def slow_disk_check(cfg, record_dir, web=None, state=None):
        hang["thread"] = threading.current_thread()
        hang["start"] = time.monotonic()
        time.sleep(1.5)
        hang["end"] = time.monotonic()

    ended = []
    monkeypatch.setattr(capture, "make_capture", lambda cfg: _Cap())
    monkeypatch.setattr(ocr, "OCREngine", _Engine)
    monkeypatch.setattr(main, "_setup_session", lambda cfg, dry_run: s)
    monkeypatch.setattr(main, "_tune_performance", lambda cfg: None)
    monkeypatch.setattr(main, "_start_perf_monitor", lambda *a, **k: None)
    monkeypatch.setattr(main, "_end_session", lambda *a, **k: ended.append(a))
    monkeypatch.setattr(main, "check_disk_space", slow_disk_check)

    main._run_live_inner(cfg, dry_run=True, stop_event=stop)

    assert len(frames) == n_frames
    assert "start" in hang, "the disk check never ran"
    assert hang["thread"] is not threading.main_thread()
    during = [t for t in frames if hang["start"] < t < hang["start"] + 1.4]
    assert len(during) >= 20, f"loop stalled behind the disk check ({len(during)} frames)"
    assert s["obs"].rb_calls == 1
    assert "end" in hang, "teardown did not wait for the running check"
    assert len(ended) == 1


def test_housekeeping_busy_flag_skips_overlapping_run(monkeypatch, capsys):
    release = threading.Event()
    calls = []

    def blocking_disk_check(cfg, record_dir, web=None, state=None):
        calls.append(time.monotonic())
        release.wait(5)

    monkeypatch.setattr(main, "check_disk_space", blocking_disk_check)
    cfg = {"black_check_minutes": 0}
    s = _session(cfg)
    t = 10_000.0
    th = main._maybe_housekeeping(cfg, s, t)
    assert th is not None
    # not due yet: nothing happens
    assert main._maybe_housekeeping(cfg, s, t + 30) is None
    # due again while the first run is still hung: skipped, not queued
    assert main._maybe_housekeeping(cfg, s, t + 61) is None
    assert "previous check still running — skipped" in capsys.readouterr().out
    release.set()
    th.join(5)
    assert len(calls) == 1
    assert not s["_hk_busy"]
    # the skipped run restarted the period; the next due one runs normally
    assert main._maybe_housekeeping(cfg, s, t + 100) is None
    th2 = main._maybe_housekeeping(cfg, s, t + 122)
    assert th2 is not None
    th2.join(5)
    assert len(calls) == 2


def test_black_check_runs_on_its_own_period(monkeypatch):
    seen = []
    monkeypatch.setattr(main, "check_disk_space", lambda *a, **k: seen.append("disk"))
    monkeypatch.setattr(main, "check_obs_not_black",
                        lambda cfg, obs, web=None, state=None, label="": seen.append("black"))
    cfg = {"black_check_minutes": 5}
    s = _session(cfg)
    s["_last_rb_check"] = 1000.0
    s["_black_check_at"] = 1000.0
    main._maybe_housekeeping(cfg, s, 1061.0).join(5)        # 60 s: disk only
    assert seen == ["disk"]
    main._maybe_housekeeping(cfg, s, 1300.0).join(5)        # 300 s: both
    assert seen == ["disk", "disk", "black"]
    assert s["obs"].rb_calls == 2


def test_slow_housekeeping_prints_timing_line(monkeypatch, capsys):
    monkeypatch.setattr(main, "HOUSEKEEPING_SLOW_SECONDS", 0.05)
    monkeypatch.setattr(main, "check_disk_space",
                        lambda *a, **k: time.sleep(0.12))
    s = _session({})
    took = main._housekeeping_work({}, s, do_rb_disk=True, do_black=False)
    out = capsys.readouterr().out
    assert set(took) == {"replay-buffer check", "disk check"}
    line = [ln for ln in out.splitlines() if "[housekeeping] slow:" in ln]
    assert len(line) == 1
    assert "replay-buffer check" in line[0] and "disk check" in line[0]


def test_fast_housekeeping_is_quiet(monkeypatch, capsys):
    monkeypatch.setattr(main, "check_disk_space", lambda *a, **k: None)
    main._housekeeping_work({}, _session({}), do_rb_disk=True, do_black=False)
    assert "[housekeeping]" not in capsys.readouterr().out


def test_a_failing_check_does_not_kill_the_next_run(monkeypatch):
    def boom(*a, **k):
        raise OSError("drive vanished")
    monkeypatch.setattr(main, "check_disk_space", boom)
    cfg = {"black_check_minutes": 0}
    s = _session(cfg)
    main._maybe_housekeeping(cfg, s, 10_000.0).join(5)
    assert not s["_hk_busy"]
    assert main._maybe_housekeeping(cfg, s, 10_061.0) is not None
