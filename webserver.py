"""A tiny built-in web dashboard so a phone/iPad/second screen can watch the
kill feed live over the local network. No dependencies (stdlib http.server).

Open http://<PC-IP>:<port> in the device's browser (same Wi-Fi). The page polls
/status once a second; the recorder updates a shared LiveState as kills happen.
"""

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
             "track_names": False, "reel_announcer": False}},
    {"key": "standard", "label": "STANDARD",
     "desc": "The shipped defaults: clips, reels, stats, name tracking, "
             "quiet visual chips. No voices.",
     "set": {"show_overlays": True, "overlay_multikill": True,
             "overlay_clip_saved": True, "overlay_reel_ready": True,
             "announcer_medals": False, "team_wipe": True,
             "play_sound": False, "make_match_reels": True,
             "reel_music": True, "reel_announcer": True,
             "make_shorts": False, "make_montage": False, "make_card": True,
             "capture_exfil_stats": True, "track_names": True}},
    {"key": "showtime", "label": "SHOWTIME",
     "desc": "The full broadcast: medal voices, banners, team wipes, "
             "announced reels. Best with friends watching the dashboard.",
     "set": {"show_overlays": True, "overlay_multikill": True,
             "overlay_clip_saved": True, "overlay_reel_ready": True,
             "announcer_medals": True, "team_wipe": True,
             "play_sound": False, "make_match_reels": True,
             "reel_music": True, "reel_announcer": True,
             "make_shorts": False, "make_montage": False, "make_card": True,
             "capture_exfil_stats": True, "track_names": True}},
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
             "capture_exfil_stats": True, "track_names": True}},
]


# Theme: per-game look. Game profiles (games/<game>.yaml) may carry a
# theme: block; these are Marathon's colors and the fallback for any key a
# profile doesn't set. Values are swapped into the page CSS at request time.
THEME_DEFAULTS = {
    "bg": "#0b0f12", "panel": "#12181d", "line": "#232d34",
    "text": "#e8edf0", "muted": "#7d8a94",
    "accent": "#9c58da", "danger": "#ff4d3d",
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
        self._cfg = None       # live config dict (bound per session)
        self._save_cb = None   # persists changed settings to disk

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

    def request_kill(self):
        with self._lock:
            self._kill_requested = True

    def pop_kill_request(self) -> bool:
        with self._lock:
            if getattr(self, "_kill_requested", False):
                self._kill_requested = False
                return True
            return False

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

    def set_running(self, running):
        with self._lock:
            self.running = running
            if running:
                self.started = time.strftime("%H:%M")
                self._mono = time.monotonic()

    def record(self, count, tag, text):
        with self._lock:
            self.count = count
            self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1
            self.events.appendleft(
                {"time": time.strftime("%H:%M:%S"), "tag": tag, "text": (text or "")[:60]})

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
                    "tags": dict(self.tag_counts),
                    "events": list(self.events),
                    "reels": [{"i": i, "label": r["label"], "time": r["time"]}
                              for i, r in enumerate(self.reels)],
                    "replays": [{"i": r["id"], "label": r["label"], "time": r["time"]}
                                for r in reversed(self.replays)]}


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


import re as _re
import urllib.parse as _uparse

_SAFE_PART = _re.compile(r"^[A-Za-z0-9 ._+\-]+$")


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
    if not path.lower().endswith((".mp4", ".mkv", ".png")):
        return None
    return path


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


