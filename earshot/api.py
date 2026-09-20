"""The two endpoints the frame needs, and the page itself.

/recent is the contract written into the comment block in
index-7inch.html. /health is for whoever is holding an SSH session open
wondering why the screen is empty.

The page is served untouched except for one substitution: its CONFIG.API
is null so that opening the file directly shows mock birds, and it has to
point at this server when served from it. Nothing else about the file is
rewritten, and the file on disk keeps working as its own preview.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time
from pathlib import Path

from flask import Flask, Response, jsonify, send_from_directory
from werkzeug.exceptions import HTTPException

from . import clock, pi, plates as plates_module

log = logging.getLogger(__name__)

PAGE = "index-7inch.html"
API_NULL = re.compile(r"(API\s*):\s*null")
# The footer's place name. Kept out of the file for the same reason the
# coordinates are: the repository is public, the feeder is not.
LOCATION_LINE = re.compile(r"(LOCATION\s*):\s*'[^']*'")

# The page asks Google for its typeface, via <link rel="stylesheet"> --
# which is render-blocking. On a frame with no route to the internet
# Chromium paints *nothing* until DNS gives up: a white screen, which is
# the one thing this is never allowed to show. A picture frame also has
# no business phoning out to draw its own footer.
FONT_LINK = re.compile(
    r'(<link\s+href="https://fonts\.googleapis\.com/[^"]*"\s+rel="stylesheet")\s*>')
# Rewritten to our own copy, served from this Pi. If the build never
# managed to download the faces, that is an instant 404 on localhost and
# the page falls straight through to the system serif -- fonts-liberation
# comes along with Chromium. Either way nothing touches the network.
FONT_LINK_LOCAL = '<link href="/fonts/spectral.css" rel="stylesheet">'
# The preconnect hints point at hosts we no longer use.
FONT_PRECONNECT = re.compile(
    r'\s*<link\s+rel="preconnect"\s+href="https://fonts\.g[^"]*"[^>]*>')

# Shown instead of a stack trace if the page file is missing. Same ground
# colour as the real thing, so a mistake looks like a quiet night rather
# than a crash.
FALLBACK_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>The Window</title><style>
html,body{height:100%;margin:0;background:#060E10;color:#5E6E6E;
font-family:Georgia,serif;display:flex;align-items:center;
justify-content:center;flex-direction:column;gap:8px;cursor:none}
</style></head><body><div style="font-size:21px;color:#BCB49C">Quiet at the feeder</div>
<div style="font-size:13px">{reason}</div></body></html>"""


def fallback_page(reason: str) -> str:
    """The quiet stand-in, built without %-formatting.

    It used to be FALLBACK_PAGE % reason, which raised ValueError on
    "unsupported format character ';'" -- because the CSS above contains
    height:100%, and % means something to that operator. The page meant
    to appear when everything else has failed was itself the thing that
    failed, and the panel showed {"error":"ValueError"}. A plain
    substitution can't have that class of bug.
    """
    return FALLBACK_PAGE.replace("{reason}", reason)


# Where index-7inch.html — and the plates/ directory beside it — can be.
# The installed case has to be listed explicitly: once the package is pip
# installed, __file__ is inside the venv's site-packages, which is nowhere
# near /opt/earshot.
WEB_DIRS = (
    Path(__file__).resolve().parent.parent,   # a git checkout
    Path.cwd(),                               # the unit's WorkingDirectory
    Path("/opt/earshot"),                     # what install.sh lays down
    Path("/opt/earshot/web"),
)


def find_web_dir(configured: str = "") -> Path | None:
    """Wherever index-7inch.html actually is."""
    candidates = [Path(configured)] if configured else []
    candidates += list(WEB_DIRS)
    for candidate in candidates:
        page = candidate / PAGE
        # os.access, not just is_file: a mode-600 file copied in as root
        # is present and completely useless to the service user, which
        # is exactly how this shipped once.
        if page.is_file() and os.access(page, os.R_OK):
            return candidate
    return None


class PageSource:
    """The page, pointed at us and told where it is. Re-read on change."""

    def __init__(self, web_dir: Path | None, location: str = ""):
        self.web_dir = web_dir
        self.location = location
        self._mtime: float | None = None
        self._html: str | None = None

    def html(self) -> str:
        if self.web_dir is None:
            return fallback_page("index-7inch.html is not installed")
        path = self.web_dir / PAGE
        try:
            mtime = path.stat().st_mtime
            if self._html is None or mtime != self._mtime:
                source = path.read_text(encoding="utf8")
                # location.origin rather than a baked-in port: the page
                # then follows whatever address it was opened on.
                html = API_NULL.sub(r"\1: location.origin", source, count=1)
                if html == source:
                    log.warning("could not point the page at this server: "
                                "CONFIG.API is not 'null' in %s", path)
                # Serve the typeface from here rather than from Google.
                html = FONT_LINK.sub(FONT_LINK_LOCAL, html, count=1)
                html = FONT_PRECONNECT.sub("", html)
                # The footer's place name, kept out of the file itself.
                if self.location:
                    html = LOCATION_LINE.sub(
                        lambda m: f"{m.group(1)}: {json.dumps(self.location)}",
                        html, count=1)
                self._html = html
                self._mtime = mtime
        except OSError as exc:
            log.warning("cannot read %s: %s", path, exc)
            return fallback_page("the display file could not be read")
        return self._html


