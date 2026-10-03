"""Docs must not promise what a new install does not do.

config.yaml ships make_shorts: false and make_montage: false, but QUICKSTART,
the dashboard's How to use, the CHANGELOG and the README feature list all said
STOP leaves vertical Shorts and a montage waiting. A new user pressed STOP and
found neither."""
import os
import re

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


def _shipped():
    return yaml.safe_load(_read("config.yaml"))


def test_the_shipped_config_keeps_shorts_and_montage_opt_in():
    cfg = _shipped()
    assert cfg["make_shorts"] is False and cfg["make_montage"] is False


def test_quickstart_stop_step_says_shorts_and_montage_are_off():
    step = re.search(r"\*\*Press STOP\*\*(.+?)\n\n", _read("QUICKSTART.md"), re.S).group(1)
    step = " ".join(step.split())
    assert "off until you turn them on" in step
    assert "Shorts" in step and "montage" in step


def test_dashboard_help_says_shorts_and_montage_are_off():
    src = _read("webserver.py")
    para = re.search(r"Clips land in your OBS output folder(.+?)</p>", src, re.S).group(1)
    para = " ".join(para.split())
    assert "off until you turn them on" in para
    assert "montage are built" not in para


def test_changelog_and_readme_mark_them_opt_in():
    log = " ".join(_read("CHANGELOG.md").split())
    assert "Vertical Shorts and a session montage are opt-in" in log
    readme = " ".join(_read("README.md").split())
    assert "**Vertical Shorts** (opt-in)" in readme
    assert "montage (opt-in)" in readme
