"""tts_online: false keeps announcer text off Microsoft's speech service.

The neural voice (edge-tts) sends the text it speaks, which carries the
announcer name and, in the WITNESS Report, other players' gamertags, to
Microsoft. tts_online is the off switch. The code default is ON: configs that
predate the key (updates never rewrite config.yaml) must keep the voice they
have, so a missing key may never be read as false.

Callers that never pass cfg (main.py, export_reel.py) still get the switch:
renders read it from config.yaml / settings_override.yaml beside announcer.py
and read it again whenever either file changes, so an edit followed by START
(or a dashboard export) is honoured in a long-lived process. configure(cfg)
pins a session's value until the files change.

Nothing here touches the network or a speaker: edge_tts is replaced with a
recorder, ElevenLabs sees no key, and the offline synths are stubbed.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import announcer  # noqa: E402

# synth_to_wav only has an offline voice on Windows (System.Speech) and macOS
# (say); elsewhere (a Linux CI runner) the offline tier returns None.
HAS_OFFLINE = sys.platform in ("win32", "darwin")


@pytest.fixture
def edge(monkeypatch, tmp_path):
    """A fake edge_tts that records every Communicate() instead of calling
    Microsoft, plus stubbed offline voices so nothing runs or speaks."""
    sent = []

    class Communicate:
        def __init__(self, text, voice, **kw):
            sent.append(text)

        async def save(self, path):
            with open(path, "wb") as f:
                f.write(b"\0" * 2048)

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Communicate))
    monkeypatch.setattr(announcer, "_eleven_key", lambda: "")
    # A fresh process whose install folder has no config.yaml yet (= online),
    # so no test reads or depends on the repo's own config.yaml.
    cfg_dir = tmp_path / "install"
    cfg_dir.mkdir()
    monkeypatch.setattr(announcer, "_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(announcer, "_ONLINE", None)
    monkeypatch.setattr(announcer, "_CONFIGURED_AT", None)
    monkeypatch.setattr(announcer, "_disk", (announcer._NEVER_READ, True))
    monkeypatch.setattr(announcer, "_told_offline", False)

    offline = []

    def fake_offline(text, out_wav):
        offline.append(text)
        with open(out_wav, "wb") as f:
            f.write(b"RIFF")
        return out_wav

    monkeypatch.setattr(announcer, "_win_sapi", fake_offline)
    monkeypatch.setattr(announcer, "_mac_say", fake_offline)
    return types.SimpleNamespace(sent=sent, offline=offline, cfg_dir=cfg_dir)


# --- reading the switch ------------------------------------------------------

def test_missing_key_means_online():
    assert announcer.tts_online({}) is True
    assert announcer.tts_online(None) is True
    assert announcer.tts_online({"tts_online": None}) is True


@pytest.mark.parametrize("value", [False, "false", "False", "no", "off", "0", 0])
def test_false_in_any_spelling_means_offline(value):
    assert announcer.tts_online({"tts_online": value}) is False


@pytest.mark.parametrize("value", [True, "true", "yes", 1])
def test_true_means_online(value):
    assert announcer.tts_online({"tts_online": value}) is True


# --- the gate ----------------------------------------------------------------

def test_tts_online_false_never_reaches_microsoft(edge, tmp_path, capsys):
    assert announcer.configure({"tts_online": False}) is False
    out = announcer.synth_to_wav("Mister Somebody put three in the dirt",
                                 str(tmp_path / "a.wav"))
    assert edge.sent == []
    if HAS_OFFLINE:
        assert edge.offline == ["Mister Somebody put three in the dirt"]
        assert out and out.endswith(".wav")
    assert "tts_online is false" in capsys.readouterr().out


def test_edge_neural_returns_none_when_off(edge, tmp_path):
    assert announcer._edge_neural("hi", str(tmp_path / "x.mp3"),
                                  announcer.DEFAULT_VOICE, online=False) is None
    assert edge.sent == []


def test_missing_key_still_tries_the_online_voice(edge, tmp_path):
    """The author's config has no tts_online line. His reels must keep the
    neural voice."""
    assert announcer.configure({}) is True
    out = announcer.synth_to_wav("Roll the tape", str(tmp_path / "b.wav"))
    assert edge.sent == ["Roll the tape"]
    assert out and out.endswith(".mp3")
    assert edge.offline == []


def test_per_call_override_beats_the_session_setting(edge, tmp_path):
    announcer.configure({})
    announcer.synth_to_wav("quiet please", str(tmp_path / "c.wav"), online=False)
    assert edge.sent == []
    announcer.configure({"tts_online": False})
    announcer.synth_to_wav("loud please", str(tmp_path / "d.wav"), online=True)
    assert edge.sent == ["loud please"]


def test_shouted_callouts_are_gated_too(edge, tmp_path):
    assert announcer._medal_shout("CLUTCH!", str(tmp_path / "s.mp3"),
                                  announcer.DEFAULT_VOICE, "-18Hz",
                                  online=False) is None
    assert edge.sent == []
    assert announcer._medal_shout("CLUTCH!", str(tmp_path / "s.mp3"),
                                  announcer.DEFAULT_VOICE, "-18Hz") is not None
    assert edge.sent == ["CLUTCH!"]


def test_elevenlabs_is_not_gated_by_the_switch(edge, monkeypatch, tmp_path):
    """ElevenLabs is opt-in by adding a key; tts_online is about the default
    Microsoft voice only."""
    calls = []

    def fake_eleven(text, out_mp3, voice_id="", shout=False):
        calls.append(text)
        with open(out_mp3, "wb") as f:
            f.write(b"\0" * 2048)
        return out_mp3

    monkeypatch.setattr(announcer, "_elevenlabs", fake_eleven)
    announcer.configure({"tts_online": False})
    out = announcer.synth_to_wav("keyed", str(tmp_path / "e.wav"))
    assert calls == ["keyed"] and out.endswith(".mp3")
    assert edge.sent == []


# --- call-out cache ----------------------------------------------------------

def test_online_cache_dir_is_unchanged(edge, tmp_path):
    """Existing installs keep their rendered call-outs: the online cache path is
    byte-for-byte the pre-switch formula."""
    voice, pitch = "en-US-ChristopherNeural", "-18Hz"
    legacy = os.path.join(str(tmp_path), "cache_medals", "".join(
        c for c in f"{voice}{pitch}{announcer._MEDAL_CACHE_VER}"
        if c.isalnum() or c in "-_"))
    assert announcer._medal_dir(str(tmp_path), voice, pitch, "", True) == legacy


def test_offline_callouts_cache_separately(edge, tmp_path):
    """An offline render must not be served as 'the neural voice' after the
    switch goes back on (or the reverse)."""
    on = announcer._medal_dir(str(tmp_path), "v", "-1Hz", "", True)
    off = announcer._medal_dir(str(tmp_path), "v", "-1Hz", "", False)
    assert on != off and os.path.basename(off).startswith("offline_")


def test_ensure_callout_offline_sends_nothing(edge, monkeypatch, tmp_path):
    ran = []

    def fake_run(args, **kw):
        ran.append(args)
        with open(args[-1], "wb") as f:      # the arena-processed wav
            f.write(b"RIFF")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(announcer.subprocess, "run", fake_run)
    announcer.configure({"tts_online": False})
    wav = announcer.ensure_callout(str(tmp_path), "You just killed SOMEDUDE!",
                                   announcer.DEFAULT_VOICE, "ffmpeg")
    assert edge.sent == []
    if HAS_OFFLINE:
        assert wav and os.path.exists(wav)
        assert "offline_" in wav
        assert edge.offline == ["You just killed SOMEDUDE!"]


# --- a caller that never ran configure() --------------------------------------
# main.py, export_reel.py and the window app render voices without passing cfg.
# Until (and even if) they call configure(cfg), the switch must still work, so
# the first render reads it from config.yaml beside announcer.py.

def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_disk_read_missing_key_or_file_means_online(tmp_path):
    assert announcer._switch_from_disk(str(tmp_path)) is True        # no files
    _write(tmp_path / "config.yaml", "announcer_voice: x\n")
    assert announcer._switch_from_disk(str(tmp_path)) is True        # no key


def test_disk_read_honours_false(tmp_path):
    _write(tmp_path / "config.yaml", "tts_online: false\n")
    assert announcer._switch_from_disk(str(tmp_path)) is False


def test_settings_override_wins_like_load_config(tmp_path):
    _write(tmp_path / "config.yaml", "tts_online: true\n")
    _write(tmp_path / "settings_override.yaml", "tts_online: false\n")
    assert announcer._switch_from_disk(str(tmp_path)) is False
    _write(tmp_path / "config.yaml", "tts_online: false\n")
    _write(tmp_path / "settings_override.yaml", "tts_online: true\n")
    assert announcer._switch_from_disk(str(tmp_path)) is True


def test_an_ansi_saved_config_is_still_read(tmp_path):
    """Notepad's ANSI (cp1252) save is not valid UTF-8."""
    with open(tmp_path / "config.yaml", "wb") as f:
        f.write("# caf\u00e9 \u2014 note\ntts_online: false\n".encode("cp1252"))
    assert announcer._switch_from_disk(str(tmp_path)) is False


