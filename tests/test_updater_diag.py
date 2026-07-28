"""The updater's failure message must name the real cause.

'Update check skipped (offline?)' sent Stan chasing his network when the
actual cause was the repo being PRIVATE — the updater fetches anonymously
(so it works on any user's machine) and GitHub answers 404, not 403.
"""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import updater  # noqa: E402


def _http(code, headers=None):
    return urllib.error.HTTPError("u", code, "err", headers or {}, None)


def test_404_says_private_not_offline():
    msg = updater._why(_http(404))
    assert "PRIVATE" in msg and "public" in msg
    assert "offline" not in msg.lower()


def test_rate_limit_is_named_with_reset():
    msg = updater._why(_http(403, {"X-RateLimit-Remaining": "0",
                                   "X-RateLimit-Reset": "1900000000"}))
    assert "rate limit" in msg.lower() and "Resets at" in msg


def test_real_offline_still_reads_as_offline():
    msg = updater._why(urllib.error.URLError("Name or service not known"))
    assert "no connection" in msg


def test_other_http_codes_are_reported_plainly():
    assert "HTTP 500" in updater._why(_http(500))
