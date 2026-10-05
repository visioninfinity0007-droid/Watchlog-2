#!/usr/bin/env python3
"""Contract for server-side recovery of abandoned incident stills (MNVR-033).

The still claim's lease rules run only while an Agent polls. Like the
production 0142 clip finalizer, the still finalizer must be service_role only
and scheduled by pg_cron every 2 minutes, so it runs when no Agent is left to
poll. The e2e scripts must not read migration files
(test_e2e_no_stale_migration_rerun.py), so the schedule source is checked here;
real behaviour is proven by e2e_multi_recorder_evidence_lease_pg.py.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
FUNCTION = "wl_finalize_stale_incident_stills"
JOB = "watchlog-finalize-stale-incident-stills"


def _final_definition() -> str:
    """The last migration that defines the finalizer: the body production runs."""
    pattern = re.compile(
        r"create\s+or\s+replace\s+function\s+public\." + FUNCTION + r"\s*\(", re.I
    )
    found = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            found = text
    assert found, f"no migration defines public.{FUNCTION}"
    return " ".join(found.lower().split())


SQL = _final_definition()
START = SQL.index(f"create or replace function public.{FUNCTION}(")
BODY = SQL[START:SQL.index("$function$;", START)]
TAIL = SQL[START:]


def test_stale_still_finalizer_is_server_only_and_bounded():
    assert "security definer set search_path = public" in BODY
    assert "e.status='processing'" in BODY
    assert "coalesce(e.claim_expires_at,now())<=now()" in BODY
    assert "case when l.attempts>=3 then 'failed' else 'pending' end" in BODY
    assert (f"revoke all on function public.{FUNCTION}() "
            "from public,anon,authenticated,service_role;") in TAIL
    assert f"grant execute on function public.{FUNCTION}() to service_role;" in TAIL


def test_stale_still_finalizer_runs_independently_of_agent_polling():
    block = TAIL[TAIL.index("do $$"):]
    block = block[:block.index("end $$;")]
    assert "pg_available_extensions where name = 'pg_cron'" in block
    assert f"where jobname = '{JOB}'" in block
    assert re.search(
        r"cron\.schedule\( '" + JOB + r"', '\*/2 \* \* \* \*',"
        r" 'select public\." + FUNCTION + r"\(\)' \)",
        block,
    )
    assert "exception when others" in block


if __name__ == "__main__":
    tests = [v for k, v in globals().copy().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"OK: {len(tests)} stale incident still recovery contracts passed")
