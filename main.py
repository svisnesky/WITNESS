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
import shutil
import sys
import threading
import time
from collections import Counter

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
    if not path:
        # default to the bundled soft blip if present
        base = os.path.dirname(os.path.abspath(__file__))
        default = os.path.join(base, "kill_blip.wav")
        if os.path.exists(default):
            path = default
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


def is_suppressed(cfg: dict, lines) -> bool:
    """True if the frame shows a state where kills can't happen (you're downed /
    on the self-revive or give-up screen). Prevents false kills from the death
    UI + lingering kill-feed text."""
    from detector import _normalize
    from rapidfuzz import fuzz
    phrases = cfg.get("suppress_phrases", ["SELF REVIVE", "GIVE UP"])
    blob = _normalize(" ".join(lines))
    if not blob:
        return False
    for p in phrases:
        n = _normalize(p)
        if n and (n in blob or fuzz.partial_ratio(n, blob) >= 80):
            return True
    return False


def classify_event(raw_line: str) -> str:
    """Tag a kill by type from its popup text (for clip names + the recap).
    Fuzzy so OCR slips like 'Runner Dorm' still read as a down."""
    from detector import _normalize
    from rapidfuzz import fuzz
    b = _normalize(raw_line)

    def has(p):
        return p in b or fuzz.partial_ratio(p, b) >= 78

    if has("precision"):
        return "precision"
    if has("finisher"):
        return "finisher"
    if has("assist") or has("elim"):
        return "assist"
    if has("down") or has("runner down"):
        return "down"
    return "kill"


