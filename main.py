"""Marathon Auto Kill Recorder — main entry point.

Modes:
  python main.py                 # live: capture -> OCR -> detect -> OBS clip + counter
  python main.py --dry-run       # live capture + detection, but OBS calls are logged only
  python main.py --test-image shot.png   # OCR one screenshot, print lines + any kills (no OBS)
  python main.py --test-lines "Stan downed Ripper" "Ghost downed Bob"  # feed lines directly

Ctrl-C to stop.
"""

from __future__ import annotations

import argparse
import csv
import os
import threading
import time

import yaml

from detector import KillDetector, PopupDetector

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config(path=CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_detector(cfg: dict):
    """Return (detector, mode). mode is 'popup' or 'killfeed'."""
    mode = cfg.get("detection_mode", "popup")
    if mode == "popup":
        det = PopupDetector(
            trigger_phrases=cfg.get("popup_trigger_phrases", ["RUNNER DOWN"]),
            phrase_match_threshold=cfg.get("popup_match_threshold", 80),
            absence_frames=cfg.get("popup_absence_frames", 2),
            require_xp_reward=cfg.get("require_xp_reward", False),
        )
    elif mode == "killfeed":
        det = KillDetector(
            player_name=cfg["player_name"],
            name_aliases=cfg.get("name_aliases"),
            trigger_keywords=cfg.get("trigger_keywords"),
            match_mode=cfg.get("match_mode", "self_or_assist"),
            name_match_threshold=cfg.get("name_match_threshold", 82),
            dedup_ttl_seconds=cfg.get("dedup_ttl_seconds", 8.0),
        )
    else:
        raise ValueError(f"Unknown detection_mode: {mode!r} (use 'popup' or 'killfeed')")
    return det, mode


def detect_events(det, mode: str, lines, now: float) -> list:
    """Uniform interface over both detectors -> list of KillEvents for this frame."""
    if mode == "popup":
        ev = det.process_frame(lines, now)
        return [ev] if ev else []
    return det.process_lines(lines, now)


def play_kill_sound(cfg: dict) -> None:
    """Non-blocking audio cue on each detected kill (Windows).

    Defaults ON so no config change is needed. Set play_sound: false to mute,
    or sound_file: "path\\to\\clip.wav" for a custom sound.
    """
    if not cfg.get("play_sound", True):
        return
    try:
        import winsound  # Windows-only; silently no-ops elsewhere
    except Exception:
        return

    path = (cfg.get("sound_file") or "").strip()
    if path and os.path.exists(path):
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass
        return

    # No custom file: play a short distinct tone off-thread so the capture
    # loop never stalls waiting on the beep.
    def _beep():
        try:
            winsound.Beep(1245, 140)
        except Exception:
            pass
    threading.Thread(target=_beep, daemon=True).start()


def should_overlay(cfg: dict, raw_line: str) -> bool:
    """True if this kill's text matches an event configured to flash the overlay
    image (default: a precision down -> the Marathon skull)."""
    from rapidfuzz import fuzz
    from detector import _normalize

    events = cfg.get("overlay_events") or ["PRECISION DOWN"]
    blob = _normalize(raw_line)
    if not blob:
        return False
    for phrase in events:
        p = _normalize(phrase)
        if p and (p in blob or fuzz.partial_ratio(p, blob) >= 80):
            return True
    return False


def show_overlay(cfg: dict) -> None:
    """Launch overlay.py as its own process (non-blocking) to flash the image."""
    if not cfg.get("show_overlays", True):
        return
    try:
        import subprocess
        import sys
        base = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(base, "overlay.py")
        image = os.path.join(base, cfg.get("overlay_image", "marathon_skull.png"))
        if not (os.path.exists(script) and os.path.exists(image)):
            return
        # Prefer pythonw.exe so no extra console window flashes.
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        runner = pyw if os.path.exists(pyw) else sys.executable
        dur = str(cfg.get("overlay_duration_ms", 1400))
        alpha = str(cfg.get("overlay_alpha", 1.0))
        subprocess.Popen(
            [runner, script, image, dur, alpha],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def log_kill(cfg: dict, event, count: int) -> None:
    path = os.path.join(os.path.dirname(CONFIG_PATH), cfg.get("session_log", "session_log.csv"))
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["wall_time", "count", "is_self_kill", "victim", "raw_line"])
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            count,
            event.is_self_kill,
            event.victim.strip(),
            event.raw_line,
        ])


# --- offline test helpers ----------------------------------------------------

