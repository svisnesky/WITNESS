"""A tiny built-in web dashboard so a phone/iPad/second screen can watch the
kill feed live over the local network. No dependencies (stdlib http.server).

Open <PC-IP>:<port> in the device's browser (same Wi-Fi). The page polls
/status once a second; the recorder updates a shared LiveState as kills happen.
"""

import ipaddress
import json
import os
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# Dashboard-editable settings: key -> (default, type). All of these are read
# at use-time in the main loop, so changes apply live — no restart needed.
SETTINGS = {
    "play_sound": (False, bool),            # PC-side beep on kills
    "show_overlays": (True, bool),          # master switch for all on-screen flashes
    "overlay_multikill": (True, bool),      # DOUBLE/TRIPLE KILL banner
    "overlay_clip_saved": (True, bool),     # CLIP SAVED chip
    "overlay_reel_ready": (True, bool),     # HIGHLIGHTS READY chip
    "announcer_medals": (True, bool),       # voiced "Double kill!" call-outs
    "team_wipe": (True, bool),              # TEAM WIPE banner + call-out
    "kill_coalesce_seconds": (8.0, float),  # group kills into one clip window
    "make_match_reels": (True, bool),
    "reel_music": (True, bool),
    "reel_music_volume": (0.08, float),     # 0-1 loudness of the reel music bed
    "reel_announcer": (True, bool),
    "make_shorts": (True, bool),
    "shorts_labels": (True, bool),
    "make_montage": (True, bool),
    "make_card": (True, bool),
    "capture_exfil_stats": (True, bool),
    "track_names": (True, bool),            # read gamertags off the kill feed
    "auto_sweat": (True, bool),             # last-one-standing = flair mutes
    "splash_sound": (True, bool),           # boot-splash boom (Windows)
    "heat_streaks": (True, bool),           # killstreak flair (master)
    "heat_overlays": (True, bool),          # streak toasts on screen
    "heat_callouts": (True, bool),          # streak call-outs (voice)
    "witness_report": (True, bool),         # end-of-night WITNESS Report dossier
    # The montage is the one Stan actually wants uploaded ("mainly the end
    # montage") and it had no toggle at all — he had to edit config.yaml, which
    # he couldn't find. Anything he'll realistically want to flip belongs here.
    "youtube_upload_montage": (False, bool),       # auto-upload session montage
    "youtube_upload_session_reel": (False, bool),  # auto-upload session reel
    "youtube_upload_match_reels": (False, bool),   # auto-upload each match reel
    "youtube_upload_shorts": (False, bool),        # auto-upload each Short
}

# One-tap MODES: named bundles applied over the toggles above (through the
# same validated apply path). A mode is a starting point, not a lock — every
# setting stays individually adjustable afterward.
MODES = [
    {"key": "sweat", "label": "SWEAT",
     "desc": "Clips only. Nothing on screen, nothing in your ears, zero "
             "mid-match rendering. Session reel still builds when you stop.",
     "set": {"show_overlays": False, "overlay_multikill": False,
             "overlay_clip_saved": False, "overlay_reel_ready": False,
             "announcer_medals": False, "team_wipe": False,
             "play_sound": False, "make_match_reels": False,
             "make_shorts": False, "make_montage": False, "make_card": False,
             "track_names": False, "reel_announcer": False,
             "heat_overlays": False, "heat_callouts": False}},
    {"key": "standard", "label": "STANDARD",
     "desc": "The shipped defaults: clips, reels, stats, name tracking, "
             "quiet visual chips. No voices.",
     "set": {"show_overlays": True, "overlay_multikill": True,
             "overlay_clip_saved": True, "overlay_reel_ready": True,
             "announcer_medals": False, "team_wipe": True,
             "play_sound": False, "make_match_reels": True,
             "reel_music": True, "reel_announcer": True,
             "make_shorts": False, "make_montage": False, "make_card": True,
             "capture_exfil_stats": True, "track_names": True,
             "heat_overlays": True, "heat_callouts": False}},
    {"key": "showtime", "label": "SHOWTIME",
     "desc": "The full broadcast: medal voices, banners, team wipes, "
             "announced reels. Best with friends watching the dashboard.",
     "set": {"show_overlays": True, "overlay_multikill": True,
             "overlay_clip_saved": True, "overlay_reel_ready": True,
             "announcer_medals": True, "team_wipe": True,
             "play_sound": False, "make_match_reels": True,
             "reel_music": True, "reel_announcer": True,
             "make_shorts": False, "make_montage": False, "make_card": True,
             "capture_exfil_stats": True, "track_names": True,
             "heat_overlays": True, "heat_callouts": True}},
    {"key": "creator", "label": "CREATOR",
     "desc": "Showtime plus every render: vertical Shorts, montage, match "
             "cards. Maximum footage out the other end.",
     "set": {"show_overlays": True, "overlay_multikill": True,
             "overlay_clip_saved": True, "overlay_reel_ready": True,
             "announcer_medals": True, "team_wipe": True,
             "play_sound": False, "make_match_reels": True,
             "reel_music": True, "reel_announcer": True,
             "make_shorts": True, "shorts_labels": True,
             "make_montage": True, "make_card": True,
             "capture_exfil_stats": True, "track_names": True,
             "heat_overlays": True, "heat_callouts": True}},
]


# Theme: per-game look. Game profiles (games/<game>.yaml) may carry a
# theme: block; these are Marathon's colors and the fallback for any key a
# profile doesn't set. Values are swapped into the page CSS at request time.
THEME_DEFAULTS = {
    "bg": "#0e0e16", "panel": "#12181d", "line": "#232d34",
    "text": "#e9e9ed", "muted": "#8a90a0",
    "accent": "#9184d9", "danger": "#ff4d3d",
}
_HEX = None  # compiled lazily


def apply_theme(html: str, cfg) -> str:
    """Swap the default palette + branding for the active game's theme.
    No theme block (Marathon) = untouched. Non-hex values are ignored so a
    typo'd profile can't inject anything into the page."""
    import re
    global _HEX
    th = (cfg or {}).get("theme") or {}
    if not th:
        return html
    if _HEX is None:
        _HEX = re.compile(r"#[0-9a-fA-F]{3,8}\Z")
    for key, default in THEME_DEFAULTS.items():
        v = str(th.get(key) or "").strip()
        if v and v != default and _HEX.match(v):
            html = html.replace(default, v)
    name = re.sub(r"[^A-Za-z0-9 :'\-]", "", str(th.get("display_name") or "")).strip()
    if name and name.upper() != "MARATHON":
        html = html.replace("<title>Marathon ", f"<title>{name} ")
        html = html.replace(
            '<img src="/wordmark.png" alt="MARATHON">',
            f'<span style="font-weight:800;letter-spacing:.14em;'
            f'color:var(--accent);font-size:1.05rem;">{name.upper()}</span>')
    return html


# Human labels for the settings panel, in display order.
SETTINGS_META = [
    ("show_overlays", "On-screen flashes (master)"),
    ("overlay_multikill", "DOUBLE KILL banner"),
    ("overlay_clip_saved", "CLIP SAVED chip"),
    ("overlay_reel_ready", "HIGHLIGHTS READY chip"),
    ("announcer_medals", "Medal call-outs (voice)"),
    ("team_wipe", "TEAM WIPE banner + voice"),
    ("kill_coalesce_seconds", "Group kills within (seconds)"),
    ("make_match_reels", "Match highlight reels"),
    ("reel_music", "Reel music bed"),
    ("reel_music_volume", "Reel music volume (0-1)"),
    ("reel_announcer", "Reel announcer version"),
    ("make_shorts", "Vertical Shorts renders"),
    ("shorts_labels", "Shorts kill labels"),
    ("make_montage", "Session montage"),
    ("make_card", "Session match card"),
    ("capture_exfil_stats", "Exfil stats capture"),
    ("track_names", "Name tracking (kill feed)"),
    ("auto_sweat", "Auto-sweat (clutch focus)"),
    ("splash_sound", "Boot splash sound"),
    ("heat_streaks", "Killstreak flair (master)"),
    ("heat_overlays", "Streak toasts on screen"),
    ("heat_callouts", "Streak call-outs (voice)"),
    ("witness_report", "End-of-night WITNESS Report"),
    ("youtube_upload_montage", "YouTube: session montage"),
    ("youtube_upload_session_reel", "YouTube: session reel"),
    ("youtube_upload_match_reels", "YouTube: match reels"),
    ("youtube_upload_shorts", "YouTube: Shorts (quota-heavy)"),
    ("play_sound", "PC beep on kill"),
]


class LiveState:
    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.count = 0
        self.started = ""
        self._mono = None
        self.events = deque(maxlen=30)
        self._clip_requested = False
        self._kill_requested = False
        self.reels = []  # [{label, path}] — per-match highlight reels
        self.replays = []  # [{id, label, path, time}] — per-kill instant replays
        self._replay_seq = 0
        self.tag_counts = {}  # kill-type breakdown for the dashboard tiles
        self.update_msg = ""  # auto-updater status, shown in the dashboard
        self.version = ""     # build label '0.1.0 (1a2b3c4)', bottom-right + /api/info
        self.build_sha = ""   # bare short sha, for the Bonjour TXT (see read_build_sha)
        self.detect = {}      # live detection health (fps/device/vram), from perf monitor
        self.recap = {}       # end-of-session build status ({status, note, report})
        self.heat = {}        # live killstreak ({streak, label, color})
        # Active problems the user must see: {key: {level, title, message, since}}.
        # First-class state rather than text inside the recap banner, so the iOS
        # app can raise a real alert (and clear it) instead of parsing a sentence.
        self.alerts = {}
        # Archive 'Upload to YouTube' jobs: {'<session>/<rel>': {state, url, error}}
        self.uploads = {}
        # Why the last START failed ('' = it didn't), and whether a START is in
        # progress. The desktop launcher (gui._WebSession) sets both as plain
        # attributes. Before these, an OBS mistake on START left the button on
        # STARTING… for 12 s, then back to START, with the reason nowhere a
        # window-app user could see it (pythonw has no console).
        self.last_error = ""
        self.starting = False
        # 'ip:port' addresses a phone/iPad on the same Wi-Fi can open, best
        # first. Filled once by start_web when it serves the LAN. No scheme:
        # the page adds it.
        self.lan_urls = []
        self._cfg = None       # live config dict (bound per session)
        self._save_cb = None   # persists changed settings to disk

    def set_alert(self, key: str, level: str, title: str, message: str = ""):
        """Raise (or refresh) an alert. level: 'critical' | 'warning' | 'info'.
        Keeps the original `since` on a refresh so the app doesn't re-notify
        every time the same problem is re-detected."""
        with self._lock:
            prev = self.alerts.get(key) or {}
            self.alerts[key] = {"key": key, "level": level, "title": title,
                                "message": message,
                                "since": prev.get("since") or time.time()}

    def clear_alert(self, key: str):
        with self._lock:
            self.alerts.pop(key, None)

    def reset(self):
        """Clear counts/feed for a fresh session (server stays up)."""
        with self._lock:
            self.count = 0
            self.events.clear()
            self._clip_requested = False
            self._kill_requested = False
            self.reels.clear()
            self.replays.clear()
            self.tag_counts = {}
            self.heat = {}
            # a new session re-runs its own checks; an alert from the last
            # one (e.g. black capture) must not outlive it
            self.alerts = {}

    def request_kill(self):
        with self._lock:
            self._kill_requested = True

    def pop_kill_request(self) -> bool:
        with self._lock:
            if getattr(self, "_kill_requested", False):
                self._kill_requested = False
                return True
            return False

    def request_update(self, base_dir: str) -> dict:
        """Check GitHub and apply an update, in the background.

        REFUSES while a session is running or a recap/rebuild is building. Stan
        lost two recaps to updating at the wrong moment — the render was killed
        mid-flight — so this will not touch the files under a live session.

        Deliberately does NOT relaunch: update_and_relaunch_if_needed() replaces
        the process, which from inside the webserver would kill the very session
        the user is watching. Files are staged and the UI says to restart."""
        import threading
        with self._lock:
            if self.running:
                return {"ok": False,
                        "error": "stop the session first — updating mid-session "
                                 "can kill a recap render"}
            if (self.recap or {}).get("status") == "building":
                return {"ok": False,
                        "error": "a recap is still building — let it finish"}
            if getattr(self, "_updating", False):
                return {"ok": False, "error": "already checking"}
            self._updating = True

        def work():
            try:
                import updater
                changed, msg = updater.check_and_update(base_dir)
                with self._lock:
                    self.update_msg = msg
                    self.update_changed = bool(changed)
                print(f"  [update] {msg}")
            except Exception as e:
                with self._lock:
                    self.update_msg = f"update failed: {e}"
                print(f"  [update] failed: {e}")
            finally:
                with self._lock:
                    self._updating = False

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def request_export(self, base_dir: str, session: str, selection,
                       opts=None) -> dict:
        """Render a hand-picked reel in the BACKGROUND.

        Same shape as request_rebuild: returns immediately, reports progress on
        the recap banner, because a render takes minutes and the page's fetch
        would time out. Refuses while a session is running — an export competes
        for the same ffmpeg and encoder as live match reels."""
        import threading
        with self._lock:
            if self.running:
                return {"ok": False,
                        "error": "stop the session first — exporting while "
                                 "recording competes with the live reels"}
            if getattr(self, "_exporting", False):
                return {"ok": False, "error": "an export is already running"}
            self._exporting = True
        cfg = self._cfg or {}

        def rc(**kw):            # tagged, so the Live page words it as an export
            kw.setdefault("kind", "export")
            self.set_recap(**kw)

        def work():
            try:
                import main
                sdir = main._resolve_session_dir(cfg, session)
                if not sdir:
                    rc(status="error", note="couldn't find that session folder")
                    return
                import export_reel
                rc(status="building", note="starting export…")
                out = export_reel.build(sdir, selection, opts=opts, cfg=cfg,
                                        on_note=rc)
                if out:
                    rc(status="ready", note=f"exported {os.path.basename(out)}")
                else:
                    rc(status="error", note="export failed")
            except Exception as e:
                print(f"  [export] failed: {e}")
                rc(status="error", note=f"export failed: {e}")
            finally:
                with self._lock:
                    self._exporting = False

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def request_rebuild(self, session: str = "latest") -> dict:
        """Kick off a session rebuild in the BACKGROUND and return immediately.

        Rendering takes minutes, and the dashboard poll would time out waiting —
        so this reports 'started' and progress shows up on the recap banner the
        same way an end-of-session build does.

        REFUSES while a session is running, like request_export: a rebuild runs
        the whole ffmpeg pipeline during gameplay and ends by tidying the folder,
        which moves the live session's clips out from under its match reels.
        Deliberately NOT refused on recap status 'building': a resume that
        raised leaves 'building' set forever, and this button is the way out."""
        import threading
        with self._lock:
            if self.running:
                return {"ok": False,
                        "error": "stop the session first — rebuilding while "
                                 "recording competes with the live reels"}
            if getattr(self, "_rebuilding", False):
                return {"ok": False, "error": "a rebuild is already running"}
            self._rebuilding = True
        cfg = self._cfg or {}

        def rc(**kw):            # tagged, so the Live page words it as a rebuild
            kw.setdefault("kind", "rebuild")
            self.set_recap(**kw)

        def work():
            try:
                import main
                # set_recap takes KEYWORDS, not a dict.
                rc(status="building", note="starting rebuild…")
                # Pass the callback through so each stage ("building session
                # montage…", "cutting Play of the Night…", "rendering vertical
                # Shorts…") reaches the UI. Without this the status sat on one
                # message for the several minutes a render takes, which is
                # indistinguishable from being hung.
                main.rebuild_session(cfg, session, rc=rc)
                # rebuild_session sets the final status itself on every path
                # ('ready' / 'error' with the failed stages). This used to set
                # 'ready — rebuild complete' unconditionally right after, which
                # overwrote every failure: the Archive said "Done — reloading"
                # over a rebuild that had built nothing.
                with self._lock:
                    hung = self.recap.get("status") == "building"
                if hung:             # never left without an answer
                    rc(status="error", note="rebuild ended without reporting a "
                                            "result — see the session log")
            except Exception as e:
                print(f"  [rebuild] failed: {e}")
                rc(status="error", note=f"rebuild failed: {e}")
            finally:
                with self._lock:
                    self._rebuilding = False

        threading.Thread(target=work, daemon=True).start()
        # the web route passes a full path; report just the session's name
        return {"ok": True,
                "started": os.path.basename(str(session).rstrip("/\\")) or session}

    def add_replay(self, label, path):
        with self._lock:
            self._replay_seq += 1
            self.replays.append({"id": self._replay_seq, "label": label,
                                 "path": path, "time": time.strftime("%H:%M:%S")})
            if len(self.replays) > 20:
                self.replays.pop(0)

    def get_replay_path(self, rid):
        with self._lock:
            for r in self.replays:
                if r["id"] == rid:
                    return r["path"]
            return None

    def bind_config(self, cfg, save_cb):
        """Attach the live session config so the dashboard can read/change it."""
        with self._lock:
            self._cfg = cfg
            self._save_cb = save_cb

    def bind_control(self, start_fn, stop_fn):
        """Let the web UI start/stop the detection session (webview desktop)."""
        with self._lock:
            self._start_fn = start_fn
            self._stop_fn = stop_fn

    def fire_start(self):
        f = getattr(self, "_start_fn", None)
        if f:
            try:
                f()
            except Exception as e:
                print(f"  [control] start failed: {e}")

    def fire_stop(self):
        f = getattr(self, "_stop_fn", None)
        if f:
            try:
                f()
            except Exception as e:
                print(f"  [control] stop failed: {e}")

    def get_settings(self):
        with self._lock:
            if self._cfg is None:
                return {}
            return {k: self._cfg.get(k, d) for k, (d, _) in SETTINGS.items()}

    def apply_settings(self, changes: dict):
        """Validate + apply dashboard-changed settings to the live config and
        persist them. Returns the settings dict after applying."""
        with self._lock:
            if self._cfg is None:
                return {}
            clean = {}
            for k, v in changes.items():
                if k not in SETTINGS:
                    continue
                _, typ = SETTINGS[k]
                try:
                    clean[k] = bool(v) if typ is bool else max(0.0, float(v))
                except (TypeError, ValueError):
                    continue
            self._cfg.update(clean)
            if clean and self._save_cb is not None:
                try:
                    self._save_cb(clean)
                except Exception as e:
                    print(f"  [settings] could not persist: {e}")
        return self.get_settings()

    def add_reel(self, label, path):
        with self._lock:
            self.reels.append({"label": label, "path": path,
                               "time": time.strftime("%H:%M")})

    def get_reel_path(self, idx):
        with self._lock:
            if 0 <= idx < len(self.reels):
                return self.reels[idx]["path"]
            return None

    def set_detect(self, info: dict):
        """Latest detection health from the perf monitor (fps/device/vram)."""
        with self._lock:
            self.detect = dict(info or {})

    def set_heat(self, info: dict):
        """Live killstreak state for the dashboard chip ({} = no streak)."""
        with self._lock:
            self.heat = dict(info or {})

    def set_recap(self, **kw):
        """End-of-session build status, so the dashboard can show 'building…' /
        'ready' / 'error' and the WITNESS Report instead of relying on a browser
        popup.

        `kind` says who wrote it — 'recap' (the end-of-session build or the
        launch resume: the default), 'rebuild' or 'export' — so the Live page
        can word a failure correctly. Every write sets it, so an old export's
        tag can't stick to the next night's recap. Status values are unchanged:
        the Archive and Clips pollers stop on 'ready' / 'error'."""
        kw.setdefault("kind", "recap")
        with self._lock:
            self.recap.update(kw)

    def set_running(self, running):
        with self._lock:
            self.running = running
            if running:
                self.started = time.strftime("%H:%M")
                self._mono = time.monotonic()
                # it started: the last failure is history, and so is "starting"
                self.last_error = ""
                self.starting = False

    def record(self, count, tag, text):
        with self._lock:
            self.count = count
            self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1
            self.events.appendleft(
                {"time": time.strftime("%H:%M:%S"), "tag": tag, "text": (text or "")[:60]})

    def annotate_last_kill(self, name: str):
        """Append the victim's gamertag to the newest kill row in the feed
        ('RUNNER DOWN +15 XP  ·  KRYPT KING05'). The name arrives a beat after
        the kill (separate feed OCR pass), so it's stitched on afterwards."""
        if not name:
            return
        with self._lock:
            for e in self.events:                     # newest first
                if e.get("tag") in ("down", "precision", "finisher", "kill"):
                    if "·" not in e.get("text", ""):
                        e["text"] = f"{e['text']}  ·  {name}"[:80]
                    return

    def notice(self, text, tag="alert"):
        """A feed-only line (streamer alerts etc.) — shows in the kill feed
        without touching the kill count or the tag tiles."""
        with self._lock:
            self.events.appendleft(
                {"time": time.strftime("%H:%M:%S"), "tag": tag, "text": (text or "")[:60]})

    def request_clip(self):
        with self._lock:
            self._clip_requested = True

    def pop_clip_request(self) -> bool:
        with self._lock:
            if self._clip_requested:
                self._clip_requested = False
                return True
            return False

    def snapshot(self):
        with self._lock:
            elapsed = 0
            if self.running and self._mono is not None:
                elapsed = int(time.monotonic() - self._mono)
            return {"running": self.running, "count": self.count,
                    "started": self.started, "elapsed": elapsed,
                    "update": self.update_msg, "version": self.version,
                    "update_changed": getattr(self, "update_changed", False),
                    "updating": getattr(self, "_updating", False),
                    # getattr: the launcher assigns these from outside
                    "last_error": str(getattr(self, "last_error", "") or ""),
                    "starting": bool(getattr(self, "starting", False)),
                    "lan_urls": list(getattr(self, "lan_urls", None) or []),
                    "detect": dict(self.detect),
                    "recap": dict(self.recap),
                    "heat": dict(self.heat),
                    "alerts": sorted(self.alerts.values(),
                                     key=lambda a: a.get("since", 0)),
                    "tags": dict(self.tag_counts),
                    "downs": _tile_downs(self.tag_counts),
                    "events": list(self.events),
                    "reels": [{"i": i, "label": r["label"], "time": r["time"]}
                              for i, r in enumerate(self.reels)],
                    "replays": [{"i": r["id"], "label": r["label"], "time": r["time"]}
                                for r in reversed(self.replays)]}


