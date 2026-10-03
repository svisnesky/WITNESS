"""Self-updater — keeps the app current with GitHub automatically.

On launch, compares the local version (.app_version, the last-synced commit
sha) with the repo's latest commit. If newer, it asks GitHub for the file list
of that commit, which carries each file's git blob sha, hashes the local copy
the same way, and downloads ONLY the files that differ or are missing. Then it
swaps those in and reports what happened so the launcher can relaunch with
fresh code. No git needed, stdlib only.

Line endings: git stores text files with LF and raw.githubusercontent serves
them that way, but a GitHub ZIP (and .gitattributes) gives the .bat files CRLF.
Text files are therefore compared with line endings normalized, so a fresh ZIP
costs two API calls and writes nothing, and a .bat that really changed is
written with CRLF, the way cmd.exe wants it.

Never touched: settings_override.yaml (your dashboard settings), logs/,
music/, client_secret.json / youtube_token.json, .venv — anything not
tracked in the repo. Turn the whole thing off with auto_update: false in
config.yaml.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
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
# Compared with CRLF folded to LF. Binary assets are compared byte for byte:
# folding them could call a changed image "unchanged".
TEXT_EXTS = (".py", ".yaml", ".md", ".txt", ".bat")


def _is_text(path: str) -> bool:
    return path.lower().endswith(TEXT_EXTS)


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _blob_sha(data: bytes) -> str:
    """git's object id for a blob: what the tree API lists as each file's sha."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _local_matches(dest: str, path: str, sha) -> bool:
    """True when the file on disk already holds the blob GitHub lists for this
    path, either byte for byte or (text only) once CRLF is folded to LF.
    Anything unreadable, missing or without a sha counts as changed: a false
    "changed" costs one download, a false "unchanged" strands the user on
    stale code."""
    if not sha:
        return False
    try:
        with open(dest, "rb") as f:
            local = f.read()
    except OSError:
        return False
    if _blob_sha(local) == sha:
        return True
    if _is_text(path):
        folded = _lf(local)
        return folded != local and _blob_sha(folded) == sha
    return False


def _same_content(path: str, old: bytes, new: bytes) -> bool:
    if _is_text(path):
        return _lf(old) == _lf(new)
    return old == new


def _for_disk(path: str, data: bytes) -> bytes:
    """.bat files are written with CRLF (cmd.exe); everything else exactly as
    GitHub serves it."""
    if path.lower().endswith(".bat"):
        return _lf(data).replace(b"\n", b"\r\n")
    return data


def _dest(base_dir: str, path: str) -> str:
    return os.path.join(base_dir, path.replace("/", os.sep))


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


AUTO_UPDATE_OFF_MSG = ("Auto-update is off (auto_update: false). Double-click "
                       "'Update WITNESS.bat' to update manually.")
DEFERRED_MSG = "Update deferred to next launch"

# Set by a launcher that has stopped waiting (the desktop splash gives up after
# its cap and goes on to load the app). The run in flight then writes nothing
# more: files swapped under code that is already loading would mix old and new
# modules. Checked during and after the downloads and before every write;
# cleared when that run ends, so a later "Check for update" works normally.
ABORT_WRITES = threading.Event()

# Repo paths the most recent run wrote (e.g. to spot requirements.txt).
LAST_CHANGED_FILES: list = []

# One run at a time: a dashboard click while the launch check is still
# downloading must not start a second writer.
_RUN_LOCK = threading.Lock()


def check_and_update(base_dir: str, force: bool = False) -> tuple[bool, str]:
    """Returns (files_changed, message). Quietly no-ops when offline or when
    auto_update: false. All downloads complete before anything is written, so
    a dropped connection can't leave the app half-updated.

    force=True ignores auto_update: false. Only the double-clicked
    'Update WITNESS.bat' passes it: that is a deliberate local action. The
    launchers and the dashboard button keep honouring the opt-out, because the
    dashboard has no login and a guest on the Wi-Fi must not be able to pull
    new code onto a PC whose owner turned updates off.

    Sets LAST_CHANGED_FILES to the paths it wrote. Honours ABORT_WRITES."""
    global LAST_CHANGED_FILES
    if not force and not _auto_update_enabled(base_dir):
        LAST_CHANGED_FILES = []
        return False, AUTO_UPDATE_OFF_MSG
    if not _RUN_LOCK.acquire(blocking=False):
        return False, "An update check is already running."
    try:
        LAST_CHANGED_FILES = []
        written = []
        try:
            return _sync(base_dir, written)
        finally:
            LAST_CHANGED_FILES = list(written)
            ABORT_WRITES.clear()
    finally:
        _RUN_LOCK.release()


