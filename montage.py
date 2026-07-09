"""Stitch a session's clips into one highlight reel with ffmpeg.

Uses the concat demuxer with stream copy (-c copy) so it's fast and lossless —
the clips all come from the same OBS Replay Buffer, so they share a codec.
Needs ffmpeg: either ffmpeg.exe in the app folder, an ffmpeg_path in config, or
ffmpeg on PATH.
"""

import os
import subprocess

VIDEO_EXTS = (".mkv", ".mp4", ".mov", ".flv", ".ts")


def find_ffmpeg(base_dir, cfg):
    p = (cfg.get("ffmpeg_path") or "").strip()
    if p and os.path.exists(p):
        return p
    local = os.path.join(base_dir, "ffmpeg.exe")
    if os.path.exists(local):
        return local
    return "ffmpeg"  # fall back to PATH


def build_montage(session_dir, ffmpeg):
    """Concatenate the session's clips into highlights_<session>.<ext>."""
    if not session_dir or not os.path.isdir(session_dir):
        print("  [montage] no session folder to stitch")
        return None

    clips = sorted(
        f for f in os.listdir(session_dir)
        if f.lower().endswith(VIDEO_EXTS) and not f.lower().startswith("highlights")
    )
    if not clips:
        print("  [montage] no clips in the session folder to stitch")
        return None

    ext = os.path.splitext(clips[0])[1]
    list_path = os.path.join(session_dir, "_montage_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for c in clips:
            # ffmpeg concat: escape single quotes in filenames
            f.write("file '%s'\n" % c.replace("'", "'\\''"))

    out = os.path.join(session_dir, "highlights_%s%s" % (os.path.basename(session_dir), ext))
    cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0",
           "-i", list_path, "-c", "copy", out]
    try:
        r = subprocess.run(cmd, cwd=session_dir, capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0 and os.path.exists(out):
            print("  [montage] highlight reel -> %s (%d clips)" % (out, len(clips)))
            try:
                os.remove(list_path)
            except OSError:
                pass
            return out
        tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
        print("  [montage] ffmpeg failed (%s): %s" % (r.returncode, tail))
    except FileNotFoundError:
        print("  [montage] ffmpeg not found — put ffmpeg.exe in the app folder "
              "or set ffmpeg_path in config.yaml.")
    except Exception as e:
        print("  [montage] error: %s" % e)
    return None
