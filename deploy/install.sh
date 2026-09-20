#!/usr/bin/env bash
# Install earshot and the frame's two other units on a Raspberry Pi.
#
#   sudo deploy/install.sh              everything, including the kiosk
#   sudo deploy/install.sh --no-kiosk   service only (headless development)
#
# Idempotent: run it again after a git pull and it will update in place.
set -euo pipefail

PREFIX=/opt/earshot
STATE=/var/lib/earshot
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
WITH_KIOSK=1
BOOT_CONFIG=/boot/firmware/config.txt        # bookworm and later
[ -f "$BOOT_CONFIG" ] || BOOT_CONFIG=/boot/config.txt

for arg in "$@"; do
    case "$arg" in
        --no-kiosk) WITH_KIOSK=0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

[ "$(id -u)" = "0" ] || { echo "run this with sudo" >&2; exit 1; }

say() { printf '\n== %s\n' "$*"; }

say "packages"
apt-get update -qq
PACKAGES="python3-venv python3-pip libportaudio2 curl sqlite3"
if [ "$WITH_KIOSK" = "1" ]; then
    # Pi OS Lite has no X at all. xinit plus one client is the lightest
    # way to get Chromium onto the panel on a 1GB board.
    PACKAGES="$PACKAGES xserver-xorg xinit x11-xserver-utils chromium-browser"
fi
# shellcheck disable=SC2086
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $PACKAGES

say "users"
id -u earshot >/dev/null 2>&1 || useradd --system --home "$STATE" --shell /usr/sbin/nologin earshot
usermod -aG audio,video earshot
if [ "$WITH_KIOSK" = "1" ]; then
    id -u kiosk >/dev/null 2>&1 || useradd --create-home --shell /bin/bash kiosk
    usermod -aG video,render,input,tty kiosk
fi

say "code into $PREFIX"
mkdir -p "$PREFIX"
# Everything the service needs at runtime, and nothing else.
for item in earshot deploy index-7inch.html backlight.py pyproject.toml README.md; do
    [ -e "$SOURCE/$item" ] && cp -r "$SOURCE/$item" "$PREFIX/"
