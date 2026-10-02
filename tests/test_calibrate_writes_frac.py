"""Calibrate must change the region live detection actually uses.

It used to write only the legacy pixel `detect_region`, while the shipped
`detect_region_frac` always wins, and then print "You're calibrated". These
tests load the result the way live mode does and check that the crop equals the
box that was drawn. Now that Calibrate really changes detection, the safety rails
are tested too: the .bak backup, refusing edits it can't prove clean, never
writing settings_override.yaml, and leaving the rest of the file byte-for-byte
alone (em dashes, CRLF, a Notepad BOM, an ANSI-saved file).

Nothing here opens a window: the selection and the screen grab are stubbed.
"""

import codecs
import os
import shutil
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import calibrate  # noqa: E402
import capture  # noqa: E402

SHIPPED = os.path.join(ROOT, "config.yaml")
BOX_4K = {"x": 1001, "y": 1533, "w": 731, "h": 257}


def _app_dir(tmp_path, config_text=None):
    """A throwaway app folder holding a copy of the shipped config.yaml."""
    d = tmp_path / "app"
    d.mkdir()
    if config_text is None:
        shutil.copy(SHIPPED, d / "config.yaml")
    else:
        (d / "config.yaml").write_bytes(config_text.encode("utf-8"))
    return d


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# --- the region that loads is the region that was drawn ---------------------

def test_calibrate_writes_the_frac_that_live_detection_uses(tmp_path):
    d = _app_dir(tmp_path)
    cfg_path = str(d / "config.yaml")
    frac = calibrate.box_to_frac(BOX_4K, 3840, 2160)
    calibrate.write_region(cfg_path, frac, BOX_4K)

    cfg = _load(cfg_path)
    got = capture._resolve(cfg.get("detect_region"), cfg.get("detect_region_frac"),
                           3840, 2160)
    assert got == BOX_4K
    # the legacy pixel block is kept in step for anyone who deletes the frac
    assert cfg["detect_region"] == BOX_4K


def test_main_load_config_picks_up_the_new_region(tmp_path, monkeypatch):
    """Through the real loader, not just yaml.safe_load."""
    import main
    monkeypatch.setattr(main, "save_setting_overrides",
                        lambda *a, **k: pytest.fail("load_config wrote overrides"))
    d = _app_dir(tmp_path)
    cfg_path = str(d / "config.yaml")
    box = {"x": 700, "y": 760, "w": 520, "h": 140}
    calibrate.write_region(cfg_path, calibrate.box_to_frac(box, 1920, 1080), box)
    cfg = main.load_config(cfg_path)
    assert capture._resolve(cfg.get("detect_region"), cfg["detect_region_frac"],
                            1920, 1080) == box


@pytest.mark.parametrize("W,H", [(1280, 720), (1920, 1080), (2560, 1440),
                                 (3440, 1440), (3840, 2160), (5120, 1440)])
def test_fractions_round_trip_to_the_exact_pixel(W, H):
    """4 decimals, and _resolve's int() truncation still lands on the pixel."""
    for v in range(0, W + 1, 7):
        f = calibrate.box_to_frac({"x": v, "y": 0, "w": v, "h": 0}, W, H)
        assert len(repr(f["x"]).split(".")[-1]) <= 4
        assert int(float(f"{f['x']:.4f}") * W) == v, (W, v, f)


def test_backup_is_made_and_the_rest_of_the_file_is_untouched(tmp_path):
    d = _app_dir(tmp_path)
    cfg_path = str(d / "config.yaml")
    original = open(cfg_path, "rb").read()
    calibrate.write_region(cfg_path, calibrate.box_to_frac(BOX_4K, 3840, 2160), BOX_4K)

    bak = d / "config.yaml.bak"
    assert bak.exists() and bak.read_bytes() == original
    new = open(cfg_path, "rb").read().decode("utf-8")
    old = original.decode("utf-8")
    assert new.count("—") == old.count("—") > 0      # em dashes survive
    assert new.splitlines()[0] == old.splitlines()[0]
    a, b = yaml.safe_load(old), yaml.safe_load(new)
    for k in ("detect_region_frac", "detect_region"):
        a.pop(k, None)
        b.pop(k, None)
    assert a == b
    # only the eight region lines differ
    changed = [i for i, (x, y) in enumerate(zip(old.splitlines(), new.splitlines()))
               if x != y]
    assert len(old.splitlines()) == len(new.splitlines())
    assert 1 <= len(changed) <= 8


def test_crlf_and_bom_are_preserved(tmp_path):
    text = open(SHIPPED, encoding="utf-8").read().replace("\n", "\r\n")
    d = _app_dir(tmp_path, config_text="x")
    p = d / "config.yaml"
    p.write_bytes(codecs.BOM_UTF8 + text.encode("utf-8"))
    calibrate.write_region(str(p), calibrate.box_to_frac(BOX_4K, 3840, 2160), BOX_4K)
    raw = p.read_bytes()
    assert raw.startswith(codecs.BOM_UTF8)
    body = raw[3:]
    assert body.count(b"\n") == body.count(b"\r\n"), "a bare LF crept in"
    cfg = yaml.safe_load(body.decode("utf-8"))
    assert capture._resolve(None, cfg["detect_region_frac"], 3840, 2160) == BOX_4K


