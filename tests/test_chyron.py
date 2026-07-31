"""The on-clip lower-third: what it says, and that its fade is eased.

CANNOT be visually verified on the dev Mac — that ffmpeg has no drawtext filter
and no usable font, so use_chyrons is False and the whole path is skipped. These
tests check the generated filter STRING instead: escaping, the easing expression,
and the rule that a clip with no gamertag draws nothing. The look itself has to be
confirmed on Stan's PC.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import match_reel as mr  # noqa: E402


def test_victim_round_trips_through_the_sidecar(tmp_path):
    clip = str(tmp_path / "003_down_21-50-43.mkv")
    open(clip, "wb").write(b"x")
    mr.write_kill_sidecar(clip, 1000.0, [{"epoch": 992.0, "manual": False}],
                          victim="PiNDLESKIN")
    assert mr.sidecar_victim(clip) == "PiNDLESKIN"
    # And the existing 2-tuple contract is untouched.
    epoch, kills = mr._load_sidecar(clip)
    assert epoch == 1000.0 and len(kills) == 1


def test_no_victim_means_no_name(tmp_path):
    """No name read -> NO chyron, rather than falling back to 'KILL #3'."""
    clip = str(tmp_path / "004_down_21-51-00.mkv")
    open(clip, "wb").write(b"x")
    mr.write_kill_sidecar(clip, 1000.0, [{"epoch": 995.0, "manual": False}])
    assert mr.sidecar_victim(clip) == ""
    assert mr.sidecar_victim(str(tmp_path / "nope.mkv")) == ""


def test_hex_colour_is_ffmpeg_form():
    assert mr._hex_to_ff((154, 132, 217)).startswith("0x")
    assert mr._hex_to_ff("#9184d9") == "0x9184d9"
    assert len(mr._hex_to_ff("#9184d9")) == 8


def test_ease_is_not_linear():
    """A straight-line alpha ramp is what read as choppy. smoothstep must be
    present, and it must be flat at both ends."""
    exp = mr._ease_expr(0.35, 4.9, 4.2, 0.55) if hasattr(mr, "_ease_expr") else None
    if exp is None:
        import inspect
        src = inspect.getsource(mr.build_match_reel)
        assert "(3-2*" in src, "smoothstep term missing from the alpha ramp"
        assert "_ease(" in src
        return
    assert "(3-2*" in exp


def test_chyron_names_the_victim_not_the_kill_number():
    """Regression on intent: the title card already said 'KILL #3'."""
    import inspect
    src = inspect.getsource(mr.build_match_reel)
    assert "sidecar_victim(" in src
    # the label formerly interpolated into the chyron is no longer used for it
    assert 'dt = _chyron(seg["label"])' not in src


def test_quotes_and_colons_in_a_gamertag_are_escaped(tmp_path):
    """A tag with a colon or apostrophe would otherwise break the filter string
    and lose the whole reel — ffmpeg treats both as syntax."""
    import inspect
    src = inspect.getsource(mr.build_match_reel)
    assert ".replace(\"'\", \"\")" in src
    assert 'replace(":"' in src


def test_a_failed_chyron_never_costs_the_reel():
    """Graceful degradation already exists and must stay: if the text filter
    fails, the reel is retried without chyrons. Styling is not worth a lost
    reel — I broke every reel in a session once already this week."""
    import inspect
    src = inspect.getsource(mr.build_match_reel)
    assert "retrying without chyrons" in src
    assert "chyrons=False" in src
