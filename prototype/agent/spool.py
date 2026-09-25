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
create table if not exists spool_recovery_gap (
  singleton   integer primary key check(singleton=1),
  started_at  text not null,
  ended_at    text not null
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
        """Bound disk usage without turning overflow into permanent data loss.

        Before deleting oldest rows, persist the dropped observation interval in the
        same SQLite database. The recovery worker later opens that interval against
        the recorder archive once cloud + recorder connectivity are available again.
        Repeated overflow merges into one durable interval.
        """
        with self._lock:
            n = self.db.execute("select count(*) from spool").fetchone()[0]
            if n <= self.max_rows:
                return 0
            excess = n - self.max_rows
            times = self.db.execute(
                "select created_at from spool order by id limit ?", (excess,)
            ).fetchall()
            if times:
                started, ended = times[0][0], times[-1][0]
                current = self.db.execute(
                    "select started_at, ended_at from spool_recovery_gap where singleton=1"
                ).fetchone()
                if current:
                    started = min(started, current[0])
                    ended = max(ended, current[1])
                self.db.execute(
                    "insert into spool_recovery_gap(singleton,started_at,ended_at) "
                    "values(1,?,?) "
                    "on conflict(singleton) do update set "
                    "started_at=excluded.started_at, ended_at=excluded.ended_at",
                    (started, ended),
                )
            self.db.execute(
                "delete from spool where id in "
                "(select id from spool order by id limit ?)", (excess,))
            return excess

    def pending_recovery_gap(self):
        """Return the durable spool-overflow interval, if any."""
        with self._lock:
            row = self.db.execute(
                "select started_at, ended_at from spool_recovery_gap where singleton=1"
            ).fetchone()
        return tuple(row) if row else None

    def clear_recovery_gap(self, started_at: str, ended_at: str) -> bool:
        """Clear only the exact interval we successfully handed to the cloud.

        If new overflow extended the interval while recovery was being opened, the
        equality guard preserves the newer/wider marker for the next pass.
        """
        with self._lock:
            cur = self.db.execute(
                "delete from spool_recovery_gap where singleton=1 "
                "and started_at=? and ended_at=?",
                (started_at, ended_at),
            )
            return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self.db.close()
