"""The JSON API the iOS companion app is built on, plus the server fixes found
while building it."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import webserver  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8947
SID = "2026-08-08_21-35-33"
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("iosapi")
    sess = root / "Marathon Sessions" / SID
    sess.mkdir(parents=True)
    if HAVE_FFMPEG:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=320x180:rate=15:duration=3", "-f", "lavfi",
                        "-i", "sine=duration=3", "-c:v", "libx264", "-preset",
                        "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(sess / "001_down_20-07-34.mkv")], check=True)
    else:
        (sess / "001_down_20-07-34.mkv").write_bytes(b"x")
    # a TIDIED clip: lives in clips/, which the Archive used to count as 0
    (sess / "clips").mkdir()
    (sess / "clips" / "002_down_20-30-32.mkv").write_bytes(b"x")
    (sess / "reels").mkdir()
    (sess / "reels" / "match_1.mp4").write_bytes(b"x")
    (sess / "reels" / "play_of_the_night.mp4").write_bytes(b"x")
    st = webserver.LiveState()
    st._cfg = {}
    st.record_dir = str(root)
    main.cached_record_dir = lambda: str(root)
    threading.Thread(target=webserver.start_web,
                     args=(st, PORT, BASE, "127.0.0.1"), daemon=True).start()
    time.sleep(0.7)
    return st, str(sess), str(root)


def _get(path):
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=8))


def test_info_identifies_the_server(server):
    """The app refuses any address that doesn't prove it's WITNESS."""
    d = _get("/api/info")
    assert d["app"] == "witness" and d["api"] == webserver.API_VERSION


def test_sessions_count_tidied_clips(server):
    """Bug found building the API: the Archive counted clips at the top level
    only, so every tidied session (clips moved into clips/) showed 0 clips."""
    d = _get("/api/sessions")
    s = next(x for x in d["sessions"] if x["id"] == SID)
    assert s["clips"] == 2, s
    kinds = {m["kind"] for m in s["media"]}
    assert {"reel", "potn"} <= kinds


def test_media_urls_point_at_the_playable_route(server):
    """iOS can't play .mkv, so every URL the app gets goes through /api/play."""
    d = _get(f"/api/session?s={SID}")
    assert d["clips"], d
    for c in d["clips"]:
        assert c["url"].startswith("/api/play?"), c["url"]
        assert c["thumb"].startswith("/clipthumb?")
    for m in d["media"]:
        assert m["url"].startswith("/api/play?")


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_play_turns_mkv_into_a_ranged_mp4(server):
    _st, sess, _root = server
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/api/play?s={SID}&f=001_down_20-07-34.mkv",
        headers={"Range": "bytes=0-1"})
    r = urllib.request.urlopen(req, timeout=30)
    assert r.status == 206
    assert r.headers["Content-Type"] == "video/mp4"
    assert os.path.exists(os.path.join(sess, "mp4", "001_down_20-07-34.mp4"))


def test_play_refuses_paths_outside_the_session(server):
    for bad in ("../secret.mkv", "..%2F..%2Fsecret.mkv", "/etc/hosts"):
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/api/play?s={SID}&f={bad}", timeout=5)


def test_alerts_are_first_class_state(server):
    """The app raises a real alert from structured state, not by parsing the
    recap banner's sentence."""
    st = server[0]
    st.set_alert("black_capture", "critical", "OBS is recording a black screen")
    a = _get("/api/status")["alerts"]
    assert [x["key"] for x in a] == ["black_capture"] and a[0]["level"] == "critical"
    first_since = a[0]["since"]
    st.set_alert("black_capture", "critical", "OBS is recording a black screen")
    assert _get("/api/status")["alerts"][0]["since"] == first_since, \
        "a refresh must not look like a NEW alert, or the app re-notifies every check"
    st.clear_alert("black_capture")
    assert _get("/api/status")["alerts"] == []


def test_playback_cache_is_capped_least_recently_played_first(tmp_path):
    """Every .mkv the iPad plays gets an .mp4 copy. Uncapped, watching a night's
    clips (and the multi-GB montage) silently doubled the session on disk."""
    import playable
    cache = tmp_path / playable.CACHE_DIR
    cache.mkdir()
    now = time.time()
    for i, name in enumerate(["old.mp4", "mid.mp4", "new.mp4"]):
        fp = cache / name
        fp.write_bytes(b"x" * 400)
        os.utime(fp, (now - 300 + i * 100, now - 300 + i * 100))
    (cache / "busy.part.mp4").write_bytes(b"x" * 400)     # mid-remux: never touched
    freed = playable.prune(str(tmp_path), keep=str(cache / "old.mp4"), budget=900)
    left = sorted(p.name for p in cache.iterdir())
    # over budget by 300: the oldest is being served (keep), so the next-oldest goes
    assert left == ["busy.part.mp4", "new.mp4", "old.mp4"], left
    assert freed == 400
    assert playable.prune(str(tmp_path), budget=10_000) == 0     # under budget: no-op
    assert playable.prune(str(tmp_path / "nope")) == 0           # no cache dir: fine
