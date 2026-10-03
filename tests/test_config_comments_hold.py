"""config.yaml comments that must hold whichever release packages merge.

config.yaml has one owner, but three of its comments describe code that other
release packages change: the window app's bind host (desktop-launcher), where
Tesseract is looked for (detection-hotpath) and how ffmpeg is found
(reel-pipeline). Every package has to be able to merge on its own, so each
comment says only what is true both before and after those changes land:

- web_dashboard: once the window app binds 127.0.0.1 when web_dashboard is
  false, "console launcher only" is wrong. web_lan: false is the lock that
  works in both launchers on every branch, so that is the one to name.
- ocr_engine: once ocr.py also looks in the standard install folder, PATH is
  no longer required, so the comment can't say "and on PATH".
- ffmpeg_path: once there is a bundled fallback, "this folder / PATH" reads
  as the complete list. What holds everywhere is the order the owner
  decided on: an ffmpeg.exe in the app folder wins.
"""

import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def _config_text() -> str:
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        return f.read()


def _flat(lines) -> str:
    """Comment lines as one lower-case sentence: '#' and wrapping removed."""
    words = []
    for ln in lines:
        words.append(ln.split("#", 1)[1] if "#" in ln else "")
    return re.sub(r"\s+", " ", " ".join(words)).strip().lower()


def _inline_comment(key: str) -> str:
    """`key: value  # ...` plus the indented '#' lines that continue it."""
    lines = _config_text().splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith(key + ":"))
    block = [lines[i]]
    for ln in lines[i + 1:]:
        if ln[:1].isspace() and ln.lstrip().startswith("#"):
            block.append(ln)
        else:
            break
    return _flat(block)


def _comment_above(key: str) -> str:
    lines = _config_text().splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith(key + ":"))
    block = []
    for ln in reversed(lines[:i]):
        if not ln.startswith("#"):
            break
        block.append(ln)
    return _flat(reversed(block))


def _source(name: str) -> str:
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


def test_web_dashboard_comment_names_the_lock_that_works_in_both_launchers():
    text = _inline_comment("web_dashboard")
    assert "console launcher only" not in text
    assert "in either launcher" in text and "web_lan: false" in text
    # ...and both launchers really do pick their bind host from web_lan.
    for launcher in ("main.py", "gui.py"):
        assert 'cfg.get("web_lan", True)' in _source(launcher), launcher


def test_tesseract_comment_does_not_say_path_is_required():
    text = _comment_above("ocr_engine")
    assert "and on path" not in text
    assert "installed" in text and "experimental" in text
    assert "does not make the install smaller" in text


def test_ffmpeg_comment_states_the_order_that_holds_everywhere(tmp_path, monkeypatch):
    text = _inline_comment("ffmpeg_path")
    assert "folder / path" not in text           # read as the complete list
    assert "ffmpeg.exe in this folder always wins" in text
    # find_ffmpeg agrees, even with an ffmpeg on PATH.
    import montage
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr("shutil.which", lambda name, *a, **k: "/usr/bin/" + name)
    assert montage.find_ffmpeg(str(tmp_path), {"ffmpeg_path": ""}) == str(exe)
