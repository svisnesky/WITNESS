"""OCR of the kill-feed region.

Preprocess (upscale + grayscale + threshold) then read text with either EasyOCR
(GPU, robust to game fonts) or Tesseract (light). Returns a list of text lines.
"""

from __future__ import annotations

from typing import List

import cv2
import numpy as np


def preprocess(img_bgr: np.ndarray, upscale: int = 3, binarize: bool = True) -> np.ndarray:
    """Upscale + grayscale, optionally hard-thresholded to a bilevel image.

    Otsu binarization helps Tesseract, but tends to HURT the neural OCR
    (EasyOCR) on busy/varied backgrounds — there, feed the upscaled grayscale."""
    if upscale and upscale > 1:
        img_bgr = cv2.resize(
            img_bgr, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC
        )
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    if not binarize:
        return gray
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if thresh.mean() > 127:
        thresh = cv2.bitwise_not(thresh)
    return thresh


class OCREngine:
    """Wrapper that lazily loads whichever backend is configured."""

    def __init__(self, engine: str = "easyocr", upscale: int = 3, languages=("en",)):
        self.engine_name = engine
        self.upscale = upscale
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

    def read_lines(self, img_bgr: np.ndarray) -> List[str]:
        self._ensure_loaded()

        if self.engine_name == "easyocr":
            # neural OCR does better on grayscale than a hard-thresholded image
            proc = preprocess(img_bgr, self.upscale, binarize=False)
            results = self._reader.readtext(proc, detail=0, paragraph=True)
            return [r for r in results if r and r.strip()]

        # tesseract needs a clean bilevel image
        import pytesseract

        proc = preprocess(img_bgr, self.upscale, binarize=True)
        text = pytesseract.image_to_string(proc)
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    def read_rows(self, img_bgr: np.ndarray) -> List[str]:
        """Like read_lines, but each returned string is ONE visual row: boxes
        grouped by y-center, joined left-to-right. paragraph=True merges
        neighboring rows, which is wrong when the row structure IS the data
        (the kill feed: killer on the left, victim on the right)."""
        self._ensure_loaded()
        if self.engine_name != "easyocr":
            return self.read_lines(img_bgr)

        proc = preprocess(img_bgr, self.upscale, binarize=False)
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