def run_test_lines(cfg: dict, lines):
    det, mode = build_detector(cfg)
    print(f"Detection mode: {mode}. Testing {len(lines)} line(s):\n")
    if mode == "popup":
        # Each arg is treated as one frame's OCR output (space-split into lines).
        for i, ln in enumerate(lines):
            ev = detect_events(det, mode, ln.split("  "), now=float(i))
            verdict = "KILL " if ev else "  -  "
            print(f"  [{verdict}] frame {i}: {ln!r}")
    else:
        now = time.monotonic()
        for ln in lines:
            evs = det.process_lines([ln], now)
            ev = evs[0] if evs else None
            verdict = "KILL " if ev else "  -  "
            extra = f"(self_kill={ev.is_self_kill}, victim='{ev.victim.strip()}')" if ev else ""
            print(f"  [{verdict}] {ln!r} {extra}")


def run_test_image(cfg: dict, image_path: str):
    import cv2
    from ocr import OCREngine

    img = cv2.imread(image_path)
    if img is None:
        raise SystemExit(f"Could not read image: {image_path}")
    engine = OCREngine(cfg.get("ocr_engine", "easyocr"), cfg.get("ocr_upscale", 3))
    print(f"OCR ({engine.engine_name}) on {image_path} ...")
    lines = engine.read_lines(img)
    print(f"Read {len(lines)} line(s) from the region:")
    for ln in lines:
        print(f"   {ln!r}")
    print()

    # A screenshot is a SINGLE frame — evaluate all its lines together.
    det, mode = build_detector(cfg)
    events = detect_events(det, mode, lines, now=0.0)
    if events:
        print(f"RESULT ({mode}): KILL detected -> {events[0].raw_line!r}")
    else:
        print(f"RESULT ({mode}): no kill detected in this frame.")
        print("  If this frame SHOULD be a kill: check the region covers the "
              "popup, add the exact phrase to popup_trigger_phrases, or lower "
              "popup_match_threshold / ocr_upscale.")


# --- live loop ---------------------------------------------------------------

def run_live(cfg: dict, dry_run: bool):
    from capture import make_capture
    from ocr import OCREngine
    from obs_client import OBSClient, DryRunOBS

    det, mode = build_detector(cfg)
    engine = OCREngine(cfg.get("ocr_engine", "easyocr"), cfg.get("ocr_upscale", 3))

    obs_cfg = cfg.get("obs", {})
    if dry_run:
        obs = DryRunOBS()
    else:
        obs = OBSClient(
            host=obs_cfg.get("host", "localhost"),
            port=obs_cfg.get("port", 4455),
            password=obs_cfg.get("password", ""),
            counter_source=obs_cfg.get("counter_source", "KillCounter"),
            counter_format=obs_cfg.get("counter_format", "Kills: {count}"),
            auto_start_replay_buffer=obs_cfg.get("auto_start_replay_buffer", True),
        )
    obs.connect()

    count = 0
    obs.set_counter(count)

    poll_fps = max(1, cfg.get("poll_fps", 5))
    interval = 1.0 / poll_fps
    min_save = cfg.get("min_save_interval_seconds", 2.0)
    last_save = 0.0

    region = cfg.get("detect_region") or cfg.get("feed_region")
    print(f"Detecting [{mode}] at {poll_fps} fps via {cfg.get('capture_source')}. "
          f"Region={region}. "
          f"{'DRY-RUN' if dry_run else 'LIVE'}. Ctrl-C to stop.\n")

    with make_capture(cfg) as cap:
        try:
            while True:
                loop_start = time.monotonic()
                img = cap.grab()
                lines = engine.read_lines(img)
                events = detect_events(det, mode, lines, now=loop_start)

                for ev in events:
                    now = time.monotonic()
                    count += 1
                    print(f"KILL #{count}: {ev.raw_line!r}")
                    play_kill_sound(cfg)
                    if should_overlay(cfg, ev.raw_line):
                        print("  -> HEADSHOT (skull popup)")
                        show_overlay(cfg)
                    obs.set_counter(count)
                    log_kill(cfg, ev, count)
                    if now - last_save >= min_save:
                        if obs.save_replay():
                            last_save = now
                    else:
                        print("  (skipped replay save — within min_save_interval)")

                # pace the loop
                elapsed = time.monotonic() - loop_start
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        except KeyboardInterrupt:
            print(f"\nStopped. Total kills this session: {count}")


def main():
    p = argparse.ArgumentParser(description="Marathon Auto Kill Recorder")
    p.add_argument("--config", default=CONFIG_PATH)
    p.add_argument("--dry-run", action="store_true",
                   help="live capture + detection, but log OBS actions instead of doing them")
    p.add_argument("--test-image", metavar="PATH",
                   help="OCR a saved screenshot and report detected kills (no OBS, no loop)")
    p.add_argument("--test-lines", nargs="+", metavar="LINE",
                   help="run detection on literal feed lines (no OCR, no OBS)")
    args = p.parse_args()

    cfg = load_config(args.config)

    if args.test_lines:
        run_test_lines(cfg, args.test_lines)
    elif args.test_image:
        run_test_image(cfg, args.test_image)
    else:
        run_live(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
