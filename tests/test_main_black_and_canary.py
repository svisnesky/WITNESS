"""Safety warnings reach the dashboard, and the black check knows WHY it's black.

Before: the black-capture warning went into the recap state as status 'error',
which the Live page never draws (and which clobbered a resume build's
'building'); the detection canary's "DETECTION LOOKS BROKEN" was printed to a
console the window app doesn't have; and a Game Capture user who pressed START
before launching the game got the full red "OBS IS BLACK - NOT RECORDING"
overlay and voice every time.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

QUIET = {"announcer_medals": False}


def _black_png():
    import cv2
    ok, buf = cv2.imencode(".png", np.zeros((90, 160, 3), dtype=np.uint8))
    assert ok
    return buf.tobytes()


class _Obs:
    """OBS whose program scene is black. kinds=... adds get_scene_input_kinds."""

    def __init__(self, kinds="absent"):
        if kinds != "absent":
            self.get_scene_input_kinds = lambda: kinds

    def get_scene_screenshot(self, width=480):
        return _black_png()


class _Web:
    def __init__(self):
        self.notices, self.alerts, self.recaps = [], {}, []

    def notice(self, text, tag="alert"):
        assert len(text) <= 60, "LiveState.notice cuts at 60 chars"
        self.notices.append(text)

    def set_alert(self, key, level, title, message=""):
        self.alerts[key] = message

    def clear_alert(self, key):
        self.alerts.pop(key, None)

    def set_recap(self, **kw):
        self.recaps.append(kw)


def _spy_overlay(monkeypatch):
    shown = []
    monkeypatch.setattr(main, "show_text_overlay",
                        lambda cfg, text, **k: shown.append(text))
    return shown


def test_game_capture_before_the_game_is_a_soft_note_at_startup(monkeypatch, capsys):
    shown = _spy_overlay(monkeypatch)
    web = _Web()
    ok = main.check_obs_not_black(QUIET, _Obs(kinds=["game_capture"]),
                                  web=web, label="startup")
    assert ok is False
    assert shown == [] and web.notices == [] and web.alerts == {}
    assert "Game/Window Capture" in capsys.readouterr().out


def test_game_capture_still_alarms_on_the_periodic_check(monkeypatch):
    shown = _spy_overlay(monkeypatch)
    web = _Web()
    main.check_obs_not_black(QUIET, _Obs(kinds=["window_capture"]), web=web)
    assert shown == ["OBS IS BLACK - NOT RECORDING"]
    assert web.notices == ["OBS IS BLACK - re-pick the display in OBS"]


def test_an_empty_scene_says_add_a_capture_source(monkeypatch, capsys):
    shown = _spy_overlay(monkeypatch)
    web = _Web()
    main.check_obs_not_black(QUIET, _Obs(kinds=[]), web=web, label="startup")
    assert web.notices == ["No visible capture source in OBS - unhide or add one"]
    assert "add Display Capture or Game Capture" in web.alerts["black_capture"]
    # blackcheck leaves hidden sources out, so the same verdict means "its eye
    # icon is off": the text must not only say "add one"
    assert "eye icon" in web.alerts["black_capture"]
    assert shown == ["OBS IS BLACK - NOT RECORDING"]       # still loud
    assert "Sources > +" in capsys.readouterr().out


def test_display_capture_or_an_old_client_keeps_todays_warning(monkeypatch):
    for obs in (_Obs(), _Obs(kinds=["monitor_capture"]), _Obs(kinds=None)):
        shown = _spy_overlay(monkeypatch)
        web = _Web()
        main.check_obs_not_black(QUIET, obs, web=web, label="startup")
        assert shown == ["OBS IS BLACK - NOT RECORDING"]
        assert web.notices == ["OBS IS BLACK - re-pick the display in OBS"]
        assert "re-pick the display" in web.alerts["black_capture"]


def test_the_black_check_no_longer_writes_the_recap_state(monkeypatch):
    _spy_overlay(monkeypatch)
    web = _Web()
    main.check_obs_not_black(QUIET, _Obs(), web=web)
    state = {"clutch": True}
    main.check_obs_not_black(dict(QUIET, black_overlay_in_clutch=False), _Obs(),
                             web=web, state=state)
    assert state["black_pending"] is True
    assert web.recaps == []


def test_blackchecks_own_scene_verdict_is_used_when_present(monkeypatch):
    import blackcheck
    shown = _spy_overlay(monkeypatch)
    monkeypatch.setattr(blackcheck, "check_obs_capture",
                        lambda obs: {"ok": True, "blank": True, "mean": 0.0,
                                     "stddev": 0.0, "reason": "black",
                                     "scene": "hook_only"})
    web = _Web()
    main.check_obs_not_black(QUIET, object(), web=web, label="startup")
    assert shown == [] and web.notices == []


def test_the_startup_check_counts_as_the_first_periodic_one(monkeypatch):
    """The startup check runs inside _setup_session; without stamping
    _black_check_at the housekeeping thread ran it AGAIN on the first frame:
    two alarms for one problem."""
    import obs_client
    seen = []
    monkeypatch.setattr(main, "check_obs_not_black",
                        lambda cfg, obs, web=None, state=None, label="": seen.append(label))

    class _Dry:
        def connect(self):
            pass

        def set_counter(self, n):
            pass

        def replay_buffer_active(self):
            return True

        def get_record_directory(self):
            return ""

    class _W(_Web):
        record_dir = ""

        def reset(self):
            pass

        def bind_config(self, cfg, cb):
            pass

        def set_running(self, r):
            pass

    monkeypatch.setattr(obs_client, "DryRunOBS", _Dry)
    monkeypatch.setattr(main, "_web_server", object())
    monkeypatch.setattr(main, "_web_state", _W())
    monkeypatch.setattr(main, "_ffmpeg_available", lambda base, cfg: True)
    cfg = {"announcer_medals": False, "heat_streaks": False,
           "detection_canary": False, "black_check_minutes": 5}
    s = main._setup_session(cfg, dry_run=True)
    assert seen == ["startup"]
    assert "_black_check_at" in s
    monkeypatch.setattr(main, "check_disk_space", lambda *a, **k: None)
    s["obs"].recover_replay_buffer = lambda: False
    th = main._maybe_housekeeping(cfg, s, s["_black_check_at"] + 61)  # first minute
    if th is not None:
        th.join(5)
    assert seen == ["startup"], "no second black check on the first frames"


# --- the detection canary ------------------------------------------------------------

class _Canary:
    def __init__(self, level, msg="the game credited 6 kill(s) and WITNESS detected 0"):
        self.level, self.msg = level, msg

    def verdict(self):
        return self.level, self.msg


def test_a_broken_canary_reaches_the_feed_and_the_recap_note():
    web = _Web()
    for level in ("broken", "blind"):
        web.notices.clear()
        note = main._canary_health({"canary": _Canary(level), "web": web})
        assert web.notices == ["DETECTION LOOKS BROKEN - see recap"]
        assert note.startswith("DETECTION LOOKS BROKEN - ") and "detected 0" in note


def test_a_quiet_night_is_reported_without_crying_broken():
    web = _Web()
    note = main._canary_health({"canary": _Canary("quiet", "no kill popup matched"),
                                "web": web})
    assert web.notices == ["No kill popup matched all session - see recap"]
    assert "BROKEN" not in note and "no kill popup matched" in note


def test_an_ok_canary_says_nothing():
    web = _Web()
    assert main._canary_health({"canary": _Canary("ok", ""), "web": web}) == ""
    assert main._canary_health({"canary": None, "web": web}) == ""
    assert web.notices == []


def test_the_session_end_hands_the_verdict_to_the_recap(monkeypatch):
    got = {}
    monkeypatch.setattr(main, "_end_session", lambda *a, **k: got.update(k))
    monkeypatch.setattr(main, "_flush_coalesce", lambda s: None)
    web = _Web()
    web.set_running = lambda r: None
    s = {"canary": _Canary("broken"), "web": web, "session_tags": [],
         "session_start": 0.0, "session_start_wall": "20:00", "obs": None,
         "session_id": "s1", "count": 0}
    main._finish_session({}, s, dry_run=False)
    assert got["health_note"].startswith("DETECTION LOOKS BROKEN")
