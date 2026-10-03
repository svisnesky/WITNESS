"""README claims that must match what the code (and the other docs) do.

Each test pins one sentence a review caught promising something the code
does not do. They read README.md as text; nothing here runs the app.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _readme():
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
        return f.read()


def _flat(text):
    return re.sub(r"\s+", " ", text)


def test_check_voice_is_not_sold_as_a_tts_diagnosis():
    # voice_check.py only checks ElevenLabs. It never reads tts_online and
    # never probes edge-tts, so with no key it names Christopher (the neural
    # voice) even when the user is hearing the offline one.
    text = _flat(_readme())
    assert "`Check Voice.bat` says which" not in text
    row = [ln for ln in _readme().splitlines()
           if ln.startswith("| `Check Voice.bat`")]
    assert len(row) == 1
    assert "ElevenLabs" in row[0]
    assert "which announcer voice" not in row[0]


def test_readme_does_not_name_a_security_md_section():
    # The SECURITY.md headings belong to another package and were renamed
    # ("Reporting something" -> "Reporting a vulnerability"); a quoted section
    # name here goes stale silently. Link the file, not a heading.
    text = _flat(_readme())
    assert not re.search(r'"[^"]+" section of \[SECURITY\.md\]', text)
