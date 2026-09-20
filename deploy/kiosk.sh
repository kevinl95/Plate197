#!/bin/sh
# Start X on tty1 with one client: the page.
#
# Waiting on /health rather than trusting unit ordering alone, because a
# blank white Chromium window that loaded too early is exactly the thing
# this frame must never show.
set -eu

URL="${KIOSK_URL:-http://127.0.0.1:8197}"

i=0
while ! curl -sf -o /dev/null --max-time 2 "$URL/health"; do
    i=$((i + 1))
    if [ "$i" -ge 90 ]; then
        echo "kiosk: $URL/health never answered; starting anyway" >&2
        break
    fi
    sleep 1
done

# -nocursor hides the pointer at the X level; the page hides it again in
# CSS. Both, because a stray white arrow on a dark plate is the kind of
# detail that makes a gift look unfinished.
exec xinit /opt/earshot/deploy/kiosk-session.sh -- :0 vt1 -nocursor -keeptty