def _tile_downs(tag_counts) -> int:
    """The DOWNS tile: exfil_stats.count_events' downs, the number the desktop
    tiles and the recap show. It counts a +1 KILL press ('manual_kill') as a
    down; the raw 'down' tag count did not, so the +1 button on this very page
    never moved its own DOWNS tile. Downs do not depend on event order, so the
    per-tag counts are enough."""
    counts = dict(tag_counts or {})
    try:
        import exfil_stats
        tags = [t for t, n in counts.items() for _ in range(int(n or 0))]
        return int(exfil_stats.count_events(tags)[0])
    except Exception:
        return int(counts.get("down", 0) or 0) + int(counts.get("manual_kill", 0) or 0)


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


import re as _re
import urllib.parse as _uparse

_RANGE = _re.compile(r"bytes=([0-9]*)-([0-9]*)")
_SAFE_PART = _re.compile(r"^[A-Za-z0-9 ._+\-]+$")


API_VERSION = 1


def _record_dir(state) -> str:
    """Where OBS writes clips. state.record_dir is only set once a session has
    started this run; before that, fall back to the folder cached from the last
    run so the Archive, the Clip viewer and the iOS app work right after launch."""
    rec = getattr(state, "record_dir", "") or ""
    if rec:
        return rec
    try:
        import main
        return main.cached_record_dir() or ""
    except Exception:
        return ""


_SESSIONS_DIR = "Marathon Sessions"


def _web_session_dir(state, which) -> str:
    """The session folder a DASHBOARD request names, or '' if it names anything
    else.

    main._resolve_session_dir is built for the --rebuild CLI, which accepts a
    full path, so '/Users/me/Pictures' or '../..' in a request used to be
    tidied (images moved into exfil/, videos into clips/), rendered into and
    thumbnailed. A web request may only name a session: 'latest', or one bare
    folder name directly inside a 'Marathon Sessions' folder. The name is
    checked here AND passed with strict=True, which refuses a full path, so
    either check alone still holds.

    Finding the root is left to main._resolve_session_dir (cached record dir,
    then asking OBS) so the Archive and the clip viewer keep working right after
    launch; this only adds the name check and the containment check."""
    which = str(which or "").strip()
    try:
        import main
    except Exception:
        return ""
    cfg = getattr(state, "_cfg", None) or {}
    nc = os.path.normcase
    if which in ("", "latest"):
        sdir = main._resolve_session_dir(cfg, "latest", strict=True)
        return os.path.abspath(sdir) if sdir and os.path.isdir(sdir) else ""
    if not _SAFE_PART.match(which) or which in (".", ".."):
        return ""
    sdir = main._resolve_session_dir(cfg, which, strict=True)
    # Relative = it came from the CLI's "existing folder" branch, i.e. a folder
    # in the current directory that merely shares the name. A real session is
    # always <record dir>/Marathon Sessions/<name>, an absolute path.
    if not sdir or not os.path.isabs(sdir) or not os.path.isdir(sdir):
        return ""
    parent = os.path.dirname(sdir)
    if nc(os.path.basename(parent)) != nc(_SESSIONS_DIR):
        return ""
    # Must sit DIRECTLY in that folder once links are resolved, under the very
    # name asked for. Comparing basenames (not full paths) keeps a mapped drive
    # that realpath() turns into a UNC path working; '...' or 'x.' (which
    # Windows collapses onto another folder) fail the name match.
    real = os.path.realpath(sdir)
    if (nc(os.path.dirname(real)) != nc(os.path.realpath(parent))
            or nc(os.path.basename(real)) != nc(which)):
        return ""
    return sdir


def _api_info(state) -> dict:
    """Identity for the iOS app: proves this really is WITNESS, and which
    API shape it speaks, before the app trusts anything else it says."""
    return {"app": "witness", "api": API_VERSION,
            "version": getattr(state, "version", "") or "",
            "name": socket.gethostname()}


def _media_url(session: str, rel: str) -> str:
    """Playable on Apple devices: /api/play remuxes .mkv to .mp4 on demand."""
    return f"/api/play?s={_uparse.quote(session)}&f={_uparse.quote(rel)}"


def _api_sessions(state, limit: int = 60) -> dict:
    """Sessions for the iOS app — the same scan the Archive page uses."""
    rec = _record_dir(state)
    base = os.path.dirname(os.path.abspath(__file__))
    out = []
    for sess in _scan_sessions(base, rec, limit=limit):
        out.append({
            "id": sess["id"],
            "clips": sess["clips"],
            "size": sess["size"],
            "size_label": _fmt_size(sess["size"]),
            "failed": sess.get("failed", False),
            "recaps": sess["recaps"],
            "has_report": bool(sess.get("report")),
            "media": [{"label": lbl, "rel": rel, "kind": _media_kind(rel),
                       "url": _media_url(sess["id"], rel)}
                      for lbl, rel in sess["media"]],
        })
    return {"record_dir_known": bool(rec), "sessions": out}


def _api_session(state, session: str) -> dict:
    """One session for the iOS app: its media plus every clip with what
    happened inside it (the clip viewer's data)."""
    data = _clip_data(state, session)
    sid = data.get("session") or session
    for c in data.get("clips", []):
        c["url"] = _media_url(sid, c["rel"])
        c["thumb"] = (f"/clipthumb?s={_uparse.quote(sid)}"
                      f"&f={_uparse.quote(c['rel'])}")
    listing = _api_sessions(state).get("sessions", [])
    match = next((x for x in listing if x["id"] == sid), None)
    data["media"] = match["media"] if match else []
    data["report"] = ""
    if match and match.get("has_report"):
        try:
            sdir = _web_session_dir(state, sid)
            with open(os.path.join(sdir, "witness_report.txt"), encoding="utf-8") as f:
                data["report"] = f.read()
        except Exception:
            data["report"] = ""
    return data


def _clip_data(state, session: str) -> dict:
    """Everything the clip viewer needs for one session: which session it is, and
    every clip with what happened inside it.

    Durations come from ffprobe, which is a subprocess per clip — fine for the
    ~20 clips a session holds, and the result is what lets the page place event
    markers on the scrub bar."""
    try:
        import clip_index
        import montage
        cfg = state._cfg or {}
        sdir = _web_session_dir(state, session or "latest")
        if not sdir:
            if str(session or "latest") != "latest":
                return {"error": "couldn't find that session folder", "clips": []}
            return {"error": "no session folder found — press START once so the "
                             "app learns your OBS folder", "clips": []}
        base = os.path.dirname(os.path.abspath(__file__))
        ff = montage.find_ffmpeg(base, cfg)
        clips = clip_index.build_index(sdir, ff, with_duration=True)
        return {"session": os.path.basename(sdir.rstrip(os.sep)),
                "count": len(clips), "clips": clips}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "clips": []}