def test_garbage_config_falls_back_to_the_code_default(tmp_path):
    _write(tmp_path / "config.yaml", "tts_online: [unclosed\n")
    assert announcer._switch_from_disk(str(tmp_path)) is True


def test_the_shipped_config_reads_online():
    assert announcer._switch_from_disk() is True


def _rewrite(path, text):
    """Write `text` and move the mtime forward a second, so the change is seen
    even on a filesystem with coarse timestamps or when the size is equal."""
    before = os.stat(str(path)).st_mtime_ns if os.path.exists(str(path)) else 0
    _write(path, text)
    st = os.stat(str(path))
    later = max(st.st_mtime_ns, before) + 10**9
    os.utime(str(path), ns=(st.st_atime_ns, later))


def test_unconfigured_process_parses_the_files_only_when_they_change(
        edge, monkeypatch, tmp_path):
    _rewrite(edge.cfg_dir / "config.yaml", "tts_online: false\n")
    real = announcer._read_switch
    reads = []

    def counting(base):
        reads.append(base)
        return real(base)

    monkeypatch.setattr(announcer, "_read_switch", counting)
    announcer.synth_to_wav("first", str(tmp_path / "f.wav"))
    announcer.synth_to_wav("second", str(tmp_path / "g.wav"))
    assert edge.sent == []
    assert len(reads) == 1                       # unchanged files: stat only
    _rewrite(edge.cfg_dir / "config.yaml", "tts_online: false  # still\n")
    announcer.synth_to_wav("third", str(tmp_path / "h.wav"))
    assert len(reads) == 2
    # an explicit configure() (main's loaded cfg) still wins over the file
    announcer.configure({"tts_online": True})
    announcer.synth_to_wav("fourth", str(tmp_path / "i.wav"))
    assert edge.sent == ["fourth"]


