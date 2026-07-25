"""Teach-a-game wizard: point the app at ANY game in about two minutes.

    python main.py --teach        (or the TEACH A GAME button in the app)

The flow: you name the game, alt-tab in, and get a kill while the wizard
watches the screen and records every piece of text it reads — what it said,
where it was, and how long it stayed up. Kill popups are TRANSIENT (they
flash and vanish) while HUD text is persistent, so the wizard can rank the
likely candidates itself; you just pick the line that appeared because of
your kill. From that it derives the stable trigger phrase, measures the
popup's screen region, checks for a reward marker (+XP style), and writes a
ready-to-run profile to games/<game>.yaml.

Profiles are pure data — share yours, PR it to the repo, and the app
supports a game its author has never played.
"""

from __future__ import annotations

import os
import re
import time

WATCH_SECONDS = 90
BASE = os.path.dirname(os.path.abspath(__file__))

# Feature gates that are Marathon-specific parsing — a taught game starts
# with them off (its exfil/deploy screens haven't been mapped).
PROFILE_DEFAULTS = {
    "capture_exfil_stats": False,
    "runner_detection": False,
    "squad_stats": False,
    "team_wipe": False,
    "track_names": False,
    "auto_sweat": False,      # reads Marathon's squad panel — needs mapping
}


def _norm(s: str) -> str:
    s = re.sub(r"\s+", " ", s.lower().strip())
    return re.sub(r"[^a-z0-9 +]", "", s)


def stable_phrase(raw: str) -> str:
    """The part of a popup that repeats every kill: drop reward numbers and
    counters ('RUNNER DOWN +15 XP' -> 'RUNNER DOWN')."""
    keep = []
    for tok in raw.split():
        t = tok.strip()
        if not t or t.startswith("+") or any(c.isdigit() for c in t):
            continue
        if t.lower() in ("xp", "pts", "points"):
            continue
        keep.append(re.sub(r"[^A-Za-z' \-]", "", t))
    return " ".join(x for x in keep if x).upper().strip()


def variant_group(raw: str, raws, threshold: int = 62) -> list:
    """Every sighting that looks like the SAME popup as `raw`.

    Many games print the victim's name in the kill popup ("KNOCKED DOWN
    xXTTVGamerXx"). Each kill then reads as a different line, so the wizard sees
    them as unrelated candidates — and a phrase derived from one of them matches
    that one player and nobody else, which is a profile that silently detects
    nothing. Grouping the variants is what exposes the part that actually
    repeats."""
    from rapidfuzz import fuzz
    base = _norm(raw)
    out = []
    for other in raws:
        o = _norm(other)
        if not o:
            continue
        if o == base or fuzz.ratio(base, o) >= threshold or fuzz.partial_ratio(base, o) >= 88:
            out.append(other)
    return out or [raw]


def common_phrase(raws) -> str:
    """The trigger phrase shared by every variant, in order — per-kill text
    (victim names) drops out because it isn't in all of them."""
    seqs = [stable_phrase(r).split() for r in raws if stable_phrase(r)]
    seqs = [s for s in seqs if s]
    if not seqs:
        return ""
    if len(seqs) == 1:
        return " ".join(seqs[0])
    common = set(seqs[0])
    for s in seqs[1:]:
        common &= set(s)
    if not common:
        return ""
    # keep the first variant's ordering
    return " ".join(t for t in seqs[0] if t in common).strip()


def looks_name_bearing(phrase: str, variants_seen: int) -> bool:
    """True when a single-sighting phrase probably still carries a victim name,
    so the wizard can say so instead of writing a profile that never matches."""
    if variants_seen > 1:
        return False          # variants already removed anything per-kill
    toks = phrase.split()
    if len(toks) < 2:
        return False
    tail = toks[-1]
    # A trailing token that isn't a short common kill word is suspect: real
    # triggers read "RUNNER DOWN" / "KNOCKED DOWN" / "YOU KILLED".
    KILLWORDS = {"DOWN", "DOWNED", "KILLED", "KILL", "ELIMINATED", "ELIM",
                 "FINISHER", "HEADSHOT", "KNOCKED", "TAKEDOWN", "NEUTRALIZED",
                 "DEFEATED", "YOU", "ENEMY", "TARGET", "PRECISION", "ASSIST"}
    return tail.upper() not in KILLWORDS


