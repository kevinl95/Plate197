"""The endpoints, and the one substitution made to the page."""

import datetime as dt
import json

import pytest

from earshot import api
from earshot.config import Config
from earshot.store import Store, connect

REPO = __import__("pathlib").Path(__file__).resolve().parent.parent


@pytest.fixture()
def client(tmp_path):
    cfg = Config(data_dir=str(tmp_path), web_dir=str(REPO))
    store = Store(cfg.db_path)
    app = api.create_app(cfg, store, lambda: {"listening": True, "stream": {"device": "Test Mic"}})
    app.config.update(TESTING=True)
    return app.test_client(), store


def test_recent_is_a_list(client):
    http, _ = client
    response = http.get("/recent")
    assert response.status_code == 200
    assert response.json == []
    assert response.headers["Cache-Control"] == "no-store"


def test_recent_carries_the_fields_the_page_reads(client):
    http, store = client
    with connect(store.path) as conn:
        conn.execute("INSERT INTO detections (species, sci, ts, conf) VALUES (?,?,?,?)",
                     ("House Finch", "Haemorhous mexicanus",
                      dt.datetime.now().replace(microsecond=0).isoformat(sep="T"), 0.91))
    row = http.get("/recent").json[0]
    assert row["species"] == "House Finch"
    assert row["sci"] == "Haemorhous mexicanus"
    assert row["conf"] == 0.91
    assert row["count_today"] == 1
    assert row["first_ever"] is True


def test_health_has_what_the_brief_asks_for(client):
    http, _ = client
    body = http.get("/health").json
    for key in ("device", "last_detection", "soc_temp_c", "throttled"):
        assert key in body, key
    assert body["device"] == "Test Mic"
    # On anything that is not a Pi this reports unavailable rather than failing.
    assert "available" in body["throttled"]


def test_the_page_is_pointed_at_this_server(client):
    http, _ = client
    html = http.get("/").data.decode()
    assert "API          : location.origin" in html
    assert "API          : null" not in html

    # Only the substitutions we actually make, and nothing structural.
    # This used to assert an exact byte delta, which was too brittle to
    # survive adding a second rewrite.
    original = (REPO / "index-7inch.html").read_text()
    assert "renderBird" in html and "Black-capped Chickadee" in html
    for marker in ("#stage", "--night-deep", "latestBySpecies", "opacityFor"):
        assert marker in html, f"{marker} must survive the rewrite"
    # The only lines that disappear are the two preconnect hints for the
    # font host we no longer use.
    assert 0 <= len(original.splitlines()) - len(html.splitlines()) <= 2


def test_missing_page_gives_a_quiet_fallback_not_a_stack_trace(tmp_path):
    cfg = Config(data_dir=str(tmp_path), web_dir=str(tmp_path / "nowhere"))
    store = Store(cfg.db_path)
    http = api.create_app(cfg, store).test_client()
    body = http.get("/").data.decode()
    assert "Quiet at the feeder" in body
    assert "Traceback" not in body


def test_unknown_paths_are_json_404s(client):
    http, _ = client
    response = http.get("/whatever")
    assert response.status_code == 404
    assert json.loads(response.data)["error"] == "not found"


def test_a_wrong_method_is_not_a_server_error(client):
    http, _ = client
    response = http.post("/recent")
    assert response.status_code == 405
    assert "error" in response.json


def test_web_dir_is_found_in_an_installed_layout(tmp_path, monkeypatch):
    # pip install puts the package in site-packages, so the page has to be
    # looked for at /opt/earshot explicitly. This is the shape of the Pi.
    opt = tmp_path / "opt" / "earshot"
    opt.mkdir(parents=True)
    (opt / api.PAGE).write_text("<html>x</html>")
    monkeypatch.setattr(api, "WEB_DIRS", (tmp_path / "site-packages", opt))
    assert api.find_web_dir() == opt


def test_an_explicit_web_dir_wins(tmp_path, monkeypatch):
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    (chosen / api.PAGE).write_text("<html>x</html>")
    other = tmp_path / "other"
    other.mkdir()
    (other / api.PAGE).write_text("<html>y</html>")
    monkeypatch.setattr(api, "WEB_DIRS", (other,))
    assert api.find_web_dir(str(chosen)) == chosen


