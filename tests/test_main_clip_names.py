"""+1 KILL clips keep their tag when read back from disk.

A +1 KILL press is tagged 'manual_kill', and clips are named
NNN_<tags>_HH-MM-SS. _session_clips_from_dir split on '_', so
'002_manual_kill_20-05-00.mkv' came back as tag 'manual' (a kept moment, 0
kills) in Rebuild Last Session, the launch-time resume and Play of the Night.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def _clips(tmp_path, *names):
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    return {os.path.basename(c["path"]): c for c in main._session_clips_from_dir(str(tmp_path))}


def test_manual_kill_clips_keep_their_tag_and_kill(tmp_path):
    got = _clips(tmp_path, "002_manual_kill_20-05-00.mkv",
                 "003_down+manual_kill_20-06-00.mkv")
    a = got["002_manual_kill_20-05-00.mkv"]
    assert a["tag"] == "manual_kill" and a["kills"] == 1 and a["downs"] == 1
    b = got["003_down+manual_kill_20-06-00.mkv"]
    assert b["tag"] == "down+manual_kill" and b["kills"] == 2 and b["downs"] == 2


def test_ordinary_tags_are_unchanged(tmp_path):
    got = _clips(tmp_path, "001_down+finisher_20-07-34.mkv",
                 "004_down+precision_20-08-00.mkv", "005_manual_20-09-00.mkv",
                 "006_assist_20-10-00.mkv")
    assert got["001_down+finisher_20-07-34.mkv"]["kills"] == 1
    assert got["004_down+precision_20-08-00.mkv"]["tag"] == "down+precision"
    assert got["005_manual_20-09-00.mkv"]["kills"] == 0
    assert got["006_assist_20-10-00.mkv"]["kills"] == 0


def test_names_that_dont_fit_stay_one_kill_clips(tmp_path):
    """OBS default names and hand-dropped files must not drop out of reels."""
    got = _clips(tmp_path, "Replay 2026-07-29 20-00-15.mkv", "clip.mp4")
    for c in got.values():
        assert c["tag"] == "kill" and c["kills"] == 1


def test_a_watchlist_death_clip_keeps_its_name_out_of_the_clock(tmp_path):
    assert main._clip_tags_from_name("007_downedby_somestreamer_21-00-00.mkv") == \
        ["downedby_somestreamer"]


def test_the_play_of_the_night_card_spells_out_the_tag(tmp_path, monkeypatch):
    import match_reel
    (tmp_path / "002_manual_kill_20-05-00.mkv").write_bytes(b"x")
    seen = {}

    def fake_build(clips, out, ff, title, kills, sub, *a, **k):
        seen["sub"] = sub
        return True

    monkeypatch.setattr(match_reel, "build_match_reel", fake_build)
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    cfg = {"make_montage": False, "play_of_the_night": True, "make_shorts": False,
           "make_session_reel": False, "tidy_session_folder": False,
           "reel_music": False}
    main._build_session_artifacts(cfg, str(tmp_path), ["manual_kill"])
    assert "MANUAL KILL" in seen["sub"][0]
