"""Bungie-derived assets stay out of the repo.

sounds/kill.wav, precision.wav and finisher.wav are recordings of Marathon's
own kill sounds (templates for an audio detector removed in ca48766), and
marathon_emblem.png / marathon_wordmark.png are Marathon logo art;
marathon_skull.ico and witness_headshot_splash.png were orphans too. None of
them was read by any code, yet the updater shipped them to every install under
the MIT license. They were removed with git rm only: the updater never deletes
local files, so existing installs keep their copies and nothing breaks.
"""

import os
import subprocess

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Recordings of Marathon's kill sounds and Marathon logo art: unused, and not
# ours to ship under MIT. marathon_skull.png is NOT here: main.py still names it
# (tray-icon fallback + an old-config upgrade check) and it is main.py's owner
# that swaps that fallback to witness_logo.png.
REMOVED = ("sounds/kill.wav", "sounds/precision.wav", "sounds/finisher.wav",
           "marathon_emblem.png", "marathon_wordmark.png", "marathon_skull.ico",
           "witness_headshot_splash.png")


def _tracked():
    try:
        r = subprocess.run(["git", "ls-files"], cwd=BASE, capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.splitlines()


def test_removed_game_assets_are_not_tracked_or_referenced():
    tracked = _tracked()
    if tracked is None:
        pytest.skip("not a git checkout (ZIP install keeps old files on disk)")
    back = [f for f in REMOVED if f in tracked]
    assert not back, f"removed assets are tracked again: {back}"
    needles = [os.path.basename(f) for f in REMOVED]
    hits = []
    for rel in tracked:
        if not rel.endswith((".py", ".bat", ".yaml", ".yml", ".html", ".md", ".txt",
                             ".json", ".swift", ".plist", ".pbxproj")):
            continue
        if rel == "tests/test_removed_assets.py":
            continue
        try:
            with open(os.path.join(BASE, rel), encoding="utf-8", errors="replace") as f:
                src = f.read()
        except OSError:
            continue
        hits += [f"{rel}: {n}" for n in needles if n in src]
    assert not hits, f"something still references a removed asset: {hits}"
