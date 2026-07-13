"""A simple control-panel window for the Marathon Kill Recorder.

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
import tkinter as tk
from tkinter import scrolledtext

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import main as app  # noqa: E402  (reuse run_live / load_config)

BG = "#0b0f12"
PANEL = "#12181d"
ACCENT = "#d3f24b"
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


class ControlPanel:
    def __init__(self, root):
        self.root = root
        self.cfg = app.load_config()
        self.q = queue.Queue()
        self.stop_event = None
        self.worker = None
        self.running = False
        self.count = 0
        self._icon = None
        self._build()
        self.root.after(100, self._drain)

    # --- layout --------------------------------------------------------------

    def _build(self):
        r = self.root
        r.title("Marathon Kill Recorder")
        r.configure(bg=BG)
        r.geometry("580x620")
        r.minsize(520, 560)

        # header: O emblem + MARATHON wordmark + subtitle
        head = tk.Frame(r, bg=BG)
        head.pack(fill="x", padx=20, pady=(18, 8))
        try:
            skull = tk.PhotoImage(file=os.path.join(BASE, "marathon_skull.png"))
            self._icon_full = skull  # keep a ref for the window/taskbar icon
            self._icon = skull.subsample(max(1, skull.height() // 46))
            tk.Label(head, image=self._icon, bg=BG).pack(side="left", padx=(0, 14))
            r.iconphoto(True, skull)
        except Exception:
            pass
        title = tk.Frame(head, bg=BG)
        title.pack(side="left", anchor="w")
        try:
            self._wordmark = tk.PhotoImage(file=os.path.join(BASE, "marathon_wordmark.png"))
            tk.Label(title, image=self._wordmark, bg=BG).pack(anchor="w")
        except Exception:
            tk.Label(title, text="MARATHON", bg=BG, fg=ACCENT,
                     font=("Segoe UI Black", 18, "bold")).pack(anchor="w")
        tk.Label(title, text="KILL RECORDER // TAU CETI IV", bg=BG, fg=MUTED,
                 font=("Consolas", 9)).pack(anchor="w", pady=(3, 0))

        # status + count row
        row = tk.Frame(r, bg=BG)
        row.pack(fill="x", padx=20, pady=8)
        self.status = tk.Label(row, text="STOPPED", bg=BG, fg=MUTED,
                               font=("Consolas", 11, "bold"))
        self.status.pack(side="left")
        self.count_lbl = tk.Label(row, text="0 KILLS", bg=BG, fg=ACCENT,
                                  font=("Consolas", 15, "bold"))
        self.count_lbl.pack(side="right")

        # start/stop
        self.toggle = tk.Button(r, text="START", command=self.toggle_run,
                                bg=ACCENT, fg=BG, activebackground="#bfe038",
                                activeforeground=BG, relief="flat",
                                font=("Segoe UI", 13, "bold"), height=2, cursor="hand2")
        self.toggle.pack(fill="x", padx=20, pady=(6, 4))

        self.dry = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="Test mode (detect only, don't save clips)",
                       variable=self.dry, bg=BG, fg=MUTED, selectcolor=PANEL,
                       activebackground=BG, activeforeground=TEXT,
                       font=("Consolas", 9)).pack(anchor="w", padx=20)

        # quick buttons
        btns = tk.Frame(r, bg=BG)
        btns.pack(fill="x", padx=20, pady=(10, 4))
        for label, cmd in [("Dashboard", self.open_dashboard),
                           ("Settings", self.open_settings),
                           ("How to use", self.open_help),
                           ("Open Folder", self.open_folder)]:
            tk.Button(btns, text=label, command=cmd, bg=PANEL, fg=TEXT,
                      activebackground=LINE, activeforeground=TEXT, relief="flat",
                      font=("Consolas", 9), cursor="hand2").pack(
                          side="left", expand=True, fill="x", padx=3)

        # log
        tk.Label(r, text="ACTIVITY", bg=BG, fg=MUTED,
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=20, pady=(12, 2))
        self.log = scrolledtext.ScrolledText(
            r, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
            font=("Consolas", 9), height=12, wrap="word", borderwidth=0)
        self.log.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.log.configure(state="disabled")
        self._log("Ready. Make sure OBS is open with the Replay Buffer running, "
                  "then press START.")

    # --- actions -------------------------------------------------------------

    def toggle_run(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        self.count = 0
        self.count_lbl.config(text="0 KILLS")
        self.stop_event = threading.Event()
        self._orig_out = sys.stdout
        self._orig_err = sys.stderr
        sys.stdout = sys.stderr = QueueWriter(self.q)
        self.running = True
        self.toggle.config(text="STOP", bg=RED, activebackground="#d63a30", fg="white")
        self._set_status("STARTING...", MUTED)
        self._log("Starting — loading the text reader (first start takes a few seconds)...")

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
        self._set_status("STOPPING...", MUTED)
        self._log("Stopping — writing your session recap...")

    def _finish_stop(self):
        self.running = False
        try:
            sys.stdout = self._orig_out
            sys.stderr = self._orig_err
        except Exception:
            pass
        self.toggle.config(text="START", bg=ACCENT, activebackground="#bfe038", fg=BG)
        self._set_status("STOPPED", MUTED)

    def open_dashboard(self):
        if self.running and self.cfg.get("web_dashboard", True):
            self._open(f"http://localhost:{self.cfg.get('web_port', 8000)}")
            return
        p = os.path.join(BASE, "stats", "dashboard.html")
        if os.path.exists(p):
            self._open(p)
        else:
            self._log("No dashboard yet — press START (live view) or finish a "
                      "session first (stats page).")

    def open_settings(self):
        SettingsWindow(self.root, self.cfg, self._log)

    def open_help(self):
        HelpWindow(self.root)

    def open_folder(self):
        self._open(BASE)

    def recalibrate(self):
        self._log("Launching calibration in a separate window...")
        try:
            subprocess.Popen([sys.executable, os.path.join(BASE, "calibrate.py")],
                             cwd=BASE)
        except Exception as e:
            self._log(f"Could not launch calibrate: {e}")

    # --- helpers -------------------------------------------------------------

    def _open(self, path):
        try:
            os.startfile(path)  # Windows
        except Exception as e:
            self._log(f"Could not open {path}: {e}")

    def _set_status(self, text, color):
        self.status.config(text=text, fg=color)

    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    s = val.rstrip()
                    if s:
                        self._log(s)
                        if "Detecting [" in s:
                            self._set_status("RUNNING", GREEN)
                elif kind == "count":
                    self.count = val
                    self.count_lbl.config(text=f"{val} KILLS")
                    if self.running:
                        self._set_status("RUNNING", GREEN)
                elif kind == "stopped":
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
        btn = tk.Button(row, width=5, relief="flat", borderwidth=0,
                        font=("Consolas", 9, "bold"), cursor="hand2")

        def render(val):
            btn.config(text="ON" if val else "OFF",
                       bg=ACCENT if val else LINE,
                       fg=BG if val else MUTED,
                       activebackground="#bfe038" if val else LINE,
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
        tk.Label(w, text="HOW TO USE", bg=BG, fg=ACCENT,
                 font=("Consolas", 11, "bold")).pack(anchor="w", padx=20, pady=(16, 6))
        t = scrolledtext.ScrolledText(w, bg=PANEL, fg=TEXT, relief="flat",
                                      font=("Consolas", 10), wrap="word", borderwidth=0)
        t.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        t.insert("1.0", HELP_TEXT)
        t.configure(state="disabled")


def main():
    root = tk.Tk()
    ControlPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
