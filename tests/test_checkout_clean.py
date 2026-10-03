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

A status code alone is not enough. A file that is already ' M' before the run
and gets appended to during it is still ' M' afterwards, and that is exactly how
the _kill_count_test.csv bug looked on Stan's tree (already +8 rows, every run
added more). So each snapshot also fingerprints every dirty path's CONTENT, and
a path that is dirty in both snapshots fails when its content moved.

If the before-snapshot worked but the end-of-run one does not (git failed or
timed out), that fails too: a check that quietly did not run is not a pass.

Scope is what `git status` shows: tracked files that changed and untracked files
that appeared. Gitignored files are not checked (__pycache__ and .pytest_cache are
expected there). It is skipped where there is no git checkout; an install made by
the updater has no .git folder.
"""

import hashlib
import os
import shutil
import stat
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _same_dir(a, b):
    return (os.path.normcase(os.path.realpath(os.path.normpath(a)))
            == os.path.normcase(os.path.realpath(os.path.normpath(b))))


def _git(*args, base=BASE):
    """stdout of a read-only git command run in `base`, or None if it failed.
    --no-optional-locks: `git status` otherwise refreshes .git/index, and this
    must not fight a git command Stan is running at the same moment."""
    try:
        r = subprocess.run(["git", "--no-optional-locks", "-C", base, *args],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


# Above this size a dirty file is fingerprinted by (size, mtime) instead of read
# in full, so an untracked multi-GB recording sitting in a dev checkout does not
# add a full read to the start and the end of every run.
_HASH_LIMIT = 64 * 1024 * 1024


def _fingerprint(full):
    """What a dirty path holds right now: a sha1 of its bytes, (size, mtime_ns)
    for a file over _HASH_LIMIT, or None when it is gone, not a regular file, or
    unreadable. Both snapshots fingerprint the same way, so only a change during
    the run makes two of them differ."""
    try:
        st = os.stat(full)
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_size > _HASH_LIMIT:
            return ("size+mtime", st.st_size, st.st_mtime_ns)
        h = hashlib.sha1()
        with open(full, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def snapshot(base=BASE):
    """{path: (two-letter git status, content fingerprint)} for every changed or
    untracked path, or None when `base` is not the root of a git checkout or
    git failed."""
    if not shutil.which("git"):
        return None
    top = _git("rev-parse", "--show-toplevel", base=base)
    # A WITNESS folder unzipped inside some OTHER repo would report that repo.
    if not top or not _same_dir(top.decode("utf-8", "replace").strip(), base):
        return None
    out = _git("status", "--porcelain=v1", "-z", "--untracked-files=all", base=base)
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
        snap[path] = (code, _fingerprint(os.path.join(base, path)))
        if "R" in code or "C" in code:
            i += 1            # -z puts a rename's ORIGINAL path in the next field
    return snap


_CLEAN = ("clean", None)


def changed_paths(before, after):
    """Paths the run changed, with both states: the git status moved, or the
    status stayed the same (a file already dirty before the run) but its content
    did not."""
    out = []
    for p in sorted(set(before) | set(after)):
        b_code, b_fp = before.get(p, _CLEAN)
        a_code, a_fp = after.get(p, _CLEAN)
        if b_code != a_code:
            out.append(f"{p}: {b_code!r} -> {a_code!r}")
        elif b_fp != a_fp:
            out.append(f"{p}: {b_code!r} -> {a_code!r}, already dirty before the "
                       f"run and its content changed during it")
    return out


def end_of_run_failure(before, after):
    """The end-of-run verdict: None to pass, else the failure message.
    before None: there was no checkout to compare, so nothing to say. after None
    while before worked: git failed at the end, and passing quietly would turn
    the check off without anyone knowing."""
    if before is None:
        return None
    if after is None:
        return ("the end-of-run `git status` failed (the one before the run "
                "worked), so whether the run changed the checkout was NOT "
                "checked. Run the suite again; if it keeps failing, run "
                "`git status` yourself and see what git says.")
    changed = changed_paths(before, after)
    if not changed:
        return None
    return ("the test run changed the checkout. A test is writing into the repo "
            "instead of tmp_path (if you edited one of these yourself during the "
            "run, just run it again):\n  " + "\n  ".join(changed))


_BEFORE = snapshot()      # module import = collection = before any test runs


@pytest.fixture(scope="session")
def _checkout_after_the_run():
    yield
    if _BEFORE is None:
        return
    failure = end_of_run_failure(_BEFORE, snapshot())
    assert failure is None, failure


def test_the_run_leaves_the_checkout_as_it_found_it(_checkout_after_the_run):
    """The real assertion runs when the whole session ends (see the module
    docstring). This test exists to request the session fixture."""
    if _BEFORE is None:
        pytest.skip("not a git checkout")


def test_a_new_or_changed_file_is_reported():
    """The comparison itself, without a real repo: a file a test appended to
    (clean -> ' M') and one it created ('??') are both caught; a file that was
    already dirty before the run and stayed the same is not."""
    before = {"wip.py": (" M", "aaa")}
    after = {"wip.py": (" M", "aaa"),
             "tests/_kill_count_test.csv": (" M", "bbb"),
             "stray.csv": ("??", "ccc")}
    got = changed_paths(before, after)
    assert len(got) == 2
    assert got[0].startswith("stray.csv: 'clean' -> '??'")
    assert got[1].startswith("tests/_kill_count_test.csv: 'clean' -> ' M'")
    assert changed_paths(after, after) == []


def test_a_deleted_tracked_file_is_reported():
    assert changed_paths({}, {"config.yaml": (" D", None)}) == [
        "config.yaml: 'clean' -> ' D'"]


def test_a_file_reverted_by_the_run_is_reported():
    assert changed_paths({"wip.py": (" M", "aaa")}, {}) == [
        "wip.py: ' M' -> 'clean'"]


def test_more_writes_to_an_already_dirty_file_are_reported():
    """The status code is ' M' before and after, so only the content tells."""
    got = changed_paths({"tests/_kill_count_test.csv": (" M", "aaa")},
                        {"tests/_kill_count_test.csv": (" M", "bbb")})
    assert len(got) == 1
    assert got[0].startswith("tests/_kill_count_test.csv: ' M' -> ' M', already dirty")


def _git_in(repo, *args):
    """Set-up git for a throwaway repo in tmp_path. Identity and signing come in
    on the command line: CI runners have no identity, and this must not read or
    write anybody's git config."""
    subprocess.run(["git", "-C", repo, "-c", "user.name=witness-test",
                    "-c", "user.email=witness-test@example.invalid",
                    "-c", "commit.gpgsign=false", *args],
                   check=True, capture_output=True, timeout=60)


