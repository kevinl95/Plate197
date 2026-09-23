# Plate 197

A bird frame where a Raspberry Pi listens at a window by a feeder, identifies
birds by sound with BirdNET, and draws the ones heard recently on a 7"
touchscreen. Tap a bird for its name, how many observations there have been today, and when it was there.

*Plate 197* is the Audubon plate number for the house finch, the most
common bird at my feeder and my personal favorite bird.

This repo lets you build an image for a Raspberry Pi 3 or newer.

```

## Hardware

- Raspberry Pi 3 or newer, which is also where 64-bit Raspberry Pi OS
  starts. 1GB is enough; everything runs on the Pi.
- Official Raspberry Pi 7" touchscreen, 800x480 over DSI.
- A USB microphone, anything that will do 48kHz mono. This one is a
  lapel mic.
- A microSD card, 8GB or larger.
- A power supply with headroom. Undervoltage is the most common cause of
  trouble here; see "Things that will happen" below.

This build is housed in a SmartiPi Touch 2, which holds the Pi behind
the official display. Raspberry Pi OS Lite 64-bit is the target.

## Install

Start from Raspberry Pi OS Lite, 64-bit. The 64-bit part is not
optional: every Python wheel this installs is aarch64. Write it with
[Raspberry Pi Imager](https://www.raspberrypi.com/software/), which is
also where the OS itself comes from: pick *Raspberry Pi OS (other)*, then
*Raspberry Pi OS Lite (64-bit)*.

Lite has no desktop, which is what you want here; this project installs
its own display stack. Set your user, Wi-Fi and SSH in Imager's OS
Customisation (the gear icon, or Ctrl+Shift+X) before writing the card,
because a frame has nowhere to plug a keyboard.

Bookworm and Trixie both work. Bookworm gives Python 3.11 and the 2.3MB
`tflite-runtime`; Trixie gives Python 3.13, which has no arm64 wheel for
that, so it falls back to the 14MB `ai-edge-litert`. On a 1GB Pi 3 the
smaller one is worth having. [distro/README.md](distro/README.md) has
the detail.

Then, over SSH or on the Pi itself:

```sh
git clone https://github.com/kevinl95/Plate197.git
cd Plate197
sudo deploy/install.sh
sudo reboot
```

That installs the packages, makes a virtualenv in `/opt/earshot`, fetches
the BirdNET models (~120MB, once), writes `/etc/earshot.toml`, adds
`dtoverlay=rpi-backlight` to `config.txt`, and enables three units:

| unit | what it does |
|---|---|
| `earshot` | listens, classifies, stores, serves |
| `plate197-kiosk` | X on tty1 with Chromium in kiosk mode |
| `plate197-backlight` | follows the sun; dim at night |

For a whole SD-card image instead, see [distro/README.md](distro/README.md).

## Checking on it

```sh
earshot check      # everything that has to be true, one line each
earshot devices    # what the mic is called from here
earshot bench      # one 3-second window, timed
curl -s localhost:8197/health | python3 -m json.tool
journalctl -u earshot -f
```

Run `earshot check` first when the screen is empty. It reports the
interpreter, models, microphone, database, disk, page, undervoltage and
SoC temperature separately.

## How it works

Audio arrives through ALSA and `sounddevice` at 48kHz mono, in 3-second
windows overlapping by 1 second so a call across a boundary is heard
once. The mic is matched on a substring of its *name*, never an index,
because `hw:1,0` moves the moment another USB device is plugged in.

BirdNET v2.4 TFLite identifies, across 6522 classes. The range
meta-model takes latitude, longitude and week of year and returns the
species actually plausible there, which on the Colorado Front Range in
September is 138 of the 6522. It runs weekly and the result is cached,
so the 29MB interpreter is loaded and dropped rather than kept.

Detections go into SQLite in WAL mode, one `detections` table, batched
inserts, timestamps ISO8601 local with no offset. No audio is kept.
Flask serves the page, `/recent` and `/health` on localhost.

### /recent

One entry per species heard since local midnight, latest first. This is
the contract the page reads:

```json
[{"species": "House Finch", "sci": "Haemorhous mexicanus",
  "time": "2026-09-18T07:12:04", "conf": 0.91,
  "first_ever": false, "first_this_year": false,
  "count_today": 37, "first_today": "2026-09-18T06:42:11"}]
