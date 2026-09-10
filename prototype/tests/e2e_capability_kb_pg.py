#!/usr/bin/env python3
"""Recorder-capability KB batch 2 (0066) — applies + resolves against live PG, rolled back.

Proves the honest-verdict rules survive into the resolver: a datasheet's exhaustive alarm
list yields 'unsupported', silence yields 'unknown', and the within-series contrast (an
exhaustive Cooper-I sibling vs a silent one) is preserved rather than generalized.

    python prototype/tests/e2e_capability_kb_pg.py
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

MIG_61 = (ROOT / "supabase" / "migrations" / "0061_recorder_capability_model.sql").read_text(encoding="utf-8")
MIG_66 = (ROOT / "supabase" / "migrations" / "0066_recorder_capability_kb_batch2.sql").read_text(encoding="utf-8")

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def run() -> int:
    dsn = dict(host=ENV["SUPABASE_DB_HOST"], port=int(ENV.get("SUPABASE_DB_PORT", 5432)),
               user=ENV["SUPABASE_DB_USER"], password=ENV["SUPABASE_DB_PASSWORD"],
               dbname=ENV.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=30, autocommit=False)

    def cap(cur, vendor, model, capability):
        cur.execute("select wl_recorder_capability(%s,%s,%s)", (vendor, model, capability))
        return cur.fetchone()[0]

    with psycopg.connect(**dsn) as conn, conn.cursor() as cur:
        try:
            cur.execute(MIG_61)     # idempotent; ensures tables+resolver exist on a fresh DB
            cur.execute(MIG_66)

            a = cap(cur, "Dahua", "DH-XVR5108H-I3", "line_crossing")
            step(a["verdict"] == "supported" and a["evidence_class"] == "OFFICIAL_DOCUMENTED",
                 "XVR5108H-I3 line_crossing = supported/OFFICIAL", a["verdict"])

            b = cap(cur, "Hikvision", "DS-7104HGHI-K1", "line_crossing")
            step(b["verdict"] == "unsupported", "DS-7104HGHI-K1 line_crossing = unsupported (exhaustive MD2.0-only)", b["verdict"])

            c = cap(cur, "Dahua", "DH-XVR1B16-I", "line_crossing")
            step(c["verdict"] == "unknown", "DH-XVR1B16-I line_crossing = unknown (silent — NOT generalized from 1B04-I)", c["verdict"])

            d = cap(cur, "Hikvision", "DS-7604NI-K1(B)", "video_loss")
            step(d["verdict"] == "unknown", "DS-7604NI-K1(B) video_loss = unknown (no event table)", d["verdict"])

            e = cap(cur, "Dahua", "DHI-NVR2104HS-P-I2", "face_recognition")
            step(e["verdict"] == "supported" and e["ai_location"] == "both",
                 "NVR2104HS-P-I2 face_recognition = supported / ai_location both", f"{e['verdict']}/{e['ai_location']}")

            f = cap(cur, "Dahua", "DH-XVR1B04-I", "human_vehicle_classification")
            step(f["verdict"] == "supported", "XVR1B04-I SMD human/vehicle = supported", f["verdict"])

            cur.execute("select count(distinct model) from recorder_capabilities")
            n = cur.fetchone()[0]
            step(n >= 18, "at least 18 distinct models seeded", str(n))

            # No FIELD_VERIFIED laundering: every batch-2 fact is OFFICIAL (or below).
            cur.execute("""select count(*) from recorder_capabilities
                           where source_ids && array['DAHUA-XVR-S10','HIK-NVR-S43']
                             and evidence_class = 'FIELD_VERIFIED'""")
            step(cur.fetchone()[0] == 0, "no batch-2 fact is FIELD_VERIFIED")
        finally:
            conn.rollback()
    ok = sum(1 for s in STEPS if s)
    print(f"\n  {ok}/{len(STEPS)} steps passed")
    return 0 if ok == len(STEPS) else 1


if __name__ == "__main__":
    sys.exit(run())
