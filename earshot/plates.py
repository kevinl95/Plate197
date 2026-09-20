"""Audubon's pictures, BirdNET's names.

The plate is Audubon's. The filename is BirdNET's. Those are almost never
the same words — Audubon's House Finch is the *Crimson-necked Bull-finch*,
his Northern Flicker is the *Red-shafted Woodpecker*, his Dark-eyed Junco
is the *Snow Bird* — so a folder named after the plates would match
nothing the classifier ever says.

The rule is one line: find what BirdNET calls the bird, lowercase it,
name the file that. `earshot species` exists to answer the first half.

Matching is deliberately forgiving, because this is a gift and not a
build system: house-finch.png, house_finch.png, "House Finch.png" and
housefinch.png all answer to "House Finch".
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

# What counts as a picture. PNG with alpha is what the plates will be;
# the rest are here so nobody loses an afternoon to a .jpg.
IMAGE_SUFFIXES = frozenset({".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg"})

_NOT_ALNUM = re.compile(r"[^a-z0-9]+")


def _ascii(text: str) -> str:
    """Fold accents, so Wilson's and Müller's behave like everything else."""
    return (unicodedata.normalize("NFKD", text)
            .encode("ascii", "ignore").decode("ascii"))


def key(name: str) -> str:
    """One comparable token from either a species name or a file name.

    "Black-capped Chickadee", "black_capped_chickadee.png" and
    "BlackCappedChickadee.PNG" all reduce to blackcappedchickadee.
    """
    stem = name.rsplit("/", 1)[-1]
    if "." in stem and ("." + stem.rsplit(".", 1)[-1].lower()) in IMAGE_SUFFIXES:
        stem = stem.rsplit(".", 1)[0]
    return _NOT_ALNUM.sub("", _ascii(stem).lower())


def filename_for(common_name: str, suffix: str = ".png") -> str:
    """The name to give a new plate: lowercase, hyphenated, url-safe."""
    slug = _NOT_ALNUM.sub("-", _ascii(common_name).lower().replace("'", "")).strip("-")
    return slug + suffix


class Plates:
    """The contents of plates/, re-read when the directory changes."""

    def __init__(self, directory: Path | None):
        self.directory = Path(directory) if directory else None
        self._mtime: float | None = None
        self._by_key: dict[str, Path] = {}

    def _refresh(self) -> None:
        if self.directory is None or not self.directory.is_dir():
            self._by_key, self._mtime = {}, None
            return
        try:
            mtime = self.directory.stat().st_mtime
        except OSError:
            self._by_key, self._mtime = {}, None
            return
        if mtime == self._mtime:
            return
        found: dict[str, Path] = {}
        for path in sorted(self.directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            k = key(path.name)
            if not k:
                continue
            if k in found:
                # Two files for one bird. Say so once rather than picking
                # silently and leaving someone wondering which won.
                log.warning("two plates match %r: using %s, ignoring %s",
                            k, found[k].name, path.name)
                continue
            found[k] = path
        self._by_key, self._mtime = found, mtime

    def path_for(self, name: str) -> Path | None:
        """The file for a species name, however it was spelled."""
        self._refresh()
        return self._by_key.get(key(name))

    def has(self, name: str) -> bool:
        return self.path_for(name) is not None

    def keys(self) -> list[str]:
        self._refresh()
        return sorted(self._by_key)

    def files(self) -> list[Path]:
        self._refresh()
        return [self._by_key[k] for k in sorted(self._by_key)]

    def __len__(self) -> int:
        self._refresh()
        return len(self._by_key)


# The largest current on-panel draw is the Black-billed Magpie at
# ~118px (58 * (175/21)**(1/3)). 300px is comfortable headroom above
# that without carrying multi-megapixel scans around for no visual
# benefit. Chromium has to hold a decoded RGBA bitmap for whatever it's
# handed, at width*height*4 bytes, regardless of the PNG's own
# compression -- a handful of full-scan plates on screen at once can
# already exceed the RAM budget of a Pi 3. This is a size cap, not a
# crop: it never decides what to keep in frame, only how large the
# pixels are once that decision has been made by a person.
MAX_SIDE = 300


def describe(plates: Plates) -> list[dict]:
    """One row per plate: dimensions, both sizes, whether it needs fixing.

    No PIL import here -- this only reads what the OS already knows
    (file size) plus a PNG's own header (width/height, cheap and doesn't
    need decoding the image), so `earshot check` can call it unconditionally
    without requiring Pillow to be installed.
    """
    import struct

    rows = []
    for path in plates.files():
        row = {"name": path.name, "bytes": path.stat().st_size,
               "width": None, "height": None, "oversized": False,
               "error": None}
        try:
            with path.open("rb") as fh:
                header = fh.read(24)
            png_magic = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
            if header[:8] == png_magic and header[12:16] == b"IHDR":
                width, height = struct.unpack(">II", header[16:24])
                row["width"], row["height"] = width, height
                row["oversized"] = max(width, height) > MAX_SIDE
        except OSError as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def optimize(plates: Plates, max_side: int = MAX_SIDE) -> list[str]:
    """Shrink whatever's oversized, in place. Alpha in, alpha out.

    Requires Pillow -- imported here, not at module load, so nothing
    about the running service (which never calls this) needs it
    installed. Every source mode PNG can produce (RGBA, palette+tRNS,
    grayscale, ...) is normalised through .convert("RGBA") first, which
    is also what correctly expands a palette image's transparency into
    real per-pixel alpha before resampling.
    """
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError(
            "Pillow is required for this: pip install pillow "
            "(or: pip install earshot[plates])"
        ) from None

    changed = []
    for path in plates.files():
        try:
            with Image.open(path) as img:
                longest = max(img.size)
                if longest <= max_side:
                    continue
                before = img.size
                rgba = img.convert("RGBA")
                scale = max_side / longest
                new_size = (round(rgba.width * scale), round(rgba.height * scale))
                resized = rgba.resize(new_size, Image.LANCZOS)
            resized.save(path, optimize=True)
            changed.append(f"{path.name}: {before[0]}x{before[1]} -> "
                           f"{new_size[0]}x{new_size[1]}")
        except OSError as exc:
            changed.append(f"{path.name}: could not process ({exc})")
    return changed


def unmatched(plates: Plates, known_names) -> list[str]:
    """Plate files that match no species the classifier can name.

    A typo in a filename is otherwise invisible: the bird simply never
    gets its picture, and nothing anywhere says why.
    """
    known = {key(name) for name in known_names}
    return [path.name for path in plates.files() if key(path.name) not in known]
