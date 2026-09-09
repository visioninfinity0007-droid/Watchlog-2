#!/usr/bin/env python3
"""Archive / Historical Scan — REAL Postgres integration for the server model
(migration 0051). Disposable CI Postgres only (SUPABASE_DB_* env), never
production. Proves:

  * a bounded, tenant-scoped scan request (>7 days rejected; foreign camera rejected);
  * recovered results carry the MANDATORY 'Recovered from recorder archive' label;
  * archive results are kept SEPARATE from live streams (no events / operations_incidents);
  * RBAC (a viewer cannot request) + cross-tenant isolation.

Companion to e2e_operations_pg.py (0049) / e2e_report_pg.py (0050).
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

FUNCS = ("wl_request_archive_scan", "wl_set_archive_scan_status",
         "wl_record_archive_result", "wl_archive_scan")


def connect():
    miss = [k for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD")
            if not os.environ.get(k)]
    if miss:
        sys.exit("FATAL: e2e_archive_pg needs " + ", ".join(miss) + " (disposable integration DB)")
    return psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=20, autocommit=True)


def main() -> None:
    conn = connect()
    q = lambda sql, *p: conn.execute(sql, p or None).fetchone()          # noqa: E731

    def as_user(uid, sql, *p):
        conn.execute("select set_config('request.jwt.claims', %s, false)",
                     (json.dumps({"sub": str(uid), "role": "authenticated"}),))
        conn.execute("set role authenticated")
        try:
            return conn.execute(sql, p or None).fetchone()
        finally:
            conn.execute("reset role")

    def bootstrap(email, company, site_name):
        uid = q("insert into auth.users (email) values (%s) returning id", email)[0]
        boot = as_user(uid, "select wl_bootstrap_tenant(%s,%s)", company, site_name)[0]
        tenant = uuid.UUID(boot["tenant_id"])
        site = q("select id from sites where tenant_id=%s order by created_at limit 1", tenant)[0]
        return uid, tenant, site

    def add_member(tenant, email, role):
        uid = q("insert into auth.users (email) values (%s) returning id", email)[0]
        conn.execute("insert into memberships (user_id, tenant_id, role) values (%s,%s,%s)", (uid, tenant, role))
        return uid

    for fn in FUNCS:
        if not q("select exists(select 1 from pg_proc where proname=%s)", fn)[0]:
            raise AssertionError(f"function {fn} missing — 0051 did not apply")

    owner, tenant, site = bootstrap("arch-owner@watchlog.test", "Arch Co", "Arch Site")
    cam = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','Gate') "
            "returning id", tenant, site)[0]
    owner_b, tenant_b, site_b = bootstrap("arch-ownerb@watchlog.test", "Other Arch Co", "Other Arch Site")
    cam_b = q("insert into cameras (tenant_id, site_id, channel, name) values (%s,%s,'1','Other') "
              "returning id", tenant_b, site_b)[0]

    # 1. request a bounded scan
    scan = as_user(owner, "select wl_request_archive_scan(%s, %s::uuid[], now()-interval '2 hours', now(), null::uuid[])",
                   site, [cam])[0]
    assert scan["status"] == "requested" and scan["provenance"] == "recorder_archive", f"scan: {scan}"
    scan_id = uuid.UUID(scan["id"])

    # 2. a >7 day window is rejected (bounded retrieval, never a firehose)
    too_long = False
    try:
        as_user(owner, "select wl_request_archive_scan(%s, %s::uuid[], now()-interval '8 days', now(), null::uuid[])",
                site, [cam])
    except psycopg.Error:
        too_long = True
    assert too_long, "a >7 day scan window must be rejected"

    # 3. a camera outside the site is rejected (no cross-site scan)
    bad_cam = False
    try:
        as_user(owner, "select wl_request_archive_scan(%s, %s::uuid[], now()-interval '1 hour', now(), null::uuid[])",
                site, [cam_b])
    except psycopg.Error:
        bad_cam = True
    assert bad_cam, "a camera outside the site must be rejected"

    # 4. engine records a recovered result (service_role via superuser) -> mandatory provenance label
    res = q("select wl_record_archive_result(%s,%s,'zone_entry', now()-interval '90 min', 0.8)", scan_id, cam)[0]
    assert res["provenance_label"] == "Recovered from recorder archive", f"result label: {res}"
    st = q("select wl_set_archive_scan_status(%s,'complete',null,%s::jsonb)", scan_id, json.dumps({"candidates": 1}))[0]
    assert st["status"] == "complete" and st["completed_at"] is not None, f"status: {st}"

    # 5. read model: scan + results, every result labeled; NOT in the live streams
    rep = as_user(owner, "select wl_archive_scan(%s)", scan_id)[0]
    assert rep["scan"]["status"] == "complete" and rep["results"], f"read: {rep}"
    assert all(r["provenance_label"] == "Recovered from recorder archive" for r in rep["results"]), \
        "every archive result must carry the recovered-from-archive label"
    assert q("select count(*) from operations_incidents where site_id=%s", site)[0] == 0, \
        "archive reprocessing must NOT create live operations incidents"
    assert q("select count(*) from events where site_id=%s", site)[0] == 0, \
        "archive reprocessing must NOT create live events"

    # 6. RBAC + isolation
    viewer = add_member(tenant, "arch-viewer@watchlog.test", "viewer")
    denied_viewer = False
    try:
        as_user(viewer, "select wl_request_archive_scan(%s, %s::uuid[], now()-interval '1 hour', now(), null::uuid[])",
                site, [cam])
    except psycopg.Error:
        denied_viewer = True
    assert denied_viewer, "a viewer must NOT request an archive scan"
    denied_cross = False
    try:
        as_user(owner_b, "select wl_archive_scan(%s)", scan_id)
    except psycopg.Error:
        denied_cross = True
    assert denied_cross, "a foreign tenant must NOT read another tenant's archive scan"

    print("Archive scan Postgres integration (0051): PASS")


if __name__ == "__main__":
    main()
