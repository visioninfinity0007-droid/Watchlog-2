#!/usr/bin/env python3
"""Journeys (0070) — multi-camera movement paths.

Self-contained, rolled back: applies 0065 + 0070, builds a morning chain across three
cameras plus an isolated afternoon presence, derives the pipeline + journeys, and pins:
  * the three-camera chain becomes ONE journey with 3 hops, path in order,
  * the isolated single-camera presence is NOT a journey,
  * provenance (episode_ids) is preserved,
  * re-derivation is idempotent.

    python prototype/tests/e2e_journeys_pg.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = {}
for line in (ROOT.parent / ".env").read_text(errors="ignore").splitlines() \
        if (ROOT.parent / ".env").exists() else []:
    m = re.match(r"^([A-Za-z0-9_]+)=(.*)$", line)
    if m:
        ENV.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
import os
for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_PORT", "SUPABASE_DB_USER",
          "SUPABASE_DB_PASSWORD", "SUPABASE_DB_NAME"):
    if os.environ.get(k):
        ENV[k] = os.environ[k]

import psycopg  # noqa: E402

MIG_PIPE = (ROOT / "supabase" / "migrations" / "0065_intelligence_pipeline.sql").read_text(encoding="utf-8")
MIG_JOUR = (ROOT / "supabase" / "migrations" / "0070_journeys.sql").read_text(encoding="utf-8")

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)
    D = "2026-06-01"
    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(MIG_PIPE)
            cur.execute(MIG_JOUR)
            cur.execute("insert into tenants (name) values ('jour-e2e') returning id")
            tid = cur.fetchone()[0]
            cur.execute("insert into sites (tenant_id, name, timezone) values (%s,'jour-e2e','Asia/Karachi') returning id", (tid,))
            sid = cur.fetchone()[0]
            cams = {}
            for ch, name in [("1", "Reception"), ("2", "Admin"), ("4", "Director"), ("5", "Store")]:
                cur.execute("insert into cameras (tenant_id, site_id, channel, name, purpose) values (%s,%s,%s,%s,'area') returning id",
                            (tid, sid, ch, name))
                cams[name] = cur.fetchone()[0]

            def ev(cam, hhmmss):
                cur.execute("""insert into events (tenant_id, site_id, camera_id, event_type, device_ts,
                               agent_ts, received_at, dedupe_key)
                               values (%s,%s,%s,'person',%s::timestamptz,%s::timestamptz,now(),%s)""",
                            (tid, sid, cams[cam], f"{D} {hhmmss}+05:00", f"{D} {hhmmss}+05:00", f"j-{cam}-{hhmmss}"))

            # Morning movement: Reception -> Admin -> Director within a few minutes.
            for s in ("10:00:00", "10:00:02"):
                ev("Reception", s)
            for s in ("10:03:00", "10:03:01"):
                ev("Admin", s)
            for s in ("10:06:00", "10:06:01"):
                ev("Director", s)
            # Isolated afternoon presence on one camera (NOT a journey).
            for s in ("14:00:00", "14:00:03"):
                ev("Store", s)

            lo, hi = f"{D} 00:00+05", f"{D} 23:59+05"
            cur.execute("select wl_derive_activities(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            cur.execute("select wl_derive_episodes(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            cur.execute("select wl_derive_journeys(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            n = cur.fetchone()[0]
            step(n == 1, "one multi-camera journey derived (isolated presence excluded)", str(n))

            cur.execute("select hop_count, camera_path, cardinality(episode_ids) from journeys where site_id=%s", (sid,))
            hop, path, epc = cur.fetchone()
            step(hop == 3, "journey has 3 hops", str(hop))
            step(path == ["Reception", "Admin", "Director"], "path is in movement order", str(path))
            step(epc == 3, "provenance: 3 source episodes", str(epc))

            cur.execute("select wl_site_journeys(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            js = cur.fetchone()[0]
            step(len(js) == 1 and js[0]["hops"] == 3 and js[0]["path"] == ["Reception", "Admin", "Director"],
                 "wl_site_journeys returns the journey")

            cur.execute("select wl_derive_journeys(%s,%s::timestamptz,%s::timestamptz)", (sid, lo, hi))
            cur.execute("select count(*) from journeys where site_id=%s", (sid,))
            step(cur.fetchone()[0] == 1, "idempotent re-derive (still one journey)")
        finally:
            conn.rollback()
    ok = sum(1 for s in STEPS if s)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())
