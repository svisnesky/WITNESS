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

# Why is it black? The program scene's source types (obs-websocket inputKind,
# from OBSClient.get_scene_input_kinds) tell a broken capture apart from two
# setups where black is expected or the fix is different:
#
#   Game Capture / Window Capture hook the game's window, so they are black
#   until Marathon is running. A Game Capture user who presses START first got
#   the full 'OBS IS BLACK - NOT RECORDING' alarm every time.
#
#   A brand-new OBS scene is empty, and the old advice ("click your Display
#   Capture source and re-pick the display") named a source they don't have.
HOOK_KINDS = frozenset({"game_capture", "window_capture"})
# Sources that add sound or decoration but capture nothing of the game.
_AUDIO_HINTS = ("wasapi", "audio", "pulse_", "alsa_", "jack_")
_OVERLAY_PREFIXES = ("text_", "image_source", "color_source", "browser_source",
                     "slideshow")


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
    # Only worth a second request when there is something to explain.
    kinds = _scene_kinds(obs) if res.get("blank") else None
    res["scene_kinds"] = kinds
    res["scene"] = scene_setup(kinds)
    return res


def _scene_kinds(obs):
    """obs.get_scene_input_kinds(), or None. The tests' fake OBS (and any old
    client) only has get_scene_screenshot; that must read as 'unknown', which
    keeps today's warning, never as an empty scene."""
    fn = getattr(obs, "get_scene_input_kinds", None)
    if not callable(fn):
        return None
    try:
        return fn()
    except Exception:
        return None


def _is_capture(kind: str) -> bool:
    k = kind.lower()
    if any(h in k for h in _AUDIO_HINTS):
        return False
    return not k.startswith(_OVERLAY_PREFIXES)


def scene_setup(kinds) -> str:
    """What the program scene's source types say about a black picture.

      'unknown'    kinds is None (couldn't ask): keep today's warning
      'no_capture' nothing in the scene captures the screen or the game (empty,
                   or only audio / text / images): tell them to add a source
      'hook_only'  every capture source is Game or Window Capture: black is
                   NORMAL until the game is running
      'other'      Display Capture, a capture card, a nested scene that couldn't
                   be read, anything else: keep today's warning

    'unknown' and 'other' deliberately behave exactly as before, so a Display
    Capture user (Stan) never gets a softer warning than he has today."""
    if kinds is None:
        return "unknown"
    try:
        capture = [k for k in (str(x) for x in kinds) if _is_capture(k)]
    except TypeError:
        return "unknown"
    if not capture:
        return "no_capture"
    if all(k in HOOK_KINDS for k in capture):
        return "hook_only"
    return "other"


_SCENE_LEAD = {
    "no_capture": (
        "  Your OBS scene has NO capture source, so there is nothing to record.\n"
        "  In OBS: Sources > + > Display Capture (the monitor Marathon is on).\n\n"),
    "hook_only": (
        "  Your scene captures with Game/Window Capture, which stays black until\n"
        "  Marathon is running. If the game IS running, read on.\n\n"),
}


def warning_text(res: dict) -> str:
    """The message Stan should see. Names the actual fix, because 'capture looks
    black' without 'go re-pick your display' is only half a warning.

    When check_obs_capture could read the scene's source types (res['scene']),
    the likeliest cause goes first. The general advice always follows, so a
    wrong guess still leaves the right fix on screen."""
    return (
        "\n" + "!" * 68 + "\n"
        "  OBS IS RECORDING A BLACK SCREEN — your clips will be empty.\n"
        f"  {res.get('reason', '')}\n\n"
        + _SCENE_LEAD.get(res.get("scene"), "") +
        "  No capture source in your scene? Sources > + > Display Capture (or Game Capture).\n"
        "  Using Game Capture? It stays black until the game is running.\n\n"
        "  In OBS, click your Display Capture source and re-pick the display\n"
        "  (Properties -> Display). A driver update, a monitor sleeping, or an\n"
        "  HDR/resolution change makes Windows renumber the display, and the\n"
        "  source keeps pointing at the old id — even with only one monitor.\n\n"
        "  If it stays black, switch that source's Capture Method to the other\n"
        "  option (Windows 10/11 <-> DXGI), or use Game Capture instead.\n"
        + "!" * 68
    )
