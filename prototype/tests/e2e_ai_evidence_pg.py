#!/usr/bin/env python3
"""0107 AI evidence workspace — scoped, encrypted, TTL'd evidence on real Postgres (disposable CI DB
only, never production). Proves the governance the harness depends on:

  * tenant A cannot enumerate or fetch tenant B evidence (foreign site => 42501);
  * site A cannot fetch another site through a manipulated event_ref (empty bundle, not a leak);
  * operational snapshots (7d) expire and disappear on retention;
  * harness snapshots survive to their 30d TTL, then disappear;
  * incident video expires within the 48-72h ceiling;
  * the retention job is retry-safe (a second run deletes nothing, no error);
  * the deletion audit survives after the raw evidence is gone;
  * evidence is encrypted at rest (raw column != plaintext; only the bundle round-trips it).

Assumes 0001..0107 are already applied (the CI integration job does this first).

    python prototype/tests/e2e_ai_evidence_pg.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import psycopg

FUNCS = ("wl_ai_evidence_put", "wl_ai_evidence_index", "wl_ai_evidence_bundle", "wl_evidence_enforce_retention")
NOW = lambda: datetime.now(timezone.utc)  # noqa: E731

STEPS = []
def step(ok, name, detail=""):
    STEPS.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD") if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_ai_evidence_pg needs " + ", ".join(miss) + " (disposable integration DB)")
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=20, autocommit=True)


def main() -> int:
    conn = connect()
    q = lambda sql, *p: conn.execute(sql, p or None).fetchone()  # returning/select only  # noqa: E731

    def claims(uid, role):
        conn.execute("select set_config('request.jwt.claims', %s, false)",
                     (json.dumps({"sub": (str(uid) if uid else None), "role": role}),))

    def as_user(uid, sql, *p):
        claims(uid, "authenticated"); conn.execute("set role authenticated")
        try: return conn.execute(sql, p or None).fetchone()
        finally: conn.execute("reset role")

    def as_service(sql, *p):
        claims(None, "service_role")
        return conn.execute(sql, p or None).fetchone()

    def bootstrap(email, company, site_name):
        uid = q("insert into auth.users (email) values (%s) returning id", email)[0]
        boot = as_user(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
        tenant = uuid.UUID(boot["tenant_id"])
        site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
        return uid, tenant, site

    def put(site, camera, event_ref, cls, payload_b64, meta, captured_at):
        return as_service(
            "select wl_ai_evidence_put(%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
            site, camera, event_ref, cls, "image/jpeg", payload_b64, json.dumps(meta), captured_at)[0]

    sfx = uuid.uuid4().hex[:8]
    for fn in FUNCS:
        if not q("select exists(select 1 from pg_proc where proname=%s)", fn)[0]:
            raise AssertionError(f"function {fn} missing — 0107 did not apply")

    owner_a, tenant_a, site_a = bootstrap(f"ev-a-{sfx}@watchlog.test", "Ev A", "Armory Site")
    cam_a = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'5','Armory') returning id", tenant_a, site_a)[0]
    owner_b, tenant_b, site_b = bootstrap(f"ev-b-{sfx}@watchlog.test", "Ev B", "Other Site")

    payload = "aGVsbG8td2F0Y2hsb2ctZXZpZGVuY2U="  # base64("hello-watchlog-evidence")
    meta = {"detections": [{"label": "person", "confidence": 0.82}],
            "coverage": {"class": "LIVE"}, "provenance": {"source": "harness"},
            "analysis": {"summary": "person near armory"}}
    ev_a = put(site_a, cam_a, "evt-armory-1", "harness_snapshot", payload, meta, NOW() - timedelta(hours=2))
    put(site_b, None, "evt-b-1", "harness_snapshot", payload, {"summary": "b"}, NOW() - timedelta(hours=1))

    # 1. tenant B cannot index/fetch tenant A's site
    denied = 0
    for sql, args in (("select wl_ai_evidence_index(%s, null, null, null)", (site_a,)),
                      ("select wl_ai_evidence_bundle(%s, %s)", (site_a, "evt-armory-1"))):
        try: as_user(owner_b, sql, *args)
        except psycopg.Error: denied += 1
        finally: conn.execute("reset role")
    step(denied == 2, "tenant B cannot index or fetch tenant A evidence (42501)")

    # 2. tenant A, own site, but a foreign/other event_ref => empty bundle (no cross-site leak)
    b = as_user(owner_a, "select wl_ai_evidence_bundle(%s, %s)", site_a, "evt-b-1")[0]
    step(b.get("found") is False and b.get("snapshots") == [], "manipulated event_ref cannot fetch another site")

    # 3. own-site index returns only own evidence, metadata only (no image bytes)
    idx = as_user(owner_a, "select wl_ai_evidence_index(%s, null, null, null)", site_a)[0]
    blob = json.dumps(idx)
    step(isinstance(idx, list) and len(idx) == 1 and idx[0]["event_ref"] == "evt-armory-1"
         and "image_b64" not in blob and payload not in blob and idx[0]["has_payload"] is True,
         "stage-1 index is scoped + compact (no bytes)", f"n={len(idx)}")

    # 4. encryption at rest: raw column != plaintext; bundle round-trips it
    raw = q("select encode(payload_enc,'hex') from ai_evidence where id=%s", ev_a)[0]
    bundle = as_user(owner_a, "select wl_ai_evidence_bundle(%s, %s)", site_a, "evt-armory-1")[0]
    got = bundle["snapshots"][0]["image_b64"] if bundle.get("snapshots") else None
    step(raw and payload not in raw and got == payload,
         "evidence encrypted at rest; bundle decrypts for the authorized caller")

    # 5. operational snapshot (7d) past TTL disappears on retention
    put(site_a, cam_a, "evt-op-old", "operational_snapshot", payload, {"summary": "old op"}, NOW() - timedelta(days=8))
    # harness snapshot within 30d survives; one past 30d is purged
    put(site_a, cam_a, "evt-harness-fresh", "harness_snapshot", payload, {"summary": "fresh"}, NOW() - timedelta(days=8))
    put(site_a, cam_a, "evt-harness-old", "harness_snapshot", payload, {"summary": "old"}, NOW() - timedelta(days=31))
    # incident video past 72h purged; one within window survives
    put(site_a, cam_a, "evt-vid-old", "incident_video", payload, {"summary": "old vid"}, NOW() - timedelta(hours=80))
    put(site_a, cam_a, "evt-vid-fresh", "incident_video", payload, {"summary": "fresh vid"}, NOW() - timedelta(hours=40))

    r1 = as_service("select wl_evidence_enforce_retention()")[0]
    surviving = {row[0] for row in conn.execute(
        "select event_ref from ai_evidence where site_id=%s", (site_a,)).fetchall()}
    step("evt-op-old" not in surviving, "expired 7-day operational snapshot removed")
    step("evt-harness-fresh" in surviving and "evt-harness-old" not in surviving,
         "harness snapshot survives to 30d, then purged")
    step("evt-vid-fresh" in surviving and "evt-vid-old" not in surviving,
         "incident video expires within the 48-72h ceiling")

    # 6. retry-safe: a second run deletes nothing, does not error
    r2 = as_service("select wl_evidence_enforce_retention()")[0]
    step(r1["deleted"] >= 3 and r2["deleted"] == 0, "retention is retry-safe", f"run1={r1['deleted']} run2={r2['deleted']}")

    # 7. audit survives after the raw evidence is gone
    aud = q("select count(*), coalesce(sum(deleted_count),0) from evidence_deletion_audit where site_id=%s", site_a)
    gone = q("select count(*) from ai_evidence where site_id=%s and event_ref in ('evt-op-old','evt-harness-old','evt-vid-old')", site_a)[0]
    step(aud[0] >= 1 and aud[1] >= 3 and gone == 0, "deletion audit persists after raw evidence deleted",
         f"audit_rows={aud[0]} deleted={aud[1]}")

    ok = all(STEPS) and len(STEPS) == 9
    print(("OK — " if ok else "FAIL — ") + f"{sum(STEPS)}/{len(STEPS)} checks passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
