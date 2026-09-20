# Plate 197

A bird frame. A Raspberry Pi listens at a window by a feeder, identifies
birds by sound with BirdNET, and draws the ones heard recently on a 7"
touchscreen as a quiet dark-ground scene. Tap a bird and it tells you
what it is and when it was there.

*Plate 197* is the Audubon plate number for the house finch, which is the
most common bird at this feeder.

Everything runs on the Pi. Nothing is sent anywhere.

```
earshot/        the service: capture -> classify -> SQLite -> HTTP
index-7inch.html  the display, 800x480, native touch
backlight.py    panel brightness on a dawn/dusk schedule
deploy/         systemd units, kiosk launcher, installer
distro/         CustomPiOS build for a ready-to-write image
tests/          the parts that are easy to get quietly wrong
```

## On a Pi

```sh
git clone https://github.com/kevinl95/Plate197.git
cd Plate197
sudo deploy/install.sh
sudo reboot
```

That installs the packages, makes a virtualenv in `/opt/earshot`,
downloads the BirdNET models (~120MB, once), writes `/etc/earshot.toml`,
adds `dtoverlay=rpi-backlight` to `config.txt`, and enables three units:

| unit | what it does |
|---|---|
| `earshot` | listens, classifies, stores, serves |
| `plate197-kiosk` | X on tty1 with Chromium in kiosk mode, nothing else |
| `plate197-backlight` | follows the sun; dim at night |

For a whole image instead of an install, see [distro/README.md](distro/README.md).

## Checking on it

```sh
earshot check      # everything that has to be true, one line each
earshot devices    # what the mic is called from here
earshot bench      # one 3-second window, honestly timed
curl -s localhost:8197/health | python3 -m json.tool
journalctl -u earshot -f
```

Run `earshot check` first when the screen is empty. It reports the
interpreter, the models, the microphone, the database, disk space, the
page, undervoltage and SoC temperature separately, so you find out which
of those it is rather than guessing.

## The service

Capture uses ALSA through `sounddevice`, 48kHz mono 16-bit, in 3-second
windows with 1 second of overlap so a call across a boundary is still
heard once. The device is chosen by a substring of its *name*, never an
index, because `hw:1,0` moves the moment another USB device is plugged
in. It is re-resolved on every reconnect, and PortAudio is reinitialised
first so a mic plugged in after boot is actually visible.

Classify runs BirdNET v2.4 TFLite, 6522 classes, plus the range
meta-model. Given a latitude, longitude and week of the year the
meta-model says which species are plausible. On the Colorado Front Range
in September that is 138 of 6522, and cutting the list that far is the
biggest accuracy win available here. It runs weekly rather than per
window, and its result is cached, so the 29MB interpreter is loaded and
dropped rather than kept.

Store is SQLite in WAL mode, `synchronous=NORMAL`, with batched inserts.
One table, `detections`. Timestamps are ISO8601 local time with no
offset, which is what the page's history queries compare against and what
JavaScript parses as local. No audio is kept, and there is no speaker.

Serve is Flask on localhost. `GET /recent` is the contract in the comment
block in `index-7inch.html`. `GET /health` is everything a person needs
at 7am. `/` is the page itself.

### /recent

One entry per species heard since local midnight, latest first:

```json
[{"species": "House Finch", "sci": "Haemorhous mexicanus",
  "time": "2026-09-18T07:12:04", "conf": 0.91,
  "first_ever": false, "first_this_year": false,
  "count_today": 37, "first_today": "2026-09-18T06:42:11"}]
```

Both `first_*` flags exclude today. Include it and every bird is a first
every morning, and the red ping on the panel stops meaning anything.
There are tests that exist only to hold that line.

## Configuration

`/etc/earshot.toml`, all optional. See
[deploy/earshot.toml.example](deploy/earshot.toml.example). Any single key
can also be overridden by environment (`EARSHOT_MIN_CONF=0.7`), which is
the tidy way to change one thing from a systemd drop-in.