def test_a_real_repo_catches_an_append_to_an_already_modified_file(tmp_path):
    """The bug as it happened: a tracked file is already ' M' when the run
    starts, and a test appends to it. Also an untracked file that already
    existed and grew, and a dirty file nobody touched (must stay quiet)."""
    if not shutil.which("git"):
        pytest.skip("git not installed")
    repo = os.path.realpath(str(tmp_path))
    _git_in(repo, "init", "-q")
    for name in ("tracked.csv", "untouched.py"):
        with open(os.path.join(repo, name), "w") as f:
            f.write("header\n")
    _git_in(repo, "add", "tracked.csv", "untouched.py")
    _git_in(repo, "commit", "-q", "--no-verify", "-m", "init")
    for name, text in (("tracked.csv", "row from an earlier run\n"),
                       ("untouched.py", "# Stan mid-edit\n"),
                       ("notes.txt", "scratch\n")):
        with open(os.path.join(repo, name), "a") as f:
            f.write(text)

    before = snapshot(repo)
    assert before is not None
    assert before["tracked.csv"][0] == " M"
    assert before["notes.txt"][0] == "??"
    assert end_of_run_failure(before, snapshot(repo)) is None

    for name in ("tracked.csv", "notes.txt"):
        with open(os.path.join(repo, name), "a") as f:
            f.write("row appended by a test\n")
    after = snapshot(repo)

    assert after["tracked.csv"][0] == " M"          # the code alone cannot tell
    got = changed_paths(before, after)
    assert [line.split(":")[0] for line in got] == ["notes.txt", "tracked.csv"]
    assert "untouched.py" not in "".join(got)
    assert "tracked.csv" in end_of_run_failure(before, after)


def test_a_failed_end_of_run_snapshot_fails_instead_of_passing():
    """A before-snapshot that worked and an after-snapshot that did not used to
    return quietly, which turned the whole check off."""
    failure = end_of_run_failure({"wip.py": (" M", "aaa")}, None)
    assert failure is not None and "NOT checked" in failure
    assert end_of_run_failure(None, None) is None        # no checkout: skip
    assert end_of_run_failure({}, {}) is None


def test_a_big_dirty_file_is_fingerprinted_without_reading_it(tmp_path, monkeypatch):
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 100)
    monkeypatch.setattr(sys.modules[__name__], "_HASH_LIMIT", 10)
    fp = _fingerprint(str(path))
    assert fp[0] == "size+mtime" and fp[1] == 100
    monkeypatch.setattr(sys.modules[__name__], "_HASH_LIMIT", 1000)
    assert isinstance(_fingerprint(str(path)), str)
    assert _fingerprint(str(tmp_path / "missing")) is None
    assert _fingerprint(str(tmp_path)) is None           # a directory


def test_the_snapshot_reads_this_checkout():
    """In a git checkout the snapshot must work, or the end-of-run check is
    silently off. A worktree's .git is a file, not a folder, so test git itself
    rather than looking for .git."""
    top = _git("rev-parse", "--show-toplevel") if shutil.which("git") else None
    if not top or not _same_dir(top.decode("utf-8", "replace").strip(), BASE):
        pytest.skip("not the root of a git checkout")
    assert isinstance(snapshot(), dict)
    assert isinstance(_BEFORE, dict), "the before-the-run snapshot failed"
