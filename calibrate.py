"""Calibrate the detection region: drag a box around where the kill popup shows.

    python calibrate.py                  switch to the game during the countdown
    python calibrate.py screenshot.png   use a saved full-screen screenshot
                                         (or drag one onto "1 - Calibrate.bat")

The box is saved as detect_region_frac: fractions of the frame, so it holds at
any resolution with the same aspect ratio. That is the key live detection
reads; it wins over the legacy pixel detect_region, which is updated too.

Where it is written:
  - Marathon (the default game): config.yaml
  - a taught game (game: <name>): games/<name>.yaml, because that profile
    replaces config.yaml's region while the game is active
It never writes settings_override.yaml. That file is applied last, so a region
there would pin the same box for every game.

Calibrating used to be a no-op that still said "You're calibrated": it wrote
only the pixel block, which detect_region_frac overrides. Now that it really
changes detection, a mis-drawn box can break a region that worked. So before
anything is written it prints the box in pixels and asks, the old file is
copied to <file>.bak, and the new text is parsed back and compared, so a file
it cannot edit cleanly is left untouched.
"""

from __future__ import annotations

import codecs
import locale
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.yaml")

COUNTDOWN_SECONDS = 10
# A full-frame screenshot is needed. A crop (diagnose_region.png, say) would
# turn into a nonsense region, so anything this small is refused.
MIN_IMAGE = (640, 360)
# A box smaller than this is a slipped click, not a popup area. A box bigger
# than this makes every live OCR pass slower and shrinks the popup text under
# the OCR size cap, so kills get missed. The shipped Marathon box is 0.19x0.12.
MIN_BOX_FRAC = (0.03, 0.02)
MAX_BOX_FRAC = (0.60, 0.50)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
_KEYS = ("x", "y", "w", "h")


class CalibrateError(Exception):
    """A reason to stop with nothing written. The message is for the user."""


# --- pure helpers (tested) ----------------------------------------------------

def box_to_frac(box: dict, W: int, H: int) -> dict:
    """Pixel box -> 4-decimal fractions of a WxH frame, chosen so that
    capture._resolve() turns them back into exactly this box at WxH.

    Plain round(x / W, 4) can land a hair under the pixel, and _resolve's
    int() then truncates it one short; so each value takes the nearest
    4-decimal fraction that truncates back to the right pixel."""
    def one(v, size):
        v, size = int(v), int(size)
        k = int(round(v * 10000.0 / size))
        for c in (k, k + 1, k - 1, k + 2, k - 2):
            f = round(c / 10000.0, 4)
            if 0.0 <= f <= 1.0 and int(f * size) == v:
                return f
        return round(v / float(size), 4)
    return {"x": one(box["x"], W), "y": one(box["y"], H),
            "w": one(box["w"], W), "h": one(box["h"], H)}


def _block_re(key: str):
    """Matches one top-level `key:` mapping: the block form this repo writes,
    or a one-line flow form {x: .., y: ..}. Anything else is appended instead,
    and the parse-back check proves the appended copy is the one that wins."""
    k = re.escape(key)
    block = (r"^" + k + r":[ \t]*(?:#[^\r\n]*)?\r?\n"
             r"(?:[ \t]+[A-Za-z_]\w*[ \t]*:[^\r\n]*(?:\r?\n|\Z))+")
    flow = r"^" + k + r":[ \t]*\{[^\r\n]*\}[ \t]*(?:#[^\r\n]*)?(?:\r?\n|\Z)"
    return re.compile(block + "|" + flow, re.M)


def _fmt_block(key: str, d: dict, nl: str, frac: bool) -> str:
    vals = [(f"{float(d[k]):.4f}" if frac else str(int(d[k]))) for k in _KEYS]
    return f"{key}:{nl}" + "".join(f"  {k}: {v}{nl}" for k, v in zip(_KEYS, vals))


def update_region_text(text: str, frac: dict, box: dict = None) -> str:
    """Return `text` with its detect_region_frac block replaced (or appended
    when there is none) and, when `box` is given and the file already has
    one, its legacy pixel detect_region block too. Comments, other keys and
    line endings are left exactly as they were."""
    nl = "\r\n" if "\r\n" in text else "\n"
    new = _fmt_block("detect_region_frac", frac, nl, frac=True)
    pat = _block_re("detect_region_frac")
    if pat.search(text):
        # every copy, so a duplicate key further down can't silently win
        text = pat.sub(lambda m: new, text)
    else:
        text = (text.rstrip("\r\n") + nl + nl
                + "# Detection region, set by 1 - Calibrate.bat" + nl + new)
    if box is not None:
        legacy = _block_re("detect_region")
        if legacy.search(text):
            text = legacy.sub(lambda m: _fmt_block("detect_region", box, nl, False),
                              text)
    return text


