"""Per-match highlight reel — built automatically when the EXFILTRATED screen
appears, from the clips saved during that match.

Broadcast package:
  - Stat title card (kills, elims, damage, run time) fades in first.
  - PLAY OF THE GAME: the clip with the most kills leads the reel, with its
    own card (Overwatch-style). Ties go to the flashier tag.
  - Optional music bed: drop mp3/wav/m4a files in the music/ folder and one
    is mixed under the gameplay audio.
  - Optional announcer: a second "_announced" version with an offline-TTS
    voiceover of the stat line (video stream copied, audio-only re-encode).

Output is an iPad-friendly mp4 (h264+aac+faststart) in <session>/reels/.
"""

from __future__ import annotations

import os
import subprocess

from matchcard import (_font, _text_w, _gradient_text, BG, LINE, TEXT, MUTED,
                       ACCENT, ACCENT_LIGHT)


def _render_flags():
    """No console window AND below-normal priority so background ffmpeg work
    yields CPU to the game instead of competing with it (in-match frame drops)."""
    import subprocess
    import sys
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if sys.platform == "win32":
        flags |= 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
    return flags


def _rgb(c):
    """Accept an (r,g,b) tuple or a '#rrggbb' string -> (r,g,b) tuple."""
    if isinstance(c, str) and c.startswith("#") and len(c) == 7:
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    return tuple(c)


def _lighten(c, f=0.45):
    return tuple(int(c[i] + (255 - c[i]) * f) for i in range(3))

CARD_SECONDS = 2.8
MUSIC_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

# flashier tags win Play of the Game ties
TAG_PRIORITY = ("finisher", "precision", "down", "kill", "assist", "manual")


def _run(cmd) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          creationflags=_render_flags())


def _build_card(out_png: str, title: str, kills, kills_label: str,
                sub_lines: list[str], wordmark_path: str = "",
                theme: dict | None = None) -> bool:
    """1920x1080 stat card in the match-card style. theme (from the game
    profile) overrides the Marathon palette + brand text."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  [reel] Pillow not installed — no stat cards. "
              "Run: .venv\\Scripts\\python -m pip install pillow")
        return False
    try:
        th = theme or {}
        bg, accent = _rgb(th.get("bg", BG)), _rgb(th.get("accent", ACCENT))
        text, muted = _rgb(th.get("text", TEXT)), _rgb(th.get("muted", MUTED))
        line_c = _rgb(th.get("line", LINE))
        # gradient top colour: a themed accent gets a lightened variant; the
        # default WITNESS accent uses the exact dashboard accent-light.
        accent_light = _lighten(accent) if th.get("accent") else ACCENT_LIGHT
        brand = str(th.get("display_name") or "WITNESS").upper()

        W, H = 1920, 1080
        img = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(img)
        pad = 110

        d.rectangle([0, 0, W, 10], fill=accent)
        d.rectangle([0, H - 10, W, H], fill=accent)

        y = pad
        # a themed game gets its name as the brand; the wordmark image is
        # Marathon's and only used when the brand IS Marathon
        if brand == "WITNESS" and wordmark_path and os.path.exists(wordmark_path):
            try:
                wm = Image.open(wordmark_path).convert("RGBA")
                scale = 64 / wm.height
                wm = wm.resize((int(wm.width * scale), 64), Image.LANCZOS)
                img.paste(wm, (pad, y), wm)
            except Exception:
                d.text((pad, y), brand, font=_font("black", 56), fill=accent)
        else:
            d.text((pad, y), brand, font=_font("black", 56), fill=accent)

        d.text((pad, y + 110), title, font=_font("black", 110), fill=text)

        kf = _font("black", 380)
        ks = str(kills)
        _gradient_text(img, (pad - 10, 360), ks, kf, accent_light, accent)
        d.text((pad + _text_w(d, ks, kf) + 40, 640), kills_label,
               font=_font("bold", 64), fill=text)

        ly = 880
        for line in sub_lines[:2]:
            d.text((pad, ly), line, font=_font("mono", 40), fill=muted)
            ly += 58

        d.line([pad, 840, W - pad, 840], fill=line_c, width=2)
        img.save(out_png)
        return True
    except Exception as e:
        print(f"  [reel] card failed: {e}")
        return False


def _save_time(path: str):
    """Seconds-of-day parsed from the clip filename's HH-MM-SS suffix
    (e.g. '003_kill_21-36-32.mkv'), or None."""
    import re
    m = re.search(r"(\d{2})-(\d{2})-(\d{2})\.\w+$", os.path.basename(path))
    if not m:
        return None
    h, mi, s = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + s


def clip_epoch(path: str) -> float:
    """Absolute time a clip was saved, for ordering reels chronologically.

    Prefers the sidecar's saved_epoch (exact), then the file's mtime, then the
    HH-MM-SS in the filename. Filename order is NOT usable: clips are named
    <killcount>_<tag>_<time>, and since assists no longer bump the kill counter
    several clips share a prefix — so a plain sort put '013_assist_22-02' before
    '013_down_21-48' ("assist" < "down"). That is the jarring cut back to an
    earlier moment mid-reel."""
    epoch, _kills = _load_sidecar(path)
    if epoch > 0:
        return float(epoch)
    try:
        return float(os.path.getmtime(path))
    except OSError:
        pass
    secs = _save_time(path)
    return float(secs) if secs is not None else 0.0


def sort_chronologically(clips):
    """Order clips oldest-first. Accepts paths or reel dicts with a 'path'."""
    def key(c):
        return clip_epoch(c["path"] if isinstance(c, dict) else c)
    return sorted(clips, key=key)


def write_kill_sidecar(clip_path: str, saved_epoch: float, kills: list) -> None:
    """Record WHEN the kills inside a clip happened: <clip>.json with the save
    moment and each kill's wall-clock epoch (+ whether it was a manual +1).
    Reels use this to cut straight to the action instead of playing the whole
    30s buffer. Best-effort — a reel without a sidecar just isn't trimmed."""
    import json
    try:
        with open(clip_path + ".json", "w", encoding="utf-8") as f:
            json.dump({"saved_epoch": float(saved_epoch),
                       "kills": [{"epoch": float(k.get("epoch", 0)),
                                  "manual": bool(k.get("manual", False))}
                                 for k in (kills or []) if k.get("epoch")]}, f)
    except OSError:
        pass


