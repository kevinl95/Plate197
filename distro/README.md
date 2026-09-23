# Plate197 image

A CustomPiOS build of the whole frame: Raspberry Pi OS Lite 64-bit,
plus `earshot`, the backlight schedule, and a Chromium kiosk pointed at
the page. Write it to a card, plug in the mic, and it comes up listening.

## Build

    distro/build.sh

Needs sudo and about 6GB free. The first run downloads CustomPiOS and
the base image; later runs reuse both. The finished image lands in
`distro/src/workspace/`. See "Finding the finished image" below.

### What the host needs first

The build loop-mounts an ARM image and runs `apt` *inside* it. On an
x86_64 machine that only works if the kernel knows how to execute arm64
binaries, so `qemu-aarch64-static` has to be installed *and* registered
with binfmt. Everything else is ordinary disk plumbing.

```sh
# Debian / Ubuntu
sudo apt install qemu-user-static binfmt-support kpartx parted \
     e2fsprogs zip unzip xz-utils p7zip-full rsync curl git

# Fedora
sudo dnf install qemu-user-static-aarch64 kpartx parted \
     e2fsprogs zip unzip xz 7zip rsync curl git
```

Check the registration took. This is the failure that looks like a
broken module rather than a missing package: an unregistered binfmt
shows up twenty minutes in as `Exec format error` from somewhere deep
inside the chroot.

```sh
test -e /proc/sys/fs/binfmt_misc/qemu-aarch64 && echo ok
```

Building on a Raspberry Pi itself needs none of this: it is already
arm64, so there is nothing to emulate.

### Artwork goes in before the build

`build.sh` copies `plates/` into the image if it exists, so drop your
PNGs in there first and they are on the card when it boots. They are
gitignored deliberately, because the Audubon scans are not ours to
redistribute. That also means a fresh `git clone` has none, and an image
built from one draws the silhouettes instead. See
"Artwork" in the top-level README for naming.

Everything configurable is an environment variable:

| Variable | Default | What it does |
|---|---|---|
| `PLATE197_LATITUDE` / `PLATE197_LONGITUDE` | Denver, Colorado | Feeds BirdNET's range model. A placeholder; see below. |
| `PLATE197_LOCATION` | `Colorado` | The place name printed in the page's footer. |
| `PLATE197_TIMEZONE` | `America/Denver` | Also what every "first today" is measured against. |
| `PLATE197_DEVICE_MATCH` | `USB` | Substring of the microphone's name. |
| `PLATE197_MIN_CONF` | `0.65` | Minimum confidence for writing a detection down. |
| `PLATE197_MODEL_PRECISION` | `fp32` | `fp32` (52MB, reference), `fp16` (26MB), `int8` (41MB, quicker). |
| `PLATE197_BAKE_MODELS` | `yes` | Put the models in the image so first boot needs no network. |
| `PLATE197_KIOSK` | `yes` | `no` builds the service without X or Chromium. |
| `PLATE197_PORT` | `8197` | Where the page and the API live. |
| `PLATE197_WIFI_SSID` | *(empty)* | Bake Wi-Fi into the card. Empty ships a card that asks later. |
| `PLATE197_WIFI_PASSWORD` | *(empty)* | Goes in `config.local`, never in the repo. |
| `PLATE197_WIFI_COUNTRY` | `US` | Required whenever an SSID is set. See below. |
| `PLATE197_WIFI_HIDDEN` | `no` | For a network that does not broadcast. |

The coordinates matter more than they look. BirdNET's meta-model turns a
place and a week into the list of species that could plausibly be there,
which cuts 6522 classes to about 140 and removes most of the ways this
can embarrass itself. An image built with the wrong coordinates still
works; it just gets worse at its job.

The defaults are a placeholder pointing at the middle of Denver, because
this repository is public. Real coordinates belong in
`distro/src/config.local`, which is gitignored and which CustomPiOS
sources automatically:

```sh
# distro/src/config.local, never committed
export PLATE197_LATITUDE=00.0000
export PLATE197_LONGITUDE=-000.0000
export PLATE197_LOCATION="Your Town"

# Baked into the card, so it joins the network on first boot.
export PLATE197_WIFI_SSID="Your Network"
export PLATE197_WIFI_PASSWORD="your password"
export PLATE197_WIFI_COUNTRY="US"
```

## Why the base image is pinned to Bookworm

