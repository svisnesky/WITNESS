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
