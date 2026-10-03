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


def test_network_destinations_are_only_the_documented_ones():
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


def _security_doc():
    with open(os.path.join(BASE, "SECURITY.md"), encoding="utf-8") as f:
        return f.read()


def _network_section():
    """SECURITY.md from '## Network' up to the next '## ' heading."""
    doc = _security_doc()
    start = doc.index("## Network")
    end = doc.find("\n## ", start + 1)
    return doc[start:] if end < 0 else doc[start:end]


def _requirements():
    with open(os.path.join(BASE, "requirements.txt"), encoding="utf-8") as f:
        names = []
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                names.append(re.split(r"[<>=!~\[ ;]", line, 1)[0].lower())
        return names


def _mentions_host(text, host):
    """`host` as a whole host name: 'github.com' must not be satisfied by
    'api.github.com' alone."""
    return re.search(r"(?<![A-Za-z0-9.\-])" + re.escape(host)
                     + r"(?![A-Za-z0-9\-])", text) is not None


def test_url_literal_hosts_are_named_in_security_md():
    """The other direction of the allowlist above: every host that appears as
    a URL in the app's own code is named in SECURITY.md's network section."""
    net = _network_section()
    hosts = set()
    for src in _app_source().values():
        for m in re.finditer(r"https?://([A-Za-z0-9.\-]+)", src):
            hosts.add(m.group(1))
    missing = sorted(h for h in hosts - {"localhost", "127.0.0.1"}
                     if not _mentions_host(net, h))
    assert not missing, f"SECURITY.md's network table doesn't name: {missing}"


def test_edge_tts_traffic_is_disclosed():
    """edge-tts sends the narration text (the announcer name, and opponents'
    gamertags in the WITNESS Report) to Microsoft. A URL grep can't see it: the
    host lives inside the library. SECURITY.md said nothing left the PC by
    default while this was on by default."""
    if "edge-tts" not in _requirements():
        return
    assert _mentions_host(_network_section(), "speech.platform.bing.com")


def test_library_traffic_is_disclosed():
    """Hosts reached from inside a dependency, which the URL tests above
    can't see. Each is checked only while its package is a requirement."""
    net = _network_section()
    reqs = _requirements()
    expect = {
        "easyocr": "github.com",                    # first-run model download
        "google-api-python-client": "www.googleapis.com",
    }
    missing = [f"{pkg} -> {host}" for pkg, host in expect.items()
               if pkg in reqs and not _mentions_host(net, host)]
    assert not missing, f"undisclosed library traffic: {missing}"


def test_runtime_pip_install_is_disclosed():
    """Any app module that runs pip itself (the window app's pywebview
    install, the updater installing new requirements) reaches PyPI."""
    pip_call = re.compile(r"""["']-m["']\s*,\s*["']pip["']""")
    runs_pip = [f for f, src in _app_source().items() if pip_call.search(src)]
    if runs_pip:
        assert _mentions_host(_network_section(), "pypi.org"), runs_pip


def test_elevenlabs_env_var_is_disclosed():
    """announcer.py also reads the key from an environment variable, so a
    key can be in use without elevenlabs_key.txt in the folder."""
    if any("ELEVENLABS_API_KEY" in src for src in _app_source().values()):
        assert "ELEVENLABS_API_KEY" in _network_section()


def test_security_doc_says_keep_obs_auth_on():
    """QUICKSTART used to offer turning OBS WebSocket auth off. obs-websocket
    listens on the network, so SECURITY.md has to say not to."""
    doc = _security_doc().lower()
    assert "keep obs websocket authentication on" in doc


def test_security_doc_points_zip_users_at_a_real_update_path():
    """'git pull' alone doesn't exist for a ZIP install (QUICKSTART's path)."""
    doc = _security_doc()
    assert "Update WITNESS.bat" in doc
    assert os.path.exists(os.path.join(BASE, "Update WITNESS.bat"))


def test_security_doc_links_point_at_this_repo():
    """Every github.com/<owner>/<repo> path in SECURITY.md is this repo."""
    doc = _security_doc()
    for m in re.finditer(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)", doc):
        assert m.group(1) == "svisnesky" and m.group(2) == "WITNESS", m.group(0)


def test_bonjour_announcement_stays_disclosed():
    """discovery.py announces the PC on the LAN. The paragraph saying so, and
    its off switch, must not be lost in a rewrite of the network section."""
    if not os.path.exists(os.path.join(BASE, "discovery.py")):
        return
    net = _network_section()
    assert "_witness._tcp" in net and "bonjour_discovery: false" in net
