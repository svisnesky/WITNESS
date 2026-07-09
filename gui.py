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
                           ("Recalibrate", self.recalibrate),
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
        p = os.path.join(BASE, "stats", "dashboard.html")
        if os.path.exists(p):
            self._open(p)
        else:
            self._log("No dashboard yet — finish a session first.")

    def open_settings(self):
        self._open(os.path.join(BASE, "config.yaml"))
        self._log("Opened config.yaml. Restart the recorder after saving changes.")

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


def main():
    root = tk.Tk()
    ControlPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
