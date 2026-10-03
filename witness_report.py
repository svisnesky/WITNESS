"""WITNESS Report — the end-of-night dossier.

Composes a session summary in the WITNESS voice: a written 'case file' (for the
console / archive) and a spoken narration (optional TTS at session end). Reads
the same session dict logged to stats, plus the Menace Report boards.
"""

from __future__ import annotations

import random


def _verdict_tier(total: int, kpm: float) -> str:
    if total == 0:
        return "quiet"
    if total >= 12 or kpm >= 1.0:
        return "menace"
    if total >= 5:
        return "solid"
    return "light"


def _cap(s: str) -> str:
    """First character only (keeps a proper name's casing)."""
    return s[:1].upper() + s[1:] if s else s


def _spoken_number(n: int) -> str:
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven",
             "eight", "nine", "ten", "eleven", "twelve"]
    return words[n] if 0 <= n < len(words) else str(n)


def build_report(session: dict, victims=None, killers=None, player: str = "") -> dict:
    """session: the dict logged at match end (total, precision, finisher,
    assist, down, duration_min, kpm, date, start). victims/killers: the Menace
    Report boards, [(name, count, last), ...]. Returns {title, lines, speech}."""
    session = session or {}
    total = int(session.get("total", 0) or 0)
    prec = int(session.get("precision", 0) or 0)
    fin = int(session.get("finisher", 0) or 0)
    assist = int(session.get("assist", 0) or 0)
    dur = float(session.get("duration_min", 0) or 0)
    kpm = float(session.get("kpm", 0) or 0)
    date = session.get("date", "")
    who = player or "the runner"

    top_victim = victims[0] if victims else None      # (name, count, last)
    top_nemesis = killers[0] if killers else None
    tier = _verdict_tier(total, kpm)

    # --- written case file ---
    def row(label, val):
        dots = "." * max(3, 26 - len(label))
        return f"  {label} {dots} {val}"

    lines = [
        f"WITNESS REPORT — {date}",
        "=" * 40,
        row("CONFIRMED KILLS", total),
    ]
    if prec:
        lines.append(row("  precision", prec))
    if fin:
        lines.append(row("  finishers", fin))
    if assist:
        lines.append(row("  assists (logged)", assist))
    lines += [
        row("TIME IN THE FIELD", f"{dur:.0f} min"),
        row("KILL RATE", f"{kpm:.2f}/min"),
    ]
    if top_victim:
        lines.append(row("PRIME TARGET", f"{top_victim[0]} ({top_victim[1]}x)"))
    if top_nemesis:
        lines.append(row("STANDING THREAT", f"{top_nemesis[0]} ({top_nemesis[1]}x)"))
    lines.append("=" * 40)

    verdicts = {
        "menace": [
            "Verdict: a menace. The field was theirs, and the tape has the receipts.",
            "Verdict: nothing walked away clean tonight. Case closed, in their favor.",
        ],
        "solid": [
            "Verdict: a working night. Bodies logged, threats noted. The record grows.",
            "Verdict: steady hands, steady count. The lens caught all of it.",
        ],
        "light": [
            "Verdict: a light night on the feed — but every second is still on file.",
            "Verdict: few fell, all recorded. The watch continues.",
        ],
        "quiet": [
            "Verdict: no confirmed kills. A quiet shift — but the cameras never slept.",
            "Verdict: an empty scoreboard. Even silence goes in the file.",
        ],
    }
    lines.append(random.choice(verdicts[tier]))

    # --- spoken narration (ominous dossier) ---
    k = _spoken_number(total)
    ks = f"{k} confirmed" if total != 1 else "one confirmed"
    openers = [
        "Filing tonight's report.",
        "The night's on record.",
        "Here's what the cameras kept.",
        "Case notes, end of shift.",
    ]
    if total == 0:
        body = (f"No kills to log for {who} — a quiet one. "
                "But quiet gets filed too.")
    else:
        body = f"{_cap(who)}, {ks}"
        if prec:
            body += f", {_spoken_number(prec)} of them precise"
        body += f", over {dur:.0f} minutes in the field."
    threat = ""
    if top_nemesis and top_nemesis[1] >= 2:
        threat = (f" One name keeps coming up — {top_nemesis[0]}, "
                  f"{top_nemesis[1]} times. The lens is watching them now.")
    elif top_victim and top_victim[1] >= 2:
        threat = (f" {top_victim[0]} learned the hard way, "
                  f"{top_victim[1]} times over.")
    closers = {
        "menace": "A menace tonight. The record stands in their favor.",
        "solid": "A working night. The file grows.",
        "light": "A light night — but nothing escapes the record.",
        "quiet": "Nothing fell. The watch continues.",
    }
    speech = f"{random.choice(openers)} {body}{threat} {closers[tier]}"

    return {"title": f"WITNESS Report — {date}", "lines": lines, "speech": speech}
