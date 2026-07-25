"""Disk-space guard: a full clip drive fails silently and costs a whole night
(OBS can't write clips, every render dies), so warn before that happens."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _Web:
    def __init__(self):
        self.notices = []

    def notice(self, text, tag="alert"):
        self.notices.append(text)


def test_free_gb_reads_a_real_path(tmp_path):
    gb = main.free_gb(str(tmp_path))
    assert gb is not None and gb > 0


def test_free_gb_walks_up_to_an_existing_dir(tmp_path):
    """OBS may report a session subfolder that doesn't exist yet."""
    deep = os.path.join(str(tmp_path), "Marathon Sessions", "2026-07-25_20-00-00")
    assert main.free_gb(deep) is not None


def test_free_gb_unknown_path_is_none():
    assert main.free_gb("") is None


def test_warns_once_per_level(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "free_gb", lambda p: 6.0)     # below warn, above crit
    web, state = _Web(), {}
    cfg = {"disk_warn_gb": 10, "disk_critical_gb": 3}
    for _ in range(5):
        main.check_disk_space(cfg, str(tmp_path), web=web, state=state)
    assert len(web.notices) == 1, "warning repeated every check"


def test_critical_warns_even_after_a_low_warning(monkeypatch, tmp_path):
    web, state = _Web(), {}
    cfg = {"disk_warn_gb": 10, "disk_critical_gb": 3}
    monkeypatch.setattr(main, "free_gb", lambda p: 6.0)
    main.check_disk_space(cfg, str(tmp_path), web=web, state=state)
    monkeypatch.setattr(main, "free_gb", lambda p: 1.0)     # got worse
    main.check_disk_space(cfg, str(tmp_path), web=web, state=state)
    assert len(web.notices) == 2


def test_plenty_of_space_is_silent(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "free_gb", lambda p: 500.0)
    web, state = _Web(), {}
    main.check_disk_space({}, str(tmp_path), web=web, state=state)
    assert web.notices == []


def test_no_record_dir_is_safe():
    main.check_disk_space({}, "", web=None, state={})       # must not raise


def test_session_dict_carries_record_dir():
    """The guard reads s['record_dir'] — if the key is missing it silently
    never runs, which is how this nearly shipped."""
    import inspect
    src = inspect.getsource(main._setup_session)
    assert '"record_dir"' in src
