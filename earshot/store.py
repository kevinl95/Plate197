"""SQLite, gently.

This runs on an SD card, so the rules are: WAL, synchronous=NORMAL, and
writes arrive in batches inside one transaction rather than a commit per
bird. A morning of house finches is a few hundred rows; there is no
reason for any of it to be urgent.

The writer is one thread with one connection. Readers (the web app) open
their own — WAL lets them read while a batch is committing.

Timestamps are ISO8601 *local* time with no offset: "2026-09-18T07:12:04".
That is what the page's history queries compare against
(date('now','localtime')), what JavaScript parses as local time, and what
sorts correctly as text.
"""

from __future__ import annotations

import datetime as dt
import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path

from . import clock
from .classify import Detection

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS detections (
  id      INTEGER PRIMARY KEY,
  species TEXT NOT NULL,
  sci     TEXT NOT NULL,
  ts      TEXT NOT NULL,
  conf    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_species_ts ON detections(species, ts);
"""

# Today, and the start of this year, in the same shape as the stored
# timestamps. Both "first" flags stop at the beginning of today on
# purpose: include today and every bird is a first every morning.
TODAY = "date('now','localtime')"
YEAR_START = "date('now','localtime','start of year')"


def connect(path: Path, timeout: float = 5.0) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # no fsync per row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


class Store:
    def __init__(self, path: Path, batch: int = 8, interval_s: float = 15.0,
                 min_year: int = 2025, max_pending: int = 2000):
        self.path = Path(path)
        self.batch = batch
        self.interval_s = interval_s
        self.min_year = min_year
        self.max_pending = max_pending

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with connect(self.path) as conn:
            conn.executescript(SCHEMA)

        self._inbox: queue.Queue = queue.Queue(maxsize=10_000)
        self._local = threading.local()
        self._stop = threading.Event()
        self._writer: threading.Thread | None = None
        self.rows_written = 0
        self.rows_dropped = 0
        self.last_write_error: str | None = None
        self.waiting_for_clock = 0

    # -- reading ---------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        """One connection per reading thread; WAL makes that cheap."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = connect(self.path)
        return conn

    def recent(self, hours: int = 6) -> list[dict]:
        """The page's contract: one entry per species heard lately.

        The page reduces a detection list to the latest per species
        anyway, so sending one row each keeps the payload to a handful of
        objects and lets the history flags ride along without repeating.
        """
        cutoff = clock.iso(dt.datetime.now() - dt.timedelta(hours=hours))
        cur = self.conn.execute(
            # A bare column beside MAX() takes its value from that same
            # row — a documented SQLite guarantee, and much cheaper here
            # than a window function on a Pi 3.
            "SELECT species, sci, MAX(ts) AS ts, conf FROM detections "
            "WHERE ts >= ? GROUP BY species ORDER BY ts DESC", (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return []

        species = [r["species"] for r in rows]
        marks = ",".join("?" * len(species))

        before_today = {
            r["species"]: r["n"] for r in self.conn.execute(
                f"SELECT species, COUNT(*) AS n FROM detections "
                f"WHERE species IN ({marks}) AND ts < {TODAY} "
                f"GROUP BY species", species)
        }
        this_year = {
            r["species"]: r["n"] for r in self.conn.execute(
                f"SELECT species, COUNT(*) AS n FROM detections "
                f"WHERE species IN ({marks}) AND ts >= {YEAR_START} "
                f"AND ts < {TODAY} GROUP BY species", species)
        }
        today = {
            r["species"]: (r["n"], r["first"]) for r in self.conn.execute(
                f"SELECT species, COUNT(*) AS n, MIN(ts) AS first FROM detections "
                f"WHERE species IN ({marks}) AND ts >= {TODAY} "
                f"GROUP BY species", species)
        }

        out = []
        for row in rows:
            name = row["species"]
            count_today, first_today = today.get(name, (0, None))
            out.append({
                "species": name,
                "sci": row["sci"],
                "time": row["ts"],
                "conf": round(row["conf"], 3),
                "first_ever": before_today.get(name, 0) == 0,
                "first_this_year": this_year.get(name, 0) == 0,
                "count_today": count_today,
                "first_today": first_today,
            })
        return out

    def last_detection(self) -> dict | None:
        row = self.conn.execute(
            "SELECT species, ts, conf FROM detections "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def counts(self) -> dict:
        row = self.conn.execute(
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN ts >= {TODAY} THEN 1 ELSE 0 END) AS today "
            f"FROM detections").fetchone()
        return {"total": row["total"], "today": row["today"] or 0}

    # -- writing ---------------------------------------------------------
    def record(self, detection: Detection, heard_at: float) -> None:
        """Queue one detection. `heard_at` is a time.monotonic() reading.

        Monotonic, not wall clock, because at boot the clock may still be
        wrong; the conversion happens at flush time, once NTP has landed.
        """
        try:
            self._inbox.put_nowait((detection, heard_at))
        except queue.Full:
            self.rows_dropped += 1

    def start(self) -> None:
        self._writer = threading.Thread(target=self._run, name="store-writer",
                                        daemon=True)
        self._writer.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._writer:
            self._writer.join(timeout)

    def _run(self) -> None:
        conn = connect(self.path)
        pending: list[tuple[Detection, float]] = []
        last_flush = time.monotonic()
        while not self._stop.is_set():
            try:
                pending.append(self._inbox.get(timeout=1.0))
            except queue.Empty:
                pass
            due = (len(pending) >= self.batch
                   or (pending and time.monotonic() - last_flush >= self.interval_s))
            if not due:
                continue
            if not clock.is_sane(self.min_year):
                # Hold them. They keep their monotonic readings, so when
                # the clock arrives the timestamps are still right.
                self.waiting_for_clock = len(pending)
                if len(pending) > self.max_pending:
                    dropped = len(pending) - self.max_pending
                    pending = pending[-self.max_pending:]
                    self.rows_dropped += dropped
                    log.warning("clock still unset; dropped %d held detections",
                                dropped)
                last_flush = time.monotonic()
                continue
            self.waiting_for_clock = 0
            if self._flush(conn, pending):
                pending = []
            last_flush = time.monotonic()
        # Last chance on the way out.
        if pending and clock.is_sane(self.min_year):
            self._flush(conn, pending)
        conn.close()

    def _flush(self, conn: sqlite3.Connection, pending: list) -> bool:
        rows = [(d.species, d.sci, clock.iso(clock.wall_time_for(at)), d.conf)
                for d, at in pending]
        try:
            with conn:
                conn.executemany(
                    "INSERT INTO detections (species, sci, ts, conf) "
                    "VALUES (?,?,?,?)", rows)
        except sqlite3.Error as exc:
            # A full card, or a card that has gone read-only. Neither is
            # worth dying over: the frame should keep showing the birds it
            # already knows about.
            self.last_write_error = f"{exc.__class__.__name__}: {exc}"
            self.rows_dropped += len(rows)
            log.warning("could not write %d detections: %s", len(rows), exc)
            return True        # drop them rather than retry forever
        self.rows_written += len(rows)
        self.last_write_error = None
        return True

    def stats(self) -> dict:
        size = self.path.stat().st_size if self.path.exists() else 0
        wal = self.path.with_suffix(self.path.suffix + "-wal")
        return {
            "path": str(self.path),
            "bytes": size + (wal.stat().st_size if wal.exists() else 0),
            "rows_written": self.rows_written,
            "rows_dropped": self.rows_dropped,
            "queued": self._inbox.qsize(),
            "held_for_clock": self.waiting_for_clock,
            "last_write_error": self.last_write_error,
        }
