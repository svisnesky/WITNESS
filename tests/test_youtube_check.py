"""Check YouTube.bat — the diagnostic must be honest and must not publish.

Stan asked for this after an upload didn't appear and there was no way to tell
"auth is broken" from "it never ran" without playing a session.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import youtube_check as yc  # noqa: E402


def _stub_libs(monkeypatch):
    """Pretend the Google libraries are installed."""
    for name in ("googleapiclient", "googleapiclient.discovery",
                 "google_auth_oauthlib"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    disc = sys.modules["googleapiclient.discovery"]
    disc.build = lambda *a, **k: object()
    monkeypatch.setattr(sys.modules["googleapiclient"], "discovery", disc,
                        raising=False)


def test_missing_libraries_is_reported_first(capsys):
    """On a machine without the libs, that's the answer — not a stack trace."""
    rc = yc.run("/nonexistent", do_upload=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert "libraries missing" in out and "pip install" in out


def test_missing_client_secret_says_where_to_get_it(monkeypatch, tmp_path, capsys):
    _stub_libs(monkeypatch)
    # Stub the finder: otherwise this depends on whether the machine running the
    # tests happens to have a client_secret in its Downloads folder.
    monkeypatch.setattr(yc, "_find_downloaded_secret", lambda: "")
    rc = yc.run(str(tmp_path), do_upload=False)
    out = capsys.readouterr().out
    assert rc == 1
    assert "client_secret.json not found" in out
    assert "Desktop app" in out          # tells him the exact thing to create


def test_a_web_client_instead_of_desktop_is_flagged(monkeypatch, tmp_path, capsys):
    """A very easy mistake in the Google console, and the resulting error later
    is cryptic."""
    _stub_libs(monkeypatch)
    (tmp_path / "client_secret.json").write_text('{"web": {"client_id": "x"}}')
    monkeypatch.setattr("youtube_upload._get_credentials", lambda b: None)
    yc.run(str(tmp_path), do_upload=False)
    out = capsys.readouterr().out
    assert "not a Desktop app" in out


def test_read_only_by_default_never_uploads(monkeypatch, tmp_path, capsys):
    """THE constraint: a diagnostic must not put anything on someone's channel
    unless they explicitly ask."""
    _stub_libs(monkeypatch)
    (tmp_path / "client_secret.json").write_text('{"installed": {"client_id": "x"}}')
    monkeypatch.setattr("youtube_upload._get_credentials", lambda b: object())

    called = []
    monkeypatch.setattr("youtube_upload.upload",
                        lambda *a, **k: called.append(a) or "https://youtu.be/x")

    rc = yc.run(str(tmp_path), do_upload=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert called == [], "read-only run must NOT upload"
    assert "--upload" in out, "must tell him how to run the conclusive test"


def test_the_upload_test_is_private(monkeypatch, tmp_path, capsys):
    """If it does upload, it must be private and tell him to delete it."""
    _stub_libs(monkeypatch)
    (tmp_path / "client_secret.json").write_text('{"installed": {"client_id": "x"}}')
    monkeypatch.setattr("youtube_upload._get_credentials", lambda b: object())
    monkeypatch.setattr("montage.find_ffmpeg", lambda b, c: "ffmpeg")

    seen = {}

    def fake_upload(path, title, desc, base, privacy="unlisted"):
        seen["privacy"] = privacy
        seen["title"] = title
        return "https://youtu.be/TEST"
    monkeypatch.setattr("youtube_upload.upload", fake_upload)

    rc = yc.run(str(tmp_path), do_upload=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert seen["privacy"] == "private", "a test upload must never be public"
    assert "WITNESS upload test" in seen["title"]
    assert "Delete it" in out
    assert "7 days" in out, "must remind him to publish the consent screen"


def test_the_bat_exists_and_passes_arguments_through():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "Check YouTube.bat")
    assert os.path.exists(p)
    body = open(p, encoding="utf-8", errors="replace").read()
    assert "youtube_check.py %*" in body, "--upload must reach the script"


def test_the_exact_destination_path_is_printed(monkeypatch, tmp_path, capsys):
    """Stan: "not sure instructions are clear where to put the api key bc its in
    the 'main' folder". GitHub zips extract to <name>\\<name>, so "save it here"
    is ambiguous — print the full path INCLUDING the filename."""
    _stub_libs(monkeypatch)
    monkeypatch.setattr(yc, "_find_downloaded_secret", lambda: "")
    yc.run(str(tmp_path), do_upload=False)
    out = capsys.readouterr().out
    assert os.path.join(str(tmp_path), "client_secret.json") in out
    assert "main.py" in out            # anchors WHICH folder it means
    assert "RENAME" in out             # the step people miss


def test_a_downloaded_key_is_found_and_a_copy_command_offered(monkeypatch,
                                                              tmp_path, capsys):
    """Google downloads it as client_secret_<long-id>.json, so the rename is
    hidden work. Finding the file lets us hand over the exact command."""
    _stub_libs(monkeypatch)
    dl = tmp_path / "Downloads"
    dl.mkdir()
    got = dl / "client_secret_123-abc.apps.googleusercontent.com.json"
    got.write_text("{}")
    monkeypatch.setattr(yc, "_find_downloaded_secret", lambda: str(got))
    app = tmp_path / "app"
    app.mkdir()
    yc.run(str(app), do_upload=False)
    out = capsys.readouterr().out
    assert "I found what looks like your downloaded key" in out
    assert str(got) in out
    assert 'copy "' in out and "client_secret.json" in out


def test_the_finder_never_opens_the_credential_file(monkeypatch, tmp_path):
    """It reports a PATH only. Reading a credential to locate it would be
    gratuitous — nothing here needs its contents."""
    import inspect
    src = inspect.getsource(yc._find_downloaded_secret)
    assert "open(" not in src
    assert "read(" not in src


def test_the_finder_is_quiet_when_there_is_nothing_to_find(monkeypatch, tmp_path):
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path))
    assert yc._find_downloaded_secret() == ""
