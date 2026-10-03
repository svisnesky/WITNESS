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


def test_update_button_is_not_promised_while_auto_update_is_off():
    # updater.check_and_update returns the "auto-update is off" message
    # unless force=True, and the dashboard button calls it without force.
    # Only Update WITNESS.bat (force) updates while auto_update is false.
    text = _flat(_readme())
    assert ("**Check for update** on the dashboard and `Update WITNESS.bat` "
            "update on demand") not in text
    assert "then only `Update WITNESS.bat` updates" in text


def test_package_downloads_are_not_limited_to_first_time_setup():
    # updater.ensure_requirements pip-installs from the launch path whenever
    # an update changes requirements.txt, and _env.bat reinstalls when its
    # stamp no longer matches. Downloads are not a first-setup-only event.
    text = _flat(_readme())
    assert "First-time setup also downloads packages" not in text
    assert "any update that adds a package" in text


def test_fps_answer_names_every_default_ocr_pass():
    # main._check_clutch OCRs the full-frame squad panel every ~3 s while
    # auto_sweat is on (shipped true), and _scan_feed_names reads the kill
    # feed after each kill. "One small region" alone understates the load.
    text = _flat(_readme())
    start = text.index("**Will it cost me FPS?**")
    answer = text[start:text.index("**", start + len("**Will it cost me FPS?**"))]
    assert "auto_sweat" in answer
    assert "kill-feed" in answer
