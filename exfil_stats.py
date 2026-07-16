"""Capture the EXFILTRATED summary screen at match end and log its stats.

The detection crop only sees the panel's label column, so when the summary
screen shows up we take one full-screen grab, OCR the whole stat panel, and
parse the numbers. That gives a per-match record (elims, downs, damage, run
time) in stats/match_stats.csv — and a self-audit: if the game says 3 downs
and we only clipped 2, the console says so.
"""

from __future__ import annotations

import csv
import os
import re
import time

from rapidfuzz import fuzz

# The stat panel region as fractions of the full frame (generous margins).
PANEL_FRAC = {"x": 0.34, "y": 0.46, "w": 0.32, "h": 0.44}

# Trios layout: three panels side by side, local player ALWAYS center.
# Calibrated from a real 16:9 trios exfil screenshot.
SQUAD_PANELS = {
    "left":   {"x": 0.030, "y": 0.46, "w": 0.310, "h": 0.44},
    "center": {"x": 0.340, "y": 0.46, "w": 0.320, "h": 0.44},
    "right":  {"x": 0.660, "y": 0.46, "w": 0.310, "h": 0.44},
}
# Name plates ("SupremePlays#5291") sit above the character models.
NAME_STRIP = {
    "left":   {"x": 0.030, "y": 0.085, "w": 0.310, "h": 0.085},
    "center": {"x": 0.340, "y": 0.085, "w": 0.320, "h": 0.085},
    "right":  {"x": 0.660, "y": 0.085, "w": 0.310, "h": 0.085},
}

# CSV column -> label as it appears on the exfil screen.
LABELS = {
    "combatant_elims": "Combatant Eliminations",
    "runner_elims": "Runner Eliminations",
    "runners_downed": "Runners Downed",
    "runner_damage": "Runner Damage",
    "crew_revives": "Crew Revives",
    "inventory_value": "Inventory Value",
}

# Labels the small detection crop CAN see — used to recognize the screen.
_DETECT_WORDS = ("runner damage", "crew revives", "inventory value", "exfiltrated")


def looks_like_exfil(lines) -> bool:
    """True if the OCR'd detection-crop text looks like the exfil summary."""
    blob = " ".join(lines).lower()
    if len(blob) < 8:
        return False
    return any(fuzz.partial_ratio(w, blob) >= 85 for w in _DETECT_WORDS)


def _label_text(line: str) -> str:
    """The alphabetic part of a line (drops the trailing number), for matching
    a stat label against 'Combatant Eliminations 14' etc."""
    return re.sub(r"[^a-z ]", " ", line.lower()).strip()


def _num(text: str):
    """Last integer in a line ('Inventory Value 9,015' -> 9015)."""
    m = re.findall(r"[\d][\d,\.]*", text)
    if not m:
        return None
    try:
        return int(re.sub(r"[^\d]", "", m[-1]))
    except ValueError:
        return None


def parse_exfil_lines(lines) -> dict:
    """Fuzzy-match each stat label to an OCR line and pull its number.
    A label whose number OCR'd onto the following line is handled too."""
    stats = {}
    lines = [l.strip() for l in lines if l.strip()]
    used = set()
    # For each stat, pick the SINGLE best-matching line by full-string ratio
    # (not partial_ratio — "Runner Eliminations" and "Combatant Eliminations"
    # share "Eliminations", so partial matching cross-assigns their numbers).
    for key, label in LABELS.items():
        best_i, best_score = None, 0
        for i, line in enumerate(lines):
            if i in used:
                continue
            score = fuzz.ratio(label.lower(), _label_text(line))
            if score > best_score:
                best_score, best_i = score, i
        if best_i is not None and best_score >= 75:
            val = _num(lines[best_i])
            if (val is None and best_i + 1 < len(lines)
                    and re.fullmatch(r"[\d,\.]+", lines[best_i + 1].strip())):
                val = _num(lines[best_i + 1])
            if val is not None:
                stats[key] = val
                used.add(best_i)
    # Run time reads like "Run Time 22:50"
    for line in lines:
        if fuzz.partial_ratio("run time", line.lower()) >= 82:
            m = re.search(r"(\d{1,2})[:;](\d{2})", line)
            if m:
                stats["run_time"] = f"{m.group(1)}:{m.group(2)}"
            break
    return stats


