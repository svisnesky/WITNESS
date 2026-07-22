"""Generate a shareable 'match card' PNG summarizing a session.

A 1200x630 social-card-sized image with your session stats, Marathon-styled.
Saved to stats/cards/ at the end of each session.
"""

import os

# Broadcast palette (matches the web dashboard).
BG = (14, 14, 22)          # #0e0e16
PANEL = (24, 24, 34)       # surface over the ground
LINE = (40, 40, 52)        # subtle border
TEXT = (233, 233, 237)     # #e9e9ed
MUTED = (138, 144, 160)    # #8a90a0
ACCENT = (145, 132, 217)   # #9184d9
ACCENT_LIGHT = (199, 189, 255)  # #c7bdff
ACCENT_DEEP = (122, 109, 199)   # #7a6dc7

FONT_CANDIDATES = {
    "bold": ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
             "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
             "/Library/Fonts/Arial Bold.ttf"],
    "black": ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/ariblk.ttf",
              "/System/Library/Fonts/Supplemental/Arial Black.ttf"],
    "mono": ["C:/Windows/Fonts/consola.ttf",
             "/System/Library/Fonts/Menlo.ttc",
             "/System/Library/Fonts/Courier.ttc"],
}


def _font(kind, size):
    from PIL import ImageFont
    for path in FONT_CANDIDATES.get(kind, []):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def build_card(session, out_path, wordmark_path=None):
    """session: the same dict recorded to stats. Returns out_path or None."""
    from PIL import Image, ImageDraw

    W, H = 1200, 630
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    pad = 60
    # accent rule top
    d.rectangle([0, 0, W, 6], fill=ACCENT)

    # wordmark (optional) top-left, else text
    y = pad
    if wordmark_path and os.path.exists(wordmark_path):
        try:
            wm = Image.open(wordmark_path).convert("RGBA")
            scale = 46 / wm.height
            wm = wm.resize((int(wm.width * scale), 46), Image.LANCZOS)
            img.paste(wm, (pad, y), wm)
        except Exception:
            d.text((pad, y), "WITNESS", font=_font("black", 40), fill=ACCENT)
    else:
        d.text((pad, y), "WITNESS", font=_font("black", 40), fill=ACCENT)

    d.text((pad, y + 60), "SESSION RECAP", font=_font("bold", 22), fill=TEXT)
    sub = "%s   %s   ·   %s min" % (session.get("date", ""), session.get("start", ""),
                                    session.get("duration_min", ""))
    d.text((pad, y + 92), sub, font=_font("mono", 18), fill=MUTED)

    # big kills number — vertical gradient (accent-light -> accent), the
    # dashboard's signature numeral, faked in PIL via a text mask.
    kills = str(session.get("total", 0))
    kf = _font("black", 210)
    _gradient_text(img, (pad - 6, 190), kills, kf, ACCENT_LIGHT, ACCENT)
    d.text((pad + _text_w(d, kills, kf) + 24, 350), "KILLS", font=_font("bold", 30), fill=TEXT)

    # stat tiles on the right
    tiles = [
        ("PRECISION", session.get("precision", 0)),
        ("FINISHERS", session.get("finisher", 0)),
        ("ASSISTS", session.get("assist", 0)),
        ("KILLS / MIN", session.get("kpm", 0)),
    ]
    tx, tw, th, gap = 720, 200, 118, 20
    positions = [(tx, 180), (tx + tw + gap, 180), (tx, 180 + th + gap), (tx + tw + gap, 180 + th + gap)]
    for (label, val), (px, py) in zip(tiles, positions):
        d.rounded_rectangle([px, py, px + tw, py + th], radius=14, fill=PANEL, outline=LINE)
        d.text((px + 18, py + 16), str(val), font=_font("black", 52), fill=TEXT)
        d.text((px + 18, py + 82), label, font=_font("mono", 16), fill=MUTED)

    # footer
    d.line([pad, H - 78, W - pad, H - 78], fill=LINE, width=1)
    d.text((pad, H - 58), "MARATHON // TAU CETI IV", font=_font("mono", 16), fill=MUTED)
    d.text((W - pad - 240, H - 58), "Auto Kill Recorder", font=_font("mono", 16), fill=MUTED)

    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        img.save(out_path)
        return out_path
    except Exception as e:
        print("  [card] could not save: %s" % e)
        return None


def _text_w(draw, text, font):
    try:
        b = draw.textbbox((0, 0), text, font=font)
        return b[2] - b[0]
    except Exception:
        return len(text) * 20


def _gradient_text(img, xy, text, font, top_rgb, bottom_rgb):
    """Draw `text` filled with a vertical gradient from top_rgb to bottom_rgb.
    Builds an alpha mask from the glyphs, then pastes a gradient through it."""
    from PIL import Image, ImageDraw
    x, y = xy
    tmp = Image.new("L", img.size, 0)
    ImageDraw.Draw(tmp).text((x, y), text, font=font, fill=255)
    bbox = tmp.getbbox()
    if not bbox:
        return
    grad = Image.new("RGB", img.size, top_rgb)
    top, bot = bbox[1], bbox[3]
    span = max(1, bot - top)
    px = grad.load()
    # per-row lerp; text is short so this is cheap
    for row in range(top, bot):
        t = (row - top) / span
        c = (int(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t),
             int(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t),
             int(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t))
        for col in range(bbox[0], bbox[2]):
            px[col, row] = c
    img.paste(grad, (0, 0), tmp)
