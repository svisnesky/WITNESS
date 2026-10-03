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
import re
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
_advertiser = None    # Bonjour registration for the iOS app (discovery.py)

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
        try:
            print(_build_line(base))
        except Exception:
            pass            # a missing build line must never cost the log
        return _log_path
    except Exception as e:
        print(f"(could not open session log: {e})")
        return None


def _build_line(base: str) -> str:
    """'WITNESS 0.1.0 (abc1234) | Python 3.12.4 | Windows-11-...' plus the last
    update check, for the top of every session log: these logs are what bug
    reports attach, and they never said which build wrote them. The sha is the
    updater's .app_version ('dev' without one). No torch import here: this
    runs before the OCR engine exists."""
    import platform
    try:
        from version import VERSION as ver
    except Exception:
        ver = "?"
    sha = ""
    try:
        with open(os.path.join(base, ".app_version"), encoding="utf-8") as f:
            sha = f.read().strip()[:7]
    except OSError:
        pass
    line = (f"WITNESS {ver} ({sha or 'dev'}) | Python {platform.python_version()}"
            f" | {platform.platform()}")
    try:
        with open(os.path.join(base, "update_status.txt"), encoding="utf-8",
                  errors="replace") as f:
            last = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        if last:
            line += f" | update: {last[-1]}"
    except OSError:
        pass
    return line


def _close_session_log():
    global _log_fh, _log_prev_stdout
    try:
        if _log_path:
            print(f"Session log saved: {_log_path}  (attach it to a bug report: "
                  "github.com/svisnesky/WITNESS/issues)")
        if _log_prev_stdout is not None:
            sys.stdout = _log_prev_stdout
        if _log_fh is not None:
            _log_fh.close()
    except Exception:
        pass
    finally:
        _log_fh = None; _log_prev_stdout = None


def _read_text_cfg(path) -> str:
    """Text of a user-editable YAML file (config.yaml, settings_override.yaml,
    games/*.yaml).

    UTF-8 first, with or without the BOM Notepad adds. Python's default would
    be the Windows ANSI code page: config.yaml is UTF-8 with em dashes from
    line 1, so it failed to load at all on Japanese/Chinese/Korean Windows and
    turned every em dash into 'â€”' (into the uploaded YouTube title) on
    US/EU Windows. Falls back to the locale encoding for a file someone saved
    as ANSI with a typed non-ASCII character. Raises UnicodeDecodeError when
    neither fits."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        import locale
        return raw.decode(locale.getpreferredencoding(False) or "utf-8")


def _config_unreadable(path, e) -> RuntimeError:
    """A plain message naming the file (and the line, when known) for a
    config that can't be decoded or parsed. Under pythonw a raw traceback
    here meant the window simply never opened."""
    name = os.path.basename(path) or "config.yaml"
    line, why = None, str(e)
    if isinstance(e, UnicodeDecodeError):
        try:
            line = e.object[:e.start].count(b"\n") + 1
        except Exception:
            pass
        why = (f"it is not UTF-8 text ({e.reason} at byte {e.start}); "
               "save it as UTF-8")
    elif isinstance(e, yaml.YAMLError):
        mark = getattr(e, "problem_mark", None)
        if mark is not None:
            line = mark.line + 1
        why = getattr(e, "problem", None) or str(e)
        ctx = getattr(e, "context", None)
        if ctx:
            why = f"{why} ({ctx})"
    where = f" (line {line})" if line else ""
    return RuntimeError(f"{name} could not be read{where}: {why} - {path}")


def load_config(path=CONFIG_PATH) -> dict:
    try:
        cfg = yaml.safe_load(_read_text_cfg(path))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        raise _config_unreadable(path, e) from e
    if not isinstance(cfg, dict):
        raise RuntimeError(f"{os.path.basename(path) or 'config.yaml'} could not "
                           f"be read: it is empty or not a list of settings - {path}")
    base = os.path.dirname(os.path.abspath(path)) or "."

    # The dashboard's settings live in an override file (config.yaml comments
    # never get rewritten) — read it early because it may pick the game.
    override = {}
    ov_path = os.path.join(base, "settings_override.yaml")
    if os.path.exists(ov_path):
        try:
            override = yaml.safe_load(_read_text_cfg(ov_path)) or {}
            if not isinstance(override, dict):
                raise ValueError("it is not a list of settings")
        except Exception as e:
            override = {}
            print(f"(could not read settings_override.yaml: {e})")

    # Game profile: games/<game>.yaml overrides config.yaml's detection
    # settings (config.yaml's are Marathon's). Marathon needs no file; other
    # games get theirs from the teach-a-game wizard. The dashboard override
    # is applied LAST so live settings changes always win.
    game = str(override.get("game") or cfg.get("game") or "marathon").strip().lower()
    if game and game != "marathon":
        pfile = os.path.join(base, "games", f"{game}.yaml")
        try:
            profile = yaml.safe_load(_read_text_cfg(pfile)) or {}
            if not isinstance(profile, dict):
                raise ValueError("it is not a list of settings")
            # Detection, theme and feature-gate keys only. A profile is shared
            # data, so it must not be able to change which executables run
            # (ffmpeg_path, obs.exe_path), how videos are published
            # (youtube_*) or who can reach the dashboard (web_lan). And
            # cfg.update is shallow: a profile's obs: block replaced the
            # user's whole host/port/password.
            ignored = sorted(str(k) for k in profile if k not in PROFILE_KEYS)
            cfg.update({k: v for k, v in profile.items() if k in PROFILE_KEYS})
            print(f"(game profile: {profile.get('game_name', game)} — {pfile})")
            if ignored:
                print(f"(game profile {pfile}: ignored non-profile keys {ignored}"
                      " - set those in config.yaml)")
        except FileNotFoundError:
            print(f"(game {game!r} has no profile at {pfile} — using Marathon "
                  "settings; run  python main.py --teach  to create one)")
        except Exception as e:
            print(f"(could not read game profile {pfile}: {e})")

    cfg.update(override)
    _migrate_legacy_voice(cfg, override)
    return cfg


# What a game profile (games/<game>.yaml) may set. Everything teach.py writes,
# plus the hand-tuned detection keys build_detector and the live loop read.
# teach.PROFILE_DEFAULTS' keys are copied here rather than imported, so loading
# the config never imports teach; tests/test_profile_keys.py keeps them in step.
PROFILE_KEYS = frozenset({
    "game_name", "popup_trigger_phrases", "popup_match_threshold",
    "popup_confirm_frames", "popup_absence_frames", "popup_cooldown_seconds",
    "detection_mode", "detect_region_frac", "detect_region", "require_reward",
    "suppress_phrases", "overlay_events", "theme", "kill_coalesce_seconds",
    # teach.PROFILE_DEFAULTS: Marathon-only systems a new game starts with off
    "capture_exfil_stats", "runner_detection", "squad_stats", "team_wipe",
    "track_names", "auto_sweat",
})


def _migrate_legacy_voice(cfg, override) -> None:
    """Configs written before the deep-Christopher upgrade carry the old
    default voice (GuyNeural, no pitch key) — and config.yaml is never
    overwritten by updates, so the better voice never arrived. Upgrade once,
    persisting to settings_override.yaml (which wins over config.yaml). A
    deliberate later choice of Guy (made via a config that HAS a pitch key,
    or a prior override) is left alone."""
    legacy = (cfg.get("announcer_voice") == "en-US-GuyNeural"
              and "announcer_pitch" not in cfg
              and "announcer_voice" not in override)
    if not legacy:
        return
    changes = {"announcer_voice": "en-US-ChristopherNeural",
               "announcer_pitch": "-18Hz"}
    cfg.update(changes)
    try:
        save_setting_overrides(changes)
        print("(voice upgraded to the deep broadcast announcer — "
              "en-US-ChristopherNeural at -18Hz)")
    except Exception:
        pass


_RECORD_DIR_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 ".record_dir")


def free_gb(path: str):
    """Free space in GB on the drive holding `path`, or None if unknown."""
    try:
        import shutil as _sh
        probe = path
        while probe and not os.path.isdir(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                return None
            probe = parent
        if not probe:
            return None
        return _sh.disk_usage(probe).free / (1024 ** 3)
    except Exception:
        return None


def check_disk_space(cfg, record_dir: str, web=None, state=None) -> None:
    """Warn when the clip drive is running out. A session is 1-2 GB of clips,
    reels and Shorts, and a full drive fails SILENTLY in the worst way: OBS
    can't write clips, every render dies, and the night is lost with only
    scattered ffmpeg errors to show for it. Warns once per session per level."""
    if not record_dir:
        return
    gb = free_gb(record_dir)
    if gb is None:
        return
    low = float(cfg.get("disk_warn_gb", 10))
    crit = float(cfg.get("disk_critical_gb", 3))
    level = "critical" if gb < crit else ("low" if gb < low else "")
    if not level:
        return
    seen = state.setdefault("_disk_warned", set()) if state is not None else set()
    if level in seen:
        return
    seen.add(level)
    msg = (f"only {gb:.1f} GB free on the clip drive"
           + (" — clips and reels WILL start failing" if level == "critical"
              else " (a session runs 1-2 GB)"))
    print(f"  [disk] {'CRITICAL' if level == 'critical' else 'WARNING'}: {msg}")
    if web is not None:
        try:
            web.notice(f"LOW DISK — {gb:.1f} GB free", "alert")
        except Exception:
            pass


def cache_record_dir(path: str) -> None:
    """Remember OBS's recording folder so the Archive can find past sessions
    on the very next app open (before START is pressed)."""
    try:
        with open(_RECORD_DIR_CACHE, "w", encoding="utf-8") as f:
            f.write(path)
    except Exception:
        pass


def cached_record_dir() -> str:
    try:
        with open(_RECORD_DIR_CACHE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def save_setting_overrides(changes: dict) -> None:
    """Persist dashboard-changed settings to settings_override.yaml."""
    base = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base, "settings_override.yaml")
    current = {}
    if os.path.exists(path):
        try:
            current = yaml.safe_load(_read_text_cfg(path)) or {}
            if not isinstance(current, dict):
                current = {}
        except Exception:
            current = {}
    current.update(changes)
    # Written as UTF-8, matching the read: safe_dump escapes non-ASCII today,
    # but a write in the ANSI code page next to a UTF-8 read is how a file
    # corrupts itself on the first round trip.
    with open(path, "w", encoding="utf-8") as f:
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
    """Detector interface -> list of KillEvents for this frame. Can be more
    than one: a double kill shows two popups stacked in the same frame."""
    return det.process_frame_all(lines, now)


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
    UI + lingering kill-feed text.

    A frame that matches a KILL TRIGGER at least as well as a suppress phrase is
    never suppressed. Suppression is a hard block — the frame never reaches the
    detector — so a fuzzy collision silently destroys a real kill with no trace
    in the log. And the phrases genuinely collide: OCR reading "RUNNER DOWN" as
    "RUNNER DAWN" (an O->A slip; O->I slips are already in Stan's logs) scores
    82 against the "RUNNER DAMAGE" suppress phrase while scoring 91 against the
    real "RUNNER DOWN" trigger. Whichever the text matches better wins."""
    from rapidfuzz import fuzz
    from detector import _normalize, fold_ocr_tokens, phrase_matches
    phrases = cfg.get("suppress_phrases",
                      ["SELF REVIVE", "GIVE UP", "RUNNER DAMAGE",
                       "CREW REVIVES", "INVENTORY VALUE"])
    blob = _normalize(" ".join(lines))
    if not blob:
        return False
    hits = [_normalize(p) for p in phrases
            if phrase_matches(_normalize(p), blob, 80)]
    if not hits:
        return False
    triggers = cfg.get("popup_trigger_phrases",
                       ["RUNNER DOWN", "PRECISION DOWN", "FINISHER", "RUNNER ELIM"])
    t_thresh = cfg.get("popup_match_threshold", 85)
    # Score the trigger side with DOIN read as DOWN (detector.OCR_TOKEN_FIXES):
    # otherwise a clean GIVE UP (100) beats every 'RUNNER DOIN' kill (90.9) and a
    # kill traded on the death screen is dropped. The suppress side is unchanged.
    tblob = fold_ocr_tokens(blob)
    best_trigger = max((fuzz.partial_ratio(_normalize(t), tblob)
                        for t in triggers if str(t).strip()), default=0)
    if best_trigger >= t_thresh:
        best_suppress = max(fuzz.partial_ratio(h, blob) for h in hits)
        if best_trigger >= best_suppress:
            return False          # reads more like a kill than a dead screen
    return True


# The reward each popup pays: +10 elim, +15 down, +25 precision, +50 finisher.
_REWARD_OF_TAG = {"kill": "10", "down": "15", "precision": "25", "finisher": "50"}


def classify_event(raw_line: str) -> str:
    """Tag a kill by type from its popup text (for clip names + the recap).

    The tag is the trigger phrase that actually matched best, scored on
    glyph-folded tokens (detector._tokens), not the first keyword found
    anywhere. The old order-of-checks read a down segment as 'precision' when a
    garbled PRECISION from the popup below bled into it, and fell through to
    'kill' (an ELIM) whenever no keyword passed, so reading DOIN as DOWN in
    the detector would have minted false elims without this."""
    import re

    from rapidfuzz import fuzz

    from detector import _tokens, phrase_matches, phrase_windows, skel_phrase

    # A manual "+1 KILL" press is you correcting a DOWN the OCR missed, so it
    # must be distinguishable from a real RUNNER ELIM popup: both used to tag
    # 'kill', which meant _reconcile_missed counted the press as an elim, still
    # saw a missing down, and credited the same kill a SECOND time.
    if raw_line.startswith("MANUAL"):
        return "manual_kill"

    toks = _tokens(raw_line)
    sks = [sk for _, sk in toks]
    if not sks:
        return "kill"
    phrases = (("runner down", "down"), ("precision down", "precision"),
               ("runner elim", "kill"), ("finisher", "finisher"))
    cands = [(score, i, j, tag) for p, tag in phrases
             for score, i, j in phrase_windows(toks, skel_phrase(p), 80)]
    if cands:
        best = max(c[0] for c in cands)
        top = [c for c in cands if c[0] == best]
        if len({c[3] for c in top}) > 1:
            # A garble can score the same against two phrases ('RUNNER ELN' is as
            # close to DOWN as to ELIM). Let the popup's own reward break the tie.
            nums = set(re.findall(r"(?<!\d)(10|15|25|50)(?!\d)", raw_line))
            by_reward = [c for c in top if _REWARD_OF_TAG[c[3]] in nums]
            if by_reward:
                top = by_reward
        _s, i, j, tag = min(top, key=lambda c: c[1])     # earliest on a tie
        # The [ASSIST] marker is glued to ITS phrase (directly after the phrase
        # word in 44 of 44 real reads). Look only in the few tokens after this
        # phrase, stopping at the next phrase: when a stacked frame reads as one
        # popup, a NEIGHBOUR's [ASSIST] must not turn your own kill into an
        # assist (and a lost own kill). There is deliberately no loose "any
        # word between phrase and reward is an assist" rule: real junk like
        # 'SMSTE' / 'ISTM' sits there and would flip own kills.
        nxt = min([c[1] for c in cands if c[1] >= j] + [len(sks)])
        # Score both the folded token and the plain one with a glued reward's
        # trailing digits removed ('[ASSIT]+15' -> 'assit'), and adjacent pairs
        # joined, so a split marker ('[AS SIST]') still reads as one.
        stop = min(nxt, j + 3)
        norms = [nm for nm, _ in toks]
        look = (sks[j:stop] + [sks[j - 1]]
                + [re.sub(r"\d+$", "", nm) for nm in norms[j:stop] + [norms[j - 1]]]
                + [a + b for a, b in zip(sks[j:stop], sks[j + 1:stop])]
                + [re.sub(r"\d+$", "", a + b) for a, b in zip(norms[j:stop], norms[j + 1:stop])])
        if any("assist" in t or (5 <= len(t) <= 8 and fuzz.ratio("assist", t) >= 80)
               for t in look):
            return "assist"
        return tag
    # No phrase window passed (a heavily garbled read the detector still fired
    # on): fall back to the closest phrase, never an unconditional 'kill'.
    blob = " ".join(sks)
    if phrase_matches("assist", blob, 78):
        return "assist"
    return max((fuzz.partial_ratio(skel_phrase(p), blob), tag) for p, tag in phrases)[1]


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
        img_name = cfg.get("overlay_image", "witness_headshot.png")
        # soft upgrade: configs that still carry the old default get the
        # WITNESS headshot skull (an explicit custom image is left alone).
        # A string comparison only: marathon_skull.png is Marathon game art
        # and is no longer shipped, so this must not need that file.
        if img_name == "marathon_skull.png":
            img_name = "witness_headshot.png"
        image = os.path.join(base, img_name)
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
                      color="#9184d9", duration_ms=1600, rise=True,
                      critical=False):
    """Flash click-through text over the game (DOUBLE KILL, CLIP SAVED...).
    Same animation language as the skull; never blocks the capture loop.

    critical=True bypasses show_overlays. That switch exists to mute FLAIR; it
    must not also mute a warning that you are losing footage right now. Stan
    turning off the fun banners should not cost him another black night."""
    if not critical and not cfg.get("show_overlays", True):
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
    """Append one row to session_log.csv. Never raises an OSError: Excel locks
    a CSV it has open, and that PermissionError used to escape _handle_kill and
    end the whole session. The row is lost; the kill, its clip and the rest of
    the night are not."""
    path = os.path.join(os.path.dirname(CONFIG_PATH), cfg.get("session_log", "session_log.csv"))
    new_file = not os.path.exists(path)
    try:
        # UTF-8: raw_line and victim are OCR text and can hold characters the
        # Windows code page cannot encode. No code reads this file back.
        with open(path, "a", newline="", encoding="utf-8") as f:
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
    except OSError as e:
        print(f"  [log] could not write {path}: {e} (is it open in Excel?)")


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


