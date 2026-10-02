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


def test_bonjour_type_matches_the_ios_app():
    """discovery.py and the app's Info.plist must name the same service, or the
    app browses forever and finds nothing. The Swift browser names it too."""
    import discovery
    bare = discovery.SERVICE_TYPE.replace(".local.", "")          # _witness._tcp
    plist = open(os.path.join(BASE, "ios", "WITNESS", "Info.plist"), encoding="utf-8").read()
    swift = open(os.path.join(BASE, "ios", "WITNESS", "WITNESS", "Discovery.swift"),
                 encoding="utf-8").read()
    assert f"<string>{bare}</string>" in plist
    assert f'type: "{bare}"' in swift


def test_the_app_asks_for_every_api_route_the_server_serves():
    """The app and the PC update independently; a route renamed on one side
    only fails silently on the iPad. Every /api path the Swift code requests
    must be one webserver.py routes."""
    import re
    import webserver
    src = open(webserver.__file__, encoding="utf-8").read()
    served = set(re.findall(r'path == "(/api/[a-z]+)"', src))
    app_dir = os.path.join(BASE, "ios", "WITNESS", "WITNESS")
    asked = set()
    for name in os.listdir(app_dir):
        if name.endswith(".swift"):
            asked |= set(re.findall(r'"(/api/[a-z]+)', open(os.path.join(app_dir, name),
                                                             encoding="utf-8").read()))
    assert asked, "found no /api calls in the app"
    assert asked <= served, f"app calls routes the PC doesn't serve: {asked - served}"