```

Both `first_*` flags exclude today. Include it and every bird is a first
every morning.

## Configuration

`/etc/earshot.toml`, all optional. See
[deploy/earshot.toml.example](deploy/earshot.toml.example). Any key can
be overridden by environment (`EARSHOT_MIN_CONF=0.7`).

Two matter: `device_match`, a substring of the microphone's name, and
`latitude`/`longitude`, which decide the species list.

The committed coordinates point at central Denver and the footer says
"Colorado". That is a placeholder, because this repository is public.
Real ones go in `/etc/earshot.toml` on the Pi, or `earshot.toml` beside
the code, both gitignored. For a built image, `distro/src/config.local`.
Twenty miles off costs almost nothing: the range model returns 97.7% the
same species across the year.

## Things that will happen

- Undervoltage. A Pi 3 with a USB mic on a marginal supply drops audio
  blocks, which looks exactly like a bug in the capture loop. `/health`
  carries `vcgencmd get_throttled`, decoded.
- The clock is wrong at boot, because a Pi has no RTC. Detections are
  held with monotonic readings and timestamped once the clock is
  believable, so nothing is written as 1970 and nothing is lost.
- The mic is unplugged. The stream stops delivering without erroring, so
  five seconds of silence counts as wedged and it is reopened with a
  growing backoff. The page keeps showing what is already stored.
- The card fills up. A failed write is a warning and a dropped batch.
- The process wedges. `Type=notify` with `WatchdogSec=90`, pinged from
  the supervision loop, so a PortAudio call that never returns gets the
  service restarted.

Logging is at `warning` on purpose. Constant journald writes kill SD
cards.

## Artwork

Pictures go in `plates/` beside `index-7inch.html`, which is
`/opt/earshot/plates/` on the Pi, and are served at `/plates/<name>`.
Name each file after what BirdNET calls the bird, lowercased:
`house-finch.png`. [plates/README.md](plates/README.md) covers cropping,
sizing and the spelling rules.

```sh
earshot species --missing   # plausible at this feeder, no picture yet
```

The naming rule exists because the picture is Audubon's and the name is
not. He painted decades before the names settled, so a folder organised
by plate titles matches nothing the classifier will say:

| BirdNET's name (name the file this) | Audubon's name (find the plate by this) |
|---|---|
| House Finch | Crimson-necked Bull-finch *(octavo Pl. 197, Crimson-fronted Purple Finch)* |
| Mourning Dove | Carolina Turtle Dove / Carolina Pigeon |
| Red-winged Blackbird | Red-winged Starling, or Marsh Blackbird |
| Dark-eyed Junco | Snow Bird *(Fringilla hyemalis)* |
| Northern Flicker | Red-shafted Woodpecker, not Golden-winged. Golden-winged is the eastern yellow-shafted bird; the interior west gets red-shafted. |
| Black-capped Chickadee | Black-capt Titmouse |
| Cedar Waxwing | Cedar Bird |
| Pine Siskin | Pine Finch / Pine Linnet |
| Common Grackle | Purple Grackle, or Common Crow Blackbird |
| Red-breasted Nuthatch | Red-bellied Nuthatch |
| Black-billed Magpie | American Magpie |
| American Goldfinch | American Goldfinch, one of the few he named the same. Also *Yellow-bird* or *Thistle-bird*. |
| Spotted Towhee | Arctic Ground Finch |

Nothing in the code reads that table. It is a worked example of the one
rule, kept so the next person doesn't have to rediscover that the House
Finch was a bull-finch.

A new species also needs one line in `SPECIES` in the page so it can be
sized:

```js
'Cedar Waxwing': { sci:'Bombycilla cedrorum', mass_g:32 },
```

### When there is no picture

| the bird has | it draws |
|---|---|
| a plate | the plate |
| no plate, but one of the eight hand-drawn shapes | the silhouette |
| a plate that 404s | the silhouette, via `onerror` |
| only a mass, or no entry at all | nothing, until its plate exists |

A magpie-sized finch silhouette labelled "Black-billed Magpie" would be
worse than drawing nothing, which is why the last row draws nothing at
all. The bird is still
recorded the whole time, so adding its plate a year later gives a "first
ever" that is already correct.

House Sparrow, European Starling and Eurasian Collared-Dove post-date
Audubon and will never have a plate. They are recorded, then filtered
out of the display.

The plate list is re-read on every poll, so a picture dropped into
`plates/` appears within a minute. No restart.

## Thanks

Inspired by [fugleramme](https://github.com/arnegiacomo/fugleramme) by
Arne Giacomo, a Raspberry Pi bird frame that identifies birds by sound
and shows them as hand-cut 1800s illustrations, entirely locally.
Norwegian for "bird frame".

Fugleramme is e-ink: a 13.3" Inky Impression on a Pi 5, classifying with
BirdNET-Go. Plate 197 is the same idea on cheaper parts, with a 7" DSI
touchscreen on a Pi 3, BirdNET's TFLite model directly, and Audubon.

## Licence

This project: MIT, see [LICENSE](LICENSE).

The BirdNET v2.4 models are CC BY-NC-SA 4.0, from the K. Lisa Yang
Center for Conservation Bioacoustics at the Cornell Lab of Ornithology
and Chemnitz University of Technology. They are downloaded at install
time, not committed here.

The Audubon plates in `plates/`, wherever they are shown or shared:

> Courtesy of the John James Audubon Center at Mill Grove, Montgomery
> County Audubon Collection, and Zebra Publishing
