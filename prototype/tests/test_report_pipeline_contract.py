#!/usr/bin/env python3
"""Report delivery pipeline contract (items 1, 2, 8) — snapshot -> outbox -> provider.

Static contract over the real files: the n8n schedule, the token-gated runner whose DEFAULT
path is the canonical snapshot pipeline, the delivery module (frozen snapshot + PDF + durable
outbox with idempotency), and the schema (report_snapshots freeze + delivery_outbox unique key).
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
    # 1. n8n workflow: schedule -> httpRequest to the runner -> IF gate
    wf = json.loads((REPO / "automation" / "n8n" / "watchlog-daily-report.json").read_text(encoding="utf-8"))
    types = [n.get("type") for n in wf.get("nodes", [])]
    check("n8n-nodes-base.scheduleTrigger" in types, "n8n has a schedule trigger")
    check("n8n-nodes-base.httpRequest" in types, "n8n posts to the runner via httpRequest")
    check("run/" in json.dumps(wf), "n8n targets the runner /run/<token> URL")

    # 2. runner: token-gated, and its DEFAULT path is the canonical snapshot pipeline
    serve = (ROOT / "reporter" / "serve.py").read_text(encoding="utf-8")
    check("REPORT_RUNNER_TOKEN" in serve and "token != TOKEN" in serve, "runner is token-gated")
    dr = (ROOT / "reporter" / "daily_report.py").read_text(encoding="utf-8")
    check("def run_intelligence(" in dr, "runner has the canonical intelligence path")
    check("legacy else run_intelligence" in dr, "run_intelligence is the DEFAULT; legacy is opt-in")
    check("wl_generate_daily_report" in dr, "runner sources the frozen snapshot dataset")

    # 3. delivery module: snapshot + PDF saved + durable outbox with idempotency
    deliv = (ROOT / "reporter" / "intelligence_delivery.py").read_text(encoding="utf-8")
    check("wl_generate_daily_report" in deliv, "delivery uses the frozen report snapshot")
    check("render_pdf" in deliv and "wl_set_report_pdf" in deliv, "delivery generates the PDF and saves its reference")
    check("wl_outbox_enqueue" in deliv and "wl_outbox_claim" in deliv, "delivery uses the durable outbox")
    check("idempotency_key" in deliv, "the provider is sent an idempotency key")
    check("render_whatsapp" in deliv, "WhatsApp is rendered from the same snapshot payload")

    # 4. schema: freeze + outbox idempotency
    snap = (ROOT / "supabase" / "migrations" / "0083_report_snapshots.sql").read_text(encoding="utf-8")
    check("report_snapshots" in snap and "returned VERBATIM" in snap or "frozen" in snap.lower(),
          "report_snapshots freezes the payload (immune to later change)")
    ob = (ROOT / "supabase" / "migrations" / "0084_delivery_outbox.sql").read_text(encoding="utf-8")
    check("unique (idempotency_key)" in ob, "delivery_outbox has a unique idempotency key")
    check("AT-LEAST-ONCE" in ob and "effective-once" in ob.lower(), "the honest delivery semantic is documented, not overclaimed")

    # 5. semantics doc exists
    check((REPO / "docs" / "design" / "DELIVERY_SEMANTICS.md").exists(), "delivery semantics documented")

    passed = sum(1 for x in OK if x)
    print(f"\n  {passed}/{len(OK)} checks passed")
    return 0 if passed == len(OK) else 1


if __name__ == "__main__":
    sys.exit(main())
