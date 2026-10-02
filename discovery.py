"""Advertise WITNESS on the local network so the iOS app finds it by itself.

The Safari dashboard needs you to type the PC's LAN address and port, and that
address changes whenever the router hands the PC a new one. This publishes a Bonjour
(mDNS/DNS-SD) service instead — `_witness._tcp` — which the iOS app browses for,
the same way AirPlay and printers show up without any setup.

Uses python-zeroconf, which is pure Python: no Apple Bonjour service needs to be
installed on Windows. It is OPTIONAL. If the package is missing, or the network
refuses multicast (some corporate/guest Wi-Fi does), WITNESS keeps working and the
app falls back to "enter the address shown on the PC".

Advertises only what the app needs to connect: the PC's LAN address and port,
the API version, and the machine name for the picker. Nothing about sessions,
kills or the user.
"""

from __future__ import annotations

import atexit
import socket

SERVICE_TYPE = "_witness._tcp.local."
TTL_SECONDS = 120


class Advertiser:
    """Holds the zeroconf registration; call stop() on shutdown."""

    def __init__(self, zc, info):
        self._zc = zc
        self._info = info

    def stop(self):
        try:
            self._zc.unregister_service(self._info)
        except Exception:
            pass
        try:
            self._zc.close()
        except Exception:
            pass


def _instance_name(hostname: str) -> str:
    """'WITNESS on GAMING-PC' — what the iOS picker shows. DNS-SD instance names
    may contain spaces, but not dots (a dot would start a new label)."""
    safe = (hostname or "PC").replace(".", "-")[:40]
    return f"WITNESS on {safe}"


def advertise(port: int, ip: str, version: str = "", api: int = 1):
    """Start advertising. Returns an Advertiser, or None if it could not be done
    (zeroconf not installed, no usable IP, multicast blocked) — always with a
    one-line reason printed, never an exception."""
    if not ip or ip.startswith("127."):
        print("  [discovery] no LAN address — the iPad app won't find this PC "
              "automatically (enter the address by hand)")
        return None
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        print("  [discovery] 'zeroconf' not installed — the iPad app can't find "
              "this PC automatically. Fix: .venv\\Scripts\\python -m pip install "
              "zeroconf")
        return None
    host = socket.gethostname()
    name = _instance_name(host)
    try:
        info = ServiceInfo(
            SERVICE_TYPE,
            f"{name}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=int(port),
            # ip/port in the TXT record let the app connect straight from the
            # browse result; it still falls back to resolving the service.
            properties={"api": str(api), "v": (version or "")[:12], "host": host[:40],
                        "ip": ip, "port": str(int(port))},
            server=f"{host.replace(' ', '-').lower()}.local.",
        )
        zc = Zeroconf()
        # allow_name_change: two PCs with the same hostname on one network get
        # "WITNESS on PC (2)" instead of one silently failing to register.
        # ttl: the atexit goodbye below never runs when the process is KILLED
        # (closing the console window on Windows, SIGTERM), and zeroconf's
        # default 4500s TTL then kept a dead PC in the iPad's list for 75
        # minutes. At 120s a stale entry fades in about two.
        zc.register_service(info, ttl=TTL_SECONDS, allow_name_change=True)
        print(f"  [discovery] advertising '{name}' on the network for the iPad app")
        adv = Advertiser(zc, info)
        # Unregistering sends mDNS "goodbye" packets, so the iPad drops this PC
        # the moment WITNESS closes instead of offering a dead entry until its
        # cache expires.
        atexit.register(adv.stop)
        return adv
    except Exception as e:
        print(f"  [discovery] couldn't advertise ({type(e).__name__}: {e}) — "
              "the iPad app will need the address entered by hand")
        return None
