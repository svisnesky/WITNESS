"""Reel announcer voiceover — neural voice when possible, offline fallback.

Voice quality tiers, tried in order:
  1. edge-tts (Microsoft neural voices — sounds human; free, no API key;
     needs internet at reel-build time): pip install edge-tts
  2. Windows System.Speech via PowerShell (offline, robotic — the fallback)
  3. macOS `say` (development testing)

Pick the voice with announcer_voice in config.yaml (e.g. "en-US-GuyNeural",
"en-US-ChristopherNeural", "en-GB-RyanNeural"). List them all with:
  .venv\\Scripts\\python -m edge_tts --list-voices
"""

from __future__ import annotations

import os
import subprocess
import sys

DEFAULT_VOICE = "en-US-ChristopherNeural"
DEFAULT_PITCH = "-18Hz"          # deep broadcast voice — Stan-approved

# ElevenLabs (optional, best-in-class delivery): drop your API key into
# elevenlabs_key.txt in this folder (or the ELEVENLABS_API_KEY env var) and
# every render upgrades automatically. Call-outs render ONCE and cache, so
# even the free tier covers the whole medal set with room to spare.
ELEVEN_DEFAULT_VOICE = "N2lVS1w4EtoT3dr4eOWO"   # "Callum" — intense, hoarse,
                                                # dark. A PREMADE voice, so it
                                                # works on the free tier via
                                                # the API. The WITNESS voice.
# The casting bench (swap via elevenlabs_voice_id in config):
#   "TsHrPyMlNFuIYnbODF01"  "Alien Master" — the most ominous, but a LIBRARY
#                           voice: needs a paid ElevenLabs plan to use via API.
#   "6F5Zhi321D3Oq7v1oNT4"  "Hank" — also library (paid).
#   Free premade alternatives (no plan needed): "JBFqnCBsd6RMkjVDRZzb" George
#   (deep, calm authority), "pqHfZKP75CvOlQylNhV4" Bill (older, deep narrator).
ELEVEN_FALLBACK_VOICE = "pNInz6obpgDQGcFmaJgB"  # "Adam" — premade, always
                                                # available (library voices
                                                # must be added per-account)


def _clean_key(raw: str) -> str:
    """Sanitize a pasted API key: drop a UTF-8 BOM, surrounding quotes, and
    any stray whitespace/newlines. Notepad loves to add a BOM when it saves
    as UTF-8, and an invisible BOM makes ElevenLabs reject the key (401)."""
    if not raw:
        return ""
    k = raw.lstrip("﻿").strip()          # BOM + outer whitespace
    k = k.strip('"').strip("'").strip()       # accidental quotes
    k = k.splitlines()[0].strip() if k else k  # first line only
    return k


def _eleven_key() -> str:
    k = _clean_key(os.environ.get("ELEVENLABS_API_KEY", ""))
    if k:
        return k
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "elevenlabs_key.txt")
        with open(p, encoding="utf-8-sig") as f:   # -sig strips a BOM too
            return _clean_key(f.read())
    except OSError:
        return ""


def _elevenlabs(text: str, out_mp3: str, voice_id: str = "",
                shout: bool = False) -> str | None:
    """Best-quality render via the ElevenLabs API (needs a key; free tier is
    plenty for cached call-outs). Quietly returns None without one."""
    key = _eleven_key()
    if not key:
        return None
    import json
    import urllib.request
    vid = voice_id or ELEVEN_DEFAULT_VOICE
    body = json.dumps({
        "text": text,
        "model_id": "eleven_multilingual_v2",
        # shout: less stability + more style = the hype delivery
        "voice_settings": {"stability": 0.30 if shout else 0.45,
                           "similarity_boost": 0.80,
                           "style": 0.65 if shout else 0.30},
    }).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
        data=body, method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            audio = r.read()
        if len(audio) > 1000:
            with open(out_mp3, "wb") as f:
                f.write(audio)
            return out_mp3
    except Exception as e:
        # Library voices only work for accounts that added them — if this
        # one 4xx's, retry once with the always-available premade voice
        # before giving up on ElevenLabs entirely.
        if vid != ELEVEN_FALLBACK_VOICE:
            return _elevenlabs(text, out_mp3, voice_id=ELEVEN_FALLBACK_VOICE,
                               shout=shout)
        print(f"  [announcer] ElevenLabs unavailable ({type(e).__name__}) — "
              "using the neural voice")
    return None


