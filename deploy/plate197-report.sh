#!/bin/bash
# What happened on this boot, written somewhere you can read without a
# keyboard, a monitor, or a network.
#
# The boot partition is FAT32, so pulling the SD card and putting it in
# any laptop shows this file at the top level of the only partition that
# machine will mount. That is the entire point: a frame with no keyboard
# that comes up wrong is otherwise a black box.
#
# Rewritten every boot. Never fails, never blocks the boot.

set +e

# Overridable so this can be exercised somewhere other than a Pi.
BOOT="${PLATE197_BOOT_DIR:-}"
if [ -z "$BOOT" ]; then
    BOOT=/boot/firmware
    [ -d "$BOOT" ] || BOOT=/boot
fi
OUT="${BOOT}/plate197-report.txt"

# Let the units get where they are going first -- a report taken at
# second 3 of a 90-second wait for /health says nothing useful.
sleep "${PLATE197_REPORT_DELAY:-60}"

section() { printf '\n\n===== %s =====\n' "$1"; }

{
    echo "Plate 197 boot report"
    echo "written: $(date -Is 2>/dev/null)"
    echo "uptime:  $(uptime -p 2>/dev/null)"
    echo "host:    $(hostname 2>/dev/null)   $(uname -srm 2>/dev/null)"
    if [ -r /sys/firmware/devicetree/base/model ]; then
        echo "model:   $(tr -d '\0' < /sys/firmware/devicetree/base/model)"
    else
        echo "model:   unknown (no devicetree -- not a Pi?)"
    fi

    section "THE ONE-LINE ANSWER"
    # Ordered so the most common failures are stated plainly rather than
    # left for someone to infer from a wall of systemctl output.
    if systemctl is-active --quiet plate197-kiosk.service; then
        echo "kiosk service: RUNNING"
    else
        echo "kiosk service: NOT RUNNING ($(systemctl is-active plate197-kiosk.service 2>&1))"
    fi
    if systemctl is-active --quiet getty@tty1.service; then
        echo "getty on tty1: RUNNING  <-- this is what puts a login on the panel"
    else
        echo "getty on tty1: not running (good)"
    fi
    echo "getty@tty1 is-enabled: $(systemctl is-enabled getty@tty1.service 2>&1)"
    echo "earshot service: $(systemctl is-active earshot.service 2>&1)"
    # No RTC on a Pi: an unsynchronised clock is plausible-looking and
    # wrong, which silently changes the species list and the schedule.
    if [ -e /run/systemd/timesync/synchronized ] \
       || [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ]; then
        echo "clock:           synchronised ($(date -Is 2>/dev/null))"
    else
        echo "clock:           NOT SYNCHRONISED -- $(date -Is 2>/dev/null) may be wrong"
    fi
    echo "health endpoint: $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8197/health 2>&1)"
    echo "chromium binary: $(command -v chromium-browser || command -v chromium || echo 'NOT FOUND')"
    echo "X binary:        $(command -v Xorg || command -v X || echo 'NOT FOUND')"

    section "FAILED UNITS"
    systemctl list-units --failed --no-pager --no-legend 2>&1

    for unit in plate197-kiosk earshot plate197-backlight getty@tty1; do
        section "systemctl status ${unit}"
        systemctl status "${unit}.service" --no-pager -l 2>&1 | head -40
    done

    for unit in plate197-kiosk earshot plate197-backlight; do
        section "journal: ${unit} (this boot)"
        journalctl -u "${unit}.service" -b --no-pager 2>&1 | tail -60
    done

    section "Xorg log"
    for log in /var/log/Xorg.0.log /home/kiosk/.local/share/xorg/Xorg.0.log; do
        if [ -f "$log" ]; then
            echo "--- $log"
            tail -50 "$log" 2>&1
        fi
    done

    section "hardware the kiosk needs"
    echo "--- /dev/dri (graphics)"; ls -la /dev/dri 2>&1
    echo "--- /dev/tty1";           ls -la /dev/tty1 2>&1
    echo "--- kiosk user";          id kiosk 2>&1
    echo "--- backlight";           ls -la /sys/class/backlight/ 2>&1
    echo "--- audio capture";       arecord -l 2>&1 | head -20

    section "the units as installed"
    for f in /etc/systemd/system/plate197-kiosk.service /etc/systemd/system/earshot.service; do
        echo "--- $f"; cat "$f" 2>&1; echo
    done
    echo "--- getty@tty1 mask state (should be a symlink to /dev/null)"
    ls -la /etc/systemd/system/getty@tty1.service 2>&1

    section "python versions (ABI mismatches live here)"
    # tflite-runtime declares numpy>=1.23.2 with no upper bound, but its
    # C extension is built against NumPy 1.x -- install it next to
    # NumPy 2 and every model load dies with "_ARRAY_API not found".
    # Cheap to print, and it names that failure on sight.
    /opt/earshot/venv/bin/python -c "
import importlib, importlib.metadata as md
for dist, mod in (('numpy','numpy'), ('tflite-runtime','tflite_runtime'),
                  ('ai-edge-litert','ai_edge_litert'), ('flask','flask'),
                  ('sounddevice','sounddevice'), ('pillow','PIL')):
    try:
        version = md.version(dist)
    except Exception:
        version = 'not installed'
    try:
        importlib.import_module(mod)
        status = 'imports ok'
    except Exception as exc:
        status = f'IMPORT FAILS -- {exc.__class__.__name__}: {exc}'
    print(f'  {dist:16} {version:16} {status}')
" 2>&1

    section "earshot state (are the models actually there?)"
    # A missing or half-downloaded model is the likeliest reason the
    # page has nothing to show. audio-model.tflite should be ~52MB.
    ls -la /var/lib/earshot/ 2>&1
    echo "--- models"
    ls -la /var/lib/earshot/models/ 2>&1
    echo "--- database"
    ls -la /var/lib/earshot/detections.db 2>&1

    section "is it actually hearing anything?"
    echo "--- detections recorded (all time / today)"
    sqlite3 /var/lib/earshot/detections.db \
        "select count(*) || ' total', (select count(*) from detections where ts >= date('now','localtime')) || ' today' from detections;" 2>&1
    echo "--- the last 10, whatever their confidence"
    sqlite3 -header -column /var/lib/earshot/detections.db \
        "select species, round(conf,3) as conf, ts from detections order by id desc limit 10;" 2>&1
    echo "--- /recent (what the page is being given)"
    curl -s -m 5 http://127.0.0.1:8197/recent 2>&1 | head -c 1200
    echo
    echo "--- note: nothing is written below min_conf, so an empty table"
    echo "    can simply mean every call scored under the threshold."

    section "first-boot account wizard (fights the kiosk for tty1)"
    for unit in userconfig userconf regenerate_ssh_host_keys; do
        state=$(systemctl is-enabled "${unit}.service" 2>&1)
        active=$(systemctl is-active "${unit}.service" 2>&1)
        echo "  ${unit}.service: ${state} / ${active}"
    done
    echo "--- has it been failing repeatedly?"
    systemctl show -p NRestarts --value userconfig.service 2>&1 \
        | sed 's/^/  restarts: /'
    journalctl -u userconfig.service -b --no-pager 2>&1 | tail -20
    echo "--- is there still a userconf.txt waiting to be consumed?"
    ls -la "${BOOT}/userconf.txt" 2>&1
    echo "--- did the default user actually get created?"
    getent passwd pi 2>&1 || echo "  no 'pi' user"
    echo "--- everything holding a tty"
    systemctl list-units --all --no-pager --no-legend 2>/dev/null \
        | grep -iE "getty|userconf|kiosk" | head -10
    echo "--- which VT is on screen right now?"
    fgconsole 2>&1 || echo "  (fgconsole unavailable)"

    section "memory (1GB, shared with Chromium and a 52MB model)"
    free -m 2>&1
    echo "--- anything killed for memory this boot?"
    dmesg 2>/dev/null | grep -iE "out of memory|killed process|oom-kill" | tail -10 \
        || echo "  (nothing, or dmesg not readable)"
    echo "--- per-unit memory"
    for unit in earshot plate197-kiosk; do
        echo "  ${unit}: $(systemctl show -p MemoryCurrent --value ${unit}.service 2>&1)"
    done
    echo "--- biggest processes"
    ps -eo rss,comm --sort=-rss 2>&1 | head -8

    section "what is actually on the screen"
    echo "--- is chromium running?"
    pgrep -a -f chromium 2>&1 | head -5 || echo "  NO CHROMIUM PROCESS"
    echo "--- is X running?"
    pgrep -a -f "Xorg|xinit" 2>&1 | head -5 || echo "  NO X PROCESS"
    echo "--- first bytes of what / serves (should be the dark page)"
    curl -s -m 5 http://127.0.0.1:8197/ 2>&1 | head -c 400
    echo
    echo "--- what did the running Chromium actually load?"
    # Tells "never loaded the page" apart from "loaded it and painted
    # nothing", which look identical on a blank panel.
    curl -s -m 5 http://127.0.0.1:9222/json/list 2>/dev/null \
        | grep -oE '"(title|url)": "[^"]*"' | head -6 \
        || echo "  no devtools endpoint (older build, or chromium not up)"

    echo "--- render the same page headlessly, as a picture you can look at"
    # If this PNG is dark, the page is fine and the fault is in the
    # kiosk's rendering path. If it is white, the page itself will not
    # render on this board. Either way it is on the boot partition.
    CHROMIUM_BIN=$(command -v chromium-browser || command -v chromium || true)
    if [ -n "$CHROMIUM_BIN" ]; then
        rm -rf /tmp/plate197-shot && mkdir -p /tmp/plate197-shot
        timeout 90 "$CHROMIUM_BIN" --headless --no-sandbox --disable-gpu \
            --user-data-dir=/tmp/plate197-shot \
            --window-size=800,480 \
            --screenshot="${BOOT}/plate197-screen.png" \
            http://127.0.0.1:8197/ >/dev/null 2>&1
        ls -la "${BOOT}/plate197-screen.png" 2>&1
    else
        echo "  no chromium binary to render with"
    fi

    echo "--- typeface: served from here, or reaching for the network?"
    ls -la /opt/earshot/fonts/spectral.css 2>&1
    curl -s -m 5 http://127.0.0.1:8197/ 2>/dev/null | grep -c "fonts.googleapis.com" \
        | sed 's/^0$/  0 external font references (good)/; s/^[1-9].*/  STILL REACHING FOR GOOGLE FONTS/'
    echo "--- can the page file be read by the service user?"
    ls -la /opt/earshot/index-7inch.html 2>&1
    sudo -u earshot test -r /opt/earshot/index-7inch.html 2>/dev/null \
        && echo "  readable by earshot: yes" || echo "  readable by earshot: NO"

    section "config"
    echo "--- /etc/earshot.toml"; cat /etc/earshot.toml 2>&1
    echo "--- custom.toml from Raspberry Pi Imager present?"
    ls -la "${BOOT}/custom.toml" 2>&1
    echo "--- network"
    ip -brief address 2>&1
    echo "--- is the wireless radio blocked? (no country set = blocked)"
    rfkill list wifi 2>&1 | head -6
    echo "--- regulatory domain"
    iw reg get 2>&1 | grep -m1 country || echo "  (iw unavailable)"
    echo "--- NetworkManager connections"
    nmcli -t -f NAME,TYPE,DEVICE connection show 2>&1 | head -6
    echo "--- did plate197-wifi apply anything?"
    systemctl is-enabled plate197-wifi.service 2>&1
    journalctl -u plate197-wifi.service -b --no-pager 2>&1 | tail -12
    echo "--- is there a [wifi] section on the boot partition?"
    grep -c "^\[wifi\]" "${BOOT}/plate197.toml" 2>/dev/null \
        | sed 's/^0$/  no (relying on Imager)/; s/^1$/  yes/' || echo "  no plate197.toml"

    section "end of report"
} > "${OUT}.tmp" 2>&1

# Swap in whole, so pulling the card mid-write can't show half a file.
mv -f "${OUT}.tmp" "${OUT}" 2>/dev/null
sync 2>/dev/null
exit 0
