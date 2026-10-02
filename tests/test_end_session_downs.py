"""The saved session record's 'down' field is the count_events downs.

It used to be down+kill: the retired rule that filed every RUNNER ELIM popup
under downs. Against the audited matches the saved field was exact on 3 of 13;
the count_events downs (the same number the 'Session over' line prints and the
game's 'Runners Downed' reports) is exact on 13 of 13.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import stats  # noqa: E402

QUIET = {"make_card": False, "witness_report": False, "make_montage": False,
         "play_of_the_night": False, "make_shorts": False,
         "make_session_reel": False, "tidy_session_folder": False}


def _saved(monkeypatch, tags, kills):
    got = []
    monkeypatch.setattr(stats, "record_session",
                        lambda base, session: got.append(session) or "recap.html")
    main._end_session(dict(QUIET), tags, start_monotonic=0.0, start_wall="20:00",
                      dry_run=False, obs=None, session_id="s1", kills=kills,
                      web=None)
    assert len(got) == 1
    return got[0]


def test_end_session_saves_count_events_downs(monkeypatch, capsys):
    # two runners, each downed then eliminated (one with a melee finish), a
    # headshot modifier, and a teammate's kill
    tags = ["down", "kill", "finisher", "down", "precision", "kill", "assist"]
    session = _saved(monkeypatch, tags, kills=2)
    assert session["down"] == 2                    # was 4 (down + kill)
    out = capsys.readouterr().out
    assert "downs 2  elims 2" in out               # the same number as printed


def test_end_session_down_field_counts_manual_and_reconciled_downs(monkeypatch):
    """A '+1 KILL' press is a down the OCR missed; a reconciled elim ('elim')
    is not a down."""
    tags = ["manual_kill", "down", "elim"]
    session = _saved(monkeypatch, tags, kills=2)
    assert session["down"] == 2                    # was 1 (manual press ignored)
    assert session["total"] == 2
