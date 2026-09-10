"""
Local event spool.

The driver writes here; the uploader drains it. That separation is what
makes a site survive its internet link: events keep accumulating on the
site PC's disk and go up when the link returns, instead of being lost
between two polls.

SQLite, one file, WAL mode, so an abrupt power cut on a site PC — which
is the normal case in Pakistan, not the exceptional one — cannot corrupt
the queue or lose an acknowledged write.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

# Bound the queue so a month-long outage cannot fill the disk. Oldest go
# first, and dropping is logged loudly rather than done silently.
MAX_ROWS = 200_000

SCHEMA = """
create table if not exists spool (
  id          integer primary key autoincrement,
  payload     text not null,
  created_at  text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


class Spool:
    def __init__(self, path: Path, max_rows: int = MAX_ROWS) -> None:
        self.path = path
        # Buffer cap is per-instance so an Edge box with more storage can buffer a longer
        # outage than a shared desktop (Edge sizing, not a hardcoded desktop assumption).
        self.max_rows = int(max_rows) if max_rows else MAX_ROWS
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False,
                                  isolation_level=None)
        self.db.execute("pragma journal_mode=WAL")
        self.db.execute("pragma synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    def add(self, payload: dict) -> None:
        with self._lock:
            self.db.execute("insert into spool (payload) values (?)",
                            (json.dumps(payload),))

    def count(self) -> int:
        with self._lock:
            return self.db.execute("select count(*) from spool").fetchone()[0]

    def take(self, limit: int) -> tuple[list[int], list[dict]]:
        """Peek at the oldest `limit` rows. Nothing is removed until ack()."""
        with self._lock:
            rows = self.db.execute(
                "select id, payload from spool order by id limit ?",
                (limit,)).fetchall()
        return [r[0] for r in rows], [json.loads(r[1]) for r in rows]

    def ack(self, ids: list[int]) -> None:
        """Delete rows the server has confirmed. At-least-once, never at-most-once."""
        if not ids:
            return
        with self._lock:
            self.db.execute(
                f"delete from spool where id in ({','.join('?' * len(ids))})",
                ids)

    def trim(self) -> int:
        """Drop the oldest rows past the configured cap. Returns how many were dropped."""
        with self._lock:
            n = self.db.execute("select count(*) from spool").fetchone()[0]
            if n <= self.max_rows:
                return 0
            excess = n - self.max_rows
            self.db.execute(
                "delete from spool where id in "
                "(select id from spool order by id limit ?)", (excess,))
            return excess

    def close(self) -> None:
        with self._lock:
            self.db.close()
