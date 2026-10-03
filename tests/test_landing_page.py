"""The public landing page (docs/index.html, served by GitHub Pages) must stay
honest and usable.

What these lock out:
- "Get it free" used to be href="#" with a click handler that only swapped its
  own text to the repo name, so a visitor from Reddit had no way to download.
- The page advertised things that don't ship: 60FPS tracking (poll_fps is 5),
  "<1s kill to clip" (kill_coalesce_seconds holds every save >= 8s), "Alien
  Master" (a paid ElevenLabs library voice), a weekly Wrapped card (CLI only),
  and four game chips with no profile in games/.
- No requirements, no "not affiliated with Bungie" line.

The page carries ~300 KB of inline base64, so it is edited surgically; the
script test below makes sure an edit never drops the boot-splash / mode-picker
/ IntersectionObserver code (losing it hides every .reveal section).
"""

import os
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(BASE, "docs", "index.html")
REPO_PATH = "/svisnesky/WITNESS"

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []          # (attrs dict, text)
        self.chips = []          # (classes, text)
        self.stack = []
        self.mismatched = []
        self.text = []
        self.scripts = []
        self._a = None
        self._chip = None
        self._in_script = False
        self._in_style = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in VOID:
            self.stack.append(tag)
        cls = (a.get("class") or "").split()
        if tag == "a":
            self._a = [a, ""]
        if tag == "span" and "chip" in cls:
            self._chip = [cls, ""]
        if tag == "script":
            self._in_script = True
            self.scripts.append("")
        if tag == "style":
            self._in_style += 1

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.mismatched.append((tag, list(self.stack[-3:])))
        else:
            self.stack.pop()
        if tag == "a" and self._a is not None:
            self.links.append((self._a[0], self._a[1].strip()))
            self._a = None
        if tag == "span" and self._chip is not None:
            self.chips.append((self._chip[0], self._chip[1].strip()))
            self._chip = None
        if tag == "script":
            self._in_script = False
        if tag == "style":
            self._in_style -= 1

    def handle_data(self, data):
        if self._in_script:
            self.scripts[-1] += data
            return
        if self._in_style:
            return
        self.text.append(data)
        if self._a is not None:
            self._a[1] += data
        if self._chip is not None:
            self._chip[1] += data


@pytest.fixture(scope="module")
def page():
    with open(PAGE, encoding="utf-8") as f:
        src = f.read()
    p = _Page()
    p.feed(src)
    p.close()
    p.src = src
    p.visible = re.sub(r"\s+", " ", " ".join(p.text))
    return p


def _gh(href):
    """(path, fragment) if href points at this repo on github.com, else None."""
    u = urlsplit(href or "")
    if u.scheme == "https" and u.netloc == "github.com" and u.path.startswith(REPO_PATH):
        return u.path, u.fragment
    return None


def test_page_tags_are_balanced(page):
    assert not page.mismatched, f"unbalanced tags: {page.mismatched[:5]}"
    assert page.stack == [], f"unclosed tags: {page.stack}"


def test_get_it_free_is_a_real_link(page):
    btn = [a for a, _ in page.links if a.get("id") == "getBtn"]
    assert len(btn) == 1
    assert _gh(btn[0].get("href")), f"getBtn goes nowhere: {btn[0].get('href')!r}"


def test_there_is_a_download(page):
    zips = [a for a, _ in page.links
            if _gh(a.get("href")) and _gh(a.get("href"))[0].endswith(".zip")]
    assert zips, "no ZIP download link on the landing page"


def test_doc_links_point_at_files_that_exist(page):
    """A 'Setup guide' / 'Is it safe?' link to a renamed file would 404."""
    seen = set()
    for a, _ in page.links:
        hit = _gh(a.get("href"))
        if hit and "/blob/main/" in hit[0]:
            rel = hit[0].split("/blob/main/", 1)[1]
            seen.add(rel)
            assert os.path.exists(os.path.join(BASE, rel)), f"dead link to {rel}"
    assert {"SECURITY.md", "QUICKSTART.md"} <= seen


def test_requirements_and_disclaimer_are_stated(page):
    t = page.visible
    for need in ("Windows 10/11", "OBS 28+", "NVIDIA GPU recommended",
                 "not affiliated with Bungie", "Is it safe?"):
        assert need in t, f"landing page no longer says {need!r}"


