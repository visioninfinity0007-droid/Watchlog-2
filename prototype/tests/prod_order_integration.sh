#!/usr/bin/env bash
# Production-ORDER migration simulation on a DISPOSABLE Postgres (never production).
#
# Production was activated at 0001-0039 + 0042-0048 (Phase-A health), WITHOUT 0040/0041 —
# they did not exist at activation time. The real upgrade will therefore apply 0040/0041
# LATER, out of numeric order, on top of a DB that already has 0048. The default `integration`
# job applies 0001..0048 in numeric order (a clean install); that does NOT exercise the actual
# production upgrade path. This does:
#
#   stage 1  apply 0001-0039 + 0042-0048     (the current production baseline)
#   step  2  prove Phase-A health RPCs execute
#   stage 2  apply 0040 then 0041            (the pending upgrade, applied AFTER 0048)
#   step  6  run the full incident-footage lifecycle + authz
#   step  7  re-run the Phase-A health smoke — prove 0040/0041 did not disturb health
#
# Run from the repo root AFTER ci_prelude.sql has created roles/auth on the disposable DB.
# Connection comes from SUPABASE_DB_* env (set by the CI job).
set -euo pipefail

MIG=prototype/supabase/migrations
APPLY=prototype/supabase/apply_migrations.py
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "===== stage 1: production baseline — 0001-0039 + 0042-0048 (NO 0040/0041) ====="
cp $MIG/00[0-3][0-9]_*.sql "$STAGE"/
cp $MIG/004[2-8]_*.sql "$STAGE"/
n1=$(ls "$STAGE"/*.sql | wc -l)
echo "  staged $n1 migrations (expect 46)"
[ "$n1" -eq 46 ] || { echo "FATAL: baseline stage expected 46 migrations, got $n1"; exit 1; }
WATCHLOG_MIGRATIONS_DIR="$STAGE" python "$APPLY"

echo "===== step 2: prove Phase-A health RPCs execute on the production baseline ====="
python prototype/tests/e2e_health_pg.py

echo "===== stage 2: introduce 0040 then 0041 (applied AFTER 0048, exactly as production will) ====="
cp $MIG/0040_*.sql $MIG/0041_*.sql "$STAGE"/
WATCHLOG_MIGRATIONS_DIR="$STAGE" python "$APPLY"

echo "===== step 6: full incident-footage lifecycle + authz (request..retention, RBAC, cross-tenant, agent auth) ====="
python prototype/tests/e2e_incident_pg.py

echo "===== step 7: re-run Phase-A health smoke — prove 0040/0041 did NOT disturb health ====="
python prototype/tests/e2e_health_pg.py

# ---------------------------------------------------------------------------------------------
# Operations-Intelligence + governance layer. Production is already at 0049-0053 (Operations
# Intelligence, report, archive, lease, rule governance); the pending upgrade is 0054 (live
# incident bridge), 0055 (agent archive execution) and 0056 (governance reaches the agent).
# They apply in numeric order AFTER the 0040/0041 baseline above — exactly as production will.
# ---------------------------------------------------------------------------------------------
echo "===== stage 3: operations + governance + authoring + evidence — 0049-0058 (0049-0053 live, 0054-0058 pending) ====="
cp $MIG/0049_*.sql $MIG/005[0-8]_*.sql "$STAGE"/
n3=$(ls "$STAGE"/*.sql | wc -l)
echo "  staged $n3 migrations total (baseline 46 + 0040/0041 + 0049-0058 = 58)"
[ "$n3" -eq 58 ] || { echo "FATAL: stage 3 expected 58 migrations, got $n3"; exit 1; }
WATCHLOG_MIGRATIONS_DIR="$STAGE" python "$APPLY"

echo "===== step 8: prove the pending upgrade EXECUTES on the production-order DB ====="
python prototype/tests/e2e_operations_pg.py        # 0049 lifecycle + versioning + authz + 0054 bridge
python prototype/tests/e2e_report_pg.py            # 0050 executive report + completeness truth
python prototype/tests/e2e_archive_pg.py           # 0051 bounded scan + 0055 agent execution + provenance
python prototype/tests/e2e_multiagent_pg.py        # 0052 single-authority lease + fencing failover
python prototype/tests/e2e_config_governance_pg.py # 0056 governance/primitives travel to the runtime
python prototype/tests/e2e_operations_authoring_pg.py  # 0057 every primitive authorable -> engine
python prototype/tests/e2e_incident_evidence_pg.py     # 0058 bounded evidence: authz + idempotency

echo "===== step 9: re-run Phase-A health smoke — prove 0049-0056 did NOT disturb health ====="
python prototype/tests/e2e_health_pg.py

echo "PRODUCTION-ORDER SIMULATION: PASS (health baseline -> 0040/0041 -> incident lifecycle -> "\
"operations/governance 0049-0056 -> governance travels -> health undisturbed)"
