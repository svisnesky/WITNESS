"""WITNESS teach-a-game wizard — the windowed, branded version.

A guided flow (name -> watch -> pick -> review -> done) that learns any
game's kill popup and writes games/<slug>.yaml. Reuses the pure logic in
teach.py; this file is only the front-end. Run standalone:

    pythonw teach_gui.py

The console wizard (python main.py --teach) stays as the fallback.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import teach  # noqa: E402  (pure logic: ranking, phrase derivation, profile)

# WITNESS brand tokens (match gui.py / the dashboard)
# Broadcast palette (matches the web dashboard).
BG = "#0e0e16"
PANEL = "#17161f"
PANEL_2 = "#1c1b27"
ACCENT = "#9184d9"
ACCENT_DIM = "#2b1c40"
TEXT = "#e9e9ed"
MUTED = "#8a90a0"
LINE = "#2a2a38"
RED = "#ff4d3d"

HEAD = ("Segoe UI", 20, "bold")
SUB = ("Segoe UI", 11)
MONO = ("Consolas", 10)
MONO_B = ("Consolas", 10, "bold")
LABEL = ("Consolas", 9)


def _font_ok(family):
    """Segoe UI on Windows, a graceful fallback elsewhere (dev on Mac)."""
    try:
        import tkinter.font as tkfont
        return family in tkfont.families()
    except Exception:
        return True


class TeachWizard:
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.q = queue.Queue()
        self.stop_watch = threading.Event()
        self.candidates = []
        self.selected = set()
        self.name = ""
        self.slug = ""

        if not _font_ok("Segoe UI"):
            global HEAD, SUB
            HEAD = ("Helvetica", 20, "bold")
            SUB = ("Helvetica", 11)

        root.title("WITNESS — Teach a game")
        root.configure(bg=BG)
        root.geometry("560x640")
        root.minsize(520, 600)
        try:
            root.iconphoto(True, tk.PhotoImage(
                file=os.path.join(BASE, "witness_logo.png")))
        except Exception:
            pass
        if sys.platform == "win32":
            try:
                import ctypes
                root.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                v = ctypes.c_int(1)
                for a in (20, 19):
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, a, ctypes.byref(v), ctypes.sizeof(v))
                root.withdraw(); root.deiconify()
            except Exception:
                pass

        self._build_header()
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=26, pady=(4, 22))
        self.step_name()
        root.after(80, self._drain)

    # --- chrome --------------------------------------------------------------

    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=26, pady=(20, 6))
        try:
            self._logo = tk.PhotoImage(
                file=os.path.join(BASE, "witness_logo_small.png"))
            tk.Label(head, image=self._logo, bg=BG).pack(side="left", padx=(0, 12))
        except Exception:
            pass
        col = tk.Frame(head, bg=BG)
        col.pack(side="left", anchor="w")
        try:
            self._wm = tk.PhotoImage(
                file=os.path.join(BASE, "witness_wordmark_small.png"))
            tk.Label(col, image=self._wm, bg=BG).pack(anchor="w")
        except Exception:
            tk.Label(col, text="WITNESS", bg=BG, fg=ACCENT,
                     font=("Segoe UI Black", 16, "bold")).pack(anchor="w")
        tk.Label(col, text="TEACH A GAME", bg=BG, fg=MUTED,
                 font=LABEL).pack(anchor="w", pady=(3, 0))
        self.steps_lbl = tk.Label(head, text="", bg=BG, fg=MUTED, font=LABEL)
        self.steps_lbl.pack(side="right", anchor="e")
        tk.Frame(self.root, bg=LINE, height=1).pack(fill="x")

    def _set_step(self, n):
        self.steps_lbl.config(text=f"STEP {n} / 4")

    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _btn(self, parent, text, cmd, primary=False, side="right"):
        b = tk.Button(parent, text=text, command=cmd, relief="flat",
                      font=("Consolas", 11, "bold"), cursor="hand2",
                      padx=22, pady=11, bd=0,
                      bg=ACCENT if primary else PANEL,
                      fg=BG if primary else TEXT,
                      activebackground="#a89dff" if primary else LINE,
                      activeforeground=BG if primary else TEXT)
        b.pack(side=side, padx=4)
        return b

    def _footer(self):
        f = tk.Frame(self.body, bg=BG)
        f.pack(side="bottom", fill="x", pady=(14, 0))
        return f

    # --- step 1: name --------------------------------------------------------

    def step_name(self):
        self._clear()
        self._set_step(1)
        tk.Label(self.body, text="What game are you teaching?", bg=BG, fg=TEXT,
                 font=HEAD, anchor="w", wraplength=480, justify="left"
                 ).pack(anchor="w", pady=(26, 6))
        tk.Label(self.body,
                 text="WITNESS learns its kill popup by watching you play. "
                      "Best in a bot match or practice range — anywhere you "
                      "can get a kill on demand.",
                 bg=BG, fg=MUTED, font=SUB, anchor="w", wraplength=480,
                 justify="left").pack(anchor="w", pady=(0, 26))

        self.name_var = tk.StringVar()
        entry = tk.Entry(self.body, textvariable=self.name_var, font=("Consolas", 14),
                         bg=PANEL, fg=TEXT, insertbackground=ACCENT, relief="flat",
                         highlightthickness=1, highlightbackground=LINE,
                         highlightcolor=ACCENT)
        entry.pack(fill="x", ipady=10)
        entry.focus_set()
        entry.bind("<Return>", lambda e: self._begin())
        tk.Label(self.body, text="e.g. Arc Raiders", bg=BG, fg=MUTED,
                 font=LABEL, anchor="w").pack(anchor="w", pady=(8, 0))

        f = self._footer()
        self._btn(f, "BEGIN  →", self._begin, primary=True)

    def _begin(self):
        self.name = self.name_var.get().strip()
        if not self.name:
            return
        self.slug = teach.slugify(self.name)
        self.step_ready()

    # --- step 2: ready + watch ----------------------------------------------

    def step_ready(self):
        self._clear()
        self._set_step(2)
        tk.Label(self.body, text=f"Get a kill in {self.name}.", bg=BG, fg=TEXT,
                 font=HEAD, anchor="w", wraplength=480, justify="left"
                 ).pack(anchor="w", pady=(26, 10))
        card = tk.Frame(self.body, bg=PANEL, highlightbackground=LINE,
                        highlightthickness=1)
        card.pack(fill="x", pady=(0, 20))
        for i, line in enumerate([
            "1.  Click START WATCHING below.",
            "2.  Alt-tab into the game.",
            f"3.  Get a kill within {teach.WATCH_SECONDS} seconds.",
            "4.  Come back — WITNESS will show you what it saw.",
        ]):
            tk.Label(card, text=line, bg=PANEL, fg=TEXT, font=MONO,
                     anchor="w", justify="left").pack(
                         anchor="w", padx=18, pady=(14 if i == 0 else 4,
                                                    14 if i == 3 else 4))
        tk.Label(self.body,
                 text="WITNESS reads the whole screen and remembers every "
                      "line of text. The kill popup flashes and vanishes — "
                      "that's how it tells your kill from the HUD.",
                 bg=BG, fg=MUTED, font=SUB, wraplength=480, justify="left",
                 anchor="w").pack(anchor="w")

        f = self._footer()
        self._btn(f, "START WATCHING  ◉", self._start_watch, primary=True)
        self._btn(f, "Back", self.step_name, side="left")

    def _start_watch(self):
        self._clear()
        self._set_step(2)
        self.stop_watch.clear()
        wrap = tk.Frame(self.body, bg=BG)
        wrap.pack(fill="both", expand=True)

        self.rec = tk.Label(wrap, text="●  WATCHING", bg=BG, fg=RED,
                            font=("Consolas", 13, "bold"))
        self.rec.pack(pady=(48, 6))
        self.count_lbl = tk.Label(wrap, text=str(teach.WATCH_SECONDS), bg=BG,
                                  fg=ACCENT, font=("Segoe UI", 72, "bold"))
        self.count_lbl.pack()
        tk.Label(wrap, text="SECONDS LEFT — GO GET A KILL", bg=BG, fg=MUTED,
                 font=LABEL).pack()

        bar_bg = tk.Frame(wrap, bg=LINE, height=4)
        bar_bg.pack(fill="x", padx=40, pady=(28, 8))
        self.bar = tk.Frame(bar_bg, bg=ACCENT, height=4)
        self.bar.place(x=0, y=0, relwidth=1.0, height=4)
        self.read_lbl = tk.Label(wrap, text="reading the screen…", bg=BG,
                                 fg=MUTED, font=MONO)
        self.read_lbl.pack(pady=(6, 0))

        f = self._footer()
        self._btn(f, "I GOT ONE — STOP", self._stop_early, primary=True)

        self._blink(True)
        threading.Thread(target=self._watch_thread, daemon=True).start()

    def _blink(self, on):
        if not self.rec.winfo_exists():
            return
        self.rec.config(fg=RED if on else BG)
        self.root.after(600, self._blink, not on)

    def _stop_early(self):
        self.stop_watch.set()

    def _watch_thread(self):
        try:
            from exfil_stats import _grab_full
            from ocr import OCREngine
            self.q.put(("read", "warming up the text reader…"))
            engine = OCREngine(self.cfg.get("ocr_engine", "easyocr"), upscale=1)
            seen, frames = {}, 0
            t_end = time.monotonic() + teach.WATCH_SECONDS
            while time.monotonic() < t_end and not self.stop_watch.is_set():
                remain = max(0, int(round(t_end - time.monotonic())))
                frame = _grab_full(self.cfg)
                h, w = frame.shape[:2]
                frames += 1
                now = time.monotonic()
                for raw, (x0, y0, x1, y1) in engine.read_boxes(frame, max_dim=0):
                    key = teach._norm(raw)
                    if not key:
                        continue
                    bb = (x0 / w, y0 / h, x1 / w, y1 / h)
                    e = seen.get(key)
                    if e is None:
                        seen[key] = {"raw": raw, "count": 1, "bbox": bb,
                                     "first": now, "last": now}
                    else:
                        e["count"] += 1
                        e["last"] = now
                        e["bbox"] = (min(e["bbox"][0], bb[0]), min(e["bbox"][1], bb[1]),
                                     max(e["bbox"][2], bb[2]), max(e["bbox"][3], bb[3]))
                self.q.put(("tick", remain, len(seen)))
                time.sleep(0.1)
            self.q.put(("watched", seen, frames))
        except Exception as e:
            self.q.put(("error", f"{type(e).__name__}: {e}"))

    # --- step 3: pick --------------------------------------------------------

    def step_pick(self, seen, frames):
        self._clear()
        self._set_step(3)
        # Keep every sighting, not just the ranked shortlist: _confirm() groups
        # variants of the chosen popup across ALL of them to strip victim names.
        self.seen = seen
        self.candidates = teach.rank_candidates(seen, frames)[:12]
        self.selected = set()
        if not self.candidates:
            self._empty_watch()
            return
        tk.Label(self.body, text="Which line was your kill?", bg=BG, fg=TEXT,
                 font=HEAD, anchor="w").pack(anchor="w", pady=(20, 4))
        tk.Label(self.body,
                 text="Tap the line the game showed when you got the kill. "
                      "Most likely candidates are first. Pick more than one "
                      "if several mean a kill.",
                 bg=BG, fg=MUTED, font=SUB, wraplength=480, justify="left",
                 anchor="w").pack(anchor="w", pady=(0, 14))

        scroll = tk.Frame(self.body, bg=BG)
        scroll.pack(fill="both", expand=True)
        self.rows = []
        for i, e in enumerate(self.candidates):
            secs = max(0.0, e["last"] - e["first"])
            row = tk.Frame(scroll, bg=PANEL, highlightbackground=LINE,
                           highlightthickness=1, cursor="hand2")
            row.pack(fill="x", pady=3)
            txt = tk.Label(row, text=e["raw"], bg=PANEL, fg=TEXT, font=MONO_B,
                           anchor="w")
            txt.pack(side="left", padx=(16, 8), pady=11)
            meta = tk.Label(row, text=f"~{secs:.0f}s on screen", bg=PANEL,
                            fg=MUTED, font=LABEL)
            meta.pack(side="right", padx=16)
            for widget in (row, txt, meta):
                widget.bind("<Button-1>", lambda ev, idx=i: self._toggle(idx))
            self.rows.append((row, txt, meta))

        f = self._footer()
        self.confirm_btn = self._btn(f, "CONFIRM  →", self._confirm, primary=True)
        self._btn(f, "Watch again", self.step_ready, side="left")
        self._sync_confirm()

    def _toggle(self, idx):
        if idx in self.selected:
            self.selected.discard(idx)
        else:
            self.selected.add(idx)
        for i, (row, txt, meta) in enumerate(self.rows):
            on = i in self.selected
            row.config(bg=ACCENT_DIM if on else PANEL,
                       highlightbackground=ACCENT if on else LINE)
            for wdg, base in ((txt, TEXT), (meta, MUTED)):
                wdg.config(bg=ACCENT_DIM if on else PANEL)
        self._sync_confirm()

    def _sync_confirm(self):
        on = bool(self.selected)
        self.confirm_btn.config(
            state="normal" if on else "disabled",
            bg=ACCENT if on else PANEL, fg=BG if on else MUTED,
            cursor="hand2" if on else "arrow")

    def _empty_watch(self):
        tk.Label(self.body, text="Nothing popup-like turned up.", bg=BG,
                 fg=TEXT, font=HEAD, anchor="w").pack(anchor="w", pady=(24, 8))
        tk.Label(self.body,
                 text="Usually that means no kill happened in the window, or "
                      "the game is on a different monitor than the one WITNESS "
                      "captures. Try again when you can get a kill on demand.",
                 bg=BG, fg=MUTED, font=SUB, wraplength=480, justify="left",
                 anchor="w").pack(anchor="w")
        f = self._footer()
        self._btn(f, "TRY AGAIN", self.step_ready, primary=True)
        self._btn(f, "Close", self.root.destroy, side="left")

    def _confirm(self):
        chosen = [self.candidates[i] for i in sorted(self.selected)]
        if not chosen:
            return
        self.chosen = chosen
        # Derive each trigger from ALL sightings of the same popup, so a victim
        # name printed in it ("KNOCKED DOWN xXTTVGamerXx") drops out — keeping it
        # would match one player and silently never fire again. Shares teach.py's
        # logic so the GUI and console wizards can't drift apart.
        all_raws = [e["raw"] for e in (self.seen or {}).values()] or \
                   [c["raw"] for c in self.candidates]
        phrases, bboxes, self.name_risk = [], [], []
        for c in chosen:
            variants = teach.variant_group(c["raw"], all_raws)
            ph = teach.common_phrase(variants) or teach.stable_phrase(c["raw"])
            if not ph:
                continue
            phrases.append(ph)
            for v in variants:                    # widen region over variants
                e = (self.seen or {}).get(teach._norm(v))
                if e:
                    bboxes.append(e["bbox"])
            if teach.looks_name_bearing(ph, len(variants)):
                self.name_risk.append(ph)
        self.phrases = sorted(set(phrases))
        self.region = teach.region_around([c["bbox"] for c in chosen] + bboxes)
        self.reward = all(teach.has_reward(c["raw"]) for c in chosen)
        self.step_review()

    # --- step 4: review ------------------------------------------------------

    def step_review(self):
        self._clear()
        self._set_step(4)
        tk.Label(self.body, text="Here's the profile.", bg=BG, fg=TEXT,
                 font=HEAD, anchor="w").pack(anchor="w", pady=(22, 4))
        tk.Label(self.body, text=f"For {self.name}. Edit the trigger text if "
                 "WITNESS read it slightly wrong.", bg=BG, fg=MUTED, font=SUB,
                 wraplength=480, justify="left", anchor="w").pack(
                     anchor="w", pady=(0, 18))

        tk.Label(self.body, text="KILL TRIGGER TEXT", bg=BG, fg=ACCENT,
                 font=LABEL, anchor="w").pack(anchor="w")
        self.phrase_var = tk.StringVar(value=",  ".join(self.phrases))
        pe = tk.Entry(self.body, textvariable=self.phrase_var, font=MONO_B,
                      bg=PANEL, fg=TEXT, insertbackground=ACCENT, relief="flat",
                      highlightthickness=1, highlightbackground=LINE,
                      highlightcolor=ACCENT)
        pe.pack(fill="x", ipady=9, pady=(6, 6))
        # Only one sighting of the popup: we can't tell a victim's name from the
        # words that always appear, and a name here means the profile matches
        # that one player and nothing else. Say so plainly.
        if getattr(self, "name_risk", None):
            tk.Label(self.body,
                     text="Heads up: I only saw that popup once. If it ends in a "
                          "player's name, delete the name and keep only the words "
                          "that appear on EVERY kill "
                          "(e.g. \"KNOCKED DOWN Someguy\" → \"KNOCKED DOWN\").",
                     bg=BG, fg=RED, font=SUB, wraplength=470, justify="left",
                     anchor="w").pack(anchor="w", pady=(0, 12))
        else:
            tk.Frame(self.body, bg=BG, height=6).pack()

        info = tk.Frame(self.body, bg=PANEL, highlightbackground=LINE,
                        highlightthickness=1)
        info.pack(fill="x")
        r = self.region
        rows = [("Reward marker", "yes (+XP style)" if self.reward else "none"),
                ("Popup region", f"x {r['x']}  y {r['y']}  w {r['w']}  h {r['h']}"),
                ("Profile file", f"games/{self.slug}.yaml"),
                ("Extra systems", "kept off until this game's screens are mapped")]
        for i, (k, v) in enumerate(rows):
            line = tk.Frame(info, bg=PANEL)
            line.pack(fill="x", padx=16, pady=(12 if i == 0 else 5,
                                               12 if i == len(rows) - 1 else 5))
            tk.Label(line, text=k, bg=PANEL, fg=MUTED, font=LABEL,
                     width=15, anchor="w").pack(side="left")
            tk.Label(line, text=v, bg=PANEL, fg=TEXT, font=MONO, anchor="w",
                     wraplength=300, justify="left").pack(side="left")

        f = self._footer()
        self._btn(f, "WRITE PROFILE  →", self._write, primary=True)
        self._btn(f, "Back", lambda: self.step_pick(
            {c["raw"]: c for c in self.candidates}, 1) if False else self._back_pick(),
            side="left")

    def _back_pick(self):
        # rebuild the pick step from the candidates we already have
        self._clear()
        self._set_step(3)
        seen = {teach._norm(c["raw"]): c for c in self.candidates}
        self.step_pick(seen, max(1, max((c["count"] for c in self.candidates),
                                        default=1)))

    def _write(self):
        edited = [p.strip().upper() for p in self.phrase_var.get().split(",")
                  if p.strip()]
        phrases = edited or self.phrases
        os.makedirs(os.path.join(BASE, "games"), exist_ok=True)
        path = os.path.join(BASE, "games", f"{self.slug}.yaml")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(teach.profile_yaml(self.name, phrases, self.region,
                                            self.reward))
            self.step_done()
        except Exception as e:
            self.read_lbl = tk.Label(self.body, text=f"Could not write: {e}",
                                     bg=BG, fg=RED, font=MONO)
            self.read_lbl.pack()

    # --- step 5: done --------------------------------------------------------

    def step_done(self):
        self._clear()
        self.steps_lbl.config(text="DONE")
        wrap = tk.Frame(self.body, bg=BG)
        wrap.pack(fill="both", expand=True)
        try:
            self._done_logo = tk.PhotoImage(
                file=os.path.join(BASE, "witness_logo_splash.png"))
            tk.Label(wrap, image=self._done_logo, bg=BG).pack(pady=(40, 14))
        except Exception:
            tk.Label(wrap, text="✓", bg=BG, fg=ACCENT,
                     font=("Segoe UI", 60)).pack(pady=(40, 14))
        tk.Label(wrap, text=f"{self.name} is ready.", bg=BG, fg=TEXT,
                 font=HEAD).pack()
        tk.Label(wrap, text=f"Profile written to games/{self.slug}.yaml",
                 bg=BG, fg=MUTED, font=MONO).pack(pady=(6, 24))

        card = tk.Frame(wrap, bg=PANEL, highlightbackground=ACCENT_DIM,
                        highlightthickness=1)
        card.pack(fill="x")
        tk.Label(card, text="Switch WITNESS to this game now?", bg=PANEL,
                 fg=TEXT, font=("Consolas", 11, "bold")).pack(
                     anchor="w", padx=18, pady=(14, 2))
        tk.Label(card, text="You can change it back anytime in the dashboard "
                 "settings.", bg=PANEL, fg=MUTED, font=LABEL, wraplength=440,
                 justify="left", anchor="w").pack(anchor="w", padx=18,
                                                  pady=(0, 14))

        f = self._footer()
        self._btn(f, f"PLAY {self.name.upper()}", self._switch, primary=True)
        self._btn(f, "Keep current game", self.root.destroy, side="left")

    def _switch(self):
        try:
            import main as app
            app.save_setting_overrides({"game": self.slug})
        except Exception:
            pass
        self.root.destroy()

    # --- event pump ----------------------------------------------------------

    def _drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "tick":
                    _, remain, n = msg
                    if self.count_lbl.winfo_exists():
                        self.count_lbl.config(text=str(remain))
                        self.read_lbl.config(text=f"reading the screen — "
                                             f"{n} line{'s' if n != 1 else ''} so far")
                        frac = remain / max(1, teach.WATCH_SECONDS)
                        self.bar.place_configure(relwidth=frac)
                elif kind == "read":
                    if hasattr(self, "read_lbl") and self.read_lbl.winfo_exists():
                        self.read_lbl.config(text=msg[1])
                elif kind == "watched":
                    self.stop_watch.set()
                    self.step_pick(msg[1], msg[2])
                elif kind == "error":
                    self._error(msg[1])
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _error(self, text):
        self._clear()
        tk.Label(self.body, text="Something went wrong.", bg=BG, fg=RED,
                 font=HEAD, anchor="w").pack(anchor="w", pady=(24, 8))
        tk.Label(self.body, text=text, bg=BG, fg=MUTED, font=MONO,
                 wraplength=480, justify="left", anchor="w").pack(anchor="w")
        f = self._footer()
        self._btn(f, "TRY AGAIN", self.step_ready, primary=True)
        self._btn(f, "Close", self.root.destroy, side="left")


def main():
    import main as app
    cfg = app.load_config()
    root = tk.Tk()
    TeachWizard(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
