"""What the dashboard tells the user: rebuild results, recap failures.

Each of these was a place the page said something untrue: "Done — reloading"
over a rebuild that failed, "Everything built" over a night that built nothing,
and a failed recap that simply vanished from the Live page."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import webserver  # noqa: E402


def _state():
    st = webserver.LiveState()
    st._cfg = {}
    return st


def _wait_rebuild(st):
    for _ in range(200):
        if not getattr(st, "_rebuilding", False):
            return
        time.sleep(0.01)
    raise AssertionError("rebuild thread never finished")


# --- Archive "Rebuild reels" --------------------------------------------------

def test_rebuild_is_refused_while_a_session_is_running():
    """A rebuild runs the full ffmpeg pipeline and ends by tidying the folder,
    moving the LIVE session's clips out from under its match reels."""
    st = _state()
    st.set_running(True)
    r = st.request_rebuild("2026-08-08_21-35-33")
    assert r["ok"] is False
    assert "stop the session" in r["error"]
    assert not getattr(st, "_rebuilding", False)


def test_a_failed_rebuild_stays_failed(monkeypatch):
    """rebuild_session reports 'error' and returns normally; the worker then
    used to overwrite it with 'ready — rebuild complete'."""
    def builder(cfg, which, rc=None):
        rc(status="building", note="building session montage…")
        rc(status="error", note="rebuild incomplete — montage: ffmpeg not found")

    monkeypatch.setattr(main, "rebuild_session", builder)
    st = _state()
    assert st.request_rebuild("2026-08-08_21-35-33")["ok"] is True
    _wait_rebuild(st)
    assert st.recap["status"] == "error", st.recap
    assert "montage" in st.recap["note"]
    assert st.recap["kind"] == "rebuild"


def test_a_successful_rebuild_keeps_its_own_note(monkeypatch):
    monkeypatch.setattr(main, "rebuild_session",
                        lambda cfg, which, rc=None: rc(status="ready",
                                                       note="rebuilt s1"))
    st = _state()
    st.request_rebuild("s1")
    _wait_rebuild(st)
    assert st.recap["status"] == "ready" and st.recap["note"] == "rebuilt s1"


def test_a_rebuild_that_reports_nothing_does_not_hang_the_ui(monkeypatch):
    monkeypatch.setattr(main, "rebuild_session", lambda cfg, which, rc=None: None)
    st = _state()
    st.request_rebuild("s1")
    _wait_rebuild(st)
    assert st.recap["status"] == "error", st.recap


def test_a_stuck_building_status_does_not_lock_out_rebuild(monkeypatch):
    """resume_unfinished_recap leaves 'building' set if it raised. Rebuild is
    the recovery tool, so that status must not block it."""
    monkeypatch.setattr(main, "rebuild_session",
                        lambda cfg, which, rc=None: rc(status="ready", note="ok"))
    st = _state()
    st.set_recap(status="building", note="building your highlights…")
    assert st.request_rebuild("s1")["ok"] is True
    _wait_rebuild(st)


# --- who wrote the recap ------------------------------------------------------

def test_recap_kind_defaults_to_recap_and_is_reset_by_every_write():
    st = _state()
    st.set_recap(status="building", note="x")
    assert st.recap["kind"] == "recap"
    st.set_recap(status="error", note="export failed", kind="export")
    assert st.recap["kind"] == "export"
    st.set_recap(status="building", note="building your highlights…")
    assert st.recap["kind"] == "recap", "an old export tag must not stick"
    assert st.snapshot()["recap"]["kind"] == "recap"


def test_export_failures_are_tagged_as_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_resolve_session_dir", lambda cfg, w, **k: "")
    st = _state()
    assert st.request_export(str(tmp_path), "nope", [])["ok"] is True
    for _ in range(200):
        if not getattr(st, "_exporting", False):
            break
        time.sleep(0.01)
    assert st.recap["status"] == "error" and st.recap["kind"] == "export"


# --- the Live page draws them -------------------------------------------------

def test_the_live_page_draws_a_failed_recap():
    p = webserver.PAGE
    assert "rc.status === 'error'" in p
    assert "Recap had problems" in p
    assert "open the Archive and press Rebuild reels after fixing it" in p
    # 'ready' shows the note when there is one; the fixed text only without
    assert "rc.note || 'Everything built" in p


# --- a START that fails says why ----------------------------------------------

def test_snapshot_carries_the_start_error_and_starting_flag():
    st = _state()
    snap = st.snapshot()
    assert snap["last_error"] == "" and snap["starting"] is False
    # the desktop launcher assigns these as plain attributes
    st.starting = True
    assert st.snapshot()["starting"] is True
    st.starting = False
    st.last_error = "OBS: cannot reach the WebSocket server"
    assert st.snapshot()["last_error"] == "OBS: cannot reach the WebSocket server"


def test_snapshot_tolerates_a_state_without_the_new_attributes():
    """getattr in snapshot(): a LiveState built by older code, or one the
    launcher hasn't touched, must not raise on the poll every client makes."""
    st = _state()
    del st.last_error
    del st.starting
    snap = st.snapshot()
    assert snap["last_error"] == "" and snap["starting"] is False


