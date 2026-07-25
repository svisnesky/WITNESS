"""Kills = your kills. Assists (someone else's kill) and a finisher on your own
down must NOT inflate the headline count or the reel's 'N kills'."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_kill_count_excludes_assists_and_own_finisher():
    assert main._kill_count(["down"]) == 1
    assert main._kill_count(["precision"]) == 1
    assert main._kill_count(["assist"]) == 0            # not your kill
    assert main._kill_count(["down", "assist"]) == 1    # one real kill
    assert main._kill_count(["down", "finisher"]) == 1  # own finisher = same kill
    assert main._kill_count(["down", "down"]) == 2      # a real double
    assert main._kill_count(["finisher"]) == 1          # standalone finisher counts
    assert main._kill_count(["manual"]) == 0   # SAVE CLIP = kept moment, not a kill
    assert main._kill_count(["kill"]) == 1     # +1 KILL button arrives as 'kill'
    # the reported regression: 2 downs + 2 assists is 2 kills, not 4
    assert main._kill_count(["down", "down", "assist", "assist"]) == 2


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

    assert s["count"] == 2                     # two downs, assists excluded
    assert s["session_tags"].count("assist") == 2   # still in the breakdown

    try:
        os.remove(cfg["session_log"])
    except OSError:
        pass
