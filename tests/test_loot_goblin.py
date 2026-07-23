"""Loot Goblin decal — crowns the clear top looter, skips ties/empties."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import loot_goblin  # noqa: E402

pytest = None
try:
    import numpy as np
    from PIL import Image
    HAVE_IMG = True
except Exception:
    HAVE_IMG = False


def _img(tmp):
    p = os.path.join(tmp, "exfil.png")
    Image.new("RGB", (1920, 1080), (20, 20, 26)).save(p)
    return p


def test_clear_winner_gets_decorated(tmp_path):
    if not HAVE_IMG:
        return
    src = _img(str(tmp_path))
    squad = [{"position": "left", "name": "A", "inventory_value": 5000},
             {"position": "center", "name": "You", "inventory_value": 26000},
             {"position": "right", "name": "C", "inventory_value": 12000}]
    out = loot_goblin.decorate(src, squad, out_path=os.path.join(str(tmp_path), "g.png"))
    assert out and os.path.exists(out)


def test_tie_is_skipped(tmp_path):
    if not HAVE_IMG:
        return
    src = _img(str(tmp_path))
    squad = [{"position": "left", "name": "A", "inventory_value": 12000},
             {"position": "center", "name": "You", "inventory_value": 12000}]
    assert loot_goblin.decorate(src, squad) is None


def test_no_squad_or_zero_loot_skipped(tmp_path):
    if not HAVE_IMG:
        return
    src = _img(str(tmp_path))
    assert loot_goblin.decorate(src, []) is None
    assert loot_goblin.decorate(src, [{"position": "center", "inventory_value": 0}]) is None
