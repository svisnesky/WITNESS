"""Transient on-screen image callout (e.g. the Marathon skull) over the game.

Launched as its own process by main.py so it never blocks the capture loop:
    pythonw overlay.py "C:\\path\\marathon_skull.png" 1400

On Windows it makes itself click-through and non-activating (WS_EX_TRANSPARENT |
WS_EX_NOACTIVATE) so it can NEVER steal focus or eat a click mid-fight. Needs
the game in borderless windowed (same as the screen-capture method).
"""

import sys
import tkinter as tk


def main():
    if len(sys.argv) < 2:
        return
    image_path = sys.argv[1]
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 1400
    alpha = float(sys.argv[3]) if len(sys.argv) > 3 else 0.94

    root = tk.Tk()
    root.overrideredirect(True)          # no title bar / borders
    root.attributes("-topmost", True)
    root.configure(bg="#0d1216")
    try:
        root.attributes("-alpha", alpha)  # slight translucency, HUD feel
    except tk.TclError:
        pass

    try:
        img = tk.PhotoImage(file=image_path)   # Tk 8.6+ reads PNG
    except Exception:
        return

    w, h = img.width(), img.height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x = (sw - w) // 2
    y = int(sh * 0.14)                    # upper-center, clear of the crosshair
    root.geometry(f"{w}x{h}+{x}+{y}")

    lbl = tk.Label(root, image=img, borderwidth=0, highlightthickness=0, bg="#0d1216")
    lbl.image = img                       # keep a reference so it isn't GC'd
    lbl.pack()

    # Click-through + non-activating on Windows.
    root.update_idletasks()
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_NOACTIVATE = 0x08000000
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass  # non-Windows or API hiccup: still shows, just not click-through

    root.after(duration, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
