"""Reels must play in the order things happened, and Play of the Night must
pick the clip with the most PEOPLE taken out.

Both regressions are from the 2026-07-27 session:
  - four out-of-order cuts in the session reel, because clips sorted by NAME and
    the leading number is the kill count (which repeats now that assists don't
    bump it) — '013_assist_22-02' sorted before '013_down_21-48'.
  - Play of the Night picked one runner downed-then-finished over a genuine
    two-person double, because it scored the composite kill count.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import match_reel  # noqa: E402


# The real out-of-order names from the session, oldest-first as they SHOULD play.
REAL_ORDER = [
    "000_assist_20-27-54.mkv",
    "001_down+precision_20-55-40.mkv",
    "003_down+precision+kill_20-56-17.mkv",
    "007_down_21-04-57.mkv",
    "008_down_21-08-59.mkv",
    "009_down+precision_21-32-28.mkv",
    "011_down+precision+assist_21-44-33.mkv",
    "013_down_21-48-21.mkv",          # sorted AFTER 013_assist_22-02 by name
    "013_assist_22-02-17.mkv",
    "014_down+assist_22-05-25.mkv",   # sorted AFTER 014_assist+kill_22-05-42
    "014_assist+kill_22-05-42.mkv",
    "015_assist_22-27-06.mkv",
    "016_down+kill+finisher_22-27-48.mkv",
    "018_down+assist+finisher_22-30-02.mkv",
    "018_assist_22-30-57.mkv",
]


def _make_clips(d, names):
    """Create the files with mtimes matching their filename timestamps."""
    paths = []
    for i, n in enumerate(names):
        p = os.path.join(d, n)
        with open(p, "wb") as f:
            f.write(b"x")
        secs = match_reel._save_time(p)
        os.utime(p, (secs, secs))
        paths.append(p)
    return paths


def test_session_clips_sort_chronologically_not_alphabetically():
    with tempfile.TemporaryDirectory() as d:
        # Feed them in alphabetical order — the broken order.
        alpha = sorted(REAL_ORDER)
        assert alpha != REAL_ORDER, "fixture must actually differ from name order"
        _make_clips(d, alpha)
        got = [os.path.basename(p) for p in match_reel.sort_chronologically(
            [os.path.join(d, n) for n in alpha])]
        assert got == REAL_ORDER, got


def test_no_backwards_cuts_in_sorted_order():
    with tempfile.TemporaryDirectory() as d:
        _make_clips(d, sorted(REAL_ORDER))
        ordered = match_reel.sort_chronologically(
            [os.path.join(d, n) for n in sorted(REAL_ORDER)])
        times = [match_reel._save_time(p) for p in ordered]
        assert times == sorted(times), times


def test_sort_accepts_reel_dicts():
    with tempfile.TemporaryDirectory() as d:
        _make_clips(d, ["005_down_21-00-00.mkv", "004_down_20-00-00.mkv"])
        dicts = [{"path": os.path.join(d, "005_down_21-00-00.mkv"), "kills": 1},
                 {"path": os.path.join(d, "004_down_20-00-00.mkv"), "kills": 1}]
        got = [os.path.basename(c["path"])
               for c in match_reel.sort_chronologically(dicts)]
        assert got == ["004_down_20-00-00.mkv", "005_down_21-00-00.mkv"]


def test_sidecar_epoch_wins_and_survives_midnight():
    """Filename time is seconds-of-day, so a session crossing midnight would
    order 00:05 before 23:50. The sidecar's absolute epoch prevents that."""
    with tempfile.TemporaryDirectory() as d:
        late = os.path.join(d, "001_down_23-50-00.mkv")
        early = os.path.join(d, "002_down_00-05-00.mkv")   # next DAY
        for p in (late, early):
            with open(p, "wb") as f:
                f.write(b"x")
        match_reel.write_kill_sidecar(late, 1_000_000.0, [])
        match_reel.write_kill_sidecar(early, 1_001_000.0, [])   # later in absolute time
        got = [os.path.basename(p)
               for p in match_reel.sort_chronologically([early, late])]
        assert got == [os.path.basename(late), os.path.basename(early)], got


