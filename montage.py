"""Stitch a session's clips into one highlight reel with ffmpeg.

Uses the concat demuxer with stream copy (-c copy) so it's fast and lossless —
the clips all come from the same OBS Replay Buffer, so they share a codec.
Needs ffmpeg: either ffmpeg.exe in the app folder, an ffmpeg_path in config,
ffmpeg on PATH, or (last resort) the binary bundled with the imageio-ffmpeg
package. See find_ffmpeg for the order.
"""

import os
import shutil
import subprocess

import tidy

VIDEO_EXTS = (".mkv", ".mp4", ".mov", ".flv", ".ts")


def _render_flags():
    """No console window AND below-normal priority so background ffmpeg work
    yields CPU to the game instead of competing with it (in-match frame drops)."""
    import subprocess
    import sys
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if sys.platform == "win32":
        flags |= 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
    return flags


# imageio-ffmpeg's bundled binary: None = not looked up yet, "" = not available.
_IMAGEIO_FFMPEG = None


def _imageio_ffmpeg() -> str:
    """The ffmpeg that the imageio-ffmpeg package ships, or "".

    GUARDED, and that is the whole point: an install can run code that lists
    this package before pip has installed it. The updater swaps files first and
    installs new requirements afterwards (updater.ensure_requirements, or
    _env.bat on the next .bat launch), and that install can fail or be offline.
    An unguarded import here would take down every replay and reel on exactly
    the installs that were working. Looked up once per process: this is
    reached on every kill for anyone without ffmpeg."""
    global _IMAGEIO_FFMPEG
    if _IMAGEIO_FFMPEG is None:
        exe = ""
        try:
            import imageio_ffmpeg
            exe = str(imageio_ffmpeg.get_ffmpeg_exe() or "")
        except Exception:            # not installed, or no binary for this OS
            exe = ""
        _IMAGEIO_FFMPEG = exe if exe and os.path.isfile(exe) else ""
    return _IMAGEIO_FFMPEG


def find_ffmpeg(base_dir, cfg):
    """The ffmpeg to run. Order matters, and the first two are Stan's setup:

      1. ffmpeg_path from config
      2. ffmpeg.exe in the app folder
      3. ffmpeg on PATH          -> the bare name "ffmpeg", exactly as before
      4. imageio-ffmpeg's bundled binary, if that package is installed
      5. the bare name "ffmpeg" anyway, so callers behave exactly as they always
         have (and fail the way they always have) when there is no ffmpeg at all

    The bundled binary comes LAST on purpose: it has no ffprobe (probe_duration
    already falls back to `ffmpeg -i`) and may lack NVENC or drawtext, both of
    which already degrade gracefully. Any ffmpeg the user installed wins."""
    p = ((cfg or {}).get("ffmpeg_path") or "").strip()
    if p and os.path.exists(p):
        return p
    local = os.path.join(base_dir, "ffmpeg.exe")
    if os.path.exists(local):
        return local
    if shutil.which("ffmpeg"):
        return "ffmpeg"  # on PATH
    bundled = _imageio_ffmpeg()
    if bundled:
        return bundled
    return "ffmpeg"  # nothing found; keep the old sentinel


def has_ffmpeg(base_dir, cfg) -> bool:
    """True if find_ffmpeg() resolves to something that can actually run.

    The bare "ffmpeg" sentinel is checked with shutil.which, never
    os.path.exists — a PATH install is not a file in the current folder, and
    testing it that way would wrongly switch off replays for everyone with
    ffmpeg on PATH. Not cached: the window app runs for days, and an ffmpeg
    installed meanwhile must count at the next START. main._ffmpeg_available
    is this, guarded."""
    ff = find_ffmpeg(base_dir, cfg)
    return bool(ff) and (os.path.isfile(ff) or shutil.which(ff) is not None)


