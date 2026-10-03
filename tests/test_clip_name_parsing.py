"""Clip filenames are <count>_<tags>_<HH-MM-SS>.<ext>, and a tag can contain '_'.

A +1 KILL press is tagged 'manual_kill'. Every reader used to split the name on
'_', so '002_manual_kill_20-05-00.mkv' came back as tag 'manual' at clock 'kill':
the clip viewer showed a hand-credited kill as a 0-kill 'SAVED' moment, and
Wrapped and the export read it the same way. The shared parser is now anchored
on the clock at the END of the name, so the tags keep their underscores.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clip_index as ci  # noqa: E402


def test_a_plus_one_kill_keeps_its_tag_and_its_clock():
    assert ci.parse_name("002_manual_kill_20-05-00.mkv") == (
        2, ["manual_kill"], "20:05:00")


def test_a_combo_with_a_plus_one_splits_on_plus_only():
    assert ci.parse_name("003_down+manual_kill_20-06-00.mkv") == (
        3, ["down", "manual_kill"], "20:06:00")


def test_ordinary_names_parse_exactly_as_before():
    assert ci.parse_name("003_down+precision_21-36-32.mkv") == (
        3, ["down", "precision"], "21:36:32")
    assert ci.parse_name("000_assist_20-27-54.mp4") == (0, ["assist"], "20:27:54")


def test_a_path_is_reduced_to_its_name():
    assert ci.parse_name("clips/002_manual_kill_20-05-00.mkv") == (
        2, ["manual_kill"], "20:05:00")


def test_the_clock_never_holds_a_gamertag():
    """The watchlist death clip is tagged downedby_<name>; the old split put the
    player's name in the clock field."""
    num, tags, clock = ci.parse_name("005_downedby_some_runner_20-05-00.mkv")
    assert num == 5 and clock == "20:05:00"
    assert tags == ["downedby_some_runner"]


def test_names_that_do_not_fit_return_exactly_the_fallback():
    """Callers fall back on (None, [], '') — OBS default names still count as a
    1-kill clip in a rebuilt reel because of that. Never a partial guess."""
    for bad in ("Replay 2026-09-01 20-05-00.mkv", "highlights_2026-07-29.mkv",
                "session_reel.mp4", "junk.mkv", "abc_down_20-05-00.mkv",
                "002_down_2026.mkv", "002__20-05-00.mkv", ""):
        assert ci.parse_name(bad) == (None, [], ""), bad


def test_the_clip_viewer_labels_a_plus_one_as_a_kill(tmp_path):
    (tmp_path / "002_manual_kill_20-05-00.mkv").write_bytes(b"x")
    (tmp_path / "003_down+manual_kill_20-06-00.mkv").write_bytes(b"x")
    rows = {r["name"]: r for r in ci.build_index(str(tmp_path), with_duration=False)}
    plus1 = rows["002_manual_kill_20-05-00.mkv"]
    assert [c["label"] for c in plus1["chips"]] == ["+1 KILL"]
    assert plus1["clock"] == "20:05:00"
    assert "credited by hand" in plus1["summary"]
    assert plus1["summary"] != "Moment you saved by hand"
    combo = rows["003_down+manual_kill_20-06-00.mkv"]
    assert [c["label"] for c in combo["chips"]] == ["DOWN", "+1 KILL"]


def test_an_export_of_a_plus_one_keeps_the_tag(tmp_path):
    import export_reel
    (tmp_path / "002_manual_kill_20-05-00.mkv").write_bytes(b"x")
    clip = export_reel.resolve(str(tmp_path),
                               [{"rel": "002_manual_kill_20-05-00.mkv"}])[0]
    assert clip["tag"] == "manual_kill"


def test_the_shorts_parser_was_already_right_and_is_unchanged():
    """shorts._parse_name uses a greedy [A-Za-z+_]+ match, which backtracks to
    the last '_' before the clock. Left alone on purpose."""
    import shorts
    assert shorts._parse_name("002_manual_kill_20-05-00.mkv") == (2, "MANUAL KILL")
    assert shorts._parse_name("003_down+manual_kill_20-06-00.mkv") == (
        3, "DOWN + MANUAL KILL")
