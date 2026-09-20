"""Settings, with defaults that already fit the frame on the table.

One optional TOML file (``/etc/earshot.toml``) and per-key environment
overrides, so a systemd drop-in can change the mic name without anyone
editing a file inside /opt.

Nothing here reaches the network or the disk; it is only values.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = "/etc/earshot.toml"
# Looked for beside the code first, so a checkout can carry real settings
# without them ever being staged. It is in .gitignore.
LOCAL_CONFIG_NAME = "earshot.toml"
# And on the boot partition, which is the only part of the card a person
# can edit with no keyboard, no SSH and no Linux: pull the card, open the
# file in any text editor, put it back. Settings here win over the ones
# the image baked into /etc.
BOOT_CONFIG_PATHS = ("/boot/firmware/plate197.toml", "/boot/plate197.toml")
ENV_PREFIX = "EARSHOT_"


@dataclass(frozen=True)
class Config:
    # ---- capture -------------------------------------------------------
    # Substring match against sounddevice's device names, case-insensitive.
    # Never an index: hw:1,0 moves the moment another USB device appears.
    device_match: str = "USB"
    sample_rate: int = 48000
    block_ms: int = 100          # PortAudio callback size; 100ms is unhurried
    window_s: float = 3.0        # BirdNET's input length, not negotiable
    hop_s: float = 2.0           # => 1s of overlap between windows
    # No audio for this long means the stream is wedged rather than quiet.
    # A live stream delivers a block every block_ms, silence included.
    stall_s: float = 5.0
    restart_min_s: float = 2.0   # backoff bounds for reopening the stream
    restart_max_s: float = 60.0

    # ---- classify ------------------------------------------------------
    min_conf: float = 0.65
    # A placeholder, deliberately. This is a public repository and the
    # feeder's coordinates are nobody's business; Denver's centre is close
    # enough to the Front Range that the species list is 98% the same.
    # The real ones go in /etc/earshot.toml, or earshot.toml beside the
    # code, neither of which is committed.
    latitude: float = 39.7392    # Denver, Colorado
    longitude: float = -104.9903
    # What the page prints in its footer. Same reasoning.
    location: str = "Colorado"
    # BirdNET-Analyzer's own default for the range model. Anything the
    # meta-model scores below this is not plausible here this week.
    location_filter_threshold: float = 0.03
    model_precision: str = "fp32"   # fp32 | fp16 | int8
    tflite_threads: int = 2      # of 4 cores; leaves room for Chromium
    # BirdNET's 11 non-event classes (Dog, Engine, Siren, ...) are real
    # predictions but not birds, and writing them every few seconds would
    # be a lot of pointless SD card traffic.
    keep_non_event: bool = False

    # ---- store ---------------------------------------------------------
    data_dir: str = "/var/lib/earshot"
    db_name: str = "detections.db"
    write_batch: int = 8         # rows before a flush
    write_interval_s: float = 15.0   # ...or seconds, whichever comes first

    # ---- serve ---------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8197
    # How far back /recent looks before handing rows to the page. The
    # page's own window is "since local midnight" (see DECAY_HOURS in
    # index-7inch.html), which is at most ~24h regardless of what time it
    # is now — so 25 always covers it with a little slack, and costs
    # nothing extra: the query groups by species, so the response is at
    # most one row per species whether this is 6 or 25.
    recent_hours: int = 25
    web_dir: str = ""            # blank => look next to the package

    # ---- housekeeping --------------------------------------------------
    log_level: str = "WARNING"   # journald on an SD card; info is not free
    # Below this, the clock has not been set yet and a timestamp would be
    # a lie. Detections wait (in memory) until NTP lands.
    min_plausible_year: int = 2025

    # ---- derived -------------------------------------------------------
    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_path / self.db_name

    @property
    def model_dir(self) -> Path:
        return self.data_path / "models"

    @property
    def species_cache_path(self) -> Path:
        return self.data_path / "species-filter.json"

    @property
    def window_samples(self) -> int:
        return int(round(self.window_s * self.sample_rate))

    @property
    def hop_samples(self) -> int:
        return int(round(self.hop_s * self.sample_rate))

    @property
    def block_samples(self) -> int:
        return int(round(self.block_ms / 1000.0 * self.sample_rate))

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _coerce(raw, kind):
    if kind is bool:
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return kind(raw)


def config_paths(path: str | os.PathLike | None = None) -> list[Path]:
    """Every file to read, weakest first. Later files win.

    An explicit path (or EARSHOT_CONFIG) replaces the set entirely --
    tests and drop-ins want exactly one file and no surprises.
    Otherwise it is /etc, then a checkout-local file, then the boot
    partition, so the card can always have the last word.
    """
    if path:
        return [Path(path)]
    env = os.environ.get(ENV_PREFIX + "CONFIG")
    if env:
        return [Path(env)]
    found = [Path(DEFAULT_CONFIG_PATH), Path(LOCAL_CONFIG_NAME)]
    found += [Path(p) for p in BOOT_CONFIG_PATHS]
    return found


def load(path: str | os.PathLike | None = None) -> Config:
    """TOML (if present) under environment overrides, both optional."""
    values: dict[str, object] = {}

    for cfg_path in config_paths(path):
        if not cfg_path.is_file():
            continue
        try:
            with cfg_path.open("rb") as fh:
                values.update(tomllib.load(fh))
        except (OSError, tomllib.TOMLDecodeError):
            # A typo on the boot partition must not stop the frame from
            # starting -- it just doesn't get applied.
            continue

    known = {f.name: f.type for f in fields(Config)}
    kinds = {"str": str, "int": int, "float": float, "bool": bool}
    for name, annotation in known.items():
        env = os.environ.get(ENV_PREFIX + name.upper())
        if env is not None:
            values[name] = env
    for name in list(values):
        if name not in known:
            values.pop(name)  # a typo in the TOML should not crash the service
            continue
        kind = kinds[known[name]] if isinstance(known[name], str) else known[name]
        values[name] = _coerce(values[name], kind)

    return Config(**values)  # type: ignore[arg-type]
