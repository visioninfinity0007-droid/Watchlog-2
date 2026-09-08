"""Phase A — increment 5: durable LOCAL health store.

A dedicated SQLite/WAL database, SEPARATE from the event spool (spool.py), that lets health
monitoring survive an internet/cloud outage and a restart without losing evidence or crashing
the agent:

  * transitions  — one row per OBSERVED health change (gated on change), carrying provenance
    (native | probe | inventory | upper_layer), the device-observed time, and a stable dedupe
    id ("<agent>:<seq>") so a reconnect resend is idempotent.
  * checkpoints  — periodic proof that local monitoring continued (agent alive, recorder state
    known, a camera-health cycle completed) — without storing raw probe spam or image bytes.

Every method is fail-safe: a locked/full/corrupt database degrades to a no-op (logged), never
an exception into the agent's loops. Retention is bounded (compaction), and an UNSENT
transition is never dropped merely for being old.

Deliberately stores NO image bytes and NO secrets — only state/reason/source/timestamp text.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

# Bound unsent history so a long outage cannot fill the disk. Checkpoints are continuity
# evidence and MAY be coarsened; an unresolved transition is never dropped for AGE alone.
DEFAULT_MAX_CHECKPOINTS = 20_000
DEFAULT_MAX_TRANSITION_AGE_DAYS = 90
# Hard disk-pressure ceiling for UNSENT transitions. Below this, pending transitions are never
# dropped. If a pathological long/flapping outage pushes past it, the OLDEST unsent are dropped
# but LOUDLY and with a persisted, observable counter — never silently. See compact().
DEFAULT_MAX_PENDING_TRANSITIONS = 100_000

_SCHEMA = """
create table if not exists meta (k text primary key, v text);

create table if not exists last_state (
  key   text primary key,          -- "<layer>:<entity>"
  state text not null
);

create table if not exists transitions (
  seq        integer primary key autoincrement,   -- monotonic, stable across restarts
  dedupe_key text unique,                          -- "<agent>:<seq>"
  layer      text not null,
  entity     text not null,
  from_state text,
  to_state   text not null,
  reason     text not null,
  source     text not null,                        -- native|probe|inventory|upper_layer
  device_ts  text not null,                        -- observed time (ISO8601), NOT upload time
  status     text not null default 'pending',      -- pending|uploaded
  created_at integer not null default (strftime('%s','now'))
);
create index if not exists transitions_status_idx on transitions(status, seq);

