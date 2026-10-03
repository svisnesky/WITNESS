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


def _section(doc, heading):
    start = doc.index(heading)
    end = doc.find("\n## ", start + 1)
    return doc[start:] if end < 0 else doc[start:end]


def _bold_quotes(text):
    return re.findall(r'\*\*"([^"]+)"\*\*', text)


def test_youtube_doc_quotes_the_library_messages_both_scripts_print():
    """YOUTUBE.md step 1 told users to watch for "upload libraries not
    installed" from WITNESS *or* Check YouTube.bat. Only youtube_upload.py
    prints that; youtube_check.py (what the .bat runs) prints "upload
    libraries missing". Every quote in that step must be text one of them
    really prints, and each script's message must be quoted."""
    step = _flat(_section(_read("YOUTUBE.md"), "## 1."))
    srcs = {}
    for name in ("youtube_check.py", "youtube_upload.py"):
        path = os.path.join(BASE, name)
        if os.path.exists(path):
            srcs[name] = _read(name)
    if not srcs:
        return
    for q in _bold_quotes(step):
        assert any(q in s for s in srcs.values()), \
            f'YOUTUBE.md quotes "{q}", which no YouTube script prints'
    for name, src in srcs.items():
        for m in re.finditer(r'"(?:\s*\[youtube\]\s*)?(upload libraries [a-z ]+)',
                             src):
            msg = m.group(1).strip()
            if msg == "upload libraries installed":    # youtube_check's OK line
                continue
            assert f'"{msg}"' in step, \
                f'{name} prints "{msg}" but YOUTUBE.md step 1 doesn\'t quote it'


def test_quickstart_quotes_the_phone_line_with_its_scheme():
    """The dashboard builds the phone/iPad line as location.protocol + '//' +
    address, and main.py prints its "Live view:" address with the scheme too,
    so the window shows the address with http in front. QUICKSTART quoted it
    bare. (Scheme assembled here, not written out: no .py file in the repo
    carries a URL literal.)"""
    scheme = "http" + ":" + "//"
    doc = _flat(_read("QUICKSTART.md"))
    quoted = [q for q in _bold_quotes(doc) if "phone/iPad (same Wi-Fi)" in q]
    assert quoted, "QUICKSTART no longer quotes the phone/iPad line"
    label = "On your phone/iPad (same Wi-Fi): "
    for q in quoted:
        assert q.startswith(label + scheme), q
    web = os.path.join(BASE, "webserver.py")
    if os.path.exists(web):
        src = _read("webserver.py")
        if label in src:
            assert "location.protocol + '//'" in src, \
                "webserver.py no longer prefixes the scheme; update QUICKSTART"
