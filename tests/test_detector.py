"""Unit tests for PopupDetector — runnable on any machine (no OBS/Windows).

Run:  python -m pytest tests/ -v     (or)     python tests/test_detector.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detector import PopupDetector, _normalize, phrase_matches  # noqa: E402



# --- PopupDetector tests -----------------------------------------------------

def popup(**kw):
    kw.setdefault("require_reward", False)  # these tests exercise frame/dedup logic
    return PopupDetector(
        trigger_phrases=["RUNNER DOWN"],
        phrase_match_threshold=80,
        absence_frames=2,
        **kw,
    )


def test_popup_fires_on_appearance():
    p = popup()
    ev = p.process_frame(["RUNNER DOWN", "+15 XP"], now=1.0)
    assert ev is not None
    assert ev.is_self_kill is True


def test_popup_lingering_counts_once():
    p = popup()
    assert p.process_frame(["RUNNER DOWN +15 XP"], now=1.0) is not None
    # popup still on screen the next frames -> no new events
    assert p.process_frame(["RUNNER DOWN +15 XP"], now=1.2) is None
    assert p.process_frame(["RUNNER DOWN +15 XP"], now=1.4) is None


def test_popup_recounts_after_disappearing():
    p = popup()
    assert p.process_frame(["RUNNER DOWN +15 XP"], now=1.0) is not None
    # popup gone for >= absence_frames frames
    assert p.process_frame([], now=1.2) is None
    assert p.process_frame([], now=1.4) is None
    # a second down later -> fires again
    assert p.process_frame(["RUNNER DOWN +15 XP"], now=3.0) is not None


def test_popup_single_flicker_does_not_recount():
    p = popup()  # absence_frames=2
    assert p.process_frame(["RUNNER DOWN"], now=1.0) is not None
    # one dropped frame (OCR flicker) then popup returns -> still same popup
    assert p.process_frame([], now=1.2) is None
    assert p.process_frame(["RUNNER DOWN"], now=1.4) is None


def test_popup_ignores_unrelated_text():
    p = popup()
    assert p.process_frame(["SOUTH RELAY", "LIGHT ROUNDS 002"], now=1.0) is None
    assert p.process_frame(["029"], now=1.2) is None


def test_popup_ocr_garbled_phrase_still_fires():
    p = popup()
    # 'RUNNER D0WN' with a zero, extra noise
    ev = p.process_frame(["RUNNER D0WN  +15 XP"], now=1.0)
    assert ev is not None


def test_require_reward_blocks_no_reward_text():
    # loading/menu text: phrase-ish but no reward -> never counts
    p = PopupDetector(trigger_phrases=["RUNNER DOWN"], absence_frames=2,
                      confirm_frames=1, require_reward=True)
    assert p.process_frame(["RUNNER DOWN"], now=1.0) is None      # no +XP
    assert p.process_frame(["RUNNER DOWN"], now=1.2) is None


def test_require_reward_allows_real_popup():
    p = PopupDetector(trigger_phrases=["RUNNER DOWN"], absence_frames=2,
                      confirm_frames=1, require_reward=True)
    assert p.process_frame(["RUNNER DOWN +15 XP"], now=1.0) is not None
    # finisher-style "+50" (no XP) also passes
    f = PopupDetector(trigger_phrases=["FINISHER"], absence_frames=2,
                      confirm_frames=1, require_reward=True)
    assert f.process_frame(["FINISHER +50"], now=1.0) is not None


def test_short_scrap_does_not_match_long_phrase():
    # menu/vault false positive: 'fi' must NOT match 'finisher'
    assert phrase_matches("finisher", _normalize("# fi"), 80) is False
    assert phrase_matches("finisher", _normalize("fi"), 80) is False
    assert phrase_matches("precision down", _normalize("pr"), 80) is False


def test_real_phrase_still_matches():
    assert phrase_matches("finisher", _normalize("FINISHER +50"), 80) is True
    assert phrase_matches("runner down", _normalize("RUNNER DOWN +15 XP"), 80) is True
    assert phrase_matches("precision down", _normalize("PRECISI0N DQWN"), 80) is True


def test_menu_scrap_not_a_kill():
    d = PopupDetector(trigger_phrases=["FINISHER", "RUNNER DOWN"], absence_frames=2)
    assert d.process_frame(["# fi", "FI"], now=1.0) is None


def test_confirm_frames_rejects_single_frame_noise():
    d = PopupDetector(trigger_phrases=["RUNNER DOWN"], absence_frames=2, confirm_frames=2)
    # a one-frame fluke that matches then vanishes -> not counted
    assert d.process_frame(["RUNNER DOWN +15 XP"], now=1.0) is None
    assert d.process_frame([], now=1.2) is None
    assert d.process_frame([], now=1.4) is None
    # a real popup persisting 2 frames -> counts once on the 2nd
    assert d.process_frame(["RUNNER DOWN +15 XP"], now=2.0) is None
    assert d.process_frame(["RUNNER DOWN +15 XP"], now=2.2) is not None
    assert d.process_frame(["RUNNER DOWN +15 XP"], now=2.4) is None


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


# --- multi-popup (double kill) counting ---------------------------------------

def _mk(threshold=85, cooldown=0.0):
    from detector import PopupDetector
    return PopupDetector(trigger_phrases=["RUNNER DOWN", "PRECISION DOWN",
                                          "FINISHER", "RUNNER ELIM"],
                         phrase_match_threshold=threshold,
                         absence_frames=3, confirm_frames=1,
                         cooldown_seconds=cooldown)


def test_double_kill_two_stacked_popups_fires_twice():
    d = _mk()
    # frame 1: both popups visible -> first fires immediately
    evs = d.process_frame_all(["RUNNER DOWN +15 XP RUNNER DOWN +15 XP"], now=1.0)
    assert len(evs) == 1
    # frame 2: count of 2 sustained -> the second fires (count_confirm_frames=2)
    evs2 = d.process_frame_all(["RUNNER DOWN +15 XP RUNNER DOWN +15 XP"], now=1.2)
    assert len(evs2) == 1
    # no third event while both linger
    assert d.process_frame_all(["RUNNER DOWN +15 XP RUNNER DOWN +15 XP"], now=1.4) == []


def test_second_popup_joining_later_fires():
    d = _mk()
    assert len(d.process_frame_all(["RUNNER DOWN +15 XP"], now=1.0)) == 1
    assert d.process_frame_all(["RUNNER DOWN +15 XP"], now=1.2) == []
    # a second down stacks while the first popup is still up
    assert d.process_frame_all(["RUNNER DOWN +15 XP RUNNER DOWN +15 XP"], now=3.0) == []
    evs = d.process_frame_all(["RUNNER DOWN +15 XP RUNNER DOWN +15 XP"], now=3.2)
    assert len(evs) == 1     # sustained -> fires


def test_stacked_assist_and_down_classify_separately():
    import main
    d = _mk()
    line = ["RUNNER DOWN [ASSIST] +15 XP RUNNER DOWN +15 XP"]
    evs = d.process_frame_all(line, now=1.0) + d.process_frame_all(line, now=1.2)
    assert len(evs) == 2
    tags = sorted(main.classify_event(e.raw_line) for e in evs)
    assert tags == ["assist", "down"]


def test_single_popup_never_double_fires():
    d = _mk()
    for i, t in enumerate([1.0, 1.2, 1.4, 1.6, 1.8, 2.0]):
        evs = d.process_frame_all(["RUNNER DOWN +15 XP"], now=t)
        assert len(evs) == (1 if i == 0 else 0)


def test_flicker_does_not_double_fire():
    d = _mk(cooldown=2.0)
    assert len(d.process_frame_all(["RUNNER DOWN +15 XP"], now=1.0)) == 1
    assert d.process_frame_all(["RUNNER D§WN garbled"], now=1.2) == []   # bad read
    assert d.process_frame_all(["RUNNER DOWN +15 XP"], now=1.4) == []   # same popup back
    assert d.process_frame_all(["RUNNER DOWN +15 XP"], now=1.6) == []


def test_ai_elim_next_to_down_is_one_kill():
    d = _mk()
    line = ["UESC ELIM +5 XP UESC ELIM +5 XP RUNNER DOWN +15 XP"]
    evs = d.process_frame_all(line, now=1.0) + d.process_frame_all(line, now=1.2) \
        + d.process_frame_all(line, now=1.4)
    assert len(evs) == 1


def test_fused_ocr_tokens_still_fire_once():
    d = _mk()
    # OCR fuses 'RUNNER DOWN' into one token — token windows can't count it,
    # but the whole-blob fallback must still fire the single kill
    evs = d.process_frame_all(["RUNNERDOWN +15 XP"], now=1.0)
    assert len(evs) == 1
