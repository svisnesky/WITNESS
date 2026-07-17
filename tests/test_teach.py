"""Teach-a-game wizard logic + game-profile loading."""

import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from teach import (PROFILE_DEFAULTS, has_reward, profile_yaml,  # noqa: E402
                   rank_candidates, region_around, slugify, stable_phrase)


def test_stable_phrase_strips_rewards_and_counters():
    assert stable_phrase("RUNNER DOWN +15 XP") == "RUNNER DOWN"
    assert stable_phrase("ENEMY DOWNED") == "ENEMY DOWNED"
    assert stable_phrase("KILL CONFIRMED +100") == "KILL CONFIRMED"
    assert stable_phrase("ELIMINATED 3/5") == "ELIMINATED"
    assert stable_phrase("+25") == ""


def test_has_reward():
    assert has_reward("RUNNER DOWN +15 XP")
    assert has_reward("KILL +100")
    assert not has_reward("ENEMY DOWNED")


def test_region_around_pads_and_clamps():
    r = region_around([(0.40, 0.72, 0.55, 0.76)])
    assert r["x"] == 0.35 and r["y"] == 0.69
    assert abs(r["w"] - 0.25) < 1e-9 and abs(r["h"] - 0.10) < 1e-9
    r2 = region_around([(0.0, 0.0, 0.1, 0.05)])   # clamped at the edges
    assert r2["x"] == 0.0 and r2["y"] == 0.0


def test_rank_candidates_prefers_transient_central_text():
    seen = {
        "hud ammo": {"raw": "AMMO 240", "count": 90, "bbox": (0.8, 0.9, 0.9, 0.95),
                     "first": 0, "last": 89},          # persistent HUD -> dropped
        "kill line": {"raw": "ENEMY DOWNED +50", "count": 4,
                      "bbox": (0.45, 0.55, 0.6, 0.6), "first": 30, "last": 33},
        "corner toast": {"raw": "CONNECTING", "count": 4,
                         "bbox": (0.02, 0.02, 0.2, 0.06), "first": 0, "last": 3},
        "scrap": {"raw": "x1", "count": 2, "bbox": (0.5, 0.5, 0.52, 0.52),
                  "first": 1, "last": 2},              # too short -> dropped
    }
    ranked = rank_candidates(seen, total_frames=90)
    raws = [e["raw"] for e in ranked]
    assert "AMMO 240" not in raws and "x1" not in raws
    assert raws[0] == "ENEMY DOWNED +50"               # central beats corner


def test_profile_yaml_is_valid_and_gated():
    text = profile_yaml("Arc Raiders", ["ENEMY DOWNED"],
                        {"x": 0.35, "y": 0.69, "w": 0.25, "h": 0.1}, True)
    prof = yaml.safe_load(text)
    assert prof["game_name"] == "Arc Raiders"
    assert prof["popup_trigger_phrases"] == ["ENEMY DOWNED"]
    assert prof["require_reward"] is True
    assert prof["detect_region_frac"]["w"] == 0.25
    for k, v in PROFILE_DEFAULTS.items():
        assert prof[k] == v                            # marathon systems off


def test_slugify():
    assert slugify("Arc Raiders") == "arc_raiders"
    assert slugify("Delta Force: Hawk Ops!") == "delta_force_hawk_ops"


def test_load_config_applies_profile_then_override(tmp_path):
    import main
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "game: arc_raiders\npopup_trigger_phrases: [RUNNER DOWN]\n"
        "make_match_reels: true\n")
    games = tmp_path / "games"
    games.mkdir()
    (games / "arc_raiders.yaml").write_text(
        "game_name: Arc Raiders\npopup_trigger_phrases: [ENEMY DOWNED]\n"
        "capture_exfil_stats: false\n")
    (tmp_path / "settings_override.yaml").write_text("make_match_reels: false\n")

    cfg = main.load_config(str(cfg_file))
    assert cfg["popup_trigger_phrases"] == ["ENEMY DOWNED"]   # profile wins
    assert cfg["capture_exfil_stats"] is False
    assert cfg["make_match_reels"] is False                   # override wins last


def test_load_config_override_can_switch_game(tmp_path):
    import main
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("game: marathon\npopup_trigger_phrases: [RUNNER DOWN]\n")
    games = tmp_path / "games"
    games.mkdir()
    (games / "apex.yaml").write_text("popup_trigger_phrases: [KILL CONFIRMED]\n")
    (tmp_path / "settings_override.yaml").write_text("game: apex\n")

    cfg = main.load_config(str(cfg_file))
    assert cfg["popup_trigger_phrases"] == ["KILL CONFIRMED"]


def test_load_config_marathon_untouched(tmp_path):
    import main
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("game: marathon\npopup_trigger_phrases: [RUNNER DOWN]\n")
    cfg = main.load_config(str(cfg_file))
    assert cfg["popup_trigger_phrases"] == ["RUNNER DOWN"]
