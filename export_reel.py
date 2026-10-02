"""Build a reel from clips a PERSON chose, in the order they chose, trimmed how
they chose.

Stan: "then an export button to have the clips become a reel etc. would that be
possible? id like this to be polished with as many options as we can throw at it"

The auto-reel has to guess. This is the escape hatch: the clip viewer sends a
selection, and this renders exactly that. Thumbnails live here too, since they're
the other thing the viewer needs and they come from the same ffmpeg.

Design note: this deliberately reuses match_reel.build_match_reel rather than
building its own ffmpeg command. That function carries every lesson already paid
for — xfade offset pinning, the encoder ladder, the chyron fallback, faststart —
and a second renderer would have to relearn all of it.
"""

from __future__ import annotations

import os

THUMB_DIR = "thumbs"
# Thumbnails are small and cached; a session of 20 clips costs a few hundred KB.
THUMB_WIDTH = 480


def thumb_path(session_dir: str, clip_rel: str) -> str:
    """Where this clip's thumbnail lives. Kept in a SUBFOLDER so tidy.py leaves
    it alone (tidy only moves top-level files) and so it never looks like a clip
    to the indexer."""
    flat = clip_rel.replace("/", "__").replace("\\", "__")
    return os.path.join(session_dir, THUMB_DIR, flat + ".jpg")


def ensure_thumb(session_dir: str, clip_rel: str, clip_full: str, ffmpeg: str,
                 at_seconds=None) -> str:
    """Extract one frame, cached. Returns the thumbnail path, or '' on failure.

    Grabs the frame at the FIRST KILL when we know where it is — a frame from the
    action is far more recognisable than one from the approach, which on a 33s
    buffer save is usually an empty corridor."""
    dest = thumb_path(session_dir, clip_rel)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest
    if not ffmpeg or not os.path.exists(clip_full):
        return ""
    import subprocess

    import match_reel
    t = 3.0 if at_seconds is None else max(0.0, float(at_seconds))
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        r = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", clip_full,
             "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "5",
             dest],
            capture_output=True, text=True,
            creationflags=match_reel._render_flags())
        if r.returncode == 0 and os.path.exists(dest):
            return dest
        # A seek past the end yields nothing; retry from the very start.
        r = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", clip_full, "-frames:v", "1",
             "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "5", dest],
            capture_output=True, text=True,
            creationflags=match_reel._render_flags())
        return dest if r.returncode == 0 and os.path.exists(dest) else ""
    except Exception:
        return ""


def resolve(session_dir: str, selection) -> list:
    """Turn the viewer's selection into reel clip dicts, preserving ITS order.

    selection: [{rel, ss?, to?, tag?}]. A rel that escapes the session folder or
    doesn't exist is dropped — this arrives over HTTP from a page anyone on the
    LAN can reach, so it is not trusted."""
    root = os.path.realpath(session_dir)
    out = []
    for item in selection or []:
        rel = str((item or {}).get("rel") or "")
        if not rel or ".." in rel.replace("\\", "/").split("/"):
            continue
        full = os.path.realpath(os.path.join(session_dir, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            continue
        import clip_index
        _num, tags, _clock = clip_index.parse_name(os.path.basename(rel))
        clip = {"path": full, "tag": "+".join(tags) or "kill",
                "kills": max(1, len([t for t in tags if t == "down"])),
                "downs": tags.count("down")}
        if item.get("ss") not in (None, ""):
            try:
                clip["ss"] = max(0.0, float(item["ss"]))
            except (TypeError, ValueError):
                pass
        if item.get("to") not in (None, ""):
            try:
                clip["to"] = float(item["to"])
            except (TypeError, ValueError):
                pass
        out.append(clip)
    return out


def build(session_dir: str, selection, opts=None, cfg=None,
          on_note=None) -> str:
    """Render the chosen clips into <session>/reels/<name>.mp4. Returns the path,
    or '' on failure.

    opts: title, subtitle, music, transitions, chyrons, announcer, name.
    Everything defaults to the same behaviour as an auto reel, so an export with
    no options set still looks like the rest of the app."""
    opts = dict(opts or {})
    cfg = dict(cfg or {})

    def note(msg):
        print(f"  [export] {msg}")
        if on_note:
            try:
                on_note(status="building", note=msg)
            except Exception:
                pass

    import match_reel
    import montage
    base = os.path.dirname(os.path.abspath(__file__))
    ffmpeg = montage.find_ffmpeg(base, cfg)

    clips = resolve(session_dir, selection)
    if not clips:
        note("nothing to export — no valid clips in the selection")
        return ""

    safe = "".join(ch for ch in str(opts.get("name") or "custom_reel")
                   if ch.isalnum() or ch in "-_ ").strip() or "custom_reel"
    out_dir = os.path.join(session_dir, "reels")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{safe}.mp4")

    downs = sum(c.get("downs", 0) for c in clips)
    title = str(opts.get("title") or "WITNESS").upper()[:40]
    sub = [str(opts.get("subtitle")
               or f"{len(clips)} CLIP{'S' if len(clips) != 1 else ''}"
                  f"  ·  {downs} DOWN{'S' if downs != 1 else ''}")[:60]]
    import time
    sub.append(time.strftime("%Y-%m-%d"))

    music = []
    if opts.get("music", True) and cfg.get("reel_music", True):
        music = match_reel.list_music(os.path.join(base, "music"))

    note(f"rendering {len(clips)} clip(s) -> {os.path.basename(out)}")
    ok = match_reel.build_match_reel(
        clips, out, ffmpeg, title, downs, sub,
        os.path.join(base, "witness_wordmark.png"),
        music_volume=cfg.get("reel_music_volume", 0.08),
        music_tracks=music, theme=cfg.get("theme"),
        transitions=bool(opts.get("transitions", True)),
        chyrons=bool(opts.get("chyrons", True)),
        # tight_cuts off: explicit ss/to from the editor win, and clips with no
        # trim set should play in full rather than being auto-cut behind the
        # user's back — they chose these moments.
        tight_cuts=False,
        # Respect the selection exactly — no overlap-merging. The user picked
        # these; dropping one because its buffer overlaps another would be the
        # app overruling them.
        dedupe=False,
        # ...and in THEIR order. Play of the Game moved the best clip to the
        # front behind its own card, so 003,001,002 rendered as 002,003,001.
        potg_enabled=False,
        render_encoder=str(cfg.get("render_encoder", "auto")))
    if not ok:
        note("render failed")
        return ""

    if opts.get("announcer") and cfg.get("reel_announcer", True):
        try:
            import announcer
            line = f"{downs} down{'s' if downs != 1 else ''}. {title}."
            wav = announcer.synth_to_wav(
                line, os.path.join(session_dir, "_export_vo.wav"),
                voice=cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH),
                eleven_voice=cfg.get("elevenlabs_voice_id", ""))
            if wav:
                vo = os.path.join(out_dir, f"{safe}_announced.mp4")
                if match_reel.add_announcer(out, vo, wav, ffmpeg):
                    note(f"announced version -> {os.path.basename(vo)}")
                try:
                    os.remove(wav)
                except OSError:
                    pass
        except Exception as e:
            note(f"announcer skipped: {e}")

    note(f"done -> {out}")
    return out
