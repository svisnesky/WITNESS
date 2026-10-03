"""A recap tidies the session folder LAST: clips move into clips/, while
highlights_*, session_reel.mp4 and witness_report_* stay at the top level.

The montage and Shorts builders listed only the top level. So a Rebuild (or a
resumed recap) of a tidied session stitched session_reel.mp4 into a "montage" of
itself and rendered it as a vertical "Short" — which Creator mode uploads — while
the real clips in clips/ were ignored. Wrapped's best play read the top level
too, so it never found anything after the first recap.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import match_reel  # noqa: E402
import montage  # noqa: E402
import shorts  # noqa: E402

CLIPS = ("001_down_20-01-00.mkv", "002_down+precision_20-05-00.mkv",
         "003_manual_kill_20-12-00.mkv")


def _tidied(root, name="2026-09-01_20-00-00"):
    """A session folder as the recap leaves it."""
    s = root / name
    (s / "clips").mkdir(parents=True)
    for i, n in enumerate(CLIPS):
        p = s / "clips" / n
        p.write_bytes(b"x")
        os.utime(p, (1_000_000 + i * 600, 1_000_000 + i * 600))  # oldest first
    (s / "session_reel.mp4").write_bytes(b"x")
    (s / f"highlights_{name}.mkv").write_bytes(b"x")
    (s / "witness_report_tts.mp4").write_bytes(b"x")
    (s / "reels").mkdir()
    (s / "reels" / "match_1.mp4").write_bytes(b"x")
    return s


def _no_probe(monkeypatch):
    """drop_overlapping probes each clip's length; there is no real ffmpeg here.
    The clips are minutes apart, so nothing overlaps."""
    monkeypatch.setattr(match_reel, "probe_duration", lambda p, f: 30.0)


def _fake_montage_ffmpeg(monkeypatch, seen, returncode=0, raises=None):
    def fake(cmd, cwd=None, **k):
        lst = cmd[cmd.index("-i") + 1]
        with open(lst, encoding="utf-8") as f:
            seen["list"] = f.read()
        seen["cwd"] = cwd
        if raises:
            raise raises
        if returncode == 0:
            with open(cmd[-1], "wb") as f:
                f.write(b"mkv")
        return subprocess.CompletedProcess(cmd, returncode, "", "concat failed")
    monkeypatch.setattr(montage.subprocess, "run", fake)


def test_montage_of_a_tidied_session_uses_only_clips(tmp_path, monkeypatch):
    s = _tidied(tmp_path)
    _no_probe(monkeypatch)
    seen = {}
    _fake_montage_ffmpeg(monkeypatch, seen)
    out = montage.build_montage(str(s), "FAKE_FFMPEG")
    assert out and os.path.basename(out).startswith("highlights_")
    # Relative to the session folder, forward slashes: the concat demuxer
    # resolves entries against the list file's folder (= the session folder).
    assert seen["list"] == "".join(f"file 'clips/{n}'\n" for n in CLIPS)
    assert seen["cwd"] == str(s)
    assert "session_reel" not in seen["list"]
    assert not os.path.exists(s / "_montage_list.txt")


def test_a_clip_left_at_the_top_level_by_tidy_is_used_once(tmp_path, monkeypatch):
    """tidy skips a name collision, leaving the same clip in both places."""
    s = _tidied(tmp_path)
    (s / CLIPS[0]).write_bytes(b"x")
    _no_probe(monkeypatch)
    seen = {}
    _fake_montage_ffmpeg(monkeypatch, seen)
    montage.build_montage(str(s), "FAKE_FFMPEG")
    assert seen["list"].count(CLIPS[0]) == 1
    assert len(seen["list"].splitlines()) == len(CLIPS)


def test_an_untidied_session_still_lists_top_level_names(tmp_path, monkeypatch):
    """Mid-session (before tidy) the clips are at the top level, and the list
    keeps the bare names it always had."""
    s = tmp_path / "flat"
    s.mkdir()
    for i, n in enumerate(CLIPS):
        (s / n).write_bytes(b"x")
        os.utime(s / n, (1_000_000 + i * 600, 1_000_000 + i * 600))
    _no_probe(monkeypatch)
    seen = {}
    _fake_montage_ffmpeg(monkeypatch, seen)
    montage.build_montage(str(s), "FAKE_FFMPEG")
    assert seen["list"] == "".join(f"file '{n}'\n" for n in CLIPS)


def test_the_montage_list_is_removed_when_ffmpeg_fails(tmp_path, monkeypatch):
    s = _tidied(tmp_path)
    _no_probe(monkeypatch)
    _fake_montage_ffmpeg(monkeypatch, {}, returncode=1)
    assert montage.build_montage(str(s), "FAKE_FFMPEG") is None
    assert not os.path.exists(s / "_montage_list.txt")


def test_the_montage_list_is_removed_when_ffmpeg_is_missing(tmp_path, monkeypatch):
    s = _tidied(tmp_path)
    _no_probe(monkeypatch)
    _fake_montage_ffmpeg(monkeypatch, {}, raises=FileNotFoundError("WinError 2"))
    assert montage.build_montage(str(s), "FAKE_FFMPEG") is None
    assert not os.path.exists(s / "_montage_list.txt")


def test_shorts_of_a_tidied_session_come_only_from_clips(tmp_path, monkeypatch):
    s = _tidied(tmp_path)
    _no_probe(monkeypatch)
    monkeypatch.setattr(shorts, "_has_drawtext", lambda ff: False)
    made = []

    def fake_short(src, dest, ffmpeg, **k):
        made.append((src, dest))
        return True
    monkeypatch.setattr(shorts, "build_short", fake_short)
    # one Short already exists from an earlier run: not rendered again
    (s / "shorts").mkdir()
    (s / "shorts" / "001_down_20-01-00.mp4").write_bytes(b"x")

    shorts.build_shorts(str(s), "FAKE_FFMPEG")

    srcs = [os.path.relpath(src, str(s)).replace(os.sep, "/") for src, _d in made]
    assert srcs == [f"clips/{n}" for n in CLIPS[1:]]
    # outputs are named by the clip's basename, in shorts/
    assert [os.path.relpath(d, str(s)).replace(os.sep, "/") for _s, d in made] == [
        "shorts/" + os.path.splitext(n)[0] + ".mp4" for n in CLIPS[1:]]
    assert not any("session_reel" in src or "highlights" in src
                   or "witness_report" in src for src, _d in made)


def test_shorts_render_one_per_clip_when_tidy_left_a_duplicate(tmp_path, monkeypatch):
    s = _tidied(tmp_path)
    (s / CLIPS[1]).write_bytes(b"x")
    _no_probe(monkeypatch)
    monkeypatch.setattr(shorts, "_has_drawtext", lambda ff: False)
    made = []
    monkeypatch.setattr(shorts, "build_short",
                        lambda src, dest, ff, **k: made.append(dest) or True)
    shorts.build_shorts(str(s), "FAKE_FFMPEG")
    assert len(made) == len(set(made)) == len(CLIPS)


def test_wrapped_best_play_reads_clips_and_counts_a_plus_one(tmp_path):
    import wrapped
    sess_root = tmp_path / "Marathon Sessions"
    _tidied(sess_root)
    s2 = sess_root / "2026-09-02_20-00-00"
    (s2 / "clips").mkdir(parents=True)
    (s2 / "clips" / "004_down+down+manual_kill_21-00-00.mkv").write_bytes(b"x")
    (s2 / "session_reel.mp4").write_bytes(b"x")
    bp = wrapped.best_play(str(tmp_path))
    assert bp is not None, "tidied sessions must still have a best play"
    # two downs plus a hand-credited +1: three, not two ('manual' read as 0)
    assert bp == (3, "down+down+manual_kill", "2026-09-02_20-00-00")


def test_wrapped_ranks_a_plus_one_with_kills_not_below_an_assist(tmp_path):
    """Equal kill counts are broken by tag. A +1 KILL is a kill: it used to be
    misread as 'manual' and, once parsed correctly, matched no rank at all."""
    import wrapped
    root = tmp_path / "Marathon Sessions"
    a = root / "2026-09-03_20-00-00" / "clips"
    a.mkdir(parents=True)
    (a / "001_assist_20-01-00.mkv").write_bytes(b"x")
    b = root / "2026-09-04_20-00-00" / "clips"
    b.mkdir(parents=True)
    (b / "002_manual_kill_20-05-00.mkv").write_bytes(b"x")
    assert wrapped.best_play(str(tmp_path)) == (1, "manual_kill",
                                                "2026-09-04_20-00-00")
