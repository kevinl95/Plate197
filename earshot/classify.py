"""BirdNET v2.4: what was that.

Two models. The audio model turns 3 seconds of 48kHz mono into 6522
logits. The meta-model turns a place and a week into the subset of those
6522 that could plausibly be here now — around 140 species for one place
in one week of the year, rather than every bird on earth. Restricting the list is the single
biggest thing that can be done for accuracy: most false positives are
tropical birds that were never in the running.

The meta-model runs once a week, not once a window, and its 29MB
interpreter is dropped as soon as it has answered.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import runtime

log = logging.getLogger(__name__)

SEGMENT_SAMPLES = 144_000      # 3.0s at 48kHz — the model's fixed input
NUM_CLASSES = 6522

# V2.4 has "6,522 classes (incl. 11 non-event classes)", and these are the
# eleven. Named rather than detected: the obvious heuristics all get it
# wrong. "Human vocal" has a space in it like a binomial does, and two
# genuine species — Gryllus assimilis and Miogryllus saussurei, both
# crickets — are labelled with the same name on both sides of the
# underscore, so neither test separates the list cleanly.
NON_EVENT_CLASSES = frozenset({
    "Dog", "Engine", "Environmental", "Fireworks", "Gun",
    "Human non-vocal", "Human vocal", "Human whistle",
    "Noise", "Power tools", "Siren",
})


@dataclass(frozen=True)
class Detection:
    species: str       # common name, as the page's SPECIES keys spell it
    sci: str
    conf: float


def week_of_48(day: dt.date) -> int:
    """BirdNET's calendar: four weeks to a month, 1..48.

    Not ISO weeks. Days 1-7 of a month are week 1 of that month, 8-14
    week 2, and everything from the 22nd on is week 4.
    """
    return (day.month - 1) * 4 + min(4, (day.day - 1) // 7 + 1)


def flat_sigmoid(logits: np.ndarray, sensitivity: float = 1.0) -> np.ndarray:
    """BirdNET-Analyzer's own output transform, clipping included."""
    return 1.0 / (1.0 + np.exp(-sensitivity * np.clip(logits, -20.0, 20.0)))


class Labels:
    """The 6522 class names, split into scientific and common."""

    def __init__(self, path: Path):
        lines = [line.strip() for line in path.read_text(encoding="utf8").splitlines()]
        self.raw = [line for line in lines if line]
        # "Haemorhous mexicanus_House Finch" -> last underscore is the seam
        self.sci: list[str] = []
        self.common: list[str] = []
        for line in self.raw:
            sci, _, common = line.rpartition("_")
            self.sci.append(sci or line)
            self.common.append(common or line)
        # Everything that is not one of the eleven. The pairing of both
        # halves is checked too, so a bird that happened to be called one
        # of these would still count.
        self.is_bird = np.array(
            [not (sci == common and sci in NON_EVENT_CLASSES)
             for sci, common in zip(self.sci, self.common)], dtype=bool)

    def __len__(self) -> int:
        return len(self.raw)

    def index_of_common(self, name: str) -> int | None:
        try:
            return self.common.index(name)
        except ValueError:
            return None


