"""Heat / killstreak engine — the escalating in-match flair.

Tracks kills-without-dying and fires tiered "heat" events (toast + optional
call-out) as you climb, plus a few extra beats: FIRST BLOOD on the night's first
kill, a SHARPSHOOTER precision streak, and a bittersweet STREAK ENDED when the
arena finally gets you. Pure logic — main.py turns the events into overlays and
announcer call-outs. Deliberately game-agnostic (works for any taught game).

The streak is PER-MATCH: it resets when a new raid starts and when you die.
It used to persist across matches while you stayed alive, which meant surviving
a few raids parked you permanently at the top tier and every call-out became
noise. Session-long totals live in HeatTracker.total.
"""

from __future__ import annotations

from collections import namedtuple

HeatEvent = namedtuple("HeatEvent", "key label color callout streak")

# (streak_threshold, key, label, color, callout). Colors are the app's medal/
# danger palette. HEATING UP -> ON FIRE is the NBA-Jam bottom of the ladder;
# RAMPAGE/MENACE/APEX are the WITNESS-flavored escalation (MENACE nods to the
# Menace Report).
DEFAULT_TIERS = (
    (2, "heatingup", "HEATING UP", "#f5a623", "He's heating up."),
    (3, "onfire", "ON FIRE", "#ff8c42", "He's on fire!"),
    (5, "rampage", "RAMPAGE", "#ff6a58", "Rampage. The arena can't slow him down."),
    (7, "menace", "MENACE", "#ff4d3d", "A menace on the field now. Nobody's safe."),
    (10, "apex", "APEX WITNESS", "#c7bdff", "Apex. Nothing escapes him tonight."),
)


class HeatTracker:
    def __init__(self, tiers=DEFAULT_TIERS, precision_at: int = 3):
        self.tiers = tuple(sorted(tiers))
        self.precision_at = max(2, int(precision_at))
        self.streak = 0        # kills this match, since your last death
        self.prec = 0          # consecutive precision kills (per enemy)
        self._awaiting_precision = False   # last kill's modifier hasn't landed yet
        self.total = 0         # kills this session
        self.first_blood_armed = True   # first kill of each match earns FIRST BLOOD

    def new_match(self):
        """A new match started — re-arm FIRST BLOOD and RESET the streak.

        The streak used to persist across matches, which sounded good and played
        terribly: survive a few raids and you sit permanently at the top tier, so
        APEX WITNESS fired on the second kill of a match and every call-out
        became noise. A killstreak people actually recognise is per-match — the
        run you went on in THAT raid. Session totals live in `total`."""
        self.first_blood_armed = True
        self.streak = 0
        self.prec = 0
        self._awaiting_precision = False

    def on_kill(self, tag: str = "", clutch: bool = False) -> list:
        """Feed a confirmed kill (its tag). Returns HeatEvents to surface."""
        events = []
        # The precision streak can only be broken LAZILY. Marathon prints the
        # PRECISION modifier just after the RUNNER DOWNED popup for the same
        # runner, so at the moment a kill lands we do not yet know whether it was
        # a headshot. If the PREVIOUS kill never got its modifier, it wasn't
        # precision — break the streak now.
        if self._awaiting_precision:
            self.prec = 0
        self._awaiting_precision = True
        self.total += 1
        self.streak += 1
        if self.first_blood_armed:
            self.first_blood_armed = False
            events.append(HeatEvent("firstblood", "FIRST BLOOD", "#e63b2e",
                                    "First blood.", self.streak))
        for thr, key, label, color, callout in self.tiers:
            if self.streak == thr:
                events.append(HeatEvent(key, label, color, callout, self.streak))
        if "precision" in (tag or "").lower():
            events.extend(self.mark_precision())
        return events

    def mark_precision(self) -> list:
        """A PRECISION popup landed on the down we just counted — same runner,
        so it is a modifier, not a kill (see main.MODIFIER_TAGS). It still needs
        to feed the SHARPSHOOTER streak, which is why this exists separately:
        on_kill() is only ever called for real kills now, so precision would
        otherwise never reach the tracker at all.

        Counting per ENEMY rather than per popup is also the more honest streak."""
        self._awaiting_precision = False
        self.prec += 1
        if self.prec == self.precision_at:
            return [HeatEvent("sharpshooter", "SHARPSHOOTER", "#9184d9",
                              "Precision after precision. Surgical.", self.streak)]
        return []

    def on_death(self) -> HeatEvent | None:
        """You died — the streak breaks. Returns a STREAK ENDED event only if
        the streak was worth mourning."""
        ended = self.streak
        self.streak = 0
        self.prec = 0
        self._awaiting_precision = False
        if ended >= 3:
            return HeatEvent("streakend", "STREAK ENDED", "#7d8a94",
                             f"The streak ends at {ended}. The arena always collects.",
                             ended)
        return None

    def peak_label(self) -> str:
        """The highest tier the CURRENT streak has reached (for status/UI)."""
        return self.peak()[0]

    def peak(self) -> tuple:
        """(label, color) of the highest tier the current streak has reached,
        or ('', '') below the first tier — feeds the dashboard streak chip."""
        label, color = "", ""
        for thr, _key, tier_label, tier_color, _co in self.tiers:
            if self.streak >= thr:
                label, color = tier_label, tier_color
        return label, color