def test_an_ansi_saved_config_is_not_re_encoded(tmp_path, monkeypatch):
    """Someone typed an accented name in Notepad and saved as ANSI (cp1252).
    Calibrate must read it and write it back in the same code page."""
    monkeypatch.setattr(calibrate.locale, "getpreferredencoding",
                        lambda *a, **k: "cp1252")
    d = tmp_path / "app"
    d.mkdir()
    p = d / "config.yaml"
    p.write_bytes('announcer_player_name: "José"\n'
                  "detect_region_frac:\n  x: 0.375\n  y: 0.71\n  w: 0.19\n  h: 0.12\n"
                  .encode("cp1252"))
    calibrate.write_region(str(p), calibrate.box_to_frac(BOX_4K, 3840, 2160))
    raw = p.read_bytes()
    assert b"Jos\xe9" in raw and b"Jos\xc3\xa9" not in raw


def test_config_without_a_frac_block_gets_one_appended(tmp_path):
    d = _app_dir(tmp_path, config_text='game: "marathon"\ndetect_region:\n'
                                       "  x: 780\n  y: 760\n  w: 460\n  h: 110\n")
    p = str(d / "config.yaml")
    calibrate.write_region(p, calibrate.box_to_frac(BOX_4K, 3840, 2160), BOX_4K)
    cfg = _load(p)
    assert capture._resolve(cfg["detect_region"], cfg["detect_region_frac"],
                            3840, 2160) == BOX_4K


def test_flow_style_frac_is_replaced(tmp_path):
    d = _app_dir(tmp_path, config_text='game: "marathon"\n'
                                       "detect_region_frac: {x: 0.1, y: 0.1, w: 0.1, h: 0.1}\n"
                                       "poll_fps: 5\n")
    p = str(d / "config.yaml")
    calibrate.write_region(p, calibrate.box_to_frac(BOX_4K, 3840, 2160))
    cfg = _load(p)
    assert capture._resolve(None, cfg["detect_region_frac"], 3840, 2160) == BOX_4K
    assert cfg["poll_fps"] == 5


def test_a_layout_it_cannot_edit_cleanly_is_left_alone(tmp_path):
    """A comment inside the block stops the regex mid-block; the leftover y:
    would win as a duplicate key. The parse-back check must catch it."""
    text = ('game: "marathon"\ndetect_region_frac:\n  x: 0.375\n'
            "  # measured on 4K\n  y: 0.71\n  w: 0.19\n  h: 0.12\n")
    d = _app_dir(tmp_path, config_text=text)
    p = d / "config.yaml"
    with pytest.raises(calibrate.CalibrateError):
        calibrate.write_region(str(p), calibrate.box_to_frac(BOX_4K, 3840, 2160))
    assert p.read_text(encoding="utf-8") == text
    assert not (d / "config.yaml.bak").exists()


# --- which file it writes ----------------------------------------------------

def test_marathon_writes_config_yaml(tmp_path):
    d = _app_dir(tmp_path)
    game, target, eff = calibrate.resolve_target(str(d))
    assert game == "marathon"
    assert target == str(d / "config.yaml")
    assert eff.get("capture_source")


