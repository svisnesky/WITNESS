"""Cross-package hooks, checked with both sides present.

Each release-prep package was built alone and reached the others through
getattr / try-import guards, so every package passed its own suite whether or
not the other side existed. These tests pin that the guarded call actually
reaches the real thing now that both sides are merged.
"""

import os
import sys
import threading
import types

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


# --- Bonjour TXT 'v': web-dashboard publishes state.build_sha for the callers --

def test_main_advertises_the_bare_build_sha():
    """discovery.advertise keeps 12 characters of 'v'; the full label
    '0.1.0 (1a2b3c4)' is 15, so the iPad picker read '0.1.0 (1a2b3'."""
    src = _src("main.py")
    i = src.index("discovery.advertise(")
    call = src[i:i + 600]
    assert 'getattr(_web_state, "build_sha", "")' in call


def test_gui_advertises_the_bare_build_sha(monkeypatch):
    pytest.importorskip("tkinter")
    import gui
    got, done = {}, threading.Event()
    disc = types.ModuleType("discovery")

    def advertise(port, ip, version="", api=1):
        got["v"] = version
        done.set()
        return "ADV"
    disc.advertise = advertise
    monkeypatch.setitem(sys.modules, "discovery", disc)
    monkeypatch.setattr(gui, "app", types.SimpleNamespace(_advertiser=None))
    ws = types.SimpleNamespace(local_ip=lambda: "192.168.1.20", API_VERSION=1)
    st = types.SimpleNamespace(version="0.1.0 (1a2b3c4)", build_sha="1a2b3c4")
    gui._advertise_for_ipad({}, "0.0.0.0", 8000, st, ws)
    assert done.wait(5)
    assert got["v"] == "1a2b3c4"


# --- gamertag: one key-absent rule in encounters, main and webserver ---------

@pytest.mark.parametrize("cfg, want", [
    ({}, "MRVIZNASTY"),                 # Stan's old config: no key at all
    ({"gamertag": ""}, ""),             # shipped config.yaml: no tag
    ({"gamertag": None}, ""),           # YAML's bare `gamertag:`
    ({"gamertag": " Runner#1 "}, "Runner#1"),
])
def test_gamertag_rule_is_the_same_everywhere(cfg, want):
    import encounters
    import main
    import webserver
    assert encounters.resolve_gamertag(cfg) == want
    assert main._gamertag(cfg) == want
    assert webserver._gamertag(cfg) == want


def test_shipped_config_has_a_blank_tag_and_code_default_stays():
    import encounters
    import main
    import yaml
    # the shipped file itself, not load_config(): that would also merge a
    # settings_override.yaml sitting beside a developer's checkout
    cfg = yaml.safe_load(_src("config.yaml"))
    assert "gamertag" in cfg and main._gamertag(cfg) == ""
    assert main._announcer_name(cfg) == ""
    assert encounters.DEFAULT_GAMERTAG == "MRVIZNASTY"


# --- ffmpeg: reel-pipeline's imageio fallback satisfies main's preflight -----

