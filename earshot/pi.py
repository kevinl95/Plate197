"""What the board will tell us about itself.

Undervoltage is the one that matters. A Pi 3 with a USB microphone on a
marginal supply doesn't announce itself — it drops audio blocks, and that
looks exactly like a bug in the capture loop. get_throttled is how you
tell the two apart, so /health carries it whether or not anyone asks.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")
MODEL = Path("/sys/firmware/devicetree/base/model")

# From the firmware's get_throttled bitmask. The low bits are now, the
# high bits are "since boot" — a frame that was fine at 9am and browned
# out at noon still says so at midnight.
THROTTLE_BITS = {
    0: "under-voltage",
    1: "arm frequency capped",
    2: "currently throttled",
    3: "soft temperature limit",
    16: "under-voltage has occurred",
    17: "arm frequency capping has occurred",
    18: "throttling has occurred",
    19: "soft temperature limit has occurred",
}


def soc_temp_c() -> float | None:
    try:
        return round(int(THERMAL.read_text().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def board_model() -> str | None:
    try:
        return MODEL.read_bytes().decode("utf8").rstrip("\x00").strip() or None
    except OSError:
        return None


def throttled() -> dict:
    """vcgencmd get_throttled, decoded. Empty-ish dict when not on a Pi."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": str(exc)}
    _, _, value = out.partition("=")
    try:
        bits = int(value, 16)
    except ValueError:
        return {"available": False, "reason": out or "no output"}
    flags = [text for bit, text in THROTTLE_BITS.items() if bits & (1 << bit)]
    return {
        "available": True,
        "raw": value or hex(bits),
        "flags": flags,
        # The one line a human should read.
        "power_ok": not (bits & (1 << 0) or bits & (1 << 16)),
    }


def disk_free(path: Path) -> dict:
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return {"available": False, "reason": str(exc)}
    return {
        "available": True,
        "free_mb": round(usage.free / 1e6),
        "percent_used": round(100 * usage.used / usage.total, 1),
        # Below this, SQLite is close to the point where WAL can't grow.
        "low": usage.free < 50e6,
    }


def load_average() -> list[float]:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):
        return []
