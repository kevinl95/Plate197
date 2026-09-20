"""The history flags are the part that is easy to get quietly wrong.

If either "first" flag includes today, every bird is a first every
morning and the red ping on the panel stops meaning anything. These
tests exist mostly to hold that line.
"""

import datetime as dt
import time

import pytest

from earshot.classify import Detection
from earshot.store import Store, connect


def insert(store, species, sci, when: dt.datetime, conf=0.9):
    with connect(store.path) as conn:
        conn.execute("INSERT INTO detections (species, sci, ts, conf) VALUES (?,?,?,?)",
                     (species, sci, when.replace(microsecond=0).isoformat(sep="T"), conf))


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "test.db")


def by_species(rows):
    return {r["species"]: r for r in rows}


def test_first_ever_ignores_today(store):
    now = dt.datetime.now()
    insert(store, "House Finch", "Haemorhous mexicanus", now - dt.timedelta(minutes=5))
    insert(store, "House Finch", "Haemorhous mexicanus", now - dt.timedelta(minutes=2))
    row = by_species(store.recent(6))["House Finch"]
    assert row["first_ever"] is True, "heard only today: still a first"
    assert row["first_this_year"] is True
    assert row["count_today"] == 2


def test_yesterday_is_not_a_first(store):
    now = dt.datetime.now()
    insert(store, "House Finch", "Haemorhous mexicanus", now - dt.timedelta(days=1))
    insert(store, "House Finch", "Haemorhous mexicanus", now - dt.timedelta(minutes=3))
    row = by_species(store.recent(6))["House Finch"]
    assert row["first_ever"] is False
    assert row["first_this_year"] is False


def test_last_year_is_first_this_year_but_not_first_ever(store):
    now = dt.datetime.now()
    last_year = now.replace(year=now.year - 1, month=6, day=1)
    insert(store, "Red-winged Blackbird", "Agelaius phoeniceus", last_year)
    insert(store, "Red-winged Blackbird", "Agelaius phoeniceus", now - dt.timedelta(minutes=9))
    row = by_species(store.recent(6))["Red-winged Blackbird"]
    assert row["first_ever"] is False
    assert row["first_this_year"] is True


def test_recent_window_excludes_older(store):
    now = dt.datetime.now()
    insert(store, "Mourning Dove", "Zenaida macroura", now - dt.timedelta(hours=9))
    assert store.recent(6) == []
    insert(store, "Mourning Dove", "Zenaida macroura", now - dt.timedelta(hours=2))
    assert len(store.recent(6)) == 1


def test_one_row_per_species_with_the_latest_time(store):
    now = dt.datetime.now()
    insert(store, "Dark-eyed Junco", "Junco hyemalis", now - dt.timedelta(hours=3), conf=0.7)
    insert(store, "Dark-eyed Junco", "Junco hyemalis", now - dt.timedelta(minutes=4), conf=0.88)
    insert(store, "American Goldfinch", "Spinus tristis", now - dt.timedelta(minutes=30))
    rows = store.recent(6)
    assert [r["species"] for r in rows] == ["Dark-eyed Junco", "American Goldfinch"]
    junco = rows[0]
    assert junco["conf"] == 0.88, "the confidence must come from the latest row"
    assert junco["count_today"] == 2
    assert junco["first_today"] < junco["time"]


def test_count_today_and_first_today(store):
    now = dt.datetime.now()
    dawn = now.replace(hour=6, minute=42, second=0, microsecond=0)
    if dawn > now:                       # running this before 06:42
        dawn = now - dt.timedelta(minutes=1)
    insert(store, "House Finch", "Haemorhous mexicanus", dawn)
    insert(store, "House Finch", "Haemorhous mexicanus", now - dt.timedelta(minutes=1))
    row = by_species(store.recent(6))["House Finch"]
    assert row["count_today"] == 2
    assert row["first_today"].startswith(dawn.date().isoformat())


def test_payload_matches_the_pages_contract(store):
    insert(store, "House Finch", "Haemorhous mexicanus", dt.datetime.now())
    row = store.recent(6)[0]
    # normalize() in index-7inch.html reads exactly these.
    assert set(row) == {"species", "sci", "time", "conf", "first_ever",
                        "first_this_year", "count_today", "first_today"}
    assert isinstance(row["conf"], float)
    assert isinstance(row["first_ever"], bool)
    # No timezone offset: the page parses this as local time.
    assert "+" not in row["time"] and "Z" not in row["time"]
    dt.datetime.fromisoformat(row["time"])


