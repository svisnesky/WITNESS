"""Auto-sweat: when you're the last one standing, the app goes quiet.

The squad panel (bottom-left) tags downed teammates with a DOWNED label.
When every teammate shows it, you're clutching — all flair (banners, medal
voices, skull, ding) mutes automatically while clips keep recording. The
clutch is CONFIRMED by its resolution, not guessed at: the panel clears
(you revived them) or you exfil alive. If you got kills while solo, THAT's
when the announcer lets loose — celebration lands exactly when the
pressure's off, which is exactly when flair is welcome again.

If you go down instead: quiet exit, no shame.
"""

from __future__ import annotations

from rapidfuzz import fuzz

# The teammate rows of the squad panel (fractions of the frame). Sits below
# the kill feed (~0.65-0.68) and above your own name plate (~0.96).
SQUAD_REGION = {"x": 0.0, "y": 0.70, "w": 0.24, "h": 0.15}


def count_downed(lines) -> int:
    """DOWNED tags in the squad-panel crop — one per teammate row at most."""
    n = 0
    for line in lines:
        for tok in str(line).split():
            t = "".join(c for c in tok.lower() if c.isalpha())
            # leading 'd' required: 'owned'/'ownedq' score 90+ against
            # 'downed' on ratio alone, but real OCR slips keep the D
            if len(t) >= 4 and t[0] == "d" and fuzz.ratio(t, "downed") >= 85:
                n += 1
                break
    return n


def teammates_down(cfg, engine) -> int:
    """One squad-panel crop + OCR -> how many teammates are down right now."""
    from exfil_stats import _crop, _grab_full
    frame = _grab_full(cfg)
    return count_downed(engine.read_lines(_crop(frame, SQUAD_REGION)))
