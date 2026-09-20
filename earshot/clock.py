"""Don't write 1970.

A Pi has no RTC. At boot the clock reads whatever the filesystem was
last stamped with until NTP answers, which on a cold morning can be a
minute or two after the microphone is already working. A detection
written in that window gets a timestamp that sorts before everything and
makes the page's "first ever" flags nonsense forever.

So detections are held with a monotonic reading instead, and only turned
into wall-clock time once the clock is believable. Nothing is lost: the
offset from `time.monotonic()` is still exact when the flush happens.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

# systemd-timesyncd touches this the moment it has synced.
TIMESYNC_FLAG = Path("/run/systemd/timesync/synchronized")


def is_sane(min_year: int = 2025) -> bool:
    if TIMESYNC_FLAG.exists():
        return True
    return datetime.now().year >= min_year


def synchronized() -> bool:
    """Has the clock actually been set by something authoritative?

    Distinct from is_sane(). A Pi has no RTC, so fake-hwclock restores
    whatever time the card was last shut down at -- a plausible year,
    which is_sane() accepts, and which can still be days wrong. The date
    picks the species list and the sunrise/sunset schedule, so being
    quietly wrong about it matters. This is the honest answer.
    """
    if TIMESYNC_FLAG.exists():
        return True
    try:
        import subprocess
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        return out.lower() == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


def wall_time_for(monotonic_at: float) -> datetime:
    """Local wall time of a moment recorded as `time.monotonic()`."""
    return datetime.fromtimestamp(time.time() - (time.monotonic() - monotonic_at))


def iso(moment: datetime) -> str:
    """ISO8601 local time, no offset — what the schema and the page expect.

    No offset is deliberate. The queries in the page's comment block
    compare against date('now','localtime'), which has none either, and
    JavaScript parses an offset-less timestamp as local. Adding one would
    break both.
    """
    return moment.replace(microsecond=0).isoformat(sep="T")