def _test_image_region(cfg: dict, img, crop: bool = True):
    """(image to OCR, what was done) for run_test_image: the live detect
    region when `img` looks like a full screenshot, else `img` as given.

    The live loop OCRs only the detect region. Reading a whole 4K screenshot
    instead shrank it to the 800 px cap (about 0.21x, popup text ~7 px), so
    real kill screenshots said 'no kill detected'. A small, already-cropped
    image must NOT be cut again: detect_region_frac would take a tiny piece of
    it. Same region order as capture.make_capture: frac first."""
    from capture import _crop, _resolve
    H, W = img.shape[:2]
    if not crop:
        return img, f"--no-crop: reading the whole {W}x{H} image as given"
    region = cfg.get("detect_region") or cfg.get("feed_region")
    frac = cfg.get("detect_region_frac")
    if not (region or frac):
        return img, (f"no detect_region in config.yaml: reading the whole "
                     f"{W}x{H} image")
    try:
        r = _resolve(region, frac, W, H)
    except Exception as e:
        return img, f"detect region unusable ({e}): reading the whole image"
    full_frame = max(W, H) >= 1280 and W > 1.5 * r["w"]
    if not full_frame:
        return img, (f"{W}x{H} looks already cropped: reading it as given "
                     "(--no-crop forces this)")
    if (r["w"] <= 0 or r["h"] <= 0 or r["x"] < 0 or r["y"] < 0
            or r["x"] + r["w"] > W or r["y"] + r["h"] > H):
        return img, (f"the detect region (x={r['x']}, y={r['y']}, w={r['w']}, "
                     f"h={r['h']}) doesn't fit this {W}x{H} screenshot - run "
                     "Calibrate; reading the whole image")
    return _crop(img, r), (f"Cropped {W}x{H} screenshot to the detect region "
                           f"x={r['x']}, y={r['y']}, w={r['w']}, h={r['h']} "
                           "(same as live)")


def run_test_image(cfg: dict, image_path: str, crop: bool = True):
    import cv2
    from ocr import OCREngine

    img = cv2.imread(image_path)
    if img is None:
        raise SystemExit(f"Could not read image: {image_path}")
    region_img, how = _test_image_region(cfg, img, crop=crop)
    print(how)
    # Save exactly what gets read, so "what is WITNESS looking at?" has an
    # answer you can open.
    region_png = os.path.splitext(image_path)[0] + "_region.png"
    try:
        if cv2.imwrite(region_png, region_img):
            print(f"Saved what WITNESS reads: {region_png}")
        else:
            region_png = ""
    except Exception:
        region_png = ""
    engine = OCREngine(cfg.get("ocr_engine", "easyocr"), cfg.get("ocr_upscale", 3), max_dim=cfg.get("ocr_max_dim", 800))
    print(f"OCR ({engine.engine_name}) on {image_path} ...")
    lines = engine.read_lines(region_img)
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
        shown = os.path.basename(region_png) if region_png else "<name>_region.png"
        print(f"  If this frame SHOULD be a kill: open {shown}. If the popup "
              "isn't inside it, run 1 - Calibrate (detect_region_frac wins over "
              "detect_region). If it is, add the exact phrase to "
              "popup_trigger_phrases or lower popup_match_threshold.")


# --- live loop ---------------------------------------------------------------

def _render_flags():
    """Creation flags for background ffmpeg work: no console window AND
    below-normal priority so encodes/remuxes yield CPU to the game rather than
    competing with it mid-match."""
    import subprocess
    import sys
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if sys.platform == "win32":
        flags |= 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
    return flags


def _rss_mb() -> float:
    """This process's working-set (RAM) in MB, dependency-free."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            kernel32 = ctypes.windll.kernel32
            # HANDLE is pointer-sized: without c_void_p restype the 64-bit
            # pseudo-handle gets truncated to a 32-bit int and the call fails
            # (that's why RAM logged as 0).
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            # K32GetProcessMemoryInfo (kernel32) is the reliable Win10+ export;
            # fall back to psapi.GetProcessMemoryInfo on older builds.
            fn = None
            for dll_name in ("kernel32", "psapi"):
                dll = getattr(ctypes.windll, dll_name, None)
                if dll is None:
                    continue
                fn = (getattr(dll, "K32GetProcessMemoryInfo", None)
                      or getattr(dll, "GetProcessMemoryInfo", None))
                if fn is not None:
                    break
            if fn is not None:
                fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wintypes.DWORD]
                fn.restype = wintypes.BOOL
                c = PMC(); c.cb = ctypes.sizeof(PMC)
                if fn(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                    return c.WorkingSetSize / (1024 * 1024)
        except Exception:
            pass
        return 0.0
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024
    except Exception:
        return 0.0


def _gpu_mb() -> float:
    """CUDA memory reserved by torch, or 0 if not on GPU."""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.memory_reserved() / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def _start_perf_monitor(cfg, perf, mon_stop, get_kills, web=None):
    """Background sampler: every few seconds log RAM, GPU memory, average OCR
    loop work time and effective fps to logs/perf_<time>.csv (and a compact
    line to the session log). Answers 'is memory creeping / is OCR keeping up
    over a long session' without a profiler. Off with perf_log: false."""
    if not cfg.get("perf_log", True):
        return
    every = max(2, int(cfg.get("perf_interval_seconds", 5)))
    # Blind periodic release is off (it just makes torch re-allocate), but a
    # CEILING is not optional: torch's caching allocator never shrinks, so one
    # burst of large one-off reads keeps its peak reserved for the whole session.
    # Measured 11-12 GB reserved on a 16 GB card, which exhausted VRAM alongside
    # the game/OBS/Discord and made the driver spill to system RAM — that is what
    # "awful frames" actually was. Over the ceiling, hand the cache back.
    release_gpu = bool(cfg.get("perf_release_gpu", False))
    vram_ceiling = float(cfg.get("vram_ceiling_mb", 2500))

    def run():
        import csv
        base = os.path.dirname(os.path.abspath(__file__))
        logdir = os.path.join(base, "logs")
        try:
            os.makedirs(logdir, exist_ok=True)
            path = os.path.join(logdir, f"perf_{time.strftime('%Y-%m-%d_%H-%M-%S')}.csv")
            f = open(path, "w", newline="", encoding="utf-8")
        except Exception:
            return
        w = csv.writer(f)
        w.writerow(["t_sec", "rss_mb", "gpu_mb", "avg_ocr_ms", "eff_fps", "kills", "threads"])
        t0 = time.monotonic()
        base_rss = None
        last_gpu_release = t0
        while not mon_stop.is_set():
            mon_stop.wait(every)
            if mon_stop.is_set():
                break
            iters = perf["iters"]; work = perf["work_sum"]
            perf["iters"] = 0; perf["work_sum"] = 0.0
            avg_ms = (work / iters) if iters else 0.0
            fps = iters / every
            rss = _rss_mb(); gpu = _gpu_mb()
            if base_rss is None:
                base_rss = rss
            t = int(time.monotonic() - t0)
            w.writerow([t, round(rss, 1), round(gpu, 1), round(avg_ms, 1),
                        round(fps, 2), get_kills(), threading.active_count()])
            f.flush()
            # push live detection health to the dashboard's DETECTION panel
            if web is not None:
                try:
                    web.set_detect({"fps": round(fps, 1), "avg_ms": round(avg_ms),
                                    "device": "GPU" if gpu > 0 else "CPU",
                                    "vram_mb": round(gpu),
                                    "engine": cfg.get("ocr_engine", "easyocr")})
                except Exception:
                    pass
            # compact heartbeat in the session log (once per ~30s to stay quiet)
            if t % 30 < every:
                grew = f" (+{rss - base_rss:.0f}MB)" if rss - base_rss > 50 else ""
                print(f"  [perf] RAM {rss/1024:.1f}GB{grew} · OCR {avg_ms:.0f}ms · {fps:.1f} fps")
            # Ceiling: reserved VRAM this high starves the game.
            if vram_ceiling and gpu > vram_ceiling:
                try:
                    import ocr as _ocr
                    freed = _ocr.release_vram()
                    if freed > 50:
                        print(f"  [perf] VRAM was {gpu:.0f}MB (over the "
                              f"{vram_ceiling:.0f}MB ceiling) — released "
                              f"{freed:.0f}MB back to the driver")
                except Exception:
                    pass
            # optional blind periodic release (off by default)
            if release_gpu and time.monotonic() - last_gpu_release > 60:
                last_gpu_release = time.monotonic()
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
        try:
            f.close()
            print(f"  [perf] log saved: {path}")
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


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


def _on_windows() -> bool:
    return sys.platform == "win32"


# The value config.yaml used to ship. It was never a real password: treat it as
# "not set", and say so when OBS turns the connection down.
OBS_PASSWORD_PLACEHOLDER = "CHANGE_ME"

# Every OBS setup error starts with "OBS": the desktop launcher shows a message
# with that prefix as-is in its START error banner.
OBS_WS_OFF = ("OBS is open but its WebSocket server is off: Tools > WebSocket "
              "Server Settings > Enable WebSocket server (port {port})")
OBS_WS_OLD = (" - port 4444 answers, which looks like OBS 27 (websocket 4.x); "
              "WITNESS needs OBS 28 or newer")
OBS_NOT_FOUND = ("OBS is not running and obs64.exe was not found: start OBS "
                 "yourself or set obs.exe_path in config.yaml")
OBS_BAD_PASSWORD = ("OBS rejected the WebSocket password: copy it from OBS Tools > "
                    "WebSocket Server Settings > Show Connect Info into "
                    "obs.password in config.yaml")
OBS_REFUSED = ("OBS: cannot reach the WebSocket server - Tools > WebSocket "
               "Server Settings > Enable")
# Seconds an already-running OBS gets to open its WebSocket port before
# WITNESS says the server is off.
OBS_BOOT_WAIT = 10


def _obs_password(obs_cfg) -> str:
    """obs.password from config, with the old CHANGE_ME placeholder as ''."""
    pw = (obs_cfg or {}).get("password", "")
    pw = "" if pw is None else str(pw)
    return "" if pw.strip() == OBS_PASSWORD_PLACEHOLDER else pw


def _obs_process_running() -> bool:
    """Is obs64.exe running? (Windows: tasklist.) False when it can't tell.

    Matches the image name only: tasklist's 'INFO: No tasks are running...'
    text is localized. CREATE_NO_WINDOW, or a console flashes under pythonw."""
    try:
        import subprocess
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/NH"],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = r.stdout or b""
        if isinstance(out, bytes):
            out = out.decode("ascii", "replace")   # OEM code page: names are ASCII
        return "obs64.exe" in out.lower()
    except Exception:
        return False


