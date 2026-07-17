"""WITNESS — control-panel window. It sees everything.

Replaces the console: big Start/Stop button, live kill count, a log pane, and
quick buttons to open the dashboard / settings / folder. Launch with:
    pythonw gui.py        (no console window)

Uses only the standard library (tkinter) plus this project's own modules.
"""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import scrolledtext

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

UPDATE_MSG = ""   # set during boot (behind the splash), read by ControlPanel
app = None        # main module, imported during boot

BG = "#0b0f12"
PANEL = "#12181d"
ACCENT = "#9c58da"
TEXT = "#e8edf0"
MUTED = "#7d8a94"
LINE = "#232d34"
RED = "#ff4b42"
GREEN = "#5bd66b"


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

    def __init__(self, root):
        self.root = root
        self.cfg = app.load_config()
        self.q = queue.Queue()
        self.stop_event = None
        self.worker = None
        self.running = False
        self.count = 0
        self.tags = []                 # kill tags this session (for the tiles)
        self.recent = []               # (hhmmss, tag) newest-last
        self.latest = None             # (tag, hhmmss) for the LATEST CLIP card
        self.session_start = None
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
        if UPDATE_MSG:
            self._log(UPDATE_MSG)

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
        c = {}
        for t in self.tags:
            c[t] = c.get(t, 0) + 1
        vals = {"downs": c.get("down", 0) + c.get("kill", 0),
                "precision": c.get("precision", 0),
                "finishers": c.get("finisher", 0),
                "assists": c.get("assist", 0)}
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
        self.session_start = time.monotonic()
        self.stop_event = threading.Event()
        self._orig_out = sys.stdout
        self._orig_err = sys.stderr
        sys.stdout = sys.stderr = QueueWriter(self.q)
        self.running = True
        self.toggle.config(text="STOP", bg=RED, activebackground="#d63a30", fg="white")
        self._show_live()
        self._set_status(f"{self.DOT}  STARTING…", MUTED, "loading the text reader…")
        dry = self.dry.get()

        def run():
            try:
                app.run_live(self.cfg, dry, self.stop_event,
                             on_count=lambda n: self.q.put(("count", n)))
            except Exception as e:
                self.q.put(("log", f"ERROR: {e}\n"))
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
        # command center has no log pane; keep for the session-log file + debug
        pass

    def _tick(self):
        if self.running and self.session_start and hasattr(self, "status_sub"):
            secs = int(time.monotonic() - self.session_start)
            if getattr(self, "_watching", False):
                self.status_sub.config(text=f"SESSION  {secs // 60}:{secs % 60:02d}")
        self.root.after(1000, self._tick)

    def _parse_kill(self, line):
        # 'KILL #7 [precision]: ...'
        try:
            import re
            m = re.search(r"KILL #\d+ \[(\w+)\]", line)
            if m:
                tag = m.group(1)
                self.tags.append(tag)
                self.recent.append((time.strftime("%H:%M:%S"), tag))
                self.latest = (tag, time.strftime("%H:%M"))
                self._refresh_live()
        except Exception:
            pass

    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    s = val.rstrip()
                    if not s:
                        continue
                    if "Detecting [" in s:
                        self._watching = True
                        self._set_status(f"{self.DOT}  WATCHING", GREEN,
                                         "SESSION  0:00")
                    if "KILL #" in s and "[" in s:
                        self._parse_kill(s)
                elif kind == "count":
                    self.count = val
                    if hasattr(self, "count_lbl") and self.count_lbl.winfo_exists():
                        self.count_lbl.config(text=str(val))
                elif kind == "stopped":
                    self._watching = False
                    self._finish_stop()
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

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
Kills are detected automatically from the game screen. The count and
activity log update within a second or two of each kill.

TEST MODE
Runs the full detector but never saves clips — good for a first run.

DASHBOARD
While running, opens the live view (also reachable from an iPad or phone
on the same Wi-Fi — the URL is printed in the activity log). The live
view has SAVE CLIP, instant replays, match highlight reels, a kill ding,
and the same settings panel.

