"""Marathon Wrapped — a weekly recap card from your own data.

Reads stats/squad_stats.csv (written at every exfil) and renders a shareable
PNG: your week in kills, downs, damage, and loot, the squad damage
leaderboard, and your best play (the highest-kill clip of the week, read from
the clip filenames in your session folders).

Run on demand:  python main.py --wrapped
Cards land in stats/wrapped/.
"""

from __future__ import annotations

import csv
import os
import time

from matchcard import _font, _text_w, BG, PANEL, LINE, TEXT, MUTED, ACCENT

TAG_PRIORITY = ("finisher", "precision", "down", "kill", "assist", "manual")


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def week_rows(base_dir: str, days: int = 7) -> list[dict]:
    path = os.path.join(base_dir, "stats", "squad_stats.csv")
    if not os.path.exists(path):
        return []
    cutoff = time.time() - days * 86400
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                ts = time.mktime(time.strptime(
                    f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M:%S"))
            except (ValueError, KeyError):
                continue
            if ts >= cutoff:
                out.append(r)
    return out


def best_play(record_dir: str, days: int = 7):
    """Highest-kill clip of the week from clip filenames
    (NNN_tag_HH-MM-SS.ext; kills = number of down/precision parts in the tag).
    Returns (kills, tag, session) or None."""
    root = os.path.join(record_dir, "Marathon Sessions")
    if not os.path.isdir(root):
        return None
    cutoff = time.time() - days * 86400
    best = None
    for sess in os.listdir(root):
        sdir = os.path.join(root, sess)
        if not os.path.isdir(sdir) or os.path.getmtime(sdir) < cutoff:
            continue
        for f in os.listdir(sdir):
            if not f.lower().endswith((".mkv", ".mp4")) or f.startswith("highlights"):
                continue
            parts = f.split("_")
            if len(parts) < 3:
                continue
            tag = parts[1]
            kills = sum(1 for t in tag.split("+") if t in ("down", "precision"))
            kills = max(1, kills)
            rank = -TAG_PRIORITY.index(tag.split("+")[0]) if tag.split("+")[0] in TAG_PRIORITY else -99
            key = (kills, rank)
            if best is None or key > best[0]:
                best = (key, kills, tag, sess)
    if best is None:
        return None
    return best[1], best[2], best[3]


def build_wrapped(base_dir: str, record_dir: str = "", days: int = 7):
    """Render stats/wrapped/wrapped_<date>.png. Returns the path or None."""
    rows = week_rows(base_dir, days)
    if not rows:
        print("  [wrapped] no matches in the window yet — play some first")
        return None
    from PIL import Image, ImageDraw

    yours = [r for r in rows if r.get("is_you") == "1"]
    me = {
        "matches": len(yours),
        "elims": sum(_num(r.get("runner_elims")) for r in yours),
        "downs": sum(_num(r.get("runners_downed")) for r in yours),
        "damage": sum(_num(r.get("runner_damage")) for r in yours),
        "loot": sum(_num(r.get("inventory_value")) for r in yours),
        "best_haul": max((_num(r.get("inventory_value")) for r in yours), default=0),
    }
    by_player = {}
    for r in rows:
        name = (r.get("player") or "").split("#")[0] or ("You" if r.get("is_you") == "1" else "?")
        by_player.setdefault(name, 0)
        by_player[name] += _num(r.get("runner_damage"))
    board = sorted(by_player.items(), key=lambda kv: -kv[1])[:4]

    W, H = 1200, 1500
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    pad = 70

    d.rectangle([0, 0, W, 8], fill=ACCENT)
    d.rectangle([0, H - 8, W, H], fill=ACCENT)
    d.text((pad, 60), "WITNESS", font=_font("black", 40), fill=ACCENT)
    d.text((pad, 120), "WRAPPED", font=_font("black", 110), fill=TEXT)
    end = time.strftime("%b %d, %Y")
    d.text((pad, 250), f"THE LAST {days} DAYS  ·  THROUGH {end.upper()}",
           font=_font("mono", 22), fill=MUTED)

    # big number: total elims+downs involvement
    total = me["elims"] + me["downs"]
    kf = _font("black", 260)
    d.text((pad - 6, 320), str(total), font=kf, fill=ACCENT)
    d.text((pad + _text_w(d, str(total), kf) + 30, 470),
           "DOWNS + ELIMS", font=_font("bold", 34), fill=TEXT)

    tiles = [("MATCHES", me["matches"]), ("RUNNER DAMAGE", f"{me['damage']:,}"),
             ("LOOT EXTRACTED", f"{me['loot']:,}"), ("BEST HAUL", f"{me['best_haul']:,}")]
    tx, tw, th, gap = pad, (W - 2 * pad - 30) // 2, 130, 15
    for i, (label, val) in enumerate(tiles):
        px = tx + (i % 2) * (tw + 2 * gap)
        py = 650 + (i // 2) * (th + gap)
        d.rounded_rectangle([px, py, px + tw, py + th], radius=14, fill=PANEL, outline=LINE)
        d.text((px + 22, py + 20), str(val), font=_font("black", 54), fill=TEXT)
        d.text((px + 22, py + 92), label, font=_font("mono", 17), fill=MUTED)

    y = 970
    d.text((pad, y), "SQUAD DAMAGE — THE WEEK", font=_font("mono", 20), fill=MUTED)
    y += 44
    top_dmg = board[0][1] if board and board[0][1] else 1
    for name, dmg in board:
        barw = int((W - 2 * pad - 260) * dmg / top_dmg)
        d.rounded_rectangle([pad + 210, y + 6, pad + 210 + max(barw, 8), y + 34],
                            radius=6, fill=ACCENT if dmg == top_dmg else LINE)
        d.text((pad, y), name[:14], font=_font("bold", 26), fill=TEXT)
        d.text((pad + 220 + max(barw, 8) + 14, y + 2), f"{dmg:,}",
               font=_font("mono", 22), fill=MUTED)
        y += 56

    bp = best_play(record_dir, days) if record_dir else None
    y += 20
    d.line([pad, y, W - pad, y], fill=LINE, width=1)
    y += 28
    if bp:
        kills, tag, sess = bp
        tag_txt = tag.replace("+", " + ").upper()
        d.text((pad, y), "BEST PLAY", font=_font("mono", 20), fill=MUTED)
        d.text((pad, y + 34), f"{kills} KILL{'S' if kills != 1 else ''} — {tag_txt}",
               font=_font("black", 44), fill=ACCENT)
    else:
        d.text((pad, y), "BEST PLAY: play more, clip more.",
               font=_font("mono", 20), fill=MUTED)

    d.text((pad, H - 70), "MARATHON // AUTO KILL RECORDER",
           font=_font("mono", 16), fill=MUTED)

    out_dir = os.path.join(base_dir, "stats", "wrapped")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"wrapped_{time.strftime('%Y-%m-%d')}.png")
    img.save(out)
    print(f"  [wrapped] -> {out}")
    return out
