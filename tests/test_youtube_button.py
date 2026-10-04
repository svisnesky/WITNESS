"""The Archive's 'Upload to YouTube' button (Stan, 2026-10-03: "could we add an
upload to youtube button?"). YouTube itself is stubbed: nothing leaves the
machine running the tests."""

import json
import os
import sys
import threading
import time
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import webserver  # noqa: E402
import youtube_upload  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8953
SID = "2026-10-02_20-11-04"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("yt")
    sess = root / "Marathon Sessions" / SID
    (sess / "reels").mkdir(parents=True)
    (sess / "session_reel.mp4").write_bytes(b"x")
    (sess / "reels" / "match_1.mp4").write_bytes(b"x")
    (sess / "witness_report_tts.mp3").write_bytes(b"x")
    st = webserver.LiveState()
    st._cfg = {"youtube_privacy": "unlisted",
               "youtube_title_template": "WITNESS Auto Reel — {date}"}
    st.record_dir = ""                       # no START yet this run
    main.cached_record_dir = lambda: str(root)
    threading.Thread(target=webserver.start_web,
                     args=(st, PORT, BASE, "127.0.0.1"), daemon=True).start()
    time.sleep(0.6)
    return st, sess


def _post(f):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/youtube",
                                 data=json.dumps({"s": SID, "f": f}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=5))


def _status():
    return json.load(urllib.request.urlopen(
        f"http://127.0.0.1:{PORT}/api/uploads?s={SID}", timeout=5))


def _wait(f, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        st = _status().get(f, {})
        if st.get("state") in ("done", "failed"):
            return st
        time.sleep(0.05)
    raise AssertionError("upload never finished")


def test_archive_lists_sessions_before_the_first_start(server):
    """It read state.record_dir, which is empty until a session starts, so the
    Archive said 'No sessions found yet' right after launch."""
    html = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/archive", timeout=5).read().decode()
    assert SID.replace("_", " · ") in html


def test_videos_get_a_button_and_the_spoken_report_does_not(server):
    html = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/archive", timeout=5).read().decode()
    assert 'data-f="session_reel.mp4"' in html and 'data-f="reels/match_1.mp4"' in html
    assert 'data-f="witness_report_tts.mp3"' not in html


def test_an_upload_succeeds_is_remembered_and_never_repeats(server, monkeypatch):
    st, sess = server
    calls = []

    def fake(path, title, desc, base, privacy="unlisted", why=None):
        calls.append((os.path.basename(path), title, privacy))
        return "https://youtu.be/abc123"
    monkeypatch.setattr(youtube_upload, "upload", fake)

    assert _post("session_reel.mp4") == {"ok": True, "state": "uploading"}
    st_ = _wait("session_reel.mp4")
    assert st_ == {"state": "done", "url": "https://youtu.be/abc123", "error": ""}
    assert calls == [("session_reel.mp4", "WITNESS Auto Reel — Oct 02, 2026", "unlisted")], \
        "titled with the SESSION's date, not today's"
    rec = json.loads((sess / webserver.YT_RECORD).read_text())
    assert rec == {"session_reel.mp4": "https://youtu.be/abc123"}

    again = _post("session_reel.mp4")
    assert again["state"] == "done" and len(calls) == 1, "must not upload twice"
    html = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/archive", timeout=5).read().decode()
    assert "https://youtu.be/abc123" in html and "on YouTube" in html


def test_a_failure_says_why(server, monkeypatch):
    def fake(path, title, desc, base, privacy="unlisted", why=None):
        why.append("client_secret.json not found in the app folder — YouTube "
                   "isn't set up yet. See YOUTUBE.md.")
        return None
    monkeypatch.setattr(youtube_upload, "upload", fake)
    _post("reels/match_1.mp4")
    st_ = _wait("reels/match_1.mp4")
    assert st_["state"] == "failed" and "client_secret.json" in st_["error"]


def test_only_this_sessions_videos_can_be_uploaded(server, monkeypatch):
    monkeypatch.setattr(youtube_upload, "upload",
                        lambda *a, **k: pytest.fail("must not upload"))
    for bad in ("../../etc/passwd", "witness_report_tts.mp3", "nope.mp4", ""):
        r = _post(bad)
        assert r["ok"] is False, bad


def test_uploader_reports_its_reason(tmp_path):
    """The reason reaches the caller, not just the console."""
    why = []
    assert youtube_upload.upload(str(tmp_path / "missing.mp4"), "t", "d",
                                 str(tmp_path), why=why) is None
    assert why and "video not found" in why[0]