def test_a_plate_is_served_from_plates(tmp_path):
    from earshot.config import Config
    from earshot.store import Store
    web = tmp_path / "web"
    (web / "plates").mkdir(parents=True)
    (web / api.PAGE).write_text("<html>x</html>")
    # A one-pixel PNG is enough to prove the route and the content type.
    (web / "plates" / "house-finch.png").write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000"
                      "001f15c4890000000a49444154789c6300010000050001"
                      "0d0a2db40000000049454e44ae426082"))
    cfg = Config(data_dir=str(tmp_path), web_dir=str(web))
    http = api.create_app(cfg, Store(cfg.db_path)).test_client()

    found = http.get("/plates/house-finch.png")
    assert found.status_code == 200
    assert found.headers["Content-Type"] == "image/png"

    # A species with no plate yet is a 404, not a 500 — the page decides
    # what to do about it.
    assert http.get("/plates/black-billed-magpie.png").status_code == 404


def test_the_footer_says_what_the_config_says(tmp_path):
    from earshot.config import Config
    from earshot.store import Store
    cfg = Config(data_dir=str(tmp_path), web_dir=str(REPO),
                 location="Somewhere Else")
    http = api.create_app(cfg, Store(cfg.db_path)).test_client()
    html = http.get("/").data.decode()
    assert 'LOCATION     : "Somewhere Else"' in html
    # ...and the file it was read from is untouched, as with CONFIG.API.
    assert "Somewhere Else" not in (REPO / api.PAGE).read_text()


def test_the_fallback_page_does_not_blow_up_on_its_own_css():
    """The placeholder page must not be the thing that fails.

    It was built with `FALLBACK_PAGE % reason`, and its CSS contains
    `height:100%`, so the % operator raised ValueError: unsupported
    format character ';'. The frame showed {"error":"ValueError"} --
    the page that exists to cover a failure caused a worse one.
    """
    html = api.fallback_page("the display file could not be read")
    assert "the display file could not be read" in html
    assert "height:100%" in html, "the CSS must survive substitution intact"
    assert "{reason}" not in html


def test_index_never_returns_a_server_error(tmp_path, monkeypatch):
    """A picture frame must never render JSON at a person."""
    from earshot.config import Config
    from earshot.store import Store
    cfg = Config(data_dir=str(tmp_path), web_dir=str(REPO))
    store = Store(cfg.db_path)
    app = api.create_app(cfg, store)

    # Make page rendering fail the worst way it can.
    def explode(self):
        raise RuntimeError("disk fell off")
    monkeypatch.setattr(api.PageSource, "html", explode)

    response = app.test_client().get("/")
    assert response.status_code == 200
    body = response.data.decode()
    assert body.lstrip().startswith("<!DOCTYPE html"), "must still be a page"
    assert "Quiet at the feeder" in body


def test_an_unreadable_page_is_treated_as_missing(tmp_path, monkeypatch):
    """Mode 600 root-owned is 'present' but useless to the service user.

    WEB_DIRS is emptied so only the directory under test is considered;
    otherwise the search correctly falls through to a readable copy
    elsewhere and the skip is invisible.
    """
    monkeypatch.setattr(api, "WEB_DIRS", ())
    web = tmp_path / "web"
    web.mkdir()
    page = web / api.PAGE
    page.write_text("<html>x</html>")
    assert api.find_web_dir(str(web)) == web

    page.chmod(0o000)
    try:
        assert api.find_web_dir(str(web)) is None, \
            "an unreadable page must not be reported as found"
    finally:
        page.chmod(0o644)


def test_the_page_never_reaches_the_network_for_its_typeface(client):
    """A frame with no route must still paint, immediately.

    The typeface arrived as a <link rel="stylesheet"> pointing at
    fonts.googleapis.com, which is render-blocking: with no network
    Chromium paints nothing until DNS gives up, and the panel is white.
    An offline picture frame also has no business phoning out to draw
    its own footer. It is served from the Pi instead.
    """
    http, _ = client
    html = http.get("/").data.decode()
    assert "fonts.googleapis.com" not in html, "must not reach out for the font"
    assert "fonts.gstatic.com" not in html, "preconnect hints must go too"
    assert '<link href="/fonts/spectral.css" rel="stylesheet">' in html

    # The committed file keeps the designer's original markup.
    assert "fonts.googleapis.com" in (REPO / api.PAGE).read_text()


def test_a_missing_font_is_a_fast_404_not_a_hang(tmp_path):
    """If the build never fetched the faces, the page still paints.

    A 404 on localhost is instant; the page falls through to the system
    serif. That is the whole point of serving it ourselves.
    """
    from earshot.config import Config
    from earshot.store import Store
    web = tmp_path / "web"
    web.mkdir()
    (web / api.PAGE).write_text("<html>x</html>")
    cfg = Config(data_dir=str(tmp_path), web_dir=str(web))
    http = api.create_app(cfg, Store(cfg.db_path)).test_client()
    assert http.get("/fonts/spectral.css").status_code == 404