def _same_region(got, want, cast) -> bool:
    if not isinstance(got, dict):
        return False
    try:
        return all(cast(got[k]) == cast(want[k]) for k in _KEYS)
    except (KeyError, TypeError, ValueError):
        return False


def check_rewrite(old_text: str, new_text: str, frac: dict, box: dict = None):
    """Parse both versions and prove the edit did what it says: the region
    that loads is the new one, and nothing else in the file changed.
    Raises CalibrateError otherwise (the caller then writes nothing)."""
    import yaml
    try:
        old = yaml.safe_load(old_text)
        new = yaml.safe_load(new_text)
    except yaml.YAMLError as e:
        raise CalibrateError(f"the file has a YAML error, so it was left alone: {e}")
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise CalibrateError("the file isn't a YAML mapping, so it was left alone.")
    if not _same_region(new.get("detect_region_frac"), frac, float):
        raise CalibrateError("couldn't rewrite detect_region_frac cleanly (unusual "
                             "layout), so the file was left alone.")
    if box is not None and "detect_region" in old and "detect_region" in new:
        if not _same_region(new.get("detect_region"), box, int):
            raise CalibrateError("couldn't rewrite the legacy detect_region block "
                                 "cleanly, so the file was left alone.")
    skip = ("detect_region_frac", "detect_region")
    if ({k: v for k, v in old.items() if k not in skip}
            != {k: v for k, v in new.items() if k not in skip}):
        raise CalibrateError("the edit would have changed other settings, so the "
                             "file was left alone.")


def _read_text(path: str):
    """(text, encoding, had_bom). UTF-8 first, keeping a Notepad BOM. A file
    someone re-saved as ANSI is read, and later written back, in the Windows
    code page, so calibrating never re-encodes the rest of it."""
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(codecs.BOM_UTF8)
    if bom:
        raw = raw[len(codecs.BOM_UTF8):]
    try:
        return raw.decode("utf-8"), "utf-8", bom
    except UnicodeDecodeError:
        enc = locale.getpreferredencoding(False) or "utf-8"
        try:
            return raw.decode(enc), enc, bom
        except UnicodeDecodeError:
            raise CalibrateError(f"{os.path.basename(path)} isn't valid UTF-8 or "
                                 f"{enc} text, so it was left alone.")


def write_region(path: str, frac: dict, box: dict = None) -> str:
    """Back up `path` to `path`.bak, then write the new region into it.
    Returns the backup path. Raises CalibrateError (nothing written) when the
    edit can't be proven clean."""
    text, enc, bom = _read_text(path)
    new_text = update_region_text(text, frac, box)
    check_rewrite(text, new_text, frac, box)
    with open(path, "rb") as f:
        original = f.read()
    bak = path + ".bak"
    with open(bak, "wb") as f:
        f.write(original)
    data = new_text.encode(enc)
    if bom:
        data = codecs.BOM_UTF8 + data
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise CalibrateError(f"couldn't write {os.path.basename(path)} ({e}). "
                             "Is it open in another program? Nothing changed.")
    return bak


# --- which file, which settings -------------------------------------------------

