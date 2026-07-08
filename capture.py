"""Screen capture of the kill-feed region using mss (cross-platform)."""

from __future__ import annotations

import numpy as np


class RegionCapture:
    def __init__(self, region: dict, monitor_index: int = 1):
        """region: {x, y, w, h} in pixels on the chosen monitor.

        monitor_index 1 == primary monitor in mss (0 == the virtual "all monitors").
        """
        self.region = region
        self.monitor_index = monitor_index
        self._sct = None
        self._bbox = None

    def __enter__(self):
        import mss

        self._sct = mss.mss()
        mons = self._sct.monitors
        base = mons[self.monitor_index] if self.monitor_index < len(mons) else mons[0]
        self._bbox = {
            "left": base["left"] + int(self.region["x"]),
            "top": base["top"] + int(self.region["y"]),
            "width": int(self.region["w"]),
            "height": int(self.region["h"]),
        }
        return self

    def __exit__(self, *exc):
        if self._sct:
            self._sct.close()

    def grab(self) -> np.ndarray:
        """Return the region as a BGR numpy array (H, W, 3)."""
        raw = self._sct.grab(self._bbox)
        img = np.asarray(raw)          # BGRA
        return img[:, :, :3]           # drop alpha -> BGR


def grab_full_screenshot(monitor_index: int = 1) -> np.ndarray:
    """One-off full-monitor grab (used by calibrate.py). Returns BGR array."""
    import mss

    with mss.mss() as sct:
        mons = sct.monitors
        base = mons[monitor_index] if monitor_index < len(mons) else mons[0]
        raw = sct.grab(base)
        return np.asarray(raw)[:, :, :3]
