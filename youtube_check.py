"""Check the YouTube upload setup without playing a session.

Same idea as Check Voice: find out in ten seconds whether the thing is wired up,
instead of playing for two hours and discovering it wasn't.

Deliberately READ-ONLY by default. It verifies everything it can WITHOUT putting
anything on your channel. A real upload is the only fully conclusive test, so
that exists too — but behind an explicit --upload flag, uploaded PRIVATE, with
the video id printed so you can delete it. A diagnostic tool should never quietly
publish to someone's account.

Note on what can't be checked: the app requests only the youtube.upload scope,
which is intentionally narrow — it does not permit listing your channel. Asking
for a read scope here would force the main app to re-authorize. So a green result
below means "credentials are valid and the API accepted them"; --upload is what
proves the whole path.
"""

from __future__ import annotations

import os
import sys

OK, BAD, WARN = "[OK]", "[X]", "[!]"


def _line(sym, msg):
    print(f"{sym} {msg}")


def _find_downloaded_secret() -> str:
    """A client_secret*.json sitting in Downloads/Desktop/home.

    Google's download is named client_secret_<long-id>.apps.googleusercontent
    .com.json, so "save it as client_secret.json" hides a rename step that is
    easy to miss. Finding the file lets us print the exact copy command.

    Only the PATH is used — the file is never opened here."""
    home = os.path.expanduser("~")
    for folder in (os.path.join(home, "Downloads"), os.path.join(home, "Desktop"),
                   home):
        try:
            hits = sorted((f for f in os.listdir(folder)
                           if f.lower().startswith("client_secret")
                           and f.lower().endswith(".json")),
                          key=lambda f: os.path.getmtime(os.path.join(folder, f)),
                          reverse=True)
        except OSError:
            continue
        if hits:
            return os.path.join(folder, hits[0])
    return ""


def run(base_dir: str, do_upload: bool = False) -> int:
    print("=" * 58)
    print(" WITNESS — YouTube upload check")
    print("=" * 58)

    # 1. libraries
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        _line(OK, "upload libraries installed")
    except ImportError:
        _line(BAD, "upload libraries missing. Run:")
        print("       .venv\\Scripts\\python -m pip install "
              "google-api-python-client google-auth-oauthlib google-auth-httplib2")
        return 1

    # 2. the client secret
    import youtube_upload as yu
    secret = os.path.join(base_dir, yu.CLIENT_SECRET)
    if not os.path.exists(secret):
        _line(BAD, f"{yu.CLIENT_SECRET} not found")
        print()
        # The install folder is often <zip-name>\<zip-name>, so "save it here"
        # is ambiguous. Print the EXACT destination path, filename included.
        print("       It must go exactly here (this is the folder with main.py")
        print("       and the START bat):")
        print()
        print(f"         {secret}")
        print()
        found = _find_downloaded_secret()
        if found:
            # Google names the download client_secret_<long-id>.json, so the
            # rename is the step people miss.
            print("       I found what looks like your downloaded key:")
            print(f"         {found}")
            print()
            print("       Copy it into place with this (it renames it too):")
            print(f'         copy "{found}" "{secret}"')
        else:
            print("       Don't have it yet? Google Cloud console -> APIs &")
            print("       Services -> Credentials -> Create OAuth client ID ->")
            print("       Desktop app -> download the JSON.")
            print(f"       Then RENAME it to exactly {yu.CLIENT_SECRET} — the")
            print("       download has a long name and the rename is required.")
        return 1
    _line(OK, f"{yu.CLIENT_SECRET} found")

    # A downloaded client secret is JSON with an "installed" key for Desktop apps.
    try:
        import json
        with open(secret, encoding="utf-8") as f:
            data = json.load(f)
        if "installed" not in data:
            kind = ", ".join(data.keys())
            _line(WARN, f"this looks like a '{kind}' client, not a Desktop app.")
            print("       Recreate it as Application type: Desktop app.")
        else:
            _line(OK, "it's a Desktop-app client (the right kind)")
    except Exception as e:
        _line(BAD, f"{yu.CLIENT_SECRET} isn't valid JSON: {e}")
        return 1

    # 3. token / consent
    token = os.path.join(base_dir, yu.TOKEN_FILE)
    had_token = os.path.exists(token)
    if had_token:
        _line(OK, f"{yu.TOKEN_FILE} exists (already authorized once)")
    else:
        _line(WARN, "not authorized yet — a browser window will open now.")
        print("       You'll see 'Google hasn't verified this app'; that's your")
        print("       own app. Advanced -> Continue.")

    creds = yu._get_credentials(base_dir)
    if creds is None:
        _line(BAD, "could not obtain credentials (see the reason above)")
        return 1
    _line(OK, "credentials obtained")

    if not had_token and os.path.exists(token):
        _line(OK, f"{yu.TOKEN_FILE} written — it won't ask again")

    # 4. the API accepts them
    try:
        from googleapiclient.discovery import build
        build("youtube", "v3", credentials=creds)
        _line(OK, "YouTube Data API client built")
    except Exception as e:
        _line(BAD, f"API client failed: {yu._explain(e)}")
        return 1

    if not do_upload:
        print()
        _line(WARN, "Everything checkable without touching your channel passed.")
        print("       The app only requests the 'upload' scope, so it can't list")
        print("       your channel to confirm further. To prove the whole path,")
        print("       run:  Check YouTube.bat --upload")
        print("       That uploads a 3-second PRIVATE test video you can delete.")
        return 0

    # 5. the conclusive test — an actual upload, private
    print()
    _line(WARN, "uploading a 3-second PRIVATE test video...")
    import subprocess
    import time

    import montage
    ff = montage.find_ffmpeg(base_dir, {})
    tmp = os.path.join(base_dir, "_yt_test.mp4")
    try:
        subprocess.run(
            [ff, "-y", "-v", "error", "-f", "lavfi", "-i",
             "color=c=black:s=640x360:d=3", "-f", "lavfi", "-i",
             "anullsrc=r=48000:cl=stereo", "-t", "3", "-c:v", "libx264",
             "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", tmp],
            capture_output=True, check=True)
    except Exception as e:
        _line(BAD, f"couldn't build the test clip with ffmpeg: {e}")
        return 1

    url = yu.upload(tmp, f"WITNESS upload test — {time.strftime('%b %d %H:%M')}",
                    "Test upload from WITNESS. Safe to delete.", base_dir,
                    privacy="private")
    try:
        os.remove(tmp)
    except OSError:
        pass

    if url:
        _line(OK, f"UPLOAD WORKS -> {url}")
        print("       It's PRIVATE. Delete it in YouTube Studio -> Content.")
        print()
        print("       Reminder: publish your OAuth consent screen")
        print("       (Google Auth Platform -> Audience -> Publish app) or the")
        print("       token expires in 7 days and uploads stop.")
        return 0
    _line(BAD, "upload failed — the reason is printed above")
    return 1


def main() -> int:
    base = os.path.dirname(os.path.abspath(__file__))
    return run(base, do_upload="--upload" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