def _grab_full(cfg):
    if cfg.get("capture_source") == "screen":
        from capture import grab_full_screenshot
        return grab_full_screenshot(cfg.get("monitor_index", 1))
    from capture import grab_full_virtualcam
    return grab_full_virtualcam(cfg.get("obs_virtualcam_index", 0))


def _crop(frame, frac):
    H, W = frame.shape[:2]
    x, y = int(frac["x"] * W), int(frac["y"] * H)
    w, h = int(frac["w"] * W), int(frac["h"] * H)
    return frame[y:y + h, x:x + w]


def _parse_panel(frame, engine) -> dict:
    return parse_exfil_lines(engine.read_lines(_crop(frame, PANEL_FRAC)))


def _read_player_name(frame, engine, pos: str) -> str:
    """The gamertag from a panel's name plate ('SupremePlays#5291')."""
    lines = engine.read_lines(_crop(frame, NAME_STRIP[pos]))
    for line in lines:
        for tok in line.replace("|", " ").split():
            if "#" in tok and len(tok) > 3:
                return tok.strip()
    # no #tag read: take the longest word-ish token as a best effort
    toks = [t for l in lines for t in l.replace("|", " ").split() if len(t) >= 4]
    return max(toks, key=len) if toks else ""


def parse_squad(frame, engine) -> list[dict]:
    """All player panels on the exfil screen. Solo -> just you (center).
    Returns [{position, name, is_you, **stats}] for panels that parsed."""
    players = []
    for pos, frac in SQUAD_PANELS.items():
        stats = parse_exfil_lines(engine.read_lines(_crop(frame, frac)))
        if len(stats) < 3:      # panel not present (solo/duo) or unreadable
            continue
        players.append({"position": pos,
                        "name": _read_player_name(frame, engine, pos),
                        "is_you": pos == "center", **stats})
    return players


def capture_exfil_stats(cfg, engine, save_dir: str = "", retries: int = 3):
    """Grab the exfil screen and OCR the stat panels. The panel animates in,
    so retry a few times and keep the first good parse. Returns
    (your_stats, squad) — squad is every readable panel (trios: all three,
    with gamertags). Saves the screen PNG once if save_dir given."""
    saved = False
    best = {}
    squad = []
    for attempt in range(max(1, retries)):
        frame = _grab_full(cfg)
        if save_dir and not saved:
            try:
                import cv2
                os.makedirs(save_dir, exist_ok=True)
                shot = os.path.join(save_dir, f"exfil_{time.strftime('%H-%M-%S')}.png")
                cv2.imwrite(shot, frame)
                print(f"  [exfil] screen saved -> {shot}")
                saved = True
            except Exception as e:
                print(f"  [exfil] could not save screen: {e}")
        stats = _parse_panel(frame, engine)
        if len(stats) > len(best):
            best = stats
        # a good read has most of the labels; stop early once we have them
        if len(best) >= 4:
            if cfg.get("squad_stats", True):
                try:
                    squad = parse_squad(frame, engine)
                except Exception as e:
                    print(f"  [exfil] squad parse failed: {e}")
            break
        time.sleep(0.6)  # let the panel finish animating in
    return best, squad


def log_squad_stats(base_dir: str, session_id: str, squad: list[dict]) -> str:
    """One row per player per match -> stats/squad_stats.csv. Feeds the
    career/economy/squad views (and the buddy loot-hog leaderboard)."""
    sdir = os.path.join(base_dir, "stats")
    os.makedirs(sdir, exist_ok=True)
    path = os.path.join(sdir, "squad_stats.csv")
    cols = ["date", "time", "session", "player", "is_you",
            *LABELS.keys(), "run_time"]
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        if new:
            wr.writeheader()
        for p in squad:
            wr.writerow({
                "date": time.strftime("%Y-%m-%d"),
                "time": time.strftime("%H:%M:%S"),
                "session": session_id,
                "player": p.get("name", ""),
                "is_you": int(bool(p.get("is_you"))),
                **{k: p.get(k, "") for k in LABELS},
                "run_time": p.get("run_time", ""),
            })
    return path