def has_reward(raw: str) -> bool:
    t = raw.lower()
    return ("xp" in t) or (re.search(r"\+\s*\d", t) is not None)


def region_around(bboxes_frac, pad_x: float = 0.05, pad_y: float = 0.03) -> dict:
    """A detect region (fractions) around the chosen popup lines, padded so
    wider variants of the same popup (assist tags etc.) still fit."""
    x0 = min(b[0] for b in bboxes_frac) - pad_x
    y0 = min(b[1] for b in bboxes_frac) - pad_y
    x1 = max(b[2] for b in bboxes_frac) + pad_x
    y1 = max(b[3] for b in bboxes_frac) + pad_y
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(1.0, x1), min(1.0, y1)
    return {"x": round(x0, 3), "y": round(y0, 3),
            "w": round(x1 - x0, 3), "h": round(y1 - y0, 3)}


def rank_candidates(seen: dict, total_frames: int) -> list:
    """Transient, name-able lines first. seen: norm_text -> entry dict with
    count/raw/bbox(frac)/first/last. HUD text (visible most of the watch) and
    tiny scraps are dropped; the rest sort by how popup-like they are:
    rarer on screen and nearer screen center wins."""
    out = []
    for e in seen.values():
        frac_visible = e["count"] / max(1, total_frames)
        alpha = sum(c.isalpha() for c in e["raw"])
        if alpha < 4 or len(e["raw"]) < 4:
            continue
        if frac_visible > 0.5:          # persistent = HUD, not a popup
            continue
        cx = (e["bbox"][0] + e["bbox"][2]) / 2
        cy = (e["bbox"][1] + e["bbox"][3]) / 2
        center_dist = abs(cx - 0.5) + abs(cy - 0.5)
        out.append((frac_visible + center_dist, e))
    return [e for _, e in sorted(out, key=lambda t: t[0])]


def _watch(cfg, seconds: int, engine, grab):
    """OCR the full frame in a loop, merging what's read into per-line
    sightings. Returns (seen dict, frames_read)."""
    seen: dict = {}
    frames = 0
    t_end = time.monotonic() + seconds
    last_note = seconds
    while time.monotonic() < t_end:
        remain = int(t_end - time.monotonic())
        if remain <= last_note - 15:
            last_note = remain
            print(f"   ...still watching ({remain}s left)")
        frame = grab(cfg)
        h, w = frame.shape[:2]
        frames += 1
        now = time.monotonic()
        for raw, (x0, y0, x1, y1) in engine.read_boxes(frame, max_dim=0):
            key = _norm(raw)
            if not key:
                continue
            bb = (x0 / w, y0 / h, x1 / w, y1 / h)
            e = seen.get(key)
            if e is None:
                seen[key] = {"raw": raw, "count": 1, "bbox": bb,
                             "first": now, "last": now}
            else:
                e["count"] += 1
                e["last"] = now
                e["bbox"] = (min(e["bbox"][0], bb[0]), min(e["bbox"][1], bb[1]),
                             max(e["bbox"][2], bb[2]), max(e["bbox"][3], bb[3]))
        time.sleep(0.1)
    return seen, frames


def profile_yaml(name: str, phrases, region: dict, reward: bool) -> str:
    """The games/<slug>.yaml text — commented, human-editable, PR-able."""
    ph = "\n".join(f'  - "{p}"' for p in phrases)
    gates = "\n".join(f"{k}: {str(v).lower()}" for k, v in PROFILE_DEFAULTS.items())
    return f"""# {name} — game profile for the Kill Recorder
# Made with the teach-a-game wizard. Everything here overrides config.yaml
# while  game: {slugify(name)}  is set. Share it: PR this file to the repo
# and everyone gets {name} support.

game_name: "{name}"

# Text that means YOU got a kill (fuzzy-matched, so OCR slips still count).
popup_trigger_phrases:
{ph}
popup_match_threshold: 85

# Where on screen the kill popup appears (fractions of the frame).
detect_region_frac:
  x: {region['x']}
  y: {region['y']}
  w: {region['w']}
  h: {region['h']}

# Kill popups in this game {'show' if reward else 'do NOT show'} a reward marker (+XP style).
require_reward: {str(reward).lower()}

# Screens where kills can't happen (death / match summary). Add phrases here
# if something false-triggers, e.g. a results screen re-showing kill text.
suppress_phrases: []

# Look & feel — the dashboard, on-screen banners, and reel stat cards all
# take this game's colors. Hex values; make it yours.
#   accent = kill counter / banners / highlights   danger = death-red alerts
theme:
  display_name: "{name.upper()}"
  accent: "#9c58da"
  danger: "#ff4d3d"
  bg: "#0b0f12"
  panel: "#12181d"
  line: "#232d34"
  text: "#e8edf0"
  muted: "#7d8a94"

# Marathon-specific systems (exfil stat parsing, runner detection, kill-feed
# names) stay off until this game's screens are mapped.
{gates}
"""


