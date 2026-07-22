"""Regenerate witness_splash.gif — the 'Broadcast' viewfinder boot animation.

Reproduces the handoff storyboard (design_handoff_witness_ui/Witness Splash):
REC pill + WITNESS CAM label, corner brackets scaling in, a scan-line sweep,
the skull badge blurring in, an accent flash ring, the WITNESS wordmark with
animated letter-spacing, an accent rule, then the 'IT SEES EVERYTHING' tag.

Run:  python tools/gen_splash.py     (writes ../witness_splash.gif)
The launcher (gui.py) plays the frames and fires splash_boom.wav at the frame
in splash_boom_frame.txt.
"""
from __future__ import annotations
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# --- palette (Broadcast) ---
BG_IN = (28, 28, 48)      # #1c1c30 gradient center
BG_MID = (16, 16, 24)     # #101018
BG_OUT = (8, 8, 13)       # #08080d
ACCENT = (145, 132, 217)  # #9184d9
ACCENT_LT = (199, 189, 255)  # #c7bdff
TEXT = (233, 233, 237)
MUTED = (138, 144, 160)   # #8a90a0
DIM = (95, 101, 114)      # #5f6572
REC_RED = (255, 77, 61)   # #ff4d3d
REC_RED_LT = (255, 106, 88)  # #ff6a58

SS = 2                     # supersample factor
W, H = 900, 560            # design canvas
OUTW, OUTH = 600, 373      # final gif size
CW, CH = W * SS, H * SS
FPS_MS = 55                # per-frame duration

FONT_BLACK = ["C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/segoeuib.ttf",
              "/System/Library/Fonts/Supplemental/Arial Black.ttf",
              "/Library/Fonts/Arial Black.ttf"]
FONT_MONO = ["C:/Windows/Fonts/consola.ttf", "/System/Library/Fonts/Menlo.ttc",
             "/System/Library/Fonts/Courier.ttc"]


def _font(cands, size):
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def ease_out_expo(t):
    return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


def phase(now, start, dur):
    """0..1 progress of an element that starts at `start` and lasts `dur`."""
    if now < start:
        return 0.0
    return clamp((now - start) / dur)


# background gradient baked once (expensive per-pixel), reused each frame
def _make_bg():
    bg = Image.new("RGB", (CW, CH))
    px = bg.load()
    cx, cy = CW * 0.5, CH * 0.40
    maxr = (max(CW, CH)) * 0.72
    for y in range(CH):
        for x in range(CW):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / maxr
            if d < 0.55:
                t = d / 0.55
                c = tuple(int(BG_IN[i] + (BG_MID[i] - BG_IN[i]) * t) for i in range(3))
            else:
                t = clamp((d - 0.55) / 0.45)
                c = tuple(int(BG_MID[i] + (BG_OUT[i] - BG_MID[i]) * t) for i in range(3))
            px[x, y] = c
    # faint scan texture (every 3px a 1px lighter line)
    d = ImageDraw.Draw(bg, "RGBA")
    for y in range(0, CH, 3 * SS):
        d.line([(0, y), (CW, y)], fill=(255, 255, 255, 5), width=1)
    return bg


def draw_spaced(draw, cx, y, text, font, fill, tracking, anchor_center=True):
    """Draw text with per-char tracking (px), optionally centered on cx."""
    widths = []
    for ch in text:
        b = draw.textbbox((0, 0), ch, font=font)
        widths.append(b[2] - b[0])
    total = sum(widths) + tracking * (len(text) - 1)
    x = cx - total / 2 if anchor_center else cx
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + tracking


def layer():
    return Image.new("RGBA", (CW, CH), (0, 0, 0, 0))


