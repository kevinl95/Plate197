"""How long one 3-second window actually takes on this machine.

Step 2 of the build brief, and the number that decides whether any of
the rest is viable. Run it on the Pi itself — a laptop figure means
nothing here.

    python -m earshot bench
    python -m earshot bench --windows 50 --threads 1 --wav some.wav
"""

from __future__ import annotations

import logging
import statistics
import time
import wave
from pathlib import Path

import numpy as np

from . import models, pi, runtime
from .classify import SEGMENT_SAMPLES, Classifier
from .config import Config


def _audio(path: Path | None, samplerate: int) -> np.ndarray:
    """A window of real audio if offered, otherwise quiet noise."""
    if path is None:
        rng = np.random.default_rng(0)
        return (rng.standard_normal(SEGMENT_SAMPLES) * 0.05).astype(np.float32)
    with wave.open(str(path)) as wav:
        if wav.getframerate() != samplerate:
            raise SystemExit(f"{path} is {wav.getframerate()} Hz; "
                             f"BirdNET needs {samplerate}")
        frames = wav.readframes(min(wav.getnframes(), SEGMENT_SAMPLES))
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if pcm.size < SEGMENT_SAMPLES:
        pcm = np.pad(pcm, (0, SEGMENT_SAMPLES - pcm.size))
    return pcm[:SEGMENT_SAMPLES]


def run(cfg: Config, windows: int = 20, threads: int | None = None,
        wav: Path | None = None) -> int:
    threads = threads or cfg.tflite_threads
    # This is a report, not a service; the species-list line it would
    # otherwise print is already in the startup line below.
    logging.getLogger("earshot.classify").setLevel(logging.ERROR)
    if not models.have(cfg.model_dir, cfg.model_precision):
        raise SystemExit(f"no models in {cfg.model_dir}; "
                         f"run `python -m earshot models` first")

    print(f"board      {pi.board_model() or 'unknown'}")
    print(f"runtime    {runtime.interpreter_class()[0]}")
    print(f"model      BirdNET v2.4 {cfg.model_precision}, {threads} thread(s)")
    temp_before = pi.soc_temp_c()

    t0 = time.perf_counter()
    classifier = Classifier(
        model_dir=cfg.model_dir, cache_path=cfg.species_cache_path,
        lat=cfg.latitude, lon=cfg.longitude, min_conf=cfg.min_conf,
        location_threshold=cfg.location_filter_threshold, threads=threads,
        keep_non_event=cfg.keep_non_event)
    startup = time.perf_counter() - t0
    print(f"startup    {startup*1000:.0f} ms "
          f"(models loaded, {int(classifier.range.mask.sum())} species in range "
          f"for week {classifier.range.week})")

    samples = _audio(wav, cfg.sample_rate)
    for _ in range(3):
        classifier.classify(samples)

    times = []
    for _ in range(windows):
        start = time.perf_counter()
        classifier.classify(samples)
        times.append((time.perf_counter() - start) * 1000.0)
    times.sort()

    p50 = statistics.median(times)
    p90 = times[min(len(times) - 1, int(0.9 * len(times)))]
    print(f"\n{windows} windows of {cfg.window_s:.0f}s audio")
    print(f"  p50      {p50:7.1f} ms")
    print(f"  p90      {p90:7.1f} ms")
    print(f"  max      {times[-1]:7.1f} ms")
    # What matters operationally: a window every hop_s, so this is the
    # share of one core the listening costs, continuously.
    print(f"  duty     {100*p50/1000/cfg.hop_s:7.1f} % of one core "
          f"at a {cfg.hop_s:.0f}s hop")
    if p50 / 1000.0 > cfg.hop_s:
        print("  WARNING: slower than the hop — windows will be dropped")

    temp_after = pi.soc_temp_c()
    if temp_before is not None:
        print(f"\nSoC temp   {temp_before} C -> {temp_after} C")
    state = pi.throttled()
    if state.get("available"):
        print(f"throttled  {state['raw']} "
              f"{'(power ok)' if state['power_ok'] else '(UNDERVOLTAGE)'}"
              + (f" {state['flags']}" if state["flags"] else ""))
    return 0
