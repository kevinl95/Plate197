#!/usr/bin/env python3
"""
Panel brightness for The Window.

The page can't touch /sys/class/backlight, so the schedule lives here.
The display should never be the brightest thing in a dark room — it
follows the light outside instead of sitting at one level all day.

Setup on the Pi:
    # vc4-kms-v3d handles video + touch but NOT backlight, so
    # /sys/class/backlight/ is empty until you add this overlay:
    echo 'dtoverlay=rpi-backlight' | sudo tee -a /boot/firmware/config.txt

    # let non-root write it
    echo 'SUBSYSTEM=="backlight",RUN+="/bin/chmod 666 /sys/class/backlight/%k/brightness /sys/class/backlight/%k/bl_power"' \
      | sudo tee /etc/udev/rules.d/backlight-permissions.rules
    sudo reboot

    pip install rpi-backlight astral
    # then run this from systemd
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from rpi_backlight import Backlight
from astral import LocationInfo
from astral.sun import sun


def _timezone(default="America/Denver"):
    """Whatever the system is set to; the panel follows the same clock."""
    try:
        return Path("/etc/timezone").read_text().strip() or default
    except OSError:
        pass
    try:
        return os.readlink("/etc/localtime").split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return default


def _home():
    """Where the window is.

    The coordinates below are a placeholder — this repository is public
    and the feeder's are not. The real ones live in /etc/earshot.toml,
    which is not committed, so the schedule and the bird detection agree
    on one answer without either of them being written down here.
    """
    latitude, longitude = 39.7392, -104.9903        # Denver, Colorado
    try:
        from earshot.config import load
        settings = load()
        latitude, longitude = settings.latitude, settings.longitude
    except Exception:
        pass                      # standalone: the placeholder will do
    return LocationInfo("home", "", _timezone(), latitude, longitude)


HOME = _home()

# brightness is 0-100 in rpi-backlight's API (0-255 in the raw sysfs file)
DAY     = 85   # full sun through the window
EVENING = 35   # after sunset, still clearly readable across the room
NIGHT   = 6    # a faint glow — you can see a bird is there, nothing more
OFF_AT  = 23   # hour to drop to NIGHT
ON_AT   = 5    # hour to come back up

FADE_S  = 45.0   # slow enough that nobody sees it happen


def target_for(now, s):
    """Pick a level for this moment."""
    if now.hour >= OFF_AT or now.hour < ON_AT:
        return NIGHT
    # ramp up with the actual sunrise rather than a fixed clock time,
    # so it tracks the season the same way the birds do
    if s["dawn"] <= now <= s["sunset"]:
        return DAY
    return EVENING


def main():
    backlight = Backlight()
    backlight.fade_duration = FADE_S
    last = None

    while True:
        now = datetime.now(HOME.tzinfo)
        try:
            s = sun(HOME.observer, date=now.date(), tzinfo=HOME.tzinfo)
        except Exception:
            # polar edge cases / bad clock — hold at evening rather than
            # blinding anyone or going dark at noon
            s = {"dawn": now - timedelta(hours=1), "sunset": now + timedelta(hours=1)}

        want = target_for(now, s)
        if want != last:
            with backlight.fade(duration=FADE_S):
                backlight.brightness = want
            last = want

        time.sleep(120)


if __name__ == "__main__":
    main()
