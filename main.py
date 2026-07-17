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

from detector import PopupDetector

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

# One web server per process, reused across sessions (so restarting a session
# doesn't collide on the port and leave the browser on stale data).
_web_state = None
_web_server = None

# Per-session console log: everything printed is also written to a file so you
# can just send the file instead of copy-pasting a live, scrolling console.
_log_fh = None
_log_prev_stdout = None
_log_path = None


class _Tee:
    """Write to the real stream AND the session log file."""
    def __init__(self, stream, fh):
        self._stream, self._fh = stream, fh
    def write(self, s):
        try:
            self._stream.write(s)
        except Exception:
            pass
        try:
            self._fh.write(s); self._fh.flush()
        except Exception:
            pass
    def flush(self):
        for t in (self._stream, self._fh):
            try:
                t.flush()
            except Exception:
                pass


def _install_session_log():
    """Tee stdout to logs/session_<time>.log. Returns the path (or None)."""
    global _log_fh, _log_prev_stdout, _log_path
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        logdir = os.path.join(base, "logs")
        os.makedirs(logdir, exist_ok=True)
        _log_path = os.path.join(logdir, f"session_{time.strftime('%Y-%m-%d_%H-%M-%S')}.log")
        _log_fh = open(_log_path, "w", encoding="utf-8")
        _log_prev_stdout = sys.stdout
        sys.stdout = _Tee(sys.stdout, _log_fh)
        print(f"Logging this session to: {_log_path}")
        return _log_path
    except Exception as e:
        print(f"(could not open session log: {e})")
        return None


def _close_session_log():
    global _log_fh, _log_prev_stdout
    try:
        if _log_path:
            print(f"Session log saved: {_log_path}  (send me this file)")
        if _log_prev_stdout is not None:
            sys.stdout = _log_prev_stdout
        if _log_fh is not None:
            _log_fh.close()
    except Exception:
        pass
    finally:
        _log_fh = None; _log_prev_stdout = None


def load_config(path=CONFIG_PATH) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    base = os.path.dirname(os.path.abspath(path)) or "."

    # The dashboard's settings live in an override file (config.yaml comments
    # never get rewritten) — read it early because it may pick the game.
    override = {}
    ov_path = os.path.join(base, "settings_override.yaml")
    if os.path.exists(ov_path):
        try:
            with open(ov_path) as f:
                override = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"(could not read settings_override.yaml: {e})")

    # Game profile: games/<game>.yaml overrides config.yaml's detection
    # settings (config.yaml's are Marathon's). Marathon needs no file; other
    # games get theirs from the teach-a-game wizard. The dashboard override
    # is applied LAST so live settings changes always win.
    game = str(override.get("game") or cfg.get("game") or "marathon").strip().lower()
    if game and game != "marathon":
        pfile = os.path.join(base, "games", f"{game}.yaml")
        try:
            with open(pfile) as f:
                profile = yaml.safe_load(f) or {}
            cfg.update(profile)
            print(f"(game profile: {profile.get('game_name', game)} — {pfile})")
        except FileNotFoundError:
            print(f"(game {game!r} has no profile at {pfile} — using Marathon "
                  "settings; run  python main.py --teach  to create one)")
        except Exception as e:
            print(f"(could not read game profile {pfile}: {e})")

    cfg.update(override)
    return cfg


def save_setting_overrides(changes: dict) -> None:
    """Persist dashboard-changed settings to settings_override.yaml."""
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "settings_override.yaml")
    current = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                current = yaml.safe_load(f) or {}
        except Exception:
            current = {}
    current.update(changes)
    with open(path, "w") as f:
        yaml.safe_dump(current, f, default_flow_style=False)


def build_detector(cfg: dict):
    """Popup detection is the one mode that works for Marathon (the kill feed
    uses icons; audio and template matching were tried and retired).
    NOTE: defaults here must mirror config.yaml's tuned values — existing
    installs keep their own config.yaml across updates, so behavior changes
    ship as these code defaults."""
    mode = cfg.get("detection_mode", "popup")
    if mode != "popup":
        print(f"(detection_mode {mode!r} was retired — using popup detection)")
    det = PopupDetector(
        trigger_phrases=cfg.get("popup_trigger_phrases",
                                ["RUNNER DOWN", "PRECISION DOWN",
                                 "FINISHER", "RUNNER ELIM"]),
        phrase_match_threshold=cfg.get("popup_match_threshold", 85),
        absence_frames=cfg.get("popup_absence_frames", 3),
        confirm_frames=cfg.get("popup_confirm_frames", 1),
        require_reward=cfg.get("require_reward", True),
        cooldown_seconds=cfg.get("popup_cooldown_seconds", 2.0),
    )
    return det, "popup"


def detect_events(det, mode: str, lines, now: float) -> list:
    """Detector interface -> list of KillEvents for this frame."""
    ev = det.process_frame(lines, now)
    return [ev] if ev else []


def play_kill_sound(cfg: dict) -> None:
    """Non-blocking audio cue on each detected kill (Windows).

    Defaults OFF — the on-screen chips + dashboard flash cover it. Set
    play_sound: true to re-enable,
    or sound_file: "path\\to\\clip.wav" for a custom sound.
    """
    if not cfg.get("play_sound", False):
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
    from detector import _normalize, phrase_matches
    phrases = cfg.get("suppress_phrases",
                      ["SELF REVIVE", "GIVE UP", "RUNNER DAMAGE",
                       "CREW REVIVES", "INVENTORY VALUE"])
    blob = _normalize(" ".join(lines))
    if not blob:
        return False
    for p in phrases:
        if phrase_matches(_normalize(p), blob, 80):
            return True
    return False


def classify_event(raw_line: str) -> str:
    """Tag a kill by type from its popup text (for clip names + the recap).
    Fuzzy so OCR slips like 'Runner Dorm' still read as a down."""
    from detector import _normalize, phrase_matches
    b = _normalize(raw_line)  # brackets stripped, so "[ASSIST]" -> "assist"

    def has(p):
        return phrase_matches(p, b, 78)

    # The [ASSIST] marker wins over everything: a "PRECISION DOWN [ASSIST]" is
    # an assist, not your precision kill. Label it distinctly.
    if has("assist"):
        return "assist"
    if has("precision"):
        return "precision"
    if has("finisher"):
        return "finisher"
    if has("down") or has("runner down"):
        return "down"
    if has("elim"):        # solo "RUNNER ELIM" (no assist marker) = your kill
        return "kill"
    return "kill"


