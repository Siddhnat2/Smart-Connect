"""Small helpers: defaults, credential generation, optional config file."""
import json
import os
import secrets
import sys

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"  # no look-alikes


def gen_username() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(9))


def gen_password(n: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _config_paths():
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()
    return [os.path.join(base, "rsupport_config.json"),
            os.path.join(os.path.expanduser("~"), ".rsupport.json")]


def load_config() -> dict:
    for p in _config_paths():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def default_relay() -> str:
    return os.environ.get("RS_RELAY") or load_config().get("relay") or "ws://127.0.0.1:8765"


def default_relay_key() -> str:
    return os.environ.get("RS_RELAY_KEY") or load_config().get("relay_key") or ""


def local_ips() -> list:
    """Best-effort list of this machine's IPv4 addresses (LAN / VPN) to show to the technician."""
    import socket
    ips = []
    try:  # primary outbound interface; a UDP connect() sends no packets
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for ai in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = ai[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips
