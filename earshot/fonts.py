"""Spectral, kept on the card.

The page asks for its typeface from fonts.googleapis.com. On a frame
with no route to the internet that request is render-blocking: Chromium
paints nothing at all until DNS gives up, which is a white screen on a
picture frame — and a picture frame has no business phoning Google to
draw its own footer.

So the faces are downloaded once, at build time, and served from the Pi.
If that download never happened the page simply falls back to whatever
serif the system has (Chromium brings fonts-liberation with it), which
is a perfectly good Times-metric face. Either way nothing reaches for
the network while the frame is running.

Spectral is licensed OFL 1.1 — free to redistribute, including inside
an image, with the licence alongside it.
"""

from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

# Exactly the faces the page's own link asks for: 300, 400, and 300
# italic. Anything more is bytes on a card for nothing.
CSS_URL = ("https://fonts.googleapis.com/css2"
           "?family=Spectral:ital,wght@0,300;0,400;1,300&display=swap")
LICENSE_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/spectral/OFL.txt"
STYLESHEET = "spectral.css"

# Google serves woff2 only to browsers that admit to supporting it.
_UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_FONT_URL = re.compile(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)")


def _get(url: str, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def have(font_dir: Path) -> bool:
    css = Path(font_dir) / STYLESHEET
    return css.is_file() and css.stat().st_size > 0


def fetch(font_dir: Path) -> int:
    """Download the faces and a stylesheet that points at them locally.

    Returns how many font files were written. Raises on failure, so the
    caller can decide whether that is fatal — for the image build it is
    not: a missing typeface is a cosmetic loss, not a broken frame.
    """
    font_dir = Path(font_dir)
    font_dir.mkdir(parents=True, exist_ok=True)

    css = _get(CSS_URL).decode("utf8")
    urls = list(dict.fromkeys(_FONT_URL.findall(css)))
    if not urls:
        raise RuntimeError("no woff2 URLs in the stylesheet Google returned")

    for url in urls:
        name = url.rsplit("/", 1)[-1]
        if not name.endswith(".woff2"):
            name += ".woff2"
        (font_dir / name).write_bytes(_get(url))
        # Point the stylesheet at our copy rather than gstatic.
        css = css.replace(url, name)

    (font_dir / STYLESHEET).write_text(css, encoding="utf8")
    try:
        (font_dir / "OFL.txt").write_bytes(_get(LICENSE_URL))
    except Exception as exc:          # nice to have, never fatal
        log.warning("could not fetch the Spectral licence: %s", exc)
    return len(urls)
