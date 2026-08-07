"""Every factual claim in SECURITY.md, enforced.

A security doc that drifts from the code is worse than none — people will act on
it. These tests fail if the claims stop being true.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = [f for f in os.listdir(BASE) if f.endswith(".py")]


def _app_source():
    out = {}
    for f in APP:
        with open(os.path.join(BASE, f), encoding="utf-8") as fh:
            out[f] = fh.read()
    return out


def test_no_dynamic_code_execution():
    """SECURITY.md: "No eval, exec, pickle, os.system, shell=True"."""
    pat = re.compile(r"\beval\(|\bexec\(|\bpickle\.|os\.system\(|shell\s*=\s*True"
                     r"|__import__\(|\bmarshal\.")
    bad = [f"{f}:{i+1}" for f, src in _app_source().items()
           for i, line in enumerate(src.splitlines())
           if pat.search(line) and not line.lstrip().startswith("#")]
    assert not bad, f"dynamic execution introduced: {bad}"


def test_network_destinations_are_only_the_documented_four():
    """SECURITY.md lists every host the app can reach. Adding a new one must
    force an update to that list."""
    allowed = {"api.github.com", "raw.githubusercontent.com", "api.elevenlabs.io",
               "www.googleapis.com", "youtu.be", "oauth2.googleapis.com",
               "accounts.google.com", "localhost", "127.0.0.1", "docs.github.com"}
    hosts = set()
    for src in _app_source().values():
        for m in re.finditer(r"https?://([A-Za-z0-9.\-]+)", src):
            hosts.add(m.group(1))
    undocumented = {h for h in hosts if h not in allowed}
    assert not undocumented, f"undocumented network destination(s): {undocumented}"


def test_credentials_are_gitignored():
    with open(os.path.join(BASE, ".gitignore"), encoding="utf-8") as f:
        ig = f.read()
    for name in ("client_secret.json", "youtube_token.json", "elevenlabs_key.txt",
                 "*.key", "settings_override.yaml"):
        assert name in ig, name


def test_no_credential_files_are_tracked():
    import subprocess
    tracked = subprocess.run(["git", "ls-files"], cwd=BASE, capture_output=True,
                             text=True).stdout.splitlines()
    bad = [t for t in tracked
           if re.search(r"secret|token|\.key$|\.pem$|credential|\.env$", t, re.I)]
    assert not bad, f"credential-ish files are tracked: {bad}"


def test_the_documented_privacy_switches_exist():
    """SECURITY.md tells people to set these. They must be real."""
    import yaml
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("web_lan", "auto_update", "track_names", "capture_source"):
        assert key in cfg, f"SECURITY.md references {key}, which doesn't exist"


def test_security_doc_still_discloses_the_two_known_risks():
    """These are honest disclosures, not marketing. If someone deletes them the
    doc becomes misleading."""
    with open(os.path.join(BASE, "SECURITY.md"), encoding="utf-8") as f:
        doc = f.read()
    assert "no authentication" in doc.lower()
    assert "auto-update" in doc.lower() and "supply-chain" in doc.lower()
