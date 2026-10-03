"""Gamertag: a missing key keeps the old default, a blank one means no tag.

Owner decision: new installs ship gamertag: "". encounters.DEFAULT_GAMERTAG
stays, used ONLY when the key is absent (configs from before the key existed,
Stan's included, have always run on it). A deliberate blank must not turn back
into someone else's name, and with no tag the per-kill full-frame name scan is
skipped with one line saying how to turn it on.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import encounters  # noqa: E402
import main  # noqa: E402


@pytest.fixture(params=["encounters", "local"])
def rule(request, monkeypatch):
    """Run each check with encounters.resolve_gamertag (config-and-identity
    package) and with main's own fallback for an encounters without it."""
    if request.param == "local":
        monkeypatch.delattr(encounters, "resolve_gamertag", raising=False)
    return request.param


def test_a_missing_key_keeps_the_old_default(rule):
    assert main._gamertag({}) == encounters.DEFAULT_GAMERTAG
    assert main._gamertag({"game": "marathon"}) == encounters.DEFAULT_GAMERTAG


def test_a_blank_key_means_no_tag(rule):
    assert main._gamertag({"gamertag": ""}) == ""
    assert main._gamertag({"gamertag": "   "}) == ""
    assert main._gamertag({"gamertag": None}) == ""        # a bare `gamertag:`


def test_a_set_key_is_used(rule):
    assert main._gamertag({"gamertag": " KRYPT KING05 "}) == "KRYPT KING05"


def test_announcer_name_blank_or_absent_is_none():
    assert main._announcer_name({}) == ""
    assert main._announcer_name({"announcer_player_name": None}) == ""
    assert main._announcer_name({"announcer_player_name": " Viz "}) == "Viz"


class _Web:
    def __init__(self):
        self.notices = []

    def notice(self, text, tag="alert"):
        assert len(text) <= 60
        self.notices.append(text)


def test_no_tag_skips_the_scan_and_says_so_once(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(encounters, "capture",
                        lambda cfg, engine: calls.append(1) or [])
    web = _Web()
    s = {"web": web, "session_id": "s1"}
    for expect in ("victim", "killed_by", "victim"):
        main._scan_feed_names({"gamertag": ""}, engine=None, s=s, expect=expect)
    assert calls == [], "no full-frame grab + OCR without a tag to match"
    assert web.notices == ["Set gamertag in config.yaml for name tracking"]
    out = capsys.readouterr().out
    assert out.count("gamertag is blank in config.yaml") == 1


def test_an_old_config_without_the_key_still_scans(monkeypatch):
    calls = []
    monkeypatch.setattr(encounters, "capture",
                        lambda cfg, engine: calls.append(1) or [])
    s = {"web": None, "session_id": "s1"}
    main._scan_feed_names({}, engine=None, s=s, expect="victim")
    main._scan_feed_names({"gamertag": "MRVIZNASTY"}, engine=None, s=s,
                          expect="victim")
    assert calls == [1, 1]
    assert not s.get("_warned_no_tag")
