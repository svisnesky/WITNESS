"""Catch names used but never defined or imported — the bug class that shipped
--rebuild broken.

_resolve_session_dir() called OBSClient(cfg), but OBSClient is only imported
LOCALLY inside two other functions, so at runtime the name was simply undefined:

    (couldn't ask OBS for the clip folder: name 'OBSClient' is not defined)

Two things hid it: the `except Exception` swallowed the NameError as if it were an
OBS connection problem, and no test ran that path. A static check finds this in
every function — including the ones only reachable after hours of play.

Uses pyflakes rather than a hand-rolled AST walk. My first attempt at the latter
reported __file__ and comprehension targets as undefined; a checker that cries
wolf gets ignored, which is worse than not having one.
"""

import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODULES = ["main", "webserver", "match_reel", "montage", "shorts", "exfil_stats",
           "encounters", "heat", "tidy", "detector", "ocr", "obs_client",
           "youtube_upload", "updater", "witness_report", "loot_goblin",
           "announcer", "capture", "teach", "teach_gui", "benchmark", "diagnose"]


def _paths():
    return [os.path.join(BASE, f"{m}.py") for m in MODULES
            if os.path.exists(os.path.join(BASE, f"{m}.py"))]


def test_no_undefined_names():
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        pytest.skip("pyflakes not installed (pip install pyflakes)")
    out = subprocess.run([sys.executable, "-m", "pyflakes", *_paths()],
                         capture_output=True, text=True).stdout
    # Only the fatal class: a name that does not exist at runtime.
    fatal = [ln for ln in out.splitlines()
             if "undefined name" in ln or "syntax error" in ln.lower()]
    assert not fatal, "\n".join(fatal)


def test_every_module_compiles():
    """A syntax error in a module used only at end-of-session would otherwise
    surface for the first time after a long play session."""
    import py_compile
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for i, path in enumerate(_paths()):
            py_compile.compile(path, doraise=True,
                               cfile=os.path.join(d, f"{i}.pyc"))


def test_rebuild_does_not_need_obs_running():
    """Rebuilding works on files already on disk, so it must not require a live
    OBS connection — it reads the cached record folder first, and only falls back
    to OBS with the import actually in scope."""
    import inspect

    sys.path.insert(0, BASE)
    import main

    src = inspect.getsource(main._resolve_session_dir)
    assert "cached_record_dir()" in src, "must work with OBS closed"
    assert "from obs_client import OBSClient" in src, "fallback needs the import"
