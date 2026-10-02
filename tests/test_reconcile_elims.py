"""Runner Elims are reconciled against the exfil panel, the per-match headline
can't sit below the panel, and a match break closes each match.

Downs were reconciled against the game's 'Runners Downed' but elims never were,
so a missed RUNNER ELIM popup left the session record below the game's own
numbers (07-16 m1, 07-23 m4, 07-24 m6 and 07-27 m6 in the audited logs). The
headline kill count could also end a match below max(downs, elims), which is
impossible: every elim is a different dead runner (07-27 m6: headline 2, game
says 3 runners eliminated).

Rules pinned here:
  - same guards as the downs: 3+ panel labels besides the outcome, sane values,
    never subtract; the elim gap is capped at 3 (corpus max is 2);
  - the floor is per match (count_base), or it never fires after match 1;
  - AUDIT lines, the accuracy tally and the canary keep the DETECTED tags;
  - a second capture of the same panel credits nothing twice.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exfil_stats  # noqa: E402
import main  # noqa: E402

EXFIL_LINES = ["Runner Damage 576", "Inventory Value 26,424"]
GAME_LINES = ["RUNNER DOWN +15 XP"]

# 07-23 m4: two downs detected, the game says 2 downs AND 2 runner elims.
PANEL_0723_M4 = {"runners_downed": 2, "runner_elims": 2, "combatant_elims": 3,
                 "runner_damage": 576}
# 07-27 m6: the game says 2 downs, 3 runner elims.
PANEL_0727_M6 = {"runners_downed": 2, "runner_elims": 3, "combatant_elims": 1,
                 "runner_damage": 614}
TAGS_0727_M6 = ["assist", "down", "kill", "finisher", "down", "assist",
                "finisher", "assist", "assist", "assist", "assist"]


class _Obs:
    def __init__(self):
        self.counter = None

    def get_record_directory(self):
        return ""

    def set_counter(self, n):
        self.counter = n


class _Web:
    def __init__(self):
        self.rows = []

    def record(self, count, tag, text):
        self.rows.append((count, tag, text))

    def set_heat(self, *a, **k):
        pass


class _Canary:
    def __init__(self):
        self.audits = []

    def note_audit(self, *a):
        self.audits.append(a)


def _session(**kw):
    s = {"count": 0, "downs": 0, "elims": 0, "session_tags": [], "match_tags": [],
         "match_clips": [], "match_num": 0, "obs": _Obs(), "web": _Web(),
         "cfg": {}, "heat": None, "session_id": "s1", "clutch": False,
         "organize": False, "last_save": 0, "min_save": 2.0, "medal_sounds": {}}
    s.update(kw)
    return s


@pytest.fixture
def exfil(monkeypatch):
    """Neutralise the heavy exfil work. Returns (panels, logged): append the
    panel dict each capture should read; logged collects log_kill rows."""
    panels = []
    logged = []

    def fake_capture(cfg, engine, save_dir="", retries=3):
        return dict(panels[0] if len(panels) == 1 else panels.pop(0)), [], "died"
    monkeypatch.setattr(exfil_stats, "capture_exfil_stats", fake_capture)
    monkeypatch.setattr(exfil_stats, "log_outcome", lambda *a, **k: None)
    monkeypatch.setattr(exfil_stats, "read_outcome", lambda *a, **k: "died")
    monkeypatch.setattr(exfil_stats, "log_match_stats", lambda *a, **k: None)
    monkeypatch.setattr(main, "_build_match_reel_async", lambda *a, **k: None)
    monkeypatch.setattr(main, "log_kill",
                        lambda cfg, ev, count: logged.append((ev.raw_line, count)))
    return panels, logged


def _pass(s, cfg=None, t=1.0):
    """One exfil appearance, then enough clear frames to re-arm the trigger."""
    cfg = {} if cfg is None else cfg
    main._maybe_capture_exfil(cfg, None, EXFIL_LINES, s, t)
    for i in range(main.EXFIL_REARM_FRAMES):
        main._maybe_capture_exfil(cfg, None, GAME_LINES, s, t + 0.2 * (i + 1))


# --- the pure guards ----------------------------------------------------------

def test_reconcile_elims_guards():
    good = dict(PANEL_0723_M4)
    assert main._reconcile_missed_elims(["down", "down"], good) == 2
    # fewer than 3 labels besides the outcome -> not trusted
    assert main._reconcile_missed_elims([], {"runner_elims": 2}) == 0
    assert main._reconcile_missed_elims([], {"runner_elims": 2, "runners_downed": 2,
                                             "outcome": "died"}) == 0
    # a gap of 4 looks like a misread panel, not missed popups
    assert main._reconcile_missed_elims([], dict(good, runner_elims=4)) == 0
    assert main._reconcile_missed_elims([], dict(good, runner_elims=3)) == 3
    # never subtracts
    assert main._reconcile_missed_elims(["kill"] * 5, good) == 0
    # missing / insane values
    assert main._reconcile_missed_elims([], dict(good, runner_elims=None)) == 0
    assert main._reconcile_missed_elims([], dict(good, runner_elims=88)) == 0
    # like with like: a FINISHER on a pending elim is the same elim
    assert main._reconcile_missed_elims(["kill", "finisher"], good) == 1


def test_headline_floor_guards():
    assert main._headline_floor(PANEL_0727_M6, 2) == 1
    assert main._headline_floor(PANEL_0727_M6, 5) == 0          # never subtracts
    assert main._headline_floor(PANEL_0727_M6, 0) == 3
    assert main._headline_floor(dict(PANEL_0727_M6, runner_elims=9), 0) == 3   # capped
    assert main._headline_floor({"runner_elims": 3}, 0) == 0     # weak panel
    assert main._headline_floor(dict(PANEL_0727_M6, runners_downed=88), 0) == 0
    assert main._headline_floor(dict(PANEL_0727_M6, runner_elims=None), 0) == 2


# --- wired into the exfil pass -----------------------------------------------

def test_reconcile_elims_tops_up_to_game(exfil, capsys):
    panels, logged = exfil
    panels.append(PANEL_0723_M4)
    s = _session(count=2, downs=2, session_tags=["down", "down"],
                 match_tags=["down", "down"])
    _pass(s)
    out = capsys.readouterr().out
    assert s["session_tags"][:4] == ["down", "down", "elim", "elim"]
    assert s["elims"] == 2
    assert exfil_stats.count_events(s["session_tags"]) == (2, 2)   # = the game
    assert s["count"] == 2                 # the two elims finished the two downs
    assert [r[1] for r in s["web"].rows] == ["elim", "elim"]
    assert [r[0] for r in logged] == ["RECONCILED elim (exfil screen)"] * 2
    assert s["obs"].counter == 2
    assert "[reconcile] game credited more runner elims than detected — added 2" in out


def test_headline_floor_uses_per_match_count_base(exfil, capsys):
    """07-27 m6 as the SECOND match of a session: the floor must compare this
    match's kills, not the session total, or it never fires after match 1."""
    panels, logged = exfil
    panels += [dict(PANEL_0723_M4), dict(PANEL_0727_M6)]
    s = _session(count=2, downs=2, session_tags=["down", "down"],
                 match_tags=["down", "down"])
    _pass(s)                                   # match 1 closes at count 2
    assert s["count_base"] == 2
    # match 2: the live counter made 2 kills of it (down+kill+finisher, down+finisher)
    s["count"] = 4
    s["session_tags"] += TAGS_0727_M6
    s["match_tags"] = list(TAGS_0727_M6)
    _pass(s, t=100.0)
    out = capsys.readouterr().out
    assert s["count"] == 5                     # this match: 2 -> 3 = max(2 downs, 3 elims)
    assert s["count"] - 2 == 3
    assert s["count_base"] == 5
    assert ("RECONCILED floor (exfil screen)", 5) in logged
    assert (5, "kill", "RECONCILED — exfil screen kill floor") in s["web"].rows
    floor_lines = [ln for ln in out.splitlines() if "raised by" in ln]
    assert len(floor_lines) == 1
    # the desktop tiles parse 'game credited more downs/runner elims' lines into
    # extra tags; the floor line must never look like one
    assert "game credited more" not in floor_lines[0]


