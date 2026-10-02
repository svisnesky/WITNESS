"""Keep the test suite silent and windowless on whatever machine runs it.

Some code under test is SUPPOSED to make noise: the black-capture warning opens a
banner (overlay.py, its own Python process) and speaks "Warning. O B S is not
recording." test_main_returns_false_on_a_black_capture reached both for real, so
every `pytest` run on Stan's Mac popped a Python window and said the warning out
loud through his speakers — and the review agents run the suite too, so it
happened over and over while he wasn't using WITNESS at all.

Rather than trusting every test to remember to stub the right function, this
blocks the side effects themselves, for every test, at the boundary where they
leave the process:
  - any subprocess that would open the overlay or play/speak audio
  - winsound (how the announcer plays sound on Windows)
Everything else (ffmpeg renders, ffprobe, git) runs normally. A test that wants
to inspect the real launch call monkeypatches subprocess.Popen itself, which
overrides this guard for that test only (monkeypatch puts the guard back).
"""

import os
import subprocess

import pytest

# Programs that put sound in the room. `say` writes a file with -o, but a bare
# `say` speaks, and a test should never be able to do either by accident.
_AUDIO = {"afplay", "say", "aplay", "paplay", "ffplay", "mpg123"}


def _is_noisy(args) -> bool:
    argv = list(args) if isinstance(args, (list, tuple)) else [args]
    if not argv:
        return False
    prog = os.path.splitext(os.path.basename(str(argv[0])))[0].lower()
    return prog in _AUDIO or any(os.path.basename(str(a)) == "overlay.py" for a in argv)


class _Silenced:
    """Stands in for a process that was never started."""
    returncode = 0
    pid = 0
    stdin = stdout = stderr = None

    def __init__(self, args):
        self.args = args

    def communicate(self, *a, **k):
        return (b"", b"")

    def wait(self, *a, **k):
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass

    terminate = kill

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# Everything blocked this run, for tests that want to assert on it.
BLOCKED = []


def _install():
    """Installed ONCE for the whole run, and never undone. A per-test fixture
    is not enough: the spoken warning renders and plays on a daemon thread that
    outlives its test (the probe caught it firing during a LATER test), so it
    would slip through between tests or after the last one, when the real
    Popen had been put back."""
    real_popen = subprocess.Popen

    def guarded(args, *a, **k):
        if _is_noisy(args):
            BLOCKED.append(args)
            return _Silenced(args)
        return real_popen(args, *a, **k)

    subprocess.Popen = guarded
    try:
        import winsound
        winsound.PlaySound = lambda *a, **k: BLOCKED.append(a)
        winsound.Beep = lambda *a, **k: BLOCKED.append(a)
    except ImportError:
        pass                          # not Windows


def pytest_configure(config):
    _install()


@pytest.fixture
def blocked_side_effects():
    """What the code under test tried to open or play since this test began."""
    mark = len(BLOCKED)
    yield lambda: BLOCKED[mark:]
