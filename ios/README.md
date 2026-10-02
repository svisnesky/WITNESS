# WITNESS for iPad and iPhone

A companion app for the WITNESS PC app. It finds your gaming PC on your Wi-Fi by
itself, plays your clips and reels, raises an alert when something's wrong (like
OBS recording a black screen), and has the session controls: Start, Stop, Clip,
and + Kill.

It talks only to your own PC, over your own Wi-Fi. No accounts, no cloud, no
analytics. See [SECURITY.md](../SECURITY.md).

The Safari dashboard still works exactly as before. The app is an extra, not a
replacement.

## What you need

- A **Mac** with **Xcode** (free from the Mac App Store). Apple only lets you
  install apps you build yourself from a Mac.
- An iPad or iPhone on **iPadOS / iOS 17** or newer.
- WITNESS running on your PC with `web_lan: true` in `config.yaml` (the default).
- A **free Apple ID** is enough. The catch: apps signed with a free Apple ID
  **stop opening after 7 days**. When that happens, plug in and press Run again
  (step 6), and it's good for another 7 days. A paid Apple Developer account
  ($99/yr) stretches that to a year.

## Install it

### 1. Sign Xcode into your Apple ID (Mac)

Xcode → **Settings** (⌘,) → **Accounts** → **+** → **Apple Account** → sign in.
This creates a free "Personal Team".

### 2. Open the project (Mac)

Open `ios/WITNESS/WITNESS.xcodeproj`. Click **WITNESS** at the top of the file
list → the **WITNESS** target → **Signing & Capabilities**:

- **Team**: pick your Personal Team.
- **Bundle Identifier**: change `com.svisnesky.witness` to something of your
  own, like `com.yourname.witness`. Each Apple ID needs its own; Xcode says
  "failed to register bundle identifier" if you keep someone else's.

### 3. Plug in and trust (iPad)

Connect the iPad with a cable and **unlock it**. Tap **Trust** on *"Trust This
Computer?"* and enter your passcode.

### 4. Let Xcode see it (Mac)

Xcode → **Window → Devices and Simulators**. The iPad appears under
**Devices**. The first time, it says *"Preparing"* for a few minutes; let it
finish.

Optional: tick **Connect via network**, and later installs work over Wi-Fi.

### 5. Turn on Developer Mode (iPad)

**Settings → Privacy & Security** → scroll to the bottom → **Developer Mode** →
**On** → **Restart**. After it reboots, tap **Turn On** and enter your passcode.

> No Developer Mode switch? It only appears **after** the iPad has been
> connected to Xcode. Do step 4 first, then look again.

### 6. Run it (Mac)

At the top of the Xcode window, pick your iPad as the destination, then press
**Run** (▶ or ⌘R).

### 7. First launch only (iPad)

iPadOS blocks apps from a free Personal Team until you trust them once:
**Settings → General → VPN & Device Management** → your Apple ID → **Trust**.

When WITNESS opens:

- **Allow** finding devices on your local network. That's how it finds your PC
  without you typing an address.
- **Allow** notifications if you want alerts (like a black OBS capture) to pop up.

## Using it

- **Your PC shows up by itself** as *"WITNESS on YOUR-PC"*. Tap it. The app
  remembers it next time.
- **Not listed?** Type the address the PC prints when a session starts
  (`Live view: http://192.168.1.20:8000`). Check the PC and iPad are on the
  same Wi-Fi. Guest networks and some routers block devices from seeing each
  other.
- **Clips** open about 5 seconds before the first kill. Use ◀ ▶ in the player to
  go through the night.
- **Alerts** show as a red banner and a notification while the app is open. iOS
  pauses apps in the background, so keep it open on a stand next to the monitor
  during a session. "Keep iPad awake while recording" (the PC menu, top left)
  stops the screen from locking.
- **Switch PC** is in the same menu.

## Troubleshooting

| What you see | What to do |
|---|---|
| "WITNESS isn't allowed to look on your Wi-Fi" | Settings → Privacy & Security → Local Network → turn on WITNESS |
| PC never appears | Type its address instead. Or on the PC: `.venv\Scripts\python -m pip install zeroconf`, then restart WITNESS |
| "Nothing answered at …" | WITNESS isn't running on the PC, `web_lan` is `false`, or Windows Firewall is blocking Python. Allow it on **Private** networks |
| "PC OFFLINE" in the top bar | The PC stopped answering. The app reconnects by itself when it's back |
| App won't open after a week | The free 7-day signature ran out. Plug in and press Run in Xcode again |
| A clip won't play | The PC converts each clip for the iPad the first time you open it (about a second) and needs ffmpeg for that, the same ffmpeg the reels use |

## For developers

SwiftUI, Swift 6 strict concurrency, no third-party code. It reads the PC's
JSON API (`/api/info`, `/api/status`, `/api/sessions`, `/api/session`,
`/api/play`), which lives in `webserver.py`. Discovery is Bonjour
(`_witness._tcp`), advertised by `discovery.py`. Nothing in the app is
specific to one person's setup.
