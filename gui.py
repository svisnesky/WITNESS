"""WITNESS — control-panel window. It sees everything.

Replaces the console: big Start/Stop button, live kill count, a log pane, and
quick buttons to open the dashboard / settings / folder. Launch with:
    pythonw gui.py        (no console window)

Uses only the standard library (tkinter) plus this project's own modules.
"""

import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import scrolledtext

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

UPDATE_MSG = ""   # set during boot  (auto-update self-test da7a37c) (behind the splash), read by ControlPanel
app = None        # main module, imported during boot

BG = "#0e0e16"
PANEL = "#17161f"
ACCENT = "#9184d9"
TEXT = "#e9e9ed"
MUTED = "#8a90a0"
LINE = "#2a2a38"
RED = "#ff4b42"
GREEN = "#5bd66b"
AMBER = "#f5a623"


# --- failures you can see ---------------------------------------------------
# The recommended launcher is pythonw: no console, sys.stdout and sys.stderr
# are None, and Tk swallows callback errors. Every failure used to end in an
# invisible pythonw.exe (or a START button that quietly flipped back). Each
# path below ends in a message on screen and a file in logs/ instead.

def _logs_dir() -> str:
    d = os.path.join(BASE, "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _stamp() -> str:
    return time.strftime("%Y-%m-%d_%H-%M-%S")


def _write_log(name: str, text: str) -> str:
    """Write text to logs/<name>. Returns the path, or '' if even that failed."""
    path = os.path.join(_logs_dir(), name)
    try:
        with open(path, "w", encoding="utf-8", errors="backslashreplace") as f:
            f.write(text)
        return path
    except Exception:
        return ""


def _ensure_stderr() -> None:
    """Under pythonw sys.stderr is None, so tracebacks from threads, warnings
    and libraries vanish. Point it at logs/pythonw_stderr.log (append, utf-8,
    line-buffered; started fresh once it passes 5 MB)."""
    if sys.stderr is not None:
        return
    path = os.path.join(_logs_dir(), "pythonw_stderr.log")
    mode = "a"
    try:
        if os.path.getsize(path) > 5 * 1024 * 1024:
            mode = "w"
    except OSError:
        pass
    try:
        sys.stderr = open(path, mode, encoding="utf-8", errors="backslashreplace",
                          buffering=1)
        sys.stderr.write(f"\n--- WITNESS window started {_stamp()} ---\n")
    except Exception:
        pass


def _note(msg: str) -> None:
    """A diagnostic line. Goes to stderr: under pythonw that is the log file
    above, while stdout is None and print() would drop it."""
    try:
        print(msg, file=sys.stderr if sys.stderr is not None else sys.stdout)
    except Exception:
        pass


def _show_error(message: str, title: str = "WITNESS", info: bool = False,
                parent=None) -> None:
    """A message box that works with or without a live Tk root, and falls back
    to the native Windows box if Tk itself is broken."""
    try:
        from tkinter import messagebox
        own = None
        if parent is None:
            own = parent = tk.Tk()
            own.withdraw()
            try:   # only our throwaway root: never pin the user's window on top
                own.attributes("-topmost", True)
            except Exception:
                pass
        (messagebox.showinfo if info else messagebox.showerror)(
            title, message, parent=parent)
        if own is not None:
            own.destroy()
        return
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title,
                                             0x40 if info else 0x10)
            return
        except Exception:
            pass
    _note(f"{title}: {message}")


def _fatal(message: str, parent=None, info: bool = False, code: int = 1):
    """Show the message, then end the process for real. os._exit, because a
    daemon web server or a pywebview thread must not keep it alive unseen."""
    _note(message)
    _show_error(message, info=info, parent=parent)
    os._exit(code)


def _config_error_text(exc) -> str:
    return ("config.yaml could not be read, so WITNESS can't start.\n\n"
            f"{exc}\n\nFix that line in config.yaml (or put back the copy from "
            "the WITNESS download) and start again.")


_GPU_MSG = ("The graphics card couldn't run the text reader (CUDA error). Update "
            "the NVIDIA driver (RTX 50-series needs 570 or newer) and press START "
            "again. If it keeps happening, delete the .venv folder and run START "
            "again - setup reinstalls the GPU parts.")
_VRAM_MSG = ("The graphics card ran out of memory for the text reader. Close other "
             "GPU-heavy apps and press START again.")


def friendly_start_error(exc, log_path=None) -> str:
    """One line a first-time user can act on, for an exception that ended a
    START before the session went live. The common ones are OBS setup
    mistakes: WebSocket server off (OBS 28+ ships with it off) and the password
    not copied into config.yaml.

    main.py's own OBS setup errors already start with 'OBS' and say exactly
    what to click, so they pass through verbatim, as does anything naming
    config.yaml."""
    text = (str(exc) or "").strip() or type(exc).__name__
    low = text.lower()
    if text.startswith("OBS") or "config.yaml" in low:
        msg = text
    elif isinstance(exc, ConnectionRefusedError) or "10061" in text:
        msg = ("Can't reach OBS. In OBS: Tools > WebSocket Server Settings > "
               "Enable WebSocket server.")
    elif "identify" in low or "no password" in low or "authentication" in low:
        # obsws-python's wording for a wrong or missing password:
        # 'failed to identify client with the server, ...'
        msg = "OBS rejected the password - copy it into obs.password in config.yaml."
    elif "out of memory" in low and "cuda" in low:
        msg = _VRAM_MSG
    elif "no kernel image" in low or "cuda" in low:
        msg = _GPU_MSG
    else:
        msg = f"{type(exc).__name__}: {text}"
    where = f"logs/{os.path.basename(log_path)}" if log_path else "logs/session_...log"
    return f"{msg} (details: {where})"


# Bound once when the dashboard server starts; a new value needs a full restart,
# so a START-time reload keeps the launch value (the "Live view" address main.py
# prints would otherwise name a port nothing is listening on).
_BOUND_AT_LAUNCH = ("web_port", "web_lan", "web_dashboard")


def reload_config_into(cfg: dict, load, lock=None, keep=()) -> str:
    """Re-read config.yaml (load = main.load_config) INTO the existing dict, so
    an edit (say, the OBS password) applies on the next START instead of only
    after a full app restart. Returns '' or a message saying why it couldn't.

    Updates in place and never clear()s or rebinds: LiveState.bind_config and
    the running session hold this exact dict, and a clear() would let a
    dashboard GET /settings land on an empty one. Dashboard toggles survive
    because load_config applies settings_override.yaml last. A key deleted
    from the file keeps its old value until restart, which is harmless."""
    try:
        fresh = load()
        if not isinstance(fresh, dict):
            raise ValueError("it is empty or not a list of settings")
    except Exception as e:
        return f"config.yaml could not be read - using launch settings: {e}"
    for k in keep:
        fresh.pop(k, None)
    if lock is not None:
        with lock:
            cfg.update(fresh)
    else:
        cfg.update(fresh)
    return ""


# Feed line (LiveState.notice keeps 60 chars) for a START whose reload failed.
_RELOAD_NOTICE = "config.yaml unreadable - using launch settings"


def with_reload_error(reload_err: str, msg: str) -> str:
    """A START error with the failed config reload in front of it. The reload
    is usually the real cause: the user fixes obs.password but leaves a YAML
    typo, the old password is kept, and OBS rejects it. Alone, the OBS
    message would send them back to redo the edit that never loaded. One
    line: yaml's errors span several, and the tk panel splits on newlines."""
    if not reload_err:
        return msg
    return " ".join(str(reload_err).split()) + " / " + msg


# --- one copy at a time ---------------------------------------------------------

def _read_yaml(path: str) -> dict:
    """A user-editable YAML file as a dict. utf-8-sig so a Notepad BOM is fine;
    falls back to the Windows locale encoding for a file saved as ANSI."""
    import yaml
    try:
        with open(path, encoding="utf-8-sig") as f:
            return yaml.safe_load(f) or {}
    except UnicodeDecodeError:
        import locale
        with open(path, encoding=locale.getpreferredencoding(False)) as f:
            return yaml.safe_load(f) or {}


def _configured_port() -> int:
    """web_port before main.py is imported (settings_override.yaml wins, as in
    load_config). 8000 when the files or yaml itself can't be read."""
    port = 8000
    for name in ("config.yaml", "settings_override.yaml"):
        try:
            d = _read_yaml(os.path.join(BASE, name))
            if isinstance(d, dict) and "web_port" in d:
                port = int(d["web_port"])
        except Exception:
            pass
    return port


