"""Config files are read as UTF-8, whatever the Windows code page.

config.yaml is UTF-8 with em dashes from line 1. open() without an encoding
uses the ANSI code page on Windows: Japanese/Chinese/Korean Windows could not
load it at all, and US/EU Windows turned the em dash in youtube_title_template
into 'â€”' in every uploaded title. A macOS test run can't see either, which is
why the first check here is static.
"""
import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _text_mode_opens_without_encoding(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            continue
        mode = "r"
        if len(node.args) >= 2:
            a = node.args[1]
            mode = a.value if isinstance(a, ast.Constant) else "?"
        for kw in node.keywords:
            if kw.arg == "mode":
                mode = kw.value.value if isinstance(kw.value, ast.Constant) else "?"
        if "b" in str(mode):
            continue
        if not any(kw.arg == "encoding" for kw in node.keywords):
            bad.append(f"{os.path.basename(path)}:{node.lineno} open(..., {mode!r})")
    return bad


def test_every_text_mode_open_in_main_names_its_encoding():
    bad = _text_mode_opens_without_encoding(os.path.join(BASE, "main.py"))
    assert not bad, "\n".join(bad)


def test_a_bom_and_em_dashes_round_trip(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_bytes(
        "﻿# WITNESS — configuration\n"
        "game: marathon\n"
        "youtube_title_template: \"WITNESS Auto Reel — {date}\"\n".encode("utf-8"))
    cfg = main.load_config(str(cfg_file))
    assert cfg["youtube_title_template"] == "WITNESS Auto Reel — {date}"
    assert cfg["game"] == "marathon"           # the BOM didn't eat the first key


def test_the_shipped_config_loads_with_its_em_dashes(tmp_path):
    import shutil
    # a copy: load_config may write settings_override.yaml next to the file
    shutil.copy(os.path.join(BASE, "config.yaml"), tmp_path / "config.yaml")
    cfg = main.load_config(str(tmp_path / "config.yaml"))
    assert isinstance(cfg, dict) and cfg


def test_an_ansi_saved_config_falls_back_to_the_locale(tmp_path, monkeypatch):
    import locale
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_bytes("youtube_title_template: \"Reel — {date}\"\n".encode("cp1252"))
    assert main.load_config(str(cfg_file))["youtube_title_template"] == "Reel — {date}"


def test_a_yaml_mistake_names_the_file_and_line(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("game: marathon\npoll_fps: 5\n  bad: [indent\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as ex:
        main.load_config(str(cfg_file))
    msg = str(ex.value)
    assert msg.startswith("config.yaml could not be read (line ")
    assert str(cfg_file) in msg


def test_undecodable_bytes_name_the_file(tmp_path, monkeypatch):
    import locale
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_bytes(b"game: marathon\nname: \x81\x8d\xff\n")
    with pytest.raises(RuntimeError) as ex:
        main.load_config(str(cfg_file))
    assert str(ex.value).startswith("config.yaml could not be read (line 2)")
    assert "UTF-8" in str(ex.value)


def test_an_empty_or_missing_config_is_a_clear_error(tmp_path):
    empty = tmp_path / "config.yaml"
    empty.write_text("# nothing here\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as ex:
        main.load_config(str(empty))
    assert "config.yaml could not be read" in str(ex.value)
    with pytest.raises(RuntimeError) as ex:
        main.load_config(str(tmp_path / "nope" / "config.yaml"))
    assert "config.yaml could not be read" in str(ex.value)


def test_overrides_are_read_and_written_as_utf8(tmp_path, monkeypatch):
    # save_setting_overrides writes next to main.py: point that at tmp_path
    monkeypatch.setattr(main, "__file__", str(tmp_path / "main.py"))
    ov = tmp_path / "settings_override.yaml"
    ov.write_bytes("﻿announcer_player_name: \"Zoë\"\n".encode("utf-8"))
    main.save_setting_overrides({"poll_fps": 3})
    import yaml
    data = yaml.safe_load(ov.read_text(encoding="utf-8"))
    assert data == {"announcer_player_name": "Zoë", "poll_fps": 3}
