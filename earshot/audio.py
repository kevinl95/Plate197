"""The microphone.

Two things in here matter more than the rest.

The device is found by *name*. ALSA's hw:1,0 is a position in a list, and
the list is rebuilt every time something is plugged in — a phone charging
on the other USB port can renumber the mic and the service would happily
record silence from a webcam forever. So: match a substring of the name,
and do it again on every reconnect.

And a stream that stops delivering audio without raising anything is the
normal failure here, not a crash. Silence arrives as blocks of zeros;
nothing arriving at all means the stream is wedged. Five seconds of
literally no callbacks is the signal to tear it down and reopen.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)


class DeviceNotFound(LookupError):
    """No input device whose name contains the configured substring."""


class StreamStalled(RuntimeError):
    """The stream is open but no audio has arrived for too long."""


@dataclass(frozen=True)
class Device:
    index: int
    name: str
    channels: int
    default_samplerate: float

    def __str__(self) -> str:
        return f"{self.name} (device {self.index})"


def rescan() -> None:
    """Make PortAudio look at the USB bus again.

    PortAudio reads the device list once, at initialisation. A mic
    plugged in after that does not exist as far as it is concerned, which
    is precisely the case we have to recover from. These two are private
    in sounddevice, and they are also the only way to do it.
    """
    try:
        sd._terminate()
        sd._initialize()
    except Exception as exc:                      # never fatal
        log.warning("could not reinitialise PortAudio: %s", exc)


def input_devices() -> list[Device]:
    return [
        Device(i, d["name"], int(d["max_input_channels"]), float(d["default_samplerate"]))
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]


def resolve(match: str) -> Device:
    """First input device whose name contains `match`, case-insensitive."""
    wanted = match.strip().lower()
    devices = input_devices()
    for device in devices:
        if wanted in device.name.lower():
            return device
    raise DeviceNotFound(
        f"no input device matching {match!r}; saw: "
        + (", ".join(repr(d.name) for d in devices) or "none")
    )


class Capture:
    """A stream, chopped into overlapping windows.

    Blocks go through a bounded queue rather than being assembled in the
    PortAudio callback, because that callback runs on a realtime thread
    and must never wait for anything. If classification falls behind, the
    queue fills and the oldest audio is dropped on the floor with a count
    kept — better than growing until the Pi runs out of memory.
    """

    def __init__(self, device_match: str, samplerate: int = 48000,
                 window_samples: int = 144_000, hop_samples: int = 96_000,
                 block_samples: int = 4_800, stall_s: float = 5.0,
                 queue_seconds: float = 10.0):
        self.device_match = device_match
        self.samplerate = samplerate
        self.window_samples = window_samples
        self.hop_samples = hop_samples
        self.block_samples = block_samples
        self.stall_s = stall_s
        self._queue: queue.Queue = queue.Queue(
            maxsize=max(4, int(queue_seconds * samplerate / block_samples)))
        self._stream: sd.InputStream | None = None
        self._channels = 1
        self.device: Device | None = None
        self.dropped_blocks = 0
        self.overflows = 0
        self.last_block_at: float | None = None   # time.monotonic()
        self._stop = threading.Event()

    # -- lifecycle ------------------------------------------------------
    def open(self) -> Device:
        rescan()
        device = resolve(self.device_match)
        # Some USB mics refuse a mono stream; take stereo and fold it.
        self._channels = 1
        try:
            sd.check_input_settings(device=device.index, channels=1,
                                    samplerate=self.samplerate, dtype="int16")
        except Exception:
            self._channels = min(2, device.channels)
            sd.check_input_settings(device=device.index, channels=self._channels,
                                    samplerate=self.samplerate, dtype="int16")
            log.warning("%s will not do mono at %d Hz; opening %d channels",
                        device, self.samplerate, self._channels)

        self._stream = sd.InputStream(
            device=device.index,
            channels=self._channels,
            samplerate=self.samplerate,
            dtype="int16",
            blocksize=self.block_samples,
            # 'high' asks PortAudio for a roomier buffer. On a Pi that is
            # also running a browser, the extra latency is invisible and
            # the missing xruns are not.
            latency="high",
            callback=self._on_block,
        )
        self._stream.start()
        self.device = device
        self.last_block_at = time.monotonic()
        log.warning("listening on %s at %d Hz", device, self.samplerate)
        return device

    def wake(self) -> None:
        """Ask the consumer to stop, without touching PortAudio.

        Called from the signal handler, where closing a stream would mean
        re-entering PortAudio from an interrupt. The sentinel unblocks
        windows() immediately, so shutdown doesn't wait out the stall
        timeout and doesn't log a stall that never happened.
        """
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def close(self) -> None:
        self.wake()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                log.warning("error closing stream: %s", exc)
        while not self._queue.empty():           # start clean next time
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def __enter__(self) -> "Capture":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- the realtime side ----------------------------------------------
    def _on_block(self, indata, frames, time_info, status) -> None:
        if status:
            self.overflows += 1          # xrun; the audio is still usable
        block = indata[:, 0] if self._channels == 1 else indata.mean(axis=1)
        try:
            self._queue.put_nowait((np.asarray(block, dtype=np.int16).copy(),
                                    time.monotonic()))
        except queue.Full:
            self.dropped_blocks += 1

    # -- the consumer side ----------------------------------------------
    def windows(self):
        """Yield (samples, heard_at_monotonic) forever.

        Windows are assembled from consecutive blocks, so no audio is
        skipped and the overlap is exactly hop vs window, whatever the
        classifier's timing does.
        """
        buffer = np.zeros(0, dtype=np.int16)
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=self.stall_s)
            except queue.Empty:
                if self._stop.is_set():
                    return
                raise StreamStalled(
                    f"no audio for {self.stall_s:.0f}s from "
                    f"{self.device or self.device_match}")
            if item is None:            # sentinel from wake()
                return
            block, arrived = item
            self.last_block_at = arrived
            buffer = np.concatenate((buffer, block))
            while buffer.size >= self.window_samples:
                window = buffer[:self.window_samples]
                buffer = buffer[self.hop_samples:]
                yield window.astype(np.float32) / 32768.0, arrived

    # -- for /health -----------------------------------------------------
    def stats(self) -> dict:
        idle = (time.monotonic() - self.last_block_at) if self.last_block_at else None
        return {
            "device": self.device.name if self.device else None,
            "device_index": self.device.index if self.device else None,
            "channels": self._channels,
            "samplerate": self.samplerate,
            "seconds_since_audio": round(idle, 1) if idle is not None else None,
            "dropped_blocks": self.dropped_blocks,
            "xruns": self.overflows,
            "queue_depth": self._queue.qsize(),
        }
