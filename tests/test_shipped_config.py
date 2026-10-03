"""The config.yaml a new install gets carries nobody's identity.

config.yaml is copied once and never overwritten by updates, so what ships here
is exactly what a stranger starts with: no gamertag, no announcer name, no OBS
password placeholder, a clean clutch call-out, and an explicit tts_online
switch they can find. The CODE defaults for missing keys are a different thing
(they keep older installs behaving as before) and are pinned by
test_encounters_blank_tag.py and test_tts_online_switch.py.
"""

import os
import re

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg():
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _text():
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        return f.read()


def test_identity_ships_blank():
    cfg = _cfg()
    assert cfg["gamertag"] == ""
    assert cfg["announcer_player_name"] == ""
    assert cfg["obs"]["password"] == ""


def test_no_personal_leftovers_in_the_shipped_file():
    text = _text().lower()
    for leftover in ("mrviznasty", "viz nasty", "change_me", "photoshopped",
                     "joe's review", "self-test marker", "holy shit"):
        assert leftover not in text, leftover


def test_clutch_callout_is_clean():
    assert _cfg()["clutch_callout"] == "CLUTCH!"


def test_tts_online_is_present_on_and_disclosed():
    cfg = _cfg()
    assert cfg["tts_online"] is True
    text = _text()
    i = text.index("tts_online:")
    comment = text[max(0, i - 600):i]
    assert "Microsoft" in comment and "offline" in comment


def test_debug_ocr_ships_off():
    assert _cfg()["debug_ocr"] is False


def test_youtube_section_appears_once():
    # the orphaned duplicate header used to sit above tidy_session_folder
    assert len(re.findall(r"^# --- YouTube auto-upload", _text(), re.M)) == 1


def test_marathon_profile_still_parses():
    with open(os.path.join(BASE, "games", "marathon.yaml"), encoding="utf-8") as f:
        prof = yaml.safe_load(f)
    assert prof["game_name"] == "Marathon"
