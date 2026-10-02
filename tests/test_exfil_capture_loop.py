"""capture_exfil_stats runs ON the detection thread, so every way out of its
retry loop has to be bounded.

The bug this locks out: when the stat panel read (4+ labels) but the
EXFILTRATED/ELIMINATED header did not, the "one more spin for the header"
branch did `continue` without counting the attempt. That was harmless while the
loop was `for attempt in range(...)`; commit 39ef6e3 turned it into a manual
while loop and it spun forever, so detection, clip saving and STOP all hung
for the rest of the session. tests/test_exfil_edge.py stubs the whole function
out, so nothing exercised the real loop until this file.
"""
import os
import sys
import time as _real_time
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exfil_stats  # noqa: E402

SIX_STATS = ["Combatant Eliminations 14", "Runner Eliminations 2",
             "Runners Downed 3", "Runner Damage 788", "Crew Revives 1",
             "Inventory Value 26,424"]
H, W = 1080, 1920


def _install(monkeypatch, seconds_per_grab=0.0):
    """Fake clock + no-op sleep (on exfil_stats only), and a _grab_full that
    counts its calls. Each frame's pixels hold its 1-based grab number, so a
    stub engine can tell which spin a crop came from."""
    clock = [0.0]
    grabs = []
    monkeypatch.setattr(exfil_stats, "time", types.SimpleNamespace(
        sleep=lambda s: None,
        monotonic=lambda: clock[0],
        strftime=_real_time.strftime))

    def grab(cfg):
        # fail instead of hanging the suite if the loop regresses
        assert len(grabs) < 40, "capture loop still spinning after 40 grabs"
        grabs.append(1)
        clock[0] += seconds_per_grab
        return np.full((H, W, 3), len(grabs), np.uint8)

    monkeypatch.setattr(exfil_stats, "_grab_full", grab)
    return grabs


_HEADER_SHAPE = exfil_stats._crop(np.zeros((H, W, 3), np.uint8),
                                  exfil_stats.HEADER_FRAC).shape[:2]


class _Engine:
    """read_lines stand-in. `header_from` = first spin whose header band reads
    ELIMINATED (None = never). Every other crop reads the six stat lines, or
    nothing at all when `empty`."""

    def __init__(self, header_from=None, empty=False):
        self.header_from = header_from
        self.empty = empty

    def read_lines(self, img, max_dim=None):
        if self.empty:
            return []
        spin = int(img[0, 0, 0])
        if img.shape[:2] == _HEADER_SHAPE:
            if self.header_from is not None and spin >= self.header_from:
                return ["ELIMINATED"]
            return []
        return list(SIX_STATS)


def test_six_stat_lines_parse_to_six_stats():
    """Sanity check on the fixture the loop tests rely on."""
    assert len(exfil_stats.parse_exfil_lines(SIX_STATS)) == 6
    assert exfil_stats.outcome(SIX_STATS) == ""   # rows are not the header


def test_panel_reads_but_header_never_does_stops_within_retries(monkeypatch):
    """The hang itself: this used to be still looping after 40 grabs."""
    grabs = _install(monkeypatch)
    stats, squad, outc = exfil_stats.capture_exfil_stats(
        {"squad_stats": False}, _Engine(header_from=None))
    assert len(stats) == 6
    assert squad == []
    assert outc == ""
    assert len(grabs) == 3          # == retries: two header spins, then done


def test_header_miss_still_parses_the_squad(monkeypatch):
    """The last spin falls through to parse_squad, so trios gamertags survive
    a header that never reads (main.py then tries read_outcome once more)."""
    grabs = _install(monkeypatch)
    stats, squad, outc = exfil_stats.capture_exfil_stats({}, _Engine())
    assert len(stats) == 6 and outc == ""
    assert [p["position"] for p in squad] == ["left", "center", "right"]
    assert [p["is_you"] for p in squad] == [False, True, False]
    assert len(grabs) == 3


def test_empty_panel_waits_for_empty_retries_then_stops(monkeypatch):
    grabs = _install(monkeypatch)
    stats, squad, outc = exfil_stats.capture_exfil_stats(
        {"squad_stats": False}, _Engine(empty=True))
    assert (stats, squad, outc) == ({}, [], "")
    assert len(grabs) == 7          # empty_retries


def test_header_on_the_second_spin_reads_died(monkeypatch):
    """The normal path is unchanged: the header-wait spin still gets its
    chance, and the loop stops on the spin the header reads."""
    grabs = _install(monkeypatch)
    stats, squad, outc = exfil_stats.capture_exfil_stats(
        {"squad_stats": False}, _Engine(header_from=2))
    assert outc == "died"
    assert len(stats) == 6
    assert len(grabs) == 2


def test_header_on_the_first_spin_stops_at_once(monkeypatch):
    grabs = _install(monkeypatch)
    _stats, _squad, outc = exfil_stats.capture_exfil_stats(
        {"squad_stats": False}, _Engine(header_from=1))
    assert outc == "died"
    assert len(grabs) == 1


def test_deadline_backstops_a_slow_loop_and_keeps_the_squad(monkeypatch, capsys):
    """Even with a generous retries count (or CPU OCR taking seconds a spin),
    the capture gives up after EXFIL_DEADLINE_S, and still parses the squad
    from the best frame it kept."""
    grabs = _install(monkeypatch, seconds_per_grab=5.0)
    stats, squad, outc = exfil_stats.capture_exfil_stats(
        {}, _Engine(), retries=50)
    assert len(grabs) == 3          # t=0, 5, 10 grab; t=15 > 12 gives up
    assert len(stats) == 6 and outc == ""
    assert len(squad) == 3
    assert "gave up waiting for the panel/header" in capsys.readouterr().out
