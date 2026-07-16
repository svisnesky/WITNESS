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

DEFAULT_VOICE = "en-US-GuyNeural"


def synth_to_wav(text: str, out_wav: str, voice: str = DEFAULT_VOICE,
                 pitch: str = "+0Hz") -> str | None:
    """Render `text` to audio near out_wav. Returns the actual file written
    (mp3 for the neural voice, wav for fallbacks) or None.
    pitch: e.g. "-18Hz" to deepen any voice (neural only)."""
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)

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
    2: ("double_kill", "Double kill!"),
    3: ("triple_kill", "Triple kill!"),
    4: ("quad_kill", "Quadra kill!"),
    5: ("multi_kill", "Multi kill!"),   # 5+ all use this one
    "wipe": ("team_wipe", "Team wipe!"),
}


def ensure_medal_sounds(base_dir: str, voice: str, ffmpeg: str,
                        pitch: str = "+0Hz") -> dict:
    """Pre-render the medal call-outs ('Double kill!' ...) so playback is
    instant mid-game. Cached per voice under cache_medals/ — after the first
    render they work offline forever. Returns {kill_count: wav_path}."""
    safe_voice = "".join(c for c in f"{voice}{pitch}" if c.isalnum() or c in "-_")
    mdir = os.path.join(base_dir, "cache_medals", safe_voice)
    os.makedirs(mdir, exist_ok=True)
    out = {}
    for n, (name, text) in MEDALS.items():
        wav = os.path.join(mdir, f"{name}.wav")
        if os.path.exists(wav):
            out[n] = wav
            continue
        src = synth_to_wav(text, os.path.join(mdir, f"{name}_raw.wav"), voice, pitch)
        if not src:
            continue
        if src.endswith(".wav"):
            os.replace(src, wav)
        else:
            # neural output is mp3; winsound needs wav
            r = subprocess.run([ffmpeg, "-y", "-i", src, "-ar", "48000", wav],
                               capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                os.remove(src)
            except OSError:
                pass
            if r.returncode != 0:
                continue
        if os.path.exists(wav):
            out[n] = wav
    if out:
        print(f"  [medals] {len(out)} call-outs ready ({voice})")
    return out


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


def stat_line(kills: int, stats: dict, potg_tag: str = "",
              player: str = "", runner: str = "") -> str:
    """A short broadcast-style script, varied per match instead of a fixed
    monotone template. Keeps to ~2 sentences so it lands over the intro."""
    import random

    k = _spoken_number(kills)
    who = player or "our runner"
    if runner:
        who = f"{who}, on {runner},"

    if kills == 0:
        return random.choice([
            f"Quiet one on the kill feed, but {who} made it out. Roll the tape.",
            f"No kills this run, but an exfil is an exfil. Here's how it went.",
        ])

    openers = [
        f"Match highlights. {who} drops {k} kill{'s' if kills != 1 else ''}.",
        f"{k.capitalize()} kill{'s' if kills != 1 else ''} for {who} this run.",
        f"Highlights incoming. {k.capitalize()} kill{'s' if kills != 1 else ''} on the board.",
    ]
    parts = [random.choice(openers)]

    if potg_tag:
        tag = potg_tag.replace("+", " and ").replace("_", " ").lower()
        parts.append(random.choice([
            f"Play of the game: a {tag}.",
            f"The big one? A {tag}.",
        ]))
    elif stats.get("runner_damage"):
        parts.append(f"{stats['runner_damage']} runner damage dealt.")

    parts.append(random.choice([
        "Roll the tape.", "Watch this.", "To the footage.", "Enjoy.",
    ]))
    return " ".join(parts)