def _load_sidecar(clip_path: str):
    import json
    try:
        with open(clip_path + ".json", encoding="utf-8") as f:
            d = json.load(f)
        return float(d.get("saved_epoch", 0)), list(d.get("kills", []))
    except Exception:
        return 0.0, []


def _trim_start(dur, offsets_from_end, preroll: float,
                min_len: float = 6.0) -> float:
    """In-point (seconds) that starts a clip ~preroll before its EARLIEST kill.
    0.0 = don't trim. offsets_from_end: seconds between each kill and the end
    of the clip (save moment)."""
    if not dur or not offsets_from_end:
        return 0.0
    sane = [o for o in offsets_from_end if 0 <= o <= dur + 30]
    if not sane:
        return 0.0
    start = dur - max(sane) - preroll
    start = min(start, dur - min_len)   # never leave less than min_len
    if start < 1.0:                     # not worth a cut
        return 0.0
    return round(start, 2)


def preroll_for(clip: dict, preroll: float, manual_preroll: float,
                context_preroll: float, is_manual: bool = False) -> float:
    """How much lead-in this clip keeps before its first kill.

    A single ordinary kill gets the LONGEST lead-in, because with one kill the
    interesting part is the approach — the situation unfolding. A multikill needs
    less setup: the action is the content, and a long ramp just delays it.

    From Joe's review of a real reel: "for a kill montage it's a bit chaotic,
    like someone telling you something with no context... I personally like to
    watch the situation unfold if they're just normal kills. A short kill clip is
    cool but for tactical play I think a bit of a longer clip with more gameplay
    works better." The footage was always there — OBS saves ~30s and we were
    throwing away 17 of them on single kills."""
    if is_manual:
        return manual_preroll          # the press lands well after the kill
    return context_preroll if int(clip.get("kills", 1) or 1) <= 1 else preroll


def clip_trim_start(clip: dict, dur, ffmpeg, preroll: float = 8.0,
                    manual_preroll: float = 18.0,
                    context_preroll: float = 16.0) -> float:
    """Trim in-point for a reel clip from its sidecar (plus the sidecars of any
    clips merged into it by drop_overlapping). Manual +1 kills get a longer
    preroll — the button press lands well after the actual kill — and a lone
    ordinary kill gets a longer one too, so the setup is visible."""
    saved, kills = _load_sidecar(clip.get("path", ""))
    if not saved:
        return 0.0
    for fp in clip.get("_folded_paths", []) or []:
        _s, more = _load_sidecar(fp)
        kills += more
    if not kills:
        return 0.0
    offsets = [saved - k.get("epoch", 0) for k in kills]
    pre = preroll_for(clip, preroll, manual_preroll, context_preroll,
                      is_manual=any(k.get("manual") for k in kills))
    return _trim_start(dur, offsets, pre)


