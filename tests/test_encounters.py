"""Kill-feed name extraction — fed with real feed shapes from Stan's frames."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from encounters import _group, extract, should_log  # noqa: E402

TAG = "MRVIZNASTY"


def test_downed_by_real_frame_line():
    # exactly what the 2026-07-16 clip frame showed (arrow + icon OCR to scraps)
    rows = ["XX SANIK XX MRVIZNASTY"]
    assert extract(rows, TAG) == [("killed_by", "XX SANIK XX")]


def test_you_downed_someone():
    rows = ["MRVIZNASTY SOMEDUDE"]
    assert extract(rows, TAG) == [("victim", "SOMEDUDE")]


def test_ocr_slip_in_own_tag_still_matches():
    rows = ["MRV1ZNASTY LOOTGOBLIN"]
    assert extract(rows, TAG) == [("victim", "LOOTGOBLIN")]


def test_ping_line_without_tag_ignored():
    rows = ["SUPREMEPLAYS PINGED TUNNELS"]
    assert extract(rows, TAG) == []


def test_squad_panel_row_rejected():
    # own name plate ("A1 MRVIZNASTY") has no real name on either side
    assert extract(["A1 MRVIZNASTY"], TAG) == []
    assert extract(["MRVIZNASTY"], TAG) == []


def test_distance_marker_and_junk_stripped():
    rows = ["10M MRVIZNASTY RUNNERONE"]
    assert extract(rows, TAG) == [("victim", "RUNNERONE")]


def test_teammate_kill_line_ignored():
    rows = ["SUPREMEPLAYS SOMEDUDE"]
    assert extract(rows, TAG) == []


def test_multiple_rows_multiple_results():
    rows = ["MRVIZNASTY VICTIMONE", "XX SANIK XX MRVIZNASTY"]
    assert extract(rows, TAG) == [("victim", "VICTIMONE"),
                                  ("killed_by", "XX SANIK XX")]


def test_should_log_debounces_repeat_sightings():
    recent = {}
    assert should_log(recent, "victim", "SOMEDUDE", now=100.0)
    assert not should_log(recent, "victim", "SOMEDUDE", now=110.0)   # same line re-read
    assert should_log(recent, "victim", "SOMEDUDE", now=200.0)       # a later, real re-kill
    assert should_log(recent, "killed_by", "SOMEDUDE", now=112.0)    # other direction ok


def test_group_merges_ocr_spellings():
    rows = [("SOMEDUDE", "2026-07-16 20:01:00"),
            ("S0MEDUDE", "2026-07-16 20:44:00"),
            ("OTHERGUY", "2026-07-16 21:00:00")]
    board = _group(rows)
    assert board[0] == ("SOMEDUDE", 2, "2026-07-16 20:44:00")
    assert board[1] == ("OTHERGUY", 1, "2026-07-16 21:00:00")
