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

from matchcard import _font, _text_w, BG, LINE, TEXT, MUTED, ACCENT

CARD_SECONDS = 2.8
MUSIC_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")

# flashier tags win Play of the Game ties
TAG_PRIORITY = ("finisher", "precision", "down", "kill", "assist", "manual")


def _run(cmd) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _build_card(out_png: str, title: str, kills, kills_label: str,
                sub_lines: list[str], wordmark_path: str = "") -> bool:
    """1920x1080 stat card in the match-card style."""
    try:
        from PIL import Image, ImageDraw

        W, H = 1920, 1080
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        pad = 110

        d.rectangle([0, 0, W, 10], fill=ACCENT)
        d.rectangle([0, H - 10, W, H], fill=ACCENT)

        y = pad
        if wordmark_path and os.path.exists(wordmark_path):
            try:
                wm = Image.open(wordmark_path).convert("RGBA")
                scale = 64 / wm.height
                wm = wm.resize((int(wm.width * scale), 64), Image.LANCZOS)
                img.paste(wm, (pad, y), wm)
            except Exception:
                d.text((pad, y), "MARATHON", font=_font("black", 56), fill=ACCENT)
        else:
            d.text((pad, y), "MARATHON", font=_font("black", 56), fill=ACCENT)

        d.text((pad, y + 110), title, font=_font("black", 110), fill=TEXT)

        kf = _font("black", 380)
        ks = str(kills)
        d.text((pad - 10, 360), ks, font=kf, fill=ACCENT)
        d.text((pad + _text_w(d, ks, kf) + 40, 640), kills_label,
               font=_font("bold", 64), fill=TEXT)

        ly = 880
        for line in sub_lines[:2]:
            d.text((pad, ly), line, font=_font("mono", 40), fill=MUTED)
            ly += 58

        d.line([pad, 840, W - pad, 840], fill=LINE, width=2)
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
    if not os.path.isdir(music_dir):
        return ""
    tracks = [f for f in os.listdir(music_dir) if f.lower().endswith(MUSIC_EXTS)]
    if not tracks:
        return ""
    import random
    return os.path.join(music_dir, random.choice(tracks))


def build_match_reel(clips, out_path: str, ffmpeg: str,
                     title: str, kills: int, sub_lines: list[str],
                     wordmark_path: str = "", music_path: str = "",
                     music_volume: float = 0.08) -> bool:
    """Title card [+ POTG card] + clips [+ music bed] -> one mp4.

    music_volume is 0-1 (0.08 = quiet bed under the game audio)."""
    clips = _normalize_clips(clips)
    if not clips:
        print("  [reel] no clips on disk to build a reel from")
        return False
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    potg = pick_potg(clips)
    if potg is not None:
        clips = [potg] + [c for c in clips if c is not potg]

    stem = os.path.splitext(out_path)[0]
    cards = []  # (png_path, ok)
    title_png = stem + "_card.png"
    if _build_card(title_png, title, kills, "KILLS", sub_lines, wordmark_path):
        cards.append(title_png)
    potg_png = stem + "_potg.png"
    have_potg_card = False
    if potg is not None:
        tag_txt = potg["tag"].replace("+", " + ").replace("_", " ").upper()
        if _build_card(potg_png, "PLAY OF THE GAME", potg["kills"],
                       "KILL" + ("S" if potg["kills"] != 1 else ""),
                       [tag_txt], wordmark_path):
            have_potg_card = True

    # Input plan: title card, [clip0 (potg)], [potg card inserted BEFORE clip0]...
    # Segment order: title card -> (potg card -> potg clip) -> remaining clips.
    segments = []  # ("card", png) | ("clip", path)
    if cards:
        segments.append(("card", title_png))
    if potg is not None and have_potg_card:
        segments.append(("card", potg_png))
    for c in clips:
        segments.append(("clip", c["path"]))

    cmd = [ffmpeg, "-y"]
    for kind, path in segments:
        if kind == "card":
            cmd += ["-loop", "1", "-framerate", "60", "-t", str(CARD_SECONDS), "-i", path,
                    "-f", "lavfi", "-t", str(CARD_SECONDS), "-i", "anullsrc=r=48000:cl=stereo"]
        else:
            cmd += ["-i", path]

    # Cards consume two inputs each (image + silence); clips consume one.
    in_i = 0
    chains = []
    for si, (kind, path) in enumerate(segments):
        if kind == "card":
            fade = (f",fade=t=in:d=0.4,fade=t=out:st={CARD_SECONDS - 0.4}:d=0.4")
            chains.append(f"[{in_i}:v]scale=1920:1080,setsar=1,format=yuv420p{fade}[v{si}];"
                          f"[{in_i + 1}:a]anull[a{si}]")
            in_i += 2
        else:
            chains.append(f"[{in_i}:v]scale=1920:1080,setsar=1,fps=60,format=yuv420p[v{si}];"
                          f"[{in_i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{si}]")
            in_i += 1

    pairs = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
    chains.append(f"{pairs}concat=n={len(segments)}:v=1:a=1[v][cat]")

    a_out = "[cat]"
    if music_path and os.path.exists(music_path):
        cmd += ["-stream_loop", "-1", "-i", music_path]
        vol = max(0.0, float(music_volume))
        chains.append(f"[{in_i}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                      f"volume={vol}[mus]")
        chains.append("[cat][mus]amix=inputs=2:duration=first:normalize=0[mixed]")
        a_out = "[mixed]"

    cmd += ["-filter_complex", ";".join(chains), "-map", "[v]", "-map", a_out,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path]

    r = _run(cmd)
    for png in (title_png, potg_png):
        try:
            os.remove(png)
        except OSError:
            pass
    if r.returncode == 0 and os.path.exists(out_path):
        return True
    tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
    print(f"  [reel] ffmpeg failed: {tail}")
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
