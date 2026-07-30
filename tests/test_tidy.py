"""Tidying a session folder organizes it and NEVER destroys anything.

Stan asked for the folder to be "tidied up at least" — and his clip drive had
15 TB free, so there is no reason to delete footage to achieve tidiness. Every
assertion here exists to keep that promise: files move, counts are preserved,
and a tidied folder still reads back correctly.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tidy  # noqa: E402

# A realistic top level, from the 2026-07-27 session.
FILES = [
    "000_assist_20-27-54.mkv",
    "000_assist_20-27-54.mkv.json",
    "001_down+precision_20-55-40.mkv",
    "016_down+kill+finisher_22-27-48.mkv",
    "exfil_20-28-01.png",
    "exfil_21-15-04.png",
    "exfil_21-15-04_lootgoblin.png",
    "highlights_2026-07-27_20-10-11.mkv",
    "session_reel.mp4",
    "witness_report_tts.mp3",
]


def _session(tmp_path):
    d = tmp_path / "2026-07-27_20-10-11"
    d.mkdir()
    for f in FILES:
        (d / f).write_bytes(b"x")
    (d / "reels").mkdir()
    (d / "reels" / "match_1.mp4").write_bytes(b"x")
    (d / "shorts").mkdir()
    (d / "shorts" / "s1.mp4").write_bytes(b"x")
    return str(d)


def test_nothing_is_ever_deleted(tmp_path):
    d = _session(tmp_path)
    before = sum(len(fs) for _r, _ds, fs in os.walk(d))
    tidy.tidy_session(d)
    after = sum(len(fs) for _r, _ds, fs in os.walk(d))
    assert after == before, "tidying must not lose a single file"


def test_clips_and_screenshots_are_filed(tmp_path):
    d = _session(tmp_path)
    tidy.tidy_session(d)
    clips = sorted(os.listdir(os.path.join(d, "clips")))
    shots = sorted(os.listdir(os.path.join(d, "exfil")))
    assert clips == ["000_assist_20-27-54.mkv", "000_assist_20-27-54.mkv.json",
                     "001_down+precision_20-55-40.mkv",
                     "016_down+kill+finisher_22-27-48.mkv"]
    assert shots == ["exfil_20-28-01.png", "exfil_21-15-04.png",
                     "exfil_21-15-04_lootgoblin.png"]


def test_the_payoff_files_stay_at_the_top_level(tmp_path):
    d = _session(tmp_path)
    tidy.tidy_session(d)
    top = set(os.listdir(d))
    assert "highlights_2026-07-27_20-10-11.mkv" in top
    assert "session_reel.mp4" in top
    assert "witness_report_tts.mp3" in top


def test_existing_folders_are_untouched(tmp_path):
    d = _session(tmp_path)
    tidy.tidy_session(d)
    assert os.listdir(os.path.join(d, "reels")) == ["match_1.mp4"]
    assert os.listdir(os.path.join(d, "shorts")) == ["s1.mp4"]


def test_tidying_twice_is_safe(tmp_path):
    d = _session(tmp_path)
    tidy.tidy_session(d)
    before = sum(len(fs) for _r, _ds, fs in os.walk(d))
    assert tidy.tidy_session(d) == {}      # nothing left to move
    assert sum(len(fs) for _r, _ds, fs in os.walk(d)) == before


def test_dry_run_moves_nothing(tmp_path):
    d = _session(tmp_path)
    counts = tidy.tidy_session(d, dry_run=True)
    assert counts == {"clips": 4, "exfil": 3}
    assert not os.path.isdir(os.path.join(d, "clips"))
    assert set(os.listdir(d)) >= set(FILES)


def test_a_name_collision_is_left_alone_not_overwritten(tmp_path):
    d = _session(tmp_path)
    os.makedirs(os.path.join(d, "clips"))
    keep = os.path.join(d, "clips", "000_assist_20-27-54.mkv")
    with open(keep, "wb") as f:
        f.write(b"ORIGINAL")
    tidy.tidy_session(d)
    with open(keep, "rb") as f:
        assert f.read() == b"ORIGINAL", "must not overwrite an existing file"
    # and the source is still there, not silently lost
    assert os.path.exists(os.path.join(d, "000_assist_20-27-54.mkv"))


def test_clips_are_found_before_and_after_tidying(tmp_path):
    """The Archive and a resumed recap must work on either layout."""
    d = _session(tmp_path)
    flat = sorted(os.path.basename(p) for _rel, p in tidy.iter_session_media(d))
    tidy.tidy_session(d)
    filed = sorted(os.path.basename(p) for _rel, p in tidy.iter_session_media(d))
    assert flat == filed
    assert "highlights_2026-07-27_20-10-11.mkv" not in flat   # not a kill clip
    assert "session_reel.mp4" not in flat
    assert len(flat) == 3                                     # the three .mkv clips


def test_relative_paths_are_archive_safe_after_tidying(tmp_path):
    """The Archive resolves 'sub/name' one level deep, so tidied clips must be
    addressable as 'clips/<name>'."""
    d = _session(tmp_path)
    tidy.tidy_session(d)
    rels = [rel for rel, _p in tidy.iter_session_media(d)]
    assert all(r.startswith("clips/") for r in rels), rels
    assert all(len(r.split("/")) == 2 for r in rels)


def test_empty_or_missing_dir_is_harmless(tmp_path):
    assert tidy.plan("") == []
    assert tidy.tidy_session(str(tmp_path / "nope")) == {}
