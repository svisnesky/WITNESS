"""Transient floating image callout (the Marathon skull) over the game.

Launched as its own process by main.py so it never blocks the capture loop:
    pythonw overlay.py "C:\\path\\marathon_skull.png" 1400 1.0 120 top-right 40
    args: image  duration_ms  alpha  size_px(height)  position  margin_px

The image floats with a transparent background (no visible box). On Windows it
is also click-through and non-activating (WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
so it can NEVER steal focus or eat a click mid-fight. Needs the game in
borderless windowed (same as the screen-capture method).
"""

import os
import sys
import tkinter as tk

# A color that does NOT appear in the skull art (it's yellow + white). Areas of
# this color are made fully see-through, so the skull floats.
MAGIC = "#010101"


def scaled_image_path(image_path, size):
    """Return a path to a version of the image resized to `size` px tall.
    Uses Pillow (present via easyocr); caches per size next to the original.
    Falls back to the original if Pillow isn't available."""
    if size <= 0:
        return image_path
    cache = f"{image_path}.{size}.png"
    if os.path.exists(cache):
        return cache
    try:
        from PIL import Image
        im = Image.open(image_path).convert("RGBA")
        w = max(1, round(im.width * size / im.height))
        im.resize((w, size), Image.LANCZOS).save(cache)
        return cache
    except Exception:
        return image_path


def place(position, sw, sh, w, h, m):
    position = (position or "top-right").lower().strip()
    # custom:fx,fy -> fx,fy are fractions (0-1) of the screen for the image CENTER
    if position.startswith("custom:"):
        try:
            fx, fy = (float(v) for v in position.split(":", 1)[1].split(","))
            return int(sw * fx - w / 2), int(sh * fy - h / 2)
        except Exception:
            pass
    xmid, ymid = (sw - w) // 2, (sh - h) // 2
    coords = {
        "top-left": (m, m),
        "top-right": (sw - w - m, m),
        "top-center": (xmid, m),
        "bottom-left": (m, sh - h - m),
        "bottom-right": (sw - w - m, sh - h - m),
        "bottom-center": (xmid, sh - h - m),
        "center": (xmid, ymid),
        "left": (m, ymid),
        "right": (sw - w - m, ymid),
    }
    return coords.get(position, (sw - w - m, m))


def main():
    if len(sys.argv) < 2:
        return
    # Two modes:
    #   image:  overlay.py <image.png> [duration alpha size position margin]
    #   text:   overlay.py --text "DOUBLE KILL" [duration alpha size position
    #           margin color rise]   (size = font px, rise 0/1)
    text_mode = sys.argv[1] == "--text"
    if text_mode:
        text = sys.argv[2] if len(sys.argv) > 2 else ""
        argv = sys.argv[2:]
    else:
        image_path = sys.argv[1]
        argv = sys.argv[1:]
    duration = int(argv[1]) if len(argv) > 1 else 1400
    alpha = float(argv[2]) if len(argv) > 2 else 1.0
    size = int(argv[3]) if len(argv) > 3 else 120
    position = argv[4] if len(argv) > 4 else "top-right"
    margin = int(argv[5]) if len(argv) > 5 else 40
    color = argv[6] if len(argv) > 6 else "#9c58da"
    rise_on = (argv[7] != "0") if len(argv) > 7 else True

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=MAGIC)
    try:
        root.attributes("-transparentcolor", MAGIC)  # transparent background
    except tk.TclError:
        pass
    try:
        root.attributes("-alpha", alpha)
    except tk.TclError:
        pass

    if text_mode:
        if not text:
            return
        lbl = tk.Label(root, text=text, bg=MAGIC, fg=color,
                       font=("Arial Black", max(10, int(size * 0.75)), "bold"))
        lbl.pack()
        root.update_idletasks()
        w, h = lbl.winfo_reqwidth(), lbl.winfo_reqheight()
    else:
        path = scaled_image_path(image_path, size)
        try:
            img = tk.PhotoImage(file=path)     # Tk 8.6+ reads PNG
        except Exception:
            return
        w, h = img.width(), img.height()
        lbl = tk.Label(root, image=img, borderwidth=0, highlightthickness=0, bg=MAGIC)
        lbl.image = img                   # keep a reference so it isn't GC'd
        lbl.pack()

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x, y = place(position, sw, sh, w, h, margin)
    root.geometry(f"{w}x{h}+{x}+{y}")

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

    # Animate like the game's own reward popups: fade in fast, hold, then
    # drift upward while fading out. A static blink-on/blink-off reads as a
    # glitch next to Marathon's animated UI.
    FADE_IN = 120                     # ms
    FADE_OUT = 450                    # ms
    RISE = max(20, h // 4) if rise_on else 0   # upward drift during fade-out
    TICK = 16                         # ~60fps
    hold = max(0, duration - FADE_IN - FADE_OUT)

    def set_alpha(a):
        try:
            root.attributes("-alpha", max(0.0, min(1.0, a)))
        except tk.TclError:
            pass

    def fade_in(step=0):
        t = step * TICK
        set_alpha(alpha * min(1.0, t / FADE_IN))
        if t < FADE_IN:
            root.after(TICK, fade_in, step + 1)
        else:
            root.after(hold, fade_out, 0)

    def fade_out(step=0):
        t = step * TICK
        p = min(1.0, t / FADE_OUT)
        ease = 1 - (1 - p) * (1 - p)          # ease-out
        set_alpha(alpha * (1.0 - p))
        root.geometry(f"{w}x{h}+{x}+{int(y - RISE * ease)}")
        if t < FADE_OUT:
            root.after(TICK, fade_out, step + 1)
        else:
            root.destroy()

    set_alpha(0.0)
    fade_in()
    root.mainloop()


if __name__ == "__main__":
    main()
