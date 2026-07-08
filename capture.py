"""Capture of the kill-feed region.

Two sources, chosen in config (`capture_source`):

  "obs_virtualcam"  (recommended, lower anti-cheat risk):
      Read frames from OBS's Virtual Camera. OBS does the game capture (which is
      universally tolerated); our tool only reads a webcam device. Requires
      "Start Virtual Camera" in OBS.

  "screen":
      Screenshot the monitor directly with mss. Simpler, no Virtual Camera
      needed, but our own process performs the screen grab.

Both return the cropped feed region as a BGR numpy array using the same
`feed_region` pixel rectangle, so calibration is shared.
"""

from __future__ import annotations

import numpy as np


def _crop(frame: np.ndarray, region: dict) -> np.ndarray:
    x, y = int(region["x"]), int(region["y"])
    w, h = int(region["w"]), int(region["h"])
    return frame[y:y + h, x:x + w]


class RegionCapture:
    """Screen-capture source (mss)."""

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


class VirtualCamCapture:
    """OBS Virtual Camera source (cv2.VideoCapture). Lower anti-cheat exposure:
    OBS captures the game; we only read OBS's webcam-style output device."""

    def __init__(self, region: dict, cam_index: int = 0):
        self.region = region
        self.cam_index = cam_index
        self._cap = None

    def __enter__(self):
        import cv2

        # On Windows, DSHOW is the reliable backend for OBS Virtual Camera.
        backend = getattr(cv2, "CAP_DSHOW", 0)
        self._cap = cv2.VideoCapture(self.cam_index, backend)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"Could not open OBS Virtual Camera (index {self.cam_index}). "
                "Is 'Start Virtual Camera' running in OBS? Try a different "
                "obs_virtualcam_index in config.yaml."
            )
        return self

    def __exit__(self, *exc):
        if self._cap:
            self._cap.release()

    def grab(self) -> np.ndarray:
        ok, frame = self._cap.read()      # BGR already
        if not ok or frame is None:
            raise RuntimeError("Failed to read a frame from OBS Virtual Camera.")
        return _crop(frame, self.region)


def make_capture(cfg: dict):
    """Factory: build the capture source named in config."""
    src = cfg.get("capture_source", "obs_virtualcam")
    region = cfg["feed_region"]
    if src == "screen":
        return RegionCapture(region, monitor_index=cfg.get("monitor_index", 1))
    if src == "obs_virtualcam":
        return VirtualCamCapture(region, cam_index=cfg.get("obs_virtualcam_index", 0))
    raise ValueError(f"Unknown capture_source: {src!r} (use 'obs_virtualcam' or 'screen')")


# --- full-frame grabs for calibrate.py ---------------------------------------

def grab_full_screenshot(monitor_index: int = 1) -> np.ndarray:
    """One-off full-monitor grab. Returns BGR array."""
    import mss

    with mss.mss() as sct:
        mons = sct.monitors
        base = mons[monitor_index] if monitor_index < len(mons) else mons[0]
        raw = sct.grab(base)
        return np.asarray(raw)[:, :, :3]


def grab_full_virtualcam(cam_index: int = 0) -> np.ndarray:
    """One-off full-frame grab from the OBS Virtual Camera. Returns BGR array."""
    import cv2

    backend = getattr(cv2, "CAP_DSHOW", 0)
    cap = cv2.VideoCapture(cam_index, backend)
    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open OBS Virtual Camera (index {cam_index}). "
            "Start the Virtual Camera in OBS first."
        )
    try:
        # Read a few frames to let the device warm up.
        frame = None
        for _ in range(5):
            ok, frame = cap.read()
        if frame is None:
            raise RuntimeError("Failed to read from OBS Virtual Camera.")
        return frame
    finally:
        cap.release()