def _ensure_obs_running(cfg) -> str:
    """If OBS's websocket isn't reachable, launch OBS ourselves and wait for it
    (Windows). Kills the classic 'connection refused because OBS wasn't open'
    startup failure. Controlled by obs.auto_launch (default true).

    Returns 'up' (the WebSocket port answers), 'launched' (we started OBS),
    'not_found' (OBS isn't running and obs64.exe wasn't found) or 'disabled'
    (auto_launch off, or not Windows). Raises RuntimeError when OBS is already
    running with its WebSocket server off: launching it again only produced
    OBS's 'already running' dialog and a 30-second wait."""
    obs_cfg = cfg.get("obs", {}) or {}
    host = obs_cfg.get("host", "localhost")
    port = obs_cfg.get("port", 4455)
    if _obs_port_open(host, port):
        return "up"
    if not obs_cfg.get("auto_launch", True) or not _on_windows():
        return "disabled"

    if _obs_process_running():
        # OBS may simply still be booting (opened a moment before START): its
        # port opens a few seconds after the process appears. Give it that.
        for _ in range(int(OBS_BOOT_WAIT)):
            time.sleep(1)
            if _obs_port_open(host, port):
                return "up"
        msg = OBS_WS_OFF.format(port=port)
        if int(port) != 4444 and _obs_port_open(host, 4444):
            msg += OBS_WS_OLD
        raise RuntimeError(msg)

    exe = (obs_cfg.get("exe_path") or "").strip()
    if not exe:
        for cand in (r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
                     r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe"):
            if os.path.exists(cand):
                exe = cand
                break
    if not exe or not os.path.exists(exe):
        return "not_found"

    print("OBS isn't running — launching it for you...")
    try:
        # OBS must be started FROM its own bin directory or it errors out.
        import subprocess
        subprocess.Popen([exe, "--startreplaybuffer", "--minimize-to-tray",
                          "--disable-shutdown-check"],
                         cwd=os.path.dirname(exe))
    except Exception as e:
        raise RuntimeError(f"OBS could not be started ({e}): start OBS yourself "
                           "or set obs.exe_path in config.yaml")
    # Wait for the websocket to come up (OBS cold start takes a few seconds).
    for _ in range(30):
        time.sleep(1)
        if _obs_port_open(host, port):
            print("OBS is up.")
            return "launched"
    print("OBS was launched but its WebSocket isn't answering yet — check "
          "Tools -> WebSocket Server Settings is enabled.")
    return "launched"


def _obs_error_kind(e) -> str:
    """'auth' (OBS turned the password down), 'refused' (nothing listening on
    the port) or 'other' (timeouts, code 207 'not ready', anything else)."""
    text = str(e).lower()
    # obsws-python: "failed to identify client with the server ..." (wrong
    # password: OBS closes the socket, code 4009 is dropped) and
    # "authentication enabled but no password provided".
    if "identify" in text or "no password" in text:
        return "auth"
    if (isinstance(e, ConnectionRefusedError)
            or getattr(e, "winerror", None) == 10061
            or "10061" in text or "connection refused" in text):
        return "refused"
    return "other"


OBS_CONNECT_ATTEMPTS = 20
OBS_CONNECT_WAIT = 1.5


def _connect_obs(obs, status: str, raw_password="") -> None:
    """obs.connect(), retried while OBS boots, with a clear 'OBS ...' error.

    A freshly launched OBS opens its port before it can answer ('code 207: not
    ready'), so timeouts and not-ready keep retrying (20 x 1.5 s). A rejected
    password stops after 3 tries: obsws-python reports a socket closed during
    the handshake with the same 'failed to identify' text, so one failure is
    not proof. While OBS is still starting (we launched it) it gets 6."""
    auth_limit = 6 if status == "launched" else 3
    auth_fails = 0
    for attempt in range(OBS_CONNECT_ATTEMPTS):
        try:
            obs.connect()
            return
        except Exception as e:
            kind = _obs_error_kind(e)
            if kind == "auth":
                auth_fails += 1
                if auth_fails >= auth_limit:
                    msg = OBS_BAD_PASSWORD
                    if str(raw_password).strip() == OBS_PASSWORD_PLACEHOLDER:
                        msg += " (it is still CHANGE_ME)"
                    raise RuntimeError(msg) from e
            if attempt >= OBS_CONNECT_ATTEMPTS - 1:
                if kind == "refused":
                    raise RuntimeError(OBS_REFUSED) from e
                raise RuntimeError(f"OBS did not answer: {type(e).__name__}: "
                                   f"{e}") from e
            if attempt == 0:
                if status == "launched":
                    print("OBS is still starting up — waiting for it...")
                else:
                    print(f"Can't reach OBS yet ({type(e).__name__}: {e}) - "
                          "retrying for about 30 s...")
            time.sleep(OBS_CONNECT_WAIT)


def _setup_session(cfg, dry_run):
    """Session setup. Returns a dict
    of session state (obs, web, counters, etc.)."""
    from obs_client import OBSClient, DryRunOBS

    obs_cfg = cfg.get("obs", {}) or {}
    status = "dry-run"
    if dry_run:
        obs = DryRunOBS()
    else:
        status = _ensure_obs_running(cfg)
        if status == "not_found":
            raise RuntimeError(OBS_NOT_FOUND)
        obs = OBSClient(
            host=obs_cfg.get("host", "localhost"),
            port=obs_cfg.get("port", 4455),
            password=_obs_password(obs_cfg),
            counter_source=obs_cfg.get("counter_source", "KillCounter"),
            counter_format=obs_cfg.get("counter_format", "Kills: {count}"),
            auto_start_replay_buffer=obs_cfg.get("auto_start_replay_buffer", True),
        )
    _connect_obs(obs, status, raw_password=obs_cfg.get("password", ""))
    obs.set_counter(0)
    # Replay Buffer disabled in OBS settings = kills count, NO clip saves, and
    # nothing said so beyond one console line from obs_client.
    rb_ok = _replay_buffer_ok(obs)
    if not rb_ok:
        print("WARNING: OBS's Replay Buffer is not running - kills will count but "
              "clips will NOT save. In OBS: Settings > Output > Replay Buffer > "
              "Enable Replay Buffer, then Start Replay Buffer.")

    web = None
    black_checked_at = None
    if cfg.get("web_dashboard", True):
        try:
            import webserver
            global _web_state, _web_server, _advertiser
            base = os.path.dirname(os.path.abspath(__file__))
            port = cfg.get("web_port", 8000)
            if _web_server is None:
                _web_state = webserver.LiveState()
                # web_lan: true serves to your Wi-Fi (needed for the iPad).
                # false = this PC only (most locked-down).
                host = "0.0.0.0" if cfg.get("web_lan", True) else "127.0.0.1"
                _web_server = webserver.start_web(_web_state, port, base, host=host)
                # Let the iOS app find this PC by itself. Only when the dashboard
                # is actually reachable from the LAN — advertising a server bound
                # to 127.0.0.1 would send the iPad to a dead address.
                if (_advertiser is None and cfg.get("web_lan", True)
                        and cfg.get("bonjour_discovery", True)):
                    try:
                        import discovery
                        _advertiser = discovery.advertise(
                            port, webserver.local_ip(),
                            version=getattr(_web_state, "version", ""),
                            api=webserver.API_VERSION)
                    except Exception as e:
                        print(f"  [discovery] skipped: {e}")
            web = _web_state
            web.reset()
            web.bind_config(cfg, save_setting_overrides)
            if not rb_ok:
                web.notice("Replay Buffer OFF in OBS - clips will NOT save", "alert")
            try:
                rd = obs.get_record_directory() or ""
                web.record_dir = rd
                if rd:            # remember it so the archive works on next open
                    cache_record_dir(rd)
                    gb = free_gb(rd)
                    if gb is not None:
                        print(f"Clip drive: {gb:.1f} GB free")
            except Exception:
                pass
            web.set_running(True)
            # Preflight: catch a black capture BEFORE he plays for three hours.
            check_obs_not_black(cfg, obs, web=web, label="startup")
            black_checked_at = time.monotonic()
            print(f"Live view: http://{webserver.local_ip()}:{port}  "
                  f"(open in your iPad/phone browser on the same Wi-Fi)")
        except Exception as e:
            print(f"(web dashboard off: {e})")

    s = {
        "obs": obs,
        "web": web,
        # OBS's output folder — where clips land (used for the disk-space guard).
        # cached_record_dir() covers the case where the web dashboard is off.
        "record_dir": (getattr(web, "record_dir", "") or cached_record_dir()),
        "count": 0,
        "count_base": 0,     # headline at the last exfil (per-match floor)
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
    if black_checked_at is not None:
        # The startup check just ran: the periodic one waits its full period
        # instead of firing a second alarm on the first frame.
        s["_black_check_at"] = black_checked_at
    if cfg.get("heat_streaks", True):
        try:
            import heat
            s["heat"] = heat.HeatTracker(
                precision_at=cfg.get("heat_precision_streak", 3))
        except Exception:
            s["heat"] = None
    if cfg.get("detection_canary", True):
        try:
            import canary
            s["canary"] = canary.DetectionCanary()
        except Exception:
            s["canary"] = None
    # ffmpeg preflight. Without it clips still save, but every kill printed a
    # cryptic '[replay] error: [WinError 2]' while replays, reels, Shorts and
    # voiced call-outs silently never appeared.
    if not _ffmpeg_available(os.path.dirname(os.path.abspath(__file__)), cfg):
        s["no_ffmpeg"] = True
        print("ffmpeg not found: clips still save, but instant replays, reels, "
              "Shorts and voiced call-outs are OFF. Put ffmpeg.exe in this "
              "folder or set ffmpeg_path in config.yaml.")
        if web is not None:
            try:
                web.notice("ffmpeg missing: no replays/reels/voice")
            except Exception:
                pass
    if cfg.get("announcer_medals", True) and not dry_run:
        _prepare_medals_async(cfg, s)
    return s


def _ffmpeg_available(base, cfg) -> bool:
    """Does the ffmpeg that montage.find_ffmpeg picks actually exist?

    find_ffmpeg falls back to the bare name 'ffmpeg' (PATH), so that one is
    checked with shutil.which, never os.path.exists: a PATH install is not a
    file in the current folder, and testing it that way would switch off
    replays for everyone who has ffmpeg on PATH. True when it can't tell."""
    try:
        import montage
        ff = montage.find_ffmpeg(base, cfg)
    except Exception:
        return True
    return bool(ff) and (os.path.isfile(ff) or shutil.which(ff) is not None)


def _replay_buffer_ok(obs) -> bool:
    """True only when OBS says its Replay Buffer is running. An unreadable
    status (None) is a disabled buffer in practice: every status request fails
    when it's switched off in Settings > Output. A client without the request
    at all (a test fake) is assumed fine rather than cried wolf about."""
    fn = getattr(obs, "replay_buffer_active", None)
    if not callable(fn):
        return True
    try:
        return fn() is True
    except Exception:
        return False


CLIP_FAIL_NOTICE_EVERY = 60.0


def _clip_not_saved(s, what: str) -> None:
    """OBS refused a replay save. Say so on the console every time and on the
    dashboard at most once a minute, so a buffer that stays off can't flood
    the feed (and a kill that counted without a clip isn't silent)."""
    print(f"  [clip] {what}: OBS did not save a replay - is the Replay Buffer "
          "running? (OBS: Settings > Output > Replay Buffer)")
    now = time.monotonic()
    if now - s.get("_clip_fail_notice_at", -1e9) < CLIP_FAIL_NOTICE_EVERY:
        return
    s["_clip_fail_notice_at"] = now
    if s.get("web") is not None:
        try:
            s["web"].notice("CLIP NOT SAVED - check OBS Replay Buffer", "alert")
        except Exception:
            pass


def _prepare_medals_async(cfg, s):
    """Render the medal call-outs in the background so the first DOUBLE KILL
    plays instantly (cached per voice; offline after the first render)."""
    if s.get("no_ffmpeg"):
        return            # every render needs ffmpeg; the preflight said so
    def work():
        try:
            import announcer
            import montage
            base = os.path.dirname(os.path.abspath(__file__))
            s["medal_sounds"] = announcer.ensure_medal_sounds(
                base, cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                montage.find_ffmpeg(base, cfg),
                pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH),
                eleven_voice=cfg.get("elevenlabs_voice_id", ""))
        except Exception as e:
            print(f"  [medals] prep failed: {e}")
    threading.Thread(target=work, daemon=True).start()


def _clip_ready_callback(s, tag, count, kills=1, kill_epochs=None,
                         saved_epoch=0.0, victim=""):
    """Callback for rename_clip_async: collect the clip (with its kill count,
    for Play of the Game), record WHEN its kills happened (sidecar, for tight
    reel cuts), and put it on the iPad as an instant replay."""
    def on_done(dest):
        if saved_epoch and kill_epochs:
            try:
                import match_reel
                match_reel.write_kill_sidecar(dest, saved_epoch, kill_epochs,
                                              victim=victim)
            except Exception:
                pass
        s["match_clips"].append({"path": dest, "kills": kills, "tag": tag})
        _register_replay_async(s, dest, tag, count)
        # Manual saves already flashed a prominent chip on the button press —
        # don't double up. Auto kill-clips get the quiet corner blip.
        if tag != "manual" and s["cfg"].get("overlay_clip_saved", True):
            show_text_overlay(s["cfg"], "CLIP SAVED", size=26,
                              position="bottom-right", color="#aab4bd",
                              duration_ms=1100, rise=False)
    return on_done


def _register_replay_async(s, clip_path, tag, count):
    """Remux the organized .mkv to .mp4 (stream copy, ~instant) so Safari can
    play it, and add it to the dashboard's Instant Replays list."""
    if s["web"] is None or s.get("no_ffmpeg"):
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
                creationflags=_render_flags())
            if r.returncode == 0 and os.path.exists(mp4):
                s["web"].add_replay(tag.replace("+", " + ").upper(), mp4)
                print(f"  [replay] kill #{count} ready on the iPad")
            else:
                tail = (r.stderr.strip().splitlines() or ["(no output)"])[-1]
                print(f"  [replay] remux failed: {tail}")
        except Exception as e:
            print(f"  [replay] error: {e}")
    threading.Thread(target=work, daemon=True).start()


# Popups that MODIFY another event rather than being an event of their own.
# Marathon prints several popups for ONE enemy: downing a runner with a headshot
# shows "RUNNER DOWNED +15 XP" *and* "PRECISION DOWNED +25". Counting the
# modifier as its own kill inflated every headshot into a double — which also
# tripped false DOUBLE KILL overlays and ran the heat ladder up twice as fast.
MODIFIER_TAGS = ("precision",)
# Tags that mean "an enemy died" (your elim / your finisher).
ELIM_TAGS = ("kill", "elim", "finisher")
# Never a kill of yours: an assist is someone else's, a manual SAVE CLIP press
# is just a kept moment.
NON_KILL_TAGS = ("assist", "manual") + MODIFIER_TAGS


def check_obs_not_black(cfg, obs, web=None, state=None, label="") -> bool:
    """Look through OBS's eyes and shout if it's recording nothing.

    Stan lost a full night to this: OBS's Display Capture had lost its display, so
    every clip was black. WITNESS couldn't tell, because it reads the SCREEN
    directly — its own frames were perfect the whole time. Asking OBS for a
    screenshot of its program scene is the only check that answers the question
    that actually matters.

    Returns True when the capture looks fine (or when the check couldn't run — an
    inconclusive check must never be reported as a broken capture)."""
    if not cfg.get("check_black_capture", True):
        return True
    try:
        import blackcheck
        res = blackcheck.check_obs_capture(obs)
    except Exception as e:
        print(f"  [capture] check skipped: {e}")
        return True
    if not res.get("ok"):
        if res.get("reason"):
            print(f"  [capture] check skipped: {res['reason']}")
        return True
    if not res.get("blank"):
        if label:
            print(f"  [capture] OBS is seeing picture "
                  f"(brightness {res['mean']:.0f}, variation {res['stddev']:.0f})")
        if web is not None:
            try:
                web.clear_alert("black_capture")   # fixed — stop alerting the iPad
            except Exception:
                pass
        return True
    # WHY is it black? The scene's source types tell a broken Display Capture
    # from an empty scene and from a Game Capture that simply hasn't hooked the
    # game yet (START pressed before launching Marathon).
    scene = _black_scene(res, obs)
    if scene == "hook_only" and label == "startup":
        # Expected until the game runs: a soft note, no overlay, no voice, no
        # alert. The periodic re-check (black_check_minutes) stays loud.
        print("  [capture] OBS's picture is black, but your scene captures with "
              "Game/Window Capture, which stays black until Marathon is running. "
              "WITNESS checks again in a few minutes.")
        return False
    print(blackcheck.warning_text(res))
    no_source = scene == "no_capture"
    if no_source:
        print(f"  {OBS_NO_SOURCE}")
    # A first-class alert for the iOS app (and any future client), raised before
    # the clutch check below so the iPad hears about it even when the on-screen
    # overlay is being held back.
    if web is not None:
        try:
            web.set_alert("black_capture", "critical",
                          "OBS is recording a black screen",
                          OBS_NO_SOURCE + "." if no_source else
                          "Your clips will be empty. In OBS, open your Display "
                          "Capture source and re-pick the display.")
        except Exception:
            pass
        # ...and a line in the dashboard feed, which the desktop window and
        # the iPad browser both show. (This used to go into the recap state as
        # status 'error', which the Live page never draws, and which clobbered
        # a resume build's 'building' status.)
        try:
            web.notice("OBS scene has no capture source - add one" if no_source
                       else "OBS IS BLACK - re-pick the display in OBS", "alert")
        except Exception:
            pass
    # SWEAT MODE. I first held this back during a clutch, reasoning he couldn't
    # act on it mid-fight. Stan overruled that: "if im using sweat mode i would
    # like to see the alert though" — and he's right, a clutch is exactly the
    # footage worth not losing. Showing it is now the DEFAULT.
    #
    # The deferral is kept behind black_overlay_in_clutch: false for anyone who
    # wants the quiet version; it releases at every clutch exit.
    if (state is not None and state.get("clutch")
            and not cfg.get("black_overlay_in_clutch", True)):
        state["black_pending"] = True
        print("  [capture] holding the on-screen warning — you're clutching")
        return False
    if cfg.get("black_overlay", True):
        try:
            show_text_overlay(
                cfg, "OBS IS BLACK - NOT RECORDING",
                size=int(cfg.get("black_overlay_size", 54)),
                position=str(cfg.get("black_overlay_position",
                                     "custom:0.5,0.12")),
                color=_theme_color(cfg, "danger", "#ff4d3d"),
                duration_ms=int(cfg.get("black_overlay_ms", 6000)),
                rise=False, critical=True)
        except Exception as e:
            print(f"  [capture] overlay failed: {e}")
    # And say it out loud, since he may be looking down the sights, not at the
    # top of the screen.
    if cfg.get("black_callout", True) and cfg.get("announcer_medals", True):
        try:
            import threading

            import announcer
            import montage
            base = os.path.dirname(os.path.abspath(__file__))

            def speak():
                try:
                    wav = announcer.ensure_callout(
                        base, "Warning. O B S is not recording.",
                        cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                        montage.find_ffmpeg(base, cfg),
                        pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH),
                        eleven_voice=cfg.get("elevenlabs_voice_id", ""))
                    if wav:
                        announcer.play_medal({"blk": wav}, "blk")
                except Exception:
                    pass
            threading.Thread(target=speak, daemon=True).start()
        except Exception:
            pass
    if state is not None:
        state["black_warned"] = time.time()
    return False


OBS_NO_SOURCE = ("OBS scene has no capture source - add Display Capture or Game "
                 "Capture (Sources > +)")
_HOOK_KINDS = ("game_capture", "window_capture")


def _black_scene(res, obs) -> str:
    """Why OBS's picture is black, from the program scene's source types:
      'no_capture'  nothing in the scene captures anything
      'hook_only'   only Game/Window Capture: black until the game runs
      'other'       Display Capture etc.: today's warning
      'unknown'     couldn't ask (old OBS client, test fakes): today's warning
    blackcheck works this out itself when it can (res['scene']); otherwise
    OBSClient.get_scene_input_kinds is asked here, if the client has it."""
    scene = res.get("scene")
    if scene in ("no_capture", "hook_only", "other", "unknown"):
        return scene
    if "scene_kinds" in res:
        kinds = res["scene_kinds"]
    else:
        try:
            kinds = getattr(obs, "get_scene_input_kinds", lambda: None)()
        except Exception:
            kinds = None
    if kinds is None:
        return "unknown"
    try:
        kinds = [str(k) for k in kinds]
    except TypeError:
        return "unknown"
    if not kinds:
        return "no_capture"
    if all(k in _HOOK_KINDS for k in kinds):
        return "hook_only"
    return "other"


def _kill_counts(tags) -> tuple:
    """(downs, elims) for an ORDERED batch of popups — deliberately the SAME
    two numbers the game's own exfil panel reports, so they can be audited
    against it. It is exfil_stats.count_events; see that for the evidence.

    downs == 'down' and 'manual_kill' events (a +1 press is a down the OCR
    missed). PRECISION is a modifier on its down, not a down.
    elims == 'kill'/'elim' events, plus any FINISHER that does NOT follow a
    pending elim. A FINISHER right after an elim is a modifier on that elim
    (the same runner, melee-finished), not a second elim, so order matters.
    Assists count for nothing.

    Do not "fix" a bracket-dropped '[ASSIST' read into an own kill here or in
    classify_event: those reads are real assists (07-27 m5 rules out the
    general rule), and the narrow 'bracket-dropped ELIM = own' variant only
    fit 07-27 m6 because it was built from that one case."""
    import exfil_stats
    return exfil_stats.count_events(tags)


