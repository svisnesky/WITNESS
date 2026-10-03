# Auto-upload to YouTube

One-time setup (~10 min). After this, the videos you pick upload to your channel
automatically — unlisted by default — when you press STOP.

You do this once. It's fiddly only because it's Google. Follow it in order.
Google renames its console now and then; where a menu has an older name, both
are given.

---

## 1. The upload libraries

Already installed by setup (they're in `requirements.txt`). Only if WITNESS
says **"upload libraries not installed"**, or `Check YouTube.bat` says
**"upload libraries missing"**, run this in the app folder:

```
.venv\Scripts\python -m pip install -r requirements.txt
```

## 2. Make a Google Cloud project

1. Go to **console.cloud.google.com** (sign in with the Google account that
   owns the YouTube channel you want to upload to).
2. Top bar → project dropdown → **New Project** → name it `WITNESS` → Create.
3. Make sure that new project is selected (top bar) for every step below.

## 3. Turn on the YouTube API

1. Search bar → type **YouTube Data API v3** → open it → **Enable**.

## 4. Set up the consent screen

1. Left menu → **Google Auth Platform** (older consoles: **APIs & Services →
   OAuth consent screen**) → **Get started**.
2. App name `WITNESS`, your email as the support email → Next.
3. Audience: **External** → Next.
4. Your email as the contact address → Next → agree → **Create**.
5. **Google Auth Platform → Audience → Publish app → Confirm.**
   *(This step matters. While the app sits in "Testing", Google expires your
   sign-in after 7 days and uploads quietly stop.)* You don't need Google to
   verify the app for your own channel; you'll just see an "unverified app"
   screen once, in step 6.

If you'd rather leave it in Testing, add your own Gmail address under
**Audience → Test users** instead (without it the upload is blocked), and
expect to sign in again every 7 days.

## 5. Create the credentials file

1. **Google Auth Platform → Clients → Create client** (older consoles:
   **APIs & Services → Credentials → Create Credentials → OAuth client ID**).
2. Application type: **Desktop app** → name it `WITNESS` → Create.
3. **Download JSON** (in the popup, or the download icon next to the client).
4. Rename that file to exactly **`client_secret.json`** and move it into the
   app folder (same folder as `config.yaml`).

## 6. Check it, and sign in once

Double-click **`Check YouTube.bat`**. It checks every step above without
playing a session or posting anything. The first time, a browser window opens
asking you to sign in and grant access:

- Pick your Google account.
- You'll see **"Google hasn't verified this app"** — that's normal for a
  personal app. Click **Advanced → Go to WITNESS (unsafe)** → **Continue**.
- Grant access.

A `youtube_token.json` file is saved, so it won't ask again (as long as you
published the app in step 4; in Testing the sign-in expires after 7 days).

To prove the whole path, open a Command Prompt in the app folder and run:

```
"Check YouTube.bat" --upload
```

That uploads a 3-second **PRIVATE** test video you can delete in YouTube
Studio → Content.

## 7. Turn on what you want uploaded

Open the app → **Settings**. Four separate toggles — flip on whichever you want
(all off by default):

- **YouTube: session reel** — the one whole-session highlights video. Best
  starting point (one upload per session).
- **YouTube: match reels** — each match's highlight reel (a few per session).
- **YouTube: session montage** — the end-of-session montage of every clip
  (only made when `make_montage: true` in config.yaml).
- **YouTube: Shorts (quota-heavy)** — every vertical clip as a YouTube Short.
  A busy session makes many, and Google allows ~6 uploads/day. Leave it off
  unless you post a lot.

*(Or set the matching `youtube_upload_*` flags in config.yaml.)*

From now on, everything you switched on uploads by itself when you press STOP.
If you skipped step 6, the first STOP opens the sign-in browser instead.

---

## Notes

- **Privacy**: uploads are **unlisted** (link-only, not public). Change with
  `youtube_privacy` in config.yaml: `unlisted` | `private` | `public`.
- **Quota**: Google allows ~6 uploads/day by default — one per session is
  fine.
- **Security**: `client_secret.json` and `youtube_token.json` are gitignored —
  they never leave your PC and are never shared.
- **If it doesn't upload**: run `Check YouTube.bat`, or look in
  `logs/session_*.log` for the `[youtube] ...` line with the reason. Usual
  causes:
  - the app is still in **Testing** and the sign-in expired after 7 days:
    publish it (step 4.5), delete `youtube_token.json`, and run
    `Check YouTube.bat` to sign in again;
  - it's in Testing and you skipped the Test users step;
  - `client_secret.json` isn't in the app folder, or isn't a Desktop-app
    client.
- **Turn it off** anytime: Settings → switch off all four **YouTube: …**
  toggles (or set every `youtube_upload_*` flag in config.yaml to `false`).
