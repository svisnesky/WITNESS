"""Every kill of the night must reach the reel.

Stan, 2026-10-03: "can you see why every kill from the session wouldnt be in the
session reel? ... i know its missing a good amount of kills".

Cause: drop_overlapping kept only the LATER of two overlapping Replay-Buffer saves
and threw the earlier one away, on the premise that the later 30s buffer
"contains" it. It doesn't: a save holds the 30s BEFORE it, so everything older
than (later save - 30s) exists only in the earlier clip. On 2026-08-08 four saves
of one fight (22:07:07, :27, :40, :51) collapsed into one clip covering
22:07:21-22:07:51, and the first kills of the fight were nowhere in the reel.
Real logs: 7/29 merged 5 of 18 clips away, 8/8 four of 9.
"""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import match_reel as mr  # noqa: E402

DUR = 30.0
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _secs(hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


# The 2026-08-08 fight, from the real log: four saves, each ~8s after its kill.
AUG8 = [("003_assist+kill_22-07-07.mkv", "22:06:59", 1, "assist+kill"),
        ("005_down+kill_22-07-27.mkv", "22:07:19", 2, "down+kill"),
        ("006_down_22-07-40.mkv", "22:07:32", 1, "down"),
        ("007_kill_22-07-51.mkv", "22:07:43", 1, "kill")]


def _clips(rows):
    return [{"path": f"Z:/s/{n}", "kills": k, "tag": t} for n, _kill, k, t in rows]


def _played(clips):
    """Wall-clock intervals the reel actually shows, from save times + ss_min."""
    out = []
    for c in clips:
        save = mr._save_time(c["path"])
        out.append((save - DUR + float(c.get("ss_min", 0.0)), save))
    return out


def _covered(t, intervals):
    return any(a <= t <= b for a, b in intervals)


def test_the_old_merge_really_lost_the_aug_8_kills(monkeypatch):
    """Documents the bug against the function that is still used for Shorts."""
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: DUR)
    kept = mr.drop_overlapping(_clips(AUG8), "ffmpeg")
    assert len(kept) == 1
    shown = _played(kept)
    lost = [kill for _n, kill, _k, _t in AUG8 if not _covered(_secs(kill), shown)]
    assert lost == ["22:06:59", "22:07:19"], lost


def test_chaining_keeps_every_clip_and_every_kill_on_screen(monkeypatch):
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: DUR)
    out = mr.chain_overlapping(_clips(AUG8), "ffmpeg")
    assert [os.path.basename(c["path"]) for c in out] == [r[0] for r in AUG8]
    assert sum(c["kills"] for c in out) == sum(r[2] for r in AUG8)
    shown = _played(out)
    for _n, kill, _k, _t in AUG8:
        assert _covered(_secs(kill), shown), f"kill at {kill} not in the reel"


def test_each_chained_clip_starts_where_the_last_one_ended(monkeypatch):
    """No footage plays twice: the later clip starts at the earlier save, minus
    the 0.5s crossfade so the blend shows the same moment instead of a skip."""
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: DUR)
    out = mr.chain_overlapping(_clips(AUG8), "ffmpeg")
    assert "ss_min" not in out[0]
    assert [c["ss_min"] for c in out[1:]] == [9.5, 16.5, 18.5]   # 30 - gap - 0.5
    assert len({c["_chain"] for c in out}) == 1, "one fight, one chain"
    shown = _played(out)
    for (a0, b0), (a1, _b1) in zip(shown, shown[1:]):
        assert b0 - 0.5 == pytest.approx(a1), "continuous, overlap = crossfade only"


def test_spaced_saves_are_separate_fights(monkeypatch):
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: DUR)
    out = mr.chain_overlapping(_clips([("001_down_19-48-47.mkv", "", 1, "down"),
                                       ("002_down_19-55-44.mkv", "", 1, "down")]),
                               "ffmpeg")
    assert all("ss_min" not in c for c in out)
    assert all("_chain" not in c for c in out), "a lone save is not part of a fight"


