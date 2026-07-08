"""Kill-feed detection logic.

Turns raw OCR text lines into confirmed KillEvents. This is the brain of the
tool and the only part that is fully unit-testable without a Windows PC / OBS.

A kill-feed line looks like:  "<killer> downed <victim>"
We match it when the player's name (fuzzily, to tolerate OCR errors) appears
either as the killer (self_only) or anywhere in the line (self_or_assist).

Lingering feed lines are de-duplicated with a short TTL so one kill is counted
once even though the line stays on screen (and re-OCRs) for several seconds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from rapidfuzz import fuzz


@dataclass
class KillEvent:
    timestamp: float          # monotonic-ish seconds (caller supplies the clock)
    raw_line: str             # the OCR line that triggered it
    killer: str               # parsed killer text (best-effort)
    victim: str               # parsed victim text (best-effort)
    is_self_kill: bool        # True if you were the killer (vs assist)


def _normalize(s: str) -> str:
    """Lowercase and collapse whitespace/punctuation for stable matching + dedup."""
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return s


class KillDetector:
    def __init__(
        self,
        player_name: str,
        name_aliases: Optional[Iterable[str]] = None,
        trigger_keywords: Optional[Iterable[str]] = None,
        match_mode: str = "self_or_assist",
        name_match_threshold: int = 82,
        dedup_ttl_seconds: float = 8.0,
    ):
        names = [player_name] + list(name_aliases or [])
        self.names = [_normalize(n) for n in names if n and n.strip()]
        self.keywords = [k.lower().strip() for k in (trigger_keywords or ["downed"])]
        self.match_mode = match_mode
        self.threshold = name_match_threshold
        self.dedup_ttl = dedup_ttl_seconds
        # normalized line -> last-seen timestamp
        self._seen: dict[str, float] = {}

    # --- name matching -------------------------------------------------------

    def _name_in(self, text: str) -> bool:
        """Fuzzy: does any of our names appear as a token/substring of `text`?"""
        norm = _normalize(text)
        if not norm:
            return False
        tokens = norm.split()
        for name in self.names:
            # exact substring is a fast, high-confidence path
            if name and name in norm:
                return True
            # fuzzy per-token (handles OCR slips like "stao" -> "stan")
            for tok in tokens:
                if fuzz.ratio(name, tok) >= self.threshold:
                    return True
            # fuzzy against the whole side (handles multi-word names)
            if fuzz.partial_ratio(name, norm) >= self.threshold:
                return True
        return False

    # --- line parsing --------------------------------------------------------

    def _split_on_keyword(self, line: str) -> Optional[tuple[str, str]]:
        """Return (killer_side, victim_side) if a trigger keyword is present."""
        low = line.lower()
        for kw in self.keywords:
            # word-ish boundary so "downedown" doesn't match "downed"
            m = re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", low)
            if m:
                return line[: m.start()].strip(), line[m.end():].strip()
        return None

    # --- dedup ---------------------------------------------------------------

    def _prune(self, now: float) -> None:
        expired = [k for k, t in self._seen.items() if now - t > self.dedup_ttl]
        for k in expired:
            del self._seen[k]

    def _is_new(self, line: str, now: float) -> bool:
        key = _normalize(line)
        if not key:
            return False
        self._prune(now)
        if key in self._seen:
            self._seen[key] = now   # refresh so a still-visible line keeps its TTL
            return False
        self._seen[key] = now
        return True

    # --- public API ----------------------------------------------------------

    def process_line(self, line: str, now: float) -> Optional[KillEvent]:
        """Evaluate a single OCR line; return a KillEvent if it's a NEW kill of ours."""
        parts = self._split_on_keyword(line)
        if not parts:
            return None
        killer_side, victim_side = parts

        is_self_kill = self._name_in(killer_side)
        # The victim is the immediate name after the verb; anything in trailing
        # brackets like "(assist: You)" is NOT the victim.
        victim_primary = re.split(r"[(\[]", victim_side)[0].strip()
        victim_is_me = self._name_in(victim_primary)

        # If YOU are the victim, this is your death — never a kill of yours.
        if victim_is_me and not is_self_kill:
            return None

        if self.match_mode == "self_only":
            matched = is_self_kill
        else:  # self_or_assist: your name anywhere on the line (but not as victim)
            matched = is_self_kill or self._name_in(line)

        if not matched:
            return None
        if not self._is_new(line, now):
            return None

        return KillEvent(
            timestamp=now,
            raw_line=line.strip(),
            killer=killer_side,
            victim=victim_side,
            is_self_kill=is_self_kill,
        )

    def process_lines(self, lines: Iterable[str], now: float) -> list[KillEvent]:
        events = []
        for line in lines:
            ev = self.process_line(line, now)
            if ev:
                events.append(ev)
        return events
