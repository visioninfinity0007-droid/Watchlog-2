#!/usr/bin/env python3
"""Static contract for Phase A increment 2 (migration 0043) — no database.

Pins the watchdog + coverage SQL to its security posture and to the invariant proven
in prototype/server/coverage_model.py:
  * the sweep is cron-only, idempotent, backdates gaps to last contact, does not guess
    the cause, and mutates ONLY agent_unreachable_intervals;
  * the coverage read is tenant-scoped, read-only, unions unverified windows (no double
    count), and returns monitored = wall - unverified as the availability denominator.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "server"))

import coverage_model as cov  # noqa: E402  (import proves the spec module is present/valid)

MIG = (ROOT / "prototype/supabase/migrations/0043_agent_watchdog_coverage.sql").read_text(encoding="utf-8")


def require(needle, message):
    if needle not in MIG:
        raise AssertionError(message)


def func_body(name: str) -> str:
    """Slice the whole `create or replace function <name> ... $$;` definition (header+body)."""
    m = re.search(rf"(create or replace function public\.{name}\b.*?\$\$.*?\$\$;)", MIG, re.S | re.I)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(1)


def main():
    sweep = func_body("wl_agent_watchdog_sweep")
    covfn = func_body("wl_site_monitoring_coverage")

    # --- watchdog sweep: server-only, definer, hardened ------------------------
    require("create or replace function public.wl_agent_watchdog_sweep", "sweep function missing")
    assert "security definer" in sweep, "sweep must be security definer"
    assert "set search_path = public" in sweep, "sweep must pin search_path"
    require("revoke all on function public.wl_agent_watchdog_sweep(int) from public, anon, authenticated",
            "sweep must be revoked from all client roles")
    if re.search(r"grant execute on function public\.wl_agent_watchdog_sweep", MIG):
        raise AssertionError("sweep must NOT be granted to any client role (cron/superuser only)")

    # idempotency + correctness markers
    assert "pg_advisory_xact_lock" in sweep, "sweep must serialize to stay idempotent under races"
    assert "not exists" in sweep.lower(), "open must be guarded by a no-open-interval check"
    assert re.search(r"started_at\b", sweep) and "last_seen_at" in sweep, "gap must record started_at"
    # OPEN backdates started_at to last_seen_at (the last confirmed contact)
    assert re.search(r"insert into agent_unreachable_intervals\s*\([^)]*started_at[^)]*\)\s*select[^;]*last_seen_at",
                     sweep, re.S | re.I), "open interval must be backdated to last_seen_at"
    # CLOSE only when the agent actually reported again
    assert re.search(r"ended_at\s*=\s*a\.last_seen_at", sweep), "close must set ended_at to resumed contact"
    assert "a.last_seen_at > u.started_at" in sweep, "close must require last_seen advanced past the gap start"

    # cause is NOT guessed: the sweep never writes a PC-off/internet-down cause
    for guessed in ("pc_off", "internet", "power", "network_down"):
        if guessed in sweep.lower():
            raise AssertionError(f"sweep must not guess the cause ({guessed})")

    # SAFETY: the watchdog mutates ONLY agent_unreachable_intervals — never camera/event data
    writes = set(re.findall(r"\b(?:insert into|update)\s+(?:public\.)?([a-z_]+)", sweep, re.I))
    assert writes <= {"agent_unreachable_intervals"}, f"sweep writes unexpected tables: {writes}"

    # --- coverage read: tenant-scoped, read-only, correct union ----------------
    require("create or replace function public.wl_site_monitoring_coverage", "coverage function missing")
    assert "security definer" in covfn and "stable" in covfn, "coverage must be a definer STABLE read"
    assert "wl_my_tenant()" in covfn, "coverage must resolve the active tenant"
    assert "s.tenant_id = v_tenant" in covfn, "coverage must verify site ownership"
    require("revoke all on function public.wl_site_monitoring_coverage(uuid, timestamptz, timestamptz) from public, anon",
            "coverage read must revoke anon")
    require("grant execute on function public.wl_site_monitoring_coverage(uuid, timestamptz, timestamptz) to authenticated",
            "coverage read must be granted to authenticated")

    # read-only: no mutations in the coverage function
    if re.search(r"\b(insert into|update|delete from)\b", covfn, re.I):
        raise AssertionError("coverage read must not mutate any table")

    # union without double counting + clip to window + clamp
    assert "range_agg" in covfn, "coverage must union unverified windows with range_agg (no double count)"
    assert "greatest(started_at, v_lo)" in covfn and "least(coalesce(ended_at, v_hi), v_hi)" in covfn, \
        "coverage must clip intervals to the window"
    assert "greatest(0, v_wall - v_unverified)" in covfn, \
        "monitored_seconds must be wall - unverified, clamped >= 0"
    # both server-derived AND reconciled unverified sources are unioned
    assert "from agent_unreachable_intervals" in covfn and "from unverified_intervals" in covfn, \
        "coverage must union both agent-unreachable and reconciled unverified windows"

    # --- the invariant is documented at the source -----------------------------
    assert "denominator" in MIG.lower() and "unverified" in MIG.lower(), \
        "the coverage/denominator invariant must be stated in the migration"

    # --- scheduling: guarded pg_cron ------------------------------------------
    assert "pg_available_extensions" in MIG and "watchlog-agent-watchdog" in MIG, \
        "watchdog must self-schedule via guarded pg_cron"

    print("Agent watchdog + coverage contract: PASS")


if __name__ == "__main__":
    main()