def _archive_page(base_dir: str, record_dir: str) -> str:
    root = os.path.join(record_dir, "Marathon Sessions") if record_dir else ""
    sessions = []
    if root and os.path.isdir(root):
        recaps = _session_recaps(base_dir)
        for name in sorted(os.listdir(root), reverse=True)[:60]:
            sdir = os.path.join(root, name)
            if not os.path.isdir(sdir) or not _SAFE_PART.match(name):
                continue
            media = []
            sr = os.path.join(sdir, "session_reel.mp4")
            if os.path.exists(sr):
                media.append(("Session reel", f"session_reel.mp4"))
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
            n_clips = sum(1 for f in os.listdir(sdir)
                          if f.lower().endswith((".mkv", ".mp4"))
                          and not f.startswith(("highlights", "session_reel")))
            sessions.append({"id": name, "media": media, "clips": n_clips,
                             "recaps": recaps.get(name, [])})

    blocks = []
    for s in sessions:
        date = s["id"].replace("_", " · ")
        rec_html = "".join(f'<div class="rc">{_esc(l)}</div>' for l in s["recaps"]) \
            or '<div class="rc none">no exfil stats recorded</div>'
        rows = ""
        for label, rel in s["media"]:
            u = f"/avideo?s={_uparse.quote(s['id'])}&f={_uparse.quote(rel)}"
            rows += (f'<div class="mrow"><span class="play">&#9658;</span>'
                     f'<a class="ml" href="{u}" target="_blank">{_esc(label)}</a>'
                     f'<a class="dl" href="{u}&dl=1">save</a></div>')
        if not rows:
            rows = '<div class="rc none">no reels/replays kept for this session</div>'
        blocks.append(f"""<details><summary><b>{_esc(date)}</b>
          <span class="meta">{s['clips']} clip{'s' if s['clips'] != 1 else ''}</span></summary>
          <h4>Matches</h4>{rec_html}<h4>Watch / share</h4>{rows}</details>""")

    body = "".join(blocks) or ('<p class="rc none">No sessions found yet'
                               + ("" if record_dir else " — press START once so the app learns your OBS folder")
                               + ".</p>")
    return ARCHIVE_PAGE.replace("%%BODY%%", body)