Raspberry Pi OS Lite arm64 has shipped Trixie (Debian 13, Python 3.13)
since late 2025. There is no arm64 `tflite-runtime` wheel for 3.13; that
project's newest release, 2.14.0, publishes cp38 through cp311 only. A
Trixie image therefore has to use `ai-edge-litert`, the same TFLite
interpreter under Google's current name, at 14MB rather than 2.3MB.

Both work, and `earshot` picks up whichever is installed without being
told. The image pins the archived 2024-11-19 Bookworm release (Python
3.11) because it is deterministic, it is what the Pi 3 is best tested on,
and the small interpreter is the one to want on a 1GB board.

To build on current Pi OS instead, override the base image:

    BASE_IMAGE_URL=https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-09-15/2026-09-15-raspios-trixie-arm64-lite.img.xz distro/build.sh

The chroot script tries `tflite-runtime` first and falls back on its own.

## If the build fails

`Temporary failure resolving 'deb.debian.org'`, nothing installed.
Before it runs apt, CustomPiOS points the chroot at 8.8.8.8, 8.8.4.4 and
1.1.1.1. Behind a VPN, particularly Mullvad and WireGuard setups, or any
resolver that refuses to forward elsewhere, all three are unreachable,
every fetch fails, and the build stops at the first `apt-get update`
looking like a broken module. It is not.

