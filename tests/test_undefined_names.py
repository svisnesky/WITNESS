"""Catch names used but never defined or imported — the bug class that shipped
--rebuild broken.

_resolve_session_dir() called OBSClient(cfg), but OBSClient is only imported
LOCALLY inside two other functions, so at runtime the name was simply undefined:

    (couldn't ask OBS for the clip folder: name 'OBSClient' is not defined)

Two things hid it: the `except Exception` swallowed the NameError as if it were an
OBS connection problem, and no test ran that path. A static check finds this in
every function — including the ones only reachable after hours of play.

Uses pyflakes rather than a hand-rolled AST walk. My first attempt at the latter
reported __file__ and comprehension targets as undefined; a checker that cries
wolf gets ignored, which is worse than not having one.

Coverage: every top-level .py, found by listing the folder. This used to be a
hand-written list, and a release audit found it had silently missed 15 modules
(gui, overlay, calibrate, blackcheck, canary, clip_index, clutch, export_reel,
matchcard, runner_detect, stats, test_popup_sim, voice_check, wrapped,
youtube_check): every module added after the list was written. tests/ and tools/
are checked too.

Only the classes that RAISE when the line runs fail the test (an undefined name,
an unbound local, a % or .format() that cannot work). Unused imports, unused
variables and f-strings without placeholders are style, not crashes; CI prints
them (`python tests/test_undefined_names.py`) without failing on them.
"""

import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every top-level module, by listing the folder (the same pattern
# test_security_claims.py uses), so a new module is covered the day it lands.
MODULES = sorted(f[:-3] for f in os.listdir(BASE) if f.endswith(".py"))

# Scripts that ship or run beside the app but are not top-level modules.
SUPPORT_DIRS = ("tests", "tools")

# pyflakes message classes that mean "this raises when the line runs" (or the
# file does not compile at all). Looked up by name so a pyflakes release that
# drops one cannot break the import; _fatal_classes() refuses to run if the core
# ones are missing, so a rename cannot quietly turn this check into a no-op.
FATAL = (
    "UndefinedName", "UndefinedLocal", "UndefinedExport",
    # compile errors pyflakes reports as messages
    "DuplicateArgument", "ReturnOutsideFunction", "YieldOutsideFunction",
    "ContinueOutsideLoop", "BreakOutsideLoop", "DefaultExceptNotLast",
    "TwoStarredExpressions", "TooManyExpressionsInStarredAssignment",
    "LateFutureImport", "FutureFeatureNotDefined", "ImportStarNotPermitted",
    # `"%s %s" % (a,)` and `"{0} {}".format(...)`: TypeError/KeyError/ValueError
    "PercentFormatInvalidFormat", "PercentFormatMixedPositionalAndNamed",
    "PercentFormatUnsupportedFormatCharacter", "PercentFormatPositionalCountMismatch",
    "PercentFormatMissingArgument", "PercentFormatExpectedMapping",
    "PercentFormatExpectedSequence", "PercentFormatStarRequiresSequence",
    "StringDotFormatInvalidFormat", "StringDotFormatMissingArgument",
    "StringDotFormatMixingAutomatic",
    # `print >> f, x` is a TypeError on Python 3; `raise NotImplemented` too
    "InvalidPrintSyntax", "RaiseNotImplemented",
)


def _paths():
    paths = [os.path.join(BASE, f"{m}.py") for m in MODULES]
    for d in SUPPORT_DIRS:
        full = os.path.join(BASE, d)
        if os.path.isdir(full):
            paths += [os.path.join(full, f) for f in sorted(os.listdir(full))
                      if f.endswith(".py")]
    return paths


def _fatal_classes():
    from pyflakes import messages
    found = tuple(c for c in (getattr(messages, n, None) for n in FATAL) if c)
    missing = [n for n in ("UndefinedName", "UndefinedLocal")
               if not getattr(messages, n, None)]
    if missing:
        raise RuntimeError(f"pyflakes no longer has {missing}; update FATAL in "
                           f"tests/test_undefined_names.py")
    return found


def _rel(path):
    """path relative to the repo, for readable output; anything outside the
    repo stays absolute. relpath raises across Windows drives, and GitHub's
    Windows runner checks out to D: while pytest's tmp_path is on C:."""
    try:
        rel = os.path.relpath(path, BASE)
    except ValueError:
        return path
    return path if rel.startswith(os.pardir) else rel


class _Collect:
    """A pyflakes reporter that keeps every line and sorts out the fatal ones."""

    def __init__(self, fatal_classes):
        self.fatal_classes = fatal_classes
        self.fatal = []
        self.everything = []

    def _add(self, line, fatal):
        self.everything.append(line)
        if fatal:
            self.fatal.append(line)

    def unexpectedError(self, filename, msg):
        self._add(f"{_rel(filename)}: could not check: {msg}", True)

    def syntaxError(self, filename, msg, lineno, offset, text):
        self._add(f"{_rel(filename)}:{lineno}: syntax error: {msg}", True)

    def flake(self, message):
        line = str(message)
        if line.startswith(message.filename):
            line = _rel(message.filename) + line[len(message.filename):]
        self._add(line, isinstance(message, self.fatal_classes))


def check(paths):
    """pyflakes over `paths`: (lines that crash at runtime, every line)."""
    from pyflakes import api
    rep = _Collect(_fatal_classes())
    for p in paths:
        api.checkPath(p, rep)
    return rep.fatal, rep.everything


def test_every_top_level_module_is_checked():
    """The old hand-written list missed 15 modules. Listing the folder means a
    module cannot be left out, and gui/overlay (two of the 15) prove it."""
    on_disk = {f for f in os.listdir(BASE) if f.endswith(".py")}
    checked = {os.path.basename(p) for p in _paths()
               if os.path.dirname(p) == BASE}
    assert checked == on_disk
    assert {"main.py", "gui.py", "overlay.py"} <= checked
    assert os.path.abspath(__file__) in [os.path.abspath(p) for p in _paths()]


def test_no_undefined_names():
    pytest.importorskip("pyflakes")       # pip install -r requirements-dev.txt
    fatal, _ = check(_paths())
    assert not fatal, "\n".join(fatal)


def test_the_checker_catches_what_it_claims(tmp_path):
    """A checker that never fires looks exactly like a clean codebase. Feed it
    one of each kind and make sure it fires on the crashes and not on style."""
    pytest.importorskip("pyflakes")       # pip install -r requirements-dev.txt
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import os\n"                                   # unused import: style
        "total = 0\n"
        "def resolve():\n"
        "    return OBSClient()\n"                      # the --rebuild bug
        "def count():\n"
        "    total += 1\n"                              # UnboundLocalError
        "    return total\n"
        "def label(a):\n"
        "    return '%s of %s' % (a,)\n"                # TypeError
        "def hint():\n"
        "    return f'no placeholders'\n",              # style
        encoding="utf-8")
    fatal, everything = check([str(bad)])
    text = "\n".join(fatal)
    assert "undefined name 'OBSClient'" in text
    assert "local variable 'total'" in text and "before assignment" in text
    assert "2 placeholder(s) but 1 substitution(s)" in text
    assert len(everything) > len(fatal), "style warnings are listed, not fatal"
    assert not any("imported but unused" in ln for ln in fatal)
    assert not any("placeholders" in ln and "f-string" in ln for ln in fatal)