def test_a_session_that_starts_clears_the_last_failure():
    st = _state()
    st.last_error = "OBS rejected the WebSocket password"
    st.starting = True
    st.set_running(True)
    snap = st.snapshot()
    assert snap["last_error"] == "" and snap["starting"] is False
    st.set_running(False)
    assert st.snapshot()["last_error"] == ""


def test_the_live_page_shows_the_error_and_holds_starting():
    p = webserver.PAGE
    assert 'id="errbox"' in p and "d.last_error" in p
    # the banner is drawn only while idle: it must never cover a live feed
    assert "d.last_error && !d.running && !d.starting" in p
    # the 12 s safety release waits while the launcher says it's starting
    assert "setTimeout(releaseRun, 12000)" in p
    assert "if (runStarting){ setTimeout(releaseRun" in p


# --- the phone/iPad address ---------------------------------------------------

def test_lan_ips_puts_the_default_route_first_then_192_168():
    got = webserver.lan_ips(primary="10.8.0.2",
                            others=["172.24.16.1", "192.168.1.50", "10.8.0.2",
                                    "127.0.0.1", "169.254.3.4", "8.8.4.4",
                                    "192.168.1.50"])
    # VPN/default route first (as decided), the Wi-Fi 192.168 next, the WSL /
    # Hyper-V 172.x last; loopback, link-local, public and dupes gone
    assert got == ["10.8.0.2", "192.168.1.50", "172.24.16.1"], got


def test_lan_ips_with_no_network_is_empty():
    assert webserver.lan_ips(primary="127.0.0.1", others=["127.0.0.1"]) == []
    assert webserver.lan_ips(primary="169.254.9.9", others=[]) == []


def test_snapshot_carries_the_lan_addresses(monkeypatch):
    st = _state()
    assert st.snapshot()["lan_urls"] == []
    monkeypatch.setattr(webserver, "lan_ips", lambda: ["192.168.1.50"])
    webserver._fill_lan_urls(st, 8000)
    assert st.snapshot()["lan_urls"] == ["192.168.1.50:8000"]
    # no scheme in the data: no URL literal in the app, the page adds its own
    assert "://" not in st.snapshot()["lan_urls"][0]


def test_a_failed_lookup_leaves_no_address(monkeypatch):
    def boom():
        raise OSError("name resolution stalled")
    monkeypatch.setattr(webserver, "lan_ips", boom)
    st = _state()
    webserver._fill_lan_urls(st, 8000)
    assert st.snapshot()["lan_urls"] == []


def test_a_localhost_only_server_offers_no_phone_address():
    """web_lan: false binds 127.0.0.1; a phone address would be a dead link."""
    st = _state()
    srv = webserver.start_web(st, 0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "127.0.0.1")
    try:
        time.sleep(0.1)
        assert st.snapshot()["lan_urls"] == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_page_shows_the_address_only_on_the_pc():
    p = webserver.PAGE
    assert 'id="phoneurl"' in p and "On your phone/iPad (same Wi-Fi)" in p
    assert "window.pywebview" in p and "onThePC() && lan.length" in p
    # the How-to-use modal carries it too, with the firewall hint
    assert 'id="helpurl"' in p and "Private networks" in p


# --- the build label ----------------------------------------------------------

def _fake_version(monkeypatch, value):
    import types
    if value is None:
        monkeypatch.setitem(sys.modules, "version", None)   # import -> ImportError
    else:
        monkeypatch.setitem(sys.modules, "version",
                            types.SimpleNamespace(VERSION=value))


def test_build_label_is_the_number_and_the_sha(tmp_path, monkeypatch):
    _fake_version(monkeypatch, "0.1.0")
    (tmp_path / ".app_version").write_text("1a2b3c4d5e6f\n", encoding="utf-8")
    assert webserver.build_label(str(tmp_path)) == "0.1.0 (1a2b3c4)"


def test_build_label_without_a_sha_says_dev_next_to_the_number(tmp_path, monkeypatch):
    _fake_version(monkeypatch, "0.1.0")
    assert webserver.build_label(str(tmp_path)) == "0.1.0 (dev)"


def test_build_label_without_version_py(tmp_path, monkeypatch):
    """version.py ships from another change; until it lands this must not
    break, and must not go back to a bare 'dev'."""
    _fake_version(monkeypatch, None)
    assert webserver.build_label(str(tmp_path)) == ""
    (tmp_path / ".app_version").write_text("1a2b3c4d", encoding="utf-8")
    assert webserver.build_label(str(tmp_path)) == "1a2b3c4"


def test_start_web_publishes_the_label(tmp_path, monkeypatch):
    _fake_version(monkeypatch, "0.1.0")
    st = _state()
    srv = webserver.start_web(st, 0, str(tmp_path), "127.0.0.1")
    try:
        assert st.snapshot()["version"] == "0.1.0 (dev)"
        assert webserver._api_info(st)["version"] == "0.1.0 (dev)"
    finally:
        srv.shutdown()
        srv.server_close()


# --- Save clip / +1 Kill with no session --------------------------------------

