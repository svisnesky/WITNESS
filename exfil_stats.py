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
    for key, label in LABELS.items():
        for i, line in enumerate(lines):
            if fuzz.partial_ratio(label.lower(), line.lower()) >= 82:
                val = _num(line)
                if val is None and i + 1 < len(lines):
                    val = _num(lines[i + 1])
                if val is not None:
                    stats[key] = val
                break
    # Run time reads like "Run Time 22:50"
    for line in lines:
        if fuzz.partial_ratio("run time", line.lower()) >= 82:
            m = re.search(r"(\d{1,2})[:;](\d{2})", line)
            if m:
                stats["run_time"] = f"{m.group(1)}:{m.group(2)}"
            break
    return stats


def capture_exfil_stats(cfg, engine) -> dict:
    """One-off full-frame grab + OCR of the stat panel. Returns parsed stats
    ({} if the panel couldn't be read)."""
    if cfg.get("capture_source") == "screen":
        from capture import grab_full_screenshot
        frame = grab_full_screenshot(cfg.get("monitor_index", 1))
    else:
        from capture import grab_full_virtualcam
        frame = grab_full_virtualcam(cfg.get("obs_virtualcam_index", 0))

    H, W = frame.shape[:2]
    x, y = int(PANEL_FRAC["x"] * W), int(PANEL_FRAC["y"] * H)
    w, h = int(PANEL_FRAC["w"] * W), int(PANEL_FRAC["h"] * H)
    panel = frame[y:y + h, x:x + w]
    return parse_exfil_lines(engine.read_lines(panel))


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
