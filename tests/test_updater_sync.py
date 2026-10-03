"""The self-updater against a fake GitHub: what it downloads, what it writes,
and what it must leave alone.

Every install tracks main and updates itself, and Stan's gaming PC runs
whatever this file writes every night, so the expensive mistakes are:
  - a hashing bug that calls a changed file "unchanged" and strands a user on
    stale code,
  - rewriting files that did not change (the .bat that is running right now),
  - writing the user's config.yaml,
  - ignoring auto_update: false.
No network: updater._get is replaced by a dict lookup.
"""

import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import updater  # noqa: E402

LATEST = "c" * 40


def git_blob_sha(data: bytes) -> str:
    """git's object id for a blob, written out independently of updater.py so
    a bug there cannot agree with itself here."""
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class FakeGitHub:
    """Serves /commits/main, /git/trees/<sha> and raw blobs; logs every URL."""

    def __init__(self, files, latest=LATEST, tree_sha=None):
        self.files = dict(files)            # repo path -> bytes (as git stores it)
        self.latest = latest
        self.tree_sha = dict(tree_sha or {})  # path -> override sha (tests a lie)
        self.urls = []

    def tree(self):
        out = []
        for p, b in self.files.items():
            out.append({"path": p, "mode": "100644", "type": "blob",
                        "sha": self.tree_sha.get(p, git_blob_sha(b)),
                        "size": len(b)})
        return {"sha": "t" * 40, "tree": out, "truncated": False}

    def __call__(self, url, timeout=10):
        self.urls.append(url)
        if url == f"{updater.API}/commits/{updater.BRANCH}":
            return json.dumps({"sha": self.latest}).encode()
        if url.startswith(f"{updater.API}/git/trees/{self.latest}"):
            return json.dumps(self.tree()).encode()
        prefix = f"{updater.RAW}/{self.latest}/"
        if url.startswith(prefix):
            import urllib.parse
            return self.files[urllib.parse.unquote(url[len(prefix):])]
        raise AssertionError(f"unexpected URL {url}")

    def raw_fetches(self):
        return [u for u in self.urls if u.startswith(updater.RAW)]

    def api_calls(self):
        return [u for u in self.urls if u.startswith(updater.API)]


def _write(base, rel, data):
    p = os.path.join(str(base), *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def _read(base, rel):
    with open(os.path.join(str(base), *rel.split("/")), "rb") as f:
        return f.read()


def _auto_update(base, on):
    _write(base, "config.yaml",
           ("auto_update: " + ("true" if on else "false") + "\n").encode())


# --------------------------------------------------------------------------
# auto_update: false and the manual override

def test_auto_update_off_is_honoured_by_default(tmp_path, monkeypatch):
    _auto_update(tmp_path, False)
    gh = FakeGitHub({"app.py": b"print('new')\n"})
    monkeypatch.setattr(updater, "_get", gh)
    changed, msg = updater.check_and_update(str(tmp_path))
    assert changed is False
    assert "auto_update: false" in msg and "Update WITNESS.bat" in msg
    assert gh.urls == []                       # did not even ask GitHub
    assert not os.path.exists(tmp_path / "app.py")


def test_force_ignores_auto_update_off(tmp_path, monkeypatch):
    """The double-clicked Update WITNESS.bat is the opted-out user's only way to
    update a ZIP install (there is no .git to `git pull`)."""
    _auto_update(tmp_path, False)
    gh = FakeGitHub({"app.py": b"print('new')\n"})
    monkeypatch.setattr(updater, "_get", gh)
    changed, msg = updater.check_and_update(str(tmp_path), force=True)
    assert changed is True, msg
    assert _read(tmp_path, "app.py") == b"print('new')\n"


def test_positional_signature_is_kept():
    """webserver.py and gui.py call check_and_update(base_dir) positionally."""
    import inspect
    params = list(inspect.signature(updater.check_and_update).parameters.values())
    assert params[0].name == "base_dir"
    assert params[1].name == "force" and params[1].default is False


# --------------------------------------------------------------------------
# Download only what differs

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256))
BAT = b"@echo off\nREM launcher\npython main.py\npause\n"
REPO = {
    "main.py": b"print('main')\n",
    "games/marathon.yaml": b"name: marathon\n",
    "START Kill Recorder.bat": BAT,
    "witness_logo.png": PNG,
    "witness_splash.gif": b"GIF89a\x01\x00\x01\x00\n\r\n",
    "witness.ico": b"\x00\x00\x01\x00\n",
    "config.yaml": b"auto_update: true\ngamertag: \"\"\n",
}