done
[ -d "$SOURCE/plates" ] && cp -r "$SOURCE/plates" "$PREFIX/"
chmod +x "$PREFIX"/deploy/*.sh
# Some repo files are mode 600; as root-owned copies the service user
# cannot read them. a+rX = readable files, traversable directories.
chmod -R a+rX "$PREFIX"

say "virtualenv"
[ -d "$PREFIX/venv" ] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip wheel
"$PREFIX/venv/bin/pip" install --quiet "$PREFIX"
"$PREFIX/venv/bin/pip" install --quiet rpi-backlight astral
# For `earshot plates --fix` -- not used by the running service, but
# small, and worth having on hand for whoever's adding artwork.
"$PREFIX/venv/bin/pip" install --quiet pillow

say "tflite interpreter"
# tflite-runtime is the small one (2.3MB) but its newest arm64 wheels are
# cp311, i.e. Bookworm. Anything newer gets ai-edge-litert, which is the
# same interpreter under Google's current name.
if "$PREFIX/venv/bin/pip" install --quiet tflite-runtime 2>/dev/null; then
    echo "   tflite-runtime"
    # Built against NumPy 1.x; under NumPy 2 its C extension fails to
    # import and no model will ever load.
    "$PREFIX/venv/bin/pip" install --quiet 'numpy<2'
else
    echo "   no tflite-runtime wheel for this Python; using ai-edge-litert"
    "$PREFIX/venv/bin/pip" install --quiet ai-edge-litert
fi

say "boot-partition settings file"
# The one settings file reachable without a keyboard or SSH.
BOOTFS=/boot/firmware
[ -d "$BOOTFS" ] || BOOTFS=/boot
TEMPLATE="$SOURCE/distro/src/modules/plate197/filesystem/boot/plate197.toml"
if [ -f "$TEMPLATE" ] && [ -d "$BOOTFS" ] && [ ! -f "$BOOTFS/plate197.toml" ]; then
    cp "$TEMPLATE" "$BOOTFS/plate197.toml"
    echo "   $BOOTFS/plate197.toml (edit it from any laptop)"
fi

say "state directory"
mkdir -p "$STATE"
chown -R earshot:earshot "$STATE"

say "BirdNET v2.4 models (~120MB, once)"
if [ -f /etc/earshot.toml ]; then
    echo "   keeping existing /etc/earshot.toml"
else
    cp "$PREFIX/deploy/earshot.toml.example" /etc/earshot.toml
fi
sudo -u earshot EARSHOT_DATA_DIR="$STATE" "$PREFIX/venv/bin/python" -m earshot models

say "Spectral, kept on the card"
# The page must not reach for fonts.googleapis.com at runtime.
"$PREFIX/venv/bin/python" -c "
import pathlib, sys
sys.path.insert(0, '$PREFIX')
from earshot import fonts
try:
    print('  ', fonts.fetch(pathlib.Path('$PREFIX/fonts')), 'font files')
except Exception as exc:
    print('   could not fetch Spectral (', exc, '); using the system serif')
" || true
chmod -R a+rX "$PREFIX/fonts" 2>/dev/null || true

say "panel backlight"
# /sys/class/backlight is empty until this overlay is loaded; vc4-kms-v3d
# brings up video and touch but not the backlight.
if ! grep -q '^dtoverlay=rpi-backlight' "$BOOT_CONFIG"; then
    printf '\n# Plate 197: backlight control for the official 7" panel\ndtoverlay=rpi-backlight\n' >> "$BOOT_CONFIG"
    echo "   added dtoverlay=rpi-backlight to $BOOT_CONFIG (reboot needed)"
fi
cat > /etc/udev/rules.d/99-backlight-permissions.rules <<'RULE'
SUBSYSTEM=="backlight",RUN+="/bin/chmod 666 /sys/class/backlight/%k/brightness /sys/class/backlight/%k/bl_power"
RULE
udevadm control --reload || true

if [ "$WITH_KIOSK" = "1" ]; then
    say "X for a non-root user"
    cat > /etc/X11/Xwrapper.config <<'WRAP'
allowed_users=anybody
needs_root_rights=yes
WRAP
    # Nothing should be printing kernel messages onto the panel.
    if ! grep -q 'consoleblank' "$BOOT_CONFIG" 2>/dev/null; then
        printf 'disable_splash=1\n' >> "$BOOT_CONFIG"
    fi
fi

say "units"
cp "$PREFIX/deploy/earshot.service" /etc/systemd/system/
cp "$PREFIX/deploy/plate197-backlight.service" /etc/systemd/system/
cp "$PREFIX/deploy/plate197-report.service" /etc/systemd/system/
cp "$PREFIX/deploy/plate197-wifi.service" /etc/systemd/system/
[ "$WITH_KIOSK" = "1" ] && cp "$PREFIX/deploy/plate197-kiosk.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now earshot.service
systemctl enable plate197-backlight.service
# Each boot, dumps its own diagnosis to the boot partition.
systemctl enable plate197-report.service
# And reads wifi credentials from it, if Imager's did not take.
systemctl enable plate197-wifi.service
if [ "$WITH_KIOSK" = "1" ]; then
    systemctl enable plate197-kiosk.service
    # Otherwise tty1's default console login starts first and wins the
    # tty, showing a login prompt instead of the kiosk. Only for a kiosk
    # install -- a headless box should keep its console.
    #
    # mask, not disable: disable only removes an /etc symlink, and this
    # unit is often enabled from the vendor tree instead, where disable
    # silently does nothing. Reverse with `systemctl unmask`.
    systemctl mask --now getty@tty1.service
fi

say "done"
cat <<'NEXT'
Check it:
    sudo -u earshot EARSHOT_DATA_DIR=/var/lib/earshot /opt/earshot/venv/bin/python -m earshot check
    curl -s localhost:8197/health | python3 -m json.tool
    journalctl -u earshot -f

If the mic is not the one it picked, `earshot devices` lists the names;
put a distinctive substring in /etc/earshot.toml as device_match.

Reboot to bring up the panel and the backlight overlay.
NEXT
