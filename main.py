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
import time

import yaml

from detector import KillDetector

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config(path=CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def build_detector(cfg: dict) -> KillDetector:
    return KillDetector(
        player_name=cfg["player_name"],
        name_aliases=cfg.get("name_aliases"),
        trigger_keywords=cfg.get("trigger_keywords"),
        match_mode=cfg.get("match_mode", "self_or_assist"),
        name_match_threshold=cfg.get("name_match_threshold", 82),
        dedup_ttl_seconds=cfg.get("dedup_ttl_seconds", 8.0),
    )


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
    det = build_detector(cfg)
    now = time.monotonic()
    print(f"Testing {len(lines)} line(s), player='{cfg['player_name']}', "
          f"mode='{cfg.get('match_mode')}':\n")
    for ln in lines:
        ev = det.process_line(ln, now)
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
    print(f"Read {len(lines)} line(s):")
    for ln in lines:
        print(f"   {ln!r}")
    print()
    run_test_lines(cfg, lines)


# --- live loop ---------------------------------------------------------------

def run_live(cfg: dict, dry_run: bool):
    from capture import make_capture
    from ocr import OCREngine
    from obs_client import OBSClient, DryRunOBS

    det = build_detector(cfg)
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

    print(f"Watching kill feed at {poll_fps} fps via {cfg.get('capture_source')}. "
          f"Region={cfg['feed_region']}. "
          f"{'DRY-RUN' if dry_run else 'LIVE'}. Ctrl-C to stop.\n")

    with make_capture(cfg) as cap:
        try:
            while True:
                loop_start = time.monotonic()
                img = cap.grab()
                lines = engine.read_lines(img)
                events = det.process_lines(lines, now=loop_start)

                for ev in events:
                    now = time.monotonic()
                    count += 1
                    print(f"KILL #{count}: {ev.raw_line!r}")
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