def verify_profile(cfg, phrases, region, reward, seconds: int = 45,
                   engine=None, grab=None, on_tick=None) -> dict:
    """Prove the profile before the user trusts it: watch the REAL detect region
    with the REAL detector and count kills.

    Without this the wizard's promise is unfalsifiable — you'd only find out a
    profile was wrong after playing a whole session that recorded nothing, which
    is exactly how a feature like this earns a reputation for empty promises.
    Returns {'fired': n, 'reads': [...], 'frames': n}."""
    from detector import PopupDetector
    if engine is None:
        from ocr import OCREngine
        engine = OCREngine(cfg.get("ocr_engine", "easyocr"),
                           cfg.get("ocr_upscale", 3),
                           max_dim=cfg.get("ocr_max_dim", 800))
    if grab is None:
        from exfil_stats import _grab_full, _crop
        def grab(c):
            return _crop(_grab_full(c), region)

    det = PopupDetector(
        trigger_phrases=phrases,
        phrase_match_threshold=cfg.get("popup_match_threshold", 85),
        absence_frames=cfg.get("popup_absence_frames", 3),
        confirm_frames=cfg.get("popup_confirm_frames", 1),
        require_reward=reward,
        cooldown_seconds=cfg.get("popup_cooldown_seconds", 2.0),
    )
    fired, reads, frames = 0, [], 0
    interval = 1.0 / max(1, int(cfg.get("poll_fps", 5)))
    t_end = time.monotonic() + seconds
    while time.monotonic() < t_end:
        start = time.monotonic()
        try:
            lines = engine.read_lines(grab(cfg))
        except Exception:
            lines = []
        frames += 1
        if lines:
            reads.append(" | ".join(lines))
        got = det.process_frame_all(lines, now=start)
        fired += len(got)
        if got and on_tick:
            on_tick(fired)
        rest = interval - (time.monotonic() - start)
        if rest > 0:
            time.sleep(rest)
    return {"fired": fired, "reads": reads[-8:], "frames": frames}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "game"


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def run(cfg) -> None:
    from exfil_stats import _grab_full
    from ocr import OCREngine

    print()
    print("=" * 60)
    print("  TEACH IT YOUR GAME")
    print("=" * 60)
    print("In ~2 minutes this wizard learns any game's kill popup and")
    print("writes a profile for it. Best done in a bot match / practice")
    print("range — anywhere you can get a kill on demand.")
    print()
    name = _ask("What game is this? (e.g. Arc Raiders): ")
    if not name:
        print("No name given — wizard cancelled.")
        return
    slug = slugify(name)

    print()
    print(f"Alright. When you press Enter you'll have {WATCH_SECONDS} seconds:")
    print("ALT-TAB into the game and GET A KILL. I'll watch the screen the")
    print("whole time and write down every piece of text I see.")
    _ask("Press Enter when you're ready... ")
    print("\nWatching. Go get 'em.")

    # Full-frame reads at native resolution: no upscale (a 4K frame is
    # already plenty for UI text, and 3x on a full frame would be too slow).
    engine = OCREngine(cfg.get("ocr_engine", "easyocr"), upscale=1)
    seen, frames = _watch(cfg, WATCH_SECONDS, engine, _grab_full)
    print(f"\nDone — read {len(seen)} distinct line(s) across {frames} frames.")

    cands = rank_candidates(seen, frames)[:15]
    if not cands:
        print("Nothing popup-like was read. Usual causes: no kill happened, or")
        print("the game isn't on the monitor set by monitor_index in config.yaml.")
        print("Run the wizard again when you can get a kill inside the window.")
        return

    print("\nMost popup-like lines I saw (rarest + most central first):")
    for i, e in enumerate(cands, 1):
        secs = max(0.0, e["last"] - e["first"])
        print(f"  {i:2d}. {e['raw']!r}   (on screen ~{secs:.0f}s)")
    print()
    picks = _ask("Which line(s) appeared BECAUSE of your kill? "
                 "(numbers, comma-separated, or Enter to cancel): ")
    idx = []
    for p in picks.replace(",", " ").split():
        if p.isdigit() and 1 <= int(p) <= len(cands):
            idx.append(int(p) - 1)
    if not idx:
        print("Nothing picked — wizard cancelled, nothing written.")
        return
    chosen = [cands[i] for i in idx]

    # Derive each phrase from ALL sightings of the same popup, not just the one
    # line picked: if the game prints the victim's name, only the invariant part
    # survives. (A phrase with a name in it matches one player and nobody else.)
    all_raws = [e["raw"] for e in seen.values()]
    phrases, group_bboxes, name_risk = [], [], []
    for c in chosen:
        variants = variant_group(c["raw"], all_raws)
        ph = common_phrase(variants) or stable_phrase(c["raw"])
        if not ph:
            continue
        phrases.append(ph)
        if len(variants) > 1:
            print(f"  saw this popup {len(variants)}x with differing text "
                  f"— the repeating part is {ph!r}")
            for v in variants:                # widen the region over variants
                e = seen.get(_norm(v))
                if e:
                    group_bboxes.append(e["bbox"])
        if looks_name_bearing(ph, len(variants)):
            name_risk.append(ph)
    phrases = sorted(set(phrases))
    print(f"\nTrigger phrase(s) I derived: {phrases}")
    if name_risk:
        print("  NOTE: I only saw that popup once, so I can't tell a victim's")
        print("  name from the words that always appear. If any of these ends")
        print("  in a player's name, retype just the part that never changes")
        print(f"  (e.g. 'KNOCKED DOWN Someguy' -> 'KNOCKED DOWN'): {name_risk}")
    extra = _ask("Look right? (Enter = yes, or type corrected phrases "
                 "separated by commas): ")
    if extra:
        phrases = sorted({p.strip().upper() for p in extra.split(",") if p.strip()})

    region = region_around([c["bbox"] for c in chosen] + group_bboxes)
    reward = all(has_reward(c["raw"]) for c in chosen)

    # PROVE IT before writing anything. A profile that looks plausible but
    # never fires is worse than no profile — the user finds out after a wasted
    # session, and the feature reads as an empty promise.
    print()
    print("Let's prove it works before you rely on it.")
    print(f"Press Enter, alt-tab back in, and get ONE more kill. I'll watch the")
    print(f"region I just measured with the real detector for 45 seconds.")
    if _ask("Press Enter to verify (or type 'skip'): ").lower() != "skip":
        res = verify_profile(cfg, phrases, region, reward, seconds=45)
        if res["fired"]:
            print(f"\n  CONFIRMED — detected {res['fired']} kill(s). "
                  "This profile works.")
        else:
            print(f"\n  NOTHING DETECTED in {res['frames']} frames.")
            if res["reads"]:
                print("  What I could read in that region:")
                for r in res["reads"]:
                    print(f"    {r!r}")
                print("  The popup is in the region but the phrase didn't match —")
                print("  re-run the wizard and correct the phrase when asked.")
            else:
                print("  The region read NOTHING, so the popup is probably")
                print("  outside it (or no kill happened in the window).")
                print("  Re-run the wizard and pick the kill line again.")
            if _ask("  Save this profile anyway? [y/N]: ").lower() not in ("y", "yes"):
                print("  Nothing written. Run the wizard again when ready.")
                return

    os.makedirs(os.path.join(BASE, "games"), exist_ok=True)
    path = os.path.join(BASE, "games", f"{slug}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(profile_yaml(name, phrases, region, reward))
    print(f"\nProfile written: {path}")

    switch = _ask(f"Switch the app to {name} now? [Y/n]: ").lower()
    if switch in ("", "y", "yes"):
        from main import save_setting_overrides
        save_setting_overrides({"game": slug})
        print(f"Done — the app now detects {name}. Restart it and play.")
        print('(Back to Marathon anytime: set  game: marathon  in the dashboard'
              " settings file, settings_override.yaml.)")
    else:
        print(f"Profile saved but not active. Activate it later with"
              f"  game: {slug}  in settings_override.yaml.")
    print("\nIf kills get missed or false-fire, run the wizard again — or send")
    print("me the session log. Happy hunting.")
