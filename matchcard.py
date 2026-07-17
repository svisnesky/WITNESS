"""Generate a shareable 'match card' PNG summarizing a session.

A 1200x630 social-card-sized image with your session stats, Marathon-styled.
Saved to stats/cards/ at the end of each session.
"""

import os

BG = (11, 15, 18)
PANEL = (18, 24, 29)
LINE = (35, 45, 52)
TEXT = (232, 237, 240)
MUTED = (125, 138, 148)
ACCENT = (156, 88, 218)

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

    # big kills number
    kills = str(session.get("total", 0))
    kf = _font("black", 210)
    d.text((pad - 6, 190), kills, font=kf, fill=ACCENT)
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
