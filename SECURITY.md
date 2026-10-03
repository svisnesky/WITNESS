# Security & Privacy

WITNESS reads your screen and controls OBS, so "just trust me" isn't good enough.
This document says exactly what it does, what it sends where, and the two design
choices you should decide about before running it. Everything here can be checked
in the source.

## What it does on your machine

| | |
|---|---|
| Reads | A small region of your screen (the kill popup) several times a second, plus full-screen grabs from time to time: the squad panel about every 3 seconds while `auto_sweat` is on, the kill feed after a kill (name tracking), and the exfil and deploy screens. They are processed in memory. The only screen grabs it keeps are the exfil-screen screenshots (and decorated copies of them), saved in your session folder |
| Talks to | OBS on `localhost` (or the `obs: host` you set), over the official obs-websocket API |
| Writes | Only inside its own folder and your OBS recording folder, plus EasyOCR's model files in your user folder (`.EasyOCR`). Dashboard requests can only reach session folders inside `Marathon Sessions` |
| Sends out | Your announcer name and some opponents' gamertags, inside the narration text, to Microsoft's speech service when a voiced reel or report is built (on by default; `tts_online: false` turns it off). Nothing else about you unless you turn it on. See "Network" below |

It does **not** read or write game memory, inject into any process, hook input,
or modify game files. It is a screenshot-and-OCR tool. The game is a black box to
it — it can only read pixels that are already on your screen.

**Keep OBS WebSocket authentication ON.** obs-websocket accepts connections from
your network, not just this PC. With authentication off, anyone on your Wi-Fi can
control OBS: start and stop recording, switch scenes, read your settings. Put the
password from OBS (*Tools → WebSocket Server Settings → Show Connect Info*) in
`config.yaml` under `obs: password:` instead.

## Network: everything it can reach

This is every host WITNESS contacts, including traffic made by the libraries it
uses. A grep for URLs (`grep -rhoE 'https?://[A-Za-z0-9.-]+' *.py`) only finds
the ones written in WITNESS's own code, so the library traffic is listed here by
hand, and `tests/test_security_claims.py` checks that it stays listed.

| Destination | When | Sends |
|---|---|---|
| `api.github.com` / `raw.githubusercontent.com` | Every launch if `auto_update: true`, the dashboard's **Check for update** button, and `Update WITNESS.bat` | Nothing about you — an anonymous public read |
| `speech.platform.bing.com` (Microsoft's neural voice, via the `edge-tts` package) | **On by default**, whenever a voiced reel or report is built: the announcer on match reels, the WITNESS Report and the session-reel voiceover, and voiced call-outs if you turn `announcer_medals` on. `tts_online: false` in `config.yaml` turns it off and uses the offline Windows voice instead | The narration text. It includes your `announcer_player_name`; in the WITNESS Report, the gamertags of opponents read off the kill feed (with `track_names: true`); and, with `announcer_medals` on, the name of a `streamer_watchlist` player in that call-out ("You just killed ...!") |
| `api.elevenlabs.io` | Only if you add your own ElevenLabs key (`elevenlabs_key.txt`, or the `ELEVENLABS_API_KEY` environment variable). `tts_online` does not affect it | The same narration text, to synthesise |
| `github.com` (which hands the download off to `release-assets.githubusercontent.com`) | The first time the OCR engine starts (and again if its model files are deleted): EasyOCR downloads its model files | Nothing about you — a file download |
| `www.googleapis.com` / `oauth2.googleapis.com` / `accounts.google.com` (YouTube) | Only if you set up OAuth **and** turn on an upload toggle, or run `Check YouTube.bat` | The video you chose to upload |
| `youtu.be` | — | Only appears in a printed result URL |

**Installing and updating** also download packages, from `pypi.org` /
`files.pythonhosted.org` and, on a PC with an NVIDIA card, `download.pytorch.org`
(the GPU build of PyTorch). That happens during first-time setup, when an update
adds a package to `requirements.txt`, and if the window app finds `pywebview`
missing and installs it. These are package downloads; nothing about you is sent.

**One local-network announcement.** With `bonjour_discovery: true` (the default,
and only when `web_lan` is on), WITNESS announces itself on your *local* network
over mDNS/Bonjour as `_witness._tcp` so the iOS companion app can find the PC
without you typing an address — the same mechanism printers and AirPlay use. The
announcement carries the PC's LAN address, the dashboard port, the PC's hostname,
and the app version.
It never leaves your LAN and contains nothing about your sessions. Turn it off
with `bonjour_discovery: false` (discovery.py).

**No telemetry, no analytics, no crash reporting, no phoning home.** Nothing goes
anywhere that isn't in the table above. The only row that sends anything about
you by default is the neural voice; everything else that sends data is off until
you set it up.

Every upload flag defaults to `false` and there's a test that enforces it
(`tests/test_call_contracts.py::test_youtube_uploads_all_default_off`), so a
future change can't silently start publishing.

## Two things you should decide about

These are deliberate design choices, not oversights — but they're real, and you
should make them knowingly.

### 1. The dashboard has no password

By default (`web_lan: true`) the dashboard binds `0.0.0.0:8000` so your iPad can
reach it. **There is no authentication.** Anyone on the same Wi-Fi can open it
and use every control: start/stop a session, add kills, rebuild reels, change
settings (including the four YouTube auto-upload toggles), and trigger an update.
The update button only pulls this repo's code, and it does nothing when
`auto_update: false`.

On a home network that's usually fine. On shared, dorm, office, or event Wi-Fi it
is not.

**Web pages in your own browser can't use it.** Every request has to name this
PC: an IP address, `localhost`, a `.local` name, or the PC's own name (also
as your router names it, such as `my-pc.lan` or `my-pc.home.arpa`). A POST whose Origin (or Referer) names another site
is refused. These Host and Origin checks block cross-site requests (a web page you visit
quietly posting to `localhost:8000`) and DNS rebinding. They are not a password:
they do nothing about another device on your Wi-Fi.

