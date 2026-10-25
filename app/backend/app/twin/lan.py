"""LAN address of this laptop for the phone terminal (GET /twin/lan-url, loopback callers only).

`lan_ip()` uses the UDP-connect trick: a datagram socket "connected" to a private address never sends a packet, but the
kernel picks the outbound interface and `getsockname()` reveals its IPv4. Loopback results are rejected (no Wi-Fi/LAN).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import sqlite3
from typing import Final

from . import constants as C

logger = logging.getLogger("twin.lan")
PROBE_TARGET: Final[tuple[str, int]] = ("10.255.255.255", 9)  # private address, discard port; nothing is transmitted
NO_LAN_NOTE: Final[str] = "와이파이 연결 없음"


def lan_ip() -> str | None:
    """The laptop's current non-loopback IPv4, or None when there is no LAN route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(PROBE_TARGET)
            ip = str(s.getsockname()[0])
    except OSError as exc:
        logger.info("no LAN route: %s", type(exc).__name__)
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.is_loopback or addr.is_unspecified:
        return None
    return ip


def default_stop_id(conn: sqlite3.Connection, sgg_code: str = C.DEFAULT_SGG) -> str | None:
    """The default terminal stop: the top recommended stop of the demo 구 (else the top recommended stop anywhere)."""
    row = conn.execute("SELECT stop_id FROM mobility_stop WHERE sgg_code = ? AND recommended = 1 ORDER BY greedy_rank, stop_id LIMIT 1",
                       (sgg_code,)).fetchone()
    if row is None:
        row = conn.execute("SELECT stop_id FROM mobility_stop WHERE recommended = 1 ORDER BY greedy_rank, stop_id LIMIT 1").fetchone()
    return None if row is None else str(row["stop_id"])


def terminal_url(ip: str | None, port: int, stop_id: str | None) -> str | None:
    if ip is None:
        return None
    return f"http://{ip}:{port}/terminal" + (f"?stop={stop_id}" if stop_id else "")


__all__ = ["NO_LAN_NOTE", "default_stop_id", "lan_ip", "terminal_url"]