def test_no_claims_that_do_not_ship(page):
    t = page.visible.lower()
    for claim in ("60fps", "<1s", "alien master", "weekly wrapped",
                  "push it once", "two-minute wizard", "watches any game you point"):
        assert claim not in t, f"landing page advertises {claim!r} again"


def test_save_latency_stat_is_not_faster_than_the_coalesce_window(page):
    """Saves wait kill_coalesce_seconds after the last kill; the stat strip must
    not promise anything quicker."""
    yaml = pytest.importorskip("yaml")
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    coalesce = float(cfg.get("kill_coalesce_seconds", 8.0))
    m = re.search(r'<div class="n">~?(\d+(?:\.\d+)?)s</div><div class="l">Kill to saved clip',
                  page.src)
    assert m, "kill-to-clip stat missing or reworded; update this test with it"
    assert float(m.group(1)) >= coalesce


def test_only_games_with_a_profile_are_shown_live(page):
    profiles = {os.path.splitext(f)[0].lower()
                for f in os.listdir(os.path.join(BASE, "games")) if f.endswith(".yaml")}
    dashed = 0
    for cls, text in page.chips:
        name = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
        if "live" in cls:
            assert name in profiles, f"chip {text!r} shown live with no games/{name}.yaml"
        if "q" in cls:
            dashed += 1
    if dashed:
        assert "Dashed = no profile shipped yet" in page.visible


def test_inline_script_keeps_boot_modes_and_reveal(page):
    """Only the getBtn text-swap listener was removed. Losing the rest would
    break the mode picker and leave every .reveal section invisible."""
    js = "\n".join(page.scripts)
    for need in ("getElementById('boot')", "querySelectorAll('.mode')",
                 "IntersectionObserver", "querySelectorAll('.reveal')",
                 "getElementById('modeDesc')"):
        assert need in js, f"inline script lost {need}"
    assert "getBtn" not in js
    assert "preventDefault" not in js


def test_inline_script_survives_blocked_storage(page):
    """With site data blocked, touching sessionStorage throws SecurityError. The
    unguarded read on the script's first line stopped everything after it: the
    boot overlay never left, the mode picker was dead and no .reveal section
    ever showed. Every storage access must sit inside a try/catch."""
    js = re.sub(r"(?m)^\s*//.*$", "", "\n".join(page.scripts))   # comment lines
    assert "sessionStorage" in js, "boot splash no longer remembers it played; update this test"
    guarded = re.compile(r"try\s*\{[^{}]*\}\s*catch\s*\([^)]*\)\s*\{[^{}]*\}")
    bare = guarded.sub("", js)
    for api in ("sessionStorage", "localStorage"):
        assert api not in bare, f"{api} used outside a try/catch in the inline script"


def test_page_still_renders_without_javascript(page):
    """Without JS the boot overlay must go and every .reveal section must show.

    The <noscript> rules sit in <head>, BEFORE the main stylesheet, and use the
    same selectors (#boot, .reveal), so the later `#boot{display:flex}` and
    `.reveal{opacity:0}` won the cascade and the fallback did nothing. Checking
    that the selector strings exist passed on that broken page; what matters is
    that each fallback declaration can actually win: it carries !important, or
    the block comes after every main stylesheet."""
    m = re.search(r"<noscript>(.*?)</noscript>", page.src, re.S)
    assert m, "no <noscript> fallback"
    css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", m.group(1), re.S))
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    got = {}
    for sel, body in rules:
        for decl in body.split(";"):
            if decl.strip():
                prop, _, val = decl.partition(":")
                got[(sel.strip(), prop.strip())] = re.sub(r"\s+", "", val)
    for sel, prop, want in (("#boot", "display", "none"),
                            (".reveal", "opacity", "1"),
                            (".reveal", "transform", "none")):
        assert got.get((sel, prop), "").replace("!important", "") == want, \
            f"noscript fallback no longer sets {sel}{{{prop}:{want}}}"
    # Last main-stylesheet </style>, in coordinates where the noscript block is
    # cut out: anything at or past m.start() came after the block.
    outside = page.src[:m.start()] + page.src[m.end():]
    after_main = outside.rfind("</style>") < m.start()
    for (sel, prop), val in got.items():
        assert after_main or val.endswith("!important"), (
            f"noscript {sel}{{{prop}:{val}}} loses to the later main stylesheet; "
            "mark it !important or move the block after the last </style>")
