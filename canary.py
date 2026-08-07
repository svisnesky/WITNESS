"""Detection canary — notices when WITNESS has silently stopped working.

The failure this exists for: Bungie renames "RUNNER DOWNED", or moves the popup,
and detection quietly stops. Nothing errors. The app runs all night, clips
nothing, and Stan finds out from an empty recap — with no way to tell a broken
build from a bad night.

The trick is separating "detection is broken" from "you didn't get kills", and
Marathon hands us the answer for free: the exfil panel reports Runners Downed and
Runner Elims itself. If the GAME says you downed four runners and we fired zero
popups, that is not a slump — that is proof.

Three distinct verdicts, because they need three different fixes:

  BROKEN   the game credited kills we never saw     -> the popup text changed
  BLIND    the OCR region produced no text at all   -> capture/region is wrong
  QUIET    text is being read, no trigger ever hit  -> suspicious, not conclusive

Deliberately conservative. A false "your app is broken" on a genuinely quiet
night would teach Stan to ignore the warning, which is worse than not having one.
"""

from __future__ import annotations

# The game must credit at least this many events before silence is damning. Two
# is noise (a teammate's kill miscounted, an unlucky panel read); four is a
# pattern.
MIN_GAME_EVENTS = 4

# Frames that must be OCR'd, all empty, before we call the region blind. At 5 fps
# that's ~4 minutes of a completely blank crop.
MIN_BLIND_FRAMES = 1200

# Frames carrying text with no trigger ever matching, before we get suspicious.
MIN_QUIET_FRAMES = 3000


class DetectionCanary:
    """Accumulates evidence over a session. Pure bookkeeping — no I/O."""

    def __init__(self, min_game_events: int = MIN_GAME_EVENTS):
        self.min_game_events = int(min_game_events)
        self.frames = 0            # frames OCR'd
        self.frames_with_text = 0  # ...that produced any text at all
        self.triggers = 0          # trigger phrases matched
        self.game_events = 0       # downs+elims the exfil panel credited
        self.our_events = 0        # downs+elims we detected in audited matches
        self.audits = 0

    def note_frame(self, lines) -> None:
        self.frames += 1
        if any(str(l).strip() for l in (lines or [])):
            self.frames_with_text += 1

    def note_trigger(self, n: int = 1) -> None:
        self.triggers += int(n)

    def note_audit(self, game_downs, game_elims, our_downs, our_elims) -> None:
        """One match's ground truth vs what we detected."""
        self.audits += 1
        self.game_events += int(game_downs or 0) + int(game_elims or 0)
        self.our_events += int(our_downs or 0) + int(our_elims or 0)

    def verdict(self) -> tuple:
        """(level, message). level: 'ok' | 'quiet' | 'blind' | 'broken'."""
        # 1. The game credited real kills and we saw NOTHING. Conclusive.
        if self.game_events >= self.min_game_events and self.our_events == 0:
            return ("broken",
                    f"the game credited {self.game_events} kill(s) this session "
                    f"and WITNESS detected 0. The popup wording or position has "
                    f"probably changed — re-run calibration, and check "
                    f"popup_trigger_phrases against what the game now prints.")

        # 2. Detected far fewer than the game credited, across several matches.
        if (self.audits >= 2 and self.game_events >= self.min_game_events
                and self.our_events * 2 < self.game_events):
            return ("broken",
                    f"the game credited {self.game_events} kill(s) but WITNESS "
                    f"only detected {self.our_events}. Detection is missing more "
                    f"than half — check the popup region and phrases.")

        # 3. Nothing is being read at all: capture or region, not the phrases.
        if self.frames >= MIN_BLIND_FRAMES and self.frames_with_text == 0:
            return ("blind",
                    f"{self.frames} frames read and not one contained any text. "
                    f"The capture source or detect_region is wrong — run "
                    f"Test Screenshot to see what WITNESS is actually looking at.")

        # 4. Text is there, but no trigger ever matched. Suspicious only.
        if (self.triggers == 0 and self.frames_with_text >= MIN_QUIET_FRAMES
                and self.game_events == 0):
            return ("quiet",
                    "no kill popup matched all session, though text is being "
                    "read. Normal if you had a rough night — worth a look if you "
                    "know you got kills.")

        return ("ok", "")

    def summary(self) -> str:
        """One line for the end-of-session log, always printed."""
        acc = ""
        if self.audits:
            acc = (f"  ·  audited {self.audits} match(es): game {self.game_events}"
                   f" / detected {self.our_events}")
        return (f"[canary] {self.frames} frames, {self.frames_with_text} with "
                f"text, {self.triggers} trigger(s){acc}")