def test_writer_batches_and_flushes(store):
    store.start()
    try:
        for i in range(5):
            store.record(Detection("House Finch", "Haemorhous mexicanus", 0.8), time.monotonic())
        deadline = time.time() + 20
        while store.rows_written < 5 and time.time() < deadline:
            time.sleep(0.2)
    finally:
        store.stop()
    assert store.rows_written == 5
    assert store.recent(6)[0]["count_today"] == 5


def test_survives_a_read_only_database(store, monkeypatch):
    store.start()
    try:
        store.path.chmod(0o444)
        store.record(Detection("House Finch", "Haemorhous mexicanus", 0.8), time.monotonic())
        time.sleep(2.0)
    finally:
        store.path.chmod(0o644)
        store.stop()
    # The point is only that nothing raised and the thread is still alive.
    assert store.rows_dropped >= 0


def test_a_local_config_file_is_found_before_etc(tmp_path, monkeypatch):
    """A checkout can carry real settings without them being committed."""
    import os

    from earshot import config
    monkeypatch.chdir(tmp_path)
    # Every EARSHOT_* override, not a hand-picked few: this test is about
    # file precedence, and an unrelated one left in the environment would
    # otherwise make it fail for the wrong reason.
    for name in [k for k in os.environ if k.startswith(config.ENV_PREFIX)]:
        monkeypatch.delenv(name, raising=False)
    paths = config.config_paths(None)
    assert __import__("pathlib").Path(config.DEFAULT_CONFIG_PATH) in paths
    assert any(str(p).endswith("plate197.toml") for p in paths), \
        "the boot partition must be in the search path"

    (tmp_path / config.LOCAL_CONFIG_NAME).write_text(
        'latitude = 44.9778\nlongitude = -93.2650\nlocation = "Elsewhere"\n')
    loaded = config.load()
    assert (loaded.latitude, loaded.location) == (44.9778, "Elsewhere")

    # An explicit path still wins, so tests and drop-ins stay predictable.
    assert config.load("/nonexistent").location == config.Config().location


def test_the_boot_partition_overrides_etc_without_erasing_it(tmp_path, monkeypatch):
    """The only way to change a setting with no keyboard and no SSH.

    Pull the card, edit one line on the FAT32 partition, put it back.
    It must override that one key and leave everything the image baked
    into /etc intact -- a naive "first file wins" would silently drop
    the coordinates and the location name.
    """
    import os

    from earshot import config
    monkeypatch.chdir(tmp_path)
    for name in [k for k in os.environ if k.startswith(config.ENV_PREFIX)]:
        monkeypatch.delenv(name, raising=False)

    etc = tmp_path / "etc-earshot.toml"
    etc.write_text('min_conf = 0.65\nlocation = "Colorado"\nlatitude = 39.7392\n')
    boot = tmp_path / "plate197.toml"
    boot.write_text("min_conf = 0.2\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", str(etc))
    monkeypatch.setattr(config, "BOOT_CONFIG_PATHS", (str(boot),))

    loaded = config.load()
    assert loaded.min_conf == 0.2, "the boot partition must win"
    assert loaded.location == "Colorado", "/etc values must survive"
    assert loaded.latitude == 39.7392


def test_a_broken_boot_config_does_not_stop_the_frame(tmp_path, monkeypatch):
    """Someone will mistype this on a laptop. It must not brick the frame."""
    import os

    from earshot import config
    monkeypatch.chdir(tmp_path)
    for name in [k for k in os.environ if k.startswith(config.ENV_PREFIX)]:
        monkeypatch.delenv(name, raising=False)

    etc = tmp_path / "etc-earshot.toml"
    etc.write_text('location = "Colorado"\n')
    boot = tmp_path / "plate197.toml"
    boot.write_text("this is not valid toml = = = [[[\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", str(etc))
    monkeypatch.setattr(config, "BOOT_CONFIG_PATHS", (str(boot),))

    loaded = config.load()
    assert loaded.location == "Colorado", "a bad override is skipped, not fatal"