def _kill_count(tags) -> int:
    """Distinct enemies a batch of popups represents. An elim landing on one of
    YOUR downs in the same window is that runner being finished — the same kill,
    not a new one — so only unattributed elims add on top of the downs."""
    downs, elims = _kill_counts(tags)
    return downs + max(0, elims - downs)


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
    # A multikill means multiple DISTINCT downs. A down followed by the finisher
    # on the same runner is ONE kill, and a PRECISION popup is a modifier on a
    # down (same runner) — counting it here fired DOUBLE KILL on every headshot.
    n_downs = sum(1 for t in tags if t == "down")
    clip_kills = _kill_count(tags)   # assists excluded; own-finisher not doubled
    if n_downs >= 2 and not s.get("clutch"):
        if s["cfg"].get("overlay_multikill", True):
            show_text_overlay(s["cfg"],
                              MULTIKILL_NAMES.get(n_downs, "MULTI KILL"),
                              size=64, position="custom:0.5,0.40", duration_ms=1700,
                              color=_theme_color(s["cfg"], "accent", "#9c58da"))
        if s["cfg"].get("announcer_medals", True):
            import announcer
            announcer.play_medal(s["medal_sounds"], n_downs)
    if s["obs"].save_replay():
        s["last_save"] = time.monotonic()
        saved_epoch = time.time()   # the buffer ends ~here; kills sit earlier
        if s["organize"]:
            rename_clip_async(s["obs"], s["session_id"], combo_tag, counts[0],
                              on_done=_clip_ready_callback(
                                  s, combo_tag, counts[0], kills=clip_kills,
                                  kill_epochs=[{"epoch": p.get("epoch", 0),
                                                "manual": p.get("manual", False)}
                                               for p in pending],
                                  saved_epoch=saved_epoch,
                                  # only for clips that contain a real down —
                                  # an assist-only clip's victim isn't yours
                                  victim=(s.pop("_last_victim", "")
                                          if any(p["tag"] == "down"
                                                 for p in pending) else "")))
    else:
        _clip_not_saved(s, f"kill(s) #{label}")
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
    tag = classify_event(ev.raw_line)
    # A finisher on YOUR OWN down is the same kill, not a new one — the clip
    # coalescer already knows this; the headline counter now agrees. (A
    # standalone finisher — finishing a teammate's down — still counts.)
    # An elim that lands shortly after one of YOUR downs is that runner being
    # finished — the same kill, not a new one.
    #
    # This used to look ONLY inside the current coalesce window, which flushes on
    # a timer. Stan's 2026-08-08 sequence ("i downed, killed, then downed another
    # and killed" = 2 enemies) had the second elim arrive after its down's window
    # had already flushed, so it counted as a third kill. The window is a
    # CLIPPING concept and must not decide what a kill is — track the down time
    # directly instead.
    now_epoch = time.time()
    recent_down = (s.get("_last_down_epoch", 0)
                   and now_epoch - s["_last_down_epoch"] <= float(
                       cfg.get("own_finisher_seconds", 25.0))
                   and not s.get("_last_down_finished"))
    own_finisher = tag in ELIM_TAGS and (
        any(p["tag"] == "down" for p in s.get("_coalesce_pending", []))
        or recent_down)
    if tag == "down":
        s["_last_down_epoch"] = now_epoch
        s["_last_down_finished"] = False        # this runner isn't finished yet
    elif own_finisher:
        s["_last_down_finished"] = True         # don't fold a second elim onto it
    # An assist is someone ELSE's kill: Stan's rule is "for those I just want the
    # recordings — they shouldn't count toward anything else". So an assist gets
    # a clip and NOTHING else: no counter, no sound, no gamertag scan, no
    # team-wipe vote, no dashboard row, no heat. PRECISION is a modifier on a
    # down (same runner), so it must not bump the counter either. An
    # own-finisher is the same kill as the down it lands on.
    is_assist = (tag == "assist")
    counts_as_kill = tag not in NON_KILL_TAGS and not own_finisher
    if counts_as_kill:
        s["count"] += 1
    # The two numbers the game's exfil panel reports, tracked separately so they
    # can be audited against it (precision excluded — it is a modifier).
    if not is_assist:
        if tag in ("down", "manual_kill"):
            s["downs"] = s.get("downs", 0) + 1
        elif tag in ELIM_TAGS:
            s["elims"] = s.get("elims", 0) + 1
    count = s["count"]
    s["session_tags"].append(tag)
    s.setdefault("match_tags", []).append(tag)  # reset each exfil for the audit
    print((f"KILL #{count}" if counts_as_kill else f"(not a kill: {tag})")
          + f" [{tag}]: {ev.raw_line!r}")
    if is_assist:
        # Clip only. Fall straight through to the coalescer.
        _queue_coalesce(cfg, s, tag, count, now)
        return
    if tag == "precision":
        # Modifier on the down we just counted (same runner): no kill, but it
        # still feeds the SHARPSHOOTER streak, and it still earns the skull
        # overlay + a clip.
        if s.get("heat") is not None and cfg.get("heat_streaks", True):
            try:
                for i, hev in enumerate(s["heat"].mark_precision()):
                    print(f"  [heat] {hev.label} (streak {hev.streak})")
                    _fire_heat(cfg, s, hev, delay=i * 2.3)
            except Exception as e:
                print(f"  [heat] error: {e}")
        if should_overlay(cfg, ev.raw_line) and not s.get("clutch"):
            print("  -> HEADSHOT (skull popup)")
            show_overlay(cfg)
        _queue_coalesce(cfg, s, tag, count, now, raw_line=ev.raw_line)
        return
    if s.get("clutch") and counts_as_kill:
        s["clutch_kills"] = s.get("clutch_kills", 0) + 1
        print(f"  [clutch] solo kill #{s['clutch_kills']} — staying quiet")

    # Team wipe: an enemy DEATH shows an ELIM or FINISHER popup (yours or an
    # assist). Three distinct deaths in a couple of minutes = squad wiped —
    # in trios you're almost always fighting exactly one team.
    # A wipe means three DIFFERENT people went down. Counting elim popups
    # instead fired on Stan's 2026-08-08 "downed, killed, downed another, killed"
    # — two enemies, each producing a down AND an elim, which looked like three
    # deaths. An own-finisher is by definition the runner we already counted, so
    # it can never be a new one.
    # Decide by TAG, not raw substrings: a garbled 'RUNNER EL1M' is tagged an
    # elim but contains no 'elim', so it never voted. A manual +1 press
    # ('manual_kill') casts no vote, as before; assists and precision return
    # earlier.
    if (cfg.get("team_wipe", True) and not own_finisher
            and (tag == "down" or tag in ELIM_TAGS)):
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

    # Heat / killstreak: a real kill feeds the streak; fire any tier crossings
    # (HOT STREAK, RAMPAGE, MENACE, APEX), FIRST BLOOD, SHARPSHOOTER.
    if counts_as_kill and s.get("heat") is not None and cfg.get("heat_streaks", True):
        try:
            for i, hev in enumerate(s["heat"].on_kill(tag, clutch=bool(s.get("clutch")))):
                print(f"  [heat] {hev.label} (streak {hev.streak})")
                _fire_heat(cfg, s, hev, delay=i * 2.3)   # stagger stacked events
            if s["web"] is not None:                     # live streak chip
                label, color = s["heat"].peak()
                s["web"].set_heat({"streak": s["heat"].streak,
                                   "label": label, "color": color})
        except Exception as e:
            print(f"  [heat] error: {e}")

    _queue_coalesce(cfg, s, tag, count, now, raw_line=ev.raw_line)

    if should_overlay(cfg, ev.raw_line) and not s.get("clutch"):
        print("  -> HEADSHOT (skull popup)")
        show_overlay(cfg)


def _queue_coalesce(cfg, s, tag, count, now, raw_line: str = "") -> None:
    """Add an event to the clip-coalesce buffer and (re)arm its deadline.

    Split out of _handle_kill so the assist path can reach it directly: an
    assist earns a recording and nothing else."""
    coalesce_secs = cfg.get("kill_coalesce_seconds", 8.0)
    if "_coalesce_pending" not in s:
        s["_coalesce_pending"] = []
        s["_coalesce_deadline"] = 0.0
    s["_coalesce_pending"].append({
        "tag": tag, "count": count,
        "epoch": time.time(),                          # when the kill landed
        "manual": raw_line.startswith("MANUAL"),       # +1 presses lag the kill
    })
    s["_coalesce_deadline"] = now + coalesce_secs


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
        _fire_deferred_black(cfg, s)
    # No timeout exit: an ELIMINATED teammate stays on the panel for minutes,
    # and a timeout would exit + instantly re-trigger, wiping the solo-kill
    # tally. Clutch ends only on resolution: panel clears, you go down
    # (GIVE UP), you exfil, or the next match's deploy screen appears.


def _fire_deferred_black(cfg, s):
    """Show a black-capture warning that was held back during a clutch.

    Deferring rather than dropping: he still needs to know, just not while he's
    fighting for his life."""
    if not s.get("black_pending"):
        return
    s["black_pending"] = False
    print("  [capture] clutch over — showing the held warning")
    try:
        check_obs_not_black(cfg, s["obs"], web=s.get("web"), state=None)
    except Exception as e:
        print(f"  [capture] deferred warning failed: {e}")


def _end_clutch_quietly(s, reason, cfg=None):
    """You went down / the match ended some other way — no celebration."""
    if s.get("clutch"):
        s["clutch"] = False
        print(f"  [clutch] {reason}")
        if cfg is not None:
            _fire_deferred_black(cfg, s)


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
                      color=_theme_color(cfg, "accent", "#9c58da"),
                      duration_ms=2600)
    if cfg.get("announcer_medals", True) and not s.get("no_ffmpeg"):
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
                    pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH),
                    eleven_voice=cfg.get("elevenlabs_voice_id", ""))
                if wav:
                    announcer.play_medal({"co": wav}, "co")
            except Exception as e:
                print(f"  [clutch] call-out failed: {e}")
        threading.Thread(target=speak, daemon=True).start()


def _reel_cut_kwargs(cfg) -> dict:
    """Tight-cut settings for build_match_reel, from config."""
    return {"tight_cuts": bool(cfg.get("reel_tight_cuts", True)),
            "preroll": float(cfg.get("reel_preroll_seconds", 8.0)),
            "manual_preroll": float(cfg.get("reel_manual_preroll_seconds", 18.0)),
            "context_preroll": float(cfg.get("reel_context_preroll_seconds", 16.0)),
            "render_encoder": str(cfg.get("render_encoder", "auto"))}


def _reconcile_missed(match_tags, stats_d) -> int:
    """How many kills the game credited this match that detection missed —
    the exfil screen's 'Runners Downed' is ground truth. Conservative:
    requires a good panel read (3+ labels parsed) and sane values, and never
    subtracts (an OCR misread must not delete real kills)."""
    game_downs = (stats_d or {}).get("runners_downed")
    if game_downs is None or not (0 <= game_downs <= 30):
        return 0
    if len([k for k in (stats_d or {}) if k != "outcome"]) < 3:
        return 0                          # weak panel read — don't trust it
    # Compare LIKE WITH LIKE: the game's "Runners Downed" against our DOWN
    # events. This used to count down+precision+kill, so our side was inflated
    # past the game's number and `missed` was always negative — the function was
    # dead code that could never credit a genuinely missed kill. Shares
    # _kill_counts so it can't drift from the audit again.
    ours, _elims = _kill_counts(match_tags or [])
    missed = game_downs - ours
    return missed if 0 < missed <= 10 else 0


# A gap bigger than this is more likely a misread panel than missed popups. The
# largest real elim gap in the audited logs is 2, so 3 is the cap for elims and
# for the headline floor (a combatant row read as 'Eliminations', or a 2->7
# digit glitch, can add at most 3).
RECONCILE_ELIM_MAX = 3


def _panel_trusted(stats_d) -> bool:
    """The same guard as _reconcile_missed: a good panel read (3+ labels
    besides the outcome)."""
    return len([k for k in (stats_d or {}) if k != "outcome"]) >= 3


def _reconcile_missed_elims(match_tags, stats_d) -> int:
    """The same reconciliation for the game's 'Runner Eliminations'. Downs were
    reconciled but elims never were, so a missed elim left the session record
    below the game's own panel (07-16 m1, 07-23 m4, 07-24 m6, 07-27 m6).
    Same guards as the downs, a tighter gap cap, and it never subtracts."""
    game_elims = (stats_d or {}).get("runner_elims")
    if game_elims is None or not (0 <= game_elims <= 30):
        return 0
    if not _panel_trusted(stats_d):
        return 0                          # weak panel read — don't trust it
    _downs, ours = _kill_counts(match_tags or [])
    missed = game_elims - ours
    return missed if 0 < missed <= RECONCILE_ELIM_MAX else 0


def _headline_floor(stats_d, match_count) -> int:
    """How far this match's headline kill count sits below the game's own
    panel. Every runner you downed is a kill, and every runner you eliminated
    is a different dead runner, so the match's kills can never be fewer than
    max(Runners Downed, Runner Eliminations). `match_count` is the headline's
    growth since the previous exfil (s['count'] - s['count_base']). Never
    subtracts; at most RECONCILE_ELIM_MAX."""
    if not _panel_trusted(stats_d):
        return 0
    vals = [(stats_d or {}).get("runners_downed"), (stats_d or {}).get("runner_elims")]
    if any(v is not None and not (0 <= v <= 30) for v in vals):
        return 0
    floor = max([v for v in vals if v is not None] or [0])
    return min(RECONCILE_ELIM_MAX, max(0, floor - match_count))


def _fire_heat(cfg, s, ev, delay: float = 0.0):
    """Surface a heat/streak event: an on-screen toast + an optional voice
    call-out. Suppressed during a clutch (the clutch owns that moment).
    delay staggers multiple events from one kill (FIRST BLOOD + HEATING UP)
    so their toasts/call-outs don't land on top of each other."""
    if s.get("clutch"):
        return
    if delay > 0:
        threading.Timer(delay, _fire_heat, args=(cfg, s, ev)).start()
        return
    if s["web"] is not None:
        try:
            s["web"].notice(f"{ev.label} · streak {ev.streak}", "clutch")
        except Exception:
            pass
    if cfg.get("heat_overlays", True) and cfg.get("show_overlays", True):
        show_text_overlay(cfg, ev.label, size=68, position="custom:0.5,0.30",
                          color=ev.color, duration_ms=1900)
    if (cfg.get("heat_callouts", True) and cfg.get("announcer_medals", True)
            and ev.callout and not s.get("no_ffmpeg")):
        callout = ev.callout

        def speak():
            try:
                import announcer
                import montage
                base = os.path.dirname(os.path.abspath(__file__))
                wav = announcer.ensure_callout(
                    base, callout,
                    cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                    montage.find_ffmpeg(base, cfg),
                    pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH),
                    eleven_voice=cfg.get("elevenlabs_voice_id", ""))
                if wav:
                    announcer.play_medal({"co": wav}, "co")
            except Exception as e:
                print(f"  [heat] call-out failed: {e}")
        threading.Thread(target=speak, daemon=True).start()


NO_TAG_LINE = ("  [names] gamertag is blank in config.yaml - set it to your exact "
               "in-game name to enable the Menace Report, kill-name chyrons and "
               "streamer alerts")


def _gamertag(cfg) -> str:
    """Your gamertag by the KEY-ABSENT rule: encounters.DEFAULT_GAMERTAG only
    when config has no gamertag key at all (configs from before the key, Stan's
    among them, have always run on it); a key that is present but blank (or a
    bare `gamertag:`) means no tag. `cfg.get("gamertag") or DEFAULT` would turn
    a new install's deliberate blank back into someone else's name."""
    try:
        import encounters
        fn = getattr(encounters, "resolve_gamertag", None)
        if callable(fn):
            return str(fn(cfg) or "").strip()
        default = str(getattr(encounters, "DEFAULT_GAMERTAG", "") or "")
    except Exception:
        default = ""
    if "gamertag" not in (cfg or {}):
        return default
    return str(cfg.get("gamertag") or "").strip()


def _announcer_name(cfg) -> str:
    """How the announcer says your name. Absent and blank both mean none (the
    announcer then says 'your runner'): the code default was always ''."""
    return str((cfg or {}).get("announcer_player_name", "") or "").strip()


