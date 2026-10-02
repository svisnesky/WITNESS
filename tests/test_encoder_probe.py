"""NVENC detection that asks the GPU, not just the ffmpeg build — without ever
turning NVENC off on a transient error.

`ffmpeg -encoders` lists h264_nvenc on every PC with the gyan.dev build the docs
link, NVIDIA or not. On AMD/Intel/GPU-less PCs every reel then failed twice on
NVENC and printed three "failed" lines before rendering on CPU.

The trap on the other side is Stan's: a probe that caches False on ANY failure
(OBS holding the encoder sessions, a driver hiccup, a timeout while the game is
loading) would silently move his reels onto libx264 for the night — the CPU
contention that NVENC was switched on to remove. So only a missing-hardware
message ever turns it off.

Also here: the reel's card PNGs are cleaned up even when ffmpeg can't start, and
reels letterbox non-16:9 footage instead of squashing it.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import match_reel as mr  # noqa: E402

LISTED = " V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)\n"
NO_DRIVER = "[h264_nvenc @ 0x1] Cannot load nvcuda.dll\nError while opening encoder\n"
NO_DEVICE = "[h264_nvenc @ 0x1] No NVENC capable devices found\n"
BUSY = ("[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: incompatible client key"
        " (21): (no details)\n")


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """Every test starts with nothing cached, and leaves the real cache alone."""
    monkeypatch.setattr(mr, "_ENCODER_CACHE", {})


def _fake_ffmpeg(monkeypatch, encoders_out=LISTED, probe=None, calls=None):
    """subprocess.run stand-in: answers `-encoders`, then the one-frame probe.
    `probe` is (returncode, stderr) or an exception to raise."""
    def run(cmd, **k):
        if calls is not None:
            calls.append(cmd)
        if "-encoders" in cmd:
            return subprocess.CompletedProcess(cmd, 0, encoders_out, "")
        assert "h264_nvenc" in cmd and "-frames:v" in cmd, cmd
        assert k.get("timeout") == 20
        if isinstance(probe, BaseException):
            raise probe
        rc, err = probe or (0, "")
        return subprocess.CompletedProcess(cmd, rc, "", err)
    monkeypatch.setattr(mr.subprocess, "run", run)


def test_listed_but_no_nvidia_driver_renders_on_cpu(monkeypatch):
    _fake_ffmpeg(monkeypatch, probe=(1, NO_DRIVER))
    assert mr.has_nvenc("FF") is False
    assert mr.video_encoder_args({"render_encoder": "auto"}, "FF")[:2] == [
        "-c:v", "libx264"]


def test_a_card_without_nvenc_renders_on_cpu(monkeypatch):
    _fake_ffmpeg(monkeypatch, probe=(1, NO_DEVICE))
    assert mr.has_nvenc("FF") is False


def test_an_unrelated_probe_error_keeps_nvenc(monkeypatch):
    """OBS holding every encoder session is NOT 'no GPU'."""
    _fake_ffmpeg(monkeypatch, probe=(1, BUSY))
    assert mr.has_nvenc("FF") is True
    assert mr.video_encoder_args({"render_encoder": "auto"}, "FF")[:2] == [
        "-c:v", "h264_nvenc"]


def test_a_probe_timeout_keeps_nvenc(monkeypatch):
    """The game loading the GPU can stall the probe; that proves nothing."""
    _fake_ffmpeg(monkeypatch, probe=subprocess.TimeoutExpired("ffmpeg", 20))
    assert mr.has_nvenc("FF") is True


def test_a_probe_that_cannot_launch_keeps_nvenc(monkeypatch):
    _fake_ffmpeg(monkeypatch, probe=OSError("launch hiccup"))
    assert mr.has_nvenc("FF") is True


def test_a_working_gpu_keeps_nvenc(monkeypatch):
    _fake_ffmpeg(monkeypatch, probe=(0, ""))
    assert mr.has_nvenc("FF") is True


def test_no_probe_when_the_build_has_no_nvenc(monkeypatch):
    calls = []
    _fake_ffmpeg(monkeypatch, encoders_out=" V..... libx264\n", calls=calls)
    assert mr.has_nvenc("FF") is False
    assert len(calls) == 1, "probed an encoder the build doesn't have"


def test_the_answer_is_probed_once_per_ffmpeg(monkeypatch):
    calls = []
    _fake_ffmpeg(monkeypatch, probe=(1, NO_DRIVER), calls=calls)
    mr.has_nvenc("FF")
    mr.has_nvenc("FF")
    mr.video_encoder_args({}, "FF")
    assert len(calls) == 2           # -encoders + one probe, then cached


# ---- the encode ladder inside build_match_reel ----------------------------

def _two_clips(tmp_path):
    out = []
    for n in ("001_down_20-01-00.mkv", "002_down+down_20-09-00.mkv"):
        p = tmp_path / n
        p.write_bytes(b"x")
        out.append(str(p))
    return out


def _stub_reel(monkeypatch, cards=True):
    """No ffprobe, no fonts; card PNGs are real files so cleanup can be seen."""
    import shorts
    monkeypatch.setattr(mr, "probe_duration", lambda p, f: 30.0)
    monkeypatch.setattr(shorts, "_find_font", lambda: "")

    def card(out_png, *a, **k):
        if not cards:
            return False
        with open(out_png, "wb") as f:
            f.write(b"png")
        return True
    monkeypatch.setattr(mr, "_build_card", card)


def _ladder(monkeypatch, nvenc_err, legacy_err):
    """_run stand-in: modern NVENC and legacy NVENC fail as given, CPU works."""
    runs = []

    def run(cmd):
        enc = cmd[cmd.index("-c:v") + 1]
        runs.append((enc, "-preset" in cmd and cmd[cmd.index("-preset") + 1]))
        if enc == "h264_nvenc":
            err = nvenc_err if len(runs) == 1 else legacy_err
            return subprocess.CompletedProcess(cmd, 1, "", err)
        with open(cmd[-1], "wb") as f:
            f.write(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(mr, "_run", run)
    return runs


def test_no_nvidia_hardware_says_so_once_and_remembers(tmp_path, monkeypatch, capsys):
    _stub_reel(monkeypatch)
    mr._ENCODER_CACHE["FF"] = True              # listed; probe was inconclusive
    runs = _ladder(monkeypatch, NO_DRIVER, NO_DRIVER)
    out = str(tmp_path / "reels" / "match_1.mp4")
    assert mr.build_match_reel(_two_clips(tmp_path), out, "FF", "T", 2, [])
    printed = capsys.readouterr().out
    assert "NVIDIA encoder unavailable - rendering on CPU" in printed
    assert "hardware encode failed" not in printed
    assert "legacy NVENC also failed" not in printed
    assert [e for e, _p in runs] == ["h264_nvenc", "h264_nvenc", "libx264"]
    assert mr._ENCODER_CACHE["FF"] is False     # next reel / Shorts skip NVENC
    assert mr.video_encoder_args({}, "FF")[1] == "libx264"


def test_a_transient_nvenc_failure_is_reported_and_not_cached(tmp_path, monkeypatch,
                                                              capsys):
    """The case that must NOT move Stan onto the CPU for the rest of the night."""
    _stub_reel(monkeypatch)
    mr._ENCODER_CACHE["FF"] = True
    runs = _ladder(monkeypatch, BUSY, BUSY)
    out = str(tmp_path / "reels" / "match_1.mp4")
    assert mr.build_match_reel(_two_clips(tmp_path), out, "FF", "T", 2, [])
    printed = capsys.readouterr().out
    assert "hardware encode failed" in printed
    assert "legacy NVENC also failed" in printed
    assert "falling back to CPU" in printed
    assert "NVIDIA encoder unavailable" not in printed
    assert [e for e, _p in runs][-1] == "libx264"
    assert mr._ENCODER_CACHE["FF"] is True


def test_one_rung_saying_no_hardware_is_not_enough(tmp_path, monkeypatch, capsys):
    """Both rungs must agree before the session gives up on the GPU."""
    _stub_reel(monkeypatch)
    mr._ENCODER_CACHE["FF"] = True
    _ladder(monkeypatch, NO_DRIVER, BUSY)
    out = str(tmp_path / "reels" / "match_1.mp4")
    assert mr.build_match_reel(_two_clips(tmp_path), out, "FF", "T", 2, [])
    printed = capsys.readouterr().out
    assert "hardware encode failed" in printed
    assert mr._ENCODER_CACHE["FF"] is True


def test_legacy_nvenc_success_still_wins(tmp_path, monkeypatch, capsys):
    _stub_reel(monkeypatch)
    mr._ENCODER_CACHE["FF"] = True
    runs = []

    def run(cmd):
        preset = cmd[cmd.index("-preset") + 1]
        runs.append(preset)
        if preset == "p4":
            return subprocess.CompletedProcess(cmd, 1, "", "Preset p4 not found")
        with open(cmd[-1], "wb") as f:
            f.write(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(mr, "_run", run)
    out = str(tmp_path / "reels" / "match_1.mp4")
    assert mr.build_match_reel(_two_clips(tmp_path), out, "FF", "T", 2, [])
    assert runs == ["p4", "hq"]
    assert "succeeded with legacy NVENC" in capsys.readouterr().out
    assert mr._ENCODER_CACHE["FF"] is True


# ---- letterbox -------------------------------------------------------------

def test_reels_letterbox_instead_of_squashing(tmp_path, monkeypatch):
    """scale=1920:1080 alone squashed 21:9/32:9 clips. Fit, then pad to exactly
    1920x1080 (xfade and concat need every segment the same size). No
    force_divisible_by: it needs ffmpeg 4.3+, and an older build would fail
    every reel."""
    _stub_reel(monkeypatch)
    mr._ENCODER_CACHE["FF"] = False
    graphs = []

    def run(cmd):
        graphs.append(cmd[cmd.index("-filter_complex") + 1])
        with open(cmd[-1], "wb") as f:
            f.write(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(mr, "_run", run)
    mr.build_match_reel(_two_clips(tmp_path), str(tmp_path / "reels" / "r.mp4"),
                        "FF", "T", 2, [])
    g = graphs[0]
    fit = ("scale=1920:1080:force_original_aspect_ratio=decrease,"
           "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,")
    # title card + POTG card + 2 clips + end card: every segment is fitted
    assert g.count(fit) == 5
    assert "scale=1920:1080,setsar" not in g
    assert "force_divisible_by" not in g


# ---- card PNG cleanup ------------------------------------------------------

def test_card_pngs_are_removed_when_ffmpeg_cannot_start(tmp_path, monkeypatch):
    """No ffmpeg: _run raises WinError 2 before the old cleanup loop, which left
    _card/_potg/_end PNGs behind on every failed recap."""
    _stub_reel(monkeypatch)
    mr._ENCODER_CACHE["FF"] = False

    def run(cmd):
        raise FileNotFoundError(2, "The system cannot find the file specified")
    monkeypatch.setattr(mr, "_run", run)
    reels = tmp_path / "reels"
    with pytest.raises(FileNotFoundError):
        mr.build_match_reel(_two_clips(tmp_path), str(reels / "match_1.mp4"),
                            "FF", "T", 2, [])
    assert [f for f in os.listdir(reels) if f.endswith(".png")] == []


def test_card_pngs_are_removed_after_every_failed_retry(tmp_path, monkeypatch):
    _stub_reel(monkeypatch)
    mr._ENCODER_CACHE["FF"] = False
    seen_pngs = []

    def run(cmd):
        seen_pngs.extend(i for i in cmd if str(i).endswith(".png"))
        return subprocess.CompletedProcess(cmd, 1, "", "render failed")
    monkeypatch.setattr(mr, "_run", run)
    reels = tmp_path / "reels"
    assert mr.build_match_reel(_two_clips(tmp_path), str(reels / "match_1.mp4"),
                               "FF", "T", 2, []) is False
    assert seen_pngs, "cards were never used — the test proves nothing"
    assert [f for f in os.listdir(reels) if f.endswith(".png")] == []


# ---- auto reels keep Play of the Game; exports turn it off -----------------

def _render_order(tmp_path, monkeypatch, **kw):
    _stub_reel(monkeypatch, cards=False)
    mr._ENCODER_CACHE["FF"] = False
    order = []

    def run(cmd):
        order.extend(os.path.basename(i) for i in cmd if str(i).endswith(".mkv"))
        with open(cmd[-1], "wb") as f:
            f.write(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(mr, "_run", run)
    clips = _two_clips(tmp_path)
    mr.build_match_reel(clips, str(tmp_path / "reels" / "r.mp4"), "FF", "T", 2,
                        [], dedupe=False, **kw)
    return order


def test_auto_reels_still_lead_with_play_of_the_game(tmp_path, monkeypatch):
    assert _render_order(tmp_path, monkeypatch) == [
        "002_down+down_20-09-00.mkv", "001_down_20-01-00.mkv"]


def test_potg_off_keeps_the_given_order(tmp_path, monkeypatch):
    assert _render_order(tmp_path, monkeypatch, potg_enabled=False) == [
        "001_down_20-01-00.mkv", "002_down+down_20-09-00.mkv"]
