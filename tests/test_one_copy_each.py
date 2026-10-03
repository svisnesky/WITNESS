"""Duplicates left by the package merge now have one copy each: the ffmpeg
check (test_find_ffmpeg), the session-name check, and the no-gamertag line."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import encounters  # noqa: E402
import main  # noqa: E402
import webserver  # noqa: E402


def test_the_no_gamertag_line_is_encounters_wording():
    assert main._no_tag_line() == encounters.NO_TAG_HINT
    assert not hasattr(main, "NO_TAG_LINE")


def test_web_session_lookups_pass_strict(monkeypatch, tmp_path):
    seen = []
    d = tmp_path / "Marathon Sessions" / "2026-10-01_20-00-00"
    d.mkdir(parents=True)

    def resolve(cfg, which, **k):
        seen.append((which, k.get("strict")))
        return str(d)
    monkeypatch.setattr(main, "_resolve_session_dir", resolve)
    state = type("S", (), {"_cfg": {}})()
    webserver._web_session_dir(state, "latest")
    webserver._web_session_dir(state, "2026-10-01_20-00-00")
    assert seen == [("latest", True), ("2026-10-01_20-00-00", True)]


def test_strict_refuses_a_full_path_even_if_the_name_check_were_skipped():
    assert main._resolve_session_dir({}, os.path.abspath(os.sep), strict=True) == ""