def render_frame(now, logo):
    img = BG.copy().convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    s = SS
    cx = CW / 2

    # geometry (design coords * SS)
    box_l, box_r, box_t, box_b = 300 * s, 600 * s, 96 * s, 346 * s
    badge_cx, badge_cy = 450 * s, 221 * s

    # --- REC pill (visible from 0; blinks after .3s) ---
    rec_on = True
    if now >= 0.3:
        rec_on = ((now - 0.3) % 1.0) < 0.5
    if rec_on:
        rx, ry = 28 * s, 24 * s
        d.ellipse([rx, ry + 2 * s, rx + 10 * s, ry + 12 * s], fill=REC_RED)
        d.text((rx + 18 * s, ry), "REC 00:00", font=F_MONO_S, fill=REC_RED_LT)
    # WITNESS CAM (static, right)
    camtxt = "WITNESS CAM"
    cw = d.textbbox((0, 0), camtxt, font=F_MONO_XS)
    d.text((CW - 28 * s - (cw[2] - cw[0]), 24 * s), camtxt, font=F_MONO_XS, fill=DIM)

    # --- corner brackets: scale 1.55->1, fade in, from .3s over .85s ---
    bp = phase(now, 0.3, 0.85)
    if bp > 0:
        e = ease_out_cubic(bp)
        scale = 1.55 + (1.0 - 1.55) * e
        al = int(255 * e)
        blen = 46 * s
        lw = max(1, int(2 * s))
        corners = [(box_l, box_t, 1, 1), (box_r, box_t, -1, 1),
                   (box_l, box_b, 1, -1), (box_r, box_b, -1, -1)]
        bl = layer()
        bd = ImageDraw.Draw(bl)
        for (xc, yc, dx, dy) in corners:
            # anchor corner point fixed; arms scale outward
            L = blen * scale
            # horizontal arm
            bd.line([(xc, yc), (xc + dx * L, yc)], fill=ACCENT + (al,), width=lw)
            # vertical arm
            bd.line([(xc, yc), (xc, yc + dy * L)], fill=ACCENT + (al,), width=lw)
        img.alpha_composite(bl)
        d = ImageDraw.Draw(img, "RGBA")

    # --- scan line: .5s over 1.5s, top 6%->92%, fade ends ---
    sp = phase(now, 0.5, 1.5)
    if 0 < sp < 1:
        yy = (0.06 + (0.92 - 0.06) * sp) * CH
        op = 0.9
        if sp < 0.12:
            op = 0.9 * (sp / 0.12)
        elif sp > 0.88:
            op = 0.9 * ((1 - sp) / 0.12)
        sl = layer()
        sd = ImageDraw.Draw(sl)
        # soft glow band
        sd.rectangle([0, yy - 8 * s, CW, yy + 8 * s], fill=ACCENT + (int(60 * op),))
        sd.rectangle([0, yy - 1 * s, CW, yy + 1 * s], fill=ACCENT_LT + (int(230 * op),))
        sl = sl.filter(ImageFilter.GaussianBlur(3 * s))
        img.alpha_composite(sl)
        d = ImageDraw.Draw(img, "RGBA")

    # --- skull badge: .95s over 1s, scale .62->1, blur 6->0, fade ---
    gp = phase(now, 0.95, 1.0)
    if gp > 0:
        e = ease_out_expo(gp)
        scale = 0.62 + (1.0 - 0.62) * e
        blur = 6 * (1 - e)
        al = e
        base_h = 150 * s
        h = int(base_h * scale)
        w = int(logo.width * (h / logo.height))
        lg = logo.resize((max(1, w), max(1, h)), Image.LANCZOS)
        if blur > 0.3:
            lg = lg.filter(ImageFilter.GaussianBlur(blur * s))
        if al < 1:
            a = lg.getchannel("A").point(lambda p: int(p * al))
            lg.putalpha(a)
        img.alpha_composite(lg, (int(badge_cx - w / 2), int(badge_cy - h / 2)))
        d = ImageDraw.Draw(img, "RGBA")

    # --- flash ring: 1.75s over .9s, scale .2->2.6, opacity .85->.22->0 ---
    fp = phase(now, 1.75, 0.9)
    if 0 < fp < 1:
        scale = 0.2 + (2.6 - 0.2) * fp
        if fp < 0.7:
            op = 0.85 + (0.22 - 0.85) * (fp / 0.7)
        else:
            op = 0.22 * (1 - (fp - 0.7) / 0.3)
        r = int(75 * s * scale)
        fl = layer()
        fd = ImageDraw.Draw(fl)
        fd.ellipse([badge_cx - r, badge_cy - r, badge_cx + r, badge_cy + r],
                   outline=ACCENT_LT + (int(255 * clamp(op)),), width=max(1, int(2 * s)))
        fl = fl.filter(ImageFilter.GaussianBlur(2 * s))
        img.alpha_composite(fl)
        d = ImageDraw.Draw(img, "RGBA")

    # --- wordmark: 1.95s over .7s; translateY 12->0, spacing .5em->.22em, blur ---
    wp = phase(now, 1.95, 0.7)
    if wp > 0:
        e = ease_out_cubic(wp)
        al = e
        dy = 12 * s * (1 - e)
        # .5em -> .22em at 50px font ~ 25px -> 11px, * SS
        track = (25 + (11 - 25) * e) * s
        blur = 4 * (1 - e)
        wl = layer()
        wd = ImageDraw.Draw(wl)
        draw_spaced(wd, cx, 360 * s + dy, "WITNESS", F_WM,
                    TEXT + (int(255 * al),), track)
        if blur > 0.3:
            wl = wl.filter(ImageFilter.GaussianBlur(blur * s))
        img.alpha_composite(wl)
        d = ImageDraw.Draw(img, "RGBA")

    # --- accent rule: 2.3s over .55s, scaleX 0->1 ---
    rp = phase(now, 2.3, 0.55)
    if rp > 0:
        e = ease_out_cubic(rp)
        full = 170 * s
        wdt = full * e
        ry = 426 * s
        rl = layer()
        rd = ImageDraw.Draw(rl)
        x0, x1 = cx - wdt / 2, cx + wdt / 2
        # gradient rule: transparent->accent->accent-lt->transparent
        steps = max(2, int(wdt))
        for i in range(steps):
            t = i / (steps - 1)
            if t < 0.3:
                col = tuple(int(ACCENT[j]) for j in range(3))
                a = int(255 * (t / 0.3))
            elif t < 0.7:
                tt = (t - 0.3) / 0.4
                col = tuple(int(ACCENT[j] + (ACCENT_LT[j] - ACCENT[j]) * tt) for j in range(3))
                a = 255
            else:
                col = tuple(int(ACCENT_LT[j]) for j in range(3))
                a = int(255 * (1 - (t - 0.7) / 0.3))
            xx = x0 + wdt * t
            rd.line([(xx, ry), (xx, ry + 3 * s)], fill=col + (a,), width=max(1, s))
        img.alpha_composite(rl)
        d = ImageDraw.Draw(img, "RGBA")

    # --- tagline: 2.55s over .8s, fade up ---
    tp = phase(now, 2.55, 0.8)
    if tp > 0:
        e = ease_out_cubic(tp)
        al = e
        dy = 9 * s * (1 - e)
        tl = layer()
        td = ImageDraw.Draw(tl)
        draw_spaced(td, cx, 448 * s + dy, "IT SEES EVERYTHING", F_TAG,
                    MUTED + (int(255 * al),), 8 * s)
        img.alpha_composite(tl)

    return img.convert("RGB").resize((OUTW, OUTH), Image.LANCZOS)


