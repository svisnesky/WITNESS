"""Bonjour advertising for the iOS app (discovery.py)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_discovery_degrades_without_a_lan_address():
    import discovery
    assert discovery.advertise(8000, "127.0.0.1") is None
    assert discovery.advertise(8000, "") is None


def test_discovery_instance_names_are_valid():
    import discovery
    assert "." not in discovery._instance_name("my.pc.local")
    assert discovery._instance_name("").startswith("WITNESS on")
