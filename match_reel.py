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
    first = tag.split("+")[0]
    for i, t in enumerate(TAG_PRIORITY):
        if t in tag:
            return i
    return len(TAG_PRIORITY)


def pick_potg(clips: list[dict]):
    """The clip with the most kills; ties go to the flashier tag, then latest."""
    if len(clips) < 2:
        return None
    return max(enumerate(clips),
               key=lambda ic: (ic[1]["kills"], -_tag_rank(ic[1]["tag"]), ic[0]))[1]


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
                     theme: dict | None = None) -> bool:
    """Title card [+ POTG card] + clips [+ music bed] -> one mp4.

    music_volume is 0-1 (0.08 = quiet bed under the game audio).
    music_tracks: pass the whole music library — the soundtrack starts at a
    random point in a random track, fades in/out, and long reels rotate
    through up to three tracks with crossfades. music_path (single file) is
    the legacy fallback."""
    clips = _normalize_clips(clips)
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
        segments.append({"kind": "clip", "path": c["path"],
                         "dur": probe_duration(c["path"], ffmpeg), "label": label})
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