**To keep other devices out**, in `config.yaml`:

```yaml
web_lan: false      # the dashboard listens on 127.0.0.1 only
```

Other devices then can't connect at all. You lose the iPad view and the Bonjour
announcement, and keep everything else.

### 2. Auto-update runs code from GitHub

With `auto_update: true` (the default), every launch fetches the latest files
from this repo's `main` branch and relaunches on them. When an update adds a
package to `requirements.txt`, that package is installed from PyPI too. That is
genuinely convenient and it is genuinely a supply-chain trust decision: **you are
trusting this repo's owner and GitHub account on every single launch.** If that
account were compromised, malicious code would reach you automatically.

Nothing about that is unique to WITNESS — it's true of any auto-updating app —
but most don't say so out loud.

**To review changes yourself before running them:**

```yaml
auto_update: false
```

Then, when you want to update, read the new commits at
github.com/svisnesky/WITNESS/commits/main and double-click
**`Update WITNESS.bat`** (or `git pull` if you cloned the repo). While this is
off, launches and the dashboard's **Check for update** button don't update.

## What was checked, and how

Run these yourself; they're the same commands used to produce this document.

**No dynamic code execution.** No `eval`, `exec`, `pickle`, `os.system`,
`shell=True`, or `__import__` anywhere in the app:

```bash
grep -rnE '\beval\(|\bexec\(|pickle\.|os\.system\(|shell=True|__import__\(' *.py
```

**No secrets, in any commit, ever.** The full history was scanned for key-shaped
strings and credential filenames:

```bash
git log --all -p | grep -E '^\+' | grep -iE 'sk_[A-Za-z0-9]{20}|ghp_[A-Za-z0-9]{20}|AIza[A-Za-z0-9_-]{30}|BEGIN .*PRIVATE KEY'
git log --all --pretty=format: --name-only --diff-filter=A | sort -u | grep -iE 'secret|token|\.key$|\.pem$|credential'
```

Both return nothing. Your own credentials never enter the repo either —
`client_secret.json`, `youtube_token.json`, `elevenlabs_key.txt`, `*.key` and
your `settings_override.yaml` are all gitignored, with a test enforcing it.

**Dependencies** are all mainstream, widely-used packages: PyYAML, rapidfuzz,
mss, numpy, opencv-python, easyocr (and PyTorch), pytesseract, obsws-python,
Pillow, edge-tts, pystray, the Google API clients, pywebview, zeroconf and
imageio-ffmpeg. Full list in `requirements.txt`. Nothing obscure, nothing
vendored, no binaries in the repo (imageio-ffmpeg brings its own ffmpeg build
when pip installs it).

## Anti-cheat

WITNESS takes screenshots and reads OBS's websocket. It does not touch the game
process. That is the same category of behaviour as OBS, Discord overlay, or any
capture card, and there is no known anti-cheat that treats screen capture as a
violation.

That said: **no anti-cheat vendor has audited this, and neither has Bungie.** If
you compete for money or care about your account, the conservative choice is to
use the `capture_source: obs_virtualcam` mode, where OBS does the capturing and
WITNESS only reads a virtual webcam device — WITNESS itself never screenshots
anything.

## Other people's data

Name tracking (`track_names: true`) reads gamertags off the kill feed and writes
them to `stats/encounters.csv` **on your machine**. That file is never uploaded.
Two things do leave the PC, both as text to be spoken. The WITNESS Report can
name an opponent or two ("One name keeps coming up — ..."), and with
`announcer_medals` on, killing or being downed by a player on your
`streamer_watchlist` voices a call-out with their name in it ("You just killed
...!"). With the neural voice on (the default), that narration text is sent to
Microsoft's speech service to be voiced. Set `tts_online: false` to keep both,
and your own announcer name, on this PC.

If you enable YouTube uploads, other players' names may appear in the footage as
they would in any clip you'd post manually.

If your party chat is mixed into your OBS audio, it will be in your clips — and
therefore in anything you upload. Your friends probably haven't thought about
that. Worth a conversation before you turn on automatic uploading.

Session logs (`logs/session_*.log`) hold other players' gamertags, your Windows
username inside clip paths, and your LAN address. Read them before you paste one
anywhere public; the bug-report form says what to redact.

Turn name tracking off entirely with `track_names: false`.

## Supported versions

Installs track the `main` branch and update themselves, so fixes go into the
latest version only. Your version is in the bottom-right corner of the dashboard
(`WITNESS · 0.1.0 (abc1234)`); the part in brackets is the exact build.

## Reporting a vulnerability

Please don't put security problems in a public issue. Report them privately
through GitHub: the repo's **Security** tab → **Report a vulnerability**
(github.com/svisnesky/WITNESS/security/advisories/new). Only the owner can see
the report. If that button isn't there, open an issue that just asks for a
security contact, with no details, and the owner will get in touch.

Everything else: open an issue at github.com/svisnesky/WITNESS/issues/new/choose.
