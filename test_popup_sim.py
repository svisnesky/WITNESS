"""Simulate real Marathon popup text against the PopupDetector config —
verifies real kills fire and NPC/menu popups don't, without burning a match.

Run:  python test_popup_sim.py
"""

import yaml

from detector import PopupDetector, _normalize, phrase_matches


def build_detector(cfg):
    return PopupDetector(
        trigger_phrases=cfg.get("popup_trigger_phrases"),
        phrase_match_threshold=cfg.get("popup_match_threshold", 85),
        absence_frames=cfg.get("popup_absence_frames", 2),
        confirm_frames=cfg.get("popup_confirm_frames", 2),
        require_reward=cfg.get("require_reward", True),
        cooldown_seconds=cfg.get("popup_cooldown_seconds", 0.0),
    )


def is_suppressed(cfg, lines):
    blob = _normalize(" ".join(lines))
    for p in cfg.get("suppress_phrases") or []:
        if phrase_matches(_normalize(p), blob, 80):
            return True
    return False


# (name, ocr_lines, should_fire)
CASES = [
    # --- real kills (must fire) ---
    ("runner down",            ["RUNNER DOWN +15 XP"],            True),
    ("runner down ocr slip",   ["RUNNER D0WN +15 XP"],            True),
    ("runner doin (real ocr)", ["RUNNER DOIN | +15 XP"],          True),
    ("precision down",         ["PRECISION DOWN +25"],            True),
    ("finisher",               ["FINISHER 5Tz +50"],              True),
    ("runner elim",            ["RUNNER ELIM +10 XP"],            True),
    ("elim + finisher combo",  ["RUNNER ELIM +10 XP", "FINISHER 5Tz +50"], True),
    # real combined read from the live log (down+elim+finisher in one frame)
    ("real combined kill",     ["RUNNER DOIN +15 XP RUNNER ELIM +10 XP FINISHER 5 +50"], True),
    # real assist reads from the live log
    ("assist down+precision",  ["HA RUNNER DOIN [ASSIST] +15 XP PRECISION DOIN [ASSIST] T +25"], True),
    ("assist runner elim",     ["RUNNER DOIN [ASSIST] +15 XP"],   True),
    # --- your kill sharing a frame with an NPC popup (must STILL fire) ---
    ("kill next to uesc",      ["RUNNER DOIN [ASSIST] | +15 XP | UESC ELIM | +5 XP | RUNNER ELIM [ASSIST] | +10 XP"], True),
    ("finisher next to uesc",  ["UESC ELIM | +5 XP | RUNNER ELIM [ASSIST] | +10 XP | FINISHER [ASSIST] | 5Tz | +50"], True),
    # --- NPC kills (must NOT fire) ---
    ("uesc cmdr elim",         ["UESC CMDR ELIM +15XP"],          False),
    ("uesc drone elim",        ["UESC DRONE ELIM +5XP"],          False),
    ("uesc drone ocr slip",    ["UE5C DRONE ELIM +5XP"],          False),
    ("uesc defense elim",      ["UESC DEFENSE ELIM | +5 XP"],     False),
    ("uesc elim (real ocr)",   ["UESC ELIM | +5 XP"],             False),
    ("harvesting reward",      ["HARVESTING | NU +5"],            False),
    ("step complete reward",   ["STEP COMPLETE | 6 +25"],         False),
    # --- non-kill screens (must NOT fire) ---
    ("menu text",              ["PLAY  STATS  LOADOUT"],          False),
    ("loading tip",            ["RUNNERS CAN BE REVIVED BY CREW"], False),
    ("death screen",           ["SELF REVIVE", "GIVE UP"],        False),
    ("exfil summary",          ["RUNNERS DOWNED 3"],              False),
    ("exfil summary full",     ["RUNNERS DOWNED 3", "RUNNER DAMAGE 566",
                                "CREW REVIVES 0", "INVENTORY VALUE 9,015"], False),
    ("exfil w/ ocr plus",      ["RUNNERS DOWNED 3", "INVENTORY VALUE +9,015"], False),
    ("empty",                  [],                                 False),
]


def run():
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    failures = []
    for name, lines, should_fire in CASES:
        det = build_detector(cfg)  # fresh detector per case
        fired = False
        # feed the same popup for confirm_frames+1 frames like a lingering popup
        for i in range(cfg.get("popup_confirm_frames", 2) + 1):
            if is_suppressed(cfg, lines):
                break
            if det.process_frame(lines, now=float(i)):
                fired = True
        status = "OK " if fired == should_fire else "FAIL"
        if fired != should_fire:
            failures.append(name)
        print(f"  {status} {name:24s} fired={fired} expected={should_fire}  {lines}")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {', '.join(failures)}")
        raise SystemExit(1)
    print("all popup sim cases pass")


if __name__ == "__main__":
    run()
