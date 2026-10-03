# Changelog

WITNESS installs follow the `main` branch and update themselves, so the
version number only moves at a release. The build sha shown next to it — in
the bottom-right corner of the dashboard and at the top of every session log,
e.g. `0.1.0 (abc1234)` — tells you exactly which commit you are running.

## 0.1.0 — first public release (unreleased)

The first version meant for people other than its author.

**What WITNESS does**
- Reads kill popups in Marathon off the screen with OCR and saves an OBS
  Replay Buffer clip of each one.
- Builds match highlight reels, a session reel, vertical Shorts and a spoken
  WITNESS Report when you press STOP.
- Serves a live dashboard to a phone or iPad on the same Wi-Fi: kill feed,
  instant replays, reels and a +1 button for missed kills.
- Optional YouTube auto-upload (all off by default), teachable game profiles,
  and a self-updater.

**Getting ready for strangers**
- Setup: the installer checks that it really finished, picks Python 3.12 over
  newer versions, warns about OneDrive folders, and installs packages that an
  update adds. ffmpeg is bundled as a fallback.
- New installs no longer carry the author's gamertag, announcer name or OBS
  password placeholder.
- START failures, OBS setup mistakes, a Replay Buffer that is off and a
  missing ffmpeg are named on screen instead of failing silently.
- The desktop window updates itself again, shows the phone/iPad address, and
  runs one copy at a time.
- The dashboard refuses cross-site and DNS-rebinding requests, and its routes
  only touch session folders.
- Older NVIDIA cards (GTX 10-series and earlier) run OCR on the CPU instead of
  crashing.
- Detection: kills traded on the death screen count, missed Runner Elims are
  credited from the exfil panel, and a slow disk check no longer stalls
  detection.
- `tts_online` switch for the Microsoft neural voice (on by default), and
  SECURITY.md lists every host the app and its libraries contact.
- A version number, this changelog, a contributing guide and a bug-report
  form.
