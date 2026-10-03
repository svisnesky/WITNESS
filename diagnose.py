"""Capture diagnostic — shows EXACTLY what WITNESS is looking at.

Run this (or double-click "Diagnose Capture.bat") with Marathon on screen —
ideally with a RUNNER DOWN / PRECISION DOWN popup visible (pause on a clip or
a kill cam if you can). It:

  1. prints your real capture settings (source, region, monitor/cam),
  2. saves TWO images next to the app:
       diagnose_full.png    - the whole frame WITNESS receives
       diagnose_region.png  - the cropped area it actually reads for popups
  3. runs OCR on that crop and prints what it read, and whether that would
     have counted as a kill.

Then open the two images. The answer is almost always one of:
  - diagnose_full.png is black / wrong screen  -> capture source is wrong
       (OBS Virtual Camera not started, or wrong monitor).
  - diagnose_region.png doesn't sit over where the popup appears
       -> run "1 - Calibrate.bat" to move the detection region.
  - region looks right but OCR read nothing -> no popup was on screen in
       this frame (retry with one visible), or OCR needs tuning.
"""

from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def _save_png(path, img):
    try:
        import cv2
        cv2.imwrite(path, img)
        return True
    except Exception:
        try:
            from PIL import Image
            # img is BGR -> RGB for PIL
            Image.fromarray(img[:, :, ::-1]).save(path)
            return True
        except Exception as e:
            print(f"  (could not save {os.path.basename(path)}: {e})")
            return False


def main():
    print("=" * 60)
    print("  WITNESS capture diagnostic")
    print("=" * 60)

    import main as app
    import capture

    cfg = app.load_config()
    src = cfg.get("capture_source", capture.DEFAULT_SOURCE)
    frac = cfg.get("detect_region_frac")
    region = cfg.get("detect_region") or cfg.get("feed_region")

    print(f"\n  capture_source : {src}")
    if src == "screen":
        print(f"  monitor_index  : {cfg.get('monitor_index', 1)}")
    else:
        print(f"  virtualcam idx : {cfg.get('obs_virtualcam_index', 0)}")
    print(f"  region (frac)  : {frac}")
    print(f"  region (pixels): {region}")
    print(f"  ocr_engine     : {cfg.get('ocr_engine', 'easyocr')}")
    print(f"  trigger phrases: {cfg.get('popup_trigger_phrases')}")

    # --- 1) full frame from the configured source ---
    print("\n  Grabbing a full frame from the source...")
    try:
        if src == "screen":
            full = capture.grab_full_screenshot(cfg.get("monitor_index", 1))
        else:
            full = capture.grab_full_virtualcam(cfg.get("obs_virtualcam_index", 0))
    except Exception as e:
        print(f"\n[X] Could not capture from '{src}': {type(e).__name__}: {e}")
        if src != "screen":
            print("    -> OBS Virtual Camera isn't available. In OBS click")
            print("       'Start Virtual Camera' (Controls panel, bottom right).")
            print("       Also make sure the active SCENE shows your gameplay.")
            print("    -> Or switch to direct capture: set capture_source: screen")
            print("       in config.yaml (simplest — no Virtual Camera needed).")
        else:
            print("    -> Screen grab failed. Check monitor_index (1 = primary).")
        print("=" * 60)
        return

    h, w = full.shape[:2]
    print(f"    Got a {w}x{h} frame.")
    try:
        print(f"    Detection region at this size: {capture._resolve(region, frac, w, h)}")
    except Exception:
        pass
    fullpath = os.path.join(BASE, "diagnose_full.png")
    if _save_png(fullpath, full):
        print(f"    Saved: {fullpath}")

    # black-frame check (virtual cam with no scene, etc.)
    try:
        import numpy as np
        if float(np.asarray(full).mean()) < 4.0:
            print("\n[!] That frame is essentially BLACK. WITNESS is receiving")
            print("    an empty feed — the OBS scene/Virtual Camera isn't showing")
            print("    your game. Fix the OBS scene (or use capture_source: screen).")
    except Exception:
        pass

    # --- 2) the cropped region it actually reads ---
    print("\n  Grabbing the detection region (what OCR reads)...")
    try:
        with capture.make_capture(cfg) as cap:
            crop = cap.grab()
        ch, cw = crop.shape[:2]
        print(f"    Region is {cw}x{ch} px.")
        if cw < 5 or ch < 5:
            print("[!] The region is TINY or empty — detect_region_frac/detect_region")
            print("    is off. Run 1 - Calibrate.bat so it covers the popup area.")
        regpath = os.path.join(BASE, "diagnose_region.png")
        if _save_png(regpath, crop):
            print(f"    Saved: {regpath}")
    except Exception as e:
        print(f"[X] Region crop failed: {type(e).__name__}: {e}")
        print("=" * 60)
        return

    # --- 3) OCR + would-it-count ---
    print("\n  Running OCR on the region...")
    try:
        from ocr import OCREngine
        engine = OCREngine(cfg.get("ocr_engine", "easyocr"), cfg.get("ocr_upscale", 3))
        lines = engine.read_lines(crop)
        if lines:
            print(f"    OCR read: {lines}")
            det, mode = app.build_detector(cfg)
            events = app.detect_events(det, mode, lines, now=0.0)
            if events:
                print(f"\n[OK] This frame WOULD count as a kill: {events[0].raw_line!r}")
                print("     Detection works — if live sessions miss kills, the")
                print("     popup just wasn't inside the region long enough. Try a")
                print("     bigger region or higher poll_fps.")
            else:
                print("\n[!] OCR read text, but it didn't match a kill phrase.")
                print(f"    Trigger phrases: {cfg.get('popup_trigger_phrases')}")
                print("    If a popup WAS on screen, the text above shows how OCR")
                print("    saw it. Open an issue at github.com/svisnesky/WITNESS/issues")
                print("    and paste this window's text so the matching can be tuned.")
        else:
            print("    OCR read NOTHING in the region.")
            print("\n[!] Either no popup was on screen in this frame, or the region")
            print("    isn't over the popup. Open diagnose_full.png and")
            print("    diagnose_region.png to see. Rerun with a RUNNER DOWN /")
            print("    PRECISION DOWN popup visible.")
    except Exception as e:
        print(f"[X] OCR failed: {type(e).__name__}: {e}")
        print("    (If this mentions CUDA/torch, OCR isn't loading: open an issue at")
        print("    github.com/svisnesky/WITNESS/issues and paste this window's text.)")
    print("=" * 60)
    print("  For help, open an issue at github.com/svisnesky/WITNESS/issues and")
    print("  attach diagnose_full.png and diagnose_region.png.")
    print("  diagnose_full.png is a full-screen capture - crop anything private first.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"diagnose error: {type(e).__name__}: {e}")
        traceback.print_exc()
    if sys.platform == "win32":
        input("\nPress Enter to close...")
