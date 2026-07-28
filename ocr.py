"""OCR of the kill-feed region.

Preprocess (upscale + grayscale + threshold) then read text with either EasyOCR
(GPU, robust to game fonts) or Tesseract (light). Returns a list of text lines.
"""

from __future__ import annotations

from typing import List

import cv2
import numpy as np


# Cap the long side of the image actually fed to OCR. Upscaling the region Nx
# helps small fonts, but on a high-res capture Nx makes a huge image that takes
# 300-800ms/frame to OCR — which starves the game. The reward popup text is
# large, so ~800px is plenty; this bounds OCR cost regardless of resolution.
OCR_MAX_DIM = 800

# Cap for ONE-OFF reads (teach wizard full frame, gamertag / runner / exfil
# scans). These need far more detail than the detection crop — 800px turns 4K
# UI text into ~7px and nothing reads — but they must still be BOUNDED.
#
# Truly uncapped is not offered, and that is deliberate: EasyOCR's tensors scale
# with pixel count and torch's caching allocator keeps the peak reserved. Full
# 4K reads drove reserved VRAM to ~11-12 GB on a 16 GB card, which exhausted it
# alongside the game/OBS/Discord and made the driver spill to system RAM — the
# real cause of the "awful frames" sessions (measured: 11,992 MB uncapped vs
# 1,394 MB capped). 1600px keeps 4K text at ~half size (readable) for a quarter
# of the pixels of a full frame.
OCR_ONEOFF_MAX_DIM = 1600


def _target_scale(h: int, w: int, upscale, max_dim: int = OCR_MAX_DIM) -> float:
    """Scale factor to apply: the requested upscale, but clamped so the long
    side never exceeds max_dim (and downscaled if the region is already big)."""
    s = float(upscale or 1)
    longest = max(h, w)
    if max_dim and longest * s > max_dim:
        s = max_dim / longest
    return s


def preprocess(img_bgr: np.ndarray, upscale: int = 3, binarize: bool = True,
               max_dim: int = OCR_MAX_DIM) -> np.ndarray:
    """Scale + grayscale, optionally hard-thresholded to a bilevel image.

    Scaling is capped (see _target_scale/OCR_MAX_DIM) so OCR stays fast on any
    resolution. Otsu binarization helps Tesseract, but tends to HURT the neural
    OCR (EasyOCR) on busy/varied backgrounds — there, feed the scaled grayscale."""
    h, w = img_bgr.shape[:2]
    s = _target_scale(h, w, upscale, max_dim)
    if abs(s - 1.0) > 0.01:
        interp = cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA
        img_bgr = cv2.resize(img_bgr, None, fx=s, fy=s, interpolation=interp)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    if not binarize:
        return gray
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if thresh.mean() > 127:
        thresh = cv2.bitwise_not(thresh)
    return thresh