MATCH HIGHLIGHTS
About 30 seconds after you exfil, that match's clips become a highlight
reel: stat card, Play of the Game, then every clip. An announcer version
is built alongside it. Drop mp3s in the music folder for a soundtrack.

SETTINGS
Every option applies to the running session immediately and persists.
config.yaml still holds the full documented configuration.

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
    try:
        import yaml
        want = True
        for name in ("config.yaml", "settings_override.yaml"):
            p = os.path.join(BASE, name)
            if os.path.exists(p):
                d = yaml.safe_load(open(p)) or {}
                if "splash_sound" in d:
                    want = bool(d["splash_sound"])
        if not want:
            return None
    except Exception:
        pass
    try:
        with open(marker) as f:
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
    global UPDATE_MSG, app
    _claim_app_identity()
    root = tk.Tk()
    root.withdraw()

    # Update check runs BEHIND the splash so the cinematic actually plays.
    state = {"done": False, "relaunch": False, "msg": ""}

    def check_updates():
        try:
            import updater
            state["msg"] = updater.update_and_relaunch_if_needed(BASE)
        except SystemExit:
            # updater spawned the fresh process and asked us to die —
            # sys.exit in a thread doesn't kill the app, so signal it
            state["relaunch"] = True
        except Exception as e:
            state["msg"] = f"Update check skipped: {e}"
        state["done"] = True

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
        import main as app_module
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
        if state["relaunch"]:
            os._exit(0)
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
        if state["relaunch"]:
            os._exit(0)          # replaced by the freshly-updated process
        elapsed = time.monotonic() - born
        ready = state["done"] and anim["done"] and elapsed >= MIN_HOLD
        # hard cap: never let a slow update check leave the splash hanging —
        # show the app after MAX_WAIT regardless (the update thread will still
        # relaunch if it finishes with new code).
        if skip["on"] or ready or elapsed >= 9.0:
            leave()
        else:
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
    if not _run_webview():
        _run_tk_fallback()


class _WebSession:
    """Bridges the web START/STOP buttons to the detection session."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.thread = None
        self.stop_event = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event = threading.Event()

        def run():
            try:
                app.run_live(self.cfg, False, self.stop_event)
            except Exception as e:
                print(f"(session error: {e})")
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def stop(self):
        if self.stop_event:
            self.stop_event.set()


def _run_webview():
    """Serve the dashboard and show it in a native window. run_live reuses the
    already-running server, so START/STOP inside the HTML drive the session.
    Returns False (→ tk fallback) if pywebview / the webview engine is absent."""
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
        import webserver
        cfg = app.load_config()
        port = cfg.get("web_port", 8000)
        host = "0.0.0.0" if cfg.get("web_lan", True) else "127.0.0.1"
        st = webserver.LiveState()
        srv = webserver.start_web(st, port, BASE, host=host)
        # hand the running server to run_live so it reuses it (no 2nd server)
        app._web_state = st
        app._web_server = srv
        st.bind_config(cfg, app.save_setting_overrides)
        try:                       # so Archive/Reels show past sessions on open
            st.record_dir = app.cached_record_dir()
        except Exception:
            pass
        sess = _WebSession(cfg)
        st.bind_control(sess.start, sess.stop)
        webview.create_window("WITNESS", f"http://localhost:{port}",
                              width=960, height=660, min_size=(820, 560),
                              background_color="#0b0f12")
        ico = os.path.join(BASE, "witness.ico")
        try:
            webview.start(icon=ico)   # window / taskbar icon
        except TypeError:             # older pywebview without the icon arg
            webview.start()
        return True
    except Exception as e:
        print(f"(webview unavailable, using the control panel: {e})")
        return False


def _run_tk_fallback():
    r = tk.Tk()
    _claim_app_identity()
    ControlPanel(r)
    r.mainloop()


if __name__ == "__main__":
    main()
