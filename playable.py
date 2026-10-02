"""Hand iPads and iPhones a video they can actually play.

OBS's Replay Buffer writes .mkv, and so does the session montage. Apple's video
stack (Safari's <video> and AVPlayer in the iOS app) cannot play Matroska at all
— so on an iPad every raw clip and the montage simply failed to load, both in the
web Clip viewer and in the app.

WITNESS already remuxes each clip to .mp4 for instant replays (main.py,
_register_replay_async), but that runs seconds after OBS writes the file and on
2026-07-29 about ten of those remuxes failed ("Conversion failed!") — most likely
because the clip was still being written. So this resolves a playable file in
three steps:

  1. it's already an .mp4  -> serve it
  2. a replays/<name>.mp4 remux exists from the session -> serve that
  3. otherwise remux now (stream copy, no re-encode, ~1s) into <session>/mp4/
     and serve that — by now the file is long finished, so it succeeds

HEVC needs the 'hvc1' sample-entry tag or Apple players refuse it, even though
the codec is supported — OBS on NVIDIA commonly records HEVC.

Remuxes are written to a temp name and renamed into place, and serialized per
file, because AVPlayer fires several byte-range requests at once on open; without
the lock each request would start its own ffmpeg on the same output.
"""

from __future__ import annotations

import os
import subprocess
import threading

CACHE_DIR = "mp4"            # inside the session folder; tidy.py ignores subfolders
_locks: dict = {}
_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(path)
        if lk is None:
            lk = _locks[path] = threading.Lock()
        return lk


def _flags():
    try:
        import match_reel
        return match_reel._render_flags()
    except Exception:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def video_codec(path: str, ffmpeg: str) -> str:
    """'h264' / 'hevc' / 'av1' / '' via ffprobe (next to ffmpeg)."""
    try:
        import match_reel
        probe = match_reel._ffprobe_path(ffmpeg)
        r = subprocess.run([probe, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_name",
                            "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, timeout=15,
                           creationflags=_flags())
        return (r.stdout or "").strip().lower()
    except Exception:
        return ""


def cache_path(session_dir: str, rel: str) -> str:
    flat = rel.replace("/", "__").replace("\\", "__")
    return os.path.join(session_dir, CACHE_DIR, os.path.splitext(flat)[0] + ".mp4")


def existing_replay(session_dir: str, full: str) -> str:
    """The replays/<stem>.mp4 the session already made for this clip, or ''."""
    stem = os.path.splitext(os.path.basename(full))[0]
    cand = os.path.join(session_dir, "replays", stem + ".mp4")
    return cand if os.path.isfile(cand) and os.path.getsize(cand) > 0 else ""


def remux(src: str, dest: str, ffmpeg: str) -> bool:
    """Stream-copy src into an Apple-playable mp4 at dest. Atomic."""
    codec = video_codec(src, ffmpeg)
    cmd = [ffmpeg, "-y", "-v", "error", "-i", src, "-map", "0:v:0", "-map", "0:a?",
           "-c", "copy", "-movflags", "+faststart"]
    if codec == "hevc":
        cmd += ["-tag:v", "hvc1"]
    tmp = dest + ".part.mp4"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        r = subprocess.run(cmd + [tmp], capture_output=True, text=True, timeout=180,
                           creationflags=_flags())
        if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            print(f"  [play] remux failed for {os.path.basename(src)}: "
                  f"{(r.stderr or '').strip().splitlines()[-1:] or ['?']}")
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False
        os.replace(tmp, dest)
        return True
    except Exception as e:
        print(f"  [play] remux error for {os.path.basename(src)}: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def playable(session_dir: str, rel: str, full: str, ffmpeg: str) -> str:
    """Path of an Apple-playable version of `full`, or '' if none can be made."""
    if full.lower().endswith(".mp4"):
        return full
    rep = existing_replay(session_dir, full)
    if rep:
        return rep
    dest = cache_path(session_dir, rel)
    with _lock_for(dest):
        if os.path.isfile(dest) and os.path.getsize(dest) > 0 \
                and os.path.getmtime(dest) >= os.path.getmtime(full):
            return dest
        if not ffmpeg:
            return ""
        return dest if remux(full, dest, ffmpeg) else ""
