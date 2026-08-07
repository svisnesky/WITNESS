# Security & Privacy

WITNESS reads your screen and controls OBS, so "just trust me" isn't good enough.
This document says exactly what it does, what it sends where, and the two design
choices you should decide about before running it. Everything here is verifiable
in the source — file and line references included.

## What it does on your machine

| | |
|---|---|
| Reads | A small crop of your screen (the kill popup region), a few times a second |
| Talks to | OBS on `localhost`, over the official obs-websocket API |
| Writes | Only inside its own folder and your OBS clip folder |
| Sends out | **Nothing, unless you turn it on.** See "Network" below |

It does **not** read or write game memory, inject into any process, hook input,
or modify game files. It is a screenshot-and-OCR tool. The game is a black box to
it — it can only read pixels that are already on your screen.

## Network: everything it can reach

Four destinations exist in the entire codebase (`grep -rhoE 'https?://' *.py`):

| Destination | When | Sends |
|---|---|---|
| `api.github.com` / `raw.githubusercontent.com` | Every launch, if `auto_update: true` | Nothing about you — an anonymous public read |
| `api.elevenlabs.io` | Only if you add your own API key | The call-out text to synthesise |
| `googleapis.com` (YouTube) | Only if you set up OAuth **and** enable an upload flag | The video you chose to upload |
| `youtu.be` | — | Only appears in a printed result URL |

**No telemetry, no analytics, no crash reporting, no phoning home.** There is
nowhere for your data to go that you did not configure yourself.

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
settings, and trigger an update.

On a home network that's usually fine. On shared, dorm, office, or event Wi-Fi it
is not.

**To lock it to this machine only**, in `config.yaml`:

```yaml
web_lan: false      # dashboard reachable only at 127.0.0.1
```

You lose the iPad view and keep everything else.

### 2. Auto-update runs code from GitHub

With `auto_update: true` (the default), every launch fetches the latest files
from this repo and relaunches on them. That is genuinely convenient and it is
genuinely a supply-chain trust decision: **you are trusting this repo's owner and
GitHub account on every single launch.** If that account were compromised,
malicious code would reach you automatically.

Nothing about that is unique to WITNESS — it's true of any auto-updating app —
but most don't say so out loud.

**To review changes yourself before running them:**

```yaml
auto_update: false
```

Then update with `git pull` when you've read the diff.

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

**Dependencies** are all mainstream, widely-audited packages: PyYAML, rapidfuzz,
mss, numpy, opencv-python, easyocr, obsws-python, Pillow, edge-tts, pystray,
the Google API clients, pywebview. Full list in `requirements.txt`. Nothing
obscure, nothing vendored, no binaries in the repo.

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
them to `stats/encounters.csv` **on your machine only**. They are never uploaded.
If you enable YouTube uploads, other players' names may appear in the footage as
they would in any clip you'd post manually.

If your party chat is mixed into your OBS audio, it will be in your clips — and
therefore in anything you upload. Your friends probably haven't thought about
that. Worth a conversation before you turn on automatic uploading.

Turn name tracking off entirely with `track_names: false`.

## Reporting something

Found a problem? Open an issue, or for anything sensitive, contact the repo owner
directly rather than filing publicly.
