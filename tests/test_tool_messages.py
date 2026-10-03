"""What the troubleshooting tools tell people must be true for a stranger.

These tools were written for one user and his friends: "send it to me", "tell
me", a teach wizard promising you could "change it back anytime in the
dashboard settings" (there is no such setting; the switch lives in
settings_override.yaml, and only takes effect after a restart). Static checks,
because the tk wizard and the capture tools can't run on a test machine.
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = ["calibrate.py", "canary.py", "diagnose.py", "benchmark.py", "teach.py",
         "teach_gui.py"]


def _src(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def _strings(name):
    """Every string literal in the file, joined, so wrapped messages read whole."""
    import ast
    out = []
    for node in ast.walk(ast.parse(_src(name))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return " ".join(out)


def test_no_tool_asks_users_to_send_things_to_an_unnamed_me():
    pat = re.compile(r"send (it|them|me)\b|tell me\b|to me\b", re.I)
    bad = [n for n in TOOLS if pat.search(_strings(n))]
    assert not bad, f"personal 'send it to me' wording in: {bad}"


def test_bug_reports_go_to_the_issue_tracker_without_a_scheme():
    for name in ("diagnose.py", "teach.py"):
        s = _src(name)
        assert "github.com/svisnesky/WITNESS/issues" in s, name
        assert not re.search(r"https?://github\.com", s), name


def test_diagnose_warns_the_full_screenshot_may_be_private():
    assert "crop anything private" in _strings("diagnose.py")


def test_teach_gui_done_text_is_true():
    s = _strings("teach_gui.py")
    assert "dashboard settings" not in s
    assert "Restart WITNESS" in s
    assert "settings_override.yaml" in s and "game: marathon" in s