def _witness_already_running(port, connect_timeout: float = 0.5,
                             read_timeout: float = 2.0) -> bool:
    """True when a WITNESS dashboard already answers on this PC's port.

    A failed bind can't be relied on to catch a second copy: on Windows,
    ThreadingHTTPServer's SO_REUSEADDR lets it bind :8000 too, and then two OCR
    loops share one GPU and every clip is saved twice. Asks /status on
    127.0.0.1 (an IP-literal Host with no Origin, which the dashboard's request
    guard allows) and checks that the JSON looks like WITNESS's snapshot.

    Two timeouts instead of one: Windows retries a refused loopback connect for
    about a second instead of failing at once, so the usual case (nothing
    running) costs the whole connect timeout on every launch. A listening
    copy's kernel accepts at once whatever its OCR loop is doing, so a short
    connect timeout loses nothing; the reply itself gets longer."""
    import http.client
    import json
    conn = None
    try:
        conn = http.client.HTTPConnection("127.0.0.1", int(port),
                                          timeout=connect_timeout)
        conn.connect()
        conn.sock.settimeout(read_timeout)
        conn.request("GET", "/status")
        resp = conn.getresponse()
        if resp.status != 200:
            return False
        data = json.loads(resp.read().decode("utf-8", "replace"))
        return isinstance(data, dict) and "running" in data and "count" in data
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


_ALREADY_RUNNING = ("WITNESS is already running.\n\nLook for its window, or its "
                    "icon on the taskbar. Close it first if you want to restart it.")


def _bind_host(cfg) -> str:
    """Serve the Wi-Fi only when BOTH web_lan and web_dashboard allow it. The
    window app always needs its own server (the window IS the dashboard), so
    either switch off means this-PC-only, never 'off'."""
    if cfg.get("web_lan", True) and cfg.get("web_dashboard", True):
        return "0.0.0.0"
    return "127.0.0.1"


def _set_attr(obj, name, value) -> None:
    """Set a plain attribute on the dashboard's LiveState under its lock (the
    web-dashboard code reads them with getattr inside snapshot())."""
    if obj is None:
        return
    lock = getattr(obj, "_lock", None)
    try:
        if lock is not None:
            with lock:
                setattr(obj, name, value)
        else:
            setattr(obj, name, value)
    except Exception:
        pass


# --- the tk panel's tiles ------------------------------------------------------
# The tiles used to count only 'KILL #' lines and show DOWNS = down + kill: the
# retired rule that filed every RUNNER ELIM under downs, while PRECISION and
# ASSISTS never saw the '(not a kill: ...)' lines those events print. They now
# replay the console into the same ordered tag list main.py keeps, and DOWNS
# comes from exfil_stats.count_events, the tally the exfil audit uses.
# Measured on Stan's audited matches: DOWNS exact on 13/13 (was 10/13).

_EVENT_RE = re.compile(r"^(?:KILL #\d+|\(not a kill: \w+\)) \[(\w+)\]")
_RECON_RE = re.compile(
    r"\[reconcile\] game credited more (downs|runner elims) .*added (\d+)")


def _match_break() -> str:
    """exfil_stats.MATCH_BREAK, or its value before the detection change that
    adds it, so a missing attribute can't silently kill the tiles."""
    try:
        import exfil_stats
        return getattr(exfil_stats, "MATCH_BREAK", "exfil")
    except Exception:
        return "exfil"


def parse_console_line(line: str, tags: list) -> None:
    """Append what one console line means to the ordered session tag list:
    the tag of a 'KILL #N [t]' or '(not a kill: x) [t]' line, 'down'/'elim'
    x N for a '[reconcile] game credited more downs/runner elims ... added N'
    line, and the match break for '[exfil] match stats'. Other lines (the
    headline-floor reconcile included) add nothing."""
    s = (line or "").strip()
    m = _EVENT_RE.match(s)
    if m:
        tags.append(m.group(1))
        return
    m = _RECON_RE.search(s)
    if m:
        add = ["down" if m.group(1) == "downs" else "elim"] * int(m.group(2))
        brk = _match_break()
        if tags and tags[-1] == brk:
            # The stats line prints before the reconcile lines, but main.py
            # puts reconciled events BEFORE the break. Same order here, or a
            # reconciled elim would fold the next match's first FINISHER.
            tags[-1:-1] = add
        else:
            tags.extend(add)
        return
    if "[exfil] match stats" in s:
        tags.append(_match_break())


def tile_values(tags) -> dict:
    """The four tiles from the ordered tag list. DOWNS is count_events' downs,
    so it matches the audit; the rest are plain counts of their tag."""
    import exfil_stats
    downs, _elims = exfil_stats.count_events(tags)
    return {"downs": downs,
            "precision": sum(1 for t in tags if t == "precision"),
            "finishers": sum(1 for t in tags if t == "finisher"),
            "assists": sum(1 for t in tags if t == "assist")}


class QueueWriter:
    """File-like object that funnels print() output into a queue for the GUI."""
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("log", s))

    def flush(self):
        pass


RAIL_BG = "#0d1218"
NAV_MUTED = "#8a90a0"

TAG_LABEL = {
    "precision": "PRECISION DOWN +25 XP",
    "finisher": "FINISHER +50",
    "down": "RUNNER DOWN +15 XP",
    "kill": "RUNNER ELIM +10 XP",
    "assist": "ASSIST +15 XP",
    "manual": "MANUAL +1",
}
TAG_PILL = {"precision": ACCENT, "finisher": "#f5a623", "down": "#8a90a0",
            "kill": "#8a90a0", "assist": "#37cabb", "manual": "#8a90a0"}