def test_every_module_compiles():
    """A syntax error in a module used only at end-of-session would otherwise
    surface for the first time after a long play session."""
    import py_compile
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for i, path in enumerate(_paths()):
            py_compile.compile(path, doraise=True,
                               cfile=os.path.join(d, f"{i}.pyc"))


def test_rebuild_does_not_need_obs_running():
    """Rebuilding works on files already on disk, so it must not require a live
    OBS connection — it reads the cached record folder first, and only falls back
    to OBS with the import actually in scope."""
    import inspect

    sys.path.insert(0, BASE)
    import main

    src = inspect.getsource(main._resolve_session_dir)
    assert "cached_record_dir()" in src, "must work with OBS closed"
    assert "from obs_client import OBSClient" in src, "fallback needs the import"


if __name__ == "__main__":
    # CI's pyflakes step. Prints EVERY pyflakes line so style warnings stay
    # visible, and fails only on the ones that crash (the same rule as the test).
    # A missing pyflakes is an ImportError here, i.e. a red step, never a skip.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")   # cp1252 console on Windows
    paths = _paths()
    fatal, everything = check(paths)
    for line in everything:
        print(line)
    print(f"pyflakes: {len(paths)} files, {len(everything)} warnings, "
          f"{len(fatal)} that would crash at runtime")
    if fatal:
        print("\nWOULD CRASH:")
        print("\n".join(fatal))
    sys.exit(1 if fatal else 0)
