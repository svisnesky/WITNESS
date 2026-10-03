"""A sentence saying a shared game profile is harmless must be true in code.

games/<game>.yaml files are meant to be shared ("PR yours"). config.yaml and
games/marathon.yaml tell the user what a profile can change. Saying that a
profile's other keys are ignored is a security claim: ffmpeg_path is executed
on every render, obs.exe_path is launched, youtube_privacy decides who sees an
upload. So this runs a hostile profile through the real main.load_config, and
if any of those keys get through, neither file may claim they are ignored.

The claim was written for an allowlist in load_config (PROFILE_KEYS, planned
in another release package) that no branch had yet. If that allowlist lands,
the claim can come back and this test keeps passing; if it slips, this test
stops the claim from shipping on its own.
"""

import os
import re
import sys

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# Wording that promises load_config filters a profile.
CLAIM = re.compile(r"\bignored\b|\bcan(?:'|no)t change\b|\bcan only set\b",
                   re.IGNORECASE)

HOSTILE_PROFILE = """\
game_name: "Example"
popup_trigger_phrases: ["EXAMPLE DOWN"]
ffmpeg_path: "C:/Users/Public/x/ffmpeg.exe"
youtube_privacy: "public"
obs:
  host: "192.0.2.9"
  exe_path: "C:/Users/Public/x/obs64.exe"
"""

GUARDED = ("ffmpeg_path", "youtube_privacy", "obs")


def _shipped_config() -> dict:
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_with_profile(tmp_path) -> dict:
    """The shipped config.yaml, with settings_override.yaml switching to a
    shared games/example.yaml, through main.load_config."""
    import main
    # Re-dumped as ASCII: load_config's open() has no encoding= on this base,
    # and that is not what this test is about.
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(_shipped_config(), allow_unicode=False), encoding="ascii")
    (tmp_path / "games").mkdir()
    (tmp_path / "games" / "example.yaml").write_text(HOSTILE_PROFILE,
                                                       encoding="ascii")
    (tmp_path / "settings_override.yaml").write_text("game: example\n",
                                                      encoding="ascii")
    return main.load_config(str(tmp_path / "config.yaml"))


def _comment_block_above(text: str, key: str) -> str:
    """The run of comment lines directly above `key:` at column 0."""
    lines = text.splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith(key + ":"))
    block = []
    for ln in reversed(lines[:idx]):
        if not ln.startswith("#"):
            break
        block.append(ln)
    return "\n".join(reversed(block))


def _leading_comments(text: str) -> str:
    out = []
    for ln in text.splitlines():
        if ln.strip() and not ln.startswith("#"):
            break
        out.append(ln)
    return "\n".join(out)


def _profile_docs() -> dict:
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        cfg_text = f.read()
    with open(os.path.join(BASE, "games", "marathon.yaml"), encoding="utf-8") as f:
        marathon_text = f.read()
    return {
        "config.yaml (above game:)": _comment_block_above(cfg_text, "game"),
        "games/marathon.yaml (header)": _leading_comments(marathon_text),
    }


def test_the_profile_is_really_loaded(tmp_path):
    """Guards the test below from passing because no profile was read."""
    cfg = _load_with_profile(tmp_path)
    assert cfg["popup_trigger_phrases"] == ["EXAMPLE DOWN"]
    assert cfg["game_name"] == "Example"


def test_docs_claim_profiles_are_filtered_only_if_load_config_filters(tmp_path):
    shipped = _shipped_config()
    cfg = _load_with_profile(tmp_path)
    leaked = [k for k in GUARDED if cfg.get(k) != shipped.get(k)]
    if not leaked:
        return      # load_config filters profiles: the docs may say so
    for where, text in _profile_docs().items():
        said = CLAIM.findall(text)
        assert not said, (
            f"{where} says a profile's other keys are ignored ({said}), but "
            f"main.load_config applied {leaked} from a shared profile")


def test_the_docs_still_tell_people_to_read_a_shared_profile():
    """Whatever load_config does, the user is told a profile is applied on top
    of config.yaml and what to look for in one from someone else."""
    docs = _profile_docs()
    above_game = docs["config.yaml (above game:)"].lower()
    assert "on top of" in above_game
    assert "from someone else, read it" in above_game
    assert "paths" in above_game and "obs" in above_game
    assert "on top of config.yaml" in docs["games/marathon.yaml (header)"].lower()
