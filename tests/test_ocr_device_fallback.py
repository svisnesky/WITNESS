"""OCR falls back to the CPU ONLY when PyTorch has no kernels for the GPU.

Recent cu128 PyTorch wheels dropped GTX 10-series and older (sm_50-61), but
torch.cuda.is_available() is still True on those cards, so the first OCR call
died with "no kernel image is available". Those cards now get CPU OCR, loudly.

The other half matters just as much: a CUDA out-of-memory (or any other error)
must re-raise, never quietly move a working RTX card to CPU OCR mid-game. And
the arch check must follow CUDA's real compatibility rules: RTX 40-series is
sm_89, which official wheels don't list (it runs the sm_86 kernels), so a naive
`sm_89 in get_arch_list()` would have put every 40-series card on the CPU.

torch / easyocr / pytesseract are faked here (none are installed on the Mac
that runs this suite). Nothing touches a real GPU.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ocr  # noqa: E402

CU128 = ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"]
CU126 = ["sm_50", "sm_60", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"]

NO_KERNEL = RuntimeError(
    "CUDA error: no kernel image is available for execution on the device\n"
    "CUDA kernel errors might be asynchronously reported at some other API call")
OOM = RuntimeError(
    "CUDA out of memory. Tried to allocate 20.00 MiB (GPU 0; 15.92 GiB total "
    "capacity; 14.80 GiB already allocated)")


class _Rig:
    """A fake torch + easyocr pair. Records every Reader built and every
    warm-up readtext."""

    def __init__(self, cuda=True, cap=(12, 0), archs=None, warmup_error=None,
                 build_error=None, arch_error=None):
        self.cuda, self.cap, self.archs = cuda, cap, CU128 if archs is None else archs
        self.warmup_error, self.build_error = warmup_error, build_error
        self.arch_error = arch_error
        self.readers = []           # gpu= argument of each Reader built
        self.readtexts = []         # shapes passed to readtext
        self.empty_cache_calls = 0

    def install(self, monkeypatch):
        rig = self

        def get_arch_list():
            if rig.arch_error:
                raise rig.arch_error
            return list(rig.archs)

        def empty_cache():
            rig.empty_cache_calls += 1

        torch = types.ModuleType("torch")
        torch.cuda = types.SimpleNamespace(
            is_available=lambda: rig.cuda,
            get_device_capability=lambda *a: rig.cap,
            get_arch_list=get_arch_list,
            get_device_name=lambda *a: "Fake GeForce",
            memory_reserved=lambda *a: 0,
            empty_cache=empty_cache,
        )

        class Reader:
            def __init__(self, langs, gpu=True):
                rig.readers.append(gpu)
                if gpu and rig.build_error:
                    raise rig.build_error
                # easyocr's own rule: gpu=True only means CUDA if it's there
                self.device = "cuda" if (gpu and rig.cuda) else "cpu"

            def readtext(self, img, **kw):
                rig.readtexts.append((self.device, img.shape))
                if self.device == "cuda" and rig.warmup_error:
                    raise rig.warmup_error
                return []

        easyocr = types.ModuleType("easyocr")
        easyocr.Reader = Reader
        monkeypatch.setitem(sys.modules, "torch", torch)
        monkeypatch.setitem(sys.modules, "easyocr", easyocr)
        return self


def _load(monkeypatch, **kw):
    rig = _Rig(**kw).install(monkeypatch)
    eng = ocr.OCREngine("easyocr")
    eng._ensure_loaded()
    return rig, eng


def test_a_working_rtx_card_stays_on_the_gpu(monkeypatch, capsys):
    rig, eng = _load(monkeypatch, cap=(12, 0))          # RTX 5080 on cu128
    assert rig.readers == [True]
    assert eng.device == "cuda"
    assert rig.readtexts == [("cuda", (32, 32))]          # one tiny warm-up
    out = capsys.readouterr().out
    assert "OCR device: cuda (Fake GeForce)" in out
    assert "GPU OCR unavailable" not in out


def test_rtx_40_series_is_not_mistaken_for_unsupported(monkeypatch):
    """sm_89 is in no official arch list; it runs the sm_86 kernels."""
    rig, eng = _load(monkeypatch, cap=(8, 9), archs=CU128)
    assert rig.readers == [True] and eng.device == "cuda"


def test_ptx_entry_counts_as_supported(monkeypatch):
    rig, eng = _load(monkeypatch, cap=(12, 0),
                     archs=["sm_80", "sm_90", "compute_90"])
    assert rig.readers == [True] and eng.device == "cuda"


def test_pascal_on_a_cu126_build_stays_on_the_gpu(monkeypatch):
    """sm_61 runs sm_60 kernels: GTX 10-series is fine on a build that has them."""
    rig, eng = _load(monkeypatch, cap=(6, 1), archs=CU126)
    assert rig.readers == [True] and eng.device == "cuda"


def test_pascal_on_a_cu128_build_goes_straight_to_cpu(monkeypatch, capsys):
    rig, eng = _load(monkeypatch, cap=(6, 1), archs=CU128)
    assert rig.readers == [False]                         # never built on GPU
    assert eng.device == "cpu"
    out = capsys.readouterr().out
    assert "GPU OCR unavailable on this card (sm_61 is not in this PyTorch build" in out
    assert "Set poll_fps: 3 and ocr_upscale: 2 for lighter load." in out
    assert "OCR device: cpu" in out


def test_unknown_arch_list_still_tries_the_gpu(monkeypatch):
    rig, eng = _load(monkeypatch, cap=(6, 1), archs=[])
    assert rig.readers == [True] and eng.device == "cuda"
    rig, eng = _load(monkeypatch, arch_error=RuntimeError("driver says no"))
    assert rig.readers == [True] and eng.device == "cuda"


def test_no_kernel_image_on_warmup_rebuilds_on_cpu(monkeypatch, capsys):
    rig, eng = _load(monkeypatch, cap=(12, 0), warmup_error=NO_KERNEL)
    assert rig.readers == [True, False]
    assert eng.device == "cpu"
    assert rig.empty_cache_calls == 1                     # failed reader's VRAM back
    out = capsys.readouterr().out
    assert ("GPU OCR unavailable on this card (CUDA error: no kernel image is "
            "available for execution on the device) - using CPU.") in out
    assert out.count("GPU OCR unavailable") == 1


def test_not_compatible_message_on_build_also_falls_back(monkeypatch):
    err = RuntimeError("NVIDIA GeForce GTX 1080 with CUDA capability sm_61 is "
                       "not compatible with the current PyTorch installation.")
    rig, eng = _load(monkeypatch, build_error=err)
    assert rig.readers == [True, False] and eng.device == "cpu"


def test_out_of_memory_reraises_and_never_moves_to_cpu(monkeypatch):
    rig = _Rig(warmup_error=OOM).install(monkeypatch)
    eng = ocr.OCREngine("easyocr")
    with pytest.raises(RuntimeError, match="out of memory"):
        eng._ensure_loaded()
    assert rig.readers == [True]                          # no CPU reader built
    assert eng._loaded is False


def test_out_of_memory_mentioning_an_arch_still_reraises(monkeypatch):
    err = RuntimeError("CUDA out of memory on sm_120 device")
    rig = _Rig(build_error=err).install(monkeypatch)
    with pytest.raises(RuntimeError, match="out of memory"):
        ocr.OCREngine("easyocr")._ensure_loaded()
    assert rig.readers == [True]


def test_any_other_gpu_error_reraises(monkeypatch):
    rig = _Rig(warmup_error=RuntimeError("cuDNN error: CUDNN_STATUS_INTERNAL_ERROR")
               ).install(monkeypatch)
    with pytest.raises(RuntimeError, match="CUDNN_STATUS_INTERNAL_ERROR"):
        ocr.OCREngine("easyocr")._ensure_loaded()
    assert rig.readers == [True]


def test_no_cuda_keeps_todays_call_and_skips_the_warmup(monkeypatch):
    """No CUDA: Reader(gpu=True) exactly as before, easyocr picks the CPU."""
    rig, eng = _load(monkeypatch, cuda=False)
    assert rig.readers == [True]
    assert rig.readtexts == []
    assert eng.device == "cpu"


def test_load_happens_once(monkeypatch):
    rig, eng = _load(monkeypatch)
    eng._ensure_loaded()
    eng._ensure_loaded()
    assert rig.readers == [True] and len(rig.readtexts) == 1


def test_the_fallback_leaves_the_frozen_caps_alone():
    assert ocr.OCR_MAX_DIM == 800
    assert ocr.OCR_ONEOFF_MAX_DIM == 1600
    e = ocr.OCREngine()
    assert (e._cap(None), e._cap(0)) == (800, 1600)


def test_is_kernel_incompat_is_narrow():
    assert ocr._is_kernel_incompat(NO_KERNEL)
    assert not ocr._is_kernel_incompat(OOM)
    assert not ocr._is_kernel_incompat(RuntimeError("CUDA error: unspecified launch failure"))
    assert not ocr._is_kernel_incompat(RuntimeError("device-side assert triggered"))
    assert not ocr._is_kernel_incompat(ValueError("bad option sm_count"))  # needs digits