def test_potg_prefers_more_distinct_enemies_over_more_popups():
    """Stan's case: one guy downed then finished (3 scoring popups off ONE
    enemy) must NOT beat downing two different people."""
    one_guy_finished = {"path": "/a/016_down+kill+finisher_22-27-48.mkv",
                        "tag": "down+kill+finisher", "kills": 2, "downs": 1}
    two_downs = {"path": "/a/017_down+down_22-31-00.mkv",
                 "tag": "down+down", "kills": 2, "downs": 2}
    assert match_reel.pick_potg([one_guy_finished, two_downs]) is two_downs
    # Order of the input must not matter.
    assert match_reel.pick_potg([two_downs, one_guy_finished]) is two_downs


def test_potg_infers_downs_from_the_tag_when_absent():
    a = {"path": "/a/1_down+kill+finisher_1.mkv", "tag": "down+kill+finisher",
         "kills": 2}
    b = {"path": "/a/2_down+precision+down+precision_2.mkv",
         "tag": "down+precision+down+precision", "kills": 2}
    assert match_reel.pick_potg([a, b]) is b


def test_potg_still_needs_two_clips():
    assert match_reel.pick_potg([{"path": "/a/x.mkv", "tag": "down",
                                  "kills": 1, "downs": 1}]) is None


def _c(tag, kills=1, downs=None, path=None):
    d = {"path": path or f"/a/x_{tag}_1.mkv", "tag": tag, "kills": kills}
    if downs is not None:
        d["downs"] = downs
    return d


def test_manual_saves_are_potn_eligible():
    """Stan chose this: a SAVE CLIP press is the only signal in the app that comes
    from a human deciding "that was good", so it must be able to win. Previously
    it scored 0 kills / 0 downs and could never place."""
    manual = _c("manual", kills=0, downs=0)
    routine = _c("down", kills=1, downs=1)
    # A deliberate save beats a routine single down...
    assert match_reel.pick_potg([routine, manual]) is manual
    assert match_reel.pick_potg([manual, routine]) is manual


def test_a_real_multikill_still_beats_a_manual_save():
    """Eligible, not privileged."""
    manual = _c("manual", kills=0, downs=0)
    double = _c("down+down", kills=2, downs=2)
    assert match_reel.pick_potg([manual, double]) is double
    triple = _c("down+down+down", kills=3, downs=3)
    assert match_reel.pick_potg([manual, double, triple]) is triple


def test_manual_bonus_does_not_leak_into_kill_counts():
    """THE trap: a SAVE CLIP is still worth zero in the stats. The boost must live
    only in the ranking key, never in the clip's kills/downs."""
    import main
    manual = _c("manual", kills=0, downs=0)
    match_reel.pick_potg([manual, _c("down", 1, 1)])
    assert manual["kills"] == 0 and manual["downs"] == 0, manual
    assert main._kill_count(["manual"]) == 0
    assert main._kill_counts(["manual"]) == (0, 0)


def test_plus_one_press_is_not_given_the_manual_bonus():
    """'manual_kill' is a +1 KILL press — already a real down. Substring matching
    on 'manual' would have handed it the bonus on top."""
    assert not match_reel._is_manual("manual_kill")
    assert match_reel._is_manual("manual")
    assert match_reel._is_manual("down+manual")
    assert not match_reel._is_manual("down+precision")
    # A +1 press must not have its COUNT inflated — it already has a real down.
    plus_one = _c("manual_kill", kills=1, downs=1)
    assert match_reel.potg_score(plus_one)[:2] == (1, 1)
    # But it IS human-flagged, same as SAVE CLIP, so both carry that bit...
    assert match_reel.human_flagged("manual_kill")
    assert match_reel.human_flagged("manual")
    assert not match_reel.human_flagged("down+precision")
    # ...and both beat a clip the app merely happened to catch.
    routine = _c("down", kills=1, downs=1)
    assert match_reel.pick_potg([routine, plus_one]) is plus_one


def test_assist_only_clips_still_lose_to_a_manual_save():
    assist = _c("assist", kills=0, downs=0)
    manual = _c("manual", kills=0, downs=0)
    assert match_reel.pick_potg([assist, manual]) is manual
