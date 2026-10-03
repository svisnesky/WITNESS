"""No ffmpeg: say so once at START, then stop trying on every kill.

Before, nothing checked. Each kill's replay remux printed '[replay] error:
[WinError 2] The system cannot find the file specified', match reels and medal
call-outs failed the same way, and in the window app none of it was visible.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import montage  # noqa: E402


def test_the_path_fallback_is_checked_on_path_not_on_disk(monkeypatch, tmp_path):
    """find_ffmpeg's last resort is the bare name 'ffmpeg'. os.path.exists on
    that would switch replays off for everyone with ffmpeg on PATH."""
    monkeypatch.setattr(montage, "find_ffmpeg", lambda base, cfg: "ffmpeg")
    monkeypatch.setattr(main.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert main._ffmpeg_available(str(tmp_path), {}) is True
    monkeypatch.setattr(main.shutil, "which", lambda name: None)
    assert main._ffmpeg_available(str(tmp_path), {}) is False


def test_a_configured_ffmpeg_file_counts(monkeypatch, tmp_path):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(main.shutil, "which", lambda name: None)
    assert main._ffmpeg_available(str(tmp_path), {"ffmpeg_path": str(exe)}) is True


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
        assert len(text) <= 60
        self.notices.append(text)


def test_start_without_ffmpeg_says_so_and_skips_the_medal_renders(monkeypatch, capsys):
    import obs_client
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: False)
    prepared = []
    monkeypatch.setattr(main.threading, "Thread", _Recorder(prepared))
    web = _Web()
    monkeypatch.setattr(main, "_web_server", object())
    monkeypatch.setattr(main, "_web_state", web)
    monkeypatch.setattr(obs_client, "DryRunOBS", _DryObs)
    s = main._setup_session({"heat_streaks": False, "detection_canary": False},
                            dry_run=True)
    assert s["no_ffmpeg"] is True
    assert "ffmpeg missing: no replays/reels/voice" in web.notices
    out = capsys.readouterr().out
    assert "ffmpeg not found" in out and "ffmpeg_path" in out
    main._prepare_medals_async({}, s)
    assert prepared == [], "no medal render thread without ffmpeg"


def test_no_replay_remux_and_no_match_reel_without_ffmpeg(monkeypatch):
    started = []
    monkeypatch.setattr(main.threading, "Thread", _Recorder(started))
    s = {"web": _Web(), "no_ffmpeg": True, "match_clips": [{"path": "x.mkv"}],
         "match_num": 0, "cfg": {}}
    main._register_replay_async(s, "/clips/001_down_20-00-00.mkv", "down", 1)
    main._build_match_reel_async({}, s, "/session", {})
    assert started == []
    assert s["match_clips"] == [] and s["match_num"] == 0


def test_with_ffmpeg_nothing_changes(monkeypatch):
    started = []
    monkeypatch.setattr(main.threading, "Thread", _Recorder(started))
    s = {"web": _Web(), "match_clips": [], "match_num": 0, "cfg": {}}
    main._register_replay_async(s, "/clips/001_down_20-00-00.mkv", "down", 1)
    assert len(started) == 1


class _Recorder:
    """Stands in for threading.Thread: records instead of starting."""

    def __init__(self, sink):
        self.sink = sink

    def __call__(self, *a, **k):
        sink = self.sink

        class _T:
            def start(self_inner):
                sink.append(k.get("target"))
        return _T()


class _DryObs:
    def connect(self):
        pass

    def set_counter(self, n):
        pass

    def replay_buffer_active(self):
        return True

    def get_record_directory(self):
        return ""

    def get_scene_screenshot(self, width=480):
        return b""