def test_unconfigured_process_without_the_key_stays_online(edge, tmp_path):
    """The author's install: config.yaml has no tts_online line and nothing
    calls configure(). The neural voice must still be tried."""
    _write(edge.cfg_dir / "config.yaml", "announcer_voice: en-US-ChristopherNeural\n")
    out = announcer.synth_to_wav("Roll the tape", str(tmp_path / "r.wav"))
    assert edge.sent == ["Roll the tape"]
    assert out and out.endswith(".mp3")


# --- one long-lived process, the user edits config.yaml between sessions ------
# The window app is one process; START re-reads config.yaml for every other
# setting. The first version of the disk read latched its answer for the life
# of the process, so turning tts_online off and pressing START kept sending
# text to Microsoft (and turning it back on was ignored).

def test_turning_it_off_takes_effect_without_a_restart(edge, tmp_path):
    cfg = edge.cfg_dir / "config.yaml"
    _rewrite(cfg, "tts_online: true\n")
    announcer.synth_to_wav("session one", str(tmp_path / "a.wav"))
    _rewrite(cfg, "tts_online: false\n")
    announcer.synth_to_wav("session two, opponent SOMEDUDE", str(tmp_path / "b.wav"))
    assert edge.sent == ["session one"]


def test_turning_it_back_on_takes_effect_without_a_restart(edge, tmp_path):
    cfg = edge.cfg_dir / "config.yaml"
    _rewrite(cfg, "tts_online: false\n")
    announcer.synth_to_wav("quiet", str(tmp_path / "a.wav"))
    _rewrite(cfg, "tts_online: true\n")
    announcer.synth_to_wav("loud", str(tmp_path / "b.wav"))
    assert edge.sent == ["loud"]


def test_a_dashboard_override_written_mid_run_is_seen(edge, tmp_path):
    _rewrite(edge.cfg_dir / "config.yaml", "tts_online: true\n")
    announcer.synth_to_wav("before", str(tmp_path / "a.wav"))
    _rewrite(edge.cfg_dir / "settings_override.yaml", "tts_online: false\n")
    announcer.synth_to_wav("after", str(tmp_path / "b.wav"))
    assert edge.sent == ["before"]