def _load_yaml(path: str, missing_ok: bool = False) -> dict:
    import yaml
    if missing_ok and not os.path.exists(path):
        return {}
    text, _enc, _bom = _read_text(path)
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def resolve_target(base: str = BASE):
    """(game, file to write, effective settings). Mirrors main.load_config's
    order (config.yaml, then games/<game>.yaml, then settings_override.yaml)
    but only READS; main.load_config can write settings_override.yaml."""
    cfg_path = os.path.join(base, "config.yaml")
    try:
        cfg = _load_yaml(cfg_path)
    except CalibrateError:
        raise
    except Exception as e:
        raise CalibrateError(f"couldn't read config.yaml: {e}")
    try:
        override = _load_yaml(os.path.join(base, "settings_override.yaml"),
                              missing_ok=True)
    except Exception as e:
        print(f"(could not read settings_override.yaml: {e})")
        override = {}

    if override.get("detect_region_frac"):
        raise CalibrateError(
            "settings_override.yaml sets detect_region_frac, and that file wins "
            "over everything Calibrate can write. Delete the detect_region_frac "
            "lines from settings_override.yaml, then run Calibrate again.")

    game = str(override.get("game") or cfg.get("game") or "marathon").strip().lower()
    eff = dict(cfg)
    if game == "marathon":
        target = cfg_path
    else:
        if not _SLUG.match(game):
            raise CalibrateError(f"game: {game!r} isn't a valid game name.")
        target = os.path.join(base, "games", f"{game}.yaml")
        if not os.path.exists(target):
            raise CalibrateError(
                f"game: {game} is active but games/{game}.yaml doesn't exist, so "
                "WITNESS is using Marathon's settings. Teach the game first, or "
                "set game: marathon (or delete the game: line) in "
                "settings_override.yaml.")
        try:
            eff.update(_load_yaml(target))
        except CalibrateError:
            raise
        except Exception as e:
            raise CalibrateError(f"couldn't read games/{game}.yaml: {e}")
    eff.update(override)
    return game, target, eff


# --- the interactive part (Windows, needs a screen) -----------------------------

def _load_image(path: str):
    import cv2
    import numpy as np
    if not os.path.isfile(path):
        raise CalibrateError(f"no such file: {path}")
    # np.fromfile + imdecode: cv2.imread can't open non-ASCII paths on Windows.
    img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise CalibrateError(f"couldn't read {path} as an image (use a .png or .jpg).")
    return img


def _countdown(game_name: str, seconds: int = COUNTDOWN_SECONDS):
    print(f"\nSwitch to {game_name} now, with a kill popup on screen if you can")
    print("(or just where one appears). Grabbing the screen in:")
    for i in range(seconds, 0, -1):
        print(f"  {i}...", flush=True)
        time.sleep(1)


def _grab(eff: dict):
    from capture import grab_full_screenshot, grab_full_virtualcam
    source = eff.get("capture_source", "screen")
    if source == "obs_virtualcam":
        print("Grabbing a frame from the OBS Virtual Camera...")
        return grab_full_virtualcam(cam_index=eff.get("obs_virtualcam_index", 0))
    print("Grabbing a screenshot of the game monitor...")
    return grab_full_screenshot(monitor_index=eff.get("monitor_index", 1))


def _screen_limit():
    """Largest selection window that fits this screen, in the same DPI terms
    the window will be drawn in. Off Windows: a size that fits 1080p."""
    try:
        import ctypes
        u = ctypes.windll.user32
        sw, sh = int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
        if sw > 0 and sh > 0:
            return int(sw * 0.9), int(sh * 0.8)
    except Exception:
        pass
    return 1600, 900


def fit_scale(W: int, H: int, limit) -> float:
    """Scale (<= 1) that fits a WxH frame inside limit=(w, h)."""
    return min(1.0, limit[0] / float(W), limit[1] / float(H))


def unscale_box(sel, scale: float, W: int, H: int) -> dict:
    """A box drawn on the scaled-down view -> pixels of the full frame."""
    x, y, bw, bh = (float(v) for v in sel)
    x0 = max(0, min(W - 1, int(round(x / scale))))
    y0 = max(0, min(H - 1, int(round(y / scale))))
    w0 = max(0, min(W - x0, int(round(bw / scale))))
    h0 = max(0, min(H - y0, int(round(bh / scale))))
    return {"x": x0, "y": y0, "w": w0, "h": h0}


def check_box(box: dict, W: int, H: int):
    """Refuse boxes that would break detection rather than calibrate it."""
    if box["w"] < MIN_BOX_FRAC[0] * W or box["h"] < MIN_BOX_FRAC[1] * H:
        raise CalibrateError(f"that box is only {box['w']}x{box['h']} px, too small "
                             "to hold a popup. Nothing changed; run it again and "
                             "drag around the whole popup area.")
    if box["w"] > MAX_BOX_FRAC[0] * W or box["h"] > MAX_BOX_FRAC[1] * H:
        raise CalibrateError(f"that box is {box['w']}x{box['h']} px, most of the "
                             "screen. OCR would be slow and miss kills. Nothing "
                             "changed; box just the area where the popup appears.")