def _sync(base_dir: str, changed: list) -> tuple[bool, str]:
    """The body of check_and_update. Appends each path it writes to changed."""
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
        entries = [e for e in tree.get("tree", [])
                   if e.get("type") == "blob"
                   and e["path"].lower().endswith(EXTS)
                   and os.path.basename(e["path"]) not in SKIP]
        if not entries:
            return False, "Update check failed: repo file list came back empty."
        # Only what differs. A fresh ZIP (no .app_version) used to re-download
        # all ~130 files one connection at a time and then relaunch, purely
        # because its .bat files are CRLF and GitHub serves LF.
        todo = []
        for e in entries:
            path = e["path"]
            dest = _dest(base_dir, path)
            if os.path.basename(path) in SKIP_IF_EXISTS and os.path.exists(dest):
                continue  # user-owned file: never overwrite after first install
            if _local_matches(dest, path, e.get("sha")):
                continue
            todo.append(path)
        # Download EVERYTHING that differs first; only then write.
        fetched = []
        for p in todo:
            if ABORT_WRITES.is_set():
                return False, DEFERRED_MSG
            fetched.append((p, _get(f"{RAW}/{latest}/{urllib.parse.quote(p)}", timeout=30)))
    except Exception as e:
        return False, f"Update aborted, nothing changed: {type(e).__name__}: {e}"
    if ABORT_WRITES.is_set():
        return False, DEFERRED_MSG

    failed = []
    for path, data in fetched:
        if ABORT_WRITES.is_set():
            # Rare: the launcher gave up mid-write. Say so; .app_version is not
            # written, so the next launch fetches whatever is still missing.
            return bool(changed), (
                f"{DEFERRED_MSG} ({len(changed)} of {len(fetched)} file(s) "
                "were already written; restart WITNESS to finish).")
        dest = _dest(base_dir, path)
        if os.path.basename(path) in SKIP_IF_EXISTS and os.path.exists(dest):
            continue  # user-owned file: never overwrite after first install
        try:
            with open(dest, "rb") as f:
                if _same_content(path, f.read(), data):
                    continue  # differs only in line endings: leave it alone
        except OSError:
            pass  # new file
        try:
            os.makedirs(os.path.dirname(dest) or base_dir, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(_for_disk(path, data))
        except OSError as e:
            # A locked or read-only file must not abandon the rest halfway
            # through with no message. Keep going, report it, and leave
            # .app_version alone so the next launch retries just these.
            failed.append(f"{path} ({e.strerror or type(e).__name__})")
            continue
        changed.append(path)

    # pip is run by update_and_relaunch_if_needed (launch path) or by _env.bat
    # on the next .bat launch (it re-installs whenever requirements.txt no
    # longer matches its stamp), so nobody has to type a pip command.
    req_note = ("  NOTE: requirements.txt changed - new components install "
                "automatically." if "requirements.txt" in changed else "")
    if failed:
        msg = (f"Update incomplete: could not write {', '.join(failed[:3])}"
               + (f" (+{len(failed) - 3} more)" if len(failed) > 3 else "")
               + ". It will retry next launch.")
        if changed:
            msg += f" Updated {len(changed)} other file(s)."
        return bool(changed), msg + req_note

    ver_note = ""
    try:
        with open(ver_path, "w", encoding="utf-8") as f:
            f.write(latest)
    except OSError as e:
        # The files are already in place; only the bookmark failed (antivirus
        # lock, read-only folder). Raising here used to hide changed=True from
        # the launcher, so it ran old code over new files. The next launch
        # re-checks by sha (two API calls) and finds nothing to fetch.
        ver_note = (f"  (could not record the version in {VERSION_FILE}: "
                    f"{e.strerror or type(e).__name__})")

    if not changed:
        return False, f"Up to date ({latest[:7]}).{ver_note}"
    msg = f"Updated {len(changed)} file(s) to {latest[:7]}: {', '.join(changed[:6])}"
    if len(changed) > 6:
        msg += f" (+{len(changed) - 6} more)"
    return True, msg + req_note + ver_note


PIP_TIMEOUT = 900   # seconds; a new small dependency takes well under a minute

# Installed by _env.bat from PyTorch's CUDA index, not from PyPI, and pinned to
# whatever build is installed whenever ensure_requirements runs pip. PyPI's
# Windows torch wheels are CPU-only: if pip ever swapped one in, OCR would
# quietly move off the GPU (Stan's RTX 5080 runs 2.x+cu128).
GPU_PINNED = ("torch", "torchvision", "torchaudio")


def _installed_version(name: str):
    """Version of an installed distribution (e.g. '2.7.1+cu128'), or None."""
    try:
        from importlib import metadata
        return (metadata.version(name) or "").strip() or None
    except Exception:
        return None


def _gpu_pins() -> list:
    """pip constraint lines, e.g. ['torch==2.7.1+cu128'], for each installed
    GPU_PINNED distribution. `==` with the +cu128 label matches only that
    exact build, so the installed one satisfies it and nothing else does."""
    pins = []
    for name in GPU_PINNED:
        version = _installed_version(name)
        if version:
            pins.append(f"{name}=={version}")
    return pins


def _canon(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _held_by_pin(lines: list, pins: list) -> str:
    """When pip refused because a pin held, the line naming what wanted the
    change ('easyocr 1.8 depends on torch>=2.9'), else ''. pip lists the pin
    itself as 'The user requested (constraint) torch==...'."""
    import re
    pinned = {_canon(p.split("==", 1)[0]) for p in pins}
    held = set()
    for ln in lines:
        m = re.search(r"\(constraint\)\s*([A-Za-z0-9_.\-]+)", ln)
        if m and _canon(m.group(1)) in pinned:
            held.add(_canon(m.group(1)))
    if not held:
        return ""
    wants = []
    for ln in lines:
        m = re.search(r"depends on ([A-Za-z0-9_.\-]+)\s*([<>=!~]?)", ln)
        if m and _canon(m.group(1)) in held:
            # Best: a version bound from a package that is not itself pinned
            # ('torchvision 0.22.1 depends on torch==2.7.1' is not the cause).
            rank = (0 if m.group(2) else 2) + (1 if _canon(ln.split()[0]) in pinned else 0)
            wants.append((rank, ln))
    if wants:
        return min(wants, key=lambda w: w[0])[1]
    return f"a requirement needs a different {sorted(held)[0]}"


def ensure_requirements(base_dir: str) -> tuple[bool, str]:
    """Install whatever requirements.txt lists that this venv is missing.

    Plain `pip install -r requirements.txt`, never --upgrade. That alone does
    NOT leave installed packages alone: pip still replaces one whose version a
    requirement excludes (a raised lower bound, or a new package that needs a
    newer version of it). For an ordinary package that is an upgrade from
    PyPI, which is how a fix that needs a newer library arrives. For torch it
    would be PyPI's CPU-only wheel over the CUDA build, so the installed
    GPU_PINNED builds are pinned with `-c`: an update that needs a different
    torch fails here with a note that says so, and the GPU build stays.

    A raised bound on a package this process has already loaded (yaml,
    rapidfuzz) can still fail on a locked .pyd on Windows; pip then exits with
    an error and the note carries it.

    The pin only lasts if _env.bat pins the same way: its `pip install -r`
    after requirements.txt changes would otherwise make, on the next .bat
    launch, the swap refused here.

    Returns (ok, note) and never raises. Refuses to run outside a virtualenv,
    so a dev box's system Python is never touched. Any other failure is
    retried by _env.bat on the next .bat launch."""
    import subprocess
    import sys
    import tempfile
    if not os.path.exists(os.path.join(base_dir, "requirements.txt")):
        return False, "requirements.txt not found; nothing installed."
    if getattr(sys, "base_prefix", sys.prefix) == sys.prefix:
        return False, ("New components were not installed (not running from "
                       "the .venv). Run: python -m pip install -r requirements.txt")
    cmd = [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]
    pins = _gpu_pins()
    pin_file = None
    try:
        if pins:
            # If this cannot be written, the except below returns and pip
            # does not run at all: never unpinned while torch is installed.
            fd, pin_file = tempfile.mkstemp(prefix="witness-pins-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(pins) + "\n")
            cmd += ["-c", pin_file]
        r = subprocess.run(
            cmd, cwd=base_dir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=PIP_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, (f"New components did NOT install (pip took over "
                       f"{PIP_TIMEOUT // 60} min). Start WITNESS from its .bat "
                       "launcher to retry.")
    except Exception as e:
        return False, (f"New components did NOT install ({type(e).__name__}: "
                       f"{e}). Start WITNESS from its .bat launcher to retry.")
    finally:
        if pin_file:
            try:
                os.remove(pin_file)
            except OSError:
                pass
    if r.returncode == 0:
        return True, "New components installed."
    out = (r.stderr or b"") + b"\n" + (r.stdout or b"")
    lines = [ln.strip() for ln in out.decode("utf-8", "replace").splitlines()
             if ln.strip()]
    held = _held_by_pin(lines, pins)
    if held:
        kept = ", ".join(p.replace("==", " ") for p in pins)
        return False, (f"New components did NOT install: {held[:160]}. WITNESS "
                       f"keeps the installed {kept} rather than let pip swap "
                       "it during a launch (PyPI's Windows torch is CPU-only, "
                       "so GPU OCR would stop). To move to a newer one, "
                       "delete the .venv folder and run START again (it "
                       "re-downloads the OCR engine).")
    # pip ends with "[notice] A new release of pip..." on stderr, and a
    # dependency conflict ends with a bare "ResolutionImpossible: for help
    # visit ..." line; the useful line is the last other ERROR.
    errs = [ln for ln in lines if ln.startswith("ERROR")]
    errs = [ln for ln in errs if "ResolutionImpossible" not in ln] or errs
    why = (errs or lines or ["no output"])[-1]
    return False, (f"New components did NOT install (pip exit {r.returncode}: "
                   f"{why[:200]}). Start WITNESS from its .bat launcher to retry.")


def _write_status(base_dir: str, msg: str, append: bool = False) -> None:
    """Persist the last update outcome to update_status.txt for diagnosis
    (the webview has no console to print to). append=True adds to it: the
    relaunched process must not wipe what the updating one wrote."""
    try:
        import time
        with open(os.path.join(base_dir, "update_status.txt"),
                  "a" if append else "w", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{msg}\n")
    except Exception:
        pass


def _say(text: str) -> None:
    """print() that can never stop a relaunch (pythonw has no stdout; a
    redirected Windows console may not encode every character)."""
    try:
        print(text, flush=True)
    except Exception:
        pass


def _console_attached() -> bool:
    """python.exe started from a .bat (or a terminal), not pythonw.exe/GUI."""
    import sys
    exe = (sys.executable or "").replace("\\", "/").rsplit("/", 1)[-1]
    if exe.lower().startswith("pythonw"):
        return False
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        return False


def _swallow_ctrl_c(signum, frame) -> None:
    """SIGINT handler for a parent that is only waiting on its relaunched
    child. Ctrl-C reaches the child too (same console), and the child is the
    one that stops the session cleanly and builds the recap."""


def _ignore_ctrl_c() -> None:
    """Not SIG_IGN: on POSIX an ignored SIGINT is inherited across exec and
    Python keeps it, so the relaunched app would ignore Ctrl-C as well. A
    Python-level no-op handler resets to the default in the child."""
    import signal
    try:
        signal.signal(signal.SIGINT, _swallow_ctrl_c)
    except (ValueError, OSError, AttributeError):
        pass   # not the main thread / no SIGINT: nothing to protect


def update_and_relaunch_if_needed(base_dir: str, argv=None) -> str:
    """For launchers: run the update; if code changed, relaunch this process
    so the fresh files actually load. Returns the status message (only when
    no relaunch was needed — otherwise the process is replaced).

    From a console (.bat + python.exe) the parent WAITS for the relaunched
    run and exits with its code, so the .bat's `pause` comes after the real
    output (the Benchmark verdict used to be lost) and Ctrl-C does not drop
    cmd into "Terminate batch job (Y/N)?" while the app is still running.
    The parent ignores Ctrl-C meanwhile; otherwise subprocess.call would kill
    the child mid-recap on KeyboardInterrupt. pythonw/GUI keeps Popen + exit."""
    import subprocess
    import sys
    if os.environ.get("MKR_UPDATED") == "1":
        # Just relaunched after an update — don't loop.
        os.environ.pop("MKR_UPDATED", None)
        note = os.environ.pop("MKR_UPDATE_NOTE", "").strip()
        tail = f" {note}" if note else ""
        _write_status(base_dir, "Restarted on the latest version (update "
                      f"applied).{tail}", append=True)
        return f"Restarted on the latest version.{tail}"
    changed, msg = check_and_update(base_dir)
    note = ""
    if changed and "requirements.txt" in LAST_CHANGED_FILES:
        # Before the relaunch, or the fresh process imports a module that is
        # not installed yet (pywebview once shipped exactly like that).
        _say("  requirements.txt changed - installing new components...")
        _ok, note = ensure_requirements(base_dir)
    _write_status(base_dir, f"{msg}  {note}" if note else msg)
    if changed:
        env = dict(os.environ, MKR_UPDATED="1")
        if note:
            env["MKR_UPDATE_NOTE"] = note
        cmd = [sys.executable] + list(argv or sys.argv)
        _say(f"{msg}\n  Restarting on the new version...")
        if (_console_attached()
                and threading.current_thread() is threading.main_thread()):
            _ignore_ctrl_c()
            sys.exit(subprocess.call(cmd, cwd=base_dir, env=env))
        subprocess.Popen(cmd, cwd=base_dir, env=env)
        sys.exit(0)
    return msg
