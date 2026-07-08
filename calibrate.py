"""One-time calibration: drag-select the kill-feed rectangle on a screenshot.

Run on the gaming PC while Marathon (or a screenshot of it) is showing the feed:

    python calibrate.py

A window opens with a full screenshot of your primary monitor. Drag a box
around the kill-feed area, then press ENTER (or SPACE). The pixel rectangle is
printed and written into config.yaml under `feed_region`.
"""

from __future__ import annotations

import re
import sys

import cv2
import yaml

from capture import grab_full_screenshot, grab_full_virtualcam

CONFIG_PATH = "config.yaml"


def main():
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    source = cfg.get("capture_source", "obs_virtualcam")

    if source == "screen":
        print("Grabbing a screenshot of your primary monitor...")
        shot = grab_full_screenshot(monitor_index=cfg.get("monitor_index", 1))
    else:
        print("Grabbing a frame from the OBS Virtual Camera "
              "(make sure it's started in OBS)...")
        shot = grab_full_virtualcam(cam_index=cfg.get("obs_virtualcam_index", 0))
    h, w = shot.shape[:2]
    print(f"Screenshot is {w}x{h}. Drag a box around the kill feed, then press ENTER.")

    # cv2.selectROI handles the drag-select UI for us.
    win = "Select kill-feed region — drag a box, then ENTER (c = cancel)"
    x, y, bw, bh = cv2.selectROI(win, shot, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()

    if bw == 0 or bh == 0:
        print("No region selected — aborted. Nothing changed.")
        sys.exit(1)

    region = {"x": int(x), "y": int(y), "w": int(bw), "h": int(bh)}
    print(f"Selected feed_region: {region}")

    # Replace just the feed_region block in-place so config comments are preserved.
    with open(CONFIG_PATH) as f:
        text = f.read()
    new_block = (
        "feed_region:\n"
        f"  x: {region['x']}\n"
        f"  y: {region['y']}\n"
        f"  w: {region['w']}\n"
        f"  h: {region['h']}\n"
    )
    pattern = re.compile(r"feed_region:\n(?:[ \t]+\w+:.*\n?)+")
    if pattern.search(text):
        text = pattern.sub(new_block, text, count=1)
    else:
        text = text.rstrip() + "\n\n" + new_block
    with open(CONFIG_PATH, "w") as f:
        f.write(text)
    print(f"Wrote feed_region into {CONFIG_PATH}. You're calibrated.")


if __name__ == "__main__":
    main()
