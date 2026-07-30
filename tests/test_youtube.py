"""YouTube upload plumbing — the parts testable without credentials.

Nothing here touches a real account or a real token. The point is that when Stan
drops client_secret.json in, the failure modes are already legible instead of
being a raw googleapiclient stack trace.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import youtube_upload as yt  # noqa: E402


def test_mimetype_matches_the_container():
    """The montage renders as .mkv (a stream copy of OBS's Replay Buffer output,
    never re-encoded) but the uploader hardcoded video/mp4."""
    assert yt._mimetype("highlights_2026-07-29.mkv") == "video/x-matroska"
    assert yt._mimetype("session_reel.mp4") == "video/mp4"
    assert yt._mimetype("clip.MKV") == "video/x-matroska"          # case-insensitive
    assert yt._mimetype("weird.xyz") == "video/mp4"                 # sane default


def test_api_not_enabled_is_explained():
    err = ("<HttpError 403 ... YouTube Data API v3 has not been used in project "
           "12345 before or it is disabled. accessNotConfigured>")
    msg = yt._explain(err)
    assert "isn't enabled" in msg
    assert "Library" in msg


def test_expired_token_points_at_the_7_day_trap():
    """The failure that would otherwise look like "the app broke a week later"."""
    msg = yt._explain("('invalid_grant: Token has been expired or revoked.')")
    assert "7 days" in msg
    assert "consent screen" in msg


def test_quota_is_explained_not_just_echoed():
    msg = yt._explain("<HttpError 403 ... quotaExceeded>")
    assert "quota" in msg.lower()
    assert "resets" in msg


def test_no_channel_is_explained():
    msg = yt._explain("<HttpError 401 ... youtubeSignupRequired>")
    assert "no YouTube channel" in msg


def test_unknown_errors_pass_through_unchanged():
    """Never swallow a message we don't recognise."""
    assert yt._explain("something totally new") == "something totally new"


def test_missing_client_secret_returns_none_without_raising(tmp_path):
    """No credentials must degrade quietly, never break the end of a session."""
    assert yt._get_credentials(str(tmp_path)) is None


def test_upload_of_a_missing_file_returns_none(tmp_path):
    assert yt.upload(str(tmp_path / "nope.mp4"), "t", "d", str(tmp_path)) is None


def test_montage_upload_is_a_real_config_flag():
    """Stan's actual request was "mainly the end montage" — which had no flag."""
    import yaml
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert "youtube_upload_montage" in cfg
    assert cfg["youtube_upload_montage"] is False       # opt-in, off by default
    # And every upload flag defaults off, so nothing publishes without asking.
    for k, v in cfg.items():
        if k.startswith("youtube_upload"):
            assert v is False, k


def test_secrets_stay_gitignored():
    """These must never be committable — the whole repo is public."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, ".gitignore"), encoding="utf-8") as f:
        ignored = f.read()
    assert "client_secret.json" in ignored
    assert "youtube_token.json" in ignored
