#!/usr/bin/env python3
"""Report delivery pipeline contract (item 8) — n8n trigger -> runner -> idempotent delivery.

Static contract over the real files, asserting the pieces connect and the safety/idempotency
rules are present in source: the n8n schedule+webhook, the token-gated runner, the delivery
module rendering from the canonical dataset with duplicate suppression + retry, and the
report_deliveries sent-only idempotency index.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
OK = []
def check(cond, name):
    OK.append(bool(cond)); print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def main() -> int:
    # 1. n8n workflow: schedule -> httpRequest to the runner -> IF gate on ok
    wf = json.loads((REPO / "automation" / "n8n" / "watchlog-daily-report.json").read_text(encoding="utf-8"))
    types = [n.get("type") for n in wf.get("nodes", [])]
    check("n8n-nodes-base.scheduleTrigger" in types, "n8n has a schedule trigger")
    check("n8n-nodes-base.httpRequest" in types, "n8n posts to the runner via httpRequest")
    check("n8n-nodes-base.if" in types, "n8n gates on the runner result (IF node)")
    blob = json.dumps(wf)
    check("run/" in blob and "RUNNER" in blob.upper(), "n8n targets the runner /run/<token> URL")

    # 2. runner is token-gated and invokes the report run
    serve = (ROOT / "reporter" / "serve.py").read_text(encoding="utf-8")
    check("REPORT_RUNNER_TOKEN" in serve, "runner reads REPORT_RUNNER_TOKEN")
    check('token != TOKEN' in serve and "/run/" in serve, "runner rejects a missing/wrong token")
    check("daily_report" in serve and ".run(" in serve, "runner invokes daily_report.run")

    # 3. delivery module: canonical dataset render + idempotency + retry + persistence
    deliv = (ROOT / "reporter" / "intelligence_delivery.py").read_text(encoding="utf-8")
    check("wl_daily_intelligence" in deliv, "delivery renders from the canonical dataset")
    check("render_whatsapp" in deliv, "delivery uses the intelligence WhatsApp renderer")
    check("report_deliveries" in deliv, "delivery persists to report_deliveries")
    check("status = 'sent'" in deliv, "delivery suppresses duplicates (sent-only pre-check)")
    check("wl_reporting_enabled" in deliv, "delivery honors the entitlement gate")

    # 4. idempotency index exists at the schema layer
    sql = (ROOT / "supabase" / "migrations" / "0011_report_delivery.sql").read_text(encoding="utf-8")
    check("report_deliveries_once" in sql and "where status = 'sent'" in sql,
          "report_deliveries has the sent-only idempotency index (retry-safe)")

    passed = sum(1 for x in OK if x)
    print(f"\n  {passed}/{len(OK)} checks passed")
    return 0 if passed == len(OK) else 1


if __name__ == "__main__":
    sys.exit(main())
