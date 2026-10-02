"""The troubleshooting tools must not read or write text in the Windows locale.

Windows Python opens text files in the ANSI code page unless told otherwise.
config.yaml is UTF-8 with em dashes, so on Japanese/Chinese/Korean Windows a
plain open() raises UnicodeDecodeError, and on US Windows a read-then-write in
cp1252 can turn an em dash into the single byte 0x97, which the next UTF-8 read
rejects. macOS defaults to UTF-8, so only a static check catches this here.
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TOOLS = ["calibrate.py", "canary.py", "diagnose.py", "benchmark.py", "teach.py",
         "teach_gui.py", "stats.py"]


def _text_opens_without_encoding(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            continue
        mode = None
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = kw.value.value
        if isinstance(mode, str) and "b" in mode:
            continue
        if not any(kw.arg == "encoding" for kw in node.keywords):
            bad.append(f"{os.path.basename(path)}:{node.lineno}")
    return bad


def test_every_text_open_in_the_tools_names_an_encoding():
    bad = []
    for name in TOOLS:
        bad += _text_opens_without_encoding(os.path.join(ROOT, name))
    assert not bad, f"open() without encoding=: {bad}"


def test_stats_csv_round_trips_through_an_old_ansi_file(tmp_path):
    """An existing stats CSV was written in the code page; its rows are dates
    and numbers (pure ASCII), so the UTF-8 reader must take it unchanged."""
    import stats
    sdir = tmp_path / "stats"
    sdir.mkdir()
    (sdir / "marathon_stats.csv").write_bytes(
        b"date,start,duration_min,total,precision,finisher,assist,down,kpm\r\n"
        b"2026-07-29,20:00,60.0,12,1,2,3,4,0.2\r\n")
    html = stats.record_session(str(tmp_path), {
        "date": "2026-10-02", "start": "21:00", "duration_min": 30.0, "total": 5,
        "precision": 0, "finisher": 1, "assist": 0, "down": 4, "kpm": 0.17})
    rows = (sdir / "marathon_stats.csv").read_bytes().splitlines()
    assert len(rows) == 3 and rows[-1].startswith(b"2026-10-02,21:00")
    assert os.path.exists(html)
