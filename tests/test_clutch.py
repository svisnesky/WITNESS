"""Auto-sweat: squad-panel reading + the clutch state machine."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clutch  # noqa: E402
import main  # noqa: E402


def test_count_downed_from_panel_lines():
    assert clutch.count_downed(["DOWNED", "B2 SUPREMEPLAYS"]) == 1
    assert clutch.count_downed(["DOWNED B2 SUPREMEPLAYS",
                                "DOWNED C3 LOOTGOBLIN"]) == 2
    assert clutch.count_downed(["B2 SUPREMEPLAYS", "C3 LOOTGOBLIN"]) == 0
    assert clutch.count_downed(["D0WNED SUPREMEPLAYS"]) == 1     # OCR slip
    assert clutch.count_downed([]) == 0


def test_count_all_out_of_fight_states_from_stans_frames():
    # dead teammates (clip frame 1): ELIMINATED tags
    assert clutch.count_downed(["ELIMINATED", "A1 LILCROISSANT#5807",
                                "ELIMINATED", "C3 DBIDS#4403"]) == 2
    # one self-reviving + one dead (clip frame 2)
    assert clutch.count_downed(["REVIVING... A1 TROY#8442",
                                "ELIMINATED C3 TYR#7407"]) == 2
    # mixed with a healthy teammate row
    assert clutch.count_downed(["DOWNED B2 SUPREMEPLAYS",
                                "C3 LOOTGOBLIN"]) == 1


def test_count_downed_ignores_short_scraps_and_other_words():
    assert clutch.count_downed(["DOWN", "OWNED?"]) == 0   # too short / wrong word
    # merged OCR line with two tag rows must count BOTH (per-token counting)
    assert clutch.count_downed(["DOWNED DOWNED"]) == 2
    assert clutch.count_downed(["ELIMINATED DOWNED"]) == 2


def _run_state(downs_sequence, kills_at=(), cfg=None):
    """Drive _check_clutch through a scripted panel history. Returns (session
    state, celebrations)."""
    cfg = cfg or {"auto_sweat": True, "team_wipe_size": 3,
                  "announcer_medals": False, "show_overlays": False}
    s = {"web": None, "cfg": cfg}
    fired = []
    orig_cel = main._clutch_celebrate
    orig_td = clutch.teammates_down
    main._clutch_celebrate = lambda c, st, k: fired.append(k)
    now = [0.0]
    try:
        for i, down in enumerate(downs_sequence):
            clutch.teammates_down = lambda c, e, _d=down: _d
            now[0] += 4.0
            main._check_clutch(cfg, None, s, now[0])
            if i in kills_at:
                s["clutch_kills"] = s.get("clutch_kills", 0) + 1
    finally:
        main._clutch_celebrate = orig_cel
        clutch.teammates_down = orig_td
    return s, fired


def test_clutch_pulled_off_celebrates():
    s, fired = _run_state([0, 2, 2, 0], kills_at=(1, 2))
    assert fired == [2]
    assert not s["clutch"]


def test_clutch_without_kills_ends_quietly():
    s, fired = _run_state([0, 2, 0])
    assert fired == []
    assert not s["clutch"]


def test_one_teammate_down_is_not_clutch_in_trios():
    s, fired = _run_state([0, 1, 1, 0])
    assert not s.get("clutch") and fired == []


def test_auto_sweat_off_never_triggers():
    s, fired = _run_state([0, 2, 2], cfg={"auto_sweat": False,
                                          "team_wipe_size": 3})
    assert not s.get("clutch") and fired == []
