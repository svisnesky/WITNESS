"""A test run must not change the checkout: everything a test writes goes to
tmp_path.

test_kill_count.py used to point session_log at tests/_kill_count_test.csv, a
TRACKED file, and _handle_kill appends a row per kill, so every `pytest` left the
working tree dirty. Nobody noticed for weeks: it took a release audit, and it
would have failed CI's clean-tree check on the first run. That one is fixed; this
is what stops the next one.

How it works: this module takes a `git status` snapshot when pytest IMPORTS it.
Collection imports every test module before any test runs, so that is the state
before the run. A session-scoped fixture takes the second snapshot when the
session ends. Two snapshots are compared, rather than demanding a clean tree, so
a checkout that was already dirty (Stan mid-edit) still passes. Only what the run
itself changed fails. If it does fail, the error is reported at the teardown of
the LAST test in the run, whichever file that is; the message names the paths.

Scope is what `git status` shows: tracked files that changed and untracked files
that appeared. Gitignored files are not checked (__pycache__ and .pytest_cache are
expected there). It is skipped where there is no git checkout; an install made by
the updater has no .git folder.
"""

import os
import shutil
import subprocess

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _same_dir(a, b):
    return (os.path.normcase(os.path.realpath(os.path.normpath(a)))
            == os.path.normcase(os.path.realpath(os.path.normpath(b))))


def _git(*args):
    """stdout of a read-only git command run in BASE, or None if it failed.
    --no-optional-locks: `git status` otherwise refreshes .git/index, and this
    must not fight a git command Stan is running at the same moment."""
    try:
        r = subprocess.run(["git", "--no-optional-locks", "-C", BASE, *args],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def snapshot():
    """{path: two-letter git status} for every changed or untracked path, or
    None when BASE is not the root of a git checkout."""
    if not shutil.which("git"):
        return None
    top = _git("rev-parse", "--show-toplevel")
    # A WITNESS folder unzipped inside some OTHER repo would report that repo.
    if not top or not _same_dir(top.decode("utf-8", "replace").strip(), BASE):
        return None
    out = _git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    if out is None:
        return None
    entries = out.decode("utf-8", "replace").split("\0")
    snap = {}
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        snap[path] = code
        if "R" in code or "C" in code:
            i += 1            # -z puts a rename's ORIGINAL path in the next field
    return snap


def changed_paths(before, after):
    """Paths whose git status differs between two snapshots, with both states."""
    return sorted(f"{p}: {before.get(p, 'clean')!r} -> {after.get(p, 'clean')!r}"
                  for p in set(before) | set(after)
                  if before.get(p) != after.get(p))


_BEFORE = snapshot()      # module import = collection = before any test runs


@pytest.fixture(scope="session")
def _checkout_after_the_run():
    yield
    if _BEFORE is None:
        return
    after = snapshot()
    if after is None:
        return
    changed = changed_paths(_BEFORE, after)
    assert not changed, (
        "the test run changed the checkout. A test is writing into the repo "
        "instead of tmp_path (if you edited one of these yourself during the "
        "run, just run it again):\n  " + "\n  ".join(changed))


def test_the_run_leaves_the_checkout_as_it_found_it(_checkout_after_the_run):
    """The real assertion runs when the whole session ends (see the module
    docstring). This test exists to request the session fixture."""
    if _BEFORE is None:
        pytest.skip("not a git checkout")


def test_a_new_or_changed_file_is_reported():
    """The comparison itself, without a real repo: a file a test appended to
    (clean -> ' M') and one it created ('??') are both caught; a file that was
    already dirty before the run and stayed the same is not."""
    before = {"wip.py": " M"}
    after = {"wip.py": " M",
             "tests/_kill_count_test.csv": " M",
             "stray.csv": "??"}
    got = changed_paths(before, after)
    assert len(got) == 2
    assert got[0].startswith("stray.csv: 'clean' -> '??'")
    assert got[1].startswith("tests/_kill_count_test.csv: 'clean' -> ' M'")
    assert changed_paths(after, after) == []


def test_a_deleted_tracked_file_is_reported():
    assert changed_paths({}, {"config.yaml": " D"}) == ["config.yaml: 'clean' -> ' D'"]


def test_the_snapshot_reads_this_checkout():
    """In a git checkout the snapshot must work, or the end-of-run check is
    silently off. A worktree's .git is a file, not a folder, so test git itself
    rather than looking for .git."""
    top = _git("rev-parse", "--show-toplevel") if shutil.which("git") else None
    if not top or not _same_dir(top.decode("utf-8", "replace").strip(), BASE):
        pytest.skip("not the root of a git checkout")
    assert isinstance(snapshot(), dict)
    assert isinstance(_BEFORE, dict), "the before-the-run snapshot failed"