def _select(shot):
    """Let the user drag the box. Returns a full-frame pixel box, or None."""
    import cv2
    H, W = shot.shape[:2]
    scale = fit_scale(W, H, _screen_limit())
    view = shot
    if scale < 1.0:
        view = cv2.resize(shot, (max(1, int(W * scale)), max(1, int(H * scale))),
                          interpolation=cv2.INTER_AREA)
    win = "WITNESS calibrate - drag a box, then ENTER (c = cancel)"
    try:
        sel = cv2.selectROI(win, view, showCrosshair=True, fromCenter=False)
        cv2.destroyAllWindows()
    except cv2.error as e:
        raise CalibrateError(
            "OpenCV couldn't open the selection window. This usually means the "
            "'headless' OpenCV build is installed (easyocr pulls it in). Nothing "
            "changed. Fix it with:\n"
            "  .venv\\Scripts\\python -m pip install --force-reinstall --no-deps "
            f"opencv-python\nthen run Calibrate again. ({e})")
    if not sel or sel[2] <= 0 or sel[3] <= 0:
        return None
    return unscale_box(sel, scale, W, H)


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return "n"            # no console to confirm in: don't write


def main(argv=None) -> int:
    import numpy as np
    import capture

    argv = list(sys.argv[1:] if argv is None else argv)
    image = argv[0].strip().strip('"') if argv else ""
    try:
        game, target, eff = resolve_target(BASE)
        game_name = "Marathon" if game == "marathon" else eff.get("game_name", game)
        rel = os.path.relpath(target, BASE)
        what = ("the RUNNER DOWN / +XP popup area" if game == "marathon"
                else f"the area where {game_name}'s kill popup appears")

        if image:
            print(f"Using the screenshot {image}")
            shot = _load_image(image)
        else:
            print("Tip: a screenshot that shows a kill popup is easier. Drag one onto")
            print("     1 - Calibrate.bat instead of racing this countdown.")
            _countdown(game_name)
            try:
                shot = _grab(eff)
            except Exception as e:
                raise CalibrateError(f"couldn't capture the screen: "
                                     f"{type(e).__name__}: {e}. Nothing changed.")
        shot = np.ascontiguousarray(shot)
        H, W = shot.shape[:2]
        if W < MIN_IMAGE[0] or H < MIN_IMAGE[1]:
            raise CalibrateError(f"that image is only {W}x{H}. Calibrate needs a "
                                 "FULL-screen screenshot of the game, not a crop.")

        print(f"\nFrame is {W}x{H}. Drag a box around {what}, with a little margin,")
        print("then press ENTER. (The window may open behind the game: alt-tab to it.)")
        box = _select(shot)
        if box is None:
            print("No region selected. Nothing changed.")
            return 1
        check_box(box, W, H)

        frac = box_to_frac(box, W, H)
        live = capture._resolve(None, frac, W, H)
        old_frac = eff.get("detect_region_frac")
        print(f"\n  Selected box  : x={box['x']} y={box['y']} w={box['w']} h={box['h']}"
              f"  (on this {W}x{H} frame)")
        print(f"  As fractions  : x={frac['x']} y={frac['y']} w={frac['w']} h={frac['h']}")
        print(f"  Live will read: {live}  at {W}x{H}")
        if isinstance(old_frac, dict):
            try:
                old_live = capture._resolve(None, old_frac, W, H)
                print(f"  Old region    : {old_frac}  = {old_live}  at {W}x{H}")
            except Exception:
                print(f"  Old region    : {old_frac}")

        if _ask(f"\nSave this region to {rel}? [Y/n]: ") not in ("", "y", "yes"):
            print("Cancelled. Nothing changed.")
            return 1
        bak = write_region(target, frac, box)
    except CalibrateError as e:
        print(f"\n[X] {e}")
        return 1

    print(f"\nBackup of the old file: {os.path.relpath(bak, BASE)}"
          f"  (copy it back over {rel} to undo)")
    print(f"Wrote detect_region_frac into {rel}: {frac}")
    print(f"At {W}x{H} that is x={live['x']} y={live['y']} w={live['w']} h={live['h']}.")
    print("Restart WITNESS if it's running. To check the box, run Diagnose Capture.bat")
    print("with the game on screen and open diagnose_region.png.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