def _scan_feed_names(cfg, engine, s, expect):
    """One kill-feed OCR pass to read gamertags before the feed lines expire.
    expect: 'victim' right after your kill popup, 'killed_by' on the downed
    screen — only that direction is logged, so a stale line the other way
    can't misfile."""
    if not any(c.isalnum() for c in _gamertag(cfg)):
        # No tag, nothing to match the feed against: skip the full-frame grab
        # and OCR entirely, and say once per session how to turn it on.
        if not s.setdefault("_warned_no_tag", False):
            s["_warned_no_tag"] = True
            print(NO_TAG_LINE)
            if s.get("web") is not None:
                try:
                    s["web"].notice("Set gamertag in config.yaml for name tracking",
                                    "alert")
                except Exception:
                    pass
        return
    try:
        import encounters
        base = os.path.dirname(os.path.abspath(__file__))
        for direction, name in encounters.capture(cfg, engine):
            if direction != expect:
                continue
            # Never silently drop a name: if it reads as a map location, say so.
            # A silent filter would hide a wrong player_name just as well.
            if cfg.get("ignore_map_locations", True):
                where = encounters.location_hit(name)
                if not where and encounters.looks_like_item(name):
                    where = "a loot item"
                if where:
                    print(f"  [names] ignored {name!r} — that's a map location "
                          f"({where}), not a player")
                    continue
            if not encounters.should_log(s.setdefault("_enc_recent", {}),
                                         direction, name):
                continue
            encounters.log(base, s["session_id"], direction, name)
            verb = "you downed" if direction == "victim" else "downed by"
            print(f"  [names] {verb}: {name}")
            if direction == "victim":
                # Remember it for the clip's sidecar, so reels can put the name
                # on screen. This is the one fact a viewer can't get any other
                # way — "KILL #3" is bookkeeping the title card already covered.
                s["_last_victim"] = name
            if direction == "victim" and s["web"] is not None:
                try:   # stitch the victim's tag onto the kill row on the dashboard
                    s["web"].annotate_last_kill(name)
                except Exception:
                    pass
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
                          color=_theme_color(cfg, "accent", "#9c58da") if killed
                          else _theme_color(cfg, "danger", "#ff4d3d"),
                          duration_ms=2600)
    if s["web"] is not None:
        try:
            s["web"].notice(text, "streamer")
        except Exception:
            pass

    if cfg.get("announcer_medals", True) and not quiet and not s.get("no_ffmpeg"):
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
                    pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH),
                    eleven_voice=cfg.get("elevenlabs_voice_id", ""))
                if wav:
                    announcer.play_medal({"co": wav}, "co")
            except Exception as e:
                print(f"  [watchlist] call-out failed: {e}")
        threading.Thread(target=speak, daemon=True).start()

    # Your kills are already being clipped; a death normally isn't. This one is.
    if not killed and cfg.get("watchlist_clip_deaths", True):
        now = time.monotonic()
        saved = False
        if now - s["last_save"] >= s["min_save"]:
            saved = s["obs"].save_replay()
            if not saved:
                _clip_not_saved(s, f"downed by {watch}")
        if saved:
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
    _end_clutch_quietly(s, "you went down — no shame, it was 1vX.", cfg)
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
        _end_clutch_quietly(s, "new match starting — clutch state cleared.",
                            cfg)
        runner = runner_detect.capture_runner(cfg, engine)
        s["current_runner"] = runner
        if runner:
            print(f"  [runner] playing {runner}")
        else:
            print("  [runner] deploy screen seen, but no shell/runner name read")
    except Exception as e:
        s["runner_checked"] = True
        print(f"  [runner] detection error: {e}")


EXFIL_REARM_FRAMES = 15   # ~3s of clear frames re-arms the next capture


def _maybe_capture_exfil(cfg, engine, lines, s, now):
    """When the EXFILTRATED/ELIMINATED summary screen appears, grab it once and
    log the match stats, outcome, W/L row, and kill-count audit.

    EDGE-TRIGGERED on the screen appearing, re-armed once it's been gone for
    EXFIL_REARM_FRAMES. It used to be a flat 180s timeout, which silently
    skipped the whole end-of-match pass for any match that ended within three
    minutes of the previous one (an early death — Marathon run times of 3:25
    happen). A skipped pass lost that match's stats, outcome/W/L row, kill
    reconciliation, match reel, match_tags reset, and FIRST BLOOD re-arm."""
    try:
        import exfil_stats
        if not exfil_stats.looks_like_exfil(lines):
            s["_exfil_away"] = s.get("_exfil_away", 0) + 1
            if s["_exfil_away"] >= EXFIL_REARM_FRAMES:
                s["_exfil_seen"] = False      # screen gone — arm the next match
            return
        s["_exfil_away"] = 0
        if s.get("_exfil_seen"):
            return                            # same appearance, already captured
        s["_exfil_seen"] = True
        s["_last_exfil"] = now
        # Exfiling AS the last one standing is a clutch by definition.
        if s.get("clutch"):
            kills = s.get("clutch_kills", 0)
            s["clutch"] = False
            if kills >= 1:
                _clutch_celebrate(cfg, s, kills)
            else:
                print("  [clutch] exfiled solo — the quiet kind of clutch.")
            _fire_deferred_black(cfg, s)
        save_dir = ""
        try:
            rec = s["obs"].get_record_directory()
            if rec:
                save_dir = os.path.join(rec, "Marathon Sessions", s["session_id"])
        except Exception:
            pass
        stats_d, squad, outc = exfil_stats.capture_exfil_stats(cfg, engine, save_dir)
        try:   # that burst (outcome bands x retries + panels + name plates) is
               # the biggest allocator spike of a match — give the cache back
            import ocr as _ocr
            _ocr.release_vram()
        except Exception:
            pass
        # Did we EXTRACT or die? Only claim survival when the screen actually
        # says so — otherwise leave it unknown and the recap stays neutral,
        # rather than narrating a death as a clean exfil. The header is read
        # from the same frames as the stats; one last fresh grab as fallback.
        stats_d = stats_d or {}
        stats_d["outcome"] = outc or exfil_stats.read_outcome(cfg, engine)
        print(f"  [exfil] outcome: {stats_d['outcome'] or 'unknown'}")
        try:   # W/L record: one row per match, feeds the Stats page
            base = os.path.dirname(os.path.abspath(__file__))
            exfil_stats.log_outcome(base, s["session_id"], stats_d["outcome"])
        except Exception:
            pass
        # Death breaks the killstreak — mourn a real one.
        if stats_d.get("outcome") == "died" and s.get("heat") is not None:
            hev = s["heat"].on_death()
            if hev:
                print(f"  [heat] {hev.label} (was {hev.streak})")
                _fire_heat(cfg, s, hev)
            if s["web"] is not None:
                s["web"].set_heat({})     # chip off — the streak is gone
        # Audit THIS match's detected kills vs the game's count, then reset the
        # per-match tally for the next match.
        match_tags = s.get("match_tags", [])
        print(exfil_stats.report(stats_d, match_tags))

        # Ground-truth reconciliation: the game's own 'Runners Downed' number
        # is authoritative — if detection missed kills this match, add them so
        # the count matches the scoreboard. Clips can't be saved retroactively
        # (the buffer moved on), but the record is right.
        #
        # Everything below adds to session_tags only. The AUDIT lines above, the
        # accuracy tally and the canary keep using match_tags as DETECTED, so
        # they still measure detection, not detection-plus-reconcile.
        #
        # A second capture of the SAME panel (the summary screen flickered away
        # long enough to re-arm the edge trigger) arrives with an empty
        # match_tags against the game's full numbers: reconciling it again would
        # credit every kill a second time. A pass with no new event since the
        # previous exfil pass is that duplicate, so it reconciles nothing.
        dup_panel = (not match_tags
                     and s.get("_exfil_tags_len") == len(s["session_tags"]))
        if dup_panel and cfg.get("exfil_reconcile", True) and _panel_trusted(stats_d):
            print("  [exfil] no new events since the last exfil pass — treating "
                  "this as the same panel, not reconciling it twice")
        if cfg.get("exfil_reconcile", True) and not dup_panel:
            missed = _reconcile_missed(match_tags, stats_d)
            for _ in range(missed):
                s["count"] += 1
                s["downs"] = s.get("downs", 0) + 1   # keep the split total honest
                s["session_tags"].append("down")
                if s["web"] is not None:
                    s["web"].record(s["count"], "down",
                                    "RECONCILED — exfil screen credit")
                try:
                    from detector import KillEvent
                    log_kill(cfg, KillEvent(now, "RECONCILED (exfil screen)",
                                            "", "", True), s["count"])
                except Exception:
                    pass
            if missed:
                try:
                    s["obs"].set_counter(s["count"])
                except Exception:
                    pass
                print(f"  [reconcile] game credited more downs than detected — "
                      f"added {missed}, count now {s['count']}")
            # Runner Elims get the same treatment. They do NOT bump the headline
            # by themselves: a missed elim is usually the finish of a down that
            # was already counted. The floor below decides the headline.
            missed_e = _reconcile_missed_elims(match_tags, stats_d)
            for _ in range(missed_e):
                s["elims"] = s.get("elims", 0) + 1
                s["session_tags"].append("elim")
                if s["web"] is not None:
                    s["web"].record(s["count"], "elim",
                                    "RECONCILED — exfil screen elim credit")
                try:
                    from detector import KillEvent
                    log_kill(cfg, KillEvent(now, "RECONCILED elim (exfil screen)",
                                            "", "", True), s["count"])
                except Exception:
                    pass
            if missed_e:
                try:
                    s["obs"].set_counter(s["count"])
                except Exception:
                    pass
                print(f"  [reconcile] game credited more runner elims than "
                      f"detected — added {missed_e}")
            # Headline floor, per match: count_base is the headline at the
            # previous exfil, so this compares THIS match's kills only. (Against
            # the session total it would never fire after match 1.)
            short = _headline_floor(stats_d, s["count"] - s.get("count_base", 0))
            for _ in range(short):
                s["count"] += 1
                if s["web"] is not None:
                    s["web"].record(s["count"], "kill",
                                    "RECONCILED — exfil screen kill floor")
                try:
                    from detector import KillEvent
                    log_kill(cfg, KillEvent(now, "RECONCILED floor (exfil screen)",
                                            "", "", True), s["count"])
                except Exception:
                    pass
            if short:
                try:
                    s["obs"].set_counter(s["count"])
                except Exception:
                    pass
                # Worded so it never reads as a 'game credited more ...' line:
                # the desktop tiles parse those into extra down/elim tags.
                print(f"  [reconcile] headline below the game's panel for this "
                      f"match — raised by {short}, count now {s['count']}")
        # Close the match in the session-wide tag list, so a pending elim (or a
        # reconciled one) can't fold the next match's opening FINISHER. The
        # marker lives in exfil_stats, which counts it; until that side ships
        # there is no marker and nothing is appended.
        mb = getattr(exfil_stats, "MATCH_BREAK", None)
        if mb:
            s["session_tags"].append(mb)
        if stats_d:
            exfil_stats.accumulate_accuracy(s.setdefault("accuracy", {}),
                                            stats_d, match_tags)
            if s.get("canary") is not None:
                od, oe = _kill_counts(match_tags)
                s["canary"].note_audit(stats_d.get("runners_downed"),
                                       stats_d.get("runner_elims"), od, oe)
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
            # Crown the match's top looter with a LOOT GOBLIN decal on a
            # decorated copy of the exfil screenshot.
            if cfg.get("loot_goblin_decal", True) and squad and save_dir:
                try:
                    import glob
                    import loot_goblin
                    shots = [p for p in glob.glob(os.path.join(save_dir, "exfil_*.png"))
                             if "_lootgoblin" not in p]
                    if shots:
                        out = loot_goblin.decorate(max(shots, key=os.path.getmtime), squad)
                        if out:
                            goblin = max(squad, key=lambda p: p.get("inventory_value") or 0)
                            print(f"  [loot goblin] crowned {goblin.get('name', '?')} "
                                  f"-> {os.path.basename(out)}")
                except Exception as e:
                    print(f"  [loot goblin] error: {e}")
        s["match_tags"] = []
        s["count_base"] = s["count"]      # the next match's floor starts here
        s["_exfil_tags_len"] = len(s["session_tags"])   # duplicate-pass check
        s["runner_checked"] = False       # re-detect next match
        if s.get("heat") is not None:     # re-arm FIRST BLOOD for the next match
            s["heat"].new_match()
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
    if s.get("no_ffmpeg"):
        s["match_clips"] = []      # this match's clips stay on disk as clips
        print("  [reel] skipped: ffmpeg not found")
        return
    s["match_num"] += 1
    match_num = s["match_num"]

    def work():
        time.sleep(30)
        # Hand off the list BY REFERENCE, don't copy: clip-organize threads
        # append here concurrently, and a copy-then-clear loses any clip that
        # lands between the two steps (a last-second kill missing from a reel).
        # Rebinding keeps late appends attached to the list we're about to use.
        clips, s["match_clips"] = s["match_clips"], []
        if not clips:
            print(f"  [reel] match {match_num}: no clips this match, skipping reel")
            return
        try:
            import match_reel
            import montage
            base = os.path.dirname(os.path.abspath(__file__))
            ffmpeg = montage.find_ffmpeg(base, cfg)
            out = os.path.join(session_dir, "reels", f"match_{match_num}.mp4")
            # Dedupe overlapping buffer saves HERE so the kill headline, the
            # announcer's POTG pick, and the reel itself all agree on the
            # same clip list. (build_match_reel dedupes again — no-op then.)
            clips = match_reel.drop_overlapping(
                match_reel._normalize_clips(clips), ffmpeg)
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
                os.path.join(base, "witness_wordmark.png"),
                music_volume=cfg.get("reel_music_volume", 0.08),
                music_tracks=tracks, theme=cfg.get("theme"),
                **_reel_cut_kwargs(cfg))
            if ok:
                print(f"  [reel] match {match_num} highlights -> {out}")
                best = out          # upload the announced version if we make one
                if s["web"] is not None:
                    s["web"].add_reel(f"Match {match_num} — {len(clips)} clip"
                                      f"{'s' if len(clips) != 1 else ''}", out)
                if cfg.get("overlay_reel_ready", True):
                    show_text_overlay(cfg, "HIGHLIGHTS READY", size=34,
                                      position="custom:0.5,0.85", color="#c7bdff",
                                      duration_ms=2600, rise=False)
                if cfg.get("reel_announcer", True):
                    import announcer
                    potg = match_reel.pick_potg(match_reel._normalize_clips(clips))
                    script = announcer.stat_line(
                        total_kills, stats_d,
                        potg_tag=(potg["tag"] if potg else ""),
                        player=_announcer_name(cfg),
                        runner=s.get("last_runner", ""))
                    wav = announcer.synth_to_wav(
                        script,
                        os.path.join(session_dir, "reels", f"match_{match_num}_tts.wav"),
                        voice=cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                        pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH),
                        eleven_voice=cfg.get("elevenlabs_voice_id", ""))
                    if wav:
                        aout = os.path.join(session_dir, "reels",
                                            f"match_{match_num}_announced.mp4")
                        if match_reel.add_announcer(out, aout, wav, ffmpeg):
                            print(f"  [reel] announced version -> {aout}")
                            best = aout
                            if s["web"] is not None:
                                s["web"].add_reel(f"Match {match_num} (announced)", aout)
                        try:
                            os.remove(wav)
                        except OSError:
                            pass
                if cfg.get("youtube_upload_match_reels", False):
                    _yt_upload(cfg, base, best,
                               _yt_title(cfg, f"Match {match_num}",
                                         kills=total_kills, match=match_num),
                               "Auto-uploaded match highlights, made with WITNESS.")
        except Exception as e:
            print(f"  [reel] error: {e}")

    threading.Thread(target=work, daemon=True).start()


def _check_manual_kill(cfg, s, on_count=None):
    """If the dashboard's '+1 KILL' button was tapped, count a kill the
    detector missed — full pipeline: counter, ding, clip, instant replay.
    The raw line must keep starting with 'MANUAL': classify_event keys on it."""
    web = s["web"]
    if web is not None and getattr(web, "pop_kill_request", None) and web.pop_kill_request():
        from detector import KillEvent
        print("  [+1 kill added from the dashboard]")
        ev = KillEvent(timestamp=time.monotonic(),
                       raw_line="MANUAL +1 (added from the dashboard)",
                       killer="", victim="", is_self_kill=True)
        _handle_kill(cfg, ev, s, on_count)


def _check_manual_clip(s):
    """If the dashboard's 'SAVE CLIP' button was tapped, save a replay now."""
    web = s["web"]
    if web is not None and web.pop_clip_request():
        now = time.monotonic()
        if now - s["last_save"] >= s["min_save"]:
            if s["obs"].save_replay():
                s["last_save"] = now
                print("  [manual clip saved from the dashboard]")
                # Immediate, visible confirmation for the deliberate button press
                # (the auto kill-clip chip is a quiet corner blip; this one you
                # actually see). Fires the instant OBS confirms the save.
                if s["cfg"].get("overlay_clip_saved", True):
                    show_text_overlay(s["cfg"], "CLIP SAVED", size=42,
                                      position="custom:0.5,0.85", color="#c7bdff",
                                      duration_ms=1600, rise=False)
                if s["organize"]:
                    rename_clip_async(s["obs"], s["session_id"], "manual",
                                      s["count"],
                                      on_done=_clip_ready_callback(s, "manual", s["count"],
                                                                   kills=0))
            else:
                print("  [manual clip: replay save FAILED — is OBS's Replay "
                      "Buffer running?]")
                _clip_not_saved(s, "manual clip")
        else:
            print("  [manual clip: too soon after last save]")