def test_duplicate_exfil_capture_does_not_double_credit(exfil, capsys):
    """The summary screen can flicker away long enough to re-arm and be read
    again. The second pass has an empty match_tags against the full panel."""
    panels, logged = exfil
    panels.append(PANEL_0723_M4)
    s = _session(count=1, downs=1, session_tags=["down"], match_tags=["down"])
    _pass(s)
    after_first = (s["count"], s["downs"], s["elims"], list(s["session_tags"]),
                   len(logged), len(s["web"].rows))
    assert s["count"] == 2 and s["downs"] == 2 and s["elims"] == 2
    _pass(s, t=50.0)                           # same panel, no new events
    _pass(s, t=90.0)
    out = capsys.readouterr().out
    assert (s["count"], s["downs"], s["elims"]) == after_first[:3]
    assert [t for t in s["session_tags"] if t != getattr(exfil_stats, "MATCH_BREAK", None)] \
        == [t for t in after_first[3] if t != getattr(exfil_stats, "MATCH_BREAK", None)]
    assert len(logged) == after_first[4]
    assert len(s["web"].rows) == after_first[5]
    assert "not reconciling it twice" in out


def test_new_events_after_an_exfil_reconcile_normally(exfil):
    """Only a pass with NO new event is a duplicate."""
    panels, logged = exfil
    panels += [dict(PANEL_0723_M4), dict(PANEL_0723_M4)]
    s = _session(count=2, downs=2, session_tags=["down", "down"],
                 match_tags=["down", "down"])
    _pass(s)
    s["count"] += 1
    s["downs"] += 1
    s["session_tags"].append("down")
    s["match_tags"] = ["down"]
    _pass(s, t=100.0)
    assert s["downs"] == 4                     # +1 missed down this match
    assert s["elims"] == 4                     # +2 elims each match


