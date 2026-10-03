"""_resolve_session_dir: a full path for the CLI, or a name INSIDE Marathon Sessions.

It returned any existing directory: a name relative to the current folder,
'../..', anything. The dashboard routes passed request data straight in, and a
rebuild ends by tidying (moving) the files of whatever folder it was given.
web-dashboard now validates before calling; this resolver no longer escapes
on its own either. Full paths keep working for 'Rebuild Last Session.bat'.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    rec = tmp_path / "OBS"
    sessions = rec / "Marathon Sessions"
    for name in ("2026-07-28_20-00-00", "2026-07-29_20-00-15"):
        (sessions / name).mkdir(parents=True)
    monkeypatch.setattr(main, "cached_record_dir", lambda: str(rec))
    return sessions


def test_a_session_name_resolves_inside_the_root(root):
    assert main._resolve_session_dir({}, "2026-07-28_20-00-00") == \
        os.path.join(str(root), "2026-07-28_20-00-00")


def test_latest_is_the_newest(root):
    assert main._resolve_session_dir({}, "latest") == \
        os.path.join(str(root), "2026-07-29_20-00-15")
    assert main._resolve_session_dir({}, "") == \
        os.path.join(str(root), "2026-07-29_20-00-15")


def test_an_absolute_path_still_works_for_the_cli(root, tmp_path):
    elsewhere = tmp_path / "copied session"
    elsewhere.mkdir()
    assert main._resolve_session_dir({}, str(elsewhere)) == str(elsewhere)


@pytest.mark.parametrize("bad", ["..", "../x", ".", "../..", "../OBS",
                                 os.path.join("..", "Marathon Sessions")])
def test_names_that_escape_the_root_are_refused(root, bad, capsys):
    assert main._resolve_session_dir({}, bad) == ""
    assert "No session folder named" in capsys.readouterr().out


def test_a_cwd_relative_folder_is_not_a_session(root, tmp_path, monkeypatch):
    """The old first branch was os.path.isdir(which): a bare name matched a
    folder in the CURRENT directory."""
    cwd = tmp_path / "somewhere"
    (cwd / "victim_dir").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    assert main._resolve_session_dir({}, "victim_dir") == ""


def test_a_link_inside_the_root_that_points_out_is_refused(root, tmp_path):
    outside = tmp_path / "pictures"
    outside.mkdir()
    try:
        os.symlink(str(outside), str(root / "sneaky"), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("can't create a directory symlink here")
    assert main._resolve_session_dir({}, "sneaky") == ""


def test_no_root_prints_a_generic_example_not_stans_drive(monkeypatch, capsys):
    monkeypatch.setattr(main, "cached_record_dir", lambda: "")
    import obs_client

    class _NoObs:
        def __init__(self, **kw):
            pass

        def connect(self):
            raise ConnectionRefusedError(61, "refused")

    monkeypatch.setattr(obs_client, "OBSClient", _NoObs)
    assert main._resolve_session_dir({}, "latest") == ""
    out = capsys.readouterr().out
    assert "<your OBS recording folder>/Marathon Sessions/<session folder>" in out
    assert "Z:" not in out


def test_the_obs_fallback_builds_its_client_properly(monkeypatch, tmp_path):
    """It called OBSClient(cfg) - the whole config dict as the host - and then
    a .close() the client didn't have. It must also not start OBS's Replay
    Buffer: this is a folder lookup, not a session."""
    rec = tmp_path / "OBS"
    (rec / "Marathon Sessions" / "s1").mkdir(parents=True)
    monkeypatch.setattr(main, "cached_record_dir", lambda: "")
    monkeypatch.setattr(main, "cache_record_dir", lambda path: None)
    import obs_client
    made = {}

    class _Obs:
        def __init__(self, **kw):
            made.update(kw)

        def connect(self):
            pass

        def get_record_directory(self):
            return str(rec)

    monkeypatch.setattr(obs_client, "OBSClient", _Obs)
    cfg = {"obs": {"host": "10.0.0.5", "port": 4456, "password": "CHANGE_ME"}}
    assert main._resolve_session_dir(cfg, "s1") == \
        os.path.join(str(rec), "Marathon Sessions", "s1")
    assert made == {"host": "10.0.0.5", "port": 4456, "password": "",
                    "auto_start_replay_buffer": False}