def test_medals_and_callouts_follow_the_edit(edge, monkeypatch, tmp_path):
    """START re-renders the medal set and every call-out asks the switch:
    after the edit, they come from the offline_ cache, not Microsoft."""
    def fake_run(args, **kw):
        with open(args[-1], "wb") as f:
            f.write(b"RIFF")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(announcer.subprocess, "run", fake_run)
    cfg = edge.cfg_dir / "config.yaml"
    _rewrite(cfg, "tts_online: true\n")
    announcer.ensure_callout(str(tmp_path), "first", announcer.DEFAULT_VOICE, "ffmpeg")
    _rewrite(cfg, "tts_online: false\n")
    wav = announcer.ensure_callout(str(tmp_path), "You just killed SOMEDUDE!",
                                   announcer.DEFAULT_VOICE, "ffmpeg")
    medals = announcer.ensure_medal_sounds(str(tmp_path), announcer.DEFAULT_VOICE,
                                           "ffmpeg")
    assert edge.sent == ["first"]
    if HAS_OFFLINE:
        assert "offline_" in wav
        assert medals and all("offline_" in p for p in medals.values())


def test_configure_holds_until_the_files_change(edge, tmp_path):
    """configure(cfg) is the session's own loaded config (maybe from
    --config). It wins while config.yaml is untouched; an edit to false after
    it (then an export, with no new START) is still honoured."""
    cfg = edge.cfg_dir / "config.yaml"
    _rewrite(cfg, "tts_online: false\n")
    assert announcer.configure({"tts_online": True}) is True
    announcer.synth_to_wav("pinned", str(tmp_path / "a.wav"))
    assert edge.sent == ["pinned"]
    _rewrite(cfg, "tts_online: false  # really\n")
    announcer.synth_to_wav("edited off", str(tmp_path / "b.wav"))
    assert edge.sent == ["pinned"]


def test_an_edit_cannot_re_enable_a_configured_false(edge, tmp_path):
    """A session configured off stays off until the next configure() (the
    next START), even if a file edit says on: the safe direction to wait."""
    cfg = edge.cfg_dir / "config.yaml"
    _rewrite(cfg, "tts_online: false\n")
    assert announcer.configure({"tts_online": False}) is False
    _rewrite(cfg, "tts_online: true\n")
    announcer.synth_to_wav("still off", str(tmp_path / "a.wav"))
    assert edge.sent == []
    announcer.configure({"tts_online": True})
    announcer.synth_to_wav("next START", str(tmp_path / "b.wav"))
    assert edge.sent == ["next START"]


def test_a_half_typed_edit_keeps_the_last_value(edge, tmp_path):
    """Re-reading must not let a YAML typo elsewhere undo the user's false."""
    cfg = edge.cfg_dir / "config.yaml"
    _rewrite(cfg, "tts_online: false\n")
    announcer.synth_to_wav("a", str(tmp_path / "a.wav"))
    _rewrite(cfg, "obs: [unclosed\ntts_online: false\n")
    announcer.synth_to_wav("b", str(tmp_path / "b.wav"))
    assert edge.sent == []
    _rewrite(cfg, "tts_online: true\n")          # fixed: read again
    announcer.synth_to_wav("c", str(tmp_path / "c.wav"))
    assert edge.sent == ["c"]


# --- nothing reaches edge-tts around the gate ---------------------------------

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_only_announcer_imports_edge_tts():
    """A second module calling edge-tts directly would bypass tts_online."""
    hits = []
    for name in sorted(os.listdir(BASE)):
        if name.endswith(".py") and name != "announcer.py":
            with open(os.path.join(BASE, name), encoding="utf-8") as f:
                if "edge_tts" in f.read():
                    hits.append(name)
    assert hits == []


def test_every_edge_tts_render_checks_the_switch_first():
    import ast
    path = os.path.join(BASE, "announcer.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    gated = []
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef):
            body = ast.get_source_segment(src, node)
            if "edge_tts.Communicate" in body:
                assert body.index("_online_ok(") < body.index("import edge_tts"), node.name
                gated.append(node.name)
    assert sorted(gated) == ["_edge_neural", "_medal_shout"]
