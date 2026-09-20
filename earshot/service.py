"""The whole thing, supervised.

Capture, classify and store run in this thread; the web app runs in
another; systemd watches both through the watchdog. The shape of the
loop is the important part: anything the microphone does — vanishing,
being replaced by a different USB device, going silent while still
pretending to be open — comes back here as an exception, gets a warning
line, and is retried with a growing delay. The web app keeps serving the
birds already in the database the entire time, so the panel shows
yesterday evening's finches rather than an error.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
import urllib.request

import sounddevice as sd

from . import api, audio, classify, models, notify
from .config import Config, load
from .store import Store

log = logging.getLogger(__name__)


class Service:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.stopping = threading.Event()
        self.store = Store(cfg.db_path, batch=cfg.write_batch,
                           interval_s=cfg.write_interval_s,
                           min_year=cfg.min_plausible_year)
        self.classifier: classify.Classifier | None = None
        self.capture: audio.Capture | None = None
        self.listening = False
        self.last_error: str | None = None
        self.web_error: str | None = None
        self.windows_seen = 0
        self.detections_seen = 0
        self.started = time.monotonic()
        self._watchdog_every = notify.watchdog_interval_s()
        self._last_ping = 0.0

    # -- status for /health ---------------------------------------------
    def status(self) -> dict:
        return {
            "listening": self.listening,
            "last_error": self.last_error,
            "web_error": self.web_error,
            "windows": self.windows_seen,
            "detections": self.detections_seen,
            "stream": self.capture.stats() if self.capture else {},
            "classifier": {
                "runtime": self.classifier.runtime_name if self.classifier else None,
                "week": self.classifier.range.week if self.classifier else None,
                "species_in_range": (int(self.classifier.range.mask.sum())
                                     if self.classifier else None),
                "min_conf": self.cfg.min_conf,
                "models": models.describe(self.cfg.model_dir),
            },
        }

    # -- systemd ---------------------------------------------------------
    def ping(self, force: bool = False) -> None:
        """Tell systemd we are still going round the loop."""
        if self._watchdog_every is None:
            return
        now = time.monotonic()
        if force or now - self._last_ping >= self._watchdog_every:
            notify.watchdog()
            self._last_ping = now

    def rest(self, seconds: float) -> None:
        """Sleep, but keep the watchdog fed and stay interruptible."""
        deadline = time.monotonic() + seconds
        while not self.stopping.is_set() and time.monotonic() < deadline:
            self.ping()
            self.stopping.wait(min(1.0, deadline - time.monotonic()))

    # -- the loop --------------------------------------------------------
    def listen_once(self) -> None:
        """One life of the stream, from open until something goes wrong."""
        cfg = self.cfg
        assert self.classifier is not None
        self.capture = audio.Capture(
            device_match=cfg.device_match,
            samplerate=cfg.sample_rate,
            window_samples=cfg.window_samples,
            hop_samples=cfg.hop_samples,
            block_samples=cfg.block_samples,
            stall_s=cfg.stall_s,
        )
        with self.capture as cap:
            self.listening = True
            self.last_error = None
            notify.status(f"listening on {cap.device}")
            next_week_check = 0.0
            for samples, heard_at in cap.windows():
                if self.stopping.is_set():
                    return
                self.windows_seen += 1
                for detection in self.classifier.classify(samples):
                    self.detections_seen += 1
                    self.store.record(detection, heard_at)
                    log.debug("%s %.2f", detection.species, detection.conf)
                # The species list only moves four times a month; checking
                # the calendar hourly is generous.
                if time.monotonic() >= next_week_check:
                    self.classifier.range.refresh()
                    next_week_check = time.monotonic() + 3600
                self.ping()

    def _serve(self, app) -> None:
        """Run the web server, and make its death impossible to miss.

        This used to be api.serve on a bare daemon thread. When it threw
        -- a port still held by the previous process after a restart is
        the easy way -- the traceback went to the journal and *nothing
        else happened*: the capture loop kept running, kept pinging the
        watchdog, and systemd went on believing the unit was healthy
        while the panel showed "site can't be reached". A frame with no
        page is no use, so this takes the whole service down and lets
        systemd restart it properly.
        """
        try:
            api.serve(app, self.cfg.host, self.cfg.port)
            self.web_error = "web server returned unexpectedly"
        except Exception as exc:
            self.web_error = f"{exc.__class__.__name__}: {exc}"
            log.warning("web server stopped: %s", exc)
        self.stopping.set()
        if self.capture is not None:
            self.capture.wake()

    def _wait_until_listening(self, timeout: float = 20.0) -> bool:
        """True once *our* app answers on the configured port.

        Deliberately an HTTP request and not a bare TCP connect: if some
        other process is still holding the port — the previous instance
        on a quick restart, most likely — a connect succeeds against it
        and tells us nothing. Asking /health and requiring our own 200
        back is the difference between "the port is occupied" and "the
        page can be fetched".
        """
        url = f"http://{self.cfg.host}:{self.cfg.port}/health"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.web_error or self.stopping.is_set():
                return False
            try:
                with urllib.request.urlopen(url, timeout=2.0) as response:
                    if response.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(0.25)
        self.web_error = self.web_error or "timed out waiting for the page"
        return False

    def ensure_classifier(self) -> None:
        """Load the models. Raises if they aren't there and can't be had."""
        if self.classifier is not None:
            return
        cfg = self.cfg
        if not models.have(cfg.model_dir, cfg.model_precision):
            log.warning("BirdNET models missing from %s — fetching", cfg.model_dir)
            models.fetch(cfg.model_dir, cfg.model_precision)
        self.classifier = classify.Classifier(
            model_dir=cfg.model_dir,
            cache_path=cfg.species_cache_path,
            lat=cfg.latitude, lon=cfg.longitude,
            min_conf=cfg.min_conf,
            location_threshold=cfg.location_filter_threshold,
            threads=cfg.tflite_threads,
            keep_non_event=cfg.keep_non_event,
        )

    def run(self) -> int:
        cfg = self.cfg

        # The page goes up before anything that can fail.
        #
        # It used to be the other way round: models, then classifier,
        # then the web server. A missing model meant the service exited
        # before ever listening, so the panel showed Chromium's "site
        # can't be reached" — a browser error page on a picture frame,
        # which is the one thing this is not allowed to do. Serving
        # first means the worst case is an empty frame that says "Quiet
        # at the feeder", with /health explaining why.
        self.store.start()
        app = api.create_app(cfg, self.store, self.status)
        web = threading.Thread(target=self._serve, args=(app,),
                               name="web", daemon=True)
        web.start()

        # Don't claim to be ready until the socket actually accepts.
        # systemd's Type=notify is what the kiosk unit's After= hangs
        # off, so READY=1 has to mean "the page can be fetched", not
        # "a thread was started that intends to serve it".
        if not self._wait_until_listening():
            log.warning("web server never came up: %s",
                        self.web_error or "timed out")
            notify.status("web server failed")
            self.store.stop()
            return 1
        log.warning("serving %s", cfg.base_url)

        notify.ready(f"serving {cfg.base_url}")
        self.ping(force=True)

        backoff = cfg.restart_min_s
        while not self.stopping.is_set():
            try:
                # Models live in here too, so a bad or missing model is
                # retried on the same backoff as a missing microphone
                # rather than killing the process and taking the page
                # down with it.
                self.ensure_classifier()
                self.listen_once()
                backoff = cfg.restart_min_s
            except audio.DeviceNotFound as exc:
                self.last_error = str(exc)
                log.warning("microphone not found: %s", exc)
            except audio.StreamStalled as exc:
                self.last_error = str(exc)
                log.warning("capture stalled: %s", exc)
            except sd.PortAudioError as exc:
                self.last_error = f"PortAudio: {exc}"
                log.warning("audio error: %s", exc)
            except Exception as exc:                 # keep the frame alive
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                log.warning("unexpected failure in capture: %s", exc, exc_info=True)
            finally:
                self.listening = False
                if self.capture is not None:
                    self.capture.close()
            if self.stopping.is_set():
                break
            notify.status(f"retrying in {backoff:.0f}s: {self.last_error}")
            self.rest(backoff)
            backoff = min(cfg.restart_max_s, backoff * 2)

        notify.status("stopping")
        self.store.stop()
        # Nonzero so systemd restarts us rather than treating a dead web
        # server as a clean shutdown.
        return 1 if self.web_error else 0

    def stop(self, *_) -> None:
        # Only flags and a sentinel in here: the stream itself is closed
        # by the loop that owns it, on its way out.
        self.stopping.set()
        if self.capture is not None:
            self.capture.wake()


def configure_logging(level: str) -> None:
    # journald already stamps the time and the unit; a bare message is
    # what shows up cleanly in `journalctl -u earshot`.
    logging.basicConfig(level=getattr(logging, level.upper(), logging.WARNING),
                        format="%(name)s: %(message)s")
    for noisy in ("waitress", "werkzeug", "waitress.queue"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


def main(cfg: Config | None = None) -> int:
    cfg = cfg or load()
    configure_logging(cfg.log_level)
    service = Service(cfg)
    signal.signal(signal.SIGTERM, service.stop)
    signal.signal(signal.SIGINT, service.stop)
    return service.run()
