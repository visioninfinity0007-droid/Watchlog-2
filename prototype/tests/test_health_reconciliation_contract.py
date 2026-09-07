#!/usr/bin/env python3
"""Static contract for Phase A increment 5 (migration 0046) — no database.

Pins wl_reconcile_health, the replaced current-state-only wl_report_camera_health, and
wl_site_local_monitoring to the increment-5 rules: agent-auth with server-authoritative
tenant/site, idempotent replay (dedupe_key), observed-vs-received times kept distinct,
forward-only current state, checkpoints deduped, cloud-vs-local coverage kept separate,
no secrets, and the ledger owned solely by reconciliation (no double-write).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "server"))
import reconcile_model as rc  # noqa: E402  (proves the oracle module is valid)

MIG = (ROOT / "prototype/supabase/migrations/0046_health_reconciliation.sql").read_text(encoding="utf-8")


def func_body(name: str) -> str:
    m = re.search(rf"(create or replace function public\.{name}\b.*?\$\$.*?\$\$;)", MIG, re.S | re.I)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(1)


def require(needle, message, hay=MIG):
    if needle not in hay:
        raise AssertionError(message)


def main():
    rec = func_body("wl_reconcile_health")
    rep = func_body("wl_report_camera_health")
    loc = func_body("wl_site_local_monitoring")

    # --- schema: dedupe / observed-time / checkpoint idempotency --------------
    require("add column if not exists dedupe_key", "ledger needs a dedupe_key for idempotent replay")
    require("add column if not exists received_at", "ledger must separate received_at from observed at")
    require("add column if not exists observed_at", "camera_health needs observed_at for forward-only")
    require("create unique index if not exists camera_health_tx_dedupe_uidx",
            "dedupe_key must be unique (no duplicate ledger row on replay)")
    require("create table if not exists public.local_monitoring_checkpoints", "checkpoint table missing")
    # checkpoints dedupe on the EPOCH-QUALIFIED id, not bare (agent, seq) — a corrupt-store
    # rebuild restarts seq, so bare seq would silently discard genuinely new evidence.
    require("checkpoint_id    text not null", "checkpoints need an epoch-qualified checkpoint_id")
    require("store_epoch      text", "checkpoints must carry the store epoch")
    require("unique (checkpoint_id)", "checkpoints must dedupe on the rebuild-proof checkpoint_id")
    if re.search(r"unique \(agent_id, agent_seq\)", MIG):
        raise AssertionError("bare (agent_id, agent_seq) dedupe collides after a store rebuild")
    require("alter table public.local_monitoring_checkpoints enable row level security", "checkpoint RLS")
    require("revoke all on table public.local_monitoring_checkpoints from public, anon, authenticated",
            "checkpoint table must be fail-closed")

    # --- wl_reconcile_health: auth + server-authoritative binding -------------
    assert "security definer" in rec and "set search_path = public" in rec, "definer + search_path"
    require("wl_auth_agent(p_agent_id, p_agent_key)", "must authenticate the agent", rec)
    require("using errcode = '28000'", "unrecognised agent -> 28000", rec)
    assert "v_agent.tenant_id" in rec and "v_agent.site_id" in rec, "bind to agent row"
    require("cm.site_id = v_agent.site_id", "transitions bind to the agent's site only", rec)
    if re.search(r"(p_transitions|p_checkpoints|t|c)\s*(#>>?|->>?)\s*'?\{?[^;']*(tenant|site_id)", rec, re.I):
        raise AssertionError("tenant/site must not come from the payload")
    require("grant execute on function public.wl_reconcile_health(uuid, text, jsonb, jsonb, int) to anon, authenticated",
            "reconcile RPC (5-arg) granted to anon+authenticated")
    require("revoke all on function public.wl_reconcile_health(uuid, text, jsonb, jsonb, int) from public",
            "reconcile RPC (5-arg) revoked from public")

    # --- idempotent replay + observed vs received + forward-only --------------
    require("on conflict (dedupe_key) where dedupe_key is not null do nothing",
            "replay must be idempotent on dedupe_key", rec)
    require("on conflict (checkpoint_id) do nothing", "checkpoint replay must be idempotent (epoch-qualified)", rec)
    # `at` (raw device) + effective_at (server-safe) + received_at (v_now) are all distinct
    assert "device_ts, effective_at, v_now, source, dedupe_key" in rec, \
        "ledger must store RAW device_ts (at), effective_at, AND received v_now — all distinct"

    # --- clock-skew: forward-only uses EFFECTIVE (clamped) time, never raw device clock ----
    require("wl_try_timestamptz", "device_ts must be parsed with the safe cast, not a raw ::cast")
    if re.search(r"\(t->>'device_ts'\)::timestamptz", rec) or re.search(r"\(c->>'device_ts'\)::timestamptz", rec):
        raise AssertionError("raw ::timestamptz cast on device_ts can fail the whole batch (poison)")
    require("case when device_ts > v_now + v_skew then v_now else device_ts end",
            "future-skew must be clamped to now for the effective ordering time", rec)
    require("l.effective_at > coalesce(ch.observed_at, '-infinity'::timestamptz)",
            "forward-only watermark must use effective_at (clamped), not raw device_ts", rec)
    require("observed_at       = l.effective_at", "current-state watermark must be the effective time", rec)
    require("p_max_future_skew_seconds int default 300", "future-skew tolerance must be configurable")

    # --- poison-row safety: one bad row does not fail the batch; rejects are observable -------
    require("where device_ts is not null", "unparseable rows must be quarantined, not fatal", rec)
    for k in ("transitions_rejected", "transitions_clamped", "checkpoints_rejected"):
        require(k, f"reconcile must report {k} (observable)", rec)

    # safe-cast helper is a real fail-soft cast
    require("create or replace function public.wl_try_timestamptz", "safe timestamptz cast helper missing")
    require("exception when others then", "wl_try_timestamptz must return NULL on bad input, not raise")

    # --- ledger owned solely by reconciliation: live path writes NO transitions ----
    if re.search(r"insert into camera_health_transitions", rep, re.I):
        raise AssertionError("wl_report_camera_health must NOT write the transition ledger (no double-write)")
    require("where v_now >= coalesce(chh.observed_at, '-infinity'::timestamptz)",
            "live current-state upsert must also be forward-only", rep)

    # --- cloud vs local coverage kept DISTINCT --------------------------------
    for k in ("cloud_gap_seconds", "locally_monitored_seconds", "still_unverified_seconds"):
        require(k, f"local-monitoring read must report {k} distinctly", loc)
    # must not claim the cloud was online just because we were locally monitoring
    if re.search(r"cloud.{0,20}online|online.{0,20}cloud", loc, re.I):
        raise AssertionError("local monitoring must never be reclassified as cloud connectivity")
    assert "wl_my_tenant()" in loc and "s.tenant_id = v_tenant" in loc, "local-monitoring read must be tenant-scoped"

    # --- no secrets anywhere in 0046 ------------------------------------------
    code = "\n".join(l for l in MIG.splitlines() if not l.lstrip().startswith("--"))
    for secret in ("password", "rtsp", "base_url", "stream_url", "authorization", "credential",
                   "agent_key_hash", "image", "snapshot", "jpeg"):
        if secret in code.lower():
            raise AssertionError(f"reconciliation must not handle/store {secret}")

    print("Health reconciliation contract: PASS")


if __name__ == "__main__":
    main()