HOUSEKEEPING_SLOW_SECONDS = 1.0   # print a timing line when a run takes longer


def _housekeeping_work(cfg, s, do_rb_disk: bool, do_black: bool) -> dict:
    """The periodic checks, timed: Replay Buffer health and free disk space
    (every 60 s) and OBS's black-capture check (every black_check_minutes).
    Runs on the housekeeping thread, never the detection loop. Returns
    {step: seconds}."""
    took = {}
    if do_rb_disk:
        # Replay Buffer health. If it stops mid-session (OBS restart, stray
        # hotkey, encoder/disk error) every remaining clip is silently lost, so
        # check occasionally and restart it BEFORE the next kill needs it
        # rather than after one is already gone.
        t0 = time.monotonic()
        try:
            if s["obs"].recover_replay_buffer() and s["web"] is not None:
                s["web"].notice("Replay Buffer had stopped — restarted", "alert")
        except Exception:
            pass
        took["replay-buffer check"] = time.monotonic() - t0
        # Clips + reels + Shorts run 1-2 GB a session; a full drive fails
        # silently and costs the whole night.
        t0 = time.monotonic()
        try:
            check_disk_space(cfg, s.get("record_dir", ""), web=s["web"], state=s)
        except Exception as e:
            print(f"  [housekeeping] disk check error: {e}")
        took["disk check"] = time.monotonic() - t0
    if do_black:
        # A Display Capture can lose its target MID-SESSION (monitor sleeps,
        # driver reloads), so a clean startup is not a guarantee for the next
        # three hours.
        t0 = time.monotonic()
        try:
            check_obs_not_black(cfg, s["obs"], web=s["web"], state=s)
        except Exception as e:
            print(f"  [housekeeping] black-capture check error: {e}")
        took["black-capture check"] = time.monotonic() - t0
    if sum(took.values()) > HOUSEKEEPING_SLOW_SECONDS:
        print("  [housekeeping] slow: "
              + ", ".join(f"{k} {v:.1f} s" for k, v in took.items()))
    return took


def _maybe_housekeeping(cfg, s, loop_start):
    """Start the periodic checks on a daemon thread when they're due.

    They used to run inline on the detection loop. On the real 07-29 log, 9
    mid-match windows with zero OCR (320 s, 3.6-4.0% of the session) started on
    a 60.13-s grid, the 60-s housekeeping period; a clip drive whose
    disk_usage hangs, or a slow OBS reply, froze detection for that long and
    popups on screen in the meantime were never read.

    Busy flag: when the previous run is still going, the due run is SKIPPED
    (and its period restarts), not queued behind it. Returns the thread
    started, or None."""
    do_rb_disk = loop_start - s.get("_last_rb_check", 0.0) > 60
    every = float(cfg.get("black_check_minutes", 5)) * 60
    do_black = every > 0 and (loop_start - s.get("_black_check_at", 0)) >= every
    if not (do_rb_disk or do_black):
        return None
    if do_rb_disk:
        s["_last_rb_check"] = loop_start
    if do_black:
        s["_black_check_at"] = loop_start
    if s.get("_hk_busy"):
        print("  [housekeeping] previous check still running — skipped this one")
        return None
    s["_hk_busy"] = True

    def run():
        try:
            _housekeeping_work(cfg, s, do_rb_disk, do_black)
        except Exception as e:
            print(f"  [housekeeping] error: {e}")
        finally:
            s["_hk_busy"] = False

    th = threading.Thread(target=run, name="witness-housekeeping", daemon=True)
    s["_hk_thread"] = th
    try:
        th.start()
    except Exception as e:          # e.g. can't start a thread: never stop detection
        s["_hk_busy"] = False
        print(f"  [housekeeping] could not start: {e}")
        return None
    return th


def run_live(cfg: dict, dry_run: bool = False, stop_event=None, on_count=None):
    _install_session_log()
    try:
        return _run_live_inner(cfg, dry_run, stop_event, on_count)
    except Exception:
        # Into the session log BEFORE the finally closes it. The window launcher
        # runs pythonw (no console), catches this in gui.py and prints there,
        # after stdout has been restored, so the cause of a failed START never
        # reached logs/session_*.log. print(), not traceback.print_exc(): _Tee
        # only wraps stdout, and under pythonw stderr is None.
        import traceback
        print(traceback.format_exc())
        raise
    finally:
        _close_session_log()


def _run_live_inner(cfg: dict, dry_run: bool = False, stop_event=None, on_count=None):
    det, mode = build_detector(cfg)

    from ocr import OCREngine

    _tune_performance(cfg)
    engine = OCREngine(cfg.get("ocr_engine", "easyocr"), cfg.get("ocr_upscale", 3), max_dim=cfg.get("ocr_max_dim", 800))
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

    # performance sampler (RAM / OCR loop time / fps) -> logs/perf_*.csv
    perf = {"iters": 0, "work_sum": 0.0}
    mon_stop = threading.Event()
    _start_perf_monitor(cfg, perf, mon_stop, lambda: s["count"], web=s["web"])

    def _wait(seconds):
        # STOP must still work while a locked screen keeps the loop backing off
        if stop_event is not None:
            stop_event.wait(seconds)
        else:
            time.sleep(seconds)

    loop = {"cap_fails": 0, "fails": 0, "tb_at": -1e9, "paused_at": None,
            "ocr_ok": False}
    cm = None
    try:
        try:
            # The FIRST open is not retried: a capture that cannot start at all
            # (bad capture_source, OBS Virtual Camera not running) is a setup
            # mistake, and START should fail with the reason. Once the session
            # is running, capture errors are retried and never end it.
            cm, cap = _open_capture(cfg)
            while not (stop_event is not None and stop_event.is_set()):
                loop_start = time.monotonic()
                # --- grab. Win+L, Ctrl+Alt+Del or a UAC prompt switch to the
                # secure desktop and mss raises on every grab until it's back.
                # That used to end the night's recording; now it waits.
                try:
                    if cap is None:
                        cm, cap = _open_capture(cfg)
                    img = cap.grab()
                except Exception as e:
                    loop["cap_fails"] += 1
                    _loop_error(loop, "capture", e)
                    _capture_paused_notice(s, loop)
                    if loop["cap_fails"] % CAPTURE_REOPEN_AFTER == 0:
                        # mss is thread-bound: close and re-open on THIS thread
                        _close_capture(cm)
                        cm = cap = None
                    _wait(CAPTURE_RETRY_SECONDS)
                    continue
                if loop["cap_fails"]:
                    _capture_recovered(s, loop)

                # --- OCR. A CUDA hiccup (OOM, a driver reset) gets the cached
                # VRAM handed back and a retry. Never a switch to CPU: CPU
                # EasyOCR at this rate is the frame-drop problem the size caps
                # were frozen to prevent.
                try:
                    lines = engine.read_lines(img)
                except ImportError:
                    raise              # OCR is not installed: retrying can't help
                except Exception as e:
                    _release_vram(engine)
                    if _loop_failed(s, loop, "ocr", e):
                        break
                    if not loop["ocr_ok"] and loop["fails"] >= OCR_STARTUP_TRIES:
                        # OCR hasn't worked once this session (model won't
                        # load, wrong CUDA build...): a setup problem. Fail
                        # START with the reason instead of retrying for minutes.
                        print("  [ocr] OCR has not worked once this session - "
                              "stopping so the error reaches START")
                        raise
                    _wait(min(2.0, 0.5 * loop["fails"]))
                    continue
                loop["ocr_ok"] = True

                try:
                    if debug_ocr and lines:
                        print(f"  [ocr] {' | '.join(lines)}")
                    blocked = is_suppressed(cfg, lines)
                    events = [] if blocked else detect_events(det, mode, lines, now=loop_start)

                    if s.get("canary") is not None:
                        s["canary"].note_frame(lines)
                        s["canary"].note_trigger(len(events))
                    for ev in events:
                        _handle_kill(cfg, ev, s, on_count)

                    # Right after a kill, read the victim's gamertag off the kill
                    # feed (the feed line expires in seconds — no waiting).
                    # ONLY for a real kill of yours: an assist popup would otherwise
                    # log a teammate's victim as "you downed", which is how a name
                    # you never killed ended up as the report's PRIME TARGET.
                    if cfg.get("track_names", True) and any(
                            classify_event(e.raw_line) == "down" for e in events):
                        _scan_feed_names(cfg, engine, s, expect="victim")

                    _check_coalesce(s)

                    # Every frame (not just suppressed ones): the handler is
                    # edge-triggered and needs the clear frames to re-arm.
                    if cfg.get("capture_exfil_stats", True):
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

                    # Replay Buffer health, free disk space and the black-capture
                    # re-check, on their own thread: a hung disk or OBS reply must
                    # not stop the OCR loop (see _maybe_housekeeping).
                    _maybe_housekeeping(cfg, s, loop_start)
                except Exception as e:
                    if _loop_failed(s, loop, "detect", e):
                        break
                    _wait(min(2.0, 0.5 * loop["fails"]))
                    continue
                loop["fails"] = 0

                elapsed = time.monotonic() - loop_start
                perf["iters"] += 1
                perf["work_sum"] += elapsed * 1000.0   # ms of grab+OCR+detect
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        except KeyboardInterrupt:
            pass
        finally:
            mon_stop.set()   # stop the perf sampler
            _close_capture(cm)
    finally:
        # On EVERY exit, a crash included: save the pending clip, put the
        # dashboard back to READY and build the recap. These used to run only
        # after a clean loop exit, so any exception lost the clip and the
        # recap and left the dashboard on WATCHING with a ticking timer.
        _finish_session(cfg, s, dry_run)


# --- capture-loop resilience -------------------------------------------------
# The loop never ends a session because the SCREEN is unavailable, and survives
# a run of OCR / detection errors. Tracebacks go to the session log with print
# (stderr is None under pythonw), at most one every LOOP_TRACEBACK_EVERY s.
CAPTURE_RETRY_SECONDS = 1.5    # backoff between failed grabs
CAPTURE_REOPEN_AFTER = 5       # consecutive failed grabs before re-opening it
LOOP_GIVE_UP_AFTER = 60        # consecutive OCR/detection failures, then stop
OCR_STARTUP_TRIES = 3          # ...or this many before OCR has worked even once
LOOP_TRACEBACK_EVERY = 30.0
CAPTURE_NOTICE_EVERY = 60.0


def _open_capture(cfg):
    """make_capture(cfg), entered: (context manager, capture). If entering
    fails half way the half-open capture is closed before the error goes up."""
    from capture import make_capture
    cm = make_capture(cfg)
    try:
        cap = cm.__enter__()
    except BaseException:
        _close_capture(cm)
        raise
    return cm, cap


def _close_capture(cm) -> None:
    if cm is None:
        return
    try:
        cm.__exit__(None, None, None)
    except Exception as e:
        print(f"  [capture] close failed: {e}")


def _loop_error(loop, what, e) -> None:
    """Call from inside an except block: one line plus the traceback, at most
    once every LOOP_TRACEBACK_EVERY seconds so a locked screen can't flood
    the session log."""
    import traceback
    now = time.monotonic()
    if now - loop["tb_at"] < LOOP_TRACEBACK_EVERY:
        return
    loop["tb_at"] = now
    n = loop["cap_fails"] if what == "capture" else loop["fails"]
    print(f"  [{what}] error ({n} in a row): {type(e).__name__}: {e}")
    print(traceback.format_exc())


def _capture_paused_notice(s, loop) -> None:
    """'Detection paused' on the dashboard, at most once per minute."""
    now = time.monotonic()
    if loop["paused_at"] is None:
        print("  [capture] the screen can't be captured (locked, Ctrl+Alt+Del or "
              "a UAC prompt?) - detection paused, retrying")
    if loop["paused_at"] is not None and now - loop["paused_at"] < CAPTURE_NOTICE_EVERY:
        return
    loop["paused_at"] = now
    if s.get("web") is not None:
        try:
            s["web"].notice("Detection paused - screen unavailable", "alert")
        except Exception:
            pass


def _capture_recovered(s, loop) -> None:
    print(f"  [capture] screen capture is back after {loop['cap_fails']} failed "
          "grab(s) - detecting again")
    if loop["paused_at"] is not None and s.get("web") is not None:
        try:
            s["web"].notice("Detection resumed", "alert")
        except Exception:
            pass
    loop["cap_fails"] = 0
    loop["paused_at"] = None


def _release_vram(engine) -> None:
    """Hand cached VRAM back after an OCR error (engine's own, else ocr's).
    The frozen size caps are not touched."""
    try:
        fn = getattr(engine, "release_vram", None)
        if not callable(fn):
            import ocr as _ocr
            fn = getattr(_ocr, "release_vram", None)
        if callable(fn):
            fn()
    except Exception:
        pass


def _loop_failed(s, loop, what, e) -> bool:
    """Count one OCR/detection failure (call from inside the except block).
    True when it's time to stop: LOOP_GIVE_UP_AFTER failures in a row."""
    loop["fails"] += 1
    _loop_error(loop, what, e)
    if loop["fails"] < LOOP_GIVE_UP_AFTER:
        return False
    print(f"  [{what}] {loop['fails']} failures in a row - stopping detection. "
          "The traceback above says why.")
    if s.get("web") is not None:
        try:
            s["web"].notice("Detection stopped - kept failing, see log", "alert")
        except Exception:
            pass
    return True


def _finish_session(cfg, s, dry_run) -> None:
    """The end of a session, run on every exit path. Each step is guarded on
    its own, so a recap failure can't hide the error that ended the session
    (that one is still propagating, and run_live logs it)."""
    import traceback

    def step(label, fn):
        try:
            return fn()
        except Exception:
            print(f"  [end] {label} failed:")
            print(traceback.format_exc())
            return None

    # Let a running check finish (bounded) so its lines land before the recap.
    hk = s.get("_hk_thread")
    if hk is not None:
        step("housekeeping", lambda: hk.join(timeout=3.0))
    step("saving the last clip", lambda: _flush_coalesce(s))
    if s.get("web") is not None:
        step("dashboard", lambda: s["web"].set_running(False))
    health = step("canary verdict", lambda: _canary_health(s)) or ""
    acc_line = step("accuracy", lambda: exfil_stats_accuracy(s))
    if acc_line:
        print(acc_line)
    can_line = step("canary", lambda: canary_report(s))
    if can_line:
        print(can_line)
    step("recap", lambda: _end_session(
        cfg, s["session_tags"], s["session_start"], s["session_start_wall"],
        dry_run, s["obs"], s["session_id"], kills=s["count"], web=s["web"],
        health_note=health))


def _canary_health(s) -> str:
    """The detection canary's verdict for the dashboard: a feed notice now and
    the full message for the final recap note. '' when it's 'ok'. It used to
    be printed only, and the window app has no console."""
    c = s.get("canary")
    if c is None:
        return ""
    level, msg = c.verdict()
    if level in ("broken", "blind"):
        notice, note = ("DETECTION LOOKS BROKEN - see recap",
                        f"DETECTION LOOKS BROKEN - {msg}")
    elif level == "quiet":
        # Quieter on purpose: the canary itself calls this normal on a rough
        # night, and a false 'broken' teaches people to ignore the real one.
        notice, note = ("No kill popup matched all session - see recap",
                        f"Detection check: {msg}")
    else:
        return ""
    if s.get("web") is not None:
        try:
            s["web"].notice(notice, "alert")
        except Exception:
            pass
    return note


def exfil_stats_accuracy(s) -> str:
    try:
        import exfil_stats
        return exfil_stats.accuracy_summary(s.get("accuracy", {}))
    except Exception:
        return ""


def canary_report(s) -> str:
    """End-of-session detection health. Always prints the counts; shouts only
    when the evidence is conclusive, because a false 'your app is broken' on a
    quiet night teaches you to ignore the warning."""
    c = s.get("canary")
    if c is None:
        return ""
    lines = ["  " + c.summary()]
    level, msg = c.verdict()
    if level == "broken":
        lines += ["", "  " + "!" * 66,
                  "  DETECTION LOOKS BROKEN — " + msg,
                  "  " + "!" * 66]
    elif level in ("blind", "quiet"):
        lines.append(f"  [canary] {level.upper()}: {msg}")
    return "\n".join(lines)