def rename_clip_async(obs, session_id: str, tag: str, count: int) -> None:
    """In the background: wait for OBS to finish writing the clip, then move it
    into a per-session folder with an event+time name. Never blocks or raises
    into the capture loop — on any hiccup the clip just keeps OBS's default name.
    Prints what it does so clip organizing can be diagnosed."""
    def work():
        try:
            # Wait for OBS to report the path AND finish writing (size stabilizes).
            path, prev_size = "", -1
            for _ in range(16):  # up to ~8s
                time.sleep(0.5)
                path = obs.get_last_replay_path()
                if path and os.path.exists(path):
                    sz = os.path.getsize(path)
                    if sz > 0 and sz == prev_size:
                        break
                    prev_size = sz
            if not path:
                print("  [organize] OBS did not report a clip path "
                      "(GetLastReplayBufferReplay came back empty)")
                return
            if not os.path.exists(path):
                print(f"  [organize] clip path not found on disk: {path}")
                return

            base = os.path.dirname(path)
            ext = os.path.splitext(path)[1] or ".mkv"
            sdir = os.path.join(base, "Marathon Sessions", session_id)
            os.makedirs(sdir, exist_ok=True)
            dest = os.path.join(sdir, f"{count:03d}_{tag}_{time.strftime('%H-%M-%S')}{ext}")

            for attempt in range(4):  # retry if the file is briefly locked
                try:
                    shutil.move(path, dest)
                    print(f"  [organize] clip -> {dest}")
                    return
                except Exception as e:
                    last = e
                    time.sleep(0.7)
            print(f"  [organize] could not move clip: {last}")
        except Exception as e:
            print(f"  [organize] error: {e}")
    threading.Thread(target=work, daemon=True).start()


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
        size = str(cfg.get("overlay_size", 140))
        position = str(cfg.get("overlay_position", "custom:0.542,0.696"))
        margin = str(cfg.get("overlay_margin", 40))
        subprocess.Popen(
            [runner, script, image, dur, alpha, size, position, margin],
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

def run_live(cfg: dict, dry_run: bool = False, stop_event=None, on_count=None):
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

    # independent watcher for the skull overlay, so a precision down still fires
    # the skull even when it follows a normal down in the same popup window.
    overlay_det = None
    if cfg.get("show_overlays", True) and mode == "popup":
        overlay_det = PopupDetector(
            trigger_phrases=cfg.get("overlay_events") or ["PRECISION DOWN"],
            phrase_match_threshold=cfg.get("popup_match_threshold", 80),
            absence_frames=cfg.get("popup_absence_frames", 2),
        )

    # session tracking (for clip organizing + end-of-session recap)
    organize = cfg.get("organize_clips", True) and not dry_run
    session_id = time.strftime("%Y-%m-%d_%H-%M-%S")
    session_start_wall = time.strftime("%H:%M")
    session_start = time.monotonic()
    session_tags = []

    # live web dashboard (view on a phone/iPad browser over the LAN)
    web = None
    if cfg.get("web_dashboard", True):
        try:
            import webserver
            base = os.path.dirname(os.path.abspath(__file__))
            web = webserver.LiveState()
            port = cfg.get("web_port", 8000)
            webserver.start_web(web, port, base)
            web.set_running(True)
            print(f"Live view: http://{webserver.local_ip()}:{port}  "
                  f"(open in your iPad/phone browser on the same Wi-Fi)")
        except Exception as e:
            print(f"(web dashboard off: {e})")

    region = cfg.get("detect_region") or cfg.get("feed_region")
    print(f"Detecting [{mode}] at {poll_fps} fps via {cfg.get('capture_source')}. "
          f"Region={region}. "
          f"{'DRY-RUN' if dry_run else 'LIVE'}. Ctrl-C to stop.\n")

    with make_capture(cfg) as cap:
        try:
            while not (stop_event is not None and stop_event.is_set()):
                loop_start = time.monotonic()
                img = cap.grab()
                lines = engine.read_lines(img)
                blocked = is_suppressed(cfg, lines)  # downed / give-up screen
                events = [] if blocked else detect_events(det, mode, lines, now=loop_start)

                for ev in events:
                    now = time.monotonic()
                    count += 1
                    tag = classify_event(ev.raw_line)
                    session_tags.append(tag)
                    print(f"KILL #{count} [{tag}]: {ev.raw_line!r}")
                    play_kill_sound(cfg)
                    obs.set_counter(count)
                    if on_count is not None:
                        on_count(count)
                    if web is not None:
                        web.record(count, tag, ev.raw_line)
                    log_kill(cfg, ev, count)
                    if now - last_save >= min_save:
                        if obs.save_replay():
                            last_save = now
                            if organize:
                                rename_clip_async(obs, session_id, tag, count)
                    else:
                        print("  (skipped replay save — within min_save_interval)")

                # skull overlay: independent per-frame check for precision downs
                if overlay_det is not None and not blocked:
                    oev = overlay_det.process_frame(lines, now=loop_start)
                    if oev:
                        print("  -> HEADSHOT (skull popup)")
                        show_overlay(cfg)

                # pace the loop
                elapsed = time.monotonic() - loop_start
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        except KeyboardInterrupt:
            pass

    if web is not None:
        web.set_running(False)
    _end_session(cfg, session_tags, session_start, session_start_wall, dry_run,
                 obs, session_id)


def _end_session(cfg, tags, start_monotonic, start_wall, dry_run, obs=None, session_id=None):
    total = len(tags)
    dur_min = max(0.01, (time.monotonic() - start_monotonic) / 60.0)
    c = Counter(tags)
    print("\n" + "-" * 44)
    print(f"Session over. Kills: {total}  |  precision {c.get('precision', 0)}  "
          f"finisher {c.get('finisher', 0)}  assist {c.get('assist', 0)}  "
          f"downs {c.get('down', 0) + c.get('kill', 0)}")
    print(f"Duration: {dur_min:.1f} min  |  {total / dur_min:.2f} kills/min")

    if total == 0 or dry_run:
        return
    session = {
        "date": time.strftime("%Y-%m-%d"),
        "start": start_wall,
        "duration_min": round(dur_min, 1),
        "total": total,
        "precision": c.get("precision", 0),
        "finisher": c.get("finisher", 0),
        "assist": c.get("assist", 0),
        "down": c.get("down", 0) + c.get("kill", 0),
        "kpm": round(total / dur_min, 2),
    }
    try:
        import stats
        base = os.path.dirname(os.path.abspath(__file__))
        html_path = stats.record_session(base, session)
        print(f"Recap: {html_path}")
        try:
            os.startfile(html_path)  # auto-open in browser (Windows)
        except Exception:
            pass
    except Exception as e:
        print(f"(could not write recap: {e})")

    # Highlight montage of this session's clips (needs ffmpeg + organized clips).
    if cfg.get("make_montage", True) and obs is not None and session_id:
        try:
            import montage
            base = os.path.dirname(os.path.abspath(__file__))
            rec = obs.get_record_directory()
            session_dir = os.path.join(rec, "Marathon Sessions", session_id) if rec else ""
            montage.build_montage(session_dir, montage.find_ffmpeg(base, cfg))
        except Exception as e:
            print(f"(montage error: {e})")


def _tray_icon_image(base: str, cfg: dict):
    """Build a square tray icon from the skull PNG (transparent, centered)."""
    from PIL import Image
    size = 64
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    try:
        p = os.path.join(base, cfg.get("app_icon", "marathon_skull.png"))
        im = Image.open(p).convert("RGBA")
        scale = min(size / im.width, size / im.height)
        nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
        im = im.resize((nw, nh), Image.LANCZOS)
        canvas.paste(im, ((size - nw) // 2, (size - nh) // 2), im)
    except Exception:
        # fallback: a plain acid-yellow square
        from PIL import ImageDraw
        ImageDraw.Draw(canvas).rectangle([6, 6, size - 6, size - 6], fill=(211, 242, 75, 255))
    return canvas


def run_tray(cfg: dict, dry_run: bool):
    """Run with a system-tray skull icon instead of a console window."""
    base = os.path.dirname(os.path.abspath(__file__))
    # Under pythonw there's no console (sys.stdout is None), so send output to a
    # log file. When run from a real console, keep printing there so you can debug.
    if sys.stdout is None:
        try:
            logf = open(os.path.join(base, "marathon.log"), "a", buffering=1, encoding="utf-8")
            sys.stdout = logf
            sys.stderr = logf
        except Exception:
            pass

    try:
        import pystray
    except Exception:
        print("pystray not installed. Run:  python -m pip install pystray")
        print("Falling back to console mode.")
        return run_live(cfg, dry_run)

    stop_event = threading.Event()
    state = {"icon": None, "count": 0}

    def on_count(n):
        state["count"] = n
        ic = state["icon"]
        if ic is not None:
            try:
                ic.title = f"Marathon Kill Recorder — {n} kills"
            except Exception:
                pass

    def on_quit(icon, item):
        stop_event.set()
        icon.stop()

    menu = pystray.Menu(pystray.MenuItem("Quit", on_quit))
    icon = pystray.Icon("marathon", _tray_icon_image(base, cfg),
                        "Marathon Kill Recorder", menu)
    state["icon"] = icon

    worker = threading.Thread(
        target=run_live, args=(cfg, dry_run, stop_event, on_count), daemon=True)
    worker.start()
    try:
        icon.run()           # blocks on the main thread until Quit
    except KeyboardInterrupt:
        pass
    stop_event.set()
    try:
        icon.stop()
    except Exception:
        pass
    worker.join(timeout=10)  # let the end-of-session recap finish


def main():
    p = argparse.ArgumentParser(description="Marathon Auto Kill Recorder")
    p.add_argument("--config", default=CONFIG_PATH)
    p.add_argument("--dry-run", action="store_true",
                   help="live capture + detection, but log OBS actions instead of doing them")
    p.add_argument("--test-image", metavar="PATH",
                   help="OCR a saved screenshot and report detected kills (no OBS, no loop)")
    p.add_argument("--test-lines", nargs="+", metavar="LINE",
                   help="run detection on literal feed lines (no OCR, no OBS)")
    p.add_argument("--tray", action="store_true",
                   help="run from a system-tray skull icon (no console window)")
    args = p.parse_args()

    cfg = load_config(args.config)

    if args.test_lines:
        run_test_lines(cfg, args.test_lines)
    elif args.test_image:
        run_test_image(cfg, args.test_image)
    elif args.tray:
        run_tray(cfg, dry_run=args.dry_run)
    else:
        run_live(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