def build_montage(session_dir, ffmpeg):
    """Concatenate the session's clips into highlights_<session>.<ext>."""
    if not session_dir or not os.path.isdir(session_dir):
        print("  [montage] no session folder to stitch")
        return None

    # Tidy-aware: clips live at the top level during a session and in clips/
    # once the recap has tidied the folder. Listing only the top level made a
    # Rebuild of a tidied session stitch session_reel.mp4 — the one video left
    # there — into a "montage" of itself. iter_session_media also skips the
    # session's own outputs (highlights*, session_reel*, witness_report*).
    rel_of, seen = {}, set()
    for rel, full in tidy.iter_session_media(session_dir, VIDEO_EXTS):
        name = os.path.basename(rel).lower()
        if name in seen:
            continue    # tidy skipped a name collision: same clip in both places
        seen.add(name)
        rel_of[full] = rel

    # CHRONOLOGICAL, not alphabetical: the filename's leading number is the kill
    # count, which repeats now that assists don't bump it, so a plain sort cut
    # back to an earlier moment mid-montage.
    try:
        from match_reel import sort_chronologically
        fulls = sort_chronologically(list(rel_of))
    except Exception:
        fulls = sorted(rel_of, key=lambda f: os.path.basename(f))
    if not fulls:
        print("  [montage] no clips in the session folder to stitch")
        return None
    # Back-to-back saves overlap (each is the last ~30s). CHAIN them: keep every
    # clip and start each later one where the clip before it ended, via the
    # concat demuxer's inpoint. This used to DROP the earlier clip, which threw
    # away the start of every fight. lead=0: a stream-copy concat has no
    # crossfade, and inpoint already lands on the keyframe at or before it.
    inpoints = {}
    try:
        from match_reel import chain_overlapping
        chained = chain_overlapping(
            [{"path": f, "kills": 1, "tag": ""} for f in fulls], ffmpeg, lead=0.0)
        fulls = [c["path"] for c in chained]
        inpoints = {c["path"]: c["ss_min"] for c in chained if c.get("ss_min")}
    except Exception:
        pass
    # Paths RELATIVE to the session folder, forward slashes ("clips/001_...").
    # The concat demuxer resolves them against the list file's own folder, which
    # is session_dir. A bare basename would point at the top level, where a
    # tidied clip no longer is.
    clips = [rel_of[f] for f in fulls]
    clip_inpoint = {rel_of[f]: inpoints[f] for f in fulls if f in inpoints}

    ext = os.path.splitext(os.path.basename(clips[0]))[1]
    list_path = os.path.join(session_dir, "_montage_list.txt")
    out = os.path.join(session_dir, "highlights_%s%s" % (os.path.basename(session_dir), ext))
    # +genpts / make_zero: OBS Replay-Buffer files carry non-zero start
    # timestamps; naive stream-copy concat inherits them and players cut clips
    # short / jump ("the clip ends, then the end shows later"). Regenerate
    # clean, monotonic timestamps while still stream-copying.
    cmd = [ffmpeg, "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0",
           "-i", list_path, "-c", "copy", "-avoid_negative_ts", "make_zero", out]
    try:
        with open(list_path, "w", encoding="utf-8") as f:
            for c in clips:
                # ffmpeg concat: escape single quotes in filenames
                f.write("file '%s'\n" % c.replace("'", "'\\''"))
                if c in clip_inpoint:
                    f.write("inpoint %.3f\n" % clip_inpoint[c])
        try:
            r = subprocess.run(cmd, cwd=session_dir, capture_output=True,
                               text=True, creationflags=_render_flags())
            if r.returncode == 0 and os.path.exists(out):
                print("  [montage] highlight reel -> %s (%d clips)" % (out, len(clips)))
                return out
            tail = ((r.stderr or "").strip().splitlines() or ["(no output)"])[-1]
            print("  [montage] ffmpeg failed (%s): %s" % (r.returncode, tail))
        except FileNotFoundError:
            print("  [montage] ffmpeg not found — put ffmpeg.exe in the app folder "
                  "or set ffmpeg_path in config.yaml.")
        except Exception as e:
            print("  [montage] error: %s" % e)
    finally:
        # Every exit, not just success: a failed render used to leave the list
        # file behind in the session folder. A list file that can't be WRITTEN
        # still raises to the caller, as before, so the recap records it.
        try:
            os.remove(list_path)
        except OSError:
            pass
    return None