def _end_session(cfg, tags, start_monotonic, start_wall, dry_run, obs=None,
                 session_id=None, kills=None, web=None, health_note=""):
    # kills = real kills (a down + its finisher counted once); tags = every
    # detected event, for the type breakdown. health_note: the detection
    # canary's verdict when it isn't 'ok', shown on the final recap.
    def _rc(**kw):   # push build status to the dashboard's recap panel
        if web is not None:
            try:
                web.set_recap(**kw)
            except Exception:
                pass

    total = kills if kills is not None else len(tags)
    dur_min = max(0.01, (time.monotonic() - start_monotonic) / 60.0)
    c = Counter(tags)
    print("\n" + "-" * 44)
    # Report the SAME two numbers the game's exfil panel shows, so the summary is
    # checkable against what Stan saw on screen. 'downs' used to lump elims in
    # with downs and precision was double-counted into the headline.
    s_downs, s_elims = _kill_counts(tags)
    print(f"Session over. Kills: {total}  |  downs {s_downs}  elims {s_elims}"
          f"  (precision {c.get('precision', 0)}, finisher {c.get('finisher', 0)})")
    print(f"Assists (clips only, not counted): {c.get('assist', 0)}")
    print(f"Duration: {dur_min:.1f} min  |  {total / dur_min:.2f} kills/min")

    # The recap used to say "Everything built" for these too. No session
    # marker is written: an assist-only session's clips still get their
    # montage from the launch-time resume, as before.
    if dry_run:
        _rc(status="ready", note=_join_notes(health_note, DRY_RUN_NOTE),
            report="", kills=total)
        return
    if total == 0:
        _rc(status="ready", note=_join_notes(health_note, NO_KILLS_NOTE),
            report="", kills=total)
        return
    _rc(status="building", note="building your highlights…", report="", kills=total)
    session = {
        "date": time.strftime("%Y-%m-%d"),
        "start": start_wall,
        "duration_min": round(dur_min, 1),
        "total": total,
        "precision": c.get("precision", 0),
        "finisher": c.get("finisher", 0),
        "assist": c.get("assist", 0),
        # The count_events downs printed on the 'Session over' line (and the
        # game's 'Runners Downed'). It used to be down+kill, the retired rule
        # that filed every RUNNER ELIM under downs (3/13 audited matches exact,
        # vs 13/13 for this). The CSV column changes meaning with no back-fill.
        "down": s_downs,
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
            p = matchcard.build_card(session, card, os.path.join(base, "witness_wordmark.png"))
            if p:
                print(f"Match card: {p}")
        except Exception as e:
            print(f"(card error: {e})")

    session_dir = ""
    if obs is not None and session_id:
        try:
            rec = obs.get_record_directory()
        except Exception as e:
            print(f"(couldn't ask OBS for the clip folder: {e})")
            rec = ""
        session_dir = os.path.join(rec, "Marathon Sessions", session_id) if rec else ""

    # WITNESS Report — the end-of-night dossier (written case file + spoken).
    report_speech = ""   # narration text, reused as the session reel's voiceover
    if cfg.get("witness_report", True):
        try:
            import witness_report
            base = os.path.dirname(os.path.abspath(__file__))
            try:
                import encounters
                # tonight's names only — the dossier must not claim last
                # week's nemesis as tonight's standing threat
                victims, killers = encounters.boards(base, session=session_id or "")
            except Exception:
                victims, killers = [], []
            rep = witness_report.build_report(
                session, victims, killers,
                player=_announcer_name(cfg))
            report_speech = rep["speech"]
            _rc(report="\n".join(rep["lines"]), note="writing the WITNESS Report…")
            print("\n" + "\n".join(rep["lines"]))
            if session_dir:
                try:
                    with open(os.path.join(session_dir, "witness_report.txt"),
                              "w", encoding="utf-8") as f:
                        f.write("\n".join(rep["lines"]))
                except OSError:
                    pass
            if cfg.get("witness_report_voice", cfg.get("reel_announcer", True)) and session_dir:
                try:
                    import announcer
                    wav = announcer.synth_to_wav(
                        rep["speech"],
                        os.path.join(session_dir, "witness_report_tts.wav"),
                        voice=cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                        pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH),
                        eleven_voice=cfg.get("elevenlabs_voice_id", ""))
                    if wav:
                        print(f"  [report] spoken dossier -> {wav}")
                except Exception as e:
                    print(f"  [report] voice failed: {e}")
        except Exception as e:
            print(f"(witness report error: {e})")

    failures = _build_session_artifacts(cfg, session_dir, tags, rc=_rc,
                                        report_speech=report_speech)
    # 'ready' with an empty note is the only "Everything built"; a failed
    # stage is 'error' with the reasons (the dashboard draws both).
    status, note = _recap_verdict(session_dir, failures)
    _rc(status=status, note=_join_notes(health_note, note))


# Final recap notes (the dashboard shows them on the recap box).
NO_KILLS_NOTE = "No kills this session - nothing to build"
DRY_RUN_NOTE = "Dry run - nothing was saved"
FFMPEG_SKIP_NOTE = "ffmpeg not found - clips saved, reels skipped (QUICKSTART step 3)"
NO_FOLDER_NOTE = "OBS didn't report its recording folder - reels skipped"
# Launch-time auto-resume of a FAILED recap stops after this many failed
# attempts (the end-of-session build counts as the first). The Archive's
# Rebuild button and "Rebuild Last Session.bat" still retry by hand.
RECAP_AUTO_RESUME_MAX = 2


def _join_notes(*notes) -> str:
    return " | ".join(n for n in notes if n)


def _recap_verdict(session_dir, failures) -> tuple:
    """(status, note) for the final recap after _build_session_artifacts."""
    failures = list(failures or [])
    if failures:
        return "error", (f"{len(failures)} stage(s) failed: "
                         + "; ".join(str(f)[:160] for f in failures))
    if not session_dir:
        return "ready", NO_FOLDER_NOTE
    if os.path.exists(os.path.join(session_dir, ".recap_skipped")):
        return "ready", FFMPEG_SKIP_NOTE
    return "ready", ""


def _recap_attempts(session_dir) -> int:
    """How many recap builds of this session have failed: the attempts=N in
    .recap_failed (1 for a file from before the counter), 0 for none."""
    path = os.path.join(session_dir or "", ".recap_failed")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except OSError:
        return 0
    m = re.search(r"attempts=(\d+)", first)
    return int(m.group(1)) if m else 1


def _build_session_artifacts(cfg, session_dir, tags, rc=None, report_speech=""):
    """The heavy end-of-session renders: montage, Play of the Night, Shorts,
    and the session reel. Factored out of _end_session so an interrupted
    build can be RESUMED at next launch (resume_unfinished_recap)."""
    def _rc(**kw):
        if rc is not None:
            try:
                rc(**kw)
            except Exception:
                pass

    # Every stage failure is collected, NOT just printed. Last time the reels all
    # raised TypeError, each except printed a line, and .recap_done was written
    # anyway — so the session looked complete forever and could never be resumed
    # or rebuilt. A recap is only "done" if nothing failed.
    failures = []

    # No ffmpeg: every render stage below would fail (and leave card PNGs
    # behind). Skip them cleanly, still tidy, and mark the session SKIPPED so
    # the launch-time resume doesn't retry it on every boot.
    base = os.path.dirname(os.path.abspath(__file__))
    renders_on = bool(session_dir) and any((
        cfg.get("make_montage", True), cfg.get("play_of_the_night", True),
        cfg.get("make_shorts", True), cfg.get("make_session_reel", True),
        cfg.get("youtube_upload_session_reel", False)))
    skip = renders_on and not _ffmpeg_available(base, cfg)
    if skip:
        print(f"  [recap] {FFMPEG_SKIP_NOTE}")
        _rc(note=FFMPEG_SKIP_NOTE)

    # Highlight montage of this session's clips (needs ffmpeg + organized clips).
    if not skip:
        _rc(note="building session montage…")
    if cfg.get("make_montage", True) and session_dir and not skip:
        try:
            import montage
            made = montage.build_montage(session_dir,
                                         montage.find_ffmpeg(base, cfg))
            # None with clips on disk is a failed montage, not "nothing to do"
            # (build_montage prints why and returns None).
            if made is None and _session_clips_from_dir(session_dir):
                failures.append("montage: no montage was built")
            # The montage is the one Stan actually wants on YouTube ("mainly the
            # end montage"), and it was the only artifact with no upload flag.
            if made and cfg.get("youtube_upload_montage", False):
                downs, elims = _kill_counts(tags)
                _yt_upload(
                    cfg, base, made,
                    _yt_title(cfg, "Session Montage", downs=downs, elims=elims),
                    "Session montage, auto-uploaded with WITNESS.\n"
                    f"{downs} runners downed, {elims} elims.")
        except Exception as e:
            print(f"(montage error: {e})")
            failures.append(f"montage: {e}")

    # Play of the Night — the single best clip across the WHOLE session, cut
    # into its own reel. Scoring is the same pick as per-match POTG (most kills,
    # ties to the flashier tag, then latest), run over every session clip.
    if not skip:
        _rc(note="cutting Play of the Night…")
    if cfg.get("play_of_the_night", True) and session_dir and not skip:
        try:
            import match_reel
            import montage
            clips = _session_clips_from_dir(session_dir)
            if clips:
                best = (match_reel.pick_potg(clips)
                        or max(clips, key=lambda c: c.get("kills", 1)))
                ff = montage.find_ffmpeg(base, cfg)
                out = os.path.join(session_dir, "reels", "play_of_the_night.mp4")
                tracks = (match_reel.list_music(os.path.join(base, "music"))
                          if cfg.get("reel_music", True) else [])
                tagtxt = (str(best.get("tag", "")).replace("+", " + ")
                          .replace("_", " ").upper())
                sub = [f"BEST OF {len(clips)} CLIP{'S' if len(clips) != 1 else ''}"
                       + (f"  ·  {tagtxt}" if tagtxt else ""),
                       time.strftime("%Y-%m-%d")]
                if match_reel.build_match_reel(
                        [best], out, ff, "PLAY OF THE NIGHT",
                        best.get("kills", 1), sub,
                        os.path.join(base, "witness_wordmark.png"),
                        music_volume=cfg.get("reel_music_volume", 0.08),
                        music_tracks=tracks, theme=cfg.get("theme"),
                        **_reel_cut_kwargs(cfg)):
                    print(f"Play of the Night -> {out}")
        except Exception as e:
            print(f"(play of the night error: {e})")
            failures.append(f"play of the night: {e}")

    # Vertical Shorts render of each clip (needs ffmpeg + organized clips).
    if not skip:
        _rc(note="rendering vertical Shorts…")
    if cfg.get("make_shorts", True) and session_dir and not skip:
        try:
            import montage
            import shorts
            shorts.build_shorts(session_dir, montage.find_ffmpeg(base, cfg),
                                with_labels=cfg.get("shorts_labels", True),
                                theme=cfg.get("theme"),
                                render_encoder=str(cfg.get("render_encoder",
                                                           "auto")))
            if cfg.get("youtube_upload_shorts", False):
                sdir = os.path.join(session_dir, "shorts")
                if os.path.isdir(sdir):
                    for f in sorted(os.listdir(sdir)):
                        if f.lower().endswith(".mp4"):
                            _yt_upload(cfg, base, os.path.join(sdir, f),
                                       f"Marathon clip — {time.strftime('%b %d')} #Shorts",
                                       "Auto-uploaded with WITNESS. #Shorts #Marathon")
        except Exception as e:
            print(f"(shorts error: {e})")
            failures.append(f"shorts: {e}")

    # Session highlight reel (all clips + title card + Play of the Game), and
    # optional unlisted YouTube upload of just that one video.
    if not skip:
        _rc(note="building the session reel (the big one)…")
    if session_dir and not skip and (cfg.get("make_session_reel", True)
                                     or cfg.get("youtube_upload_session_reel", False)):
        if not _build_session_reel_and_upload(cfg, session_dir, tags,
                                              report_speech=report_speech):
            failures.append("session reel")

    # Organize the folder — LAST, so nothing moves out from under a builder
    # above. Only moves files into clips/ and exfil/; deletes nothing.
    if session_dir and cfg.get("tidy_session_folder", True):
        try:
            import tidy
            tidy.tidy_session(session_dir)
        except Exception as e:
            print(f"  [tidy] skipped: {e}")

    # Markers for the launch-time resume check (find_unfinished_session):
    #   .recap_done     finished (covers configs where the session reel is off)
    #   .recap_failed   a stage failed: NOT done, so the next launch resumes it.
    #                   Line 1 counts the failed attempts; once it reaches
    #                   RECAP_AUTO_RESUME_MAX the auto-resume stops (a stage
    #                   that keeps failing used to re-render on every boot).
    #                   The reasons follow, one per line.
    #   .recap_skipped  no ffmpeg: nothing could be rendered, nothing to resume
    if session_dir:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            if failures:
                attempts = _recap_attempts(session_dir) + 1
                with open(os.path.join(session_dir, ".recap_failed"), "w",
                          encoding="utf-8") as f:
                    f.write(f"{stamp} attempts={attempts}\n"
                            + "\n".join(failures) + "\n")
                print(f"  [recap] {len(failures)} stage(s) FAILED — not marking "
                      f"this session complete so it can be rebuilt:")
                for why in failures:
                    print(f"          - {why}")
            elif skip:
                with open(os.path.join(session_dir, ".recap_skipped"), "w",
                          encoding="utf-8") as f:
                    f.write(f"{stamp}\n{FFMPEG_SKIP_NOTE}\n")
            else:
                with open(os.path.join(session_dir, ".recap_done"), "w",
                          encoding="utf-8") as f:
                    f.write(stamp)
                try:           # a resume that succeeded: the old failure is stale
                    os.remove(os.path.join(session_dir, ".recap_failed"))
                except OSError:
                    pass
        except OSError:
            pass
    return failures


def find_unfinished_session(record_dir: str) -> str:
    """The most recent session folder whose recap build was cut off — it has
    organized clips but no session_reel.mp4. '' when there's nothing to do.
    Only the NEWEST clip-bearing session is considered: older gaps are treated
    as deliberate (user may have turned renders off back then)."""
    import montage
    import tidy
    root = os.path.join(record_dir or "", "Marathon Sessions")
    if not os.path.isdir(root):
        return ""
    for name in sorted(os.listdir(root), reverse=True):
        sdir = os.path.join(root, name)
        if not os.path.isdir(sdir):
            continue
        # Layout-agnostic: a TIDIED session keeps its clips in clips/, and
        # scanning only the top level made every tidied session invisible here —
        # so an interrupted-then-tidied recap could never be resumed.
        clips = list(tidy.iter_session_media(sdir, montage.VIDEO_EXTS))
        if not clips:
            continue
        if (os.path.exists(os.path.join(sdir, ".recap_done"))
                or os.path.exists(os.path.join(sdir, "session_reel.mp4"))
                or os.path.exists(os.path.join(sdir, ".recap_skipped"))):
            return ""          # newest session is complete — nothing to resume
        if _recap_attempts(sdir) >= RECAP_AUTO_RESUME_MAX:
            return ""          # failed again on resume: leave it to Rebuild
        return sdir
    return ""


def resume_unfinished_recap(web=None) -> bool:
    """Called at app launch: if the last session's recap build was interrupted
    (closed mid-render), finish it in the background. Returns True if a resume
    ran. Tags are reconstructed from the clip filenames."""
    try:
        cfg = load_config()
        if not cfg.get("resume_recap", True):
            return False
        sdir = find_unfinished_session(cached_record_dir())
        if not sdir:
            return False
        name = os.path.basename(sdir)
        print(f"  [resume] last session's recap was interrupted — finishing {name}")

        def rc(**kw):
            if web is not None:
                try:
                    web.set_recap(**kw)
                except Exception:
                    pass

        rc(status="building", report="",
           note=f"finishing last session's recap ({name})…")
        tags = []
        for c in _session_clips_from_dir(sdir):
            tags += [t for t in str(c.get("tag", "")).split("+") if t]
        failures = _build_session_artifacts(cfg, sdir, tags, rc=rc)
        status, note = _recap_verdict(sdir, failures)
        rc(status=status, note=note)
        if status == "error":
            print(f"  [resume] {name} recap still had problems: {note}")
        else:
            print(f"  [resume] {name} recap finished")
        return True
    except Exception as e:
        print(f"  [resume] error: {e}")
        return False


# NNN_<tags>_HH-MM-SS, anchored at both ends: the tag part may itself contain
# '_' (a +1 KILL press is 'manual_kill', a watchlist death 'downedby_<name>').
_CLIP_NAME_RE = re.compile(r"^(\d+)_(.+)_(\d{2}-\d{2}-\d{2})$")