def _crlf(b):
    return b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def _fresh_zip(base, repo=REPO):
    """What extracting GitHub's ZIP gives you: .gitattributes turns the .bat
    files to CRLF; everything else is byte-identical; no .app_version."""
    for path, data in repo.items():
        _write(base, path, _crlf(data) if path.endswith(".bat") else data)


def _snapshot(base):
    out = {}
    for root, _dirs, files in os.walk(str(base)):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, str(base))] = (fh.read(), os.stat(p).st_mtime_ns)
    return out


def test_blob_sha_is_gits_object_id():
    assert updater._blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert updater._blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"
    for data in (BAT, PNG, b"x" * 100000):
        assert updater._blob_sha(data) == git_blob_sha(data)


def test_fresh_zip_downloads_nothing_and_writes_nothing(tmp_path, monkeypatch):
    """The first launch of a fresh ZIP: two API calls, zero raw downloads, no
    rewritten .bat (cmd.exe is executing one of them), no relaunch."""
    _fresh_zip(tmp_path)
    before = _snapshot(tmp_path)
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    changed, msg = updater.check_and_update(str(tmp_path))
    assert changed is False, msg
    assert msg.startswith("Up to date")
    assert gh.raw_fetches() == []
    assert len(gh.api_calls()) == 2
    after = _snapshot(tmp_path)
    after.pop(updater.VERSION_FILE)
    assert after == before                      # same bytes, same mtimes
    assert _read(tmp_path, "START Kill Recorder.bat") == _crlf(BAT)
    assert _read(tmp_path, updater.VERSION_FILE).decode() == LATEST


