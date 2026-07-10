"""Image-based kill detection — matches template screenshots of kill popups
against the captured game frame using OpenCV template matching (NCC).

No OCR, no text parsing, no font sensitivity. Works through OBS Virtual Camera
compression because it matches the visual shape, not individual characters.

Place cropped PNG screenshots of each popup type in the templates/ folder:
  templates/runner_down.png   -> "RUNNER DOWN +15 XP" popup
  templates/finisher.png      -> "FINISHER +50" popup
  templates/precision.png     -> "PRECISION DOWN +XX XP" popup (if visually distinct)

The detector converts both the frame region and templates to grayscale, then
runs cv2.matchTemplate with TM_CCOEFF_NORMED (normalized cross-correlation).
A score >= threshold means the popup is on screen.

Edge-triggered like PopupDetector: fires once when the popup appears, re-arms
after it disappears for absence_frames consecutive checks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import numpy as np

from detector import KillEvent


@dataclass
class Template:
    tag: str
    image: np.ndarray  # grayscale
    event_type: str    # "down", "finisher", "precision", "assist"
    scales: list[np.ndarray]  # pre-scaled versions for multi-scale matching


TAG_TO_EVENT = {
    "runner_down": "down",
    "finisher": "finisher",
    "precision": "precision",
    "assist": "assist",
}


def _classify_tag(tag: str) -> str:
    tag_l = tag.lower()
    for key, evt in TAG_TO_EVENT.items():
        if key in tag_l:
            return evt
    return "down"


def _event_to_raw_line(event_type: str) -> str:
    return {
        "down": "RUNNER DOWN",
        "finisher": "FINISHER",
        "precision": "PRECISION DOWN",
        "assist": "RUNNER ELIM",
    }.get(event_type, "RUNNER DOWN")


def load_templates(template_dir: str, scales: tuple[float, ...] = (0.6, 0.75, 0.85, 1.0, 1.15, 1.3)) -> list[Template]:
    """Load all PNG files from the template directory."""
    templates = []
    if not os.path.isdir(template_dir):
        return templates

    for fname in sorted(os.listdir(template_dir)):
        if not fname.lower().endswith(".png"):
            continue
        path = os.path.join(template_dir, fname)
        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if img is None:
            continue

        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img

        tag = os.path.splitext(fname)[0]
        event_type = _classify_tag(tag)

        scaled = []
        for s in scales:
            h, w = gray.shape[:2]
            nh, nw = max(1, int(h * s)), max(1, int(w * s))
            scaled.append(cv2.resize(gray, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR))

        templates.append(Template(tag=tag, image=gray, event_type=event_type, scales=scaled))
        print(f"  [template] loaded {tag!r} ({gray.shape[1]}x{gray.shape[0]}) -> {event_type}")

    return templates


class ImageDetector:
    """Watches captured frames for kill popup templates. Edge-triggered."""

    def __init__(
        self,
        templates: list[Template],
        threshold: float = 0.55,
        cooldown: float = 3.0,
        absence_frames: int = 3,
        confirm_frames: int = 2,
        debug: bool = False,
    ):
        self.templates = templates
        self.threshold = threshold
        self.cooldown = cooldown
        self.absence_frames = max(1, absence_frames)
        self.confirm_frames = max(1, confirm_frames)
        self.debug = debug

        self._streak: dict[str, int] = {t.tag: 0 for t in templates}
        self._fired: dict[str, bool] = {t.tag: False for t in templates}
        self._absent: dict[str, int] = {t.tag: absence_frames for t in templates}
        self._last_fire: float = 0.0

    def process_frame(self, frame: np.ndarray, now: float) -> list[KillEvent]:
        """Check a BGR frame for any matching templates. Returns KillEvents."""
        if len(frame.shape) == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        events = []

        for tmpl in self.templates:
            best_score = 0.0

            for scaled in tmpl.scales:
                th, tw = scaled.shape[:2]
                fh, fw = gray.shape[:2]
                if tw > fw or th > fh:
                    continue

                result = cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(result)
                if max_val > best_score:
                    best_score = max_val

            if self.debug:
                print(f"  [template] {tmpl.tag}: score={best_score:.3f}  (threshold={self.threshold})")

            matched = best_score >= self.threshold

            if matched:
                self._streak[tmpl.tag] += 1
                self._absent[tmpl.tag] = 0

                if (self._streak[tmpl.tag] >= self.confirm_frames
                        and not self._fired[tmpl.tag]
                        and now - self._last_fire >= self.cooldown):
                    self._fired[tmpl.tag] = True
                    self._last_fire = now
                    raw_line = _event_to_raw_line(tmpl.event_type)
                    events.append(KillEvent(
                        timestamp=now,
                        raw_line=f"{raw_line}  [template:{tmpl.tag} score={best_score:.2f}]",
                        killer="",
                        victim="",
                        is_self_kill=True,
                    ))
            else:
                self._absent[tmpl.tag] += 1
                if self._absent[tmpl.tag] >= self.absence_frames:
                    self._streak[tmpl.tag] = 0
                    self._fired[tmpl.tag] = False

        return events