def _clip_tags_from_name(fname: str) -> list:
    """Event tags from an organized clip's file name; [] if it doesn't fit.

    Splitting on '_' read '002_manual_kill_20-05-00' as tag 'manual' at clock
    'kill': a hand-credited kill came back from disk as a 0-kill 'SAVED'
    clip in rebuilds, resumes and Play of the Night. clip_index.parse_name is
    used when its answer has a real clock (the reel-pipeline package anchors
    it at the end, the same way); otherwise the end-anchored match here."""
    try:
        import clip_index
        _n, tags, clock = clip_index.parse_name(fname)
        if tags and re.fullmatch(r"\d{2}:\d{2}:\d{2}", clock or ""):
            return list(tags)
    except Exception:
        pass
    m = _CLIP_NAME_RE.match(os.path.splitext(os.path.basename(fname))[0])
    return [t for t in m.group(2).split("+") if t] if m else []


def _session_clips_from_dir(session_dir: str):
    """List this session's organized clips (top level only) as reel dicts,
    inferring the kill count from the filename tag (e.g. down+finisher = 2)."""
    import match_reel
    import montage
    import tidy
    out = []
    # Tolerates a TIDIED folder (clips moved into clips/) as well as a flat one,
    # so browsing or resuming an organized session still finds its clips.
    for rel, full in tidy.iter_session_media(session_dir, montage.VIDEO_EXTS):
        f = os.path.basename(rel)
        if f.lower().startswith("session"):
            continue
        # NNN_tags_HH-MM-SS.ext -> tags. A name that doesn't fit (an OBS
        # default 'Replay 2026-...', a hand-dropped file) stays a 1-kill
        # 'kill' clip, as before, so it never drops out of a reel.
        tags = _clip_tags_from_name(f)
        tag = "+".join(tags) or "kill"
        tags = tags or ["kill"]
        out.append({"path": full,     # may live in clips/ on a tidied session
                    "kills": _kill_count(tags),
                    "downs": _kill_counts(tags)[0],
                    "tag": tag})
    # Chronological — the filename's leading number is the kill count and now
    # repeats, so sorting by name cut back to earlier moments mid-reel.
    return match_reel.sort_chronologically(out)


def _yt_title(cfg, kind: str, **facts) -> str:
    """The YouTube title for an upload, from youtube_title_template in config.

    Stan asked for "WITNESS Auto Reel and the date", which is now the default.
    Placeholders: {kind} {date} {downs} {elims} {kills} {game} {match}. An unknown
    placeholder falls back to the plain template rather than raising mid-upload —
    a typo in config must not cost the upload."""
    tmpl = str(cfg.get("youtube_title_template")
               or "WITNESS Auto Reel — {date}")
    vals = {"kind": kind, "date": time.strftime("%b %d, %Y"),
            "game": str(cfg.get("game", "") or "").title(),
            "downs": "", "elims": "", "kills": "", "match": ""}
    vals.update({k: v for k, v in facts.items() if v is not None})
    try:
        return tmpl.format(**vals)[:100]          # YouTube caps titles at 100
    except (KeyError, IndexError, ValueError) as e:
        print(f"  [youtube] youtube_title_template is invalid ({e}); "
              "using the default")
        return f"WITNESS Auto Reel — {vals['date']}"[:100]


def _yt_upload(cfg, base, path, title, desc):
    """Upload one file to YouTube if the libraries + client_secret are set up.
    Degrades gracefully (prints why) — never breaks the end-of-session flow."""
    try:
        import youtube_upload
        youtube_upload.upload(path, title, desc, base,
                              privacy=cfg.get("youtube_privacy", "unlisted"))
    except Exception as e:
        print(f"  [youtube] error: {e}")


def _build_session_reel_and_upload(cfg, session_dir, tags, report_speech="") -> bool:
    """True if the session reel was written (or genuinely had nothing to build).
    False on a real failure, so the caller can refuse to mark the recap done."""
    try:
        import match_reel
        import montage
        base = os.path.dirname(os.path.abspath(__file__))
        clips = _session_clips_from_dir(session_dir)
        if not clips:
            print("  [session reel] no clips this session, skipping")
            return True          # nothing to do is not a failure
        ffmpeg = montage.find_ffmpeg(base, cfg)
        out = os.path.join(session_dir, "session_reel.mp4")
        c = Counter(tags)
        total = _kill_count(tags)   # real kills, not every event tag
        sub = [f"{c.get('finisher',0)} FINISHERS  ·  {c.get('precision',0)} PRECISION  "
               f"·  {c.get('assist',0)} ASSISTS",
               time.strftime("%Y-%m-%d")]
        tracks = []
        if cfg.get("reel_music", True):
            tracks = match_reel.list_music(os.path.join(base, "music"))
        ok = match_reel.build_match_reel(
            clips, out, ffmpeg, "SESSION HIGHLIGHTS", total, sub,
            os.path.join(base, "witness_wordmark.png"),
            music_volume=cfg.get("reel_music_volume", 0.08),
            music_tracks=tracks, theme=cfg.get("theme"),
            **_reel_cut_kwargs(cfg))
        if not ok:
            print("  [session reel] build failed")
            return False

        # Bake the WITNESS Report dossier in as the reel's voiceover — that's
        # where the spoken report actually earns its keep (nobody opens a lone
        # mp3). The narrated version replaces the silent session_reel.mp4.
        if report_speech and cfg.get("witness_report_on_reel", True):
            try:
                import announcer
                wav = announcer.synth_to_wav(
                    report_speech,
                    os.path.join(session_dir, "_session_dossier.wav"),
                    voice=cfg.get("announcer_voice", announcer.DEFAULT_VOICE),
                    pitch=cfg.get("announcer_pitch", announcer.DEFAULT_PITCH),
                    eleven_voice=cfg.get("elevenlabs_voice_id", ""))
                if wav:
                    narrated = os.path.join(session_dir, "_session_reel_vo.mp4")
                    if match_reel.add_announcer(out, narrated, wav, ffmpeg):
                        os.replace(narrated, out)   # session_reel.mp4 now narrated
                        print("  [session reel] WITNESS Report dossier mixed in")
                    try:
                        os.remove(wav)
                    except OSError:
                        pass
            except Exception as e:
                print(f"  [session reel] dossier mix skipped: {e}")

        print(f"  [session reel] -> {out}")

        if cfg.get("youtube_upload_session_reel", False):
            import youtube_upload
            r_downs, r_elims = _kill_counts(tags)
            title = _yt_title(cfg, "Session Reel", kills=total,
                              downs=r_downs, elims=r_elims)
            desc = ("Auto-uploaded Marathon session highlights.\n"
                    f"{c.get('finisher',0)} finishers, {c.get('precision',0)} precision, "
                    f"{c.get('assist',0)} assists.")
            youtube_upload.upload(out, title, desc, base,
                                  privacy=cfg.get("youtube_privacy", "unlisted"))
        return True
    except Exception as e:
        print(f"  [session reel] error: {e}")
        return False


def _tray_icon_image(base: str, cfg: dict):
    """Build a square tray icon from the WITNESS logo (transparent, centered)."""
    from PIL import Image
    size = 64
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    try:
        p = os.path.join(base, cfg.get("app_icon", "witness_logo.png"))
        if not os.path.exists(p):
            # our own logo, never the Marathon game art this used to fall back to
            p = os.path.join(base, "witness_logo.png")
        im = Image.open(p).convert("RGBA")
        scale = min(size / im.width, size / im.height)
        nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
        im = im.resize((nw, nh), Image.LANCZOS)
        canvas.paste(im, ((size - nw) // 2, (size - nh) // 2), im)
    except Exception:
        # fallback: a plain acid-yellow square
        from PIL import ImageDraw
        ImageDraw.Draw(canvas).rectangle([6, 6, size - 6, size - 6], fill=(156, 88, 218, 255))
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
                ic.title = f"WITNESS — {n} kills"
            except Exception:
                pass

    def on_quit(icon, item):
        stop_event.set()
        icon.stop()

    menu = pystray.Menu(pystray.MenuItem("Quit", on_quit))
    icon = pystray.Icon("marathon", _tray_icon_image(base, cfg),
                        "WITNESS", menu)
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


def _resolve_session_dir(cfg, which: str) -> str:
    """Session folder for --rebuild: a full path, a folder name, or 'latest'.

    A full path is the CLI's ('Rebuild Last Session.bat' passes its arguments
    through) and must be absolute. A name must be a folder INSIDE 'Marathon
    Sessions': '..', '../x' and names that resolve elsewhere are refused.
    Before, any existing directory was accepted, including one relative to
    the current folder, and the dashboard routes fed request data in here
    (they now validate first; this check stands on its own)."""
    if which and which != "latest" and os.path.isabs(which) and os.path.isdir(which):
        return which
    root = ""
    # The cached folder first: rebuilding is an offline job on files already on
    # disk, so it must NOT require OBS to be running. cached_record_dir() is
    # written every session for exactly this kind of use.
    rec = cached_record_dir()
    if rec:
        root = os.path.join(rec, "Marathon Sessions")
    if not os.path.isdir(root):
        try:
            from obs_client import OBSClient      # imported lazily, like elsewhere
            obs_cfg = (cfg or {}).get("obs", {}) or {}
            obs = OBSClient(host=obs_cfg.get("host", "localhost"),
                            port=obs_cfg.get("port", 4455),
                            password=_obs_password(obs_cfg),
                            # a lookup, not a session: don't start OBS's buffer
                            auto_start_replay_buffer=False)
            obs.connect()
            rec = obs.get_record_directory()
            close = getattr(obs, "close", None)   # newer obs_client only
            if callable(close):
                close()
            if rec:
                root = os.path.join(rec, "Marathon Sessions")
                cache_record_dir(rec)
        except Exception as e:
            print(f"(couldn't ask OBS for the clip folder: {e})")
    if not root or not os.path.isdir(root):
        example = (os.path.join(rec, "Marathon Sessions", "<session folder>") if rec
                   else "<your OBS recording folder>/Marathon Sessions/<session folder>")
        print("Couldn't find the 'Marathon Sessions' folder. Pass the full path:"
              f"\n  python main.py --rebuild \"{example}\"")
        return ""
    if which and which != "latest":
        if which in (".", "..") or os.path.isabs(which):
            print(f"No session folder named {which!r} in {root}")
            return ""
        cand = os.path.join(root, which)
        real_root = os.path.realpath(root)
        if (os.path.realpath(cand).startswith(real_root + os.sep)
                and os.path.isdir(cand)):
            return cand
        print(f"No session folder named {which!r} in {root}")
        return ""
    names = sorted((n for n in os.listdir(root)
                    if os.path.isdir(os.path.join(root, n))), reverse=True)
    return os.path.join(root, names[0]) if names else ""


def rebuild_session(cfg, which: str = "latest", rc=None) -> None:
    """Re-run the end-of-session renders for a session already on disk.

    The clips are the only irreplaceable part; the montage, reels, Shorts and
    dossier are all derived. So when a recap fails — as it did on 2026-07-29,
    where every reel raised TypeError while the clips organized perfectly — the
    session is fully recoverable and this is how.

    Tags are recovered from the clip FILENAMES (NNN_tag_time.mkv), which is why
    the tag is in the name at all.

    rc: optional status callback (LiveState.set_recap) so the dashboard's Rebuild
    button shows which stage is running. A multi-minute render with a single
    unchanging message looks hung."""
    def _say(note):
        print(f"  [rebuild] {note}")
        if rc is not None:
            try:
                rc(status="building", note=note)
            except Exception:
                pass
    sdir = _resolve_session_dir(cfg, which)
    if not sdir:
        if rc is not None:
            rc(status="error", note="couldn't find that session folder")
        return
    name = os.path.basename(sdir.rstrip(os.sep))
    print(f"Rebuilding: {sdir}")
    _say(f"rebuilding {name}")
    clips = _session_clips_from_dir(sdir)
    if not clips:
        print("  no clips in that folder — nothing to rebuild")
        if rc is not None:
            rc(status="error", note="no clips on disk for that session")
        return
    tags = []
    for c in clips:
        tags.extend(t for t in str(c.get("tag", "")).split("+") if t)
    downs, elims = _kill_counts(tags)
    print(f"  {len(clips)} clip(s) on disk -> {downs} downs, {elims} elims, "
          f"{_kill_count(tags)} kills")
    # Clear the stale markers so this run's outcome is what counts (and a
    # manual rebuild starts the auto-resume attempt count over).
    for marker in (".recap_done", ".recap_failed", ".recap_skipped"):
        try:
            os.remove(os.path.join(sdir, marker))
        except OSError:
            pass
    _say(f"{len(clips)} clips · {downs} downs · {elims} elims")
    _build_session_artifacts(cfg, sdir, tags, rc=rc, report_speech="")
    if os.path.exists(os.path.join(sdir, ".recap_done")):
        print("Rebuild complete.")
        if rc is not None:
            rc(status="ready", note=f"rebuilt {name}")
    elif os.path.exists(os.path.join(sdir, ".recap_skipped")):
        print(f"Rebuild skipped: {FFMPEG_SKIP_NOTE}")
        if rc is not None:
            rc(status="error", note=f"rebuild skipped: {FFMPEG_SKIP_NOTE}")
    else:
        why = ""
        try:
            with open(os.path.join(sdir, ".recap_failed"), encoding="utf-8") as f:
                why = " — " + "; ".join(f.read().splitlines()[1:])
        except OSError:
            pass
        print(f"Rebuild still had failures{why or ' — see .recap_failed'}")
        if rc is not None:
            rc(status="error", note=f"rebuild incomplete{why}")


def main():
    p = argparse.ArgumentParser(description="Marathon Auto Kill Recorder")
    p.add_argument("--config", default=CONFIG_PATH)
    p.add_argument("--dry-run", action="store_true",
                   help="live capture + detection, but log OBS actions instead of doing them")
    p.add_argument("--test-image", metavar="PATH",
                   help="OCR a saved screenshot and report detected kills (no OBS, no loop)")
    p.add_argument("--no-crop", action="store_true",
                   help="with --test-image: read the whole image instead of "
                        "cropping a full screenshot to the detect region")
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
    p.add_argument("--rebuild", nargs="?", const="latest", metavar="SESSION",
                   help="rebuild the reels/montage/Shorts for a past session "
                        "from the clips still on disk. No argument = the most "
                        "recent session; or pass a folder name or full path. "
                        "Use this when a recap failed but the clips survived.")
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

    if args.rebuild:
        rebuild_session(cfg, args.rebuild)
    elif args.teach:
        import teach
        teach.run(cfg)
    elif args.test_lines:
        run_test_lines(cfg, args.test_lines)
    elif args.test_image:
        run_test_image(cfg, args.test_image, crop=not args.no_crop)
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
    gpu = None             # True / False when PyTorch could say, else None
    try:
        import torch
        gpu = bool(torch.cuda.is_available())
        if gpu:
            print(f"  GPU: {torch.cuda.get_device_name(0)} (OCR runs on GPU)")
        else:
            print("  GPU: none detected by PyTorch — OCR will run on CPU "
                  "(works, but catches fewer fast popups; see README)")
    except Exception:
        pass

    # The SAME thread cap and priority as live detection. Without it CPU torch
    # used every core here but only ocr_threads (2) live, so the verdict was
    # too optimistic on exactly the CPU/AMD machines that miss kills.
    _tune_performance(cfg)
    print(f"  OCR threads: {max(1, int(cfg.get('ocr_threads', 2)))} "
          "(same cap as live)")

    engine = OCREngine(cfg.get("ocr_engine", "easyocr"), cfg.get("ocr_upscale", 3), max_dim=cfg.get("ocr_max_dim", 800))
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
    for line in _bench_verdict(avg, worst, budget_ms, gpu):
        print(line)


BENCH_GPU_HINT = ("  NVIDIA card? Delete the .venv folder and run START again - "
                  "setup installs GPU PyTorch when it finds nvidia-smi.")


def _bench_verdict(avg, worst, budget_ms, gpu) -> list:
    """The benchmark's verdict lines. The GPU advice appears only when PyTorch
    says there is no CUDA (it used to point at QUICKSTART steps that don't
    exist, and printed even when the GPU was already in use)."""
    if worst < budget_ms * 0.7:
        return ["  VERDICT: plenty of headroom — this machine handles it easily."]
    if avg < budget_ms:
        lines = ["  VERDICT: workable. If kills get missed, set poll_fps: 3 "
                 "and ocr_upscale: 2 in config.yaml."]
    else:
        lines = ["  VERDICT: too slow at current settings. Set poll_fps: 3 and "
                 "ocr_upscale: 2 in config.yaml."]
    if gpu is False:
        lines.append(BENCH_GPU_HINT)
    return lines


if __name__ == "__main__":
    main()
