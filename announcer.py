"""Offline text-to-speech for the reel announcer — no API keys, no internet.

Windows: System.Speech via PowerShell (built into every Windows install).
macOS: the `say` command (used for development testing).

Returns a wav path, or None if TTS isn't available — the reel builder just
skips the announced version in that case.
"""

from __future__ import annotations

import os
import subprocess
import sys


def synth_to_wav(text: str, out_wav: str) -> str | None:
    """Render `text` to a 48kHz wav at out_wav. Returns the path or None."""
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)
    try:
        if sys.platform == "win32":
            return _win_sapi(text, out_wav)
        if sys.platform == "darwin":
            return _mac_say(text, out_wav)
    except Exception as e:
        print(f"  [announcer] tts failed: {e}")
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


def stat_line(kills: int, stats: dict) -> str:
    """Build the announcer script from the match stats."""
    bits = [f"Match highlights. {kills} kill{'s' if kills != 1 else ''}"]
    if stats.get("runner_elims") is not None:
        bits.append(f"{stats['runner_elims']} runner elimination"
                    f"{'s' if stats['runner_elims'] != 1 else ''}")
    if stats.get("runner_damage") is not None:
        bits.append(f"{stats['runner_damage']} runner damage")
    return ". ".join(bits) + "."
