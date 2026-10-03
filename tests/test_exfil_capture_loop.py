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
import pytest

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


# --- What the deadline does on a fast GPU ------------------------------------
# One one-off OCR pass at 4K costs ~0.4-0.55 s on an RTX 5080 (2026-07-27 perf
# log: one exfil capture blocked ~4.2 s of the 5 fps loop for 6 passes and two
# 0.6 s waits). So the 12 s deadline is not only a CPU-OCR backstop.

class _TimedEngine:
    """Every read_lines call costs `per_pass` seconds on the fake clock. The
    stats panel is always empty; the header band reads ELIMINATED or nothing."""

    def __init__(self, clock, per_pass, header_reads):
        self.clock, self.per_pass, self.header_reads = clock, per_pass, header_reads
        self.passes = 0

    def read_lines(self, img, max_dim=None):
        self.passes += 1
        self.clock[0] += self.per_pass
        if self.header_reads and img.shape[:2] == _HEADER_SHAPE:
            return ["ELIMINATED"]
        return []


def _install_timed(monkeypatch):
    """Like _install, but sleep() advances the fake clock too."""
    clock = [0.0]
    grabs = []

    def sleep(s):
        clock[0] += s

    monkeypatch.setattr(exfil_stats, "time", types.SimpleNamespace(
        sleep=sleep, monotonic=lambda: clock[0], strftime=_real_time.strftime))

    def grab(cfg):
        assert len(grabs) < 40, "capture loop still spinning after 40 grabs"
        grabs.append(clock[0])
        return np.zeros((H, W, 3), np.uint8)

    monkeypatch.setattr(exfil_stats, "_grab_full", grab)
    return grabs, clock



@pytest.mark.parametrize("per_pass", [0.40, 0.55])
def test_empty_panel_with_the_header_read_keeps_all_seven_spins(
        monkeypatch, capsys, per_pass):
    """The 07-27 empty-panel case: the header reads on spin 1, so later spins
    are one pass + 0.6 s and the whole empty_retries wait fits the deadline."""
    grabs, clock = _install_timed(monkeypatch)
    eng = _TimedEngine(clock, per_pass, header_reads=True)
    stats, _squad, outc = exfil_stats.capture_exfil_stats(
        {"squad_stats": False}, eng)
    assert (stats, outc) == ({}, "died")
    assert len(grabs) == 7
    assert "gave up waiting" not in capsys.readouterr().out
    # settle + 8 passes (2 on spin 1, then 1 a spin) + 6 waits of 0.6 s
    assert clock[0] == pytest.approx(0.8 + 8 * per_pass + 6 * 0.6)
    assert clock[0] - 0.8 < exfil_stats.EXFIL_DEADLINE_S


@pytest.mark.parametrize("per_pass, spins", [(0.40, 5), (0.55, 4)])
def test_empty_panel_with_a_header_miss_is_cut_short_by_the_deadline(
        monkeypatch, capsys, per_pass, spins):
    """Header AND panel missing: each spin is 5 passes (4 header bands + the
    panel) + 0.6 s, so on a GPU the deadline ends the wait after 4-5 of the 7
    spins. Deliberate: the panel animates in on the wall clock, and this case
    has already waited longer than the full 7-spin wait above."""
    grabs, clock = _install_timed(monkeypatch)
    eng = _TimedEngine(clock, per_pass, header_reads=False)
    stats, _squad, outc = exfil_stats.capture_exfil_stats(
        {"squad_stats": False}, eng)
    assert (stats, outc) == ({}, "")
    assert len(grabs) == spins
    assert eng.passes == 5 * spins
    assert "gave up waiting for the panel/header" in capsys.readouterr().out
    # more wall time for the panel than the header-read case's whole wait
    full_wait = 0.8 + 8 * per_pass + 6 * 0.6
    assert clock[0] > full_wait
