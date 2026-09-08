#!/usr/bin/env python3
"""Static contract for Phase A increment 6 (migration 0047) — no database.

Proves the structural properties the review demanded:
  1. SINGLE ledger + current-state writer: there is NO separate live RPC; only
     wl_reconcile_recording_storage writes the typed ledgers AND the current-state columns, via the
     durable watermark. So a direct write cannot bypass the ordering watermark.
  2. dedicated correctly-typed ledgers + separate reason columns (no clobbering other layers).
  3. every increment-5 reconciliation invariant.
  4. PER-ID disposition (accepted_ids / duplicate_ids / rejected[{id,reason}]): the agent acknowledges
     only ledger-durable ids, so a partial rejection (RPC success) can never lose a rejected row.
  5. CREATE-IF-ABSENT preserving upsert: current state is created from the ledger winner if missing and
     touches only its own layer's columns, so a ledger insert can never leave current state a no-op.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "prototype" / "server"))
import recording_model as rm  # noqa: E402  (proves the classifier module is valid)

MIG = (ROOT / "prototype/supabase/migrations/0047_recording_storage_report.sql").read_text(encoding="utf-8")


def func_body(name: str) -> str:
    m = re.search(rf"(create or replace function public\.{name}\b.*?\$\$.*?\$\$;)", MIG, re.S | re.I)
    if not m:
        raise AssertionError(f"function {name} not found")
    return m.group(1)


def require(needle, message, hay=MIG):
    if needle not in hay:
        raise AssertionError(message)


def main():
    # === 1. SINGLE writer: no separate live current-state RPC exists ============
    if re.search(r"create or replace function public\.wl_report_recording_storage", MIG, re.I):
        raise AssertionError("a separate live RPC must not exist — it could bypass the durable watermark")
    rec = func_body("wl_reconcile_recording_storage")
    # the ONLY writes to the ledgers and to the current-state columns are inside reconcile:
    non_rec = MIG.replace(rec, "")
    for writer in (r"insert into recording_transitions", r"insert into storage_transitions",
                   r"recording_state\s*=", r"storage_state\s*=",
                   r"rec_observed_at\s*=", r"sto_observed_at\s*="):
        if re.search(writer, non_rec, re.I):
            raise AssertionError(f"only wl_reconcile_recording_storage may write {writer}")

    # === 2. schema: separate reasons, watermarks, dedicated TYPED ledgers =======
    require("camera_health add column if not exists recording_reason_code", "camera recording reason column")
    require("nvr_health    add column if not exists storage_reason_code", "nvr storage reason column")
    for wm in ("rec_observed_at", "rec_observed_epoch", "rec_observed_seq", "rec_observed_ingest",
               "sto_observed_at", "sto_observed_epoch", "sto_observed_seq", "sto_observed_ingest"):
        require(wm, f"missing forward-only watermark column {wm}")
    require("create table if not exists public.recording_transitions", "dedicated recording ledger")
    require("create table if not exists public.storage_transitions", "dedicated storage ledger")
    require("from_state   public.wl_recording_state", "recording ledger typed wl_recording_state")
    require("from_state   public.wl_storage_state", "storage ledger typed wl_storage_state")
    for t in ("recording_transitions", "storage_transitions"):
        require(f"alter table public.{t} enable row level security", f"{t} RLS")
        require(f"revoke all on table public.{t} from public, anon, authenticated", f"{t} fail-closed")
    require("create unique index if not exists recording_tx_dedupe_uidx", "recording dedupe unique index")
    require("create unique index if not exists storage_tx_dedupe_uidx", "storage dedupe unique index")
    if re.search(r"insert into camera_health_transitions", MIG, re.I):
        raise AssertionError("recording transitions must use their own typed ledger, not the video ledger")
    # never write the reason_code owned by video health / connectivity, nor video/connectivity columns
    for forbidden in (r"[^_]reason_code\s*=", r"health_state\s*=", r"nvr_reachable\s*=", r"nvr_auth_ok\s*="):
        if re.search(forbidden, rec):
            raise AssertionError(f"reconcile must not write {forbidden} (belongs to another layer)")

    # === 3. every increment-5 reconciliation invariant =========================
    assert "security definer" in rec and "set search_path = public" in rec, "definer+search_path"
    require("wl_auth_agent(p_agent_id, p_agent_key)", "authenticate the agent", rec)
    require("using errcode = '28000'", "unrecognised agent -> 28000", rec)
    assert "v_agent.tenant_id" in rec and "v_agent.site_id" in rec, "bind to the agent row"
    require("grant execute on function public.wl_reconcile_recording_storage(uuid, text, jsonb, int) to anon, authenticated",
            "reconcile grant")
    require("count(distinct coalesce(nullif(t->>'store_epoch',''), split_part(t->>'id', ':', 2)))",
            "single-epoch guard: count distinct epochs", rec)
    require("when v_mixed then 'mixed_epoch_batch'", "mixed-epoch batch rejected", rec)
    require("if not v_mixed then", "current state skipped for a mixed-epoch batch", rec)
    for helper in ("wl_try_timestamptz", "wl_try_bigint"):
        require(helper + "(", f"poison-safe cast {helper}", rec)
    if re.search(r"\(t->>'device_ts'\)::timestamptz", rec) or re.search(r"\(t->>'seq'\)::bigint", rec):
        raise AssertionError("raw cast on a payload field can poison the batch")
    require("on conflict (dedupe_key) where dedupe_key is not null do nothing", "idempotent replay", rec)
    require("case when device_ts > v_now + v_skew then v_now else device_ts end", "future-clock clamp", rec)
    # forward-only COMPOSITE watermark (effective_at -> numeric seq -> server first-seen ingest),
    # expressed against excluded.* because current state is an UPSERT (create-if-absent), not a bare UPDATE
    require("excluded.rec_observed_at > coalesce(ch.rec_observed_at, '-infinity'::timestamptz)", "camera forward-only time", rec)
    require("excluded.rec_observed_seq > coalesce(ch.rec_observed_seq, -1)", "camera same-epoch tie by numeric seq", rec)
    require("excluded.rec_observed_ingest > coalesce(ch.rec_observed_ingest, -1)", "camera cross-epoch tie by first-seen", rec)
    require("excluded.sto_observed_seq > coalesce(nh.sto_observed_seq, -1)", "storage same-epoch tie by numeric seq", rec)
    require("excluded.sto_observed_ingest > coalesce(nh.sto_observed_ingest, -1)", "storage cross-epoch tie by first-seen", rec)
    for k in ("transitions_received", "transitions_valid", "transitions_rejected", "transitions_rejected_by"):
        require(k, f"reconcile must report {k}", rec)
    require("in ('ok','degraded','fault','unknown') then to_raw", "storage clamp to wl_storage_state", rec)
    require("in ('recording','not_recording','storage_fault','unknown') then to_raw", "recording clamp", rec)

    # === 4. PER-ID disposition: RPC success must NOT be read as "all rows accepted" ==============
    require("returning dedupe_key", "ledger insert returns ids so accepted can be reported", rec)
    for k in ("'accepted_ids'", "'duplicate_ids'", "'rejected'"):
        require(k, f"reconcile must return {k}", rec)
    require("jsonb_build_object('id', dedupe_key, 'reason', verdict)", "rejected carries id + reason", rec)
    require("where v.dedupe_key not in (select dedupe_key from accepted)",
            "duplicate = valid ids not newly inserted (idempotent replay)", rec)

    # === 5. current state is a CREATE-IF-ABSENT upsert, per layer, preserving other layers =======
    # A ledger insert must never succeed while current state silently no-ops for want of a baseline row.
    if "update camera_health ch" in rec or "update nvr_health nh" in rec:
        raise AssertionError("current state must be a preserving UPSERT, not a bare UPDATE (no-ops on an absent row)")
    require("insert into camera_health as ch", "camera recording current state is an upsert", rec)
    require("on conflict (camera_id) do update", "camera upsert conflict target = camera_id", rec)
    require("insert into nvr_health as nh", "nvr storage current state is an upsert", rec)
    require("on conflict (agent_id) do update", "nvr upsert conflict target = agent_id", rec)

    # preservation by OMISSION: each upsert's DO UPDATE SET assigns ONLY its own layer's columns, so
    # every unrelated dimension (video health, inventory, connectivity/auth) is left untouched.
    def set_columns(entity):
        m = re.search(rf"insert into {entity}\b.*?do update\s+set\s+(.*?)\s+where ", rec, re.S | re.I)
        if not m:
            raise AssertionError(f"{entity} upsert set-clause not found")
        return set(re.findall(r"(\w+)\s*=", m.group(1)))
    cam_cols = set_columns("camera_health")
    if cam_cols != {"recording_state", "recording_reason_code", "rec_observed_at", "rec_observed_epoch",
                    "rec_observed_seq", "rec_observed_ingest", "updated_at"}:
        raise AssertionError(f"camera recording upsert must set ONLY its own columns; got {cam_cols}")
    sto_cols = set_columns("nvr_health")
    if sto_cols != {"storage_state", "storage_reason_code", "sto_observed_at", "sto_observed_epoch",
                    "sto_observed_seq", "sto_observed_ingest", "updated_at"}:
        raise AssertionError(f"nvr storage upsert must set ONLY its own columns; got {sto_cols}")

    # === no secrets ============================================================
    code = "\n".join(l for l in MIG.splitlines() if not l.lstrip().startswith("--"))
    for secret in ("password", "rtsp", "base_url", "stream_url", "authorization", "credential",
                   "snapshot", "image", "get_clip"):
        if secret in code.lower():
            raise AssertionError(f"0047 must not handle/store {secret}")

    print("Recording/storage reconcile contract: PASS")


if __name__ == "__main__":
    main()
