"""The web dashboard's DOWNS tile counts what the desktop tiles and the recap
count: exfil_stats.count_events' downs, +1 KILL presses included.

It rendered the raw 'down' tag count, so a +1 press ('manual_kill') moved the
headline but never the DOWNS tile on the very page that has the +1 button."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exfil_stats  # noqa: E402
import webserver  # noqa: E402


def test_snapshot_downs_counts_manual_kills():
    st = webserver.LiveState()
    st.record(1, "down", "RUNNER DOWNED")
    st.record(2, "manual_kill", "+1 KILL")
    st.record(2, "precision", "PRECISION DOWNED")
    st.record(3, "kill", "RECONCILED - exfil screen kill floor")
    snap = st.snapshot()
    assert snap["downs"] == 2
    assert snap["tags"]["down"] == 1          # raw counts unchanged for the API
    tags = ["down", "manual_kill", "precision", "kill"]
    assert snap["downs"] == exfil_stats.count_events(tags)[0]


def test_an_empty_session_has_zero_downs():
    assert webserver.LiveState().snapshot()["downs"] == 0


def test_the_page_renders_the_server_downs():
    assert "if (typeof d.downs === 'number') tags.down = d.downs;" in webserver.PAGE
