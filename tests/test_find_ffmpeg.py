"""Where ffmpeg comes from, in order — and the imageio-ffmpeg fallback that must
never get in the way of an ffmpeg the user installed.

An install can run code that lists imageio-ffmpeg before pip has installed it
(the updater swaps files first; the install can fail or be offline). The import
is guarded, and the bundled binary is used only when nothing else exists.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import montage  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(montage, "_IMAGEIO_FFMPEG", None)


def _imageio(monkeypatch, exe=None, raises=None, calls=None):
    """Install a fake imageio_ffmpeg module (None = make the import fail)."""
    if exe is None and raises is None:
        monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)  # ImportError
        return
    mod = types.ModuleType("imageio_ffmpeg")

    def get_ffmpeg_exe():
        if calls is not None:
            calls.append(1)
        if raises:
            raise raises
        return exe
    mod.get_ffmpeg_exe = get_ffmpeg_exe
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", mod)


def _which(monkeypatch, found):
    monkeypatch.setattr(montage.shutil, "which",
                        lambda name: ("/usr/bin/" + name) if found else None)


def test_config_path_wins(tmp_path, monkeypatch):
    ff = tmp_path / "custom_ffmpeg.exe"
    ff.write_bytes(b"x")
    (tmp_path / "ffmpeg.exe").write_bytes(b"x")
    _which(monkeypatch, True)
    assert montage.find_ffmpeg(str(tmp_path), {"ffmpeg_path": str(ff)}) == str(ff)


def test_the_app_folder_ffmpeg_exe_beats_path_and_imageio(tmp_path, monkeypatch):
    """Stan's setup. Nothing new may outrank it."""
    (tmp_path / "ffmpeg.exe").write_bytes(b"x")
    calls = []
    _imageio(monkeypatch, exe="/elsewhere/ffmpeg", calls=calls)
    _which(monkeypatch, True)
    assert montage.find_ffmpeg(str(tmp_path), {}) == str(tmp_path / "ffmpeg.exe")
    assert calls == []


def test_path_ffmpeg_keeps_the_bare_name(tmp_path, monkeypatch):
    calls = []
    _imageio(monkeypatch, exe="/elsewhere/ffmpeg", calls=calls)
    _which(monkeypatch, True)
    assert montage.find_ffmpeg(str(tmp_path), {}) == "ffmpeg"
    assert calls == [], "imageio consulted although PATH had ffmpeg"


def test_imageio_is_the_last_resort(tmp_path, monkeypatch):
    bundled = tmp_path / "ffmpeg-bundled"
    bundled.write_bytes(b"x")
    _imageio(monkeypatch, exe=str(bundled))
    _which(monkeypatch, False)
    assert montage.find_ffmpeg(str(tmp_path / "app"), {}) == str(bundled)


def test_no_imageio_package_keeps_the_old_sentinel(tmp_path, monkeypatch):
    _imageio(monkeypatch)                          # import raises ImportError
    _which(monkeypatch, False)
    assert montage.find_ffmpeg(str(tmp_path), {}) == "ffmpeg"


def test_imageio_without_a_binary_keeps_the_old_sentinel(tmp_path, monkeypatch):
    _imageio(monkeypatch, raises=RuntimeError("no ffmpeg exe for this platform"))
    _which(monkeypatch, False)
    assert montage.find_ffmpeg(str(tmp_path), {}) == "ffmpeg"


def test_imageio_pointing_at_nothing_is_ignored(tmp_path, monkeypatch):
    _imageio(monkeypatch, exe=str(tmp_path / "does-not-exist"))
    _which(monkeypatch, False)
    assert montage.find_ffmpeg(str(tmp_path), {}) == "ffmpeg"


def test_imageio_is_looked_up_once_per_process(tmp_path, monkeypatch):
    """find_ffmpeg runs on every kill for anyone without ffmpeg."""
    bundled = tmp_path / "ffmpeg-bundled"
    bundled.write_bytes(b"x")
    calls = []
    _imageio(monkeypatch, exe=str(bundled), calls=calls)
    _which(monkeypatch, False)
    for _ in range(3):
        montage.find_ffmpeg(str(tmp_path), {})
    assert calls == [1]


def test_a_missing_config_dict_does_not_crash(tmp_path, monkeypatch):
    _imageio(monkeypatch)
    _which(monkeypatch, False)
    assert montage.find_ffmpeg(str(tmp_path), None) == "ffmpeg"


def test_has_ffmpeg_true_for_an_app_folder_exe(tmp_path, monkeypatch):
    (tmp_path / "ffmpeg.exe").write_bytes(b"x")
    _which(monkeypatch, False)
    assert montage.has_ffmpeg(str(tmp_path), {}) is True


def test_has_ffmpeg_checks_the_bare_name_on_path_not_on_disk(tmp_path, monkeypatch):
    """os.path.exists('ffmpeg') is False for a PATH install; using it would
    switch off instant replays for everyone with ffmpeg on PATH."""
    _imageio(monkeypatch)
    _which(monkeypatch, True)
    assert montage.has_ffmpeg(str(tmp_path), {}) is True


def test_has_ffmpeg_false_when_nothing_exists(tmp_path, monkeypatch):
    _imageio(monkeypatch)
    _which(monkeypatch, False)
    assert montage.has_ffmpeg(str(tmp_path), {}) is False


def test_has_ffmpeg_true_for_the_imageio_binary(tmp_path, monkeypatch):
    bundled = tmp_path / "ffmpeg-bundled"
    bundled.write_bytes(b"x")
    _imageio(monkeypatch, exe=str(bundled))
    _which(monkeypatch, False)
    assert montage.has_ffmpeg(str(tmp_path / "app"), {}) is True


def test_has_ffmpeg_sees_an_ffmpeg_installed_while_the_app_runs(tmp_path, monkeypatch):
    """The window app runs for days: no stale cached False."""
    _imageio(monkeypatch)
    _which(monkeypatch, False)
    assert montage.has_ffmpeg(str(tmp_path), {}) is False
    (tmp_path / "ffmpeg.exe").write_bytes(b"x")
    assert montage.has_ffmpeg(str(tmp_path), {}) is True


def test_main_asks_montage(tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(montage, "has_ffmpeg", lambda base, cfg: False)
    assert main._ffmpeg_available(str(tmp_path), {}) is False
    monkeypatch.setattr(montage, "has_ffmpeg", lambda base, cfg: True)
    assert main._ffmpeg_available(str(tmp_path), {}) is True

    def boom(base, cfg):
        raise RuntimeError("x")
    monkeypatch.setattr(montage, "has_ffmpeg", boom)
    assert main._ffmpeg_available(str(tmp_path), {}) is True


def test_the_real_import_is_guarded():
    """Whatever this machine has installed, looking up the bundled ffmpeg must
    never raise — an unguarded import would crash every replay and reel."""
    montage._IMAGEIO_FFMPEG = None
    assert isinstance(montage._imageio_ffmpeg(), str)
