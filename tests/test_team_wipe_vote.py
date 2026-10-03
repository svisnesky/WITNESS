"""The TEAM WIPE vote is decided by the event's TAG, not raw substrings.

The vote used to be `tag == 'down' or 'elim' in raw or 'finisher' in raw`. A
garbled own elim ('RUNNER EL1M') is tagged 'kill' but contains no 'elim', so
it never voted (993 of 1,083 synthetic garbled elims, 1,128 of 1,272 garbled
finishers). Counts are unaffected; this is the overlay and the call-out.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _Ev:
    def __init__(self, raw):
        self.raw_line = raw
        self.is_self_kill = True
        self.victim = ""


class _Obs:
    def set_counter(self, n):
        pass


def _run(monkeypatch, tmp_path, raws):
    votes = []

    def fake_register(elim_times, now, wipe_size=3, **k):
        votes.append(now)
        return False
    monkeypatch.setattr(main, "register_elim", fake_register)
    s = {"count": 0, "downs": 0, "elims": 0, "session_tags": [], "match_tags": [],
         "_coalesce_pending": [], "obs": _Obs(), "web": None, "medal_sounds": {},
         "cfg": {}}
    cfg = {"team_wipe": True, "announcer_medals": False, "show_overlays": False,
           "play_sound": False, "heat_streaks": False,
           "session_log": str(tmp_path / "wipe_vote.csv")}
    for raw in raws:
        main._handle_kill(cfg, _Ev(raw), s)
    return votes, s


def test_garbled_elim_tagged_kill_votes_for_team_wipe(monkeypatch, tmp_path):
    votes, s = _run(monkeypatch, tmp_path, ["RUNNER EL1M +10 XP"])
    assert s["session_tags"] == ["kill"]
    assert len(votes) == 1


def test_garbled_finisher_votes_for_team_wipe(monkeypatch, tmp_path):
    votes, s = _run(monkeypatch, tmp_path, ["F1NISHER 5z +50"])
    assert s["session_tags"] == ["finisher"]
    assert len(votes) == 1


def test_clean_down_and_elim_still_vote(monkeypatch, tmp_path):
    votes, _s = _run(monkeypatch, tmp_path, ["RUNNER DOWN +15 XP"])
    assert len(votes) == 1
    votes, _s = _run(monkeypatch, tmp_path, ["RUNNER ELIM +10 XP"])
    assert len(votes) == 1


def test_manual_kill_and_own_finisher_do_not_vote(monkeypatch, tmp_path):
    votes, s = _run(monkeypatch, tmp_path, ["MANUAL +1 (added from iPad)"])
    assert s["session_tags"] == ["manual_kill"]
    assert votes == []
    # your down votes once; the elim that finishes it is the same runner
    votes, s = _run(monkeypatch, tmp_path, ["RUNNER DOWN +15 XP", "RUNNER ELIM +10 XP"])
    assert s["session_tags"] == ["down", "kill"]
    assert len(votes) == 1


def test_assist_and_precision_do_not_vote(monkeypatch, tmp_path):
    votes, _s = _run(monkeypatch, tmp_path,
                     ["RUNNER ELIM [ASSIST] +10 XP", "PRECISION DOWN +25"])
    assert votes == []
