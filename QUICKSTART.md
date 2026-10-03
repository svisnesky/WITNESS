# Quickstart

Get from zero to auto-clipped kills in about 15 minutes of your time, plus the
first-run download. One-time setup, then it's "open the app, press START"
forever. The app checks for updates every time it opens and updates itself.

## What you need

- A Windows 10/11 PC that runs Marathon (NVIDIA GPU recommended; keep its
  driver up to date)
- A 16:9 display (1080p, 1440p, 4K). Ultrawide is untested.
- [OBS Studio](https://obsproject.com/download) 28 or newer (free)
- [Python 3.12](https://www.python.org/downloads/release/python-31210/):
  scroll down, grab **Windows installer (64-bit)**, and on the installer's
  first screen **tick "Add python.exe to PATH"**. Python 3.13 and newer won't
  work yet (the OCR engine doesn't support them); 3.12 can sit alongside a
  newer one.
- Several GB of free disk space for the first-run install

## Setup (once)

1. **Download and extract it.**
   - [Direct ZIP link](https://github.com/svisnesky/WITNESS/archive/refs/heads/main.zip)
     (or the green **Code** button above → **Download ZIP**).
   - Before extracting, right-click the ZIP → **Properties** → tick
     **Unblock** at the bottom of the General tab → **OK**. This stops a
     Windows security prompt on every `.bat` file.
   - Extract it to a plain local folder such as `C:\Games\`, so you end up
     with `C:\Games\WITNESS-main\main.py`. If Extract All makes
     `WITNESS-main\WITNESS-main`, move the inner folder up one level.
   - **Don't use Documents, Desktop or Downloads.** On many Windows 11 PCs
     those sync to OneDrive, and the first run installs several GB into this
     folder that OneDrive will try to upload.
2. **Set up OBS** (about 5 minutes):
   - **Add a capture source.** A new OBS scene is empty and records black.
     In the **Sources** panel click **+** → **Display Capture** (pick the
     monitor Marathon runs on) or **Game Capture**. Check that the OBS preview
     shows your screen.
   - *Settings → Output → Replay Buffer*: enable it and set the maximum replay
     time to about **35 seconds**.
   - *Tools → WebSocket Server Settings*: tick **Enable WebSocket server**.
     Keep **Enable Authentication** ticked. Click **Show Connect Info**, copy
     the **Server Password**, and paste it into `config.yaml` (in the app
     folder) on the `password:` line under `obs:`, between the quotes.
     With authentication off, anyone on your Wi-Fi can control your OBS.
   - Make sure a recording path is set (*Settings → Output → Recording*).
3. **ffmpeg (optional).** Setup installs a basic ffmpeg for you, so you can
   skip this. If you already have ffmpeg on your PATH, WITNESS uses that. To
   use a full build (it can render reels faster on an NVIDIA card), download
   [ffmpeg](https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip),
   and copy **`bin\ffmpeg.exe`** (plus `ffprobe.exe`) from that zip into the
   app folder next to `main.py`. A copy there is used before the one on
   PATH or the bundled one.
   If WITNESS ever says **"ffmpeg not found"**, clips still save, but you get
   no instant replays on your phone/iPad, no highlight reels or Shorts, and no
   voiced call-outs until ffmpeg is there.
4. **Tell it your name.** In `config.yaml`, set `gamertag:` to your **exact**
   in-game name, e.g. `gamertag: "YOURNAME"`. Optionally set
   `announcer_player_name:` to how the announcer should say it (spell it
   phonetically if it reads oddly). With no gamertag, the Menace Report,
   kill-name chyrons and streamer alerts stay off, and the session log says so.
5. **Run `START Kill Recorder (Window).bat`.**
   - If you see **"Windows protected your PC"**, click **More info → Run
     anyway** (or Unblock the ZIP, step 1).
   - The first run finds Python 3.12 and installs everything automatically:
     a download of several GB that can take a while. It detects an NVIDIA
     card and installs GPU acceleration by itself. Leave the window open
     until it finishes. Later runs start in seconds.
   - If the setup window says the folder is inside OneDrive, close it and
     move the folder (step 1).
6. **(Optional) Double-click `Create Desktop Shortcut.bat`** once. It puts a
   **WITNESS** icon on your desktop so you can launch it without digging
   through the folder.

## Play

1. **Open the app.** It checks GitHub for updates as it opens and restarts
   itself on the new version when there is one. The dashboard also has a
   **Check for update** button. The first time it opens, Windows may ask
   whether Python can use the network: allow it on **Private networks** (this
   is what lets your phone/iPad connect).
2. **Press START.** It launches OBS for you if needed. The very first START
   also downloads the OCR model (about 100 MB) and can take a minute; the
   button reads STARTING… until it is ready.
3. **Play Marathon in borderless windowed.**
4. **Optional, on your phone/iPad:** the app window shows
   **"On your phone/iPad (same Wi-Fi): http://192.168.x.x:8000"**. Open that
   address in the browser on a phone or iPad on the same Wi-Fi: live kill feed,
   instant replays, match highlight reels, and a **+1** button for anything
   the detector misses.
5. **Press STOP** when you're done. Clips, highlight reels, vertical Shorts,
   and a session recap are waiting in your OBS recording folder under
   `Marathon Sessions/`.

## Worried about performance?

Double-click **`4 - Benchmark (will my PC handle it).bat`**. It measures the
detection loop on YOUR machine and gives a verdict. It's safe to run before
any other setup (no OBS needed; the first run installs the same things the
app needs anyway).

GTX 16-series / RTX 20-series and newer run the OCR on the GPU. GTX 10-series
and older run it on the CPU instead: it works, but catches slightly fewer
popups. The heavy video work happens after you stop playing, not mid-fight.

## If something's off

- **"Windows protected your PC"** when you open a `.bat`: click **More info →
  Run anyway**, or Unblock the ZIP before extracting (setup step 1).
- **The logo shows, then nothing happens / setup was interrupted:** run
  `START Kill Recorder (Window).bat` again; setup picks up what didn't
  finish. If it happens again, delete the `.venv` folder in the app folder
  and run START again.
- **A red box under START says it didn't work:** it names the problem and the
  session log. Most often OBS's WebSocket server is off, or the password in
  `config.yaml` doesn't match OBS (setup step 2). Fix it, save
  `config.yaml`, and press START again; it re-reads the file.
- **"OBS IS BLACK"** warning: your OBS scene has no working capture source
  (setup step 2). With Game Capture this is normal until the game is running.
- **No clips saved / "CLIP NOT SAVED"**: the Replay Buffer is off in OBS
  (setup step 2).
- **Kills not detected:** play in borderless windowed on the monitor WITNESS
  captures (the primary one unless you changed `monitor_index`). With a kill
  popup on screen, run **`Diagnose Capture.bat`** and look at the two images
  it saves. If that looks right, set `debug_ocr: true` in `config.yaml` for
  one session, and `logs/session_*.log` will show every OCR read. Set it back
  to `false` afterwards.
- **"GPU OCR unavailable on this card ... using CPU"**: your card is too old
  for the GPU build (GTX 10-series and older). It still works; for a lighter
  load set `poll_fps: 3` and `ocr_upscale: 2` in `config.yaml`.
- **Phone/iPad can't open the dashboard:** allow Python on Private networks
  when Windows asks, and set your Wi-Fi to Private (*Settings → Network &
  internet → Wi-Fi → your network → Network profile type → Private*). A VPN
  on the PC can hide or change the address; turn it off, or use the
  192.168.x.x address the window shows.
- Everything else: the README's Tuning section, or
  [open an issue](https://github.com/svisnesky/WITNESS/issues/new/choose).
  Attach your session log from `logs/`, and read the privacy note in the
  form first: logs contain other players' gamertags.
