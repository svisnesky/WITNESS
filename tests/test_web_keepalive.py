"""The dashboard server is HTTP/1.1 keep-alive. A POST body that no route reads
stays in the socket and gets parsed as the start of the next request."""

import json
import os
import socket
import sys
import threading
import time
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webserver  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KA_PORT = 8949


@pytest.fixture(scope="module")
def server():
    st = webserver.LiveState()
    st._cfg = {}
    threading.Thread(target=webserver.start_web,
                     args=(st, KA_PORT, BASE, "127.0.0.1"), daemon=True).start()
    time.sleep(0.6)
    return st


def _get(path):
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{KA_PORT}{path}", timeout=8))


def test_a_post_body_does_not_poison_the_next_request(server):
    """Bug found building the app: most POST routes never read their body, and
    the server is HTTP/1.1 keep-alive, so a body's bytes were parsed as the start
    of the NEXT request on the same connection. Send both on ONE socket."""
    s = socket.create_connection(("127.0.0.1", KA_PORT), timeout=5)
    s.sendall(b"POST /clip HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
              b"Content-Length: 2\r\n\r\n{}"
              b"GET /status HTTP/1.1\r\nHost: x\r\n\r\n")
    data = b""
    deadline = time.time() + 5
    # read until the second response's BODY is in, not just its status line
    while time.time() < deadline and not (data.count(b"HTTP/1.1 ") >= 2
                                          and data.rstrip().endswith(b"}")):
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    responses = data.split(b"HTTP/1.1 ")[1:]
    assert len(responses) == 2, data
    assert responses[1].startswith(b"200"), responses[1][:80]
    assert b'"running"' in responses[1], responses[1][-200:]


def test_an_absurd_content_length_is_capped(server):
    """Reading the body up front must not let a client make us allocate GBs."""
    s = socket.create_connection(("127.0.0.1", KA_PORT), timeout=3)
    s.sendall(b"POST /clip HTTP/1.1\r\nHost: x\r\nContent-Length: 99999999999\r\n\r\n{}")
    s.close()
    assert "running" in _get("/status")     # still alive
