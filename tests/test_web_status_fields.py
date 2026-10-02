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
    monkeypatch.setattr(main, "_resolve_session_dir", lambda cfg, w: "")
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
