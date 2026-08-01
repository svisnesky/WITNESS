"""Upload titles. Stan asked for "WITNESS Auto Reel and the date"."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


def test_default_is_what_stan_asked_for():
    t = main._yt_title({}, "Session Montage", downs=12, elims=6)
    assert t == f"WITNESS Auto Reel — {time.strftime('%b %d, %Y')}", t


def test_config_can_add_the_stats_back():
    t = main._yt_title({"youtube_title_template":
                        "WITNESS {kind} — {downs}d/{elims}e — {date}"},
                       "Session Reel", downs=12, elims=6)
    assert "Session Reel" in t and "12d/6e" in t


def test_unknown_placeholder_falls_back_instead_of_raising():
    """A typo in config must never cost the upload."""
    t = main._yt_title({"youtube_title_template": "{nonsense} {date}"}, "x")
    assert t.startswith("WITNESS Auto Reel"), t


def test_malformed_template_falls_back():
    for bad in ("{date", "{}", "{0}"):
        t = main._yt_title({"youtube_title_template": bad}, "x")
        assert t, bad


def test_title_is_capped_at_youtubes_limit():
    t = main._yt_title({"youtube_title_template": "W" * 300}, "x")
    assert len(t) <= 100


def test_empty_template_uses_the_default():
    assert main._yt_title({"youtube_title_template": ""}, "x").startswith("WITNESS")
    assert main._yt_title({"youtube_title_template": None}, "x").startswith("WITNESS")


def test_config_default_matches_the_code_default():
    import yaml
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert cfg["youtube_title_template"] == "WITNESS Auto Reel — {date}"
    assert main._yt_title(cfg, "Session Montage") == main._yt_title({}, "Session Montage")
