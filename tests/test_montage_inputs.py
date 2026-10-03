"""main._montage_inputs lists exactly the clips montage.build_montage
stitches, so "montage: no montage was built" is recorded whenever a montage
that had clips to work with failed, tidied sessions included.

build_montage became tidy-aware (top level AND clips/), but _montage_inputs
still listed only the top level, so a failed montage on 'Rebuild reels' or
the launch resume of a tidied session was silent."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import montage  # noqa: E402


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x")


def test_a_tidied_session_has_montage_inputs(tmp_path):
    d = tmp_path / "2026-10-01_20-00-00"
    _touch(str(d / "clips" / "001_down_20-01-00.mkv"))
    _touch(str(d / "session_reel.mp4"))
    _touch(str(d / "highlights_2026-10-01.mkv"))
    assert main._montage_inputs(str(d)) == ["clips/001_down_20-01-00.mkv"]


def test_a_flat_session_lists_its_top_level_clips(tmp_path):
    d = tmp_path / "s"
    _touch(str(d / "001_down_20-01-00.mkv"))
    _touch(str(d / "notes.txt"))
    assert main._montage_inputs(str(d)) == ["001_down_20-01-00.mkv"]


def test_nothing_to_stitch_and_missing_folders(tmp_path):
    d = tmp_path / "s"
    _touch(str(d / "session_reel.mp4"))
    assert main._montage_inputs(str(d)) == []
    assert main._montage_inputs(str(tmp_path / "missing")) == []
    assert main._montage_inputs("") == []


def test_it_agrees_with_what_build_montage_would_stitch(tmp_path, monkeypatch):
    d = tmp_path / "s"
    _touch(str(d / "clips" / "001_down_20-01-00.mkv"))
    _touch(str(d / "002_elim_20-02-00.mp4"))
    _touch(str(d / "session_reel.mp4"))
    seen = []

    def fake_run(cmd, *a, **k):
        # build_montage hands ffmpeg a concat list; read it back
        lst = cmd[cmd.index("-i") + 1]
        with open(lst, encoding="utf-8") as f:
            seen.extend(ln for ln in f.read().splitlines() if ln.startswith("file "))
        raise FileNotFoundError("no ffmpeg in tests")
    monkeypatch.setattr(montage.subprocess, "run", fake_run)
    assert montage.build_montage(str(d), "ffmpeg") is None
    assert len(seen) == len(main._montage_inputs(str(d))) == 2
