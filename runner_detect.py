"""Which runner (shell) are you playing? Detected once per match.

When the deployment screen appears ("MOLECULAR DISASSEMBLY / TRANSFER TO
PERIMETER" — which the detection crop already sees every match), grab one
full frame and scan the OCR text for any shell or runner name. Wherever the
name sits on screen, a vocabulary match finds it.

Roster (shell -> runner) from the Runner Shells screen:
  Destroyer/Locus, Vandal/Glitch, Recon/Blackbird, Assassin/Void,
  Triage/Aux, Thief/Icon, Rook/Prototype, Sentinel/Rampart
"""

from __future__ import annotations

from rapidfuzz import fuzz

# shell name -> runner name (either may appear on screen; we record the runner)
SHELL_TO_RUNNER = {
    "destroyer": "Locus",
    "vandal": "Glitch",
    "recon": "Blackbird",
    "assassin": "Void",
    "triage": "Aux",
    "thief": "Icon",
    "rook": "Prototype",
    "sentinel": "Rampart",
}
RUNNERS = {"locus": "Locus", "glitch": "Glitch", "blackbird": "Blackbird",
           "void": "Void", "aux": "Aux", "icon": "Icon",
           "prototype": "Prototype", "rampart": "Rampart"}

# Short/common words need an exact token match — "VOID"/"ICON"/"AUX"/"ROOK"
# appear too easily inside unrelated UI text for fuzzy matching.
_EXACT_ONLY = {"void", "aux", "icon", "rook", "thief", "locus", "recon"}

_DEPLOY_MARKERS = ("molecular disassembly", "transfer to perimeter",
                   "transfer to outpost")


def is_deploy_screen(lines) -> bool:
    blob = " ".join(lines).lower()
    if len(blob) < 10:
        return False
    return any(fuzz.partial_ratio(m, blob) >= 88 for m in _DEPLOY_MARKERS)


def detect_runner(lines) -> str:
    """Scan OCR lines for a shell/runner name. Returns the runner name ('' if
    none). Longest/most-specific match wins."""
    tokens = []
    for line in lines:
        for tok in line.replace("|", " ").replace("/", " ").split():
            t = "".join(c for c in tok.lower() if c.isalpha())
            if len(t) >= 3:
                tokens.append(t)
    blob = " ".join(tokens)

    candidates = []  # (specificity, runner)
    vocab = {**SHELL_TO_RUNNER, **{k: v for k, v in RUNNERS.items()}}
    for name, runner in vocab.items():
        if name in tokens:                     # exact token: always accepted
            candidates.append((len(name) + 10, runner))
            continue
        if name in _EXACT_ONLY or len(name) <= 4:
            continue                           # short/common words: exact only
        # long names tolerate OCR slips, but only against whole tokens —
        # blob-level fuzz would let "DESTROY" (mission text) match "destroyer"
        best = max((fuzz.ratio(name, t) for t in tokens), default=0)
        if best >= 88:
            candidates.append((len(name), runner))
    if not candidates:
        return ""
    return max(candidates)[1]


def capture_runner(cfg, engine) -> str:
    """One full-frame grab + OCR, scanned for the roster. Returns runner name."""
    from exfil_stats import _grab_full
    frame = _grab_full(cfg)
    return detect_runner(engine.read_lines(frame))
