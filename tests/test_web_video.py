"""Byte-range video serving, and staying quiet when a player hangs up.

AVPlayer and Safari open a video with several parallel range requests and
cancel them on every seek and close, so this path gets exercised hard."""

import contextlib
import io
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webserver  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8951
SID = "2026-08-08_21-35-33"
BODY = bytes(range(256)) * 40          # 10,240 bytes, every offset distinguishable


@pytest.fixture(scope="module")
def url(tmp_path_factory):
    root = tmp_path_factory.mktemp("vid")
    sess = root / "Marathon Sessions" / SID
    sess.mkdir(parents=True)
    (sess / "clip.mp4").write_bytes(BODY)     # .mp4 is served as-is, no ffmpeg
    st = webserver.LiveState()
    st._cfg = {}
    st.record_dir = str(root)
    threading.Thread(target=webserver.start_web,
                     args=(st, PORT, BASE, "127.0.0.1"), daemon=True).start()
    time.sleep(0.6)
    return f"http://127.0.0.1:{PORT}/api/play?s={SID}&f=clip.mp4"


def _get(url, rng=None):
    req = urllib.request.Request(url, headers={"Range": rng} if rng else {})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b""


def test_whole_file_without_a_range(url):
    code, h, body = _get(url)
    assert code == 200 and body == BODY and h["Accept-Ranges"] == "bytes"


def test_normal_ranges(url):
    code, h, body = _get(url, "bytes=100-199")
    assert code == 206 and body == BODY[100:200]
    assert h["Content-Range"] == f"bytes 100-199/{len(BODY)}"
    code, _h, body = _get(url, "bytes=10000-")
    assert code == 206 and body == BODY[10000:]


def test_suffix_range_is_the_END_of_the_file(url):
    """'bytes=-500' means the last 500 bytes. It used to be served as bytes
    0-500, so a player asking for the tail of a file got its head instead."""
    code, h, body = _get(url, "bytes=-500")
    assert code == 206
    assert body == BODY[-500:]
    assert h["Content-Range"] == f"bytes {len(BODY) - 500}-{len(BODY) - 1}/{len(BODY)}"


def test_unsatisfiable_range_is_416_not_a_negative_length(url):
    code, h, _ = _get(url, f"bytes={len(BODY) + 5}-")
    assert code == 416
    assert h["Content-Range"] == f"bytes */{len(BODY)}"


@pytest.mark.parametrize("bad", ["bytes=abc-", "bytes=0-1,5-9", "bytes=-", "bytes=²-", "items=0-5"])
def test_malformed_ranges_get_the_whole_file(url, bad):
    """These used to reach int() and raise, printing a traceback."""
    code, _h, body = _get(url, bad)
    assert code == 200 and body == BODY


def test_a_hang_up_prints_nothing():
    """On Windows a cancelled transfer raises ConnectionAbortedError (WinError
    10053), which the handlers didn't catch, and a reset between keep-alive
    requests happens outside every handler. Both printed a 15-line traceback
    into the WITNESS console on every seek. A real bug must still print."""
    srv = webserver._QuietServer.__new__(webserver._QuietServer)
    for err in (ConnectionAbortedError(10053, "aborted"), ConnectionResetError(54, "reset"),
                BrokenPipeError(32, "pipe")):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            try:
                raise err
            except ConnectionError:
                srv.handle_error(None, ("1.2.3.4", 5))
        assert buf.getvalue() == "", type(err).__name__
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        try:
            raise ValueError("a real bug")
        except ValueError:
            srv.handle_error(None, ("1.2.3.4", 5))
    assert "a real bug" in buf.getvalue()


def test_a_client_vanishing_mid_video_leaves_the_server_up(url):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=3)
    path = url.split(str(PORT), 1)[1]
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    s.recv(100)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
    s.close()                                   # RST, mid-body
    assert _get(url, "bytes=0-9")[2] == BODY[:10]