class RangeFilter:
    """Which species are plausible at this latitude, longitude and week."""

    def __init__(self, model_path: Path, labels: Labels, cache_path: Path,
                 lat: float, lon: float, threshold: float, threads: int = 1):
        self.model_path = Path(model_path)
        self.labels = labels
        self.cache_path = Path(cache_path)
        self.lat, self.lon = lat, lon
        self.threshold = threshold
        self.threads = threads
        self.week: int | None = None
        self.mask = np.ones(len(labels), dtype=bool)

    def _signature(self, week: int) -> dict:
        return {"week": week, "lat": self.lat, "lon": self.lon,
                "threshold": self.threshold, "classes": len(self.labels)}

    def _read_cache(self, week: int) -> np.ndarray | None:
        try:
            cached = json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return None
        if cached.get("signature") != self._signature(week):
            return None
        mask = np.zeros(len(self.labels), dtype=bool)
        mask[np.array(cached["indices"], dtype=int)] = True
        return mask

    def _write_cache(self, week: int, mask: np.ndarray) -> None:
        payload = {"signature": self._signature(week),
                   "indices": np.nonzero(mask)[0].tolist(),
                   "species": [self.labels.common[i] for i in np.nonzero(mask)[0]]}
        try:
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(self.cache_path)
        except OSError as exc:            # a full card is not fatal here
            log.warning("could not cache species filter: %s", exc)

    def _compute(self, week: int) -> np.ndarray:
        interp = runtime.load(self.model_path, num_threads=self.threads)
        try:
            inp = interp.get_input_details()[0]
            out = runtime.output_for(interp, len(self.labels))
            sample = np.expand_dims(
                np.array([self.lat, self.lon, week], dtype=np.float32), 0)
            interp.set_tensor(inp["index"], sample)
            interp.invoke()
            scores = interp.get_tensor(out["index"])[0]
        finally:
            del interp        # 29MB back to the system before we listen
        return scores >= self.threshold

    def refresh(self, today: dt.date | None = None) -> bool:
        """Recompute if the week has turned. True if the mask changed."""
        today = today or dt.date.today()
        week = week_of_48(today)
        if week == self.week:
            return False
        mask = self._read_cache(week)
        if mask is None:
            mask = self._compute(week)
            self._write_cache(week, mask)
        # A location the model knows nothing about would silence the whole
        # thing; better to listen for everything than for nothing.
        if not mask.any():
            log.warning("range model returned no species for week %d; "
                        "falling back to the full list", week)
            mask = np.ones(len(self.labels), dtype=bool)
        self.week, self.mask = week, mask
        log.warning("species list for week %d: %d of %d classes",
                    week, int(mask.sum()), len(self.labels))
        return True

    @property
    def species(self) -> list[str]:
        return [self.labels.common[i] for i in np.nonzero(self.mask)[0]]


class Classifier:
    """The audio model, plus the filtering that makes it trustworthy."""

    def __init__(self, model_dir: Path, cache_path: Path, lat: float, lon: float,
                 min_conf: float = 0.65, location_threshold: float = 0.03,
                 threads: int = 2, keep_non_event: bool = False):
        model_dir = Path(model_dir)
        self.labels = Labels(model_dir / "labels.txt")
        if len(self.labels) != NUM_CLASSES:
            log.warning("labels.txt has %d entries, expected %d",
                        len(self.labels), NUM_CLASSES)
        self.min_conf = min_conf
        self.keep_non_event = keep_non_event
        self.interp = runtime.load(model_dir / "audio-model.tflite",
                                   num_threads=threads)
        self._in = self.interp.get_input_details()[0]
        self._out = runtime.output_for(self.interp, len(self.labels))
        self.range = RangeFilter(model_dir / "meta-model.tflite", self.labels,
                                 cache_path, lat, lon, location_threshold,
                                 threads=1)
        self.range.refresh()

    @property
    def runtime_name(self) -> str:
        return runtime.interpreter_class()[0]

    def _allowed(self) -> np.ndarray:
        mask = self.range.mask
        if not self.keep_non_event:
            mask = mask & self.labels.is_bird
        return mask

    def scores(self, samples: np.ndarray) -> np.ndarray:
        """Confidences for all 6522 classes. Input is 3s of float32 [-1,1]."""
        if samples.shape[0] != SEGMENT_SAMPLES:
            raise ValueError(
                f"expected {SEGMENT_SAMPLES} samples, got {samples.shape[0]}")
        batch = np.expand_dims(samples.astype(np.float32, copy=False), 0)
        self.interp.set_tensor(self._in["index"], batch)
        self.interp.invoke()
        logits = self.interp.get_tensor(self._out["index"])[0]
        return flat_sigmoid(logits)

    def classify(self, samples: np.ndarray) -> list[Detection]:
        """Everything heard in this window worth writing down."""
        confidences = self.scores(samples)
        hits = np.nonzero((confidences >= self.min_conf) & self._allowed())[0]
        return [
            Detection(species=self.labels.common[i],
                      sci=self.labels.sci[i],
                      conf=float(confidences[i]))
            for i in sorted(hits, key=lambda i: -confidences[i])
        ]
