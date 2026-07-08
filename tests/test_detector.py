"""Unit tests for KillDetector — runnable on any machine (no OBS/Windows needed).

Run:  python -m pytest tests/ -v     (or)     python tests/test_detector.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector import KillDetector  # noqa: E402


def make(mode="self_or_assist", **kw):
    return KillDetector(
        player_name="Stan",
        name_aliases=["St4n"],
        trigger_keywords=["downed", "eliminated"],
        match_mode=mode,
        name_match_threshold=82,
        dedup_ttl_seconds=8.0,
        **kw,
    )


def test_own_kill_matches():
    d = make()
    ev = d.process_line("Stan downed Ripper", now=1.0)
    assert ev is not None
    assert ev.is_self_kill is True
    assert ev.victim.strip() == "Ripper"


def test_teammate_kill_ignored_in_self_only():
    d = make(mode="self_only")
    assert d.process_line("Ghost downed Ripper", now=1.0) is None


def test_teammate_kill_ignored_in_self_or_assist():
    d = make()  # your name isn't on the line at all
    assert d.process_line("Ghost downed Ripper", now=1.0) is None


def test_your_death_never_counts():
    d = make()
    assert d.process_line("Ripper downed Stan", now=1.0) is None


def test_assist_counts_in_self_or_assist():
    # Hypothetical assist form where your name appears but you aren't the victim.
    d = make()
    ev = d.process_line("Ghost downed Ripper (assist: Stan)", now=1.0)
    assert ev is not None
    assert ev.is_self_kill is False


def test_assist_ignored_in_self_only():
    d = make(mode="self_only")
    assert d.process_line("Ghost downed Ripper (assist: Stan)", now=1.0) is None


def test_ocr_garbled_name_still_matches():
    d = make()
    ev = d.process_line("Stao downed Ripper", now=1.0)  # 'n' misread as 'o'
    assert ev is not None


def test_alias_matches():
    d = make()
    assert d.process_line("St4n downed Ripper", now=1.0) is not None


def test_dedup_same_line_counts_once():
    d = make()
    assert d.process_line("Stan downed Ripper", now=1.0) is not None
    # line still on screen, re-OCR'd repeatedly within TTL -> no new events
    assert d.process_line("Stan downed Ripper", now=2.0) is None
    assert d.process_line("Stan downed Ripper", now=5.0) is None


def test_dedup_expires_after_ttl():
    d = make()
    assert d.process_line("Stan downed Ripper", now=1.0) is not None
    # same victim killed again much later (past TTL) -> counts again
    assert d.process_line("Stan downed Ripper", now=100.0) is not None


def test_non_kill_lines_ignored():
    d = make()
    assert d.process_line("Objective captured", now=1.0) is None
    assert d.process_line("", now=1.0) is None
    assert d.process_line("Stan revived Ghost", now=1.0) is None  # no trigger verb


def test_eliminated_keyword():
    d = make()
    assert d.process_line("Stan eliminated Ripper", now=1.0) is not None


def test_keyword_word_boundary():
    d = make()
    # 'downedtown' should not trigger on 'downed'
    assert d.process_line("Stan downedtown Ripper", now=1.0) is None


def test_process_lines_batch():
    d = make()
    lines = [
        "Ghost downed Ripper",     # not me
        "Stan downed Alpha",       # me
        "Ripper downed Stan",      # my death
        "Stan downed Bravo",       # me
    ]
    events = d.process_lines(lines, now=1.0)
    assert len(events) == 2
    assert {e.victim.strip() for e in events} == {"Alpha", "Bravo"}


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
