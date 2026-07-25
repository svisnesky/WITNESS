"""W/L record from persisted match outcomes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exfil_stats  # noqa: E402


def test_record_summary(tmp_path):
    for o in ["survived", "survived", "died", "survived", "", "died",
              "survived", "survived"]:
        exfil_stats.log_outcome(str(tmp_path), "s1", o)
    rec = exfil_stats.record_summary(str(tmp_path))
    assert rec["exfils"] == 5 and rec["deaths"] == 2 and rec["unknown"] == 1
    assert rec["survival_pct"] == round(100 * 5 / 7)
    assert rec["best_streak"] == 2         # survived,survived then died
    assert rec["current_streak"] == 2      # ends on two survives


def test_record_empty(tmp_path):
    rec = exfil_stats.record_summary(str(tmp_path))
    assert rec["exfils"] == 0 and rec["survival_pct"] is None
