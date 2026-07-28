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

# Streamer watchlist: feed names that trigger the big alert (banner + voiced
# call-out, and a saved clip when one of them downs you). Overridden by
# streamer_watchlist in config.yaml.
DEFAULT_WATCHLIST = ["MARSHYY", "SERAPHMAXYT"]

# Bottom-left slice of the frame holding the kill feed (fractions of the
# frame). Ends above the squad panel so teammate name plates aren't read.
FEED_REGION = {"x": 0.0, "y": 0.52, "w": 0.34, "h": 0.22}

# Feed/UI words that are never part of a gamertag.
_JUNK = {"pinged", "downed", "give", "up", "xp", "self", "revive", "you"}

# Feed text that reads like a gamertag but isn't a player — Marathon abilities/
# ultimates and objective labels that print in the kill feed (e.g. the Destroyer
# ult "SEARCH AND DESTROY"). Normalized to letters-only, upper. config's
# name_ignore adds to this at runtime.
_NOT_NAMES = {"SEARCHANDDESTROY"}

# Named locations (POIs) on Marathon's maps. Zone banners and objective text
# print these on screen, and they get read as gamertags: on 2026-07-27 both bad
# names of the night were Dire Marsh POIs — "ALGAE PONDS" (which became the
# WITNESS Report's PRIME TARGET) and "QUARANTINE MM".
#
# Deliberately data, not code: a player CAN name themselves ANOMALY or CONTROL,
# so suppression is logged rather than silent (see location_hit), only fires on a
# close match, and can be switched off with ignore_map_locations: false.
MAP_LOCATIONS = {
    "Perimeter": ["North Relay", "South Relay", "Station", "Overflow", "Hauler",
                  "Tunnels", "Ravine", "Data Wall", "Twin Relays", "Command Hub",
                  "Industrial Docks"],
    "Dire Marsh": ["Maintenance", "AI Uplink", "Complex", "Quarantine",
                   "Algae Ponds", "Bio-Research", "Greenhouse", "Intersection",
                   "West Gate", "East Gate", "Canal", "Anomaly", "Lockdown"],
    "Outpost": ["The Pinwheel", "Drone Wing", "Command Wing", "Destroyed Wing",
                "Conveyance Request"],
    "Cryo Archive": ["Cargo", "Control", "Index"],
}

# Short POI names are matched EXACTLY only — fuzzy-matching a 5-letter word like
# INDEX or CARGO would eat real gamertags. Longer, distinctive ones tolerate OCR
# slips.
_FUZZY_MIN_LEN = 8
_LOCATION_FUZZ = 90


def _name_key(name: str) -> str:
    return "".join(c for c in name.upper() if c.isalpha())


def _location_keys() -> dict:
    """{normalized key: "Map · POI"} for every known location."""
    out = {}
    for map_name, pois in MAP_LOCATIONS.items():
        for poi in pois:
            out[_name_key(poi)] = f"{map_name} · {poi}"
    return out


_LOCATION_KEYS = _location_keys()


def location_hit(name: str):
    """The map location `name` is really printing, or None if it looks like a
    player. Returns e.g. 'Dire Marsh · Algae Ponds' so the caller can LOG what
    it dropped — a silent filter here would hide a mis-set gamertag.

    Handles the OCR scraps that ride along with a banner: the observed string was
    "QUARANTINE MM", not "QUARANTINE"."""
    # Any digit means it's a player. No Marathon POI has one, while gamertags are
    # full of them — and _name_key() strips digits, so without this "CARGO99"
    # would collapse to the Cryo Archive POI "Cargo" and be thrown away.
    if any(c.isdigit() for c in name):
        return None
    key = _name_key(name)
    if not key:
        return None
    if key in _LOCATION_KEYS:
        return _LOCATION_KEYS[key]

    # Drop trailing 1-2 character token scraps ("QUARANTINE MM") and retry.
    toks = [t for t in name.split() if t]
    while len(toks) > 1 and len(_name_key(toks[-1])) <= 2:
        toks.pop()
        k = _name_key(" ".join(toks))
        if k in _LOCATION_KEYS:
            return _LOCATION_KEYS[k]

    # Fuzzy, for long distinctive names only ("ALGAE PONOS" -> Algae Ponds).
    if len(key) >= _FUZZY_MIN_LEN:
        best, hit = 0, None
        for lk, label in _LOCATION_KEYS.items():
            if len(lk) < _FUZZY_MIN_LEN:
                continue
            score = fuzz.ratio(key, lk)
            if score > best:
                best, hit = score, label
        if best >= _LOCATION_FUZZ:
            return hit
    return None


def _is_player(name: str, ignore=frozenset(), skip_locations: bool = True) -> bool:
    """False for known ability/game-text strings and map location banners, so
    they don't pollute the Menace Report / prime target."""
    key = _name_key(name)
    if not key or key in _NOT_NAMES or key in ignore:
        return False
    if skip_locations and location_hit(name):
        return False
    return True

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
        if len(t) <= 2 and any(c.isdigit() for c in t):
            continue                      # "F4", "4_" — icon/UI scraps, not tags
        keep.append(t)
    keep = keep[-3:] if keep else []      # tags are at most ~3 tokens
    name = " ".join(keep)
    return name if len(name.replace(" ", "")) >= 3 else ""


def extract(rows, gamertag: str, ignore=frozenset()) -> list[tuple[str, str]]:
    """Scan feed rows for lines containing your tag. Returns
    [('victim'|'killed_by', name)] — victim when your tag leads the line
    (you were the killer), killed_by when it ends it. Ability/game-text
    strings (see _is_player) are dropped."""
    out = []
    for row in rows:
        toks = _tokens(row)
        s, e = _find_tag_span(toks, gamertag)
        if s < 0:
            continue
        before = _clean_name(toks[:s]) if _is_player(_clean_name(toks[:s]), ignore) else ""
        # names read closest-first on each side; before-side wants the
        # NEAREST tokens too, so re-clean only the tail
        after = _clean_name(toks[e:e + 4])
        after = after if _is_player(after, ignore) else ""
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
    rows = (engine.read_rows(crop, max_dim=0) if hasattr(engine, "read_rows")
            else engine.read_lines(crop, max_dim=0))
    ignore = frozenset(_name_key(x) for x in (cfg.get("name_ignore") or []))
    return extract(rows, cfg.get("gamertag") or DEFAULT_GAMERTAG, ignore=ignore)


def watch_hit(name: str, watchlist) -> str:
    """The watchlist entry a feed name matches, or ''. Fuzzy: OCR slips and
    spacing differences ('SERAPHMAX YT') still hit."""
    n = "".join(c for c in name.lower() if c.isalnum())
    if not n:
        return ""
    for w in watchlist or []:
        wn = "".join(c for c in str(w).lower() if c.isalnum())
        if wn and (fuzz.ratio(n, wn) >= 85 or wn in n):
            return str(w)
    return ""


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


def boards(base_dir: str, session: str = ""):
    """(victims, killers) — each a list of (name, times, last_wall_time) sorted
    most-encountered first. Near-identical OCR spellings merge; the most common
    spelling is displayed. session: restrict to ONE session's rows (the nightly
    WITNESS Report must not claim last week's nemesis as tonight's)."""
    path = os.path.join(base_dir, "stats", "encounters.csv")
    if not os.path.exists(path):
        return [], []
    victims, killers = [], []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = (r.get("name") or "").strip()
            if not name:
                continue
            if session and (r.get("session_id") or "") != session:
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
