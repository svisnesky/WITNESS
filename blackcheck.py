"""Is OBS actually recording anything, or is it capturing a black screen?

Stan lost an entire night: "i played all of last night and it was all black. I
wouldve had some good clips. What sucks is that i have to check OBS everytime and
i forgot to last night."

Why WITNESS didn't notice on its own: it reads the SCREEN directly
(capture_source: screen), so its own frames looked perfect. Detection worked, kills
counted, clips saved — and every clip was black, because OBS's Display Capture had
lost its target. The app was watching a different window onto the world than the
one it was recording through.

The fix is to look through OBS's eyes: ask it to screenshot its own program scene
over the websocket that's already connected, and check whether that image has any
content. That is the only thing that actually answers "will my clips be black".

Deliberately not a brightness threshold alone. A legitimately dark frame — night
map, loading screen, a fade — is dark but VARIED. A broken capture is flat: every
pixel nearly identical. Variance is what separates them, and using both means a
genuinely dark game scene doesn't cry wolf.
"""

from __future__ import annotations

# A capture is called blank only when it is BOTH very dark and nearly flat.
MAX_MEAN = 12.0        # 0-255; a dark game scene still averages above this
MAX_STDDEV = 3.0       # a real frame has texture even when dark
# A frame can also be flat but NOT dark: a solid colour, or a stuck grey.
FLAT_STDDEV = 1.0


def analyse(img) -> dict:
    """{'mean','stddev','blank','reason'} for a decoded image (numpy array).

    Works on colour or greyscale. Returns blank=False and a reason when the image
    can't be analysed — refusing to guess, because a false "your capture is
    broken" mid-session would be worse than silence."""
    try:
        import numpy as np
    except ImportError:
        return {"mean": None, "stddev": None, "blank": False,
                "reason": "numpy missing — cannot check"}
    if img is None or getattr(img, "size", 0) == 0:
        return {"mean": None, "stddev": None, "blank": False,
                "reason": "no image returned"}
    arr = np.asarray(img, dtype="float32")
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)     # drop alpha, then greyscale
    mean = float(arr.mean())
    sd = float(arr.std())

    if mean <= MAX_MEAN and sd <= MAX_STDDEV:
        return {"mean": mean, "stddev": sd, "blank": True,
                "reason": f"the frame is black (brightness {mean:.1f}, "
                          f"variation {sd:.1f})"}
    if sd <= FLAT_STDDEV:
        return {"mean": mean, "stddev": sd, "blank": True,
                "reason": f"the frame is a flat solid colour (brightness "
                          f"{mean:.1f}, variation {sd:.1f})"}
    return {"mean": mean, "stddev": sd, "blank": False, "reason": ""}


def decode(data: bytes):
    """Decode PNG/JPEG bytes to an array, or None."""
    if not data:
        return None
    try:
        import cv2
        import numpy as np
        return cv2.imdecode(np.frombuffer(data, dtype="uint8"), cv2.IMREAD_COLOR)
    except Exception:
        return None


def decode_datauri(uri: str) -> bytes:
    """obs-websocket returns 'data:image/png;base64,...'. Bytes, or b''."""
    if not uri:
        return b""
    try:
        import base64
        head, _, payload = str(uri).partition(",")
        if "base64" not in head:
            return b""
        return base64.b64decode(payload)
    except Exception:
        return b""


def check_obs_capture(obs) -> dict:
    """Ask OBS what its program scene looks like right now.

    {'ok','blank','reason','mean','stddev'} — ok=False means the check itself
    couldn't run (old OBS, websocket hiccup), which is NOT the same as the
    capture being broken and must never be reported as one."""
    shot = None
    try:
        shot = obs.get_scene_screenshot()
    except Exception as e:
        return {"ok": False, "blank": False, "reason": f"could not ask OBS: {e}",
                "mean": None, "stddev": None}
    if not shot:
        return {"ok": False, "blank": False,
                "reason": "OBS returned no screenshot (needs OBS 28+)",
                "mean": None, "stddev": None}
    img = decode(shot)
    if img is None:
        return {"ok": False, "blank": False,
                "reason": "could not decode the screenshot",
                "mean": None, "stddev": None}
    res = analyse(img)
    res["ok"] = True
    return res


def warning_text(res: dict) -> str:
    """The message Stan should see. Names the actual fix, because 'capture looks
    black' without 'go re-pick your display' is only half a warning."""
    return (
        "\n" + "!" * 68 + "\n"
        "  OBS IS RECORDING A BLACK SCREEN — your clips will be empty.\n"
        f"  {res.get('reason', '')}\n\n"
        "  In OBS, click your Display Capture source and re-pick the display\n"
        "  (Properties -> Display). A driver update, a monitor sleeping, or an\n"
        "  HDR/resolution change makes Windows renumber the display, and the\n"
        "  source keeps pointing at the old id — even with only one monitor.\n\n"
        "  If it stays black, switch that source's Capture Method to the other\n"
        "  option (Windows 10/11 <-> DXGI), or use Game Capture instead.\n"
        + "!" * 68
    )
