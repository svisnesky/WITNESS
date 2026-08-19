"""Exporting a hand-picked reel. The selection arrives over HTTP from a page
anyone on the LAN can reach, so path handling is a security boundary, not a
convenience.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import export_reel as er  # noqa: E402


def _sess(tmp_path, names=("001_down_20-07-34.mkv", "002_down+precision_20-30-32.mkv")):
    d = tmp_path / "sess"
    d.mkdir()
    for n in names:
        (d / n).write_bytes(b"x")
    return str(d)


def test_selection_order_is_preserved():
    """The whole point: the user's order, not chronological."""
    pass  # covered by test_resolve_keeps_the_given_order


def test_resolve_keeps_the_given_order(tmp_path):
    d = _sess(tmp_path)
    sel = [{"rel": "002_down+precision_20-30-32.mkv"},
           {"rel": "001_down_20-07-34.mkv"}]
    got = [os.path.basename(c["path"]) for c in er.resolve(d, sel)]
    assert got == ["002_down+precision_20-30-32.mkv", "001_down_20-07-34.mkv"]


def test_path_traversal_is_refused(tmp_path):
    """A crafted rel must not reach outside the session folder."""
    d = _sess(tmp_path)
    (tmp_path / "secret.mkv").write_bytes(b"x")
    for bad in ("../secret.mkv", "..\\secret.mkv", "clips/../../secret.mkv",
                "/etc/hosts", ""):
        assert er.resolve(d, [{"rel": bad}]) == [], bad


def test_missing_files_are_dropped_not_fatal(tmp_path):
    d = _sess(tmp_path)
    out = er.resolve(d, [{"rel": "nope.mkv"},
                         {"rel": "001_down_20-07-34.mkv"}])
    assert len(out) == 1


def test_trim_points_pass_through_as_numbers(tmp_path):
    d = _sess(tmp_path)
    c = er.resolve(d, [{"rel": "001_down_20-07-34.mkv", "ss": "4.5", "to": "20"}])[0]
    assert c["ss"] == 4.5 and c["to"] == 20.0


def test_junk_trim_values_are_ignored_not_crashed(tmp_path):
    d = _sess(tmp_path)
    c = er.resolve(d, [{"rel": "001_down_20-07-34.mkv",
                        "ss": "banana", "to": None}])[0]
    assert "ss" not in c and "to" not in c


def test_negative_trim_is_clamped(tmp_path):
    d = _sess(tmp_path)
    c = er.resolve(d, [{"rel": "001_down_20-07-34.mkv", "ss": -50}])[0]
    assert c["ss"] == 0.0


def test_tags_are_recovered_from_the_filename(tmp_path):
    d = _sess(tmp_path)
    c = er.resolve(d, [{"rel": "002_down+precision_20-30-32.mkv"}])[0]
    assert c["tag"] == "down+precision"
    assert c["downs"] == 1


def test_export_with_an_empty_selection_reports_and_stops(tmp_path):
    d = _sess(tmp_path)
    notes = []
    assert er.build(d, [], on_note=lambda **k: notes.append(k.get("note"))) == ""
    assert any("nothing to export" in (n or "") for n in notes)


def test_output_name_is_sanitised(tmp_path, monkeypatch):
    """The name comes from a text box on the dashboard."""
    d = _sess(tmp_path)
    seen = {}

    def fake_build(clips, out, *a, **k):
        seen["out"] = out
        return False
    monkeypatch.setattr("match_reel.build_match_reel", fake_build)
    monkeypatch.setattr("montage.find_ffmpeg", lambda b, c: "ffmpeg")
    er.build(d, [{"rel": "001_down_20-07-34.mkv"}],
             opts={"name": "../../evil / name.mp4"})
    assert ".." not in seen["out"]
    assert os.path.dirname(seen["out"]).endswith(os.path.join("sess", "reels"))


def test_explicit_trims_disable_automatic_tight_cuts(tmp_path, monkeypatch):
    """The user picked these moments; the app must not silently re-cut them."""
    d = _sess(tmp_path)
    seen = {}

    def fake_build(clips, out, ffmpeg, title, kills, sub, *a, **k):
        seen.update(k)
        return False
    monkeypatch.setattr("match_reel.build_match_reel", fake_build)
    monkeypatch.setattr("montage.find_ffmpeg", lambda b, c: "ffmpeg")
    er.build(d, [{"rel": "001_down_20-07-34.mkv"}])
    assert seen["tight_cuts"] is False


def test_thumbnails_live_in_a_subfolder_so_tidy_ignores_them(tmp_path):
    """tidy.py moves TOP-LEVEL files; a thumbnail at the top level would be filed
    into exfil/ as a screenshot, and would look like a clip to the indexer."""
    d = _sess(tmp_path)
    p = er.thumb_path(d, "clips/001_down_20-07-34.mkv")
    assert os.path.basename(os.path.dirname(p)) == er.THUMB_DIR
    assert "/" not in os.path.basename(p) and "\\" not in os.path.basename(p)


def test_thumbnail_of_a_missing_clip_returns_empty(tmp_path):
    assert er.ensure_thumb(str(tmp_path), "x.mkv", "/nope/x.mkv", "ffmpeg") == ""


def test_thumbnail_without_ffmpeg_returns_empty(tmp_path):
    d = _sess(tmp_path)
    assert er.ensure_thumb(d, "001_down_20-07-34.mkv",
                           os.path.join(d, "001_down_20-07-34.mkv"), "") == ""


def test_normalize_does_not_discard_caller_keys():
    """THE bug found building this: _normalize_clips rebuilt each dict from three
    known keys, so the editor's explicit ss/to vanished on the way in and a
    hand-trimmed export rendered every clip in full — 46s instead of 18s."""
    import match_reel
    got = match_reel._normalize_clips(
        [{"path": __file__, "tag": "down", "kills": 1, "ss": 4.0, "to": 10.0}])
    assert got[0]["ss"] == 4.0 and got[0]["to"] == 10.0


def test_normalize_still_accepts_bare_paths_and_fills_defaults():
    import match_reel
    got = match_reel._normalize_clips([__file__])
    assert got[0]["tag"] == "kill" and got[0]["kills"] == 1


def test_a_hand_picked_export_never_merges_overlapping_clips():
    """Overlap-merging is right for an AUTO reel (back-to-back buffer saves play
    as duplicates) and wrong for a chosen one — it would drop a clip the user
    deliberately picked."""
    import inspect

    import match_reel
    assert "dedupe" in inspect.signature(match_reel.build_match_reel).parameters
    src = inspect.getsource(er.build)
    assert "dedupe=False" in src
