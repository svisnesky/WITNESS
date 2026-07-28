"""Self-updater — keeps the app current with GitHub automatically.

On launch, compares the local version (.app_version, the last-synced commit
sha) with the repo's latest commit. If newer, downloads every repo file,
swaps in the ones that changed, and reports what happened so the launcher can
relaunch with fresh code. No git needed, stdlib only.

Never touched: settings_override.yaml (your dashboard settings), logs/,
music/, client_secret.json / youtube_token.json, .venv — anything not
tracked in the repo. Turn the whole thing off with auto_update: false in
config.yaml.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

REPO = "svisnesky/WITNESS"   # GitHub redirects the old Marathon-OBS name too
BRANCH = "main"
API = f"https://api.github.com/repos/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}"
VERSION_FILE = ".app_version"

# Only files with these extensions are managed by the updater.
EXTS = (".py", ".yaml", ".md", ".txt", ".bat", ".png", ".wav", ".gif", ".ico")

# Binary assets that must exist locally. If any is missing we re-sync even
# when the version sha matches — self-heals installs whose OLD updater had a
# narrower EXTS whitelist and skipped these (e.g. the splash gif + app icon).
REQUIRED = ("witness_splash.gif", "witness.ico")
# Repo files that must never overwrite local state.
SKIP = {"settings_override.yaml", "session_log.csv"}
# Written on first install only, then left alone — a user's edits (OBS
# password, tuning) must survive updates. New options ship as code defaults.
SKIP_IF_EXISTS = {"config.yaml"}


def _why(exc) -> str:
    """Plain-English reason a fetch failed, so the log points at the real cause.

    'offline?' used to cover every failure, which is actively misleading: the
    most likely cause is the repo being PRIVATE. The updater fetches
    anonymously on purpose (that is what lets it work on any user's machine),
    and GitHub answers 404 for a private repo rather than 403, so it looks
    identical to a missing repo."""
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        if code == 404:
            return (f"GitHub returned 404 for {REPO}. The repo is PRIVATE or the "
                    "name changed. The updater fetches anonymously, so the repo "
                    "must be public for auto-update to work.")
        if code in (403, 429):
            reset = ""
            try:
                import time as _t
                epoch = int(exc.headers.get("X-RateLimit-Reset", "0"))
                if epoch:
                    reset = (" Resets at "
                             + _t.strftime('%H:%M', _t.localtime(epoch)) + ".")
            except Exception:
                pass
            if str(exc.headers.get("X-RateLimit-Remaining", "")) == "0":
                return f"GitHub API rate limit reached (60/hour).{reset}"
            return f"GitHub refused the request (HTTP {code}).{reset}"
        return f"GitHub returned HTTP {code}."
    if isinstance(exc, urllib.error.URLError):
        return f"no connection to GitHub ({exc.reason})."
    return f"{type(exc).__name__}: {exc}"


def _get(url: str, timeout: float = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Marathon-Kill-Recorder"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _auto_update_enabled(base_dir: str) -> bool:
    """Read auto_update from config.yaml without importing the app."""
    try:
        import yaml
        with open(os.path.join(base_dir, "config.yaml"), encoding="utf-8") as f:
            return bool(yaml.safe_load(f).get("auto_update", True))
    except Exception:
        return True


def check_and_update(base_dir: str) -> tuple[bool, str]:
    """Returns (files_changed, message). Quietly no-ops when offline or when
    auto_update: false. All downloads complete before anything is written, so
    a dropped connection can't leave the app half-updated."""
    if not _auto_update_enabled(base_dir):
        return False, "Auto-update is off (auto_update: false)."
    try:
        latest = json.loads(_get(f"{API}/commits/{BRANCH}", timeout=6))["sha"]
    except Exception as e:
        return False, f"Update check skipped — {_why(e)}"

    ver_path = os.path.join(base_dir, VERSION_FILE)
    local = ""
    if os.path.exists(ver_path):
        try:
            with open(ver_path, encoding="utf-8") as f:
                local = f.read().strip()
        except Exception:
            local = ""
    missing = [f for f in REQUIRED
               if not os.path.exists(os.path.join(base_dir, f))]
    if local == latest and not missing:
        return False, f"Up to date ({latest[:7]})."

    try:
        tree = json.loads(_get(f"{API}/git/trees/{latest}?recursive=1", timeout=10))
        paths = [e["path"] for e in tree.get("tree", [])
                 if e.get("type") == "blob"
                 and e["path"].lower().endswith(EXTS)
                 and os.path.basename(e["path"]) not in SKIP]
        if not paths:
            return False, "Update check failed: repo file list came back empty."
        # Download EVERYTHING first; only then write.
        fetched = []
        for p in paths:
            fetched.append((p, _get(f"{RAW}/{latest}/{urllib.parse.quote(p)}", timeout=30)))
    except Exception as e:
        return False, f"Update aborted, nothing changed: {type(e).__name__}: {e}"

    changed = []
    for path, data in fetched:
        dest = os.path.join(base_dir, path.replace("/", os.sep))
        if os.path.basename(path) in SKIP_IF_EXISTS and os.path.exists(dest):
            continue  # user-owned file: never overwrite after first install
        try:
            with open(dest, "rb") as f:
                if f.read() == data:
                    continue
        except OSError:
            pass  # new file
        os.makedirs(os.path.dirname(dest) or base_dir, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        changed.append(path)

    with open(ver_path, "w", encoding="utf-8") as f:
        f.write(latest)

    if not changed:
        return False, f"Up to date ({latest[:7]})."
    msg = f"Updated {len(changed)} file(s) to {latest[:7]}: {', '.join(changed[:6])}"
    if len(changed) > 6:
        msg += f" (+{len(changed) - 6} more)"
    if "requirements.txt" in changed:
        msg += "  NOTE: requirements.txt changed - run: .venv\\Scripts\\python -m pip install -r requirements.txt"
    return True, msg


def _write_status(base_dir: str, msg: str) -> None:
    """Persist the last update outcome to update_status.txt for diagnosis
    (the webview has no console to print to)."""
    try:
        import time
        with open(os.path.join(base_dir, "update_status.txt"), "w",
                  encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{msg}\n")
    except Exception:
        pass


def update_and_relaunch_if_needed(base_dir: str, argv=None) -> str:
    """For launchers: run the update; if code changed, relaunch this process
    so the fresh files actually load. Returns the status message (only when
    no relaunch was needed — otherwise the process is replaced)."""
    import subprocess
    import sys
    if os.environ.get("MKR_UPDATED") == "1":
        # Just relaunched after an update — don't loop.
        os.environ.pop("MKR_UPDATED", None)
        _write_status(base_dir, "Restarted on the latest version (update applied).")
        return "Restarted on the latest version."
    changed, msg = check_and_update(base_dir)
    _write_status(base_dir, msg)
    if changed:
        env = dict(os.environ, MKR_UPDATED="1")
        subprocess.Popen([sys.executable] + list(argv or sys.argv),
                         cwd=base_dir, env=env)
        sys.exit(0)
    return msg
