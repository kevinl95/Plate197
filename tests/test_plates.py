"""Naming a picture after the right bird.

The whole hazard here is that Audubon's plate names are not BirdNET's
names — his House Finch is the "Crimson-necked Bull-finch" — so a file
named after the plate matches nothing the classifier will ever say. The
matching is forgiving about spelling and unforgiving about that.
"""

import pytest

from earshot import plates

ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082")


def make(directory, *names):
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(ONE_PIXEL_PNG)
    return plates.Plates(directory)


@pytest.mark.parametrize("spelling", [
    "House Finch", "house finch", "HOUSE FINCH", "house-finch",
    "house_finch", "house-finch.png", "House Finch.PNG", "housefinch",
])
def test_one_bird_answers_to_any_reasonable_spelling(tmp_path, spelling):
    have = make(tmp_path / "plates", "house-finch.png")
    assert have.has(spelling), spelling


def test_the_file_on_disk_can_be_spelled_anyhow_too(tmp_path):
    have = make(tmp_path / "plates", "Black_Capped Chickadee.PNG")
    assert have.has("Black-capped Chickadee")


def test_accents_and_apostrophes(tmp_path):
    have = make(tmp_path / "plates", "wilsons-warbler.png")
    assert have.has("Wilson's Warbler")
    assert plates.filename_for("Wilson's Warbler") == "wilsons-warbler.png"
    assert plates.key("Müller's Barbet") == plates.key("mullers-barbet.png")


def test_suggested_filenames_are_lowercase_and_url_safe(tmp_path):
    assert plates.filename_for("Red-winged Blackbird") == "red-winged-blackbird.png"
    assert plates.filename_for("Northern Flicker") == "northern-flicker.png"
    assert " " not in plates.filename_for("Black-capped Chickadee")


def test_non_images_are_not_plates(tmp_path):
    have = make(tmp_path / "plates", "house-finch.png")
    (tmp_path / "plates" / "README.md").write_text("notes on cutting these")
    (tmp_path / "plates" / "house-finch.xcf").write_bytes(b"source file")
    assert len(have) == 1


def test_a_plate_named_after_audubon_matches_no_species(tmp_path):
    # The mistake this whole module exists to catch.
    have = make(tmp_path / "plates",
                "house-finch.png", "crimson-necked-bull-finch.png")
    stray = plates.unmatched(have, ["House Finch", "Mourning Dove"])
    assert stray == ["crimson-necked-bull-finch.png"]


def test_two_files_for_one_bird_do_not_both_win(tmp_path, caplog):
    have = make(tmp_path / "plates", "house-finch.png", "House Finch.png")
    assert len(have) == 1
    assert "two plates match" in caplog.text.lower() or len(have) == 1


def test_a_plate_added_later_is_noticed(tmp_path):
    directory = tmp_path / "plates"
    have = make(directory, "house-finch.png")
    assert not have.has("Cedar Waxwing")
    # Drop in one more picture; no restart, no cache to clear.
    (directory / "cedar-waxwing.png").write_bytes(ONE_PIXEL_PNG)
    import os
    os.utime(directory, (0, 0))          # force a different mtime
    assert have.has("Cedar Waxwing")


def test_no_plates_directory_at_all_is_fine(tmp_path):
    have = plates.Plates(tmp_path / "nothing-here")
    assert len(have) == 0
    assert not have.has("House Finch")
    assert have.keys() == []


def test_no_web_directory_at_all_is_fine():
    have = plates.Plates(None)
    assert len(have) == 0
    assert not have.has("House Finch")


def test_describe_reads_png_dimensions_without_pillow(tmp_path):
    """describe() must work with no image library installed at all --
    it reads the PNG header directly, since `earshot check` calls it on
    every run and can't require Pillow just to report a size."""
    from PIL import Image
    directory = tmp_path / "plates"
    directory.mkdir()
    Image.new("RGBA", (2000, 1500), (0, 0, 0, 0)).save(directory / "house-finch.png")
    Image.new("RGBA", (200, 150), (0, 0, 0, 0)).save(directory / "mourning-dove.png")

    rows = {r["name"]: r for r in plates.describe(plates.Plates(directory))}
    assert rows["house-finch.png"]["width"] == 2000
    assert rows["house-finch.png"]["height"] == 1500
    assert rows["house-finch.png"]["oversized"] is True
    assert rows["mourning-dove.png"]["oversized"] is False


def test_optimize_shrinks_only_what_needs_it_and_keeps_alpha(tmp_path):
    from PIL import Image
    directory = tmp_path / "plates"
    directory.mkdir()
    big = Image.new("RGBA", (2000, 1500), (0, 0, 0, 0))
    big.paste((200, 150, 100, 255), [500, 400, 1500, 1100])
    big.save(directory / "house-finch.png")
    small = Image.new("RGBA", (100, 80), (10, 10, 10, 255))
    small.save(directory / "mourning-dove.png")

    have = plates.Plates(directory)
    changed = plates.optimize(have)

    assert len(changed) == 1 and "house-finch.png" in changed[0]
    resized = Image.open(directory / "house-finch.png")
    assert max(resized.size) <= plates.MAX_SIDE
    lo, hi = resized.convert("RGBA").split()[-1].getextrema()
    assert lo == 0 and hi == 255, "alpha must survive the resize"
    # the untouched file really is untouched
    untouched = Image.open(directory / "mourning-dove.png")
    assert untouched.size == (100, 80)


def test_optimize_without_pillow_fails_clearly(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no module named PIL")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(RuntimeError, match="Pillow"):
        plates.optimize(plates.Plates(tmp_path))


def test_describe_survives_a_file_that_is_not_actually_a_png(tmp_path):
    directory = tmp_path / "plates"
    directory.mkdir()
    (directory / "house-finch.png").write_bytes(b"not a png at all")
    rows = plates.describe(plates.Plates(directory))
    assert rows[0]["width"] is None and rows[0]["oversized"] is False
