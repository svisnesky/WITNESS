"""A game profile sets detection, theme and feature gates, nothing else.

load_config applied games/<game>.yaml with a bare cfg.update(profile). The
README invites people to share profiles as "pure data", yet one could set
ffmpeg_path (run on every render), obs.exe_path (launched), youtube_privacy
and upload toggles, and web_lan. A profile's obs: block also replaced the
user's whole OBS host/port/password, because the update is shallow.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

CONFIG = """\
game: evil
ffmpeg_path: "C:/ffmpeg/bin/ffmpeg.exe"
youtube_privacy: unlisted
youtube_upload_session_reel: false
web_lan: false
obs:
  host: localhost
  port: 4455
  password: "real-password"
popup_trigger_phrases: [RUNNER DOWN]
"""

PROFILE = """\
game_name: Evil Game
ffmpeg_path: "C:/Users/Public/not-ffmpeg.exe"
youtube_privacy: public
youtube_upload_session_reel: true
web_lan: true
obs:
  exe_path: "C:/Users/Public/not-obs.exe"
popup_trigger_phrases: [ENEMY DOWNED]
popup_confirm_frames: 2
detect_region_frac: {x: 0.4, y: 0.6, w: 0.2, h: 0.1}
theme: {accent: "#00ff00"}
track_names: false
"""


def _load(tmp_path, profile=PROFILE):
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "games").mkdir()
    (tmp_path / "games" / "evil.yaml").write_text(profile, encoding="utf-8")
    return main.load_config(str(tmp_path / "config.yaml"))


def test_a_profile_cannot_change_executables_publishing_or_the_lan(tmp_path, capsys):
    cfg = _load(tmp_path)
    assert cfg["ffmpeg_path"] == "C:/ffmpeg/bin/ffmpeg.exe"
    assert cfg["youtube_privacy"] == "unlisted"
    assert cfg["youtube_upload_session_reel"] is False
    assert cfg["web_lan"] is False
    assert cfg["obs"] == {"host": "localhost", "port": 4455,
                          "password": "real-password"}
    out = capsys.readouterr().out
    assert "ignored non-profile keys" in out
    for k in ("ffmpeg_path", "obs", "web_lan", "youtube_privacy",
              "youtube_upload_session_reel"):
        assert k in out


def test_detection_theme_and_gates_still_apply(tmp_path):
    cfg = _load(tmp_path)
    assert cfg["popup_trigger_phrases"] == ["ENEMY DOWNED"]
    assert cfg["popup_confirm_frames"] == 2
    assert cfg["detect_region_frac"]["x"] == 0.4
    assert cfg["theme"] == {"accent": "#00ff00"}
    assert cfg["track_names"] is False
    assert cfg["game_name"] == "Evil Game"


def test_a_clean_profile_prints_no_ignore_line(tmp_path, capsys):
    _load(tmp_path, profile="game_name: Clean\npopup_trigger_phrases: [X DOWN]\n")
    assert "ignored" not in capsys.readouterr().out


def test_profile_keys_cover_everything_teach_writes():
    import teach
    assert set(teach.PROFILE_DEFAULTS) <= main.PROFILE_KEYS
    text = teach.profile_yaml("Arc Raiders", ["ENEMY DOWNED"],
                              {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1}, True)
    import yaml
    assert set(yaml.safe_load(text)) <= main.PROFILE_KEYS


def test_the_settings_override_is_still_applied_last(tmp_path):
    (tmp_path / "settings_override.yaml").write_text("web_lan: true\n",
                                                     encoding="utf-8")
    cfg = _load(tmp_path)
    assert cfg["web_lan"] is True       # the dashboard's own override, not the profile
