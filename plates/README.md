# Plates

One PNG per bird, cropped from an [Audubon plate](https://www.audubon.org/art/birds-of-america).

## Adding a bird

1. Crop the bird out of the plate. Many plates show several birds and
   sometimes more than one species, so pick the right figure. You will want to remove the background so it is transparent.
2. Export it about 300px on the longest side.
3. Name it after what BirdNET calls the bird, lowercased:
   `house-finch.png`, `black-capped-chickadee.png`.
4. Put it in this folder.

```sh
earshot species --missing   # birds heard here that still need a picture
earshot plates              # sizes of what's here, flags anything too big
earshot plates --fix        # shrink the oversized ones (needs Pillow)
```

## Naming

Use BirdNET's name, not Audubon's. His House Finch is the
*Crimson-necked Bull-finch*, so a folder named after plate titles matches
nothing the classifier says. The top-level README has a table of the two
names for the birds at this feeder.

All 6522 names are in `labels.txt`, which comes with the
[model download](https://zenodo.org/records/15050749) and sits at
`/var/lib/earshot/models/labels.txt` on the Pi. `earshot species towhee`
searches it.

Spelling is otherwise forgiving: `house_finch.png`, `House Finch.png` and
`housefinch.PNG` all reach the same bird.

## Size

Don't skip step 2. A full-resolution scan decodes to a multi-megabyte
bitmap in Chromium's memory however well the PNG itself is compressed,
and a few of those at once will blank the screen on a 1GB Pi 3. The
largest thing the page ever draws is a magpie at about 160px, so 300px is
already generous. `earshot plates --fix` fixes it if you forget.

## A bird the page hasn't met

Add one line to `SPECIES` in `index-7inch.html` so it knows what size to
draw:

```js
'Cedar Waxwing': { sci:'Bombycilla cedrorum', mass_g:32 },
```

## Credit

Required wherever these plates are shown or shared:

> Courtesy of the John James Audubon Center at Mill Grove, Montgomery
> County Audubon Collection, and Zebra Publishing
