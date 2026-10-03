"""playable.video_codec without an ffprobe next to ffmpeg.

imageio-ffmpeg's bundled binary ships no ffprobe (and many installs copy only
ffmpeg.exe). video_codec returned '' there, so remux() left out -tag:v hvc1
and the iPad got an HEVC mp4 tagged hev1, which AVPlayer will not play."""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import playable  # noqa: E402

HEVC_BANNER = (
    "Input #0, matroska,webm, from 'clip.mkv':\n"
    "  Duration: 00:00:30.02, start: 0.000000, bitrate: 9000 kb/s\n"
    "  Stream #0:0(eng): Video: hevc (Main), yuv420p(tv, bt709), 1920x1080, 60 fps\n"
    "  Stream #0:1(eng): Audio: aac (LC), 48000 Hz, stereo, fltp\n"
    "At least one output file must be specified\n")


class _R:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _no_ffprobe(monkeypatch, banner=HEVC_BANNER):
    calls = []

    def run(cmd, *a, **k):
        calls.append(cmd)
        if "ffprobe" in os.path.basename(cmd[0]):
            raise FileNotFoundError(cmd[0])
        return _R(stderr=banner, returncode=1)
    monkeypatch.setattr(playable.subprocess, "run", run)
    return calls


def test_codec_comes_from_ffmpeg_when_ffprobe_is_missing(monkeypatch):
    calls = _no_ffprobe(monkeypatch)
    ff = "/x/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"
    assert playable.video_codec("clip.mkv", ff) == "hevc"
    assert calls[-1][:3] == [ff, "-hide_banner", "-i"]


def test_an_hevc_remux_gets_the_hvc1_tag_without_ffprobe(monkeypatch, tmp_path):
    seen = []

    def run(cmd, *a, **k):
        if "ffprobe" in os.path.basename(cmd[0]):
            raise FileNotFoundError(cmd[0])
        if cmd[1:3] == ["-hide_banner", "-i"]:
            return _R(stderr=HEVC_BANNER, returncode=1)
        seen.append(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"mp4")
        return _R()
    monkeypatch.setattr(playable.subprocess, "run", run)
    dest = str(tmp_path / "mp4" / "clip.mp4")
    assert playable.remux(str(tmp_path / "clip.mkv"), dest, "ffmpeg-win-x86_64-v7.1.exe")
    assert seen and "-tag:v" in seen[0] and seen[0][seen[0].index("-tag:v") + 1] == "hvc1"


def test_ffprobe_still_wins_when_it_is_there(monkeypatch):
    calls = []

    def run(cmd, *a, **k):
        calls.append(cmd)
        return _R(stdout="h264\n")
    monkeypatch.setattr(playable.subprocess, "run", run)
    assert playable.video_codec("clip.mkv", "ffmpeg") == "h264"
    assert len(calls) == 1


def test_nothing_readable_is_empty(monkeypatch):
    _no_ffprobe(monkeypatch, banner="clip.mkv: Invalid data found\n")
    assert playable.video_codec("clip.mkv", "ffmpeg") == ""


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs a real ffmpeg")
def test_the_fallback_parses_a_real_ffmpeg_banner(tmp_path, monkeypatch):
    clip = str(tmp_path / "c.mkv")
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "color=black:s=64x36:d=0.2", "-c:v", "mpeg4", clip],
                       capture_output=True)
    if r.returncode != 0:
        pytest.skip("ffmpeg could not make a test clip")
    # A path whose ffprobe sibling does not exist, like imageio's binary.
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    ff = fake_dir / "ffmpeg-bundled"
    ff.symlink_to(shutil.which("ffmpeg"))
    assert playable.video_codec(clip, str(ff)) == "mpeg4"