def test_audit_and_accuracy_lines_use_pre_reconcile_tags(exfil, capsys):
    panels, logged = exfil
    panels.append(PANEL_0723_M4)
    s = _session(count=1, downs=1, session_tags=["down"], match_tags=["down"],
                 canary=_Canary())
    _pass(s)
    out = capsys.readouterr().out
    # the AUDIT line reports what DETECTION saw ...
    assert "AUDIT downs: game 2, detected 1 — missed 1" in out
    assert "AUDIT elims: game 2, detected 0 — missed 2" in out
    # ... and so do the accuracy tally and the canary, though the session
    # record was topped up to the game's numbers
    assert s["accuracy"]["downs"]["detected"] == 1
    assert s["accuracy"]["elims"]["detected"] == 0
    assert s["canary"].audits == [(2, 2, 1, 0)]
    assert exfil_stats.count_events(s["session_tags"]) == (2, 2)


def test_reconcile_off_adds_nothing(exfil):
    panels, logged = exfil
    panels.append(PANEL_0727_M6)
    s = _session(count=1, downs=1, session_tags=["down"], match_tags=["down"])
    _pass(s, cfg={"exfil_reconcile": False})
    assert (s["count"], s["downs"], s["elims"]) == (1, 1, 0)
    assert logged == [] and s["web"].rows == []


# --- the match break ----------------------------------------------------------

def test_exfil_capture_appends_match_break_to_session_tags(exfil, monkeypatch):
    panels, logged = exfil
    panels.append(PANEL_0723_M4)
    monkeypatch.setattr(exfil_stats, "MATCH_BREAK", "exfil", raising=False)
    s = _session(count=2, downs=2, session_tags=["down", "down"],
                 match_tags=["down", "down"])
    _pass(s)
    # after the reconciled elims, so a reconciled elim can't fold the next
    # match's opening FINISHER
    assert s["session_tags"] == ["down", "down", "elim", "elim", "exfil"]


def test_no_match_break_attribute_appends_nothing(exfil, monkeypatch):
    """exfil_stats.MATCH_BREAK ships in another change; without it the exfil
    pass must not invent a tag the counter doesn't know."""
    panels, logged = exfil
    panels.append(PANEL_0723_M4)

    class _WithoutBreak:
        """exfil_stats as main sees it, minus MATCH_BREAK. (Deleting the real
        attribute would break exfil_stats's own code once it ships.)"""
        def __getattr__(self, name):
            if name == "MATCH_BREAK":
                raise AttributeError(name)
            return getattr(exfil_stats, name)
    monkeypatch.setitem(sys.modules, "exfil_stats", _WithoutBreak())
    s = _session(count=2, downs=2, session_tags=["down", "down"],
                 match_tags=["down", "down"])
    _pass(s)
    assert s["session_tags"] == ["down", "down", "elim", "elim"]


@pytest.mark.skipif(not hasattr(exfil_stats, "MATCH_BREAK"),
                    reason="exfil_stats.MATCH_BREAK ships with the detection-hotpath change")
def test_match_break_stops_a_pending_elim_leaking_into_the_next_match():
    mb = exfil_stats.MATCH_BREAK
    assert exfil_stats.count_events(["kill", mb, "finisher"]) == (0, 2)
    assert exfil_stats.count_events(["kill", "finisher"]) == (0, 1)
