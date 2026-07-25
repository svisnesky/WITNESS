"""Detection logic — the brain of the tool, fully unit-testable without OBS/Windows.

PopupDetector watches the center-screen personal confirmation popup that
appears ONLY when you get a down, e.g. "RUNNER DOWN  +15 XP". Because it's
your own reward popup, no name matching is needed. Edge-triggered: fires once
each time the popup appears, then re-arms after it disappears.
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
    if len(blob_norm) < max(5, 0.6 * len(phrase_norm)):
        return False
    return fuzz.partial_ratio(phrase_norm, blob_norm) >= threshold


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
        confirm_frames: int = 1,
        require_reward: bool = True,
        cooldown_seconds: float = 0.0,
        count_confirm_frames: int = 2,
    ):
        self.phrases = [_normalize(p) for p in (trigger_phrases or ["runner down"]) if p.strip()]
        self.threshold = phrase_match_threshold
        self.absence_frames = max(1, absence_frames)
        self.confirm_frames = max(1, confirm_frames)
        self.require_reward = require_reward
        # Minimum seconds between fires. With confirm_frames=1 a single popup
        # whose OCR flickers (clean, garbled, clean) could otherwise re-fire the
        # same kill; the cooldown collapses those into one.
        self.cooldown = max(0.0, cooldown_seconds)
        # A double kill shows TWO popups stacked in the same frame. Counting
        # beyond the first requires the higher count to persist this many
        # consecutive frames, so one popup ghost-reading as two can't double-fire.
        self.count_confirm_frames = max(1, count_confirm_frames)
        self._streak = 0        # consecutive matched frames
        self._fired = False     # already counted this appearance
        self._fired_count = 0   # popups already fired for this appearance
        self._count_streak = 0  # consecutive frames showing more than fired
        self._absent_count = absence_frames
        self._last_fire = -1e9

    def _reward_present(self, raw: str) -> bool:
        """Real kill popups show a reward: '+15 XP', '+50', '+10 XP'. Loading /
        menu text does not. Checked on the RAW text (before '+' is stripped)."""
        t = raw.lower()
        return ("xp" in t) or (re.search(r"\+\s*\d", t) is not None)

    def _matches(self, lines: Iterable[str]) -> Optional[str]:
        blob = _normalize(" ".join(lines))
        if not blob:
            return None
        for ph in self.phrases:
            if phrase_matches(ph, blob, self.threshold):
                return ph
        return None

    def _count_matches(self, lines: Iterable[str]) -> tuple[int, list[str]]:
        """(count, segments): how many SEPARATE trigger popups this frame shows,
        and each popup's own text span (so a stacked 'DOWN [ASSIST]' + 'DOWN'
        classify independently). Token-window fuzzy count, non-overlapping,
        best-score-first. Conservative: counting >1 needs clean token reads —
        presence (>=1) always falls back to the looser whole-blob match."""
        blob = _normalize(" ".join(lines))
        toks = blob.split()
        if not toks:
            return 0, []
        cands = []
        for ph in self.phrases:
            n = max(1, len(ph.split()))
            for i in range(0, len(toks) - n + 1):
                score = fuzz.ratio(ph, " ".join(toks[i:i + n]))
                if score >= max(self.threshold, 80):
                    cands.append((score, i, i + n))
        cands.sort(key=lambda c: -c[0])
        taken = []
        for score, s, e in cands:
            if all(e <= ts or s >= te for _, ts, te in taken):
                taken.append((score, s, e))
        taken.sort(key=lambda c: c[1])
        segs = []
        for idx, (_, s, _e) in enumerate(taken):
            end = taken[idx + 1][1] if idx + 1 < len(taken) else len(toks)
            segs.append(" ".join(toks[s:end]))
        return len(taken), segs

    def process_frame_all(self, lines: Iterable[str], now: float) -> list:
        """All kill events this frame — MULTIPLE when several popups are
        stacked (a real double kill shows two popups at once)."""
        lines = list(lines)
        raw = " ".join(lines).strip()
        present = self._matches(lines) is not None
        if present and self.require_reward and not self._reward_present(raw):
            present = False  # phrase present but no reward on screen -> not a kill

        if not present:
            self._absent_count += 1
            if self._absent_count >= self.absence_frames:
                self._streak = 0
                self._fired = False
                self._fired_count = 0
                self._count_streak = 0
            return []

        count, segs = self._count_matches(lines)
        count = max(count, 1)
        if not segs:
            segs = [raw]

        self._streak += 1
        self._absent_count = 0
        events = []
        # first fire of this appearance (real popups linger; single-frame OCR
        # noise is rejected by confirm_frames)
        if (self._streak >= self.confirm_frames and not self._fired
                and now - self._last_fire >= self.cooldown):
            self._fired = True
            self._fired_count = 1
            self._last_fire = now
            events.append(KillEvent(
                timestamp=now,
                raw_line=raw if count == 1 else segs[0],
                killer="", victim="", is_self_kill=True))
        # extra stacked popups (multi-kill): a count above what already fired
        # must persist count_confirm_frames before the extras count.
        if self._fired and count > self._fired_count:
            self._count_streak += 1
            if self._count_streak >= self.count_confirm_frames:
                for seg in segs[self._fired_count:count]:
                    events.append(KillEvent(
                        timestamp=now, raw_line=seg,
                        killer="", victim="", is_self_kill=True))
                self._fired_count = count
                self._count_streak = 0
        else:
            self._count_streak = 0
        return events

    def process_frame(self, lines: Iterable[str], now: float) -> Optional[KillEvent]:
        """Single-event view of process_frame_all (kept for callers that only
        care whether a popup appeared, e.g. the overlay detector)."""
        events = self.process_frame_all(lines, now)
        return events[0] if events else None
