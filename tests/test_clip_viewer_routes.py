"""The clip viewer's HTTP surface. Reachable from the LAN, so the path handling
is a boundary, not a convenience."""

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import webserver  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 0          # set by the fixture: the OS picks a free port

# ONE server for the whole module. Starting a fresh one per test on the same port
# silently bind-fails, so requests hit the FIRST server while assertions poke a
# different LiveState — tests that pass alone and fail in the suite. Port 0, not
# a fixed number: a second checkout running the suite at the same time held the
# fixed port and every request here went to ITS server.
import pytest  # noqa: E402


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp("clipsrv")
    sess = root / "Marathon Sessions" / "2026-08-08_21-35-33"
    sess.mkdir(parents=True)
    (sess / "001_down_20-07-34.mkv").write_bytes(b"x")
    st = webserver.LiveState()
    st._cfg = {}
    real_cached = main.cached_record_dir
    main.cached_record_dir = lambda: str(root)
    global PORT
    srv = webserver.start_web(st, 0, BASE, "127.0.0.1")
    PORT = srv.server_address[1]
    try:
        yield st, str(sess), str(root)
    finally:
        srv.shutdown()
        srv.server_close()
        # don't leave every later test module pointed at this temp folder
        main.cached_record_dir = real_cached


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


def _nav(page):
    import re
    nav = re.search(r'<nav class="nav">(.*?)</nav>', page, re.S).group(1)
    return re.findall(r'href="(/[a-z]*)"', nav), nav


def test_all_four_pages_share_one_nav_order_with_no_duplicates():
    """PAGE, Archive and Stats each had 'Reels' AND 'Archive' going to /archive,
    and a different order from the Clips page."""
    for pg in (webserver.PAGE, webserver.STATS_PAGE, webserver.ARCHIVE_PAGE,
               webserver.CLIPS_PAGE):
        hrefs, nav = _nav(pg)
        assert ">Reels<" not in nav
        pages = [h for i, h in enumerate(hrefs) if h not in hrefs[:i]]
        assert pages == ["/", "/clips", "/stats", "/archive"], pages
    # the Live page's entries are real links now, not onclick= navigations
    assert "location.href='/" not in _nav(webserver.PAGE)[1]


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


# --- the session NAME is a boundary too ---------------------------------------
# main._resolve_session_dir is the --rebuild CLI's resolver and accepts any
# existing folder. Fed request data, that let a request tidy (move) the files in
# an arbitrary folder, render reels into it and pull frames out of its videos.

def _outside(tmp_path):
    """A folder that is NOT a session: someone's pictures and home videos."""
    d = tmp_path / "victim_dir"
    d.mkdir()
    (d / "IMG_0001.jpg").write_bytes(b"jpg")
    (d / "my_family_video.mp4").write_bytes(b"x")
    return d


def _untouched(d):
    assert sorted(os.listdir(d)) == ["IMG_0001.jpg", "my_family_video.mp4"], \
        sorted(os.listdir(d))


def _get_status(path):
    try:
        return _get(path).status
    except urllib.error.HTTPError as e:
        return e.code


import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402

_BAD_NAMES = ("..", "../x", "..%2F..", "../../etc", ".", "...", "a/b", "C:\\Windows")


def test_session_names_that_escape_are_refused_by_every_route(server, tmp_path):
    st = server[0]
    d = _outside(tmp_path)
    for bad in (str(d),) + _BAD_NAMES:
        q = urllib.parse.quote(bad, safe="")
        assert _get_status(f"/clipthumb?s={q}&f=my_family_video.mp4") == 404, bad
        data = json.load(_get(f"/clipdata?s={q}"))
        assert data["clips"] == [] and "error" in data, bad
        r = _post("/export", {"session": bad,
                              "clips": [{"rel": "my_family_video.mp4"}]})
        assert r["ok"] is False, bad
        r = _post("/rebuild", {"session": bad})
        assert r["ok"] is False, bad
        assert webserver._web_session_dir(st, bad) == "", bad
    time.sleep(0.3)               # any (wrongly) started worker would act by now
    _untouched(d)