The two worth knowing are `device_match`, a substring of the microphone's
name, and `latitude`/`longitude`, which decide the species list.

### The location is deliberately not in here

The committed defaults point at the centre of Denver, and the page's
footer says "Colorado". That is a placeholder. This repository is public
and a feeder's coordinates are not something to publish. Put the real
ones in `/etc/earshot.toml` on the Pi, or in `earshot.toml` beside the
code for development. Both are gitignored, and both the detector and
`backlight.py` read them, so there is one answer in one place.

For a built image, `distro/src/config.local` does the same job.

Being a little off costs almost nothing. A range model asked about Denver
rather than a town twenty miles away returns 97.7% the same species list
across the year. Being on the wrong continent is what breaks it.

## Things that will happen

- Undervoltage. A Pi 3 with a USB mic on a marginal supply drops audio
  blocks, and that looks exactly like a bug in the capture loop.
  `/health` carries `vcgencmd get_throttled`, decoded, including the
  since-boot bits.
- The clock is wrong at boot. A Pi has no RTC. Detections are held with
  monotonic readings and only turned into timestamps once the clock is
  believable, so nothing is ever written as 1970. Nothing is lost either,
  because the offset is still exact when NTP lands.
- The mic is unplugged mid-run. The stream stops delivering without
  erroring, so five seconds of no audio at all is treated as wedged, and
  the stream is torn down and reopened with a growing backoff. The page
  keeps showing the birds already in the database throughout.
- The card fills up. A failed write is a warning and a dropped batch,
  not a crash loop.
- The process itself wedges. The unit is `Type=notify` with
  `WatchdogSec=90`, pinged from the supervision loop, so a PortAudio call
  that never returns gets the service restarted.

Logging is at `warning` on purpose. Constant journald writes kill SD
cards, and an hourly line about nothing is not worth a card.

## Artwork

Pictures go in a `plates/` directory next to `index-7inch.html`, which is
`/opt/earshot/plates/` on the Pi. They are served at `/plates/<name>`,
and `install.sh` and the image build both pick the directory up if it
exists.

The rule is to name the file after what BirdNET calls the bird,
lowercased.

```
plates/house-finch.png
plates/black-capped-chickadee.png
plates/red-winged-blackbird.png
```

This matters more than it looks, because the picture is Audubon's and the
name is not. He was painting decades before the names settled, so a
folder organised by plate titles matches nothing the classifier will ever
say:

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
| American Goldfinch | American Goldfinch, one of the few he named the same. Also *Yellow-bird* or *Thistle-bird*; the plate puts it on a thistle. |
| Spotted Towhee | Arctic Ground Finch |

Nothing in the code knows about that table. It is a worked example of the
one rule, kept here so the next person doesn't have to rediscover that
the House Finch was a bull-finch.

### Finding the name

```sh
earshot species                 # every bird plausible at this feeder, and its filename
earshot species --missing       # ...only the ones with no picture yet
earshot species towhee          # search by common or scientific name
earshot species --all warbler   # the whole 6522, not just what turns up here
```

```
BirdNET name        scientific            plate file
American Goldfinch  Spinus tristis        american-goldfinch.png  --
Cassin's Finch      Haemorhous cassinii   cassins-finch.png       --
House Finch         Haemorhous mexicanus  house-finch.png         have
```

The default list is the species the range model considers plausible at
your coordinates this week, which is a far better to-do list than the
worldwide 6522. Some are seasonal: Lesser Goldfinch only scores here in
weeks 17-39, Spotted Towhee 8-44, so the list changes through the year.

### Spelling

Matching is forgiving on purpose. All of these are the same bird:

```
house-finch.png   house_finch.png   "House Finch.png"   housefinch.PNG
```

and the page can ask for `/plates/House%20Finch` or
`/plates/house-finch.png` and get the same file. `.png` with alpha is
what these should be. `.webp`, `.jpg` and `.svg` are accepted too.

