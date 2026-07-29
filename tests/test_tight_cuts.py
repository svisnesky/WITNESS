"""Tight reel cuts: clips start just before the kill, not 30s early."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import match_reel as mr  # noqa: E402


def test_trim_math_basic():
    # 30s clip, kill 6s before the end, 8s preroll -> start at 30-6-8 = 16
    assert mr._trim_start(30.0, [6.0], 8.0) == 16.0


def test_trim_keeps_min_length():
    # 12s clip, kill 1s from the end, 8s preroll -> start 3 (leaves 9s, fine)
    assert mr._trim_start(12.0, [1.0], 8.0) == 3.0
    # kill 0.5s from the end with a tiny 2s preroll -> raw start 9.5 would
    # leave only 2.5s; clamped so at least 6s of clip remains
    assert mr._trim_start(12.0, [0.5], 2.0) == 6.0


def test_tiny_gain_skipped():
    # kill 20s before the end of a 30s clip -> start 2s: not worth it? 2>=1 -> trims
    assert mr._trim_start(30.0, [20.0], 8.0) == 2.0
    # kill 25s before the end -> start would be -3 -> no trim
    assert mr._trim_start(30.0, [25.0], 8.0) == 0.0


def test_earliest_kill_wins():
    # kills 4s and 14s before the end: cut for the EARLIEST (14) so both show
    assert mr._trim_start(30.0, [4.0, 14.0], 8.0) == 8.0


def test_garbage_offsets_ignored():
    assert mr._trim_start(30.0, [999.0], 8.0) == 0.0
    assert mr._trim_start(30.0, [-5.0], 8.0) == 0.0
    assert mr._trim_start(None, [5.0], 8.0) == 0.0
    assert mr._trim_start(30.0, [], 8.0) == 0.0


def test_sidecar_roundtrip(tmp_path):
    clip = str(tmp_path / "005_down_21-50-43.mkv")
    open(clip, "wb").write(b"x")
    mr.write_kill_sidecar(clip, 1000.0, [{"epoch": 992.0, "manual": False}])
    # A lone ordinary kill now takes the longer CONTEXT preroll (16s default),
    # so the setup is visible: 30 - 8 - 16 = 6.
    ss = mr.clip_trim_start({"path": clip}, 30.0, "ffmpeg", preroll=8.0)
    assert ss == 6.0
    # Multikill clips still use the tight preroll: 30 - 8 - 8 = 14.
    ss = mr.clip_trim_start({"path": clip, "kills": 2}, 30.0, "ffmpeg",
                            preroll=8.0)
    assert ss == 14.0
    # And the context preroll is a knob — set it to 8 for the old behaviour.
    ss = mr.clip_trim_start({"path": clip}, 30.0, "ffmpeg", preroll=8.0,
                            context_preroll=8.0)
    assert ss == 14.0


def test_manual_kill_gets_longer_preroll(tmp_path):
    clip = str(tmp_path / "004_kill_21-49-20.mkv")
    open(clip, "wb").write(b"x")
    mr.write_kill_sidecar(clip, 1000.0, [{"epoch": 996.0, "manual": True}])
    ss = mr.clip_trim_start({"path": clip}, 30.0, "ffmpeg",
                            preroll=8.0, manual_preroll=18.0)
    # 30 - 4 - 18 = 8
    assert ss == 8.0


def test_folded_clip_epochs_extend_the_window(tmp_path):
    a = str(tmp_path / "002_down_21-36-20.mkv")
    b = str(tmp_path / "003_down_21-36-44.mkv")
    for p in (a, b):
        open(p, "wb").write(b"x")
    mr.write_kill_sidecar(a, 980.0, [{"epoch": 972.0}])   # folded earlier save
    mr.write_kill_sidecar(b, 1004.0, [{"epoch": 996.0}])  # survivor
    ss = mr.clip_trim_start({"path": b, "_folded_paths": [a]}, 30.0, "ffmpeg",
                            preroll=8.0)
    # earliest kill epoch 972 -> offset 1004-972=32 > dur -> sane-capped ok:
    # 30 - 32 - 8 < 0 -> no trim (folded kill is right at the clip head)
    assert ss == 0.0


def test_no_sidecar_no_trim(tmp_path):
    clip = str(tmp_path / "001_down_19-48-47.mkv")
    open(clip, "wb").write(b"x")
    assert mr.clip_trim_start({"path": clip}, 30.0, "ffmpeg") == 0.0


def test_single_kill_gets_more_context_than_a_multikill():
    """Joe's note on a real reel: "it's a bit chaotic, like someone telling you
    something with no context... I like to watch the situation unfold if they're
    just normal kills." One kill = show the approach; a multikill = get to it."""
    one = {"kills": 1}
    many = {"kills": 3}
    assert mr.preroll_for(one, 8.0, 18.0, 16.0) == 16.0
    assert mr.preroll_for(many, 8.0, 18.0, 16.0) == 8.0
    # A manual +1 still wins — the press lands long after the kill.
    assert mr.preroll_for(one, 8.0, 18.0, 16.0, is_manual=True) == 18.0
    assert mr.preroll_for(many, 8.0, 18.0, 16.0, is_manual=True) == 18.0
    # Missing/zero kills is treated as one.
    assert mr.preroll_for({}, 8.0, 18.0, 16.0) == 16.0


def test_longer_preroll_keeps_more_of_the_clip():
    """On the real shape from the session log: a 33s clip whose kill landed ~8s
    before the save. At 8s preroll it started at 17s (16s kept); the longer
    context preroll keeps roughly twice as much."""
    dur, offset_from_end = 33.0, 8.0
    tight = mr._trim_start(dur, [offset_from_end], 8.0)
    context = mr._trim_start(dur, [offset_from_end], 16.0)
    assert tight == 17.0
    assert context == 9.0
    assert (dur - context) > (dur - tight)


def test_context_preroll_cannot_exceed_the_footage():
    """A preroll longer than the clip must not produce a negative in-point."""
    assert mr._trim_start(12.0, [8.0], 16.0) == 0.0   # no cut worth making
