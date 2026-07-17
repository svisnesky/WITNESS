# Auto-upload to YouTube

One-time setup (~10 min). After this, every session's highlight reel uploads
to your channel automatically — unlisted by default — when you press STOP.

You do this once. It's fiddly only because it's Google. Follow it in order.

---

## 1. Install the upload libraries

In the app folder, run:

```
.venv\Scripts\python -m pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

## 2. Make a Google Cloud project

1. Go to **console.cloud.google.com** (sign in with the Google account that
   owns the YouTube channel you want to upload to).
2. Top bar → project dropdown → **New Project** → name it `WITNESS` → Create.
3. Make sure that new project is selected (top bar) for every step below.

## 3. Turn on the YouTube API

1. Search bar → type **YouTube Data API v3** → open it → **Enable**.

## 4. Set up the consent screen

1. Left menu → **APIs & Services → OAuth consent screen**.
2. User type: **External** → Create.
3. App name `WITNESS`, your email for the two email fields → Save and Continue.
4. Scopes page → **Save and Continue** (skip, add nothing).
5. **Test users → Add Users → add your own Gmail address** → Save. *(This step
   matters — without it the upload is blocked.)*
6. Back to Dashboard.

## 5. Create the credentials file

1. Left menu → **APIs & Services → Credentials**.
2. **Create Credentials → OAuth client ID**.
3. Application type: **Desktop app** → name it `WITNESS` → Create.
4. In the popup → **Download JSON**.
5. Rename that file to exactly **`client_secret.json`** and move it into the
   app folder (same folder as `config.yaml`).

## 6. Turn on what you want uploaded

Open the app → **Settings**. Three separate toggles — flip on whichever you
want (all off by default):

- **YouTube: session reel** — the one whole-session highlights video. Best
  starting point (one upload per session).
- **YouTube: match reels** — each match's highlight reel (a few per session).
- **YouTube: Shorts** — every vertical clip as a YouTube Short. *Quota-heavy*
  — a busy session makes many, and Google allows ~6 uploads/day. Leave off
  unless you post a lot.

*(Or set the matching `youtube_upload_*` flags in config.yaml.)*

## 7. First run authorizes it — once

Play a session and press STOP. The first time, a browser window opens asking
you to sign in and grant access:

- Pick your Google account.
- You'll see **"Google hasn't verified this app"** — that's normal for a
  personal app. Click **Advanced → Go to WITNESS (unsafe)** → **Continue**.
- Grant access.

A `youtube_token.json` file is saved, so it **never asks again**. Every
session reel from now on uploads on its own.

---

## Notes

- **Privacy**: uploads are **unlisted** (link-only, not public). Change with
  `youtube_privacy` in config.yaml: `unlisted` | `private` | `public`.
- **Quota**: Google allows ~6 uploads/day by default — one per session is
  fine.
- **Security**: `client_secret.json` and `youtube_token.json` are gitignored —
  they never leave your PC and are never shared.
- **If it doesn't upload**: the console prints the reason (`[youtube] ...`).
  Usual causes: forgot the Test Users step (4.5), or `client_secret.json`
  isn't in the app folder.
- **Turn it off** anytime: Settings → Auto-upload to YouTube → off.