def log_match_stats(base_dir: str, session_id: str, stats: dict, detected_kills: int) -> str:
    """Append one row to stats/match_stats.csv. Returns the csv path."""
    sdir = os.path.join(base_dir, "stats")
    os.makedirs(sdir, exist_ok=True)
    path = os.path.join(sdir, "match_stats.csv")
    cols = ["date", "time", "session", "detected_kills",
            *LABELS.keys(), "run_time"]
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        if new:
            wr.writeheader()
        wr.writerow({
            "date": time.strftime("%Y-%m-%d"),
            "time": time.strftime("%H:%M:%S"),
            "session": session_id,
            "detected_kills": detected_kills,
            **{k: stats.get(k, "") for k in LABELS},
            "run_time": stats.get("run_time", ""),
        })
    return path


def report(stats: dict, tag_counts: dict) -> str:
    """Console summary + like-for-like audit of what we detected this match
    vs what the game's summary screen says. tag_counts is a Counter of the
    session's kill tags (down/precision/finisher/assist)."""
    if not stats:
        return "  [exfil] summary screen seen but couldn't read the stats panel"
    bits = []
    if "runner_elims" in stats:
        bits.append(f"{stats['runner_elims']} runner elims")
    if "runners_downed" in stats:
        bits.append(f"{stats['runners_downed']} downs")
    if "combatant_elims" in stats:
        bits.append(f"{stats['combatant_elims']} combatants")
    if "runner_damage" in stats:
        bits.append(f"{stats['runner_damage']} dmg")
    if "run_time" in stats:
        bits.append(f"run {stats['run_time']}")
    lines = [f"  [exfil] match stats: {', '.join(bits) or stats}"]

    audits = [
        # (game stat key, matching detected tags, label)
        ("runners_downed", ("down", "precision", "kill"), "downs"),
        ("runner_elims", ("finisher", "assist"), "elims"),
    ]
    for key, tags, label in audits:
        game = stats.get(key)
        if game is None:
            continue
        ours = sum(tag_counts.get(t, 0) for t in tags)
        if ours < game:
            lines.append(f"  [exfil] AUDIT {label}: game {game}, detected {ours} "
                         f"— missed {game - ours}")
        elif ours > game:
            lines.append(f"  [exfil] AUDIT {label}: detected {ours}, game {game} "
                         f"— {ours - game} false positive(s)?")
        else:
            lines.append(f"  [exfil] AUDIT {label}: {ours} = game's count, clean")
    return "\n".join(lines)


def accumulate_accuracy(acc: dict, stats: dict, tag_counts: dict) -> None:
    """Roll one match's audit into the session accuracy tally (mutates acc)."""
    pairs = [("downs", "runners_downed", ("down", "precision", "kill")),
             ("elims", "runner_elims", ("finisher", "assist"))]
    for name, key, tags in pairs:
        game = stats.get(key)
        if game is None:
            continue
        d = acc.setdefault(name, {"game": 0, "detected": 0, "matches": 0})
        d["game"] += game
        d["detected"] += sum(tag_counts.get(t, 0) for t in tags)
        d["matches"] += 1


def accuracy_summary(acc: dict) -> str:
    """One end-of-session line: how detection did across every audited match."""
    if not acc:
        return ""
    bits = []
    for name in ("downs", "elims"):
        d = acc.get(name)
        if not d or not d["matches"]:
            continue
        pct = 100.0 * min(d["detected"], d["game"]) / d["game"] if d["game"] else 100.0
        bits.append(f"{name}: detected {d['detected']}/{d['game']} ({pct:.0f}%)")
    if not bits:
        return ""
    n = max(d["matches"] for d in acc.values())
    return f"Detection accuracy across {n} audited match(es) — " + ", ".join(bits)
