"""tts_online: false keeps announcer text off Microsoft's speech service.

The neural voice (edge-tts) sends the text it speaks, which carries the
announcer name and, in the WITNESS Report, other players' gamertags, to
Microsoft. tts_online is the off switch. The code default is ON: configs that
predate the key (updates never rewrite config.yaml) must keep the voice they
have, so a missing key may never be read as false.

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
def edge(monkeypatch):
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
    monkeypatch.setattr(announcer, "_ONLINE", True)
    monkeypatch.setattr(announcer, "_told_offline", False)

    offline = []

    def fake_offline(text, out_wav):
        offline.append(text)
        with open(out_wav, "wb") as f:
            f.write(b"RIFF")
        return out_wav

    monkeypatch.setattr(announcer, "_win_sapi", fake_offline)
    monkeypatch.setattr(announcer, "_mac_say", fake_offline)
    return types.SimpleNamespace(sent=sent, offline=offline)


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
