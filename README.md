# Plate 197

A bird frame where a Raspberry Pi listens at a window by a feeder, identifies
birds by sound with [BirdNET](https://birdnet.cornell.edu/), and draws the ones heard recently on a 7"
touchscreen. Tap a bird for its name, how many observations there have been today, and when it was there.

*Plate 197* is the Audubon plate number for the house finch, the most
common bird at my feeder and my personal favorite bird.

This repo lets you build an image for a Raspberry Pi 3 or newer or install the software on Raspberry Pi OS yourself.


## Hardware

- Raspberry Pi 3 or newer.
- [Official Raspberry Pi 7" touchscreen](https://www.raspberrypi.com/products/raspberry-pi-touch-display/), 800x480 over DSI.
- A USB microphone, anything that will do 48kHz mono. [This one is a
  lapel mic](https://a.co/d/0a1cUpJw).
- A microSD card, 8GB or larger.
- A [power supply](https://a.co/d/00Aw5ZDu)

This build is housed in a [SmartiPi Touch 2](https://smarticase.com/products/smartipi-touch-2?variant=15923872563263), which holds the Pi behind
the official display. Raspberry Pi OS Lite 64-bit is the target.

## Install

Start from Raspberry Pi OS Lite, 64-bit. Write it with
[Raspberry Pi Imager](https://www.raspberrypi.com/software/), which is
also where the OS itself comes from: pick *Raspberry Pi OS (other)*, then
*Raspberry Pi OS Lite (64-bit)*.

Lite has no desktop, which is what you want here. Set your user, Wi-Fi and SSH in Imager's OS
Customisation (the gear icon, or Ctrl+Shift+X) before writing the card.

Bookworm and Trixie both work.

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
"Colorado". Your coordiantes will go in `/etc/earshot.toml` on the Pi, or `earshot.toml` beside
the code, both gitignored. For a built image, they will go in `distro/src/config.local`.

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
| House Finch | *Crimson-fronted Purple Finch*, octavo Pl. 197, the plate this project is named for. Earlier, on Havell Pl. 424, *Crimson-necked Bull-finch* |
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


## Thanks

Inspired by [fugleramme](https://github.com/arnegiacomo/fugleramme) by
Arne Giacomo, a Raspberry Pi bird frame that identifies birds by sound
and shows them as hand-cut 1800s illustrations.

## Licence

This project: MIT, see [LICENSE](LICENSE).

The BirdNET v2.4 models are CC BY-NC-SA 4.0, from the K. Lisa Yang
Center for Conservation Bioacoustics at the Cornell Lab of Ornithology
and Chemnitz University of Technology. They are downloaded at install
time, not committed here.

The Audubon plates in `plates/`, wherever they are shown or shared:

> Courtesy of the John James Audubon Center at Mill Grove, Montgomery
> County Audubon Collection, and Zebra Publishing
