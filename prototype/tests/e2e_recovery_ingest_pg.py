#!/usr/bin/env python3
"""Recovered intelligence actually INGESTS with recovered provenance (0.4.4 §1 deep, server half).

The unit tests prove recovery_ai builds a canonical event over recovered footage; this proves the
event the runtime actually emits is ACCEPTED by wl_ingest_events on real Postgres (rolled back) and
lands correctly:

  * the HISTORICAL footage time is stored as device_ts (NOT the recovery time in agent_ts);
  * recovered provenance survives in the stored payload (source='recovered', recovered=true);
  * a representative snapshot is attached to the recovered event;
  * re-ingesting the same recovered event is idempotent (historical dedupe) — no duplicates.

    python prototype/tests/e2e_recovery_ingest_pg.py
"""
from __future__ import annotations

import json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
import recovery_ai  # noqa: E402

ENV = {}
for line in ((ROOT.parent / ".env").read_text(errors="ignore").splitlines()
             if (ROOT.parent / ".env").exists() else []):
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(k):
        ENV[k] = os.environ[k]
import psycopg  # noqa: E402

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok)); print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


class _Det:
    def __init__(self, label):
        self.label = label

    def as_dict(self):
        return {"label": self.label, "confidence": 0.9, "box": [0, 0, 10, 10]}


class _Detector:
    model_name = "e2e-yolo"

    def classify_event(self, jpeg):
        return True, [_Det("person")]


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    KEY = "recovery-ingest-e2e-key"
    HIST_TS = "2026-06-01T22:00:00+00:00"       # footage time (an after-hours outage window)

    status, event = recovery_ai.analyze_segment(
        _Detector(), b"HISTORICAL-JPEG-BYTES", channel="1", ts=HIST_TS,
        device_event_id="seg-e2e-A", segment={"start": HIST_TS, "end": "2026-06-01T22:05:00+00:00"})
    assert status == "recovered", status

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            tid = cur.execute("insert into tenants (name) values ('rec-ing-e2e') returning id").fetchone()[0]
            sid = cur.execute("insert into sites (tenant_id,name,timezone) values (%s,'rec','Asia/Karachi') returning id", (tid,)).fetchone()[0]
            cur.execute("insert into cameras (tenant_id,site_id,channel,name) values (%s,%s,'1','Reception')", (tid, sid))
            agent = cur.execute("""insert into agents (tenant_id, site_id, agent_key_hash)
                                   values (%s,%s, encode(sha256(%s::bytea),'hex')) returning id""",
                                (tid, sid, KEY)).fetchone()[0]

            r1 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",
                             (agent, KEY, json.dumps([event]))).fetchone()[0]
            step(r1.get("inserted") == 1, "recovered event is accepted by wl_ingest_events", str(r1))

            row = cur.execute("""select event_type, device_ts, agent_ts,
                                        payload->>'source' as src, payload->>'recovered' as rec,
                                        device_event_id
                                   from events where site_id=%s""", (sid,)).fetchone()
            step(row is not None and row[0] == "recovered_activity", "stored as recovered_activity",
                 str(row[0] if row else None))
            # HISTORICAL footage time preserved as device_ts (June), distinct from recovery time (agent_ts)
            step(row[1].year == 2026 and row[1].month == 6 and row[1].hour == 22,
                 "historical footage time preserved as device_ts", str(row[1]))
            step(row[2] > row[1], "agent_ts (recovery time) is later than device_ts (footage time)",
                 f"{row[2]} > {row[1]}")
            step(row[3] == "recovered" and row[4] == "true", "recovered provenance survives in payload",
                 f"source={row[3]} recovered={row[4]}")

            snaps = cur.execute("""select count(*) from snapshots s join events e on e.id=s.event_id
                                    where e.site_id=%s""", (sid,)).fetchone()[0]
            step(snaps == 1, "a representative recovered snapshot is attached", str(snaps))

            # idempotent replay: the SAME recovered event ingests nothing new
            r2 = cur.execute("select wl_ingest_events(%s,%s,%s::jsonb)",
                             (agent, KEY, json.dumps([event]))).fetchone()[0]
            step(r2.get("inserted") == 0, "re-ingesting the same recovered event is idempotent", str(r2))

            total = cur.execute("select count(*) from events where site_id=%s", (sid,)).fetchone()[0]
            step(total == 1, "exactly one recovered event stored (no duplicates)", str(total))
        finally:
            conn.rollback()
    ok = sum(1 for x in STEPS if x)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())