def rename_clip_async(obs, session_id: str, tag: str, count: int, on_done=None) -> None:
    """In the background: wait for OBS to finish writing the clip, then move it
    into a per-session folder with an event+time name. Never blocks or raises
    into the capture loop — on any hiccup the clip just keeps OBS's default name.
    Prints what it does so clip organizing can be diagnosed.
    on_done(dest) is called after a successful move (used to collect the
    match's clips for the highlight reel)."""
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

            for attempt in range(8):  # retry if the file is briefly locked
                try:
                    shutil.move(path, dest)
                    print(f"  [organize] clip -> {dest}")
                    if on_done is not None:
                        on_done(dest)
                    return
                except Exception as e:
                    last = e
                    time.sleep(1.5)
            print(f"  [organize] could not move clip: {last}")
        except Exception as e:
            print(f"  [organize] error: {e}")
    threading.Thread(target=work, daemon=True).start()


def should_overlay(cfg: dict, raw_line: str) -> bool:
    """True if this kill's text matches an event configured to flash the overlay
    image (default: a precision down -> the Marathon skull)."""
    from detector import _normalize, phrase_matches

    events = cfg.get("overlay_events") or ["PRECISION DOWN"]
    blob = _normalize(raw_line)
    if not blob:
        return False
    for phrase in events:
        if phrase_matches(_normalize(phrase), blob, 80):
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
        position = str(cfg.get("overlay_position", "custom:0.5,0.645"))
        margin = str(cfg.get("overlay_margin", 40))
        subprocess.Popen(
            [runner, script, image, dur, alpha, size, position, margin],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def show_text_overlay(cfg, text, size=60, position="custom:0.5,0.42",
                      color="#d3f24b", duration_ms=1600, rise=True):
    """Flash click-through text over the game (DOUBLE KILL, CLIP SAVED...).
    Same animation language as the skull; never blocks the capture loop."""
    if not cfg.get("show_overlays", True):
        return
    try:
        import subprocess
        import sys
        base = os.path.dirname(os.path.abspath(__file__))
        script = os.path.join(base, "overlay.py")
        if not os.path.exists(script):
            return
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        runner = pyw if os.path.exists(pyw) else sys.executable
        subprocess.Popen(
            [runner, script, "--text", text, str(duration_ms), "1.0",
             str(size), position, "40", color, "1" if rise else "0"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


MULTIKILL_NAMES = {2: "DOUBLE KILL", 3: "TRIPLE KILL", 4: "QUAD KILL"}


def _theme_color(cfg, key, default):
    """Overlay color from the active game profile's theme block (hex only)."""
    v = str(((cfg.get("theme") or {}).get(key)) or "").strip()
    return v if v.startswith("#") and 4 <= len(v) <= 9 else default


def register_elim(elim_times: list, now: float, wipe_size: int = 3,
                  window: float = 120.0, burst: float = 5.0) -> bool:
    """Track enemy DEATHS toward a team wipe. One death often fires several
    popups at once (RUNNER ELIM + FINISHER in the same moment), so events
    within `burst` seconds collapse into a single death. Returns True when
    `wipe_size` distinct deaths land inside `window` — the squad is gone.
    Mutates elim_times in place (cleared on a wipe)."""
    if elim_times and now - elim_times[-1] < burst:
        return False                      # same death, different popup
    elim_times.append(now)
    while elim_times and now - elim_times[0] > window:
        elim_times.pop(0)
    if len(elim_times) >= wipe_size:
        elim_times.clear()
        return True
    return False


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

def _tune_performance(cfg):
    """Keep OCR from starving the game of CPU (the usual cause of frame drops):
    run this process at below-normal priority and cap how many CPU threads the
    OCR/torch/opencv work can use."""
    n = max(1, int(cfg.get("ocr_threads", 2)))
    try:
        import ctypes
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass
    os.environ.setdefault("OMP_NUM_THREADS", str(n))
    try:
        import torch
        torch.set_num_threads(n)
    except Exception:
        pass
    try:
        import cv2
        cv2.setNumThreads(n)
    except Exception:
        pass


def _obs_port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_obs_running(cfg) -> None:
    """If OBS's websocket isn't reachable, launch OBS ourselves and wait for it
    (Windows). Kills the classic 'connection refused because OBS wasn't open'
    startup failure. Controlled by obs.auto_launch (default true)."""
    obs_cfg = cfg.get("obs", {})
    host = obs_cfg.get("host", "localhost")
    port = obs_cfg.get("port", 4455)
    if _obs_port_open(host, port):
        return
    if not obs_cfg.get("auto_launch", True) or sys.platform != "win32":
        return

    exe = (obs_cfg.get("exe_path") or "").strip()
    if not exe:
        for cand in (r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
                     r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe"):
            if os.path.exists(cand):
                exe = cand
                break
    if not exe or not os.path.exists(exe):
        print("OBS isn't running and obs64.exe wasn't found — start OBS "
              "manually (or set obs.exe_path in config.yaml).")
        return

    print("OBS isn't running — launching it for you...")
    try:
        # OBS must be started FROM its own bin directory or it errors out.
        import subprocess
        subprocess.Popen([exe, "--startreplaybuffer", "--minimize-to-tray",
                          "--disable-shutdown-check"],
                         cwd=os.path.dirname(exe))
    except Exception as e:
        print(f"Could not launch OBS: {e}")
        return
    # Wait for the websocket to come up (OBS cold start takes a few seconds).
    for _ in range(30):
        time.sleep(1)
        if _obs_port_open(host, port):
            print("OBS is up.")
            return
    print("OBS was launched but its WebSocket isn't answering yet — check "
          "Tools -> WebSocket Server Settings is enabled.")


def _setup_session(cfg, dry_run):
    """Session setup. Returns a dict
    of session state (obs, web, counters, etc.)."""
    from obs_client import OBSClient, DryRunOBS

    obs_cfg = cfg.get("obs", {})
    if dry_run:
        obs = DryRunOBS()
    else:
        _ensure_obs_running(cfg)
        obs = OBSClient(
            host=obs_cfg.get("host", "localhost"),
            port=obs_cfg.get("port", 4455),
            password=obs_cfg.get("password", ""),
            counter_source=obs_cfg.get("counter_source", "KillCounter"),
            counter_format=obs_cfg.get("counter_format", "Kills: {count}"),
            auto_start_replay_buffer=obs_cfg.get("auto_start_replay_buffer", True),
        )
    # A freshly-launched OBS opens its websocket port BEFORE it's ready to
    # answer requests ("code 207: OBS is not ready"). Retry the handshake
    # until it finishes booting.
    for attempt in range(20):
        try:
            obs.connect()
            break
        except Exception as e:
            if attempt >= 19:
                raise
            if attempt == 0:
                print("OBS is still starting up — waiting for it...")
            time.sleep(1.5)
    obs.set_counter(0)

    web = None
    if cfg.get("web_dashboard", True):
        try:
            import webserver
            global _web_state, _web_server
            base = os.path.dirname(os.path.abspath(__file__))
            port = cfg.get("web_port", 8000)
            if _web_server is None:
                _web_state = webserver.LiveState()
                # web_lan: true serves to your Wi-Fi (needed for the iPad).
                # false = this PC only (most locked-down).
                host = "0.0.0.0" if cfg.get("web_lan", True) else "127.0.0.1"
                _web_server = webserver.start_web(_web_state, port, base, host=host)
            web = _web_state
            web.reset()
            web.bind_config(cfg, save_setting_overrides)
            try:
                web.record_dir = obs.get_record_directory() or ""
            except Exception:
                pass
            web.set_running(True)
            print(f"Live view: http://{webserver.local_ip()}:{port}  "
                  f"(open in your iPad/phone browser on the same Wi-Fi)")
        except Exception as e:
            print(f"(web dashboard off: {e})")

    s = {
        "obs": obs,
        "web": web,
        "count": 0,
        "last_save": 0.0,
        "organize": cfg.get("organize_clips", True) and not dry_run,
        "session_id": time.strftime("%Y-%m-%d_%H-%M-%S"),
        "session_start_wall": time.strftime("%H:%M"),
        "session_start": time.monotonic(),
        "session_tags": [],
        "min_save": cfg.get("min_save_interval_seconds", 2.0),
        "match_clips": [],   # organized clip paths since the last exfil (this match)
        "match_num": 0,
        "cfg": cfg,
        "medal_sounds": {},
    }
    if cfg.get("announcer_medals", True) and not dry_run:
        _prepare_medals_async(cfg, s)
    return s


def _prepare_medals_async(cfg, s):
    """Render the medal call-outs in the background so the first DOUBLE KILL
    plays instantly (cached per voice; offline after the first render)."""
    def work():
        try:
            import announcer
            import montage
            base = os.path.dirname(os.path.abspath(__file__))
            s["medal_sounds"] = announcer.ensure_medal_sounds(
                base, cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                montage.find_ffmpeg(base, cfg),
                pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH))
        except Exception as e:
            print(f"  [medals] prep failed: {e}")
    threading.Thread(target=work, daemon=True).start()


def _clip_ready_callback(s, tag, count, kills=1):
    """Callback for rename_clip_async: collect the clip (with its kill count,
    for Play of the Game) and put it on the iPad as an instant replay."""
    def on_done(dest):
        s["match_clips"].append({"path": dest, "kills": kills, "tag": tag})
        _register_replay_async(s, dest, tag, count)
        if s["cfg"].get("overlay_clip_saved", True):
            show_text_overlay(s["cfg"], "CLIP SAVED", size=26,
                              position="bottom-right", color="#aab4bd",
                              duration_ms=1100, rise=False)
    return on_done


def _register_replay_async(s, clip_path, tag, count):
    """Remux the organized .mkv to .mp4 (stream copy, ~instant) so Safari can
    play it, and add it to the dashboard's Instant Replays list."""
    if s["web"] is None:
        return
    def work():
        try:
            import subprocess
            import montage
            base = os.path.dirname(os.path.abspath(__file__))
            rdir = os.path.join(os.path.dirname(clip_path), "replays")
            os.makedirs(rdir, exist_ok=True)
            mp4 = os.path.join(rdir, os.path.splitext(os.path.basename(clip_path))[0] + ".mp4")
            ff = montage.find_ffmpeg(base, s["cfg"])
            r = subprocess.run(
                [ff, "-y", "-i", clip_path, "-c", "copy", "-movflags", "+faststart", mp4],
                capture_output=True, text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0 and os.path.exists(mp4):
                s["web"].add_replay(f"Kill #{count} — {tag}", mp4)
                print(f"  [replay] kill #{count} ready on the iPad")
            else:
                tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
                print(f"  [replay] remux failed: {tail}")
        except Exception as e:
            print(f"  [replay] error: {e}")
    threading.Thread(target=work, daemon=True).start()


def _flush_coalesce(s):
    """Save one replay clip for all kills accumulated in the coalesce window."""
    pending = s.get("_coalesce_pending")
    if not pending:
        return
    tags = [p["tag"] for p in pending]
    counts = [p["count"] for p in pending]
    combo_tag = "+".join(dict.fromkeys(tags))  # e.g. "down+finisher", deduped, order-preserving
    label = ",".join(str(c) for c in counts)
    print(f"  [coalesce] saving clip for kill(s) #{label} [{combo_tag}]")
    # A multikill means multiple DISTINCT downs — a down followed by the
    # finisher on the same runner is ONE kill, not a double.
    n_downs = sum(1 for t in tags if t in ("down", "precision"))
    if n_downs >= 2 and not s.get("clutch"):
        if s["cfg"].get("overlay_multikill", True):
            show_text_overlay(s["cfg"],
                              MULTIKILL_NAMES.get(n_downs, "MULTI KILL"),
                              size=64, position="custom:0.5,0.40", duration_ms=1700,
                              color=_theme_color(s["cfg"], "accent", "#d3f24b"))
        if s["cfg"].get("announcer_medals", True):
            import announcer
            announcer.play_medal(s["medal_sounds"], n_downs)
    if s["obs"].save_replay():
        s["last_save"] = time.monotonic()
        if s["organize"]:
            rename_clip_async(s["obs"], s["session_id"], combo_tag, counts[0],
                              on_done=_clip_ready_callback(s, combo_tag, counts[0],
                                                           kills=max(1, n_downs)))
    s["_coalesce_pending"] = []
    s["_coalesce_deadline"] = 0.0


def _check_coalesce(s):
    """Called each loop iteration; flushes the coalesce buffer when the window expires."""
    deadline = s.get("_coalesce_deadline", 0.0)
    if deadline and time.monotonic() >= deadline and s.get("_coalesce_pending"):
        _flush_coalesce(s)


def _handle_kill(cfg, ev, s, on_count=None):
    """Process a single detected kill event. Mutates the session dict `s`."""
    now = time.monotonic()
    s["count"] += 1
    count = s["count"]
    tag = classify_event(ev.raw_line)
    s["session_tags"].append(tag)
    s.setdefault("match_tags", []).append(tag)  # reset each exfil for the audit
    print(f"KILL #{count} [{tag}]: {ev.raw_line!r}")
    if s.get("clutch"):
        s["clutch_kills"] = s.get("clutch_kills", 0) + 1
        print(f"  [clutch] solo kill #{s['clutch_kills']} — staying quiet")

    # Team wipe: an enemy DEATH shows an ELIM or FINISHER popup (yours or an
    # assist). Three distinct deaths in a couple of minutes = squad wiped —
    # in trios you're almost always fighting exactly one team.
    raw_low = ev.raw_line.lower()
    if cfg.get("team_wipe", True) and ("elim" in raw_low or "finisher" in raw_low):
        if register_elim(s.setdefault("elim_times", []), now,
                         wipe_size=int(cfg.get("team_wipe_size", 3))):
            print("  -> TEAM WIPE")
            if cfg.get("show_overlays", True) and not s.get("clutch"):
                show_text_overlay(cfg, "TEAM WIPE", size=72,
                                  position="custom:0.5,0.36",
                                  color=_theme_color(cfg, "danger", "#ff4d3d"),
                                  duration_ms=2200)
            if cfg.get("announcer_medals", True):
                import announcer
                announcer.play_medal(s["medal_sounds"], "wipe")
    if not s.get("clutch"):
        play_kill_sound(cfg)
    s["obs"].set_counter(count)
    if on_count is not None:
        on_count(count)
    if s["web"] is not None:
        s["web"].record(count, tag, ev.raw_line)
    log_kill(cfg, ev, count)

    coalesce_secs = cfg.get("kill_coalesce_seconds", 8.0)
    if "_coalesce_pending" not in s:
        s["_coalesce_pending"] = []
        s["_coalesce_deadline"] = 0.0
    s["_coalesce_pending"].append({"tag": tag, "count": count})
    s["_coalesce_deadline"] = now + coalesce_secs

    if should_overlay(cfg, ev.raw_line) and not s.get("clutch"):
        print("  -> HEADSHOT (skull popup)")
        show_overlay(cfg)


def _check_clutch(cfg, engine, s, now):
    """Auto-sweat: every ~3s, read the squad panel. All teammates DOWNED =>
    clutch mode (flair mutes, clips keep rolling). Panel clears => you
    revived them; if you racked up kills solo, celebrate NOW."""
    if not cfg.get("auto_sweat", True) or int(cfg.get("team_wipe_size", 3)) < 2:
        return
    if now - s.get("_clutch_check", -1e9) < 3.0:
        return
    s["_clutch_check"] = now
    try:
        import clutch
        down = clutch.teammates_down(cfg, engine)
    except Exception:
        return
    need = int(cfg.get("team_wipe_size", 3)) - 1

    if not s.get("clutch"):
        if down >= need:
            s["clutch"] = True
            s["clutch_kills"] = 0
            s["clutch_start"] = now
            print(f"  [clutch] {down} teammate(s) down — you're the last one "
                  "standing. Going quiet.")
            if s["web"] is not None:
                s["web"].notice("CLUTCH TIME — flair muted", "clutch")
    elif down == 0:
        kills = s.get("clutch_kills", 0)
        s["clutch"] = False
        if kills >= 1:
            _clutch_celebrate(cfg, s, kills)
        else:
            print("  [clutch] squad's back up.")
    # No timeout exit: an ELIMINATED teammate stays on the panel for minutes,
    # and a timeout would exit + instantly re-trigger, wiping the solo-kill
    # tally. Clutch ends only on resolution: panel clears, you go down
    # (GIVE UP), you exfil, or the next match's deploy screen appears.


def _end_clutch_quietly(s, reason):
    """You went down / the match ended some other way — no celebration."""
    if s.get("clutch"):
        s["clutch"] = False
        print(f"  [clutch] {reason}")


def _clutch_celebrate(cfg, s, kills):
    """The squad is back up and you carried — now the flair comes back on
    with interest."""
    print(f"  [clutch] CLUTCH PULLED OFF — {kills} kill(s) while solo")
    if s["web"] is not None:
        try:
            s["web"].notice(f"CLUTCH — {kills} solo kill(s)", "clutch")
        except Exception:
            pass
    show_text_overlay(cfg, "CLUTCH", size=84, position="custom:0.5,0.36",
                      color=_theme_color(cfg, "accent", "#d3f24b"),
                      duration_ms=2600)
    if cfg.get("announcer_medals", True):
        phrase = str(cfg.get("clutch_callout") or "HOLY SHIT!")
        def speak():
            try:
                import announcer
                import montage
                base = os.path.dirname(os.path.abspath(__file__))
                wav = announcer.ensure_callout(
                    base, phrase,
                    cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                    montage.find_ffmpeg(base, cfg),
                    pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH))
                if wav:
                    announcer.play_medal({"co": wav}, "co")
            except Exception as e:
                print(f"  [clutch] call-out failed: {e}")
        threading.Thread(target=speak, daemon=True).start()


def _scan_feed_names(cfg, engine, s, expect):
    """One kill-feed OCR pass to read gamertags before the feed lines expire.
    expect: 'victim' right after your kill popup, 'killed_by' on the downed
    screen — only that direction is logged, so a stale line the other way
    can't misfile."""
    try:
        import encounters
        base = os.path.dirname(os.path.abspath(__file__))
        for direction, name in encounters.capture(cfg, engine):
            if direction != expect:
                continue
            if not encounters.should_log(s.setdefault("_enc_recent", {}),
                                         direction, name):
                continue
            encounters.log(base, s["session_id"], direction, name)
            verb = "you downed" if direction == "victim" else "downed by"
            print(f"  [names] {verb}: {name}")
            watch = encounters.watch_hit(
                name, cfg.get("streamer_watchlist", encounters.DEFAULT_WATCHLIST))
            if watch:
                _streamer_alert(cfg, s, direction, watch)
    except Exception as e:
        print(f"  [names] scan error: {e}")


def _streamer_alert(cfg, s, direction, watch):
    """A watchlist name showed up in the feed — make it unmissable: banner,
    voiced call-out, and (for deaths) save the clip, because a streamer
    downing you is a moment you'll want on tape."""
    killed = direction == "victim"
    quiet = s.get("clutch", False)     # mid-clutch: log + clip, no flair
    text = f"YOU KILLED {watch}!" if killed else f"DOWNED BY {watch}"
    print(f"  *** STREAMER ALERT: {text} ***")
    if not quiet:
        show_text_overlay(cfg, text, size=64, position="custom:0.5,0.33",
                          color=_theme_color(cfg, "accent", "#d3f24b") if killed
                          else _theme_color(cfg, "danger", "#ff4d3d"),
                          duration_ms=2600)
    if s["web"] is not None:
        try:
            s["web"].notice(text, "streamer")
        except Exception:
            pass

    if cfg.get("announcer_medals", True) and not quiet:
        phrase = (f"You just killed {watch}!" if killed
                  else f"{watch} just downed you.")
        def speak():
            try:
                import announcer
                import montage
                base = os.path.dirname(os.path.abspath(__file__))
                wav = announcer.ensure_callout(
                    base, phrase,
                    cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                    montage.find_ffmpeg(base, cfg),
                    pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH))
                if wav:
                    announcer.play_medal({"co": wav}, "co")
            except Exception as e:
                print(f"  [watchlist] call-out failed: {e}")
        threading.Thread(target=speak, daemon=True).start()

    # Your kills are already being clipped; a death normally isn't. This one is.
    if not killed and cfg.get("watchlist_clip_deaths", True):
        now = time.monotonic()
        if now - s["last_save"] >= s["min_save"] and s["obs"].save_replay():
            s["last_save"] = now
            def on_done(dest):
                _register_replay_async(s, dest, f"downed by {watch}", s["count"])
                if s["cfg"].get("overlay_clip_saved", True):
                    show_text_overlay(s["cfg"], "CLIP SAVED", size=26,
                                      position="bottom-right", color="#aab4bd",
                                      duration_ms=1100, rise=False)
            if s["organize"]:
                rename_clip_async(s["obs"], s["session_id"],
                                  f"downedby_{watch.lower()}", s["count"],
                                  on_done=on_done)


def _maybe_capture_killer(cfg, engine, lines, s, now):
    """On the downed screen (the GIVE UP prompt persists the whole bleed-out),
    read who downed you off the kill feed — once per down."""
    if now - s.get("_last_downed_scan", -1e9) < 60:
        return
    from detector import _normalize, phrase_matches
    blob = _normalize(" ".join(lines))
    if not (phrase_matches("give up", blob, 80)
            or phrase_matches("self revive", blob, 80)):
        return
    s["_last_downed_scan"] = now
    _end_clutch_quietly(s, "you went down — no shame, it was 1vX.")
    _scan_feed_names(cfg, engine, s, expect="killed_by")


def _maybe_detect_runner(cfg, engine, lines, s):
    """Once per match: when the deployment screen is up, one full-frame scan
    for which runner/shell you're playing."""
    if not cfg.get("runner_detection", True) or s.get("runner_checked"):
        return
    try:
        import runner_detect
        if not runner_detect.is_deploy_screen(lines):
            return
        s["runner_checked"] = True
        _end_clutch_quietly(s, "new match starting — clutch state cleared.")
        runner = runner_detect.capture_runner(cfg, engine)
        s["current_runner"] = runner
        if runner:
            print(f"  [runner] playing {runner}")
        else:
            print("  [runner] deploy screen seen, but no shell/runner name read")
    except Exception as e:
        s["runner_checked"] = True
        print(f"  [runner] detection error: {e}")


def _maybe_capture_exfil(cfg, engine, lines, s, now):
    """When the EXFILTRATED summary screen is up, grab it once and log the
    match stats + a kill-count audit. Re-arms after 3 minutes (next match)."""
    if now - s.get("_last_exfil", -1e9) < 180:
        return
    try:
        import exfil_stats
        if not exfil_stats.looks_like_exfil(lines):
            return
        s["_last_exfil"] = now
        # Exfiling AS the last one standing is a clutch by definition.
        if s.get("clutch"):
            kills = s.get("clutch_kills", 0)
            s["clutch"] = False
            if kills >= 1:
                _clutch_celebrate(cfg, s, kills)
            else:
                print("  [clutch] exfiled solo — the quiet kind of clutch.")
        save_dir = ""
        try:
            rec = s["obs"].get_record_directory()
            if rec:
                save_dir = os.path.join(rec, "Marathon Sessions", s["session_id"])
        except Exception:
            pass
        stats_d, squad = exfil_stats.capture_exfil_stats(cfg, engine, save_dir)
        # Audit THIS match's detected kills vs the game's count, then reset the
        # per-match tally for the next match.
        match_tags = s.get("match_tags", [])
        print(exfil_stats.report(stats_d, Counter(match_tags)))
        if stats_d:
            exfil_stats.accumulate_accuracy(s.setdefault("accuracy", {}),
                                            stats_d, Counter(match_tags))
            base = os.path.dirname(os.path.abspath(__file__))
            exfil_stats.log_match_stats(base, s["session_id"], stats_d,
                                        len(match_tags))
            if squad:
                exfil_stats.log_squad_stats(base, s["session_id"], squad,
                                            your_runner=s.get("current_runner", ""))
                names = ", ".join(f"{p.get('name') or p['position']}"
                                  f" ({p.get('inventory_value', '?')} loot)"
                                  for p in squad)
                print(f"  [squad] logged {len(squad)} player(s): {names}")
        s["match_tags"] = []
        s["runner_checked"] = False       # re-detect next match
        s["last_runner"] = s.get("current_runner", "")
        s["current_runner"] = ""
        if cfg.get("make_match_reels", True) and save_dir:
            _build_match_reel_async(cfg, s, save_dir, stats_d)
    except Exception as e:
        print(f"  [exfil] error: {e}")


def _build_match_reel_async(cfg, s, session_dir, stats_d):
    """Build this match's highlight reel in the background and pop it onto the
    iPad dashboard. Waits before building so a final kill that's still in the
    coalesce window / being organized makes it into the reel."""
    s["match_num"] += 1
    match_num = s["match_num"]

    def work():
        time.sleep(30)
        clips, s["match_clips"] = s["match_clips"][:], []
        if not clips:
            print(f"  [reel] match {match_num}: no clips this match, skipping reel")
            return
        try:
            import match_reel
            import montage
            base = os.path.dirname(os.path.abspath(__file__))
            ffmpeg = montage.find_ffmpeg(base, cfg)
            out = os.path.join(session_dir, "reels", f"match_{match_num}.mp4")
            total_kills = sum(c.get("kills", 1) for c in clips)
            sub = []
            b1 = []
            if stats_d.get("runner_elims") is not None:
                b1.append(f"{stats_d['runner_elims']} RUNNER ELIMS")
            if stats_d.get("runner_damage") is not None:
                b1.append(f"{stats_d['runner_damage']} RUNNER DMG")
            if b1:
                sub.append("  ·  ".join(b1))
            b2 = []
            if stats_d.get("run_time"):
                b2.append(f"RUN TIME {stats_d['run_time']}")
            b2.append(time.strftime("%Y-%m-%d %H:%M"))
            sub.append("  ·  ".join(b2))

            tracks = []
            if cfg.get("reel_music", True):
                tracks = match_reel.list_music(os.path.join(base, "music"))

            ok = match_reel.build_match_reel(
                clips, out, ffmpeg,
                "MATCH HIGHLIGHTS", total_kills, sub,
                os.path.join(base, "marathon_wordmark.png"),
                music_volume=cfg.get("reel_music_volume", 0.08),
                music_tracks=tracks, theme=cfg.get("theme"))
            if ok:
                print(f"  [reel] match {match_num} highlights -> {out}")
                if s["web"] is not None:
                    s["web"].add_reel(f"Match {match_num} — {len(clips)} clip"
                                      f"{'s' if len(clips) != 1 else ''}", out)
                if cfg.get("overlay_reel_ready", True):
                    show_text_overlay(cfg, "HIGHLIGHTS READY", size=26,
                                      position="bottom-right", color="#d3f24b",
                                      duration_ms=2000, rise=False)
                if cfg.get("reel_announcer", True):
                    import announcer
                    potg = match_reel.pick_potg(match_reel._normalize_clips(clips))
                    script = announcer.stat_line(
                        total_kills, stats_d,
                        potg_tag=(potg["tag"] if potg else ""),
                        player=cfg.get("announcer_player_name", ""),
                        runner=s.get("last_runner", ""))
                    wav = announcer.synth_to_wav(
                        script,
                        os.path.join(session_dir, "reels", f"match_{match_num}_tts.wav"),
                        voice=cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                        pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH))
                    if wav:
                        aout = os.path.join(session_dir, "reels",
                                            f"match_{match_num}_announced.mp4")
                        if match_reel.add_announcer(out, aout, wav, ffmpeg):
                            print(f"  [reel] announced version -> {aout}")
                            if s["web"] is not None:
                                s["web"].add_reel(f"Match {match_num} (announced)", aout)
                        try:
                            os.remove(wav)
                        except OSError:
                            pass
        except Exception as e:
            print(f"  [reel] error: {e}")

    threading.Thread(target=work, daemon=True).start()


def _check_manual_kill(cfg, s, on_count=None):
    """If the iPad '+1 KILL' button was tapped, count a kill the detector
    missed — full pipeline: counter, ding, clip, instant replay."""
    web = s["web"]
    if web is not None and getattr(web, "pop_kill_request", None) and web.pop_kill_request():
        from detector import KillEvent
        print("  [+1 kill added from iPad]")
        ev = KillEvent(timestamp=time.monotonic(),
                       raw_line="MANUAL +1 (added from iPad)",
                       killer="", victim="", is_self_kill=True)
        _handle_kill(cfg, ev, s, on_count)


def _check_manual_clip(s):
    """If the iPad 'SAVE CLIP' button was tapped, save a replay now."""
    web = s["web"]
    if web is not None and web.pop_clip_request():
        now = time.monotonic()
        if now - s["last_save"] >= s["min_save"]:
            if s["obs"].save_replay():
                s["last_save"] = now
                print("  [manual clip saved from iPad]")
                if s["organize"]:
                    rename_clip_async(s["obs"], s["session_id"], "manual",
                                      s["count"],
                                      on_done=_clip_ready_callback(s, "manual", s["count"]))
            else:
                print("  [manual clip: replay save failed]")
        else:
            print("  [manual clip: too soon after last save]")


def run_live(cfg: dict, dry_run: bool = False, stop_event=None, on_count=None):
    _install_session_log()
    try:
        return _run_live_inner(cfg, dry_run, stop_event, on_count)
    finally:
        _close_session_log()


def _run_live_inner(cfg: dict, dry_run: bool = False, stop_event=None, on_count=None):
    det, mode = build_detector(cfg)

    from capture import make_capture
    from ocr import OCREngine

    _tune_performance(cfg)
    engine = OCREngine(cfg.get("ocr_engine", "easyocr"), cfg.get("ocr_upscale", 3))
    s = _setup_session(cfg, dry_run)

    poll_fps = max(1, cfg.get("poll_fps", 5))
    interval = 1.0 / poll_fps
    debug_ocr = cfg.get("debug_ocr", False)

    overlay_det = None
    if cfg.get("show_overlays", True) and mode == "popup":
        overlay_det = PopupDetector(
            trigger_phrases=cfg.get("overlay_events") or ["PRECISION DOWN"],
            phrase_match_threshold=cfg.get("popup_match_threshold", 80),
            absence_frames=cfg.get("popup_absence_frames", 2),
            confirm_frames=cfg.get("popup_confirm_frames", 2),
            require_reward=cfg.get("require_reward", True),
        )

    region = cfg.get("detect_region_frac") or cfg.get("detect_region") or cfg.get("feed_region")
    print(f"Detecting [{mode}] at {poll_fps} fps via {cfg.get('capture_source')}. "
          f"Region={region}. "
          f"{'DRY-RUN' if dry_run else 'LIVE'}. Ctrl-C to stop.")
    # Echo the live config so you can confirm the running session has the
    # latest settings (a mid-session file update does NOT take effect until
    # you stop and start again).
    if mode == "popup":
        print(f"  config: confirm_frames={cfg.get('popup_confirm_frames')}, "
              f"cooldown={cfg.get('popup_cooldown_seconds')}s, "
              f"suppress={cfg.get('suppress_phrases')}")
    print()

    with make_capture(cfg) as cap:
        try:
            while not (stop_event is not None and stop_event.is_set()):
                loop_start = time.monotonic()
                img = cap.grab()
                lines = engine.read_lines(img)
                if debug_ocr and lines:
                    print(f"  [ocr] {' | '.join(lines)}")
                blocked = is_suppressed(cfg, lines)
                events = [] if blocked else detect_events(det, mode, lines, now=loop_start)

                for ev in events:
                    _handle_kill(cfg, ev, s, on_count)

                # Right after a kill, read the victim's gamertag off the kill
                # feed (the feed line expires in seconds — no waiting).
                if events and cfg.get("track_names", True):
                    _scan_feed_names(cfg, engine, s, expect="victim")

                _check_coalesce(s)

                if blocked and cfg.get("capture_exfil_stats", True):
                    _maybe_capture_exfil(cfg, engine, lines, s, loop_start)
                if blocked and cfg.get("track_names", True):
                    _maybe_capture_killer(cfg, engine, lines, s, loop_start)

                if not blocked:
                    _maybe_detect_runner(cfg, engine, lines, s)
                    _check_clutch(cfg, engine, s, loop_start)

                if overlay_det is not None and not blocked and not s.get("clutch"):
                    oev = overlay_det.process_frame(lines, now=loop_start)
                    if oev:
                        print("  -> HEADSHOT (skull popup)")
                        show_overlay(cfg)

                _check_manual_kill(cfg, s, on_count)
                _check_manual_clip(s)

                elapsed = time.monotonic() - loop_start
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        except KeyboardInterrupt:
            pass

    _flush_coalesce(s)
    if s["web"] is not None:
        s["web"].set_running(False)
    acc_line = exfil_stats_accuracy(s)
    if acc_line:
        print(acc_line)
    _end_session(cfg, s["session_tags"], s["session_start"],
                 s["session_start_wall"], dry_run, s["obs"], s["session_id"])


def exfil_stats_accuracy(s) -> str:
    try:
        import exfil_stats
        return exfil_stats.accuracy_summary(s.get("accuracy", {}))
    except Exception:
        return ""


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

    # Shareable match-card image
    if cfg.get("make_card", True):
        try:
            import matchcard
            base = os.path.dirname(os.path.abspath(__file__))
            card = os.path.join(base, "stats", "cards", f"card_{session_id or 'session'}.png")
            p = matchcard.build_card(session, card, os.path.join(base, "marathon_wordmark.png"))
            if p:
                print(f"Match card: {p}")
        except Exception as e:
            print(f"(card error: {e})")

    # Highlight montage of this session's clips (needs ffmpeg + organized clips).
    session_dir = ""
    if obs is not None and session_id:
        rec = obs.get_record_directory()
        session_dir = os.path.join(rec, "Marathon Sessions", session_id) if rec else ""
    if cfg.get("make_montage", True) and session_dir:
        try:
            import montage
            base = os.path.dirname(os.path.abspath(__file__))
            montage.build_montage(session_dir, montage.find_ffmpeg(base, cfg))
        except Exception as e:
            print(f"(montage error: {e})")

    # Vertical Shorts render of each clip (needs ffmpeg + organized clips).
    if cfg.get("make_shorts", True) and session_dir:
        try:
            import montage
            import shorts
            base = os.path.dirname(os.path.abspath(__file__))
            shorts.build_shorts(session_dir, montage.find_ffmpeg(base, cfg),
                                with_labels=cfg.get("shorts_labels", True),
                                theme=cfg.get("theme"))
        except Exception as e:
            print(f"(shorts error: {e})")

    # Session highlight reel (all clips + title card + Play of the Game), and
    # optional unlisted YouTube upload of just that one video.
    if session_dir and (cfg.get("make_session_reel", True)
                        or cfg.get("youtube_upload_session_reel", False)):
        _build_session_reel_and_upload(cfg, session_dir, tags)


def _session_clips_from_dir(session_dir: str):
    """List this session's organized clips (top level only) as reel dicts,
    inferring the kill count from the filename tag (e.g. down+finisher = 2)."""
    import montage
    out = []
    for f in sorted(os.listdir(session_dir)):
        if not f.lower().endswith(montage.VIDEO_EXTS):
            continue
        if f.lower().startswith("highlights") or f.lower().startswith("session"):
            continue
        # NNN_tag_time.ext  ->  tag
        parts = f.split("_")
        tag = parts[1] if len(parts) >= 3 else "kill"
        kills = len([p for p in tag.split("+") if p]) or 1
        out.append({"path": os.path.join(session_dir, f), "kills": kills, "tag": tag})
    return out


def _build_session_reel_and_upload(cfg, session_dir, tags):
    try:
        import match_reel
        import montage
        base = os.path.dirname(os.path.abspath(__file__))
        clips = _session_clips_from_dir(session_dir)
        if not clips:
            print("  [session reel] no clips this session, skipping")
            return
        ffmpeg = montage.find_ffmpeg(base, cfg)
        out = os.path.join(session_dir, "session_reel.mp4")
        c = Counter(tags)
        total = len(tags)
        sub = [f"{c.get('finisher',0)} FINISHERS  ·  {c.get('precision',0)} PRECISION  "
               f"·  {c.get('assist',0)} ASSISTS",
               time.strftime("%Y-%m-%d")]
        tracks = []
        if cfg.get("reel_music", True):
            tracks = match_reel.list_music(os.path.join(base, "music"))
        ok = match_reel.build_match_reel(
            clips, out, ffmpeg, "SESSION HIGHLIGHTS", total, sub,
            os.path.join(base, "marathon_wordmark.png"),
            music_volume=cfg.get("reel_music_volume", 0.08),
            music_tracks=tracks, theme=cfg.get("theme"))
        if not ok:
            print("  [session reel] build failed")
            return
        print(f"  [session reel] -> {out}")

        if cfg.get("youtube_upload_session_reel", False):
            import youtube_upload
            title = f"Marathon — {total} kills — {time.strftime('%b %d, %Y')}"
            desc = ("Auto-uploaded Marathon session highlights.\n"
                    f"{c.get('finisher',0)} finishers, {c.get('precision',0)} precision, "
                    f"{c.get('assist',0)} assists.")
            youtube_upload.upload(out, title, desc, base,
                                  privacy=cfg.get("youtube_privacy", "unlisted"))
    except Exception as e:
        print(f"  [session reel] error: {e}")


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
    p.add_argument("--teach", action="store_true",
                   help="teach-a-game wizard: watch the screen while you get "
                        "a kill in ANY game, then write a profile for it")
    p.add_argument("--wrapped", action="store_true",
                   help="build this week's Marathon Wrapped card from your stats")
    p.add_argument("--bench", action="store_true",
                   help="measure OCR speed on THIS machine and say whether it "
                        "can keep up (run it with the game open for a real number)")
    p.add_argument("--tray", action="store_true",
                   help="run from a system-tray skull icon (no console window)")
    args = p.parse_args()

    # Self-update before anything loads config or starts detecting. If files
    # changed, this relaunches the process so the new code actually runs.
    if not (args.test_image or args.test_lines):
        try:
            import updater
            msg = updater.update_and_relaunch_if_needed(
                os.path.dirname(os.path.abspath(__file__)))
            print(msg)
        except SystemExit:
            raise
        except Exception as e:
            print(f"(update check skipped: {e})")

    cfg = load_config(args.config)

    if args.teach:
        import teach
        teach.run(cfg)
    elif args.test_lines:
        run_test_lines(cfg, args.test_lines)
    elif args.test_image:
        run_test_image(cfg, args.test_image)
    elif args.bench:
        run_bench(cfg)
    elif args.wrapped:
        import wrapped
        base = os.path.dirname(os.path.abspath(__file__))
        rec = ""
        try:
            from obs_client import OBSClient
            obs_cfg = cfg.get("obs", {})
            c = OBSClient(host=obs_cfg.get("host", "localhost"),
                          port=obs_cfg.get("port", 4455),
                          password=obs_cfg.get("password", ""))
            c.connect()
            rec = c.get_record_directory() or ""
        except Exception:
            pass  # no OBS running: card still builds, just without Best Play
        out = wrapped.build_wrapped(base, rec)
        if out:
            try:
                os.startfile(out)  # pop it open (Windows)
            except Exception:
                pass
    elif args.tray:
        run_tray(cfg, dry_run=args.dry_run)
    else:
        run_live(cfg, dry_run=args.dry_run)


def run_bench(cfg, frames: int = 25):
    """Answer 'will my PC handle this?' with a measurement instead of a guess:
    time the real capture+OCR loop on this machine and compare against the
    poll_fps budget. Run it with the game open for the honest number."""
    from capture import make_capture
    from ocr import OCREngine

    print("Benchmarking capture + OCR on this machine...")
    try:
        import torch
        if torch.cuda.is_available():
            print(f"  GPU: {torch.cuda.get_device_name(0)} (OCR runs on GPU)")
        else:
            print("  GPU: none detected by PyTorch — OCR will run on CPU "
                  "(works, but catches fewer fast popups; see README)")
    except Exception:
        pass

    engine = OCREngine(cfg.get("ocr_engine", "easyocr"), cfg.get("ocr_upscale", 3))
    poll_fps = max(1, cfg.get("poll_fps", 5))
    budget_ms = 1000.0 / poll_fps

    with make_capture(cfg) as cap:
        engine.read_lines(cap.grab())      # warm-up (model load, first-run JIT)
        times = []
        for _ in range(frames):
            t0 = time.perf_counter()
            engine.read_lines(cap.grab())
            times.append((time.perf_counter() - t0) * 1000)

    avg, worst = sum(times) / len(times), max(times)
    print(f"  {frames} frames: avg {avg:.0f} ms, worst {worst:.0f} ms "
          f"(budget: {budget_ms:.0f} ms per frame at poll_fps={poll_fps})")
    if worst < budget_ms * 0.7:
        print("  VERDICT: plenty of headroom — this machine handles it easily.")
    elif avg < budget_ms:
        print("  VERDICT: workable. If kills get missed, lower poll_fps to 3 "
              "or ocr_upscale to 2 in config.yaml.")
    else:
        print("  VERDICT: too slow at current settings. Set poll_fps: 3 and "
              "ocr_upscale: 2 — or install the GPU build "
              "(see QUICKSTART).")


if __name__ == "__main__":
    main()