class OCREngine:
    """Wrapper that lazily loads whichever backend is configured."""

    def __init__(self, engine: str = "easyocr", upscale: int = 3, languages=("en",),
                 max_dim: int = OCR_MAX_DIM,
                 oneoff_max_dim: int = OCR_ONEOFF_MAX_DIM):
        self.engine_name = engine
        self.upscale = upscale
        self.max_dim = int(max_dim or OCR_MAX_DIM)
        self.oneoff_max_dim = int(oneoff_max_dim or OCR_ONEOFF_MAX_DIM)
        self.languages = list(languages)
        self._reader = None      # easyocr
        self._loaded = False

    def _ensure_loaded(self):
        if self._loaded:
            return
        if self.engine_name == "easyocr":
            import easyocr

            # gpu=True uses your NVIDIA card if torch+CUDA are installed.
            self._reader = easyocr.Reader(self.languages, gpu=True)
        elif self.engine_name == "tesseract":
            import pytesseract  # noqa: F401  (import validates availability)
        else:
            raise ValueError(f"Unknown ocr_engine: {self.engine_name!r}")
        self._loaded = True

    def _cap(self, max_dim):
        """Per-call size cap.

        None -> this engine's default (the 5 fps DETECTION loop, where a 3x
                upscale of the popup crop produced a huge image).
        0    -> the ONE-OFF cap: bigger, for full-frame / large-crop reads that
                need detail. NOT uncapped — see OCR_ONEOFF_MAX_DIM for why
                (unbounded reads exhaust VRAM and tank the game's frame rate).
        n    -> exactly n."""
        if max_dim is None:
            return self.max_dim
        return self.oneoff_max_dim if int(max_dim) == 0 else int(max_dim)

    def read_lines(self, img_bgr: np.ndarray, max_dim=None) -> List[str]:
        self._ensure_loaded()

        if self.engine_name == "easyocr":
            # neural OCR does better on grayscale than a hard-thresholded image
            proc = preprocess(img_bgr, self.upscale, binarize=False, max_dim=self._cap(max_dim))
            results = self._reader.readtext(proc, detail=0, paragraph=True)
            return [r for r in results if r and r.strip()]

        # tesseract needs a clean bilevel image
        import pytesseract

        proc = preprocess(img_bgr, self.upscale, binarize=True, max_dim=self._cap(max_dim))
        text = pytesseract.image_to_string(proc)
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    def read_boxes(self, img_bgr: np.ndarray, max_dim=None):
        """[(text, (x0, y0, x1, y1))] with coordinates in ORIGINAL-image pixels
        (upscale factored back out). Used by the teach-a-game wizard, which
        needs to know WHERE text appeared, not just what it said."""
        self._ensure_loaded()
        h, w = img_bgr.shape[:2]
        if self.engine_name != "easyocr":
            return [(ln, (0, 0, w, h)) for ln in self.read_lines(img_bgr, max_dim)]

        cap = self._cap(max_dim)
        proc = preprocess(img_bgr, self.upscale, binarize=False, max_dim=cap)
        scale = _target_scale(h, w, self.upscale, cap)   # match preprocess's scale
        out = []
        for box, text, _conf in self._reader.readtext(proc, detail=1, paragraph=False):
            if not text or not text.strip():
                continue
            xs = [p[0] / scale for p in box]
            ys = [p[1] / scale for p in box]
            out.append((text.strip(), (min(xs), min(ys), max(xs), max(ys))))
        return out

    def read_rows(self, img_bgr: np.ndarray, max_dim=None) -> List[str]:
        """Like read_lines, but each returned string is ONE visual row: boxes
        grouped by y-center, joined left-to-right. paragraph=True merges
        neighboring rows, which is wrong when the row structure IS the data
        (the kill feed: killer on the left, victim on the right)."""
        self._ensure_loaded()
        if self.engine_name != "easyocr":
            return self.read_lines(img_bgr, max_dim)

        proc = preprocess(img_bgr, self.upscale, binarize=False, max_dim=self._cap(max_dim))
        results = self._reader.readtext(proc, detail=1, paragraph=False)
        items = []
        for box, text, _conf in results:
            if not text or not text.strip():
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            items.append((min(xs), (min(ys) + max(ys)) / 2,
                          max(ys) - min(ys), text.strip()))

        rows = []  # [y_center, height, [(x, text), ...]]
        for x, yc, h, text in sorted(items, key=lambda it: it[1]):
            for row in rows:
                if abs(row[0] - yc) <= max(row[1], h) * 0.6:
                    row[2].append((x, text))
                    row[0] = (row[0] + yc) / 2
                    break
            else:
                rows.append([yc, h, [(x, text)]])
        return [" ".join(t for _, t in sorted(r[2])) for r in rows]


def reserved_vram_mb() -> float:
    """VRAM torch is holding (reserved, incl. its cache), 0 if not on GPU."""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.memory_reserved() / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def release_vram() -> float:
    """Hand torch's cached VRAM back to the driver. Returns MB freed.

    The caching allocator never shrinks on its own, so one burst of large reads
    keeps its peak reserved for the rest of the session — which is what starves
    the game. Called after one-off read bursts and by the perf monitor's ceiling.
    """
    before = reserved_vram_mb()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        return 0.0
    return max(0.0, before - reserved_vram_mb())
