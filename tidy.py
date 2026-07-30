"""Tidy a finished session folder — organize, never destroy.

After a session the folder is a pile: every per-kill clip, every exfil
screenshot, the montage, the session reel and the dossier all sitting at the top
level side by side. Stan wanted it "tidied up at least" — and with 15 TB free on
his clip drive there is no reason to delete footage to achieve that.

So this MOVES files into subfolders and deletes nothing:

    <session>/
      highlights_<session>.mkv      <- kept at top level (the payoff)
      session_reel.mp4              <- kept
      witness_report_tts.mp3        <- kept
      reels/                        <- already its own folder
      shorts/                       <- already its own folder
      clips/    000_assist_20-27-54.mkv, ...   <- moved here
      exfil/    exfil_20-28-01.png, ...        <- moved here

Runs only AFTER every artifact is rendered, so nothing is moved out from under
the montage/reel builders. Readers that browse a session later (the Archive, and
_session_clips_from_dir on a resume) look in both places, so an already-tidied
session still works — and so does one tidied by hand.
"""

from __future__ import annotations

import os
import shutil

# Files that stay at the top level: the things you actually go looking for.
KEEP_PREFIXES = ("highlights", "session_reel", "witness_report")
# Subfolders we own. Anything already in a folder is left alone.
CLIPS_DIR = "clips"
EXFIL_DIR = "exfil"

CLIP_EXTS = (".mkv", ".mp4")
SHOT_EXTS = (".png", ".jpg")


def _destination(name: str) -> str:
    """Subfolder `name` belongs in, or '' to leave it at the top level."""
    low = name.lower()
    if low.startswith(KEEP_PREFIXES):
        return ""
    if low.endswith(SHOT_EXTS):
        return EXFIL_DIR
    if low.endswith(CLIP_EXTS):
        return CLIPS_DIR
    if low.endswith(".json"):        # kill sidecars ride along with their clip
        return CLIPS_DIR
    return ""


def plan(session_dir: str) -> list:
    """[(src_name, subfolder)] for everything that would move. Pure — no I/O
    beyond listing, so it can be shown before it happens."""
    if not session_dir or not os.path.isdir(session_dir):
        return []
    out = []
    for name in sorted(os.listdir(session_dir)):
        full = os.path.join(session_dir, name)
        if not os.path.isfile(full):
            continue                        # never descend into reels/ or shorts/
        dest = _destination(name)
        if dest:
            out.append((name, dest))
    return out


def tidy_session(session_dir: str, dry_run: bool = False) -> dict:
    """Move per-kill clips (and their .json sidecars) into clips/, exfil
    screenshots into exfil/. Returns {subfolder: count}.

    Nothing is deleted and nothing leaves the session folder, so this is fully
    reversible by dragging the files back. A name collision is left alone rather
    than overwritten."""
    moves = plan(session_dir)
    if not moves:
        return {}
    counts = {}
    for name, sub in moves:
        src = os.path.join(session_dir, name)
        dest_dir = os.path.join(session_dir, sub)
        dst = os.path.join(dest_dir, name)
        if os.path.exists(dst):
            print(f"  [tidy] skipped {name} — already in {sub}/")
            continue
        if dry_run:
            counts[sub] = counts.get(sub, 0) + 1
            continue
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(src, dst)
            counts[sub] = counts.get(sub, 0) + 1
        except OSError as e:
            print(f"  [tidy] could not move {name}: {e}")
    if counts and not dry_run:
        bits = ", ".join(f"{n} -> {sub}/" for sub, n in sorted(counts.items()))
        print(f"  [tidy] organized session folder: {bits} (nothing deleted)")
    return counts


def iter_session_media(session_dir: str, exts=CLIP_EXTS):
    """Yield (relative_path, full_path) for session clips whether the folder has
    been tidied or not — top level first, then clips/. Lets every reader work on
    both layouts, including folders a user tidied by hand."""
    if not session_dir or not os.path.isdir(session_dir):
        return
    for sub in ("", CLIPS_DIR):
        d = os.path.join(session_dir, sub) if sub else session_dir
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            full = os.path.join(d, name)
            if not os.path.isfile(full) or not name.lower().endswith(exts):
                continue
            if name.lower().startswith(KEEP_PREFIXES):
                continue
            yield (f"{sub}/{name}" if sub else name), full