def test_a_taught_game_writes_its_profile_never_the_override(tmp_path):
    import teach
    d = _app_dir(tmp_path)
    (d / "games").mkdir()
    prof = d / "games" / "apex.yaml"
    prof.write_text(teach.profile_yaml("Apex", ["KNOCKED DOWN"],
                                       {"x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}, False),
                    encoding="utf-8")
    ov = d / "settings_override.yaml"
    ov.write_text("game: apex\nplay_sound: true\n", encoding="utf-8")
    ov_before = ov.read_bytes()
    cfg_before = (d / "config.yaml").read_bytes()

    game, target, _eff = calibrate.resolve_target(str(d))
    assert (game, target) == ("apex", str(prof))
    calibrate.write_region(target, calibrate.box_to_frac(BOX_4K, 3840, 2160), BOX_4K)

    assert ov.read_bytes() == ov_before
    assert (d / "config.yaml").read_bytes() == cfg_before
    merged = _load(d / "config.yaml")
    merged.update(_load(prof))
    assert capture._resolve(merged.get("detect_region"), merged["detect_region_frac"],
                            3840, 2160) == BOX_4K
    # the profile had no pixel block; calibrate must not invent one there
    assert "detect_region" not in _load(prof)


def test_missing_profile_refuses(tmp_path):
    d = _app_dir(tmp_path)
    (d / "settings_override.yaml").write_text("game: apex\n", encoding="utf-8")
    with pytest.raises(calibrate.CalibrateError):
        calibrate.resolve_target(str(d))


def test_bad_game_name_refuses(tmp_path):
    d = _app_dir(tmp_path)
    (d / "settings_override.yaml").write_text('game: "../config"\n', encoding="utf-8")
    with pytest.raises(calibrate.CalibrateError):
        calibrate.resolve_target(str(d))


def test_a_region_pinned_in_the_override_refuses(tmp_path):
    """The override wins over anything Calibrate writes, so writing would be
    the old no-op all over again."""
    d = _app_dir(tmp_path)
    (d / "settings_override.yaml").write_text(
        "detect_region_frac:\n  x: 0.1\n  y: 0.1\n  w: 0.1\n  h: 0.1\n", encoding="utf-8")
    with pytest.raises(calibrate.CalibrateError):
        calibrate.resolve_target(str(d))


def test_the_source_never_opens_settings_override_for_writing():
    src = open(os.path.join(ROOT, "calibrate.py"), encoding="utf-8").read()
    assert "save_setting_overrides" not in src
    assert "import main" not in src and "from main" not in src


# --- box guards ----------------------------------------------------------------

def test_tiny_and_huge_boxes_are_refused():
    with pytest.raises(calibrate.CalibrateError):
        calibrate.check_box({"x": 10, "y": 10, "w": 30, "h": 8}, 3840, 2160)
    with pytest.raises(calibrate.CalibrateError):
        calibrate.check_box({"x": 0, "y": 0, "w": 3000, "h": 2000}, 3840, 2160)
    calibrate.check_box(BOX_4K, 3840, 2160)          # the real thing passes


def test_scaled_selection_maps_back_to_full_frame_pixels():
    W, H = 3840, 2160
    s = calibrate.fit_scale(W, H, (1600, 900))
    assert s < 1 and W * s <= 1600 and H * s <= 900
    sel = (BOX_4K["x"] * s, BOX_4K["y"] * s, BOX_4K["w"] * s, BOX_4K["h"] * s)
    back = calibrate.unscale_box(sel, s, W, H)
    for k in BOX_4K:
        assert abs(back[k] - BOX_4K[k]) <= 1 / s + 1
    assert calibrate.fit_scale(1280, 720, (1600, 900)) == 1.0


# --- the whole flow, with the window stubbed --------------------------------------

def _stub_flow(monkeypatch, d, box, answer=""):
    monkeypatch.setattr(calibrate, "BASE", str(d))
    monkeypatch.setattr(calibrate, "_select", lambda shot: dict(box))
    monkeypatch.setattr(calibrate, "_ask", lambda prompt: answer)


def test_image_argument_flow_writes_and_prints_the_box(tmp_path, monkeypatch, capsys):
    import cv2
    import numpy as np
    d = _app_dir(tmp_path)
    img = tmp_path / "shot.png"
    cv2.imwrite(str(img), np.zeros((1080, 1920, 3), np.uint8))
    box = {"x": 700, "y": 760, "w": 520, "h": 140}
    _stub_flow(monkeypatch, d, box)
    monkeypatch.setattr(calibrate, "_countdown",
                        lambda *a, **k: pytest.fail("countdown with an image given"))

    assert calibrate.main([str(img)]) == 0
    out = capsys.readouterr().out
    assert "Wrote detect_region_frac" in out
    assert "x=700 y=760 w=520 h=140" in out
    assert (d / "config.yaml.bak").exists()
    cfg = _load(d / "config.yaml")
    assert capture._resolve(None, cfg["detect_region_frac"], 1920, 1080) == box


def test_no_image_counts_down_before_grabbing(tmp_path, monkeypatch):
    import numpy as np
    d = _app_dir(tmp_path)
    order = []
    _stub_flow(monkeypatch, d, {"x": 700, "y": 760, "w": 520, "h": 140})
    monkeypatch.setattr(calibrate, "_countdown", lambda *a, **k: order.append("count"))

    def grab(eff):
        order.append("grab")
        return np.zeros((1080, 1920, 3), np.uint8)
    monkeypatch.setattr(calibrate, "_grab", grab)
    assert calibrate.main([]) == 0
    assert order == ["count", "grab"]


def test_declining_the_confirmation_writes_nothing(tmp_path, monkeypatch):
    import numpy as np
    d = _app_dir(tmp_path)
    before = (d / "config.yaml").read_bytes()
    _stub_flow(monkeypatch, d, {"x": 700, "y": 760, "w": 520, "h": 140}, answer="n")
    monkeypatch.setattr(calibrate, "_countdown", lambda *a, **k: None)
    monkeypatch.setattr(calibrate, "_grab",
                        lambda eff: np.zeros((1080, 1920, 3), np.uint8))
    assert calibrate.main([]) == 1
    assert (d / "config.yaml").read_bytes() == before
    assert not (d / "config.yaml.bak").exists()


def test_a_cropped_image_is_refused(tmp_path, monkeypatch):
    import cv2
    import numpy as np
    d = _app_dir(tmp_path)
    before = (d / "config.yaml").read_bytes()
    img = tmp_path / "diagnose_region.png"
    cv2.imwrite(str(img), np.zeros((172, 486, 3), np.uint8))
    _stub_flow(monkeypatch, d, {"x": 10, "y": 10, "w": 100, "h": 50})
    assert calibrate.main([str(img)]) == 1
    assert (d / "config.yaml").read_bytes() == before
