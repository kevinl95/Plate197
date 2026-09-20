#!/usr/bin/env python3
"""Wi-Fi credentials from the boot partition.

Raspberry Pi Imager is supposed to do this, and when it doesn't there is
no way in on a frame with no keyboard: no console, no SSH, no network to
SSH over. This reads the same `plate197.toml` that already carries the
frame's settings, from the one partition any laptop can mount, and hands
it to NetworkManager.

    [wifi]
    ssid = "Some Network"
    password = "..."
    country = "US"

Runs every boot, and does nothing at all unless the file says something
different from last time -- an SD card does not need a rewrite on every
power-on. Change the SSID on a laptop, put the card back, and the next
boot picks it up.

The password sits in plaintext on a FAT32 partition. So does every other
headless Pi Wi-Fi method, including Imager's; anyone holding the card
can read it. The connection NetworkManager writes is mode 600 on the
root partition, which is the part that matters once it is running.
"""

from __future__ import annotations

import argparse
import hashlib
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

BOOT_DIRS = ("/boot/firmware", "/boot")
CONFIG_NAME = "plate197.toml"
# Remembers what was last applied, so an unchanged file is a no-op.
STAMP = Path("/var/lib/earshot/.wifi-applied")


def log(message: str) -> None:
    print(f"plate197-wifi: {message}", flush=True)


def _printable(args: list[str]) -> str:
    """Quoted for reading, with the passphrase kept out of the log."""
    shown, redact_next = [], False
    for arg in args:
        if redact_next:
            shown.append("'********'")
            redact_next = False
            continue
        shown.append(shlex.quote(arg))
        redact_next = arg == "wifi-sec.psk"
    return " ".join(shown)


def run(args: list[str], dry_run: bool = False) -> subprocess.CompletedProcess:
    if dry_run:
        log("would run: " + _printable(args))
        # returncode 1 so nothing downstream reports success it did not have
        return subprocess.CompletedProcess(args, 1, "", "(dry run)")
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def boot_config() -> tuple[Path | None, dict]:
    for directory in BOOT_DIRS:
        path = Path(directory) / CONFIG_NAME
        if not path.is_file():
            continue
        try:
            with path.open("rb") as fh:
                return path, tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            log(f"{path} could not be read ({exc}); ignoring it")
            return path, {}
    return None, {}


def fingerprint(ssid: str, password: str, country: str) -> str:
    """Identifies the settings without ever storing the password."""
    digest = hashlib.sha256()
    digest.update("\0".join((ssid, password, country)).encode("utf8"))
    return digest.hexdigest()


def set_country(country: str, dry_run: bool) -> None:
    """Unblock the radio and set the regulatory domain.

    On a Pi the wireless radio stays soft-blocked until a country is
    set, which is a very quiet way for wlan0 to simply never come up.
    """
    run(["rfkill", "unblock", "wifi"], dry_run)
    result = run(["raspi-config", "nonint", "do_wifi_country", country], dry_run)
    if result.returncode != 0:
        # Not a Pi, or raspi-config absent -- try the direct route.
        run(["iw", "reg", "set", country], dry_run)


def apply(ssid: str, password: str, country: str, hidden: bool,
          dry_run: bool) -> int:
    if country:
        set_country(country, dry_run)

    if not shutil_which("nmcli"):
        log("nmcli not found; this image does not use NetworkManager")
        return 1

    # Replace rather than edit: simpler than reasoning about whatever
    # state a half-configured connection was left in.
    run(["nmcli", "connection", "delete", ssid], dry_run)

    args = ["nmcli", "connection", "add",
            "type", "wifi", "con-name", ssid, "ssid", ssid,
            "connection.autoconnect", "yes"]
    # Bind to the interface when there is an obvious one, otherwise let
    # NetworkManager pick whatever wireless device the board has.
    if Path("/sys/class/net/wlan0").exists():
        args += ["ifname", "wlan0"]
    if password:
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
    if hidden:
        args += ["wifi.hidden", "yes"]
    result = run(args, dry_run)
    if result.returncode != 0 and not dry_run:
        log(f"could not add the connection: {result.stderr.strip()}")
        return 1

    result = run(["nmcli", "connection", "up", ssid], dry_run)
    if dry_run:
        log("dry run: nothing was changed")
    elif result.returncode != 0:
        # Worth saying, but not worth failing over: it may simply be out
        # of range right now and will autoconnect later.
        log(f"configured, but could not connect yet: {result.stderr.strip()}")
    else:
        log(f"connected to {ssid!r}")
    return 0


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would happen, change nothing")
    parser.add_argument("--force", action="store_true",
                        help="apply even if these settings were applied before")
    args = parser.parse_args(argv)

    path, config = boot_config()
    wifi = config.get("wifi") or {}
    ssid = str(wifi.get("ssid") or "").strip()
    if not ssid:
        # The overwhelmingly common case: Imager did its job, or there is
        # no wifi section. Say nothing and cost nothing.
        return 0

    password = str(wifi.get("password") or "")
    country = str(wifi.get("country") or "").strip().upper()
    hidden = bool(wifi.get("hidden") or False)

    mark = fingerprint(ssid, password, country)
    if not args.force and not args.dry_run:
        try:
            if STAMP.read_text().strip() == mark:
                return 0          # unchanged since last boot
        except OSError:
            pass

    log(f"applying wifi settings from {path} for {ssid!r}")
    status = apply(ssid, password, country, hidden, args.dry_run)

    if status == 0 and not args.dry_run:
        try:
            STAMP.parent.mkdir(parents=True, exist_ok=True)
            STAMP.write_text(mark)
        except OSError as exc:
            log(f"could not record what was applied ({exc}); "
                "it will be reapplied next boot")
    return status


if __name__ == "__main__":
    sys.exit(main())