def _clip_thumb(state, session: str, rel: str) -> str:
    """Path to this clip's cached thumbnail, building it on first request.
    Returns '' for anything that isn't a real clip inside the session."""
    try:
        import export_reel
        import montage
        cfg = state._cfg or {}
        # The session is checked too, not just rel: even a GET writes a .thumbs
        # folder into whatever directory it resolves to.
        sdir = _web_session_dir(state, session or "latest")
        if not sdir or not rel or ".." in rel.replace("\\", "/").split("/"):
            return ""
        root = os.path.realpath(sdir)
        full = os.path.realpath(os.path.join(sdir, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return ""
        # Grab the frame at the first kill when known — a frame from the action
        # beats one from the approach, which on a 33s buffer save is a corridor.
        at = None
        try:
            import clip_index
            import match_reel
            saved, kills = match_reel._load_sidecar(full)
            dur = match_reel.probe_duration(full, montage.find_ffmpeg(
                os.path.dirname(os.path.abspath(__file__)), cfg))
            ev = clip_index.clip_events(dur, saved, kills)
            if ev:
                at = ev[0]["t"]
        except Exception:
            at = None
        return export_reel.ensure_thumb(
            sdir, rel, full,
            montage.find_ffmpeg(os.path.dirname(os.path.abspath(__file__)), cfg),
            at_seconds=at)
    except Exception:
        return ""


def _archive_video_path(record_dir: str, sess: str, rel: str):
    """Resolve a session media file SAFELY. sess is a session folder name and
    rel a file inside it (optionally one level deep: 'reels/match_1.mp4').
    Anything that isn't a plain name, or escapes the session dir, -> None."""
    if not record_dir or not _SAFE_PART.match(sess or ""):
        return None
    parts = (rel or "").split("/")
    if not (1 <= len(parts) <= 2) or not all(_SAFE_PART.match(p) for p in parts):
        return None
    root = os.path.realpath(os.path.join(record_dir, "Marathon Sessions"))
    path = os.path.realpath(os.path.join(root, sess, *parts))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    if not path.lower().endswith((".mp4", ".mkv", ".png", ".mp3", ".wav")):
        return None
    return path


YT_RECORD = "youtube_uploads.json"     # per session: {rel: watch url}


def _yt_uploaded(sdir: str) -> dict:
    """{rel: url} of this session's media already on YouTube."""
    try:
        with open(os.path.join(sdir, YT_RECORD), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _yt_remember(sdir: str, rel: str, url: str) -> None:
    d = _yt_uploaded(sdir)
    d[rel] = url
    tmp = os.path.join(sdir, YT_RECORD + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, os.path.join(sdir, YT_RECORD))


def _yt_kind(rel: str) -> str:
    """What to call this video in its YouTube title ({kind} in the template)."""
    base = os.path.basename(rel).lower()
    if base == "session_reel.mp4":
        return "Session reel"
    if base.startswith("highlights"):
        return "Session montage"
    if "play_of_the_night" in base:
        return "Play of the night"
    if rel.startswith("shorts/"):
        return "Short"
    if base.startswith("match_"):
        return "Match " + base.split("_", 1)[1].split(".")[0].split("_")[0]
    return "Highlight"


def _yt_status(state, sid: str, sdir: str) -> dict:
    """{rel: {state, url, error}} for one session: finished uploads from the
    session's record, plus anything uploading or failed this run."""
    out = {rel: {"state": "done", "url": url, "error": ""}
           for rel, url in _yt_uploaded(sdir).items()}
    with state._lock:
        for key, job in state.uploads.items():
            js, _, rel = key.partition("/")
            if js == sid and out.get(rel, {}).get("state") != "done":
                out[rel] = dict(job)
    return out


def start_youtube_upload(state, base_dir: str, sid: str, rel: str) -> dict:
    """Upload one Archive video to YouTube in the background.

    A button, not automatic: Stan asked for it on 2026-10-03 ("could we add an
    upload to youtube button?") so a night worth sharing can go up on demand,
    for any session, not just via the end-of-session upload flags. Uses the
    same OAuth setup, privacy setting and title template as those flags. A
    finished upload is recorded in the session folder (youtube_uploads.json)
    and the button turns into a link, so nothing is uploaded twice by
    accident."""
    rec = _record_dir(state)
    path = _archive_video_path(rec, sid, rel)
    if not path or path.lower().endswith((".png", ".mp3", ".wav")):
        return {"ok": False, "error": "that video isn't in this session"}
    sdir = os.path.dirname(path) if "/" not in rel else os.path.dirname(os.path.dirname(path))
    done = _yt_uploaded(sdir).get(rel)
    if done:
        return {"ok": True, "state": "done", "url": done}
    key = f"{sid}/{rel}"
    with state._lock:
        job = state.uploads.get(key)
        if job and job.get("state") == "uploading":
            return {"ok": True, "state": "uploading"}
        state.uploads[key] = {"state": "uploading", "url": "", "error": ""}
    cfg = dict(state._cfg or {})

    def work():
        why, url = [], None
        try:
            import main
            import youtube_upload
            try:
                import datetime
                day = datetime.datetime.strptime(sid[:10], "%Y-%m-%d").strftime("%b %d, %Y")
            except ValueError:
                day = None
            title = main._yt_title(cfg, _yt_kind(rel), date=day)
            desc = ("Recorded automatically with WITNESS, a free Marathon kill "
                    "recorder: github.com/svisnesky/WITNESS")
            url = youtube_upload.upload(path, title, desc, base_dir,
                                        privacy=cfg.get("youtube_privacy", "unlisted"),
                                        why=why)
            if url:
                _yt_remember(sdir, rel, url)
        except Exception as e:
            why.append(f"{type(e).__name__}: {e}")
        with state._lock:
            state.uploads[key] = ({"state": "done", "url": url, "error": ""} if url else
                                  {"state": "failed", "url": "",
                                   "error": (why[-1] if why else "upload failed")[:300]})

    threading.Thread(target=work, daemon=True, name="youtube-upload").start()
    return {"ok": True, "state": "uploading"}


def _session_recaps(base_dir: str):
    """Per-match recap lines for every session, from squad_stats.csv (your
    rows) -> {session_id: [line, ...]}."""
    import csv as _csv
    out = {}
    path = os.path.join(base_dir, "stats", "squad_stats.csv")
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            if r.get("is_you") != "1":
                continue
            def n(k):
                try:
                    return int(float(r.get(k) or 0))
                except ValueError:
                    return 0
            bits = [f"{n('runner_elims')} elim{'s' if n('runner_elims') != 1 else ''}",
                    f"{n('runners_downed')} down{'s' if n('runners_downed') != 1 else ''}",
                    f"{n('runner_damage'):,} dmg", f"{n('inventory_value'):,} loot"]
            if r.get("runner"):
                bits.append(f"on {r['runner']}")
            out.setdefault(r.get("session", ""), []).append(
                f"{r.get('time', '')[:5]} — " + " · ".join(bits))
    return out


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024.0
    return ""


def _media_kind(rel: str) -> str:
    """What kind of video/audio a session file is, for the app and the Archive."""
    low = rel.lower()
    name = os.path.basename(low)
    if name == "session_reel.mp4":
        return "session_reel"
    if name.startswith("play_of_the_night"):
        return "potn"
    if name.startswith("highlights"):
        return "montage"
    if low.startswith("shorts/"):
        return "short"
    if low.startswith("replays/"):
        return "replay"
    if name.endswith((".mp3", ".wav")):
        return "report_audio"
    if "_announced" in name:
        return "announced"
    return "reel"


def _scan_sessions(base_dir: str, record_dir: str, limit: int = 60) -> list:
    """Every session folder with its media, newest first. Shared by the Archive
    page and the iOS app's /api/sessions, so the two can never disagree.

    Clip counts come from tidy.iter_session_media, which looks in clips/ too.
    The Archive used to count only top-level files, so every session the tidy
    step had organized reported "0 clips"."""
    import tidy
    root = os.path.join(record_dir, "Marathon Sessions") if record_dir else ""
    sessions = []
    if not root or not os.path.isdir(root):
        return sessions
    recaps = _session_recaps(base_dir)
    for name in sorted(os.listdir(root), reverse=True)[:limit]:
        sdir = os.path.join(root, name)
        if not os.path.isdir(sdir) or not _SAFE_PART.match(name):
            continue
        media = []                       # [(label, rel)]
        sr = os.path.join(sdir, "session_reel.mp4")
        if os.path.exists(sr):
            media.append(("Session reel", "session_reel.mp4"))
        rdir = os.path.join(sdir, "reels")
        if os.path.isdir(rdir):
            for f in sorted(os.listdir(rdir)):
                if f.endswith(".mp4") and _SAFE_PART.match(f):
                    label = f.replace(".mp4", "").replace("_", " ").capitalize()
                    media.append((label, f"reels/{f}"))
        pdir = os.path.join(sdir, "replays")
        if os.path.isdir(pdir):
            for f in sorted(os.listdir(pdir)):
                if f.endswith(".mp4") and _SAFE_PART.match(f):
                    media.append((f.replace(".mp4", "").replace("_", " "), f"replays/{f}"))
        for f in sorted(os.listdir(sdir)):
            if f.startswith("highlights") and f.lower().endswith((".mkv", ".mp4")) \
                    and _SAFE_PART.match(f):
                media.append(("Session montage", f))
        shdir = os.path.join(sdir, "shorts")
        if os.path.isdir(shdir):
            for f in sorted(os.listdir(shdir)):
                if f.endswith(".mp4") and _SAFE_PART.match(f):
                    media.append((f"Short — {f.replace('.mp4','').replace('_',' ')}",
                                  f"shorts/{f}"))
        for cand in ("witness_report_tts.mp3", "witness_report_tts.wav"):
            if os.path.exists(os.path.join(sdir, cand)):
                media.append(("WITNESS Report (spoken)", cand))
                break
        report = ""
        rp = os.path.join(sdir, "witness_report.txt")
        if os.path.exists(rp):
            try:
                with open(rp, encoding="utf-8") as f:
                    report = f.read()
            except OSError:
                report = ""
        n_clips = sum(1 for _rel, _full in
                      tidy.iter_session_media(sdir, (".mkv", ".mp4")))
        sessions.append({"id": name, "media": media, "clips": n_clips,
                         "recaps": recaps.get(name, []), "report": report,
                         "size": _dir_size(sdir),
                         "failed": os.path.exists(os.path.join(sdir, ".recap_failed"))})
    return sessions


def _archive_page(base_dir: str, record_dir: str) -> str:
    sessions = _scan_sessions(base_dir, record_dir)

    blocks = []
    for s in sessions:
        date = s["id"].replace("_", " · ")
        rec_html = "".join(f'<div class="rc">{_esc(l)}</div>' for l in s["recaps"]) \
            or '<div class="rc none">no exfil stats recorded</div>'
        rows = ""
        on_yt = _yt_uploaded(os.path.join(record_dir, "Marathon Sessions", s["id"])) \
            if record_dir else {}
        for label, rel in s["media"]:
            u = f"/avideo?s={_uparse.quote(s['id'])}&f={_uparse.quote(rel)}"
            if rel.lower().endswith((".mp3", ".wav")):
                yt = ""                          # the spoken report isn't a video
            elif on_yt.get(rel):
                yt = (f'<a class="dl yt done" href="{_esc(on_yt[rel])}" '
                      f'target="_blank" rel="noopener">on YouTube &#8599;</a>')
            else:
                yt = (f'<button class="mini yt" data-s="{_esc(s["id"])}" '
                      f'data-f="{_esc(rel)}" onclick="ytUpload(this)">'
                      f'Upload to YouTube</button>')
            rows += (f'<div class="mrow"><span class="play">&#9658;</span>'
                     f'<a class="ml" href="{u}" target="_blank">{_esc(label)}</a>'
                     f'<a class="dl" href="{u}&dl=1">save</a>{yt}</div>')
        if not rows:
            rows = '<div class="rc none">no reels/replays kept for this session</div>'
        report_html = (f'<h4>WITNESS Report</h4><pre class="report">{_esc(s["report"])}</pre>'
                       if s.get("report") else "")
        # A session with clips but no reels had a failed recap. Say so, and offer
        # the fix right here — the clips are all that matter and they survived.
        # ANY rendered output counts (session reel, match reels, Shorts, the
        # montage); replays and the spoken report don't. Counting only some
        # kinds warned "the recap failed" at a night whose montage and Shorts
        # built fine because match reels were switched off.
        has_reels = any(rel == "session_reel.mp4"
                        or rel.startswith(("reels/", "shorts/", "highlights"))
                        for _l, rel in s["media"])
        warn = ("" if has_reels or not s["clips"] else
                '<div class="rc none" style="color:var(--warn,#f5a623)">'
                'This session has clips but no reels — the recap failed. '
                'Rebuild it below.</div>')
        # The clip viewer could only ever open the latest session; nothing led
        # to an older one. Each session with clips now links to its own.
        pick = ("" if not s["clips"] else
                f'<div class="mrow"><span class="play">&#9986;</span>'
                f'<a class="ml" href="/clips?s={_uparse.quote(s["id"])}">'
                f'Pick clips / cut a reel</a><span class="meta">choose, trim '
                f'and export your own reel from this session</span></div>')
        blocks.append(f"""<details><summary><b>{_esc(date)}</b>
          <span class="meta">{s['clips']} clip{'s' if s['clips'] != 1 else ''}
          &middot; {_fmt_size(s['size'])}</span></summary>
          <h4>Matches</h4>{rec_html}{warn}{report_html}
          <h4>Watch / share</h4>{rows}{pick}
          <div class="mrow"><button class="mini rb"
            onclick="rebuild('{_esc(s['id'])}',this)">Rebuild reels</button>
            <span class="meta">re-renders the montage, reels and Shorts from the
            clips on disk &mdash; takes a few minutes</span></div></details>""")

    body = "".join(blocks) or ('<p class="rc none">No sessions found yet'
                               + ("" if record_dir else " — press START once so the app learns your OBS folder")
                               + ".</p>")
    if sessions:
        total = sum(s["size"] for s in sessions)
        body += (f'<p class="sub" style="margin-top:14px">{len(sessions)} '
                 f'session{"s" if len(sessions) != 1 else ""} &middot; '
                 f'{_fmt_size(total)} on disk</p>')
    return ARCHIVE_PAGE.replace("%%BODY%%", body)


def _gamertag(cfg):
    """The gamertag the recorder will match, by the key-absent rule, or None
    when no config is bound (unknown: say nothing about it).

    A config with NO gamertag key predates the setting and keeps
    encounters.DEFAULT_GAMERTAG; an explicit blank (or YAML's bare
    `gamertag:`) means no tag. `cfg.get("gamertag") or DEFAULT` would turn a
    deliberate blank back into the default. encounters.resolve_gamertag
    (config-and-identity) is the one rule; the fallback here is the same rule
    for a checkout that doesn't have it yet."""
    if cfg is None:
        return None
    try:
        import encounters
    except Exception:
        encounters = None
    resolve = getattr(encounters, "resolve_gamertag", None)
    if resolve is not None:
        try:
            return str(resolve(cfg) or "").strip()
        except Exception:
            pass
    if "gamertag" not in cfg:
        return str(getattr(encounters, "DEFAULT_GAMERTAG", "") or "").strip()
    return str(cfg.get("gamertag") or "").strip()


def _stats_page(base_dir: str, cfg=None) -> str:
    """Career + economy + squad leaderboard, built from stats/squad_stats.csv
    (with match_stats.csv as the fallback for your own older rows).

    cfg is the bound live config: with a blank gamertag the Menace Report can
    never fill, so its empty state says how to turn it on rather than
    promising names "as downs happen"."""
    import csv as _csv

    rows = []
    sq = os.path.join(base_dir, "stats", "squad_stats.csv")
    if os.path.exists(sq):
        with open(sq, encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))

    def num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    # your rows: squad_stats where is_you, plus legacy match_stats.csv
    yours = [r for r in rows if r.get("is_you") == "1"]
    legacy = os.path.join(base_dir, "stats", "match_stats.csv")
    if os.path.exists(legacy):
        with open(legacy, encoding="utf-8") as f:
            for r in _csv.DictReader(f):
                r["is_you"] = "1"
                yours.append(r)

    def totals(rs):
        return {
            "matches": len(rs),
            "elims": sum(num(r.get("runner_elims")) for r in rs),
            "downs": sum(num(r.get("runners_downed")) for r in rs),
            "damage": sum(num(r.get("runner_damage")) for r in rs),
            "loot": sum(num(r.get("inventory_value")) for r in rs),
            "best_haul": max((num(r.get("inventory_value")) for r in rs), default=0),
        }

    me = totals(yours)

    # squad leaderboard: group by display name (before the #tag)
    by_player = {}
    for r in rows:
        name = (r.get("player") or "").split("#")[0] or ("You" if r.get("is_you") == "1" else "Unknown")
        by_player.setdefault(name, []).append(r)
    board = sorted(((n, totals(rs)) for n, rs in by_player.items()),
                   key=lambda kv: -kv[1]["loot"])

    # The three economy tiles (damage/loot/best haul) get the accent gradient.
    cards = "".join(
        f'<div class="tile{eco}"><div class="tn">{v:,}</div><div class="tl">{l}</div></div>'
        for l, v, eco in [("Matches", me["matches"], ""), ("Runner elims", me["elims"], ""),
                          ("Downs", me["downs"], ""), ("Runner damage", me["damage"], " eco"),
                          ("Loot extracted", me["loot"], " eco"), ("Best haul", me["best_haul"], " eco")])

    if board:
        head = "<tr><th></th><th>Player</th><th>Matches</th><th>Elims</th><th>Downs</th><th>Damage</th><th>Loot</th><th>Best haul</th></tr>"
        trs = ""
        for i, (name, t) in enumerate(board):
            crown = '<span class="hog">LOOT HOG</span>' if i == 0 and len(board) > 1 else ""
            trs += (f"<tr><td>{crown}</td><td>{_esc(name)}</td><td>{t['matches']}</td>"
                    f"<td>{t['elims']}</td><td>{t['downs']}</td><td>{t['damage']:,}</td>"
                    f"<td>{t['loot']:,}</td><td>{t['best_haul']:,}</td></tr>")
        squad_html = f'<h3>Squad leaderboard</h3><div class="tblwrap"><table>{head}{trs}</table></div>'
    else:
        squad_html = ('<h3>Squad leaderboard</h3><p class="empty2">No squad data yet — '
                      'play a trios match and exfil. Teammate panels are logged automatically.</p>')

    # Menace Report — gamertags read off the kill feed
    try:
        import encounters
        victims, killers = encounters.boards(base_dir)
    except Exception:
        victims, killers = [], []

    def names_table(board, badge_cls, badge_text, last_label):
        head = (f"<tr><th></th><th>Player</th><th>Times</th>"
                f"<th>{last_label}</th></tr>")
        trs = ""
        for i, (name, cnt, last) in enumerate(board[:15]):
            badge = (f'<span class="{badge_cls}">{badge_text}</span>'
                     if i == 0 and cnt >= 2 else "")
            trs += (f"<tr><td>{badge}</td><td>{_esc(name)}</td><td>{cnt}</td>"
                    f"<td>{_esc((last or '')[:16])}</td></tr>")
        return f'<div class="tblwrap"><table>{head}{trs}</table></div>'

    if victims or killers:
        menace_html = ""
        if victims:
            menace_html += ('<h3>Menace report — runners you downed</h3>'
                            + names_table(victims, "hog", "MENACED", "Last downed"))
        if killers:
            menace_html += ('<h3>Downed you</h3>'
                            + names_table(killers, "nem", "NEMESIS", "Last time"))
    elif _gamertag(cfg) == "":
        menace_html = ('<h3>Menace report</h3><p class="empty2">Set gamertag in '
                       'config.yaml (your exact in-game name) to turn this on.</p>')
    else:
        menace_html = ('<h3>Menace report</h3><p class="empty2">No names yet — '
                       'gamertags are read off the kill feed as downs happen.</p>')

    # W/L record from confirmed outcomes (EXFILTRATED vs ELIMINATED headers)
    try:
        import exfil_stats
        rec = exfil_stats.record_summary(base_dir)
    except Exception:
        rec = {"exfils": 0, "deaths": 0, "survival_pct": None,
               "best_streak": 0, "current_streak": 0}
    if rec["exfils"] or rec["deaths"]:
        surv = f"{rec['survival_pct']}%" if rec["survival_pct"] is not None else "—"
        record_html = ('<h3>Record</h3><div class="tiles">'
                       f'<div class="tile"><div class="tn">{rec["exfils"]}</div>'
                       f'<div class="tl">Exfils</div></div>'
                       f'<div class="tile"><div class="tn">{rec["deaths"]}</div>'
                       f'<div class="tl">Deaths</div></div>'
                       f'<div class="tile eco"><div class="tn">{surv}</div>'
                       f'<div class="tl">Survival rate</div></div>'
                       f'<div class="tile eco"><div class="tn">{rec["best_streak"]}</div>'
                       f'<div class="tl">Best exfil streak</div></div>'
                       '</div>')
    else:
        record_html = ""

    return (STATS_PAGE.replace("%%CARDS%%", cards)
            .replace("%%RECORD%%", record_html)
            .replace("%%SQUAD%%", squad_html)
            .replace("%%MENACE%%", menace_html))


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


_LAN_NETS = [ipaddress.ip_network(n) for n in
             ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12")]


def lan_ips(primary=None, others=None) -> list:
    """IPv4 addresses a phone on the same Wi-Fi could open, best first.

    local_ip() (the adapter the default route uses) leads. Then any other
    private address of this PC, 192.168.x first: with a VPN up, local_ip() is
    the VPN adapter, and the Wi-Fi address is one of the others. Hyper-V, WSL
    and Docker adapters also sit in 172.16/12 and are unreachable from a phone,
    which is why they come after the primary rather than replacing it.
    Loopback (no network: local_ip() falls back to 127.0.0.1) and link-local
    (169.254, an adapter that never got an address) are dropped; the others
    are limited to the private ranges.

    primary/others are for tests; normally they're looked up. gethostbyname_ex
    can be slow on Windows with broken name resolution, so call this once, off
    the request path (start_web does it on a thread)."""
    def v4(ip):
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return None
        return a if a.version == 4 else None

    def lan(ip):
        a = v4(ip)
        return bool(a) and any(a in n for n in _LAN_NETS)

    first = local_ip() if primary is None else primary
    if others is None:
        try:
            others = socket.gethostbyname_ex(socket.gethostname())[2]
        except Exception:
            others = []
    a = v4(first)
    usable = a is not None and not (a.is_loopback or a.is_link_local
                                    or a.is_unspecified)
    out = [first] if usable else []
    rest = [ip for ip in others if lan(ip) and ip not in out]
    rest = list(dict.fromkeys(rest))
    rest.sort(key=lambda ip: 0 if ip.startswith("192.168.") else 1)
    return out + rest


def _split_host_port(value: str):
    """'name:port' as sent in a Host header -> (name, port-or-None).
    Handles '[v6]:port' and a bare unbracketed v6 literal."""
    v = (value or "").strip()
    if v.startswith("["):
        end = v.find("]")
        if end < 0:
            return v, None
        rest = v[end + 1:]
        return v[1:end], (rest[1:] if rest.startswith(":") and rest[1:] else None)
    if v.count(":") == 1:
        name, port = v.split(":", 1)
        return name, (port or None)
    return v, None


# DNS suffixes that only the local network answers: a home router's own names
# (stan-pc.lan, stan-pc.home.arpa) live here. Public DNS cannot serve them, so
# an attacker cannot point one at this PC. Accepted only with the PC's own
# hostname in front (decision 5: the PC's own hostname, not any name).
_LOCAL_ONLY_SUFFIXES = ("lan", "home.arpa", "localdomain", "internal", "home")
_fqdn_cache = []


def _fqdn() -> str:
    """socket.getfqdn(), looked up once (it can wait on DNS) and only when a
    Host name is not already allowed otherwise."""
    if not _fqdn_cache:
        try:
            name = socket.getfqdn().strip().lower().rstrip(".")
        except Exception:
            name = ""
        _fqdn_cache.append(name)
    return _fqdn_cache[0]


def _own_hostnames(fqdn: bool = False) -> set:
    """This PC's names: the hostname (Windows: the bare 'DESKTOP-XXXX') and its
    first label, plus, with fqdn=True, socket.getfqdn() and its first label."""
    try:
        hn = socket.gethostname().strip().lower().rstrip(".")
    except Exception:
        hn = ""
    names = {hn, hn.split(".")[0]}
    if fqdn:
        f = _fqdn()
        names |= {f, f.split(".")[0]}
    return names - {""}


def _host_allowed(name: str) -> bool:
    """Is this Host header name one that really means THIS PC?

    The guard against DNS rebinding: a hostile page can make its OWN domain
    resolve to 127.0.0.1 or the PC's LAN IP, and the browser then treats the
    dashboard as same-origin with that page. The one thing the attacker can't
    change is the Host header — it carries their domain. So answer only to
    (owner decision 5):
      - IP literals (v4/v6). The iPad and the iOS app connect by IP. NOT pinned
        to local_ip(): VPN, WSL and Hyper-V adapters make that a different
        address than the one the iPad uses, and the LAN IP can change while
        the app runs.
      - localhost.
      - any *.local name (mDNS — answered only on the local link, never by a
        public DNS server an attacker controls).
      - the PC's own hostname: bare, as socket.getfqdn() gives it, or under a
        suffix only the home router answers (stan-pc.lan, stan-pc.home.arpa),
        so a Safari bookmark of the router's name for this PC keeps working.
    A bare name that is NOT this PC's is refused: decision 5 lists the PC's own
    name, and a DNS search suffix can make a bare name resolve through DNS.
    """
    h = (name or "").strip().lower().rstrip(".")
    if not h:
        return True                 # no Host at all: not a browser
    try:
        ipaddress.ip_address(_uparse.unquote(h).split("%", 1)[0])
        return True
    except ValueError:
        pass
    if h == "localhost" or h.endswith(".local"):
        return True
    if h in _own_hostnames():
        return True
    own = _own_hostnames(fqdn=True)
    if h in own:
        return True
    first, _, rest = h.partition(".")
    return first in own and rest in _LOCAL_ONLY_SUFFIXES


def _origin_matches(source: str, host_header: str) -> bool:
    """Does an Origin (or Referer) header name the same host:port as Host?
    Browsers attach Origin to every cross-site POST, including no-cors ones,
    so a mismatch means another site's page is driving the dashboard."""
    try:
        u = _uparse.urlsplit((source or "").strip())
        oport = u.port
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False               # 'null', file:, a garbled value
    if oport is None:
        oport = 443 if u.scheme == "https" else 80
    name, port = _split_host_port(host_header)
    try:
        hport = int(port) if port else 80
    except ValueError:
        return False
    return (u.hostname.lower().rstrip(".") == name.lower().rstrip(".")
            and oport == hport)


_refusals_logged = set()


def request_refusal(headers, post: bool) -> str:
    """'' when a request may proceed, else a short reason for the 403.

    Host must mean this PC (see _host_allowed). On a POST, an Origin header —
    or a Referer when there's no Origin — must name the same host:port as Host.
    A request with neither is allowed: the iOS app and plain navigations send
    none, and every browser attaches Origin to a cross-site POST.

    Deliberately NOT required: a JSON Content-Type (five dashboard buttons send
    none, and they'd silently stop working) or a login token (owner's call:
    the iPad bookmark keeps working; LAN access is disclosed in SECURITY.md)."""
    host = headers.get("Host") or ""
    name, _port = _split_host_port(host)
    if not _host_allowed(name):
        return f"host {name[:60]!r} is not this PC"
    if post:
        src = headers.get("Origin") or headers.get("Referer") or ""
        if src and not _origin_matches(src, host):
            return f"request came from another site ({src[:60]!r})"
    return ""


class _QuietServer(ThreadingHTTPServer):
    """Don't print a traceback when a client simply hangs up.

    Video players cancel range requests all the time (every seek, every close),
    and a phone drops idle keep-alive connections. The handlers already ignore
    a hang-up mid-response, but the one that lands while the server is waiting
    for the NEXT request on a kept-alive connection happens outside every
    handler, and the stock handle_error prints a 15-line traceback for it. On
    Windows that arrives as ConnectionAbortedError (WinError 10053), which the
    handlers didn't catch either; it and BrokenPipe/ConnectionReset are all
    ConnectionError. Anything else is a real bug and still prints."""

    # HTTPServer turns SO_REUSEADDR on. On Linux/macOS that only lets a restart
    # rebind past TIME_WAIT. On WINDOWS it lets a second process bind the very
    # same port while the first still listens — so a second WITNESS came up
    # "fine" on :8000, ran a second OCR loop on the GPU and saved duplicate
    # replays, with the two servers splitting the iPad's requests. Off on
    # Windows, a second copy fails to bind instead (the launcher reports it).
    allow_reuse_address = (os.name != "nt")

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


def start_web(state, port, base_dir, host="0.0.0.0"):
    imgs = {"/skull.png": "witness_logo.png",
            "/wordmark.png": "witness_wordmark.png"}
    state.version = build_label(base_dir)   # bottom-right corner + /api/info
    state.build_sha = read_build_sha(base_dir)   # what main.py should advertise

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.1 with keep-alive: iOS Safari's video player streams via
        # byte-range requests and stalls a couple seconds in over HTTP/1.0
        # (the default). Every response here sends Content-Length, so keep-alive
        # is safe. The timeout keeps idle keep-alive sockets from piling up.
        protocol_version = "HTTP/1.1"
        timeout = 30

        def log_message(self, *a):
            pass  # quiet

        def _refused(self, post=False) -> bool:
            """Answer 403 and return True when request_refusal() says no."""
            why = request_refusal(self.headers, post)
            if not why:
                return False
            if why not in _refusals_logged and len(_refusals_logged) < 20:
                _refusals_logged.add(why)      # one line per cause, not per poll
                print(f"  [web] refused a request: {why}")
            self.send_error(403, "Forbidden",
                            "WITNESS refused this request: " + why + ". Open the "
                            "dashboard by the PC's IP address (as shown in the "
                            "WITNESS window), localhost, or its .local name.")
            return True

        def _send(self, body, ctype, cache=True):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_video(self, fp):
            """Stream a video file with HTTP Range support (Safari needs it)."""
            size = os.path.getsize(fp)
            start, end = 0, size - 1
            # One range only. Multi-range ("0-1,5-9") and malformed headers get
            # the whole file, which HTTP allows; int() on them used to raise.
            m = _RANGE.fullmatch((self.headers.get("Range") or "").strip())
            a, b = (m.group(1), m.group(2)) if m else ("", "")
            if a or b:
                if a:
                    start = int(a)
                    if b:
                        end = min(int(b), size - 1)
                else:
                    # "bytes=-500" is a SUFFIX range: the last 500 bytes. It
                    # used to be served as bytes 0-500, the wrong end of the file.
                    start = max(0, size - int(b))
                if start >= size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            length = end - start + 1
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(fp, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_GET(self):
            if self._refused():
                return
            path = self.path.split("?")[0]
            try:
                if path == "/status":
                    self._send(json.dumps(state.snapshot()).encode(),
                               "application/json", cache=False)
                elif path == "/config":
                    self._send(json.dumps({
                        "settings": state.get_settings(),
                        "meta": SETTINGS_META,
                        "modes": MODES,
                    }).encode(), "application/json", cache=False)
                elif path == "/stats":
                    self._send(apply_theme(_stats_page(base_dir, state._cfg),
                                           state._cfg).encode("utf-8"),
                               "text/html; charset=utf-8", cache=False)
                elif path == "/api/play":
                    q = _uparse.parse_qs(self.path.partition("?")[2])
                    sess = (q.get("s") or [""])[0]
                    rel = (q.get("f") or [""])[0]
                    rec = _record_dir(state)
                    fp = _archive_video_path(rec, sess, rel)
                    if not fp:
                        self.send_error(404)
                    else:
                        import montage
                        import playable
                        sdir = os.path.join(rec, "Marathon Sessions", sess)
                        out = playable.playable(
                            sdir, rel, fp,
                            montage.find_ffmpeg(base_dir, state._cfg or {}))
                        if out:
                            self._send_video(out)
                        else:
                            self.send_error(415, "could not make this playable")
                elif path == "/api/info":
                    self._send(json.dumps(_api_info(state)).encode(),
                               "application/json", cache=False)
                elif path == "/api/status":
                    self._send(json.dumps(state.snapshot()).encode(),
                               "application/json", cache=False)
                elif path == "/api/sessions":
                    self._send(json.dumps(_api_sessions(state)).encode(),
                               "application/json", cache=False)
                elif path == "/api/uploads":
                    q = _uparse.parse_qs(self.path.partition("?")[2])
                    sid = (q.get("s") or [""])[0]
                    sdir = (os.path.join(_record_dir(state), "Marathon Sessions", sid)
                            if _SAFE_PART.match(sid or "") else "")
                    out = (_yt_status(state, sid, sdir)
                           if sdir and os.path.isdir(sdir) else {})
                    self._send(json.dumps(out).encode(), "application/json",
                               cache=False)
                elif path == "/api/session":
                    q = _uparse.parse_qs(self.path.split("?", 1)[1]
                                         if "?" in self.path else "")
                    self._send(json.dumps(_api_session(
                        state, (q.get("s") or ["latest"])[0])).encode(),
                        "application/json", cache=False)
                elif path == "/clips":
                    self._send(apply_theme(
                        CLIPS_PAGE, state._cfg).encode("utf-8"),
                               "text/html; charset=utf-8", cache=False)
                elif path == "/clipdata":
                    q = _uparse.parse_qs(self.path.split("?", 1)[1]
                                         if "?" in self.path else "")
                    sess = (q.get("s") or ["latest"])[0]
                    self._send(json.dumps(_clip_data(state, sess)).encode(),
                               "application/json", cache=False)
                elif path == "/clipthumb":
                    q = _uparse.parse_qs(self.path.split("?", 1)[1]
                                         if "?" in self.path else "")
                    fp = _clip_thumb(state, (q.get("s") or [""])[0],
                                     (q.get("f") or [""])[0])
                    if fp:
                        with open(fp, "rb") as f:
                            self._send(f.read(), "image/jpeg")
                    else:
                        self.send_error(404)
                elif path == "/archive":
                    self._send(apply_theme(
                        # _record_dir, like every other route: before the first
                        # START of this run state.record_dir is empty, and the
                        # Archive showed "No sessions found yet".
                        _archive_page(base_dir, _record_dir(state)),
                        state._cfg).encode("utf-8"),
                               "text/html; charset=utf-8", cache=False)
                elif path == "/avideo":
                    q = _uparse.parse_qs(self.path.partition("?")[2])
                    fp = _archive_video_path(_record_dir(state),
                                             (q.get("s") or [""])[0],
                                             (q.get("f") or [""])[0])
                    if fp:
                        if q.get("dl"):
                            self.send_response(200)
                            self.send_header("Content-Type", "application/octet-stream")
                            self.send_header("Content-Disposition",
                                             f'attachment; filename="{os.path.basename(fp)}"')
                            self.send_header("Content-Length", str(os.path.getsize(fp)))
                            self.end_headers()
                            with open(fp, "rb") as f:
                                while True:
                                    chunk = f.read(65536)
                                    if not chunk:
                                        break
                                    self.wfile.write(chunk)
                        else:
                            low = fp.lower()
                            if low.endswith((".mp3", ".wav")):
                                with open(fp, "rb") as f:
                                    self._send(f.read(),
                                               "audio/mpeg" if low.endswith(".mp3") else "audio/wav")
                            elif low.endswith(".png"):
                                with open(fp, "rb") as f:
                                    self._send(f.read(), "image/png")
                            else:
                                self._send_video(fp)
                    else:
                        self.send_error(404)
                elif path.startswith("/reel/"):
                    try:
                        fp = state.get_reel_path(int(path.rsplit("/", 1)[1]))
                    except ValueError:
                        fp = None
                    if fp and os.path.exists(fp):
                        self._send_video(fp)
                    else:
                        self.send_error(404)
                elif path.startswith("/replay/"):
                    try:
                        fp = state.get_replay_path(int(path.rsplit("/", 1)[1]))
                    except ValueError:
                        fp = None
                    if fp and os.path.exists(fp):
                        self._send_video(fp)
                    else:
                        self.send_error(404)
                elif path in imgs:
                    fp = os.path.join(base_dir, imgs[path])
                    if os.path.exists(fp):
                        with open(fp, "rb") as f:
                            self._send(f.read(), "image/png")
                    else:
                        self.send_error(404)
                else:
                    self._send(apply_theme(PAGE, state._cfg).encode("utf-8"),
                               "text/html; charset=utf-8", cache=False)
            except ConnectionError:
                # the client hung up mid-response (see _QuietServer)
                pass

        def do_POST(self):
            path = self.path.split("?")[0]
            # Read the body ONCE, for every route. The server is HTTP/1.1
            # keep-alive, and most POST routes never read their body — so any
            # bytes a client sent stayed in the socket and were parsed as the
            # start of the NEXT request on that connection, which then failed.
            # Capped so a hostile Content-Length can't make us allocate GBs.
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            except ValueError:
                n = 0
            self._body = self.rfile.read(n) if n > 0 else b""
            # After the body is drained (so a refusal can't desync keep-alive),
            # before ANY route acts: settings, start/stop, update, rebuild.
            if self._refused(post=True):
                return
            try:
                if path in ("/clip", "/addkill"):
                    # Only a running session consumes these; LiveState.reset()
                    # throws an idle press away at the next START. So say so
                    # (200 + ok:false: the iOS app's post() treats any non-2xx
                    # as "the PC didn't respond"). The PC's global hotkeys
                    # don't come through here and are unchanged.
                    if not state.running:
                        self._send(b'{"ok":false,"error":"not running"}',
                                   "application/json", cache=False)
                    else:
                        if path == "/clip":
                            state.request_clip()
                        else:
                            state.request_kill()
                        self._send(b'{"ok":true}', "application/json", cache=False)
                elif path == "/benchmark":
                    try:
                        import benchmark
                        result = benchmark.run_benchmark(state._cfg or {})
                    except Exception as e:
                        result = {"error": f"{type(e).__name__}: {e}"}
                    self._send(json.dumps(result).encode(),
                               "application/json", cache=False)
                elif path == "/youtube":
                    try:
                        body = json.loads(self._body or b"{}")
                    except ValueError:
                        body = {}
                    if not isinstance(body, dict):
                        body = {}
                    result = start_youtube_upload(state, base_dir,
                                                  str(body.get("s") or ""),
                                                  str(body.get("f") or ""))
                    self._send(json.dumps(result).encode(),
                               "application/json", cache=False)
                elif path == "/export":
                    try:
                        body = json.loads(self._body or b"{}")
                    except Exception:
                        body = {}
                    if not isinstance(body, dict):
                        body = {}
                    # Validate the session HERE: an export writes <dir>/reels/,
                    # so a request must not be able to name any other folder.
                    sdir = _web_session_dir(state, str(body.get("session") or "latest"))
                    if not sdir:
                        result = {"ok": False,
                                  "error": "couldn't find that session folder"}
                    else:
                        result = state.request_export(
                            base_dir, sdir, body.get("clips") or [],
                            body.get("opts") or {})
                    self._send(json.dumps(result).encode(),
                               "application/json", cache=False)
                elif path == "/update":
                    self._send(json.dumps(state.request_update(base_dir)).encode(),
                               "application/json", cache=False)
                elif path == "/rebuild":
                    # Rebuild a past session's reels from the clips on disk.
                    # Lives on the Archive page because that is where Stan looks;
                    # a .bat in the app folder was not somewhere he'd find it.
                    try:
                        body = json.loads(self._body or b"{}")
                    except Exception:
                        body = {}
                    if not isinstance(body, dict):
                        body = {}
                    # A rebuild TIDIES the folder (moves every image into exfil/
                    # and every video into clips/), so only a real session may
                    # be named. The validated absolute path is what's passed on.
                    sdir = _web_session_dir(state, str(body.get("session") or "latest"))
                    if not sdir:
                        result = {"ok": False,
                                  "error": "couldn't find that session folder"}
                    else:
                        result = state.request_rebuild(sdir)
                    self._send(json.dumps(result).encode(),
                               "application/json", cache=False)
                elif path == "/start":
                    state.fire_start()
                    self._send(b'{"ok":true}', "application/json", cache=False)
                elif path == "/stop":
                    state.fire_stop()
                    self._send(b'{"ok":true}', "application/json", cache=False)
                elif path == "/config":
                    try:
                        changes = json.loads(self._body or b"{}")
                    except ValueError:
                        changes = {}
                    result = state.apply_settings(changes if isinstance(changes, dict) else {})
                    self._send(json.dumps({"settings": result}).encode(),
                               "application/json", cache=False)
                else:
                    self.send_error(404)
            except ConnectionError:
                # the client hung up mid-response (see _QuietServer)
                pass

    srv = _QuietServer((host, int(port)), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if host in ("0.0.0.0", "", "::"):
        # The phone/iPad address, worked out ONCE and off the startup path
        # (snapshot() is polled every second by every client, and the lookup
        # can stall on Windows). The window app shows it; before, it existed
        # only as a print into the session log. Bound to 127.0.0.1
        # (web_lan: false) there is no phone address, so none is shown.
        threading.Thread(target=_fill_lan_urls,
                         args=(state, srv.server_address[1]), daemon=True).start()
    return srv


def build_label(base_dir: str) -> str:
    """'0.1.0 (1a2b3c4)': the release number from version.py plus the build sha
    the updater writes to .app_version.

    Always the sha next to the number, never instead of it: the updater ships
    every commit on main, so a hand-kept number goes stale between releases.
    Without a sha (a ZIP install that hasn't updated yet) it's '0.1.0 (dev)'.
    With neither, '' and the page hides the line. It used to say just 'dev' on
    every such install, which read like debug output in bug reports."""
    try:
        from version import VERSION
        ver = str(VERSION or "").strip()
    except Exception:            # version.py not there yet, or broken
        ver = ""
    sha = read_build_sha(base_dir)
    if sha:
        return f"{ver} ({sha})" if ver else sha
    return f"{ver} (dev)" if ver else ""


def read_build_sha(base_dir: str) -> str:
    """The 7-character sha the updater writes to .app_version, or '' before
    the first update.

    start_web publishes it as state.build_sha for the Bonjour TXT 'v' that the
    iPad's PC picker shows. discovery.advertise keeps only 12 characters there,
    and the full label ('0.1.0 (1a2b3c4)') is 15, so advertising state.version
    showed '0.1.0 (1a2b3'. main.py should pass
    getattr(state, "build_sha", "") or state.version."""
    try:
        with open(os.path.join(base_dir, ".app_version"), encoding="utf-8") as f:
            return f.read().strip()[:7]
    except Exception:
        return ""


def _fill_lan_urls(state, port):
    try:
        state.lan_urls = [f"{ip}:{int(port)}" for ip in lan_ips()]
    except Exception:
        state.lan_urls = []


ARCHIVE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>WITNESS &mdash; Archive</title>
<style>
  :root { --bg:#0e0e16; --panel:#12181d; --line:#232d34; --text:#e9e9ed;
    --muted:#8a90a0; --accent:#9184d9; --danger:#ff4d3d;
    --sec:#b9b9c6; --dim:#5f6572; --accent-light:#c7bdff; --accent-deep:#7a6dc7;
    --surface:rgba(255,255,255,.03); --sborder:rgba(255,255,255,.07);
    --ground:radial-gradient(120% 80% at 82% -10%,#20203a 0%,#14141f 46%,#0e0e16 100%);
    --ui:'Inter',system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    --mono:'JetBrains Mono',ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  html { scrollbar-gutter: stable; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:var(--ui);
    -webkit-font-smoothing:antialiased; }
  .wrap { min-height:100vh; background:var(--ground); max-width:1120px; margin:0 auto;
    padding:calc(24px + env(safe-area-inset-top)) 30px calc(40px + env(safe-area-inset-bottom)); }
  .top { display:flex; align-items:center; gap:13px; margin-bottom:22px; }
  .top img { height:32px; }
  .top .wm { font-weight:800; letter-spacing:.16em; font-size:15px; }
  .nav { margin-left:14px; display:flex; gap:4px; flex-wrap:wrap; }
  .nav a { font:500 12.5px var(--ui); color:var(--muted); padding:6px 10px;
    border-radius:7px; text-decoration:none; }
  .nav a:hover { color:var(--sec); }
  .nav a.on { color:var(--text); background:var(--surface); }
  .kick { font:600 11px var(--mono); letter-spacing:.22em; color:var(--muted); text-transform:uppercase; }
  h2 { font-weight:900; font-size:34px; letter-spacing:-.02em; margin:4px 0 3px; }
  .sub { color:var(--muted); font:400 13px var(--ui); margin:0 0 22px; max-width:60ch; line-height:1.5; }
  details { background:var(--surface); border:1px solid var(--sborder); border-radius:12px;
    padding:2px 20px; margin-bottom:10px; }
  summary { padding:16px 0; cursor:pointer; font:500 14px var(--ui); list-style:none; }
  summary::-webkit-details-marker { display:none; }
  summary b { color:var(--text); font-weight:600; }
  .meta { color:var(--muted); font:500 11px var(--mono); float:right; }
  h4 { color:var(--dim); font:600 10px var(--mono); letter-spacing:.16em; text-transform:uppercase;
    margin:10px 0 8px; }
  .rc { font:500 13px var(--ui); color:var(--sec); padding:9px 0; border-bottom:1px solid rgba(255,255,255,.05); }
  .report { margin:2px 0 8px; padding:14px 16px; background:#0e0b16;
    border:1px solid rgba(145,132,217,.28); border-radius:10px; overflow-x:auto;
    font:500 12px/1.5 var(--mono); color:var(--sec); white-space:pre-wrap; }
  .rc.none { color:var(--muted); background:var(--surface);
    border:1px solid var(--sborder); border-radius:12px; padding:20px; }
  .mrow { display:flex; align-items:center; gap:12px; padding:12px 0;
    border-bottom:1px solid rgba(255,255,255,.05); font:500 13px var(--ui); }
  .mrow:last-child, details .rc:last-of-type { border-bottom:none; }
  .play { color:var(--accent); font-size:.85rem; }
  .ml { color:var(--text); text-decoration:none; flex:1; }
  .dl { color:var(--accent-light); text-decoration:none; font:600 10px var(--mono); letter-spacing:.08em;
    text-transform:uppercase; border:1px solid var(--accent-deep); border-radius:8px; padding:6px 13px; }
  .dl:hover { background:rgba(145,132,217,.12); }
  details > *:last-child { margin-bottom:14px; }
  .mini { font:600 11.5px var(--ui); letter-spacing:.06em; text-transform:uppercase;
    color:var(--text); background:var(--surface); border:1px solid var(--sborder);
    border-radius:8px; padding:7px 12px; cursor:pointer; }
  .mini:hover { border-color:var(--accent); color:var(--accent-light); }
  .mini.yt { margin-left:auto; padding:5px 10px; }
  .dl.yt.done { margin-left:auto; color:#ff8a7a; }
  .yterr { color:var(--warn,#f5a623); max-width:40ch; }
  .mini:disabled { opacity:.65; cursor:default; border-color:var(--sborder);
    color:var(--muted); text-transform:none; letter-spacing:.02em; }
  .rb { margin-right:10px; }
</style></head><body><div class="wrap">
  <div class="top"><img src="/skull.png" alt=""><span class="wm">WITNESS</span>
    <nav class="nav"><a href="/">Live</a><a href="/clips">Clips</a>
      <a href="/stats">Stats</a><a class="on" href="/archive">Archive</a>
      <a href="/archive" title="Refresh — new reels finish rendering after you stop">&#8635; Refresh</a></nav></div>
  <div class="kick">Archive</div>
  <h2>Every session, kept</h2>
  <p class="sub">Tap a reel to watch; "save" downloads it for sharing (Files &rarr; share sheet &rarr; group chat).</p>
  %%BODY%%
</div>
<script>
  // Rebuild a session's reels from the clips on disk, then RELOAD when it's done.
  // Stan asked "how would you refresh?" about this page once already — a build
  // that finishes silently and leaves stale content on screen is the same trap,
  // so this polls /status and reloads itself.
  // Upload one video to YouTube. The upload runs on the PC in the background
  // (a reel can take minutes); this polls its state and turns the button into a
  // link when it's up, or shows why it failed.
  function ytUpload(btn){
    if (btn.disabled) return;
    var s = btn.getAttribute('data-s'), f = btn.getAttribute('data-f');
    btn.disabled = true; btn.textContent = 'Uploading…';
    function show(st){
      if (!st) return false;
      if (st.state === 'done' && st.url){
        var a = document.createElement('a');
        a.className = 'dl yt done'; a.href = st.url; a.target = '_blank';
        a.rel = 'noopener'; a.textContent = 'on YouTube ↗';
        btn.replaceWith(a); return true;
      }
      if (st.state === 'failed'){
        btn.disabled = false; btn.textContent = 'Retry upload';
        btn.title = st.error || 'upload failed';
        var why = btn.nextElementSibling;
        if (!why || !why.classList.contains('yterr')){
          why = document.createElement('span'); why.className = 'meta yterr';
          btn.after(why);
        }
        why.textContent = st.error || 'upload failed';
        return true;
      }
      return false;
    }
    fetch('/youtube', {method:'POST', headers:{'Content-Type':'application/json'},
                       body: JSON.stringify({s: s, f: f})})
      .then(function(r){ return r.json(); })
      .then(function(j){
        if (!j.ok){ show({state:'failed', error: j.error}); return; }
        if (show(j)) return;
        var poll = setInterval(function(){
          fetch('/api/uploads?s=' + encodeURIComponent(s), {cache:'no-store'})
            .then(function(r){ return r.json(); })
            .then(function(all){ if (show(all[f])) clearInterval(poll); })
            .catch(function(){});
        }, 3000);
      })
      .catch(function(e){ show({state:'failed', error: String(e)}); });
  }

  function rebuild(sess, btn){
    if (btn.disabled) return;
    var label = btn.textContent;
    btn.disabled = true; btn.textContent = 'Rebuilding…';
    fetch('/rebuild', {method:'POST', headers:{'Content-Type':'application/json'},
                       body: JSON.stringify({session: sess})})
      .then(function(r){ return r.json(); })
      .then(function(j){
        if (!j.ok) { btn.textContent = j.error || 'failed'; btn.disabled = false; return; }
        var poll = setInterval(function(){
          fetch('/status', {cache:'no-store'}).then(function(r){ return r.json(); })
            .then(function(s){
              var rc = s.recap || {};
              if (rc.note) btn.textContent = rc.note;
              if (rc.status === 'ready') {
                clearInterval(poll);
                btn.textContent = 'Done — reloading';
                setTimeout(function(){ location.reload(); }, 700);
              } else if (rc.status === 'error') {
                clearInterval(poll);
                btn.textContent = rc.note || 'failed';
                btn.disabled = false;
              }
            }).catch(function(){});
        }, 2000);
      })
      .catch(function(e){ btn.textContent = 'failed: ' + e; btn.disabled = false; });
  }
</script>
</body></html>"""

STATS_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>WITNESS &mdash; Stats</title>
<style>
  :root { --bg:#0e0e16; --panel:#12181d; --line:#232d34; --text:#e9e9ed;
    --muted:#8a90a0; --accent:#9184d9; --danger:#ff4d3d;
    --sec:#b9b9c6; --dim:#5f6572; --accent-light:#c7bdff; --accent-deep:#7a6dc7;
    --surface:rgba(255,255,255,.03); --sborder:rgba(255,255,255,.07);
    --ground:radial-gradient(120% 80% at 82% -10%,#20203a 0%,#14141f 46%,#0e0e16 100%);
    --ui:'Inter',system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    --mono:'JetBrains Mono',ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  html { scrollbar-gutter: stable; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:var(--ui);
    -webkit-font-smoothing:antialiased; }
  .wrap { min-height:100vh; background:var(--ground); max-width:1120px; margin:0 auto;
    padding:calc(24px + env(safe-area-inset-top)) 30px calc(40px + env(safe-area-inset-bottom)); }
  .top { display:flex; align-items:center; gap:13px; margin-bottom:22px; }
  .top img { height:32px; }
  .top .wm { font-weight:800; letter-spacing:.16em; font-size:15px; }
  .nav { margin-left:14px; display:flex; gap:4px; flex-wrap:wrap; }
  .nav a { font:500 12.5px var(--ui); color:var(--muted); padding:6px 10px;
    border-radius:7px; text-decoration:none; }
  .nav a:hover { color:var(--sec); }
  .nav a.on { color:var(--text); background:var(--surface); }
  .kick { font:600 11px var(--mono); letter-spacing:.22em; color:var(--muted); text-transform:uppercase; }
  h2 { font-weight:900; font-size:34px; letter-spacing:-.02em; margin:4px 0 3px; }
  .sub { color:var(--muted); font:400 13px var(--ui); margin:0 0 22px; }
  h3 { color:var(--muted); font:600 11px var(--mono); letter-spacing:.2em; text-transform:uppercase;
    margin:28px 0 12px; }
  .tiles { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
  @media(max-width:520px){ .tiles{ grid-template-columns:repeat(2,1fr); } }
  .tile { background:var(--surface); border:1px solid var(--sborder); border-radius:12px;
    padding:20px 22px; }
  .tile .tn { font:800 40px var(--ui); letter-spacing:-.02em; color:var(--text);
    font-variant-numeric:tabular-nums; }
  .tile.eco { background:linear-gradient(135deg,rgba(145,132,217,.16),rgba(145,132,217,.04));
    border-color:rgba(145,132,217,.32); }
  .tile.eco .tn { background:linear-gradient(180deg,var(--accent-light),var(--accent));
    -webkit-background-clip:text; background-clip:text; color:transparent; }
  .tile.eco .tl { color:#b6a4ff; }
  .tile .tl { font:600 10px var(--mono); letter-spacing:.16em; text-transform:uppercase;
    color:var(--muted); margin-top:4px; }
  .tblwrap { overflow-x:auto; background:var(--surface); border:1px solid var(--sborder);
    border-radius:12px; }
  table { border-collapse:collapse; width:100%; font:500 13px var(--ui); }
  th, td { text-align:right; padding:13px 18px; border-bottom:1px solid rgba(255,255,255,.05);
    white-space:nowrap; font-variant-numeric:tabular-nums; color:var(--sec); }
  tr:last-child td { border-bottom:none; }
  th:nth-child(2), td:nth-child(2) { text-align:left; }
  td:nth-child(2) { color:var(--text); font-weight:600; }
  td:last-child, td:nth-last-child(2) { color:var(--sec); }
  th { color:var(--dim); font:600 9.5px var(--mono); letter-spacing:.12em; text-transform:uppercase; }
  .hog { background:linear-gradient(90deg,var(--accent),var(--accent-deep)); color:#12121a;
    font:700 9px var(--mono); letter-spacing:.06em; padding:3px 8px; border-radius:5px; }
  .nem { background:var(--danger); color:#12121a; font:700 9px var(--mono);
    letter-spacing:.06em; padding:3px 8px; border-radius:5px; }
  .empty2 { color:var(--muted); font:400 13px var(--ui); background:var(--surface);
    border:1px solid var(--sborder); border-radius:12px; padding:20px; }
</style></head><body><div class="wrap">
  <div class="top"><img src="/skull.png" alt=""><span class="wm">WITNESS</span>
    <nav class="nav"><a href="/">Live</a><a href="/clips">Clips</a>
      <a class="on" href="/stats">Stats</a><a href="/archive">Archive</a></nav></div>
  <div class="kick">Career</div>
  <h2>Career stats</h2>
  <p class="sub">From every exfil screen WITNESS has captured. It only gets deeper from here.</p>
  <div class="tiles">%%CARDS%%</div>
  %%RECORD%%
  %%SQUAD%%
  %%MENACE%%
</div></body></html>"""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="WITNESS">
<link rel="apple-touch-icon" href="/skull.png">
<title>WITNESS</title>
<style>
  /* Broadcast palette. The seven theme keys (bg/panel/line/text/muted/accent/
     danger) carry the exact THEME_DEFAULTS hexes so per-game apply_theme() can
     still swap them by string replace. Everything else derives from these. */
  :root {
    --bg:#0e0e16; --panel:#12181d; --line:#232d34; --text:#e9e9ed;
    --muted:#8a90a0; --accent:#9184d9; --danger:#ff4d3d;
    --sec:#b9b9c6; --dim:#5f6572;
    --accent-light:#c7bdff; --accent-deep:#7a6dc7;
    --surface:rgba(255,255,255,.03); --sborder:rgba(255,255,255,.07);
    --down:#c7ccd6; --precision:var(--accent); --finisher:#f5a623; --assist:#37cabb;
    --green:#5bd66b; --danger-br:#ff6a58;
    --ground:radial-gradient(120% 90% at 78% -10%, #20203a 0%, #14141f 46%, #0e0e16 100%);
    --ui:'Inter',system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    --mono:'JetBrains Mono',ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  }
  html { scrollbar-gutter: stable; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html, body { min-height:100%; margin:0; }
  body { background:var(--bg); color:var(--text); user-select:none;
    font-family:var(--ui); -webkit-font-smoothing:antialiased; }
  .wrap { min-height:100vh; background:var(--ground);
    padding:22px 30px calc(30px + env(safe-area-inset-bottom));
    max-width:1120px; margin:0 auto; position:relative; overflow:hidden; }
  .kick { font:600 11px var(--mono); letter-spacing:.2em; color:var(--muted);
    text-transform:uppercase; margin-bottom:6px; }

  /* end-of-session recap banner */
  .recap { margin-bottom:22px; background:linear-gradient(180deg,rgba(145,132,217,.14),var(--surface));
    border:1px solid var(--accent); border-radius:14px; padding:16px 18px; }
  .recap.done { border-color:var(--green); background:linear-gradient(180deg,rgba(91,214,107,.12),var(--surface)); }
  .rc-hd { display:flex; align-items:center; gap:10px; font:700 15px var(--ui); }
  .rc-dot { width:11px; height:11px; border-radius:50%; background:var(--accent); flex:none; }
  .rc-dot.pulse { animation:rcpulse 1s ease-in-out infinite; }
  .rc-dot.done { background:var(--green); box-shadow:0 0 10px var(--green); animation:none; }
  .recap.err { border-color:var(--danger); background:linear-gradient(180deg,rgba(255,77,61,.12),var(--surface)); }
  .rc-dot.err { background:var(--danger); box-shadow:0 0 10px var(--danger); animation:none; }
  @keyframes rcpulse { 0%,100%{ opacity:1; } 50%{ opacity:.3; } }
  .rc-note { color:var(--sec); font:500 13px var(--ui); margin-top:6px; }
  .rc-report { margin:12px 0 4px; padding:14px 16px; background:#0e0b16;
    border:1px solid rgba(145,132,217,.28); border-radius:10px; overflow-x:auto;
    font:500 12px/1.5 var(--mono); color:var(--sec); white-space:pre-wrap; }
  .phoneurl { margin:-14px 0 20px; font:500 12.5px var(--ui); color:var(--muted); }
  .phoneurl b, .help b.addr { color:var(--accent-light); font:600 13px var(--mono);
    user-select:text; -webkit-user-select:text; }
  .phoneurl .also { color:var(--dim); font:500 11.5px var(--mono); user-select:text; }
  .errbox { margin-bottom:22px; border:1px solid var(--danger); border-radius:14px;
    padding:16px 18px; background:linear-gradient(180deg,rgba(255,77,61,.12),var(--surface)); }
  .errbox .rc-note { color:var(--text); white-space:pre-wrap; }
  .rc-btn { display:inline-block; margin-top:10px; background:var(--accent); color:#12121a;
    text-decoration:none; border-radius:9px; padding:10px 18px; font:700 12px var(--ui);
    letter-spacing:.04em; }

  /* top bar */
  .bar { display:flex; align-items:center; gap:13px;
    padding-top:env(safe-area-inset-top); margin-bottom:26px; }
  .bar .logo { height:34px; }
  .bar .wm { font-family:var(--ui); font-weight:800; letter-spacing:.16em; font-size:15px; }
  .livepill { display:none; align-items:center; gap:8px; padding:6px 12px;
    border:1px solid rgba(255,77,61,.5); border-radius:100px;
    font:600 11px var(--mono); letter-spacing:.14em; color:var(--danger-br); }
  .livepill.on { display:inline-flex; }
  .livepill i { width:8px; height:8px; border-radius:50%; background:var(--danger);
    box-shadow:0 0 10px var(--danger); }
  .nav { margin-left:14px; display:flex; gap:4px; flex-wrap:wrap; }
  .nav a { font:500 12.5px var(--ui); color:var(--muted); padding:6px 10px;
    border-radius:7px; cursor:pointer; white-space:nowrap; text-decoration:none; }
  .nav a:hover { color:var(--sec); }
  .nav a.on { color:var(--text); background:var(--surface); }
  .runbtn { margin-left:auto; background:transparent; border:1px solid rgba(255,75,66,.5);
    color:var(--danger-br); border-radius:9px; padding:9px 20px;
    font:700 11px var(--ui); letter-spacing:.08em; text-transform:uppercase;
    cursor:pointer; transition:background .15s, transform .1s, color .15s; }
  .runbtn:active { transform:scale(.97); }
  .runbtn.on { } /* stop look is the default outlined red */
  .runbtn:not(.on):not(.busy) { border-color:var(--accent-deep);
    color:var(--accent-light); background:linear-gradient(180deg,var(--accent),var(--accent-deep));
    color:#12121a; border:none; }
  .runbtn.busy { border-color:var(--line); color:var(--muted);
    background:transparent; cursor:default; }

  /* hero: count + sparkline | breakdown + actions */
  .hero { display:grid; grid-template-columns:1.15fr .85fr; gap:26px; align-items:start; }
  .killrow { display:flex; align-items:flex-end; gap:18px; }
  .big { font-size:150px; line-height:.82; font-weight:900; letter-spacing:-.04em;
    background:linear-gradient(180deg,var(--accent-light),var(--accent));
    -webkit-background-clip:text; background-clip:text; color:transparent;
    font-variant-numeric:tabular-nums; }
  .killmeta { padding-bottom:16px; }
  .status .st, #statustext { font:700 13px var(--ui); letter-spacing:.02em; color:var(--muted); }
  .status { display:flex; align-items:center; }
  .status.live #statustext { color:var(--green); }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:9px;
    background:var(--muted); }
  .status.live .dot { background:var(--green); box-shadow:0 0 10px var(--green); }
  .sub { color:var(--dim); font:500 13px var(--ui); margin-top:5px; }
  .heatchip { display:inline-block; margin-top:9px; padding:5px 11px;
    border:1px solid; border-radius:7px; font:700 11px var(--mono);
    letter-spacing:.08em; }
  @media (prefers-reduced-motion: no-preference){
    .big.pop { animation:pop .5s ease-out; }
    @keyframes pop { 0%{ transform:scale(1);} 30%{ transform:scale(1.12);
      filter:drop-shadow(0 0 34px rgba(145,132,217,.8)); } 100%{ transform:scale(1); filter:none; } }
  }
  .spark { width:100%; height:90px; margin-top:12px; display:block; }

  .bars { display:flex; flex-direction:column; gap:11px; }
  .brow { }
  .barhd { display:flex; justify-content:space-between; font:600 12px var(--ui); margin-bottom:5px; }
  .barhd .lb.down{color:var(--down);} .barhd .lb.precision{color:var(--accent);}
  .barhd .lb.finisher{color:var(--finisher);} .barhd .lb.assist{color:var(--assist);}
  .barhd .vn { color:var(--muted); font-variant-numeric:tabular-nums; }
  .track { height:8px; border-radius:4px; background:#20232e; overflow:hidden; }
  .fill { height:100%; border-radius:4px; width:0; transition:width .4s ease; }
  .fill.down{background:var(--down);} .fill.precision{background:var(--accent);}
  .fill.finisher{background:var(--finisher);} .fill.assist{background:var(--assist);}
  .cta { display:flex; gap:9px; margin-top:8px; }
  .btn { flex:1; border-radius:9px; padding:13px; font:700 12px var(--ui);
    letter-spacing:.06em; text-transform:uppercase; cursor:pointer;
    transition:transform .1s, filter .15s, background .15s; }
  .btn:active { transform:scale(.97); }
  .btn.primary { background:var(--accent); color:#12121a; border:none; }
  .btn.primary:hover { filter:brightness(1.08); }
  .btn.ghost { background:transparent; color:var(--accent-light);
    border:1px solid var(--accent-deep); }
  .btn.fired { background:var(--green); color:#0b0f12; border-color:var(--green); }
  .btn:disabled { opacity:.45; cursor:default; transform:none; filter:none; }
  .btn.refused { color:var(--danger-br); border:1px solid var(--danger); background:transparent; }

  /* match timeline */
  .tl { margin-top:26px; }
  .tlhd { display:flex; align-items:baseline; justify-content:space-between; margin-bottom:11px; }
  .tlmeta { font:500 11px var(--ui); color:var(--dim); }
  .rail2 { position:relative; height:56px; border-radius:10px;
    background:linear-gradient(90deg,#16161f,#191922);
    border:1px solid var(--sborder); overflow:hidden; }
  .rail2 .baseline { position:absolute; inset:0; display:flex; align-items:center; padding:0 16px; }
  .rail2 .baseline:before { content:''; height:2px; flex:1;
    background:repeating-linear-gradient(90deg,#2a2a38 0 6px,transparent 6px 12px); }
  .mk { position:absolute; top:11px; width:34px; height:34px; border-radius:8px;
    transform:translateX(-50%); display:flex; align-items:center; justify-content:center;
    font:700 11px var(--ui); background:#22262f; border:1px solid #333a46; color:var(--down); }
  .mk.precision{ background:rgba(145,132,217,.16); border-color:var(--accent); color:var(--accent-light); }
  .mk.finisher{ background:rgba(245,166,35,.16); border-color:var(--finisher); color:var(--finisher); }
  .mk.assist{ background:rgba(55,202,187,.16); border-color:var(--assist); color:var(--assist); }
  .tlempty { position:absolute; inset:0; display:flex; align-items:center;
    justify-content:center; color:var(--dim); font:500 12px var(--ui); }

  /* reels + replays */
  .reels { margin-top:20px; }
  .reelrow { display:flex; align-items:center; gap:13px; background:var(--surface);
    border:1px solid var(--sborder); border-radius:11px; padding:12px 16px;
    cursor:pointer; margin-bottom:8px; }
  .reels#reels .reelrow:first-child { background:linear-gradient(90deg,rgba(145,132,217,.12),transparent);
    border-color:rgba(145,132,217,.35); }
  .reelrow .play { width:44px; height:30px; border-radius:6px; background:#0c0c14;
    flex:none; display:flex; align-items:center; justify-content:center;
    color:var(--accent); font-size:14px; }
  .reelrow span:not(.play):not(.t) { font:600 13px var(--ui); color:var(--text); }
  .reelrow .t { margin-left:auto; font:500 11px var(--mono); color:var(--dim); }

  /* recent feed */
  .recent { margin-top:24px; }
  .feed { display:flex; flex-direction:column; gap:6px; }
  .row { background:var(--surface); border:1px solid var(--sborder); border-radius:9px;
    padding:11px 15px; display:flex; align-items:center; gap:12px; font:500 13.5px var(--ui); }
  .row.precision { border-color:rgba(145,132,217,.4); }
  .row .t { margin-left:auto; color:var(--dim); font:500 11px var(--mono); }
  .badge { font:600 10px var(--mono); letter-spacing:.1em; text-transform:uppercase;
    padding:3px 8px; border-radius:5px; background:#1c2630; color:var(--muted); white-space:nowrap; }
  .precision .badge { background:var(--accent); color:#12121a; }
  .finisher .badge { background:var(--finisher); color:#12121a; }
  .assist .badge { background:var(--assist); color:#12121a; }
  .skull { height:15px; vertical-align:middle; }
  .empty { color:var(--dim); padding:20px 4px; font:500 13px var(--ui); }

  .ctrls { display:flex; gap:8px; margin-top:22px; max-width:400px; flex-wrap:wrap; }
  .mini { flex:1; background:var(--surface); color:var(--muted); border:1px solid var(--sborder);
    border-radius:8px; padding:9px 8px; font:500 11px var(--mono); letter-spacing:.06em; cursor:pointer; }
  .detect { margin-top:22px; background:var(--surface); border:1px solid var(--sborder);
    border-radius:12px; padding:14px 16px; max-width:420px;
    display:flex; align-items:center; gap:14px; }
  .detect .dbody { flex:1; min-width:0; }
  .detect .dval { font:600 14px var(--ui); color:var(--text); margin-top:3px; }
  .detect .dval .ok { color:var(--green); } .detect .dval .warn { color:var(--finisher); }
  .detect .dval .dim { color:var(--dim); font-weight:500; }
  .detect .dsub { font:500 11px var(--mono); color:var(--dim); margin-top:3px; }
  .benchbtn { flex:none; background:transparent; color:var(--accent-light);
    border:1px solid var(--accent-deep); border-radius:9px; padding:9px 16px;
    font:700 11px var(--ui); letter-spacing:.06em; text-transform:uppercase; cursor:pointer;
    transition:background .15s, transform .1s; }
  .benchbtn:hover { background:rgba(145,132,217,.12); } .benchbtn:active { transform:scale(.97); }
  .benchbtn:disabled { opacity:.55; cursor:default; }
  .hint { color:var(--dim); font:500 11px var(--ui); margin-top:16px; line-height:1.5; opacity:.85; }
  .ver { position:fixed; bottom:9px; right:14px; font:500 10px var(--mono);
    color:var(--dim); letter-spacing:.08em; opacity:.6; z-index:5; }

  /* modals / settings / help (ported, theme-var based) */
  .modal { display:none; position:fixed; inset:0; background:rgba(0,0,0,.93);
    z-index:50; align-items:center; justify-content:center; flex-direction:column; padding:16px; }
  .modal.open { display:flex; }
  .modal video { width:100%; max-width:900px; max-height:75vh; border-radius:12px; background:#000; }
  .modal .mlabel { color:var(--text); font-size:.85rem; margin:14px 0 10px; }
  .modal .close { background:var(--surface); color:var(--text); border:1px solid var(--sborder);
    border-radius:8px; padding:10px 26px; font:600 13px var(--ui); cursor:pointer; }
  .settings { background:#12121c; border:1px solid var(--sborder); border-radius:14px;
    padding:18px; max-width:480px; width:100%; max-height:80vh; overflow-y:auto; text-align:left;
    box-shadow:0 24px 60px -18px rgba(0,0,0,.7); }
  .settings h2 { margin:0 0 14px; font:700 12px var(--ui); letter-spacing:.14em;
    text-transform:uppercase; color:var(--accent-light); }
  .modehead { color:var(--muted); font:600 10px var(--mono); letter-spacing:.14em;
    text-transform:uppercase; margin:2px 0 8px; }
  .moderow { display:flex; align-items:center; gap:12px; padding:6px 0; }
  .modebtn { flex:0 0 92px; background:var(--bg); color:var(--text); border:1px solid var(--sborder);
    border-radius:8px; padding:9px 0; font:700 11px var(--ui); letter-spacing:.1em; cursor:pointer; }
  .modebtn.active { background:var(--accent); color:#12121a; border-color:var(--accent); }
  .modedesc { color:var(--muted); font:500 11px var(--ui); line-height:1.35; }
  .setrow { display:flex; align-items:center; justify-content:space-between; gap:12px;
    padding:11px 2px; border-bottom:1px solid var(--sborder); font:500 13.5px var(--ui); }
  .setrow:last-of-type { border-bottom:none; }
  .setrow input[type=number] { width:76px; background:var(--bg); color:var(--text);
    border:1px solid var(--sborder); border-radius:6px; padding:7px 9px; font:inherit; text-align:center; }
  .switch { position:relative; width:52px; height:30px; flex:none; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background:var(--line); border-radius:15px; cursor:pointer; transition:background .15s; }
  .slider:before { content:''; position:absolute; width:24px; height:24px; left:3px; top:3px;
    background:var(--muted); border-radius:50%; transition:transform .15s, background .15s; }
  .switch input:checked + .slider { background:var(--accent); }
  .switch input:checked + .slider:before { transform:translateX(22px); background:#12121a; }
  .savedmsg { color:var(--accent-light); font:500 12px var(--ui); text-align:center; margin-top:10px; opacity:0; transition:opacity .3s; }
  .savedmsg.show { opacity:1; }
  .help h3 { color:var(--accent-light); font:700 11px var(--ui); letter-spacing:.14em; text-transform:uppercase; margin:16px 0 6px; }
  .help h3:first-of-type { margin-top:0; }
  .help p { color:var(--text); font:500 13px var(--ui); line-height:1.5; margin:0 0 4px; }
  .help .m { color:var(--muted); } .help code { color:var(--accent-light); font:500 12px var(--mono); }

  @media(max-width:820px){
    .wrap { padding:16px 16px calc(24px + env(safe-area-inset-bottom)); }
    .bar { flex-wrap:wrap; }
    .nav { margin-left:0; order:3; width:100%; overflow-x:auto; }
    .runbtn { margin-left:auto; }
    .hero { grid-template-columns:1fr; gap:18px; }
    .big { font-size:96px; }
    .killmeta { padding-bottom:8px; }
  }
</style></head><body>
<div class="wrap">
  <header class="bar">
    <img class="logo" src="/skull.png" alt="">
    <span class="wm">WITNESS</span>
    <span class="livepill" id="livepill"><i></i>LIVE</span>
    <nav class="nav">
      <a class="on" href="/">Live</a>
      <a href="/clips">Clips</a>
      <a href="/stats">Stats</a>
      <a href="/archive">Archive</a>
      <a onclick="openSettings()">Settings</a>
      <a onclick="openHelp()">How to use</a>
    </nav>
    <button class="runbtn" id="runbtn" onclick="toggleRun()">START</button>
  </header>
  <div class="phoneurl" id="phoneurl" style="display:none"></div>

  <section class="errbox" id="errbox" style="display:none">
    <div class="rc-hd"><span class="rc-dot err"></span><span>START didn&rsquo;t work</span></div>
    <div class="rc-note" id="errnote"></div>
  </section>

  <section class="recap" id="recap" style="display:none">
    <div class="rc-hd"><span class="rc-dot" id="rcdot"></span><span id="rctitle">Building your recap&hellip;</span></div>
    <div class="rc-note" id="rcnote"></div>
    <pre class="rc-report" id="rcreport" style="display:none"></pre>
    <a class="rc-btn" id="rcarchive" href="/archive" style="display:none">Open Archive &rarr;</a>
  </section>

  <section class="hero">
    <div class="heroL">
      <div class="kick">Session kills</div>
      <div class="killrow">
        <div class="big" id="count">0</div>
        <div class="killmeta">
          <div class="status" id="status"><span class="dot"></span><span id="statustext">CONNECTING</span></div>
          <div class="sub" id="sub">&nbsp;</div>
          <div class="heatchip" id="heatchip" style="display:none"></div>
        </div>
      </div>
      <svg class="spark" id="spark" viewBox="0 0 460 90" preserveAspectRatio="none" aria-hidden="true">
        <defs><linearGradient id="rg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#9184d9" stop-opacity=".38"/>
          <stop offset="1" stop-color="#9184d9" stop-opacity="0"/></linearGradient></defs>
        <path id="sparkArea" d="" fill="url(#rg)"/>
        <path id="sparkLine" d="" fill="none" stroke="#9184d9" stroke-width="2.5"/>
        <circle id="sparkDot" cx="0" cy="86" r="4" fill="#c7bdff" style="display:none"/>
      </svg>
    </div>
    <div class="heroR">
      <div class="kick">Breakdown</div>
      <div class="bars">
        <div class="brow"><div class="barhd"><span class="lb down">Downs</span><span class="vn" id="t_down">0</span></div><div class="track"><div class="fill down" id="b_down"></div></div></div>
        <div class="brow"><div class="barhd"><span class="lb precision">Precision</span><span class="vn" id="t_precision">0</span></div><div class="track"><div class="fill precision" id="b_precision"></div></div></div>
        <div class="brow"><div class="barhd"><span class="lb finisher">Finishers</span><span class="vn" id="t_finisher">0</span></div><div class="track"><div class="fill finisher" id="b_finisher"></div></div></div>
        <div class="brow"><div class="barhd"><span class="lb assist">Assists</span><span class="vn" id="t_assist">0</span></div><div class="track"><div class="fill assist" id="b_assist"></div></div></div>
      </div>
      <div class="cta">
        <button class="btn primary" id="clip" onclick="saveClip()">Save clip</button>
        <button class="btn ghost" id="addk" onclick="addKill()">+1 Kill</button>
      </div>
    </div>
  </section>

  <section class="tl">
    <div class="tlhd"><div class="kick" style="margin-bottom:0">Match timeline</div><div class="tlmeta" id="tlmeta"></div></div>
    <div class="rail2" id="timeline"><div class="baseline"></div><div class="tlempty" id="tlempty">No kills yet this session</div></div>
  </section>

  <section class="reels" id="reels" style="display:none">
    <div class="kick">Match highlights</div><div id="reellist"></div>
  </section>
  <section class="reels" id="replays" style="display:none">
    <div class="kick">Instant replays</div><div id="replaylist"></div>
  </section>

  <section class="recent">
    <div class="kick">Recent</div>
    <div class="feed" id="feed"><div class="empty">Waiting for kills&hellip;</div></div>
  </section>

  <div class="detect">
    <div class="dbody">
      <div class="kick">Detection</div>
      <div class="dval" id="detectval"><span class="dim">Not measured yet</span></div>
      <div class="dsub" id="detectsub">Press Benchmark to test OCR speed on this PC</div>
    </div>
    <button class="benchbtn" id="benchbtn" onclick="runBench()">Benchmark</button>
  </div>
  <div class="ctrls">
    <button class="mini" id="snd" onclick="toggleSound()">SOUND: OFF</button>
    <button class="mini" id="autoplay" onclick="toggleAutoplay()">AUTO-PLAY: ON</button>
    <button class="mini" id="upd" onclick="doUpdate()">Check for update</button>
    <button class="mini" id="fs" onclick="goFull()">Full screen</button>
  </div>
  <div class="hint" id="updmsg" style="color:var(--dim)"></div>
  <div class="hint" id="hint">iPad: tap Share &rarr; Add to Home Screen for full screen.
    Screen still dimming? Settings &rarr; Display &amp; Brightness &rarr; Auto-Lock &rarr; Never.</div>
  <div class="ver" id="ver"></div>
</div>
<div class="modal" id="modal">
  <video id="reelvid" controls playsinline></video>
  <div class="mlabel" id="mlabel"></div>
  <button class="close" onclick="closeReel()">CLOSE</button>
</div>
<div class="modal" id="helpmodal">
  <div class="settings help">
    <h2>How to use</h2>
    <h3>On your phone or iPad</h3>
    <p>Open <b class="addr" id="helpurl">this PC&rsquo;s IP address, port 8000</b> in Safari or Chrome on a phone or iPad on the same Wi-Fi. The WITNESS window on the PC shows the address under the header.</p>
    <p class="m">Nothing loads? The first time WITNESS runs, Windows asks whether Python may use the network &mdash; allow <b>Private networks</b>. Set your Wi-Fi to Private too (Settings &rarr; Network &amp; internet &rarr; Wi-Fi &rarr; your network &rarr; Network profile type &rarr; Private). Turn off any VPN on the PC, or use the 192.168 address.</p>
    <h3>Kill counter + feed</h3>
    <p>Kills are detected automatically from the game screen. The big number and the feed update within a second or two of each kill.</p>
    <h3>Save clip</h3>
    <p>Saves the last ~30 seconds manually &mdash; for a moment the detector missed or anything else worth keeping.</p>
    <h3>Sound</h3>
    <p>This device dings on every kill. Tap the page once after opening it (browser rule), then use SOUND to toggle.</p>
    <h3>Screen staying awake</h3>
    <p>The same first tap starts a keep-awake trick so the screen doesn't auto-lock. If it still sleeps, set iPad Settings &rarr; Display &amp; Brightness &rarr; Auto-Lock to Never while playing.</p>
    <h3>Match highlights</h3>
    <p>About 30 seconds after you exfil, a highlight reel of that match pops up here: stat card, Play of the Game, then every clip.</p>
    <p class="m">Two versions per match &mdash; clean, and one with an announcer voiceover. Both are tappable in the list.</p>
    <h3>Instant replays</h3>
    <p>Every kill clip appears here seconds after it saves. Tap to rewatch. Keeps the last 20.</p>
    <h3>Archive</h3>
    <p>Past sessions never disappear: the Archive button lists every session with per-match recaps, all its reels and replays, and a "save" link to download any of them for sharing to the group chat.</p>
    <h3>Music on reels</h3>
    <p>Drop an mp3 into the <code>music</code> folder next to the app and reels get a soundtrack automatically.</p>
    <h3>Settings</h3>
    <p>Every toggle applies to the running session immediately &mdash; no restart, no config file editing.</p>
    <h3>Where files go</h3>
    <p class="m">Clips land in your OBS output folder under <code>Marathon Sessions/&lt;date&gt;/</code> &mdash; reels in <code>reels/</code>, plus a screenshot of each exfil screen. The session reel and recap are built when you stop; the match card and stats page go to the app folder&rsquo;s <code>stats/</code>. Vertical Shorts (<code>shorts/</code>) and the session montage are off until you turn them on in Settings.</p>
  </div>
  <div style="height:14px"></div>
  <button class="close" onclick="closeHelp()">CLOSE</button>
</div>
<div class="modal" id="setmodal">
  <div class="settings">
    <h2>Settings</h2>
    <div id="setlist"></div>
    <div class="savedmsg" id="savedmsg">Saved &mdash; applies immediately</div>
  </div>
  <div style="height:14px"></div>
  <button class="close" onclick="closeSettings()">CLOSE</button>
</div>
<script>
  async function tick(){
    try{
      var asked = Date.now();
      var r = await fetch('/status',{cache:'no-store'});
      var d = await r.json();
      if (d.count > lastCount && lastCount >= 0){ ding(); flashCount(); }
      lastCount = d.count;
      document.getElementById('count').textContent = d.count;
      var tags = Object.assign({}, d.tags || {});
      // DOWNS counts +1 presses too (server's count_events), like the desktop tiles
      if (typeof d.downs === 'number') tags.down = d.downs;
      var maxTag = Math.max(1, tags.down||0, tags.precision||0, tags.finisher||0, tags.assist||0);
      ['down','precision','finisher','assist'].forEach(function(t){
        var v = tags[t] || 0;
        document.getElementById('t_'+t).textContent = v;
        document.getElementById('b_'+t).style.width = Math.round(v/maxTag*100) + '%';
      });
      renderSpark(d.events || []);
      renderTimeline(d.events || []);
      var st = document.querySelector('.status');
      st.className = 'status' + (d.running ? ' live' : '');
      document.getElementById('livepill').className = 'livepill' + (d.running ? ' on' : '');
      var hc = document.getElementById('heatchip'), h = d.heat || {};
      if (d.running && h.streak >= 2){
        hc.style.display = 'inline-block';
        var hcol = h.color || '#f5a623';
        hc.style.color = hcol; hc.style.borderColor = hcol;
        hc.textContent = 'STREAK ' + h.streak + (h.label ? ' \u00b7 ' + h.label : '');
      } else { hc.style.display = 'none'; }
      document.getElementById('statustext').textContent = d.running ? 'WATCHING' : 'READY';
      document.getElementById('sub').textContent =
        d.running && d.elapsed ? 'SESSION  ' + fmtElapsed(d.elapsed)
        : (d.running || d.starting ? 'STARTING\\u2026' : 'Press START to begin watching');
      if (d.update){
        var m = document.getElementById('updmsg');
        m.textContent = 'Updater: ' + d.update;
        // Say plainly that a restart is what applies it. Updating does NOT
        // relaunch from here — replacing the process would kill the session
        // being watched, which is how two recaps were lost.
        if (d.update_changed){
          m.textContent += '  —  RESTART WITNESS to apply';
          m.style.color = 'var(--accent-light)';
        }
      }
      var ub = document.getElementById('upd');
      if (ub){ ub.disabled = !!d.updating;
               ub.textContent = d.updating ? 'Checking…' : 'Check for update'; }
      // Save clip / +1 Kill do nothing without a session (the request is
      // dropped at the next START), so they're off until one is running.
      ['clip', 'addk'].forEach(function(id){
        var b = document.getElementById(id);
        b.disabled = !d.running;
        b.title = d.running ? '' : 'Press START first';
      });
      if (d.version){ document.getElementById('ver').textContent = 'WITNESS \\u00b7 ' + d.version; }
      if (d.running && d.detect && typeof d.detect.fps === 'number'){
        var v = d.detect;
        var cls = v.fps >= 4 ? 'ok' : (v.fps >= 2.5 ? '' : 'warn');
        document.getElementById('detectval').innerHTML =
          '<span class="'+cls+'">'+v.fps.toFixed(1)+' fps</span> <span class="dim">\\u00b7 '+(v.device||'')+'</span>';
        document.getElementById('detectsub').textContent =
          (v.engine||'easyocr') + ' \\u00b7 ' +
          (v.vram_mb ? (v.vram_mb/1024).toFixed(1)+' GB VRAM' : (v.device==='CPU'?'CPU':'')) +
          ' \\u00b7 live';
      }
      // End-of-session recap / rebuild / export status — only when NOT running
      // (a fresh START hides it). 'error' has its own red box: a failed build
      // used to vanish (only building/ready were drawn), and 'ready' always
      // said "Everything built", even over a night that built nothing.
      var rc = d.recap || {};
      var rcbox = document.getElementById('recap');
      var rck = RC_TITLES[rc.kind] ? rc.kind : 'recap';
      if (!d.running && (rc.status === 'building' || rc.status === 'ready'
                         || rc.status === 'error')){
        rcbox.style.display = 'block';
        var built = rc.status === 'ready', failed = rc.status === 'error';
        rcbox.className = 'recap' + (built ? ' done' : (failed ? ' err' : ''));
        document.getElementById('rcdot').className =
          'rc-dot ' + (built ? 'done' : (failed ? 'err' : 'pulse'));
        document.getElementById('rctitle').textContent =
          RC_TITLES[rck][built ? 1 : (failed ? 2 : 0)];
        var note;
        if (failed){
          note = (rc.note || 'something went wrong') + (rck === 'export'
            ? ' \\u2014 check the clips you picked and export again'
            : ' \\u2014 open the Archive and press Rebuild reels after fixing it');
        } else if (built){
          note = rc.note || 'Everything built \\u2014 watch your reels in the Archive. Safe to close.';
        } else {
          note = (rc.note || 'working\\u2026') + '   \\u2014   keep WITNESS open until this finishes';
        }
        document.getElementById('rcnote').textContent = note;
        var rep = document.getElementById('rcreport');
        // the WITNESS Report belongs to the night's recap, not to an export
        if (rc.report && rck === 'recap'){ rep.textContent = rc.report; rep.style.display = 'block'; }
        else { rep.style.display = 'none'; }
        document.getElementById('rcarchive').style.display =
          (built || failed) ? 'inline-block' : 'none';
      } else { rcbox.style.display = 'none'; }
      // The phone/iPad address, on the PC only: the phone already knows it,
      // and the header there is laid out for the iPad. No scheme in the data;
      // this page's own (http:) is the right one.
      var lan = (d.lan_urls || []).map(function(a){ return location.protocol + '//' + a; });
      var pu = document.getElementById('phoneurl');
      if (onThePC() && lan.length){
        var lanSig = lan.join(' ');
        if (pu.dataset.sig !== lanSig){       // rebuild only on change
          pu.dataset.sig = lanSig;
          pu.textContent = 'On your phone/iPad (same Wi-Fi): ';
          var addr = document.createElement('b');
          addr.textContent = lan[0];
          pu.appendChild(addr);
          if (lan.length > 1){
            var also = document.createElement('span');
            also.className = 'also';
            also.textContent = '   \\u00b7  or ' + lan.slice(1).join(', ');
            pu.appendChild(also);
          }
        }
        pu.style.display = 'block';
      } else { pu.style.display = 'none'; }
      if (lan.length){ document.getElementById('helpurl').textContent = lan.join('  or  '); }
      // A START that failed says why (the launcher sets last_error). Shown only
      // while idle, so it can never cover the kill feed of a running session.
      var eb = document.getElementById('errbox');
      if (d.last_error && !d.running && !d.starting){
        document.getElementById('errnote').textContent = d.last_error;
        eb.style.display = 'block';
      } else { eb.style.display = 'none'; }
      runStarting = !!d.starting;
      // The START we sent has failed: stop waiting for the 12 s release. Only a
      // status asked for AFTER the press returned counts, so an old error
      // can't release a fresh press.
      if (runBusy && runWant && runPostedAt && asked >= runPostedAt
          && !d.running && !d.starting && d.last_error){ runBusy = false; }
      if (runBusy && d.running === runWant){ runBusy = false; }
      var rb = document.getElementById('runbtn');
      if (d.starting && !d.running){
        // still starting (OBS launch + connect can take a minute): say so on
        // every device, including one that didn't press the button
        rb.className = 'runbtn busy';
        rb.textContent = 'STARTING\\u2026';
      } else if (!runBusy){
        rb.className = 'runbtn' + (d.running ? ' on' : '');
        rb.textContent = d.running ? 'STOP' : 'START';
      }
      var feed = document.getElementById('feed');
      if(!d.events.length){ feed.innerHTML = '<div class="empty">Waiting for kills...</div>'; }
      else {
        feed.innerHTML = d.events.map(function(e){
          var sk = e.tag==='precision' ? '<img class="skull" src="/skull.png">' : '';
          return '<div class="row '+e.tag+'"><span class="badge">'+e.tag+'</span>'+sk+
                 '<span>'+e.text.replace(/</g,'&lt;')+'</span>'+
                 '<span class="t">'+e.time+'</span></div>';
        }).join('');
      }
      var reels = d.reels || [];
      var box = document.getElementById('reels');
      var sig = reels.map(function(r){ return r.i+r.label; }).join('|');
      if (reels.length){
        box.style.display = 'block';
        if (sig !== reelSig){  // only re-render on change so taps aren't eaten
          document.getElementById('reellist').innerHTML = reels.map(function(r){
            return '<div class="reelrow" onclick="openReel('+r.i+',this.dataset.label)" data-label="'+
                   r.label.replace(/"/g,'')+'"><span class="play">&#9658;</span>'+
                   '<span>'+r.label.replace(/</g,'&lt;')+'</span>'+
                   '<span class="t">'+r.time+'</span></div>';
          }).join('');
        }
        if (autoplayOn && reels.length > lastReels && lastReels >= 0) openReel(reels.length-1, reels[reels.length-1].label);
        lastReels = reels.length;
      } else { box.style.display='none'; lastReels = 0; }
      reelSig = sig;
      var reps = d.replays || [];
      var rbox = document.getElementById('replays');
      var rsig = reps.map(function(r){ return r.i; }).join('|');
      if (reps.length){
        rbox.style.display = 'block';
        if (rsig !== repSig){
          document.getElementById('replaylist').innerHTML = reps.map(function(r){
            return '<div class="reelrow" onclick="openReplay('+r.i+',this.dataset.label)" data-label="'+
                   r.label.replace(/"/g,'')+'"><span class="play">&#9658;</span>'+
                   '<span>'+r.label.replace(/</g,'&lt;')+'</span>'+
                   '<span class="t">'+r.time+'</span></div>';
          }).join('');
        }
      } else { rbox.style.display='none'; }
      repSig = rsig;
    }catch(err){ document.getElementById('statustext').textContent='OFFLINE'; }
    setTimeout(tick, 1000);
  }
  var lastReels = -1, reelSig = '', repSig = '', lastCount = -1;
  // The desktop window (pywebview injects window.pywebview, sometimes after
  // load, so this is asked every tick) or a browser on the PC itself.
  function onThePC(){
    return !!window.pywebview || location.hostname === 'localhost'
           || location.hostname === '127.0.0.1';
  }
  // recap box titles per writer: [building, ready, error]
  var RC_TITLES = {
    recap:   ['Building your recap\\u2026', 'Recap ready', 'Recap had problems'],
    rebuild: ['Rebuilding reels\\u2026', 'Rebuild done', 'Rebuild had problems'],
    export:  ['Exporting your reel\\u2026', 'Export ready', 'Export failed']};
  tick();

  // --- kill ding (WebAudio, unlocked by the first tap anywhere) ---
  var audioCtx = null;
  // Sound is opt-in: the game popup + count flash + CLIP SAVED chip already
  // confirm kills visually, so the ding defaults off. The SOUND button
  // enables it and the choice sticks.
  var soundOn = localStorage.getItem('killSound') === 'on';
  document.getElementById('snd').textContent = 'SOUND: ' + (soundOn ? 'ON' : 'OFF');
  function initAudio(){
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();
    } catch(e){}
  }
  document.addEventListener('pointerdown', initAudio);
  function ding(){
    if (!soundOn || !audioCtx) return;
    var t = audioCtx.currentTime;
    [[880, 0], [1318.5, 0.09]].forEach(function(p){
      var o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = 'sine'; o.frequency.value = p[0];
      o.connect(g); g.connect(audioCtx.destination);
      g.gain.setValueAtTime(0.0001, t + p[1]);
      g.gain.exponentialRampToValueAtTime(0.35, t + p[1] + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + p[1] + 0.28);
      o.start(t + p[1]); o.stop(t + p[1] + 0.32);
    });
  }
  function toggleSound(){
    soundOn = !soundOn;
    localStorage.setItem('killSound', soundOn ? 'on' : 'off');
    document.getElementById('snd').textContent = 'SOUND: ' + (soundOn ? 'ON' : 'OFF');
    initAudio();
    if (soundOn) ding();  // audible confirmation it's unlocked + on
  }

  // Auto-play new match reels in the modal (per device — leave it on for the
  // iPad, turn it off on the desktop so a reel doesn't pop up mid-session).
  var autoplayOn = localStorage.getItem('reelAutoplay') !== 'off';
  document.getElementById('autoplay').textContent = 'AUTO-PLAY: ' + (autoplayOn ? 'ON' : 'OFF');
  function toggleAutoplay(){
    autoplayOn = !autoplayOn;
    localStorage.setItem('reelAutoplay', autoplayOn ? 'on' : 'off');
    document.getElementById('autoplay').textContent = 'AUTO-PLAY: ' + (autoplayOn ? 'ON' : 'OFF');
  }

  function openReel(i, label){
    var v = document.getElementById('reelvid');
    document.getElementById('mlabel').textContent = label || '';
    v.src = '/reel/'+i;
    document.getElementById('modal').classList.add('open');
    v.play().catch(function(){});
  }
  function openReplay(i, label){
    var v = document.getElementById('reelvid');
    document.getElementById('mlabel').textContent = label || '';
    v.src = '/replay/'+i;
    document.getElementById('modal').classList.add('open');
    v.play().catch(function(){});
  }
  function closeReel(){
    var v = document.getElementById('reelvid');
    v.pause(); v.removeAttribute('src'); v.load();
    document.getElementById('modal').classList.remove('open');
  }

  // --- settings panel ---
  var MODE_DATA = [];
  function modeIsActive(m, settings){
    return Object.keys(m.set).every(function(k){ return settings[k] === m.set[k]; });
  }
  async function applyMode(key){
    var m = MODE_DATA.find(function(x){ return x.key === key; });
    if (!m) return;
    try {
      await fetch('/config', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(m.set)});
      var msg = document.getElementById('savedmsg');
      msg.classList.add('show');
      setTimeout(function(){ msg.classList.remove('show'); }, 1800);
      openSettings();   // re-render switches + active-mode highlight
    } catch(e){}
  }
  async function openSettings(){
    try {
      var r = await fetch('/config', {cache:'no-store'});
      var d = await r.json();
      MODE_DATA = d.modes || [];
      var modes = MODE_DATA.map(function(m){
        var on = modeIsActive(m, d.settings);
        return '<div class="moderow"><button class="modebtn'+(on ? ' active' : '')+'"'+
               ' onclick="applyMode(\\''+m.key+'\\')">'+m.label+'</button>'+
               '<span class="modedesc">'+m.desc+'</span></div>';
      }).join('');
      if (modes) modes = '<div class="modehead">MODES — one tap, tweak after</div>'+modes+
                         '<div class="modehead" style="margin-top:14px">EVERYTHING ELSE</div>';
      var html = modes + d.meta.map(function(m){
        var key = m[0], label = m[1], val = d.settings[key];
        if (typeof val === 'boolean'){
          return '<div class="setrow"><span>'+label+'</span>'+
                 '<label class="switch"><input type="checkbox" data-key="'+key+'"'+
                 (val ? ' checked' : '')+' onchange="saveSetting(this)">'+
                 '<span class="slider"></span></label></div>';
        }
        if (key.indexOf('volume') !== -1){  // 0-1 range slider with live value
          return '<div class="setrow"><span>'+label+'</span>'+
                 '<span style="display:flex;align-items:center;gap:10px">'+
                 '<input type="range" min="0" max="1" step="0.02" data-key="'+key+'" value="'+val+'"'+
                 ' oninput="document.getElementById(\\'v_'+key+'\\').textContent=(+this.value).toFixed(2)"'+
                 ' onchange="saveSetting(this)">'+
                 '<span id="v_'+key+'" style="width:34px;text-align:right">'+(+val).toFixed(2)+'</span>'+
                 '</span></div>';
        }
        return '<div class="setrow"><span>'+label+'</span>'+
               '<input type="number" step="0.5" min="0" data-key="'+key+'" value="'+val+'"'+
               ' onchange="saveSetting(this)"></div>';
      }).join('');
      document.getElementById('setlist').innerHTML = html;
      document.getElementById('setmodal').classList.add('open');
    } catch(e){}
  }
  function closeSettings(){
    document.getElementById('setmodal').classList.remove('open');
  }
  function openHelp(){ document.getElementById('helpmodal').classList.add('open'); }
  function closeHelp(){ document.getElementById('helpmodal').classList.remove('open'); }
  async function saveSetting(el){
    var key = el.dataset.key;
    var val = el.type === 'checkbox' ? el.checked : parseFloat(el.value);
    try {
      await fetch('/config', {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(Object.fromEntries([[key, val]]))});
      var m = document.getElementById('savedmsg');
      m.classList.add('show');
      setTimeout(function(){ m.classList.remove('show'); }, 1800);
    } catch(e){}
  }

  var runBusy = false, runWant = false, runPostedAt = 0, runStarting = false;
  async function toggleRun(){
    var rb = document.getElementById('runbtn');
    if (runBusy || rb.className.indexOf('busy') !== -1) return;
    var starting = rb.textContent === 'START';
    runWant = starting; runBusy = true; runPostedAt = 0;
    rb.className = 'runbtn busy';
    rb.textContent = starting ? 'STARTING\\u2026' : 'STOPPING\\u2026';
    try { await fetch(starting ? '/start' : '/stop', {method:'POST'}); } catch(e){}
    runPostedAt = Date.now();
    // tick() clears runBusy once d.running matches what we asked for, or as
    // soon as the launcher reports that the START failed
    setTimeout(releaseRun, 12000);  // safety release
  }
  // The safety release waits while the launcher says a START is still in
  // progress: flipping back to START mid-way just invited a second press.
  function releaseRun(){
    if (runStarting){ setTimeout(releaseRun, 2000); return; }
    runBusy = false;
  }

  // Both buttons report what the server said: they used to flash green
  // SAVING.../COUNTED with no session running, while the request was dropped.
  async function pressAction(id, path, okText, idleText){
    var btn = document.getElementById(id);
    if (btn.disabled) return;
    var said = okText, cls = 'fired';
    try {
      var j = await (await fetch(path, {method:'POST'})).json();
      if (j && j.ok === false){ said = 'NOT RUNNING'; cls = 'refused'; }
    } catch(e){ said = 'OFFLINE'; cls = 'refused'; }
    btn.textContent = said;
    btn.classList.add(cls);
    setTimeout(function(){ btn.textContent = idleText; btn.classList.remove(cls); }, 1500);
  }
  function saveClip(){ return pressAction('clip', '/clip', 'SAVING...', 'SAVE CLIP'); }

  async function runBench(){
    var btn = document.getElementById('benchbtn');
    var val = document.getElementById('detectval');
    var sub = document.getElementById('detectsub');
    var label = btn.textContent;
    btn.disabled = true; btn.textContent = 'Running\\u2026';
    val.innerHTML = '<span class="dim">Measuring\\u2026</span>';
    sub.textContent = 'OCRing a test frame — a few seconds';
    try {
      var r = await fetch('/benchmark', {method:'POST'});
      var d = await r.json();
      if (d.error){
        val.innerHTML = '<span class="warn">Benchmark failed</span>';
        sub.textContent = String(d.error).slice(0,80);
      } else {
        var cls = d.keeps_up ? 'ok' : 'warn';
        val.innerHTML = '<span class="'+cls+'">'+Number(d.throughput_fps).toFixed(1)+
          ' fps</span> <span class="dim">OCR \\u00b7 '+d.device+'</span>';
        var vram = d.vram_mb ? (d.vram_mb/1024).toFixed(1)+' GB VRAM' : 'CPU';
        var verdict = d.comfortable ? 'plenty of headroom'
          : (d.keeps_up ? 'keeps up with '+d.poll_fps+' fps'
                        : 'below '+d.poll_fps+' fps target — try ocr_max_dim: 640');
        sub.textContent = d.engine+' \\u00b7 '+d.avg_ms+' ms/frame \\u00b7 '+vram+' \\u00b7 '+verdict;
      }
    } catch(e){
      val.innerHTML = '<span class="warn">Benchmark failed</span>';
      sub.textContent = 'could not reach the server';
    }
    btn.disabled = false; btn.textContent = label;
  }

  function addKill(){ return pressAction('addk', '/addkill', 'COUNTED', '+1 KILL'); }

  function flashCount(){
    var el = document.getElementById('count');
    el.classList.remove('pop'); void el.offsetWidth;  // restart the animation
    el.classList.add('pop');
  }

  // Kill tags (down/precision/finisher/assist) only — alerts/notices don't
  // belong on the rate graph or the timeline.
  var KILL_TAGS = {down:1, precision:1, finisher:1, assist:1};
  function killEvents(events){
    // events arrive newest-first; return oldest-first for left-to-right plots.
    return events.filter(function(e){ return KILL_TAGS[e.tag]; }).reverse();
  }

  function renderSpark(events){
    var kills = killEvents(events);
    var line = document.getElementById('sparkLine');
    var area = document.getElementById('sparkArea');
    var dot = document.getElementById('sparkDot');
    var n = kills.length, W = 460, top = 14, bot = 84;
    if (!n){ line.setAttribute('d',''); area.setAttribute('d',''); dot.style.display='none'; return; }
    var pts = [];
    for (var i=0;i<n;i++){
      var x = n>1 ? i/(n-1)*W : 0;
      var y = bot - ((i+1)/n)*(bot-top);   // cumulative kills, last point highest
      pts.push([Math.round(x), Math.round(y)]);
    }
    var d = 'M' + pts.map(function(p){ return p[0]+','+p[1]; }).join(' L');
    line.setAttribute('d', d);
    var last = pts[n-1], first = pts[0];
    area.setAttribute('d', d + ' L'+last[0]+',90 L'+first[0]+',90 Z');
    dot.setAttribute('cx', last[0]); dot.setAttribute('cy', last[1]); dot.style.display='';
  }

  var TL_LETTER = {down:'D', precision:'P', finisher:'F', assist:'A'};
  function renderTimeline(events){
    var kills = killEvents(events);
    var rail = document.getElementById('timeline');
    var empty = document.getElementById('tlempty');
    var meta = document.getElementById('tlmeta');
    Array.prototype.slice.call(rail.querySelectorAll('.mk')).forEach(function(m){ m.remove(); });
    var shown = kills.slice(-12);
    if (!shown.length){ empty.style.display=''; meta.textContent=''; return; }
    empty.style.display='none';
    meta.textContent = kills.length + ' kill' + (kills.length===1?'':'s');
    shown.forEach(function(e, i){
      var mk = document.createElement('div');
      mk.className = 'mk ' + e.tag;
      mk.style.left = ((i+0.5)/shown.length*100) + '%';
      mk.textContent = TL_LETTER[e.tag] || '';
      mk.title = (e.text||'') + '  ' + (e.time||'');
      rail.appendChild(mk);
    });
  }

  function fmtElapsed(s){
    var h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = s%60;
    var mm = (h ? String(m).padStart(2,'0') : m) + ':' + String(sec).padStart(2,'0');
    return h ? h + ':' + mm : mm;
  }

  // Keep the screen awake. The Wake Lock API only works on secure pages
  // (https/localhost) — over plain http on the LAN, iOS ignores it. Fallback:
  // a looping video WITH a silent audio track, played unmuted at volume 0 and
  // started by the first tap. iOS only holds the screen for "real" media —
  // muted video-only playback gets ignored on newer iOS, which is why apps
  // that play actual media never sleep but the old trick did.
  var wl = null, nsVid = null;
  async function keepAwake(){
    try { if ('wakeLock' in navigator) { wl = await navigator.wakeLock.request('screen'); } } catch(e){}
  }
  function keepAwakeVideo(){
    if (nsVid) { nsVid.play().catch(function(){}); return; }
    var v = document.createElement('video');
    // full volume, NOT zero: the track is pure silence so nothing is heard,
    // but iOS treats volume-0 as muted media and lets the screen sleep anyway
    v.setAttribute('playsinline', ''); v.loop = true; v.volume = 1.0;
    v.style.cssText = 'position:fixed;bottom:0;right:0;width:2px;height:2px;opacity:0.01;pointer-events:none;z-index:1;';
    v.src = 'data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAyUbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAJxAAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAwAAA9l0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAJxAAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAACcQAAAAAAABAAAAAANRbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAAAoAAABkABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAAC/G1pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAArxzdGJsAAAAuHN0c2QAAAAAAAAAAQAAAKhhdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2Mi4yOC4xMDIgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAALmF2Y0MBQsAK/+EAFmdCwArZHsBEAAADAAQAAAMAKDxImSABAAVoy4PLIAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAAAAAAAANkAAAAAAAAABhzdHRzAAAAAAAAAAEAAAAyAAAIAAAAABRzdHNzAAAAAAAAAAEAAAABAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAABAAAAAQAAANxzdHN6AAAAAAAAAAAAAAAyAAACgwAAAAoAAAAKAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAAAJAAAACQAAAAkAAADYc3RjbwAAAAAAAAAyAAAM2QAAD3AAAA+KAAAPpAAAD8EAAA/aAAAP8wAAEBAAABApAAAQQgAAEF8AABB4AAAQkQAAEKoAABDHAAAQ4AAAEPkAABEWAAARLwAAEUgAABFlAAARfgAAEZcAABG0AAARzQAAEeYAABH/AAASHAAAEjUAABJOAAASawAAEoQAABKdAAASugAAEtMAABLsAAATCQAAEyIAABM7AAATVAAAE3EAABOKAAATowAAE8AAABPZAAAT8gAAFA8AABQoAAAUQQAAFF4AAAfldHJhawAAAFx0a2hkAAAAAwAAAAAAAAAAAAAAAgAAAAAAACcQAAAAAAAAAAAAAAABAQAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAJGVkdHMAAAAcZWxzdAAAAAAAAAABAAAnEAAABAAAAQAAAAAHXW1kaWEAAAAgbWRoZAAAAAAAAAAAAAAAAAAAViIAA2FUVcQAAAAAAC1oZGxyAAAAAAAAAABzb3VuAAAAAAAAAAAAAAAAU291bmRIYW5kbGVyAAAABwhtaW5mAAAAEHNtaGQAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAABsxzdGJsAAAAfnN0c2QAAAAAAAAAAQAAAG5tcDRhAAAAAAAAAAEAAAAAAAAAAAABABAAAAAAViIAAAAAADZlc2RzAAAAAAOAgIAlAAIABICAgBdAFQAAAAAAH0AAAALABYCAgAUTiFblAAaAgIABAgAAABRidHJ0AAAAAAAAH0AAAALAAAAAIHN0dHMAAAAAAAAAAgAAANgAAAQAAAAAAQAAAVQAAAGcc3RzYwAAAAAAAAAhAAAAAQAAAAEAAAABAAAAAgAAAAUAAAABAAAAAwAAAAQAAAABAAAABQAAAAUAAAABAAAABgAAAAQAAAABAAAACAAAAAUAAAABAAAACQAAAAQAAAABAAAACwAAAAUAAAABAAAADAAAAAQAAAABAAAADwAAAAUAAAABAAAAEAAAAAQAAAABAAAAEgAAAAUAAAABAAAAEwAAAAQAAAABAAAAFQAAAAUAAAABAAAAFgAAAAQAAAABAAAAGAAAAAUAAAABAAAAGQAAAAQAAAABAAAAHAAAAAUAAAABAAAAHQAAAAQAAAABAAAAHwAAAAUAAAABAAAAIAAAAAQAAAABAAAAIgAAAAUAAAABAAAAIwAAAAQAAAABAAAAJQAAAAUAAAABAAAAJgAAAAQAAAABAAAAKQAAAAUAAAABAAAAKgAAAAQAAAABAAAALAAAAAUAAAABAAAALQAAAAQAAAABAAAALwAAAAUAAAABAAAAMAAAAAQAAAABAAAAMgAAAAUAAAABAAAAMwAAAAQAAAABAAADeHN0c3oAAAAAAAAAAAAAANkAAAAVAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAA3HN0Y28AAAAAAAAAMwAADMQAAA9cAAAPegAAD5QAAA+tAAAPygAAD+MAAA/8AAAQGQAAEDIAABBLAAAQaAAAEIEAABCaAAAQswAAENAAABDpAAARAgAAER8AABE4AAARUQAAEW4AABGHAAARoAAAEb0AABHWAAAR7wAAEggAABIlAAASPgAAElcAABJ0AAASjQAAEqYAABLDAAAS3AAAEvUAABMSAAATKwAAE0QAABNdAAATegAAE5MAABOsAAATyQAAE+IAABP7AAAUGAAAFDEAABRKAAAUZwAAABpzZ3BkAQAAAHJvbGwAAAACAAAAAf//AAAAHHNiZ3AAAAAAcm9sbAAAAAEAAADZAAAAAQAAAGJ1ZHRhAAAAWm1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAAAAAAAAAALWlsc3QAAAAlqXRvbwAAAB1kYXRhAAAAAQAAAABMYXZmNjIuMTIuMTAyAAAACGZyZWUAAAe7bWRhdN4CAExhdmM2Mi4yOC4xMDIAAjBADgAAAnAGBf//bNxF6b3m2Ui3lizYINkj7u94MjY0IC0gY29yZSAxNjUgcjMyMjIgYjM1NjA1YSAtIEguMjY0L01QRUctNCBBVkMgY29kZWMgLSBDb3B5bGVmdCAyMDAzLTIwMjUgLSBodHRwOi8vd3d3LnZpZGVvbGFuLm9yZy94MjY0Lmh0bWwgLSBvcHRpb25zOiBjYWJhYz0wIHJlZj0zIGRlYmxvY2s9MTowOjAgYW5hbHlzZT0weDE6MHgxMTEgbWU9aGV4IHN1Ym1lPTcgcHN5PTEgcHN5X3JkPTEuMDA6MC4wMCBtaXhlZF9yZWY9MSBtZV9yYW5nZT0xNiBjaHJvbWFfbWU9MSB0cmVsbGlzPTEgOHg4ZGN0PTAgY3FtPTAgZGVhZHpvbmU9MjEsMTEgZmFzdF9wc2tpcD0xIGNocm9tYV9xcF9vZmZzZXQ9LTIgdGhyZWFkcz0xIGxvb2thaGVhZF90aHJlYWRzPTEgc2xpY2VkX3RocmVhZHM9MCBucj0wIGRlY2ltYXRlPTEgaW50ZXJsYWNlZD0wIGJsdXJheV9jb21wYXQ9MCBjb25zdHJhaW5lZF9pbnRyYT0wIGJmcmFtZXM9MCB3ZWlnaHRwPTAga2V5aW50PTI1MCBrZXlpbnRfbWluPTUgc2NlbmVjdXQ9NDAgaW50cmFfcmVmcmVzaD0wIHJjX2xvb2thaGVhZD00MCByYz1jcmYgbWJ0cmVlPTEgY3JmPTIzLjAgcWNvbXA9MC42MCBxcG1pbj0wIHFwbWF4PTY5IHFwc3RlcD00IGlwX3JhdGlvPTEuNDAgYXE9MToxLjAwAIAAAAALZYiEBHyYoAA2I4ABGCAHARggBwEYIAcBGCAHARggBwAAAAZBmjgI+oABGCAHARggBwEYIAcBGCAHAAAABkGaVAI+oAEYIAcBGCAHARggBwEYIAcAAAAFQZpgEfUBGCAHARggBwEYIAcBGCAHARggBwAAAAVBmoAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZqgEfUBGCAHARggBwEYIAcBGCAHAAAABUGawBH1ARggBwEYIAcBGCAHARggBwEYIAcAAAAFQZrgEfUBGCAHARggBwEYIAcBGCAHAAAABUGbABH1ARggBwEYIAcBGCAHARggBwAAAAVBmyAR9QEYIAcBGCAHARggBwEYIAcBGCAHAAAABUGbQBH1ARggBwEYIAcBGCAHARggBwAAAAVBm2AR9QEYIAcBGCAHARggBwEYIAcAAAAFQZuAEfUBGCAHARggBwEYIAcBGCAHAAAABUGboBH1ARggBwEYIAcBGCAHARggBwEYIAcAAAAFQZvAEfUBGCAHARggBwEYIAcBGCAHAAAABUGb4BH1ARggBwEYIAcBGCAHARggBwAAAAVBmgAR9QEYIAcBGCAHARggBwEYIAcBGCAHAAAABUGaIBH1ARggBwEYIAcBGCAHARggBwAAAAVBmkAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZpgEfUBGCAHARggBwEYIAcBGCAHARggBwAAAAVBmoAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZqgEfUBGCAHARggBwEYIAcBGCAHAAAABUGawBH1ARggBwEYIAcBGCAHARggBwEYIAcAAAAFQZrgEfUBGCAHARggBwEYIAcBGCAHAAAABUGbABH1ARggBwEYIAcBGCAHARggBwAAAAVBmyAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZtAEfUBGCAHARggBwEYIAcBGCAHARggBwAAAAVBm2AR9QEYIAcBGCAHARggBwEYIAcAAAAFQZuAEfUBGCAHARggBwEYIAcBGCAHAAAABUGboBH1ARggBwEYIAcBGCAHARggBwEYIAcAAAAFQZvAEfUBGCAHARggBwEYIAcBGCAHAAAABUGb4BH1ARggBwEYIAcBGCAHARggBwAAAAVBmgAR9QEYIAcBGCAHARggBwEYIAcBGCAHAAAABUGaIBH1ARggBwEYIAcBGCAHARggBwAAAAVBmkAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZpgEfUBGCAHARggBwEYIAcBGCAHARggBwAAAAVBmoAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZqgEfUBGCAHARggBwEYIAcBGCAHAAAABUGawBH1ARggBwEYIAcBGCAHARggBwAAAAVBmuAR9QEYIAcBGCAHARggBwEYIAcBGCAHAAAABUGbABH1ARggBwEYIAcBGCAHARggBwAAAAVBmyAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZtAEfUBGCAHARggBwEYIAcBGCAHARggBwAAAAVBm2AR9QEYIAcBGCAHARggBwEYIAcAAAAFQZuAEfUBGCAHARggBwEYIAcBGCAHAAAABUGboBH1ARggBwEYIAcBGCAHARggBwEYIAcAAAAFQZvAEfUBGCAHARggBwEYIAcBGCAHAAAABUGb4BH1ARggBwEYIAcBGCAHARggBwAAAAVBmgAQ9QEYIAcBGCAHARggBwEYIAcBGCAHAAAABUGaID/UARggBwEYIAcBGCAHARggBw==';
    document.body.appendChild(v);
    v.play().then(function(){ nsVid = v; }).catch(function(){ v.remove(); });
  }
  document.addEventListener('pointerdown', keepAwakeVideo);
  document.addEventListener('visibilitychange', function(){
    if (document.visibilityState === 'visible'){ keepAwake(); if (nsVid) nsVid.play().catch(function(){}); }
  });
  keepAwake();
  // Watchdog: iOS quietly pauses background media after a while, which
  // releases the screen hold — restart it every few seconds.
  setInterval(function(){
    if (nsVid && nsVid.paused) nsVid.play().catch(function(){});
    if (wl && wl.released) keepAwake();
  }, 5000);

  function doUpdate(){
    var b = document.getElementById('upd');
    b.disabled = true; b.textContent = 'Checking…';
    fetch('/update', {method:'POST'}).then(function(r){ return r.json(); })
      .then(function(j){
        if (!j.ok){
          document.getElementById('updmsg').textContent = 'Updater: ' + j.error;
          b.disabled = false; b.textContent = 'Check for update';
        }
        // on success the /status poll takes over and reports the result
      })
      .catch(function(e){
        document.getElementById('updmsg').textContent = 'Updater: ' + e;
        b.disabled = false; b.textContent = 'Check for update';
      });
  }

  function goFull(){
    var el = document.documentElement;
    if (el.requestFullscreen) el.requestFullscreen();
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
  }
  // Hide the Full-screen button + iPad hint where they don't apply: the
  // desktop app (pywebview injects window.pywebview and has its own maximize
  // button) and iOS home-screen standalone mode.
  if (window.pywebview || window.navigator.standalone
      || window.matchMedia('(display-mode: standalone)').matches){
    document.getElementById('fs').style.display='none';
    document.getElementById('hint').style.display='none';
  }
</script></body></html>"""


CLIPS_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>WITNESS &mdash; Clips</title>
<style>
  :root { --bg:#0e0e16; --panel:#12181d; --line:#232d34; --text:#e9e9ed;
    --muted:#8a90a0; --accent:#9184d9; --danger:#ff4d3d; --warn:#f5a623;
    --sec:#b9b9c6; --dim:#5f6572; --accent-light:#c7bdff; --ok:#5ac47d;
    --surface:rgba(255,255,255,.03); --sborder:rgba(255,255,255,.07);
    --ground:radial-gradient(120% 80% at 82% -10%,#20203a 0%,#14141f 46%,#0e0e16 100%);
    --ui:'Inter',system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    --mono:'JetBrains Mono',ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:var(--ui);
    -webkit-font-smoothing:antialiased; }
  .wrap { min-height:100vh; background:var(--ground); max-width:1240px; margin:0 auto;
    padding:calc(20px + env(safe-area-inset-top)) 26px calc(120px + env(safe-area-inset-bottom)); }
  .top { display:flex; align-items:center; gap:13px; margin-bottom:18px; flex-wrap:wrap; }
  .wm { font-weight:800; letter-spacing:.16em; font-size:15px; }
  .nav { display:flex; gap:4px; flex-wrap:wrap; }
  .nav a { font:500 12.5px var(--ui); color:var(--muted); padding:6px 10px;
    border-radius:7px; text-decoration:none; }
  .nav a:hover { background:var(--surface); color:var(--text); }
  .nav a.on { background:var(--surface); color:var(--accent-light); }
  h1 { font:800 22px var(--ui); margin:0 0 4px; letter-spacing:.01em; }
  .sub { color:var(--muted); font-size:12.5px; margin:0 0 18px; }
  .bar { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }
  .mini { font:600 11.5px var(--ui); letter-spacing:.06em; text-transform:uppercase;
    color:var(--text); background:var(--surface); border:1px solid var(--sborder);
    border-radius:8px; padding:7px 12px; cursor:pointer; }
  .mini:hover { border-color:var(--accent); color:var(--accent-light); }
  .mini:disabled { opacity:.6; cursor:default; }
  .mini.go { background:var(--accent); border-color:var(--accent); color:#fff; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:12px; margin-bottom:10px; display:grid;
    grid-template-columns:200px 1fr; gap:14px; align-items:start; }
  .card.sel { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; }
  .thumbwrap { position:relative; border-radius:8px; overflow:hidden;
    background:#000; aspect-ratio:16/9; cursor:pointer; }
  .thumbwrap img { width:100%; height:100%; object-fit:cover; display:block; }
  .thumbwrap .play { position:absolute; inset:0; display:flex; align-items:center;
    justify-content:center; font-size:34px; color:#fff; opacity:.85;
    text-shadow:0 2px 10px rgba(0,0,0,.8); }
  .ord { position:absolute; top:6px; left:6px; background:var(--accent);
    color:#fff; font:800 11px var(--mono); padding:2px 7px; border-radius:20px; }
  .hd { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
  .who { font:800 17px var(--ui); }
  .who.none { color:var(--dim); font-weight:600; font-style:italic; }
  .clock { font:500 11.5px var(--mono); color:var(--muted); }
  .chips { display:flex; gap:5px; flex-wrap:wrap; margin:7px 0; }
  .chip { font:700 10px var(--ui); letter-spacing:.08em; padding:3px 8px;
    border-radius:20px; border:1px solid transparent; }
  .chip.kill { background:rgba(145,132,217,.16); color:var(--accent-light);
    border-color:rgba(145,132,217,.4); }
  .chip.prec { background:rgba(245,166,35,.14); color:#f5c76a;
    border-color:rgba(245,166,35,.38); }
  .chip.elim { background:rgba(255,77,61,.13); color:#ff8a7a;
    border-color:rgba(255,77,61,.35); }
  .chip.assist { background:rgba(255,255,255,.05); color:var(--muted);
    border-color:var(--sborder); }
  .chip.manual { background:rgba(90,196,125,.14); color:#7fd79b;
    border-color:rgba(90,196,125,.38); }
  .summary { color:var(--sec); font-size:13.5px; margin:2px 0 9px; }
  .track { position:relative; height:22px; background:var(--surface);
    border:1px solid var(--sborder); border-radius:6px; margin:8px 0 6px; }
  .mk { position:absolute; top:-3px; width:2px; height:28px; background:var(--danger);
    border-radius:2px; }
  .mk.man { background:var(--ok); }
  .mk span { position:absolute; top:-16px; left:50%; transform:translateX(-50%);
    font:700 9px var(--mono); color:var(--danger); white-space:nowrap; }
  .mk.man span { color:var(--ok); }
  .region { position:absolute; top:0; bottom:0; background:rgba(145,132,217,.22);
    border-left:2px solid var(--accent); border-right:2px solid var(--accent); }
  .trim { display:flex; gap:10px; align-items:center; flex-wrap:wrap;
    font:500 11.5px var(--ui); color:var(--muted); }
  .trim input[type=number] { width:66px; background:var(--surface); color:var(--text);
    border:1px solid var(--sborder); border-radius:6px; padding:4px 6px;
    font:500 12px var(--mono); }
  .row2 { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:8px; }
  label.ck { display:flex; gap:7px; align-items:center; cursor:pointer;
    font:600 12px var(--ui); }
  .dock { position:fixed; left:0; right:0; bottom:0; background:rgba(14,14,22,.97);
    border-top:1px solid var(--line); padding:12px 26px
    calc(12px + env(safe-area-inset-bottom)); backdrop-filter:blur(8px); }
  .dock .inner { max-width:1240px; margin:0 auto; display:flex; gap:10px;
    align-items:center; flex-wrap:wrap; }
  .dock input[type=text] { background:var(--surface); color:var(--text);
    border:1px solid var(--sborder); border-radius:8px; padding:7px 10px;
    font:500 13px var(--ui); min-width:170px; }
  .count { font:700 13px var(--ui); color:var(--accent-light); }
  .msg { font:500 12.5px var(--ui); color:var(--muted); }
  .empty { color:var(--muted); padding:30px 0; }
  dialog { background:var(--panel); color:var(--text); border:1px solid var(--line);
    border-radius:14px; padding:0; max-width:min(96vw,1000px); }
  dialog::backdrop { background:rgba(0,0,0,.75); }
  dialog video { width:100%; display:block; border-radius:14px 14px 0 0; background:#000; }
  dialog .db { padding:12px 16px; display:flex; gap:10px; align-items:center;
    justify-content:space-between; flex-wrap:wrap; }
  @media (max-width:720px){ .card { grid-template-columns:1fr; } }
</style></head><body><div class="wrap">
  <div class="top">
    <span class="wm">WITNESS</span>
    <nav class="nav">
      <a href="/">Live</a><a href="/clips" class="on">Clips</a>
      <a href="/stats">Stats</a><a href="/archive">Archive</a>
    </nav>
  </div>
  <h1>Clip viewer</h1>
  <p class="sub" id="sub">Loading&hellip;</p>
  <div class="bar">
    <button class="mini" onclick="pickAll(1)">Select all</button>
    <button class="mini" onclick="pickAll(0)">Select none</button>
    <button class="mini" onclick="pickKills()">Only real kills</button>
    <button class="mini" onclick="pickHuman()">Only ones I flagged</button>
    <button class="mini" onclick="trimToKills()">Auto-trim to the action</button>
    <button class="mini" onclick="load()">Reload</button>
  </div>
  <div id="list"><p class="empty">Loading clips&hellip;</p></div>
</div>

<dialog id="player"><video id="vid" controls preload="metadata"></video>
  <div class="db"><span class="msg" id="pmsg"></span>
    <span><button class="mini" onclick="useIn()">Set IN here</button>
    <button class="mini" onclick="useOut()">Set OUT here</button>
    <button class="mini" onclick="document.getElementById('player').close()">Close</button></span>
  </div></dialog>

<div class="dock"><div class="inner">
  <span class="count" id="cnt">0 selected</span>
  <input type="text" id="name" placeholder="reel file name" value="my_reel">
  <input type="text" id="title" placeholder="title card" value="WITNESS">
  <label class="ck"><input type="checkbox" id="opt_music" checked> Music</label>
  <label class="ck"><input type="checkbox" id="opt_trans" checked> Transitions</label>
  <label class="ck"><input type="checkbox" id="opt_chy" checked> Names on clips</label>
  <label class="ck"><input type="checkbox" id="opt_ann"> Announcer</label>
  <button class="mini go" id="exp" onclick="doExport()">Export reel</button>
  <span class="msg" id="emsg"></span>
</div></div>

<script>
// ?s=<session> comes from the Archive's "Pick clips" link; no ?s= = the latest.
// The server checks the name (only a real session folder resolves).
var CLIPS = [], curr = -1,
    SESSION = new URLSearchParams(location.search).get('s') || 'latest';

function esc(s){ return String(s==null?"":s).replace(/&/g,"&amp;")
  .replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function fmt(t){ if(t==null) return "?";
  var m=Math.floor(t/60), s=Math.floor(t%60); return m+":"+(s<10?"0":"")+s; }

function load(){
  fetch('/clipdata?s='+encodeURIComponent(SESSION),{cache:'no-store'})
    .then(function(r){return r.json();}).then(function(d){
      if(d.error){ document.getElementById('list').innerHTML =
        '<p class="empty">'+esc(d.error)+'</p>';
        document.getElementById('sub').textContent=''; return; }
      SESSION = d.session || SESSION;
      CLIPS = (d.clips||[]).map(function(c){
        c.pick = c.downs>0 || c.human;   // sensible default: real kills + your picks
        c.ss=""; c.to=""; return c; });
      document.getElementById('sub').textContent =
        d.session+'  ·  '+d.count+' clip'+(d.count===1?'':'s')+
        '  ·  pick what belongs in the reel, trim it, then export';
      render();
    }).catch(function(e){ document.getElementById('list').innerHTML =
      '<p class="empty">could not load clips: '+esc(e)+'</p>'; });
}

function render(){
  var order = 0;
  var html = CLIPS.map(function(c,i){
    if(c.pick) order++;
    var chips = (c.chips||[]).map(function(ch){
      return '<span class="chip '+esc(ch.role)+'">'+esc(ch.label)+'</span>'; }).join('');
    var marks = (c.events||[]).map(function(e){
      var pct = c.duration ? (e.t/c.duration*100) : 0;
      return '<i class="mk'+(e.manual?' man':'')+'" style="left:'+pct+'%">'+
             '<span>'+esc(e.label)+'</span></i>'; }).join('');
    var a = c.ss===""?0:parseFloat(c.ss), b = c.to===""?(c.duration||0):parseFloat(c.to);
    var reg = (c.duration && (c.ss!==""||c.to!=="")) ?
      '<i class="region" style="left:'+(a/c.duration*100)+'%;width:'+
      (Math.max(0,(b-a))/c.duration*100)+'%"></i>' : '';
    return '<div class="card'+(c.pick?' sel':'')+'">'+
      '<div class="thumbwrap" onclick="play('+i+')">'+
        (c.pick?'<span class="ord">'+order+'</span>':'')+
        '<img loading="lazy" src="/clipthumb?s='+encodeURIComponent(SESSION)+
          '&f='+encodeURIComponent(c.rel)+'" alt="">'+
        '<span class="play">&#9658;</span></div>'+
      '<div><div class="hd">'+
        (c.victim? '<span class="who">'+esc(c.victim)+'</span>'
                 : '<span class="who none">no name read</span>')+
        '<span class="clock">'+esc(c.clock)+'  ·  '+fmt(c.duration)+'</span></div>'+
        '<div class="chips">'+chips+'</div>'+
        '<div class="summary">'+esc(c.summary)+'</div>'+
        '<div class="track">'+reg+marks+'</div>'+
        '<div class="trim">Trim'+
          ' <input type="number" step="0.5" min="0" placeholder="in" value="'+esc(c.ss)+
            '" oninput="setIn('+i+',this.value)">to'+
          ' <input type="number" step="0.5" min="0" placeholder="out" value="'+esc(c.to)+
            '" oninput="setOut('+i+',this.value)">sec'+
          ' <button class="mini" onclick="clearT('+i+')">Full clip</button>'+
          ' <button class="mini" onclick="trimOne('+i+')">To the action</button>'+
        '</div>'+
        '<div class="row2">'+
          '<label class="ck"><input type="checkbox" '+(c.pick?'checked':'')+
            ' onchange="setPick('+i+',this.checked)"> In the reel</label>'+
          '<button class="mini" onclick="move('+i+',-1)">&uarr; Earlier</button>'+
          '<button class="mini" onclick="move('+i+',1)">&darr; Later</button>'+
        '</div>'+
      '</div></div>';
  }).join('');
  document.getElementById('list').innerHTML = html ||
    '<p class="empty">No clips in this session yet.</p>';
  var n = CLIPS.filter(function(c){return c.pick;}).length;
  document.getElementById('cnt').textContent = n+' selected';
  document.getElementById('exp').disabled = n===0;
}

function setPick(i,v){ CLIPS[i].pick=v; render(); }
function setIn(i,v){ CLIPS[i].ss=v; render(); }
function setOut(i,v){ CLIPS[i].to=v; render(); }
function clearT(i){ CLIPS[i].ss=""; CLIPS[i].to=""; render(); }
function pickAll(v){ CLIPS.forEach(function(c){c.pick=!!v;}); render(); }
function pickKills(){ CLIPS.forEach(function(c){c.pick=c.downs>0;}); render(); }
function pickHuman(){ CLIPS.forEach(function(c){c.pick=!!c.human;}); render(); }

/* Trim to a window around the known kill times — the marker data makes this
   exact rather than a guess. */
function trimOne(i){
  var c=CLIPS[i]; if(!c.events||!c.events.length||!c.duration) return;
  var first=c.events[0].t, last=c.events[c.events.length-1].t;
  c.ss=Math.max(0,+(first-6).toFixed(1));
  c.to=Math.min(c.duration,+(last+4).toFixed(1));
  render();
}
function trimToKills(){ CLIPS.forEach(function(c,i){ trimOne(i); }); }

function move(i,d){
  var j=i+d; if(j<0||j>=CLIPS.length) return;
  var t=CLIPS[i]; CLIPS[i]=CLIPS[j]; CLIPS[j]=t; render();
}

function play(i){
  curr=i; var c=CLIPS[i];
  var v=document.getElementById('vid');
  v.src='/api/play?s='+encodeURIComponent(SESSION)+'&f='+encodeURIComponent(c.rel);
  document.getElementById('pmsg').textContent =
    (c.victim||'no name read')+' — '+c.summary;
  document.getElementById('player').showModal();
  if(c.events && c.events.length){ v.currentTime=Math.max(0,c.events[0].t-5); }
}
/* Scrub to the moment you want, then set it as the cut point — far easier than
   typing seconds. */
function useIn(){ if(curr<0) return;
  CLIPS[curr].ss=+document.getElementById('vid').currentTime.toFixed(1); render(); }
function useOut(){ if(curr<0) return;
  CLIPS[curr].to=+document.getElementById('vid').currentTime.toFixed(1); render(); }

function doExport(){
  var picks = CLIPS.filter(function(c){return c.pick;}).map(function(c){
    return {rel:c.rel, ss:c.ss===""?null:parseFloat(c.ss),
            to:c.to===""?null:parseFloat(c.to)}; });
  if(!picks.length) return;
  var b=document.getElementById('exp'), m=document.getElementById('emsg');
  b.disabled=true; m.textContent='starting…';
  fetch('/export',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({session:SESSION, clips:picks, opts:{
      name:document.getElementById('name').value,
      title:document.getElementById('title').value,
      music:document.getElementById('opt_music').checked,
      transitions:document.getElementById('opt_trans').checked,
      chyrons:document.getElementById('opt_chy').checked,
      announcer:document.getElementById('opt_ann').checked}})})
   .then(function(r){return r.json();}).then(function(j){
     if(!j.ok){ m.textContent=j.error||'failed'; b.disabled=false; return; }
     /* A render takes minutes, so follow the recap banner like the rebuild
        button does rather than leaving the page looking stuck. */
     var poll=setInterval(function(){
       fetch('/status',{cache:'no-store'}).then(function(r){return r.json();})
        .then(function(s){ var rc=s.recap||{};
          if(rc.note) m.textContent=rc.note;
          if(rc.status==='ready'){ clearInterval(poll); b.disabled=false;
            m.textContent=(rc.note||'done')+' — see the Archive'; }
          else if(rc.status==='error'){ clearInterval(poll); b.disabled=false; }
        }).catch(function(){}); },2000);
   }).catch(function(e){ m.textContent='failed: '+e; b.disabled=false; });
}

load();
</script></body></html>"""
