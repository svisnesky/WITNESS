"""The data behind the clip viewer: what happened in each clip, and when.

Stan: "auto reels are cool but i feel like they miss stuff... show the clip, what
happened in the clip that way its easy to determine what happened."
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clip_index as ci  # noqa: E402


def test_parses_the_real_filename_shape():
    num, tags, clock = ci.parse_name("003_down+precision_21-36-32.mkv")
    assert num == 3
    assert tags == ["down", "precision"]
    assert clock == "21:36:32"


def test_a_name_that_does_not_fit_is_skipped_not_guessed():
    for bad in ("highlights_2026-07-29.mkv", "session_reel.mp4", "junk.mkv"):
        assert ci.parse_name(bad)[1] == [] or ci.parse_name(bad)[0] is None


def test_kill_positions_are_measured_back_from_the_save():
    """The Replay Buffer save lands at the END of the footage, so a kill 8s before
    the save sits 8s before the end."""
    dur, saved = 30.0, 1000.0
    ev = ci.clip_events(dur, saved, [{"epoch": 992.0}])   # 8s before the save
    assert ev == [{"t": 22.0, "manual": False, "label": "KILL"}]


def test_several_kills_come_back_in_order():
    ev = ci.clip_events(30.0, 1000.0,
                        [{"epoch": 995.0}, {"epoch": 985.0}, {"epoch": 998.0}])
    assert [e["t"] for e in ev] == [15.0, 25.0, 28.0]


def test_a_manual_press_is_labelled_as_such():
    ev = ci.clip_events(30.0, 1000.0, [{"epoch": 990.0, "manual": True}])
    assert ev[0]["label"] == "+1 KILL" and ev[0]["manual"] is True


def test_nonsense_timings_produce_no_marker_rather_than_a_wrong_one():
    """A marker in the wrong place is worse than none — it sends you scrubbing to
    a moment where nothing happens."""
    assert ci.clip_events(30.0, 1000.0, [{"epoch": 500.0}]) == []   # way too early
    assert ci.clip_events(30.0, 1000.0, [{"epoch": 1099.0}]) == []  # after the save
    assert ci.clip_events(None, 1000.0, [{"epoch": 995.0}]) == []   # no duration
    assert ci.clip_events(30.0, 0, [{"epoch": 995.0}]) == []        # no save time
    assert ci.clip_events(30.0, 1000.0, []) == []


def test_positions_are_clamped_into_the_clip():
    ev = ci.clip_events(30.0, 1000.0, [{"epoch": 1001.0}])   # 1s AFTER the save
    assert ev and 0 <= ev[0]["t"] <= 30.0


def test_summaries_read_like_english():
    assert ci.describe(["down"], "PiNDLESKIN") == "Downed PiNDLESKIN"
    assert "headshot" in ci.describe(["down", "precision"], "PiNDLESKIN")
    assert "finished them" in ci.describe(["down", "kill"], "GOOSE")
    assert "melee" in ci.describe(["down", "kill", "finisher"], "GOOSE")
    assert ci.describe(["manual"]) == "Moment you saved by hand"
    assert "2 assists" in ci.describe(["assist", "assist"])
    assert ci.describe(["down", "down"]).startswith("Downed 2 runners")


def test_an_unknown_victim_still_reads_naturally():
    assert ci.describe(["down"]) == "Downed a runner"


def test_summary_never_comes_back_empty():
    for tags in ([], ["weird"], ["assist"], ["kill"]):
        assert ci.describe(tags)


def test_index_reads_a_real_session_folder(tmp_path):
    import match_reel
    sess = tmp_path / "2026-08-08_21-35-33"
    sess.mkdir()
    clip = sess / "005_down+precision_22-07-27.mkv"
    clip.write_bytes(b"x")
    match_reel.write_kill_sidecar(str(clip), 1000.0,
                                  [{"epoch": 992.0, "manual": False}],
                                  victim="FRISKYDINGO")
    idx = ci.build_index(str(sess), ffmpeg="", with_duration=False)
    assert len(idx) == 1
    c = idx[0]
    assert c["victim"] == "FRISKYDINGO"
    assert c["downs"] == 1
    assert [ch["label"] for ch in c["chips"]] == ["DOWN", "HEADSHOT"]
    assert "headshot" in c["summary"]
    assert c["rel"] == "005_down+precision_22-07-27.mkv"


def test_index_works_on_a_tidied_folder(tmp_path):
    """Old sessions get tidied, so clips live in clips/ — the viewer must still
    find them and address them correctly for the video route."""
    import tidy
    sess = tmp_path / "sess"
    sess.mkdir()
    (sess / "001_down_20-07-34.mkv").write_bytes(b"x")
    tidy.tidy_session(str(sess))
    idx = ci.build_index(str(sess), with_duration=False)
    assert len(idx) == 1
    assert idx[0]["rel"] == "clips/001_down_20-07-34.mkv"


def test_index_is_chronological(tmp_path):
    import match_reel
    sess = tmp_path / "sess"
    sess.mkdir()
    for n in ("013_assist_22-02-17.mkv", "013_down_21-48-21.mkv"):
        p = sess / n
        p.write_bytes(b"x")
        s = match_reel._save_time(str(p))
        os.utime(str(p), (s, s))
    idx = ci.build_index(str(sess), with_duration=False)
    assert [c["name"] for c in idx] == ["013_down_21-48-21.mkv",
                                        "013_assist_22-02-17.mkv"]


def test_non_clip_files_are_excluded(tmp_path):
    sess = tmp_path / "sess"
    sess.mkdir()
    for n in ("highlights_x.mkv", "session_reel.mp4", "001_down_20-07-34.mkv"):
        (sess / n).write_bytes(b"x")
    idx = ci.build_index(str(sess), with_duration=False)
    assert [c["name"] for c in idx] == ["001_down_20-07-34.mkv"]
