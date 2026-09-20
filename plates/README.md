# Plates

Hand-cut Audubon plates go here, as PNGs with alpha.

Credit, wherever these plates are shown or shared:

> Courtesy of the John James Audubon Center at Mill Grove, Montgomery
> County Audubon Collection, and Zebra Publishing

Name the file after what BirdNET calls the bird, lowercased. Don't use
Audubon's name for it. His House Finch is the *Crimson-necked
Bull-finch*, and a folder organised by plate titles matches nothing the
classifier will ever say.

```sh
earshot species --missing      # the birds here that still need a picture
```

```
plates/house-finch.png
plates/black-capped-chickadee.png
plates/cedar-waxwing.png
```

Spelling is forgiving. `house_finch.png`, `House Finch.png` and
`housefinch.PNG` all work. A name matching no species doesn't, and it
fails quietly, so `earshot check` reports those.

You have to do the cropping. The sizing gets checked for you.

Isolating one bird from a plate that shows several, sometimes several
species, needs a person. Working out which figure is the actual Northern
Flicker is not something this project will guess at. Do that part in
whatever editor you already use; Canva worked fine for this set.
Transparent background, bird facing right, feet near the bottom.

Getting the crop right and forgetting to size it down is easy to do and
expensive on this hardware. A full photo-scan PNG, even a well
compressed one, still decodes to a multi-megabyte bitmap in Chromium's
memory, and several of those on screen at once on a 1GB Pi 3 is a real
way to get a blank panel. Check before you commit to a batch:

```sh
earshot plates          # lists every plate's dimensions, flags anything too big
earshot plates --fix    # shrinks whatever's oversized, in place. Needs Pillow
```

`earshot check` runs the same size check automatically, alongside the
naming one. Aim for about 300px on the longer side, which is the cap
`earshot plates` enforces. The largest thing the page ever draws is a
magpie at roughly 160px, so 300 leaves a margin for a sharper panel and
nothing more to carry.

A new species also wants one line in `SPECIES` in `index-7inch.html`, so
the page knows how large to draw it:

```js
'Cedar Waxwing': { sci:'Bombycilla cedrorum', mass_g:32 },
```

Drop a file in and it appears on the panel within a minute. No restart.

See the Artwork section of the top-level README for the rest, including
the table of Audubon's names for the species at this feeder.