`distro/src/config` now detects the host's real resolvers
(systemd-resolved's upstreams, not its 127.0.0.53 stub) and passes them
in. Override it if you need to:

    BASE_USE_ALT_DNS="10.0.0.1 10.0.0.2" distro/build.sh

It leaves several GB behind. `distro/src/workspace/` holds the mounted
image and an apt cache, and `distro/src/image-raspberrypiarm64/` holds
the ~460MB base download. Both are gitignored. The workspace is
disposable apart from the one file described next, which is the whole
point of the build.

## Finding the finished image

`build.sh` mounts, customises, unmounts, then packages: it hands off to
CustomPiOS's own `release` script, which renames the result to
`plate197-<version>.img`, zips it, and writes a `.sha256` beside it.
All of that lands in `distro/src/workspace/`, not `distro/workspace/`.
An earlier version of this script got that path wrong in its own "done"
message, so if a build you ran before now printed a workspace that
didn't exist, that is why; the image was there all along.

If packaging fails for any reason, the raw image is still sitting in
that same folder under the *base* image's own filename
(`2024-11-19-raspios-bookworm-arm64-lite.img`). Flashing that file
directly with Raspberry Pi Imager's "Use custom" works exactly the same
as flashing the packaged `.zip`. The rename-and-compress step is for
tidiness and distribution, not something the Pi cares about.

## Wi-Fi, via Raspberry Pi Imager

Before writing the image, Imager's gear icon (or Ctrl+Shift+X) opens
"OS Customisation", whose Wi-Fi tab has one job: it connects the Pi to
a network you already have, using the SSID and password you type in
there. It does not put the Pi into access-point mode or have it create a
network of its own. Nothing in this image or in Imager does that.

This works the same way whether you picked the image from Imager's own
catalog or with "Use custom" pointed at this build. Imager writes the
same customisation payload to the boot partition either way, regardless
of where the `.img` came from. What can vary is whether the
*image being flashed* still has the machinery to read that payload on
first boot, and ours does: we build from an unmodified official
Raspberry Pi OS image and only add software inside the chroot, never
touching `raspberrypi-sys-mods` or NetworkManager, which is what
applies it. Bookworm dropped the old `wpa_supplicant.conf`-in-boot-
partition trick in favour of NetworkManager plus a `custom.toml` file
Imager drops in `/boot/firmware/`; nothing under `distro/` interferes
with that path.

If you want to be certain rather than take this on faith, which is
reasonable for a device you're about to seal into a frame, pull the SD
card's boot partition back out on your computer right after Imager finishes
and before first boot, and check for `custom.toml` (or, on older
Imager versions, `firstrun.sh` plus a modified `cmdline.txt`) sitting
there. If it's present, the Pi will apply it on its own; if you don't
see it, the Wi-Fi tab's settings didn't get written and it's worth
re-running Imager rather than finding out at boot.

## When the panel comes up wrong and there is no keyboard

Every boot writes `plate197-report.txt` to the boot partition. That
partition is FAT32, so the way to read it is to power the Pi down, pull
the SD card, and put it in any laptop. It sits at the top level of the
one partition that machine will mount, and needs no Linux to read.

It opens with the short version:

```
===== THE ONE-LINE ANSWER =====
kiosk service: NOT RUNNING (failed)
getty on tty1: RUNNING  <-- this is what puts a login on the panel
getty@tty1 is-enabled: masked
earshot service: active
health endpoint: 200
chromium binary: /usr/bin/chromium
X binary:        /usr/bin/Xorg
```

and then carries the full `systemctl status` and journal for each unit,
the Xorg log, whether `/dev/dri` and the backlight exist, the units as
actually installed, and whether Imager left a `custom.toml`. It is taken
60 seconds in, so it describes a settled system rather than one still
starting up.

It never blocks or fails the boot; if something in it errors, that error
is simply part of the report.

## Changing a setting without a keyboard

The card's boot partition carries `plate197.toml`, and that partition is
FAT32, so any laptop will mount it. Pull the card, uncomment a line, put
it back, power on:

```toml
# min_conf = 0.25
```

Anything set there overrides what the image was built with; anything
left commented keeps the built-in value, so changing one key does not
wipe the coordinates the build baked in. A typo is skipped rather than
fatal, so the frame still starts.

This is the only way to change behaviour on an assembled frame with no
keyboard and no SSH, short of building and flashing again.

`min_conf` is the one worth knowing about while testing. 0.65 is
deliberately strict for a feeder a few feet from the microphone; a call
played from a phone speaker across a room routinely scores well under
it, and nothing below the threshold is written down at all.

## What the network is still for

Almost nothing, and that is deliberate. The BirdNET models, the Audubon
plates, the typeface and the page are all on the card; everything the
frame draws is served from localhost. Unplug the router and it keeps
listening, detecting and displaying.

What it cannot do offline is know the date, and a Pi has no RTC. With
no NTP, `fake-hwclock` restores whatever time the card was last shut
down at. That gives a plausible year, so nothing looks obviously broken,
and that date quietly decides:

- which species are considered plausible (the range model works in
  weeks of the year)
- when sunrise and sunset are, which is the whole backlight schedule
- when "today" starts, which every `first today` and `first ever`
  answer is measured against

So Wi-Fi is a correctness dependency rather than a functional one. The
frame works without it; it just doesn't know *when* it is. `/health`
and the boot report both say outright whether the clock has actually
been synchronised, so a wrong one is visible rather than silent.

## Wi-Fi when Imager's settings do not take

Two ways in, and they end up in the same place.

Before the build, put it in `distro/src/config.local` (gitignored) as
shown above. The build writes it into `plate197.toml` on the boot
partition and the card joins the network the first time it is powered
on, with nothing to edit.

One thing to be aware of: a password baked in this way travels inside
the `.img`. That is fine for a card you are building for yourself, and
not fine for an image you intend to hand to anyone else. For that,
leave the SSID empty and let the recipient fill it in.

After the build, edit the same `plate197.toml` on the boot partition
from any laptop:

```toml
[wifi]
ssid = "Your Network"
password = "your password"
country = "US"
```

Put the card back, boot, and `plate197-wifi.service` hands it to
NetworkManager. Changing it later is the same loop. An unchanged file
is a no-op, because it only reapplies when something actually differs,
so the card is not rewritten on every power-on.

`country` is not optional on a Pi. The wireless radio stays
soft-blocked until a regulatory domain is set, which is a very quiet way
for `wlan0` to never come up at all, and quite possibly what you are
looking at now. The service unblocks the radio and sets the domain
before it does anything else.

To see what it would do without doing it:

    sudo /opt/earshot/venv/bin/python /opt/earshot/deploy/plate197-wifi.py --dry-run

A mistake here can't stop the frame booting: a malformed file is
ignored, and a failure to connect is logged rather than fatal. The frame
works offline, it just can't set its clock.

The password sits in plaintext on a partition any computer can read.
The same is true of every other headless Pi Wi-Fi method, Imager's
included. The connection NetworkManager writes is mode 600 on the root
partition.

## The typeface is on the card, not on the internet

The page names Spectral, and originally fetched it from
fonts.googleapis.com with a `<link rel="stylesheet">`. That is
render-blocking: with no route to the internet Chromium paints
*nothing* until DNS gives up, so the panel is white, which is the blank
screen this project is not allowed to show. A frame sitting on a table
also has no business phoning Google to draw its own footer.

The build downloads the three faces the page actually asks for (300,
400, 300 italic; about 220KB including every unicode subset) into
`/opt/earshot/fonts/` and serves them from the Pi. Nothing touches the
network at runtime.

If that download ever fails the build carries on: the page asks for
`/fonts/spectral.css`, gets an instant 404 from localhost, and falls
through to the system serif. `fonts-liberation` arrives with Chromium,
so there is always a real serif present. That is a cosmetic loss rather
than a white screen.

Spectral is OFL 1.1; the licence is downloaded alongside the faces.

## Why NumPy is pinned below 2

`tflite-runtime` 2.14.0 was built against NumPy 1.x, and its metadata
says `numpy >=1.23.2` with no upper bound, because it predates NumPy 2
entirely. So pip installs NumPy 2.x next to it quite happily, and then
every attempt to load a model dies:

```
A module that was compiled using NumPy 1.x cannot be run in NumPy 2.4.6
AttributeError: _ARRAY_API not found
ImportError: numpy.core.multiarray failed to import
SystemError: <built-in method CreateWrapperFromFile ...> returned a result with an exception set
```

The build pins `numpy<2` on the tflite-runtime branch only, since
`ai-edge-litert` is new enough not to care. `numpy<2` resolves to
1.26.4, which has an arm64 cp311 wheel, so nothing gets compiled on the
Pi.

The pin and the interpreter are asked for in the same `pip install`,
before `earshot` itself. That ordering matters. Installing `earshot`
first pulled NumPy 2.x, since it only requires `>=1.22`, and the
pin then had to *downgrade* it in a second, separate resolution. That
fetched about 14MB it immediately discarded, and added a network round
trip whose failure was fatal to a 40-minute build. On 64-bit Pi OS, PyPI
is the only index that can serve these: piwheels carries `armv6l` and
`armv7l` wheels only, nothing for `aarch64`. So when PyPI's 2.3MB numpy
index page, the largest single fetch in the build, comes back empty, pip
says `(from versions: none)` and `set -e` ends the build. Each pip call
now retries three times before believing that, and any failure of the
tflite-runtime branch falls through to `ai-edge-litert` rather than
aborting. An image carrying the larger interpreter beats no image.

The build then imports the interpreter and fails if it cannot, so this
whole class of problem is a red build rather than a frame that lights up
and never identifies anything. The boot report prints the same versions
and import results on the running Pi, which names the failure on sight
if it ever gets that far.

## Why tty1 gets masked, not disabled

`systemctl disable getty@tty1.service` looks like the right call and is
very nearly useless here: disable only removes symlinks under `/etc`,
and the console getty is commonly enabled from the vendor tree or
created by a generator instead. In a build log it shows up as `enable`
printing `Created symlink ...` on the line above while `disable` prints
nothing whatsoever. It removes nothing and leaves the login prompt on
the panel.

`systemctl mask` points the unit at `/dev/null` and overrides all of
those paths at once. The kiosk unit also carries
`Conflicts=getty@tty1.service` as a second line of defence, in case
something re-enables the getty later. If you ever want the console back:
`sudo systemctl unmask getty@tty1.service`.

## What is in the image

- `/opt/earshot`, the project and its virtualenv
- `/var/lib/earshot`, holding `detections.db`, the BirdNET models and
  the cached species list for the current week
- `/etc/earshot.toml`, written at build time from the variables above
- three units: `earshot`, `plate197-backlight`, `plate197-kiosk`
- `dtoverlay=rpi-backlight` in config.txt, and a quiet console: no kernel
  messages, no cursor, no blanking. It is a picture frame.

## Licensing note

The BirdNET v2.4 models are CC BY-NC-SA 4.0 (K. Lisa Yang Center for
Conservation Bioacoustics; Chemnitz University of Technology). Baking
them into an image redistributes them, which that licence allows with
attribution, non-commercially, under the same terms. Worth reading before
publishing built images anywhere. `PLATE197_BAKE_MODELS=no` builds an
image that fetches them on first run instead.

The Audubon plates in `plates/`, baked into the image the same way, carry
their own required credit:

> Courtesy of the John James Audubon Center at Mill Grove, Montgomery
> County Audubon Collection, and Zebra Publishing
