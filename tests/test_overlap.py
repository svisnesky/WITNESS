"""Overlapping Replay-Buffer saves must not play as duplicates in reels."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import match_reel as mr  # noqa: E402


def _clips(*names):
    return [{"path": f"Z:/x/{n}", "kills": k, "tag": t}
            for n, k, t in names]


def test_back_to_back_saves_merge(monkeypatch):
    # Stan's real session: three saves in 24s, each a ~30s buffer
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: 30.0)
    clips = _clips(("002_assist_21-36-20.mkv", 0, "assist"),
                   ("003_kill_21-36-32.mkv", 1, "kill"),
                   ("003_assist_21-36-44.mkv", 0, "assist"))
    out = mr.drop_overlapping(clips, "ffmpeg")
    assert len(out) == 1                       # one clip survives
    assert out[0]["path"].endswith("003_assist_21-36-44.mkv")   # the latest
    assert out[0]["kills"] == 1                # the kill is still credited
    assert "kill" in out[0]["tag"]


def test_spaced_saves_untouched(monkeypatch):
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: 30.0)
    clips = _clips(("001_down_19-48-47.mkv", 1, "down"),
                   ("002_down_19-55-44.mkv", 1, "down"),
                   ("003_down_20-05-24.mkv", 1, "down"))
    out = mr.drop_overlapping(clips, "ffmpeg")
    assert len(out) == 3                       # minutes apart — all kept


def test_unparseable_names_kept(monkeypatch):
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: 30.0)
    clips = _clips(("weird_name.mkv", 1, "kill"),
                   ("003_kill_21-36-32.mkv", 1, "kill"))
    out = mr.drop_overlapping(clips, "ffmpeg")
    assert len(out) == 2
