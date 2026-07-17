"""Who did you down, and who downed you? Read off the kill feed.

Marathon's kill feed (bottom-left) prints plain-text gamertags with a weapon
icon between them: killer on the LEFT, victim on the RIGHT, e.g.

    XX SANIK XX   [icon]   MRVIZNASTY      <- someone downed you
    MRVIZNASTY    [icon]   SOMEDUDE        <- you downed someone

Feed lines expire within seconds, so scans ride the moments we already know
something happened: a kill popup fired (read the victim), or the downed
screen is up — the GIVE UP prompt persists the whole bleed-out (read the
killer). One feed-region OCR pass per trigger, names logged with timestamps
to stats/encounters.csv. That powers the Stats page's Menace Report:
who you've been a menace to (with "last downed"), and who's been one to you.

OCR mangles stylized tags sometimes; the report fuzzy-merges near-identical
spellings so MRV1ZNASTY and MRVIZNASTY count as one player.
"""

from __future__ import annotations

import csv
import os
import time

from rapidfuzz import fuzz

DEFAULT_GAMERTAG = "MRVIZNASTY"

# Bottom-left slice of the frame holding the kill feed (fractions of the
# frame). Ends above the squad panel so teammate name plates aren't read.
FEED_REGION = {"x": 0.0, "y": 0.52, "w": 0.34, "h": 0.22}

# Feed/UI words that are never part of a gamertag.
_JUNK = {"pinged", "downed", "give", "up", "xp", "self", "revive", "you"}

_DEDUP_SECONDS = 45.0   # same name+direction within this window = same event


def _tokens(row: str) -> list[str]:
    out = []
    for tok in row.replace("|", " ").replace("/", " ").split():
        t = "".join(c for c in tok if c.isalnum())
        if t:
            out.append(t)
    return out


def _find_tag_span(tokens: list[str], tag: str) -> tuple[int, int]:
    """(start, end) of the token span best matching your gamertag, or (-1, -1).
    Tags with spaces OCR as several tokens, so windows of 1-3 are tried."""
    tag = "".join(c for c in tag.lower() if c.isalnum())
    best_score, best = 0, (-1, -1)
    for n in (1, 2, 3):
        for i in range(len(tokens) - n + 1):
            joined = "".join(tokens[i:i + n]).lower()
            if abs(len(joined) - len(tag)) > 5:
                continue
            score = fuzz.ratio(tag, joined)
            if score > best_score:
                best_score, best = score, (i, i + n)
    return best if best_score >= 82 else (-1, -1)


def _clean_name(tokens: list[str]) -> str:
    """Join feed tokens into a gamertag, dropping UI junk (distance markers
    like '10M', lone icon scraps, feed verbs). '' if nothing name-like."""
    keep = []
    for t in tokens:
        tl = t.lower()
        if len(t) < 2 or tl in _JUNK or t.isdigit():
            continue
        if tl.rstrip("m").isdigit():      # "10M" distance marker
            continue
        keep.append(t)
    keep = keep[-3:] if keep else []      # tags are at most ~3 tokens
    name = " ".join(keep)
    return name if len(name.replace(" ", "")) >= 3 else ""


def extract(rows, gamertag: str) -> list[tuple[str, str]]:
    """Scan feed rows for lines containing your tag. Returns
    [('victim'|'killed_by', name)] — victim when your tag leads the line
    (you were the killer), killed_by when it ends it."""
    out = []
    for row in rows:
        toks = _tokens(row)
        s, e = _find_tag_span(toks, gamertag)
        if s < 0:
            continue
        before = _clean_name(toks[:s])
        # names read closest-first on each side; before-side wants the
        # NEAREST tokens too, so re-clean only the tail
        after = _clean_name(toks[e:e + 4])
        if after and not before:
            out.append(("victim", after))
        elif before and not after:
            out.append(("killed_by", before))
        elif before and after:
            # OCR noise put scraps on both sides — trust the longer side
            out.append(("victim", after) if len(after) >= len(before)
                       else ("killed_by", before))
    return out


def capture(cfg, engine) -> list[tuple[str, str]]:
    """One feed-region grab + OCR -> [(direction, name)]."""
    from exfil_stats import _grab_full
    frame = _grab_full(cfg)
    h, w = frame.shape[:2]
    r = FEED_REGION
    crop = frame[int(r["y"] * h):int((r["y"] + r["h"]) * h),
                 int(r["x"] * w):int((r["x"] + r["w"]) * w)]
    rows = (engine.read_rows(crop) if hasattr(engine, "read_rows")
            else engine.read_lines(crop))
    return extract(rows, cfg.get("gamertag") or DEFAULT_GAMERTAG)


def should_log(recent: dict, direction: str, name: str,
               now: float | None = None) -> bool:
    """Debounce: the same feed line survives several seconds and a later scan
    can re-read it. True (and remembers it) only for a fresh sighting."""
    now = time.monotonic() if now is None else now
    key = (direction, name.lower())
    last = recent.get(key, -1e9)
    recent[key] = now
    return now - last >= _DEDUP_SECONDS


def log(base_dir: str, session_id: str, direction: str, name: str) -> None:
    path = os.path.join(base_dir, "stats", "encounters.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["wall_time", "session_id", "direction", "name"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), session_id,
                    direction, name])


def boards(base_dir: str):
    """(victims, killers) for the Stats page — each a list of
    (name, times, last_wall_time) sorted most-encountered first. Near-identical
    OCR spellings merge; the most common spelling is displayed."""
    path = os.path.join(base_dir, "stats", "encounters.csv")
    if not os.path.exists(path):
        return [], []
    victims, killers = [], []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = (r.get("name") or "").strip()
            if not name:
                continue
            row = (name, r.get("wall_time") or "")
            (victims if r.get("direction") == "victim" else killers).append(row)
    return _group(victims), _group(killers)


def _group(rows):
    groups = []  # [{spellings: Counter-ish dict, count, last}]
    for name, when in rows:
        key = name.lower()
        for g in groups:
            # 85: a single digit-for-letter OCR swap in an 8-char tag scores
            # 87.5 — the exact case merging exists for
            if any(fuzz.ratio(key, s) >= 85 for s in g["keys"]):
                g["keys"].add(key)
                g["spellings"][name] = g["spellings"].get(name, 0) + 1
                g["count"] += 1
                g["last"] = max(g["last"], when)
                break
        else:
            groups.append({"keys": {key}, "spellings": {name: 1},
                           "count": 1, "last": when})
    out = [(max(g["spellings"], key=g["spellings"].get), g["count"], g["last"])
           for g in groups]
    return sorted(out, key=lambda t: (-t[1], t[0].lower()))
