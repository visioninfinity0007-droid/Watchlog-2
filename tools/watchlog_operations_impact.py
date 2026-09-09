#!/usr/bin/env python3
"""READ-ONLY production impact simulation for the 0054 Operations behavioural migration.

0054 turns qualifying analytic events into Operations incidents. Before enabling it on live
customers we must know, from the ACTUAL production database (still at 0053), exactly what would
happen. This script answers that WITHOUT mutating anything: it forces a read-only session and runs
SELECTs only.

It reports:
  * total monitoring rules (non-health);
  * rules that qualify for the 0054 promotion condition (severity attention/incident OR
    promote_incident OR sensitive OR review_required);
  * rules configured with capture_still / request_footage actions;
  * recent analytic-event volume for the qualifying rules (7 / 30 days);
  * an estimate of Operations incidents that WOULD have been created (distinct rule+camera+class
    conditions = the cooldown-collapsed lower bound, plus the raw promotable event count);
  * any Operations incidents that already exist (should be ~0 pre-0054);
  * agent versions + last-seen per site (0053 has no capability column — reported as such).

Connection comes from the SUPABASE_DB_* env (or the repo .env). Never prints secrets.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_env():
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_env()
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        print("psycopg required: python -m pip install 'psycopg[binary]'", file=sys.stderr)
        return 2
    for k in ("SUPABASE_DB_HOST", "SUPABASE_DB_USER", "SUPABASE_DB_PASSWORD"):
        if not os.environ.get(k):
            print(f"missing {k} (set env or .env)", file=sys.stderr)
            return 2

    ref = os.environ.get("SUPABASE_PROJECT_REF", "?")
    print(f"WatchLog Operations impact simulation — project {ref} (READ-ONLY)\n" + "=" * 64)
    conn = psycopg.connect(
        host=os.environ["SUPABASE_DB_HOST"], port=int(os.environ.get("SUPABASE_DB_PORT", 5432)),
        user=os.environ["SUPABASE_DB_USER"], password=os.environ["SUPABASE_DB_PASSWORD"],
        dbname=os.environ.get("SUPABASE_DB_NAME", "postgres"), connect_timeout=25,
        autocommit=True, row_factory=dict_row)
    conn.execute("set session default_transaction_read_only = on")   # hard safety belt: no writes
    conn.execute("set statement_timeout = '60s'")
    q = lambda sql: conn.execute(sql).fetchone()                       # noqa: E731
    qa = lambda sql: conn.execute(sql).fetchall()                      # noqa: E731

    def has_col(table, col):
        return q(f"select exists(select 1 from information_schema.columns "
                 f"where table_schema='public' and table_name='{table}' and column_name='{col}') e")["e"]

    QUALIFY = ("(severity in ('attention','incident') or promote_incident "
               "or coalesce(sensitive,false) or coalesce(review_required,false))")

    total = q("select count(*) c from monitoring_rules where rule_type<>'health'")["c"]
    enabled = q("select count(*) c from monitoring_rules where rule_type<>'health' and enabled")["c"]
    qual = q(f"select count(*) c from monitoring_rules where rule_type<>'health' and enabled and {QUALIFY}")["c"]
    print(f"monitoring rules (non-health):            {total}  ({enabled} enabled)")
    print(f"  -> qualify for 0054 promotion:          {qual}")

    if has_col("monitoring_rules", "actions"):
        still = q("select count(*) c from monitoring_rules where rule_type<>'health' and enabled "
                  "and actions @> '[{\"type\":\"capture_still\"}]'::jsonb")["c"]
        clip = q("select count(*) c from monitoring_rules where rule_type<>'health' and enabled "
                 "and actions @> '[{\"type\":\"request_footage\"}]'::jsonb")["c"]
        print(f"  -> with capture_still action:           {still}")
        print(f"  -> with request_footage action:         {clip}")
    else:
        print("  -> actions column absent (pre-0049)")

    for days in (7, 30):
        vol = q(f"select count(*) c from analytic_events ae join monitoring_rules r "
                f"on r.id=ae.monitoring_rule_id where r.rule_type<>'health' and r.enabled and {QUALIFY} "
                f"and ae.occurred_at > now() - interval '{days} days'")["c"]
        distinct = q(f"select count(*) c from (select distinct ae.monitoring_rule_id, ae.camera_id, ae.object_class "
                     f"from analytic_events ae join monitoring_rules r on r.id=ae.monitoring_rule_id "
                     f"where r.rule_type<>'health' and r.enabled and {QUALIFY} "
                     f"and ae.occurred_at > now() - interval '{days} days') x")["c"]
        print(f"last {days:>2}d — promotable analytic events:    {vol}")
        print(f"          estimated incidents (cooldown lower bound = distinct rule+cam+class): {distinct}")

    if q("select exists(select 1 from information_schema.tables where table_schema='public' and table_name='operations_incidents') e")["e"]:
        existing = q("select count(*) c from operations_incidents")["c"]
        print(f"operations incidents already present:     {existing}  (expected ~0 pre-0054)")

    ver_col = "agent_version" if has_col("agents", "agent_version") else ("version" if has_col("agents", "version") else None)
    has_caps = has_col("agents", "capabilities")
    print("\nagents by site (capabilities column present: %s):" % ("yes" if has_caps else "no — pre-0059, all runtimes resolve to unsupported"))
    rows = qa(f"select s.name site, count(a.*) agents, "
              f"max(a.last_seen_at) last_seen{(', ' + ver_col + ' as ver') if ver_col else ''} "
              f"from sites s left join agents a on a.site_id=s.id "
              f"group by s.name{(', ' + ver_col) if ver_col else ''} order by s.name")
    for r in rows:
        print(f"  {r['site']:<28} agents={r['agents']:<3} "
              f"version={r.get('ver') or '—':<10} last_seen={r['last_seen'] or '—'}")

    print("\nNOTE: 0054-0059 are NOT applied here (production is at 0053). With 0059, the bridge is")
    print("default-OFF per site and requires a compatible agent capability, so applying the")
    print("migrations creates ZERO of the incidents estimated above until a site explicitly opts in.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
