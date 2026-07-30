"""Upload a video to YouTube as unlisted, hands-off after a one-time setup.

ONE-TIME SETUP (see README "YouTube auto-upload"):
  1. Make a free Google Cloud project, enable "YouTube Data API v3".
  2. Create an OAuth client (type: Desktop app), download it as
     client_secret.json into this app folder.
  3. The first upload opens a browser once to grant access; the token is
     cached in youtube_token.json so it never asks again.

Everything here degrades gracefully: if the libraries aren't installed, the
credentials are missing, or the API errors, it prints why and returns None
instead of breaking the session.

Daily quota note: YouTube's default API quota is ~6 uploads/day. We only
upload one session reel per session, so that's plenty.
"""

from __future__ import annotations

import os

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET = "client_secret.json"
TOKEN_FILE = "youtube_token.json"


_MIMETYPES = {".mp4": "video/mp4", ".mkv": "video/x-matroska",
              ".mov": "video/quicktime", ".webm": "video/webm",
              ".avi": "video/x-msvideo", ".flv": "video/x-flv"}


def _mimetype(path: str) -> str:
    """Mimetype from the extension, defaulting to mp4."""
    return _MIMETYPES.get(os.path.splitext(path)[1].lower(), "video/mp4")


def _explain(err) -> str:
    """Turn an upload failure into something actionable instead of a stack trace."""
    text = str(err)
    low = text.lower()
    if "accessnotconfigured" in low or "has not been used in project" in low:
        return ("the YouTube Data API v3 isn't enabled for this project. "
                "Google Cloud console -> APIs & Services -> Library -> "
                "'YouTube Data API v3' -> Enable.")
    if "quotaexceeded" in low or "dailylimitexceeded" in low:
        return ("daily API quota used up (~6 uploads/day on the free tier). "
                "It resets at midnight Pacific.")
    # Most specific first: youtubeSignupRequired arrives AS a 401, so the generic
    # auth branch below would otherwise swallow it and send Stan chasing tokens.
    if "youtubesignuprequired" in low:
        return "that Google account has no YouTube channel yet — create one first."
    if "unauthorized" in low or "invalid_grant" in low or "401" in text:
        return ("authorization was rejected. Delete youtube_token.json and let "
                "it re-authorize; if it keeps happening, publish the OAuth "
                "consent screen (Testing status expires tokens after 7 days).")
    if "forbidden" in low or "403" in text:
        return ("YouTube refused the upload (403). Usually the API isn't enabled, "
                "or the account has no channel.")
    return text


def _get_credentials(base_dir: str):
    """Load cached creds or run the one-time browser consent. Returns creds
    or None (with a printed reason)."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("  [youtube] upload libraries not installed. Run: "
              ".venv\\Scripts\\python -m pip install "
              "google-api-python-client google-auth-oauthlib google-auth-httplib2")
        return None

    token_path = os.path.join(base_dir, TOKEN_FILE)
    secret_path = os.path.join(base_dir, CLIENT_SECRET)
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            return creds
        except Exception as e:
            print(f"  [youtube] token refresh failed ({e}); re-authorizing")
            if "invalid_grant" in str(e).lower():
                # The 7-day trap: while the OAuth consent screen is in TESTING
                # status Google expires refresh tokens after a week. Uploads work
                # beautifully, then stop, and it looks like the app broke.
                print("  [youtube] ^ this usually means your OAuth consent screen"
                      " is still in 'Testing' status, which expires refresh"
                      " tokens after 7 DAYS.\n"
                      "            Fix permanently: Google Auth Platform ->"
                      " Audience -> Publish app.\n"
                      "            (An unverified single-user app just shows a"
                      " warning you click past.)")

    if not os.path.exists(secret_path):
        print(f"  [youtube] {CLIENT_SECRET} not found in the app folder — "
              "skipping upload. See README 'YouTube auto-upload' to set it up.")
        return None
    try:
        flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        return creds
    except Exception as e:
        print(f"  [youtube] authorization failed: {e}")
        return None


def upload(video_path: str, title: str, description: str, base_dir: str,
           privacy: str = "unlisted") -> str | None:
    """Upload video_path unlisted. Returns the watch URL or None."""
    if not os.path.exists(video_path):
        print(f"  [youtube] video not found: {video_path}")
        return None
    creds = _get_credentials(base_dir)
    if creds is None:
        return None
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        youtube = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {"title": title[:100], "description": description,
                        "categoryId": "20"},  # 20 = Gaming
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        }
        # Derive the mimetype from the file. It was hardcoded video/mp4, which is
        # a lie for the session montage — that renders as .mkv (it's a stream copy
        # of OBS's Replay Buffer output, never re-encoded).
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True,
                                mimetype=_mimetype(video_path))
        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        print(f"  [youtube] uploading {os.path.basename(video_path)} ({privacy})...")
        resp = req.execute()
        vid = resp.get("id")
        if vid:
            url = f"https://youtu.be/{vid}"
            print(f"  [youtube] uploaded -> {url}")
            return url
        print(f"  [youtube] upload returned no video id: {resp}")
        return None
    except Exception as e:
        print(f"  [youtube] upload failed: {_explain(e)}")
        return None
