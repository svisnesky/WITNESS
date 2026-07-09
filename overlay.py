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
    position = (position or "top-right").lower()
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
    image_path = sys.argv[1]
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 1400
    alpha = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    size = int(sys.argv[4]) if len(sys.argv) > 4 else 120
    position = sys.argv[5] if len(sys.argv) > 5 else "top-right"
    margin = int(sys.argv[6]) if len(sys.argv) > 6 else 40

    path = scaled_image_path(image_path, size)

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

    try:
        img = tk.PhotoImage(file=path)     # Tk 8.6+ reads PNG
    except Exception:
        return

    w, h = img.width(), img.height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x, y = place(position, sw, sh, w, h, margin)
    root.geometry(f"{w}x{h}+{x}+{y}")

    lbl = tk.Label(root, image=img, borderwidth=0, highlightthickness=0, bg=MAGIC)
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