def drop_overlapping(clips, ffmpeg) -> list[dict]:
    """Merge consecutive clips whose Replay-Buffer footage overlaps.

    Every clip is a save of the last ~30s, so two saves a few seconds apart
    are mostly the SAME footage — in a reel that plays as duplicates ('the
    clip ends, then the end shows again later'). When the gap between save
    times is smaller than the later clip's length, the later clip contains
    everything the earlier one had: drop the earlier clip and credit its
    kills to the survivor. Clips without a parseable time are kept as-is."""
    clips = list(clips)
    if len(clips) < 2:
        return clips
    out = []
    i = 0
    while i < len(clips):
        cur = clips[i]
        t_cur = _save_time(cur.get("path", ""))
        merged = dict(cur)
        j = i + 1
        while j < len(clips):
            nxt = clips[j]
            t_nxt = _save_time(nxt.get("path", ""))
            if t_cur is None or t_nxt is None:
                break
            gap = t_nxt - t_cur
            dur = probe_duration(nxt["path"], ffmpeg) or 30.0
            if not (0 <= gap < dur - 3):
                break
            # nxt's buffer covers `merged` — fold it in and advance
            print(f"  [reel] overlap: {os.path.basename(merged['path'])} is inside "
                  f"{os.path.basename(nxt['path'])} (gap {gap:.0f}s) — merged")
            folded = dict(nxt)
            folded["kills"] = int(merged.get("kills", 1)) + int(nxt.get("kills", 1))
            tags = [t for t in (merged.get("tag", ""), nxt.get("tag", "")) if t]
            folded["tag"] = "+".join(dict.fromkeys("+".join(tags).split("+")))
            # keep the swallowed clip's path so its kill-timing sidecar still
            # informs the survivor's trim point
            folded["_folded_paths"] = (merged.get("_folded_paths", [])
                                       + [merged["path"]])
            merged = folded
            t_cur = t_nxt
            j += 1
        out.append(merged)
        i = j if j > i + 1 else i + 1
    return out


def _normalize_clips(clips) -> list[dict]:
    """Accept plain paths or {path, kills, tag} dicts."""
    out = []
    for c in clips:
        if isinstance(c, dict):
            out.append({"path": c["path"], "kills": int(c.get("kills", 1)),
                        "tag": c.get("tag", "kill")})
        else:
            out.append({"path": c, "kills": 1, "tag": "kill"})
    return [c for c in out if os.path.exists(c["path"])]


def _tag_rank(tag: str) -> int:
    for i, t in enumerate(TAG_PRIORITY):
        if t in tag:
            return i
    return len(TAG_PRIORITY)


def pick_potg(clips: list[dict]):
    """The best clip: most DISTINCT ENEMIES first, then total kills, then the
    flashier tag, then latest.

    Distinct downs lead deliberately. Scoring on the composite kill number let a
    single runner downed AND finished (down + elim + finisher popups) outrank
    genuinely downing two different people, because it racked up more scoring
    events off one enemy. What actually looks like a play is how many people you
    took out — Stan: "i downed a guy, then flipped around and downed another.
    That should probably be play of the night"."""
    if len(clips) < 2:
        return None

    def downs(c):
        if "downs" in c:
            return c["downs"]
        return sum(1 for t in str(c.get("tag", "")).split("+") if t == "down")

    return max(enumerate(clips),
               key=lambda ic: (downs(ic[1]), ic[1]["kills"],
                               -_tag_rank(ic[1]["tag"]), ic[0]))[1]


def find_music(music_dir: str) -> str:
    """A random music file from the folder — drop several in music/ and each
    reel gets a different soundtrack."""
    tracks = list_music(music_dir)
    if not tracks:
        return ""
    import random
    return random.choice(tracks)


def list_music(music_dir: str) -> list[str]:
    if not os.path.isdir(music_dir):
        return []
    return [os.path.join(music_dir, f) for f in sorted(os.listdir(music_dir))
            if f.lower().endswith(MUSIC_EXTS)]


