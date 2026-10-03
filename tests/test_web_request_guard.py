"""The dashboard's Host/Origin guard (no login token, by the owner's decision).

Before it, any web page open in the user's browser could POST to
localhost:8000/config and switch on YouTube auto-upload, or start/stop a session,
or push an update — a cross-site 'no-cors' POST needs no preflight. And a page
on a DNS-rebinding domain could READ /status, /clipdata and the videos.

The iPad must keep working, so the cases that matter most are the allowed ones:
an IP-literal Host with no Origin (the iOS app), a .local name, localhost with a
matching Origin (the desktop window)."""

import http.client
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webserver  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def srv():
    st = webserver.LiveState()
    cfg = {"youtube_upload_session_reel": False, "play_sound": False}
    saved = []
    st.bind_config(cfg, lambda changes: saved.append(dict(changes)))
    fired = []
    st.bind_control(lambda: fired.append("start"), lambda: fired.append("stop"))
    server = webserver.start_web(st, 0, BASE, "127.0.0.1")
    port = server.server_address[1]
    yield {"st": st, "cfg": cfg, "saved": saved, "fired": fired, "port": port}
    server.shutdown()
    server.server_close()


def _req(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def _post_config(srv, headers, body=b'{"youtube_upload_session_reel": true}'):
    h = {"Content-Type": "application/json"}
    h.update(headers)
    return _req(srv["port"], "POST", "/config", h, body)


# --- the ways in that must keep working --------------------------------------

def test_ipad_style_request_by_lan_ip_with_no_origin_is_allowed(srv):
    """What the iOS app and a Safari bookmark send: Host is the PC's LAN IP, no
    Origin. This is the request that must never 403."""
    port = srv["port"]
    code, body = _req(port, "GET", "/status", {"Host": f"192.168.1.50:{port}"})
    assert code == 200 and b'"running"' in body
    code, _ = _req(port, "GET", "/api/info", {"Host": f"192.168.1.50:{port}"})
    assert code == 200
    # the iOS app's POSTs: JSON body, no Origin
    code, body = _req(port, "POST", "/stop", {"Host": f"192.168.1.50:{port}",
                                             "Content-Type": "application/json"}, b"{}")
    assert code == 200 and b'"ok"' in body


def test_safari_on_the_ipad_posting_with_its_own_origin_is_allowed(srv):
    port = srv["port"]
    code, _ = _post_config(srv, {"Host": f"192.168.1.50:{port}",
                                 "Origin": f"http://192.168.1.50:{port}"},
                           body=b'{"play_sound": true}')
    assert code == 200
    assert srv["cfg"]["play_sound"] is True
    _post_config(srv, {"Host": f"192.168.1.50:{port}"}, body=b'{"play_sound": false}')


def test_a_dot_local_host_is_allowed(srv):
    port = srv["port"]
    code, _ = _req(port, "GET", "/status", {"Host": f"gaming-pc.local:{port}"})
    assert code == 200
    # Bonjour hands out names with a trailing root dot
    code, _ = _req(port, "GET", "/status", {"Host": f"gaming-pc.local.:{port}"})
    assert code == 200


def test_the_desktop_window_localhost_with_matching_origin_is_allowed(srv):
    port = srv["port"]
    code, _ = _post_config(srv, {"Host": f"localhost:{port}",
                                 "Origin": f"http://localhost:{port}"},
                           body=b"{}")
    assert code == 200


def test_ipv6_loopback_and_the_pcs_own_hostname_are_allowed(srv):
    import socket
    port = srv["port"]
    assert _req(port, "GET", "/status", {"Host": f"[::1]:{port}"})[0] == 200
    assert _req(port, "GET", "/status",
                {"Host": f"{socket.gethostname()}:{port}"})[0] == 200


def test_a_post_with_no_origin_and_no_referer_is_allowed(srv):
    port = srv["port"]
    code, _ = _req(port, "POST", "/clip", {"Host": f"127.0.0.1:{port}"}, b"")
    assert code == 200


# --- the attacks --------------------------------------------------------------

def test_a_foreign_origin_cannot_change_settings(srv):
    """The exact attack: a page on another site flips YouTube auto-upload on."""
    port = srv["port"]
    before = list(srv["saved"])
    code, _ = _post_config(srv, {"Host": f"localhost:{port}",
                                 "Origin": "http://evil.example",
                                 "Content-Type": "text/plain;charset=UTF-8"})
    assert code == 403
    assert srv["cfg"]["youtube_upload_session_reel"] is False
    assert srv["saved"] == before, "a refused request must not be persisted"


def test_a_foreign_origin_cannot_start_a_session(srv):
    port = srv["port"]
    srv["fired"].clear()
    code, _ = _req(port, "POST", "/start", {"Host": f"127.0.0.1:{port}",
                                            "Origin": "https://evil.example"}, b"")
    assert code == 403
    assert srv["fired"] == []


def test_same_host_but_a_different_port_is_another_origin(srv):
    port = srv["port"]
    code, _ = _post_config(srv, {"Host": f"localhost:{port}",
                                 "Origin": f"http://localhost:{port + 1}"})
    assert code == 403


def test_a_foreign_referer_counts_when_there_is_no_origin(srv):
    port = srv["port"]
    code, _ = _post_config(srv, {"Host": f"localhost:{port}",
                                 "Referer": "http://evil.example/page.html"})
    assert code == 403


def test_a_null_origin_is_refused_on_post(srv):
    """Sandboxed iframes and file: pages send Origin: null."""
    port = srv["port"]
    code, _ = _post_config(srv, {"Host": f"localhost:{port}", "Origin": "null"})
    assert code == 403


def test_dns_rebinding_host_is_refused(srv):
    """A rebinding page reaches the server under ITS OWN name, which is the one
    thing it can't change. Reads are what rebinding buys, so GET is checked."""
    port = srv["port"]
    for host in (f"evil.example:{port}", "attacker.rebind.example",
                 f"192.168.1.50.nip.io:{port}", f"localhost.evil.example:{port}"):
        code, _ = _req(port, "GET", "/status", {"Host": host})
        assert code == 403, host
    code, _ = _post_config(srv, {"Host": f"evil.example:{port}"})
    assert code == 403
    assert srv["cfg"]["youtube_upload_session_reel"] is False


def test_the_server_survives_a_refusal(srv):
    port = srv["port"]
    _req(port, "GET", "/status", {"Host": "evil.example"})
    assert _req(port, "GET", "/status", {"Host": f"127.0.0.1:{port}"})[0] == 200


# --- the rule itself ----------------------------------------------------------

@pytest.fixture
def pc_named(monkeypatch):
    """Pretend this PC is DESKTOP-ABC123 and getfqdn() says what it's told."""
    import socket

    def set_names(host="DESKTOP-ABC123", fqdn="DESKTOP-ABC123"):
        monkeypatch.setattr(socket, "gethostname", lambda: host)
        monkeypatch.setattr(socket, "getfqdn", lambda *a: fqdn)
        monkeypatch.setattr(webserver, "_fqdn_cache", [])
    set_names()
    return set_names


@pytest.mark.parametrize("name", ["127.0.0.1", "192.168.1.50", "10.0.0.7", "::1",
                                  "fe80::1", "localhost", "LOCALHOST", "gaming-pc.local",
                                  "Stans-PC.local.", "DESKTOP-ABC123", "desktop-abc123",
                                  "desktop-abc123.lan", "DESKTOP-ABC123.home.arpa",
                                  "desktop-abc123.localdomain", "desktop-abc123.internal",
                                  "desktop-abc123.home", "desktop-abc123.lan.", ""])
def test_host_names_that_mean_this_pc(name, pc_named):
    assert webserver._host_allowed(name), name


@pytest.mark.parametrize("name", ["GAMING-PC", "x", "someone-else.lan",
                                  "desktop-abc123.evil.lan", "desktop-abc123.example.com",
                                  "desktop-abc123.lan.evil.example"])
def test_names_that_are_not_this_pc(name, pc_named):
    """Decision 5 lists the PC's OWN hostname: another bare name, or this PC's
    name under a public suffix an attacker could publish, is refused."""
    assert not webserver._host_allowed(name), name


def test_the_fqdn_counts_as_this_pcs_name(pc_named):
    pc_named(host="DESKTOP-ABC123", fqdn="desktop-abc123.corp.example")
    assert webserver._host_allowed("desktop-abc123.corp.example")
    assert not webserver._host_allowed("other.corp.example")


def test_the_fqdn_is_looked_up_once_and_only_when_needed(pc_named, monkeypatch):
    import socket
    calls = []

    def fqdn(*a):
        calls.append(1)
        return "desktop-abc123.lan"
    monkeypatch.setattr(socket, "getfqdn", fqdn)
    assert webserver._host_allowed("192.168.1.5")
    assert webserver._host_allowed("desktop-abc123")
    assert calls == []                      # IPs and the bare name never ask DNS
    webserver._host_allowed("nope.example")
    webserver._host_allowed("nope2.example")
    assert calls == [1]


@pytest.mark.parametrize("name", ["evil.example", "localhost.evil.example",
                                  "127.0.0.1.nip.io", "local.evil.example",
                                  "gaming-pc.local.evil.example"])
def test_host_names_an_attacker_could_publish(name):
    assert not webserver._host_allowed(name), name


def test_host_header_parsing():
    assert webserver._split_host_port("192.168.1.5:8000") == ("192.168.1.5", "8000")
    assert webserver._split_host_port("[::1]:8000") == ("::1", "8000")
    assert webserver._split_host_port("[::1]") == ("::1", None)
    assert webserver._split_host_port("pc.local") == ("pc.local", None)


def test_origin_matching_uses_default_ports():
    assert webserver._origin_matches("http://192.168.1.5", "192.168.1.5")
    assert webserver._origin_matches("http://192.168.1.5:80", "192.168.1.5")
    assert not webserver._origin_matches("https://192.168.1.5", "192.168.1.5")
    assert webserver._origin_matches("http://[::1]:8000", "[::1]:8000")
    assert not webserver._origin_matches("garbage", "localhost:8000")


def test_windows_does_not_share_the_port_with_a_second_copy():
    """SO_REUSEADDR on Windows lets a second WITNESS bind :8000 too. Unix keeps
    it (it only means 'rebind past TIME_WAIT' there)."""
    assert webserver._QuietServer.allow_reuse_address == (os.name != "nt")
