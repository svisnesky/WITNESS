# Quickstart

Get from zero to auto-clipped kills in about 15 minutes. One-time setup, then
it's "open the app, press START" forever (the app keeps itself updated).

## What you need

- Windows PC that runs Marathon (NVIDIA GPU recommended)
- [OBS Studio](https://obsproject.com/download) (free)
- [Python 3.12](https://www.python.org/downloads/release/python-31210/) —
  scroll down, grab **Windows installer (64-bit)**, and during install
  **check the box "Add python.exe to PATH"**
- [ffmpeg](https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip)
  (free) — powers the highlight reels and Shorts; setup step 3 below says
  where it goes

## Setup (once)

1. **Download this project**: [direct ZIP link](https://github.com/svisnesky/WITNESS/archive/refs/heads/main.zip)
   (or the green **Code** button above → **Download ZIP**) → unzip anywhere
   (e.g. Documents).
2. **Set up OBS** (2 minutes):
   - *Settings → Output → Replay Buffer*: enable it, set to ~35 seconds.
   - *Tools → WebSocket Server Settings*: check **Enable WebSocket server**.
     Either uncheck **Enable Authentication**, or copy the password into
     `config.yaml` (the `obs: password:` line).
   - Make sure a recording path is set (*Settings → Output → Recording*).
3. **ffmpeg**: open the ffmpeg zip you downloaded, and copy **`bin\ffmpeg.exe`**
   (plus `ffprobe.exe`) into the app folder next to `main.py`. Without it you
   still get clips — just no highlight reels or Shorts.
4. **Run `START Kill Recorder (Window).bat`**. The first run installs
   everything automatically (big download, several minutes — it detects an
   NVIDIA card and installs GPU acceleration by itself). Later runs start in
   seconds.

## Play

1. Open the app and press **START** — it launches OBS for you if needed.
2. Play Marathon in **borderless windowed**.
3. Optional: open the printed `http://...:8000` link on a phone/iPad on the
   same Wi-Fi — live kill feed, instant replays, match highlight reels, and a
   +1 button for anything the detector misses.
4. Press **STOP** when you're done. Clips, highlight reels, vertical Shorts,
   and a session recap are waiting in your OBS recording folder under
   `Marathon Sessions/`.

## Worried about performance?

Double-click **`4 - Benchmark (will my PC handle it).bat`** — it measures the
detection loop on YOUR machine and gives a verdict. Safe to run before any
other setup (no OBS needed; the first run installs the same things the app
needs anyway). Any 6 GB+
NVIDIA card is comfortable; the heavy video work happens after you stop
playing, not mid-fight.

## If something's off

- **No clips saved** — is the Replay Buffer enabled in OBS?
- **"Connection refused"** — enable the WebSocket server (step 2).
- **Kills not detected** — the game must be on your primary monitor,
  borderless windowed; check `logs/session_*.log` for what the OCR saw.
- Everything else: the README's Tuning section, or open an issue.