def test_crlf_bat_matching_the_tree_is_not_downloaded(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    updater.check_and_update(str(tmp_path))
    assert not any("Kill%20Recorder.bat" in u for u in gh.urls)


def test_lf_bat_from_the_old_updater_is_left_alone(tmp_path, monkeypatch):
    """Stan's install: the old updater already rewrote his .bat files as LF.
    They match the tree byte for byte and must not churn back to CRLF."""
    _fresh_zip(tmp_path)
    _write(tmp_path, "START Kill Recorder.bat", BAT)
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    changed, _ = updater.check_and_update(str(tmp_path))
    assert changed is False
    assert _read(tmp_path, "START Kill Recorder.bat") == BAT


def test_matching_binary_is_skipped(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    updater.check_and_update(str(tmp_path))
    assert not any(u.endswith("witness_logo.png") for u in gh.urls)


def test_binary_is_never_line_ending_folded(tmp_path, monkeypatch):
    """A PNG whose LF bytes were turned into CRLF is CORRUPT, not unchanged.
    Folding binaries would hide that, so only text files are folded."""
    _fresh_zip(tmp_path)
    _write(tmp_path, "witness_logo.png", PNG.replace(b"\n", b"\r\n"))
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    changed, msg = updater.check_and_update(str(tmp_path))
    assert changed is True, msg
    assert _read(tmp_path, "witness_logo.png") == PNG


def test_one_changed_py_is_the_only_download(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    repo = dict(REPO, **{"main.py": b"print('fixed')\n"})
    gh = FakeGitHub(repo)
    monkeypatch.setattr(updater, "_get", gh)
    changed, msg = updater.check_and_update(str(tmp_path))
    assert changed is True
    assert gh.raw_fetches() == [f"{updater.RAW}/{LATEST}/main.py"]
    assert _read(tmp_path, "main.py") == b"print('fixed')\n"
    assert "main.py" in msg and "1 file" in msg
    assert _read(tmp_path, updater.VERSION_FILE).decode() == LATEST


def test_a_crlf_py_that_really_changed_is_downloaded(tmp_path, monkeypatch):
    """Folding must not make a CHANGED text file look unchanged."""
    _fresh_zip(tmp_path)
    _write(tmp_path, "main.py", b"print('old')\r\n")
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    changed, _ = updater.check_and_update(str(tmp_path))
    assert changed is True
    assert _read(tmp_path, "main.py") == REPO["main.py"]


def test_missing_file_is_downloaded(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    os.remove(tmp_path / "games" / "marathon.yaml")
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    changed, _ = updater.check_and_update(str(tmp_path))
    assert changed is True
    assert gh.raw_fetches() == [f"{updater.RAW}/{LATEST}/games/marathon.yaml"]
    assert _read(tmp_path, "games/marathon.yaml") == REPO["games/marathon.yaml"]


def test_existing_config_yaml_is_never_fetched_or_written(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    mine = b"auto_update: true\ngamertag: MRVIZNASTY\nweb_port: 8123\n"
    _write(tmp_path, "config.yaml", mine)
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    updater.check_and_update(str(tmp_path))
    assert not any(u.endswith("config.yaml") for u in gh.raw_fetches())
    assert _read(tmp_path, "config.yaml") == mine


def test_config_yaml_is_written_on_first_install(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    os.remove(tmp_path / "config.yaml")
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    updater.check_and_update(str(tmp_path))
    assert _read(tmp_path, "config.yaml") == REPO["config.yaml"]


def test_a_changed_bat_is_written_with_crlf(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    new_bat = b"@echo off\nREM launcher v2\npython main.py\npause\n"
    gh = FakeGitHub(dict(REPO, **{"START Kill Recorder.bat": new_bat}))
    monkeypatch.setattr(updater, "_get", gh)
    changed, _ = updater.check_and_update(str(tmp_path))
    assert changed is True
    on_disk = _read(tmp_path, "START Kill Recorder.bat")
    assert on_disk == _crlf(new_bat)
    assert b"\n" not in on_disk.replace(b"\r\n", b"")     # no bare LF left


def test_line_ending_only_difference_is_never_written(tmp_path, monkeypatch):
    """Belt and braces behind the sha check: even if the tree's sha does not
    match, a download that differs from the local file only in line endings
    is not written (and does not trigger a relaunch)."""
    _fresh_zip(tmp_path)
    _write(tmp_path, "main.py", _crlf(REPO["main.py"]))
    before = _snapshot(tmp_path)["main.py"]
    gh = FakeGitHub(REPO, tree_sha={"main.py": "0" * 40})
    monkeypatch.setattr(updater, "_get", gh)
    changed, _ = updater.check_and_update(str(tmp_path))
    assert changed is False
    assert f"{updater.RAW}/{LATEST}/main.py" in gh.raw_fetches()
    assert _snapshot(tmp_path)["main.py"] == before


def test_already_on_latest_costs_one_api_call(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    _write(tmp_path, updater.VERSION_FILE, LATEST.encode())
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    changed, msg = updater.check_and_update(str(tmp_path))
    assert changed is False and msg.startswith("Up to date")
    assert len(gh.urls) == 1


def test_missing_required_asset_resyncs_even_on_latest(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    _write(tmp_path, updater.VERSION_FILE, LATEST.encode())
    os.remove(tmp_path / "witness_splash.gif")
    gh = FakeGitHub(REPO)
    monkeypatch.setattr(updater, "_get", gh)
    changed, _ = updater.check_and_update(str(tmp_path))
    assert changed is True
    assert gh.raw_fetches() == [f"{updater.RAW}/{LATEST}/witness_splash.gif"]


def test_a_failed_download_writes_nothing(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    repo = dict(REPO, **{"main.py": b"print('fixed')\n",
                         "games/marathon.yaml": b"name: v2\n"})
    gh = FakeGitHub(repo)

    def flaky(url, timeout=10):
        if url.endswith("marathon.yaml"):
            raise OSError("connection reset")
        return gh(url, timeout)

    monkeypatch.setattr(updater, "_get", flaky)
    before = _snapshot(tmp_path)
    changed, msg = updater.check_and_update(str(tmp_path))
    assert changed is False and "nothing changed" in msg
    assert _snapshot(tmp_path) == before        # no .app_version either


def _git(*args, **kw):
    import subprocess
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run(["git"] + list(args), cwd=base, capture_output=True,
                          check=True, **kw).stdout


def test_real_repo_blobs_hash_to_gits_own_ids(tmp_path):
    """Against git itself, not our oracle: every tracked .bat (as the CRLF a
    ZIP gives you) and every tracked .png in HEAD must count as unchanged
    against the sha git lists for it, the same sha GitHub's tree API returns."""
    try:
        listing = _git("ls-tree", "-r", "-z", "HEAD").decode("utf-8", "replace")
    except Exception:
        pytest.skip("not a git checkout (ZIP install)")
    checked = 0
    for line in filter(None, listing.split("\0")):
        meta, path = line.split("\t", 1)
        _mode, kind, sha = meta.split()
        if kind != "blob" or not path.lower().endswith((".bat", ".png")):
            continue
        blob = _git("cat-file", "blob", sha)
        local = _crlf(blob) if path.lower().endswith(".bat") else blob
        dest = _write(tmp_path, os.path.basename(path), local)
        assert updater._local_matches(dest, path, sha), path
        checked += 1
    assert checked >= 5


# --------------------------------------------------------------------------
# ABORT_WRITES, LAST_CHANGED_FILES, one run at a time

def _two_changes():
    return dict(REPO, **{"main.py": b"print('fixed')\n",
                         "games/marathon.yaml": b"name: v2\n"})


def test_abort_writes_set_means_zero_files_written(tmp_path, monkeypatch):
    """The desktop splash gave up waiting and the app is loading: nothing may
    be swapped under it."""
    _fresh_zip(tmp_path)
    gh = FakeGitHub(_two_changes())

    def get_then_abort(url, timeout=10):
        data = gh(url, timeout)
        if url.startswith(updater.RAW):
            updater.ABORT_WRITES.set()          # cap hit mid-download
        return data

    monkeypatch.setattr(updater, "_get", get_then_abort)
    before = _snapshot(tmp_path)
    changed, msg = updater.check_and_update(str(tmp_path))
    assert (changed, msg) == (False, "Update deferred to next launch")
    assert _snapshot(tmp_path) == before        # not even .app_version
    assert len(gh.raw_fetches()) == 1           # stopped downloading too
    assert updater.LAST_CHANGED_FILES == []
    assert not updater.ABORT_WRITES.is_set()    # the next check runs normally


def test_abort_set_before_the_run_writes_nothing(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    gh = FakeGitHub(_two_changes())
    monkeypatch.setattr(updater, "_get", gh)
    updater.ABORT_WRITES.set()
    before = _snapshot(tmp_path)
    try:
        changed, msg = updater.check_and_update(str(tmp_path))
    finally:
        updater.ABORT_WRITES.clear()
    assert changed is False and msg == "Update deferred to next launch"
    assert _snapshot(tmp_path) == before


def test_abort_between_writes_stops_and_says_so(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    gh = FakeGitHub(_two_changes())
    monkeypatch.setattr(updater, "_get", gh)
    real = updater._for_disk

    def write_one_then_abort(path, data):
        updater.ABORT_WRITES.set()
        return real(path, data)

    monkeypatch.setattr(updater, "_for_disk", write_one_then_abort)
    changed, msg = updater.check_and_update(str(tmp_path))
    assert msg.startswith("Update deferred to next launch")
    assert "1 of 2" in msg and changed is True
    assert len(updater.LAST_CHANGED_FILES) == 1
    assert not os.path.exists(tmp_path / updater.VERSION_FILE)
    assert not updater.ABORT_WRITES.is_set()


def test_last_changed_files_lists_what_was_written(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    monkeypatch.setattr(updater, "_get", FakeGitHub(_two_changes()))
    updater.check_and_update(str(tmp_path))
    assert sorted(updater.LAST_CHANGED_FILES) == ["games/marathon.yaml", "main.py"]
    monkeypatch.setattr(updater, "_get", FakeGitHub(_two_changes()))
    updater.check_and_update(str(tmp_path))     # now current
    assert updater.LAST_CHANGED_FILES == []


def test_a_second_concurrent_run_is_refused(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    gh = FakeGitHub(_two_changes())
    monkeypatch.setattr(updater, "_get", gh)
    assert updater._RUN_LOCK.acquire(blocking=False)
    try:
        changed, msg = updater.check_and_update(str(tmp_path))
    finally:
        updater._RUN_LOCK.release()
    assert changed is False and "already running" in msg
    assert gh.urls == []


def test_an_unwritable_file_does_not_strand_the_rest(tmp_path, monkeypatch):
    """A locked file used to raise out of the write loop half way through.
    Now the others are written, the failure is named, and .app_version stays
    old so the next launch retries just that file."""
    _fresh_zip(tmp_path)
    monkeypatch.setattr(updater, "_get", FakeGitHub(_two_changes()))
    real_open = open

    def locked(path, mode="r", *a, **k):
        if "w" in mode and str(path).endswith("marathon.yaml"):
            raise PermissionError(13, "Permission denied")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", locked)
    changed, msg = updater.check_and_update(str(tmp_path))
    monkeypatch.undo()
    assert changed is True
    assert "could not write games/marathon.yaml" in msg
    assert updater.LAST_CHANGED_FILES == ["main.py"]
    assert _read(tmp_path, "main.py") == b"print('fixed')\n"
    assert not os.path.exists(tmp_path / updater.VERSION_FILE)


# --------------------------------------------------------------------------
# New dependencies install themselves

class FakeRun:
    def __init__(self, returncode=0, stdout=b"", stderr=b"", exc=None):
        self.returncode, self.stdout, self.stderr, self.exc = (
            returncode, stdout, stderr, exc)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.exc:
            raise self.exc
        import subprocess
        return subprocess.CompletedProcess(cmd, self.returncode,
                                           self.stdout, self.stderr)


def _in_venv(monkeypatch):
    monkeypatch.setattr(sys, "base_prefix", sys.prefix + "-base", raising=False)


def test_ensure_requirements_installs_missing_only(tmp_path, monkeypatch):
    import subprocess
    _write(tmp_path, "requirements.txt", b"PyYAML>=6.0\n")
    _in_venv(monkeypatch)
    run = FakeRun(0)
    monkeypatch.setattr(subprocess, "run", run)
    ok, note = updater.ensure_requirements(str(tmp_path))
    assert ok is True and "installed" in note
    (cmd, kw), = run.calls
    assert cmd == [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
    assert not any("upgrade" in a for a in cmd)   # never touch loaded .pyd files
    assert kw["cwd"] == str(tmp_path)
    assert kw["timeout"] and kw["timeout"] > 0
    assert "creationflags" in kw                   # CREATE_NO_WINDOW on Windows
    assert kw.get("stdin") is subprocess.DEVNULL   # pip can never wait on input


def test_ensure_requirements_reports_pips_error_not_its_notice(tmp_path, monkeypatch):
    import subprocess
    _write(tmp_path, "requirements.txt", b"nosuchpkg\n")
    _in_venv(monkeypatch)
    run = FakeRun(1, stderr=(b"ERROR: No matching distribution found for nosuchpkg\n"
                             b"[notice] A new release of pip is available\n"))
    monkeypatch.setattr(subprocess, "run", run)
    ok, note = updater.ensure_requirements(str(tmp_path))
    assert ok is False
    assert "No matching distribution" in note and "notice" not in note
    assert ".bat" in note                          # says how it gets retried


def test_ensure_requirements_survives_a_timeout(tmp_path, monkeypatch):
    import subprocess
    _write(tmp_path, "requirements.txt", b"PyYAML\n")
    _in_venv(monkeypatch)
    monkeypatch.setattr(subprocess, "run",
                        FakeRun(exc=subprocess.TimeoutExpired("pip", 900)))
    ok, note = updater.ensure_requirements(str(tmp_path))
    assert ok is False and "did NOT install" in note


def test_ensure_requirements_never_touches_a_system_python(tmp_path, monkeypatch):
    import subprocess
    _write(tmp_path, "requirements.txt", b"PyYAML\n")
    monkeypatch.setattr(sys, "base_prefix", sys.prefix, raising=False)
    run = FakeRun(0)
    monkeypatch.setattr(subprocess, "run", run)
    ok, note = updater.ensure_requirements(str(tmp_path))
    assert ok is False and run.calls == []
    assert "pip install -r requirements.txt" in note


def test_requirements_change_note_no_longer_asks_for_a_pip_command(tmp_path, monkeypatch):
    _fresh_zip(tmp_path)
    _write(tmp_path, "requirements.txt", b"PyYAML>=6.0\n")
    repo = dict(REPO, **{"requirements.txt": b"PyYAML>=6.0\nzeroconf>=0.130\n"})
    monkeypatch.setattr(updater, "_get", FakeGitHub(repo))
    changed, msg = updater.check_and_update(str(tmp_path))
    assert changed is True
    assert "new components install automatically" in msg
    assert "pip install" not in msg
    assert updater.LAST_CHANGED_FILES == ["requirements.txt"]


class Relaunch:
    """Records how the launcher would restart itself."""

    def __init__(self):
        self.popen = []
        self.call = []

    def Popen(self, cmd, **kw):
        self.popen.append((cmd, kw))

    def call_(self, cmd, **kw):
        self.call.append((cmd, kw))
        return 7


def _fake_update(monkeypatch, changed, msg, files):
    # Restored after the test, so a fake list never leaks into the next one.
    monkeypatch.setattr(updater, "LAST_CHANGED_FILES", [])

    def fake(base_dir, force=False):
        updater.LAST_CHANGED_FILES = list(files)
        return changed, msg
    monkeypatch.setattr(updater, "check_and_update", fake)


def _gui_process(monkeypatch):
    """Look like pythonw.exe: no console, so the old Popen + exit path."""
    monkeypatch.setattr(sys, "executable", r"C:\WITNESS\.venv\Scripts\pythonw.exe")
    monkeypatch.setattr(sys, "stdin", None)


def test_relaunch_installs_new_requirements_first(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.delenv("MKR_UPDATED", raising=False)
    monkeypatch.delenv("MKR_UPDATE_NOTE", raising=False)
    _fake_update(monkeypatch, True, "Updated 2 file(s) to ccccccc",
                 ["main.py", "requirements.txt"])
    seen = []
    monkeypatch.setattr(updater, "ensure_requirements",
                        lambda b: seen.append(b) or (True, "New components installed."))
    rl = Relaunch()
    monkeypatch.setattr(subprocess, "Popen", rl.Popen)
    _gui_process(monkeypatch)
    with pytest.raises(SystemExit) as ex:
        updater.update_and_relaunch_if_needed(str(tmp_path), ["gui.py"])
    assert ex.value.code == 0
    assert seen == [str(tmp_path)]
    (cmd, kw), = rl.popen
    assert cmd == [sys.executable, "gui.py"]
    assert kw["env"]["MKR_UPDATED"] == "1"
    assert kw["env"]["MKR_UPDATE_NOTE"] == "New components installed."
    status = _read(tmp_path, "update_status.txt").decode()
    assert "Updated 2 file(s)" in status and "New components installed." in status


def test_relaunch_without_requirements_change_skips_pip(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.delenv("MKR_UPDATED", raising=False)
    monkeypatch.delenv("MKR_UPDATE_NOTE", raising=False)
    _fake_update(monkeypatch, True, "Updated 1 file(s)", ["main.py"])
    monkeypatch.setattr(updater, "ensure_requirements",
                        lambda b: pytest.fail("pip must not run"))
    rl = Relaunch()
    monkeypatch.setattr(subprocess, "Popen", rl.Popen)
    _gui_process(monkeypatch)
    with pytest.raises(SystemExit):
        updater.update_and_relaunch_if_needed(str(tmp_path), ["gui.py"])
    (_cmd, kw), = rl.popen
    assert "MKR_UPDATE_NOTE" not in kw["env"]


def test_the_relaunched_run_appends_the_note_instead_of_overwriting(tmp_path, monkeypatch):
    """The updating process wrote what changed; the relaunched one used to
    overwrite that with 'Restarted...' and the pip result was lost."""
    _write(tmp_path, "update_status.txt",
           b"2026-10-02 21:00:00\nUpdated 2 file(s) to ccccccc: main.py, requirements.txt\n")
    monkeypatch.setenv("MKR_UPDATED", "1")
    monkeypatch.setenv("MKR_UPDATE_NOTE", "New components installed.")
    monkeypatch.setattr(updater, "check_and_update",
                        lambda *a, **k: pytest.fail("must not update again (loop)"))
    msg = updater.update_and_relaunch_if_needed(str(tmp_path))
    assert "New components installed." in msg
    status = _read(tmp_path, "update_status.txt").decode()
    assert "Updated 2 file(s)" in status                     # kept
    assert "Restarted on the latest version (update applied). New components installed." in status
    assert "MKR_UPDATED" not in os.environ and "MKR_UPDATE_NOTE" not in os.environ


# --------------------------------------------------------------------------
# Console launchers wait for the relaunched run

class _Tty:
    def isatty(self):
        return True


def _console_process(monkeypatch):
    monkeypatch.setattr(sys, "executable", r"C:\WITNESS\.venv\Scripts\python.exe")
    monkeypatch.setattr(sys, "stdin", _Tty())


def _patch_relaunch(monkeypatch):
    import signal
    import subprocess
    rl = Relaunch()
    monkeypatch.setattr(subprocess, "Popen", rl.Popen)
    monkeypatch.setattr(subprocess, "call", rl.call_)
    handlers = []
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.append((sig, h)))
    monkeypatch.delenv("MKR_UPDATED", raising=False)
    monkeypatch.delenv("MKR_UPDATE_NOTE", raising=False)
    monkeypatch.setattr(updater, "ensure_requirements",
                        lambda b: pytest.fail("pip must not run"))
    _fake_update(monkeypatch, True, "Updated 1 file(s)", ["main.py"])
    return rl, handlers


def test_console_parent_waits_and_exits_with_the_childs_code(tmp_path, monkeypatch):
    """'4 - Benchmark' printed its VERDICT from the child while the .bat had
    already reached `pause` (parent gone). Now the parent waits."""
    import signal
    rl, handlers = _patch_relaunch(monkeypatch)
    _console_process(monkeypatch)
    with pytest.raises(SystemExit) as ex:
        updater.update_and_relaunch_if_needed(str(tmp_path), ["main.py", "--bench"])
    assert ex.value.code == 7
    assert rl.popen == []
    (cmd, kw), = rl.call
    assert cmd == [sys.executable, "main.py", "--bench"]
    assert kw["cwd"] == str(tmp_path) and kw["env"]["MKR_UPDATED"] == "1"
    (sig, handler), = handlers
    assert sig == signal.SIGINT
    assert callable(handler) and handler is not signal.SIG_IGN


def test_gui_process_keeps_popen_and_exit(tmp_path, monkeypatch):
    rl, handlers = _patch_relaunch(monkeypatch)
    _gui_process(monkeypatch)
    with pytest.raises(SystemExit) as ex:
        updater.update_and_relaunch_if_needed(str(tmp_path), ["gui.py"])
    assert ex.value.code == 0
    assert rl.call == [] and len(rl.popen) == 1 and handlers == []


def test_console_but_not_main_thread_does_not_block(tmp_path, monkeypatch):
    """gui.py runs its update check on a worker thread. Waiting there would
    keep the old window up next to the new one (and signal.signal raises
    off the main thread), so that path keeps Popen + exit."""
    import threading
    rl, handlers = _patch_relaunch(monkeypatch)
    _console_process(monkeypatch)
    out = {}

    def worker():
        try:
            updater.update_and_relaunch_if_needed(str(tmp_path), ["gui.py"])
        except SystemExit as e:
            out["code"] = e.code

    t = threading.Thread(target=worker)
    t.start()
    t.join(5)
    assert out == {"code": 0}
    assert rl.call == [] and len(rl.popen) == 1 and handlers == []


def test_console_detection():
    import types
    real = (sys.executable, sys.stdin)
    try:
        sys.executable = r"C:\x\.venv\Scripts\pythonw.exe"
        sys.stdin = _Tty()
        assert updater._console_attached() is False
        sys.executable = r"C:\x\.venv\Scripts\python.exe"
        sys.stdin = None
        assert updater._console_attached() is False
        sys.stdin = types.SimpleNamespace(isatty=lambda: False)
        assert updater._console_attached() is False
        sys.stdin = _Tty()
        assert updater._console_attached() is True
    finally:
        sys.executable, sys.stdin = real


def test_the_relaunched_child_still_gets_ctrl_c():
    """SIG_IGN would be inherited across exec on POSIX and Python keeps it, so
    the relaunched app would ignore Ctrl-C too. Run the real thing in a
    throwaway interpreter (never touch this test runner's SIGINT)."""
    import subprocess
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    child = ("import signal; print(signal.getsignal(signal.SIGINT) "
             "is signal.default_int_handler)")
    parent = ("import sys, subprocess, updater; updater._ignore_ctrl_c(); "
              f"sys.stdout.write(subprocess.check_output([sys.executable, '-c', {child!r}]).decode())")
    out = subprocess.run([sys.executable, "-c", parent], cwd=repo,
                         capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == b"True"


def test_a_console_that_cannot_print_does_not_stop_the_relaunch(monkeypatch):
    class Cp1252Console:
        def write(self, s):
            s.encode("cp1252")                 # raises on characters it lacks
            return len(s)

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", Cp1252Console())
    updater._say("Updated 1 file(s): ☠.py")   # must not raise
    monkeypatch.setattr(sys, "stdout", None)        # pythonw
    updater._say("Updated 1 file(s)")