class ControlPanel:
    """WITNESS command center — rail + live main stage."""
    DOT = "●"

    def __init__(self, root, boot=None):
        self.root = root
        # the launch update check (see check_updates); its result is shown
        # once it lands, however late (_poll_boot)
        self._boot = boot
        try:
            self.cfg = app.load_config()
        except Exception as e:
            # a typo in config.yaml used to kill this window before it drew,
            # silently under pythonw
            _fatal(_config_error_text(e), parent=root)
        self.q = queue.Queue()
        self.stop_event = None
        self.worker = None
        self.running = False
        self.count = 0
        self.tags = []                 # kill tags this session (for the tiles)
        self.recent = []               # (hhmmss, tag) newest-last
        self.latest = None             # (tag, hhmmss) for the LATEST CLIP card
        self.session_start = None
        self._last_error = ""          # why the last START failed (kept on idle)
        self._icon = None
        self.dry = tk.BooleanVar(value=False)
        self._build()
        self.root.after(100, self._drain)
        self._tick()

    # --- shell ---------------------------------------------------------------

    def _build(self):
        r = self.root
        r.title("WITNESS")
        r.configure(bg=BG)
        r.geometry("860x560")
        r.minsize(760, 520)
        try:
            self._icon_full = tk.PhotoImage(file=os.path.join(BASE, "witness_logo.png"))
            r.iconphoto(True, self._icon_full)
            ico = os.path.join(BASE, "witness.ico")
            if sys.platform == "win32" and os.path.exists(ico):
                r.iconbitmap(ico)
        except Exception:
            pass
        _dark_titlebar(r)   # match the app instead of a light system bar

        body = tk.Frame(r, bg=BG)
        body.pack(fill="both", expand=True)
        rail = tk.Frame(body, bg=RAIL_BG, width=196)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        self.main = tk.Frame(body, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)
        self._build_rail(rail)
        self._show_idle()
        if getattr(self, "_boot", None) is None and UPDATE_MSG:
            self._log(UPDATE_MSG)
        self._poll_boot()

    def _poll_boot(self):
        """Show the launch update check's result once, when it has landed:
        at build time, or later from _drain if it outlived the splash."""
        boot = getattr(self, "_boot", None)
        if boot is None or getattr(self, "_boot_shown", False):
            return
        if not boot["evt"].is_set():
            return
        self._boot_shown = True
        text = _update_text(boot)
        if text:
            self._log(text)

    def _build_rail(self, rail):
        brand = tk.Frame(rail, bg=RAIL_BG)
        brand.pack(fill="x", padx=18, pady=(20, 16))
        try:
            self._icon = tk.PhotoImage(file=os.path.join(BASE, "witness_logo_small.png"))
            tk.Label(brand, image=self._icon, bg=RAIL_BG).pack(side="left", padx=(0, 11))
        except Exception:
            pass
        # text wordmark (the image clipped inside the narrow rail)
        tk.Label(brand, text="WITNESS", bg=RAIL_BG, fg=ACCENT,
                 font=("Segoe UI", 15, "bold")).pack(side="left")

        nav = tk.Frame(rail, bg=RAIL_BG)
        nav.pack(fill="x", padx=12, pady=(6, 0))
        items = [("Live", self._go_live), ("Reels", self.open_reels),
                 ("Stats", self.open_stats), ("Teach a game", self.open_teach),
                 ("Settings", self.open_settings), ("How to use", self.open_help),
                 ("Folder", self.open_folder)]
        self._nav_btns = {}
        for label, cmd in items:
            b = tk.Button(nav, text=f"{self.DOT}   {label}", command=cmd,
                          bg=RAIL_BG, fg=NAV_MUTED, activebackground="#17202b",
                          activeforeground=TEXT, relief="flat", bd=0, anchor="w",
                          font=("Consolas", 11), cursor="hand2", padx=12, pady=9)
            b.pack(fill="x", pady=1)
            self._nav_btns[label] = b
        self._nav_active("Live")

        tk.Frame(rail, bg=RAIL_BG).pack(fill="both", expand=True)  # spacer

        # test mode (subtle) + start/stop at the foot of the rail
        drow = tk.Frame(rail, bg=RAIL_BG)
        drow.pack(fill="x", padx=18, pady=(0, 6))
        tk.Label(drow, text="Test mode", bg=RAIL_BG, fg=NAV_MUTED,
                 font=("Consolas", 9)).pack(side="left")
        self.dry_btn = tk.Button(drow, width=8, relief="flat", bd=0, pady=2,
                                 font=("Consolas", 8, "bold"), cursor="hand2",
                                 command=self._flip_dry)
        self._render_dry()
        self.dry_btn.pack(side="right")
        self.toggle = tk.Button(rail, text="START", command=self.toggle_run,
                                bg=ACCENT, fg=BG, activebackground="#8746c4",
                                activeforeground=BG, relief="flat",
                                font=("Segoe UI", 13, "bold"), height=2,
                                cursor="hand2")
        self.toggle.pack(fill="x", padx=16, pady=(0, 18))

    def _nav_active(self, name):
        for label, b in self._nav_btns.items():
            on = label == name
            b.config(bg="#17202b" if on else RAIL_BG,
                     fg=TEXT if on else NAV_MUTED)

    # --- main stage: idle / live --------------------------------------------

    def _clear_main(self):
        for w in self.main.winfo_children():
            w.destroy()

    def _show_idle(self):
        self._clear_main()
        self._live_built = False
        wrap = tk.Frame(self.main, bg=BG)
        wrap.place(relx=.5, rely=.5, anchor="center")
        try:
            self._idle_logo = tk.PhotoImage(file=os.path.join(BASE, "witness_logo_splash.png"))
            tk.Label(wrap, image=self._idle_logo, bg=BG).pack()
        except Exception:
            tk.Label(wrap, text=self.DOT, bg=BG, fg=ACCENT,
                     font=("Segoe UI", 60)).pack()
        tk.Label(wrap, text="Ready when you are.", bg=BG, fg=TEXT,
                 font=("Segoe UI", 20, "bold")).pack(pady=(18, 6))
        tk.Label(wrap, text="P R E S S   S T A R T   T O   B E G I N", bg=BG,
                 fg=MUTED, font=("Consolas", 9)).pack()
        # why the last START failed stays here, in red, until the next START
        text, color = self._idle_note_text()
        self.idle_note = tk.Label(wrap, text=text, bg=BG, fg=color,
                                  font=("Consolas", 9), wraplength=520,
                                  justify="center")
        self.idle_note.pack(pady=(14, 0))

    def _idle_note_text(self):
        """(text, color) under 'Ready when you are.': a failed START in red,
        else how the last session ended if it crashed, in grey."""
        err = getattr(self, "_last_error", "")
        if err:
            return err, RED
        return getattr(self, "_session_note", ""), MUTED

    def _show_live(self):
        self._clear_main()
        pad = tk.Frame(self.main, bg=BG)
        pad.pack(fill="both", expand=True, padx=26, pady=24)
        top = tk.Frame(pad, bg=BG)
        top.pack(fill="x")
        stat = tk.Frame(top, bg=BG)
        stat.pack(side="left", anchor="nw")
        self.status = tk.Label(stat, text=f"{self.DOT}  WATCHING", bg=BG, fg=GREEN,
                               font=("Consolas", 15, "bold"))
        self.status.pack(anchor="w")
        self.status_sub = tk.Label(stat, text="SESSION  0:00", bg=BG, fg=MUTED,
                                   font=("Consolas", 10))
        self.status_sub.pack(anchor="w", pady=(5, 0))
        # the phone/iPad address, from main.py's "Live view:" line
        self.addr_lbl = tk.Label(stat, text=getattr(self, "_phone_addr", ""),
                                 bg=BG, fg=MUTED, font=("Consolas", 9))
        self.addr_lbl.pack(anchor="w", pady=(3, 0))
        cnt = tk.Frame(top, bg=BG)
        cnt.pack(side="right", anchor="ne")
        self.count_lbl = tk.Label(cnt, text=str(self.count), bg=BG, fg=ACCENT,
                                  font=("Segoe UI", 40, "bold"))
        self.count_lbl.pack(anchor="e")
        tk.Label(cnt, text="KILLS", bg=BG, fg=MUTED,
                 font=("Consolas", 8, "bold")).pack(anchor="e")

        tiles = tk.Frame(pad, bg=BG)
        tiles.pack(fill="x", pady=(20, 4))
        self._tile_vals = {}
        for key, label, col in [("downs", "DOWNS", TEXT), ("precision", "PRECISION", ACCENT),
                                ("finishers", "FINISHERS", "#f5a623"),
                                ("assists", "ASSISTS", "#37cabb")]:
            t = tk.Frame(tiles, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
            t.pack(side="left", expand=True, fill="x", padx=4)
            v = tk.Label(t, text="0", bg=PANEL, fg=col, font=("Segoe UI", 20, "bold"))
            v.pack(pady=(12, 0))
            tk.Label(t, text=label, bg=PANEL, fg=MUTED,
                     font=("Consolas", 8, "bold")).pack(pady=(2, 12))
            self._tile_vals[key] = v

        tk.Label(pad, text="RECENT", bg=BG, fg="#5f6572",
                 font=("Consolas", 9)).pack(anchor="w", pady=(18, 8))
        self.feed = tk.Frame(pad, bg=BG)
        self.feed.pack(fill="x")

        tk.Label(pad, text="LATEST CLIP", bg=BG, fg="#5f6572",
                 font=("Consolas", 9)).pack(anchor="w", pady=(18, 8))
        clip = tk.Frame(pad, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        clip.pack(fill="x")
        thumb = tk.Frame(clip, bg="#1a1224", width=104, height=60,
                         highlightbackground="#2a1f3a", highlightthickness=1)
        thumb.pack(side="left", padx=14, pady=14)
        thumb.pack_propagate(False)
        tk.Label(thumb, text="▷", bg="#1a1224", fg=ACCENT,
                 font=("Segoe UI", 18)).pack(expand=True)
        cmeta = tk.Frame(clip, bg=PANEL)
        cmeta.pack(side="left", anchor="w")
        self.clip_title = tk.Label(cmeta, text="no clips yet", bg=PANEL, fg=TEXT,
                                   font=("Consolas", 12, "bold"))
        self.clip_title.pack(anchor="w")
        self.clip_sub = tk.Label(cmeta, text="your saved kills show up here",
                                 bg=PANEL, fg=MUTED, font=("Consolas", 9))
        self.clip_sub.pack(anchor="w", pady=(4, 0))
        self._live_built = True
        self._refresh_live()

    def _refresh_live(self):
        if not getattr(self, "_live_built", False):
            return
        vals = tile_values(self.tags)
        for k, v in vals.items():
            self._tile_vals[k].config(text=str(v))
        for w in self.feed.winfo_children():
            w.destroy()
        for hhmmss, tag in self.recent[-3:][::-1]:
            row = tk.Frame(self.feed, bg=PANEL, highlightbackground="#1e2530",
                           highlightthickness=1)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=hhmmss, bg=PANEL, fg="#5f6572",
                     font=("Consolas", 9)).pack(side="left", padx=(14, 10), pady=8)
            tk.Label(row, text=TAG_LABEL.get(tag, tag.upper()), bg=PANEL, fg="#c7ccd6",
                     font=("Consolas", 11)).pack(side="left")
            pill = TAG_PILL.get(tag, "#8a90a0")
            tk.Label(row, text=f" {tag} ", bg=PANEL, fg=pill,
                     font=("Consolas", 8, "bold")).pack(side="right", padx=12)
        if self.latest:
            tag, when = self.latest
            self.clip_title.config(text=TAG_LABEL.get(tag, tag.upper()))
            self.clip_sub.config(text=f"SAVED  {when}")

    def _go_live(self):
        self._nav_active("Live")
        if self.running:
            if not getattr(self, "_live_built", False):
                self._show_live()
        else:
            self._show_idle()

    # --- test mode -----------------------------------------------------------

    def _render_dry(self):
        on = self.dry.get()
        self.dry_btn.config(text="[X] ON" if on else "[  ]OFF",
                            bg=ACCENT if on else RAIL_BG,
                            fg=BG if on else NAV_MUTED,
                            activebackground="#8746c4" if on else "#17202b",
                            activeforeground=BG if on else TEXT)

    def _flip_dry(self):
        self.dry.set(not self.dry.get())
        self._render_dry()

    # --- run control ---------------------------------------------------------

    def toggle_run(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        self.count = 0
        self.tags = []
        self.recent = []
        self.latest = None
        self._last_error = ""
        self._session_note = ""
        # Same reload as the web window: an edited config.yaml applies on
        # START. Server keys stay as launched once main.py has bound the port.
        web = getattr(app, "_web_state", None)
        cfg_err = reload_config_into(
            self.cfg, app.load_config, lock=getattr(web, "_lock", None),
            keep=_BOUND_AT_LAUNCH if getattr(app, "_web_server", None) else ())
        self._cfg_err = cfg_err
        self.session_start = time.monotonic()
        self.stop_event = threading.Event()
        self._orig_out = sys.stdout
        self._orig_err = sys.stderr
        sys.stdout = sys.stderr = QueueWriter(self.q)
        self.running = True
        self.toggle.config(text="STOP", bg=RED, activebackground="#d63a30", fg="white")
        self._show_live()
        self._set_status(f"{self.DOT}  STARTING…", MUTED,
                         cfg_err[:110] if cfg_err else "loading the text reader…")
        dry = self.dry.get()
        real_err = self._orig_err

        def run():
            try:
                app.run_live(self.cfg, dry, self.stop_event,
                             on_count=lambda n: self.q.put(("count", n)))
            except Exception as e:
                try:   # the traceback to the real stderr (a log file under pythonw)
                    traceback.print_exc(file=real_err)
                except Exception:
                    pass
                self.q.put(("log", "ERROR: " + friendly_start_error(
                    e, getattr(app, "_log_path", None)) + "\n"))
            finally:
                self.q.put(("stopped", None))
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def stop(self):
        if self.stop_event:
            self.stop_event.set()
        self.toggle.config(text="FINISHING…", state="disabled",
                           bg=PANEL, activebackground=PANEL, fg=MUTED)
        if hasattr(self, "status"):
            self._set_status(f"{self.DOT}  FINISHING…", MUTED,
                             "building your recap + reels…")

    def _finish_stop(self):
        self.running = False
        try:
            sys.stdout = self._orig_out
            sys.stderr = self._orig_err
        except Exception:
            pass
        self.toggle.config(text="START", state="normal", bg=ACCENT,
                           activebackground="#8746c4", fg=BG)
        self.session_start = None
        self._show_idle()

    # --- nav actions ---------------------------------------------------------

    def open_dashboard(self):
        if self.running and self.cfg.get("web_dashboard", True):
            self._open(f"http://localhost:{self.cfg.get('web_port', 8000)}")
            return
        p = os.path.join(BASE, "stats", "dashboard.html")
        self._open(p if os.path.exists(p) else BASE)

    def open_reels(self):
        self._nav_active("Reels")
        # reels live in the Archive (every session, watch + download)
        if self.running and self.cfg.get("web_dashboard", True):
            self._open(f"http://localhost:{self.cfg.get('web_port', 8000)}/archive")
            return
        p = os.path.join(BASE, "stats", "dashboard.html")
        self._open(p if os.path.exists(p) else BASE)

    def open_stats(self):
        self._nav_active("Stats")
        if self.running and self.cfg.get("web_dashboard", True):
            self._open(f"http://localhost:{self.cfg.get('web_port', 8000)}/stats")
            return
        p = os.path.join(BASE, "stats", "dashboard.html")
        self._open(p if os.path.exists(p) else BASE)

    def open_settings(self):
        SettingsWindow(self.root, self.cfg, self._log)

    def open_teach(self):
        if self.running:
            self._log("Stop the current session first, then teach a game.")
            return
        try:
            subprocess.Popen([sys.executable, os.path.join(BASE, "teach_gui.py")],
                             cwd=BASE,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self._log(f"Could not launch the wizard: {e}")

    def open_help(self):
        HelpWindow(self.root)

    def open_folder(self):
        self._open(BASE)

    # --- helpers -------------------------------------------------------------

    def _open(self, path):
        try:
            os.startfile(path)
        except Exception as e:
            self._log(f"Could not open {path}: {e}")

    def _set_status(self, text, color, sub=None):
        if hasattr(self, "status") and self.status.winfo_exists():
            self.status.config(text=text, fg=color)
            if sub is not None and hasattr(self, "status_sub"):
                self.status_sub.config(text=sub)

    def _log(self, msg):
        """No log pane here, so show the latest line where the eye already is:
        under the status while live, under 'Ready when you are.' when idle."""
        lines = str(msg or "").strip().splitlines()
        if not lines:
            return
        text = lines[-1][:140]
        for name in ("status_sub", "idle_note"):
            if name == "idle_note" and getattr(self, "_last_error", ""):
                return          # a failed START's reason outranks chatter
            w = getattr(self, name, None)
            try:
                if w is not None and w.winfo_exists():
                    w.config(text=text, fg=MUTED)
                    return
            except Exception:
                pass

    def _tick(self):
        if self.running and self.session_start and hasattr(self, "status_sub"):
            secs = int(time.monotonic() - self.session_start)
            if getattr(self, "_watching", False):
                self.status_sub.config(text=self._session_sub(secs))
        self.root.after(1000, self._tick)

    def _session_sub(self, secs):
        """The line under WATCHING. A START whose config reload failed says so
        for the whole session: the edit in config.yaml was not applied."""
        text = f"SESSION  {secs // 60}:{secs % 60:02d}"
        if getattr(self, "_cfg_err", ""):
            text += "   " + _RELOAD_NOTICE
        return text

    def _show_phone_address(self, url):
        """This panel has no log pane, so the address main.py prints for the
        phone/iPad used to be visible nowhere. Shown under the status.

        main.py prints its LAN address whatever web_lan says, but with
        web_lan: false it binds 127.0.0.1, so that address refuses the iPad.
        self.cfg holds the value it bound with (web_lan is kept as launched
        once the server is up)."""
        cfg = getattr(self, "cfg", None) or {}
        if cfg.get("web_lan", True):
            self._phone_addr = f"PHONE / IPAD (same Wi-Fi):  {url}"
        else:
            self._phone_addr = ("PHONE / IPAD: off - this PC only "
                                "(web_lan: false in config.yaml)")
        w = getattr(self, "addr_lbl", None)
        try:
            if w is not None and w.winfo_exists():
                w.config(text=self._phone_addr)
        except Exception:
            pass

    def _parse_kill(self, line):
        # 'KILL #7 [down]: ...', '(not a kill: precision) [precision]: ...',
        # '[reconcile] ...', '[exfil] match stats: ...': every event, in order,
        # so the tiles come from the same tally as the audit.
        try:
            before = len(self.tags)
            parse_console_line(line, self.tags)
            if len(self.tags) == before:
                return
            m = re.match(r"\s*KILL #\d+ \[(\w+)\]", line)
            if m:      # the feed and LATEST CLIP stay kills only
                tag = m.group(1)
                self.recent.append((time.strftime("%H:%M:%S"), tag))
                self.latest = (tag, time.strftime("%H:%M"))
            self._refresh_live()
        except Exception:
            pass

    def _drain(self):
        try:
            self._poll_boot()
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    # one print() can carry several lines (exfil report)
                    for s in str(val).splitlines():
                        self._drain_line(s.rstrip())
                elif kind == "count":
                    self.count = val
                    if hasattr(self, "count_lbl") and self.count_lbl.winfo_exists():
                        self.count_lbl.config(text=str(val))
                elif kind == "stopped":
                    self._watching = False
                    self._finish_stop()
        except queue.Empty:
            pass
        finally:
            # rescheduled even if a line blew up, or the panel froze for good
            self.root.after(100, self._drain)

    def _drain_line(self, s):
        if not s:
            return
        if s.startswith("ERROR:"):
            detail = s[len("ERROR:"):].strip()
            if getattr(self, "_watching", False):
                # The session had gone live ("Detecting [" seen; 'stopped' is
                # queued after this line): detection or the recap failed after
                # a good start. Not a start error, and not red.
                self._session_note = ("The last session ended with an error: "
                                      + detail)
                self._set_status(f"{self.DOT}  SESSION ENDED WITH AN ERROR",
                                 AMBER, detail[:140])
                return
            # a failed START: red, and kept on the idle screen after "stopped";
            # a failed config reload goes first, it is usually the cause
            s = "ERROR: " + with_reload_error(getattr(self, "_cfg_err", ""),
                                              detail)
            self._last_error = s
            self._set_status(f"{self.DOT}  COULD NOT START", RED, s[:140])
            return
        m = re.search(r"Live view:\s+(\S+)", s)
        if m:
            self._show_phone_address(m.group(1))
        if "Detecting [" in s:
            self._watching = True
            self._set_status(f"{self.DOT}  WATCHING", GREEN,
                             self._session_sub(0))
        if ((("KILL #" in s or "(not a kill:" in s) and "[" in s)
                or "[reconcile]" in s or "[exfil] match stats" in s):
            self._parse_kill(s)

class SettingsWindow:
    """Same live-appliable settings as the web dashboard's panel — changes hit
    the running session immediately and persist to settings_override.yaml."""

    def __init__(self, parent, cfg, log):
        from webserver import SETTINGS, SETTINGS_META
        self.cfg = cfg
        self.log = log
        self.settings = SETTINGS

        w = tk.Toplevel(parent)
        w.title("Settings")
        w.configure(bg=BG)
        w.geometry("460x520")
        w.transient(parent)
        _dark_titlebar(w)

        tk.Label(w, text="SETTINGS", bg=BG, fg=ACCENT,
                 font=("Consolas", 11, "bold")).pack(anchor="w", padx=20, pady=(16, 4))
        tk.Label(w, text="Changes apply immediately — no restart needed.",
                 bg=BG, fg=MUTED, font=("Consolas", 9)).pack(anchor="w", padx=20, pady=(0, 10))

        body = tk.Frame(w, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        self.vars = {}
        for key, label in SETTINGS_META:
            default, typ = SETTINGS[key]
            row = tk.Frame(body, bg=BG)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=BG, fg=TEXT,
                     font=("Consolas", 10)).pack(side="left")
            if typ is bool:
                # explicit ON/OFF button — native Checkbutton indicators are
                # invisible on a dark theme on Windows
                self._make_toggle(row, key, bool(cfg.get(key, default)))
            else:
                var = tk.StringVar(value=str(cfg.get(key, default)))
                e = tk.Entry(row, textvariable=var, width=6, bg=PANEL, fg=TEXT,
                             insertbackground=TEXT, relief="flat", justify="center",
                             font=("Consolas", 10))
                e.pack(side="right")
                e.bind("<FocusOut>", lambda _e, k=key, v=var: self._save_num(k, v))
                e.bind("<Return>", lambda _e, k=key, v=var: self._save_num(k, v))
                self.vars[key] = var

    def _make_toggle(self, row, key, initial):
        btn = tk.Button(row, width=9, relief="raised", bd=2, padx=6, pady=2,
                        font=("Consolas", 9, "bold"), cursor="hand2")

        def render(val):
            # ASCII box in the label so the state reads even if button bg
            # colors don't paint on the user's Windows theme
            btn.config(text="[X] ON" if val else "[  ] OFF",
                       bg=ACCENT if val else PANEL,
                       fg=BG if val else TEXT,
                       activebackground="#8746c4" if val else LINE,
                       activeforeground=BG if val else TEXT)

        def flip():
            val = not bool(self.cfg.get(key, initial))
            render(val)
            self._save(key, val)

        btn.config(command=flip)
        render(initial)
        btn.pack(side="right")

    def _save(self, key, value):
        self.cfg[key] = value
        try:
            app.save_setting_overrides({key: value})
        except Exception as e:
            self.log(f"Could not save setting: {e}")
            return
        self.log(f"Setting saved: {key} = {value}")

    def _save_num(self, key, var):
        try:
            value = max(0.0, float(var.get()))
        except ValueError:
            var.set(str(self.cfg.get(key, 0)))
            return
        self._save(key, value)


HELP_TEXT = """KILL COUNTER + FEED
Kills are detected automatically from the game screen. The count, the
tiles and the RECENT feed update within a second or two of each kill.

TEST MODE
Runs the full detector but never saves clips — good for a first run.

DASHBOARD
While running, opens the live view. It also works on an iPad or phone on
the same Wi-Fi: once a session starts, this window shows the address to
open, under the status line. If the phone can't load it, allow Python on
Private networks when Windows Firewall asks, and set your Wi-Fi to
Private. The live view has SAVE CLIP, instant replays, match highlight
reels, a kill ding, and the same settings panel.

MATCH HIGHLIGHTS
About 30 seconds after you exfil, that match's clips become a highlight
reel: stat card, Play of the Game, then every clip. An announcer version
is built alongside it. Drop mp3s in the music folder for a soundtrack.

SETTINGS
Every option applies to the running session immediately and persists.
config.yaml still holds the full documented configuration; edits to it
apply the next time you press START (port and Wi-Fi changes need a
restart of WITNESS).

WHERE FILES GO
Clips land in your OBS output folder under Marathon Sessions/<date>/ —
reels in reels/, vertical Shorts in shorts/, instant-replay copies in
replays/, plus a screenshot of each exfil screen. A session recap,
match card, and montage are written when you press STOP."""


class HelpWindow:
    def __init__(self, parent):
        w = tk.Toplevel(parent)
        w.title("How to use")
        w.configure(bg=BG)
        w.geometry("560x560")
        w.transient(parent)
        _dark_titlebar(w)
        tk.Label(w, text="HOW TO USE", bg=BG, fg=ACCENT,
                 font=("Consolas", 11, "bold")).pack(anchor="w", padx=20, pady=(16, 6))
        t = scrolledtext.ScrolledText(w, bg=PANEL, fg=TEXT, relief="flat",
                                      font=("Consolas", 10), wrap="word", borderwidth=0)
        t.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        t.insert("1.0", HELP_TEXT)
        t.configure(state="disabled")


def _load_splash_frames():
    """Every frame of witness_splash.gif as PhotoImages (the sheen/glitch
    boot), or None. Uses Pillow's frame iterator — reliable across Tk builds,
    unlike tk's native 'gif -index' (which silently fails on some Windows Tk
    versions, leaving only the static fallback). Needs a Tk root to exist."""
    path = os.path.join(BASE, "witness_splash.gif")
    if not os.path.exists(path):
        return None
    try:
        from PIL import Image, ImageSequence, ImageTk
        im = Image.open(path)
        frames = [ImageTk.PhotoImage(f.convert("RGBA"))
                  for f in ImageSequence.Iterator(im)]
        return frames or None
    except Exception:
        pass
    # last-ditch: tk's native gif reader
    try:
        frames, i = [], 0
        while i < 400:
            try:
                frames.append(tk.PhotoImage(file=path, format=f"gif -index {i}"))
            except tk.TclError:
                break
            i += 1
        return frames or None
    except Exception:
        return None


def _show_splash(root, frames):
    """Borderless splash. Plays the pre-rendered sheen frames when available,
    else a clean static badge + wordmark + tagline. Window-opacity fade is
    handled in main(). Click dismisses early."""
    sp = tk.Toplevel(root)
    sp.overrideredirect(True)
    sp.configure(bg=BG)
    try:
        sp.attributes("-alpha", 0.0)   # start invisible; main() fades it in
    except Exception:
        pass
    if frames:
        w, h = frames[0].width(), frames[0].height()
    else:
        w, h = 420, 340
    x = (sp.winfo_screenwidth() - w) // 2
    y = (sp.winfo_screenheight() - h) // 2
    sp.geometry(f"{w}x{h}+{x}+{y}")
    if frames:
        sp._lbl = tk.Label(sp, image=frames[0], bd=0, bg=BG)
        sp._lbl.pack(fill="both", expand=True)
    else:
        card = tk.Frame(sp, bg=BG, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=BG)
        inner.place(relx=.5, rely=.5, anchor="center")
        try:
            sp._logo = tk.PhotoImage(file=os.path.join(BASE, "witness_logo_splash.png"))
            tk.Label(inner, image=sp._logo, bg=BG).pack()
        except Exception:
            tk.Label(inner, text="WITNESS", bg=BG, fg=ACCENT,
                     font=("Segoe UI Black", 26, "bold")).pack()
        try:
            sp._wm = tk.PhotoImage(file=os.path.join(BASE, "witness_wordmark_small.png"))
            tk.Label(inner, image=sp._wm, bg=BG).pack(pady=(20, 0))
        except Exception:
            tk.Label(inner, text="WITNESS", bg=BG, fg=ACCENT,
                     font=("Segoe UI Black", 22, "bold")).pack(pady=(20, 0))
        tk.Label(inner, text="I T   S E E S   E V E R Y T H I N G", bg=BG,
                 fg=MUTED, font=("Consolas", 9)).pack(pady=(12, 0))
    sp.update()
    return sp


def _fade(win, start, end, ms, done):
    """Ease the window's opacity from start to end over ~ms, then call done().
    Degrades to an instant switch where -alpha isn't supported."""
    steps, i = 10, [0]
    delay = max(12, ms // steps)

    def step():
        t = i[0] / steps
        v = start + (end - start) * (t * t * (3 - 2 * t))   # smoothstep
        try:
            win.attributes("-alpha", max(0.0, min(1.0, v)))
        except Exception:
            done()
            return
        if i[0] >= steps:
            done()
            return
        i[0] += 1
        win.after(delay, step)
    step()


def _splash_boom_setup():
    """(boom_frame_index, wav_path) if the boot sound should fire, else None.
    Off unless the wav + frame marker exist AND splash_sound isn't disabled."""
    if sys.platform != "win32":
        return None
    wav = os.path.join(BASE, "splash_boom.wav")
    marker = os.path.join(BASE, "splash_boom_frame.txt")
    if not (os.path.exists(wav) and os.path.exists(marker)):
        return None
    # honor splash_sound (settings_override.yaml wins, else config.yaml, default on)
    # UTF-8 explicitly: config.yaml is UTF-8 with em dashes, and the Windows
    # default code page can't decode it on CJK systems (cp932 etc.)
    want = True
    for name in ("config.yaml", "settings_override.yaml"):
        p = os.path.join(BASE, name)
        try:
            if os.path.exists(p):
                d = _read_yaml(p)
                if isinstance(d, dict) and "splash_sound" in d:
                    want = bool(d["splash_sound"])
        except Exception:
            pass
    if not want:
        return None
    try:
        with open(marker, encoding="utf-8") as f:
            return int(f.read().strip()), wav
    except Exception:
        return None


def _play_boom(wav):
    try:
        import winsound
        winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)
    except Exception:
        pass


def _dark_titlebar(win):
    """Paint the native Windows title bar dark so it matches the app instead
    of the light 'Windows 95' grey. No-op on non-Windows / older builds."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (20 new, 19 old)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
        # nudge a redraw so the bar repaints immediately
        win.withdraw(); win.deiconify()
    except Exception:
        pass


def _claim_app_identity():
    """Tell Windows this is its own app (not 'Python'), so the taskbar shows
    the WITNESS icon and groups under our name."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WITNESS.KillRecorder")
    except Exception:
        pass


def main():
    """Entry point (`pythonw gui.py`). Whatever goes wrong ends in a message
    box naming a file in logs/, never in an invisible pythonw.exe that only
    Task Manager can find."""
    _ensure_stderr()
    try:
        _boot()
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        _note(tb)
        path = _write_log(f"crash_{_stamp()}.log", tb)
        _fatal("WITNESS stopped unexpectedly.\n\nDetails were saved to:\n"
               + (path or os.path.join(BASE, "logs")))


def _boot_callback_error(root):
    """root.report_callback_exception for the boot (splash) loop. The root is
    withdrawn and the splash may already be faded out, so an error here would
    otherwise leave a process with no window at all: log it, say so, exit."""
    def handler(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        _note(text)
        path = _write_log(f"crash_{_stamp()}.log", text)
        try:
            for w in root.winfo_children():   # the splash must not cover the box
                w.destroy()
        except Exception:
            pass
        _fatal(f"WITNESS hit an error while starting and has to close.\n\n{val}"
               f"\n\nDetails were saved to:\n{path or os.path.join(BASE, 'logs')}",
               parent=root)
    return handler


def _panel_callback_error(root):
    """report_callback_exception for the (visible) tk control panel: log it and
    show it, but keep the window, at most one box per 10 s."""
    last = {"t": 0.0}

    def handler(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        _note(text)
        path = _write_log(f"crash_{_stamp()}.log", text)
        if time.monotonic() - last["t"] > 10:
            last["t"] = time.monotonic()
            _show_error(f"Something went wrong: {val}\n\nDetails: {path or 'logs/'}",
                        parent=root)
    return handler


# --- auto-update at launch ---------------------------------------------------------
# The thread that ran it was dropped in 1e8ec12 (the splash rework), so the
# desktop app never updated and the dashboard read "Updater: (no update
# message)". Restoring the old one-liner would bring back its race: it
# relaunched from a thread whenever the download finished, which on a slow
# first sync was after the splash had gone, the window was up and :8000 was
# bound, so a second copy started next to the first. Now the check only
# STAGES files, and the relaunch happens only from the splash, before
# `import main` and before start_web.

UPDATE_HOLD_CAP = 30.0     # s the splash waits on the launch update check
# ...while pip adds a package for files already written, or when the updater
# has no ABORT_WRITES yet: then a check cut off at 30 s would go on to write
# files under the running app, so waiting longer is the lesser evil.
LONG_HOLD_CAP = 120.0


def _new_boot_state() -> dict:
    return {"done": False, "changed": False, "msg": "", "note": "",
            "phase": "checking", "can_abort": False, "evt": threading.Event()}


def _write_update_status(msg: str, append: bool = False) -> None:
    """update_status.txt, the updater's diagnosis file (no console here)."""
    try:
        with open(os.path.join(BASE, "update_status.txt"), "a" if append else "w",
                  encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{msg}\n")
    except Exception:
        pass


def check_updates(boot: dict) -> None:
    """Runs on a thread behind the splash. Stage only: updater.check_and_update
    downloads and writes; it never relaunches from here. If requirements.txt
    changed, new packages are installed before the relaunch, so the fresh copy
    can import them."""
    try:
        import updater
        boot["can_abort"] = getattr(updater, "ABORT_WRITES", None) is not None
        changed, msg = updater.check_and_update(BASE)
        boot["msg"] = msg
        _write_update_status(msg)
        files = getattr(updater, "LAST_CHANGED_FILES", None) or ()
        if changed and "requirements.txt" in files:
            ensure = getattr(updater, "ensure_requirements", None)
            if ensure is not None:
                boot["phase"] = "installing"
                try:
                    _ok, note = ensure(BASE)
                    boot["note"] = str(note or "")
                except Exception as e:
                    boot["note"] = (f"New components will install on the next "
                                    f"launch ({type(e).__name__}: {e})")
        boot["changed"] = bool(changed)     # only now: hold() relaunches on it
    except Exception as e:
        boot["msg"] = f"Update check skipped: {e}"
    finally:
        boot["done"] = True
        boot["evt"].set()
        if boot.get("aborted"):
            _abort_flag(clear=True)   # this check is over; manual updates work again


def _take_relaunch_marker(boot: dict) -> bool:
    """True when this process is the fresh copy an update just started. Then
    there is no second check (no loop): record it and carry the note on."""
    if os.environ.get("MKR_UPDATED") != "1":
        return False
    os.environ.pop("MKR_UPDATED", None)            # not inherited by teach_gui etc.
    note = os.environ.pop("MKR_UPDATE_NOTE", "")
    msg = "Restarted on the latest version (update applied)."
    if note:
        msg += "  " + note
    boot["msg"] = msg
    _write_update_status(msg, append=True)
    boot["done"] = True
    boot["evt"].set()
    return True


def _splash_step(elapsed: float, intro_over: bool, boot: dict) -> str:
    """What the splash does next. Pure, so it is testable without a window.
      'relaunch' the update wrote new files: restart on them NOW, while
                 nothing from main/webserver is imported and no port is bound
      'leave'    the intro has played and the check is finished
      'abort'    the check overran its cap: stop it writing, go in
      'wait'     intro still playing, or the check still running"""
    if boot.get("done"):
        if boot.get("changed"):
            return "relaunch"
        return "leave" if intro_over else "wait"
    if not intro_over:
        return "wait"
    short = boot.get("can_abort") and boot.get("phase") != "installing"
    cap = UPDATE_HOLD_CAP if short else LONG_HOLD_CAP
    return "abort" if elapsed >= cap else "wait"


def _relaunch_updated(note: str = ""):
    """Start a fresh copy on the files the updater just wrote, then end this
    one. Returns an error string only if the new copy could not be started."""
    try:
        env = dict(os.environ, MKR_UPDATED="1", MKR_UPDATE_NOTE=str(note or ""))
        subprocess.Popen([sys.executable] + list(sys.argv), cwd=BASE, env=env)
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    os._exit(0)


def _abort_flag(clear: bool = False) -> None:
    try:
        import updater
        ev = getattr(updater, "ABORT_WRITES", None)
        if ev is None:
            return
        if clear:
            ev.clear()
        else:
            ev.set()
    except Exception:
        pass


def _abort_update_writes(boot: dict) -> None:
    """Cap hit: tell the updater to write nothing (it finishes downloading in
    the background and tries again next launch). ABORT_WRITES arrives with the
    updater package; without it this is a no-op and a late check can still
    write, which the dashboard then reports as RESTART WITNESS to apply.

    The flag is cleared again once this check is over (by whichever of the
    two threads sees the other's step last), or the dashboard's Check for
    update button would defer every update for the rest of the session."""
    boot["aborted"] = True
    _abort_flag()
    if boot.get("done"):
        _abort_flag(clear=True)


def _late_update_msg(st, boot: dict, timeout: float = 600.0) -> None:
    """The launch check outlived the splash: copy its result onto the
    dashboard when it lands. Never relaunches: a session may be running by
    then. `_updating` disables the Check for update button meanwhile."""
    _set_attr(st, "_updating", True)
    try:
        if not boot["evt"].wait(timeout):
            return
        _set_attr(st, "update_msg", boot.get("msg", ""))
        if boot.get("changed"):
            _set_attr(st, "update_changed", True)
    finally:
        _set_attr(st, "_updating", False)


def _update_text(boot: dict) -> str:
    """The launch check's result as one line for the tk panel, which has no
    RESTART banner of its own."""
    msg = str(boot.get("msg") or "")
    if msg and (boot.get("changed") or boot.get("restart_needed")):
        msg += "  - restart WITNESS to apply"
    return msg


def _splash_note(sp, text: str) -> None:
    """A small line along the bottom of the splash ('Checking for updates...')."""
    try:
        lbl = getattr(sp, "_note", None)
        if lbl is None:
            lbl = tk.Label(sp, text=text, bg=BG, fg=MUTED, font=("Consolas", 9))
            lbl.place(relx=0.5, rely=1.0, y=-12, anchor="s")
            sp._note = lbl
        elif lbl.cget("text") != text:
            lbl.config(text=text)
    except Exception:
        pass


def _boot():
    _claim_app_identity()
    # One copy at a time, checked before anything else: before the update
    # check (which would rewrite files under the copy that's running) and
    # before a second splash.
    if _witness_already_running(_configured_port()):
        _show_error(_ALREADY_RUNNING, info=True)
        return
    root = tk.Tk()
    root.withdraw()
    root.report_callback_exception = _boot_callback_error(root)

    # Update check runs BEHIND the splash so the cinematic actually plays.
    # Started before the splash so the network round-trip overlaps it.
    state = _new_boot_state()
    if not _take_relaunch_marker(state):
        threading.Thread(target=check_updates, args=(state,), daemon=True).start()

    try:
        frames = _load_splash_frames()
    except Exception:
        frames = None
    splash = _show_splash(root, frames)
    born = time.monotonic()
    MIN_HOLD = 0.4 if frames else 1.3   # frames carry their own runtime
    skip = {"on": False}
    phase = {"leaving": False}
    anim = {"done": not frames}
    boom = _splash_boom_setup()         # (frame_index, wav_path) or None
    splash.bind("<Button-1>", lambda e: skip.update(on=True))

    def finish():
        global UPDATE_MSG, app
        UPDATE_MSG = state["msg"]
        try:
            import main as app_module
        except Exception as e:
            # Typically a half-finished install (the multi-GB download was cut
            # off): main imports yaml/torch/easyocr. This runs inside a Tk
            # callback after the splash faded to nothing, so without this the
            # process lived on with no window at all.
            tb = traceback.format_exc()
            _note(tb)
            path = _write_log("startup_error.log", tb)
            try:
                splash.destroy()
            except Exception:
                pass
            if isinstance(e, ImportError):
                text = ("Some components are missing (setup did not finish). "
                        "Close this and run START again - setup finishes itself. "
                        "If it keeps happening, delete the .venv folder and run "
                        "START again.\n" + str(e))
            else:
                text = f"WITNESS could not start: {type(e).__name__}: {e}"
            if path:
                text += f"\n\nDetails: {path}"
            _show_error(text, parent=root)
            try:
                root.quit()
            except Exception:
                pass
            os._exit(1)
        app = app_module
        try:
            splash.destroy()
        except Exception:
            pass
        try:
            root.quit()          # end the splash loop; the app runs after it
        except Exception:
            pass

    def leave():
        if phase["leaving"]:
            return
        phase["leaving"] = True
        _fade(splash, 1.0, 0.0, 220, finish)

    def play(i=0):
        if not frames or not splash.winfo_exists():
            return
        splash._lbl.config(image=frames[min(i, len(frames) - 1)])
        if boom and i == boom[0] and not skip["on"]:
            _play_boom(boom[1])         # low hit, synced to the glitch
        if i < len(frames) - 1 and not skip["on"]:
            splash.after(54, play, i + 1)   # ~54ms/frame (matches the gif)
        else:
            anim["done"] = True     # hold on the last frame

    def hold():
        if phase["leaving"]:
            return
        elapsed = time.monotonic() - born
        # The intro is over once the animation has played and the sound has
        # had time to breathe (or on a click, or at 4 s if frames stall).
        # After that the splash only waits for the update check, and says so.
        intro_over = (skip["on"] or (anim["done"] and elapsed >= MIN_HOLD)
                      or elapsed >= 4.0)
        step = _splash_step(elapsed, intro_over, state)
        if step == "relaunch":
            # Still on the splash: main/webserver not imported, no port bound,
            # so the fresh copy can't collide with this one or mix old and
            # new code. _relaunch_updated returns only if Popen failed.
            err = _relaunch_updated(state.get("note", ""))
            state["changed"] = False
            state["restart_needed"] = True
            state["msg"] = (f"{state.get('msg', '')}  (could not restart by "
                            f"itself: {err})").strip()
            step = _splash_step(elapsed, intro_over, state)
        if step == "abort":
            _abort_update_writes(state)
            leave()
        elif step == "leave":
            leave()
        else:
            if intro_over and not state["done"]:
                _splash_note(splash, "Installing new components..."
                             if state.get("phase") == "installing"
                             else "Checking for updates...")
            splash.after(80, hold)

    _fade(splash, 0.0, 1.0, 260, lambda: None)
    if frames:
        play()
    hold()
    root.mainloop()          # returns when the splash calls root.quit()
    try:
        root.destroy()
    except Exception:
        pass

    # The command center is the HTML dashboard in a native window (webview).
    # If that engine isn't available, fall back to the tk control panel.
    if not _run_webview(state):
        _run_tk_fallback(state)


_LIVE_POLL = 0.25    # s between looks for the session going live


def _notice_when_live(st, mono0, over, text) -> None:
    """Post a feed notice once the session goes live (set_running(True)
    restamps _mono, after main.py's reset() cleared the feed). Gives up when
    the attempt is over without going live: run() then shows the error."""
    while not over.wait(_LIVE_POLL):
        if getattr(st, "_mono", None) != mono0:
            try:
                st.notice(text, "alert")
            except Exception:
                pass
            return


class _WebSession:
    """Bridges the web START/STOP buttons to the detection session.

    A START that fails used to vanish: the error was print()ed after run_live
    had closed its session log, to a stdout that is None under pythonw, and
    the button flipped back after the page's 12 s safety release. Now the
    reason lands on the LiveState as `last_error` (shown by the dashboard while
    not running) and `starting` stays True until the attempt is over."""
    def __init__(self, cfg, state=None):
        self.cfg = cfg
        self.state = state
        self.thread = None
        self.stop_event = None
        self._reload_err = ""      # why the last START's config reload failed
        # Held from the is_alive check until the thread is started: the PC
        # window and the iPad each POST /start on their own server thread,
        # and the config reload in between takes tens of ms.
        self._start_lock = threading.Lock()

    def start(self):
        with self._start_lock:
            self._start()

    def _start(self):
        if self.thread and self.thread.is_alive():
            return      # still running, or still building the last recap
        st = self.state
        _set_attr(st, "last_error", "")
        err = reload_config_into(self.cfg, app.load_config,
                                 lock=getattr(st, "_lock", None),
                                 keep=_BOUND_AT_LAUNCH)
        self._reload_err = err
        if err:
            _note(err)
            _set_attr(st, "last_error", err)
            try:
                st.notice(_RELOAD_NOTICE, "alert")
            except Exception:
                pass
        _set_attr(st, "starting", True)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        st = self.state
        # set_running(True) stamps _mono; a change means the session went live
        mono0 = getattr(st, "_mono", None)
        reload_err = getattr(self, "_reload_err", "")
        over = threading.Event()
        if reload_err:
            # Going live resets the feed (main.py: web.reset(), then
            # set_running), which wipes the notice start() posted. Post it
            # again once live, so a session that starts fine still says the
            # config.yaml edit was not applied.
            threading.Thread(target=_notice_when_live,
                             args=(st, mono0, over, _RELOAD_NOTICE),
                             daemon=True).start()
        try:
            app.run_live(self.cfg, False, self.stop_event)
        except Exception as e:
            _note(traceback.format_exc())
            went_live = (bool(getattr(st, "running", False))
                         or getattr(st, "_mono", None) != mono0)
            try:
                if went_live:
                    # Detection or the recap failed after a good start: a feed
                    # line, not a red banner over the START button.
                    st.notice("Session crashed - see logs/", "alert")
                else:
                    _set_attr(st, "last_error", with_reload_error(
                        reload_err, friendly_start_error(
                            e, getattr(app, "_log_path", None))))
                # STOP / Update / Export refuse while `running` is stuck True
                st.set_running(False)
            except Exception:
                pass
        finally:
            over.set()
            _set_attr(st, "starting", False)

    def stop(self):
        if self.stop_event:
            self.stop_event.set()


def _advertise_for_ipad(cfg, host, port, st, webserver):
    """Bonjour for the iOS app, as main.py does for the console launcher.

    main.py only advertises when it starts the server itself, and in window
    mode it never does (_run_webview did), so the iPad app could not find a PC
    running the window launcher. Same conditions as main.py: served on the
    LAN, bonjour_discovery not off, not already advertising. On a thread,
    because zeroconf's name probing can take a second or two and the window
    should not wait for it. discovery.advertise never raises."""
    if host != "0.0.0.0" or not cfg.get("bonjour_discovery", True):
        return
    if getattr(app, "_advertiser", None) is not None:
        return

    def work():
        try:
            import discovery
            app._advertiser = discovery.advertise(
                port, webserver.local_ip(), version=getattr(st, "version", ""),
                api=getattr(webserver, "API_VERSION", 1))
        except Exception as e:
            _note(f"  [discovery] skipped: {e}")
    threading.Thread(target=work, daemon=True).start()


def _run_webview(boot=None):
    """Serve the dashboard and show it in a native window. run_live reuses the
    already-running server, so START/STOP inside the HTML drive the session.
    Returns False (→ tk fallback) if pywebview / the webview engine is absent.
    `boot` is the launch update check's state (see check_updates)."""
    try:
        import webview
    except ImportError:
        try:   # one-time auto-install, then retry
            subprocess.run([sys.executable, "-m", "pip", "install", "pywebview"],
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            import webview
        except Exception:
            return False
    try:
        cfg = app.load_config()
    except Exception as e:
        # The tk fallback would hit the same file and die the same way, so
        # say what is wrong instead of dropping to it.
        _fatal(_config_error_text(e))
    try:
        import webserver
        port = cfg.get("web_port", 8000)
        host = _bind_host(cfg)
        # The window IS the dashboard, so the session must feed it even when
        # web_dashboard is false; "false" here means this-PC-only (host above).
        # Without this, run_live passed web=None and the window went dead.
        cfg["web_dashboard"] = True
        # Second check, with the port load_config settled on: covers two
        # launches racing past the one at the top of _boot.
        if _witness_already_running(port):
            _fatal(_ALREADY_RUNNING, info=True, code=0)
        st = webserver.LiveState()
        try:
            srv = webserver.start_web(st, port, BASE, host=host)
        except OSError as e:
            # Never hop to another port: the iPad bookmark and the iOS app
            # expect this one, and a silently-working second copy would run a
            # second OCR loop. Never drop to the tk panel for this either.
            _fatal(f"Port {port} is in use. WITNESS may already be running, or "
                   f"change web_port in config.yaml.\n\n({e})")
        # hand the running server to run_live so it reuses it (no 2nd server)
        app._web_state = st
        app._web_server = srv
        _advertise_for_ipad(cfg, host, port, st, webserver)
        st.bind_config(cfg, app.save_setting_overrides)
        # Empty means nothing to say: the page hides the Updater line.
        st.update_msg = UPDATE_MSG
        if boot is not None:
            if boot.get("restart_needed"):
                st.update_changed = True
            if boot["evt"].is_set():
                # finish() read UPDATE_MSG before `import main`; a check that
                # landed after the cap (during the fade or that import) is only
                # in boot. Without this its 'changed' was dropped: files written
                # under the running app, and no RESTART prompt.
                st.update_msg = boot.get("msg") or UPDATE_MSG
                if boot.get("changed"):
                    st.update_changed = True
            else:                              # the check outlived the splash
                threading.Thread(target=_late_update_msg, args=(st, boot),
                                 daemon=True).start()
        try:                       # so Archive/Reels show past sessions on open
            st.record_dir = app.cached_record_dir()
        except Exception:
            pass
        sess = _WebSession(cfg, st)
        st.bind_control(sess.start, sess.stop)
        # If the last session's recap build was interrupted (app closed while
        # rendering), finish it now in the background — banner shows progress.
        threading.Thread(target=lambda: app.resume_unfinished_recap(st),
                         daemon=True).start()
        webview.create_window("WITNESS", f"http://localhost:{port}",
                              width=960, height=660, min_size=(820, 560),
                              background_color="#0e0e16")
        ico = os.path.join(BASE, "witness.ico")
        try:
            webview.start(icon=ico)   # window / taskbar icon
        except TypeError:             # older pywebview without the icon arg
            webview.start()
        return True
    except Exception as e:
        _note(f"(webview unavailable, using the control panel: {e})")
        return False


def _run_tk_fallback(boot=None):
    r = tk.Tk()
    _claim_app_identity()
    r.report_callback_exception = _panel_callback_error(r)
    ControlPanel(r, boot)
    r.mainloop()


if __name__ == "__main__":
    main()