def _ffprobe_path(ffmpeg: str) -> str:
    """ffprobe ships next to ffmpeg."""
    d, base = os.path.split(ffmpeg)
    probe = base.replace("ffmpeg", "ffprobe") if "ffmpeg" in base else "ffprobe"
    return os.path.join(d, probe) if d else probe


def probe_duration(path: str, ffmpeg: str):
    """Media duration in seconds, or None."""
    try:
        r = _run([_ffprobe_path(ffmpeg), "-v", "error", "-show_entries",
                  "format=duration", "-of", "csv=p=0", path])
        return float(r.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        pass
    # ffprobe.exe missing — the classic setup has only ffmpeg.exe copied out
    # of the zip, and that raised WinError 2 here and killed the whole reel.
    # ffmpeg itself prints the duration when asked to open the file.
    try:
        import re
        r = _run([ffmpeg, "-hide_banner", "-i", path])
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
        if m:
            return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                    + float(m.group(3)))
    except OSError:
        pass
    return None


def _music_inputs_and_chain(tracks: list[str], total: float, vol: float,
                            ffmpeg: str, first_input_index: int):
    """Build the soundtrack: random start points into each track, up to three
    tracks crossfaded across the reel, faded in over the intro card and out at
    the end. Returns (extra_cmd_args, [filter_chains]) producing [mus]."""
    import random

    XF = 2.0          # crossfade seconds between tracks
    k = 1 if total <= 150 else min(3, len(tracks))
    chosen = random.sample(tracks, k)
    seg = total / k + XF + 2  # overlap + tail headroom per segment

    args, chains, labels = [], [], []
    idx = first_input_index
    for j, t in enumerate(chosen):
        dur = probe_duration(t, ffmpeg) or 0
        # Start somewhere interesting, not the intro — but leave room to play
        # a full segment before the track loops back around.
        max_off = max(0.0, dur - seg - 4)
        off = random.uniform(min(10.0, max_off), max_off) if max_off > 0 else 0.0
        args += ["-stream_loop", "-1", "-ss", f"{off:.2f}", "-t", f"{seg:.2f}", "-i", t]
        chains.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                      f"volume={vol}[m{j}]")
        labels.append(f"[m{j}]")
        idx += 1

    cur = labels[0]
    for j in range(1, k):
        nxt = f"[mx{j}]"
        chains.append(f"{cur}{labels[j]}acrossfade=d={XF}{nxt}")
        cur = nxt

    fade_out_at = max(0.0, total - 2.5)
    chains.append(f"{cur}afade=t=in:d=1.5,afade=t=out:st={fade_out_at:.2f}:d=2.5[mus]")
    return args, chains


