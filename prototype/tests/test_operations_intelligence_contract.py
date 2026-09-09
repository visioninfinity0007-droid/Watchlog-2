#!/usr/bin/env python3
"""Static contract for 0049 Operations Intelligence — regexes the migration SQL
so a text-level regression (a dropped REVOKE, a weakened authz check, a lost
'candidate' state) fails fast in the offline job, before the disposable-Postgres
e2e. Genericity is part of the contract: no industry/customer hardcoding, no
facial recognition. Whitespace is normalized so alignment padding is irrelevant.
"""
import re
from pathlib import Path

RAW = (Path(__file__).resolve().parents[1] / "supabase" / "migrations"
       / "0049_operations_intelligence.sql").read_text(encoding="utf-8")
SQL = re.sub(r"[ \t]+", " ", RAW)          # collapse runs of spaces/tabs


def has(*needles):
    for n in needles:
        assert n in SQL, f"0049 must contain: {n!r}"


def check(cond, msg):
    assert cond, msg


# --- generic engine, no hardcoded verticals, no biometrics ----------------
has("create table if not exists public.operations_incidents")
for banned in ("gatehouse", "al-khalid", "alkhalid", "restaurant", "retail",
               "facial", "face_recognition", "biometric"):
    check(banned not in RAW.lower(),
          f"0049 must stay generic / no facial recognition — found {banned!r}")

# --- per-rule immutable versioning + provenance ---------------------------
has("create table if not exists public.monitoring_rule_versions",
    "constraint monitoring_rule_versions_uniq unique (rule_id, version)",
    "trg_monitoring_rule_bump_version", "trg_monitoring_rule_snapshot",
    "new.rule_version := old.rule_version + 1",
    "rule_version int,")

# --- incident lifecycle: candidate/review, dedupe on open states ----------
has("'candidate','open','acknowledged','resolved','dismissed'",
    "operations_incidents_open_uidx",
    "where status in ('candidate','open','acknowledged')")

# --- engine governance: confidence gate, cooldown, sensitive->candidate ----
has("p_confidence < v_rule.confidence_min",
    "make_interval(secs => v_rule.cooldown_seconds)",
    "when v_rule.sensitive or v_rule.review_required then 'candidate'",
    "in ('capture_still','request_footage','mark_review','include_in_report',")

# --- authz: emit is service-role only; lifecycle owner/admin; reads scoped --
has("grant execute on function public.wl_emit_operations_incident",
    "to service_role")
check("wl_emit_operations_incident(uuid,uuid,text,numeric,timestamptz,text,jsonb) to authenticated"
      not in SQL, "emit must NOT be granted to authenticated (no customer-fabricated incidents)")
check(SQL.count("wl_require_role(array['owner','admin'])") >= 3,
      "acknowledge/resolve/dismiss must each require owner/admin")
has("wl_my_tenant()", "raise exception 'site not in your account'")

# --- RLS on every new table -----------------------------------------------
for t in ("monitoring_rule_versions", "operations_incidents", "operations_incident_actions"):
    check(f"alter table public.{t} enable row level security" in SQL,
          f"{t} must have RLS enabled")

print("Operations Intelligence static contract (0049): PASS")
