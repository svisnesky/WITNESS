"""Resuming an interrupted end-of-session recap build."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def _mk_session(root, name, clips=(), session_reel=False):
    sdir = os.path.join(root, "Marathon Sessions", name)
    os.makedirs(sdir, exist_ok=True)
    for c in clips:
        open(os.path.join(sdir, c), "wb").write(b"x")
    if session_reel:
        open(os.path.join(sdir, "session_reel.mp4"), "wb").write(b"x")
    return sdir


def test_finds_interrupted_session(tmp_path):
    root = str(tmp_path)
    _mk_session(root, "2026-07-23_19-00-00", ["001_down_19-10-00.mkv"], session_reel=True)
    cut = _mk_session(root, "2026-07-24_20-00-00", ["001_down_20-10-00.mkv",
                                                    "002_kill_20-20-00.mkv"])
    assert main.find_unfinished_session(root) == cut


def test_complete_session_means_nothing_to_do(tmp_path):
    root = str(tmp_path)
    _mk_session(root, "2026-07-24_20-00-00", ["001_down_20-10-00.mkv"], session_reel=True)
    assert main.find_unfinished_session(root) == ""


def test_no_clips_no_resume(tmp_path):
    root = str(tmp_path)
    _mk_session(root, "2026-07-24_20-00-00", [])          # exfil PNGs only, say
    assert main.find_unfinished_session(root) == ""
    assert main.find_unfinished_session("") == ""


def test_older_gaps_ignored(tmp_path):
    root = str(tmp_path)
    _mk_session(root, "2026-07-20_20-00-00", ["001_down_20-10-00.mkv"])  # old, unfinished
    _mk_session(root, "2026-07-24_20-00-00", ["001_down_20-10-00.mkv"], session_reel=True)
    assert main.find_unfinished_session(root) == ""       # newest is complete -> done
