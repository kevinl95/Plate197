#!/bin/sh
# The only X client: Chromium, trimmed to what an 800x480 page needs.
set -eu

URL="${KIOSK_URL:-http://127.0.0.1:8197}"
PROFILE="${KIOSK_PROFILE:-/run/plate197-kiosk}"

# No screen blanking, no DPMS: brightness is backlight.py's job and the
# panel should never go black on its own at 9pm.
xset s off
xset s noblank
xset -dpms

CHROMIUM=$(command -v chromium-browser || command -v chromium || true)
if [ -z "$CHROMIUM" ]; then
    echo "kiosk: no chromium found" >&2
    exit 1
fi

mkdir -p "$PROFILE/cache"

# Chromium on 1GB is the tight part of this build. Everything below is
# either memory, or a feature that would phone home, or a dialog that
# must never appear on a picture frame.
# Raspberry Pi OS's chromium-browser wrapper prepends its own flags --
# --enable-gpu-rasterization and --use-angle=gles among them -- tuned for
# the V3D in a Pi 4/5. This is a Pi 3: VideoCore IV, GLES 2.0, and that
# combination is a well-known way to get a window that composites to
# nothing at all. A white rectangle, exactly the size of the page.
#
# Ours come last and win. This page is a dark ground, a handful of PNGs
# and some text; software rendering is more than enough for it, and it
# is the path that actually works on this board.
exec "$CHROMIUM" \
    --disable-gpu \
    --disable-gpu-compositing \
    --kiosk "$URL" \
    --window-size=800,480 \
    --window-position=0,0 \
    --user-data-dir="$PROFILE" \
    --disk-cache-dir="$PROFILE/cache" \
    --disk-cache-size=8388608 \
    --enable-low-end-device-mode \
    --process-per-site \
    --renderer-process-limit=1 \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=Translate,TranslateUI,MediaRouter,OptimizationHints,AutofillServerCommunication,CalculateNativeWinOcclusion,InterestFeedContentSuggestions \
    --no-first-run \
    --no-default-browser-check \
    --disable-default-apps \
    --disable-extensions \
    --disable-component-update \
    --disable-background-networking \
    --disable-sync \
    --disable-breakpad \
    --disable-crash-reporter \
    --disable-translate \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --hide-scrollbars \
    --force-device-scale-factor=1 \
    --password-store=basic \
    --check-for-update-interval=31536000 \
    --autoplay-policy=no-user-gesture-required \
    --remote-debugging-port=9222
