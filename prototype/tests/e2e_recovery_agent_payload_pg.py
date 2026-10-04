#!/usr/bin/env python3
"""The Agent's exact recovery payload against the real recovery RPCs (MNVR-006, MNVR-007).

Rolled-back txn. recovery_worker runs one cycle with a cloud adaptor that does what PostgREST does
with an RPC body: it converts the JSON arguments with json_to_record, using the function's own
argument types, and calls the function. The Agent must open the interval with camera UUIDs (the
p_cameras parameter is uuid[]), and the claimed interval must be read by recorder channel.

The 5.0.27 payload (recorder channel numbers in p_cameras) is replayed first as a control: the
database rejects it with 22P02, which is why no interval was ever opened.

    python prototype/tests/e2e_recovery_agent_payload_pg.py
"""
from __future__ import annotations

import json, os, re, sys, threading, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
ENV = {}
for line in (ROOT.parent / ".env").read_text(errors="ignore").splitlines() \
        if (ROOT.parent / ".env").exists() else []:
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m: ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
for k in ("SUPABASE_DB_HOST","SUPABASE_DB_PORT","SUPABASE_DB_USER","SUPABASE_DB_PASSWORD","SUPABASE_DB_NAME"):
    if os.environ.get(k): ENV[k] = os.environ[k]
import psycopg  # noqa: E402

import backfill  # noqa: E402
import watchlog_agent as core  # noqa: E402

KEY = "recov-payload-e2e-key"
CHANNELS = [{"channel": "1", "name": "Gate"}, {"channel": "3", "name": "Yard"}]
G0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


class PgCloud:
    """POST /rpc/<fn> as PostgREST runs it: json_to_record with the function's argument types."""

    def __init__(self, conn):
        self.conn, self.calls = conn, []

    def _arg_types(self, fn):
        rows = self.conn.execute(
            """select a.name, format_type(a.typ, null)
                 from pg_proc p,
                      unnest(p.proargnames, p.proargtypes::oid[]) with ordinality a(name, typ, ord)
                where p.proname = %s and p.pronamespace = 'public'::regnamespace
                order by a.ord""", (fn,)).fetchall()
        return dict(rows)

    def call(self, fn, **params):
        self.calls.append((fn, params))
        types = self._arg_types(fn)
        cols = ", ".join(f"{k} {types[k]}" for k in params)
        args = ", ".join(f"{k} => x.{k}" for k in params)
        try:
            with self.conn.transaction():                 # savepoint: a rejected call is isolated
                return self.conn.execute(
                    f"select public.{fn}({args}) from json_to_record(%s::json) as x({cols})",
                    (json.dumps(params),)).fetchone()[0]
        except psycopg.Error as error:
            raise core.CloudError(fn, 400, error.sqlstate, str(error).splitlines()[0]) from error


class _Spool:
    def __init__(self, gap):
        self.gap, self.added = gap, []

    def pending_recovery_gap(self):
        return self.gap

    def clear_recovery_gap(self, *gap):
        self.gap = None
        return True

    def count(self):
        return 0

    def add(self, event):
        self.added.append(event)


class _Archive(backfill.ReferenceArchiveDriver):
    def __init__(self):
        super().__init__([{"ts": (G0 + timedelta(minutes=20)).isoformat(), "type": "person",
                           "device_event_id": "E1"}], page_size=10)
        self.channels = []

    def enumerate_historical_events(self, channel, start, end, cursor=None, limit=500):
        self.channels.append(str(channel))
        return super().enumerate_historical_events(channel, start, end, cursor, limit)

    def close(self):
        pass


class _OneCycle(threading.Event):
    def __init__(self):
        super().__init__()
        self.left = 2                                   # start-up settle wait + one cycle

    def wait(self, timeout=None):
        self.left -= 1
        if self.left <= 0:
            self.set()
        return self.is_set()


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT",5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME","postgres"), connect_timeout=30, autocommit=False)
    with psycopg.connect(**dsn) as conn:
        try:
            tid = conn.execute("insert into tenants (name) values ('recov-payload') returning id").fetchone()[0]
            sid = conn.execute("insert into sites (tenant_id,name,timezone) values (%s,'recov-payload','Asia/Karachi') returning id",(tid,)).fetchone()[0]
            agent = conn.execute("""insert into agents (tenant_id, site_id, agent_key_hash, enrolled_at, last_seen_at)
                                    values (%s,%s, encode(sha256(%s::bytea),'hex'), '2026-05-01', now()) returning id""",(tid,sid,KEY)).fetchone()[0]
            cloud = PgCloud(conn)
            state = {"agent_id": str(agent), "agent_key": KEY}
            gap = (G0.isoformat(), (G0 + timedelta(hours=1)).isoformat())

            # Control: the 5.0.27 payload sent recorder channel numbers into uuid[].
            try:
                cloud.call("wl_open_recovery_interval", p_agent_id=state["agent_id"], p_agent_key=KEY,
                           p_started_at=gap[0], p_ended_at=gap[1], p_cameras=["1", "3"])
                step(False, "channel numbers in p_cameras are rejected (22P02)", "accepted")
            except core.CloudError as error:
                step(error.code == "22P02", "channel numbers in p_cameras are rejected (22P02)", str(error.code))

            archive = _Archive()
            saved = core.open_archive_driver, core.log
            core.open_archive_driver, core.log = (lambda _cfg: (archive, None)), (lambda *_a: None)
            try:
                cfg = type("Cfg", (), dict(
                    recovery_enabled=True, recovery_seconds=300, recovery_ai_enabled=False,
                    last_live_path=Path(os.devnull), recovery_threshold_seconds=180,
                    recovery_chunk_seconds=3600, recovery_throttle_seconds=0.0,
                    recovery_live_backlog=500, recovery_ai_max_frames=40,
                    recovery_snapshot_seconds=300))()
                core.recovery_worker(cfg, state, cloud, _OneCycle(), _Spool(gap), CHANNELS,
                                     {"recorder_live_at": time.monotonic()})
            finally:
                core.open_archive_driver, core.log = saved

            opens = [p for fn, p in cloud.calls if fn == "wl_open_recovery_interval"][1:]
            cams = dict(conn.execute("select channel, id::text from cameras where site_id=%s",(sid,)).fetchall())
            synced = sorted(v for v in (cams.get("1"), cams.get("3")) if v)
            step(len(synced) == 2 and len(opens) == 1 and sorted(opens[0]["p_cameras"]) == synced,
                 "the Agent opens the interval with the synced camera UUIDs", str(opens and opens[0]["p_cameras"]))
            row = conn.execute("select cameras::text[], status, attempts from recovery_intervals where site_id=%s",(sid,)).fetchone()
            step(row is not None and len(synced) == 2 and sorted(row[0]) == synced,
                 "recovery_intervals.cameras holds those camera UUIDs", str(row and row[0]))
            step(set(archive.channels) == {"1", "3"},
                 "the claimed interval is read by recorder channel, never by UUID", str(sorted(set(archive.channels))))
            step(row is not None and row[1] == "recovered" and row[2] == 1,
                 "the interval completes as recovered after one claim", str(row and row[1:]))
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x); print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())