def create_app(cfg, store, runtime_status=lambda: {}) -> Flask:
    web_dir = find_web_dir(cfg.web_dir)
    if web_dir is None:
        log.warning("no %s found; serving the fallback page", PAGE)
    page = PageSource(web_dir, cfg.location)
    plates = plates_module.Plates((web_dir / "plates") if web_dir else None)
    started = time.monotonic()

    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index() -> Response:
        # Whatever happens in here, something dark and quiet has to come
        # back. The generic error handler returns JSON, and a panel
        # displaying {"error": ...} is worse than a panel displaying
        # nothing at all.
        try:
            body = page.html()
        except Exception as exc:
            log.warning("could not build the page: %s", exc)
            body = fallback_page("the display could not be prepared")
        return Response(body, mimetype="text/html",
                        headers={"Cache-Control": "no-store"})

    @app.get("/recent")
    def recent() -> Response:
        response = jsonify(store.recent(cfg.recent_hours))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> Response:
        status = runtime_status()
        last = store.last_detection()
        body = {
            "ok": bool(status.get("listening")),
            "device": status.get("stream", {}).get("device"),
            "last_detection": last["ts"] if last else None,
            "last_species": last["species"] if last else None,
            # A Pi has no RTC. Unsynchronised means the date may be
            # days off, which quietly changes which species are
            # considered plausible and when "today" starts.
            "clock": {
                "now": clock.iso(dt.datetime.now()),
                "synchronized": clock.synchronized(),
            },
            "soc_temp_c": pi.soc_temp_c(),
            "throttled": pi.throttled(),
            "uptime_s": round(time.monotonic() - started),
            "board": pi.board_model(),
            "load": pi.load_average(),
            "disk": pi.disk_free(cfg.data_path),
            "display": {
                "web_dir": str(web_dir) if web_dir else None,
                "page": web_dir is not None,
                # How many Audubon plates are installed. 0 is fine — the
                # page falls back to the drawn silhouettes.
                "plates": len(plates),
            },
            "db": store.stats(),
            "counts": store.counts(),
            **status,
        }
        return jsonify(body)

    # The seam for the Audubon cutouts. Ask for a bird by whatever
    # BirdNET calls it — /plates/House%20Finch, /plates/house-finch.png,
    # either works — and the matching file comes back if it exists.
    @app.get("/plates/<path:name>")
    def plate(name: str):
        path = plates.path_for(name)
        if path is None:
            return jsonify({"error": "no plate for that species"}), 404
        # Short, not a day. Artwork gets replaced -- that is the whole
        # point of the plates/ seam -- and a browser holding a stale copy
        # for 24 hours makes a new cut look like it did nothing. Flask
        # still sends ETag/Last-Modified, so a revalidation on localhost
        # is a cheap 304 rather than a re-download.
        return send_from_directory(path.parent, path.name, max_age=60)

    @app.get("/fonts/<path:name>")
    def font(name: str):
        # Missing is fine and fast: the page falls back to the system
        # serif rather than waiting on anything.
        if web_dir is None:
            return Response(status=404)
        return send_from_directory(web_dir / "fonts", name, max_age=31536000)

    # Which birds have pictures. The page needs this to decide what it
    # can draw, and it is how the artwork stays additive: cut one plate,
    # drop it in, that bird starts appearing.
    @app.get("/plates")
    def plate_list() -> Response:
        response = jsonify({
            "species": plates.keys(),
            "files": [p.name for p in plates.files()],
        })
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(Exception)
    def failed(exc):
        # Whatever went wrong, the panel gets valid JSON and the page
        # keeps showing the birds it already has.
        if isinstance(exc, HTTPException):
            # A wrong method or a bad path is not a server fault; say so
            # accurately, because /health debugging leans on these codes.
            return jsonify({"error": exc.name.lower()}), exc.code
        log.warning("request failed: %s", exc, exc_info=False)
        return jsonify({"error": exc.__class__.__name__}), 500

    return app


def serve(app: Flask, host: str, port: int) -> None:
    """waitress if it is installed, Flask's own server if it is not."""
    try:
        from waitress import serve as waitress_serve
    except ImportError:
        app.run(host=host, port=port, threaded=True, debug=False,
                use_reloader=False)
        return
    waitress_serve(app, host=host, port=port, threads=4,
                   clear_untrusted_proxy_headers=True, ident=None)
