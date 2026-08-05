"""The in-app update button, and the two things it must refuse.

Stan: "are we able to put the update witness shortcut IN the app? makes the most
sense there." The .bat existed but he works in the app UI.

The guards are the point. He lost TWO recaps to updating at the wrong moment —
the render was killed mid-flight — so this must not touch files under a live
session or an in-progress build.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webserver  # noqa: E402


def _state():
    st = webserver.LiveState()
    st._cfg = {}
    return st


def test_refused_while_a_session_is_running(tmp_path):
    st = _state()
    st.set_running(True)
    r = st.request_update(str(tmp_path))
    assert r["ok"] is False
    assert "stop the session" in r["error"]


def test_refused_while_a_recap_is_building(tmp_path):
    st = _state()
    st.recap = {"status": "building"}
    r = st.request_update(str(tmp_path))
    assert r["ok"] is False
    assert "building" in r["error"]


def test_refused_while_already_checking(tmp_path):
    st = _state()
    st._updating = True
    assert st.request_update(str(tmp_path))["ok"] is False


def test_it_does_not_relaunch_the_process():
    """update_and_relaunch_if_needed() replaces the process — calling it from the
    webserver would kill the session the user is looking at. This must use the
    plain check_and_update and tell the user to restart."""
    import inspect
    src = inspect.getsource(webserver.LiveState.request_update)
    assert "updater.check_and_update(" in src
    # match a CALL, not the docstring that explains why we avoid it
    assert "updater.update_and_relaunch_if_needed(" not in src


def test_status_exposes_what_the_button_needs():
    snap = _state().snapshot()
    for key in ("update", "update_changed", "updating"):
        assert key in snap, key


def test_the_page_has_the_button_and_says_to_restart():
    assert 'onclick="doUpdate()"' in webserver.PAGE
    assert "Check for update" in webserver.PAGE
    assert "RESTART WITNESS to apply" in webserver.PAGE