def build_match_reel(clips, out_path: str, ffmpeg: str,
                     title: str, kills: int, sub_lines: list[str],
                     wordmark_path: str = "", music_path: str = "",
                     music_volume: float = 0.08,
                     music_tracks: list[str] | None = None,
                     transitions: bool = True, chyrons: bool = True,
                     theme: dict | None = None, tight_cuts: bool = True,
                     preroll: float = 8.0, manual_preroll: float = 18.0,
                     context_preroll: float = 16.0) -> bool:
    """Title card [+ POTG card] + clips [+ music bed] -> one mp4.

    music_volume is 0-1 (0.08 = quiet bed under the game audio).
    music_tracks: pass the whole music library — the soundtrack starts at a
    random point in a random track, fades in/out, and long reels rotate
    through up to three tracks with crossfades. music_path (single file) is
    the legacy fallback."""
    clips = drop_overlapping(_normalize_clips(clips), ffmpeg)
    if not clips:
        print("  [reel] no clips on disk to build a reel from")
        return False
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    for i, c in enumerate(clips):        # kill numbers in match order,
        c["n"] = i + 1                   # assigned before the POTG reorder
    potg = pick_potg(clips)
    if potg is not None:
        clips = [potg] + [c for c in clips if c is not potg]

    stem = os.path.splitext(out_path)[0]
    cards = []  # (png_path, ok)
    title_png = stem + "_card.png"
    if _build_card(title_png, title, kills, "KILLS", sub_lines, wordmark_path,
                   theme=theme):
        cards.append(title_png)
    potg_png = stem + "_potg.png"
    have_potg_card = False
    if potg is not None:
        tag_txt = potg["tag"].replace("+", " + ").replace("_", " ").upper()
        if _build_card(potg_png, "PLAY OF THE GAME", potg["kills"],
                       "KILL" + ("S" if potg["kills"] != 1 else ""),
                       [tag_txt], wordmark_path, theme=theme):
            have_potg_card = True

    n_cards = len(cards) + (1 if have_potg_card else 0)
    if n_cards:
        print(f"  [reel] {n_cards} title/POTG card(s) built")
    else:
        print("  [reel] NO cards built (Pillow missing?) — reel will be clips only. "
              "Run: .venv\\Scripts\\python -m pip install pillow")

    # End card ("GG") closes the reel.
    end_png = stem + "_end.png"
    brand = str((theme or {}).get("display_name") or "WITNESS").upper()
    have_end_card = _build_card(end_png, brand, "GG", "",
                                ["WITNESSED."], wordmark_path,
                                theme=theme)
    END_SECONDS = 2.4

    # Segment order: title card -> (POTG card -> POTG clip) -> clips -> end card.
    segments = []  # {kind, path, dur, label}
    if cards:
        segments.append({"kind": "card", "path": title_png, "dur": CARD_SECONDS})
    if potg is not None and have_potg_card:
        segments.append({"kind": "card", "path": potg_png, "dur": CARD_SECONDS})
    for c in clips:
        tag_txt = c["tag"].replace("+", " + ").replace("_", " ").upper()
        label = (f"PLAY OF THE GAME - {tag_txt}" if c is potg
                 else f"KILL {c.get('n', '?')} - {tag_txt}")
        dur = probe_duration(c["path"], ffmpeg)
        # Tight cuts: start ~preroll before the earliest kill instead of
        # playing the whole 30s buffer (kill timing from the clip's sidecar).
        ss = (clip_trim_start(c, dur, ffmpeg, preroll, manual_preroll,
                              context_preroll)
              if tight_cuts else 0.0)
        if ss and dur:
            print(f"  [reel] tight cut: {os.path.basename(c['path'])} "
                  f"starts at {ss:.0f}s (was {dur:.0f}s long)")
            dur = dur - ss
        segments.append({"kind": "clip", "path": c["path"], "ss": ss,
                         "dur": dur, "label": label})
    if have_end_card:
        segments.append({"kind": "card", "path": end_png, "dur": END_SECONDS})

    # Broadcast chyron ("KILL 3 - PRECISION") on each clip, if this ffmpeg
    # build has drawtext.
    import shorts as _shorts
    font = _shorts._find_font()
    use_chyrons = bool(chyrons and font and _shorts._has_drawtext(ffmpeg))

    def _chyron(label: str) -> str:
        ff = font.replace(":", r"\:")
        txt = label.replace("'", "").replace(":", r"\:")
        a = ("'if(lt(t,0.4),0,if(lt(t,0.9),(t-0.4)*2,"
             "if(lt(t,4.2),1,if(lt(t,4.9),(4.9-t)/0.7,0))))'")
        return (f",drawtext=fontfile='{ff}':text='{txt}':fontsize=52:fontcolor=white:"
                f"borderw=4:bordercolor=black@0.85:x=64:y=h-150:alpha={a}")

    cmd = [ffmpeg, "-y"]
    for seg in segments:
        if seg["kind"] == "card":
            cmd += ["-loop", "1", "-framerate", "60", "-t", str(seg["dur"]), "-i", seg["path"],
                    "-f", "lavfi", "-t", str(seg["dur"]), "-i", "anullsrc=r=48000:cl=stereo"]
        elif seg.get("ss"):
            # -t pins the segment to EXACTLY the duration the xfade offsets
            # below were computed from (full - ss). Without it, input seeking
            # can land a little off that mark and every later transition drifts,
            # which reads as clips cutting early / jumping.
            trim = ["-ss", f"{seg['ss']:.3f}"]
            if seg.get("dur"):
                trim += ["-t", f"{seg['dur']:.3f}"]
            cmd += trim + ["-i", seg["path"]]
        else:
            cmd += ["-i", seg["path"]]

    # Cards consume two inputs each (image + silence); clips consume one.
    n_seg = len(segments)
    use_xfade = bool(transitions) and all(s["dur"] is not None for s in segments) and n_seg > 1
    in_i = 0
    chains = []
    for si, seg in enumerate(segments):
        if seg["kind"] == "card":
            # In xfade mode the transitions handle the blend; only the very
            # first fade-in stays.
            if use_xfade:
                fade = ",fade=t=in:d=0.4" if si == 0 else ""
            else:
                fade = f",fade=t=in:d=0.4,fade=t=out:st={seg['dur'] - 0.4}:d=0.4"
            chains.append(f"[{in_i}:v]scale=1920:1080,setsar=1,fps=60,format=yuv420p{fade}[v{si}];"
                          f"[{in_i + 1}:a]anull[a{si}]")
            in_i += 2
        else:
            dt = _chyron(seg["label"]) if use_chyrons else ""
            chains.append(f"[{in_i}:v]scale=1920:1080,setsar=1,fps=60,format=yuv420p{dt}[v{si}];"
                          f"[{in_i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{si}]")
            in_i += 1

    if use_xfade:
        XF = 0.5
        prev_v, prev_a, acc = "[v0]", "[a0]", segments[0]["dur"]
        for k in range(1, n_seg):
            nv, na = f"[vx{k}]", f"[ax{k}]"
            chains.append(f"{prev_v}[v{k}]xfade=transition=fade:duration={XF}:"
                          f"offset={acc - XF:.3f}{nv}")
            chains.append(f"{prev_a}[a{k}]acrossfade=d={XF}{na}")
            prev_v, prev_a = nv, na
            acc += segments[k]["dur"] - XF
        total = acc
        chains.append(f"{prev_v}fade=t=out:st={max(0.0, total - 0.7):.3f}:d=0.7[v]")
        chains.append(f"{prev_a}anull[cat]")
    else:
        pairs = "".join(f"[v{i}][a{i}]" for i in range(n_seg))
        chains.append(f"{pairs}concat=n={n_seg}:v=1:a=1[v][cat]")
        durs = [s["dur"] for s in segments]
        total = sum(durs) if all(d is not None for d in durs) else None

    a_out = "[cat]"
    tracks = [t for t in (music_tracks or ([music_path] if music_path else []))
              if t and os.path.exists(t)]
    if tracks:
        vol = max(0.0, min(1.0, float(music_volume)))
        if total is not None:
            m_args, m_chains = _music_inputs_and_chain(tracks, total, vol, ffmpeg, in_i)
            cmd += m_args
            chains += m_chains
        else:
            cmd += ["-stream_loop", "-1", "-i", tracks[0]]
            chains.append(f"[{in_i}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                          f"volume={vol},afade=t=in:d=1.5[mus]")
        chains.append("[cat][mus]amix=inputs=2:duration=first:normalize=0[mixed]")
        a_out = "[mixed]"

    cmd += ["-filter_complex", ";".join(chains), "-map", "[v]", "-map", a_out,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path]

    r = _run(cmd)
    for png in (title_png, potg_png, end_png):
        try:
            os.remove(png)
        except OSError:
            pass
    if r.returncode == 0 and os.path.exists(out_path):
        return True
    tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
    print(f"  [reel] ffmpeg failed: {tail}")
    # Degrade gracefully rather than produce nothing: drop chyrons first
    # (drawtext is the flakiest across ffmpeg builds), then transitions.
    if use_chyrons:
        print("  [reel] retrying without chyrons...")
        return build_match_reel(clips, out_path, ffmpeg, title, kills, sub_lines,
                                wordmark_path, music_path, music_volume,
                                music_tracks, transitions=transitions, chyrons=False)
    if use_xfade:
        print("  [reel] retrying without transitions...")
        return build_match_reel(clips, out_path, ffmpeg, title, kills, sub_lines,
                                wordmark_path, music_path, music_volume,
                                music_tracks, transitions=False, chyrons=False)
    return False


def add_announcer(reel_path: str, out_path: str, tts_wav: str, ffmpeg: str) -> bool:
    """Mix a TTS voiceover over the reel's opening. Video is stream-copied so
    this is fast; only the audio re-encodes."""
    cmd = [ffmpeg, "-y", "-i", reel_path, "-i", tts_wav,
           "-filter_complex",
           "[1:a]adelay=400|400,volume=1.6[tts];"
           "[0:a][tts]amix=inputs=2:duration=first:normalize=0[a]",
           "-map", "0:v", "-map", "[a]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", out_path]
    r = _run(cmd)
    if r.returncode == 0 and os.path.exists(out_path):
        return True
    tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
    print(f"  [reel] announcer mix failed: {tail}")
    return False
