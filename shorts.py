"""Turn each session clip into a YouTube Shorts / TikTok-ready vertical video.

Standard gameplay-shorts look: the 16:9 clip centered in a 1080x1920 frame,
with a blurred, zoomed copy of itself filling the background, plus a baked-in
label ("KILL #3 - FINISHER"). Replay Buffer clips are ~30s, well under the
60s Shorts cap, so no trimming needed.

Output: <session_dir>/shorts/<clipname>.mp4 (h264+aac, ready to upload).
"""

from __future__ import annotations

import os
import re
import subprocess

VIDEO_EXTS = (".mkv", ".mp4", ".mov", ".flv", ".ts")

_WIN_FONTS = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
)


def _find_font() -> str:
    for f in _WIN_FONTS:
        if os.path.exists(f):
            return f
    return ""


def _has_drawtext(ffmpeg: str) -> bool:
    """Not every ffmpeg build includes drawtext (needs libfreetype).
    If it's missing, render the shorts without labels instead of failing."""
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-filters"],
                           capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return " drawtext " in r.stdout
    except Exception:
        return False


def _label_from_name(fname: str) -> str:
    """'003_down+finisher_19-27-52.mkv' -> 'KILL #3 - DOWN + FINISHER'."""
    m = re.match(r"(\d+)_([A-Za-z+_]+)_", fname)
    if not m:
        return ""
    num = int(m.group(1))
    tag = m.group(2).replace("+", " + ").replace("_", " ").upper()
    return f"KILL #{num} - {tag}"


def _drawtext(label: str, font: str) -> str:
    # ffmpeg filter escaping: colons in the font path and quotes in text
    fontfile = font.replace(":", r"\:")
    text = label.replace("'", "").replace(":", r"\:")
    return (f",drawtext=fontfile='{fontfile}':text='{text}'"
            f":fontsize=64:fontcolor=white:borderw=5:bordercolor=black"
            f":x=(w-text_w)/2:y=180")


def build_short(src: str, dest: str, ffmpeg: str, label: str = "") -> bool:
    """Render one vertical short. Returns True on success."""
    font = _find_font()
    overlay_chain = "[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
    if label and font:
        overlay_chain += _drawtext(label, font)
    filt = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=20:5[bgb];"
        "[fg]scale=1080:-2[fgs];"
        + overlay_chain + "[v]"
    )
    cmd = [ffmpeg, "-y", "-i", src,
           "-filter_complex", filt, "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
           dest]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode == 0 and os.path.exists(dest):
        return True
    tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
    print(f"  [shorts] ffmpeg failed on {os.path.basename(src)}: {tail}")
    return False


def build_shorts(session_dir: str, ffmpeg: str, with_labels: bool = True):
    """Render a vertical short for every clip in the session folder."""
    if not session_dir or not os.path.isdir(session_dir):
        return
    clips = sorted(
        f for f in os.listdir(session_dir)
        if f.lower().endswith(VIDEO_EXTS) and not f.lower().startswith("highlights")
    )
    if not clips:
        return
    out_dir = os.path.join(session_dir, "shorts")
    os.makedirs(out_dir, exist_ok=True)
    if with_labels and not _has_drawtext(ffmpeg):
        print("  [shorts] this ffmpeg build has no drawtext filter — rendering without labels")
        with_labels = False
    done = 0
    for c in clips:
        dest = os.path.join(out_dir, os.path.splitext(c)[0] + ".mp4")
        if os.path.exists(dest):
            done += 1
            continue
        label = _label_from_name(c) if with_labels else ""
        if build_short(os.path.join(session_dir, c), dest, ffmpeg, label):
            done += 1
    print(f"  [shorts] {done}/{len(clips)} vertical clips -> {out_dir}")
