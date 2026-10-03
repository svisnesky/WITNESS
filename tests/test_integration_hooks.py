"""Cross-package hooks, checked with both sides present.

Each release-prep package was built alone and reached the others through
getattr / try-import guards, so every package passed its own suite whether or
not the other side existed. These tests pin that the guarded call actually
reaches the real thing now that both sides are merged.
"""

import os
import sys
import threading
import types

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


# --- Bonjour TXT 'v': web-dashboard publishes state.build_sha for the callers --

def test_main_advertises_the_bare_build_sha():
    """discovery.advertise keeps 12 characters of 'v'; the full label
    '0.1.0 (1a2b3c4)' is 15, so the iPad picker read '0.1.0 (1a2b3'."""
    src = _src("main.py")
    i = src.index("discovery.advertise(")
    call = src[i:i + 600]
    assert 'getattr(_web_state, "build_sha", "")' in call


def test_gui_advertises_the_bare_build_sha(monkeypatch):
    pytest.importorskip("tkinter")
    import gui
    got, done = {}, threading.Event()
    disc = types.ModuleType("discovery")

    def advertise(port, ip, version="", api=1):
        got["v"] = version
        done.set()
        return "ADV"
    disc.advertise = advertise
    monkeypatch.setitem(sys.modules, "discovery", disc)
    monkeypatch.setattr(gui, "app", types.SimpleNamespace(_advertiser=None))
    ws = types.SimpleNamespace(local_ip=lambda: "192.168.1.20", API_VERSION=1)
    st = types.SimpleNamespace(version="0.1.0 (1a2b3c4)", build_sha="1a2b3c4")
    gui._advertise_for_ipad({}, "0.0.0.0", 8000, st, ws)
    assert done.wait(5)
    assert got["v"] == "1a2b3c4"