def _stats_page(base_dir: str) -> str:
    """Career + economy + squad leaderboard, built from stats/squad_stats.csv
    (with match_stats.csv as the fallback for your own older rows)."""
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

    cards = "".join(
        f'<div class="tile"><div class="tn">{v:,}</div><div class="tl">{l}</div></div>'
        for l, v in [("Matches", me["matches"]), ("Runner elims", me["elims"]),
                     ("Downs", me["downs"]), ("Runner damage", me["damage"]),
                     ("Loot extracted", me["loot"]), ("Best haul", me["best_haul"])])

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
    else:
        menace_html = ('<h3>Menace report</h3><p class="empty2">No names yet — '
                       'gamertags are read off the kill feed as downs happen.</p>')

    return (STATS_PAGE.replace("%%CARDS%%", cards)
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


def start_web(state, port, base_dir, host="0.0.0.0"):
    imgs = {"/skull.png": "witness_logo.png",
            "/wordmark.png": "witness_wordmark.png"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # quiet

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
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                if a:
                    start = int(a)
                if b:
                    end = min(int(b), size - 1)
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
                    self._send(apply_theme(_stats_page(base_dir),
                                           state._cfg).encode("utf-8"),
                               "text/html; charset=utf-8", cache=False)
                elif path == "/archive":
                    self._send(apply_theme(
                        _archive_page(base_dir, getattr(state, "record_dir", "")),
                        state._cfg).encode("utf-8"),
                               "text/html; charset=utf-8", cache=False)
                elif path == "/avideo":
                    q = _uparse.parse_qs(self.path.partition("?")[2])
                    fp = _archive_video_path(getattr(state, "record_dir", ""),
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
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                if path == "/clip":
                    state.request_clip()
                    self._send(b'{"ok":true}', "application/json", cache=False)
                elif path == "/addkill":
                    state.request_kill()
                    self._send(b'{"ok":true}', "application/json", cache=False)
                elif path == "/config":
                    n = int(self.headers.get("Content-Length") or 0)
                    try:
                        changes = json.loads(self.rfile.read(n) or b"{}")
                    except ValueError:
                        changes = {}
                    result = state.apply_settings(changes if isinstance(changes, dict) else {})
                    self._send(json.dumps({"settings": result}).encode(),
                               "application/json", cache=False)
                else:
                    self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer((host, int(port)), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


ARCHIVE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Marathon Archive</title>
<style>
  :root { --bg:#0b0f12; --panel:#12181d; --line:#232d34; --text:#e8edf0;
          --muted:#7d8a94; --accent:#9c58da; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    padding:calc(20px + env(safe-area-inset-top)) 16px calc(40px + env(safe-area-inset-bottom)); }
  .wrap { max-width:720px; margin:0 auto; }
  h2 { color:var(--accent); font-size:.95rem; letter-spacing:.16em; text-transform:uppercase; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:.78rem; margin:0 0 18px; }
  details { background:var(--panel); border:1px solid var(--line); border-radius:12px;
    padding:0 16px; margin-bottom:10px; }
  summary { padding:14px 0; cursor:pointer; font-size:.9rem; list-style:none; }
  summary::-webkit-details-marker { display:none; }
  summary b { color:var(--text); }
  .meta { color:var(--muted); font-size:.72rem; float:right; }
  h4 { color:var(--muted); font-size:.62rem; letter-spacing:.16em; text-transform:uppercase;
    margin:10px 0 8px; }
  .rc { font-size:.8rem; color:var(--text); padding:6px 0; border-bottom:1px solid var(--line); }
  .rc.none { color:var(--muted); border:none; }
  .mrow { display:flex; align-items:center; gap:10px; padding:9px 0;
    border-bottom:1px solid var(--line); font-size:.82rem; }
  .mrow:last-child, details .rc:last-of-type { border-bottom:none; }
  .play { color:var(--accent); font-size:.8rem; }
  .ml { color:var(--text); text-decoration:none; flex:1; }
  .dl { color:var(--accent); text-decoration:none; font-size:.7rem; letter-spacing:.08em;
    text-transform:uppercase; border:1px solid var(--line); border-radius:6px; padding:4px 10px; }
  details > *:last-child { margin-bottom:14px; }
  .back { display:inline-block; margin-top:22px; color:var(--muted); font-size:.75rem;
    text-decoration:none; border:1px solid var(--line); border-radius:8px; padding:8px 16px; }
</style></head><body><div class="wrap">
  <h2>Archive</h2>
  <p class="sub">Every session, kept. Tap a reel to watch; "save" downloads it for sharing (Files &rarr; share sheet &rarr; group chat).</p>
  %%BODY%%
  <a class="back" href="/">&larr; Back to the kill feed</a>
</div></body></html>"""

STATS_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Marathon Stats</title>
<style>
  :root { --bg:#0b0f12; --panel:#12181d; --line:#232d34; --text:#e8edf0;
          --muted:#7d8a94; --accent:#9c58da; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    padding:calc(20px + env(safe-area-inset-top)) 16px calc(40px + env(safe-area-inset-bottom)); }
  .wrap { max-width:720px; margin:0 auto; }
  h2 { color:var(--accent); font-size:.95rem; letter-spacing:.16em; text-transform:uppercase;
    margin:0 0 4px; }
  .sub { color:var(--muted); font-size:.78rem; margin:0 0 18px; }
  h3 { color:var(--muted); font-size:.7rem; letter-spacing:.16em; text-transform:uppercase;
    margin:26px 0 10px; }
  .tiles { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }
  @media(max-width:480px){ .tiles{ grid-template-columns:repeat(2,1fr); } }
  .tile { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:14px 6px 11px; text-align:center; }
  .tile .tn { font-size:1.35rem; font-weight:800; color:var(--accent);
    font-variant-numeric:tabular-nums; }
  .tile .tl { font-size:.58rem; letter-spacing:.1em; text-transform:uppercase;
    color:var(--muted); margin-top:4px; }
  .tblwrap { overflow-x:auto; }
  table { border-collapse:collapse; width:100%; font-size:.78rem; }
  th, td { text-align:right; padding:8px 10px; border-bottom:1px solid var(--line);
    white-space:nowrap; font-variant-numeric:tabular-nums; }
  th:nth-child(2), td:nth-child(2) { text-align:left; }
  th { color:var(--muted); font-size:.6rem; letter-spacing:.1em; text-transform:uppercase; }
  .hog { background:var(--accent); color:#0b0f12; font-size:.55rem; font-weight:700;
    letter-spacing:.08em; padding:2px 7px; border-radius:4px; }
  .nem { background:#ff4d3d; color:#0b0f12; font-size:.55rem; font-weight:700;
    letter-spacing:.08em; padding:2px 7px; border-radius:4px; }
  .empty2 { color:var(--muted); font-size:.8rem; }
  .back { display:inline-block; margin-top:26px; color:var(--muted); font-size:.75rem;
    text-decoration:none; border:1px solid var(--line); border-radius:8px; padding:8px 16px; }
</style></head><body><div class="wrap">
  <h2>Career Stats</h2>
  <p class="sub">From every exfil screen the app has captured. It only gets deeper from here.</p>
  <div class="tiles">%%CARDS%%</div>
  %%SQUAD%%
  %%MENACE%%
  <a class="back" href="/">&larr; Back to the kill feed</a>
</div></body></html>"""

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Kill Feed">
<link rel="apple-touch-icon" href="/skull.png">
<title>Marathon Kill Feed</title>
<style>
  :root { --bg:#0b0f12; --panel:#12181d; --line:#232d34; --text:#e8edf0;
          --muted:#7d8a94; --accent:#9c58da; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html, body { min-height:100%; }
  body { margin:0; background:var(--bg); color:var(--text); text-align:center;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
    -webkit-font-smoothing:antialiased; user-select:none;
    padding:calc(20px + env(safe-area-inset-top)) 16px calc(40px + env(safe-area-inset-bottom)); }
  .wrap { max-width:640px; margin:0 auto; }
  header { display:flex; align-items:center; justify-content:center; gap:12px; margin-bottom:6px; }
  header img { height:34px; }
  .status { font-size:.8rem; letter-spacing:.16em; text-transform:uppercase; margin-bottom:18px; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:7px;
    vertical-align:middle; background:var(--muted); }
  .live .dot { background:#5bd66b; box-shadow:0 0 10px #5bd66b; }
  .big { font-size:28vw; line-height:.9; font-weight:800; font-variant-numeric:tabular-nums; }
  @media(min-width:520px){ .big{ font-size:140px; } }
  @media (prefers-reduced-motion: no-preference){
    .big.pop { animation:pop .5s ease-out; }
    @keyframes pop { 0%{ transform:scale(1); text-shadow:none; }
      30%{ transform:scale(1.14); text-shadow:0 0 42px rgba(211,242,75,.85); }
      100%{ transform:scale(1); text-shadow:none; } }
  }
  .tiles { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:14px 0 4px; }
  .tile { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:10px 4px 8px; text-align:center; }
  .tile .tn { font-size:1.5rem; font-weight:800; font-variant-numeric:tabular-nums; }
  .tile .tl { font-size:.58rem; letter-spacing:.12em; text-transform:uppercase;
    color:var(--muted); margin-top:2px; }
  .tile.down .tn { color:#aab4bd; }
  .tile.precision .tn { color:var(--accent); }
  .tile.finisher .tn { color:#f5a623; }
  .tile.assist .tn { color:#37cabb; }
  .accent { color:var(--accent); }
  .lab { color:var(--muted); font-size:.68rem; letter-spacing:.14em; text-transform:uppercase; margin-top:8px; }
  .sub { color:var(--muted); font-size:.82rem; margin:4px 0 16px; }
  .clipbtn { background:var(--accent); color:#0b0f12; border:none; border-radius:10px;
    padding:14px 28px; font:inherit; font-size:.9rem; font-weight:700; letter-spacing:.08em;
    text-transform:uppercase; cursor:pointer; margin-bottom:16px;
    transition: opacity .15s, transform .1s; }
  .clipbtn:active { transform:scale(.95); opacity:.85; }
  .clipbtn.fired { background:#5bd66b; }
  .btnrow { display:flex; gap:10px; justify-content:center; margin-bottom:16px; }
  .fsbtn { background:var(--panel); color:var(--muted); border:1px solid var(--line);
    border-radius:8px; padding:7px 14px; font:inherit; font-size:.75rem;
    cursor:pointer; }
  .hint { color:var(--muted); font-size:.72rem; margin-top:22px; opacity:.8; }
  .reels { text-align:left; margin-bottom:16px; }
  .reels h3 { color:var(--muted); font-size:.68rem; letter-spacing:.14em;
    text-transform:uppercase; margin:0 0 8px 2px; }
  .reelrow { background:var(--panel); border:1px solid var(--accent); border-radius:10px;
    padding:12px 14px; display:flex; align-items:center; gap:12px; cursor:pointer;
    margin-bottom:8px; }
  .reelrow .play { color:var(--accent); font-size:1.1rem; }
  .reelrow .t { color:var(--muted); font-size:.75rem; margin-left:auto; }
  .modal { display:none; position:fixed; inset:0; background:rgba(0,0,0,.92);
    z-index:50; align-items:center; justify-content:center; flex-direction:column;
    padding:16px; }
  .modal.open { display:flex; }
  .modal video { width:100%; max-width:900px; max-height:75vh; border-radius:12px;
    background:#000; }
  .modal .mlabel { color:var(--text); font-size:.85rem; margin:14px 0 10px; }
  .modal .close { background:var(--panel); color:var(--text); border:1px solid var(--line);
    border-radius:8px; padding:10px 26px; font:inherit; font-size:.8rem; cursor:pointer; }
  .settings { background:var(--panel); border:1px solid var(--line); border-radius:14px;
    padding:18px; max-width:480px; width:100%; max-height:80vh; overflow-y:auto;
    text-align:left; }
  .settings h2 { margin:0 0 14px; font-size:.85rem; letter-spacing:.14em;
    text-transform:uppercase; color:var(--accent); }
  .modehead { color:var(--muted); font-size:.62rem; letter-spacing:.14em;
    text-transform:uppercase; margin:2px 0 8px; }
  .moderow { display:flex; align-items:center; gap:12px; padding:6px 0; }
  .modebtn { flex:0 0 92px; background:var(--bg); color:var(--text);
    border:1px solid var(--line); border-radius:8px; padding:9px 0;
    font:inherit; font-size:.7rem; font-weight:700; letter-spacing:.1em;
    cursor:pointer; }
  .modebtn.active { background:var(--accent); color:var(--bg); border-color:var(--accent); }
  .modedesc { color:var(--muted); font-size:.68rem; line-height:1.35; }
  .setrow { display:flex; align-items:center; justify-content:space-between;
    gap:12px; padding:11px 2px; border-bottom:1px solid var(--line); font-size:.85rem; }
  .setrow:last-of-type { border-bottom:none; }
  .setrow input[type=number] { width:76px; background:var(--bg); color:var(--text);
    border:1px solid var(--line); border-radius:6px; padding:7px 9px; font:inherit;
    font-size:.85rem; text-align:center; }
  .switch { position:relative; width:52px; height:30px; flex:none; }
  .switch input { opacity:0; width:0; height:0; }
  .slider { position:absolute; inset:0; background:var(--line); border-radius:15px;
    cursor:pointer; transition:background .15s; }
  .slider:before { content:''; position:absolute; width:24px; height:24px; left:3px;
    top:3px; background:var(--muted); border-radius:50%; transition:transform .15s, background .15s; }
  .switch input:checked + .slider { background:var(--accent); }
  .switch input:checked + .slider:before { transform:translateX(22px); background:#0b0f12; }
  .savedmsg { color:var(--accent); font-size:.75rem; text-align:center; margin-top:10px;
    opacity:0; transition:opacity .3s; }
  .savedmsg.show { opacity:1; }
  .help h3 { color:var(--accent); font-size:.72rem; letter-spacing:.14em;
    text-transform:uppercase; margin:16px 0 6px; }
  .help h3:first-of-type { margin-top:0; }
  .help p { color:var(--text); font-size:.82rem; line-height:1.5; margin:0 0 4px; }
  .help .m { color:var(--muted); }
  .help code { color:var(--accent); font-size:.78rem; }
  .feed { text-align:left; display:flex; flex-direction:column; gap:8px; }
  .row { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:11px 14px; display:flex; align-items:center; gap:12px; }
  .row.precision { border-color:var(--accent); }
  .badge { font-size:.62rem; letter-spacing:.1em; text-transform:uppercase; padding:3px 8px;
    border-radius:5px; background:#1c2630; color:var(--muted); white-space:nowrap; }
  .precision .badge { background:var(--accent); color:#0b0f12; }
  .finisher .badge { background:#f5a623; color:#0b0f12; }
  .assist .badge { background:#37cabb; color:#0b0f12; }
  .row .t { color:var(--muted); font-size:.75rem; margin-left:auto; font-variant-numeric:tabular-nums; }
  .skull { height:18px; vertical-align:middle; }
  .empty { color:var(--muted); padding:30px; }
</style></head><body><div class="wrap">
  <header><img src="/wordmark.png" alt="MARATHON"></header>
  <div class="status" id="status"><span class="dot"></span><span id="statustext">CONNECTING</span></div>
  <div><div class="big accent" id="count">0</div><div class="lab">Kills</div></div>
  <div class="sub" id="sub">&nbsp;</div>
  <div class="tiles">
    <div class="tile down"><div class="tn" id="t_down">0</div><div class="tl">Downs</div></div>
    <div class="tile precision"><div class="tn" id="t_precision">0</div><div class="tl">Precision</div></div>
    <div class="tile finisher"><div class="tn" id="t_finisher">0</div><div class="tl">Finishers</div></div>
    <div class="tile assist"><div class="tn" id="t_assist">0</div><div class="tl">Assists</div></div>
  </div>
  <div class="btnrow">
    <button class="clipbtn" id="clip" onclick="saveClip()">SAVE CLIP</button>
    <button class="clipbtn" id="addk" onclick="addKill()">+1 KILL</button>
  </div>
  <div class="btnrow">
    <button class="fsbtn" id="snd" onclick="toggleSound()">SOUND: ON</button>
    <button class="fsbtn" onclick="openSettings()">Settings</button>
    <button class="fsbtn" onclick="location.href='/stats'">Stats</button>
    <button class="fsbtn" onclick="location.href='/archive'">Archive</button>
    <button class="fsbtn" onclick="openHelp()">How to use</button>
    <button class="fsbtn" id="fs" onclick="goFull()">Full screen</button>
  </div>
  <div class="reels" id="reels" style="display:none"><h3>Match Highlights</h3><div id="reellist"></div></div>
  <div class="reels" id="replays" style="display:none"><h3>Instant Replays</h3><div id="replaylist"></div></div>
  <div class="feed" id="feed"><div class="empty">Waiting for kills...</div></div>
  <div class="hint" id="hint">iPad: tap Share &rarr; Add to Home Screen for full screen.
    Screen still dimming? Settings &rarr; Display &amp; Brightness &rarr; Auto-Lock &rarr; Never (the guaranteed fix).</div>
</div>
<div class="modal" id="modal">
  <video id="reelvid" controls playsinline></video>
  <div class="mlabel" id="mlabel"></div>
  <button class="close" onclick="closeReel()">CLOSE</button>
</div>
<div class="modal" id="helpmodal">
  <div class="settings help">
    <h2>How to use</h2>
    <h3>Kill counter + feed</h3>
    <p>Kills are detected automatically from the game screen. The big number and the feed update within a second or two of each kill.</p>
    <h3>Save clip</h3>
    <p>Saves the last ~30 seconds manually — for a moment the detector missed or anything else worth keeping.</p>
    <h3>Sound</h3>
    <p>This device dings on every kill. Tap the page once after opening it (browser rule), then use SOUND to toggle.</p>
    <h3>Screen staying awake</h3>
    <p>The same first tap starts a keep-awake trick so the screen doesn't auto-lock. If it still sleeps, set iPad Settings &rarr; Display &amp; Brightness &rarr; Auto-Lock to Never while playing.</p>
    <h3>Match highlights</h3>
    <p>About 30 seconds after you exfil, a highlight reel of that match pops up here: stat card, Play of the Game, then every clip.</p>
    <p class="m">Two versions per match — clean, and one with an announcer voiceover. Both are tappable in the list.</p>
    <h3>Instant replays</h3>
    <p>Every kill clip appears here seconds after it saves. Tap to rewatch. Keeps the last 20.</p>
    <h3>Archive</h3>
    <p>Past sessions never disappear: the Archive button lists every session with per-match recaps, all its reels and replays, and a "save" link to download any of them for sharing to the group chat.</p>
    <h3>Music on reels</h3>
    <p>Drop an mp3 into the <code>music</code> folder next to the app and reels get a soundtrack automatically.</p>
    <h3>Settings</h3>
    <p>Every toggle applies to the running session immediately — no restart, no config file editing.</p>
    <h3>Where files go</h3>
    <p class="m">Clips land in your OBS output folder under <code>Marathon Sessions/&lt;date&gt;/</code> — reels in <code>reels/</code>, vertical Shorts in <code>shorts/</code>, plus a screenshot of each exfil screen. A session recap, match card, and montage are built when you stop.</p>
  </div>
  <div style="height:14px"></div>
  <button class="close" onclick="closeHelp()">CLOSE</button>
</div>
<div class="modal" id="setmodal">
  <div class="settings">
    <h2>Settings</h2>
    <div id="setlist"></div>
    <div class="savedmsg" id="savedmsg">Saved — applies immediately</div>
  </div>
  <div style="height:14px"></div>
  <button class="close" onclick="closeSettings()">CLOSE</button>
</div>
<script>
  async function tick(){
    try{
      var r = await fetch('/status',{cache:'no-store'});
      var d = await r.json();
      if (d.count > lastCount && lastCount >= 0){ ding(); flashCount(); }
      lastCount = d.count;
      document.getElementById('count').textContent = d.count;
      var tags = d.tags || {};
      ['down','precision','finisher','assist'].forEach(function(t){
        document.getElementById('t_'+t).textContent = tags[t] || 0;
      });
      var st = document.querySelector('.status');
      st.className = 'status' + (d.running ? ' live' : '');
      document.getElementById('statustext').textContent = d.running ? 'RUNNING' : 'STOPPED';
      document.getElementById('sub').textContent =
        d.running && d.elapsed ? 'session ' + fmtElapsed(d.elapsed) + ' \\u00b7 started ' + d.started
        : (d.started ? 'started ' + d.started : '\\u00a0');
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
        if (reels.length > lastReels && lastReels >= 0) openReel(reels.length-1, reels[reels.length-1].label);
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

  async function saveClip(){
    var btn = document.getElementById('clip');
    btn.textContent = 'SAVING...';
    btn.classList.add('fired');
    try { await fetch('/clip', {method:'POST'}); } catch(e){}
    setTimeout(function(){ btn.textContent='SAVE CLIP'; btn.classList.remove('fired'); }, 1500);
  }

  async function addKill(){
    var btn = document.getElementById('addk');
    btn.textContent = 'COUNTED';
    btn.classList.add('fired');
    try { await fetch('/addkill', {method:'POST'}); } catch(e){}
    setTimeout(function(){ btn.textContent='+1 KILL'; btn.classList.remove('fired'); }, 1500);
  }

  function flashCount(){
    var el = document.getElementById('count');
    el.classList.remove('pop'); void el.offsetWidth;  // restart the animation
    el.classList.add('pop');
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
    v.src = 'data:video/mp4;base64,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AAAAHAAAAAUAAAABAAAAHQAAAAQAAAABAAAAHwAAAAUAAAABAAAAIAAAAAQAAAABAAAAIgAAAAUAAAABAAAAIwAAAAQAAAABAAAAJQAAAAUAAAABAAAAJgAAAAQAAAABAAAAKQAAAAUAAAABAAAAKgAAAAQAAAABAAAALAAAAAUAAAABAAAALQAAAAQAAAABAAAALwAAAAUAAAABAAAAMAAAAAQAAAABAAAAMgAAAAUAAAABAAAAMwAAAAQAAAABAAADeHN0c3oAAAAAAAAAAAAAANkAAAAVAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAEAAAABAAAAAQAAAAE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GCAHAAAABUGawBH1ARggBwEYIAcBGCAHARggBwEYIAcAAAAFQZrgEfUBGCAHARggBwEYIAcBGCAHAAAABUGbABH1ARggBwEYIAcBGCAHARggBwAAAAVBmyAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZtAEfUBGCAHARggBwEYIAcBGCAHARggBwAAAAVBm2AR9QEYIAcBGCAHARggBwEYIAcAAAAFQZuAEfUBGCAHARggBwEYIAcBGCAHAAAABUGboBH1ARggBwEYIAcBGCAHARggBwEYIAcAAAAFQZvAEfUBGCAHARggBwEYIAcBGCAHAAAABUGb4BH1ARggBwEYIAcBGCAHARggBwAAAAVBmgAR9QEYIAcBGCAHARggBwEYIAcBGCAHAAAABUGaIBH1ARggBwEYIAcBGCAHARggBwAAAAVBmkAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZpgEfUBGCAHARggBwEYIAcBGCAHARggBwAAAAVBmoAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZqgEfUBGCAHARggBwEYIAcBGCAHAAAABUGawBH1ARggBwEYIAcBGCAHARggBwAAAAVBmuAR9QEYIAcBGCAHARggBwEYIAcBGCAHAAAABUGbABH1ARggBwEYIAcBGCAHARggBwAAAAVBmyAR9QEYIAcBGCAHARggBwEYIAcAAAAFQZtAEfUBGCAHARggBwEYIAcBGCAHARggBwAAAAVBm2AR9QEYIAcBGCAHARggBwEYIAcAAAAFQZuAEfUBGCAHARggBwEYIAcBGCAHAAAABUGboBH1ARggBwEYIAcBGCAHARggBwEYIAcAAAAFQZvAEfUBGCAHARggBwEYIAcBGCAHAAAABUGb4BH1ARggBwEYIAcBGCAHARggBwAAAAVBmgAQ9QEYIAcBGCAHARggBwEYIAcBGCAHAAAABUGaID/UARggBwEYIAcBGCAHARggBw==';
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

  function goFull(){
    var el = document.documentElement;
    if (el.requestFullscreen) el.requestFullscreen();
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
  }
  if (window.navigator.standalone || window.matchMedia('(display-mode: standalone)').matches){
    document.getElementById('fs').style.display='none';
    document.getElementById('hint').style.display='none';
  }
</script></body></html>"""