# fonts (module-level, after SS known)
F_MONO_S = _font(FONT_MONO, 12 * SS)
F_MONO_XS = _font(FONT_MONO, 11 * SS)
F_WM = _font(FONT_BLACK, 50 * SS)
F_TAG = _font(FONT_MONO, 13 * SS)
BG = _make_bg()


def main():
    logo = Image.open(os.path.join(ROOT, "witness_logo.png")).convert("RGBA")
    logo = logo.resize((logo.width * SS // 2, logo.height * SS // 2), Image.LANCZOS)

    frames = []
    total = 3.5   # seconds of animation
    t = 0.0
    while t <= total:
        frames.append(render_frame(t, logo))
        t += FPS_MS / 1000.0
    # hold the final frame briefly
    for _ in range(4):
        frames.append(frames[-1])

    out = os.path.join(ROOT, "witness_splash.gif")
    frames[0].save(out, save_all=True, append_images=frames[1:], loop=0,
                   duration=FPS_MS, disposal=2, optimize=True)
    print(f"wrote {out}: {len(frames)} frames, {OUTW}x{OUTH}")
    # flash-ring frame (for reference); sound is fired earlier at the scan
    print("flash ring at frame ~", int(1.75 / (FPS_MS / 1000.0)))


if __name__ == "__main__":
    main()
