"""Voice diagnostic — tells you EXACTLY which voice WITNESS will use, and why.

Run it (or double-click "Check Voice.bat") whenever a reel or call-out comes
out in the wrong voice. It answers three questions in plain English:

  1. Is your ElevenLabs API key being found?  (the #1 reason you hear the
     backup "Christopher" voice instead of your ElevenLabs pick)
  2. If found, is it valid and does your chosen voice exist on the account?
  3. Which voice will actually play right now — ElevenLabs, the neural
     backup, or the offline robot?

No app changes, nothing rendered to your session — just a report.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(BASE, "elevenlabs_key.txt")


def _cfg(*keys, default=""):
    """Read a key from settings_override.yaml (wins) then config.yaml."""
    val = default
    try:
        import yaml
        for name in ("config.yaml", "settings_override.yaml"):
            p = os.path.join(BASE, name)
            if os.path.exists(p):
                d = yaml.safe_load(open(p, encoding="utf-8")) or {}
                for k in keys:
                    if k in d and d[k] not in (None, ""):
                        val = d[k]
    except Exception:
        pass
    return val


def _find_key():
    """Returns (key, source) — source is 'env', 'file', or ''."""
    from announcer import _clean_key
    k = _clean_key(os.environ.get("ELEVENLABS_API_KEY", ""))
    if k:
        return k, "env"
    try:
        with open(KEY_FILE, encoding="utf-8-sig") as f:
            k = _clean_key(f.read())
        if k:
            return k, "file"
        return "", "empty-file"
    except OSError:
        return "", ""


def _mask(k):
    """Safe preview: length + a few chars, never the whole key."""
    if len(k) <= 10:
        return f"{len(k)} chars"
    return f"{len(k)} chars, looks like: {k[:5]}...{k[-4:]}"


def _sanity(k, voice_id):
    """Cheap heuristics that catch the usual paste mistakes before we even
    hit the API."""
    notes = []
    if k == voice_id:
        notes.append("This is your VOICE ID, not the API key. The key starts "
                     "with 'sk_' and comes from Profile -> API Keys.")
    if " " in k:
        notes.append("There's a SPACE inside the key — recopy it cleanly.")
    if not k.startswith("sk_") and len(k) != 32:
        notes.append("Doesn't look like an ElevenLabs key (those start with "
                     "'sk_'). Double-check you copied the API key.")
    return notes


def _api(url, key):
    req = urllib.request.Request(url, headers={"xi-api-key": key})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _tts_test(key, voice_id):
    """Actually try to render one short word — the ground-truth test, since a
    key can be permission-scoped to only TTS (and would 401 on account reads
    while working fine here). Returns (ok, status_code, detail)."""
    body = json.dumps({"text": "ok", "model_id": "eleven_multilingual_v2"}).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        data=body, method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return (len(r.read()) > 500), 200, ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:
            pass
        return False, e.code, detail
    except Exception as e:
        return False, None, type(e).__name__


def main():
    print("=" * 58)
    print("  WITNESS voice check")
    print("=" * 58)

    # what the app is configured to want
    from announcer import ELEVEN_DEFAULT_VOICE
    want_voice = _cfg("elevenlabs_voice_id") or ELEVEN_DEFAULT_VOICE
    edge_voice = _cfg("announcer_voice", default="en-US-ChristopherNeural")

    key, source = _find_key()

    if not key:
        if source == "empty-file":
            print("\n[X] Found elevenlabs_key.txt but it is EMPTY.")
        else:
            print("\n[X] No ElevenLabs key found.")
        print("    Looked in: the ELEVENLABS_API_KEY variable, then this file:")
        print(f"      {KEY_FILE}")
        print("\n  -> This is why you're hearing the backup voice")
        print(f"     ('{edge_voice}', a.k.a. Christopher).")
        print("\n  FIX (30 seconds):")
        print("   1. Go to elevenlabs.io -> your profile icon -> API Keys ->")
        print("      copy your key (starts with 'sk_').")
        print("   2. Make a plain text file next to this app named exactly:")
        print("        elevenlabs_key.txt")
        print("      Paste ONLY the key inside, save. (In Notepad, set")
        print("      'Save as type: All Files' so it isn't elevenlabs_key.txt.txt)")
        print("   3. Run this check again — it should say ElevenLabs is live.")
        print("\n  Until then, reels & call-outs use the neural backup voice.")
        print("=" * 58)
        return

    print(f"\n[OK] ElevenLabs key found (from {source}).")
    print(f"     Read {_mask(key)}")
    for note in _sanity(key, want_voice):
        print(f"  [!] {note}")

    # Ground truth: try an actual render on the chosen voice. This is what the
    # app really does, and it works even if the key is scoped to only TTS.
    from announcer import ELEVEN_FALLBACK_VOICE
    ok, code, detail = _tts_test(key, want_voice)

    if ok:
        print(f"\n[OK] Rendered a test clip with voice id {want_voice}.")
        print("     Reels and call-outs will use this ElevenLabs voice.")
        if not _cfg("elevenlabs_voice_id"):
            print("     (Built-in default 'Alien Master'. Change it with")
            print("      elevenlabs_voice_id in config.yaml.)")
        print("=" * 58)
        return

    # The chosen voice failed. Is it the KEY or just the VOICE? Test the key
    # against a premade voice that every account can use.
    if code in (400, 404, 422):
        ok2, code2, _ = _tts_test(key, ELEVEN_FALLBACK_VOICE)
        if ok2:
            print(f"\n[X]  Your key WORKS, but voice id {want_voice} isn't on your")
            print("     account. Library voices (like 'Alien Master') must be")
            print("     ADDED first: open the voice in ElevenLabs -> 'Add to my")
            print("     voices'. Then rerun this. Until then the app uses a")
            print(f"     premade voice or the neural backup ('{edge_voice}').")
            print("=" * 58)
            return
        code = code2 or code   # premade also failed -> it's really the key

    if code in (401, 403):
        print("\n[X]  Key was REJECTED by the render endpoint (%s)." % code)
        print("     The key string is well-formed, so this is almost always:")
        print("       - the key was REVOKED or regenerated (old one pasted), or")
        print("       - it was created with RESTRICTED permissions that exclude")
        print("         Text to Speech.")
        print("     Fix: elevenlabs.io -> Profile -> API Keys -> create a NEW key")
        print("     with Text to Speech enabled (or 'has access to all'), paste")
        print("     it into elevenlabs_key.txt, rerun this.")
    elif code == 429:
        print("\n[!]  Rate-limited or out of credits (429). The key is fine —")
        print("     your ElevenLabs quota is used up. Reels/call-outs use the")
        print(f"     neural backup ('{edge_voice}') until it resets.")
    elif code is None:
        print(f"\n[!]  Couldn't reach ElevenLabs ({detail}). If you're online this")
        print("     may be a brief outage; the app falls back to the neural voice.")
    else:
        print(f"\n[!]  Render failed (HTTP {code}). Detail: {detail}")
    print("=" * 58)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"voice_check error: {type(e).__name__}: {e}")
    if sys.platform == "win32":
        input("\nPress Enter to close...")