def test_a_cwd_relative_folder_is_not_a_session(server, tmp_path, monkeypatch):
    """The CLI resolver's first branch is os.path.isdir(which), so a bare name
    matched a folder in the CURRENT directory — even one inside some other
    folder that happens to be called 'Marathon Sessions'."""
    st = server[0]
    other = tmp_path / "elsewhere" / "Marathon Sessions"
    other.mkdir(parents=True)
    d = _outside(other)
    monkeypatch.chdir(other)
    assert webserver._web_session_dir(st, "victim_dir") == ""
    assert _post("/rebuild", {"session": "victim_dir"})["ok"] is False
    assert _get_status("/clipthumb?s=victim_dir&f=my_family_video.mp4") == 404
    time.sleep(0.2)
    _untouched(d)


def test_a_real_session_name_still_resolves(server):
    st, sess, _root = server
    assert webserver._web_session_dir(st, "2026-08-08_21-35-33") == sess
    assert webserver._web_session_dir(st, "latest") == sess
    assert webserver._web_session_dir(st, "") == sess
    assert webserver._web_session_dir(st, "2026-01-01_00-00-00") == ""   # absent
    d = json.load(_get("/clipdata?s=2026-08-08_21-35-33"))
    assert d["session"] == "2026-08-08_21-35-33" and d["clips"]


def test_rebuild_gets_the_resolved_absolute_path(server, monkeypatch):
    st, sess, _root = server
    seen = []

    def fake(cfg, which, rc=None):
        seen.append(which)
        rc(status="ready", note="rebuilt")

    monkeypatch.setattr(main, "rebuild_session", fake)
    r = _post("/rebuild", {"session": "2026-08-08_21-35-33"})
    assert r["ok"] is True and r["started"] == "2026-08-08_21-35-33"
    for _ in range(50):
        if seen and not getattr(st, "_rebuilding", False):
            break
        time.sleep(0.02)
    assert seen == [sess]


# --- the Archive leads to each session's clips ------------------------------

def _archive_root(tmp_path):
    root = tmp_path / "rec"
    ms = root / "Marathon Sessions"
    clips_only = ms / "2026-08-01_20-00-00"           # recap failed: no output
    (clips_only / "clips").mkdir(parents=True)        # tidied: clips/ only
    (clips_only / "clips" / "001_down_20-01-00.mkv").write_bytes(b"x")
    montage_only = ms / "2026-08-02_20-00-00"         # match reels were off
    (montage_only / "clips").mkdir(parents=True)
    (montage_only / "clips" / "001_down_20-01-00.mkv").write_bytes(b"x")
    (montage_only / "highlights_2026-08-02.mp4").write_bytes(b"x")
    (montage_only / "shorts").mkdir()
    (montage_only / "shorts" / "001_down_20-01-00.mp4").write_bytes(b"x")
    empty = ms / "2026-08-03_20-00-00"
    empty.mkdir()
    return str(root)


def _block(page, sess):
    """The <details> block of one session on the Archive page."""
    i = page.index(sess.replace("_", " · "))
    j = page.find("<details>", i)
    return page[i:j if j > 0 else len(page)]


def test_archive_links_each_session_with_clips_to_its_clip_viewer(tmp_path):
    page = webserver._archive_page(BASE, _archive_root(tmp_path))
    b = _block(page, "2026-08-01_20-00-00")
    assert 'href="/clips?s=2026-08-01_20-00-00"' in b
    assert "Pick clips / cut a reel" in b
    # tidied clips are counted (they live in clips/, not the top level)
    assert "1 clip" in b
    # no clips, no link
    assert "/clips?s=" not in _block(page, "2026-08-03_20-00-00")


def test_archive_warns_only_when_nothing_was_rendered(tmp_path):
    page = webserver._archive_page(BASE, _archive_root(tmp_path))
    assert "the recap failed" in _block(page, "2026-08-01_20-00-00")
    # a montage and Shorts are reels too: no false "recap failed"
    assert "the recap failed" not in _block(page, "2026-08-02_20-00-00")
    assert "the recap failed" not in _block(page, "2026-08-03_20-00-00")


def test_the_clip_viewer_opens_the_session_it_was_linked_to():
    p = webserver.CLIPS_PAGE
    assert "new URLSearchParams(location.search).get('s') || 'latest'" in p
    assert 'SESSION = "latest"' not in p