def synth_to_wav(text: str, out_wav: str, voice: str = DEFAULT_VOICE,
                 pitch: str = DEFAULT_PITCH, eleven_voice: str = "") -> str | None:
    """Render `text` to audio near out_wav. Returns the actual file written
    (mp3 for the neural voice, wav for fallbacks) or None.
    pitch: e.g. "-18Hz" to deepen any voice (neural only).
    eleven_voice: ElevenLabs voice ID override (used when a key is set)."""
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)

    path = _elevenlabs(text, os.path.splitext(out_wav)[0] + ".mp3",
                       voice_id=eleven_voice)
    if path:
        return path
    path = _edge_neural(text, os.path.splitext(out_wav)[0] + ".mp3", voice, pitch)
    if path:
        return path
    try:
        if sys.platform == "win32":
            return _win_sapi(text, out_wav)
        if sys.platform == "darwin":
            return _mac_say(text, out_wav)
    except Exception as e:
        print(f"  [announcer] tts failed: {e}")
    return None


def _edge_neural(text: str, out_mp3: str, voice: str,
                 pitch: str = "+0Hz") -> str | None:
    """Microsoft neural TTS via edge-tts. Quietly returns None when the
    package is missing or there's no internet — callers fall back."""
    try:
        import asyncio

        import edge_tts
    except ImportError:
        print("  [announcer] tip: for a human-sounding voice run "
              ".venv\\Scripts\\python -m pip install edge-tts")
        return None
    try:
        async def go():
            await edge_tts.Communicate(text, voice, rate="+8%",
                                       pitch=pitch).save(out_mp3)
        asyncio.run(asyncio.wait_for(go(), timeout=25))
        if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 1000:
            return out_mp3
    except Exception as e:
        print(f"  [announcer] neural voice unavailable ({type(e).__name__}) — "
              "using the offline voice")
    return None


def _win_sapi(text: str, out_wav: str) -> str | None:
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 1; "
        f"$s.SetOutputToWaveFile('{out_wav}'); "
        f"$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        input=text, capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode == 0 and os.path.exists(out_wav):
        return out_wav
    print(f"  [announcer] powershell tts failed: {(r.stderr or '').strip()[:120]}")
    return None