A name that matches no species is the case that isn't forgiven, because
it fails quietly: the bird simply never gets its picture and nothing says
why. So `earshot check` reports it:

```
  [--] artwork: 5 in /opt/earshot/plates; no species matches crimson-necked-bull-finch.png
```

`GET /plates` lists what is installed and `/health` counts them.

### Cutting them

The page sizes each bird by mass, `95 * (mass_g/21)^0.25` px wide. That
works out to 81px for a chickadee, 95px for a house finch, 150px for a
flicker and 161px for a magpie, on a non-HiDPI 800x480 panel. Export at
about 300px on the longer side, transparent, bird facing right with its
feet near the bottom. 300px is also the cap `earshot plates` enforces.

Only the hand-drawn silhouettes are ever mirrored. A plate is drawn the
way Audubon painted it.

Isolating one bird from a plate that shows several, and some plates mix
species entirely, is a judgment call for a person rather than something
this project automates. Sizing the result correctly is not, and getting
it wrong is expensive here. A full-resolution export still decodes to a
multi-megabyte bitmap in memory no matter how well the PNG itself is
compressed, and several of those on screen at once is a real way to run a
Pi 3 out of RAM. So that part is checked automatically:

```sh
earshot plates          # dimensions and file size for everything in plates/
earshot plates --fix    # shrinks anything oversized, in place (needs Pillow)
```

`earshot check` runs the same size check on its own, alongside the
naming one.

A new species needs one line in `SPECIES` in the page, next to the
others, so it can be sized:

```js
'Cedar Waxwing': { sci:'Bombycilla cedrorum', mass_g:32 },
```

Leave out `shape`, which is only for the hand-drawn silhouettes. A
species with no entry at all still draws the moment its plate appears; it
just uses the House Finch size until you add its mass.

### When there is no picture

Two different cases, and only one of them needs handling.

A species the page has no entry for, such as a robin or a house sparrow,
is still classified, still written to the database with its scientific
name and confidence, and still returned by `/recent`. The page drops it
in `latestBySpecies()` and it never draws. Nothing breaks, and the
history accrues quietly: add a plate for the magpie in a year and its
"first ever" is already accurate back to the day the frame was plugged
in.

House Sparrow, European Starling and Eurasian Collared-Dove post-date
Audubon entirely and will never have a plate. They are common here and
the range model scores all three highly, so they are recorded and then
filtered out of the display, which is the behaviour the brief asks for.

What the page draws, in order:

| the bird has | it draws |
|---|---|
| a plate | the plate |
| no plate, but one of the eight hand-drawn shapes | the silhouette |
| a plate that 404s or fails to load | the silhouette, via `onerror` |
| only a mass, or no entry at all | nothing, until its plate exists |

That last row is deliberate. A magpie-sized finch silhouette labelled
"Black-billed Magpie" would be a worse answer than an absence, so a
species added to `SPECIES` with only a mass waits quietly for its
picture. It is still being recorded the whole time.

The drawn silhouettes stay rather than being deleted, because a bare
`<img>` with no file shows a broken-image icon, and that is the one thing
this panel must never do. Art can be added one bird at a time and the
frame never looks wrong in between.

The plate list is re-read on every poll, so a picture dropped into
`plates/` shows up within a minute. No restart.

## Licence

This project: MIT, see [LICENSE](LICENSE).

The BirdNET v2.4 models are CC BY-NC-SA 4.0, from the K. Lisa Yang Center
for Conservation Bioacoustics at the Cornell Lab of Ornithology and
Chemnitz University of Technology. They are downloaded at install time,
not committed here. The non-commercial terms are fine for a frame on a
table, and worth reading properly before anything else.

The Audubon plates in `plates/`, wherever they are shown or shared:

> Courtesy of the John James Audubon Center at Mill Grove, Montgomery
> County Audubon Collection, and Zebra Publishing
