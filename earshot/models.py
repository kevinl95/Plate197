"""Getting the BirdNET v2.4 files onto the Pi, once.

The models are not in the BirdNET-Analyzer git tree any more; they are
published as zips on Zenodo (record 15050749) and that is what the
official `birdnet` package downloads. We take the same two files and
skip the 200MB of scientific Python that package brings with it:

    audio-model.tflite   the classifier, 6522 classes
    meta-model.tflite    the range model: lat/lon/week -> plausible here
    labels/en_us.txt     "Scientific name_Common Name", one per class

The meta-model ships only in the int8 zip and is the same file for every
precision, so an fp32 install fetches both archives and an int8 install
fetches one.

Licence: the models are CC BY-NC-SA 4.0 (K. Lisa Yang Center for
Conservation Bioacoustics / Chemnitz University of Technology). Fine for
a bird frame on a table; worth reading before any of this is sold.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ZENODO = "https://zenodo.org/records/15050749/files/"
LABELS_LANG = "en_us"


@dataclass(frozen=True)
class Asset:
    """One file we want, and the archive it lives in."""
    local: str          # name under model_dir
    archive: str        # zip on Zenodo
    member: str         # path inside the zip
    size: int           # expected bytes, from the birdnet package's manifest


AUDIO = {
    "fp32": Asset("audio-model.tflite", "BirdNET_v2.4_tflite.zip",
                  "audio-model.tflite", 51_726_412),
    "fp16": Asset("audio-model.tflite", "BirdNET_v2.4_tflite_fp16.zip",
                  "audio-model-fp16.tflite", 25_932_528),
    "int8": Asset("audio-model.tflite", "BirdNET_v2.4_tflite_int8.zip",
                  "audio-model-int8.tflite", 41_064_296),
}
META = Asset("meta-model.tflite", "BirdNET_v2.4_tflite_int8.zip",
             "meta-model.tflite", 29_526_096)
LABELS = Asset("labels.txt", "BirdNET_v2.4_tflite_int8.zip",
               f"labels/{LABELS_LANG}.txt", 0)   # small; size not pinned


def assets_for(precision: str) -> list[Asset]:
    try:
        return [AUDIO[precision], META, LABELS]
    except KeyError:
        raise ValueError(
            f"model_precision must be one of {sorted(AUDIO)}, got {precision!r}"
        ) from None


def have(model_dir: Path, precision: str) -> bool:
    for asset in assets_for(precision):
        path = model_dir / asset.local
        if not path.is_file():
            return False
        if asset.size and path.stat().st_size != asset.size:
            return False
        if not asset.size and path.stat().st_size == 0:
            return False
    return True


def _download(url: str, dest: Path, on_progress=None) -> None:
    with urllib.request.urlopen(url, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        read = 0
        with dest.open("wb") as fh:
            while chunk := response.read(1 << 20):
                fh.write(chunk)
                read += len(chunk)
                if on_progress:
                    on_progress(read, total)


def fetch(model_dir: Path, precision: str = "fp32", on_progress=None) -> None:
    """Download whatever is missing. Safe to call when everything is there."""
    wanted = [a for a in assets_for(precision)
              if not (model_dir / a.local).is_file()
              or (a.size and (model_dir / a.local).stat().st_size != a.size)]
    if not wanted:
        return

    model_dir.mkdir(parents=True, exist_ok=True)
    by_archive: dict[str, list[Asset]] = {}
    for asset in wanted:
        by_archive.setdefault(asset.archive, []).append(asset)

    with tempfile.TemporaryDirectory(prefix="earshot-models-") as tmp:
        for archive, assets in by_archive.items():
            zip_path = Path(tmp) / archive
            log.warning("downloading %s", archive)   # rare and worth a line
            _download(ZENODO + archive, zip_path, on_progress)
            with zipfile.ZipFile(zip_path) as zf:
                for asset in assets:
                    # Write beside the target and rename, so a cut power
                    # cable can't leave half a model behind.
                    target = model_dir / asset.local
                    staged = target.with_suffix(target.suffix + ".part")
                    with zf.open(asset.member) as src, staged.open("wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    if asset.size and staged.stat().st_size != asset.size:
                        staged.unlink(missing_ok=True)
                        raise OSError(
                            f"{asset.member}: expected {asset.size} bytes, "
                            f"got {staged.stat().st_size}"
                        )
                    staged.replace(target)
            zip_path.unlink(missing_ok=True)


def describe(model_dir: Path) -> dict:
    """What is actually on disk — for /health and for the bench report."""
    out = {}
    for name in ("audio-model.tflite", "meta-model.tflite", "labels.txt"):
        path = model_dir / name
        out[name] = path.stat().st_size if path.is_file() else None
    return out


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()