def test_a_save_that_adds_almost_nothing_is_credited_not_shown(monkeypatch):
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: DUR)
    out = mr.chain_overlapping(_clips([("004_down_22-00-00.mkv", "", 1, "down"),
                                       ("004_kill_22-00-00.mkv", "", 1, "kill")]),
                               "ffmpeg")
    assert len(out) == 1
    assert out[0]["kills"] == 2 and "kill" in out[0]["tag"]
    assert out[0]["_folded_paths"][-1].endswith("004_kill_22-00-00.mkv")


def test_chaining_is_idempotent(monkeypatch):
    """main.py chains the match clips and build_match_reel chains them again."""
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: DUR)
    once = mr.chain_overlapping(_clips(AUG8), "ffmpeg")
    twice = mr.chain_overlapping(once, "ffmpeg")
    assert [c.get("ss_min") for c in once] == [c.get("ss_min") for c in twice]


def test_play_of_the_game_is_the_whole_fight(monkeypatch):
    """Split across clips, a 3-down fight must still beat a lone 2-down clip,
    and it plays from the fight's first clip."""
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: DUR)
    clips = mr.chain_overlapping(_clips([
        ("001_down+down_21-00-00.mkv", "", 2, "down+down"),
        ("002_down_21-30-00.mkv", "", 1, "down"),
        ("003_down_21-30-15.mkv", "", 1, "down"),
        ("004_down_21-30-28.mkv", "", 1, "down")]), "ffmpeg")
    potg = mr.pick_potg(clips)
    assert potg["path"].endswith("002_down_21-30-00.mkv")
    members = mr.potg_members(clips, potg)
    assert [os.path.basename(m["path"]) for m in members] == [
        "002_down_21-30-00.mkv", "003_down_21-30-15.mkv", "004_down_21-30-28.mkv"]


def test_a_long_fight_saves_before_its_first_kill_leaves_the_buffer():
    """Every event re-armed the 8s wait, so popups landing under 8s apart for a
    whole fight delayed the save until the fight ended. The first event's
    deadline is now capped at kill_coalesce_max_seconds after it."""
    import main
    s = {}
    cfg = {"kill_coalesce_seconds": 8.0, "kill_coalesce_max_seconds": 20.0}
    t0 = 1000.0
    for i in range(12):                      # an event every 5s for a minute
        main._queue_coalesce(cfg, s, "assist", 0, t0 + 5 * i)
        assert s["_coalesce_deadline"] <= t0 + 20.0
        if t0 + 5 * i >= s["_coalesce_deadline"]:
            break
    # after a flush, the next clip gets its own 20s budget
    s["_coalesce_pending"] = []
    main._queue_coalesce(cfg, s, "down", 1, t0 + 100)
    assert s["_coalesce_deadline"] == t0 + 108.0


def test_a_lone_kill_still_waits_the_normal_window():
    import main
    s = {}
    main._queue_coalesce({}, s, "down", 1, 50.0)
    assert s["_coalesce_deadline"] == 58.0


def _duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    return float(r.stdout.strip())


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_the_montage_keeps_the_whole_fight_on_real_files(tmp_path):
    """End to end with real ffmpeg. Four overlapping 30s saves cut from one
    74-second stretch of 'gameplay', named like OBS names them. The montage used
    to keep only the last save (30s). Chained, it must cover the whole stretch
    once: 74s, give or take a keyframe."""
    import montage
    src = tmp_path / "wall.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=160x90:rate=10:duration=74", "-c:v", "libx264",
                    "-g", "10", "-pix_fmt", "yuv420p", str(src)], check=True)
    sess = tmp_path / "2026-08-08_21-34-45"
    sess.mkdir()
    # saves at wall seconds 30, 50, 63, 74 (gaps 20, 13, 11 like 8/8)
    for name, end in (("003_assist+kill_22-07-07.mkv", 30), ("005_down+kill_22-07-27.mkv", 50),
                      ("006_down_22-07-40.mkv", 63), ("007_kill_22-07-51.mkv", 74)):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(end - 30), "-i", str(src),
                        "-t", "30", "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p",
                        str(sess / name)], check=True)
    out = montage.build_montage(str(sess), "ffmpeg")
    assert out and os.path.exists(out)
    got = _duration(out)
    assert 72.0 <= got <= 77.0, f"montage is {got:.1f}s; the fight is 74s"
