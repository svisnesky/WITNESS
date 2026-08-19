"""What actually happened inside each clip — the data behind the clip viewer.

Stan: "auto reels are cool but i feel like they miss stuff. could we add a clip
viewer/editor in there? show the clip, what happened in the clip that way its easy
to determine what happened."

The auto-reel has to guess which moments matter. He knows. This module turns the
raw session folder into something a person can scan: for every clip, WHAT it
contains and WHEN inside the clip each thing happened.

Everything here is derived from data already on disk, so it works on old sessions
too:
  filename  NNN_tag_time.mkv  -> the event tags and the wall-clock save time
  sidecar   <clip>.json       -> saved_epoch, victim gamertag, each kill's epoch

The useful trick: a kill's position inside the clip is
    t_in_clip = duration - (saved_epoch - kill_epoch)
because the Replay Buffer save lands at the END of the footage. That's what lets
the viewer mark the exact second of each down instead of making you scrub.
"""

from __future__ import annotations

import os

# Human labels for the event tags that appear in clip filenames.
TAG_LABELS = {
    "down": "DOWN",
    "precision": "HEADSHOT",
    "kill": "ELIM",
    "elim": "ELIM",
    "finisher": "FINISHER",
    "assist": "ASSIST",
    "manual": "SAVED",
    "manual_kill": "+1 KILL",
}

# Colour role per tag, resolved to real colours by the page's CSS.
TAG_ROLE = {
    "down": "kill",
    "precision": "prec",
    "kill": "elim",
    "elim": "elim",
    "finisher": "elim",
    "assist": "assist",
    "manual": "manual",
    "manual_kill": "manual",
}


def parse_name(name: str) -> tuple:
    """'003_down+precision_21-36-32.mkv' -> (3, ['down','precision'], '21:36:32').
    Returns (None, [], '') for anything that doesn't fit the pattern."""
    stem = os.path.splitext(os.path.basename(name))[0]
    parts = stem.split("_")
    if len(parts) < 3:
        return None, [], ""
    try:
        num = int(parts[0])
    except ValueError:
        num = None
    tags = [t for t in parts[1].split("+") if t]
    clock = parts[2].replace("-", ":")
    return num, tags, clock


def clip_events(dur, saved_epoch, kills) -> list:
    """[{t, label, manual}] — each kill's position IN SECONDS from the clip's
    start, oldest first.

    The Replay Buffer save lands at the END of the footage, so a kill that
    happened 8s before the save sits at (duration - 8). Without a duration or a
    save time there is nothing to anchor against, so return nothing rather than
    guess a position — a wrong marker is worse than no marker."""
    if not dur or not saved_epoch:
        return []
    out = []
    for k in kills or []:
        epoch = float(k.get("epoch") or 0)
        if not epoch:
            continue
        t = float(dur) - (float(saved_epoch) - epoch)
        if t < -2 or t > float(dur) + 2:
            continue                      # nonsense; don't place a false marker
        out.append({"t": round(max(0.0, min(float(dur), t)), 2),
                    "manual": bool(k.get("manual")),
                    "label": "+1 KILL" if k.get("manual") else "KILL"})
    return sorted(out, key=lambda e: e["t"])


def describe(tags, victim: str = "") -> str:
    """One plain-English line: "Downed PiNDLESKIN with a headshot, then finished
    them". This is the bit that makes a list of 20 clips scannable."""
    t = list(tags or [])
    if "manual" in t and len(t) == 1:
        return "Moment you saved by hand"
    who = victim or "a runner"
    bits = []
    n_down = t.count("down")
    if n_down:
        head = " with a headshot" if "precision" in t else ""
        bits.append(f"Downed {who}{head}" if n_down == 1
                    else f"Downed {n_down} runners")
    if any(x in t for x in ("kill", "elim", "finisher")):
        melee = " (melee finisher)" if "finisher" in t else ""
        bits.append(("then finished them" + melee) if bits
                    else f"Finished a downed runner{melee}")
    if "manual_kill" in t:
        bits.append("credited by hand (+1)")
    n_assist = t.count("assist")
    if n_assist:
        bits.append(f"{n_assist} assist{'s' if n_assist > 1 else ''}")
    return ", ".join(bits) if bits else "Recorded moment"


def build_index(session_dir: str, ffmpeg: str = "", with_duration: bool = True):
    """Every clip in a session, newest data first computed, oldest-first order.

    Works on a tidied folder (clips in clips/) and a flat one alike. Durations
    need ffprobe; with_duration=False skips that so the page can render instantly
    and fill them in later."""
    import match_reel
    import tidy

    out = []
    for rel, full in tidy.iter_session_media(session_dir, (".mkv", ".mp4")):
        name = os.path.basename(rel)
        num, tags, clock = parse_name(name)
        if not tags:
            continue
        saved_epoch, kills = match_reel._load_sidecar(full)
        victim = match_reel.sidecar_victim(full)
        dur = None
        if with_duration and ffmpeg:
            dur = match_reel.probe_duration(full, ffmpeg)
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        out.append({
            "rel": rel,                       # addressable by the Archive route
            "name": name,
            "num": num,
            "tags": tags,
            "chips": [{"label": TAG_LABELS.get(t, t.upper()),
                       "role": TAG_ROLE.get(t, "kill")} for t in tags],
            "clock": clock,
            "victim": victim,
            "summary": describe(tags, victim),
            "duration": round(float(dur), 2) if dur else None,
            "events": clip_events(dur, saved_epoch, kills),
            "kills": match_reel.potg_score({"tag": "+".join(tags)})[1],
            "downs": tags.count("down"),
            "human": match_reel.human_flagged("+".join(tags)),
            "size": size,
            "epoch": match_reel.clip_epoch(full),
        })
    return sorted(out, key=lambda c: c["epoch"])
