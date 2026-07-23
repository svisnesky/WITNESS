"""Heat / killstreak engine — the escalating in-match flair.

Tracks kills-without-dying and fires tiered "heat" events (toast + optional
call-out) as you climb, plus a few extra beats: FIRST BLOOD on the night's first
kill, a SHARPSHOOTER precision streak, and a bittersweet STREAK ENDED when the
arena finally gets you. Pure logic — main.py turns the events into overlays and
announcer call-outs. Deliberately game-agnostic (works for any taught game).

The streak persists across matches while you stay alive — it only breaks on
death — so a hot night can build a genuinely long chain.
"""

from __future__ import annotations

from collections import namedtuple

HeatEvent = namedtuple("HeatEvent", "key label color callout streak")

# (streak_threshold, key, label, color, callout). Colors are the app's medal/
# danger palette. Labels lean arena; MENACE nods to the Menace Report.
DEFAULT_TIERS = (
    (3, "hotstreak", "HOT STREAK", "#f5a623", "He's heating up."),
    (5, "rampage", "RAMPAGE", "#ff6a58", "Rampage. The arena can't slow him down."),
    (7, "menace", "MENACE", "#ff4d3d", "A menace on the field now. Nobody's safe."),
    (10, "apex", "APEX WITNESS", "#c7bdff", "Apex. Nothing escapes him tonight."),
)


class HeatTracker:
    def __init__(self, tiers=DEFAULT_TIERS, precision_at: int = 3):
        self.tiers = tuple(sorted(tiers))
        self.precision_at = max(2, int(precision_at))
        self.streak = 0        # kills since your last death (persists across matches)
        self.prec = 0          # consecutive precision kills
        self.total = 0         # kills this session (for FIRST BLOOD)

    def on_kill(self, tag: str = "", clutch: bool = False) -> list:
        """Feed a confirmed kill (its tag). Returns HeatEvents to surface."""
        events = []
        self.total += 1
        self.streak += 1
        if self.total == 1:
            events.append(HeatEvent("firstblood", "FIRST BLOOD", "#c7ccd6",
                                    "First blood. The night begins.", 1))
        for thr, key, label, color, callout in self.tiers:
            if self.streak == thr:
                events.append(HeatEvent(key, label, color, callout, self.streak))
        if "precision" in (tag or "").lower():
            self.prec += 1
            if self.prec == self.precision_at:
                events.append(HeatEvent("sharpshooter", "SHARPSHOOTER", "#9184d9",
                                        "Precision after precision. Surgical.",
                                        self.streak))
        else:
            self.prec = 0
        return events

    def on_death(self) -> HeatEvent | None:
        """You died — the streak breaks. Returns a STREAK ENDED event only if
        the streak was worth mourning."""
        ended = self.streak
        self.streak = 0
        self.prec = 0
        if ended >= 3:
            return HeatEvent("streakend", "STREAK ENDED", "#7d8a94",
                             f"The streak ends at {ended}. The arena always collects.",
                             ended)
        return None

    def peak_label(self) -> str:
        """The highest tier the CURRENT streak has reached (for status/UI)."""
        label = ""
        for thr, _key, tier_label, _c, _co in self.tiers:
            if self.streak >= thr:
                label = tier_label
        return label
