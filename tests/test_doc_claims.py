"""Claims the user-facing docs make about the code, enforced.

Each one was found wrong in review: a doc quoting text the program never
prints, or describing a process the repo doesn't actually run. A reader
acts on these (they search for the quoted message, or trust a gate that
isn't there), so they're tested like code.
"""

import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()


def _flat(text):
    """Collapse wrapped Markdown lines so a phrase can be searched whole."""
    return re.sub(r"\s+", " ", text)


def test_contributing_describes_ci_as_it_runs():
    """The workflow (.github/workflows/tests.yml) triggers on pushes to main,
    pull requests and workflow_dispatch, and its own header says it reports
    rather than gates. CONTRIBUTING once said it ran 'for every push' and
    'has to pass before anything lands on main', which read as an enforced
    gate on every branch."""
    doc = _flat(_read("CONTRIBUTING.md"))
    assert "every push" not in doc
    assert "has to pass before anything lands" not in doc
    assert "pull requests and pushes to `main`" in doc
    assert "Nothing enforces it" in doc
    wf = os.path.join(BASE, ".github", "workflows", "tests.yml")
    if os.path.exists(wf):
        with open(wf, encoding="utf-8") as f:
            y = f.read()
        assert re.search(r"push:\s*\n\s*branches:\s*\[\s*main\s*\]", y), \
            "workflow push trigger changed; update CONTRIBUTING.md"
        assert "pull_request" in y