def _post_raw(port, path):
    import http.client
    import json
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request("POST", path, body=b"{}",
                  headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")
    finally:
        c.close()


def test_clip_and_addkill_say_not_running_when_idle():
    """They used to answer ok and flash green, while LiveState.reset() threw
    the request away at the next START."""
    st = _state()
    srv = webserver.start_web(st, 0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "127.0.0.1")
    port = srv.server_address[1]
    try:
        for path in ("/clip", "/addkill"):
            code, body = _post_raw(port, path)
            # 200, not 4xx: the iOS app treats any non-2xx as "PC didn't respond"
            assert code == 200 and body == {"ok": False, "error": "not running"}
        assert st.pop_clip_request() is False and st.pop_kill_request() is False
        st.set_running(True)
        assert _post_raw(port, "/clip") == (200, {"ok": True})
        assert _post_raw(port, "/addkill") == (200, {"ok": True})
        assert st.pop_clip_request() is True and st.pop_kill_request() is True
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_live_page_disables_the_buttons_while_idle():
    p = webserver.PAGE
    assert "b.disabled = !d.running" in p and "'Press START first'" in p
    assert "['clip', 'addk']" in p
    # the reply decides what the button says
    assert "'NOT RUNNING'" in p and "j.ok === false" in p
    assert "pressAction('clip', '/clip'" in p and "pressAction('addk', '/addkill'" in p


# --- the Stats page and a blank gamertag --------------------------------------

def test_stats_page_tells_a_blank_gamertag_how_to_turn_names_on(tmp_path):
    """New installs ship gamertag: "" and the Menace Report can never fill.
    It used to promise names 'as downs happen' forever."""
    hint = "Set gamertag in config.yaml (your exact in-game name) to turn this on."
    base = str(tmp_path)                        # no stats yet
    assert hint in webserver._stats_page(base, {"gamertag": ""})
    assert hint in webserver._stats_page(base, {"gamertag": "   "})
    assert hint in webserver._stats_page(base, {"gamertag": None})  # bare `gamertag:`


def test_stats_page_keeps_the_old_text_when_a_tag_applies(tmp_path):
    base = str(tmp_path)
    for cfg in ({"gamertag": "KRYPT KING05"},
                {},           # key ABSENT: an old config keeps DEFAULT_GAMERTAG
                None):        # nothing bound yet: unknown, say nothing new
        page = webserver._stats_page(base, cfg)
        assert "as downs happen" in page and "Set gamertag" not in page, cfg


def test_the_gamertag_rule_is_key_absent_not_falsy():
    import encounters
    assert webserver._gamertag({}) == encounters.DEFAULT_GAMERTAG.strip()
    assert webserver._gamertag({"gamertag": ""}) == ""
    assert webserver._gamertag({"gamertag": " X "}) == "X"
    assert webserver._gamertag(None) is None


def test_bonjour_gets_a_build_id_that_survives_the_txt_cap(tmp_path, monkeypatch):
    """The iPad's PC picker shows the Bonjour TXT 'v' next to the address, and
    discovery.advertise keeps only 12 characters of it. The full label,
    '0.1.0 (1a2b3c4)', is 15, so the picker read '0.1.0 (1a2b3'. start_web
    also publishes the bare sha as state.build_sha for main.py to advertise;
    the guarded getattr below is that call, and it must also hold before
    main.py passes it (an older main.py simply keeps sending state.version)."""
    import zeroconf
    import discovery
    _fake_version(monkeypatch, "0.1.0")
    (tmp_path / ".app_version").write_text("1a2b3c4d5e6f\n", encoding="utf-8")
    st = _state()
    srv = webserver.start_web(st, 0, str(tmp_path), "127.0.0.1")
    try:
        assert st.version == "0.1.0 (1a2b3c4)"      # page + /api/info: unchanged
        assert st.build_sha == "1a2b3c4"
    finally:
        srv.shutdown()
        srv.server_close()

    props = {}

    class FakeZC:
        def register_service(self, info, ttl=None, allow_name_change=False):
            props.update(info.properties)

        def unregister_service(self, info):
            pass

        def close(self):
            pass

    monkeypatch.setattr(zeroconf, "Zeroconf", FakeZC)
    adv = discovery.advertise(
        8000, "192.168.1.50",
        version=getattr(st, "build_sha", "") or getattr(st, "version", ""),
        api=webserver.API_VERSION)
    assert adv is not None
    try:
        assert props[b"v"] == b"1a2b3c4"
    finally:
        adv.stop()


def test_build_sha_is_blank_without_an_app_version(tmp_path, monkeypatch):
    """No sha yet: build_sha is '' so the guarded call falls back to the label
    ('0.1.0 (dev)', which fits the TXT cap)."""
    _fake_version(monkeypatch, "0.1.0")
    st = _state()
    assert st.build_sha == ""
    srv = webserver.start_web(st, 0, str(tmp_path), "127.0.0.1")
    try:
        assert st.build_sha == ""
        assert (getattr(st, "build_sha", "") or st.version) == "0.1.0 (dev)"
    finally:
        srv.shutdown()
        srv.server_close()