def _mac_say(text: str, out_wav: str) -> str | None:
    aiff = out_wav + ".aiff"
    r = subprocess.run(["say", "-o", aiff, text], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    r2 = subprocess.run(["ffmpeg", "-y", "-i", aiff, "-ar", "48000", out_wav],
                        capture_output=True, text=True)
    try:
        os.remove(aiff)
    except OSError:
        pass
    return out_wav if r2.returncode == 0 and os.path.exists(out_wav) else None


MEDALS = {
    2: ("double_kill", "DOUBLE KILL!"),
    3: ("triple_kill", "TRIPLE KILL!"),
    4: ("quad_kill", "QUADRA KILL!"),
    5: ("multi_kill", "MULTI KILL!"),   # 5+ all use this one
    "wipe": ("team_wipe", "TEAM WIPE!"),
}

# The "arena" treatment: punch compression, bass weight, a short stadium
# echo, and a limiter — the difference between spoken-into-a-mic and
# shouted-over-the-map. Applied when medals render.
ARENA_FX = ("volume=2.0,"
            "acompressor=threshold=-20dB:ratio=4:attack=3:release=140:makeup=4dB,"
            "bass=g=5:f=110,treble=g=2:f=4000,"
            "aecho=0.8:0.55:70|130:0.28|0.16,"
            "alimiter=limit=0.95")
_MEDAL_CACHE_VER = "arena1"   # bump to force a re-render of cached medals


def ensure_medal_sounds(base_dir: str, voice: str, ffmpeg: str,
                        pitch: str = DEFAULT_PITCH,
                        eleven_voice: str = "") -> dict:
    """Pre-render the medal call-outs ('Double kill!' ...) so playback is
    instant mid-game. Cached per voice under cache_medals/ — after the first
    render they work offline forever. Returns {kill_count: wav_path}."""
    # a present ElevenLabs key (and which of its voices) changes what renders
    # — separate cache dir so any change re-renders instead of serving stale
    tier = f"el{(eleven_voice or ELEVEN_DEFAULT_VOICE)[:8]}_" if _eleven_key() else ""
    safe_voice = tier + "".join(c for c in f"{voice}{pitch}{_MEDAL_CACHE_VER}"
                                if c.isalnum() or c in "-_")
    mdir = os.path.join(base_dir, "cache_medals", safe_voice)
    os.makedirs(mdir, exist_ok=True)
    out = {}
    for n, (name, text) in MEDALS.items():
        wav = os.path.join(mdir, f"{name}.wav")
        if os.path.exists(wav):
            out[n] = wav
            continue
        src = _medal_shout(text, os.path.join(mdir, f"{name}_raw.mp3"), voice,
                           pitch, eleven_voice=eleven_voice)
        if not src:
            # neural unavailable: plain offline synth, still arena-processed
            src = synth_to_wav(text, os.path.join(mdir, f"{name}_raw.wav"), voice, pitch)
        if not src:
            continue
        # convert + apply the arena treatment in one ffmpeg pass
        r = subprocess.run([ffmpeg, "-y", "-i", src, "-af", ARENA_FX,
                            "-ar", "48000", wav],
                           capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            os.remove(src)
        except OSError:
            pass
        if r.returncode == 0 and os.path.exists(wav):
            out[n] = wav
    if out:
        print(f"  [medals] {len(out)} call-outs ready ({voice})")
    return out


def ensure_callout(base_dir: str, text: str, voice: str, ffmpeg: str,
                   pitch: str = DEFAULT_PITCH, eleven_voice: str = "") -> str:
    """One arbitrary arena-processed call-out (e.g. 'You just killed
    Marshyy!'), rendered on first use and cached beside the medals. Returns
    the wav path, or '' if no synth worked."""
    tier = f"el{(eleven_voice or ELEVEN_DEFAULT_VOICE)[:8]}_" if _eleven_key() else ""
    safe_voice = tier + "".join(c for c in f"{voice}{pitch}{_MEDAL_CACHE_VER}"
                                if c.isalnum() or c in "-_")
    mdir = os.path.join(base_dir, "cache_medals", safe_voice)
    os.makedirs(mdir, exist_ok=True)
    slug = "".join(c for c in text.lower() if c.isalnum())[:48]
    wav = os.path.join(mdir, f"co_{slug}.wav")
    if os.path.exists(wav):
        return wav
    src = _medal_shout(text, os.path.join(mdir, f"co_{slug}_raw.mp3"), voice,
                       pitch, eleven_voice=eleven_voice)
    if not src:
        src = synth_to_wav(text, os.path.join(mdir, f"co_{slug}_raw.wav"), voice, pitch)
    if not src:
        return ""
    r = subprocess.run([ffmpeg, "-y", "-i", src, "-af", ARENA_FX, "-ar", "48000", wav],
                       capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        os.remove(src)
    except OSError:
        pass
    return wav if r.returncode == 0 and os.path.exists(wav) else ""


def _medal_shout(text: str, out_mp3: str, voice: str, pitch: str,
                 eleven_voice: str = "") -> str | None:
    """Neural render tuned for a SHOUT: faster and louder than narration."""
    path = _elevenlabs(text, out_mp3, voice_id=eleven_voice, shout=True)
    if path:
        return path
    try:
        import asyncio

        import edge_tts
    except ImportError:
        return None
    try:
        async def go():
            await edge_tts.Communicate(text, voice, rate="+15%", pitch=pitch,
                                       volume="+40%").save(out_mp3)
        asyncio.run(asyncio.wait_for(go(), timeout=25))
        if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 1000:
            return out_mp3
    except Exception:
        pass
    return None


def play_medal(medal_sounds: dict, key) -> None:
    """Fire-and-forget playback of the right call-out (async, never blocks).
    key: a kill count (2/3/4, 5+ falls back to multi) or 'wipe'."""
    wav = medal_sounds.get(key) or (medal_sounds.get(5) if key != "wipe" else None)
    if not wav or not os.path.exists(wav):
        return
    try:
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif sys.platform == "darwin":
            subprocess.Popen(["afplay", wav])
    except Exception:
        pass


def _spoken_number(n: int) -> str:
    """Numbers up to twelve read better as words."""
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven",
             "eight", "nine", "ten", "eleven", "twelve"]
    return words[n] if 0 <= n < len(words) else str(n)


def _cap(s: str) -> str:
    """Capitalize only the first character (keeps proper-noun casing,
    unlike str.capitalize() which lowercases the rest)."""
    return s[:1].upper() + s[1:] if s else s


def _potg_phrase(tag: str) -> str:
    """A clean spoken descriptor for the play-of-the-game clip, in the WITNESS
    voice. tag is the clip's combo tag (e.g. 'down+finisher')."""
    import random
    t = (tag or "").lower()
    if "precision" in t:
        return random.choice([
            "one straight through the skull",
            "the sharpest of them a precision kill",
            "one clean, precise, no wasted motion"])
    if "finisher" in t:
        return random.choice([
            "the last one finished where they lay",
            "one put down and finished, no mercy"])
    if "elim" in t or "kill" in t:
        return random.choice([
            "one an outright elimination",
            "one wiped from the record entirely"])
    return random.choice([
        "one cleaner than the rest",
        "the best of them worth watching twice"])


def stat_line(kills: int, stats: dict, potg_tag: str = "",
              player: str = "", runner: str = "") -> str:
    """WITNESS-persona reel narration — the surveillance AI recounting what it
    saw. Assembled from pools so no two matches sound the same. Kept to a few
    short clauses; neural voices breathe at every period, so clauses (commas,
    dashes) read more naturally than chains of tiny sentences."""
    import random

    who = player or "your runner"
    if runner:
        who = f"{who}, running {runner}"

    if kills == 0:
        return random.choice([
            f"No one fell this time — but {who} walked out, and I saw every step. The record stands.",
            f"A quiet run. Nothing dropped, yet {who} made exfil. I was watching all of it.",
            f"Not a single down this match. {_cap(who)} survived, and nothing escaped me.",
        ])

    k = _spoken_number(kills)
    ks = f"{k} {'runner' if kills == 1 else 'runners'}"

    openers = [
        "I saw every second of it.",
        "You were all being watched.",
        "Nothing escapes the record.",
        "I don't miss a thing.",
        "Every frame, remembered.",
        "It was all seen, all of it.",
        "The lens never blinked.",
    ]
    if potg_tag:
        pot = _potg_phrase(potg_tag)
        reports = [
            f"{ks.capitalize()} fell to {who} — {pot}.",
            f"{_cap(who)} put {ks} into the dirt, {pot}.",
            f"{ks.capitalize()} gone under {who}'s hands, {pot}.",
            f"{ks.capitalize()} down, {pot}, and {who} never slowed.",
        ]
    else:
        dmg = stats.get("runner_damage")
        if dmg:
            reports = [
                f"{ks.capitalize()} fell to {who}, {dmg} damage carved out and logged.",
                f"{_cap(who)} put {ks} down and dealt {dmg} damage — I counted every point.",
            ]
        else:
            reports = [
                f"{ks.capitalize()} fell to {who}.",
                f"{_cap(who)} put {ks} into the dirt.",
                f"{ks.capitalize()} gone, and {who} barely slowed.",
            ]
    closers = [
        "Roll it back — I don't miss.",
        "The footage remembers.",
        "Nothing gets past me.",
        "It's all on record now.",
        "Watch it again. I already have.",
        "Filed, and never forgotten.",
        "I see everything.",
    ]
    return " ".join([random.choice(openers), random.choice(reports),
                     random.choice(closers)])
