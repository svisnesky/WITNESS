"""Kills = your kills. Assists (someone else's kill) and a finisher on your own
down must NOT inflate the headline count or the reel's 'N kills'."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_kill_count_excludes_assists_and_own_finisher():
    assert main._kill_count(["down"]) == 1
    assert main._kill_count(["assist"]) == 0            # not your kill
    assert main._kill_count(["down", "assist"]) == 1    # one real kill
    assert main._kill_count(["down", "finisher"]) == 1  # own finisher = same kill
    assert main._kill_count(["down", "down"]) == 2      # a real double
    assert main._kill_count(["finisher"]) == 1          # standalone finisher counts
    assert main._kill_count(["manual"]) == 0   # SAVE CLIP = kept moment, not a kill
    assert main._kill_count(["kill"]) == 1     # +1 KILL button arrives as 'kill'
    # the reported regression: 2 downs + 2 assists is 2 kills, not 4
    assert main._kill_count(["down", "down", "assist", "assist"]) == 2


def test_precision_is_a_modifier_on_a_down_not_its_own_kill():
    """Marathon prints "RUNNER DOWNED +15 XP" AND "PRECISION DOWNED +25" for a
    single headshot down. This file used to assert precision == 1 kill on its
    own, which is what inflated a 10-down night into 18 "kills" and fired
    DOUBLE KILL on every headshot. The game's exfil panel is the arbiter — see
    tests/test_audit_mapping.py for the real matches this was checked against."""
    assert main._kill_count(["down", "precision"]) == 1
    assert main._kill_count(["precision"]) == 0        # a modifier alone is nothing
    assert main._kill_counts(["down", "precision"]) == (1, 0)
    # A genuine double headshot is still 2, not 4.
    assert main._kill_count(["down", "precision", "down", "precision"]) == 2


def test_handle_kill_headline_ignores_assists():
    """Driving _handle_kill directly: an assist event records the tag for the
    breakdown but does not bump s['count']."""
    events = []

    class _Ev:
        def __init__(self, raw):
            self.raw_line = raw
            self.is_self_kill = True
            self.victim = ""

    class _Obs:
        def set_counter(self, n):
            pass

    s = {
        "count": 0,
        "session_tags": [],
        "match_tags": [],
        "_coalesce_pending": [],
        "obs": _Obs(),
        "web": None,
        "medal_sounds": {},
        "cfg": {},
    }
    cfg = {"team_wipe": False, "announcer_medals": False, "show_overlays": False,
           "play_sound": False,
           "session_log": os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "_kill_count_test.csv")}

    for raw in ["RUNNER DOWN +15 XP", "ASSIST +10 XP", "PRECISION DOWN +25 XP",
                "ASSIST +10 XP"]:
        main._handle_kill(cfg, _Ev(raw), s)

    # ONE headshot down (the RUNNER DOWN + PRECISION DOWN pair), assists excluded.
    assert s["count"] == 1
    assert s["downs"] == 1
    assert s["session_tags"].count("assist") == 2   # still in the breakdown


def test_assist_earns_a_clip_and_nothing_else():
    """Stan's rule: "for those I just want the recordings. they shouldn't count
    toward anything else." So an assist must reach the clip coalescer but touch
    no counter, no gamertag log, and no heat."""
    class _Ev:
        def __init__(self, raw):
            self.raw_line = raw
            self.is_self_kill = True
            self.victim = ""

    class _Obs:
        def __init__(self):
            self.counter_calls = []

        def set_counter(self, n):
            self.counter_calls.append(n)

    class _Heat:
        def __init__(self):
            self.kills = 0

        def on_kill(self, tag="", clutch=False):
            self.kills += 1
            return []

    obs, hot = _Obs(), _Heat()
    s = {"count": 0, "session_tags": [], "match_tags": [], "_coalesce_pending": [],
         "obs": obs, "web": None, "medal_sounds": {}, "cfg": {}, "heat": hot}
    cfg = {"team_wipe": True, "announcer_medals": False, "show_overlays": False,
           "play_sound": True, "heat_streaks": True,
           "session_log": os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "_assist_test.csv")}

    main._handle_kill(cfg, _Ev("RUNNER ELIM [ASSIST] +10 XP"), s)

    assert s["count"] == 0                       # no headline kill
    assert s.get("downs", 0) == 0 and s.get("elims", 0) == 0
    assert hot.kills == 0                        # no heat / streak
    assert obs.counter_calls == []               # OBS counter untouched
    assert s["session_tags"] == ["assist"]       # recorded for the breakdown
    assert len(s["_coalesce_pending"]) == 1      # but it DOES get a clip
    assert s["_coalesce_pending"][0]["tag"] == "assist"

    try:
        os.remove(cfg["session_log"])
    except OSError:
        pass


def test_headshot_down_does_not_fire_double_kill():
    """A single headshot down prints RUNNER DOWNED + PRECISION DOWNED. That used
    to read as two downs and fire the DOUBLE KILL overlay + medal on every
    headshot — which is what Stan meant by "it was popping off double kills"."""
    fired = []

    class _Obs:
        def save_replay(self):
            return False

    for tags, expect in [
        (["down", "precision"], None),           # one headshot: no multikill
        (["down", "precision", "kill"], None),   # ...then finished: still one
        (["down", "down"], "DOUBLE KILL"),       # two real downs: a real double
        (["down", "precision", "down", "precision"], "DOUBLE KILL"),
        (["down", "down", "down"], "TRIPLE KILL"),
    ]:
        fired.clear()
        s = {"cfg": {"overlay_multikill": True, "announcer_medals": False},
             "obs": _Obs(), "web": None, "medal_sounds": {}, "clutch": False,
             "_coalesce_pending": [{"tag": t, "count": i, "epoch": 0.0,
                                    "manual": False} for i, t in enumerate(tags)]}
        orig = main.show_text_overlay
        main.show_text_overlay = lambda cfg, text, **kw: fired.append(text)
        try:
            main._flush_coalesce(s)
        finally:
            main.show_text_overlay = orig
        got = fired[0] if fired else None
        assert got == expect, f"{tags}: got {got!r}, expected {expect!r}"
