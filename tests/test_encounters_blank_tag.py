"""A blank gamertag means "no tag", never the author's.

New installs ship `gamertag: ""`. The code used to read
cfg.get("gamertag") or DEFAULT_GAMERTAG, so a blank silently became MRVIZNASTY
and a stranger's kill feed was matched against someone else's name. The rule
now is key-ABSENT -> DEFAULT_GAMERTAG (configs written before the key existed,
which updates never rewrite), key PRESENT but blank -> no tag: name tracking
stays off, skips its full-frame grab and OCR, and says so once on the console.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import encounters  # noqa: E402
import exfil_stats  # noqa: E402

FEED = ["MRVIZNASTY SOMEDUDE", "XX SANIK XX OTHERGUY"]


class _Engine:
    """Stands in for OCREngine: returns canned feed rows, counts reads."""

    def __init__(self, rows):
        self.rows = rows
        self.reads = 0

    def read_rows(self, crop, max_dim=0):
        self.reads += 1
        return list(self.rows)


@pytest.fixture
def no_grab(monkeypatch):
    """Fails the test if capture() touches the screen (or, on a Mac without
    capture_source set, the webcam)."""
    calls = []

    def boom(cfg):
        calls.append(cfg)
        raise AssertionError("capture() grabbed a frame with a blank gamertag")

    monkeypatch.setattr(exfil_stats, "_grab_full", boom)
    monkeypatch.setattr(encounters, "_hinted_no_tag", False)
    return calls


@pytest.fixture
def fake_grab(monkeypatch):
    np = pytest.importorskip("numpy")
    calls = []

    def grab(cfg):
        calls.append(cfg)
        return np.zeros((100, 200, 3), dtype=np.uint8)

    monkeypatch.setattr(exfil_stats, "_grab_full", grab)
    return calls


# --- resolve_gamertag: the key-absent rule ------------------------------------

def test_missing_key_keeps_the_default_for_old_configs():
    assert encounters.DEFAULT_GAMERTAG == "MRVIZNASTY"
    assert encounters.resolve_gamertag({}) == "MRVIZNASTY"
    assert encounters.resolve_gamertag(None) == "MRVIZNASTY"


@pytest.mark.parametrize("value", ["", "   ", None])
def test_present_but_blank_means_no_tag(value):
    assert encounters.resolve_gamertag({"gamertag": value}) == ""


def test_a_set_tag_is_used_and_trimmed():
    assert encounters.resolve_gamertag({"gamertag": "  SOMEONE  "}) == "SOMEONE"


# --- capture(): skips the grab and OCR, prints one line -----------------------

@pytest.mark.parametrize("value", ["", "   ", None, "#"])
def test_blank_tag_capture_returns_nothing_without_grabbing(no_grab, value):
    eng = _Engine(FEED)
    assert encounters.capture({"gamertag": value}, eng) == []
    assert no_grab == []
    assert eng.reads == 0


def test_blank_tag_says_so_once(no_grab, capsys):
    eng = _Engine(FEED)
    encounters.capture({"gamertag": ""}, eng)
    encounters.capture({"gamertag": ""}, eng)
    out = capsys.readouterr().out
    assert out.count("gamertag is blank in config.yaml") == 1


def test_missing_key_still_matches_the_default_tag(fake_grab):
    """The author's own config has no gamertag line; his Menace Report must keep
    working exactly as before."""
    eng = _Engine(FEED)
    assert encounters.capture({}, eng) == [("victim", "SOMEDUDE")]
    assert len(fake_grab) == 1 and eng.reads == 1


def test_configured_tag_is_the_one_matched(fake_grab):
    eng = _Engine(["XX SANIK XX NEWRUNNER", "MRVIZNASTY SOMEDUDE"])
    out = encounters.capture({"gamertag": "NEWRUNNER"}, eng)
    assert out == [("killed_by", "XX SANIK XX")]


# --- extract(): a blank tag never matches -------------------------------------

@pytest.mark.parametrize("tag", ["", "   ", None, "--"])
def test_extract_with_blank_tag_is_empty(tag):
    assert encounters.extract(FEED + ["SOMEDUDE"], tag) == []