create table if not exists checkpoints (
  seq             integer primary key autoincrement,
  device_ts       text not null,
  nvr_state       text not null,
  cameras_observed integer not null,
  cycle_ok        integer not null default 1,
  status          text not null default 'pending',
  created_at      integer not null default (strftime('%s','now'))
);
create index if not exists checkpoints_status_idx on checkpoints(status, seq);
"""


def _log(msg: str) -> None:
    try:
        print(f"[health-store] {msg}", flush=True)
    except Exception:       # noqa: BLE001 — logging must never be the thing that crashes us
        pass


class HealthStore:
    def __init__(self, path, agent_id: str,
                 max_checkpoints: int = DEFAULT_MAX_CHECKPOINTS,
                 max_transition_age_days: int = DEFAULT_MAX_TRANSITION_AGE_DAYS,
                 max_pending_transitions: int = DEFAULT_MAX_PENDING_TRANSITIONS) -> None:
        self.path = Path(path)
        self.agent_id = agent_id
        self.max_checkpoints = int(max_checkpoints)
        self.max_transition_age_days = int(max_transition_age_days)
        self.max_pending_transitions = int(max_pending_transitions)
        self._lock = threading.RLock()
        self.db = self._open()
        # A durable per-DATABASE-LIFETIME epoch. Fresh/rebuilt DB -> new epoch; intact restart
        # -> same epoch. This is what makes dedupe ids globally unique: after a corruption
        # rebuild the SQLite AUTOINCREMENT restarts at 1, so "<agent>:<seq>" alone would collide
        # with already-reconciled server rows and the server's ON CONFLICT DO NOTHING would
        # silently discard genuinely new evidence. "<agent>:<epoch>:<seq>" cannot collide.
        self.epoch = self._ensure_epoch()

    # -- open / resilience ---------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute("select count(*) from sqlite_master")     # provoke corruption now
            db.executescript(_SCHEMA)
        except sqlite3.DatabaseError as e:
            # Corrupt/unreadable file: preserve it for forensics and rebuild, rather than
            # crash the agent. A lost local buffer degrades coverage; it does not stop
            # monitoring.
            _log(f"store unreadable ({e}); rebuilding (old file kept as .corrupt)")
            try:
                db.close()
            except Exception:       # noqa: BLE001
                pass
            try:
                self.path.replace(self.path.with_suffix(self.path.suffix + ".corrupt"))
            except Exception as re:  # noqa: BLE001
                _log(f"could not set aside corrupt file: {re}")
                try:
                    self.path.unlink()
                except Exception:    # noqa: BLE001
                    pass
            db = self._connect()
            db.executescript(_SCHEMA)
        return db

    def _ensure_epoch(self) -> str:
        """Read the store epoch from meta, or mint a new one. A rebuilt DB has no meta -> new
        epoch; an intact DB keeps its epoch across restarts."""
        with self._lock:
            try:
                row = self.db.execute("select v from meta where k='store_epoch'").fetchone()
                if row and row["v"]:
                    return row["v"]
                ep = uuid.uuid4().hex
                self.db.execute("insert into meta(k,v) values('store_epoch',?) "
                                "on conflict(k) do update set v=excluded.v", (ep,))
                return ep
            except sqlite3.Error as e:
                # Never fail construction; fall back to a volatile epoch (still unique per boot).
                _log(f"epoch init failed ({e}); using volatile epoch")
                return "vol-" + uuid.uuid4().hex

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None,
                             timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            db.execute("pragma journal_mode=WAL")
            db.execute("pragma synchronous=NORMAL")
            db.execute("pragma busy_timeout=3000")
        except sqlite3.DatabaseError:
            pass
        return db

    # -- writes (all fail-safe) ----------------------------------------------------

    def observe(self, layer: str, entity: str, new_state: str, reason: str,
                source: str, device_ts: str) -> Optional[dict]:
        """Record a transition IFF the state changed from the last observed for this entity.
        Returns the new row, or None (no change, or a persistence failure)."""
        key = f"{layer}:{entity}"
        with self._lock:
            try:
                cur = self.db.execute("select state from last_state where key=?", (key,)).fetchone()
                prev = cur["state"] if cur else None
                if prev == new_state:
                    return None                       # steady state -> nothing to record
                self.db.execute("begin")
                self.db.execute(
                    "insert into transitions (dedupe_key, layer, entity, from_state, to_state,"
                    " reason, source, device_ts) values (?,?,?,?,?,?,?,?)",
                    (None, layer, entity, prev, new_state, reason, source, str(device_ts)))
                seq = self.db.execute("select last_insert_rowid()").fetchone()[0]
                dedupe_key = f"{self.agent_id}:{self.epoch}:{seq}"   # epoch-qualified: rebuild-proof
                self.db.execute("update transitions set dedupe_key=? where seq=?", (dedupe_key, seq))
                self.db.execute(
                    "insert into last_state(key,state) values(?,?) "
                    "on conflict(key) do update set state=excluded.state", (key, new_state))
                self.db.execute("commit")
                return {"seq": seq, "dedupe_key": dedupe_key, "layer": layer, "entity": entity,
                        "from_state": prev, "to_state": new_state, "reason": reason,
                        "source": source, "device_ts": str(device_ts)}
            except sqlite3.Error as e:
                _log(f"observe failed ({e}); dropping in-memory, will re-observe next cycle")
                self._safe_rollback()
                return None

    def checkpoint(self, device_ts: str, nvr_state: str, cameras_observed: int,
                   cycle_ok: bool = True) -> Optional[dict]:
        with self._lock:
            try:
                self.db.execute(
                    "insert into checkpoints (device_ts, nvr_state, cameras_observed, cycle_ok)"
                    " values (?,?,?,?)",
                    (str(device_ts), str(nvr_state), int(cameras_observed), 1 if cycle_ok else 0))
                seq = self.db.execute("select last_insert_rowid()").fetchone()[0]
                return {"seq": seq, "device_ts": str(device_ts), "nvr_state": str(nvr_state),
                        "cameras_observed": int(cameras_observed), "cycle_ok": bool(cycle_ok)}
            except sqlite3.Error as e:
                _log(f"checkpoint failed ({e})")
                return None

    def mark_transitions_uploaded(self, dedupe_keys: List[str]) -> None:
        self._mark("transitions", "dedupe_key", dedupe_keys)

    def mark_checkpoints_uploaded(self, seqs: List[int]) -> None:
        self._mark("checkpoints", "seq", seqs)

    def _mark(self, table: str, col: str, ids: list) -> None:
        if not ids:
            return
        with self._lock:
            try:
                q = f"update {table} set status='uploaded' where {col} in " \
                    f"({','.join('?' * len(ids))})"
                self.db.execute(q, list(ids))
            except sqlite3.Error as e:
                _log(f"mark uploaded failed ({e})")

    # -- reads ---------------------------------------------------------------------

    def pending_transitions(self, limit: int) -> List[dict]:
        return self._rows("select * from transitions where status='pending' order by seq limit ?",
                          (limit,))

    def pending_checkpoints(self, limit: int) -> List[dict]:
        return self._rows("select * from checkpoints where status='pending' order by seq limit ?",
                          (limit,))

    def export_batch(self, limit: int) -> dict:
        """The reconciliation payload: pending transitions + checkpoints, defined fields only
        (no secrets, no bytes)."""
        txs = [{"id": r["dedupe_key"], "seq": r["seq"], "store_epoch": self.epoch,
                "layer": r["layer"], "entity": r["entity"],
                "from": r["from_state"], "to": r["to_state"], "reason": r["reason"],
                "source": r["source"], "device_ts": r["device_ts"]}
               for r in self.pending_transitions(limit)]
        cps = [{"id": f"{self.agent_id}:{self.epoch}:cp:{r['seq']}", "store_epoch": self.epoch,
                "seq": r["seq"], "device_ts": r["device_ts"], "nvr_state": r["nvr_state"],
                "cameras_observed": r["cameras_observed"], "cycle_ok": bool(r["cycle_ok"])}
               for r in self.pending_checkpoints(limit)]
        return {"transitions": txs, "checkpoints": cps}

    def total_checkpoints(self) -> int:
        r = self._rows("select count(*) as n from checkpoints", ())
        return r[0]["n"] if r else 0

    def _rows(self, q: str, params) -> List[dict]:
        with self._lock:
            try:
                return [dict(r) for r in self.db.execute(q, params).fetchall()]
            except sqlite3.Error as e:
                _log(f"read failed ({e})")
                return []

    def _all_columns(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for t in ("transitions", "checkpoints", "last_state", "meta"):
            out[t] = [r["name"] for r in self.db.execute(f"pragma table_info({t})").fetchall()]
        return out

    # -- retention -----------------------------------------------------------------

    def compact(self) -> None:
        """Bound the store. Storage classification (honest):
          * checkpoints           — HARD-bounded by count (uploaded dropped first).
          * uploaded transitions  — age-pruned past max_transition_age_days.
          * unsent transitions    — retained; NOT dropped for age. Bounded only by a hard
            disk-pressure ceiling (max_pending_transitions); crossing it drops the OLDEST
            unsent, but LOUDLY and with a persisted, observable counter — never silently.
        """
        with self._lock:
            try:
                max_age = self.max_transition_age_days * 86400
                self.db.execute(
                    "delete from transitions where status='uploaded' "
                    "and (strftime('%s','now') - created_at) >= ?", (max_age,))

                n = self.db.execute("select count(*) from checkpoints").fetchone()[0]
                if n > self.max_checkpoints:
                    excess = n - self.max_checkpoints
                    self.db.execute(
                        "delete from checkpoints where seq in ("
                        "  select seq from checkpoints order by (status='pending') asc, seq asc"
                        "  limit ?)", (excess,))

                # Disk-pressure last resort for UNSENT transitions — explicit + observable.
                npend = self.db.execute(
                    "select count(*) from transitions where status='pending'").fetchone()[0]
                if npend > self.max_pending_transitions:
                    excess = npend - self.max_pending_transitions
                    _log(f"WARNING disk-pressure: {npend} unsent transitions exceed ceiling "
                         f"{self.max_pending_transitions}; dropping {excess} OLDEST unsent "
                         f"(explicit + counted, NOT silent)")
                    self._bump_meta("pending_overflow_dropped", excess)
                    self.db.execute(
                        "delete from transitions where seq in ("
                        "  select seq from transitions where status='pending' order by seq limit ?)",
                        (excess,))
            except sqlite3.Error as e:
                _log(f"compact failed ({e})")

    def _bump_meta(self, key: str, amount: int) -> None:
        try:
            self.db.execute(
                "insert into meta(k,v) values(?, ?) on conflict(k) do update "
                "set v = cast((cast(meta.v as integer) + ?) as text)",
                (key, str(int(amount)), int(amount)))
        except sqlite3.Error:
            pass

    def overflow_count(self) -> int:
        """How many unsent transitions the disk-pressure policy has dropped (observable)."""
        r = self._rows("select v from meta where k='pending_overflow_dropped'", ())
        return int(r[0]["v"]) if r else 0

    def _safe_rollback(self) -> None:
        try:
            self.db.execute("rollback")
        except sqlite3.Error:
            pass

    def close(self) -> None:
        with self._lock:
            try:
                self.db.close()
            except sqlite3.Error:
                pass


__all__ = ["HealthStore"]
