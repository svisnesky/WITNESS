"""Loot Goblin decal — crowns the match's top looter on the saved exfil screen.

When WITNESS captures the EXFILTRATED screen, whoever hauled out the most loot
(highest Inventory Value across the squad panels) gets a 'LOOT GOBLIN' decal
stamped over their panel on a decorated copy of the screenshot. Pure image work;
degrades to a no-op if Pillow is missing or no clear winner.
"""

from __future__ import annotations

import os

ACCENT = (145, 132, 217)
ACCENT_DK = (90, 78, 150)
COIN = (255, 205, 74)
COIN_DK = (176, 132, 20)
INK = (18, 18, 26)


def _decal(h: int):
    """An RGBA 'LOOT GOBLIN' badge of height h. Width is measured from the
    content (coin + label) so the text never clips."""
    from PIL import Image, ImageDraw
    from matchcard import _font
    h = max(30, int(h))
    label = "LOOT GOBLIN"
    f = _font("black", int(h * 0.44))
    scratch = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    try:
        tb = scratch.textbbox((0, 0), label, font=f)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        toff = tb[1]
    except Exception:
        tw, th, toff = len(label) * int(h * 0.28), int(h * 0.44), 0
    coin_d = int(h * 0.68)
    pad = int(h * 0.32)
    gap = int(h * 0.22)
    width = pad + coin_d + gap + tw + pad
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, width - 1, h - 1], radius=h // 2,
                        fill=ACCENT + (238,), outline=ACCENT_DK + (255,), width=2)
    # gold coin
    cx, cy = pad + coin_d // 2, h // 2
    r = coin_d // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=COIN, outline=COIN_DK,
              width=max(2, r // 6))
    cf = _font("black", int(r * 1.25))
    try:
        cb = scratch.textbbox((0, 0), "$", font=cf)
        d.text((cx - (cb[2] - cb[0]) / 2 - cb[0], cy - (cb[3] - cb[1]) / 2 - cb[1]),
               "$", font=cf, fill=COIN_DK)
    except Exception:
        pass
    # label, vertically centered
    d.text((pad + coin_d + gap, cy - th / 2 - toff), label, font=f, fill=INK)
    return img


def decorate(png_path: str, squad, out_path: str | None = None):
    """Stamp the LOOT GOBLIN decal over the top looter's panel on a copy of
    png_path. squad: [{position, name, inventory_value, ...}]. Returns the
    written path, or None if there's no clear winner / it can't render."""
    try:
        from PIL import Image
        import exfil_stats
    except Exception:
        return None
    if not squad or not png_path or not os.path.exists(png_path):
        return None
    ranked = [p for p in squad if (p.get("inventory_value") or 0) > 0]
    if not ranked:
        return None
    top = max(ranked, key=lambda p: p.get("inventory_value") or 0)
    # need a clear winner — skip a tie
    vals = sorted((p.get("inventory_value") or 0) for p in ranked)
    if len(vals) >= 2 and vals[-1] == vals[-2]:
        return None
    frac = exfil_stats.SQUAD_PANELS.get(top.get("position", "center"))
    if not frac:
        return None
    try:
        img = Image.open(png_path).convert("RGBA")
        W, H = img.size
        px, pw = int(frac["x"] * W), int(frac["w"] * W)
        py = int(frac["y"] * H)
        h = max(34, int(pw * 0.15))
        decal = _decal(h)
        if decal.width > pw:                 # too wide for the panel — shrink
            decal = _decal(int(h * pw / decal.width))
        dx = px + (pw - decal.width) // 2
        dy = max(6, py - decal.height - int(H * 0.012))   # crown it, just above
        img.alpha_composite(decal, (dx, dy))
        out = out_path or (os.path.splitext(png_path)[0] + "_lootgoblin.png")
        img.convert("RGB").save(out)
        return out
    except Exception:
        return None
