"""sd_notify, by hand.

Type=notify plus WatchdogSec is the cheapest way to satisfy the rule that
a stalled capture has to be noticed. A stream that has stopped delivering
audio without erroring still exits the ping loop, systemd stops hearing
from us, and the unit is restarted. No dependency: it is a datagram to a
socket systemd already opened.
"""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger(__name__)

_socket: socket.socket | None = None
_address: str | None = None


def _connect() -> None:
    global _socket, _address
    if _socket is not None:
        return
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):          # abstract namespace
        addr = "\0" + addr[1:]
    try:
        _socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
        _address = addr
    except OSError as exc:
        log.warning("sd_notify unavailable: %s", exc)


def send(state: str) -> None:
    """Fire-and-forget; never let telling systemd things break the service."""
    _connect()
    if _socket is None or _address is None:
        return
    try:
        _socket.sendto(state.encode("utf8"), _address)
    except OSError as exc:
        log.warning("sd_notify failed: %s", exc)


def ready(status: str = "") -> None:
    send("READY=1" + (f"\nSTATUS={status}" if status else ""))


def status(text: str) -> None:
    send(f"STATUS={text}")


def watchdog() -> None:
    send("WATCHDOG=1")


def watchdog_interval_s() -> float | None:
    """Half of WatchdogSec — the usual ping rate. None if not supervised."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        return None
    try:
        return int(usec) / 2_000_000.0
    except ValueError:
        return None
