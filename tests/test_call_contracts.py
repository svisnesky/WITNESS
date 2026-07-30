"""Static contract check: every keyword argument main.py passes to another
module must actually exist in that function's signature.

Why this file exists. On 2026-07-29 a whole session's reels were destroyed by
this one line:

    build_match_reel(..., **_reel_cut_kwargs(cfg))

_reel_cut_kwargs() had gained a 'context_preroll' key that build_match_reel()
never accepted. Python only raises on the CALL, and the call only happens at the
end of a real match — so nothing caught it until Stan had played for two and a
half hours. Every unit test passed the whole time, because no test ever exercised
the end-of-session build path.

This walks main.py's AST and checks the keywords instead of the behaviour, so it
costs nothing and covers every cross-module call at once.
"""

import ast
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules main.py calls into for the heavy end-of-session work.
CHECKED_MODULES = ("match_reel", "montage", "shorts", "exfil_stats",
                   "encounters", "heat", "tidy", "witness_report", "loot_goblin")


def _signatures():
    """{(module, func): {parameter names}} for every checked module."""
    out = {}
    for name in CHECKED_MODULES:
        try:
            mod = __import__(name)
        except Exception:
            continue
        for fname, fn in vars(mod).items():
            if not callable(fn) or getattr(fn, "__module__", None) != name:
                continue
            try:
                out[(name, fname)] = inspect.signature(fn)
            except (TypeError, ValueError):
                pass
    return out


def _keyword_mismatches(path: str, sigs: dict):
    tree = ast.parse(open(path, encoding="utf-8").read())
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        mod = getattr(node.func.value, "id", None)
        key = (mod, node.func.attr)
        if key not in sigs:
            continue
        params = sigs[key].parameters
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            continue                      # accepts **kwargs, anything goes
        for kw in node.keywords:
            if kw.arg and kw.arg not in params:
                bad.append(f"{os.path.basename(path)}:{node.lineno} "
                           f"{mod}.{node.func.attr}() has no parameter "
                           f"{kw.arg!r}")
    return bad


def test_no_keyword_argument_mismatches_from_main():
    sigs = _signatures()
    assert sigs, "no signatures collected — the import list is wrong"
    bad = _keyword_mismatches(os.path.join(BASE, "main.py"), sigs)
    assert not bad, "keyword arguments that would raise TypeError at runtime:\n" \
                    + "\n".join(bad)


def test_reel_cut_kwargs_match_every_consumer():
    """_reel_cut_kwargs() is splatted into build_match_reel at three separate
    call sites (match reel, Play of the Night, session reel). All three broke
    together last time, so assert the contract directly."""
    import main
    import match_reel

    produced = set(main._reel_cut_kwargs({}))
    accepted = set(inspect.signature(match_reel.build_match_reel).parameters)
    assert produced <= accepted, sorted(produced - accepted)


def test_every_module_imports_cleanly():
    """A syntax or import error in a module used only at end-of-session would
    otherwise surface for the first time after a long play session."""
    failures = []
    for name in CHECKED_MODULES + ("main", "webserver", "detector", "ocr",
                                   "obs_client", "teach", "updater"):
        try:
            __import__(name)
        except Exception as e:
            failures.append(f"{name}: {type(e).__name__}: {e}")
    assert not failures, "\n".join(failures)
