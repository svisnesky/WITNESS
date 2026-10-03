"""START pins the session's tts_online in the announcer.

announcer.configure(cfg) existed and was documented ("call per session, after
load_config") but nothing called it, so the switch always came from the
config.yaml / settings_override.yaml beside announcer.py. `main.py --config
other.yaml` with tts_online: false still sent narration text to Microsoft."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import announcer  # noqa: E402
import main  # noqa: E402


class _Stop(Exception):
    pass


def _start(monkeypatch, tmp_path, cfg):
    # The files beside announcer.py say nothing (an empty folder): only the
    # session's cfg can turn the voice off.
    monkeypatch.setattr(announcer, "_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(announcer, "_ONLINE", None)
    monkeypatch.setattr(announcer, "_CONFIGURED_AT", None)

    def stop(cfg):
        raise _Stop()
    monkeypatch.setattr(main, "build_detector", stop)
    with pytest.raises(_Stop):
        main._run_live_inner(cfg, dry_run=True)


def test_a_sessions_tts_online_false_reaches_the_announcer(monkeypatch, tmp_path):
    _start(monkeypatch, tmp_path, {"tts_online": False})
    assert announcer._ONLINE is False
    assert announcer._session_online() is False


def test_a_config_without_the_key_stays_online(monkeypatch, tmp_path):
    _start(monkeypatch, tmp_path, {})
    assert announcer._session_online() is True


def test_an_announcer_without_configure_is_left_alone(monkeypatch):
    monkeypatch.delattr(announcer, "configure")
    main._configure_announcer({"tts_online": False})     # no error
