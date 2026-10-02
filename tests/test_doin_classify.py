"""DOIN scores as DOWN, and an event is tagged by the phrase that actually matched.

EasyOCR reads the W in DOWN as IN on 67 of 69 real reads ('RUNNER DOIN'). That
misread spent 9 of the 15 points of fuzzy margin, so:
  - a kill traded on the death screen was suppressed: a clean GIVE UP (100)
    out-scored every 'RUNNER DOIN' kill (90.9) in is_suppressed;
  - one more slipped glyph ('RUNNEB DOIN') dropped a real down below 85.
The fix maps the exact token DOIN to DOWN for SCORING only (detector and
is_suppressed). Logged raw/segment text still shows what OCR read.

Firing more DOIN reads without changing classify_event would have minted false
elims (today's classifier falls through to 'kill'), so classify_event now tags
by the best-matching phrase. The letter-fold table and a phrase_windows presence
check in the detector were measured and REJECTED (they made 'RUNNER KILL +XP'
fire as an elim); nothing here may widen firing beyond the exact DOIN token.
"""
import json
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import detector  # noqa: E402
import main  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "corpus_tags_golden.json")


def _shipped_cfg():
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _replay(cfg, frames):
    """[(tag, emitted raw_line)] through the live order:
    is_suppressed -> PopupDetector.process_frame_all -> classify_event."""
    det, _ = main.build_detector(cfg)
    out = []
    for i, lines in enumerate(frames):
        if main.is_suppressed(cfg, lines):
            continue
        for ev in det.process_frame_all(lines, now=1000 + 0.2 * i):
            out.append((main.classify_event(ev.raw_line), ev.raw_line))
    return out


# --- the fold itself ---------------------------------------------------------

def test_fold_is_whole_token_only():
    assert detector.fold_ocr_tokens("runner doin 15 xp") == "runner down 15 xp"
    # DOINED (the exfil row 'RUNNERS DOWNED'), JOIN, COIN are left alone
    for word in ("doined", "join", "coin", "doing"):
        assert detector.fold_ocr_tokens("runner %s 15" % word) == "runner %s 15" % word


# --- is_suppressed -----------------------------------------------------------

def test_doin_kill_survives_death_screen():
    assert not main.is_suppressed({}, ["GIVE UP", "RUNNER DOIN +15 XP"])
    assert not main.is_suppressed(_shipped_cfg(), ["SELF REVIVE", "PRECISION DOIN 5z +25"])
    # and it reaches the detector as a down, counted once
    cfg = _shipped_cfg()
    got = _replay(cfg, [["GIVE UP", "RUNNER DOIN +15 XP"]] * 6 + [[]] * 4)
    assert [t for t, _ in got] == ["down"]


def test_death_screen_alone_is_still_suppressed():
    assert main.is_suppressed({}, ["GIVE UP", "SELF REVIVE"])
    assert main.is_suppressed(_shipped_cfg(), ["SELF REVIVE", "RUNNER DOINED YOU", "+0 XP"])


def test_exfil_doined_row_stays_suppressed():
    cfg = _shipped_cfg()
    assert main.is_suppressed(cfg, ["RUNNER DOINED 3", "INVENTORY VALUE +9,015"])
    assert _replay(cfg, [["RUNNERS DOINED 3", "1NVENTORY VALUF +9,015"]] * 6) == []
    assert _replay(cfg, [["RUNNER DOINED 3", "RUNNER DAMAGE 566",
                          "CREW REVIVES 0", "INVENTORY VALUE +9,015"]] * 6) == []


# --- detector firing ---------------------------------------------------------

def test_doin_fold_keeps_original_text():
    cfg = _shipped_cfg()
    # one more slip on top of DOIN used to fall under the threshold
    got = _replay(cfg, [["RUNNEB DOIN +15 XP"]] * 3)
    assert [t for t, _ in got] == ["down"]
    assert "DOIN" in got[0][1]                 # logged raw is what OCR read
    # stacked: segments are sliced from the ORIGINAL tokens
    got = _replay(cfg, [["RUNNEB DOIN +15 XP", "PRECISION DOIN 5z +25"]] * 4)
    assert [t for t, _ in got] == ["down", "precision"]
    assert all("doin" in raw for _, raw in got)
    assert all("down" not in raw for _, raw in got)


def test_fold_does_not_widen_firing_to_lookalike_words():
    """The rejected letter-fold made these fire as elims/downs."""
    cfg = _shipped_cfg()
    for text in ("RUNNER KILL +10 XP", "RUNNER WILL +10 XP", "RUNNER SHELL +10 XP",
                 "RUNNER JOIN +15 XP", "RUNNER COIN +15 XP"):
        assert _replay(cfg, [[text]] * 4) == [], text


# --- classify_event ----------------------------------------------------------

def test_classify_by_matched_phrase():
    cases = {
        "PRFCISI0N DOIN 5z +25": "precision",     # was 'kill' (a false elim)
        "RUNNER ELN +10 XP": "kill",              # DOWN/ELIM tie -> +10 says elim
        "RUNNER DOIN [A55IST] +15 XP": "assist",  # was 'down' (assist counted as own)
        "RUNNER DOIN [AS SIST] +15 XP": "assist",
        "RUNNER DOIN [ASSIT]+15": "assist",
        "RUNNER ELIM SMSTE +10 XP": "kill",        # junk between phrase and reward
        "RUNNER ELIM [ASSIST F10 Xp": "assist",
        "RUNNER DOIN +15 XP": "down",
        "PRECISION DOWN +25": "precision",
        "FINISHER 5z +50": "finisher",
        "RUNNER ELIM +10 XP": "kill",
    }
    for raw, want in cases.items():
        assert main.classify_event(raw) == want, raw


def test_neighbours_assist_does_not_steal_own_kill():
    """A stacked frame read as one popup: the [ASSIST] belongs to the popup
    below, not to this one."""
    assert main.classify_event("RUNNER DOIN +15 XP RUNNER ELIM [ASSIST] +10 XP") == "down"


def test_manual_press_still_tags_manual_kill():
    assert main.classify_event("MANUAL +1 (added from iPad)") == "manual_kill"
    assert main.classify_event("MANUAL +1 KILL") == "manual_kill"


def test_all_corpus_raws_keep_todays_tags():
    """Golden file: the 104 non-manual popup reads from Stan's real sessions
    (07-16..08-08), captured from the code before this change.

    Every read must emit the same events with the same tags and the same text
    through the live path ('replay'). 19 of them are lower-case segments of a
    stacked frame whose reward sat in the other popup, so alone they never fire;
    their direct tag ('tag') is exactly what the live loop computes on that
    segment, so it is pinned too.

    The only reads whose direct tag is NOT pinned are the two 07-16 reads that
    fused two popups ('RUNNER DOWN ... PRECISION DOIN', 'RUNNER ELIM ...
    FINISHER') under the old one-popup detector. Today's detector always splits
    those (their 'replay' is pinned), so classify_event never sees them whole;
    their direct tag now follows the first phrase instead of the old keyword
    order, which counts the same down/elim either way."""
    with open(GOLDEN, encoding="utf-8") as f:
        doc = json.load(f)
    cfg = doc["config"]
    rows = doc["rows"]
    assert len(rows) == 104
    pinned = 0
    for row in rows:
        got = [list(e) for e in _replay(cfg, [[row["raw"]]] * 3 + [[]] * 4)]
        assert got == row["replay"], row["id"]
        if len(row["replay"]) <= 1:
            pinned += 1
            assert main.classify_event(row["raw"]) == row["tag"], row["id"]
    assert pinned == 102
