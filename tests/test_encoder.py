"""Render encoder selection: hardware when available, software always as a net.

Stan: "pc really starts the fans up when building the session reel" — that's
libx264 at 4K across every core. He then noted Marathon is CPU-heavy, which is
the deciding factor: his FIRST round of frame drops was fixed by dropping render
ffmpeg to below-normal priority, so CPU contention is demonstrably what hurts his
frames. NVENC moves the work to fixed-function silicon off that contended path.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import match_reel as mr  # noqa: E402


def _fake_nvenc(available: bool):
    mr._ENCODER_CACHE["FAKE"] = available
    return "FAKE"


def test_auto_uses_nvenc_when_available():
    ff = _fake_nvenc(True)
    args = mr.video_encoder_args({"render_encoder": "auto"}, ff)
    assert args[:2] == ["-c:v", "h264_nvenc"]


def test_auto_falls_back_to_cpu_without_nvenc():
    """Most users won't have an NVIDIA card — the repo is public."""
    ff = _fake_nvenc(False)
    args = mr.video_encoder_args({"render_encoder": "auto"}, ff)
    assert args[:2] == ["-c:v", "libx264"]


def test_cpu_is_honoured_even_when_nvenc_exists():
    ff = _fake_nvenc(True)
    args = mr.video_encoder_args({"render_encoder": "cpu"}, ff)
    assert args[:2] == ["-c:v", "libx264"]


def test_explicit_nvenc_without_support_degrades_instead_of_failing():
    ff = _fake_nvenc(False)
    args = mr.video_encoder_args({"render_encoder": "nvenc"}, ff)
    assert args[:2] == ["-c:v", "libx264"], "must never leave a render unbuildable"


def test_missing_or_junk_config_still_produces_a_valid_encoder():
    ff = _fake_nvenc(False)
    for cfg in ({}, None, {"render_encoder": ""}, {"render_encoder": "banana"}):
        args = mr.video_encoder_args(cfg, ff)
        assert args[0] == "-c:v" and args[1] in ("libx264", "h264_nvenc"), cfg


def test_quality_is_specified_both_ways():
    """A hardware switch must not silently change output quality."""
    assert "-cq" in mr.video_encoder_args({"render_encoder": "nvenc"},
                                          _fake_nvenc(True))
    assert "-crf" in mr.cpu_encoder_args()


def test_nvenc_probe_is_cached():
    """Probing spawns ffmpeg; it must happen once, not per clip."""
    mr._ENCODER_CACHE.pop("/nope/ffmpeg", None)
    first = mr.has_nvenc("/nope/ffmpeg")
    assert "/nope/ffmpeg" in mr._ENCODER_CACHE
    assert mr.has_nvenc("/nope/ffmpeg") is first


def test_a_missing_ffmpeg_does_not_raise():
    mr._ENCODER_CACHE.pop("/definitely/not/here", None)
    assert mr.has_nvenc("/definitely/not/here") is False


def test_render_encoder_reaches_build_match_reel():
    import inspect

    import main
    assert "render_encoder" in main._reel_cut_kwargs({})
    assert "render_encoder" in inspect.signature(mr.build_match_reel).parameters
    import shorts
    assert "render_encoder" in inspect.signature(shorts.build_shorts).parameters
