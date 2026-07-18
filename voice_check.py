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

    # validate the key
    try:
        me = _api("https://api.elevenlabs.io/v1/user", key)
        tier = (me.get("subscription") or {}).get("tier", "?")
        used = (me.get("subscription") or {}).get("character_count", "?")
        cap = (me.get("subscription") or {}).get("character_limit", "?")
        print(f"     Account OK (tier: {tier}, used {used}/{cap} chars).")
    except Exception as e:
        code = getattr(e, "code", None)
        if code == 401:
            print("[X]  Key was REJECTED (401) — invalid, revoked, or the wrong")
            print("     string. See the note(s) above. Most often it's a stray")
            print("     character from Notepad or the voice ID pasted by mistake.")
            print("     Recopy the key (Profile -> API Keys, starts 'sk_') into")
            print("     elevenlabs_key.txt and run this again.")
        else:
            print(f"[!]  Couldn't reach ElevenLabs ({type(e).__name__}). If you're")
            print("     online this may be a temporary outage; the app falls back")
            print(f"     to the neural voice ('{edge_voice}') meanwhile.")
        print("=" * 58)
        return

    # confirm the chosen voice exists on the account
    try:
        v = _api(f"https://api.elevenlabs.io/v1/voices/{want_voice}", key)
        vname = v.get("name", "(unnamed)")
        print(f"     Voice OK: '{vname}'  [{want_voice}]")
        print("\n[OK] Reels and call-outs will use ElevenLabs -> "
              f"'{vname}'.")
        if not _cfg("elevenlabs_voice_id"):
            print("     (This is the built-in default. To use a different one,")
            print("      set elevenlabs_voice_id in config.yaml.)")
    except Exception as e:
        code = getattr(e, "code", None)
        if code in (400, 404):
            print(f"[X]  Your key is valid, but voice id {want_voice} is NOT on")
            print("     this account. Library voices must be ADDED to your")
            print("     account first (open the voice in ElevenLabs -> Add).")
            print("     The app will retry with a premade voice, or fall back")
            print(f"     to the neural voice ('{edge_voice}').")
        else:
            print(f"[!]  Voice check failed ({type(e).__name__}).")
    print("=" * 58)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"voice_check error: {type(e).__name__}: {e}")
    if sys.platform == "win32":
        input("\nPress Enter to close...")