def test_imageio_ffmpeg_counts_as_ffmpeg_at_start(tmp_path, monkeypatch):
    import main
    import montage
    exe = tmp_path / "ffmpeg-imageio"
    exe.write_bytes(b"")
    monkeypatch.setattr(montage.shutil, "which", lambda name: None)
    monkeypatch.setattr(main.shutil, "which", lambda name: None)
    monkeypatch.setattr(montage, "_IMAGEIO_FFMPEG", str(exe))
    base = tmp_path / "app"
    base.mkdir()
    assert montage.find_ffmpeg(str(base), {}) == str(exe)
    assert main._ffmpeg_available(str(base), {}) is True
    monkeypatch.setattr(montage, "_IMAGEIO_FFMPEG", "")      # nothing anywhere
    assert main._ffmpeg_available(str(base), {}) is False
    # Stan's ffmpeg.exe in the app folder still wins over the bundled one
    (base / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setattr(montage, "_IMAGEIO_FFMPEG", str(exe))
    assert montage.find_ffmpeg(str(base), {}) == str(base / "ffmpeg.exe")


# --- exfil MATCH_BREAK: main appends the marker detection-hotpath defines ----

def test_match_break_is_defined_and_counted_as_nothing():
    import exfil_stats
    assert exfil_stats.MATCH_BREAK == "exfil"
    assert exfil_stats.count_events([exfil_stats.MATCH_BREAK]) == (0, 0)
    # a pending elim must not swallow the next match's first FINISHER
    assert exfil_stats.count_events(["kill", exfil_stats.MATCH_BREAK, "finisher"]) == (0, 2)
    assert exfil_stats.count_events(["kill", "finisher"]) == (0, 1)


# --- version.VERSION reaches the dashboard label -----------------------------

def test_version_reaches_the_dashboard_label(tmp_path):
    import version
    import webserver
    assert webserver.build_label(str(tmp_path)).startswith(version.VERSION)


# --- updater hooks the launcher reads with getattr ---------------------------

def test_updater_exposes_what_the_launcher_and_bat_use():
    import inspect
    import updater
    assert hasattr(updater.ABORT_WRITES, "set")
    assert isinstance(updater.LAST_CHANGED_FILES, list)
    assert callable(updater.ensure_requirements)
    assert "force" in inspect.signature(updater.check_and_update).parameters


# --- decision 10: Marathon skull art is gone once main stopped needing it ----

def test_marathon_skull_png_is_not_shipped_and_only_named_as_an_old_setting():
    """landing-page-and-assets kept marathon_skull.png because main.py still
    used it as the tray fallback; main-runtime-hardening swapped that to
    witness_logo.png. With both merged nothing reads the file, so it goes.
    The name may remain only as main.py's old-config string comparison."""
    import subprocess
    r = subprocess.run(["git", "ls-files"], cwd=HERE, capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    tracked = r.stdout.splitlines()
    assert "marathon_skull.png" not in tracked
    hits = []
    for rel in tracked:
        if rel.startswith("tests/") or not rel.endswith(
                (".py", ".bat", ".yaml", ".yml", ".html", ".md", ".txt", ".json")):
            continue
        with open(os.path.join(HERE, rel), encoding="utf-8", errors="replace") as f:
            for line in f:
                if "marathon_skull.png" in line:
                    hits.append((rel, line.strip()))
    assert all(rel == "main.py" for rel, _ in hits), hits
    assert any('img_name == "marathon_skull.png"' in ln for _, ln in hits)


# --- the desktop tiles replay main.py's real console lines -------------------

def test_gui_tiles_parse_the_lines_main_prints():
    """gui._RECON_RE was written against the planned wording; check it against
    the f-strings main.py actually prints, rendered with real numbers."""
    pytest.importorskip("tkinter")
    import gui
    src = _src("main.py")
    assert 'f"  [reconcile] game credited more downs than detected — "' in src
    assert 'print(f"  [reconcile] game credited more runner elims than "' in src
    assert 'f"detected — added {missed_e}")' in src
    tags = ["down", "kill"]
    for line in ("  [exfil] match stats: 3 runners downed, 3 runner elims",
                 "  [reconcile] game credited more downs than detected — added 1, count now 4",
                 "  [reconcile] game credited more runner elims than detected — added 2",
                 "  [reconcile] headline below the game's panel for this match — raised by 1, count now 5"):
        gui.parse_console_line(line, tags)
    assert tags == ["down", "kill", "down", "elim", "elim", gui._match_break()]
    assert gui.tile_values(tags)["downs"] == 2
    gui.parse_console_line("KILL #3 [precision]: 'PRECISION DOWN +15 XP'", tags)
    gui.parse_console_line("(not a kill: assist) [assist]: 'RUNNER DOWN [ASSIST] +5'", tags)
    assert tags[-2:] == ["precision", "assist"]


def test_gui_breaks_the_match_when_the_panel_could_not_be_read():
    """main.py appends MATCH_BREAK at every exfil pass; with no readable panel
    the pass prints exfil_stats.report({}, ...)'s 'couldn't read' line instead
    of 'match stats'. The tiles must break there too, or the elim pending at
    exfil folds the next match's first FINISHER."""
    pytest.importorskip("tkinter")
    import exfil_stats
    import gui
    tags = ["kill"]
    gui.parse_console_line(exfil_stats.report({}, tags), tags)
    gui.parse_console_line("KILL #2 [finisher]: 'FINISHER'", tags)
    assert tags == ["kill", gui._match_break(), "finisher"]
    assert exfil_stats.count_events(tags) == (0, 2)
