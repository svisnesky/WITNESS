"""Detection logic — the brain of the tool, fully unit-testable without OBS/Windows.

Two detectors, chosen by `detection_mode` in config:

  PopupDetector (recommended, default):
      Watches the center-screen personal confirmation popup that appears ONLY
      when you get a down, e.g. "RUNNER DOWN  +15 XP". Because it's your own
      reward popup, no name matching is needed. Edge-triggered: fires once each
      time the popup appears, then re-arms after it disappears.

  KillDetector (fallback):
      Parses the kill feed "<killer> <verb> <victim>" and fuzzy-matches your
      name. Useful only if the game exposes a text kill feed with a matchable
      verb. (Marathon's feed uses icons, not words, so popup mode is preferred.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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


def phrase_matches(phrase_norm: str, blob_norm: str, threshold: int = 80) -> bool:
    """Guarded fuzzy match: does the (normalized) phrase appear in the blob?

    Requires the blob to be long enough to plausibly CONTAIN the phrase, so a
    short OCR scrap like 'fi' can't fuzzily match a long word like 'finisher'
    (partial matching otherwise rewards a bare prefix at full confidence)."""
    if not phrase_norm or not blob_norm:
        return False
    if phrase_norm in blob_norm:
        return True
    if len(blob_norm) < 0.6 * len(phrase_norm):
        return False
    return fuzz.partial_ratio(phrase_norm, blob_norm) >= threshold


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


class PopupDetector:
    """Edge-triggered detection of the transient personal down/kill popup
    (e.g. 'RUNNER DOWN  +15 XP').

    A whole OCR'd frame is passed in per call. The popup lingers for a couple of
    seconds and re-OCRs every frame, so we fire only on the RISING EDGE — the
    first frame it appears — then re-arm once it's been absent for a few frames
    (debounce against OCR flicker). Two separate downs count separately as long
    as the popup fully disappears between them.
    """

    def __init__(
        self,
        trigger_phrases: Optional[Iterable[str]] = None,
        phrase_match_threshold: int = 80,
        absence_frames: int = 2,
        require_xp_reward: bool = False,
    ):
        self.phrases = [_normalize(p) for p in (trigger_phrases or ["runner down"]) if p.strip()]
        self.threshold = phrase_match_threshold
        self.absence_frames = max(1, absence_frames)
        self.require_xp_reward = require_xp_reward
        self._present = False
        self._absent_count = absence_frames  # start armed (as if long absent)

    def _xp_reward_present(self, blob: str) -> bool:
        # matches "+15 xp", "15xp", "+ 15 xp" after normalization strips '+'
        return re.search(r"\b\d{1,4}\s?xp\b", blob) is not None

    def _matches(self, lines: Iterable[str]) -> Optional[str]:
        blob = _normalize(" ".join(lines))
        if not blob:
            return None
        if self.require_xp_reward and not self._xp_reward_present(blob):
            return None
        for ph in self.phrases:
            if phrase_matches(ph, blob, self.threshold):
                return ph
        return None

    def process_frame(self, lines: Iterable[str], now: float) -> Optional[KillEvent]:
        lines = list(lines)
        matched = self._matches(lines)

        if matched is not None:
            rising_edge = not self._present
            self._present = True
            self._absent_count = 0
            if rising_edge:
                return KillEvent(
                    timestamp=now,
                    raw_line=" ".join(lines).strip(),
                    killer="",
                    victim="",
                    is_self_kill=True,
                )
            return None

        # no match this frame
        self._absent_count += 1
        if self._absent_count >= self.absence_frames:
            self._present = False
        return None
