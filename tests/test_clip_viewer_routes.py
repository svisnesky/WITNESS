"""The clip viewer's HTTP surface. Reachable from the LAN, so the path handling
is a boundary, not a convenience."""

import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import webserver  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8931

# ONE server for the whole module. Starting a fresh one per test on the same port
# silently bind-fails, so requests hit the FIRST server while assertions poke a
# different LiveState — tests that pass alone and fail in the suite.
import pytest  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("clipsrv")
    sess = root / "Marathon Sessions" / "2026-08-08_21-35-33"
    sess.mkdir(parents=True)
    (sess / "001_down_20-07-34.mkv").write_bytes(b"x")
    st = webserver.LiveState()
    st._cfg = {}
    main.cached_record_dir = lambda: str(root)
    threading.Thread(target=webserver.start_web,
                     args=(st, PORT, BASE, "127.0.0.1"), daemon=True).start()
    time.sleep(0.7)
    return st, str(sess), str(root)


def _get(path):
    return urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5)


def _post(path, body):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method="POST",
                              data=json.dumps(body).encode(),
                              headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=5))


def test_the_page_and_data_route_serve(server):
    assert _get("/clips").status == 200
    d = json.load(_get("/clipdata?s=latest"))
    assert d["session"] == "2026-08-08_21-35-33"
    assert [c["name"] for c in d["clips"]] == ["001_down_20-07-34.mkv"]


def test_thumbnail_route_refuses_paths_outside_the_session(server):
    st, _sess, root = server
    open(os.path.join(root, "secret.mkv"), "wb").write(b"x")
    for bad in ("../secret.mkv", "..%2Fsecret.mkv", "/etc/hosts"):
        assert webserver._clip_thumb(st, "2026-08-08_21-35-33", bad) == "", bad


def test_export_is_refused_during_a_session(server):
    st = server[0]
    st.set_running(True)
    try:
        r = _post("/export", {"session": "latest",
                              "clips": [{"rel": "001_down_20-07-34.mkv"}]})
        assert r["ok"] is False
        assert "stop the session" in r["error"]
    finally:
        st.set_running(False)


def test_export_refuses_a_second_run(server):
    st = server[0]
    st._exporting = True
    try:
        assert _post("/export", {"session": "latest", "clips": []})["ok"] is False
    finally:
        st._exporting = False


def test_a_malformed_export_body_does_not_crash_the_server(server):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}/export", method="POST",
                               data=b"not json",
                               headers={"Content-Type": "application/json"})
    assert urllib.request.urlopen(r, timeout=5).status == 200
    assert _get("/clips").status == 200          # still alive


def test_clipdata_reports_a_missing_session_instead_of_500(server, monkeypatch):
    st = server[0]
    monkeypatch.setattr(main, "_resolve_session_dir", lambda cfg, w: "")
    d = webserver._clip_data(st, "latest")
    assert d["clips"] == [] and "error" in d


def test_every_page_links_to_the_clip_viewer():
    """It's useless if he can't find it — the recurring lesson this session."""
    for pg in (webserver.PAGE, webserver.STATS_PAGE, webserver.ARCHIVE_PAGE,
               webserver.CLIPS_PAGE):
        assert "/clips" in pg


def test_the_viewer_page_has_the_promised_controls():
    p = webserver.CLIPS_PAGE
    for needed in ("Export reel", "Auto-trim to the action", "Only real kills",
                   "Set IN here", "Set OUT here", "In the reel",
                   "opt_music", "opt_trans", "opt_chy", "opt_ann"):
        assert needed in p, needed


def test_the_viewer_javascript_has_no_broken_quote_escaping():
    """A \\' inside the triple-quoted page collapsed to ' and killed the whole
    script — the page rendered but nothing worked and the console was silent."""
    p = webserver.CLIPS_PAGE
    assert "setIn(" in p and "setOut(" in p
    # the old form would leave a bare quote inside a single-quoted JS string
    assert "',\\'ss\\'," not in p
    assert "',''," not in p
